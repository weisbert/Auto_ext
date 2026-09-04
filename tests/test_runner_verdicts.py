"""What the runner *records* about a dispatch it refuses, cancels or fails.

Three symptoms, one theme: the run record has to say what happened, and a
dispatch that cannot do anything useful has to say so instead of producing a
green record of nothing.

* **An empty stage set is refused, not run.** ``--stage jivaro`` against a
  recipe with no jivaro stage used to intersect down to ``[]``, allocate a run
  directory, execute nothing and report PASSED.
* **A failed stage records why.** ``StageRecord.details["failure"]`` carries a
  :class:`~auto_ext.core.failure_class.FailureVerdict`, so the GUI shows a
  diagnosis instead of re-deriving "the tool exited 1" from an exit code -- and
  a stage that failed before any log existed is not told to read one.
* **Cancelling a batch stops the batch.** Every task after the cancel used to
  get a full run directory of its own.

These drive :func:`auto_ext.core.runner.run_tasks` -- the public entry point --
rather than its helpers, and assert on ``run.json`` and on what is on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.errors import ConfigError
from auto_ext.core.progress import CancelToken, StageStatus, TaskStatus
from auto_ext.core.run_store import read_record
from auto_ext.core.runner import run_tasks

if TYPE_CHECKING:
    from auto_ext.model.pdk import PdkProfile
    from auto_ext.model.recipe import Recipe


# ---- fixtures-as-helpers ----------------------------------------------------


def _load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


def _write_cells(config_dir: Path, cells: list[str]) -> None:
    """Replace ``tasks.yaml`` with one row per cell name."""

    body = "\n".join(
        "\n".join(
            [
                f"- library: EXAMPLE_LIB",
                f"  cell: {cell}",
                "  lvs_layout_view: layout",
                "  lvs_source_view: schematic",
                "  ground_net: vss",
                "  out_file: av_ext",
            ]
        )
        for cell in cells
    )
    (config_dir / "tasks.yaml").write_text(body + "\n", encoding="utf-8")


def _set_dspf_out_path(config_dir: Path, pattern: str) -> None:
    """Re-point ``project.yaml``'s ``dspf_out_path`` at ``pattern``."""

    path = config_dir / "project.yaml"
    lines = [
        f"dspf_out_path: \"{pattern}\"" if line.startswith("dspf_out_path:") else line
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _profile(workarea: Path) -> "PdkProfile":
    """A PdkProfile matching what ``project_tools_config`` describes."""

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


def _recipe(**overrides: Any) -> "Recipe":
    from auto_ext.model.recipe import Recipe

    fields: dict[str, Any] = {
        "recipe_id": "rc-coupled-typical",
        "name": "RC coupled, typical",
    }
    fields.update(overrides)
    return Recipe(**fields)


def _run_dirs(auto_ext_root: Path) -> list[Path]:
    root = auto_ext_root / "runs"
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir() if p.is_dir() and p.name not in ("batches", "latest")
    )


# ---- M-23: requested stages that miss the recipe ---------------------------


