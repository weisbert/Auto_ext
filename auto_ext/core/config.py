"""The workspace + DUT inputs the runner needs, and the door the v1 pair hits.

Two shapes reach :func:`auto_ext.core.runner.run_tasks`: a
:class:`ProjectConfig` (where the Cadence work lands) and a list of
:class:`TaskConfig` (which DUTs to run). Since the catalog took over
rendering, that is *all* they carry -- the template slots, the four-layer
``*.manifest.yaml`` knob merge, the ``paths`` vocabulary and the per-task
override stack are gone, and with them every "which of my five override
layers won?" question.

Where the pair comes from
-------------------------
:func:`load_v2_config` is the live path: ``config/workspace.yaml`` +
``config/cells.yaml`` (:mod:`auto_ext.model.workspace`,
:mod:`auto_ext.model.cells`), adapted here into the two runner types. The
adapters are deliberately thin and one-directional; they exist so the runner
did not have to be rewritten around two new model classes in the same round
that removed the old render path.

:func:`load_project` / :func:`load_tasks` still read a ``project.yaml`` +
``tasks.yaml`` pair, but only the reduced form. Any file still carrying a
retired key (``templates``, ``knobs``, ``paths``, ``tech_name_env_vars``,
``exclude``, ``jivaro_overrides``, ``label``, per-task ``dspf_out_path``) is
refused by name with the migration command attached -- see
:data:`RETIRED_PROJECT_KEYS` / :data:`RETIRED_TASK_KEYS` and
:func:`_reject_retired_keys`. The v1 schema itself lives in
:mod:`auto_ext.legacy_v1`, which only :mod:`auto_ext.migrate` reads.

Fields with no runtime reader
-----------------------------
Three groups survive this round without one, listed here rather than
scattered through the class bodies:

* ``work_root`` / ``verify_root`` / ``setup_root`` never had one. They were
  display shadows of ``$WORK_ROOT`` and friends.
* ``tech_name`` / ``layer_map`` had one until the legacy render path was
  deleted. Both now come from :class:`~auto_ext.model.pdk.PdkProfile` via
  :func:`auto_ext.core.render.build_context`, so a value set here changes
  nothing. They are still accepted so a project.yaml that has been reduced by
  hand does not fail on them.
* ``TaskConfig.jivaro`` / ``TaskConfig.continue_on_lvs_fail`` were the legacy
  answers to "is reduction on" and "keep going past an LVS mismatch". Both
  answers now come from the Recipe (``reduction.enabled`` /
  ``policy.continue_on_lvs_fail``), which is why :func:`tasks_from_cells`
  leaves them at their defaults.

All five are on the list for the round that replaces ``ProjectConfig`` with
:class:`~auto_ext.model.workspace.WorkspaceConfig` outright.

List-valued task fields (``library``, ``cell``, ``lvs_layout_view``,
``lvs_source_view``) are still auto-expanded via nested loops in that fixed
order so ``task_id`` assignment is reproducible.
"""

from __future__ import annotations

import logging
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.errors import ConfigError

if TYPE_CHECKING:
    from auto_ext.model.cells import CellBook
    from auto_ext.model.workspace import WorkspaceConfig

logger = logging.getLogger(__name__)


# ---- pydantic models -------------------------------------------------------


class JivaroConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    frequency_limit: float | None = None
    error_max: float | None = None


