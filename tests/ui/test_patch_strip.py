"""The escape hatch, collapsed and expanded.

Artboards ``1f`` and ``1g``. Three groups of cases carry the weight:

* the nine matcher statuses fold onto four words a person can act on, and
  ``disabled`` beats whatever the last report said;
* the stored hunk is masked and the display says so, because "one edit works
  for every cell" is the whole reason the storage format looks like that;
* a hunk the catalog has absorbed offers to delete itself, which is the only
  mechanism that makes the patch list shrink instead of only ever growing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.patch_models import (  # noqa: E402
    BaseFingerprint,
    HunkOutcome,
    PatchHunk,
    PatchStatus,
    Stage,
    StagePatchReport,
    TemplatePatch,
)
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.widgets.patch_strip import (  # noqa: E402
    DIFF_ADD_FILL,
    DIFF_ADD_TEXT,
    DIFF_DEL_FILL,
    DIFF_DEL_TEXT,
    GLYPH_COLLAPSED,
    GLYPH_EXPANDED,
    NOOP_ADVICE,
    STATE_COLOR,
    STATE_LABEL,
    DiffBlock,
    HunkState,
    PatchStrip,
    diff_rows,
    file_summary,
    generated_name,
    hunk_state,
    summary_line,
    template_name,
)

_SHA = "0" * 64
_CAPTURED = datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc)

#: Every glyph the design allows anywhere in the UI.
ALLOWED_GLYPHS = set("✓✗▶■–·⇆▾▴▼")


def make_hunk(
    hunk_id: str = "0000000a",
    *,
    intent: str = "lower the decoupling factor for the DCO tank",
    enabled: bool = True,
    before: str = "              -decoupling_factor 1.0 \\\n",
    after: str = "              -decoupling_factor 0.8 \\\n",
) -> PatchHunk:
    return PatchHunk(
        id=hunk_id,
        enabled=enabled,
        intent=intent,
        before=before,
        after=after,
        context_before="  capacitance \\\n",
        context_after='              -ground_net "${ground_net}"\n',
    )


def make_patch(
    *,
    stage: Stage = Stage.QUANTUS,
    template_id: str = "quantus/ext.cmd.j2",
    hunks: list[PatchHunk] | None = None,
) -> TemplatePatch:
    return TemplatePatch(
        stage=stage,
        template_id=template_id,
        base=BaseFingerprint(
            template_sha256=_SHA, masked_sha256=_SHA, captured_at=_CAPTURED
        ),
        hunks=hunks if hunks is not None else [make_hunk()],
    )


def report(
    *outcomes: HunkOutcome,
    stage: Stage = Stage.QUANTUS,
    template_id: str = "quantus/ext.cmd.j2",
) -> StagePatchReport:
    return StagePatchReport(
        stage=stage, template_id=template_id, outcomes=list(outcomes)
    )


def _strip(qtbot) -> PatchStrip:
    strip = PatchStrip()
    qtbot.addWidget(strip)
    return strip


# ---- status folding ------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PatchStatus.CLEAN, HunkState.APPLIED),
        (PatchStatus.SHIFTED, HunkState.APPLIED),
        (PatchStatus.REVIEW, HunkState.CONFLICT),
        (PatchStatus.AMBIGUOUS, HunkState.CONFLICT),
        (PatchStatus.LOST, HunkState.CONFLICT),
        (PatchStatus.OVERLAP, HunkState.CONFLICT),
        (PatchStatus.ABSORBED, HunkState.NOOP),
        (PatchStatus.NOOP, HunkState.NOOP),
        (PatchStatus.DISABLED, HunkState.DISABLED),
    ],
)
def test_every_matcher_status_folds_onto_a_word(status, expected) -> None:
    assert hunk_state(status) is expected


def test_every_status_is_covered() -> None:
    """A status added to the matcher must not fall through to "not checked"."""

    for status in PatchStatus:
        assert hunk_state(status) is not HunkState.UNKNOWN, status


def test_no_report_means_not_checked_rather_than_applied() -> None:
    assert hunk_state(None) is HunkState.UNKNOWN
    assert STATE_LABEL[HunkState.UNKNOWN] == "not checked"


def test_switched_off_beats_the_last_report() -> None:
    """The report may predate the switch, so ``enabled`` is the later fact."""

    assert hunk_state(PatchStatus.CLEAN, enabled=False) is HunkState.DISABLED
    assert hunk_state(PatchStatus.LOST, enabled=False) is HunkState.DISABLED


def test_state_colours_come_from_the_status_scale_and_never_the_accent() -> None:
    accents = theme.accent_colors()
    for state, colour in STATE_COLOR.items():
        assert colour not in accents, f"{state} borrowed the accent colour"
    assert STATE_COLOR[HunkState.APPLIED] == theme.STATUS_PASSED
    assert STATE_COLOR[HunkState.CONFLICT] == theme.STATUS_FAILED
    assert STATE_COLOR[HunkState.NOOP] == theme.WARNING_TEXT_ON_WHITE


def test_diff_tints_are_not_the_accent_and_not_the_status_fills() -> None:
    accents = theme.accent_colors()
    tints = {DIFF_ADD_FILL, DIFF_ADD_TEXT, DIFF_DEL_FILL, DIFF_DEL_TEXT}
    assert not (tints & accents)
    assert DIFF_ADD_FILL != theme.STATUS_FILL["passed"], "added is not passed"
    assert DIFF_DEL_FILL != theme.STATUS_FILL["failed"], "removed is not failed"


# ---- diff rendering ------------------------------------------------------


def test_a_hunk_renders_as_a_unified_diff_with_context() -> None:
    rows = diff_rows(make_hunk())
    assert [tag for tag, _ in rows] == ["context", "remove", "add", "context"]
    assert rows[1][1].endswith("-decoupling_factor 1.0 \\")
    assert rows[2][1].endswith("-decoupling_factor 0.8 \\")


def test_the_masked_form_is_the_default_because_that_is_the_explanation() -> None:
    """``${ground_net}`` on screen is why one stored edit serves eight DUTs."""

    rows = diff_rows(make_hunk())
    assert any("${ground_net}" in text for _tag, text in rows)


def test_values_can_be_substituted_for_a_concrete_render() -> None:
    rows = diff_rows(make_hunk(), {"ground_net": "vss"})
    assert any('"vss"' in text for _tag, text in rows)
    assert not any("${ground_net}" in text for _tag, text in rows)


def test_context_can_be_switched_off() -> None:
    rows = diff_rows(make_hunk(), context=0)
    assert [tag for tag, _ in rows] == ["remove", "add"]


def test_a_hunk_with_no_trailing_newline_still_splits_into_two_rows() -> None:
    """``difflib`` concatenates, so an unterminated last line mashes ``-old+new``.

    A hunk captured at the end of a file without a trailing newline is
    exactly that case, and the row would otherwise be unreadable.
    """

    rows = diff_rows(
        make_hunk(before="  *cmnNumTurbo: 2", after="  *cmnNumTurbo: 8"), context=0
    )
    assert rows == [("remove", "  *cmnNumTurbo: 2"), ("add", "  *cmnNumTurbo: 8")]


def test_a_pure_insertion_shows_only_added_lines() -> None:
    hunk = PatchHunk(
        id="0000000b",
        before="",
        after="  *cmnRunHyper: 1\n",
        context_before="  *cmnRunMT: 1\n",
        context_after="  *cmnNumTurbo: 2\n",
        intent="hyper on the big cells",
    )
    tags = [tag for tag, _ in diff_rows(hunk, context=0)]
    assert tags == ["add"]


def test_the_diff_block_keeps_the_rows_and_can_be_read_back(qtbot) -> None:
    block = DiffBlock(diff_rows(make_hunk()))
    qtbot.addWidget(block)
    assert len(block.rows()) == 4
    text = block.plain_text()
    assert "-              -decoupling_factor 1.0 \\" in text
    assert "+              -decoupling_factor 0.8 \\" in text


# ---- file naming ---------------------------------------------------------


def test_the_generated_file_and_its_template_are_named_separately() -> None:
    assert generated_name("quantus/ext.cmd.j2") == "quantus/ext.cmd"
    assert template_name("quantus/ext.cmd.j2") == "ext.cmd.j2"
    assert generated_name("quantus/ext.cmd") == "quantus/ext.cmd"


def test_the_per_file_summary_counts_enabled_hunks_only() -> None:
    patches = [
        make_patch(hunks=[make_hunk("0000000a"), make_hunk("0000000b", enabled=False)]),
        make_patch(
            stage=Stage.CALIBRE,
            template_id="calibre/calibre_lvs.qci.j2",
            hunks=[make_hunk("0000000c")],
        ),
    ]
    assert file_summary(patches) == [("ext.cmd", 1), ("calibre_lvs.qci", 1)]
    assert summary_line(patches) == "ext.cmd 1 · calibre_lvs.qci 1"


# ---- collapsed state (artboard 1f) ---------------------------------------


def test_collapsed_is_one_line_that_counts_the_edits(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches(
        [
            make_patch(hunks=[make_hunk("0000000a"), make_hunk("0000000b")]),
            make_patch(
                stage=Stage.CALIBRE,
                template_id="calibre/calibre_lvs.qci.j2",
                hunks=[make_hunk("0000000c")],
            ),
        ]
    )
    assert strip.is_expanded() is False
    assert strip.hunk_count() == 3
    assert strip.summary_text() == "This recipe has 3 manual edits"
    assert strip.file_summary_text() == "ext.cmd 2 · calibre_lvs.qci 1"
    assert strip.glyph() == GLYPH_COLLAPSED
    assert "survives template changes" in strip.subtitle_text()


def test_one_edit_is_singular(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    assert strip.summary_text() == "This recipe has 1 manual edit"


def test_with_no_edits_the_strip_is_neutral_and_cannot_be_opened(qtbot) -> None:
    """Amber means "something here needs your attention". Nothing does."""

    strip = _strip(qtbot)
    strip.show()
    strip.set_patches([])
    assert strip.hunk_count() == 0
    assert strip.summary_text() == "No manual edits"
    assert strip.toggle_button().isEnabled() is False
    assert theme.STATUS_WARNING not in strip.bar().styleSheet()

    strip.toggle()
    assert strip.is_expanded() is False, "an empty strip claimed to be open"


def test_removing_the_last_hunk_folds_an_open_strip(qtbot) -> None:
    """Otherwise the recipe form stays hidden behind an empty panel."""

    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    strip.set_expanded(True)
    with qtbot.waitSignal(strip.toggled, timeout=1000) as blocker:
        strip.set_patches([])
    assert blocker.args == [False]
    assert strip.is_expanded() is False


def test_an_intent_free_hunk_says_so(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch(hunks=[make_hunk(intent="")])])
    assert strip.hunk_rows()[0].intent_text() == "(no intent recorded)"


def test_edits_turn_the_bar_amber(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    style = strip.bar().styleSheet()
    assert theme.STATUS_WARNING in style
    for accent in theme.accent_colors():
        assert accent not in style


# ---- expanded state (artboard 1g) ----------------------------------------


def test_expanding_shows_one_row_per_hunk_and_a_footer(qtbot) -> None:
    strip = _strip(qtbot)
    strip.show()
    strip.set_patches(
        [make_patch(hunks=[make_hunk("0000000a"), make_hunk("0000000b")])]
    )
    with qtbot.waitSignal(strip.toggled, timeout=1000) as blocker:
        strip.toggle_button().click()
    assert blocker.args == [True]

    assert strip.is_expanded() is True
    assert strip.glyph() == GLYPH_EXPANDED
    assert strip.summary_text() == "2 manual edits"
    assert strip.subtitle_text() == "applied after render, before the tool runs"
    assert strip.toggle_button().text() == "Hide diff"
    assert strip.revert_all_button().text() == "Revert all 2"
    assert len(strip.hunk_rows()) == 2
    assert strip.footer().isVisible()
    assert "never silently dropped" in strip.footer().text()


def test_the_row_shows_the_intent_and_the_resolved_line(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches(
        [make_patch()],
        reports=[report(HunkOutcome(hunk_id="0000000a", status=PatchStatus.CLEAN, start_line=8))],
    )
    row = strip.hunk_rows()[0]
    assert row.anchor_text() == "@@ line 8"
    assert row.chip_text() == "applied"
    assert row.state is HunkState.APPLIED
    assert "DCO tank" in row.intent_text()


def test_without_a_report_the_row_says_so_instead_of_guessing(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    row = strip.hunk_rows()[0]
    assert row.state is HunkState.UNKNOWN
    assert row.chip_text() == "not checked"
    assert row.anchor_text() == "@@ hunk 0000000a"


def test_a_conflict_row_explains_itself_and_offers_no_delete(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches(
        [make_patch()],
        reports=[
            report(
                HunkOutcome(
                    hunk_id="0000000a",
                    status=PatchStatus.LOST,
                    message="the anchor is gone entirely",
                )
            )
        ],
    )
    row = strip.hunk_rows()[0]
    assert row.state is HunkState.CONFLICT
    assert row.chip_text() == "conflict"
    assert "anchor is gone" in row.note_text()
    assert row.delete_button() is None


def test_an_absorbed_hunk_says_it_can_be_deleted(qtbot) -> None:
    """This prompt is the only thing that makes the patch list shrink.

    Absorbed means the catalog now emits the line by itself: the option was
    promoted out of the escape hatch. Leaving the hunk in place costs a
    matcher pass per run forever and hides the fact that it was a success.
    """

    strip = _strip(qtbot)
    strip.set_patches(
        [make_patch()],
        reports=[report(HunkOutcome(hunk_id="0000000a", status=PatchStatus.ABSORBED))],
    )
    row = strip.hunk_rows()[0]
    assert row.state is HunkState.NOOP
    assert row.chip_text() == "no-op"
    assert row.note_text() == NOOP_ADVICE
    assert "Delete the hunk" in row.note_text()

    delete = row.delete_button()
    assert delete is not None, "an absorbed hunk offers no way to remove itself"


def test_an_applied_row_offers_revert_but_not_delete(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches(
        [make_patch()],
        reports=[report(HunkOutcome(hunk_id="0000000a", status=PatchStatus.CLEAN))],
    )
    row = strip.hunk_rows()[0]
    assert row.revert_button().text() == "Revert"
    assert row.delete_button() is None


def test_a_disabled_hunk_is_shown_rather_than_hidden(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch(hunks=[make_hunk("0000000a", enabled=False)])])
    row = strip.hunk_rows()[0]
    assert row.state is HunkState.DISABLED
    assert row.chip_text() == "disabled"
    assert strip.hunk_count() == 0
    assert strip.total_hunk_count() == 1


# ---- requests out --------------------------------------------------------


def test_reverting_one_hunk_names_the_file_it_belongs_to(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    with qtbot.waitSignal(strip.hunk_revert_requested, timeout=1000) as blocker:
        strip.hunk_rows()[0].revert_button().click()
    assert blocker.args == ["quantus", "quantus/ext.cmd.j2", "0000000a"]


def test_deleting_an_absorbed_hunk_emits_its_own_request(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches(
        [make_patch()],
        reports=[report(HunkOutcome(hunk_id="0000000a", status=PatchStatus.NOOP))],
    )
    delete = strip.hunk_rows()[0].delete_button()
    assert delete is not None
    with qtbot.waitSignal(strip.hunk_delete_requested, timeout=1000) as blocker:
        delete.click()
    assert blocker.args == ["quantus", "quantus/ext.cmd.j2", "0000000a"]


def test_revert_all_and_edit_rendered_are_separate_requests(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    strip.set_expanded(True)
    with qtbot.waitSignal(strip.revert_all_requested, timeout=1000):
        strip.revert_all_button().click()
    with qtbot.waitSignal(strip.edit_rendered_requested, timeout=1000):
        strip.edit_button().click()


# ---- identity ------------------------------------------------------------


def test_two_files_may_hold_a_hunk_with_the_same_id(qtbot) -> None:
    """Hunk ids are unique inside one patch, not across a recipe."""

    strip = _strip(qtbot)
    strip.set_patches(
        [
            make_patch(hunks=[make_hunk("0000000a")]),
            make_patch(
                stage=Stage.CALIBRE,
                template_id="calibre/calibre_lvs.qci.j2",
                hunks=[make_hunk("0000000a")],
            ),
        ],
        reports=[report(HunkOutcome(hunk_id="0000000a", status=PatchStatus.CLEAN))],
    )
    assert len(strip.hunk_rows()) == 2
    assert strip.states() == {
        ("quantus/ext.cmd.j2", "0000000a"): HunkState.APPLIED,
        ("calibre/calibre_lvs.qci.j2", "0000000a"): HunkState.UNKNOWN,
    }
    assert (
        strip.state_of("0000000a", template_id="calibre/calibre_lvs.qci.j2")
        is HunkState.UNKNOWN
    )


# ---- design rules --------------------------------------------------------


def test_the_strip_only_uses_glyphs_the_design_allows(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    for text in (GLYPH_COLLAPSED, GLYPH_EXPANDED, strip.glyph()):
        for char in text:
            assert char in ALLOWED_GLYPHS, f"{char!r} is outside the DejaVu subset"
    strip.set_expanded(True)
    for char in strip.glyph():
        assert char in ALLOWED_GLYPHS


def test_the_strip_never_sets_the_window_floor(qtbot) -> None:
    strip = _strip(qtbot)
    strip.set_patches([make_patch()])
    assert strip.minimumSizeHint().width() <= 300
