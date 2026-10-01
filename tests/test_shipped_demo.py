"""The shipped ``examples/demo`` tree, driven through a real dispatch.

Every unit test of the path patterns passed while the demo itself aborted
every run: ``dspf_out_pattern`` named ``{layout_view}``, which
``WorkspaceConfig`` accepts and the runner's DSPF resolver did not, so each
row ended "not run" with "unknown format key 'layout_view'" (1e4ecf9, caught
by the video round on 1e3194a). The pieces were each right; the shipped
config is where they meet, so it is what this file runs.

Dry run on purpose: it builds the full render context -- the workspace, the
intermediate directory, the DSPF path -- and renders every file, without an
EDA tool. ``jobs`` 1 and 2 both, because the parallel path adds the
"two concurrent tasks may not share a workspace" refusal the demo used to
trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.config import load_v2_config, tasks_from_cells
from auto_ext.core.profile_discover import read_profile_yaml
from auto_ext.core.run_store import read_record
from auto_ext.core.runner import preflight, run_tasks
from auto_ext.model.recipe import load_recipe
from tests.test_runner_parallel import symlink_required

DEMO = Path(__file__).resolve().parents[1] / "examples" / "demo"
STAGES = ["si", "strmout", "calibre", "quantus", "jivaro"]


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The demo's config, recipe and profile, with its env vars under tmp."""

    work = tmp_path / "work"
    for name, value in {
        "WORK_ROOT": work,
        "WORK_ROOT2": work / "dspf",
        "VERIFY_ROOT": work / "verify",
        "SETUP_ROOT": work / "setup",
        "PDK_LAYER_MAP_FILE": work / "setup" / "layers.map",
        "calibre_source_added_place": work / "verify" / "empty.cdl",
    }.items():
        monkeypatch.setenv(name, str(value))

    project, book = load_v2_config(DEMO / "config")
    tasks = tasks_from_cells(book)
    recipe = load_recipe(DEMO / "recipes" / "rc-typical-55c.yaml")
    profile = read_profile_yaml(DEMO / "config" / "profiles" / f"{recipe_profile(project)}.yaml")
    workarea = tmp_path / "workarea"
    workarea.mkdir()
    (workarea / "cds.lib").write_text("", encoding="utf-8")
    (workarea / ".cdsinit").write_text("", encoding="utf-8")
    return project, tasks, recipe, profile, workarea, tmp_path / "root"


def recipe_profile(project) -> str:
    """The profile id the demo's workspace names."""

    profile_id = getattr(project, "pdk_profile", None)
    return profile_id or "hn001"


@pytest.mark.parametrize("jobs", [1, 2])
def test_the_shipped_demo_passes_preflight(demo, jobs: int) -> None:
    project, tasks, recipe, profile, _workarea, _root = demo
    assert len(tasks) >= 2, "the premise: the demo runs one cell in two views"
    preflight(
        project,
        tasks,
        stages=STAGES,
        recipe=recipe,
        profile=profile,
        max_workers=jobs if jobs >= 2 else None,
    )


@pytest.mark.parametrize(
    "jobs",
    [
        1,
        # Parallel mode symlinks cds.lib into each task's work dir.
        pytest.param(2, marks=symlink_required),
    ],
)
def test_the_shipped_demo_dry_runs_every_row_into_its_own_paths(demo, jobs: int) -> None:
    project, tasks, recipe, profile, workarea, root = demo

    summary = run_tasks(
        project,
        tasks,
        stages=STAGES,
        auto_ext_root=root,
        workarea=workarea,
        recipe=recipe,
        profile=profile,
        dry_run=True,
        max_workers=jobs if jobs >= 2 else None,
    )

    assert len(summary.tasks) == len(tasks)
    for task in summary.tasks:
        assert str(task.overall) == "passed", (
            task.task_id,
            [(stage.stage, stage.status, stage.error) for stage in task.stages],
        )
    records = [read_record(task.run_dir) for task in summary.tasks]
    workspaces = {record.workspace_dir for record in records}
    assert len(workspaces) == len(tasks), workspaces
