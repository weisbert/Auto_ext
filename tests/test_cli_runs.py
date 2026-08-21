"""Tests for the ``auto-ext runs`` command group and the views behind it.

Three layers, in this order:

1. :mod:`auto_ext.cli_reporter` unit tests — the LVS normaliser, the failure
   classifier and the formatters. These are pure functions, so they get
   exhaustive coverage here and the CLI tests below only have to prove the
   wiring.
2. ``runs list`` / ``runs show`` / ``runs prune`` through
   :class:`typer.testing.CliRunner`, against run directories written with the
   real :mod:`auto_ext.core.run_store` API.
3. The end-of-run summary's fallback path, for a task whose record never got
   finalized.

Every run directory here is built with the ``frozen_clock`` fixture, because a
run's identity is a UTC timestamp: without a controllable clock no test can
assert a directory name. Assertions on rendered tables run at ``COLUMNS=200``
(see :func:`wide_console`) so a sentence under test is never split across two
lines by Rich's wrapping.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app
from auto_ext.core.progress import StageStatus, TaskStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render tables wide enough that no asserted string wraps.

    Rich falls back to 80 columns under a non-TTY, which splits the longer
    diagnosis sentences mid-string and makes substring assertions meaningless.
    """

    monkeypatch.setenv("COLUMNS", "200")


# ---- helpers ----------------------------------------------------------------


def _stage(
    key: str,
    status: StageStatus,
    *,
    duration_s: float | None = 4.0,
    exit_code: int | None = 0,
    log: str | None = None,
    rendered: str | None = None,
    artifacts: list[str] | None = None,
    details: dict | None = None,
    error: str | None = None,
    skip_reason: str | None = None,
):
    from auto_ext.model.run import StageRecord

    return StageRecord(
        key=key,
        stage=key.split(".")[0],
        status=status,
        duration_s=duration_s,
        exit_code=exit_code,
        log_path=log if log is not None else f"logs/{key}.log",
        rendered_path=rendered,
        artifacts=artifacts or [],
        details=details or {},
        error=error,
        skip_reason=skip_reason,
    )


def _lvs(
    *,
    passed: bool,
    discrepancies: int | None,
    banner: str | None = None,
    cell_summary: list[str] | None = None,
):
    from auto_ext.model.run import LvsResult

    return LvsResult(
        passed=passed,
        banner=banner or ("CORRECT" if passed else "INCORRECT"),
        discrepancies=discrepancies,
        source_path="/work/lvs/inv.rep",
        archived_path="results/lvs.report",
        cell_summary=cell_summary or [],
    )


def _write_run(
    runs_root: Path,
    make_run_record,
    frozen_clock,
    *,
    cell: str = "amp2",
    library: str = "WB_PLL_DCO",
    layout_view: str = "layout",
    source_view: str = "schematic",
    recipe_id: str = "ext",
    overall=TaskStatus.PASSED,
    duration_s: float = 12.0,
    stages: list | None = None,
    lvs=None,
    dry_run: bool = False,
    tags: tuple[str, ...] = (),
    starred: bool = False,
    display_name: str | None = None,
    note: str | None = None,
    advance_s: float = 60.0,
) -> tuple[Path, object]:
    """Allocate a run directory and write a finished record into it.

    The clock is advanced first so consecutive calls land on distinct seconds
    and therefore on distinct, deterministically ordered directory names.
    """
    from auto_ext.core.run_store import write_annotations, write_record
    from auto_ext.model.run import (
        DutSnapshot,
        RecipeSnapshot,
        RunAnnotations,
        RunResults,
        allocate_run_dir,
        make_run_slug,
        parse_run_id,
    )

    frozen_clock.tick(advance_s)
    dut = DutSnapshot(
        library=library, cell=cell, layout_view=layout_view, source_view=source_view
    )
    recipe = RecipeSnapshot(recipe_id=recipe_id)
    run_dir = allocate_run_dir(runs_root, make_run_slug(dut, recipe))
    created, _ = parse_run_id(run_dir.name)

    record = make_run_record(
        run_dir=run_dir,
        dut=dut,
        recipe=recipe,
        created_at=created,
        overall=overall,
        stages=list(stages or []),
        ended_at=(
            None if overall == TaskStatus.PENDING else created + timedelta(seconds=duration_s)
        ),
        dry_run=dry_run,
        results=RunResults(lvs=lvs) if lvs is not None else RunResults(),
        requested_stages=["si", "strmout", "calibre", "quantus", "jivaro"],
    )
    write_record(run_dir, record)
    if tags or starred or display_name or note:
        write_annotations(
            run_dir,
            RunAnnotations(
                display_name=display_name,
                note=note,
                tags=list(tags),
                starred=starred,
            ),
        )
    return run_dir, record


