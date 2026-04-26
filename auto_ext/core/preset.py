"""Preset library for the Phase 5.6 toggle editor.

A preset captures one toggle (the two raws + the wrapped snippet + a
``meta.yaml`` describing it) under ``templates/presets/<slug>/``. Used
for two flows:

1. After a successful diff in the editor, the user can save the toggle
   as a reusable preset.
2. The picker dialog lists existing presets and applies one to a
   different (but structurally similar) template by anchoring on
   surrounding context lines from the original on-side raw.

Pure Python — no Qt deps. Round-trip via ruamel preserves comments and
formatting on hand-edited ``meta.yaml`` files.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

import auto_ext as _auto_ext
from auto_ext.core.diff_template import DiffHunk, ToggleResult
from auto_ext.core.errors import ConfigError

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9_-]+$")
_META_FILE = "meta.yaml"
_ON_FILE = "on.txt"
_OFF_FILE = "off.txt"
_SNIPPET_FILE = "snippet.j2"

_REQUIRED_FILES: tuple[str, ...] = (_META_FILE, _ON_FILE, _OFF_FILE, _SNIPPET_FILE)


# ---- data classes ----------------------------------------------------------


@dataclass(frozen=True)
class PresetHunk:
    """One persisted hunk + its anchor context for re-application.

    ``anchor_before`` is the on-side line just before this hunk in the
    source raw (``None`` if the hunk starts at file head).
    ``anchor_after`` is the line just after (``None`` at file tail).
    """

    on_lines: tuple[str, ...]
    off_lines: tuple[str, ...]
    anchor_before: str | None
    anchor_after: str | None


@dataclass(frozen=True)
class Preset:
    """A loaded toggle preset."""

    slug: str
    meta: dict[str, Any]
    on_text: str
    off_text: str
    snippet: str
    hunks: tuple[PresetHunk, ...]

    @property
    def name(self) -> str:
        return str(self.meta.get("name", self.slug))

    @property
    def description(self) -> str:
        return str(self.meta.get("description", ""))

    @property
    def applicable_tool(self) -> str | None:
        v = self.meta.get("applicable_tool")
        if v in (None, "", "any"):
            return None
        return str(v)

    @property
    def default(self) -> bool:
        return bool(self.meta.get("default", True))


@dataclass
class PresetApplyWarning:
    hunk_index: int
    message: str


# ---- public API ------------------------------------------------------------


def save_preset(
    toggle: ToggleResult,
    slug: str,
    *,
    presets_dir: Path,
    description: str = "",
    applicable_tool: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Persist a :class:`ToggleResult` to ``<presets_dir>/<slug>/``.

    Writes 4 files via a tmp dir + atomic rename so a failure mid-write
    leaves no partial preset on disk. ``slug`` must match
    ``[a-z0-9_-]+``. ``applicable_tool`` is one of ``calibre`` / ``si``
    / ``quantus`` / ``jivaro``, or ``None`` for "any tool".
    """
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"preset slug {slug!r} must match [a-z0-9_-]+ (lowercase, "
            f"no spaces or dots)"
        )
    target = presets_dir / slug
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"preset directory already exists: {target} (pass overwrite=True)"
        )

    tmp_target = presets_dir / f"{slug}.tmp"
    if tmp_target.exists():
        shutil.rmtree(tmp_target)
    tmp_target.mkdir(parents=True)

    on_lines = toggle.on_text.splitlines(keepends=True)
    hunks_meta = []
    for h in toggle.hunks:
        anchor_before = (
            on_lines[h.on_start - 1] if h.on_start > 0 else None
        )
        anchor_after = (
            on_lines[h.on_end] if h.on_end < len(on_lines) else None
        )
        hunks_meta.append(
            {
                "on_start": h.on_start,
                "on_end": h.on_end,
                "off_start": h.off_start,
                "off_end": h.off_end,
                "anchor_before": anchor_before,
                "anchor_after": anchor_after,
                "on_lines": list(h.on_lines),
                "off_lines": list(h.off_lines),
            }
        )

    meta: dict[str, Any] = {
        "name": toggle.toggle_name,
        "description": description,
        "applicable_tool": applicable_tool,
        "default": toggle.on_value,
        "on_value": toggle.on_value,
        "created_by": os.environ.get("USER")
            or os.environ.get("USERNAME")
            or "unknown",
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "auto_ext_version": getattr(_auto_ext, "__version__", "unknown"),
        "hunks": hunks_meta,
    }

    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    with (tmp_target / _META_FILE).open("w", encoding="utf-8") as fh:
        yaml.dump(meta, fh)
    (tmp_target / _ON_FILE).write_text(toggle.on_text, encoding="utf-8")
    (tmp_target / _OFF_FILE).write_text(toggle.off_text, encoding="utf-8")
    # snippet = just the wrapped hunks, joined; useful for human review.
    snippet_parts: list[str] = []
    for h in toggle.hunks:
        snippet_parts.append(_render_hunk_snippet(h, toggle.toggle_name))
    (tmp_target / _SNIPPET_FILE).write_text(
        "\n".join(snippet_parts) + ("\n" if snippet_parts else ""),
        encoding="utf-8",
    )

    if target.exists():
        shutil.rmtree(target)
    tmp_target.rename(target)
    return target


