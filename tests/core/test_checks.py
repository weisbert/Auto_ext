"""Tests for :mod:`auto_ext.core.checks`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from auto_ext.core.checks import LvsReport, parse_lvs_report, parse_lvs_report_detailed
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
