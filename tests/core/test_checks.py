"""Tests for :mod:`auto_ext.core.checks`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from auto_ext.core.checks import (
    CellSummaryRow,
    DiscrepancyTrend,
    LvsReport,
    compare_discrepancies,
    parse_lvs_report,
    parse_lvs_report_detailed,
)
from auto_ext.core.errors import CheckError


def _write(tmp_path: Path, body: str, name: str = "report.rep") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ---- basic pass / fail ------------------------------------------------------


def test_pass_report_returns_true(tmp_path: Path) -> None:
    rep = _write(tmp_path, "banner: CORRECT\nDISCREPANCIES = 0\n")
    assert parse_lvs_report(rep) is True


def test_fail_report_returns_false(tmp_path: Path) -> None:
    rep = _write(tmp_path, "banner: INCORRECT\nDISCREPANCIES = 3\n")
    assert parse_lvs_report(rep) is False


# ---- banner vs count combinations -------------------------------------------


def test_incorrect_banner_beats_zero_count(tmp_path: Path) -> None:
    rep = _write(tmp_path, "INCORRECT\nDISCREPANCIES = 0\n")
    assert parse_lvs_report(rep) is False


def test_correct_banner_with_nonzero_discrepancies_is_false(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rep = _write(tmp_path, "CORRECT\nDISCREPANCIES = 5\n")
    caplog.set_level(logging.WARNING, logger="auto_ext.core.checks")
    assert parse_lvs_report(rep) is False
    assert any("discrepancies" in m.lower() for m in caplog.messages)


def test_correct_banner_without_discrepancies_is_false(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rep = _write(tmp_path, "banner: CORRECT\n[truncated]\n")
    caplog.set_level(logging.WARNING, logger="auto_ext.core.checks")
    assert parse_lvs_report(rep) is False
    assert any("no discrepancies" in m.lower() for m in caplog.messages)


def test_correct_banner_no_discrepancies_but_cell_summary_passes(tmp_path: Path) -> None:
    # Calibre v2019.2 format: clean pass omits the "DISCREPANCIES = N" line
    # entirely and relies on CELL SUMMARY. The parser must fall through to
    # the CELL SUMMARY scan and return pass when every row is CORRECT.
    body = """
                               OVERALL COMPARISON RESULTS

                         #       #################
                         #       #    CORRECT    #
                         #       #################

**************************************************************************************************************
                                      CELL  SUMMARY
**************************************************************************************************************

  Result         Layout                        Source
  -----------    -----------                   --------------
  CORRECT        LO_Trace_v3                   LO_Trace_v3
"""
    rep = _write(tmp_path, body)
    detail = parse_lvs_report_detailed(rep)
    assert detail.passed is True
    assert detail.banner == "CORRECT"
    assert detail.discrepancies is None


def test_correct_banner_with_incorrect_cell_summary_row_is_false(tmp_path: Path) -> None:
    # Defense-in-depth: if a CELL SUMMARY row says INCORRECT, the top-level
    # INCORRECT banner search should already have fired — but if the word
    # only appears in a row and not in a banner block, treat as fail.
    body = """
                                      CELL  SUMMARY

  Result         Layout                        Source
  -----------    -----------                   --------------
  INCORRECT      cell_a                        cell_a
  CORRECT        cell_b                        cell_b