class ProjectConfig(BaseModel):
    """Where this project's Cadence work lands.

    ``source_path`` and ``raw`` are filled in by :func:`load_project` after
    validation and are excluded from serialization so they do not round-trip
    back into YAML.

    See the module docstring for which fields no longer have a runtime reader.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    #: Display-only; if None, GUI panels will show ``$WORK_ROOT`` from shell.
    work_root: Path | None = None
    #: Display-only; if None, GUI panels will show ``$VERIFY_ROOT`` from shell.
    verify_root: Path | None = None
    #: Display-only; if None, GUI panels will show ``$SETUP_ROOT`` from shell.
    setup_root: Path | None = None

    #: Substituted into template paths like ``/tmpdata/RFIC/rfic_share/<id>/...``.
    #: If None, resolved at render time via ``$USER`` / ``$USERNAME`` / fallback.
    employee_id: str | None = None

    #: Cadence tech library name (e.g. ``HN001``). Superseded by
    #: :attr:`auto_ext.model.pdk.PdkProfile.tech_name`; see the module docstring.
    tech_name: str | None = None

    #: Superseded by :attr:`auto_ext.model.pdk.PdkProfile.layer_map`; see the
    #: module docstring.
    layer_map: Path = Path("${PDK_LAYER_MAP_FILE}")

    #: Env values layered under the profile's own overrides at resolve time.
    env_overrides: dict[str, str] = Field(default_factory=dict)

    #: Per-task extraction output directory. Env vars (``$X`` / ``${X}`` /
    #: ``$env(X)``) are substituted via :func:`resolve_env`, then Python
    #: ``str.format`` substitutes axis-derived keys. Supported keys are
    #: :data:`auto_ext.core.runner._OUTPUT_DIR_FORMAT_KEYS`.
    extraction_output_dir: str = "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    intermediate_dir: str = "${WORK_ROOT2}"
    #: Per-task DSPF output file path. Templated string supporting env vars
    #: (``$X`` / ``${X}`` / ``$env(X)``), path tokens that reference other
    #: resolved fields (``${output_dir}``, ``${intermediate_dir}``,
    #: ``${calibre_lvs_dir}`` and the profile's extra path keys), and Python
    #: ``str.format`` keys (``{cell}``, ``{library}``, ``{task_id}``). The
    #: runner resolves env + path tokens first, then applies ``.format(...)``
    #: so the final value lands in the Jinja context as ``[[dspf_out_path]]``.
    dspf_out_path: str = "${WORK_ROOT2}/{cell}.dspf"

    source_path: Path | None = Field(default=None, exclude=True)
    raw: Any = Field(default=None, exclude=True)


class TaskSpec(BaseModel):
    """Raw ``tasks.yaml`` entry before expansion.

    List-valued fields are allowed on the four identity axes; scalar values
    are accepted and treated as single-element lists during expansion.
    """

    model_config = ConfigDict(extra="forbid")

    library: str | list[str]
    cell: str | list[str]
    lvs_source_view: str | list[str] = "schematic"
    lvs_layout_view: str | list[str]
    ground_net: str = "vss"
    out_file: str | None = None
    jivaro: JivaroConfig = Field(default_factory=JivaroConfig)
    continue_on_lvs_fail: bool = False


class TaskConfig(BaseModel):
    """A fully scalarized task. Immutable once built."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    library: str
    cell: str
    lvs_source_view: str
    lvs_layout_view: str
    ground_net: str
    out_file: str | None
    #: What a UI prints instead of :attr:`task_id`. Filled from
    #: :attr:`~auto_ext.model.cells.CellEntry.display_name`; ``tasks.yaml``
    #: cannot set it (``TaskSpec.label`` is retired), so it is ``None`` on that
    #: path and the record falls back to the key.
    display_name: str | None = None
    jivaro: JivaroConfig
    continue_on_lvs_fail: bool
    spec_index: int
    expansion_index: int


# ---- v1 rejection ----------------------------------------------------------

#: Keys a v1 ``project.yaml`` carried that this schema no longer accepts,
#: mapped to where the setting lives now. Read by :func:`load_project` to turn
#: a raw pydantic ``extra_forbidden`` error into a sentence naming the fix.
RETIRED_PROJECT_KEYS: dict[str, str] = {
    "templates": (
        "the catalog picks the template per render target "
        "(`auto-ext catalog list`)"
    ),
    "knobs": "recipe fields (`auto-ext recipe show <id>`)",
    "paths": "the PdkProfile's deck paths and extra_paths",
    "tech_name_env_vars": "the PdkProfile's tech_name_env_vars",
}