def load_preset(slug: str, *, presets_dir: Path) -> Preset:
    """Load a preset from ``<presets_dir>/<slug>/``.

    Raises :class:`FileNotFoundError` if the directory or any required
    file is missing; :class:`ConfigError` on malformed ``meta.yaml``.
    """
    target = presets_dir / slug
    if not target.is_dir():
        raise FileNotFoundError(f"preset directory not found: {target}")
    for fname in _REQUIRED_FILES:
        if not (target / fname).is_file():
            raise FileNotFoundError(
                f"preset {slug!r} missing required file: {fname}"
            )

    yaml = YAML(typ="rt")
    try:
        with (target / _META_FILE).open("r", encoding="utf-8") as fh:
            meta_raw = yaml.load(fh)
    except YAMLError as exc:
        raise ConfigError(f"{target / _META_FILE}: YAML parse error: {exc}") from exc
    if not isinstance(meta_raw, dict):
        raise ConfigError(
            f"{target / _META_FILE}: top-level must be a mapping"
        )
    meta = _plain(meta_raw)

    on_text = (target / _ON_FILE).read_text(encoding="utf-8")
    off_text = (target / _OFF_FILE).read_text(encoding="utf-8")
    snippet = (target / _SNIPPET_FILE).read_text(encoding="utf-8")

    hunks: list[PresetHunk] = []
    for h_meta in meta.get("hunks") or []:
        hunks.append(
            PresetHunk(
                on_lines=tuple(h_meta.get("on_lines") or ()),
                off_lines=tuple(h_meta.get("off_lines") or ()),
                anchor_before=h_meta.get("anchor_before"),
                anchor_after=h_meta.get("anchor_after"),
            )
        )

    return Preset(
        slug=slug,
        meta=meta,
        on_text=on_text,
        off_text=off_text,
        snippet=snippet,
        hunks=tuple(hunks),
    )


