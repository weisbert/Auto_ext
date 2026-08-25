"""End-to-end integration tests for :mod:`auto_ext.core.runner`.

Uses the production templates under ``Auto_ext/templates/`` and the mock
EDA binaries under ``tests/mocks/`` (bash required — skipped on Windows
if git-bash is not installed, via the ``mocks_on_path`` fixture).

Every task now lands in its own run directory
``<auto_ext_root>/runs/<UTC-stamp>_<cell>-<recipe>/`` holding ``rendered/``,
``logs/``, ``results/`` and ``run.json``. Because the directory name carries
a timestamp, tests locate it with :func:`_only_run_dir` / :func:`_run_dirs`
rather than by spelling it out.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from auto_ext.core.config import TaskConfig, load_project, load_tasks
from auto_ext.core.env import substitute_env
from auto_ext.core.errors import AutoExtError
from auto_ext.core.run_store import read_events, read_record
from auto_ext.core.runner import StageStatus, run_tasks

if TYPE_CHECKING:
    from auto_ext.model.pdk import PdkProfile


def _load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


def _write_tasks(config_dir: Path, rows: list[dict[str, str]]) -> Path:
    """Replace ``tasks.yaml`` with one entry per row.

    Every row gets the four identity axes; ``rows`` overrides any of them and
    may add ``out_file``. Written as a helper because the reduced ``TaskSpec``
    refuses the ``jivaro:`` block these tables used to carry -- reduction is a
    Recipe setting now -- and hand-editing that out of eight inline YAML
    literals is how one of them ends up subtly different from the others.
    """

    defaults = {
        "library": "EXAMPLE_LIB",
        "cell": "inv",
        "lvs_layout_view": "layout",
        "lvs_source_view": "schematic",
    }
    body = []
    for row in rows:
        entry = {**defaults, **row}
        body.append(
            "- " + "\n  ".join(f"{key}: {value}" for key, value in entry.items())
        )
    path = config_dir / "tasks.yaml"
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def _run_dirs(auto_ext_root: Path) -> list[Path]:
    """Every run directory under ``auto_ext_root``, oldest name first.

    ``batches/`` is part of the layout, not a run, so it is filtered out —
    the same rule :func:`auto_ext.core.run_store.list_runs` applies.
    """

    root = auto_ext_root / "runs"
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir() if p.is_dir() and p.name not in ("batches", "latest")
    )


def _only_run_dir(auto_ext_root: Path) -> Path:
    """The single run directory a one-task dispatch produced."""

    dirs = _run_dirs(auto_ext_root)
    assert len(dirs) == 1, f"expected exactly one run dir, found {[d.name for d in dirs]}"
    return dirs[0]


# ---- GDS export: a second file, and no way to move the first one ---------


def test_export_refuses_any_stage_set_but_strmout_alone(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The guard that makes breaking LVS impossible rather than discouraged.

    ``-strmFile`` and the runset's ``*lvsLayoutPaths`` are the same catalog
    value. A dispatch that redirected strmout *and* ran calibre would point
    LVS at a file nobody wrote -- and it would not fail loudly, it would fail
    as a confusing LVS error. So the combination is refused up front, before
    any subprocess, rather than documented as a thing not to do.
    """
    project, tasks = _load(project_tools_config)
    from auto_ext.core.errors import ConfigError

    for stages in (
        ["si", "strmout", "calibre", "quantus", "jivaro"],
        ["strmout", "calibre"],
        ["si", "strmout"],
        ["calibre"],
    ):
        with pytest.raises(ConfigError, match="strmout stage and nothing else"):
            run_tasks(
                project,
                tasks,
                stages=stages,
                auto_ext_root=tmp_path / "project_root",
                workarea=workarea,
                recipe=_recipe(),
                profile=_profile(workarea),
                dry_run=True,
                layout_export_path="/tmp/out.gds",
            )


