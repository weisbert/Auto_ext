"""Tests for :mod:`auto_ext.ui.widgets.result_card`.

Two halves. The first exercises the pure helpers (formatting, CELL SUMMARY
round-tripping, failure grouping) with no Qt involved; the second drives the
widget itself through ``qtbot``.

The layout assertions matter as much as the content ones: the window floor is
940x560 px, so a card that contributes a tall minimum makes the application
unusable on a 1366x768 laptop screen. ``test_result_card_min_height_*`` are
the nails for that.

The 1d assertions are the important content ones. That artboard is the card
this user reaches for most -- LVS failures dominate his week -- and it has to
carry enough for him to skip the habitual re-check in Calibre Interactive:
the banner, the count, the movement against the previous run, the sub-cells
that did not match, and the exact frozen runset a hand-off would open.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402

from auto_ext.core.checks import CellSummaryRow  # noqa: E402
from auto_ext.core.failure_class import (  # noqa: E402
    Confidence,
    FailureClass,
    FailureVerdict,
)
from auto_ext.core.handoff import HandoffPlan  # noqa: E402
from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.core.run_store import RunIndexEntry  # noqa: E402
from auto_ext.model.run import (  # noqa: E402
    LvsResult,
    RunAnnotations,
    RunResults,
    StageRecord,
)
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.widgets import failure_chip as fc  # noqa: E402
from auto_ext.ui.widgets import result_card as rc  # noqa: E402
from auto_ext.ui.widgets.failure_chip import Chip, FailureChip, PathLabel  # noqa: E402
from auto_ext.ui.widgets.result_card import ResultCard  # noqa: E402

#: A screen must not push the window's minimum height up; the card is the
#: tallest thing inside the Runs screen, so it is where the budget is spent.
MIN_HEIGHT_BUDGET = 400


# ---- report fixtures --------------------------------------------------------

_REPORT_WITH_MISMATCH = """\
CELL SUMMARY

    RESULT      LAYOUT      SOURCE
    INCORRECT   amp2        amp2
    CORRECT     bias        bias
    INCORRECT   dco_core    dco_core

INCORRECT

