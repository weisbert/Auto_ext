"""Tests for :mod:`auto_ext.core.failure_class`.

Two halves:

* the certain-fact rules (exit codes, the parsed LVS report, missing files),
  which are decided in code and are the ones that matter today;
* the signature table, exercised entirely with **fabricated** patterns built
  in-memory or written to ``tmp_path``. The shipped
  ``failure_signatures.yaml`` is empty on purpose -- this project has no
  captured EDA log -- and one test below nails that emptiness down so a
  future invented signature has to be a deliberate act.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from auto_ext.core.checks import LvsReport, parse_lvs_report_detailed
from auto_ext.core.errors import ConfigError
from auto_ext.core.failure_class import (
    ASSIGNABLE_CLASSES,
    DEFAULT_SIGNATURES_PATH,
    LOG_ELISION_MARKER,
    MAX_EVIDENCE_CHARS,
    Confidence,
    FailureClass,
    FailureSignature,
    FailureSignatureError,
    SignatureTable,
    classify_failure,
    classify_tool_result,
    load_signatures,
    next_action_for,
    parse_signature_table,
    read_log_excerpt,
)
from auto_ext.tools.base import ToolResult


# ---- helpers ---------------------------------------------------------------


def _lvs(
    tmp_path: Path,
    body: str = "INCORRECT\nDISCREPANCIES = 3\n",
    name: str = "inv.lvs.report",
) -> LvsReport:
    report = tmp_path / name
    report.write_text(body, encoding="utf-8")
    return parse_lvs_report_detailed(report)


def _table(*signatures: FailureSignature) -> SignatureTable:
    return SignatureTable(signatures=signatures)


#: A fabricated license signature. The wording is obviously not from a real
#: tool -- that is the point: it proves the machinery without pretending we
#: know what Calibre prints.
FAKE_LICENSE = FailureSignature(
    id="fake_license",
    failure_class=FailureClass.LICENSE_UNAVAILABLE,
    any=("FAKE-LIC-0001 no seats available",),
    stages=("calibre",),
)


# ---- classes and next actions ----------------------------------------------


def test_the_four_classes_plus_unknown() -> None:
    assert {c.value for c in FailureClass} == {
        "license_unavailable",
        "environment",
        "lvs_mismatch",
        "tool_crash",
        "unknown",
    }


def test_unknown_is_not_assignable_from_the_table() -> None:
    assert FailureClass.UNKNOWN not in ASSIGNABLE_CLASSES
    assert len(ASSIGNABLE_CLASSES) == 4


@pytest.mark.parametrize("failure_class", list(FailureClass))
def test_every_class_carries_a_distinct_next_action(failure_class: FailureClass) -> None:
    action = next_action_for(failure_class)
    assert action and action[0].isupper()
    others = {next_action_for(c) for c in FailureClass if c is not failure_class}
    assert action not in others


def test_unknown_next_action_points_at_the_signature_table() -> None:
    assert "failure_signatures.yaml" in next_action_for(FailureClass.UNKNOWN)


# ---- rule 1-3: environment --------------------------------------------------


def test_render_failure_is_environment() -> None:
    verdict = classify_failure(
        stage="quantus", render_error="unresolved env ref: $QRC_DECK_DIR"
    )
    assert verdict.failure_class is FailureClass.ENVIRONMENT
    assert verdict.confidence is Confidence.CERTAIN
    assert "QRC_DECK_DIR" in (verdict.evidence or "")


def test_missing_input_file_is_environment(tmp_path: Path) -> None:
    verdict = classify_failure(stage="calibre", missing_inputs=[tmp_path / "inv.src.net"])
    assert verdict.failure_class is FailureClass.ENVIRONMENT
    assert "inv.src.net" in verdict.reason


def test_exit_127_is_environment() -> None:
    # 127 is run_subprocess's own "executable not found on PATH" convention.
    verdict = classify_failure(stage="si", exit_code=127)
    assert verdict.failure_class is FailureClass.ENVIRONMENT
    assert verdict.confidence is Confidence.CERTAIN
    assert "127" in verdict.reason


def test_render_failure_outranks_a_non_zero_exit() -> None:
    verdict = classify_failure(stage="si", exit_code=1, render_error="bad template")
    assert verdict.failure_class is FailureClass.ENVIRONMENT


# ---- rule 4: lvs mismatch ---------------------------------------------------


def test_incorrect_lvs_report_is_a_mismatch(tmp_path: Path) -> None:
    verdict = classify_failure(stage="calibre", exit_code=0, lvs=_lvs(tmp_path))
    assert verdict.failure_class is FailureClass.LVS_MISMATCH
    assert verdict.confidence is Confidence.CERTAIN
    assert "INCORRECT" in verdict.reason
    assert "3 discrepancies" in verdict.reason


def test_mismatch_reason_names_the_incorrect_cells(tmp_path: Path) -> None:
    body = (
        "INCORRECT\nDISCREPANCIES = 2\nCELL SUMMARY\n"
        "  INCORRECT  bias_gen  bias_gen\n"
        "  CORRECT    buf_x2    buf_x2\n"
    )
    verdict = classify_failure(stage="calibre", lvs=_lvs(tmp_path, body))
    assert "bias_gen" in verdict.reason
    assert "buf_x2" not in verdict.reason


def test_mismatch_reason_caps_the_cell_list(tmp_path: Path) -> None:
    rows = "".join(f"  INCORRECT  cell_{i}  cell_{i}\n" for i in range(9))
    verdict = classify_failure(
        stage="calibre", lvs=_lvs(tmp_path, f"INCORRECT\nCELL SUMMARY\n{rows}")
    )
    assert "+4 more" in verdict.reason


def test_passing_lvs_report_is_not_a_mismatch(tmp_path: Path) -> None:
    # Calibre passed LVS but the process still died: that is not the
    # design's fault, so the report must not be blamed.
    passing = _lvs(tmp_path, "CORRECT\nDISCREPANCIES = 0\n")
    verdict = classify_failure(stage="calibre", exit_code=3, lvs=passing)
    assert verdict.failure_class is FailureClass.TOOL_CRASH


def test_parsed_report_outranks_a_log_signature(tmp_path: Path) -> None:
    # A stray license line elsewhere in the log must not overrule a report
    # we actually parsed.
    verdict = classify_failure(
        stage="calibre",
        lvs=_lvs(tmp_path),
        log_text="FAKE-LIC-0001 no seats available\n",
        signatures=_table(FAKE_LICENSE),
    )
    assert verdict.failure_class is FailureClass.LVS_MISMATCH


# ---- rule 5: signatures -----------------------------------------------------


def test_signature_match_yields_its_class_and_evidence() -> None:
    log = "calibre: starting\nFAKE-LIC-0001 no seats available for feature X\nexiting\n"
    verdict = classify_failure(
        stage="calibre", exit_code=1, log_text=log, signatures=_table(FAKE_LICENSE)
    )
    assert verdict.failure_class is FailureClass.LICENSE_UNAVAILABLE
    assert verdict.confidence is Confidence.SIGNATURE
    assert verdict.signature_id == "fake_license"
    assert verdict.evidence == "FAKE-LIC-0001 no seats available for feature X"


def test_signature_outranks_the_generic_non_zero_exit() -> None:
    verdict = classify_failure(
        stage="calibre",
        exit_code=1,
        log_text="FAKE-LIC-0001 no seats available\n",
        signatures=_table(FAKE_LICENSE),
    )
    assert verdict.failure_class is not FailureClass.TOOL_CRASH


def test_signature_is_scoped_to_its_stages() -> None:
    log = "FAKE-LIC-0001 no seats available\n"
    verdict = classify_failure(
        stage="quantus", exit_code=1, log_text=log, signatures=_table(FAKE_LICENSE)
    )
    assert verdict.failure_class is FailureClass.TOOL_CRASH


def test_signature_without_stages_fires_anywhere() -> None:
    anywhere = FailureSignature(
        id="any_stage",
        failure_class=FailureClass.TOOL_CRASH,
        any=("FAKE-CRASH-0002",),
    )
    verdict = classify_failure(
        stage="jivaro", log_text="FAKE-CRASH-0002\n", signatures=_table(anywhere)
    )
    assert verdict.signature_id == "any_stage"


def test_signature_all_patterns_must_all_appear() -> None:
    both = FailureSignature(
        id="needs_both",
        failure_class=FailureClass.ENVIRONMENT,
        any=("FAKE-ENV-0003",),
        all=("second half",),
    )
    partial = classify_failure(
        stage="si", exit_code=1, log_text="FAKE-ENV-0003\n", signatures=_table(both)
    )
    assert partial.failure_class is FailureClass.TOOL_CRASH

    complete = classify_failure(
        stage="si",
        exit_code=1,
        log_text="FAKE-ENV-0003\nand the second half too\n",
        signatures=_table(both),
    )
    assert complete.failure_class is FailureClass.ENVIRONMENT


def test_signature_matching_is_case_insensitive_by_default() -> None:
    verdict = classify_failure(
        stage="calibre",
        log_text="fake-lic-0001 NO SEATS AVAILABLE\n",
        signatures=_table(FAKE_LICENSE),
    )
    assert verdict.failure_class is FailureClass.LICENSE_UNAVAILABLE


def test_case_sensitive_signature_respects_case() -> None:
    strict = FailureSignature(
        id="strict",
        failure_class=FailureClass.TOOL_CRASH,
        any=("FAKE-CASE",),
        case_sensitive=True,
    )
    assert strict.matches("fake-case here") is None
    assert strict.matches("FAKE-CASE here") == "FAKE-CASE here"


def test_regex_signature() -> None:
    pattern = FailureSignature(
        id="regex_sig",
        failure_class=FailureClass.LICENSE_UNAVAILABLE,
        any=(r"FAKE-LIC-\d{4}",),
        regex=True,
    )
    verdict = classify_failure(
        stage="quantus", log_text="FAKE-LIC-4242 denied\n", signatures=_table(pattern)
    )
    assert verdict.failure_class is FailureClass.LICENSE_UNAVAILABLE
    assert verdict.evidence == "FAKE-LIC-4242 denied"


def test_signature_next_action_overrides_the_class_default() -> None:
    custom = FailureSignature(
        id="custom_action",
        failure_class=FailureClass.LICENSE_UNAVAILABLE,
        any=("FAKE-LIC-0001",),
        next_action="Ask Bob for his seat.",
    )
    verdict = classify_failure(
        stage="calibre", log_text="FAKE-LIC-0001\n", signatures=_table(custom)
    )
    assert verdict.next_action == "Ask Bob for his seat."


def test_first_matching_signature_wins() -> None:
    first = FailureSignature(
        id="first", failure_class=FailureClass.ENVIRONMENT, any=("SHARED-TOKEN",)
    )
    second = FailureSignature(
        id="second", failure_class=FailureClass.TOOL_CRASH, any=("SHARED-TOKEN",)
    )
    verdict = classify_failure(
        stage="si", log_text="SHARED-TOKEN\n", signatures=_table(first, second)
    )
    assert verdict.signature_id == "first"


def test_evidence_is_the_earliest_matching_line() -> None:
    sig = FailureSignature(
        id="two_patterns",
        failure_class=FailureClass.TOOL_CRASH,
        any=("LATER-TOKEN", "EARLIER-TOKEN"),
    )
    log = "line one EARLIER-TOKEN\nline two LATER-TOKEN\n"
    assert sig.matches(log) == "line one EARLIER-TOKEN"


def test_evidence_is_capped() -> None:
    sig = FailureSignature(
        id="long_line", failure_class=FailureClass.TOOL_CRASH, any=("TOKEN",)
    )
    evidence = sig.matches("TOKEN " + "x" * 5000)
    assert evidence is not None
    assert len(evidence) <= MAX_EVIDENCE_CHARS


def test_signature_does_not_fire_on_empty_log() -> None:
    assert FAKE_LICENSE.matches("") is None


# ---- rule 6-8: crash and unknown -------------------------------------------


def test_missing_declared_output_is_a_crash(tmp_path: Path) -> None:
    verdict = classify_failure(
        stage="quantus", exit_code=0, missing_outputs=[tmp_path / "inv.dspf"]
    )
    assert verdict.failure_class is FailureClass.TOOL_CRASH
    assert "inv.dspf" in verdict.reason


def test_unparsable_output_is_a_crash() -> None:
    verdict = classify_failure(
        stage="calibre", exit_code=0, unparsable_output="no LVS banner found"
    )
    assert verdict.failure_class is FailureClass.TOOL_CRASH
    assert "no LVS banner" in verdict.reason


def test_bare_non_zero_exit_is_a_crash() -> None:
    verdict = classify_failure(stage="jivaro", exit_code=139)
    assert verdict.failure_class is FailureClass.TOOL_CRASH
    assert "139" in verdict.reason


def test_nothing_known_is_unknown_not_a_guess() -> None:
    verdict = classify_failure(stage="si")
    assert verdict.failure_class is FailureClass.UNKNOWN
    assert verdict.confidence is Confidence.NONE
    assert verdict.is_unknown is True
    assert verdict.signature_id is None


def test_clean_exit_with_no_other_signal_is_unknown() -> None:
    # A stage marked failed by something we were not told about: say so
    # rather than inventing a crash.
    verdict = classify_failure(stage="strmout", exit_code=0)
    assert verdict.failure_class is FailureClass.UNKNOWN


def test_verdict_is_json_safe() -> None:
    payload = classify_failure(stage="si", exit_code=127).as_dict()
    assert json.loads(json.dumps(payload))["failure_class"] == "environment"
    assert set(payload) == {
        "failure_class",
        "confidence",
        "reason",
        "next_action",
        "evidence",
        "signature_id",
    }


# ---- the shipped table ------------------------------------------------------


def test_bundled_table_exists_and_parses() -> None:
    table = load_signatures()
    assert table.source == DEFAULT_SIGNATURES_PATH
    assert table.version == 1


def test_bundled_table_ships_empty_on_purpose() -> None:
    # Deliberate: no real EDA log has ever been captured into this repo, so
    # every pattern would be invented. If you are here because you added a
    # signature, make sure it came from a scrubbed sample (see the file
    # header) and then update this test to assert your entry.
    assert len(load_signatures()) == 0


def test_bundled_table_header_explains_how_to_fill_it() -> None:
    header = DEFAULT_SIGNATURES_PATH.read_text(encoding="utf-8")
    assert "HOW TO CAPTURE A SAMPLE" in header
    assert "ENTRY FORMAT" in header


# ---- loading and validation -------------------------------------------------


def _write_table(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "signatures.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_from_an_explicit_path(tmp_path: Path) -> None:
    path = _write_table(
        tmp_path,
        "version: 1\n"
        "signatures:\n"
        "  - id: sample_one\n"
        "    failure_class: license_unavailable\n"
        "    stages: [calibre, quantus]\n"
        "    any:\n"
        '      - "FAKE-LIC-0001"\n'
        '    note: "fabricated for tests"\n',
    )
    table = load_signatures(path)
    assert len(table) == 1
    signature = table.signatures[0]
    assert signature.id == "sample_one"
    assert signature.failure_class is FailureClass.LICENSE_UNAVAILABLE
    assert signature.stages == ("calibre", "quantus")
    assert signature.note == "fabricated for tests"


def test_star_stage_means_every_stage(tmp_path: Path) -> None:
    path = _write_table(
        tmp_path,
        'version: 1\nsignatures:\n  - id: everywhere\n    failure_class: tool_crash\n'
        '    stages: ["*"]\n    any: ["BOOM"]\n',
    )
    signature = load_signatures(path).signatures[0]
    assert signature.stages == ()
    assert signature.applies_to("si") is True
    assert signature.applies_to(None) is True


def test_scoped_signature_does_not_apply_to_an_unnamed_stage() -> None:
    assert FAKE_LICENSE.applies_to(None) is False


def test_blank_file_is_an_empty_table(tmp_path: Path) -> None:
    assert len(load_signatures(_write_table(tmp_path, "\n"))) == 0


def test_missing_explicit_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FailureSignatureError, match="not found"):
        load_signatures(tmp_path / "nope.yaml")


def test_malformed_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(FailureSignatureError, match="cannot parse"):
        load_signatures(_write_table(tmp_path, "version: 1\n  bad: [indent\n"))


def test_signature_error_is_a_config_error() -> None:
    assert issubclass(FailureSignatureError, ConfigError)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("version: 1\nsignatures: []\nextra: 1\n", "unknown top-level key"),
        ("version: 99\nsignatures: []\n", "unsupported version"),
        ("version: 1\nsignatures: {}\n", "signatures must be a list"),
        (
            'version: 1\nsignatures:\n  - id: "Bad Id"\n    failure_class: tool_crash\n'
            '    any: ["x"]\n',
            "id must match",
        ),
        (
            'version: 1\nsignatures:\n  - id: ok\n    failure_class: unknown\n'
            '    any: ["x"]\n',
            "not assignable",
        ),
        (
            'version: 1\nsignatures:\n  - id: ok\n    failure_class: nonsense\n'
            '    any: ["x"]\n',
            "not assignable",
        ),
        (
            "version: 1\nsignatures:\n  - id: ok\n    failure_class: tool_crash\n",
            "any must list",
        ),
        (
            'version: 1\nsignatures:\n  - id: ok\n    failure_class: tool_crash\n'
            "    any: []\n",
            "any must list",
        ),
        (
            'version: 1\nsignatures:\n  - id: ok\n    failure_class: tool_crash\n'
            '    any: ["x"]\n    bogus: 1\n',
            "unknown key",
        ),
        (
            'version: 1\nsignatures:\n  - id: dup\n    failure_class: tool_crash\n'
            '    any: ["x"]\n  - id: dup\n    failure_class: tool_crash\n'
            '    any: ["y"]\n',
            "duplicate signature id",
        ),
        (
            'version: 1\nsignatures:\n  - id: ok\n    failure_class: tool_crash\n'
            '    any: ["(unclosed"]\n    regex: true\n',
            "bad regular expression",
        ),
        (
            'version: 1\nsignatures:\n  - id: ok\n    failure_class: tool_crash\n'
            '    any: ["x"]\n    regex: "yes"\n',
            "regex must be true or false",
        ),
        (
            'version: 1\nsignatures:\n  - id: ok\n    failure_class: tool_crash\n'
            "    any: [3]\n",
            "must be non-empty strings",
        ),
    ],
)
def test_malformed_table_is_rejected_with_a_useful_message(
    tmp_path: Path, body: str, message: str
) -> None:
    with pytest.raises(FailureSignatureError, match=message):
        load_signatures(_write_table(tmp_path, body))


def test_a_scalar_any_is_accepted_as_a_one_element_list(tmp_path: Path) -> None:
    path = _write_table(
        tmp_path,
        'version: 1\nsignatures:\n  - id: scalar\n    failure_class: tool_crash\n'
        '    any: "BOOM"\n',
    )
    assert load_signatures(path).signatures[0].any == ("BOOM",)


def test_parse_signature_table_accepts_none() -> None:
    assert len(parse_signature_table(None)) == 0


def test_a_broken_bundled_table_does_not_break_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A typo in the data file must never take a run down with it: the
    # classifier degrades to the certain-fact rules and logs the problem.
    broken = _write_table(tmp_path, "version: 1\nsignatures:\n  - id: 'Bad Id'\n")
    monkeypatch.setattr(
        "auto_ext.core.failure_class.DEFAULT_SIGNATURES_PATH", broken
    )
    caplog.set_level(logging.ERROR, logger="auto_ext.core.failure_class")

    verdict = classify_failure(stage="si", exit_code=1, log_text="anything\n")

    assert verdict.failure_class is FailureClass.TOOL_CRASH
    assert any("malformed" in m.lower() for m in caplog.messages)


def test_a_missing_bundled_table_degrades_quietly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "auto_ext.core.failure_class.DEFAULT_SIGNATURES_PATH", tmp_path / "gone.yaml"
    )
    assert len(load_signatures()) == 0
    assert classify_failure(stage="si", exit_code=1).failure_class is (
        FailureClass.TOOL_CRASH
    )


# ---- log reading ------------------------------------------------------------


def test_read_log_excerpt_returns_a_small_log_whole(tmp_path: Path) -> None:
    log = tmp_path / "si.log"
    log.write_text("short log\n", encoding="utf-8")
    assert read_log_excerpt(log) == "short log\n"


def test_read_log_excerpt_keeps_head_and_tail_of_a_big_log(tmp_path: Path) -> None:
    log = tmp_path / "quantus.log"
    log.write_text("HEAD\n" + ("filler\n" * 5000) + "TAIL\n", encoding="utf-8")

    excerpt = read_log_excerpt(log, max_bytes=1024)

    assert excerpt.startswith("HEAD")
    assert excerpt.rstrip().endswith("TAIL")
    assert LOG_ELISION_MARKER in excerpt
    assert len(excerpt) < 2048


def test_read_log_excerpt_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_log_excerpt(tmp_path / "never_written.log") == ""


def test_classify_reads_the_log_from_a_path(tmp_path: Path) -> None:
    log = tmp_path / "calibre.log"
    log.write_text("FAKE-LIC-0001 no seats available\n", encoding="utf-8")
    verdict = classify_failure(
        stage="calibre", exit_code=1, log_path=log, signatures=_table(FAKE_LICENSE)
    )
    assert verdict.failure_class is FailureClass.LICENSE_UNAVAILABLE


# ---- the ToolResult adapter -------------------------------------------------


def test_classify_tool_result_uses_the_exit_code_field() -> None:
    result = ToolResult(success=False, exit_code=127)
    verdict = classify_tool_result(result, stage="si")
    assert verdict.failure_class is FailureClass.ENVIRONMENT


def test_classify_tool_result_falls_back_to_diagnostics_exit_code() -> None:
    # Results built before ToolResult grew an exit_code field (and every
    # hand-built one in the test suite) still carry it in diagnostics.
    result = ToolResult(success=False, diagnostics={"exit_code": 127})
    assert classify_tool_result(result, stage="si").failure_class is (
        FailureClass.ENVIRONMENT
    )


def test_classify_tool_result_reads_the_lvs_report(tmp_path: Path) -> None:
    result = ToolResult(
        success=False, exit_code=0, diagnostics={"lvs_report": _lvs(tmp_path)}
    )
    assert classify_tool_result(result, stage="calibre").failure_class is (
        FailureClass.LVS_MISMATCH
    )


def test_classify_tool_result_reads_lvs_report_missing(tmp_path: Path) -> None:
    result = ToolResult(
        success=False,
        exit_code=0,
        diagnostics={"lvs_report_missing": str(tmp_path / "inv.lvs.report")},
    )
    verdict = classify_tool_result(result, stage="calibre")
    assert verdict.failure_class is FailureClass.TOOL_CRASH
    assert "inv.lvs.report" in verdict.reason


def test_classify_tool_result_reads_missing_artifacts(tmp_path: Path) -> None:
    result = ToolResult(
        success=False,
        exit_code=0,
        diagnostics={"missing_artifacts": [str(tmp_path / "inv.dspf")]},
    )
    verdict = classify_tool_result(result, stage="quantus")
    assert verdict.failure_class is FailureClass.TOOL_CRASH
    assert "inv.dspf" in verdict.reason


def test_classify_tool_result_reads_lvs_parse_error() -> None:
    result = ToolResult(
        success=False,
        exit_code=0,
        diagnostics={"lvs_parse_error": "no LVS banner found; report truncated?"},
    )
    assert classify_tool_result(result, stage="calibre").failure_class is (
        FailureClass.TOOL_CRASH
    )


def test_classify_tool_result_scans_the_stage_log(tmp_path: Path) -> None:
    log = tmp_path / "calibre.log"
    log.write_text("FAKE-LIC-0001 no seats available\n", encoding="utf-8")
    result = ToolResult(success=False, exit_code=1, stdout_path=log)
    verdict = classify_tool_result(result, stage="calibre", signatures=_table(FAKE_LICENSE))
    assert verdict.failure_class is FailureClass.LICENSE_UNAVAILABLE


def test_classify_tool_result_on_a_bare_result_is_unknown() -> None:
    assert classify_tool_result(ToolResult(success=False), stage="si").is_unknown is True