def test_export_alone_is_accepted_and_lands_where_asked(
    project_tools_config: Path, workarea: Path, mocks_on_path: Path, tmp_path: Path
) -> None:
    project, tasks = _load(project_tools_config)
    dest = tmp_path / "reliability" / "{cell}.gds"

    summary = run_tasks(
        project,
        tasks,
        stages=["strmout"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        layout_export_path=str(dest),
    )

    assert summary.failed == 0
    strmout = summary.tasks[0].stages[0]
    argv = strmout.record.argv
    written = argv[argv.index("-strmFile") + 1].replace("\\", "/")
    # {cell} was substituted, and the destination is the one asked for --
    # NOT the workspace path the LVS file uses.
    assert written.endswith("/reliability/inv.gds"), written
    assert "QCI_PATH" not in written


def test_an_ordinary_run_is_untouched_by_the_export_feature(
    project_tools_config: Path, workarea: Path, mocks_on_path: Path, tmp_path: Path
) -> None:
    """No export requested -> the LVS layout destination is exactly as before."""

    project, tasks = _load(project_tools_config)
    summary = run_tasks(
        project,
        tasks,
        stages=["strmout"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )
    argv = summary.tasks[0].stages[0].record.argv
    written = argv[argv.index("-strmFile") + 1].replace("\\", "/")
    assert written.endswith("/inv.calibre.db"), written


def test_multi_cell_export_without_a_cell_placeholder_is_refused(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Two cells, one fixed filename: the second would silently eat the first.

    Silently, because strmout succeeds every time -- the loss shows up later
    as a GDS whose contents belong to a different cell. Refuse instead.
    """
    _write_tasks(
        project_tools_config,
        [
            {"library": "LIB", "cell": "inv", "out_file": "av_ext"},
            {"library": "LIB", "cell": "nand", "out_file": "av_ext"},
        ],
    )
    project, tasks = _load(project_tools_config)
    from auto_ext.core.errors import ConfigError

    with pytest.raises(ConfigError, match=r"overwrite the last"):
        run_tasks(
            project,
            tasks,
            stages=["strmout"],
            auto_ext_root=tmp_path / "project_root",
            workarea=workarea,
            recipe=_recipe(),
            profile=_profile(workarea),
            dry_run=True,
            layout_export_path="/tmp/fixed.gds",
        )

    # ...and it is accepted the moment the path can tell the cells apart.
    run_tasks(
        project,
        tasks,
        stages=["strmout"],
        auto_ext_root=tmp_path / "project_root2",
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
        layout_export_path="/tmp/{cell}.gds",
    )


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
        recipe=_recipe(),
        profile=_profile(workarea),
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
    # Rendered inputs and logs both live inside this run's own directory.
    run_dir = _only_run_dir(tmp_path / "project_root")
    rendered_dir = run_dir / "rendered"
    assert (rendered_dir / "si.env").is_file()
    assert (rendered_dir / "lvs.qci").is_file()
    assert (rendered_dir / "ext.cmd").is_file()
    assert (rendered_dir / "jivaro.xml").is_file()
    # The si control file is also archived under the name si actually reads.
    assert (rendered_dir / "si.env").is_file()
    for stage in ("si", "strmout", "calibre", "quantus", "jivaro"):
        assert (run_dir / "logs" / f"{stage}.log").is_file()


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
        recipe=_recipe(),
        profile=_profile(workarea),
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
        recipe=_recipe(),
        profile=_profile(workarea),
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
        recipe=_recipe(),
        profile=_profile(workarea),
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

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        # The switch used to be ``TaskConfig.continue_on_lvs_fail``. It is a
        # Recipe policy now: "keep going past a mismatch" is a property of how
        # you are extracting, not of which cell you point at.
        recipe=_recipe(policy={"continue_on_lvs_fail": True}),
        profile=_profile(workarea),
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
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
    )

    assert summary.total == 1
    stage_status = {s.stage: s.status for s in summary.tasks[0].stages}
    assert stage_status == {"si": "dry_run", "calibre": "dry_run"}
    # Renders still happened so templates are exercised without needing bash.
    rendered_dir = _only_run_dir(tmp_path / "project_root") / "rendered"
    assert (rendered_dir / "si.env").is_file()
    assert (rendered_dir / "lvs.qci").is_file()


def test_jivaro_without_out_file_rejected(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """Reduction on, but the DUT has no extracted view for it to reduce.

    Jivaro's ``inputView`` renders to ``library/cell/out_file``; with
    ``out_file`` unset there is no view name, and the run has to say so before
    a stage starts rather than hand Jivaro a path with a hole in it. The switch
    that reaches this check moved from ``task.jivaro.enabled`` to
    ``recipe.reduction.enabled``, which is why the error now names the recipe.
    """

    _write_tasks(project_tools_config, [{"library": "LIB"}])  # no out_file
    project, tasks = _load(project_tools_config)

    from auto_ext.core.errors import ConfigError

    with pytest.raises(ConfigError, match="out_file is not set"):
        run_tasks(
            project,
            tasks,
            stages=["si", "jivaro"],
            auto_ext_root=tmp_path / "project_root",
            workarea=workarea,
            recipe=_recipe(reduction={"enabled": True}),
            profile=_profile(workarea),
            dry_run=True,
        )


# ---- dspf_out_path resolution ----------------------------------------------
#
# The legacy ``_build_context`` these were written against is gone: the render
# context is ``core.render.build_context`` now, and it is handed an already
# resolved ``dspf_out_path`` on :class:`~auto_ext.core.render.RunFacts`. The
# function that does the resolving -- two phases, env references then
# ``str.format`` keys -- survived unchanged as ``_resolve_dspf_out_path``, so
# these tests moved down one level rather than being dropped: the same eight
# behaviours, asserted on the function that still owns them.


def _make_dspf_task(**overrides):
    """Helper: build a TaskConfig for dspf_out_path resolution tests."""
    from auto_ext.core.config import JivaroConfig

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
        ground_net="vss",
        out_file=None,
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )
    base.update(overrides)
    return TaskConfig(**base)


def _dspf(project, task, resolved_env, **ctx_so_far):
    """Resolve ``project.dspf_out_path`` the way the runner does.

    ``ctx_so_far`` is the runner's partially-built path context: the values
    already resolved by the time the DSPF pattern is expanded, which is what
    lets the pattern reference ``${output_dir}`` and friends.
    """
    from auto_ext.core.runner import _resolve_dspf_out_path, _resolve_output_dir

    context = {
        "output_dir": _resolve_output_dir(project, task, resolved_env),
        "intermediate_dir": substitute_env(project.intermediate_dir, resolved_env),
    }
    context.update(ctx_so_far)
    return _resolve_dspf_out_path(project, task, resolved_env, context)


def test_dspf_out_path_default(project_config) -> None:
    """Default ``${WORK_ROOT2}/{cell}.dspf`` resolves cleanly."""

    task = _make_dspf_task(cell="myCell")
    out = _dspf(
        project_config, task, {"WORK_ROOT": "/w", "WORK_ROOT2": "/wkr2"}
    )
    assert out == "/wkr2/myCell.dspf"


def test_dspf_out_path_references_output_dir(project_config) -> None:
    """``${output_dir}`` resolves to the runner-computed output_dir."""

    project_config.dspf_out_path = "${output_dir}/{cell}.dspf"
    task = _make_dspf_task(cell="inv")
    out = _dspf(project_config, task, {"WORK_ROOT": "/w", "WORK_ROOT2": "/w"})
    # extraction_output_dir default = ${WORK_ROOT}/cds/verify/QCI_PATH_{cell}.
    assert out == "/w/cds/verify/QCI_PATH_inv/inv.dspf"


def test_dspf_out_path_references_intermediate_dir(project_config) -> None:
    """``${intermediate_dir}`` resolves to the project's intermediate_dir."""

    project_config.intermediate_dir = "${WORK_ROOT2}/inter"
    project_config.dspf_out_path = "${intermediate_dir}/{cell}.dspf"
    task = _make_dspf_task(cell="cellX")
    out = _dspf(project_config, task, {"WORK_ROOT": "/w", "WORK_ROOT2": "/w2"})
    assert out == "/w2/inter/cellX.dspf"


def test_dspf_out_path_references_a_profile_path_key(project_config) -> None:
    """``${calibre_lvs_dir}`` resolves through the profile's path keys.

    Its old home was ``project.paths``, which is retired; the key reaches the
    resolver the same way either way -- as an already-resolved entry in the
    path context the runner has built so far.
    """

    project_config.dspf_out_path = "${calibre_lvs_dir}/exports/{cell}.dspf"
    task = _make_dspf_task(cell="inv")
    out = _dspf(
        project_config,
        task,
        {"WORK_ROOT": "/w", "WORK_ROOT2": "/w"},
        calibre_lvs_dir="/v/runset/CFXXX",
    )
    assert out == "/v/runset/CFXXX/exports/inv.dspf"


def test_dspf_out_path_format_keys(project_config) -> None:
    """{cell} {library} {task_id} all substitute correctly."""

    project_config.dspf_out_path = "${WORK_ROOT2}/{library}/{task_id}/{cell}.dspf"
    task = _make_dspf_task(library="L1", cell="cellY")
    out = _dspf(project_config, task, {"WORK_ROOT": "/w", "WORK_ROOT2": "/w"})
    assert out == "/w/L1/L1__cellY__layout__schematic/cellY.dspf"


def test_dspf_out_path_is_one_pattern_for_the_whole_project(project_config) -> None:
    """The per-task override is gone; ``{cell}`` is what varies a DSPF path.

    ``TaskSpec.dspf_out_path`` covered exactly one real use -- a per-DUT output
    file -- and ``{cell}`` covers it without a second override layer. A
    tasks.yaml that still carries the key is refused by name, so the pattern
    below is the only answer, and it has to give two DUTs two files.
    """

    project_config.dspf_out_path = "${WORK_ROOT2}/{cell}.dspf"
    env = {"WORK_ROOT": "/w", "WORK_ROOT2": "/w"}

    first = _dspf(project_config, _make_dspf_task(cell="cell_z"), env)
    second = _dspf(project_config, _make_dspf_task(cell="cell_q"), env)

    assert first == "/w/cell_z.dspf"
    assert second == "/w/cell_q.dspf"
    assert "dspf_out_path" not in TaskConfig.model_fields


# ---- the directory Quantus will not create ---------------------------------


def test_ensure_dspf_parent_creates_a_missing_directory(tmp_path: Path) -> None:
    from auto_ext.core.runner import _ensure_dspf_parent

    target = tmp_path / "dspf" / "inv.dspf"
    context = {"paths": {"dspf_out": str(target)}}

    assert _ensure_dspf_parent(context, dry_run=False) is None
    assert target.parent.is_dir()


def test_ensure_dspf_parent_reports_rather_than_creates_on_a_dry_run(
    tmp_path: Path,
) -> None:
    """``--dry-run`` says whether a real run would work.

    A check that makes itself pass tells you nothing, so the dry run reports
    the missing directory and leaves the filesystem alone.
    """

    from auto_ext.core.runner import _ensure_dspf_parent

    target = tmp_path / "dspf" / "inv.dspf"
    problem = _ensure_dspf_parent({"paths": {"dspf_out": str(target)}}, dry_run=True)

    assert problem is not None
    assert str(target.parent) in problem
    assert "dspf_out_pattern" in problem, "the message must name the setting"
    assert not target.parent.exists(), "a dry run created a directory"


def test_ensure_dspf_parent_is_quiet_when_there_is_nothing_to_do(
    tmp_path: Path,
) -> None:
    from auto_ext.core.runner import _ensure_dspf_parent

    existing = tmp_path / "already.dspf"
    assert _ensure_dspf_parent({"paths": {"dspf_out": str(existing)}}, dry_run=True) is None
    assert _ensure_dspf_parent({"paths": {"dspf_out": None}}, dry_run=False) is None
    assert _ensure_dspf_parent({}, dry_run=False) is None


def test_ensure_dspf_parent_refuses_a_parent_that_is_a_file(tmp_path: Path) -> None:
    from auto_ext.core.runner import _ensure_dspf_parent

    blocker = tmp_path / "notadir"
    blocker.write_text("", encoding="utf-8")
    problem = _ensure_dspf_parent(
        {"paths": {"dspf_out": str(blocker / "inv.dspf")}}, dry_run=False
    )

    assert problem is not None and "not a directory" in problem


def test_a_dspf_pattern_with_a_subdirectory_gets_its_directory_made(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """The real bug: nobody created the DSPF file's parent, Quantus included.

    extUser p.550 -- "If you specify a directory as part of the filename
    option, the directory must already exist." The flow worked only because
    the default pattern writes straight into ``WORK_ROOT2``. A pattern with a
    sub-directory in it, which the Project page invites, died inside Quantus
    at the last stage of a run that had already paid for si and Calibre.
    """

    project, tasks = _load(project_tools_config)
    out_dir = tmp_path / "dspf_out" / "nested"
    project.dspf_out_path = f"{out_dir.as_posix()}/{{cell}}.dspf"
    assert not out_dir.exists()

    summary = run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        recipe=_recipe(output={"emit": ["dspf"]}),
        profile=_profile(workarea),
    )

    assert summary.passed == 1, [s.error for t in summary.tasks for s in t.stages]
    assert out_dir.is_dir(), "the DSPF directory was still not created"


def test_a_dry_run_names_the_missing_dspf_directory_instead_of_making_it(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """Catch it before the hours are spent, without hiding it by fixing it."""

    project, tasks = _load(project_tools_config)
    out_dir = tmp_path / "dspf_out" / "nested"
    project.dspf_out_path = f"{out_dir.as_posix()}/{{cell}}.dspf"

    summary = run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        recipe=_recipe(output={"emit": ["dspf"]}),
        profile=_profile(workarea),
        dry_run=True,
    )

    # Emitting DSPF alone keeps the plain "quantus" key -- the same collapse
    # that made keying the guard off ``step.key`` wrong in the first place.
    dspf = [s for s in summary.tasks[0].stages if s.stage == "quantus"]
    assert len(dspf) == 1 and dspf[0].key == "quantus"
    assert dspf[0].status == StageStatus.FAILED
    assert str(out_dir) in (dspf[0].error or "")
    assert not out_dir.exists()


def test_dspf_out_path_unknown_env_passthrough(project_config) -> None:
    """Unknown env vars pass through unchanged (matches substitute_env semantics)."""

    project_config.dspf_out_path = "${WORK_ROOT2}/${UNDEFINED_X}/{cell}.dspf"
    task = _make_dspf_task(cell="c")
    out = _dspf(project_config, task, {"WORK_ROOT": "/w", "WORK_ROOT2": "/wkr2"})
    # ${UNDEFINED_X} is not in resolved_env so it passes through verbatim.
    assert out == "/wkr2/${UNDEFINED_X}/c.dspf"


def test_resolve_dspf_out_path_raises_on_unknown_format_key(project_config) -> None:
    """A ``{foo}`` literal with no ``$`` prefix is a real
    misconfiguration: the runner must still raise ConfigError so
    runtime is fail-fast, not silently emit a half-rendered path.
    """
    from auto_ext.core.errors import ConfigError

    project_config.dspf_out_path = "/abs/{cell}/{foo}.dspf"
    task = _make_dspf_task(cell="c")
    with pytest.raises(ConfigError, match="unknown format key 'foo'"):
        _dspf(project_config, task, {"WORK_ROOT": "/w", "WORK_ROOT2": "/w"})


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


def _phase59_bc_load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


@pytest.mark.parametrize(
    "stage,expected_stem",
    [
        ("si", "si.env"),
        ("calibre", "lvs.qci"),
        ("quantus", "ext.cmd"),
        ("jivaro", "jivaro.xml"),
    ],
)
def test_phase59_bc_rendered_path_for_reads_the_run_record(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
    stage: str,
    expected_stem: str,
) -> None:
    """rendered_path_for reports what the runner recorded, not recomputed math.

    It resolves ``StageRecord.rendered_path`` from the newest run of this
    DUT, so the returned path is by construction the file the runner wrote.
    """
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    ae_root = tmp_path / "ae_root"
    run_tasks(
        project,
        tasks,
        stages=["si", "calibre", "quantus", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
    )

    path = rendered_path_for(ae_root, tasks[0], stage, project)
    assert path is not None
    assert path == _only_run_dir(ae_root) / "rendered" / expected_stem
    assert path.is_file()


def test_phase59_bc_rendered_path_for_without_a_run_returns_none(
    project_tools_config: Path, tmp_path: Path
) -> None:
    """Nothing has run yet, so there is no recorded rendered file.

    The GUI disables "Open rendered template" on this, which is the honest
    answer: previously it returned a path that did not exist.
    """
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    assert rendered_path_for(tmp_path / "ae_root", tasks[0], "calibre", project) is None


def test_phase59_bc_rendered_path_for_uses_the_newest_run(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A rerun makes a second directory; the helper must follow the new one."""
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    ae_root = tmp_path / "ae_root"
    for _ in range(2):
        run_tasks(
            project,
            tasks,
            stages=["calibre"],
            auto_ext_root=ae_root,
            workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
            dry_run=True,
        )

    dirs = _run_dirs(ae_root)
    assert len(dirs) == 2, "a rerun must not overwrite the previous run"
    path = rendered_path_for(ae_root, tasks[0], "calibre", project)
    assert path is not None
    # Directory names sort chronologically, so the newest is last.
    assert path.parent.parent == dirs[-1]


def test_phase59_bc_rendered_path_for_accepts_an_explicit_record(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Passing a record skips the directory scan — the path is read from it."""
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    ae_root = tmp_path / "ae_root"
    summary = run_tasks(
        project,
        tasks,
        stages=["calibre"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
    )
    record = summary.runs[0]

    path = rendered_path_for(ae_root, tasks[0], "calibre", project, record=record)
    assert path == Path(record.run_dir) / "rendered" / "lvs.qci"


def test_phase59_bc_rendered_path_for_skipped_stage_returns_none(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Reduction is off in the recipe, so jivaro rendered nothing to open."""
    from auto_ext.core.runner import rendered_path_for

    project, tasks = _phase59_bc_load(project_tools_config)
    ae_root = tmp_path / "ae_root"
    run_tasks(
        project,
        tasks,
        stages=["calibre", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(reduction={"enabled": False}),
        profile=_profile(workarea),
        dry_run=True,
    )

    assert rendered_path_for(ae_root, tasks[0], "jivaro", project) is None
    assert rendered_path_for(ae_root, tasks[0], "calibre", project) is not None


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
        recipe=_recipe(),
        profile=_profile(workarea),
    )
    for stage in ("si", "calibre", "quantus", "jivaro"):
        path = rendered_path_for(ae_root, tasks[0], stage, project)
        assert path is not None and path.is_file(), f"{stage}: {path}"


# ---- S1: the run record ----------------------------------------------------
#
# Everything below is about what survives the process. Before the run layer a
# rerun opened ``logs/task_<id>/<stage>.log`` with "w" and the previous run
# ceased to exist; ``ToolResult.artifact_paths`` and the parsed LVS report
# were computed and then dropped on the floor.


def test_run_writes_a_record_with_identity_and_snapshots(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """``run.json`` is written, parses back, and names what it ran."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    run_dir = _only_run_dir(ae_root)
    assert (run_dir / "run.json").is_file()

    record = read_record(run_dir)
    assert record.run_id == run_dir.name
    assert record.overall == "passed"
    assert record.dut.library == "EXAMPLE_LIB"
    assert record.dut.cell == "inv"
    assert record.dut_label == tasks[0].task_id
    # The directory name is <stamp>_<cell>-<recipe_id>. The recipe id is the
    # Recipe's own now; it used to be derived from the quantus template stem,
    # which meant two different configurations of one cell produced the same
    # slug.
    assert record.slug == "inv-rc-coupled-typical"
    assert record.recipe.recipe_id == "rc-coupled-typical"
    assert set(record.recipe.templates) == {"si", "calibre", "quantus", "jivaro"}
    assert record.workspace_dir.endswith("QCI_PATH_inv")
    assert record.requested_stages == ["si", "strmout", "calibre", "quantus", "jivaro"]
    # The summary hands the same record back in memory, so the GUI and the
    # CLI do not have to re-read the file the runner just wrote.
    assert [r.run_id for r in summary.runs] == [record.run_id]
    assert summary.runs[0].overall == record.overall
    assert summary.run_dirs == [run_dir]


def test_run_record_carries_the_effective_configuration(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The recipe section is a snapshot: values, reduction, paths, dspf expression.

    Editing the Recipe on disk afterwards cannot rewrite it, which is the whole
    reason it is inlined rather than referenced. The values used to arrive from
    the ``--knob`` layer on top of the manifest merge; they arrive from Recipe
    fields now, and the snapshot still carries them under their old flat names
    so an archived ``run.json`` stays readable.
    """
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(extraction={"temperature_c": 85.0}),
        profile=_profile(workarea),
        dry_run=True,
    )

    recipe = read_record(_only_run_dir(ae_root)).recipe
    assert recipe.knobs["quantus"]["temperature"] == 85.0
    assert recipe.jivaro.enabled is True
    assert recipe.jivaro.frequency_limit == 14
    assert recipe.jivaro.error_max == 2
    assert set(recipe.paths) >= {"calibre_lvs_dir", "qrc_deck_dir"}


def test_run_record_keeps_the_dspf_pattern_not_only_its_result(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The snapshot has to carry the pattern, or the run is not replayable.

    ``dspf_path`` answers "where did this run put the file". Only the pattern
    answers "what would this configuration do somewhere else", which is the
    question a snapshot exists for.
    """

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
    )

    record = read_record(_only_run_dir(ae_root))
    assert record.recipe.dspf_out_path == "${WORK_ROOT2}/{cell}.dspf"
    assert record.dspf_path != record.recipe.dspf_out_path


def test_run_record_captures_env_context_and_provenance(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The resolved env and the full Jinja context used to die with the process."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project,
        tasks,
        stages=["si"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
    )

    record = read_record(_only_run_dir(ae_root))
    env_by_name = {b.name: b for b in record.env}
    assert env_by_name["WORK_ROOT"].source == "override"
    assert env_by_name["WORK_ROOT"].value == workarea.as_posix()
    assert record.context["cell"] == "inv"
    assert record.context["employee_id"] == "alice"
    assert record.context["output_dir"] == record.workspace_dir
    assert record.python_version
    assert record.auto_ext_version
    # Which binary each stage would have invoked, resolved through the same
    # PATH the subprocess sees.
    assert set(record.tools) == {"si", "strmout", "calibre", "quantus", "jivaro"}


def test_rerun_creates_a_second_run_and_leaves_the_first_untouched(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A.1: the direct opposite of the old ``open(log, "w")`` behaviour."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    run_tasks(
        project, tasks, stages=["calibre"], auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
    )
    first = _only_run_dir(ae_root)
    first_bytes = (first / "run.json").read_bytes()
    first_rendered = (first / "rendered" / "lvs.qci").read_bytes()

    run_tasks(
        project, tasks, stages=["calibre"], auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
    )

    dirs = _run_dirs(ae_root)
    assert len(dirs) == 2
    assert first in dirs
    assert (first / "run.json").read_bytes() == first_bytes
    assert (first / "rendered" / "lvs.qci").read_bytes() == first_rendered


def test_run_record_stage_rows_carry_timings_paths_and_argv(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """A.5: per-stage structured results, and log_path really points at a log."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    run_dir = _only_run_dir(ae_root)
    record = read_record(run_dir)
    assert [st.key for st in record.stages] == [
        "si", "strmout", "calibre", "quantus", "jivaro",
    ]
    for st in record.stages:
        assert st.status == "passed", f"{st.key}: {st.error}"
        assert st.started_at is not None and st.ended_at is not None
        assert st.ended_at >= st.started_at
        assert st.duration_s is not None and st.duration_s >= 0
        assert st.exit_code == 0
        assert st.argv, f"{st.key}: argv not recorded"
        assert st.cwd == str(workarea)
        # Paths are relative to the run dir so the directory can be moved.
        assert st.log_path == f"logs/{st.key}.log"
        assert (run_dir / st.log_path).is_file()

    si = record.stage("si")
    assert si is not None and si.rendered_path == "rendered/si.env"
    # strmout renders nothing, so it must not claim a rendered file.
    strmout = record.stage("strmout")
    assert strmout is not None and strmout.rendered_path is None


def test_run_record_promotes_the_lvs_report_and_archives_it(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """A.5: ``results/lvs.report`` is a copy, and the parsed verdict is typed.

    ``core/checks.py`` has always produced banner + discrepancy count; until
    the run layer it went into ``ToolResult.diagnostics["lvs_report"]`` and
    was read by nobody.
    """
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si", "strmout", "calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    run_dir = _only_run_dir(ae_root)
    record = read_record(run_dir)
    lvs = record.results.lvs
    assert lvs is not None
    assert lvs.passed is True
    assert lvs.banner == "CORRECT"
    assert lvs.discrepancies == 0
    assert lvs.archived_path == "results/lvs.report"

    archived = run_dir / "results" / "lvs.report"
    assert archived.is_file()
    # A copy, not a symlink: the source is overwritten by the next LVS run
    # of this cell.
    assert not archived.is_symlink()
    assert archived.read_bytes() == Path(lvs.source_path).read_bytes()
    # And the derived summary sits next to it.
    summary_json = json.loads((run_dir / "results" / "lvs_summary.json").read_text("utf-8"))
    assert summary_json["banner"] == "CORRECT"

    # The workarea paths that are too big to copy are still recorded.
    calibre = record.stage("calibre")
    assert calibre is not None
    assert any(a.endswith(".report") for a in calibre.artifacts)
    assert "lvs_report" in calibre.details


def test_run_record_survives_deleting_the_cadence_workspace(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """A.4: the run directory must stay self-consistent once the workarea goes.

    That is the whole point of archiving: the workspace holds gigabytes of
    regenerable intermediates and is rewritten by the next run of this cell.
    """
    import shutil

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si", "strmout", "calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )
    run_dir = _only_run_dir(ae_root)

    shutil.rmtree(workarea / "cds")

    record = read_record(run_dir)
    assert record.results.lvs is not None
    assert (run_dir / record.results.lvs.archived_path).is_file()
    assert (run_dir / "rendered" / "lvs.qci").is_file()
    assert (run_dir / "logs" / "calibre.log").is_file()


def test_failed_run_is_recorded_in_full_with_skip_reasons(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A.6: a failed run is a run. Skipped stages keep their reason."""
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "calibre")
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    record = read_record(_only_run_dir(ae_root))
    assert record.overall == "failed"
    by_key = {st.key: st for st in record.stages}
    assert by_key["calibre"].status == "failed"
    assert by_key["quantus"].status == "skipped"
    assert by_key["quantus"].skip_reason == "aborted after earlier stage failure"
    assert by_key["jivaro"].skip_reason == "aborted after earlier stage failure"
    # Even the failing LVS verdict is promoted, which is what makes a
    # "3 discrepancies, same as last time" comparison possible later.
    assert record.results.lvs is not None
    assert record.results.lvs.passed is False
    assert record.results.lvs.discrepancies == 3


def test_disabled_jivaro_records_its_own_skip_reason(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A stage that never ran must say why, or the record is unreadable later."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["calibre", "jivaro"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(reduction={"enabled": False}),
        profile=_profile(workarea), dry_run=True,
    )

    record = read_record(_only_run_dir(ae_root))
    jivaro = record.stage("jivaro")
    assert jivaro is not None
    assert jivaro.status == "skipped"
    assert jivaro.skip_reason == "jivaro disabled in recipe"


def test_cancelled_run_is_recorded_completely(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A.6: cancelling must not leave half a JSON document behind."""
    from auto_ext.core.progress import CancelToken

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    token = CancelToken()
    token.cancel()  # already cancelled before the first stage

    run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        cancel_token=token,
    )

    run_dir = _only_run_dir(ae_root)
    # Parses cleanly: the finalize write is atomic (.tmp + os.replace).
    record = read_record(run_dir)
    assert record.overall == "cancelled"
    assert record.cancelled_by == "user"
    assert record.stage("si").status == "cancelled"
    assert record.stage("calibre").status == "skipped"
    assert record.ended_at is not None


def test_run_appends_events_while_it_runs(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """``events.jsonl`` is the append-only trail; ``run.json`` lands once."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si", "strmout"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    events = read_events(_only_run_dir(ae_root))
    kinds = [e["event"] for e in events]
    assert kinds == [
        "run_start",
        "stage_start", "stage_end",
        "stage_start", "stage_end",
        "run_end",
    ]
    assert all("at" in e for e in events)
    stage_ends = [e for e in events if e["event"] == "stage_end"]
    assert [e["stage"] for e in stage_ends] == ["si", "strmout"]
    assert all(e["status"] == "passed" for e in stage_ends)
    assert all(e["duration_s"] is not None for e in stage_ends)
    assert events[-1]["overall"] == "passed"


def test_multi_task_dispatch_writes_a_batch_index(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Two tasks in one dispatch: two runs, one batch that lists both."""
    from auto_ext.core.run_store import read_batch

    _write_tasks(
        project_tools_config,
        [{"cell": "inv"}, {"cell": "buf"}],
    )
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    summary = run_tasks(
        project, tasks, stages=["calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
    )

    assert summary.batch_id is not None
    assert len(_run_dirs(ae_root)) == 2
    batch = read_batch(ae_root / "runs", summary.batch_id)
    assert batch.run_ids == [r.run_id for r in summary.runs]
    assert all(r.batch_id == summary.batch_id for r in summary.runs)


def test_single_task_dispatch_has_no_batch(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """One task is not a batch; the index file would just be noise."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    summary = run_tasks(
        project, tasks, stages=["calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
    )
    assert summary.batch_id is None
    assert summary.runs[0].batch_id is None
    assert not (ae_root / "runs" / "batches").exists()


# ---- S1: extraction_output_dir keys + the workspace lock -------------------


def test_output_dir_accepts_run_slug_format_key(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """``{run_slug}`` gives a user hard workspace isolation when they want it."""
    proj_path = project_tools_config / "project.yaml"
    proj_path.write_text(
        proj_path.read_text(encoding="utf-8").replace(
            '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"',
            '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}_{run_slug}"',
        ),
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
    )

    record = read_record(_only_run_dir(ae_root))
    assert record.workspace_dir.endswith("QCI_PATH_inv_inv-rc-coupled-typical")


def test_output_dir_run_id_key_isolates_every_rerun(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """``{run_id}`` is the strongest form: a fresh workspace per run."""
    proj_path = project_tools_config / "project.yaml"
    proj_path.write_text(
        proj_path.read_text(encoding="utf-8").replace(
            '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"',
            '"${WORK_ROOT}/cds/verify/QCI_PATH_{run_id}"',
        ),
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    workspaces = set()
    for _ in range(2):
        run_tasks(
            project, tasks, stages=["si"],
            auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
        )
    for run_dir in _run_dirs(ae_root):
        record = read_record(run_dir)
        assert record.workspace_dir.endswith(record.run_id)
        workspaces.add(record.workspace_dir)
    assert len(workspaces) == 2


def test_output_dir_unknown_format_key_lists_the_run_keys(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The error has to advertise the new keys or nobody will find them."""
    from auto_ext.core.errors import ConfigError

    proj_path = project_tools_config / "project.yaml"
    proj_path.write_text(
        proj_path.read_text(encoding="utf-8").replace(
            '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"',
            '"${WORK_ROOT}/QCI_PATH_{bogus}"',
        ),
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)

    with pytest.raises(ConfigError) as excinfo:
        run_tasks(
            project, tasks, stages=["si"],
            auto_ext_root=tmp_path / "project_root", workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
        )
    message = str(excinfo.value)
    assert "'bogus'" in message
    assert "run_slug" in message and "run_id" in message


def test_run_takes_and_releases_the_workspace_lock(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """The lock is held for the duration of the run and cleaned up after."""
    from auto_ext.core.workdir import WORKSPACE_LOCK_NAME

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    workspace = Path(read_record(_only_run_dir(ae_root)).workspace_dir)
    assert workspace.is_dir()
    assert not (workspace / WORKSPACE_LOCK_NAME).exists()


def test_run_refuses_a_workspace_another_run_holds(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A live lock is reported as a failed run, not as a silent interleave.

    Failing the task rather than raising out of ``run_tasks`` keeps the rest
    of a batch going, and the reason lands in ``run.json`` where the user can
    read it afterwards.
    """
    from auto_ext.core.workdir import acquire_workspace_lock

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    workspace = workarea / "cds" / "verify" / "QCI_PATH_inv"
    acquire_workspace_lock(workspace, "20260101T000000Z_someone-else")

    summary = run_tasks(
        project, tasks, stages=["si", "calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    assert summary.failed == 1
    record = read_record(_only_run_dir(ae_root))
    assert record.overall == "failed"
    assert "someone-else" in (record.stages[0].error or "")


def test_dry_run_does_not_touch_the_cadence_workspace(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A dry run renders and records; it must not create or lock a workspace."""
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si", "calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
    )

    workspace = Path(read_record(_only_run_dir(ae_root)).workspace_dir)
    assert not workspace.exists()


def test_serial_tasks_may_share_one_workspace(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The downgrade: same cell, two configurations, one workspace, in sequence.

    This used to be a hard ``ConfigError`` because the workspace doubled as
    the run's identity. It no longer does, so reusing it is the correct and
    expected behaviour — and each task still gets its own run directory.
    """
    _write_tasks(
        project_tools_config,
        [
            {"lvs_layout_view": "layout", "out_file": "av_ext_a"},
            {"lvs_layout_view": "layout_test", "out_file": "av_ext_b"},
        ],
    )
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    summary = run_tasks(
        project, tasks, stages=["calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
    )

    assert summary.total == 2
    run_dirs = _run_dirs(ae_root)
    assert len(run_dirs) == 2, "each task still gets its own run directory"
    workspaces = {read_record(d).workspace_dir for d in run_dirs}
    assert len(workspaces) == 1, "and they deliberately share the workspace"


def test_config_error_after_allocation_leaves_no_orphan_run(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A run directory is claimed before the context is built.

    If building it fails there is nothing worth recording, so the empty
    directory must be handed back rather than sitting in the history list
    warning "no run.json" forever.
    """
    from auto_ext.core.errors import ConfigError

    proj_path = project_tools_config / "project.yaml"
    proj_path.write_text(
        proj_path.read_text(encoding="utf-8").replace(
            'dspf_out_path: "${WORK_ROOT2}/{cell}.dspf"',
            'dspf_out_path: "${WORK_ROOT2}/{bogus}.dspf"',
        ),
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    with pytest.raises(ConfigError, match="unknown format key"):
        run_tasks(
            project, tasks, stages=["si"],
            auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea), dry_run=True,
        )

    assert _run_dirs(ae_root) == []


# ---- the catalog-driven render path ----------------------------------------
#
# Everything above this line runs the legacy path: no ``recipe=``, so templates
# come from ``project.templates`` and values from the manifest knob merge. The
# tests below pass a Recipe and a PdkProfile, which is the *only* thing that
# switches the runner over to :mod:`auto_ext.core.render`. Both paths have to
# stay green at the same time until the knob machinery is deleted.


def _profile(workarea: Path) -> "PdkProfile":
    """A PdkProfile equivalent to what ``project_tools_config`` describes.

    Same deck directories, same corner literal, same tech name -- so a recipe
    run and a legacy run of the same task produce comparable files.
    """
    from auto_ext.model.pdk import (
        CornerSpec,
        LvsDeckSet,
        LvsDeckVariant,
        PdkProfile,
        QrcDeck,
    )

    wa = workarea.as_posix()
    return PdkProfile(
        profile_id="hn001",
        display_name="HN001 (test)",
        tech_name="HN001",
        layer_map=f"{wa}/fake/layers.map",
        lvs_decks=LvsDeckSet(
            dir_expr="$calibre_source_added_place|parent",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="wodio",
        ),
        qrc=QrcDeck(
            dir_expr="$VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_QRC_B/CFXXX/QCI_deck"
        ),
        corners=[
            CornerSpec(
                name="typical", technology_corner="TYPICAL", default_temperature_c=55.0
            )
        ],
        default_corner="typical",
        env_overrides={
            "WORK_ROOT": wa,
            "WORK_ROOT2": wa,
            "VERIFY_ROOT": f"{wa}/fake/verify",
            "SETUP_ROOT": f"{wa}/fake/setup",
            "PDK_LAYER_MAP_FILE": f"{wa}/fake/layers.map",
            "calibre_source_added_place": (
                f"{wa}/fake/runset/Calibre_QRC/LVS/Ver_LVS_A/CFXXX/empty.cdl"
            ),
        },
    )


def _recipe(**overrides):
    """The Recipe every run in this file uses unless it says otherwise.

    ``reduction.enabled`` is **on**, which is not the schema default. It
    mirrors what ``project_tools_config``'s tasks.yaml used to say
    (``jivaro: {enabled: true}``): that switch moved from the task to the
    Recipe when the catalog took over rendering, and the stage-orchestration
    tests below are about what happens with all five stages live. A test that
    is about the off state passes ``reduction={"enabled": False}``.
    """

    from auto_ext.model.recipe import Recipe

    fields = {
        "recipe_id": "rc-coupled-typical",
        "name": "RC coupled, typical",
        "reduction": {"enabled": True},
    }
    fields.update(overrides)
    return Recipe(**fields)


def test_recipe_path_runs_every_stage_and_names_files_after_the_artifact(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(reduction={"enabled": True}),
        profile=_profile(workarea),
    )

    assert summary.passed == 1
    run_dir = _only_run_dir(ae_root)
    rendered = run_dir / "rendered"
    # Named after what the file *is*, not after whichever template made it.
    assert sorted(p.name for p in rendered.iterdir()) == [
        "ext.cmd",
        "jivaro.xml",
        "lvs.qci",
        "si.env",
    ]
    assert {s.stage: s.status for s in summary.tasks[0].stages} == {
        "si": "passed",
        "strmout": "passed",
        "calibre": "passed",
        "quantus": "passed",
        "jivaro": "passed",
    }


def test_recipe_path_records_catalog_version_and_profile_id(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    from auto_ext.catalog import builtin_catalog

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(), profile=_profile(workarea),
    )

    record = read_record(_only_run_dir(ae_root))
    assert record.catalog_version == builtin_catalog().catalog_version
    assert record.pdk_profile_id == "hn001"
    assert record.recipe.recipe_id == "rc-coupled-typical"
    assert record.recipe.name == "RC coupled, typical"
    # to_snapshot fills the seven legacy knob names from the typed fields, so a
    # recipe run and a knob run stay comparable in the history list.
    assert record.recipe.knobs["calibre"]["lvs_variant"] == "wodio"
    assert record.recipe.knobs["quantus"]["min_res"] == 0.001
    assert record.stage("si").render_target == "si.env"


def test_recipe_path_flattens_the_namespaced_context_into_the_record(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"
    run_tasks(
        project, tasks, stages=["si"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(), profile=_profile(workarea),
    )

    ctx = read_record(_only_run_dir(ae_root)).context
    assert ctx["cell"] == "inv"
    assert ctx["pdk.corner"] == "TYPICAL"
    assert ctx["recipe.extraction.min_res_ohm"] == 0.001
    assert ctx["recipe.lvs.deck_variant"] == "wodio"
    assert ctx["run.id"].endswith("_inv-rc-coupled-typical")


def test_recipe_emitting_both_output_forms_runs_quantus_twice(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """``ProjectConfig.templates`` has one quantus slot, so this is a shape the
    legacy path structurally cannot produce."""

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    summary = run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(output={"emit": ["extracted_view", "dspf"]}),
        profile=_profile(workarea),
    )

    assert summary.passed == 1
    keys = [s.key for s in summary.tasks[0].stages]
    assert keys == ["quantus.ext", "quantus.dspf"]
    run_dir = _only_run_dir(ae_root)
    assert (run_dir / "rendered" / "ext.cmd").is_file()
    assert (run_dir / "rendered" / "dspf.cmd").is_file()
    # One log per invocation, so neither overwrites the other.
    assert (run_dir / "logs" / "quantus.ext.log").is_file()
    assert (run_dir / "logs" / "quantus.dspf.log").is_file()
    record = read_record(run_dir)
    assert [s.key for s in record.stages] == ["quantus.ext", "quantus.dspf"]
    assert [s.stage for s in record.stages] == ["quantus", "quantus"]
    assert record.requested_stages == ["quantus.ext", "quantus.dspf"]


def test_recipe_stages_narrow_the_run(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=ae_root,
        workarea=workarea,
        recipe=_recipe(stages=["si", "calibre"]),
        profile=_profile(workarea),
    )

    assert [s.stage for s in summary.tasks[0].stages] == ["si", "calibre"]


def test_reduction_enabled_comes_from_the_recipe_not_the_task(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """``TaskConfig.jivaro`` is not consulted, whatever it says.

    The field is still on the model with a reader-less default (see
    ``core/config``'s module docstring), so the way to prove the runner
    ignores it is to set it to the *opposite* of the recipe and watch the
    recipe win. With both at their defaults the test would pass for a runner
    that read either one.
    """

    project, tasks = _load(project_tools_config)
    tasks = [
        t.model_copy(update={"jivaro": t.jivaro.model_copy(update={"enabled": True})})
        for t in tasks
    ]
    assert tasks[0].jivaro.enabled is True
    ae_root = tmp_path / "project_root"

    summary = run_tasks(
        project, tasks, stages=["jivaro"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(reduction={"enabled": False}), profile=_profile(workarea),
    )

    stage = summary.tasks[0].stages[0]
    assert stage.status == "skipped"
    assert stage.error == "jivaro disabled in recipe"


def test_half_a_configuration_cannot_even_be_expressed() -> None:
    """Neither a Recipe without a PdkProfile nor the reverse is callable.

    This used to be a pair of ``ConfigError`` tests, because both arguments
    were optional and the runner had to catch the half-configured case at
    runtime: a Recipe with no profile has no corner table, so
    ``-technology_corner`` could only be guessed. Now that the legacy render
    path is gone, both are required keyword-only parameters and the case is
    unreachable -- which is a stronger guarantee, and this is where it is
    recorded so nobody re-introduces a default for either.
    """

    import inspect

    parameters = inspect.signature(run_tasks).parameters
    for name in ("recipe", "profile"):
        parameter = parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, name
        assert parameter.default is inspect.Parameter.empty, name


def test_an_unknown_corner_fails_the_run_before_any_file_is_written(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """The corner is translated through the profile, so a name it does not
    define has to stop here rather than reach Quantus as an unknown string."""

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    with pytest.raises(AutoExtError, match="rcworst"):
        run_tasks(
            project, tasks, stages=["quantus"],
            auto_ext_root=ae_root, workarea=workarea,
            recipe=_recipe(extraction={"corner": "rcworst"}),
            profile=_profile(workarea),
        )
    assert _run_dirs(ae_root) == []


def test_a_setting_the_template_hardcodes_fails_its_stage_with_a_reason(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """The refusal is per stage and writes nothing.

    The subject used to be ``extraction.decoupling_factor`` against the quantus
    stage; that row is a ``[[var]]`` now, so the example moved to the one
    setting the shipped templates genuinely still freeze --
    ``calibre_lvs.qci.j2`` line 1 spells ``.qcilvs`` out between its three
    holes, so a PDK that names its decks anything else cannot be rendered for.
    Writing ``.qcilvs`` anyway and reporting success is the failure mode this
    check exists to kill.
    """

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    base = _profile(workarea)
    other_decks = base.lvs_decks.model_copy(
        update={"filename_pattern": "{basename}_{suffix}.rules"}
    )

    summary = run_tasks(
        project, tasks, stages=["calibre"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(),
        profile=base.model_copy(update={"lvs_decks": other_decks}),
    )

    stage = summary.tasks[0].stages[0]
    assert stage.status == "failed"
    assert "lvs_rules_filename_pattern" in stage.error
    assert "Recipe.patches" in stage.error
    assert not (_only_run_dir(ae_root) / "rendered" / "lvs.qci").exists()


def test_recipe_patches_land_in_the_rendered_file_and_in_run_json(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """The escape hatch, end to end: capture an edit against one render, then
    run again and find both the edit in the file and its outcome in run.json."""

    from auto_ext.core.env import substitute_env
    from auto_ext.core.patch import (
        capture_patch,
        mask_values,
        render_masked,
        sha256_text,
    )
    from auto_ext.core.render import plan_targets
    from auto_ext.core.template import make_jinja_env

    project, tasks = _load(project_tools_config)
    profile = _profile(workarea)
    ae_root = tmp_path / "project_root"

    # First run: no patches. Read back the context it recorded so the capture
    # sees exactly the render the runner produced.
    run_tasks(
        project, tasks, stages=["si"],
        auto_ext_root=ae_root, workarea=workarea,
        recipe=_recipe(), profile=profile,
    )
    first = _only_run_dir(ae_root)
    base_real = (first / "rendered" / "si.env").read_text(encoding="utf-8")

    plan = plan_targets(_recipe())[0]
    source = plan.spec.template_path.read_text(encoding="utf-8")
    env = {**project.env_overrides, **profile.env_overrides}
    substituted = substitute_env(source, env)
    context = _si_context(project, tasks[0], profile, first, workarea, env)
    assert make_jinja_env().from_string(substituted).render(**context) == base_real

    patch = capture_patch(
        template_source=substituted,
        template_sha256=sha256_text(source),
        stage="si",
        template_id=plan.spec.template_id,
        profile_id=profile.profile_id,
        catalog_version=None,
        base_real=base_real,
        base_masked=render_masked(substituted, context),
        edited_real=base_real.replace(
            'simSimulator = "auCdl"\n',
            'simSimulator = "auCdl"\nsimExtra = "yes"\n',
        ),
        values=mask_values(substituted, context),
        intents={0: "office asked for the extra si line"},
    )

    second_root = tmp_path / "project_root2"
    run_tasks(
        project, tasks, stages=["si"],
        auto_ext_root=second_root, workarea=workarea,
        recipe=_recipe(patches=[patch]), profile=profile,
    )
    run_dir = _only_run_dir(second_root)
    assert 'simExtra = "yes"' in (run_dir / "rendered" / "si.env").read_text(
        encoding="utf-8"
    )

    record = read_record(run_dir)
    assert len(record.patch_reports) == 1
    report = record.patch_reports[0]
    assert report.stage == "si"
    assert report.template_id == "si/default.env.j2"
    assert report.blocked is False
    assert [o.status for o in report.outcomes] == ["clean"]
    assert report.outcomes[0].intent == "office asked for the extra si line"


def _si_context(project, task, profile, run_dir, workarea, env):
    """Rebuild the render context the runner used for a si stage.

    Only the patch test needs this; every other assertion reads the context the
    runner already recorded in run.json.
    """
    from auto_ext.core import render
    from auto_ext.model.run import DutSnapshot, parse_run_id

    run_id = run_dir.name
    _, slug = parse_run_id(run_id)
    return render.build_context(
        dut=DutSnapshot.from_task_config(task),
        recipe=_recipe(),
        profile=profile,
        run=render.RunFacts(
            run_id=run_id,
            run_slug=slug,
            run_dir=run_dir,
            workarea=workarea,
            output_dir=f"{workarea.as_posix()}/cds/verify/QCI_PATH_{task.cell}",
            intermediate_dir=workarea.as_posix(),
            dspf_out_path=f"{workarea.as_posix()}/{task.cell}.dspf",
            stages=("si",),
        ),
        resolved_env=env,
        site=render.SiteFacts(
            employee_id=project.employee_id or "unknown",
            user=os.environ.get("USER") or os.environ.get("USERNAME"),
            host=socket.gethostname(),
        ),
    )


