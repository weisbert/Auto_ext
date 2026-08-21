"""Is ``run.json`` a snapshot, or a set of pointers that go stale?

``docs/refactor/04-tests-disposition.md`` section 3.A asks two questions the
rest of the run tests do not: **A.3** whether the recipe recorded in a run is a
*copy* of the configuration or a reference to files that keep changing, and
**A.4** whether ``rendered/`` holds the bytes the tool was actually handed or a
second render that happens to look similar.

Both matter for the same reason. A run record exists so that six weeks later
somebody can answer "what did this run do, and why is the layout different
now". A record that points at a Recipe file which has since been edited
answers a different question -- what the recipe says *today* -- and gives no
sign that it has done so.

These tests deliberately go through :func:`auto_ext.core.runner.run_tasks`
rather than constructing a record: the claim is about what the runner writes,
not about what the model can hold.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.run_store import read_record
from auto_ext.core.runner import run_tasks
from auto_ext.model.recipe import load_recipe, save_recipe
from tests.support.v2 import make_profile, make_recipe


def _profile(workarea: Path):
    """The PdkProfile ``project_tools_config``'s environment describes."""

    wa = workarea.as_posix()
    return make_profile(
        layer_map=f"{wa}/fake/layers.map",
        env_overrides={
            "WORK_ROOT": wa,
            "WORK_ROOT2": wa,
            "VERIFY_ROOT": f"{wa}/fake/verify",
            "SETUP_ROOT": f"{wa}/fake/setup",
            "PDK_LAYER_MAP_FILE": f"{wa}/fake/layers.map",
            "calibre_source_added_place": (
                f"{wa}/fake/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX/empty.cdl"
            ),
        },
    )


def _only_run_dir(auto_ext_root: Path) -> Path:
    runs = [
        p
        for p in (auto_ext_root / "runs").iterdir()
        if p.is_dir() and (p / "run.json").is_file()
    ]
    assert len(runs) == 1, [p.name for p in runs]
    return runs[0]


def _run(project_tools_config: Path, workarea: Path, root: Path, *, recipe, **kwargs):
    project, tasks = (
        load_project(project_tools_config / "project.yaml"),
        None,
    )
    tasks = load_tasks(project_tools_config / "tasks.yaml", project=project)
    return run_tasks(
        project,
        tasks,
        stages=kwargs.pop("stages", ["quantus"]),
        auto_ext_root=root,
        workarea=workarea,
        recipe=recipe,
        profile=_profile(workarea),
        dry_run=True,
        **kwargs,
    )


# ---- A.3: the recipe in the record is a copy -------------------------------