#: Same, for one entry of a v1 ``tasks.yaml``.
RETIRED_TASK_KEYS: dict[str, str] = {
    "templates": "the catalog's render targets",
    "knobs": "recipe fields",
    "label": "cells.yaml `display_name`",
    "exclude": "cells.yaml rows are explicit; use `enabled: false` to skip one",
    "jivaro_overrides": "a second recipe, run separately",
    "dspf_out_path": "workspace.yaml `dspf_out_pattern`",
}

_MIGRATE_HINT = (
    "Run `auto-ext migrate --config-dir {directory} --write` to convert this "
    "project. It writes config/workspace.yaml, config/cells.yaml, "
    "config/profiles/<id>.yaml, config/resources.yaml and recipes/*.yaml, "
    "reports where every field went, and leaves both source files untouched."
)


def _reject_retired_keys(
    data: Any, path: Path, retired: dict[str, str], *, where: str
) -> None:
    """Refuse a v1 file by name before pydantic gets a chance to be cryptic.

    ``extra="forbid"`` would already stop the load, but with a nested
    ``ValidationError`` that says "extra_forbidden" four times and never says
    *migrate*. The user's next action is one command; this makes that the
    thing they read.
    """

    if not isinstance(data, dict):
        return
    found = [key for key in retired if key in data]
    if not found:
        return
    moved = "; ".join(f"`{key}` -> {retired[key]}" for key in found)
    raise ConfigError(
        f"{path}{where} is in the v1 format: it still carries "
        f"{sorted(found)}. Those settings moved out of this file "
        f"({moved}). "
        + _MIGRATE_HINT.format(directory=path.parent)
    )


# ---- loaders ---------------------------------------------------------------


def load_project(path: Path) -> ProjectConfig:
    """Load ``project.yaml`` via ruamel.yaml and validate with pydantic.

    Raises :class:`ConfigError` on any parse or schema failure, and on any
    surviving v1 key with the migration command attached. The returned model
    has ``source_path`` set to ``path.resolve()`` and ``raw`` set to the
    original CommentedMap.
    """

    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at top level, got {type(data).__name__}")

    _reject_retired_keys(data, path, RETIRED_PROJECT_KEYS, where="")

    try:
        project = ProjectConfig.model_validate(_plain(data))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    project.source_path = path.resolve()
    project.raw = data
    logger.info("loaded project.yaml: %s", path)
    return project


def load_tasks(path: Path, project: ProjectConfig | None = None) -> list[TaskConfig]:
    """Load ``tasks.yaml``, expand list-valued fields, apply project defaults.

    The top level may be either a bare list of task entries or a mapping
    with a single ``tasks`` key whose value is the list.

    Raises :class:`ConfigError` on parse or schema failure. Task order is
    preserved (list order in the YAML, then inner expansion order:
    ``library`` -> ``cell`` -> ``lvs_layout_view`` -> ``lvs_source_view``).
    """

    tasks, _raw = load_tasks_with_raw(path, project)
    return tasks


def load_tasks_with_raw(
    path: Path, project: ProjectConfig | None = None
) -> tuple[list[TaskConfig], Any]:
    """Same as :func:`load_tasks` but also returns the raw ruamel tree.

    The second element is the outer YAML structure (``CommentedSeq`` for a
    bare-list file, ``CommentedMap`` for the ``tasks:`` wrapped form), used by
    :func:`apply_tasks_edits` to write spec edits back while preserving
    top-level comments.
    """

    data = _load_yaml(path)

    if data is None:
        raise ConfigError(f"{path}: file is empty")

    entries = _tasks_sequence(data, path)

    if not entries:
        raise ConfigError(f"{path}: tasks list is empty")

    tasks: list[TaskConfig] = []
    for spec_index, entry in enumerate(entries):
        _reject_retired_keys(
            entry, path, RETIRED_TASK_KEYS, where=f" [entry #{spec_index}]"
        )
        try:
            spec = TaskSpec.model_validate(_plain(entry))
        except ValidationError as exc:
            raise ConfigError(f"{path} [entry #{spec_index}]: {exc}") from exc

        tasks.extend(_expand_spec(spec, spec_index, path))

    _warn_on_duplicate_task_ids(tasks)

    logger.info("expanded %d task specs -> %d subtasks", len(entries), len(tasks))
    return tasks, data