def _root_of(runs_root: Path) -> str:
    """The ``--auto-ext-root`` value for a given ``runs/`` directory."""

    return str(runs_root.parent)


# ============================================================================
# 1. cli_reporter units
# ============================================================================


class TestLvsView:
    def test_from_none_is_none(self) -> None:
        from auto_ext.cli_reporter import LvsView

        assert LvsView.from_any(None) is None

    def test_from_checks_report_reads_source_and_cell_rows(
        self, fixtures_dir: Path
    ) -> None:
        from auto_ext.cli_reporter import LvsView
        from auto_ext.core.checks import parse_lvs_report_detailed

        report = parse_lvs_report_detailed(fixtures_dir / "lvs_fail.rep")
        view = LvsView.from_any(report)

        assert view is not None
        assert view.passed is False
        assert view.banner == "INCORRECT"
        assert view.discrepancies == report.discrepancies
        # checks.LvsReport spells the path ``source`` and holds a Path.
        assert view.source_path == str(report.source)
        assert view.cell_summary == tuple(report.cell_summary_lines())

    def test_from_lvs_result_reads_archived_path(self) -> None:
        from auto_ext.cli_reporter import LvsView

        view = LvsView.from_any(_lvs(passed=False, discrepancies=3))

        assert view is not None
        assert view.archived_path == "results/lvs.report"
        assert view.discrepancies == 3

    def test_from_plain_dict_out_of_run_json(self) -> None:
        from auto_ext.cli_reporter import LvsView

        view = LvsView.from_any(
            {
                "passed": True,
                "banner": "CORRECT",
                "discrepancies": 0,
                "source_path": "/work/x.rep",
                "archived_path": "results/lvs.report",
                "cell_summary": ["CORRECT inv inv"],
            }
        )

        assert view is not None
        assert view.passed is True
        assert view.cell_summary == ("CORRECT inv inv",)

    def test_effective_discrepancies_falls_back_to_all_correct_table(self) -> None:
        """v2019.2 omits the count on a clean pass; the table states it."""
        from auto_ext.cli_reporter import LvsView

        view = LvsView(
            passed=True,
            banner="CORRECT",
            discrepancies=None,
            cell_summary=("CORRECT inv inv", "CORRECT buf buf"),
        )

        assert view.effective_discrepancies == 0
        assert view.mismatched_cells == ()

    def test_effective_discrepancies_stays_unknown_when_a_cell_mismatched(
        self,
    ) -> None:
        from auto_ext.cli_reporter import LvsView

        view = LvsView(
            passed=False,
            banner="INCORRECT",
            discrepancies=None,
            cell_summary=("CORRECT inv inv", "INCORRECT buf buf"),
        )

        assert view.effective_discrepancies is None
        assert view.mismatched_cells == ("buf",)

    def test_empty_view_is_empty(self) -> None:
        from auto_ext.cli_reporter import LvsView

        assert LvsView().empty is True
        assert LvsView(passed=True).empty is False


class TestFormatters:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (None, "-"),
            (0.0, "0.0s"),
            (12.34, "12.3s"),
            (59.9, "59.9s"),
            (60.0, "1m00s"),
            (252.0, "4m12s"),
            (3840.0, "1h04m"),
        ],
    )
    def test_format_duration(self, seconds, expected) -> None:
        from auto_ext.cli_reporter import format_duration

        assert format_duration(seconds) == expected

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(0, "0s"), (45, "45s"), (60, "1:00"), (605, "10:05"), (7200, "2h00")],
    )
    def test_format_elapsed(self, seconds, expected) -> None:
        from auto_ext.cli_reporter import format_elapsed

        assert format_elapsed(seconds) == expected

    def test_format_when_reads_naive_input_as_utc(self) -> None:
        from datetime import datetime, timezone

        from auto_ext.cli_reporter import format_when

        naive = datetime(2026, 8, 21, 14, 32, 5)
        aware = datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc)
        assert format_when(naive, full=True) == format_when(aware, full=True)
        assert format_when(aware, full=True) == "2026-08-21 14:32:05Z"
        assert format_when(aware) == "08-21 14:32"
        assert format_when(None) == "-"

    def test_format_lvs(self) -> None:
        from auto_ext.cli_reporter import EMPTY, LvsView, format_lvs

        assert format_lvs(None) == EMPTY
        assert format_lvs(LvsView()) == EMPTY
        assert "0" in format_lvs(LvsView(passed=True, discrepancies=0))
        assert "12" in format_lvs(LvsView(passed=False, discrepancies=12))


