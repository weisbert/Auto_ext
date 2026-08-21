"""Pure-Python orchestrator for ``auto-ext init-project``.

Phase 5.7 extracted this from ``cli.py`` so the GUI wizard can drive
the same flow without spawning a subprocess. The module exposes:

- :class:`InitInputs` — frozen dataclass of every CLI / wizard knob.
- :class:`InitPreview` — what :func:`dry_run` produces. Carries every
  output text + filesystem-impact metadata, but no side effects yet.
- :func:`dry_run` — read raws, import per tool, cross-validate identity,
  aggregate PDK tokens, render every output text. Pure.
- :func:`commit` — synchronous fan-out write of ``preview.files``,
  optionally tracing each path through a ``progress`` callback.
- :func:`cross_validate_identities` — public sibling of the legacy
  ``_cross_validate_init_identities``; returns a conflicts list rather
  than raising so callers can decide UX.
- :func:`build_project_yaml` / :func:`build_tasks_yaml` — text emitters,
  reused by both the CLI and the wizard preview tab.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Callable, Literal, Optional

from ruamel.yaml import YAML

from auto_ext.core.importer import (
    Identity,
    ImportResult,
    ProjectConstants,
    TOOL,
    aggregate_pdk_tokens,
    apply_project_constants,
    import_template,
)
from auto_ext.core.io_utils import backup_if_exists


#: Filename for each tool's imported template (user may rename after init).
INIT_TEMPLATE_NAMES: dict[str, str] = {
    "calibre": "imported.qci.j2",
    "si": "imported.env.j2",
    "quantus": "imported.cmd.j2",
    "jivaro": "imported.xml.j2",
}


FileRole = Literal["template", "project_yaml", "tasks_yaml"]


@dataclass(frozen=True)
class InitInputs:
    """Every knob the wizard / CLI hands to :func:`dry_run`."""

    raw_calibre: Path
    raw_si: Path
    raw_quantus: Path
    raw_jivaro: Path | None
    output_config_dir: Path
    output_templates_dir: Path
    cell_override: str | None = None
    library_override: str | None = None
    layout_view_override: str | None = None
    source_view_override: str | None = None
    out_file_override: str | None = None
    ground_net_override: str | None = None
    force: bool = False


@dataclass(frozen=True)
class FileToWrite:
    """One target file produced by :func:`dry_run`.

    ``will_overwrite`` is captured at preview time; the caller pre-checks
    it against ``force`` before invoking :func:`commit`.
    """

    path: Path
    text: str
    will_overwrite: bool
    role: FileRole


@dataclass(frozen=True)
class InitPreview:
    """What :func:`dry_run` returns. ``conflicts`` non-empty means the
    user must resolve identity disagreements before :func:`commit` is
    called — the caller decides whether to surface them as red text or
    block a Next button."""

    inputs: InitInputs
    results: dict[TOOL, ImportResult]
    merged_identity: Identity
    constants: ProjectConstants
    files: tuple[FileToWrite, ...]
    project_yaml_text: str
    tasks_yaml_text: str
    conflicts: tuple[str, ...] = ()


# ---- public API ------------------------------------------------------------


def cross_validate_identities(
    results: dict[TOOL, ImportResult],
) -> tuple[Identity, list[str]]:
    """Cross-tool identity reconciliation; never raises.

    For each of the 6 identity fields, group distinct values by tool. A
    single distinct value adopts; zero is left as ``None``; two or more
    is appended to ``conflicts`` and the field stays ``None``. Returns
    ``(merged_identity, conflicts)`` so callers (CLI raises ConfigError;
    wizard renders a red banner) can decide the UX.
    """
    merged: dict[str, Optional[str]] = {}
    conflicts: list[str] = []
    for field_name in (
        "cell",
        "library",
        "lvs_layout_view",
        "lvs_source_view",
        "out_file",
        "ground_net",
    ):
        per_value_tools: dict[str, list[str]] = {}
        for tool, res in results.items():
            v = getattr(res.identity, field_name)
            if v is None:
                continue
            per_value_tools.setdefault(v, []).append(tool)
        if not per_value_tools:
            merged[field_name] = None
        elif len(per_value_tools) == 1:
            merged[field_name] = next(iter(per_value_tools))
        else:
            detail = ", ".join(
                f"{v!r}={tools}" for v, tools in sorted(per_value_tools.items())
            )
            conflicts.append(f"{field_name}: {detail}")
            merged[field_name] = None
    return Identity(**merged), conflicts


def build_project_yaml(*, constants: ProjectConstants) -> str:
    """Serialize a ``project.yaml`` filled with the aggregated PDK constants.

    Only keys the current schema accepts are written. Two things the raw
    exports do reveal no longer belong in this file and are emitted as a
    comment block instead of a key:

    * the deck directories (``calibre_lvs_dir`` / ``qrc_deck_dir``) belong to
      the :class:`~auto_ext.model.pdk.PdkProfile`, which ``auto-ext profile
      discover`` drafts from a machine scan;
    * which ``.j2`` renders which stage is the catalog's answer, so the four
      ``templates`` slots have nowhere to go.

    Printing them keeps the information in front of the user instead of
    writing a file the loader would reject.
    """
    data: dict = {}
    if constants.tech_name:
        data["tech_name"] = constants.tech_name

    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    buf = StringIO()
    yaml.dump(data, buf)
    header = [
        "# Generated by auto-ext init-project. Review before first run.",
        "# work_root / verify_root / setup_root / employee_id / layer_map are",
        "# resolved from shell env by default — set them here only to override.",
    ]
    if constants.paths:
        header.append("#")
        header.append("# Deck directories found in the raw exports. They belong on the")
        header.append("# PdkProfile, not here: run `auto-ext profile discover --write`,")
        header.append("# then check these against what it drafted.")
        for key in sorted(constants.paths):
            header.append(f"#   {key}: {constants.paths[key]}")
    return "\n".join(header) + "\n" + buf.getvalue()


def build_tasks_yaml(
    *,
    identity: Identity,
    jivaro_imported: bool,
) -> str:
    """Emit a one-entry ``tasks.yaml`` using the detected identity values."""
    lines: list[str] = [
        "# Generated by auto-ext init-project. Edit the cells below to match",
        "# the extraction batch; list-valued fields (library/cell/",
        "# lvs_layout_view/lvs_source_view) auto-expand to per-cell tasks.",
        "",
    ]
    lines.append(f"- library: {yaml_scalar(identity.library) or 'TODO_LIBRARY'}")
    lines.append(f"  cell: {yaml_scalar(identity.cell) or 'TODO_CELL'}")
    lines.append(
        f"  lvs_layout_view: "
        f"{yaml_scalar(identity.lvs_layout_view) or 'layout'}"
    )
    lines.append(
        f"  lvs_source_view: "
        f"{yaml_scalar(identity.lvs_source_view) or 'schematic'}"
    )
    if identity.ground_net is not None:
        lines.append(f"  ground_net: {yaml_scalar(identity.ground_net)}")
    if identity.out_file is not None:
        lines.append(f"  out_file: {yaml_scalar(identity.out_file)}")
    if jivaro_imported:
        lines.append("  jivaro:")
        lines.append("    enabled: true")
    else:
        lines.append("  jivaro:")
        lines.append("    enabled: false")
    lines.append("")
    return "\n".join(lines)


def dry_run(inputs: InitInputs) -> InitPreview:
    """Read raws, import each tool, render every output text in memory.

    No filesystem writes. Raises :class:`UnicodeDecodeError` (raw not
    utf-8), :class:`OSError` (raw unreadable), and
    :class:`auto_ext.core.importer.ImportError` (raw unparseable) — the
    caller surfaces these to the user. Identity disagreements are NOT
    raised; they land in :attr:`InitPreview.conflicts`.
    """
    overrides = _build_overrides(inputs)

    raw_paths: dict[TOOL, Path] = {
        "calibre": inputs.raw_calibre,
        "si": inputs.raw_si,
        "quantus": inputs.raw_quantus,
    }
    if inputs.raw_jivaro is not None:
        raw_paths["jivaro"] = inputs.raw_jivaro

    results: dict[TOOL, ImportResult] = {}
    for tool, path in raw_paths.items():
        raw_text = path.read_text(encoding="utf-8")
        results[tool] = import_template(
            tool, raw_text, identity_overrides=overrides
        )

    merged_identity, conflicts = cross_validate_identities(results)
    constants = aggregate_pdk_tokens(results)

    files: list[FileToWrite] = []

    # One template per tool. No sidecar: which values in a generated file a
    # user may change is the catalog's answer now, not a per-file manifest's.
    for tool, result in results.items():
        rewritten = apply_project_constants(tool, result.template_body, constants)
        subdir = inputs.output_templates_dir / tool
        tpl_path = subdir / INIT_TEMPLATE_NAMES[tool]
        files.append(
            FileToWrite(
                path=tpl_path,
                text=rewritten,
                will_overwrite=tpl_path.exists(),
                role="template",
            )
        )

    project_yaml_text = build_project_yaml(constants=constants)
    config_yaml = inputs.output_config_dir / "project.yaml"
    files.append(
        FileToWrite(
            path=config_yaml,
            text=project_yaml_text,
            will_overwrite=config_yaml.exists(),
            role="project_yaml",
        )
    )

    tasks_yaml_text = build_tasks_yaml(
        identity=merged_identity,
        jivaro_imported="jivaro" in results,
    )
    tasks_yaml = inputs.output_config_dir / "tasks.yaml"
    files.append(
        FileToWrite(
            path=tasks_yaml,
            text=tasks_yaml_text,
            will_overwrite=tasks_yaml.exists(),
            role="tasks_yaml",
        )
    )

    return InitPreview(
        inputs=inputs,
        results=results,
        merged_identity=merged_identity,
        constants=constants,
        files=tuple(files),
        project_yaml_text=project_yaml_text,
        tasks_yaml_text=tasks_yaml_text,
        conflicts=tuple(conflicts),
    )


def commit(
    preview: InitPreview,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[Path]:
    """Synchronously write every file in ``preview.files``.

    Order matches ``preview.files`` (templates → project.yaml → tasks.yaml).
    Files whose ``will_overwrite`` is true are first
    backed up via :func:`backup_if_exists`. Raises :class:`OSError`
    unmodified on the first write failure — the partial state is left on
    disk for the caller to inspect / clean up. No cancel token, no
    rollback (v1 simplification per Phase 5.7 plan).
    """
    written: list[Path] = []
    inputs = preview.inputs
    inputs.output_config_dir.mkdir(parents=True, exist_ok=True)
    inputs.output_templates_dir.mkdir(parents=True, exist_ok=True)

    for entry in preview.files:
        entry.path.parent.mkdir(parents=True, exist_ok=True)
        if entry.will_overwrite:
            backup_if_exists(entry.path)
        entry.path.write_text(entry.text, encoding="utf-8")
        written.append(entry.path)
        if progress is not None:
            progress(f"wrote {entry.path}")
    return written


# ---- internals -------------------------------------------------------------


def _build_overrides(inputs: InitInputs) -> Identity | None:
    fields = (
        ("cell", inputs.cell_override),
        ("library", inputs.library_override),
        ("lvs_layout_view", inputs.layout_view_override),
        ("lvs_source_view", inputs.source_view_override),
        ("out_file", inputs.out_file_override),
        ("ground_net", inputs.ground_net_override),
    )
    if not any(value is not None for _, value in fields):
        return None
    return Identity(**{name: value for name, value in fields})


def yaml_scalar(value: Optional[str]) -> Optional[str]:
    """Quote ``value`` if it contains any YAML-special character.

    Returns ``None`` unchanged. Single home — both ``build_tasks_yaml``
    and any future emitter share this exact policy. Used by
    :func:`build_tasks_yaml`; do not duplicate elsewhere in the codebase.
    """
    if value is None:
        return None
    if any(c in value for c in ":#{}[]&*!|>'\"%@`,\n\r\t "):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


__all__ = [
    "INIT_TEMPLATE_NAMES",
    "FileRole",
    "FileToWrite",
    "InitInputs",
    "InitPreview",
    "build_project_yaml",
    "build_tasks_yaml",
    "commit",
    "cross_validate_identities",
    "dry_run",
    "yaml_scalar",
]