def _tasks_sequence(data: Any, path: Path) -> Any:
    if isinstance(data, dict):
        if "tasks" not in data:
            raise ConfigError(f"{path}: mapping at top level must have a 'tasks' key")
        entries = data["tasks"]
    elif isinstance(data, list):
        entries = data
    else:
        raise ConfigError(
            f"{path}: expected a list or mapping at top level, got {type(data).__name__}"
        )
    if not isinstance(entries, list):
        raise ConfigError(f"{path}: 'tasks' must be a list")
    return entries


# ---- workspace.yaml + cells.yaml -> the runner's two inputs -----------------


def load_v2_config(config_dir: Path) -> tuple[ProjectConfig, "CellBook"]:
    """Load ``config/workspace.yaml`` + ``config/cells.yaml`` from ``config_dir``.

    The :class:`~auto_ext.model.workspace.WorkspaceConfig` is adapted straight
    into a :class:`ProjectConfig`; the :class:`~auto_ext.model.cells.CellBook`
    is returned whole, because callers need its ``pdk_profile`` sibling and the
    per-row ``enabled`` flag, and :func:`tasks_from_cells` is a separate step
    so a caller can filter rows first.

    Raises :class:`ConfigError` when either file is missing, so the caller can
    fall back to the v1 pair and produce the migration hint.
    """

    from auto_ext.model.cells import CELLS_FILENAME, load_cells
    from auto_ext.model.workspace import WORKSPACE_FILENAME, load_workspace

    workspace_path = config_dir / WORKSPACE_FILENAME
    cells_path = config_dir / CELLS_FILENAME
    for candidate in (workspace_path, cells_path):
        if not candidate.is_file():
            raise ConfigError(f"config file not found: {candidate}")

    workspace = load_workspace(workspace_path)
    book = load_cells(cells_path)
    project = project_from_workspace(workspace)
    project.source_path = workspace_path.resolve()
    return project, book


def project_from_workspace(workspace: "WorkspaceConfig") -> ProjectConfig:
    """Adapt a :class:`~auto_ext.model.workspace.WorkspaceConfig` for the runner.

    Only the three path patterns cross over. ``layer_map`` / ``tech_name`` /
    ``env_overrides`` deliberately do not: on the catalog render path those
    come from the :class:`~auto_ext.model.pdk.PdkProfile`, and filling them in
    here from a second source is how two sources of truth start.
    """

    return ProjectConfig(
        extraction_output_dir=workspace.output_dir_pattern,
        intermediate_dir=workspace.intermediate_dir,
        dspf_out_path=workspace.dspf_out_pattern,
    )


def tasks_from_cells(book: "CellBook", *, include_disabled: bool = False) -> list[TaskConfig]:
    """Turn a :class:`~auto_ext.model.cells.CellBook` into the runner's task list.

    ``enabled: false`` rows are dropped unless ``include_disabled`` -- that
    flag is the "temporarily not this one" half of the old ``exclude``, so
    honouring it here is what makes the checkbox mean anything.

    ``task_id`` is :attr:`~auto_ext.model.cells.CellEntry.key`, which is
    spelled exactly like the legacy ``task_id``, so a migrated run history
    still matches up by DUT.

    ``jivaro`` and ``continue_on_lvs_fail`` are left at their defaults: the
    Recipe owns both answers now (see the module docstring).
    """

    rows = [row for row in book.cells if include_disabled or row.enabled]
    return [
        TaskConfig(
            task_id=row.key,
            library=row.library,
            cell=row.cell,
            lvs_source_view=row.source_view,
            lvs_layout_view=row.layout_view,
            ground_net=row.ground_net,
            out_file=row.out_file,
            display_name=row.display_name,
            jivaro=JivaroConfig(),
            continue_on_lvs_fail=False,
            spec_index=index,
            expansion_index=0,
        )
        for index, row in enumerate(rows)
    ]


# ---- internals -------------------------------------------------------------


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    yaml = YAML(typ="rt")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.load(fh)
    except YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error: {exc}") from exc