"""
    rep = _write(tmp_path, body)
    assert parse_lvs_report(rep) is False


# ---- malformed / edge ------------------------------------------------------


def test_no_banner_raises_checkerror(tmp_path: Path) -> None:
    rep = _write(tmp_path, "unrelated content\nno banner here\n")
    with pytest.raises(CheckError, match="no LVS banner"):
        parse_lvs_report(rep)


def test_missing_file_raises_checkerror(tmp_path: Path) -> None:
    with pytest.raises(CheckError, match="missing"):
        parse_lvs_report(tmp_path / "does_not_exist.rep")


def test_empty_file_raises_checkerror(tmp_path: Path) -> None:
    rep = _write(tmp_path, "")
    with pytest.raises(CheckError):
        parse_lvs_report(rep)


def test_correct_not_matched_inside_incorrect(tmp_path: Path) -> None:
    # File contains ONLY "INCORRECT" (not CORRECT). Word boundary prevents
    # CORRECT from matching the substring inside INCORRECT.
    rep = _write(tmp_path, "INCORRECT\nDISCREPANCIES = 4\n")
    detail = parse_lvs_report_detailed(rep)
    assert detail.banner == "INCORRECT"
    assert detail.passed is False


def test_multiple_banners_incorrect_wins(tmp_path: Path) -> None:
    # Per-cell INCORRECT followed by summary CORRECT: strict mode rejects.
    body = "cell foo: INCORRECT\nsummary: CORRECT\nDISCREPANCIES = 0\n"
    rep = _write(tmp_path, body)
    assert parse_lvs_report(rep) is False


def test_discrepancies_case_insensitive(tmp_path: Path) -> None:
    rep = _write(tmp_path, "CORRECT\nDiscrepancies = 0\n")
    assert parse_lvs_report(rep) is True


def test_discrepancies_no_space(tmp_path: Path) -> None:
    rep = _write(tmp_path, "CORRECT\nDISCREPANCIES =0\n")
    assert parse_lvs_report(rep) is True


def test_discrepancies_leading_zero(tmp_path: Path) -> None:
    rep = _write(tmp_path, "CORRECT\nDISCREPANCIES = 00\n")
    assert parse_lvs_report(rep) is True


# ---- structured return ------------------------------------------------------


def test_detailed_returns_structure(tmp_path: Path) -> None:
    rep = _write(tmp_path, "CORRECT\nDISCREPANCIES = 0\n")
    detail = parse_lvs_report_detailed(rep)
    assert isinstance(detail, LvsReport)
    assert detail.passed is True
    assert detail.banner == "CORRECT"
    assert detail.discrepancies == 0
    assert detail.source == rep.resolve()


def test_detailed_fail_structure(tmp_path: Path) -> None:
    rep = _write(tmp_path, "INCORRECT\nDISCREPANCIES = 3\n")
    detail = parse_lvs_report_detailed(rep)
    assert detail.passed is False
    assert detail.banner == "INCORRECT"
    assert detail.discrepancies == 3


def test_parse_lvs_report_is_thin_wrapper(tmp_path: Path) -> None:
    rep = _write(tmp_path, "CORRECT\nDISCREPANCIES = 0\n")
    assert parse_lvs_report(rep) == parse_lvs_report_detailed(rep).passed


# ---- fixture-file tests ----------------------------------------------------


def test_lvs_pass_fixture(fixtures_dir: Path) -> None:
    assert parse_lvs_report(fixtures_dir / "lvs_pass.rep") is True


def test_lvs_fail_fixture(fixtures_dir: Path) -> None:
    assert parse_lvs_report(fixtures_dir / "lvs_fail.rep") is False


def test_lvs_malformed_fixture(fixtures_dir: Path) -> None:
    with pytest.raises(CheckError):
        parse_lvs_report(fixtures_dir / "lvs_malformed.rep")


def test_lvs_pass_no_count_fixture(fixtures_dir: Path) -> None:
    assert parse_lvs_report(fixtures_dir / "lvs_pass_no_count.rep") is False


def test_lvs_conflicting_fixture(fixtures_dir: Path) -> None:
    assert parse_lvs_report(fixtures_dir / "lvs_conflicting.rep") is False


# ---- S1: CELL SUMMARY rows are kept, not just counted -----------------------
#
# The parser has always scanned this table; it only ever kept the verdicts
# long enough to decide pass/fail on the Calibre v2019.2 fallback path. The
# rows themselves are what a GUI needs to answer "which sub-cells did not
# match", so they are now retained on every report.


_MIXED_SUMMARY = """
                  ##############################
                  #         INCORRECT          #
                  ##############################

                                      CELL  SUMMARY

  Result         Layout                        Source
  -----------    -----------                   --------------
  CORRECT        buf_x2                        buf_x2
  INCORRECT      bias_gen                      bias_gen
  INCORRECT      LO_Trace_v3                   LO_Trace_v3
