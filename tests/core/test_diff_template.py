"""Tests for :mod:`auto_ext.core.diff_template`."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.diff_template import (
    LargeDiffWarning,
    OverlapError,
    apply_toggle_to_template,
    compute_toggle,
    detect_existing_toggle_blocks,
    render_byte_equivalence_check,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---- compute_toggle: shape -------------------------------------------------


def test_compute_toggle_pure_value_flip() -> None:
    on = "a\nb\nC\nd\ne\n"
    off = "a\nb\nX\nd\ne\n"
    res = compute_toggle(on, off, "x")
    assert len(res.hunks) == 1
    h = res.hunks[0]
    assert h.on_lines == ("C\n",)
    assert h.off_lines == ("X\n",)
    assert "[% if x %]" in res.merged_text
    assert "[% else %]" in res.merged_text
    assert "[% endif %]" in res.merged_text
    assert res.warnings == ()


def test_compute_toggle_pure_deletion() -> None:
    on = "a\nb\nc\nd\ne\n"
    off = "a\nb\nd\ne\n"
    res = compute_toggle(on, off, "show_c")
    assert len(res.hunks) == 1
    assert res.hunks[0].on_lines == ("c\n",)
    assert res.hunks[0].off_lines == ()
    # Bare [% if %]…[% endif %] form (no [% else %] for empty off side).
    assert "[% if show_c %]" in res.merged_text
    assert "[% else %]" not in res.merged_text
    assert "[% endif %]" in res.merged_text


def test_compute_toggle_pure_insertion() -> None:
    on = "a\nb\nd\ne\n"
    off = "a\nb\nc\nd\ne\n"
    res = compute_toggle(on, off, "show_c")
    assert len(res.hunks) == 1
    assert res.hunks[0].on_lines == ()
    assert res.hunks[0].off_lines == ("c\n",)
    # Pure insertion -> [% if not show_c %]…[% endif %].
    assert "[% if not show_c %]" in res.merged_text
    assert "[% else %]" not in res.merged_text


def test_compute_toggle_adjacent_hunks_merged() -> None:
    on = "a\nB\nC\nd\n"
    off = "a\nX\nY\nd\n"
    res = compute_toggle(on, off, "ab")
    # Adjacent changes (lines 1 and 2) merge into a single hunk.
    assert len(res.hunks) == 1
    assert res.hunks[0].on_lines == ("B\n", "C\n")
    assert res.hunks[0].off_lines == ("X\n", "Y\n")


def test_compute_toggle_non_adjacent_hunks_separate() -> None:
    on = "a\nB\nc\nD\ne\n"
    off = "a\nX\nc\nY\ne\n"
    res = compute_toggle(on, off, "two")
    assert len(res.hunks) == 2
    assert res.merged_text.count("[% if two %]") == 2


# ---- compute_toggle: validation -------------------------------------------


def test_compute_toggle_identical_inputs_raises() -> None:
    with pytest.raises(ValueError, match="identical"):
        compute_toggle("hello\n", "hello\n", "x")


@pytest.mark.parametrize(
    "bad_name",
    ["UPPER", "with.dot", "if", "endif", "true", "cell", "1starts", "", "has space"],
)
def test_compute_toggle_invalid_toggle_name_raises(bad_name: str) -> None:
    with pytest.raises(ValueError):
        compute_toggle("a\nb\n", "a\nc\n", bad_name)


def test_compute_toggle_whitespace_only_diff_refused() -> None:
    on = "a   \nb\n"
    off = "a\nb\n"
    with pytest.raises(ValueError, match="whitespace"):
        compute_toggle(on, off, "ws")


# ---- compute_toggle: on_value polarity ------------------------------------


def test_compute_toggle_on_value_false_flips_default() -> None:
    res = compute_toggle("a\nb\n", "a\nc\n", "k", on_value=False)
    assert res.on_value is False
    assert res.off_value is True
    # Wrap structure unchanged: [% if k %] always wraps the on-side block.
    assert "[% if k %]" in res.merged_text


# ---- render_byte_equivalence_check ----------------------------------------


def test_render_byte_equivalence_on_branch() -> None:
    on = "a\nb\nC\nd\n"
    off = "a\nb\nX\nd\n"
    res = compute_toggle(on, off, "k")
    rendered_on, _ = render_byte_equivalence_check(res)
    assert rendered_on == on


def test_render_byte_equivalence_off_branch() -> None:
    on = "a\nb\nC\nd\n"
    off = "a\nb\nX\nd\n"
    res = compute_toggle(on, off, "k")
    _, rendered_off = render_byte_equivalence_check(res)
    assert rendered_off == off


def test_render_preserves_other_jinja_placeholders() -> None:
    on = "[[cell]]\nC\n"
    off = "[[cell]]\nX\n"
    res = compute_toggle(on, off, "k")
    # Sentinel substitution happens in both rendered + expected sides
    # so the comparison still passes.
    rendered_on, rendered_off = render_byte_equivalence_check(res)
    assert "<<cell>>" in rendered_on
    assert "<<cell>>" in rendered_off


def test_compute_toggle_handles_calibre_qci_brace_literals() -> None:
    """Real-world fixture: the shipped wiodio_noConnectByNetName.qci.j2
    contains literal Tcl ``{{...}}`` braces; wrapping must not break
    Jinja parsing."""
    off = (_REPO_ROOT / "templates" / "calibre" /
           "wiodio_noConnectByNetName.qci.j2").read_text(encoding="utf-8")
    # Synthesize an "on" version by adding a connect-by-net-name line.
    lines = off.splitlines(keepends=True)
    insert_idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("*lvsAbortOnSupplyError"):
            insert_idx = i
            break
    assert insert_idx is not None
    on_lines = (
        lines[:insert_idx]
        + ["*lvsConnectByName: 1\n"]
        + lines[insert_idx:]
    )
    on = "".join(on_lines)
    res = compute_toggle(on, off, "connect_by_net_name")
    assert len(res.hunks) == 1
    # Round-trip both sides.
    rendered_on, rendered_off = render_byte_equivalence_check(res)
    assert rendered_off.count("[[") == 0 or "<<" in rendered_off
    assert "*lvsConnectByName: 1" in rendered_on
    assert "*lvsConnectByName: 1" not in rendered_off


# ---- apply_toggle_to_template ---------------------------------------------


def test_apply_toggle_to_clean_template_equals_merged_text() -> None:
    on = "a\nb\nC\nd\n"
    off = "a\nb\nX\nd\n"
    res = compute_toggle(on, off, "k")
    out = apply_toggle_to_template(on, res)
    assert out == res.merged_text


def test_apply_toggle_round2_no_overlap_succeeds() -> None:
    # Round 1: produce a template with [% if first %] at lines 2-4.
    on1 = "a\nB\nc\nd\nE\nf\n"
    off1 = "a\nX\nc\nd\nE\nf\n"
    res1 = compute_toggle(on1, off1, "first")
    template_after_r1 = res1.merged_text

    # Round 2: a NEW toggle that changes a different line (E vs Y).
    # We compute it against a clean pair where lines 1 and 5 differ
    # — its hunk lands on the "E\n" line in the on raw.
    on2 = on1
    off2 = "a\nB\nc\nd\nY\nf\n"
    res2 = compute_toggle(on2, off2, "second")
    # Apply res2 to the template that already has [% if first %] in it.
    out = apply_toggle_to_template(template_after_r1, res2)
    assert "[% if first %]" in out
    assert "[% if second %]" in out


def test_apply_toggle_round2_overlap_raises() -> None:
    # Construct a template that has a multi-line [% if first %] block
    # whose wrapped on-side body contains a unique line "B\n". The new
    # toggle's hunk anchors on that "B\n" line, which lives INSIDE the
    # existing block range — must raise OverlapError.
    template = (
        "a\n"
        "[% if first %]B\nC\nD\n[% else %]X\nY\nZ\n[% endif %]e\n"
    )
    on2 = "a\nB\nC\nD\ne\n"
    off2 = "a\nB\nQ\nD\ne\n"
    res2 = compute_toggle(on2, off2, "second")
    with pytest.raises(OverlapError, match="overlaps"):
        apply_toggle_to_template(template, res2)


def test_apply_toggle_anchor_lost_raises() -> None:
    on = "a\nb\nC\nd\n"
    off = "a\nb\nX\nd\n"
    res = compute_toggle(on, off, "k")
    # Hand-edit: replace the line C looks for so the anchor disappears.
    edited = "a\nb\nZZZ_changed\nd\n"
    with pytest.raises(ValueError, match="anchor lost"):
        apply_toggle_to_template(edited, res)


def test_apply_toggle_strict_mode_refuses_existing() -> None:
    on1 = "a\nB\nc\n"
    off1 = "a\nX\nc\n"
    res1 = compute_toggle(on1, off1, "first")
    template_after_r1 = res1.merged_text

    on2 = on1
    off2 = "a\nB\nQ\n"
    res2 = compute_toggle(on2, off2, "second")
    # Strict mode: refuse anything when existing toggles present.
    with pytest.raises(OverlapError, match="strict"):
        apply_toggle_to_template(
            template_after_r1, res2, allow_existing_toggles=False
        )


# ---- detect_existing_toggle_blocks ----------------------------------------


def test_detect_existing_toggle_blocks_outermost_only() -> None:
    text = (
        "a\n"
        "[% if outer %]\n"
        "x\n"
        "[% if inner %]\n"
        "y\n"
        "[% endif %]\n"
        "z\n"
        "[% endif %]\n"
        "b\n"
    )
    blocks = detect_existing_toggle_blocks(text)
    assert len(blocks) == 1
    assert blocks[0][2] == "outer"


# ---- LargeDiffWarning -----------------------------------------------------


def test_compute_toggle_emits_large_diff_warning_above_threshold() -> None:
    # 6 of 6 lines change → ratio = 1.0, well above 0.5.
    on = "a\nb\nc\nd\ne\nf\n"
    off = "A\nB\nC\nD\nE\nF\n"
    res = compute_toggle(on, off, "k")
    assert any(isinstance(w, LargeDiffWarning) for w in res.warnings)


def test_compute_toggle_no_warning_below_threshold() -> None:
    # 1 of 10 lines changes → ratio well below 0.5.
    on = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"
    off = "a\nb\nc\nd\nQ\nf\ng\nh\ni\nj\n"
    res = compute_toggle(on, off, "k")
    assert all(not isinstance(w, LargeDiffWarning) for w in res.warnings)