def _plain(obj: Any) -> Any:
    """Convert a ruamel CommentedMap/CommentedSeq tree to plain dicts/lists.

    Pydantic accepts the commented variants (they subclass dict/list), but
    dumping to plain containers makes debug prints and equality checks
    behave naturally in tests.
    """

    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def _scalarize(value: str | list[str], field: str, spec_index: int, source: Path) -> list[str]:
    if isinstance(value, list):
        if not value:
            raise ConfigError(
                f"{source} [entry #{spec_index}]: field '{field}' is an empty list"
            )
        return list(value)
    return [value]


def _expand_spec(
    spec: TaskSpec,
    spec_index: int,
    source: Path,
) -> list[TaskConfig]:
    libs = _scalarize(spec.library, "library", spec_index, source)
    cells = _scalarize(spec.cell, "cell", spec_index, source)
    layouts = _scalarize(spec.lvs_layout_view, "lvs_layout_view", spec_index, source)
    sources = _scalarize(spec.lvs_source_view, "lvs_source_view", spec_index, source)

    result: list[TaskConfig] = []
    expansion_index = 0
    for library in libs:
        for cell in cells:
            for layout in layouts:
                for src in sources:
                    result.append(
                        TaskConfig(
                            task_id=f"{library}__{cell}__{layout}__{src}",
                            library=library,
                            cell=cell,
                            lvs_source_view=src,
                            lvs_layout_view=layout,
                            ground_net=spec.ground_net,
                            out_file=spec.out_file,
                            jivaro=spec.jivaro,
                            continue_on_lvs_fail=spec.continue_on_lvs_fail,
                            spec_index=spec_index,
                            expansion_index=expansion_index,
                        )
                    )
                    expansion_index += 1
    return result


def _warn_on_duplicate_task_ids(tasks: list[TaskConfig]) -> None:
    counts = Counter(t.task_id for t in tasks)
    dupes = [tid for tid, n in counts.items() if n > 1]
    if dupes:
        logger.warning("duplicate task_id(s) after expansion: %s", sorted(dupes))


def dump_project_yaml(project: ProjectConfig) -> str:
    """Serialize a :class:`ProjectConfig`'s original comment tree back to YAML.

    Available only when ``project.raw`` is present (set by :func:`load_project`).
    """

    if project.raw is None:
        raise ConfigError("project has no raw CommentedMap; was it loaded via load_project?")
    yaml = YAML(typ="rt")
    buf = StringIO()
    yaml.dump(project.raw, buf)
    return buf.getvalue()


# ---- GUI write-back --------------------------------------------------------


_EDIT_SCALAR_KEYS = frozenset(
    {
        "work_root",
        "verify_root",
        "setup_root",
        "employee_id",
        "tech_name",
        "layer_map",
        "extraction_output_dir",
        "intermediate_dir",
        "dspf_out_path",
    }
)

# parent → allowed children, or None for "arbitrary child keys"
_EDIT_NESTED_KEYS: dict[str, frozenset[str] | None] = {
    "env_overrides": None,  # env var names are arbitrary
}


def apply_project_edits(raw: Any, edits: dict[str, Any]) -> None:
    """Mutate a ruamel ``CommentedMap`` in place per ``edits``.

    Keys are flat (``tech_name``) or dotted for the one remaining nested
    mapping (``env_overrides.FOO``). A value of ``None`` removes the key; any
    other value overwrites. Comments attached to existing keys survive;
    newly-introduced keys appear without leading comments (expected — the dump
    is user-driven).

    Deleting the last child of a nested mapping also prunes the parent, so
    ``env_overrides: {}`` does not linger after every override is cleared.

    Raises :class:`ConfigError` on unknown keys to catch typos before they
    disappear silently into the YAML.
    """

    if raw is None:
        raise ConfigError("apply_project_edits: raw CommentedMap is None")

    for key, value in edits.items():
        parts = key.split(".")
        if len(parts) == 1:
            if key not in _EDIT_SCALAR_KEYS:
                raise ConfigError(f"apply_project_edits: unknown key {key!r}")
            if value is None:
                raw.pop(key, None)
            else:
                raw[key] = value
        elif len(parts) == 2:
            parent, child = parts
            if parent not in _EDIT_NESTED_KEYS:
                raise ConfigError(f"apply_project_edits: unknown key {key!r}")
            allowed = _EDIT_NESTED_KEYS[parent]
            if allowed is not None and child not in allowed:
                raise ConfigError(
                    f"apply_project_edits: unknown nested key {key!r} "
                    f"(allowed under {parent!r}: {sorted(allowed)})"
                )
            _apply_nested_edit(raw, parent, child, value)
        else:
            raise ConfigError(
                f"apply_project_edits: too many dotted segments in {key!r} "
                "(max 2: env_overrides.<VAR>)"
            )


