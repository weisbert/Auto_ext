"""Unit tests for the 5 :class:`auto_ext.tools.base.Tool` plugins.

These cover the stable, platform-independent behaviour: class attributes,
``build_argv`` shape, and (for calibre) the LVS-report ``parse_result``
integration. End-to-end subprocess execution is covered by
``tests/test_runner.py``.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from auto_ext.core.checks import LvsReport
from auto_ext.tools.base import CellViewRef, ToolResult, argv_path, argv_value
from auto_ext.tools.calibre import CalibreTool
from auto_ext.tools.jivaro import JivaroTool
from auto_ext.tools.quantus import QuantusTool
from auto_ext.tools.si import SiTool
from auto_ext.tools.strmout import StrmoutTool


# ---- class-attribute contract --------------------------------------------


def test_tool_identities_and_has_template() -> None:
    assert SiTool().name == "si" and SiTool().has_template is True
    assert CalibreTool().name == "calibre" and CalibreTool().has_template is True
    assert QuantusTool().name == "quantus" and QuantusTool().has_template is True
    assert JivaroTool().name == "jivaro" and JivaroTool().has_template is True
    assert StrmoutTool().name == "strmout" and StrmoutTool().has_template is False


def test_tool_executables() -> None:
    assert SiTool().executable == "si"
    assert CalibreTool().executable == "calibre"
    assert QuantusTool().executable == "qrc"
    assert JivaroTool().executable == "jivaro"
    assert StrmoutTool().executable == "strmout"


# ---- build_argv shape ----------------------------------------------------


def test_si_argv_ignores_input_path(tmp_path: Path) -> None:
    argv = SiTool().build_argv(tmp_path / "rendered.env", {})
    assert argv == ["si", "-batch", "-command", "netlist", "-cdslib", "./cds.lib"]


# ---- SiTool .running preflight ------------------------------------------


def _stub_si_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace base.run_subprocess with a no-op recorder. Returns the call log."""
    import auto_ext.tools.base as base

    calls: list[dict] = []

    def fake(argv, cwd, env, log_path, *, cancel_token=None):
        calls.append(
            {
                "argv": list(argv),
                "cwd": cwd,
                "log_path": log_path,
                "cancel_token": cancel_token,
            }
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("stubbed\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(base, "run_subprocess", fake)
    return calls


def test_si_run_unlinks_running_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = tmp_path / ".running"
    lock.write_text("pid 12345\n", encoding="utf-8")
    calls = _stub_si_subprocess(monkeypatch)

    result = SiTool().run(
        argv=["si", "-batch"], cwd=tmp_path, env={}, log_path=tmp_path / "logs" / "si.log"
    )

    assert not lock.exists()
    assert result.success is True
    assert len(calls) == 1 and calls[0]["cwd"] == tmp_path


def test_si_run_noop_when_lock_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub_si_subprocess(monkeypatch)

    result = SiTool().run(
        argv=["si", "-batch"], cwd=tmp_path, env={}, log_path=tmp_path / "logs" / "si.log"
    )

    assert result.success is True
    assert not (tmp_path / ".running").exists()
    assert len(calls) == 1


def test_calibre_argv_includes_runset(tmp_path: Path) -> None:
    runset = tmp_path / "run.qci"
    argv = CalibreTool().build_argv(runset, {})
    assert argv == ["calibre", "-gui", "-lvs", "-runset", str(runset), "-batch"]


def test_quantus_argv_has_cmd_flag(tmp_path: Path) -> None:
    cmd = tmp_path / "ext.cmd"
    assert QuantusTool().build_argv(cmd, {}) == ["qrc", "-cmd", str(cmd)]


def test_jivaro_argv_has_xml_flag(tmp_path: Path) -> None:
    xml = tmp_path / "jivaro.xml"
    assert JivaroTool().build_argv(xml, {}) == ["jivaro", "-xml", str(xml)]


def test_strmout_argv_built_from_context(tmp_path: Path) -> None:
    ctx = {
        "library": "LIB",
        "cell": "inv",
        "lvs_layout_view": "layout",
        "output_dir": "/w/cds/out",
        "layer_map": "/pdk/layers.map",
    }
    argv = StrmoutTool().build_argv(tmp_path / "unused", ctx)
    assert argv[:1] == ["strmout"]
    assert "-library" in argv and argv[argv.index("-library") + 1] == "LIB"
    assert "-topCell" in argv and argv[argv.index("-topCell") + 1] == "inv"
    assert "-view" in argv and argv[argv.index("-view") + 1] == "layout"
    strm_idx = argv.index("-strmFile") + 1
    assert argv[strm_idx].replace("\\", "/").endswith("/w/cds/out/inv.calibre.db")
    assert "-layerMap" in argv
    assert argv[argv.index("-layerMap") + 1] == "/pdk/layers.map"


# ---- strmout export mode -------------------------------------------------


def _strmout_ctx(**over) -> dict:
    ctx = {
        "library": "LIB",
        "cell": "inv",
        "lvs_layout_view": "layout",
        "output_dir": "/w/cds/out",
        "layer_map": "/pdk/layers.map",
    }
    ctx.update(over)
    return ctx


def test_strmout_export_path_redirects_the_stream_file(tmp_path: Path) -> None:
    """``layout_export_path`` replaces the destination, verbatim."""

    argv = StrmoutTool().build_argv(
        tmp_path / "unused", _strmout_ctx(layout_export_path="/elsewhere/inv.gds")
    )
    strm = argv[argv.index("-strmFile") + 1]
    assert strm.replace("\\", "/") == "/elsewhere/inv.gds"
    # Everything that identifies the layout is unchanged: same cell, same
    # view, same layer map. Only where it lands moves.
    assert argv[argv.index("-topCell") + 1] == "inv"
    assert argv[argv.index("-view") + 1] == "layout"
    assert argv[argv.index("-layerMap") + 1] == "/pdk/layers.map"


def test_strmout_without_an_export_path_keeps_the_lvs_destination(tmp_path: Path) -> None:
    """The default must survive every falsy spelling of "no export".

    A ``layout_export_path`` of None or "" is the normal case -- it is a
    plain field on RunFacts that every ordinary run leaves unset -- and it
    must not be allowed to produce ``-strmFile ''``.
    """
    for absent in (None, ""):
        argv = StrmoutTool().build_argv(
            tmp_path / "unused", _strmout_ctx(layout_export_path=absent)
        )
        strm = argv[argv.index("-strmFile") + 1].replace("\\", "/")
        assert strm.endswith("/w/cds/out/inv.calibre.db"), absent


def test_strmout_export_is_recorded_as_the_artifact(tmp_path: Path) -> None:
    """parse_result reads -strmFile out of argv, so the export follows for free."""

    exported = tmp_path / "reliability" / "inv.gds"
    exported.parent.mkdir()
    exported.write_text("gds", encoding="utf-8")
    argv = StrmoutTool().build_argv(
        tmp_path / "unused", _strmout_ctx(layout_export_path=str(exported))
    )
    result = ToolResult(
        success=True, exit_code=0, diagnostics={"exit_code": 0, "argv": argv}
    )
    assert StrmoutTool().parse_result(result).artifact_paths == [exported]


# ---- CalibreTool.parse_result integration with checks.py -----------------


def _write_qci(path: Path, run_dir: Path, report_name: str) -> None:
    path.write_text(
        f"*lvsRunDir: {run_dir}\n*lvsReportFile: {report_name}\n",
        encoding="utf-8",
    )


def _make_calibre_result(qci: Path, exit_code: int = 0) -> ToolResult:
    argv = ["calibre", "-gui", "-lvs", "-runset", str(qci), "-batch"]
    return ToolResult(
        success=(exit_code == 0),
        stdout_path=None,
        diagnostics={"exit_code": exit_code, "argv": argv},
    )


def test_calibre_parse_success_passes_lvs(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "inv.lvs.report").write_text(
        "# summary\n  CORRECT\n  DISCREPANCIES = 0\n",
        encoding="utf-8",
    )
    qci = tmp_path / "inv.qci"
    _write_qci(qci, run_dir, "inv.lvs.report")

    raw = _make_calibre_result(qci, exit_code=0)
    out = CalibreTool().parse_result(raw)
    assert out.success is True
    assert isinstance(out.diagnostics["lvs_report"], LvsReport)
    assert out.diagnostics["lvs_report"].passed is True


def test_calibre_parse_incorrect_report_is_failure(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "inv.lvs.report").write_text(
        "# summary\n  INCORRECT\n  DISCREPANCIES = 3\n",
        encoding="utf-8",
    )
    qci = tmp_path / "inv.qci"
    _write_qci(qci, run_dir, "inv.lvs.report")

    raw = _make_calibre_result(qci, exit_code=1)
    out = CalibreTool().parse_result(raw)
    assert out.success is False
    assert out.diagnostics["lvs_report"].banner == "INCORRECT"


def test_calibre_parse_missing_report_marks_failure(tmp_path: Path) -> None:
    qci = tmp_path / "inv.qci"
    _write_qci(qci, tmp_path / "run", "inv.lvs.report")  # run dir never created

    raw = _make_calibre_result(qci, exit_code=0)
    out = CalibreTool().parse_result(raw)
    assert out.success is False
    assert "lvs_report_missing" in out.diagnostics


def test_calibre_parse_missing_qci_passes_result_through(tmp_path: Path) -> None:
    # Runset file never created: parse_result can't do its job but should
    # return the raw result rather than crash.
    raw = _make_calibre_result(tmp_path / "nonexistent.qci", exit_code=0)
    out = CalibreTool().parse_result(raw)
    assert out is raw


# ---- Phase 5.9 B+C: lvs_report_path_from_runset --------------------------


def _phase59_bc_make_qci(
    path: Path, run_dir: str | Path, report_name: str
) -> None:
    """Write a minimal ``.qci`` with the two LVS directives the helper
    parses (everything else is irrelevant — calibre would also accept
    extra fields in real files)."""
    path.write_text(
        f"*lvsRunDir: {run_dir}\n*lvsReportFile: {report_name}\n",
        encoding="utf-8",
    )


def test_phase59_bc_lvs_report_path_from_runset_parses_qci(tmp_path: Path) -> None:
    """Happy path: absolute lvsRunDir + existing report file → resolved path."""
    from auto_ext.tools.calibre import lvs_report_path_from_runset

    run_dir = tmp_path / "calibre_run"
    run_dir.mkdir()
    (run_dir / "inv.lvs.report").write_text("CORRECT\n", encoding="utf-8")
    qci = tmp_path / "rendered.qci"
    _phase59_bc_make_qci(qci, run_dir, "inv.lvs.report")

    out = lvs_report_path_from_runset(qci)
    assert out == run_dir / "inv.lvs.report"


def test_phase59_bc_lvs_report_path_from_runset_missing_directives(
    tmp_path: Path,
) -> None:
    """A .qci with neither ``lvsRunDir`` nor ``lvsReportFile`` → None
    (the helper's contract — caller disables the menu action)."""
    from auto_ext.tools.calibre import lvs_report_path_from_runset

    qci = tmp_path / "rendered.qci"
    qci.write_text(
        "# misc field\n*foo: bar\n*lvsLayoutFile: /path/to/layout\n",
        encoding="utf-8",
    )
    assert lvs_report_path_from_runset(qci) is None


def test_phase59_bc_lvs_report_path_from_runset_missing_runset_file(
    tmp_path: Path,
) -> None:
    """The .qci itself doesn't exist → None (don't raise)."""
    from auto_ext.tools.calibre import lvs_report_path_from_runset

    assert lvs_report_path_from_runset(tmp_path / "no_such.qci") is None


def test_phase59_bc_lvs_report_path_from_runset_report_file_missing(
    tmp_path: Path,
) -> None:
    """Both directives present, but the report file isn't on disk yet
    (mid-run, or calibre crashed). Helper returns None — caller still
    disables the action."""
    from auto_ext.tools.calibre import lvs_report_path_from_runset

    qci = tmp_path / "rendered.qci"
    _phase59_bc_make_qci(qci, tmp_path / "calibre_run", "inv.lvs.report")
    # tmp_path/"calibre_run" never created → report file definitely absent
    assert lvs_report_path_from_runset(qci) is None


def test_phase59_bc_lvs_report_path_from_runset_relative_runset_dir(
    tmp_path: Path,
) -> None:
    """A relative lvsRunDir is anchored on the .qci's parent directory.

    Documented edge case: production runsets always carry an absolute
    path, but rendered fixtures sometimes use relative ones, and we
    need a deterministic anchor.
    """
    from auto_ext.tools.calibre import lvs_report_path_from_runset

    rendered_dir = tmp_path / "rendered"
    rendered_dir.mkdir()
    # Sibling dir to where the .qci lives.
    sibling_run = tmp_path / "rendered" / "subrun"
    sibling_run.mkdir()
    (sibling_run / "out.lvs.report").write_text("CORRECT\n", encoding="utf-8")

    qci = rendered_dir / "calibre_lvs.qci"
    # Note: relative path "subrun" — anchored on qci.parent = rendered_dir.
    _phase59_bc_make_qci(qci, "subrun", "out.lvs.report")

    out = lvs_report_path_from_runset(qci)
    assert out == sibling_run / "out.lvs.report"


# ---- render_template smoke on the real production templates -------------


@pytest.mark.parametrize(
    "subpath",
    [
        "si/default.env.j2",
        "calibre/calibre_lvs.qci.j2",
        "quantus/ext.cmd.j2",
        "quantus/dspf.cmd.j2",
        "jivaro/default.xml.j2",
    ],
)
def test_production_templates_render(
    templates_root: Path, tmp_path: Path, subpath: str
) -> None:
    out = tmp_path / Path(subpath).stem
    rendered = SiTool().render_template(
        templates_root / subpath,
        context=_production_context(tmp_path),
        env=_production_env(),
        out_path=out,
    )
    assert rendered.is_file()
    text = rendered.read_text(encoding="utf-8")
    # No unsubstituted env refs of any form should remain (the default
    # render is strict_env=True; this asserts the substitution coverage).
    assert "$env(" not in text
    assert "${" not in text
    # Explicit placeholders we care about are gone.
    assert "__CELL_NAME__" not in text
    assert "user_defined_" not in text


# ---- Phase 5.9 A: per-line flush so live log streaming works -------------


def _phase59_a_poll_for_substring(
    log_path: Path, needle: str, timeout: float
) -> bool:
    """Poll ``log_path`` until ``needle`` appears or ``timeout`` elapses.

    Returns ``True`` when found mid-execution, ``False`` on timeout.
    """

    deadline = time.time() + timeout
    while time.time() < deadline:
        if log_path.exists():
            try:
                content = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            if needle in content:
                return True
        time.sleep(0.05)
    return False


def test_phase59_a_run_subprocess_flushes_per_line(tmp_path: Path) -> None:
    """The on-disk log must show new lines DURING the subprocess run, not
    only after it exits — that is the contract the GUI's LogTab
    (QFileSystemWatcher + 1 s poll) relies on for live tailing. Prior to
    Phase 5.9 A, ``log.write(item)`` was not followed by ``log.flush()``,
    so Python's ~8 KB text-mode buffer on the *parent's* log file could
    hold the entire stage's output until close.

    Strategy: launch a child that writes one marker, sleeps long enough
    for a watcher thread to inspect the on-disk file, writes a second
    marker, then sleeps again. We assert the second marker becomes
    visible while the child is still running. The two-phase sleep
    guards against false positives where the OS happens to flush the
    page cache once at process start.
    """

    from auto_ext.tools.base import run_subprocess

    log_path = tmp_path / "logs" / "stream.log"
    # The audit header prepended by run_subprocess includes the literal
    # argv (so the source code of -c is verbatim in the log header).
    # That means any string baked into the script source — even via
    # f-strings — also lands in the header instantly. To dodge that,
    # build the marker at child-side runtime via chr() so it does NOT
    # appear textually in argv but DOES appear in the printed line.
    script = (
        "import sys, time\n"
        # Reconstruct the marker token from chr() codes so the literal
        # bytes 'STREAM-MID' are never in the argv string.
        "tok = ''.join(chr(c) for c in "
        "[83,84,82,69,65,77,45,77,73,68])\n"  # 'STREAM-MID'
        "print('PHASE59A-START', flush=True)\n"
        "time.sleep(0.4)\n"
        "print(f'phase59a:{tok}', flush=True)\n"
        "time.sleep(0.6)\n"
    )
    argv = [sys.executable, "-c", script]

    saw_mid_during_run: dict[str, bool] = {"value": False}

    def watcher() -> None:
        # Wait until the child has emitted the obfuscated marker (after
        # the +0.4 s sleep), then check disk while the child still has
        # 0.6 s of sleep ahead.
        deadline = time.time() + 0.95
        while time.time() < deadline:
            if log_path.exists():
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                if "STREAM-MID" in text:
                    saw_mid_during_run["value"] = True
                    return
            time.sleep(0.05)

    t = threading.Thread(target=watcher, daemon=True)
    t.start()

    exit_code = run_subprocess(
        argv,
        cwd=tmp_path,
        env={"PATH": str(Path(sys.executable).parent)},
        log_path=log_path,
    )
    t.join(timeout=2.0)

    assert exit_code == 0
    # The mid marker MUST have hit disk before the child exited and
    # the `with log_path.open(...)` block closed/flushed implicitly.
    assert saw_mid_during_run["value"] is True, (
        "mid-run marker did not reach disk before subprocess exit; "
        "per-line log.flush() in run_subprocess is missing or broken"
    )
    # Sanity: final content has both markers in expected order.
    final = log_path.read_text(encoding="utf-8")
    assert "PHASE59A-START" in final
    assert "phase59a:STREAM-MID" in final
    assert final.index("PHASE59A-START") < final.index("phase59a:STREAM-MID")


# ---- S1 shared fixtures for the production templates ----------------------


def _production_context(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    """The context the runner builds for a real task, built by the runner's code.

    Deliberately not a hand-written dict any more. The flat names the shipped
    templates use (``lvs_variant``, ``exclude_floating_nets_limit``,
    ``technology_corner`` ...) used to come from the manifest knob layer and
    now come from :func:`auto_ext.core.render.build_context`, one alias per
    catalog row. A literal dict here would go stale the moment a row is added
    -- which is exactly how these five tests started failing when the knob
    layer was removed.

    ``overrides`` are applied last, so a test can still pin one value.
    """

    from auto_ext.core import render

    from tests.support.v2 import make_dut, make_profile, make_recipe, make_run

    context = render.build_context(
        dut=make_dut(library="LIB"),
        recipe=make_recipe(),
        profile=make_profile(),
        run=make_run(tmp_path),
        resolved_env=_production_env(),
    )
    context.update(overrides)
    return context


def _production_env() -> dict[str, str]:
    from tests.support.v2 import ENV

    return dict(ENV)


def _render_production_quantus(
    templates_root: Path, tmp_path: Path, name: str, **ctx_overrides: Any
) -> Path:
    """Render one of the bundled quantus templates and return the output path."""

    out = tmp_path / Path(name).stem
    QuantusTool().render_template(
        templates_root / "quantus" / name,
        context=_production_context(tmp_path, **ctx_overrides),
        env=_production_env(),
        out_path=out,
    )
    return out


# ---- S1: ToolResult carries the exit code -------------------------------


def test_tool_result_exit_code_defaults_to_none() -> None:
    # None means "no process ran" -- a dry run, or a synthetic result.
    assert ToolResult(success=True).exit_code is None


def test_run_records_the_exit_code_on_the_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import auto_ext.tools.base as base

    def fake(argv, cwd, env, log_path, *, cancel_token=None):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("stubbed\n", encoding="utf-8")
        return 42

    monkeypatch.setattr(base, "run_subprocess", fake)

    result = StrmoutTool().run(
        argv=["strmout"], cwd=tmp_path, env={}, log_path=tmp_path / "logs" / "s.log"
    )

    assert result.exit_code == 42
    assert result.success is False
    # Still mirrored into diagnostics: callers have read it from there since
    # before the field existed.
    assert result.diagnostics["exit_code"] == 42


def test_run_subprocess_missing_executable_reaches_the_result_as_127(
    tmp_path: Path,
) -> None:
    result = StrmoutTool().run(
        argv=["auto_ext_no_such_binary_zz"],
        cwd=tmp_path,
        env={"PATH": str(tmp_path)},
        log_path=tmp_path / "logs" / "strmout.log",
    )
    assert result.exit_code == 127
    assert result.success is False


# ---- S1: artifact recording ---------------------------------------------


def test_with_artifacts_records_existing_files(tmp_path: Path) -> None:
    real = tmp_path / "out.dspf"
    real.write_text("x", encoding="utf-8")

    out = ToolResult(success=True).with_artifacts([real])

    assert out.artifact_paths == [real]
    assert "missing_artifacts" not in out.diagnostics


def test_with_artifacts_files_absent_declarations_separately(tmp_path: Path) -> None:
    # A run record whose artifact list points at files that were never
    # written is worse than one that admits the output is missing.
    ghost = tmp_path / "never_written.dspf"

    out = ToolResult(success=True).with_artifacts([ghost])

    assert out.artifact_paths == []
    assert out.diagnostics["missing_artifacts"] == [str(ghost)]


def test_with_artifacts_can_skip_the_existence_check(tmp_path: Path) -> None:
    ghost = tmp_path / "never_written.dspf"
    out = ToolResult(success=True).with_artifacts([ghost], require_exists=False)
    assert out.artifact_paths == [ghost]
    assert "missing_artifacts" not in out.diagnostics


def test_with_artifacts_is_idempotent(tmp_path: Path) -> None:
    real = tmp_path / "out.gds"
    real.write_text("x", encoding="utf-8")

    once = ToolResult(success=True).with_artifacts([real])
    twice = once.with_artifacts([real, real])

    assert twice.artifact_paths == [real]


def test_with_artifacts_preserves_other_fields(tmp_path: Path) -> None:
    real = tmp_path / "out.gds"
    real.write_text("x", encoding="utf-8")
    original = ToolResult(
        success=True,
        exit_code=0,
        stdout_path=tmp_path / "log.txt",
        diagnostics={"argv": ["strmout"]},
    )

    out = original.with_artifacts([real])

    assert out.exit_code == 0
    assert out.stdout_path == tmp_path / "log.txt"
    assert out.diagnostics["argv"] == ["strmout"]
    # The original is untouched: with_artifacts returns a copy.
    assert original.artifact_paths == []


def test_with_artifacts_dedupes_cellviews() -> None:
    view = CellViewRef(library="LIB", cell="inv", view="av_extracted")
    out = ToolResult(success=True).with_artifacts(cellviews=[view, view])
    assert out.cellviews == [view]


def test_cellview_ref_key_and_dict() -> None:
    view = CellViewRef(library="LIB", cell="inv", view="av_extracted")
    assert view.key == "LIB/inv/av_extracted"
    assert view.as_dict() == {"library": "LIB", "cell": "inv", "view": "av_extracted"}


def test_argv_value_and_argv_path(tmp_path: Path) -> None:
    argv = ["qrc", "-cmd", str(tmp_path / "ext.cmd")]
    assert argv_value(argv, "-cmd") == str(tmp_path / "ext.cmd")
    assert argv_value(argv, "-nope") is None
    # Flag present but nothing after it.
    assert argv_value(["qrc", "-cmd"], "-cmd") is None

    result = ToolResult(success=True, diagnostics={"argv": argv})
    assert argv_path(result, "-cmd") == tmp_path / "ext.cmd"
    assert argv_path(ToolResult(success=True), "-cmd") is None


# ---- S1: calibre artifacts ------------------------------------------------


def test_calibre_records_the_report_as_an_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    report = run_dir / "inv.lvs.report"
    report.write_text("CORRECT\nDISCREPANCIES = 0\n", encoding="utf-8")
    qci = tmp_path / "inv.qci"
    _write_qci(qci, run_dir, "inv.lvs.report")

    out = CalibreTool().parse_result(_make_calibre_result(qci))

    assert out.artifact_paths == [report]


def test_calibre_records_spice_and_erc_outputs_when_they_exist(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "inv.lvs.report").write_text("CORRECT\nDISCREPANCIES = 0\n", encoding="utf-8")
    (run_dir / "inv.sp").write_text("* spice\n", encoding="utf-8")
    (run_dir / "inv.erc.summary").write_text("erc\n", encoding="utf-8")
    qci = tmp_path / "inv.qci"
    qci.write_text(
        f"*lvsRunDir: {run_dir}\n"
        "*lvsReportFile: inv.lvs.report\n"
        "*lvsSpiceFile: inv.sp\n"
        "*lvsERCDatabase: inv.erc.results\n"
        "*lvsERCSummaryFile: inv.erc.summary\n",
        encoding="utf-8",
    )

    out = CalibreTool().parse_result(_make_calibre_result(qci))

    names = {p.name for p in out.artifact_paths}
    assert names == {"inv.lvs.report", "inv.sp", "inv.erc.summary"}
    # inv.erc.results was declared but never written -- ERC is not enabled in
    # this flow at all, so it must NOT show up as a missing output (that key
    # is the classifier's "the tool produced nothing" signal).
    assert "missing_artifacts" not in out.diagnostics


def test_calibre_preserves_the_exit_code_through_parse_result(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "inv.lvs.report").write_text("INCORRECT\nDISCREPANCIES = 2\n", encoding="utf-8")
    qci = tmp_path / "inv.qci"
    _write_qci(qci, run_dir, "inv.lvs.report")

    raw = ToolResult(
        success=True,
        exit_code=0,
        diagnostics={"exit_code": 0, "argv": ["calibre", "-runset", str(qci)]},
    )
    out = CalibreTool().parse_result(raw)

    # Calibre exited cleanly; the LVS verdict is what fails the stage. The
    # exit code must survive so the classifier can tell the two apart.
    assert out.exit_code == 0
    assert out.success is False


def test_calibre_declared_outputs_maps_every_directive(tmp_path: Path) -> None:
    from auto_ext.tools.calibre import calibre_declared_outputs

    run_dir = tmp_path / "run"
    qci = tmp_path / "inv.qci"
    qci.write_text(
        f"*lvsRunDir: {run_dir}\n"
        "*lvsReportFile: inv.lvs.report\n"
        "*lvsSpiceFile: inv.sp\n"
        "*lvsERCDatabase: inv.erc.results\n"
        "*lvsERCSummaryFile: inv.erc.summary\n",
        encoding="utf-8",
    )

    outputs = calibre_declared_outputs(qci)

    assert set(outputs) == {"report", "spice", "erc_database", "erc_summary"}
    assert outputs["report"] == run_dir / "inv.lvs.report"


def test_calibre_declared_outputs_without_a_run_dir_is_empty(tmp_path: Path) -> None:
    from auto_ext.tools.calibre import calibre_declared_outputs

    qci = tmp_path / "inv.qci"
    qci.write_text("*lvsReportFile: inv.lvs.report\n", encoding="utf-8")
    assert calibre_declared_outputs(qci) == {}


def test_calibre_cell_summary_reaches_the_diagnostics(tmp_path: Path) -> None:
    # End-to-end for the "which sub-cells mismatched" chain: report text ->
    # checks.py rows -> ToolResult.diagnostics["lvs_report"].
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "inv.lvs.report").write_text(
        "INCORRECT\nDISCREPANCIES = 2\nCELL SUMMARY\n"
        "  INCORRECT  bias_gen  bias_gen\n"
        "  CORRECT    buf_x2    buf_x2\n",
        encoding="utf-8",
    )
    qci = tmp_path / "inv.qci"
    _write_qci(qci, run_dir, "inv.lvs.report")

    out = CalibreTool().parse_result(_make_calibre_result(qci, exit_code=0))

    assert out.diagnostics["lvs_report"].mismatched_cells == ("bias_gen",)


# ---- S1: strmout artifacts ------------------------------------------------


def test_strmout_records_the_stream_file(tmp_path: Path) -> None:
    gds = tmp_path / "inv.calibre.db"
    gds.write_text("gds", encoding="utf-8")
    raw = ToolResult(
        success=True,
        exit_code=0,
        diagnostics={"argv": ["strmout", "-strmFile", str(gds)]},
    )

    out = StrmoutTool().parse_result(raw)

    assert out.artifact_paths == [gds]


def test_strmout_reports_a_stream_file_that_never_appeared(tmp_path: Path) -> None:
    gds = tmp_path / "inv.calibre.db"
    raw = ToolResult(
        success=False,
        exit_code=1,
        diagnostics={"argv": ["strmout", "-strmFile", str(gds)]},
    )

    out = StrmoutTool().parse_result(raw)

    assert out.artifact_paths == []
    assert out.diagnostics["missing_artifacts"] == [str(gds)]


def test_strmout_without_an_argv_passes_through() -> None:
    raw = ToolResult(success=True)
    assert StrmoutTool().parse_result(raw) is raw


# ---- S1: si artifacts -----------------------------------------------------


def _write_si_env(directory: Path, run_dir: str, netlist: str = "inv.src.net") -> Path:
    si_env = directory / "si.env"
    si_env.write_text(
        'simLibName = "LIB"\n'
        'simCellName = "inv"\n'
        f'hnlNetlistFileName = "{netlist}"\n'
        f'simRunDir = "{run_dir}"\n',
        encoding="utf-8",
    )
    return si_env


def test_si_netlist_path_joins_run_dir_and_file_name(tmp_path: Path) -> None:
    from auto_ext.tools.si import si_netlist_path

    out_dir = tmp_path / "cds_out"
    si_env = _write_si_env(tmp_path, str(out_dir))
    assert si_netlist_path(si_env) == out_dir / "inv.src.net"


def test_si_netlist_path_anchors_a_relative_run_dir_on_the_env_file(
    tmp_path: Path,
) -> None:
    # simRunDir is relative to the cwd si runs in, which is where si.env sits.
    si_env = _write_si_env(tmp_path, "sub_out")
    from auto_ext.tools.si import si_netlist_path

    assert si_netlist_path(si_env) == tmp_path / "sub_out" / "inv.src.net"


def test_si_netlist_path_without_the_directives_is_none(tmp_path: Path) -> None:
    from auto_ext.tools.si import si_netlist_path

    si_env = tmp_path / "si.env"
    si_env.write_text('simLibName = "LIB"\n', encoding="utf-8")
    assert si_netlist_path(si_env) is None


def test_si_run_records_the_netlist_it_produced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # si.env only exists in cwd for the duration of the stage (serial_workdir
    # deletes it on exit), which is why si records its artifact in run()
    # rather than parse_result().
    out_dir = tmp_path / "cds_out"
    out_dir.mkdir()
    netlist = out_dir / "inv.src.net"
    _write_si_env(tmp_path, str(out_dir))
    _stub_si_subprocess(monkeypatch)
    netlist.write_text("* netlist\n", encoding="utf-8")

    result = SiTool().run(
        argv=["si", "-batch"], cwd=tmp_path, env={}, log_path=tmp_path / "logs" / "si.log"
    )

    assert result.artifact_paths == [netlist]


def test_si_run_reports_a_netlist_that_was_never_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "cds_out"
    out_dir.mkdir()
    _write_si_env(tmp_path, str(out_dir))
    _stub_si_subprocess(monkeypatch)

    result = SiTool().run(
        argv=["si", "-batch"], cwd=tmp_path, env={}, log_path=tmp_path / "logs" / "si.log"
    )

    assert result.artifact_paths == []
    assert result.diagnostics["missing_artifacts"] == [str(out_dir / "inv.src.net")]


def test_si_run_without_an_si_env_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_si_subprocess(monkeypatch)

    result = SiTool().run(
        argv=["si", "-batch"], cwd=tmp_path, env={}, log_path=tmp_path / "logs" / "si.log"
    )

    assert result.success is True
    assert result.artifact_paths == []
    assert "missing_artifacts" not in result.diagnostics


# ---- S1: quantus command-file parsing -------------------------------------


def test_quantus_parser_joins_continuations_and_strips_comments() -> None:
    from auto_ext.tools.quantus import parse_quantus_commands

    text = (
        "#--------------------------------\n"
        "#OPTION COMMAND FILE created by Cadence Extraction Quantus UI Version 18.21\n"
        "#--------------------------------\n"
        "\n"
        "capacitance \\\n"
        "              -decoupling_factor 1.0 \\\n"
        '              -ground_net "vss"\n'
        "process_technology \\\n"
        "              -technology_corner \\\n"
        '              "TYPICAL" \\\n'
        "              -temperature \\\n"
        "              25\n"
    )

    commands = parse_quantus_commands(text)

    assert [c.name for c in commands] == ["capacitance", "process_technology"]
    assert commands[0].value("-ground_net") == "vss"
    assert commands[0].value("-decoupling_factor") == "1.0"
    # The value sitting on its own continuation line still binds to its option.
    assert commands[1].value("-technology_corner") == "TYPICAL"
    assert commands[1].value("-temperature") == "25"


def test_quantus_parser_keeps_multi_value_options() -> None:
    from auto_ext.tools.quantus import parse_quantus_commands

    text = 'output_db -type dspf \\\n  -output_xy \\\n  "CANONICAL_RES" \\\n  "MOS"\n'
    commands = parse_quantus_commands(text)
    assert commands[0].options["-output_xy"] == ("CANONICAL_RES", "MOS")


def test_quantus_parser_treats_a_negative_number_as_a_value() -> None:
    from auto_ext.tools.quantus import parse_quantus_commands

    commands = parse_quantus_commands("filter_res -min_res -1.5\n")
    assert commands[0].value("-min_res") == "-1.5"


def test_quantus_dspf_output_is_a_file_artifact(tmp_path: Path) -> None:
    dspf = tmp_path / "inv.dspf"
    dspf.write_text("* dspf\n", encoding="utf-8")
    cmd = tmp_path / "dspf.cmd"
    cmd.write_text(
        "output_db -type dspf \\\n"
        "              -subtype extended\n"
        "output_setup \\\n"
        f'              -file_name "{dspf}" \\\n'
        '              -temporary_directory_name "Design"\n',
        encoding="utf-8",
    )
    raw = ToolResult(
        success=True, exit_code=0, diagnostics={"argv": ["qrc", "-cmd", str(cmd)]}
    )

    out = QuantusTool().parse_result(raw)

    assert out.artifact_paths == [dspf]
    assert out.cellviews == []
    assert out.diagnostics["quantus_output_type"] == "dspf"


def test_quantus_extracted_view_is_a_cellview_not_a_path(tmp_path: Path) -> None:
    # docs/refactor/05-catalog-critique.md A5: the extracted view lands in
    # the DFII library as library/cell/view. There is no file, so nothing may
    # be written into artifact_paths for it.
    cmd = tmp_path / "ext.cmd"
    cmd.write_text(
        "input_db -type calibre \\\n"
        '              -design_cell_name "inv layout LIB" \\\n'
        '              -run_name "Design"\n'
        "output_db -type extracted_view \\\n"
        '              -view_name "av_extracted"\n',
        encoding="utf-8",
    )
    raw = ToolResult(
        success=True, exit_code=0, diagnostics={"argv": ["qrc", "-cmd", str(cmd)]}
    )

    out = QuantusTool().parse_result(raw)

    assert out.artifact_paths == []
    assert out.cellviews == [CellViewRef(library="LIB", cell="inv", view="av_extracted")]
    assert "missing_artifacts" not in out.diagnostics


def test_quantus_reports_a_dspf_that_was_never_written(tmp_path: Path) -> None:
    dspf = tmp_path / "inv.dspf"
    cmd = tmp_path / "dspf.cmd"
    cmd.write_text(
        "output_db -type dspf\n" f'output_setup -file_name "{dspf}"\n', encoding="utf-8"
    )
    raw = ToolResult(
        success=False, exit_code=1, diagnostics={"argv": ["qrc", "-cmd", str(cmd)]}
    )

    out = QuantusTool().parse_result(raw)

    assert out.artifact_paths == []
    assert out.diagnostics["missing_artifacts"] == [str(dspf)]


def test_quantus_incomplete_extracted_view_is_flagged_not_guessed(
    tmp_path: Path,
) -> None:
    # No input_db -design_cell_name: the library and cell are unknown, so no
    # triple is fabricated -- the fact is recorded as a diagnostic instead.
    cmd = tmp_path / "ext.cmd"
    cmd.write_text(
        'output_db -type extracted_view -view_name "av_extracted"\n', encoding="utf-8"
    )
    raw = ToolResult(
        success=True, exit_code=0, diagnostics={"argv": ["qrc", "-cmd", str(cmd)]}
    )

    out = QuantusTool().parse_result(raw)

    assert out.cellviews == []
    assert out.diagnostics["quantus_extracted_view_incomplete"] == "av_extracted"


def test_quantus_without_a_command_file_passes_through(tmp_path: Path) -> None:
    raw = ToolResult(
        success=True, diagnostics={"argv": ["qrc", "-cmd", str(tmp_path / "gone.cmd")]}
    )
    assert QuantusTool().parse_result(raw) is raw


def test_quantus_outputs_of_the_production_ext_template(
    templates_root: Path, tmp_path: Path
) -> None:
    # Parse what the real bundled template renders, not a hand-written stub.
    from auto_ext.tools.quantus import quantus_outputs

    cmd = _render_production_quantus(templates_root, tmp_path, "ext.cmd.j2")
    outputs = quantus_outputs(cmd)

    assert outputs.output_type == "extracted_view"
    assert outputs.dspf_path is None
    assert outputs.extracted_view == CellViewRef(
        library="LIB", cell="inv", view="av_ext"
    )


def test_quantus_outputs_of_the_production_dspf_template(
    templates_root: Path, tmp_path: Path
) -> None:
    from auto_ext.tools.quantus import quantus_outputs

    dspf_out = tmp_path / "inv.dspf"
    cmd = _render_production_quantus(
        templates_root, tmp_path, "dspf.cmd.j2", dspf_out_path=str(dspf_out)
    )
    outputs = quantus_outputs(cmd)

    assert outputs.output_type == "dspf"
    assert outputs.dspf_path == dspf_out
    assert outputs.extracted_view is None


# ---- S1: jivaro artifacts -------------------------------------------------


def test_jivaro_records_the_reduced_cellview(tmp_path: Path) -> None:
    config = tmp_path / "jivaro.xml"
    config.write_text(
        '<?xml version="1.0" ?> <reductionParameters version="2024.1"> <options>'
        '<inputView value="LIB/inv/av_ext"/>'
        '<outputView value="av_ext_red"/>'
        "</options> </reductionParameters>",
        encoding="utf-8",
    )
    raw = ToolResult(
        success=True, exit_code=0, diagnostics={"argv": ["jivaro", "-xml", str(config)]}
    )

    out = JivaroTool().parse_result(raw)

    assert out.cellviews == [CellViewRef(library="LIB", cell="inv", view="av_ext_red")]
    assert out.artifact_paths == []
    assert out.diagnostics["jivaro_output_view"] == "LIB/inv/av_ext_red"


def test_jivaro_output_view_may_carry_its_own_triple(tmp_path: Path) -> None:
    from auto_ext.tools.jivaro import jivaro_output_view

    config = tmp_path / "jivaro.xml"
    config.write_text(
        '<reductionParameters><inputView value="LIB/inv/av_ext"/>'
        '<outputView value="OTHER/cellB/av_red"/></reductionParameters>',
        encoding="utf-8",
    )
    assert jivaro_output_view(config) == CellViewRef(
        library="OTHER", cell="cellB", view="av_red"
    )


def test_jivaro_malformed_xml_is_not_guessed_at(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from auto_ext.tools.jivaro import jivaro_output_view

    config = tmp_path / "jivaro.xml"
    config.write_text("<reductionParameters><oops>", encoding="utf-8")
    caplog.set_level(logging.WARNING, logger="auto_ext.tools.jivaro")

    assert jivaro_output_view(config) is None
    assert any("well-formed" in m for m in caplog.messages)


def test_jivaro_without_an_input_view_records_nothing(tmp_path: Path) -> None:
    from auto_ext.tools.jivaro import jivaro_output_view

    config = tmp_path / "jivaro.xml"
    config.write_text(
        '<reductionParameters><outputView value="av_red"/></reductionParameters>',
        encoding="utf-8",
    )
    assert jivaro_output_view(config) is None


def test_jivaro_records_the_view_from_the_production_template(
    templates_root: Path, tmp_path: Path
) -> None:
    from auto_ext.tools.jivaro import jivaro_output_view

    out = tmp_path / "default.xml"
    JivaroTool().render_template(
        templates_root / "jivaro" / "default.xml.j2",
        context=_production_context(tmp_path),
        env=_production_env(),
        out_path=out,
    )
    assert jivaro_output_view(out) == CellViewRef(
        library="LIB", cell="inv", view="av_ext_red"
    )