"""


def test_cell_rows_are_kept_on_a_failing_report(tmp_path: Path) -> None:
    rep = _write(tmp_path, _MIXED_SUMMARY)
    detail = parse_lvs_report_detailed(rep)

    assert detail.passed is False
    assert detail.cell_summary_present is True
    assert [row.layout for row in detail.cells] == ["buf_x2", "bias_gen", "LO_Trace_v3"]
    assert [row.result for row in detail.cells] == ["CORRECT", "INCORRECT", "INCORRECT"]


def test_mismatched_cells_lists_only_the_incorrect_rows(tmp_path: Path) -> None:
    detail = parse_lvs_report_detailed(_write(tmp_path, _MIXED_SUMMARY))
    assert detail.mismatched_cells == ("bias_gen", "LO_Trace_v3")
    assert detail.matched_cells == ("buf_x2",)
    assert detail.incorrect_cell_count == 2


def test_cell_rows_are_kept_on_the_v2019_2_pass_path(tmp_path: Path) -> None:
    # Same fixture shape as the fallback pass test above: the rows must
    # survive there too, not only on failures.
    body = """
                  ##############################
                  #          CORRECT           #
                  ##############################

                                      CELL  SUMMARY

  Result         Layout                        Source
  -----------    -----------                   --------------
  CORRECT        cell_a                        cell_a
  CORRECT        cell_b                        cell_b