def test_stages_that_do_not_intersect_the_recipe_are_refused_by_name(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Asking for a stage the recipe does not run must not "pass".

    The symptom: tick only ``jivaro`` for a recipe whose stage list stops at
    calibre and the dispatch reports PASSED with an empty stage strip.
    """

    project, tasks = _load(project_tools_config)
    recipe = _recipe(stages=["si", "strmout", "calibre"])

    with pytest.raises(ConfigError) as excinfo:
        run_tasks(
            project,
            tasks,
            stages=["jivaro"],
            auto_ext_root=tmp_path / "project_root",
            workarea=workarea,
            recipe=recipe,
            profile=_profile(workarea),
            dry_run=True,
        )

    message = str(excinfo.value)
    # Both sets, so the user can see which side to change.
    assert "jivaro" in message
    assert "calibre" in message
    assert recipe.recipe_id in message
    # And nothing was written: a refused dispatch leaves no run behind.
    assert _run_dirs(tmp_path / "project_root") == []


def test_a_narrowed_stage_set_that_still_overlaps_is_accepted(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The refusal must not catch the ordinary "only run calibre" narrowing."""

    project, tasks = _load(project_tools_config)
    summary = run_tasks(
        project,
        tasks,
        stages=["calibre", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        recipe=_recipe(stages=["si", "strmout", "calibre"]),
        profile=_profile(workarea),
        dry_run=True,
    )
    assert summary.total == 1
    assert [s.key for s in summary.tasks[0].stages] == ["calibre"]


# ---- M-25: the runner records a verdict, not just a status -----------------


def test_a_stage_that_failed_before_it_ran_records_a_verdict_not_a_log_hint(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A render failure produces no log, so its next step must not name one.

    The symptom: every failure said "the tool exited 1, read the stage log"
    and for this class of failure there is no stage log to read -- nothing
    ever spawned. ``details["failure"]`` is where the verdict belongs; the
    card reads it back from there.
    """

    # A DSPF output directory nobody has created. Quantus does not create it
    # either, so the stage refuses before spawning anything -- one of the
    # three no-log failure paths.
    _set_dspf_out_path(project_tools_config, "${WORK_ROOT2}/dspf/{cell}.dspf")
    project, tasks = _load(project_tools_config)
    root = tmp_path / "project_root"

    summary = run_tasks(
        project,
        tasks,
        stages=["quantus"],
        auto_ext_root=root,
        workarea=workarea,
        recipe=_recipe(output={"emit": ["dspf"]}),
        profile=_profile(workarea),
        dry_run=True,
    )

    assert summary.tasks[0].overall == TaskStatus.FAILED
    record = read_record(_run_dirs(root)[0])
    stage = record.stages[0]
    assert stage.status == StageStatus.FAILED
    assert stage.log_path is None

    verdict = stage.details["failure"]
    assert verdict["failure_class"] == "environment"
    assert verdict["confidence"] == "certain"
    assert "dspf" in verdict["reason"].lower()
    action = verdict["next_action"].lower()
    assert "read the stage log" not in action
    assert "open the stage log" not in action
    assert "no stage log" in action, "it should say why there is nothing to open"


def test_a_stage_the_tool_failed_records_the_verdict_the_log_supports(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
    mocks_on_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real non-zero exit is classified once, by the runner, into the record.

    Before this the record held the exit code and nothing else, so every
    consumer had to re-derive the diagnosis -- and the GUI's could only ever
    be as good as the fields that survived into ``run.json``.
    """

    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "si")
    project, tasks = _load(project_tools_config)
    root = tmp_path / "project_root"

    run_tasks(
        project,
        tasks,
        stages=["si"],
        auto_ext_root=root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
    )

    record = read_record(_run_dirs(root)[0])
    stage = record.stages[0]
    assert stage.status == StageStatus.FAILED
    verdict = stage.details["failure"]
    assert verdict["failure_class"] == "tool_crash"
    assert "4" in verdict["reason"], "the exit code the mock returned"
    # This one *does* have a log, so pointing at it is the right next step.
    assert stage.log_path is not None


def test_a_verdict_is_recorded_only_for_the_stage_that_failed(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A passing (here: dry-run) stage carries no failure verdict at all."""

    project, tasks = _load(project_tools_config)
    root = tmp_path / "project_root"
    run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
    )
    record = read_record(_run_dirs(root)[0])
    assert record.stages, "the dry run should still record its stages"
    for stage in record.stages:
        assert "failure" not in stage.details, stage.key


# ---- M-42: cancelling a batch ----------------------------------------------


class _CancelOnFirstTask:
    """Reporter that pulls the cancel token as soon as the first task starts.

    Stands in for the user hitting Stop a second after Run: the first cell is
    already under way, the other four have not started.
    """

    def __init__(self, token: CancelToken) -> None:
        self._token = token
        self.tasks_started: list[str] = []
        self.tasks_ended: list[tuple[str, Any]] = []

    def on_run_start(self, total: int, stages: list[str]) -> None:
        pass

    def on_task_start(self, task_id: str, stages: list[str]) -> None:
        self.tasks_started.append(task_id)
        self._token.cancel()

    def on_stage_start(self, task_id: str, stage: str) -> None:
        pass

    def on_stage_end(self, task_id: str, stage: str, status: Any, error: Any) -> None:
        pass

    def on_task_end(self, task_id: str, status: Any) -> None:
        self.tasks_ended.append((task_id, status))

    def on_run_end(self, summary: Any) -> None:
        pass


def test_cancelling_a_batch_does_not_write_a_run_dir_per_remaining_cell(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Five cells, cancelled during the first: one run directory, not five.

    The symptom: "I cancelled one run and got twenty new rows in the history."
    A task the cancel reached before it started never ran, so it has nothing
    to record -- and a directory per never-started cell buries the run the
    user actually wants to look at.
    """

    _write_cells(project_tools_config, ["inv", "nand2", "nor2", "buf", "dff"])
    project, tasks = _load(project_tools_config)
    root = tmp_path / "project_root"
    token = CancelToken()
    reporter = _CancelOnFirstTask(token)

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=root,
        workarea=workarea,
        recipe=_recipe(),
        profile=_profile(workarea),
        dry_run=True,
        reporter=reporter,
        cancel_token=token,
    )

    assert len(_run_dirs(root)) == 1, "only the cell that started owns a run"
    # The dispatch still accounts for every cell it was given...
    assert summary.total == 5
    assert summary.cancelled == 5
    # ...and the reporter still sees a start/end pair for each, so a GUI tree
    # cannot be left with a task stuck on "running".
    assert len(reporter.tasks_started) == 5
    assert [status for _, status in reporter.tasks_ended] == [
        TaskStatus.CANCELLED
    ] * 5
