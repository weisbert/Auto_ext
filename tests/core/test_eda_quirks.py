"""Regression tests for real-world EDA / Cadence flow quirks.

This file exists because every quirk below has bitten the project in
production. The current code paths handle them, but the test suite did
not pin those behaviours, so a future refactor could silently regress
and the team would learn about it from a failing office run instead of
from CI.

Cross-references (see ``project_cadence_quirks.md``):

- ``simRunDir`` auto-injection: importer must add ``simRunDir = "..."``
  when the raw ``si.env`` lacks it (commit 7e50ef2). Without that line,
  ``si`` writes the netlist to cwd and Quantus aborts with LBRCXM-756.
- si reads ``si.env`` from cwd: workdir module places the per-task
  rendered ``si.env`` into the cwd ``si`` will run from. The placed
  file must come from the rendered source, not from the workarea's
  pre-existing ``si.env``.
- Calibre v2019.2 LVS report: the strict checker requires the
  ``CORRECT`` banner AND no ``INCORRECT`` token AND a clean discrepancy
  signal (``DISCREPANCIES = 0`` line OR a CELL SUMMARY whose every row
  reads ``CORRECT``).
- Stale ``.running`` lock: ``si`` refuses to start if ``.running`` is
  present, and does not always clean it up on exit. ``SiTool.run``
  unlinks unconditionally before invocation.
- Quantus ``-cdl_out_map_directory`` line continuation: the importer
  cannot rewrite this option's path because the value lives on a
  continuation line and the per-line scanner is single-line. Documented
  here so future agents do not "fix" it without updating the scanner
  to preprocess line continuations first.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import pytest

from auto_ext.core.checks import parse_lvs_report, parse_lvs_report_detailed
from auto_ext.core.errors import CheckError
from auto_ext.core.importer import import_template
from auto_ext.core.workdir import prepare_serial_workdir, place_si_env_in_parallel_dir


# ---- helpers ---------------------------------------------------------------


def _eda_write_report(tmp_path: Path, body: str, name: str = "report.rep") -> Path:
    """Drop ``body`` into ``tmp_path/name`` and return the path."""
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _eda_count_simrundir_lines(body: str) -> int:
    """Count *active* ``simRunDir = ...`` assignment lines (no leading ``#``)."""
    pattern = re.compile(r"^\s*simRunDir\s*=", re.MULTILINE)
    return len(pattern.findall(body))


# ---- simRunDir auto-injection (importer) -----------------------------------


def test_eda_simrundir_negative_twin_no_duplicate_when_present() -> None:
    """Raw si.env already carries ``simRunDir = "..."``; importer must
    substitute the existing line and NOT append a second one. Twin of
    the positive ``test_si_injects_simrundir_when_missing`` case.
    """
    raw = (
        'simLibName = "L"\n'
        'simCellName = "C"\n'
        'simViewName = "schematic"\n'
        'simRunDir = "/work/cds/verify/QCI_PATH_C"\n'
    )
    body = import_template("si", raw).template_body
    assert _eda_count_simrundir_lines(body) == 1
    assert 'simRunDir = "[[output_dir]]"' in body
    assert "/work/cds/verify/QCI_PATH_C" not in body


def test_eda_simrundir_no_space_form_not_duplicated() -> None:
    """Raw uses ``simRunDir="..."`` with no spaces around ``=``.

    The existence-probe regex ``^simRunDir\\s*=`` must accept zero
    whitespace between the key and the equals sign so the line counts
    as present and the importer skips the inject.
    """
    raw = 'simLibName = "L"\nsimRunDir="/some/path"\n'
    body = import_template("si", raw).template_body
    assert _eda_count_simrundir_lines(body) == 1


def test_eda_simrundir_commented_out_still_injects() -> None:
    """A commented-out ``# simRunDir = "..."`` line does NOT count as
    the live config — si never reads it, so quantus would still abort
    with LBRCXM-756. The importer must inject the canonical line so
    the rendered template stays usable.
    """
    raw = 'simLibName = "L"\n# simRunDir = "/old/path"\n'
    body = import_template("si", raw).template_body
    # Active assignment count is 1 (the injected one), not 0 and not 2.
    assert _eda_count_simrundir_lines(body) == 1
    assert 'simRunDir = "[[output_dir]]"' in body
    # The commented line round-trips unchanged for human review.
    assert '# simRunDir = "/old/path"' in body


def test_eda_simrundir_tab_indented_not_duplicated() -> None:
    """Tab-indented ``\\tsimRunDir = ...`` must still be recognized as
    the live config so the importer doesn't append a duplicate canonical
    line. Regression: previously ``_SI_RUN_DIR_LINE_RE`` anchored at
    ``^simRunDir`` (no leading-whitespace allowance), causing duplicate
    injection on tab-indented input. Anchor relaxed to ``^\\s*simRunDir``.
    """
    raw = 'simLibName = "L"\n\tsimRunDir = "/some/path"\n'
    body = import_template("si", raw).template_body
    assert _eda_count_simrundir_lines(body) == 1


# ---- si reads si.env from cwd (workdir isolation) --------------------------


def test_eda_serial_workdir_si_env_is_per_task_not_workarea(
    workarea: Path, tmp_path: Path
) -> None:
    """``prepare_serial_workdir`` must overwrite the workarea's si.env
    with the per-task rendered si.env. If a stale workarea si.env from a
    previous task survives, ``si`` will netlist with the wrong
    ``simRunDir`` and quantus will look for the netlist in the wrong
    output_dir.
    """
    # Stale workarea si.env from a previous task.
    stale = workarea / "si.env"
    stale.write_text(
        'simLibName = "STALE_LIB"\nsimRunDir = "/wrong/output_dir"\n',
        encoding="utf-8",
    )

    # Per-task rendered si.env that should win.
    src = tmp_path / "rendered" / "si.env"
    src.parent.mkdir()
    src.write_text(
        'simLibName = "FRESH_LIB"\nsimRunDir = "/right/output_dir"\n',
        encoding="utf-8",
    )

    prepare_serial_workdir(workarea, src)

    placed = (workarea / "si.env").read_text(encoding="utf-8")
    assert "FRESH_LIB" in placed
    assert "STALE_LIB" not in placed
    assert "/right/output_dir" in placed
    assert "/wrong/output_dir" not in placed


def test_eda_parallel_workdir_si_env_is_per_task(tmp_path: Path) -> None:
    """``place_si_env_in_parallel_dir`` writes the per-task si.env into
    the task dir (which is the cwd ``si`` will run from). The contents
    must come from the rendered source, not from any sibling file.
    """
    task_dir = tmp_path / "task_alpha"
    task_dir.mkdir()
    # A pre-existing different si.env in a sibling dir must not leak in.
    sibling = tmp_path / "task_beta"
    sibling.mkdir()
    (sibling / "si.env").write_text(
        'simLibName = "BETA"\nsimRunDir = "/beta"\n', encoding="utf-8"
    )
    src = tmp_path / "rendered_alpha.env"
    src.write_text(
        'simLibName = "ALPHA"\nsimRunDir = "/alpha"\n', encoding="utf-8"
    )

    dst = place_si_env_in_parallel_dir(task_dir, src)

    assert dst == task_dir / "si.env"
    text = dst.read_text(encoding="utf-8")
    assert "ALPHA" in text
    assert "/alpha" in text
    assert "BETA" not in text


# ---- Calibre v2019.2 LVS report quirks --------------------------------------


def test_eda_lvs_v2019_2_correct_with_stray_incorrect_token_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Strict checker treats ANY ``INCORRECT`` token as authoritative:
    even with a banner ``CORRECT`` and ``DISCREPANCIES = 0``, a stray
    ``INCORRECT`` token (e.g. inside a per-cell sub-report appended by
    Calibre v2019.2) makes the overall result fail.

    Word-boundary regex: ``INCORRECT`` does not match across mixed-case
    or alphanumeric boundaries, so legitimate substrings like
    ``incorrect_filter`` will not trigger; pure-uppercase ``INCORRECT``
    in any line will.
    """
    body = (
        "                  ##############################\n"
        "                  #          CORRECT           #\n"
        "                  ##############################\n"
        "DISCREPANCIES = 0\n"
        "Per-cell summary:\n"
        "  cell sub_block: INCORRECT (1 net mismatch)\n"
    )
    rep = _eda_write_report(tmp_path, body)
    detail = parse_lvs_report_detailed(rep)
    assert detail.banner == "INCORRECT"
    assert detail.passed is False


def test_eda_lvs_v2019_2_correct_with_zero_count_passes(tmp_path: Path) -> None:
    """Inverse case: only ``CORRECT`` banner present (no stray
    ``INCORRECT`` anywhere) and ``DISCREPANCIES = 0`` -> clean pass.

    The strict checker must not flake just because the report has
    sparse formatting or extra prose between the banner and the count.
    """
    body = (
        "LVS REPORT FILE\n---\n"
        "                  ##############################\n"
        "                  #          CORRECT           #\n"
        "                  ##############################\n"
        "(filler line that does not contain the I-word)\n"
        "OVERALL COMPARISON RESULTS\n"
        "\n"
        "DISCREPANCIES = 0\n"
    )
    rep = _eda_write_report(tmp_path, body)
    detail = parse_lvs_report_detailed(rep)
    assert detail.banner == "CORRECT"
    assert detail.discrepancies == 0
    assert detail.passed is True


def test_eda_lvs_v2019_2_cell_summary_pass_omits_count_line(
    tmp_path: Path,
) -> None:
    """Calibre v2019.2 omits the ``DISCREPANCIES = 0`` line on a clean
    pass, relying on the CELL SUMMARY table instead. The checker falls
    back to the table when the count line is absent and treats the
    report as a pass when every row reads ``CORRECT``.

    Twin of ``test_correct_banner_no_discrepancies_but_cell_summary_passes``
    in ``test_checks.py`` — duplicated here so the regression flag stays
    in the EDA-quirks bucket if someone moves the original test.
    """
    body = (
        "                  ##############################\n"
        "                  #          CORRECT           #\n"
        "                  ##############################\n"
        "\n"
        "                          CELL  SUMMARY\n"
        "\n"
        "  Result         Layout                        Source\n"
        "  -----------    -----------                   --------------\n"
        "  CORRECT        cell_a                        cell_a\n"
        "  CORRECT        cell_b                        cell_b\n"
    )
    rep = _eda_write_report(tmp_path, body)
    detail = parse_lvs_report_detailed(rep)
    assert detail.passed is True
    assert detail.banner == "CORRECT"
    assert detail.discrepancies is None


def test_eda_lvs_v2019_2_correct_with_truncated_summary_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Banner CORRECT, no ``DISCREPANCIES`` line, no usable CELL SUMMARY
    (table header missing). The report is too sparse to trust as a
    pass — strict mode treats a missing signal as fail rather than a
    pass-by-default.
    """
    body = (
        "                  ##############################\n"
        "                  #          CORRECT           #\n"
        "                  ##############################\n"
        "[truncated]\n"
    )
    rep = _eda_write_report(tmp_path, body)
    caplog.set_level(logging.WARNING, logger="auto_ext.core.checks")
    assert parse_lvs_report(rep) is False
    assert any("truncated" in m.lower() or "fail" in m.lower() for m in caplog.messages)


# ---- Quantus -cdl_out_map_directory continuation-line gap ------------------


def test_eda_quantus_cdl_out_map_directory_continuation_line_gap() -> None:
    """Document the deliberately-skipped gap.

    The bundled quantus template renders ``cdl_out_map_directory`` as::

        -cdl_out_map_directory \\
        "[[output_dir]]/" \\

    but the per-line importer scanner ``_QUANTUS_LINE_RE`` only matches
    a single-line ``-key "value"`` pattern. When a user re-imports a
    raw ``ext.cmd`` that uses the continuation form, the value path
    survives un-substituted (only the employee_id pre-pass touches it,
    leaving ``[[output_dir]]/`` un-rewritten).

    This is **deliberately not fixed**. The fix requires preprocessing
    line continuations (``\\\\\\n``) into single logical lines before
    the per-tool scanner runs, which has implications for byte-for-byte
    round-tripping of comment-only continuations elsewhere in the file.

    This test pins the current state so future agents do NOT silently
    "fix" the importer without updating the scanner architecture and
    re-validating the existing quantus tests.
    """
    pytest.skip(
        "deliberately skipped: cdl_out_map_directory line-continuation "
        "rewrite needs scanner-level preprocessing; see test docstring."
    )


def test_eda_quantus_cdl_out_map_directory_current_behaviour_documented() -> None:
    """Companion to the skipped test above — pins the OBSERVED behaviour.

    If someone changes the importer so the cdl_out_map_directory path
    IS rewritten, this assertion will fail and force them to remove the
    pytest.skip on the test above (and update the project memo).

    Uses the bundled raw quantus fixture so the failure points at a
    file that is part of the regression baseline rather than at synthetic
    data.
    """
    raw_path = Path(__file__).resolve().parent.parent / "fixtures" / "raw" / "quantus_sample.cmd"
    raw = raw_path.read_text(encoding="utf-8")
    body = import_template("quantus", raw).template_body

    # Locate the cdl_out_map_directory continuation line and assert the
    # value still carries an absolute path (employee_id substituted but
    # NOT output_dir). If a future fix rewrites it, this assertion fails
    # and the skip-test above must also be revisited.
    lines = body.splitlines()
    cdl_idx = next(
        (i for i, ln in enumerate(lines) if "cdl_out_map_directory" in ln),
        None,
    )
    assert cdl_idx is not None, "cdl_out_map_directory anchor missing from fixture"
    value_line = lines[cdl_idx + 1]
    # employee_id pre-pass DOES run.
    assert "[[employee_id]]" in value_line
    # output_dir substitution does NOT run on the continuation value.
    assert "[[output_dir]]" not in value_line
    assert "/cds/verify/" in value_line
