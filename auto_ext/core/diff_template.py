"""Diff-mode template engine for the Phase 5.6 toggle editor.

Given two raw EDA exports (an "on" variant and an "off" variant of the
same conceptual config) build a single Jinja-wrapped ``.j2`` template
whose render is byte-equivalent to either side based on a single
boolean knob. Pure Python; no Qt deps.

Critical: this codebase customises Jinja delimiters to ``[% %]`` /
``[[ ]]`` (see :func:`auto_ext.core.template._make_jinja_env` for why —
Calibre ``.qci`` uses literal Tcl ``{{...}}``). Every wrap emitted here
uses ``[% if toggle %]…[% else %]…[% endif %]`` — NOT the default
``{% %}`` braces.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Iterable

from auto_ext.core.template import _make_jinja_env

# Names a toggle is not allowed to take. Keeps the name out of Jinja
# keyword space and the runner's identity namespace.
_JINJA_KEYWORDS: frozenset[str] = frozenset(
    {"if", "elif", "else", "endif", "for", "endfor", "true", "false",
     "and", "or", "not", "in", "is", "none", "set"}
)

# Mirrors :data:`auto_ext.core.manifest._IDENTITY_KEYS` without importing
# (avoid a circular dep — manifest imports nothing from here today and
# we want to keep it that way).
_IDENTITY_NAMES: frozenset[str] = frozenset(
    {
        "library", "cell", "lvs_layout_view", "lvs_source_view",
        "ground_net", "out_file", "task_id", "output_dir",
        "intermediate_dir", "layer_map", "employee_id",
        "jivaro_frequency_limit", "jivaro_error_max", "tech_name",
        "pdk_subdir", "project_subdir", "lvs_runset_version",
        "qrc_runset_version",
    }
)

_TOGGLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Threshold above which compute_toggle attaches a LargeDiffWarning.
_LARGE_DIFF_THRESHOLD = 0.5

# Lexer tokens for [% if/else/elif/endif %] block scanning.
_RE_IF = re.compile(r"\[%\s*if\s+([a-z_][a-z0-9_]*)\s*%\]")
_RE_ENDIF = re.compile(r"\[%\s*endif\s*%\]")
_RE_ELSE_OR_ELIF = re.compile(r"\[%\s*(?:else|elif\b)")


# ---- data classes ----------------------------------------------------------


@dataclass(frozen=True)
class DiffHunk:
    """One contiguous region where on_text and off_text differ.

    Lines are 0-indexed half-open ranges ``[start, end)``. ``on_lines``
    / ``off_lines`` are the literal source lines (with trailing newlines
    preserved by ``splitlines(keepends=True)``).
    """

    on_start: int
    on_end: int
    off_start: int
    off_end: int
    on_lines: tuple[str, ...]
    off_lines: tuple[str, ...]


@dataclass(frozen=True)
class ToggleWarning:
    """Base class for non-fatal diagnostics attached to a ToggleResult."""

    message: str


@dataclass(frozen=True)
class LargeDiffWarning(ToggleWarning):
    """Emitted when more than ``_LARGE_DIFF_THRESHOLD`` of the on-side
    lines were classified as changed. Common cause: re-exporting from
    an EDA GUI that silently reorders fields."""

    change_ratio: float


@dataclass(frozen=True)
class ToggleResult:
    """Output of :func:`compute_toggle`: enough to render the new
    template, save a preset, and surface diagnostics in the dialog.
    """

    toggle_name: str
    on_value: bool
    off_value: bool
    merged_text: str
    base_text: str
    hunks: tuple[DiffHunk, ...]
    on_text: str
    off_text: str
    warnings: tuple[ToggleWarning, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OverlapError(Exception):
    """Raised when an existing ``[% if %]`` block conflicts with a new
    hunk. Fields make it easy for the dialog to render both ranges.
    """

    hunk: DiffHunk
    existing_block_start_line: int
    existing_block_end_line: int
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


# ---- public API ------------------------------------------------------------


def compute_toggle(
    on_text: str,
    off_text: str,
    toggle_name: str,
    *,
    on_value: bool = True,
    merge_gap: int = 0,
) -> ToggleResult:
    """Build a Jinja-wrapped template from two raw exports.

    Algorithm:
      1. Validate ``toggle_name`` (ascii ``[a-z][a-z0-9_]*``, not a Jinja
         keyword, not an identity name).
      2. Split both texts on lines (``splitlines(keepends=True)``).
      3. Run ``difflib.SequenceMatcher.get_opcodes()`` on the line lists.
      4. Convert non-equal opcodes into :class:`DiffHunk` instances;
         ``equal`` opcodes are passthrough.
      5. Merge adjacent hunks where the gap is ``<= merge_gap`` (default 0).
      6. Walk the on-side line list emitting equal regions verbatim and
         hunks wrapped in ``[% if toggle %]…[% else %]…[% endif %]``
         (with the deletion/insertion forms when one side is empty).
      7. If both texts are identical, raise ``ValueError``.
      8. If every hunk's lines differ only in trailing whitespace,
         raise ``ValueError`` (locked decision §G.5).
      9. Attach :class:`LargeDiffWarning` when the changed-line ratio
         exceeds 50%% (locked decision §G.5b).

    Raises:
        ValueError: identical inputs, invalid toggle name,
            whitespace-only diff.
    """

    _validate_toggle_name(toggle_name)
    if on_text == off_text:
        raise ValueError("the two raws are identical; nothing to toggle")

    on_lines = on_text.splitlines(keepends=True)
    off_lines = off_text.splitlines(keepends=True)

    sm = difflib.SequenceMatcher(a=on_lines, b=off_lines, autojunk=False)
    raw_hunks: list[DiffHunk] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        raw_hunks.append(
            DiffHunk(
                on_start=i1,
                on_end=i2,
                off_start=j1,
                off_end=j2,
                on_lines=tuple(on_lines[i1:i2]),
                off_lines=tuple(off_lines[j1:j2]),
            )
        )
    if not raw_hunks:  # pragma: no cover - guarded by identity check
        raise ValueError("the two raws are identical; nothing to toggle")

    merged = _merge_adjacent(raw_hunks, merge_gap=merge_gap)

    if _all_hunks_whitespace_only(merged):
        raise ValueError("toggle differs only in whitespace; refusing to wrap")

    merged_text = _splice_hunks(on_lines, merged, toggle_name)

    warnings: list[ToggleWarning] = []
    if on_lines:
        changed = sum(len(h.on_lines) + len(h.off_lines) for h in merged)
        ratio = changed / max(1, 2 * len(on_lines))
        if ratio > _LARGE_DIFF_THRESHOLD:
            warnings.append(
                LargeDiffWarning(
                    message=(
                        f"large diff: {ratio:.0%} of lines changed. Common "
                        f"cause: PDK mismatch or silent field reordering on "
                        f"re-export. Confirm both raws are the on/off of the "
                        f"same logical config."
                    ),
                    change_ratio=ratio,
                )
            )

    return ToggleResult(
        toggle_name=toggle_name,
        on_value=on_value,
        off_value=not on_value,
        merged_text=merged_text,
        base_text=on_text,
        hunks=tuple(merged),
        on_text=on_text,
        off_text=off_text,
        warnings=tuple(warnings),
    )


def apply_toggle_to_template(
    template_text: str,
    toggle: ToggleResult,
    *,
    allow_existing_toggles: bool = True,
) -> str:
    """Apply a freshly-computed toggle to an existing ``.j2`` template.

    For a clean template (==``toggle.on_text``) this returns
    ``toggle.merged_text``. For a template that already contains
    ``[% if %]`` blocks, this anchors each hunk by searching for its
    on-side lines verbatim, then splices the wrap at the anchored
    position. Refuses on overlap with an existing block (per the relaxed
    decision-2-vs-4 resolution).

    Raises:
        OverlapError: hunks overlap an existing ``[% if %]`` block, or
            ``allow_existing_toggles=False`` with any existing block.
        ValueError: anchor lost/ambiguous.
    """

    if template_text == toggle.on_text:
        return toggle.merged_text

    template_lines = template_text.splitlines(keepends=True)
    existing_blocks = _scan_jinja_block_ranges(template_text)

    if existing_blocks and not allow_existing_toggles:
        first_block = existing_blocks[0]
        raise OverlapError(
            hunk=toggle.hunks[0],
            existing_block_start_line=first_block[0],
            existing_block_end_line=first_block[1],
            message=(
                f"template already has [% if %] blocks; refusing to merge "
                f"under strict mode (uncheck 'allow existing toggles' to skip "
                f"this guard)"
            ),
        )

    # Anchor each hunk into template_lines so we can splice.
    anchored: list[tuple[int, int, DiffHunk]] = []
    for hunk in toggle.hunks:
        start, end = _anchor_hunk_in_template(template_lines, hunk)
        for blk_start, blk_end, _ in existing_blocks:
            if not (end <= blk_start or start >= blk_end):
                raise OverlapError(
                    hunk=hunk,
                    existing_block_start_line=blk_start,
                    existing_block_end_line=blk_end,
                    message=(
                        f"new toggle hunk at lines {start + 1}-{end} overlaps "
                        f"existing [% if %] block at lines "
                        f"{blk_start + 1}-{blk_end}"
                    ),
                )
        anchored.append((start, end, hunk))

    # Splice from bottom-up so earlier line indices stay valid.
    out = list(template_lines)
    for start, end, hunk in sorted(anchored, key=lambda t: -t[0]):
        wrap = _wrap_hunk(hunk, toggle.toggle_name)
        out[start:end] = [wrap]
    return "".join(out)


def detect_existing_toggle_blocks(text: str) -> list[tuple[int, int, str]]:
    """Return ``[(start_line, end_line, toggle_name), ...]`` for every
    outermost ``[% if %]…[% endif %]`` block in ``text``.

    ``start_line`` / ``end_line`` are 0-indexed; ``end_line`` is the
    line index of the matching ``[% endif %]`` (exclusive when used as a
    half-open slice). Tolerates ``[% else %]`` / ``[% elif %]``.
    """
    return _scan_jinja_block_ranges(text)


def render_byte_equivalence_check(toggle: ToggleResult) -> tuple[str, str]:
    """Render ``toggle.merged_text`` once with the toggle True and once
    False, with all other ``[[ ]]`` placeholders bound to a sentinel.

    Returns ``(rendered_on, rendered_off)``. Raises ``AssertionError``
    if either side fails to round-trip to the corresponding raw —
    valuable as a final guard in the dialog before save.
    """
    env = _make_jinja_env()
    template = env.from_string(toggle.merged_text)
    other_vars = _collect_jinja_vars(toggle.merged_text) - {toggle.toggle_name}
    sentinel_ctx = {name: f"<<{name}>>" for name in other_vars}

    on_ctx = {**sentinel_ctx, toggle.toggle_name: toggle.on_value}
    off_ctx = {**sentinel_ctx, toggle.toggle_name: toggle.off_value}
    rendered_on = template.render(**on_ctx)
    rendered_off = template.render(**off_ctx)

    # The on/off raws may themselves contain ``[[ ]]`` placeholders that
    # the sentinel substitution will rewrite. Compare against the same
    # sentinel-substituted raws to keep the equivalence check meaningful.
    on_expected = _render_with_sentinels(toggle.on_text, sentinel_ctx)
    off_expected = _render_with_sentinels(toggle.off_text, sentinel_ctx)

    if rendered_on != on_expected:
        raise AssertionError(
            "round-trip on-branch render does not match on_text"
        )
    if rendered_off != off_expected:
        raise AssertionError(
            "round-trip off-branch render does not match off_text"
        )
    return rendered_on, rendered_off


# ---- internals -------------------------------------------------------------


def _validate_toggle_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise ValueError("toggle_name must be a non-empty string")
    if not _TOGGLE_NAME_RE.match(name):
        raise ValueError(
            f"toggle_name {name!r} must match [a-z][a-z0-9_]* "
            f"(lowercase, ascii, no dots)"
        )
    if name in _JINJA_KEYWORDS:
        raise ValueError(f"toggle_name {name!r} is a Jinja keyword")
    if name in _IDENTITY_NAMES:
        raise ValueError(
            f"toggle_name {name!r} collides with a reserved identity variable"
        )


def _merge_adjacent(hunks: list[DiffHunk], *, merge_gap: int) -> list[DiffHunk]:
    if not hunks:
        return []
    merged: list[DiffHunk] = [hunks[0]]
    for h in hunks[1:]:
        prev = merged[-1]
        on_gap = h.on_start - prev.on_end
        off_gap = h.off_start - prev.off_end
        if on_gap <= merge_gap and off_gap <= merge_gap:
            merged[-1] = DiffHunk(
                on_start=prev.on_start,
                on_end=h.on_end,
                off_start=prev.off_start,
                off_end=h.off_end,
                on_lines=prev.on_lines + h.on_lines,
                off_lines=prev.off_lines + h.off_lines,
            )
        else:
            merged.append(h)
    return merged


def _all_hunks_whitespace_only(hunks: Iterable[DiffHunk]) -> bool:
    for h in hunks:
        on_norm = "".join(line.rstrip() for line in h.on_lines)
        off_norm = "".join(line.rstrip() for line in h.off_lines)
        if on_norm != off_norm:
            return False
    return True


def _wrap_hunk(hunk: DiffHunk, toggle_name: str) -> str:
    """Render one hunk as a ``[% if %]`` wrap.

    NOTE on whitespace: the project's Jinja env does NOT enable
    ``trim_blocks``, so any literal newline after a ``[% ... %]`` marker
    survives into the rendered output. To keep the render byte-identical
    to the input raws we inline each body's first line immediately after
    its opening marker (no leading newline). Bodies preserve their own
    line terminators via ``splitlines(keepends=True)``.
    """
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
    if hunk.on_lines and not hunk.off_lines:
        # Pure deletion in off side — no else branch needed.
        return (
            f"[% if {toggle_name} %]"
            f"{on_body}"
            f"[% endif %]"
        )
    # Pure insertion in off side — invert: render only when toggle is off.
    return (
        f"[% if not {toggle_name} %]"
        f"{off_body}"
        f"[% endif %]"
    )


def _splice_hunks(
    on_lines: list[str], hunks: list[DiffHunk], toggle_name: str
) -> str:
    """Walk the on-side line list emitting equals + wraps."""
    out: list[str] = []
    cursor = 0
    for h in hunks:
        out.extend(on_lines[cursor:h.on_start])
        out.append(_wrap_hunk(h, toggle_name))
        cursor = h.on_end
    out.extend(on_lines[cursor:])
    return "".join(out)


def _scan_jinja_block_ranges(text: str) -> list[tuple[int, int, str]]:
    """Forgiving line-by-line lexer for outermost ``[% if %]…[% endif %]``
    blocks. Returns ``(start_line, end_line, name)`` tuples. ``end_line``
    is the index of the ``[% endif %]`` line. Nested blocks contribute
    only the outermost range.
    """
    lines = text.splitlines(keepends=False)
    blocks: list[tuple[int, int, str]] = []
    depth = 0
    open_start = -1
    open_name = ""
    for i, line in enumerate(lines):
        if _RE_IF.search(line):
            if depth == 0:
                open_start = i
                m = _RE_IF.search(line)
                open_name = m.group(1) if m else ""
            depth += 1
        elif _RE_ENDIF.search(line):
            if depth == 0:
                continue
            depth -= 1
            if depth == 0:
                blocks.append((open_start, i + 1, open_name))
        # else / elif lines are ignored at the depth machine level.
    return blocks


def _anchor_hunk_in_template(
    template_lines: list[str], hunk: DiffHunk
) -> tuple[int, int]:
    """Find the line range in ``template_lines`` that matches
    ``hunk.on_lines`` verbatim. Raises on 0 or >1 matches.

    For a hunk with empty ``on_lines`` (pure insertion in the off
    branch) anchoring is undefined without a context window; raise
    ``ValueError`` and let the caller decline.
    """
    needle = list(hunk.on_lines)
    if not needle:
        raise ValueError(
            "cannot anchor a pure-insertion hunk into an existing template "
            "without a context window; re-author the toggle on a clean "
            "template"
        )

    matches: list[int] = []
    n = len(needle)
    for start in range(len(template_lines) - n + 1):
        if template_lines[start:start + n] == needle:
            matches.append(start)
    if not matches:
        raise ValueError(
            f"anchor lost: hunk on-lines not found in template"
        )
    if len(matches) > 1:
        raise ValueError(
            f"anchor ambiguous: hunk on-lines match at {len(matches)} "
            f"positions in template"
        )
    return matches[0], matches[0] + n


def _collect_jinja_vars(text: str) -> set[str]:
    """Best-effort scrape of ``[[name]]`` placeholders. Mirrors what
    :func:`auto_ext.core.template.scan_placeholders` would do but lives
    here to keep this module independent of the manifest layer."""
    return set(re.findall(r"\[\[\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\]\]", text))


def _render_with_sentinels(text: str, sentinel_ctx: dict[str, str]) -> str:
    """Substitute ``[[name]]`` with sentinel values without a full Jinja
    parse — used to mirror sentinel substitution across raw text that
    isn't itself a Jinja template (so unbalanced literals like Tcl
    braces don't trip the parser)."""
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        return sentinel_ctx.get(name, match.group(0))
    return re.sub(r"\[\[\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\]\]", repl, text)
