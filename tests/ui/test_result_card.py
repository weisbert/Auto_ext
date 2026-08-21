"""Tests for :mod:`auto_ext.ui.widgets.result_card`.

Two halves. The first exercises the pure helpers (formatting, CELL SUMMARY
round-tripping, failure grouping) with no Qt involved; the second drives the
widget itself through ``qtbot``.

The layout assertions matter as much as the content ones: the main window is
already pinned above 900 px by the Project tab, so a new widget that
contributes a tall minimum size makes the application unusable on a 1080p
screen. ``test_result_card_min_height_*`` are the nails for that.
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
from auto_ext.ui.widgets import result_card as rc  # noqa: E402
from auto_ext.ui.widgets.result_card import ResultCard  # noqa: E402

#: A tab must not push the main window's minimum height up; the card is the
#: tallest thing inside the Runs tab, so it is where the budget is spent.
MIN_HEIGHT_BUDGET = 500


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


def test_result_card_lvs_row_shows_banner_count_and_mismatched_cells(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    body = card._lvs_body.text()
    assert "LVS failed" in body
    assert "INCORRECT" in body
    assert "discrepancies" in body
    assert ">3<" in body
    # Names come out of the archived report even though LvsResult.cell_summary
    # is empty, which is what core.checks leaves it as today.
    assert "amp2, dco_core" in body
    assert "bias" not in body.split("Mismatched cells")[1].split("</div>")[0]


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
        library="WB_PLL_DCO",
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
    assert "20260820T101010Z_amp2-ext" in body


def test_result_card_says_so_when_there_is_nothing_to_compare(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)
    assert "No earlier run of this cell" in card._lvs_body.text()


def test_result_card_failures_group_shows_a_next_action(
    qtbot, populated_run
) -> None:
    from PyQt5.QtWidgets import QLabel

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    assert not card._failures_group.isHidden()
    texts = [
        card._failures_layout.itemAt(i).widget().text()
        for i in range(card._failures_layout.count())
        if isinstance(card._failures_layout.itemAt(i).widget(), QLabel)
    ]
    assert any("LVS mismatch" in t for t in texts)
    assert any(t.startswith("Next: ") for t in texts)
    assert any("CELL SUMMARY" in t for t in texts)


def test_result_card_hides_the_failures_group_on_a_clean_run(
    qtbot, make_run_record
) -> None:
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(
        make_run_record(overall=TaskStatus.PASSED, stages=[_stage("si")])
    )
    assert card._failures_group.isHidden()


def test_result_card_outputs_list_dspf_view_triple_and_report(
    qtbot, populated_run
) -> None:
    from PyQt5.QtWidgets import QLabel, QPushButton

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    grid = card._artifacts_layout
    labels = [
        grid.itemAt(i).widget().text()
        for i in range(grid.count())
        if isinstance(grid.itemAt(i).widget(), QLabel)
    ]
    assert "DSPF" in labels
    assert "Extracted view" in labels
    assert "LVS report" in labels
    assert "Run directory" in labels
    # The extracted view is a lib/cell/view triple, not a filesystem path.
    assert any("WB_PLL_DCO / amp2 / av_extracted" == t for t in labels)
    # The archived report exists, so its Open button is live; the DSPF file
    # was never written, so its button is disabled with an explanatory tooltip.
    buttons = {
        grid.itemAt(i).widget().toolTip(): grid.itemAt(i).widget()
        for i in range(grid.count())
        if isinstance(grid.itemAt(i).widget(), QPushButton)
    }
    disabled = [b for t, b in buttons.items() if t.startswith("Not on this host")]
    assert disabled and all(not b.isEnabled() for b in disabled)


def test_result_card_copy_button_emits_the_view_triple(
    qtbot, populated_run
) -> None:
    from PyQt5.QtWidgets import QPushButton

    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

    grid = card._artifacts_layout
    copy_btn = next(
        grid.itemAt(i).widget()
        for i in range(grid.count())
        if isinstance(grid.itemAt(i).widget(), QPushButton)
        and grid.itemAt(i).widget().text() == "Copy"
    )
    with qtbot.waitSignal(card.copy_requested, timeout=1000) as blocker:
        copy_btn.click()
    assert blocker.args == ["WB_PLL_DCO / amp2 / av_extracted"]


def test_result_card_open_log_button_tracks_the_selection(
    qtbot, populated_run
) -> None:
    record, run_dir = populated_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record, run_dir=run_dir)

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