def _apply_nested_edit(raw: Any, parent: str, child: str, value: Any) -> None:
    if value is None:
        if parent in raw and isinstance(raw[parent], dict) and child in raw[parent]:
            del raw[parent][child]
            if not raw[parent]:
                del raw[parent]
        return
    if parent not in raw or not isinstance(raw[parent], dict):
        raw[parent] = {}
    raw[parent][child] = value


def dump_tasks_yaml(raw: Any) -> str:
    """Serialize a tasks.yaml raw tree back to YAML text.

    ``raw`` is the tree returned by :func:`load_tasks_with_raw` (either a
    ruamel ``CommentedSeq`` or a ``CommentedMap`` wrapping a ``tasks:`` key).
    Symmetric with :func:`dump_project_yaml`.
    """

    if raw is None:
        raise ConfigError("dump_tasks_yaml: raw is None")
    yaml = YAML(typ="rt")
    buf = StringIO()
    yaml.dump(raw, buf)
    return buf.getvalue()


def apply_tasks_edits(raw: Any, specs: list[dict[str, Any]]) -> None:
    """Replace the tasks sequence in ``raw`` with ``specs`` at spec granularity.

    ``raw`` is the tree from :func:`load_tasks_with_raw`; it is mutated in
    place. ``specs`` is a list of fully-formed TaskSpec dicts (validated
    upstream by constructing ``TaskSpec(**spec)`` before calling).

    Semantics:
    - overlapping indexes: ``seq[i]`` is mutated in place — keys not in
      ``specs[i]`` are deleted and remaining keys are set/updated. This
      preserves ruamel's CommentedMap container, including any end-of-
      sequence trailing comment that ruamel attaches to the last item's
      CommentedMap. Wholesale replacement (``seq[i] = plain_dict``) would
      sever that link and drop the trailing comment.
    - ``i >= len(seq)``: ``specs[i]`` appended.
    - ``i >= len(specs)``: trailing entries in ``seq`` are popped.

    Raises :class:`ConfigError` if ``specs`` is empty (tasks.yaml cannot
    round-trip to an empty file — the loader rejects it on next read).
    """

    if raw is None:
        raise ConfigError("apply_tasks_edits: raw is None")
    if not specs:
        raise ConfigError("apply_tasks_edits: specs list is empty")

    if isinstance(raw, dict):
        if "tasks" not in raw:
            raise ConfigError("apply_tasks_edits: raw mapping has no 'tasks' key")
        seq = raw["tasks"]
    elif isinstance(raw, list):
        seq = raw
    else:
        raise ConfigError(
            f"apply_tasks_edits: unsupported raw type {type(raw).__name__}"
        )

    for i, spec in enumerate(specs):
        if i < len(seq):
            existing = seq[i]
            if isinstance(existing, dict):
                # In-place mutation preserves ruamel CommentedMap.ca data,
                # including the trailing comment of the whole sequence
                # (which ruamel attaches to the last item's CommentedMap).
                for k in list(existing.keys()):
                    if k not in spec:
                        del existing[k]
                for k, v in spec.items():
                    existing[k] = v
            else:
                seq[i] = spec
        else:
            seq.append(spec)
    while len(seq) > len(specs):
        seq.pop()
