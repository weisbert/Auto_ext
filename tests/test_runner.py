"""End-to-end integration tests for :mod:`auto_ext.core.runner`.

Uses the production templates under ``Auto_ext/templates/`` and the mock
EDA binaries under ``tests/mocks/`` (bash required — skipped on Windows
if git-bash is not installed, via the ``mocks_on_path`` fixture).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.runner import run_tasks


def _load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


def test_happy_path_all_stages_pass(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    project, tasks = _load(project_tools_config)
    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
    )

    assert summary.total == 1
    assert summary.passed == 1
    assert summary.failed == 0
    task_result = summary.tasks[0]
    stage_status = {s.stage: s.status for s in task_result.stages}
    assert stage_status == {
        "si": "passed",
        "strmout": "passed",
        "calibre": "passed",
        "quantus": "passed",
        "jivaro": "passed",
    }
    # Rendered inputs should exist per task.
    rendered_dir = tmp_path / "project_root" / "runs" / f"task_{tasks[0].task_id}" / "rendered"
    assert (rendered_dir / "default.env").is_file()
    assert (rendered_dir / "wiodio_noConnectByNetName.qci").is_file()
    assert (rendered_dir / "ext.cmd").is_file()
    assert (rendered_dir / "default.xml").is_file()
    # Logs should exist per stage.
    log_dir = tmp_path / "project_root" / "logs" / f"task_{tasks[0].task_id}"
    for stage in ("si", "strmout", "calibre", "quantus", "jivaro"):
        assert (log_dir / f"{stage}.log").is_file()


def test_si_env_published_to_output_dir(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """After the si stage runs, the rendered si.env must appear inside
    output_dir (= extraction_output_dir resolved for this task's cell).
    Quantus's LBRCXM-756 error fires if si.env is missing there; the
    runner stages it over post-si because si itself does not.
    """
    project, tasks = _load(project_tools_config)
    run_tasks(
        project,
        tasks,
        stages=["si"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
    )

    # extraction_output_dir = "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    # with WORK_ROOT pinned to workarea in the fixture.
    output_dir = workarea / "cds" / "verify" / f"QCI_PATH_{tasks[0].cell}"
    assert (output_dir / "si.env").is_file()


def test_si_env_not_published_when_stage_fails(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 5.1: si failure must not leave a stale si.env in output_dir.

    Publishing post-si only on success avoids masking bugs on retry —
    Quantus reads output_dir/si.env, so a leftover from a prior failed
    run would make the next Quantus look like it passed for the wrong
    reason.
    """
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "si")
    project, tasks = _load(project_tools_config)
    summary = run_tasks(
        project,
        tasks,
        stages=["si"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
    )

    assert summary.failed == 1
    output_dir = workarea / "cds" / "verify" / f"QCI_PATH_{tasks[0].cell}"
    assert not (output_dir / "si.env").exists()


def test_calibre_fail_aborts_without_continue(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "calibre")
    project, tasks = _load(project_tools_config)

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
    )

    assert summary.failed == 1
    stage_status = {s.stage: s.status for s in summary.tasks[0].stages}
    assert stage_status["si"] == "passed"
    assert stage_status["strmout"] == "passed"
    assert stage_status["calibre"] == "failed"
    # Abort => downstream stages skipped, not run.
    assert stage_status["quantus"] == "skipped"
    assert stage_status["jivaro"] == "skipped"


def test_calibre_fail_with_continue_runs_downstream(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "calibre")
    project, tasks = _load(project_tools_config)
    tasks = [t.model_copy(update={"continue_on_lvs_fail": True}) for t in tasks]

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
    )

    stage_status = {s.stage: s.status for s in summary.tasks[0].stages}
    assert stage_status["calibre"] == "failed"
    # continue_on_lvs_fail: downstream stages run regardless.
    assert stage_status["quantus"] == "passed"
    assert stage_status["jivaro"] == "passed"
    # Task overall is still failed (any stage failure = failed task).
    assert summary.failed == 1


def test_dry_run_renders_but_skips_subprocesses(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    project, tasks = _load(project_tools_config)
    summary = run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
    )

    assert summary.total == 1
    stage_status = {s.stage: s.status for s in summary.tasks[0].stages}
    assert stage_status == {"si": "dry_run", "calibre": "dry_run"}
    # Renders still happened so templates are exercised without needing bash.
    rendered_dir = tmp_path / "project_root" / "runs" / f"task_{tasks[0].task_id}" / "rendered"
    assert (rendered_dir / "default.env").is_file()
    assert (rendered_dir / "wiodio_noConnectByNetName.qci").is_file()


def test_build_context_surfaces_pdk_fields(project_config) -> None:
    """_build_context exposes tech_name / pdk_subdir / project_subdir /
    lvs_runset_version / qrc_runset_version from ProjectConfig so
    templates can reference [[tech_name]] etc. (Phase 4b2).
    """
    from auto_ext.core.config import (
        JivaroConfig,
        RunsetVersions,
        TaskConfig,
        TemplatePaths,
    )
    from auto_ext.core.runner import _build_context

    project_config.tech_name = "HN001"
    project_config.pdk_subdir = "CFXXX"
    project_config.project_subdir = "projB"
    project_config.runset_versions = RunsetVersions(
        lvs="Ver_Plus_1.0l_0.9", qrc="Ver_Plus_1.0a"
    )
    task = TaskConfig(
        task_id="L__c__layout__schematic",
        library="L",
        cell="c",
        lvs_source_view="schematic",
        lvs_layout_view="layout",
        templates=TemplatePaths(),
        ground_net="vss",
        out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )

    ctx = _build_context(
        project_config,
        task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w", "PDK_LAYER_MAP_FILE": "/w/layers.map"},
    )
    assert ctx["tech_name"] == "HN001"
    assert ctx["pdk_subdir"] == "CFXXX"
    assert ctx["project_subdir"] == "projB"
    assert ctx["lvs_runset_version"] == "Ver_Plus_1.0l_0.9"
    assert ctx["qrc_runset_version"] == "Ver_Plus_1.0a"