class TestClassifyStageFailure:
    @pytest.mark.parametrize(
        "status", [StageStatus.PASSED, StageStatus.SKIPPED, StageStatus.DRY_RUN]
    )
    def test_good_stages_are_not_classified(self, status) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        assert classify_stage_failure(stage="si", status=status) is None

    def test_cancelled(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(
            stage="quantus",
            status=StageStatus.CANCELLED,
            error="stage terminated by user cancellation",
            details={"exit_code": -15},
        )

        assert diag is not None
        assert diag.failure_class == "cancelled"
        assert "re-run" in diag.next_action

    def test_lvs_report_missing_beats_the_exit_code(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(
            stage="calibre",
            status=StageStatus.FAILED,
            details={"exit_code": 1, "lvs_report_missing": "/work/lvs/inv.rep"},
        )

        assert diag is not None
        assert diag.failure_class == "lvs-report-missing"
        assert "/work/lvs/inv.rep" in diag.detail
        assert "lvsReportFile" in diag.next_action

    def test_lvs_report_unparsable(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(
            stage="calibre",
            status=StageStatus.FAILED,
            details={"exit_code": 0, "lvs_parse_error": "no LVS banner found"},
        )

        assert diag is not None
        assert diag.failure_class == "lvs-report-unparsable"
        assert "no LVS banner found" in diag.detail

    def test_lvs_mismatch_names_count_cells_and_the_comparison_command(self) -> None:
        from auto_ext.cli_reporter import LvsView, classify_stage_failure

        diag = classify_stage_failure(
            stage="calibre",
            status=StageStatus.FAILED,
            details={"exit_code": 1},
            lvs=LvsView(
                passed=False,
                banner="INCORRECT",
                discrepancies=3,
                archived_path="results/lvs.report",
                cell_summary=("INCORRECT buf buf",),
            ),
            run_id="20260821T143205Z_amp2-ext",
        )

        assert diag is not None
        assert diag.failure_class == "lvs-mismatch"
        assert "3 discrepancy(ies)" in diag.detail
        assert "buf" in diag.detail
        assert "results/lvs.report" in diag.next_action
        assert "auto-ext runs show 20260821T143205Z_amp2-ext" in diag.next_action

    def test_lvs_mismatch_calls_out_a_truncated_correct_report(self) -> None:
        from auto_ext.cli_reporter import LvsView, classify_stage_failure

        diag = classify_stage_failure(
            stage="calibre",
            status=StageStatus.FAILED,
            lvs=LvsView(passed=False, banner="CORRECT", discrepancies=None),
        )

        assert diag is not None
        assert diag.failure_class == "lvs-mismatch"
        assert "truncated" in diag.detail

    def test_render_failed_says_nothing_was_spawned(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(
            stage="quantus",
            status=StageStatus.FAILED,
            error="render failed: 'temperature' is undefined",
        )

        assert diag is not None
        assert diag.failure_class == "render-failed"
        assert "no subprocess was spawned" in diag.next_action

    def test_no_template(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(
            stage="jivaro",
            status=StageStatus.FAILED,
            error=(
                "no template configured for jivaro: neither project.templates.jivaro "
                "nor task.templates.jivaro is set"
            ),
        )

        assert diag is not None
        assert diag.failure_class == "no-template"
        assert "project.templates.jivaro" in diag.next_action

    def test_exit_127_is_a_missing_binary_and_names_it(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(
            stage="quantus",
            status=StageStatus.FAILED,
            details={"exit_code": 127, "argv": ["qrc", "-cmd", "ext.cmd"]},
        )

        assert diag is not None
        assert diag.failure_class == "tool-not-found"
        assert "qrc" in diag.detail
        assert "run.sh" in diag.next_action

    def test_generic_tool_error_points_at_the_log(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(
            stage="si",
            status=StageStatus.FAILED,
            details={"exit_code": 2, "argv": ["si"]},
            log_path="/runs/x/logs/si.log",
        )

        assert diag is not None
        assert diag.failure_class == "tool-error"
        assert diag.next_action == "read /runs/x/logs/si.log"

    def test_failure_without_any_diagnostic_still_classifies(self) -> None:
        from auto_ext.cli_reporter import classify_stage_failure

        diag = classify_stage_failure(stage="strmout", status=StageStatus.FAILED)

        assert diag is not None
        assert diag.failure_class == "stage-error"
        assert "the strmout log" in diag.next_action


def test_run_failures_makes_log_paths_absolute(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    """``StageRecord.log_path`` is run-relative; a next_action must be openable."""
    from auto_ext.cli_reporter import run_failures

    run_dir, record = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        overall=TaskStatus.FAILED,
        stages=[
            _stage("si", StageStatus.PASSED),
            _stage(
                "calibre",
                StageStatus.FAILED,
                exit_code=1,
                details={"exit_code": 1, "argv": ["calibre"]},
            ),
        ],
    )

    diagnoses = run_failures(record, run_dir=run_dir)

    assert [d.stage for d in diagnoses] == ["calibre"]
    assert str(run_dir / "logs" / "calibre.log") in diagnoses[0].next_action


def test_run_failures_can_drop_the_compare_suggestion(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    """``runs show`` prints the delta itself, so it suppresses the hint."""
    from auto_ext.cli_reporter import run_failures

    run_dir, record = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        overall=TaskStatus.FAILED,
        stages=[_stage("calibre", StageStatus.FAILED, exit_code=1)],
        lvs=_lvs(passed=False, discrepancies=3),
    )

    with_hint = run_failures(record, run_dir=run_dir)[0]
    without = run_failures(record, run_dir=run_dir, suggest_compare=False)[0]

    assert "auto-ext runs show" in with_hint.next_action
    assert "auto-ext runs show" not in without.next_action
    # Either way the archived report is named by absolute path.
    assert str(run_dir / "results" / "lvs.report") in without.next_action


# ============================================================================
# 2. runs list
# ============================================================================


def test_runs_list_empty_root_is_not_an_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["runs", "list", "--auto-ext-root", str(tmp_path)])

    assert result.exit_code == 0, result.stdout
    assert "no runs on record" in result.stdout


def test_runs_list_shows_time_name_cell_status_and_lvs(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.PASSED,
        lvs=_lvs(passed=True, discrepancies=0),
    )
    _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="amp2",
        overall=TaskStatus.FAILED,
        lvs=_lvs(passed=False, discrepancies=12),
    )

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert "Run history" in result.stdout
    assert "inv · ext" in result.stdout
    assert "amp2 · ext" in result.stdout
    assert "failed" in result.stdout
    assert "12" in result.stdout
    # UTC wall clock of the second run: FROZEN_START + 120s.
    assert "08-21 14:34" in result.stdout


def test_runs_list_is_newest_first(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="oldest")
    _write_run(runs_root, make_run_record, frozen_clock, cell="newest")

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.stdout.index("newest") < result.stdout.index("oldest")


def test_runs_list_limit_caps_rows_and_reports_the_total(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    for i in range(5):
        _write_run(runs_root, make_run_record, frozen_clock, cell=f"c{i}")

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root), "--limit", "2"]
    )

    assert result.exit_code == 0, result.stdout
    assert "c4" in result.stdout and "c3" in result.stdout
    assert "c0" not in result.stdout
    assert "2 of 5 matching run(s), 5 on record" in result.stdout


def test_runs_list_limit_zero_shows_everything(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    for i in range(3):
        _write_run(runs_root, make_run_record, frozen_clock, cell=f"c{i}")

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root), "--limit", "0"]
    )

    assert all(f"c{i}" in result.stdout for i in range(3))


def test_runs_list_filters_by_cell(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv")
    _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    result = runner.invoke(
        app,
        ["runs", "list", "--auto-ext-root", _root_of(runs_root), "--cell", "inv"],
    )

    assert result.exit_code == 0, result.stdout
    assert "inv" in result.stdout
    assert "amp2" not in result.stdout


def test_runs_list_task_filter_matches_the_cell_row_key(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    """``--task`` is the same key ``auto-ext run --task`` takes."""
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv", layout_view="layout")
    _write_run(
        runs_root, make_run_record, frozen_clock, cell="inv", layout_view="layout_v2"
    )

    result = runner.invoke(
        app,
        [
            "runs",
            "list",
            "--auto-ext-root",
            _root_of(runs_root),
            "--task",
            "WB_PLL_DCO__inv__layout__schematic",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "1 of 1 matching run(s), 2 on record" in result.stdout


def test_runs_list_failed_filter(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="good")
    _write_run(
        runs_root, make_run_record, frozen_clock, cell="bad", overall=TaskStatus.FAILED
    )

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root), "--failed"]
    )

    assert "bad" in result.stdout
    assert "good" not in result.stdout


def test_runs_list_filter_that_matches_nothing_is_not_an_error(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv")

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root), "--cell", "nope"]
    )

    assert result.exit_code == 0, result.stdout
    assert "no run matches the filter (1 on record" in result.stdout


