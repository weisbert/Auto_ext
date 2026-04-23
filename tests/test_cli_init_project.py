"""End-to-end tests for ``auto-ext init-project`` (Phase 4b2).

These tests exercise the full orchestrator: raw-file imports, cross-file
PDK aggregation, body rewrites, and write-out of project.yaml + tasks.yaml
+ 4 per-tool templates + manifests. Two fixture sets are used (``raw/`` and
``raw_projectB/``) so cross-project abstraction is verified — a project.yaml
emitted from one set must differ only in the PDK-constant fields from the
other set, and both must dry-run through the runner without modification.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app


@pytest.fixture
def raw_projectA_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "raw"


@pytest.fixture
def raw_projectB_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "raw_projectB"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke_init(
    runner: CliRunner,
    raw_dir: Path,
    out_root: Path,
    *extra: str,
    include_jivaro: bool = True,
) -> Any:
    args = [
        "init-project",
        "--raw-calibre",
        str(raw_dir / "calibre_sample.qci"),
        "--raw-si",
        str(raw_dir / "si_sample.env"),
        "--raw-quantus",
        str(raw_dir / "quantus_sample.cmd"),
        "--output-config-dir",
        str(out_root / "config"),
        "--output-templates-dir",
        str(out_root / "templates"),
    ]
    if include_jivaro:
        args.extend(["--raw-jivaro", str(raw_dir / "jivaro_sample.xml")])
    args.extend(extra)
    return runner.invoke(app, args)


def test_init_project_projectA_writes_full_skeleton(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    result = _invoke_init(runner, raw_projectA_dir, tmp_path)
    assert result.exit_code == 0, result.output

    # All 10 target files exist.
    cfg = tmp_path / "config"
    tpl = tmp_path / "templates"
    assert (cfg / "project.yaml").is_file()
    assert (cfg / "tasks.yaml").is_file()
    for tool, ext in (
        ("calibre", "qci"),
        ("si", "env"),
        ("quantus", "cmd"),
        ("jivaro", "xml"),
    ):
        assert (tpl / tool / f"imported.{ext}.j2").is_file()
        assert (tpl / tool / f"imported.{ext}.j2.manifest.yaml").is_file()

    # project.yaml reports the detected constants.
    from auto_ext.core.config import load_project

    project = load_project(cfg / "project.yaml")
    assert project.tech_name == "HN001"
    assert project.pdk_subdir == "CFXXX"
    assert project.runset_versions.lvs == "Ver_Plus_1.0l_0.9"
    assert project.runset_versions.qrc == "Ver_Plus_1.0a"
    # Template pointers resolve to the written .j2 files.
    assert project.templates.calibre == tpl / "calibre" / "imported.qci.j2"
    assert project.templates.jivaro == tpl / "jivaro" / "imported.xml.j2"


def test_init_project_tasks_yaml_uses_detected_identity(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    result = _invoke_init(runner, raw_projectA_dir, tmp_path)
    assert result.exit_code == 0, result.output

    from auto_ext.core.config import load_project, load_tasks

    project = load_project(tmp_path / "config" / "project.yaml")
    tasks = load_tasks(tmp_path / "config" / "tasks.yaml", project=project)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.library == "INV_LIB"
    assert t.cell == "INV1"
    assert t.lvs_layout_view == "layout"
    assert t.lvs_source_view == "schematic"
    assert t.ground_net == "vss"
    assert t.out_file == "av_ext"
    assert t.jivaro.enabled is True


def test_init_project_without_jivaro_skips_template(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    result = _invoke_init(
        runner, raw_projectA_dir, tmp_path, include_jivaro=False
    )
    assert result.exit_code == 0, result.output

    tpl = tmp_path / "templates"
    assert not (tpl / "jivaro").exists()
    assert (tpl / "calibre" / "imported.qci.j2").is_file()

    from auto_ext.core.config import load_project, load_tasks

    project = load_project(tmp_path / "config" / "project.yaml")
    # jivaro pointer absent.
    assert project.templates.jivaro is None
    tasks = load_tasks(tmp_path / "config" / "tasks.yaml", project=project)
    assert tasks[0].jivaro.enabled is False


def test_init_project_dry_run_passes_end_to_end(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generated project must be immediately runnable (dry-run)
    against the shipped templates — no manual edits required.
    """
    result = _invoke_init(runner, raw_projectA_dir, tmp_path)
    assert result.exit_code == 0, result.output

    workarea = tmp_path / "w"
    workarea.mkdir()
    monkeypatch.setenv("WORK_ROOT", str(workarea))
    monkeypatch.setenv("WORK_ROOT2", str(workarea))
    monkeypatch.setenv("VERIFY_ROOT", str(workarea / "verify"))
    monkeypatch.setenv("SETUP_ROOT", str(workarea / "setup"))
    monkeypatch.setenv("PDK_LAYER_MAP_FILE", str(workarea / "layers.map"))

    run_result = runner.invoke(
        app,
        [
            "run",
            "--config-dir",
            str(tmp_path / "config"),
            "--dry-run",
            "--auto-ext-root",
            str(tmp_path / "run_root"),
            "--workarea",
            str(workarea),
        ],
    )
    assert run_result.exit_code == 0, run_result.output
    # Every stage rendered.
    rendered = tmp_path / "run_root" / "runs" / "task_INV_LIB__INV1__layout__schematic" / "rendered"
    assert (rendered / "imported.qci").is_file()
    assert (rendered / "imported.env").is_file()
    assert (rendered / "imported.cmd").is_file()
    assert (rendered / "imported.xml").is_file()