def test_build_context_pdk_fields_default_to_none(project_config) -> None:
    """When the project does not set PDK fields AND autoderive candidate env
    vars are absent, they surface as None in the render context. A template
    referencing [[tech_name]] with strict_undef would then raise — expected
    behaviour for projects that have not yet run init-project and have no
    PDK env set.
    """
    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths
    from auto_ext.core.runner import _build_context

    task = TaskConfig(
        task_id="L__c__layout__schematic",
        library="L",
        cell="c",
        lvs_source_view="schematic",
        lvs_layout_view="layout",
        templates=TemplatePaths(),
        ground_net="vss",
        out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )
    ctx = _build_context(
        project_config,
        task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
    )
    assert ctx["tech_name"] is None
    assert ctx["pdk_subdir"] is None
    assert ctx["lvs_runset_version"] is None
    assert ctx["qrc_runset_version"] is None


def test_build_context_tech_name_autoderived_from_pdk_tech_file(project_config) -> None:
    """When tech_name is unset, runner derives it from PDK_TECH_FILE's
    parent dir (first candidate in tech_name_env_vars).
    """
    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths
    from auto_ext.core.runner import _build_context

    task = TaskConfig(
        task_id="L__c__layout__schematic",
        library="L",
        cell="c",
        lvs_source_view="schematic",
        lvs_layout_view="layout",
        templates=TemplatePaths(),
        ground_net="vss",
        out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )
    ctx = _build_context(
        project_config,
        task,
        resolved_env={
            "WORK_ROOT": "/w",
            "WORK_ROOT2": "/w",
            "PDK_TECH_FILE": "/pdk/HN042/techfile.tf",
        },
    )
    assert ctx["tech_name"] == "HN042"


def test_build_context_tech_name_autoderive_falls_through_to_layer_map(project_config) -> None:
    """When PDK_TECH_FILE is not set but PDK_LAYER_MAP_FILE is, derive from
    the second candidate. Confirms candidate-list walk.
    """
    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths
    from auto_ext.core.runner import _build_context

    task = TaskConfig(
        task_id="L__c__layout__schematic",
        library="L",
        cell="c",
        lvs_source_view="schematic",
        lvs_layout_view="layout",
        templates=TemplatePaths(),
        ground_net="vss",
        out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )
    ctx = _build_context(
        project_config,
        task,
        resolved_env={
            "WORK_ROOT": "/w",
            "WORK_ROOT2": "/w",
            "PDK_LAYER_MAP_FILE": "/pdk/HN001/layers.map",
        },
    )
    assert ctx["tech_name"] == "HN001"


def test_build_context_tech_name_explicit_overrides_autoderive(project_config) -> None:
    """Explicit project.tech_name always wins over env-var derivation."""
    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths
    from auto_ext.core.runner import _build_context

    project_config.tech_name = "HN999"
    task = TaskConfig(
        task_id="L__c__layout__schematic",
        library="L",
        cell="c",
        lvs_source_view="schematic",
        lvs_layout_view="layout",
        templates=TemplatePaths(),
        ground_net="vss",
        out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )
    ctx = _build_context(
        project_config,
        task,
        resolved_env={
            "WORK_ROOT": "/w",
            "WORK_ROOT2": "/w",
            "PDK_TECH_FILE": "/pdk/HN042/techfile.tf",
        },
    )
    assert ctx["tech_name"] == "HN999"


def test_discover_env_vars_adds_tech_name_candidates_when_unset(
    project_tools_config: Path,
) -> None:
    """When tech_name is None, _discover_env_vars unions tech_name_env_vars
    so they get a row in check-env output and are available for autoderive.
    Uses a custom candidate list that does not overlap with the default
    ``layer_map`` refs so the assertion is purely about the autoderive path.
    """
    from auto_ext.core.runner import _discover_env_vars

    (project_tools_config / "project.yaml").write_text(
        "templates: {}\n"
        "tech_name_env_vars: [MY_PDK_TECH, MY_PDK_LAYERS]\n",
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)
    required = _discover_env_vars(project, tasks)
    assert "MY_PDK_TECH" in required
    assert "MY_PDK_LAYERS" in required


def test_discover_env_vars_omits_tech_name_candidates_when_set(
    project_tools_config: Path,
) -> None:
    """When tech_name is explicit, candidate env vars are not added to the
    discovered set. Uses a custom candidate list to avoid overlap with
    ``layer_map``'s default env refs."""
    from auto_ext.core.runner import _discover_env_vars

    (project_tools_config / "project.yaml").write_text(
        "tech_name: HN001\n"
        "templates: {}\n"
        "tech_name_env_vars: [MY_PDK_TECH, MY_PDK_LAYERS]\n",
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)
    required = _discover_env_vars(project, tasks)
    assert "MY_PDK_TECH" not in required
    assert "MY_PDK_LAYERS" not in required


def test_jivaro_without_out_file_rejected(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    # Rewrite tasks.yaml to enable jivaro WITHOUT setting out_file.
    (project_tools_config / "tasks.yaml").write_text(
        """\
- library: LIB
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  jivaro:
    enabled: true
""",
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)

    from auto_ext.core.errors import ConfigError

    with pytest.raises(ConfigError, match="out_file is not set"):
        run_tasks(
            project,
            tasks,
            stages=["si", "jivaro"],
            auto_ext_root=tmp_path / "project_root",
            workarea=workarea,
            dry_run=True,
        )
