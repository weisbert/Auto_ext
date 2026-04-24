"""Unit tests for the 5 :class:`auto_ext.tools.base.Tool` plugins.

These cover the stable, platform-independent behaviour: class attributes,
``build_argv`` shape, and (for calibre) the LVS-report ``parse_result``
integration. End-to-end subprocess execution is covered by
``tests/test_runner.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.checks import LvsReport
from auto_ext.tools.base import ToolResult
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

    def fake(argv, cwd, env, log_path):
        calls.append({"argv": list(argv), "cwd": cwd, "log_path": log_path})
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


# ---- render_template smoke on the real production templates -------------


@pytest.mark.parametrize(
    "subpath",
    [
        "si/default.env.j2",
        "calibre/wiodio_noConnectByNetName.qci.j2",
        "quantus/ext.cmd.j2",
        "quantus/dspf.cmd.j2",
        "jivaro/default.xml.j2",
    ],
)
def test_production_templates_render(
    templates_root: Path, tmp_path: Path, subpath: str
) -> None:
    ctx = {
        "library": "LIB",
        "cell": "inv",
        "lvs_source_view": "schematic",
        "lvs_layout_view": "layout",
        "ground_net": "vss",
        "out_file": "av_ext",
        "task_id": "LIB__inv__layout__schematic",
        "output_dir": "/w/cds/out",
        "intermediate_dir": "/w",
        "employee_id": "alice",
        "jivaro_frequency_limit": 14,
        "jivaro_error_max": 2,
        "tech_name": "HN001",
        "pdk_subdir": "CFXXX",
        "lvs_runset_version": "Ver_Plus_1.0l_0.9",
        "qrc_runset_version": "Ver_Plus_1.0a",
    }
    env = {
        "WORK_ROOT": "/w",
        "WORK_ROOT2": "/w",
        "VERIFY_ROOT": "/v",
        "SETUP_ROOT": "/s",
        "calibre_source_added_place": "/v/empty.cdl",
    }
    out = tmp_path / Path(subpath).stem
    rendered = SiTool().render_template(
        templates_root / subpath, context=ctx, env=env, out_path=out
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