"""
    detail = parse_lvs_report_detailed(_write(tmp_path, body))
    assert detail.passed is True
    assert detail.matched_cells == ("cell_a", "cell_b")
    assert detail.mismatched_cells == ()


def test_no_cell_summary_section_is_distinguishable_from_an_empty_one(
    tmp_path: Path,
) -> None:
    without = parse_lvs_report_detailed(
        _write(tmp_path, "CORRECT\nDISCREPANCIES = 0\n", name="without.rep")
    )
    assert without.cell_summary_present is False
    assert without.cells == ()

    empty = parse_lvs_report_detailed(
        _write(
            tmp_path,
            "CORRECT\nDISCREPANCIES = 0\nCELL SUMMARY\n(table truncated)\n",
            name="empty.rep",
        )
    )
    assert empty.cell_summary_present is True
    assert empty.cells == ()


def test_cell_row_verdict_is_upper_cased(tmp_path: Path) -> None:
    # The row regex is case-insensitive; the stored verdict is normalised so
    # callers can compare against the CELL_CORRECT / CELL_INCORRECT constants.
    body = "CORRECT\nDISCREPANCIES = 0\nCELL SUMMARY\n  correct  cell_a  cell_a\n"
    detail = parse_lvs_report_detailed(_write(tmp_path, body))
    assert detail.cells[0].result == "CORRECT"
    assert detail.cells[0].passed is True


def test_three_single_token_lines_do_not_form_a_phantom_row(tmp_path: Path) -> None:
    # Regression guard for the row regex: under re.MULTILINE an inter-column
    # ``\s+`` also matches a newline, so three consecutive one-token lines
    # used to parse as one row. Column separators are spaces/tabs only.
    body = "CORRECT\nDISCREPANCIES = 0\nCELL SUMMARY\nCORRECT\ncell_a\ncell_a\n"
    detail = parse_lvs_report_detailed(_write(tmp_path, body))
    assert detail.cells == ()


def test_cell_summary_lines_round_trip_the_three_columns(tmp_path: Path) -> None:
    detail = parse_lvs_report_detailed(_write(tmp_path, _MIXED_SUMMARY))
    assert detail.cell_summary_lines() == [
        "CORRECT buf_x2 buf_x2",
        "INCORRECT bias_gen bias_gen",
        "INCORRECT LO_Trace_v3 LO_Trace_v3",
    ]


def test_cell_row_name_is_the_layout_side() -> None:
    row = CellSummaryRow(result="INCORRECT", layout="lay_cell", source="sch_cell")
    assert row.name == "lay_cell"
    assert row.passed is False
    assert row.as_line() == "INCORRECT lay_cell sch_cell"


# ---- effective_discrepancies ------------------------------------------------


def test_effective_discrepancies_prefers_the_printed_count(tmp_path: Path) -> None:
    detail = parse_lvs_report_detailed(_write(tmp_path, "INCORRECT\nDISCREPANCIES = 7\n"))
    assert detail.effective_discrepancies == 7


def test_effective_discrepancies_is_zero_on_the_v2019_2_clean_pass(
    tmp_path: Path,
) -> None:
    # No DISCREPANCIES line, but the table states every cell is CORRECT.
    # Zero is what the report says, not something inferred.
    body = "CORRECT\nCELL SUMMARY\n  CORRECT  cell_a  cell_a\n"
    detail = parse_lvs_report_detailed(_write(tmp_path, body))
    assert detail.discrepancies is None
    assert detail.effective_discrepancies == 0


def test_effective_discrepancies_is_unknown_when_the_report_states_none(
    tmp_path: Path,
) -> None:
    # A failing report with no count: the INCORRECT row count is NOT a
    # discrepancy count, so the honest answer is None.
    detail = parse_lvs_report_detailed(_write(tmp_path, _MIXED_SUMMARY))
    assert detail.discrepancies is None
    assert detail.incorrect_cell_count == 2
    assert detail.effective_discrepancies is None


def test_effective_discrepancies_unknown_without_any_table(tmp_path: Path) -> None:
    detail = parse_lvs_report_detailed(_write(tmp_path, "INCORRECT\n"))
    assert detail.effective_discrepancies is None


# ---- compare_discrepancies --------------------------------------------------


def _report(count: int | None, *, passed: bool = False) -> LvsReport:
    return LvsReport(
        passed=passed,
        banner="CORRECT" if passed else "INCORRECT",
        discrepancies=count,
        source=Path("report.rep"),
    )


def test_compare_reports_improvement() -> None:
    delta = compare_discrepancies(_report(3), _report(7))
    assert delta.trend is DiscrepancyTrend.IMPROVED
    assert (delta.current, delta.previous, delta.delta) == (3, 7, -4)
    assert delta.comparable is True
    assert "7" in delta.summary and "3" in delta.summary


def test_compare_reports_no_change() -> None:
    delta = compare_discrepancies(_report(3), _report(3))
    assert delta.trend is DiscrepancyTrend.UNCHANGED
    assert delta.delta == 0
    assert "unchanged" in delta.summary.lower()


def test_compare_reports_regression() -> None:
    delta = compare_discrepancies(_report(9), _report(4))
    assert delta.trend is DiscrepancyTrend.REGRESSED
    assert delta.delta == 5
    assert "+5" in delta.summary


def test_compare_without_a_previous_run_says_so() -> None:
    delta = compare_discrepancies(_report(3), None)
    assert delta.trend is DiscrepancyTrend.NO_BASELINE
    assert delta.previous is None
    assert delta.delta is None
    assert delta.comparable is False
    assert "no previous run" in delta.summary.lower()


def test_no_baseline_beats_unknown_current() -> None:
    # Both unknown at once: "there is nothing to compare against" is the more
    # actionable statement, so it wins.
    delta = compare_discrepancies(_report(None), None)
    assert delta.trend is DiscrepancyTrend.NO_BASELINE


def test_compare_with_unknown_current_count_is_not_guessed() -> None:
    delta = compare_discrepancies(_report(None), 4)
    assert delta.trend is DiscrepancyTrend.UNKNOWN
    assert delta.current is None
    assert delta.previous == 4
    assert delta.delta is None


def test_compare_accepts_bare_ints_from_the_run_index() -> None:
    # run_store.RunIndexEntry.lvs_discrepancies is an ``int | None``; the
    # comparison must take it without re-parsing the archived report.
    delta = compare_discrepancies(2, 5)
    assert delta.trend is DiscrepancyTrend.IMPROVED
    assert delta.delta == -3


def test_compare_uses_effective_count_for_the_v2019_2_pass(tmp_path: Path) -> None:
    body = "CORRECT\nCELL SUMMARY\n  CORRECT  cell_a  cell_a\n"
    current = parse_lvs_report_detailed(_write(tmp_path, body))
    delta = compare_discrepancies(current, 3)
    assert delta.trend is DiscrepancyTrend.IMPROVED
    assert (delta.current, delta.previous, delta.delta) == (0, 3, -3)


def test_compare_result_is_json_safe() -> None:
    import json

    payload = compare_discrepancies(_report(1), _report(2)).as_dict()
    assert json.loads(json.dumps(payload))["trend"] == "improved"