def test_init_project_refuses_overwrite_without_force(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    first = _invoke_init(runner, raw_projectA_dir, tmp_path)
    assert first.exit_code == 0, first.output

    second = _invoke_init(runner, raw_projectA_dir, tmp_path)
    assert second.exit_code == 2
    assert "refusing to overwrite" in second.output


def test_init_project_force_backs_up_existing(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    first = _invoke_init(runner, raw_projectA_dir, tmp_path)
    assert first.exit_code == 0

    # Perturb the existing project.yaml so we can confirm the backup really
    # preserved the original.
    original = (tmp_path / "config" / "project.yaml").read_text(encoding="utf-8")

    second = _invoke_init(runner, raw_projectA_dir, tmp_path, "--force")
    assert second.exit_code == 0, second.output
    assert (tmp_path / "config" / "project.yaml.bak").is_file()
    assert (
        tmp_path / "config" / "project.yaml.bak"
    ).read_text(encoding="utf-8") == original


def test_init_project_identity_mismatch_errors(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    """Feed mismatched cells across tools — must refuse with a clear
    error message (no overrides given).
    """
    # Copy the si fixture but with a different cell than calibre/quantus.
    # Rewrite every cell reference in si so si is internally consistent
    # (no ImportError from per-file cross-validation) but cross-tool
    # disagrees — that's what should trip init-project's validator.
    broken_dir = tmp_path / "broken_raw"
    broken_dir.mkdir()
    for name in ("calibre_sample.qci", "quantus_sample.cmd", "jivaro_sample.xml"):
        (broken_dir / name).write_bytes((raw_projectA_dir / name).read_bytes())
    si_raw = (raw_projectA_dir / "si_sample.env").read_text(encoding="utf-8")
    si_raw = si_raw.replace("INV1", "NOT_INV1")
    (broken_dir / "si_sample.env").write_text(si_raw, encoding="utf-8")

    out_root = tmp_path / "out"
    result = _invoke_init(runner, broken_dir, out_root)
    assert result.exit_code == 2
    assert "identity mismatch" in result.output
    # The mismatching field surfaces.
    assert "cell" in result.output


def test_init_project_cross_project_abstraction(
    runner: CliRunner,
    raw_projectA_dir: Path,
    raw_projectB_dir: Path,
    tmp_path: Path,
) -> None:
    """init-project on two different PDKs must emit:

    - the same set of placeholders per template (identity + PDK constants
      all lifted into Jinja ``[[...]]`` at the same positions), and
    - project.yaml files that carry the PDK-specific raw values.

    Byte-equal template bodies is too strict — values classified as
    ``unclassified`` by the strict ≥ 2-tool rule (e.g. ``project_subdir``
    when only si carries it) remain as raw strings in the body. That's by
    design; it's surfaced in the summary report for user review.
    """
    out_A = tmp_path / "A"
    out_B = tmp_path / "B"
    resA = _invoke_init(runner, raw_projectA_dir, out_A)
    resB = _invoke_init(runner, raw_projectB_dir, out_B)
    assert resA.exit_code == 0, resA.output
    assert resB.exit_code == 0, resB.output

    # Each per-tool body must contain the placeholders for every value
    # aggregate_pdk_tokens was supposed to lift.
    expected_placeholders = {
        "calibre": {
            "[[cell]]",
            "[[library]]",
            "[[lvs_layout_view]]",
            "[[lvs_source_view]]",
            "[[pdk_subdir]]",
            "[[lvs_runset_version]]",
        },
        "si": {
            "[[cell]]",
            "[[library]]",
            "[[lvs_source_view]]",
            "[[pdk_subdir]]",
            "[[lvs_runset_version]]",
        },
        "quantus": {
            "[[cell]]",
            "[[library]]",
            "[[lvs_layout_view]]",
            "[[ground_net]]",
            "[[tech_name]]",
            "[[pdk_subdir]]",
            "[[qrc_runset_version]]",
            "[[employee_id]]",
        },
        "jivaro": {
            "[[cell]]",
            "[[library]]",
            "[[out_file]]",
        },
    }

    # Raw values from each project that MUST NOT leak into the body of
    # the other project — a sanity check that per-project constants did
    # not bleed into a template.
    raw_leak_A = {"INV1", "INV_LIB", "HN001", "CFXXX", "Ver_Plus_1.0l_0.9", "Ver_Plus_1.0a"}
    raw_leak_B = {"AMP2", "AMP_LIB", "HN042", "CFBETA", "Ver_Minus_2.1a_0.3", "Ver_Minus_2.1c"}

    for tool, ext in (("calibre", "qci"), ("si", "env"), ("quantus", "cmd"), ("jivaro", "xml")):
        body_A = (out_A / "templates" / tool / f"imported.{ext}.j2").read_text(encoding="utf-8")
        body_B = (out_B / "templates" / tool / f"imported.{ext}.j2").read_text(encoding="utf-8")
        for ph in expected_placeholders[tool]:
            assert ph in body_A, f"projectA {tool} body missing {ph}"
            assert ph in body_B, f"projectB {tool} body missing {ph}"
        for raw in raw_leak_A:
            assert raw not in body_A, f"projectA {tool} body still contains raw {raw!r}"
        for raw in raw_leak_B:
            assert raw not in body_B, f"projectB {tool} body still contains raw {raw!r}"

    # project.yaml carries different constants.
    from auto_ext.core.config import load_project

    pA = load_project(out_A / "config" / "project.yaml")
    pB = load_project(out_B / "config" / "project.yaml")
    assert pA.tech_name == "HN001"
    assert pB.tech_name == "HN042"
    assert pA.pdk_subdir == "CFXXX"
    assert pB.pdk_subdir == "CFBETA"
    assert pA.runset_versions.lvs == "Ver_Plus_1.0l_0.9"
    assert pB.runset_versions.lvs == "Ver_Minus_2.1a_0.3"
    assert pA.runset_versions.qrc == "Ver_Plus_1.0a"
    assert pB.runset_versions.qrc == "Ver_Minus_2.1c"


def test_init_project_projectB_dry_run_passes(
    runner: CliRunner,
    raw_projectB_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The projectB fixture must also complete init → dry-run without
    hand-editing, proving init-project is not hardcoded to projectA shapes.
    """
    result = _invoke_init(runner, raw_projectB_dir, tmp_path)
    assert result.exit_code == 0, result.output

    workarea = tmp_path / "w"
    workarea.mkdir()
    monkeypatch.setenv("WORK_ROOT", str(workarea))
    monkeypatch.setenv("WORK_ROOT2", str(workarea))
    monkeypatch.setenv("VERIFY_ROOT", str(workarea / "verify"))
    monkeypatch.setenv("SETUP_ROOT", str(workarea / "setup"))
    monkeypatch.setenv("PDK_LAYER_MAP_FILE", str(workarea / "layers.map"))

    run_result = runner.invoke(
        app,
        [
            "run",
            "--config-dir",
            str(tmp_path / "config"),
            "--dry-run",
            "--auto-ext-root",
            str(tmp_path / "run_root"),
            "--workarea",
            str(workarea),
        ],
    )
    assert run_result.exit_code == 0, run_result.output
    rendered = (
        tmp_path / "run_root" / "runs" / "task_AMP_LIB__AMP2__layout__schematic" / "rendered"
    )
    assert (rendered / "imported.qci").is_file()


def test_init_project_summary_shows_promoted_constants(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    """Summary output must cite every PDK constant that init-project
    promoted, so the user can eyeball correctness before running.
    """
    result = _invoke_init(runner, raw_projectA_dir, tmp_path)
    assert result.exit_code == 0, result.output
    assert "tech_name" in result.output and "HN001" in result.output
    assert "pdk_subdir" in result.output and "CFXXX" in result.output
    assert "project_subdir" in result.output and "projB" in result.output
    assert "Ver_Plus_1.0l_0.9" in result.output
    assert "Ver_Plus_1.0a" in result.output


def test_init_project_summary_surfaces_unclassified_on_conflict(
    runner: CliRunner,
    raw_projectA_dir: Path,
    tmp_path: Path,
) -> None:
    """When fixtures produce a token that can't be promoted (here: a
    synthetic project_subdir conflict), the summary flags it as
    ``Unclassified`` so the user knows to hand-review.
    """
    conflict_dir = tmp_path / "conflict_raw"
    conflict_dir.mkdir()
    for name in ("calibre_sample.qci", "quantus_sample.cmd", "jivaro_sample.xml"):
        (conflict_dir / name).write_bytes((raw_projectA_dir / name).read_bytes())
    # Original si uses /data/RFIC3/projB/alice/. Calibre normally has no
    # /data/RFIC3/... path; inject a conflicting projA reference in an
    # unused Tcl trigger line so aggregate_pdk_tokens sees projA (calibre)
    # vs projB (si) and unclassifies both.
    calibre_raw = (conflict_dir / "calibre_sample.qci").read_text(encoding="utf-8")
    calibre_raw += (
        "*lvsPostTriggers: {{cat /data/RFIC3/projA/bob/x/y} process 1}\n"
    )
    (conflict_dir / "calibre_sample.qci").write_text(calibre_raw, encoding="utf-8")
    # Copy si unchanged so it still has projB.
    (conflict_dir / "si_sample.env").write_bytes(
        (raw_projectA_dir / "si_sample.env").read_bytes()
    )

    result = _invoke_init(runner, conflict_dir, tmp_path / "out")
    assert result.exit_code == 0, result.output
    assert "Unclassified" in result.output
    assert "projA" in result.output
    assert "projB" in result.output