def list_presets(presets_dir: Path) -> list[Preset]:
    """Return all valid presets under ``presets_dir`` sorted by slug.

    Skips (with a logger warning) any preset directory that fails to
    load — never raises so the picker UI can still render.
    """
    if not presets_dir.is_dir():
        return []
    out: list[Preset] = []
    for child in sorted(presets_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.endswith(".tmp"):
            continue
        try:
            out.append(load_preset(child.name, presets_dir=presets_dir))
        except (FileNotFoundError, ConfigError) as exc:
            logger.warning(
                "list_presets: skipping %s: %s", child.name, exc
            )
    return out


def apply_preset(
    preset: Preset,
    template_text: str,
) -> tuple[str, list[PresetApplyWarning]]:
    """Splice ``preset``'s ``[% if %]`` blocks into ``template_text``.

    For each persisted hunk: locate ``anchor_before`` followed by the
    on-side block followed by ``anchor_after`` in ``template_text``.
    Refuses (raises ``ValueError``) if any anchor is missing or
    ambiguous. Strict by design — anchor mismatches signal that the
    target template's structure no longer matches the preset's source.
    """
    template_lines = template_text.splitlines(keepends=True)
    warnings: list[PresetApplyWarning] = []
    name = preset.name

    # Build splice list bottom-up so earlier indices stay valid.
    splices: list[tuple[int, int, str]] = []
    for idx, hunk in enumerate(preset.hunks):
        start, end = _locate_preset_hunk(template_lines, hunk, idx)
        wrap = _wrap_preset_hunk(hunk, name)
        splices.append((start, end, wrap))
    for start, end, wrap in sorted(splices, key=lambda t: -t[0]):
        template_lines[start:end] = [wrap]
    return "".join(template_lines), warnings


# ---- internals -------------------------------------------------------------


def _plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def _render_hunk_snippet(hunk: DiffHunk, toggle_name: str) -> str:
    on_body = "".join(hunk.on_lines)
    off_body = "".join(hunk.off_lines)
    if hunk.on_lines and hunk.off_lines:
        return (
            f"[% if {toggle_name} %]"
            f"{on_body}"
            f"[% else %]"
            f"{off_body}"
            f"[% endif %]"
        )
    if hunk.on_lines:
        return f"[% if {toggle_name} %]{on_body}[% endif %]"
    return f"[% if not {toggle_name} %]{off_body}[% endif %]"


def _wrap_preset_hunk(hunk: PresetHunk, toggle_name: str) -> str:
    on_body = "".join(hunk.on_lines)
    off_body = "".join(hunk.off_lines)
    if hunk.on_lines and hunk.off_lines:
        return (
            f"[% if {toggle_name} %]"
            f"{on_body}"
            f"[% else %]"
            f"{off_body}"
            f"[% endif %]"
        )
    if hunk.on_lines:
        return f"[% if {toggle_name} %]{on_body}[% endif %]"
    return f"[% if not {toggle_name} %]{off_body}[% endif %]"


def _locate_preset_hunk(
    template_lines: list[str], hunk: PresetHunk, idx: int
) -> tuple[int, int]:
    """Find the (start, end) line range for ``hunk`` in ``template_lines``.

    Strategy:
    - With non-empty ``on_lines``: search for the verbatim sequence in
      ``template_lines``. Filter matches by anchor_before / anchor_after
      when those fields are present. Refuse on 0 or >1 final matches.
    - With empty ``on_lines`` (pure insertion off-side): need both
      anchors to pinpoint the insertion gap. Search for
      ``[anchor_before, anchor_after]`` as a 2-line window; insert
      between them.
    """
    if hunk.on_lines:
        needle = list(hunk.on_lines)
        n = len(needle)
        candidates: list[int] = []
        for start in range(len(template_lines) - n + 1):
            if template_lines[start:start + n] == needle:
                candidates.append(start)
        # Filter on anchor_before.
        if hunk.anchor_before is not None:
            candidates = [
                s for s in candidates
                if s > 0 and template_lines[s - 1] == hunk.anchor_before
            ]
        # Filter on anchor_after.
        if hunk.anchor_after is not None:
            candidates = [
                s for s in candidates
                if s + n < len(template_lines)
                and template_lines[s + n] == hunk.anchor_after
            ]
        if not candidates:
            raise ValueError(
                f"preset hunk {idx}: anchor lost — on-side block not found "
                f"with required surrounding context"
            )
        if len(candidates) > 1:
            raise ValueError(
                f"preset hunk {idx}: anchor ambiguous — on-side block + "
                f"context match in {len(candidates)} places"
            )
        s = candidates[0]
        return s, s + n

    # Pure insertion: rely on the (anchor_before, anchor_after) pair.
    if hunk.anchor_before is None or hunk.anchor_after is None:
        raise ValueError(
            f"preset hunk {idx}: pure-insertion hunk lacks both anchors; "
            f"cannot place"
        )
    candidates = []
    for start in range(len(template_lines) - 1):
        if (template_lines[start] == hunk.anchor_before
                and template_lines[start + 1] == hunk.anchor_after):
            candidates.append(start + 1)
    if not candidates:
        raise ValueError(
            f"preset hunk {idx}: anchor lost — anchor_before/after pair "
            f"not adjacent in template"
        )
    if len(candidates) > 1:
        raise ValueError(
            f"preset hunk {idx}: anchor ambiguous — pair appears "
            f"{len(candidates)} times"
        )
    return candidates[0], candidates[0]