def test_editing_the_recipe_file_afterwards_does_not_touch_the_record(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Run, then edit the Recipe on disk. The record must not move.

    This is the property that makes a run history worth keeping. A Recipe is a
    shared, edited, git-tracked file; if ``run.json`` stored ``recipe_id`` and
    nothing else, every historical run would silently re-describe itself every
    time somebody tuned a threshold, and the one question a history exists to
    answer -- "was this run different, or was the layout?" -- would have no
    answer at all.
    """

    recipe_path = tmp_path / "recipes" / "rc.yaml"
    save_recipe(make_recipe(extraction={"temperature_c": 55.0}), recipe_path)
    root = tmp_path / "root"

    _run(project_tools_config, workarea, root, recipe=load_recipe(recipe_path))
    before = read_record(_only_run_dir(root))
    assert before.recipe.knobs["quantus"]["temperature"] == 55.0

    # Somebody re-tunes the shared recipe. Same id, same file, new value.
    save_recipe(make_recipe(extraction={"temperature_c": 125.0}), recipe_path)
    assert load_recipe(recipe_path).extraction.temperature_c == 125.0

    after = read_record(_only_run_dir(root))
    assert after.recipe.knobs["quantus"]["temperature"] == 55.0
    assert after.model_dump() == before.model_dump()


def test_the_rendered_file_and_the_record_agree_on_the_value_that_produced_it(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A snapshot nobody can check against the output is just a claim.

    The record says the run used 85 C; the file the tool would have read has
    to say 85 too. Asserting only the record would pass for a runner that
    recorded its inputs and rendered from something else -- which is exactly
    the failure mode the whole round is about.
    """

    root = tmp_path / "root"
    _run(
        project_tools_config,
        workarea,
        root,
        recipe=make_recipe(extraction={"temperature_c": 85.0}),
    )

    run_dir = _only_run_dir(root)
    record = read_record(run_dir)
    rendered = (run_dir / "rendered" / "ext.cmd").read_text(encoding="utf-8")

    assert record.recipe.knobs["quantus"]["temperature"] == 85.0
    assert "-temperature" in rendered
    assert "85" in rendered


def test_two_runs_of_one_cell_with_different_recipes_keep_their_own_snapshots(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The head-first scenario: copy a recipe, change one number, run both.

    Under the old identity model both runs were the same ``task_id`` and the
    second overwrote the first's logs. Two run directories with two different
    snapshots is the whole reason the Run object exists, so it gets a nail
    that reads both records rather than counting directories.
    """

    root = tmp_path / "root"
    for temperature in (55.0, 125.0):
        _run(
            project_tools_config,
            workarea,
            root,
            recipe=make_recipe(
                recipe_id=f"rc-{int(temperature)}c",
                extraction={"temperature_c": temperature},
            ),
        )

    run_dirs = sorted(
        p for p in (root / "runs").iterdir() if (p / "run.json").is_file()
    )
    assert len(run_dirs) == 2
    recorded = sorted(
        read_record(d).recipe.knobs["quantus"]["temperature"] for d in run_dirs
    )
    assert recorded == [55.0, 125.0]
    # ...and each rendered file matches its own record, not the other's.
    for run_dir in run_dirs:
        record = read_record(run_dir)
        text = (run_dir / "rendered" / "ext.cmd").read_text(encoding="utf-8")
        assert str(int(record.recipe.knobs["quantus"]["temperature"])) in text


# ---- A.4: rendered/ holds the bytes the tool was handed --------------------


def test_the_archived_file_is_the_one_the_argv_points_at(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """``rendered/ext.cmd`` is not a copy of the tool input -- it *is* it.

    The record's stage row carries the argv the subprocess received. The path
    inside that argv has to be the archived file itself, byte for byte. If the
    runner rendered into a scratch location and copied afterwards, a
    difference between the two could never be noticed from the record, and the
    archive would be evidence of nothing.
    """

    root = tmp_path / "root"
    project = load_project(project_tools_config / "project.yaml")
    tasks = load_tasks(project_tools_config / "tasks.yaml", project=project)
    run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=root,
        workarea=workarea,
        recipe=make_recipe(),
        profile=_profile(workarea),
    )

    run_dir = _only_run_dir(root)
    record = read_record(run_dir)
    stage = record.stage("quantus")
    assert stage is not None

    argv = stage.details.get("argv")
    assert argv, stage.details
    cmd_args = [Path(arg) for arg in argv if str(arg).endswith(".cmd")]
    assert len(cmd_args) == 1, argv

    handed_to_the_tool = cmd_args[0]
    archived = run_dir / "rendered" / "ext.cmd"
    assert archived.is_file()
    assert handed_to_the_tool.resolve() == archived.resolve()
    assert stage.rendered_path == "rendered/ext.cmd"


def test_the_archive_survives_the_cadence_workspace_being_deleted(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """The workspace is reused and overwritten; the run directory is not.

    Deleting the workspace is the cheap stand-in for what actually happens --
    the next run of the same cell rewrites it. Whatever the run directory
    still holds afterwards is the whole of what a later reader gets.
    """

    import shutil

    root = tmp_path / "root"
    project = load_project(project_tools_config / "project.yaml")
    tasks = load_tasks(project_tools_config / "tasks.yaml", project=project)
    run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=root,
        workarea=workarea,
        recipe=make_recipe(),
        profile=_profile(workarea),
    )

    run_dir = _only_run_dir(root)
    before = (run_dir / "rendered" / "ext.cmd").read_bytes()
    record_before = read_record(run_dir).model_dump()

    workspace = Path(read_record(run_dir).workspace_dir)
    if workspace.is_dir():
        shutil.rmtree(workspace)
    shutil.rmtree(workarea, ignore_errors=True)

    assert (run_dir / "rendered" / "ext.cmd").read_bytes() == before
    assert read_record(run_dir).model_dump() == record_before


@pytest.mark.parametrize("stage", ["si", "calibre", "quantus"])
def test_every_stage_that_renders_leaves_its_file_in_the_run_directory(
    project_tools_config: Path, workarea: Path, tmp_path: Path, stage: str
) -> None:
    """No stage renders into the workarea any more, not even si.

    ``si.env`` is the awkward one: the tool reads it from the *workspace*, so
    the runner publishes a copy there after the stage passes. The original
    still has to be the archived one, or "which si.env produced this netlist"
    becomes unanswerable the moment the workspace is reused.
    """

    root = tmp_path / "root"
    _run(project_tools_config, workarea, root, recipe=make_recipe(), stages=[stage])

    run_dir = _only_run_dir(root)
    record = read_record(run_dir)
    row = record.stage(stage)
    assert row is not None
    assert row.rendered_path is not None, f"{stage} recorded no rendered file"
    archived = run_dir / row.rendered_path
    assert archived.is_file()
    assert archived.parent == run_dir / "rendered"