def test_runs_list_shows_annotations(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        display_name="tapeout candidate",
        tags=("keep",),
        starred=True,
    )

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root)]
    )

    assert "tapeout candidate" in result.stdout
    assert "keep" in result.stdout
    assert "★" in result.stdout


def test_runs_list_marks_dry_runs(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv", dry_run=True)

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root)]
    )

    assert "(dry)" in result.stdout


def test_runs_list_skips_junk_directories_without_dying(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    """A hand-made dir, a legacy ``task_*`` dir and a truncated record."""
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv")
    (runs_root / "scratch").mkdir()
    (runs_root / "task_WB_PLL_DCO__inv__layout__schematic").mkdir()
    broken = runs_root / "20260821T999999Z_broken"
    broken.mkdir()
    (broken / "run.json").write_text('{"schema_version": 2, "run_i', encoding="utf-8")

    result = runner.invoke(
        app, ["runs", "list", "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert "1 of 1 matching run(s), 1 on record" in result.stdout


def test_runs_list_defaults_the_root_to_the_config_dir_parent(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    """``--config-dir`` resolves the root exactly the way ``run`` does."""
    config_dir = runs_root.parent / "config"
    config_dir.mkdir()
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv")

    result = runner.invoke(app, ["runs", "list", "--config-dir", str(config_dir)])

    assert result.exit_code == 0, result.stdout
    assert "inv · ext" in result.stdout


# ============================================================================
# 3. runs show
# ============================================================================


def test_runs_show_reports_stage_status_with_durations(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    run_dir, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        stages=[
            _stage("si", StageStatus.PASSED, duration_s=252.0, rendered="rendered/si.env"),
            _stage("strmout", StageStatus.PASSED, duration_s=8.0),
            _stage("calibre", StageStatus.PASSED, duration_s=3840.0),
            _stage("quantus.ext", StageStatus.PASSED, duration_s=61.0),
            _stage("quantus.dspf", StageStatus.PASSED, duration_s=62.0),
            _stage(
                "jivaro",
                StageStatus.SKIPPED,
                duration_s=None,
                exit_code=None,
                skip_reason="jivaro disabled for task",
            ),
        ],
    )

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert run_dir.name in result.stdout
    assert "Stages" in result.stdout
    assert "4m12s" in result.stdout
    assert "1h04m" in result.stdout
    # Both quantus invocations keep their own row.
    assert "quantus.ext" in result.stdout
    assert "quantus.dspf" in result.stdout
    assert "rendered/si.env" in result.stdout
    assert "jivaro disabled for task" in result.stdout


def test_runs_show_reports_the_structured_lvs_result(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    run_dir, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.FAILED,
        stages=[_stage("calibre", StageStatus.FAILED, exit_code=1)],
        lvs=_lvs(
            passed=False,
            discrepancies=7,
            cell_summary=["CORRECT inv inv", "INCORRECT buf buf"],
        ),
    )

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert "LVS" in result.stdout
    assert "INCORRECT" in result.stdout
    assert "7" in result.stdout
    assert "results/lvs.report" in result.stdout
    assert "INCORRECT: buf" in result.stdout


def test_runs_show_compares_discrepancies_with_the_previous_run(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.FAILED,
        lvs=_lvs(passed=False, discrepancies=2),
    )
    run_dir, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.FAILED,
        lvs=_lvs(passed=False, discrepancies=5),
    )

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert "vs previous run" in result.stdout
    assert "Discrepancies rose from 2 to 5 (+3)." in result.stdout


def test_runs_show_comparison_ignores_a_different_dut(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    """"Previous" is the same cell, not merely the previous run."""
    _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="other",
        lvs=_lvs(passed=True, discrepancies=0),
    )
    run_dir, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.FAILED,
        lvs=_lvs(passed=False, discrepancies=5),
    )

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert "No previous run on record for this DUT" in result.stdout


def test_runs_show_lists_artifacts_and_diagnoses_the_failure(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    run_dir, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.FAILED,
        stages=[
            _stage(
                "calibre",
                StageStatus.FAILED,
                exit_code=1,
                details={"exit_code": 1, "argv": ["calibre"]},
                artifacts=["/work/cds/verify/QCI_PATH_inv/svdb"],
            ),
            _stage(
                "quantus",
                StageStatus.SKIPPED,
                duration_s=None,
                exit_code=None,
                skip_reason="aborted after earlier stage failure",
            ),
        ],
        lvs=_lvs(passed=False, discrepancies=3),
    )

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert "Artifacts" in result.stdout
    assert "/work/cds/verify/QCI_PATH_inv/svdb" in result.stdout
    assert "Diagnosis" in result.stdout
    assert "lvs-mismatch" in result.stdout
    assert "next:" in result.stdout
    assert "aborted after earlier stage failure" in result.stdout
    # The next action names a path the user can actually open...
    assert str(run_dir / "results" / "lvs.report") in result.stdout
    # ...and does not tell them to run the command they are already reading.
    assert "auto-ext runs show" not in result.stdout


def test_runs_show_accepts_latest(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="older")
    run_dir, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="newer")

    result = runner.invoke(
        app, ["runs", "show", "latest", "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert run_dir.name in result.stdout


def test_runs_show_accepts_a_unique_prefix(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    run_dir, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="inv")

    result = runner.invoke(
        app,
        [
            "runs",
            "show",
            run_dir.name[:17],
            "--auto-ext-root",
            _root_of(runs_root),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert run_dir.name in result.stdout


def test_runs_show_ambiguous_prefix_exits_2_and_lists_candidates(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    a, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="inv")
    b, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    result = runner.invoke(
        app, ["runs", "show", "2026", "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 2
    combined = result.stdout + (result.stderr or "")
    assert "matches 2 runs" in combined
    assert a.name in combined and b.name in combined


def test_runs_show_unknown_run_exits_2(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv")

    result = runner.invoke(
        app, ["runs", "show", "nope", "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 2
    assert "no run matching" in (result.stdout + (result.stderr or ""))


def test_runs_show_on_an_empty_history_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["runs", "show", "latest", "--auto-ext-root", str(tmp_path)]
    )

    assert result.exit_code == 2
    assert "no runs recorded yet" in (result.stdout + (result.stderr or ""))


def test_runs_show_unreadable_record_exits_1_with_the_reason(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    """A record from a newer schema is listable but not showable."""
    run_dir, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="inv")
    record_path = run_dir / "run.json"
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    record_path.write_text(json.dumps(data), encoding="utf-8")

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 1
    assert "cannot read" in (result.stdout + (result.stderr or ""))


def test_runs_show_of_an_unfinished_run_says_so(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    run_dir, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.PENDING,
        stages=[],
    )

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert result.exit_code == 0, result.stdout
    assert "no stage recorded" in result.stdout
    assert "pending" in result.stdout


def test_runs_show_prints_annotations(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    run_dir, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        display_name="golden",
        note="kept for the tapeout review",
        tags=("keep",),
        starred=True,
    )

    result = runner.invoke(
        app, ["runs", "show", run_dir.name, "--auto-ext-root", _root_of(runs_root)]
    )

    assert "golden" in result.stdout
    assert "kept for the tapeout review" in result.stdout


# ============================================================================
# 4. runs prune
# ============================================================================


def test_runs_prune_without_yes_only_previews(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    dirs = [
        _write_run(runs_root, make_run_record, frozen_clock, cell=f"c{i}")[0]
        for i in range(4)
    ]

    result = runner.invoke(
        app,
        ["runs", "prune", "--keep", "2", "--auto-ext-root", _root_of(runs_root)],
    )

    assert result.exit_code == 0, result.stdout
    assert "would remove 2 run(s)" in result.stdout
    assert "re-run with --yes" in result.stdout
    assert all(d.is_dir() for d in dirs)


def test_runs_prune_with_yes_deletes_the_oldest(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    dirs = [
        _write_run(runs_root, make_run_record, frozen_clock, cell=f"c{i}")[0]
        for i in range(4)
    ]

    result = runner.invoke(
        app,
        [
            "runs",
            "prune",
            "--keep",
            "2",
            "--yes",
            "--auto-ext-root",
            _root_of(runs_root),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert "removed 2 run(s)" in result.stdout
    assert not dirs[0].exists() and not dirs[1].exists()
    assert dirs[2].is_dir() and dirs[3].is_dir()


def test_runs_prune_keeps_tagged_and_unfinished_runs(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    tagged, _ = _write_run(
        runs_root, make_run_record, frozen_clock, cell="tagged", tags=("keep",)
    )
    pending, _ = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="pending",
        overall=TaskStatus.PENDING,
    )
    doomed, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="doomed")
    newest, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="newest")

    result = runner.invoke(
        app,
        [
            "runs",
            "prune",
            "--keep",
            "1",
            "--yes",
            "--auto-ext-root",
            _root_of(runs_root),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert not doomed.exists()
    assert tagged.is_dir() and pending.is_dir() and newest.is_dir()
    assert "kept 2 pinned / unfinished run(s)" in result.stdout


def test_runs_prune_keep_zero_is_a_no_op(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    run_dir, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="inv")

    result = runner.invoke(
        app,
        ["runs", "prune", "--keep", "0", "--auto-ext-root", _root_of(runs_root)],
    )

    assert result.exit_code == 0, result.stdout
    assert "unlimited" in result.stdout
    assert run_dir.is_dir()


def test_runs_prune_with_nothing_to_remove_says_so(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="inv")

    result = runner.invoke(
        app,
        ["runs", "prune", "--keep", "5", "--auto-ext-root", _root_of(runs_root)],
    )

    assert result.exit_code == 0, result.stdout
    assert "nothing to prune" in result.stdout


# ============================================================================
# 5. the end-of-run summary's fallback path
# ============================================================================


def test_summary_row_falls_back_to_the_task_id_without_a_record() -> None:
    """A task that died before finalize still gets a row."""
    from auto_ext.cli import _summary_rows
    from auto_ext.core.runner import RunSummary, StageResult, TaskResult
    from auto_ext.tools.base import ToolResult

    task = TaskResult(
        task_id="WB_PLL_DCO__inv__layout__schematic",
        overall=TaskStatus.FAILED,
        stages=[
            StageResult(stage="si", status=StageStatus.PASSED),
            StageResult(
                stage="quantus",
                status=StageStatus.FAILED,
                tool_result=ToolResult(
                    success=False,
                    stdout_path=Path("/runs/x/logs/quantus.log"),
                    diagnostics={"exit_code": 127, "argv": ["qrc"]},
                ),
            ),
        ],
    )

    rows = _summary_rows(RunSummary(tasks=[task]))

    assert len(rows) == 1
    assert rows[0].run_id is None
    assert rows[0].label == "WB_PLL_DCO__inv__layout__schematic"
    assert rows[0].stages == (("si", "passed"), ("quantus", "failed"))
    assert [f.failure_class for f in rows[0].failures] == ["tool-not-found"]


def test_summary_row_prefers_the_record_when_there_is_one(
    runs_root: Path, make_run_record, frozen_clock
) -> None:
    from auto_ext.cli import _summary_rows
    from auto_ext.core.runner import RunSummary, TaskResult

    run_dir, record = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="inv",
        overall=TaskStatus.FAILED,
        stages=[
            _stage("calibre", StageStatus.FAILED, exit_code=1),
            _stage("quantus.ext", StageStatus.SKIPPED, duration_s=None, exit_code=None),
        ],
        lvs=_lvs(passed=False, discrepancies=4),
    )
    task = TaskResult(
        task_id=record.dut.key,
        overall=TaskStatus.FAILED,
        run_dir=run_dir,
        record=record,
    )

    rows = _summary_rows(RunSummary(tasks=[task]))

    assert rows[0].run_id == run_dir.name
    assert rows[0].label == "inv · ext"
    # Stage *keys* come from the record, so the second quantus pass survives.
    assert rows[0].stages == (("calibre", "failed"), ("quantus.ext", "skipped"))
    assert rows[0].lvs is not None and rows[0].lvs.discrepancies == 4
    assert [f.failure_class for f in rows[0].failures] == ["lvs-mismatch"]