DISCREPANCIES = 3
"""


def _stage(
    key: str,
    *,
    stage: str | None = None,
    status: StageStatus = StageStatus.PASSED,
    **fields: object,
) -> StageRecord:
    return StageRecord(key=key, stage=stage or key, status=status, **fields)


# ============================================================================
# pure helpers
# ============================================================================


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "-"),
        (-1.0, "-"),
        (0.0, "0.00 s"),
        (0.834, "0.83 s"),
        (12.34, "12.3 s"),
        (61.0, "1m 01s"),
        (200.0, "3m 20s"),
        (3661.0, "1h 01m 01s"),
    ],
)
def test_format_duration(seconds: float | None, expected: str) -> None:
    assert rc.format_duration(seconds) == expected


def test_format_timestamp_renders_utc() -> None:
    moment = datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc)
    assert rc.format_timestamp(moment) == "2026-08-21 14:32:05 UTC"


def test_format_timestamp_treats_naive_as_utc_and_converts_offsets() -> None:
    naive = datetime(2026, 8, 21, 14, 32, 5)
    assert rc.format_timestamp(naive) == "2026-08-21 14:32:05 UTC"
    shifted = datetime(
        2026, 8, 21, 16, 32, 5, tzinfo=timezone(timedelta(hours=2))
    )
    assert rc.format_timestamp(shifted) == "2026-08-21 14:32:05 UTC"
    assert rc.format_timestamp(None) == "-"


def test_stage_tally_excludes_skipped_from_the_denominator() -> None:
    stages = [
        _stage("si"),
        _stage("strmout"),
        _stage("calibre", status=StageStatus.FAILED),
        _stage("quantus", status=StageStatus.SKIPPED),
        _stage("jivaro", status=StageStatus.SKIPPED),
    ]
    assert rc.stage_tally(stages) == (2, 3)


def test_tally_text_names_every_non_passing_bucket() -> None:
    stages = [
        _stage("si"),
        _stage("calibre", status=StageStatus.FAILED),
        _stage("quantus", status=StageStatus.SKIPPED),
        _stage("jivaro", status=StageStatus.CANCELLED),
    ]
    text = rc.tally_text(stages)
    assert text.startswith("1/3 stages passed")
    assert "1 failed" in text
    assert "1 cancelled" in text
    assert "1 skipped" in text


def test_parse_cell_summary_line_round_trips_checks_as_line() -> None:
    row = CellSummaryRow(result="INCORRECT", layout="amp2", source="amp2")
    back = rc.parse_cell_summary_line(row.as_line())
    assert back == row
    assert back is not None and back.name == "amp2"


@pytest.mark.parametrize(
    "line",
    ["INCORRECT", "", "CORRECT amp2", "MAYBE amp2 amp2", "CORRECT a b c"],
)
def test_parse_cell_summary_line_rejects_non_rows(line: str) -> None:
    assert rc.parse_cell_summary_line(line) is None


def test_read_cell_summary_reads_rows_off_an_archived_report(
    tmp_path: Path,
) -> None:
    report = tmp_path / "amp2.lvs.report"
    report.write_text(_REPORT_WITH_MISMATCH, encoding="utf-8")
    rows = rc.read_cell_summary(report)
    assert rows == [
        "INCORRECT amp2 amp2",
        "CORRECT bias bias",
        "INCORRECT dco_core dco_core",
    ]


def test_read_cell_summary_never_raises_on_a_bad_report(tmp_path: Path) -> None:
    assert rc.read_cell_summary(None) == []
    assert rc.read_cell_summary(tmp_path / "nope.report") == []
    truncated = tmp_path / "truncated.report"
    truncated.write_text("nothing useful here\n", encoding="utf-8")
    assert rc.read_cell_summary(truncated) == []


def test_mismatched_cells_keeps_order_and_de_duplicates() -> None:
    rows = [
        "INCORRECT amp2 amp2",
        "CORRECT bias bias",
        "INCORRECT dco_core dco_core",
        "INCORRECT amp2 amp2",
    ]
    assert rc.mismatched_cells(rows) == ["amp2", "dco_core"]


def test_unnamed_mismatch_count_counts_bare_verdict_rows() -> None:
    rows = ["INCORRECT", "CORRECT", "INCORRECT", "INCORRECT amp2 amp2"]
    assert rc.unnamed_mismatch_count(rows) == 2
    assert rc.mismatched_cells(rows) == ["amp2"]


def test_cell_summary_rows_prefers_the_archived_record_over_disk(
    tmp_path: Path,
) -> None:
    report = tmp_path / "amp2.lvs.report"
    report.write_text(_REPORT_WITH_MISMATCH, encoding="utf-8")
    lvs = LvsResult(passed=False, cell_summary=["INCORRECT only_this only_this"])
    assert rc.cell_summary_rows(lvs, report) == ["INCORRECT only_this only_this"]

    # Empty on the record -> fall back to re-reading the report.
    empty = LvsResult(passed=False)
    assert rc.cell_summary_rows(empty, report)[0] == "INCORRECT amp2 amp2"
    assert rc.cell_summary_rows(None, report) == []


def test_as_lvs_report_rebuilds_the_checks_view() -> None:
    lvs = LvsResult(
        passed=False,
        banner="INCORRECT",
        discrepancies=3,
        source_path="/wa/amp2.lvs.report",
    )
    report = rc.as_lvs_report(lvs, ["INCORRECT amp2 amp2", "CORRECT bias bias"])
    assert report is not None
    assert report.mismatched_cells == ("amp2",)
    assert report.cell_summary_present is True
    assert report.discrepancies == 3
    assert rc.as_lvs_report(None, []) is None


@pytest.mark.parametrize(
    ("current", "previous", "fragment", "color"),
    [
        (3, 17, "17 -> 3 (down 14)", rc.COLOR_PASS),
        (17, 3, "3 -> 17 (up 14)", rc.COLOR_FAIL),
        (3, 3, "unchanged at 3", rc.COLOR_WARN),
        (3, None, "no count recorded on the previous run", rc.COLOR_MUTED),
        (None, 3, "previous run reported 3", rc.COLOR_MUTED),
        (None, None, "no discrepancy count on either run", rc.COLOR_MUTED),
    ],
)
def test_discrepancy_delta_text(
    current: int | None, previous: int | None, fragment: str, color: str
) -> None:
    text, got_color = rc.discrepancy_delta_text(current, previous)
    assert text == fragment
    assert got_color == color


def test_resolve_run_relative_joins_posix_onto_the_run_dir(tmp_path: Path) -> None:
    assert rc.resolve_run_relative(tmp_path, "logs/calibre.log") == (
        tmp_path / "logs" / "calibre.log"
    )
    assert rc.resolve_run_relative(tmp_path, None) is None
    assert rc.resolve_run_relative(None, "logs/calibre.log") is None


# ---- failure grouping -------------------------------------------------------


def test_verdict_from_details_round_trips_failure_verdict_as_dict() -> None:
    verdict = FailureVerdict(
        failure_class=FailureClass.LICENSE_UNAVAILABLE,
        confidence=Confidence.SIGNATURE,
        reason="log matched signature 'mgc_license'",
        next_action="Wait for a seat.",
        evidence="LICENSE FAILURE",
        signature_id="mgc_license",
    )
    back = rc.verdict_from_details({rc.DETAILS_FAILURE_KEY: verdict.as_dict()})
    assert back == verdict


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"failure": "not a dict"},
        {"failure": {"confidence": "certain"}},
        {"failure": {"failure_class": "not_a_class"}},
    ],
)
def test_verdict_from_details_returns_none_for_anything_malformed(
    details: dict | None,
) -> None:
    assert rc.verdict_from_details(details) is None


def test_stage_verdict_prefers_the_recorded_verdict_over_re_classifying() -> None:
    verdict = FailureVerdict(
        failure_class=FailureClass.LICENSE_UNAVAILABLE,
        confidence=Confidence.SIGNATURE,
        reason="log matched signature 'mgc_license'",
        next_action="Wait for a seat.",
    )
    stage = _stage(
        "calibre",
        status=StageStatus.FAILED,
        exit_code=127,  # would classify as ENVIRONMENT if re-derived
        details={rc.DETAILS_FAILURE_KEY: verdict.as_dict()},
    )
    got = rc.stage_verdict(stage)
    assert got.failure_class is FailureClass.LICENSE_UNAVAILABLE
    assert got.next_action == "Wait for a seat."


def test_stage_verdict_falls_back_to_the_core_classifier() -> None:
    stage = _stage("si", status=StageStatus.FAILED, exit_code=127)
    got = rc.stage_verdict(stage)
    assert got.failure_class is FailureClass.ENVIRONMENT
    assert got.confidence is Confidence.CERTAIN
    assert "PATH" in got.next_action


def test_group_failures_buckets_by_class_and_keeps_cancelled_separate(
    make_run_record,
) -> None:
    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[
            _stage("si"),
            _stage("strmout", status=StageStatus.FAILED, exit_code=127),
            _stage("calibre", status=StageStatus.FAILED, exit_code=1),
            _stage("quantus", status=StageStatus.CANCELLED),
            _stage("jivaro", status=StageStatus.SKIPPED, skip_reason="disabled"),
        ],
    )
    groups = rc.group_failures(record)
    keys = [g.key for g in groups]
    assert keys == [
        FailureClass.ENVIRONMENT.value,
        FailureClass.TOOL_CRASH.value,
        rc.CANCELLED_KEY,
    ]
    assert [s.key for s in groups[0].stages] == ["strmout"]
    assert [s.key for s in groups[2].stages] == ["quantus"]
    # Every group carries an actionable next step; skipped stages are not
    # failures and never appear.
    assert all(g.next_action.strip() for g in groups)
    assert "jivaro" not in [s.key for g in groups for s in g.stages]


def test_group_failures_merges_two_stages_of_the_same_class(
    make_run_record,
) -> None:
    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[
            _stage("si", status=StageStatus.FAILED, exit_code=127),
            _stage("strmout", status=StageStatus.FAILED, exit_code=127),
        ],
    )
    groups = rc.group_failures(record)
    assert len(groups) == 1
    assert [s.key for s in groups[0].stages] == ["si", "strmout"]
    assert groups[0].title == "Environment"


def test_group_failures_lvs_mismatch_uses_the_lvs_result(make_run_record) -> None:
    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[_stage("calibre", status=StageStatus.FAILED, exit_code=0)],
        results=RunResults(
            lvs=LvsResult(passed=False, banner="INCORRECT", discrepancies=3)
        ),
    )
    groups = rc.group_failures(record)
    assert [g.key for g in groups] == [FailureClass.LVS_MISMATCH.value]
    assert "CELL SUMMARY" in groups[0].next_action


# ---- stage strip (pure) -----------------------------------------------------


def test_stage_chips_one_per_tool_in_pipeline_order(make_run_record) -> None:
    record = make_run_record(
        requested_stages=["si", "strmout", "calibre", "quantus"],
        stages=[
            _stage("si"),
            _stage("strmout"),
            _stage("calibre", status=StageStatus.FAILED),
        ],
    )
    chips = rc.stage_chips(record)
    assert [c.stage for c in chips] == [
        "si",
        "strmout",
        "calibre",
        "quantus",
        "jivaro",
    ]
    by_stage = {c.stage: c for c in chips}
    assert by_stage["si"].text == "si " + theme.STATUS_GLYPH["passed"]
    assert by_stage["calibre"].text == "calibre " + theme.STATUS_GLYPH["failed"]
    # Requested but never reached vs never asked for: different sentences.
    assert by_stage["quantus"].text.endswith("not started")
    assert by_stage["jivaro"].text.endswith("off in recipe")


def test_stage_chips_say_nothing_when_the_record_lists_no_requested_stages(
    make_run_record,
) -> None:
    """An older record cannot distinguish "not started" from "off"; do not guess."""

    record = make_run_record(stages=[_stage("si")])
    assert [c.stage for c in rc.stage_chips(record)] == ["si"]


def test_stage_chips_take_the_worst_status_of_a_split_stage(
    make_run_record,
) -> None:
    """``quantus.ext`` passing must not hide ``quantus.dspf`` failing."""

    record = make_run_record(
        stages=[
            _stage("quantus.ext", stage="quantus"),
            _stage("quantus.dspf", stage="quantus", status=StageStatus.FAILED),
        ]
    )
    chips = rc.stage_chips(record)
    assert len(chips) == 1
    assert chips[0].tone == fc.CHIP_TONE_FAILED


def test_stage_chips_quote_the_skip_reason(make_run_record) -> None:
    record = make_run_record(
        stages=[
            _stage("calibre", status=StageStatus.FAILED),
            _stage(
                "quantus",
                status=StageStatus.SKIPPED,
                skip_reason="aborted after earlier stage failure",
            ),
        ]
    )
    by_stage = {c.stage: c for c in rc.stage_chips(record)}
    assert "aborted after earlier stage failure" in by_stage["quantus"].text


def test_stop_reason_names_the_stage_and_the_switch(make_run_record) -> None:
    record = make_run_record(
        continue_on_lvs_fail=False,
        stages=[
            _stage("si"),
            _stage("calibre", status=StageStatus.FAILED),
            _stage("quantus", status=StageStatus.SKIPPED, skip_reason="aborted"),
        ],
    )
    assert rc.stop_reason_text(record) == (
        "stopped at calibre - continue_on_lvs_fail is off"
    )


def test_stop_reason_is_silent_when_nothing_was_left_undone(
    make_run_record,
) -> None:
    record = make_run_record(stages=[_stage("si"), _stage("strmout", status=StageStatus.FAILED)])
    assert rc.stop_reason_text(record) == ""
    assert rc.stop_reason_text(make_run_record(stages=[_stage("si")])) == ""


def test_applied_patch_count_only_counts_hunks_that_changed_a_file(
    make_run_record,
) -> None:
    from auto_ext.core.patch_models import (
        HunkOutcome,
        PatchStatus,
        StagePatchReport,
    )

    report = StagePatchReport(
        stage="calibre",
        template_id="lvs.qci",
        outcomes=[
            HunkOutcome(hunk_id="a", status=PatchStatus.CLEAN),
            HunkOutcome(hunk_id="b", status=PatchStatus.SHIFTED),
            HunkOutcome(hunk_id="c", status=PatchStatus.NOOP),
            HunkOutcome(hunk_id="d", status=PatchStatus.DISABLED),
            HunkOutcome(hunk_id="e", status=PatchStatus.ABSORBED),
        ],
    )
    record = make_run_record(patch_reports=[report])
    assert rc.applied_patch_count(record) == 2
    assert rc.applied_patch_count(make_run_record()) == 0


# ---- the compact delta (pure) ----------------------------------------------


@pytest.mark.parametrize(
    ("current", "previous", "expected", "color"),
    [
        (3, 17, f"17 -> 3 {rc.GLYPH_DOWN}14", rc.COLOR_PASS),
        (17, 3, f"3 -> 17 {rc.GLYPH_UP}14", rc.COLOR_FAIL),
        (3, 3, "unchanged at 3", None),
        (3, None, "first run of this cell", None),
        (None, 3, "not comparable", None),
    ],
)
def test_delta_chip_text(
    current: int | None, previous: int | None, expected: str, color: str | None
) -> None:
    from auto_ext.core.checks import compare_discrepancies

    text, got = rc.delta_chip_text(compare_discrepancies(current, previous))
    assert text == expected
    if color is not None:
        assert got == color
    # Never the accent, and never blank.
    assert got not in theme.accent_colors()
    assert text.strip()


def test_delta_chip_uses_only_whitelisted_glyphs() -> None:
    assert rc.GLYPH_DOWN in "\u25bc"
    assert rc.GLYPH_UP in "\u25b4"
    assert rc.GLYPH_COLLAPSED in "\u25be"
    assert rc.GLYPH_EXPANDED in "\u25b4"


# ---- per-class actions (pure) -----------------------------------------------


def test_every_code_gets_exactly_one_primary_and_one_secondary_action() -> None:
    for code in fc.CODE_ORDER:
        actions = rc.failure_actions(code, log_name="calibre.log", discrepancies=3)
        assert len(actions) == 2, code
        assert [a.primary for a in actions] == [True, False], code
        assert all(a.label.strip() for a in actions), code


def test_action_sets_differ_per_class() -> None:
    """Canvas 1e gives each class its own pair of buttons."""

    ids = {
        code: tuple(a.action_id for a in rc.failure_actions(code))
        for code in fc.CODE_ORDER
    }
    assert ids[fc.CODE_LICENSE][0] == rc.ACTION_RERUN
    assert ids[fc.CODE_CONFIG][0] == rc.ACTION_OPEN_SETUP
    assert ids[fc.CODE_LVS][0] == rc.ACTION_OPEN_CALIBRE
    assert ids[fc.CODE_CRASH][0] == rc.ACTION_OPEN_LOG
    assert len(set(ids.values())) == len(fc.CODE_ORDER), (
        "each class gets its own pair of buttons"
    )


def test_the_lvs_secondary_action_counts_the_discrepancies() -> None:
    labelled = rc.failure_actions(fc.CODE_LVS, discrepancies=3)[1].label
    assert labelled == "Show 3 discrepancies"
    assert rc.failure_actions(fc.CODE_LVS)[1].label == "Show the LVS detail"


# ============================================================================
# the widget
# ============================================================================


def _plan(*, ok: bool = True, reason: str = "nope") -> HandoffPlan:
    return HandoffPlan(
        argv=("/opt/calibre", "-gui", "-lvs", "-runset", "/r/rendered/lvs.qci"),
        cwd=Path("/wa"),
        env={},
        runset=Path("/r/rendered/lvs.qci"),
        stage_key="calibre",
        executable="calibre",
        reasons=() if ok else (reason,),
    )


@pytest.fixture
def populated_run(make_run_record, run_dir: Path):
    """A run on disk with logs, a rendered runset and an archived LVS report."""

    (run_dir / "logs" / "si.log").write_text("si output\n", encoding="utf-8")
    (run_dir / "logs" / "calibre.log").write_text("calibre output\n", encoding="utf-8")
    (run_dir / "rendered" / "lvs.qci").write_text("*lvsRunDir: /wa\n", encoding="utf-8")
    (run_dir / "results" / "lvs.report").write_text(
        _REPORT_WITH_MISMATCH, encoding="utf-8"
    )
    artifact = run_dir / "svdb_placeholder"
    artifact.write_text("db\n", encoding="utf-8")

    record = make_run_record(
        run_dir=run_dir,
        overall=TaskStatus.FAILED,
        dspf_path=str(run_dir / "amp2.dspf"),
        stages=[
            StageRecord(
                key="si",
                stage="si",
                status=StageStatus.PASSED,
                duration_s=12.5,
                log_path="logs/si.log",
                exit_code=0,
            ),
            StageRecord(
                key="calibre",
                stage="calibre",
                status=StageStatus.FAILED,
                duration_s=200.0,
                log_path="logs/calibre.log",
                rendered_path="rendered/lvs.qci",
                exit_code=0,
                artifacts=[str(artifact)],
            ),
            StageRecord(
                key="quantus",
                stage="quantus",
                status=StageStatus.SKIPPED,
                skip_reason="aborted after earlier stage failure",
            ),
        ],
        results=RunResults(
            lvs=LvsResult(
                passed=False,
                banner="INCORRECT",
                discrepancies=3,
                source_path="/wa/amp2.lvs.report",
                archived_path="results/lvs.report",
            )
        ),
    )
    return record, run_dir


def test_result_card_min_height_stays_within_budget_when_empty(qtbot) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    assert card.minimumSizeHint().height() < MIN_HEIGHT_BUDGET


def test_result_card_min_height_stays_within_budget_when_full(
    qtbot, populated_run
) -> None:
    """The scroll area + splitter must absorb the content, not the parent.

    Without them, five stage rows plus three failure groups plus six output
    rows would each add their own minimum height and the card alone would be
    taller than a 1080p screen's usable area.
    """

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    assert card.minimumSizeHint().height() < MIN_HEIGHT_BUDGET


def test_result_card_starts_on_a_centered_placeholder(qtbot) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    assert card.record is None
    assert not card._placeholder.isHidden()
    assert card._body.isHidden()
    assert card._placeholder.alignment() & Qt.AlignCenter
    assert card._placeholder.text().strip()


def test_result_card_show_message_replaces_the_body(qtbot) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    card.show_message("run.json is unreadable")
    assert card._placeholder.text() == "run.json is unreadable"
    assert card._body.isHidden()


def test_result_card_header_shows_tally_status_and_dut(qtbot, populated_run) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert card._title.text() == record.default_display_name
    assert card._badge.text() == "FAILED"
    assert rc.COLOR_FAIL in card._badge.styleSheet()
    # 1 of 2 attempted stages passed; the skipped one is reported separately.
    assert "1/2 stages passed" in card._meta.text()
    assert "1 skipped" in card._meta.text()
    assert "amp2" in card._subtitle.text()
    assert "layout" in card._subtitle.text()


def test_result_card_display_name_prefers_the_annotation(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(
        record,
        run_dir=run_dir,
        annotations=RunAnnotations(display_name="golden run", note="keep this"),
    )
    assert card._title.text() == "golden run"
    assert card._note.text() == "keep this"
    assert not card._note.isHidden()


def test_result_card_stage_rows_carry_status_duration_and_artifacts(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    tree = card._stage_tree
    assert tree.topLevelItemCount() == 3
    si = tree.topLevelItem(0)
    assert si.text(0) == "si"
    assert "passed" in si.text(1)
    assert si.text(2) == "12.5 s"
    assert si.text(3) == "logs/si.log"

    calibre = tree.topLevelItem(1)
    assert calibre.text(2) == "3m 20s"
    # ToolResult.artifact_paths, which nothing consumed before, become children.
    assert calibre.childCount() == 1
    assert calibre.child(0).text(0) == "svdb_placeholder"

    skipped = tree.topLevelItem(2)
    assert "skipped" in skipped.text(1)
    assert skipped.toolTip(1) == "aborted after earlier stage failure"


def test_result_card_lvs_band_is_the_1d_headline(qtbot, populated_run) -> None:
    """Banner, count and CELL SUMMARY, as canvas 1d lays them out."""

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert card._lvs_banner.text() == "INCORRECT"
    assert rc.COLOR_FAIL in card._lvs_banner.styleSheet()
    assert card._lvs_count.text() == "3"
    assert rc.COLOR_FAIL in card._lvs_count.styleSheet()
    # Mono 20/700: the one oversized string the design allows.
    assert f"font-size: {theme.FONT_SIZE_MONO_HERO}px" in card._lvs_banner.styleSheet()

    # Names come out of the archived report even though LvsResult.cell_summary
    # is empty, which is what core.checks leaves it as today.
    summary = card._cell_summary.text()
    assert "amp2" in summary and "dco_core" in summary
    assert summary.index("amp2") < summary.index("bias"), "mismatches lead"
    assert "INCORRECT" in summary and "CORRECT" in summary


def test_result_card_lvs_banner_is_marked_as_the_authority(
    qtbot, populated_run
) -> None:
    """core.checks fails a clean count under an INCORRECT banner; say so."""

    from PyQt5.QtWidgets import QLabel

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    texts = [child.text() for child in card._lvs_band.findChildren(QLabel)]
    assert any("authoritative" in t for t in texts)
    assert any(t == "LVS banner" for t in texts)
    assert any(t == "DISCREPANCIES" for t in texts)
    assert any(t == "CELL SUMMARY" for t in texts)


def test_result_card_states_an_absent_discrepancy_count_as_absent(
    qtbot, make_run_record
) -> None:
    """Calibre v2019.2 omits the count; a guessed 0 would be a lie."""

    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[_stage("calibre", status=StageStatus.FAILED)],
        results=RunResults(lvs=LvsResult(passed=False, banner="INCORRECT")),
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    assert card._lvs_count.text() == "?"
    assert "no DISCREPANCIES count" in card._lvs_count.toolTip()


def test_result_card_lvs_row_without_a_result(qtbot, make_run_record) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(make_run_record())
    assert "No LVS result recorded" in card._lvs_body.text()


def test_result_card_compares_against_the_previous_run(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    previous = RunIndexEntry(
        run_id="20260820T101010Z_amp2-ext",
        run_dir=run_dir.parent / "20260820T101010Z_amp2-ext",
        created_at=datetime(2026, 8, 20, 10, 10, 10, tzinfo=timezone.utc),
        ended_at=None,
        overall="failed",
        slug="amp2-ext",
        library="EXAMPLE_LIB",
        cell="amp2",
        layout_view="layout",
        source_view="schematic",
        recipe_id="ext",
        dry_run=False,
        lvs_passed=False,
        lvs_discrepancies=17,
        display_name="amp2 - ext",
        tags=(),
        starred=False,
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir, previous=previous)

    body = card._lvs_body.text()
    assert "17 -> 3 (down 14)" in body
    # The run id is long; it lives in the tooltip so the caption stays short.
    assert "20260820T101010Z_amp2-ext" in card._lvs_body.toolTip()

    # The compact chip beside the number is the canvas 1d form.
    assert card._lvs_delta.text() == f"17 -> 3 {rc.GLYPH_DOWN}14"
    assert rc.COLOR_PASS in card._lvs_delta.styleSheet()

    delta = card.discrepancy_delta
    assert delta is not None
    assert (delta.previous, delta.current, delta.delta) == (17, 3, -14)


def test_result_card_says_so_when_there_is_nothing_to_compare(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    assert "No earlier run of this cell" in card._lvs_body.text()


def _failure_texts(card: ResultCard) -> list[str]:
    from PyQt5.QtWidgets import QLabel

    return [child.text() for child in card._failures_group.findChildren(QLabel)]


def _failure_buttons(card: ResultCard) -> list:
    from PyQt5.QtWidgets import QPushButton

    return list(card._failures_group.findChildren(QPushButton))


def _verdict(cls: FailureClass) -> FailureVerdict:
    return FailureVerdict(
        failure_class=cls,
        confidence=Confidence.CERTAIN,
        reason=f"{cls.value} happened",
        next_action="do something",
    )


def _failed_stage(key: str, verdict: FailureVerdict) -> StageRecord:
    return StageRecord(
        key=key,
        stage=key,
        status=StageStatus.FAILED,
        details={rc.DETAILS_FAILURE_KEY: verdict.as_dict()},
    )


def test_result_card_failure_row_carries_a_code_a_reason_and_two_buttons(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert not card._failures_group.isHidden()
    chips = card._failures_group.findChildren(FailureChip)
    assert [c.code for c in chips] == [fc.CODE_LVS]

    texts = _failure_texts(card)
    assert any("LVS mismatch" in t for t in texts)
    assert any(t.startswith("Next: ") for t in texts)
    assert any("CELL SUMMARY" in t for t in texts), "the core's next_action"
    # The heading names who has to act, not what class the verdict was.
    assert any(fc.ACTOR_TITLES[fc.ACTOR_DESIGN] in t for t in texts)

    labels = [b.text() for b in _failure_buttons(card)]
    assert labels == ["Open in Calibre Interactive", "Show 3 discrepancies"]


def test_result_card_orders_failures_by_who_has_to_act(
    qtbot, make_run_record
) -> None:
    """Canvas 1e: environment first, design second, unclassified last."""

    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[
            # Deliberately recorded in the *wrong* order.
            _failed_stage("quantus", _verdict(FailureClass.TOOL_CRASH)),
            _failed_stage("jivaro", _verdict(FailureClass.UNKNOWN)),
            _failed_stage("calibre", _verdict(FailureClass.LVS_MISMATCH)),
            _failed_stage("si", _verdict(FailureClass.LICENSE_UNAVAILABLE)),
            _failed_stage("strmout", _verdict(FailureClass.ENVIRONMENT)),
        ],
    )
    expected = [
        fc.CODE_LICENSE,
        fc.CODE_CONFIG,
        fc.CODE_LVS,
        fc.CODE_CRASH,
        fc.CODE_UNKNOWN,
    ]
    groups = rc.sort_failure_groups(rc.group_failures(record))
    assert [g.code for g in groups] == expected

    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    chips = card._failures_group.findChildren(FailureChip)
    assert [c.code for c in chips] == expected
    # All three headings, once each, in canvas order.
    texts = _failure_texts(card)
    headings = [t for t in texts if t in fc.ACTOR_TITLES.values()]
    assert headings == [
        fc.ACTOR_TITLES[fc.ACTOR_ENVIRONMENT],
        fc.ACTOR_TITLES[fc.ACTOR_DESIGN],
        fc.ACTOR_TITLES[fc.ACTOR_UNCLASSIFIED],
    ]


def test_result_card_header_code_chip_is_the_one_that_outlives_a_fix(
    qtbot, make_run_record
) -> None:
    """Fixing the license does not make the LVS mismatch go away."""

    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[
            _failed_stage("si", _verdict(FailureClass.LICENSE_UNAVAILABLE)),
            _failed_stage("calibre", _verdict(FailureClass.LVS_MISMATCH)),
        ],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    assert card._code_chip.code == fc.CODE_LVS
    assert not card._code_chip.isHidden()


def test_result_card_hides_the_code_chip_on_a_clean_run(
    qtbot, make_run_record
) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(make_run_record(overall=TaskStatus.PASSED, stages=[_stage("si")]))
    assert card._code_chip.isHidden()


def test_result_card_exposes_the_ordered_groups_and_computes_them_once(
    qtbot, populated_run
) -> None:
    """Classifying reads the archived log; the header and the band share one."""

    record, run_dir = populated_run
    calls: list[object] = []
    real = rc.group_failures

    def counting(rec, **kw):
        calls.append(rec)
        return real(rec, **kw)

    card = ResultCard()
    qtbot.addWidget(card)
    rc.group_failures = counting  # type: ignore[assignment]
    try:
        card.set_run(record, run_dir=run_dir)
    finally:
        rc.group_failures = real  # type: ignore[assignment]

    assert len(calls) == 1
    assert [g.code for g in card.failure_groups] == [fc.CODE_LVS]


def test_result_card_an_unclassified_failure_is_never_blank(
    qtbot, make_run_record
) -> None:
    """Nothing matched, so the row has to say so and still offer a next step."""

    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[_stage("quantus", status=StageStatus.FAILED)],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)

    chips = card._failures_group.findChildren(FailureChip)
    assert [c.code for c in chips] == [fc.CODE_UNKNOWN]
    assert chips[0].text() == "UNK"
    # Grey, never red: the classifier declined to blame the design.
    assert theme.STATUS_FAILED not in chips[0].styleSheet()

    texts = _failure_texts(card)
    assert any("Unclassified" in t for t in texts)
    # This stage archived no log, so the next step must not be "read it" and
    # cannot be "quote its line into failure_signatures.yaml" either.
    assert not any("failure_signatures.yaml" in t for t in texts)
    assert any("archived no log" in t for t in texts)
    assert [b.text() for b in _failure_buttons(card)] == [
        "Open quantus.log",
        "Copy the log line",
    ]


def test_result_card_a_cancelled_stage_gets_its_own_code(
    qtbot, make_run_record
) -> None:
    record = make_run_record(
        overall=TaskStatus.CANCELLED,
        stages=[_stage("calibre", status=StageStatus.CANCELLED)],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    chips = card._failures_group.findChildren(FailureChip)
    assert [c.code for c in chips] == [fc.CODE_CANCELLED]
    assert fc.actor_for(fc.CODE_CANCELLED) == fc.ACTOR_ENVIRONMENT


def test_result_card_config_failure_offers_the_setup_drawer(
    qtbot, make_run_record
) -> None:
    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[_stage("strmout", status=StageStatus.FAILED, exit_code=127)],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    button = next(b for b in _failure_buttons(card) if b.text() == "Fix in Setup")
    with qtbot.waitSignal(card.setup_requested, timeout=1000) as blocker:
        button.click()
    assert blocker.args == ["Paths"]


def test_result_card_copy_the_evidence_is_disabled_without_evidence(
    qtbot, make_run_record
) -> None:
    # No exit code and no log: nothing to classify and nothing to quote.
    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[_stage("quantus", status=StageStatus.FAILED)],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    button = next(
        b for b in _failure_buttons(card) if b.text() == "Copy the log line"
    )
    assert not button.isEnabled()
    assert "no evidence" in button.toolTip()


def test_result_card_copy_the_evidence_copies_the_recorded_line(
    qtbot, make_run_record
) -> None:
    verdict = FailureVerdict(
        failure_class=FailureClass.LICENSE_UNAVAILABLE,
        confidence=Confidence.SIGNATURE,
        reason="log matched signature 'calibre.no_license'",
        next_action="Check the license server.",
        evidence="ERROR: could not check out license calibre_lvs (all in use)",
        signature_id="calibre.no_license",
    )
    record = make_run_record(
        overall=TaskStatus.FAILED,
        stages=[_failed_stage("calibre", verdict)],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    button = next(
        b for b in _failure_buttons(card) if b.text() == "Copy the log line"
    )
    assert button.isEnabled()
    with qtbot.waitSignal(card.copy_requested, timeout=1000) as blocker:
        button.click()
    assert blocker.args == [verdict.evidence]


def test_result_card_hides_the_failures_group_on_a_clean_run(
    qtbot, make_run_record
) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(
        make_run_record(overall=TaskStatus.PASSED, stages=[_stage("si")])
    )
    assert card._failures_group.isHidden()


def _artifact_rows(card: ResultCard) -> dict[str, PathLabel]:
    """``{label: PathLabel}`` for the artifacts grid, in row order."""

    from PyQt5.QtWidgets import QLabel

    grid = card._artifacts_layout
    out: dict[str, PathLabel] = {}
    for row in range(grid.rowCount()):
        name_item = grid.itemAtPosition(row, 0)
        value_item = grid.itemAtPosition(row, 1)
        if name_item is None or value_item is None:
            continue
        name = name_item.widget()
        value = value_item.widget()
        if isinstance(name, QLabel) and isinstance(value, PathLabel):
            out[name.text()] = value
    return out


def test_result_card_artifacts_are_the_canvas_1c_rows(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    rows = _artifact_rows(card)
    assert "output dir" in rows
    assert "extracted" in rows
    assert "lvs report" in rows
    assert "rendered calibre" in rows
    assert "run dir" in rows
    # The extracted view is a lib/cell/view triple, not a filesystem path.
    assert rows["extracted"].full_text() == "EXAMPLE_LIB / amp2 / av_extracted"


def test_result_card_artifact_paths_are_clickable_when_they_exist(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    rows = _artifact_rows(card)
    report = rows["lvs report"]
    assert report.is_live()
    assert report.path == run_dir / "results" / "lvs.report"
    with qtbot.waitSignal(card.artifact_requested, timeout=1000) as blocker:
        report.clicked.emit(report.path)
    assert blocker.args == [run_dir / "results" / "lvs.report"]

    # The workarea was never created, so that row is inert and says why.
    workarea = rows["output dir"]
    assert not workarea.is_live()
    assert workarea.toolTip().startswith("Not on this host")


def test_result_card_a_deleted_lvs_report_says_what_happened(
    qtbot, make_run_record
) -> None:
    record = make_run_record(
        overall=TaskStatus.FAILED,
        results=RunResults(
            lvs=LvsResult(
                passed=False, banner="INCORRECT", source_path="/wa/amp2.lvs.report"
            )
        ),
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    row = _artifact_rows(card)["lvs report"]
    assert not row.is_live()
    assert "overwrote" in row.toolTip()


def test_result_card_copy_button_emits_the_view_triple(
    qtbot, populated_run
) -> None:
    from PyQt5.QtWidgets import QPushButton

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    copy_btn = next(
        b
        for b in card._artifacts_group.findChildren(QPushButton)
        if b.text() == "Copy"
    )
    with qtbot.waitSignal(card.copy_requested, timeout=1000) as blocker:
        copy_btn.click()
    assert blocker.args == ["EXAMPLE_LIB / amp2 / av_extracted"]


def test_result_card_stage_logs_are_collapsed_until_asked_for(
    qtbot, populated_run
) -> None:
    """Canvas 1c/1d draw no tree; a hidden one also costs no minimum height."""

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert not card.stage_logs_visible()
    assert card._log_toggle.text().endswith(rc.GLYPH_COLLAPSED)
    card._log_toggle.click()
    assert card.stage_logs_visible()
    assert card._log_toggle.text().endswith(rc.GLYPH_EXPANDED)
    card._log_toggle.click()
    assert not card.stage_logs_visible()


def test_result_card_open_log_button_tracks_the_selection(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    card.toggle_stage_logs(True)

    # Nothing selected yet.
    assert not card._open_log_btn.isEnabled()

    card._stage_tree.setCurrentItem(card._stage_tree.topLevelItem(0))
    assert card._open_log_btn.isEnabled()
    with qtbot.waitSignal(card.log_requested, timeout=1000) as blocker:
        card._open_log_btn.click()
    assert blocker.args == [run_dir / "logs" / "si.log"]

    # The skipped stage produced no log -> the button goes back to disabled.
    card._stage_tree.setCurrentItem(card._stage_tree.topLevelItem(2))
    assert not card._open_log_btn.isEnabled()
    assert "produced a log" in card._open_log_btn.toolTip()


def test_result_card_double_click_opens_logs_and_artifacts(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    calibre = card._stage_tree.topLevelItem(1)
    with qtbot.waitSignal(card.log_requested, timeout=1000) as blocker:
        card._on_stage_double_click(calibre, 0)
    assert blocker.args == [run_dir / "logs" / "calibre.log"]

    artifact_item = calibre.child(0)
    with qtbot.waitSignal(card.artifact_requested, timeout=1000) as blocker:
        card._on_stage_double_click(artifact_item, 0)
    assert blocker.args == [run_dir / "svdb_placeholder"]


def test_result_card_stage_context_menu_is_deferred_and_state_aware(
    qtbot, populated_run
) -> None:
    """The popup must be deferred by one event-loop tick.

    On X11 the context-menu event arrives on button *press*; a synchronous
    ``exec_()`` is then closed by the release, which is the "you have to
    right-click twice" bug. Patching ``exec_`` and pumping the loop is how the
    deferral is observable.
    """

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    card.toggle_stage_logs(True)
    card.show()

    captured: dict[str, object] = {}
    real_exec = rc.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = [a.text() for a in self.actions()]
        captured["enabled"] = [a.isEnabled() for a in self.actions()]
        return None

    rc.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        item = card._stage_tree.topLevelItem(1)
        pos = card._stage_tree.visualItemRect(item).center()
        card._on_stage_menu(pos)
        assert "actions" not in captured, "popup must not be synchronous"
        qtbot.wait(10)
    finally:
        rc.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert captured["actions"] == [
        "Open log file",
        "Open rendered input",
        "Copy path",
    ]
    # calibre has both a log and an archived runset on disk.
    assert captured["enabled"] == [True, True, True]


def test_result_card_stage_context_menu_disables_missing_files(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    card.toggle_stage_logs(True)
    card.show()

    captured: dict[str, object] = {}
    real_exec = rc.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["enabled"] = [a.isEnabled() for a in self.actions()]
        captured["tooltips"] = [a.toolTip() for a in self.actions()]
        return None

    rc.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        # The skipped stage has neither a log nor a rendered input.
        item = card._stage_tree.topLevelItem(2)
        card._on_stage_menu(card._stage_tree.visualItemRect(item).center())
        qtbot.wait(10)
    finally:
        rc.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert captured["enabled"] == [False, False, False]
    assert "no log" in captured["tooltips"][0]


def test_result_card_context_menu_ignores_empty_space(
    qtbot, populated_run
) -> None:
    from PyQt5.QtCore import QPoint

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    opened: list[object] = []
    real_exec = rc.QMenu.exec_
    rc.QMenu.exec_ = lambda self, *a, **k: opened.append(self)  # type: ignore
    try:
        card._on_stage_menu(QPoint(5, 5000))
        qtbot.wait(10)
    finally:
        rc.QMenu.exec_ = real_exec  # type: ignore[method-assign]
    assert opened == []


# ---- Calibre Interactive hand-off ------------------------------------------


def test_handoff_button_disabled_with_the_plan_reason(
    qtbot, populated_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, run_dir = populated_run
    monkeypatch.setattr(
        rc,
        "plan_calibre_handoff",
        lambda rec, *a, **k: _plan(ok=False, reason="Calibre was not found on PATH."),
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert not card._handoff_btn.isEnabled()
    assert card._handoff_btn.toolTip() == "Calibre was not found on PATH."


def test_handoff_button_enabled_and_emits_the_record(
    qtbot, populated_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, run_dir = populated_run
    monkeypatch.setattr(rc, "plan_calibre_handoff", lambda rec, *a, **k: _plan())
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert card._handoff_btn.isEnabled()
    assert "-runset" in card._handoff_btn.toolTip()
    with qtbot.waitSignal(card.handoff_requested, timeout=1000) as blocker:
        card._handoff_btn.click()
    emitted = blocker.args[0]
    assert emitted.run_id == record.run_id
    assert emitted.run_dir == str(run_dir)


def test_handoff_uses_the_real_planner_when_the_run_has_no_calibre_stage(
    qtbot, make_run_record
) -> None:
    """No monkeypatching: the genuine pre-flight must refuse and explain."""

    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(make_run_record(stages=[_stage("si")]))
    assert not card._handoff_btn.isEnabled()
    assert card._handoff_btn.toolTip().strip()


def test_handoff_record_fills_in_a_missing_run_dir(
    qtbot, make_run_record, run_dir: Path
) -> None:
    record = make_run_record()
    assert not record.run_dir
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    prepared = card.handoff_record()
    assert prepared is not None
    assert prepared.run_dir == str(run_dir)
    # The original record is untouched -- run.json is immutable.
    assert not record.run_dir


def test_result_card_clear_drops_the_record_and_the_plan(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    assert card.record is not None
    card.clear()
    assert card.record is None
    assert card.run_dir is None
    assert card.handoff_plan is None
    assert card._stage_tree.topLevelItemCount() == 0
    assert card._body.isHidden()


def test_result_card_prints_the_command_line_and_the_frozen_runset(
    qtbot, populated_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1d's reassurance: the hand-off can be verified without pressing it."""

    record, run_dir = populated_run
    runset = run_dir / "rendered" / "lvs.qci"
    monkeypatch.setattr(
        rc,
        "plan_calibre_handoff",
        lambda rec, *a, **k: HandoffPlan(
            argv=("/opt/calibre", "-gui", "-lvs", "-runset", str(runset)),
            cwd=run_dir,
            env={},
            runset=runset,
            stage_key="calibre",
            executable="calibre",
        ),
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert "-runset" in card._launch_line.full_text()
    assert "-batch" not in card._launch_line.full_text()
    assert card._runset_line.full_text() == str(runset)
    assert card._runset_line.is_live()
    assert "frozen" in card._runset_note.text()


def test_result_card_says_when_the_frozen_runset_is_gone(
    qtbot, populated_run, monkeypatch: pytest.MonkeyPatch
) -> None:
    record, run_dir = populated_run
    missing = run_dir / "rendered" / "gone.qci"
    monkeypatch.setattr(
        rc,
        "plan_calibre_handoff",
        lambda rec, *a, **k: HandoffPlan(
            argv=("/opt/calibre", "-gui", "-lvs", "-runset", str(missing)),
            cwd=run_dir,
            env={},
            runset=missing,
            stage_key="calibre",
            executable="calibre",
            reasons=("The Calibre runset for this run is missing.",),
        ),
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert not card._handoff_btn.isEnabled()
    assert not card._runset_line.is_live()
    assert card._runset_note.text() == "(missing)"


def test_result_card_stage_strip_is_the_1c_row(qtbot, populated_run) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    chips = card._stage_strip.findChildren(Chip)
    texts = [c.text() for c in chips]
    assert texts[0].startswith("si ")
    assert any(t.startswith("calibre ") for t in texts)
    tones = {c.text().split()[0]: c.tone for c in chips}
    assert tones["si"] == fc.CHIP_TONE_PASSED
    assert tones["calibre"] == fc.CHIP_TONE_FAILED
    # Never the accent: a stage state is a status, not a selection.
    for chip in chips:
        assert not any(a in chip.styleSheet() for a in theme.accent_colors())


def test_result_card_side_buttons_track_what_is_really_on_disk(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert card._report_btn.isEnabled()
    assert card._calibre_log_btn.isEnabled()
    with qtbot.waitSignal(card.artifact_requested, timeout=1000) as blocker:
        card._report_btn.click()
    assert blocker.args == [run_dir / "results" / "lvs.report"]
    with qtbot.waitSignal(card.log_requested, timeout=1000) as blocker:
        card._calibre_log_btn.click()
    assert blocker.args == [run_dir / "logs" / "calibre.log"]


def test_result_card_side_buttons_explain_themselves_when_dead(
    qtbot, make_run_record
) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(make_run_record(stages=[_stage("si")]))
    assert not card._report_btn.isEnabled()
    assert "no LVS report" in card._report_btn.toolTip()
    assert not card._calibre_log_btn.isEnabled()
    assert "no calibre log" in card._calibre_log_btn.toolTip()


def test_result_card_rerun_is_a_request_carrying_the_record(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    with qtbot.waitSignal(card.rerun_requested, timeout=1000) as blocker:
        card._rerun_btn.click()
    assert blocker.args[0].run_id == record.run_id


def test_result_card_header_reports_applied_manual_edits(
    qtbot, make_run_record
) -> None:
    """The escape hatch has to be visible in the record it produced."""

    from auto_ext.core.patch_models import (
        HunkOutcome,
        PatchStatus,
        StagePatchReport,
    )

    record = make_run_record(
        patch_reports=[
            StagePatchReport(
                stage="calibre",
                template_id="lvs.qci",
                outcomes=[
                    HunkOutcome(hunk_id="a", status=PatchStatus.CLEAN),
                    HunkOutcome(hunk_id="b", status=PatchStatus.SHIFTED),
                    HunkOutcome(hunk_id="c", status=PatchStatus.CLEAN),
                ],
            )
        ]
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    assert "3 manual edits applied" in card._meta.text()


def test_result_card_show_lvs_detail_does_not_raise_off_screen(
    qtbot, populated_run
) -> None:
    """The "Show N discrepancies" action, exercised without a visible window."""

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    card.show_lvs_detail()
