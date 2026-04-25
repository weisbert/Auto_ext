"""Pure-Python helpers for the Templates tab.

Lives outside :mod:`auto_ext.ui.tabs` so the discovery + classification
rules can be unit-tested without spinning up a QApplication. The tab
imports these and renders them; the rules themselves know nothing about
Qt.

Two responsibilities:

1. **Template discovery** — :func:`collect_template_entries` walks the
   four ``project.templates.<tool>`` slots plus any extra ``*.j2`` under
   ``<auto_ext_root>/templates/`` so the tab can show both bound and
   unused templates in one list.

2. **Placeholder classification** — :func:`env_var_status`,
   :func:`jinja_variable_status`, :func:`literal_placeholder_status`,
   and :func:`user_defined_status` map a placeholder name (in one of the
   four :class:`PlaceholderInventory` buckets) to a status string used
   by the inventory viewer to colour the row. Identity-key membership
   is sourced from :data:`auto_ext.core.manifest._IDENTITY_KEYS` so the
   classification mirrors what the runner will accept at render time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from auto_ext.core.config import ProjectConfig
from auto_ext.core.env import EnvResolution
from auto_ext.core.manifest import TemplateManifest, _IDENTITY_KEYS

#: One of the four tool slots in ``project.templates``. ``None`` for an
#: unbound ``.j2`` discovered under ``templates/`` but not currently
#: referenced by the active project.
TemplateTool = Literal["si", "calibre", "quantus", "jivaro"]

#: Placeholder colour buckets for the inventory viewer.
#: - ``ok``: resolved cleanly (shell env, identity binding, declared knob).
#: - ``override``: resolved via ``project.env_overrides`` (still works,
#:   but flag it so the user knows they're deviating from the shell).
#: - ``missing``: not satisfiable; runner will raise at render time.
#: - ``info``: informational only — no implicit binding mechanism, no
#:   verdict (literal placeholders + jivaro ``user_defined_*`` get this).
PlaceholderStatus = Literal["ok", "override", "missing", "info"]


@dataclass(frozen=True)
class TemplateEntry:
    """One row in the Templates tab's left-pane list."""

    #: Tool slot if this template is currently bound by the project.
    tool: TemplateTool | None
    #: Path as recorded in ``project.templates`` (may be relative to
    #: workarea) for bound entries; absolute for discovered-only entries.
    path: Path
    #: True iff ``project.templates.<tool>`` references this path.
    in_project: bool


def collect_template_entries(
    project: ProjectConfig | None,
    auto_ext_root: Path | None,
    workarea: Path | None = None,
) -> list[TemplateEntry]:
    """Return the list of templates the tab should display.

    Order: the four bound slots in fixed tool order
    (``si, calibre, quantus, jivaro``), then any extra ``*.j2`` under
    ``<auto_ext_root>/templates/`` not already referenced, sorted by
    path. Bound entries always appear first so the user sees the active
    project's wiring at the top of the list.

    Discovery walks ``<auto_ext_root>/templates/**/*.j2`` recursively.
    Templates whose project path resolves outside ``auto_ext_root``
    (absolute path or relative-to-workarea pointing elsewhere) still
    appear; they just don't dedupe against discovery walks.
    """

    entries: list[TemplateEntry] = []
    bound_paths: set[Path] = set()

    if project is not None:
        for tool in ("si", "calibre", "quantus", "jivaro"):
            p = getattr(project.templates, tool, None)
            if p is None:
                continue
            resolved = _resolve_for_dedup(p, workarea)
            entries.append(TemplateEntry(tool=tool, path=Path(p), in_project=True))
            if resolved is not None:
                bound_paths.add(resolved)

    if auto_ext_root is not None:
        templates_dir = auto_ext_root / "templates"
        if templates_dir.is_dir():
            for j2 in sorted(templates_dir.rglob("*.j2")):
                resolved = j2.resolve()
                if resolved in bound_paths:
                    continue
                entries.append(TemplateEntry(tool=None, path=j2, in_project=False))

    return entries


def _resolve_for_dedup(path: Path, workarea: Path | None) -> Path | None:
    """Best-effort absolute resolution for deduping bound vs. discovered.

    Returns ``None`` if the path doesn't exist on disk so the dedup set
    never collapses two unrelated relative paths just because their
    string forms differ.
    """
    candidate = path if path.is_absolute() else (
        (workarea / path) if workarea is not None else path
    )
    try:
        if candidate.exists():
            return candidate.resolve()
    except OSError:
        return None
    return None


# ---- placeholder classification --------------------------------------------


def env_var_status(name: str, resolution: EnvResolution) -> PlaceholderStatus:
    """Map an env var's :class:`EnvResolution` source to a status bucket."""
    src = resolution.sources.get(name)
    if src == "shell":
        return "ok"
    if src == "override":
        return "override"
    return "missing"


def literal_placeholder_status(_name: str) -> PlaceholderStatus:
    """Always ``info`` — literal ``__FOO__`` placeholders have no implicit
    binding mechanism in 5.5; they're a Phase 5.6 (diff editor) concern.
    Surfacing them helps the user spot un-parameterised legacy templates.
    """
    return "info"


def user_defined_status(_name: str) -> PlaceholderStatus:
    """Always ``info`` — jivaro XML's ``user_defined_*`` are documentation
    placeholders only; the runner does not bind them.
    """
    return "info"


def jinja_variable_status(
    name: str,
    manifest: TemplateManifest | None,
    identity_keys: frozenset[str] = _IDENTITY_KEYS,
) -> PlaceholderStatus:
    """Classify a Jinja variable as ``ok`` (identity or declared knob)
    or ``missing`` (StrictUndefined would fire at render time).
    """
    if name in identity_keys:
        return "ok"
    if manifest is not None and name in manifest.knobs:
        return "ok"
    return "missing"
