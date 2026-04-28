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
    assert (rendered_dir / "calibre_lvs.qci").is_file()
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
    assert (rendered_dir / "calibre_lvs.qci").is_file()


def test_calibre_lvs_default_knobs_render(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """Default knobs: lvs_variant=wodio, connect_by_name=false.

    The rendered .qci must contain the wodio rules-file path and must NOT
    contain the *cmnVConnectNamesState line.
    """
    project, tasks = _load(project_tools_config)
    run_tasks(
        project,
        tasks,
        stages=["calibre"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
    )
    rendered = (
        tmp_path / "project_root" / "runs" / f"task_{tasks[0].task_id}"
        / "rendered" / "calibre_lvs.qci"
    ).read_text(encoding="utf-8")
    assert ".wodio.qcilvs" in rendered
    assert ".widio.qcilvs" not in rendered
    assert "*cmnVConnectNamesState" not in rendered


def test_calibre_lvs_knob_overrides_flip_render(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """CLI overrides flip both knobs; both effects visible in render."""
    project, tasks = _load(project_tools_config)
    run_tasks(
        project,
        tasks,
        stages=["calibre"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
        cli_knobs={"calibre": {"lvs_variant": "widio", "connect_by_name": "true"}},
    )
    rendered = (
        tmp_path / "project_root" / "runs" / f"task_{tasks[0].task_id}"
        / "rendered" / "calibre_lvs.qci"
    ).read_text(encoding="utf-8")
    assert ".widio.qcilvs" in rendered
    assert ".wodio.qcilvs" not in rendered
    assert "*cmnVConnectNamesState: ALL" in rendered


def test_build_context_surfaces_paths(project_config) -> None:
    """_build_context resolves project.paths entries via resolve_path_expr
    and exposes each under the same key in the Jinja context. (Phase 5.6.5).
    """
    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths
    from auto_ext.core.runner import _build_context

    project_config.tech_name = "HN001"
    project_config.paths = {
        "calibre_lvs_dir": "$calibre_source_added_place|parent",
        "qrc_deck_dir": "$VERIFY_ROOT/runset/Calibre_QRC/QRC/v/CFXXX/QCI_deck",
    }
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
            "PDK_LAYER_MAP_FILE": "/w/layers.map",
            "VERIFY_ROOT": "/v",
            "calibre_source_added_place": (
                "/v/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX/empty.cdl"
            ),
        },
    )
    assert ctx["tech_name"] == "HN001"
    assert (
        ctx["calibre_lvs_dir"]
        == "/v/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX"
    )
    # calibre_lvs_basename auto-derived from the path's leaf.
    assert ctx["calibre_lvs_basename"] == "CFXXX"
    assert (
        ctx["qrc_deck_dir"]
        == "/v/runset/Calibre_QRC/QRC/v/CFXXX/QCI_deck"
    )


def test_build_context_calibre_lvs_basename_user_override(project_config) -> None:
    """If a project explicitly sets paths.calibre_lvs_basename it must win
    over the auto-derived leaf — needed when the PDK breaks the
    "rules-file basename = LVS dir leaf" convention."""
    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths
    from auto_ext.core.runner import _build_context

    project_config.paths = {
        "calibre_lvs_dir": "/v/x/CFXXX",
        "calibre_lvs_basename": "alt_basename",
    }
    task = TaskConfig(
        task_id="L__c__layout__schematic",
        library="L", cell="c",
        lvs_source_view="schematic", lvs_layout_view="layout",
        templates=TemplatePaths(),
        ground_net="vss", out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0, expansion_index=0,
    )
    ctx = _build_context(
        project_config, task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
    )
    assert ctx["calibre_lvs_basename"] == "alt_basename"


def test_build_context_pdk_fields_default_to_none(project_config) -> None:
    """When the project does not set tech_name AND its candidate env vars
    are absent, tech_name stays None. paths is empty by default → no
    extra keys land in the context.
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
    assert "calibre_lvs_dir" not in ctx
    assert "qrc_deck_dir" not in ctx
    assert "calibre_lvs_basename" not in ctx


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


# ---- dspf_out_path resolution in _build_context ---------------------------


def _make_dspf_task(**overrides):
    """Helper: build a TaskConfig for dspf_out_path resolution tests."""
    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths

    library = overrides.pop("library", "L")
    cell = overrides.pop("cell", "c")
    layout = overrides.pop("lvs_layout_view", "layout")
    src = overrides.pop("lvs_source_view", "schematic")
    base = dict(
        task_id=f"{library}__{cell}__{layout}__{src}",
        library=library,
        cell=cell,
        lvs_source_view=src,
        lvs_layout_view=layout,
        templates=TemplatePaths(),
        ground_net="vss",
        out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )
    base.update(overrides)
    return TaskConfig(**base)


def test_build_context_dspf_out_path_default(project_config) -> None:
    """Default ``${WORK_ROOT2}/{cell}.dspf`` resolves cleanly."""
    from auto_ext.core.runner import _build_context

    task = _make_dspf_task(cell="myCell")
    ctx = _build_context(
        project_config,
        task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/wkr2"},
    )
    assert ctx["dspf_out_path"] == "/wkr2/myCell.dspf"


def test_build_context_dspf_out_path_references_output_dir(project_config) -> None:
    """``${output_dir}`` resolves to the runner-computed output_dir."""
    from auto_ext.core.runner import _build_context

    project_config.dspf_out_path = "${output_dir}/{cell}.dspf"
    task = _make_dspf_task(cell="inv")
    ctx = _build_context(
        project_config,
        task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
    )
    # extraction_output_dir default = ${WORK_ROOT}/cds/verify/QCI_PATH_{cell}.
    assert ctx["dspf_out_path"] == "/w/cds/verify/QCI_PATH_inv/inv.dspf"


def test_build_context_dspf_out_path_references_intermediate_dir(
    project_config,
) -> None:
    """``${intermediate_dir}`` resolves to the project's intermediate_dir."""
    from auto_ext.core.runner import _build_context

    project_config.intermediate_dir = "${WORK_ROOT2}/inter"
    project_config.dspf_out_path = "${intermediate_dir}/{cell}.dspf"
    task = _make_dspf_task(cell="cellX")
    ctx = _build_context(
        project_config,
        task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w2"},
    )
    assert ctx["dspf_out_path"] == "/w2/inter/cellX.dspf"


def test_build_context_dspf_out_path_references_paths_key(project_config) -> None:
    """``${calibre_lvs_dir}`` resolves through project.paths."""
    from auto_ext.core.runner import _build_context

    project_config.paths = {"calibre_lvs_dir": "/v/runset/CFXXX"}
    project_config.dspf_out_path = "${calibre_lvs_dir}/exports/{cell}.dspf"
    task = _make_dspf_task(cell="inv")
    ctx = _build_context(
        project_config, task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
    )
    assert ctx["dspf_out_path"] == "/v/runset/CFXXX/exports/inv.dspf"


def test_build_context_dspf_out_path_format_keys(project_config) -> None:
    """{cell} {library} {task_id} all substitute correctly."""
    from auto_ext.core.runner import _build_context

    project_config.dspf_out_path = (
        "${WORK_ROOT2}/{library}/{task_id}/{cell}.dspf"
    )
    task = _make_dspf_task(library="L1", cell="cellY")
    ctx = _build_context(
        project_config, task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
    )
    assert ctx["dspf_out_path"] == (
        "/w/L1/L1__cellY__layout__schematic/cellY.dspf"
    )


def test_build_context_dspf_out_path_per_task_override_wins(project_config) -> None:
    """task.dspf_out_path beats project.dspf_out_path when set."""
    from auto_ext.core.runner import _build_context

    project_config.dspf_out_path = "${WORK_ROOT2}/{cell}.dspf"
    task = _make_dspf_task(
        cell="cell_z",
        dspf_out_path="/custom/path/{cell}.dspf",
    )
    ctx = _build_context(
        project_config, task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
    )
    assert ctx["dspf_out_path"] == "/custom/path/cell_z.dspf"


def test_build_context_dspf_out_path_unknown_env_passthrough(project_config) -> None:
    """Unknown env vars pass through unchanged (matches substitute_env semantics)."""
    from auto_ext.core.runner import _build_context

    project_config.dspf_out_path = "${WORK_ROOT2}/${UNDEFINED_X}/{cell}.dspf"
    task = _make_dspf_task(cell="c")
    ctx = _build_context(
        project_config, task,
        resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/wkr2"},
    )
    # ${UNDEFINED_X} is not in resolved_env so it passes through verbatim.
    assert ctx["dspf_out_path"] == "/wkr2/${UNDEFINED_X}/c.dspf"


def test_resolve_dspf_out_path_raises_on_unknown_format_key(project_config) -> None:
    """A ``{foo}`` literal with no ``$`` prefix is a real
    misconfiguration: the runner must still raise ConfigError so
    runtime is fail-fast, not silently emit a half-rendered path.
    """
    from auto_ext.core.runner import _build_context
    from auto_ext.core.errors import ConfigError

    project_config.dspf_out_path = "/abs/{cell}/{foo}.dspf"
    task = _make_dspf_task(cell="c")
    with pytest.raises(ConfigError, match="unknown format key 'foo'"):
        _build_context(
            project_config, task,
            resolved_env={"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
        )


def test_resolve_dspf_path_helper_returns_tuple_for_gui() -> None:
    """The shared :func:`resolve_dspf_path` helper exposes a
    ``(text, error_or_None)`` tuple that both the runner wrapper and
    the GUI wrapper consume. Smoke-test the three error classes.
    """
    from auto_ext.core.runner import resolve_dspf_path

    # Happy path.
    t, e = resolve_dspf_path(
        "${WK}/{cell}.dspf", {"WK": "/w"}, cell="c", library="L", task_id="T"
    )
    assert (t, e) == ("/w/c.dspf", None)
    # Unresolved env (brace form).
    t, e = resolve_dspf_path(
        "${WK}/{cell}.dspf", {}, cell="c", library="L", task_id="T"
    )
    assert t == "${WK}/c.dspf"
    assert e and e.startswith("unresolved:")
    # Bare ``$X`` form too.
    t, e = resolve_dspf_path(
        "$WK/{cell}.dspf", {}, cell="c", library="L", task_id="T"
    )
    assert t == "$WK/c.dspf"
    assert e and "unresolved" in e and "$WK" in e
    # Truly unknown format key.
    _, e = resolve_dspf_path(
        "/abs/{foo}.dspf", {}, cell="c", library="L", task_id="T"
    )
    assert e and "unknown format key" in e and "foo" in e


def test_discover_env_vars_includes_dspf_out_path(project_tools_config: Path) -> None:
    """Custom env refs in dspf_out_path (project + per-task) surface in
    the discovered set so check-env catches missing ones up-front."""
    from auto_ext.core.runner import _discover_env_vars

    (project_tools_config / "project.yaml").write_text(
        "dspf_out_path: \"${MY_DSPF_ROOT}/{cell}.dspf\"\n",
        encoding="utf-8",
    )
    (project_tools_config / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: c\n"
        "  lvs_layout_view: layout\n"
        "  dspf_out_path: \"${PER_TASK_DSPF}/{cell}.dspf\"\n",
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)
    required = _discover_env_vars(project, tasks)
    assert "MY_DSPF_ROOT" in required
    assert "PER_TASK_DSPF" in required


def test_discover_env_vars_strips_synthetic_path_tokens(
    project_tools_config: Path,
) -> None:
    """``${output_dir}`` and friends are runner-injected synthetic tokens —
    they must NOT appear in the env-var requirement set, otherwise
    resolve_env logs a "missing" warning and confuses the user even
    though the runner supplies them at render time. This regressed when
    ``dspf_out_path`` was added to ``_discover_env_vars`` sources without
    a path-token filter."""
    from auto_ext.core.runner import _discover_env_vars

    (project_tools_config / "project.yaml").write_text(
        "dspf_out_path: \"${output_dir}/{cell}.dspf\"\n"
        "paths:\n"
        "  custom_path: \"${VERIFY_ROOT}/foo\"\n",
        encoding="utf-8",
    )
    (project_tools_config / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: c\n"
        "  lvs_layout_view: layout\n"
        "  dspf_out_path: \"${calibre_lvs_dir}/per_task.dspf\"\n",
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)
    required = _discover_env_vars(project, tasks)
    # Runner-injected path tokens must not surface as required env vars.
    for token in (
        "output_dir",
        "intermediate_dir",
        "calibre_lvs_dir",
        "calibre_lvs_basename",
        "qrc_deck_dir",
        "layer_map",
    ):
        assert token not in required, f"{token} should be filtered"
    # project.paths.* keys are also synthetic.
    assert "custom_path" not in required
    # But real shell vars referenced inside paths.* values still surface.
    assert "VERIFY_ROOT" in required


# ---- Phase 5.9 B+C: rendered_path_for ------------------------------------


def _phase59_bc_load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


@pytest.mark.parametrize(
    "stage,expected_stem",
    [
        ("si", "default.env"),
        ("calibre", "calibre_lvs.qci"),
        ("quantus", "ext.cmd"),
        ("jivaro", "default.xml"),
    ],
)
def test_phase59_bc_rendered_path_for_each_templated_stage(
    project_tools_config: Path, tmp_path: Path, stage: str, expected_stem: str
) -> None:
    """rendered_path_for returns the same per-stage location the runner
    writes to: ``<auto_ext_root>/runs/task_<safe_id>/rendered/<template_stem>``.
    """
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    ae_root = tmp_path / "ae_root"
    path = rendered_path_for(ae_root, tasks[0], stage, project)
    assert path is not None
    assert path == (
        ae_root / "runs" / f"task_{tasks[0].task_id}" / "rendered" / expected_stem
    )


def test_phase59_bc_rendered_path_for_strmout_returns_none(
    project_tools_config: Path, tmp_path: Path
) -> None:
    """strmout has has_template=False — runner does not render anything,
    so the GUI must disable "Open rendered template" for that row.
    """
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    assert rendered_path_for(tmp_path / "ae_root", tasks[0], "strmout", project) is None


def test_phase59_bc_rendered_path_for_unknown_stage_returns_none(
    project_tools_config: Path, tmp_path: Path
) -> None:
    """A stage name outside STAGE_ORDER (defensive — the GUI shouldn't
    feed one in, but worth a guard) returns None rather than crashing.
    """
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    assert rendered_path_for(tmp_path / "ae_root", tasks[0], "bogus", project) is None


def test_phase59_bc_rendered_path_for_per_task_override_beats_project_default(
    project_tools_config: Path, tmp_path: Path, templates_root: Path
) -> None:
    """If a task overrides ``templates.calibre`` to a non-default path,
    rendered_path_for must follow the override (the runner does too)."""
    from auto_ext.core.runner import rendered_path_for

    # Drop a per-task override pointing at a same-named template at a
    # *different* location — easier to use a real template stem so the
    # stem comparison is meaningful. Use the production calibre template
    # but make sure the override is honored even if pointed at a copy.
    override_dir = tmp_path / "custom_templates"
    override_dir.mkdir()
    custom = override_dir / "my_custom_calibre.qci.j2"
    src = templates_root / "calibre" / "calibre_lvs.qci.j2"
    custom.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    (project_tools_config / "tasks.yaml").write_text(
        f"""\
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  templates:
    calibre: {custom.as_posix()}
""",
        encoding="utf-8",
    )

    project, tasks = _phase59_bc_load(project_tools_config)
    path = rendered_path_for(tmp_path / "ae_root", tasks[0], "calibre", project)
    assert path is not None
    # Stem follows the override's filename, not the project default.
    assert path.name == "my_custom_calibre.qci"


def test_phase59_bc_rendered_path_for_matches_runner_actual_writes(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """Cross-validate: after a real (mocked) run, rendered_path_for must
    point at a file that exists. Catches regressions where the runner's
    inline path math drifts from the helper.
    """
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project,
        tasks,
        stages=["si", "calibre", "quantus", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
    )
    for stage in ("si", "calibre", "quantus", "jivaro"):
        path = rendered_path_for(ae_root, tasks[0], stage, project)
        assert path is not None and path.is_file(), f"{stage}: {path}"
