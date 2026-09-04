"""``lvs.run_qrc_query`` off + a requested ``quantus`` is not a runnable pair.

The Calibre runset's post-trigger that runs ``calibre -query_input ... -query
svdb`` is what fills ``<output_dir>/query_output``. Both Quantus decks read
that directory unconditionally -- ``input_db -directory_name``, ``-layer_map_file
.../Design.gds.map``, ``-device_properties_file .../Design.props``. Turn the
trigger off and ask for quantus anyway and Quantus starts, looks for three
files nobody wrote, and dies there, after si and Calibre have already been paid
for.

The knob exists for a reason -- an LVS-only run should not pay for the
query_output dump -- so the *combination* is refused rather than the knob, and
the refusal names both sides so the message says which of the two to change.

**Moved on 2026-09-04.** This was a ``Recipe`` model validator keyed on
``recipe.stages``. That field is gone (ONE CONCEPT, ONE OWNER: the stage
selector owns which stages run), so the check moved to the one place the
requested stage set exists -- a ``run_tasks`` pre-flight, before a run
directory is created. It is a better home as well as a forced one: a recipe
with the query off is perfectly legal on its own, and what was ever wrong was
the dispatch that paired it with quantus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.errors import ConfigError
from auto_ext.core.runner import run_tasks
from auto_ext.model.recipe import Recipe


def _recipe(**overrides: object) -> Recipe:
    fields: dict[str, object] = {"recipe_id": "lvs-only", "name": "LVS only"}
    fields.update(overrides)
    return Recipe(**fields)  # type: ignore[arg-type]


def _dispatch(config_dir: Path, workarea: Path, tmp_path: Path, **over: object):
    """Ask ``run_tasks`` for a run. Pre-flight refusals never reach a tool."""

    from tests.support.v2 import make_profile

    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    kwargs: dict[str, object] = {
        "stages": ["si", "strmout", "calibre", "quantus"],
        "auto_ext_root": tmp_path / "project_root",
        "workarea": workarea,
        "recipe": _recipe(),
        "profile": make_profile(),
    }
    kwargs.update(over)
    return run_tasks(project, tasks, **kwargs)  # type: ignore[arg-type]


def test_requesting_quantus_with_the_qrc_query_off_is_refused(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        _dispatch(
            project_tools_config,
            workarea,
            tmp_path,
            recipe=_recipe(lvs={"run_qrc_query": False}),
        )

    message = str(excinfo.value)
    assert "run_qrc_query" in message, "the message must name the knob to change"
    assert "quantus" in message
    assert "query_output" in message


def test_it_is_refused_before_a_run_directory_exists(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """The M-23 contract, inherited: refuse before anything is claimed.

    A refusal that has already taken the workspace lock and written a run
    directory leaves a row in the history whose only content is the refusal --
    the same fake action wearing a different colour.
    """

    with pytest.raises(ConfigError):
        _dispatch(
            project_tools_config,
            workarea,
            tmp_path,
            recipe=_recipe(lvs={"run_qrc_query": False}),
        )

    assert not (tmp_path / "project_root" / "runs").exists()


def test_the_query_may_be_turned_off_when_nothing_extracts(
    project_tools_config: Path, workarea: Path, tmp_path: Path, mocks_on_path: Path
) -> None:
    """The intended use of the knob keeps working: LVS and nothing after it."""

    summary = _dispatch(
        project_tools_config,
        workarea,
        tmp_path,
        stages=["si", "strmout", "calibre"],
        recipe=_recipe(lvs={"run_qrc_query": False}),
    )
    assert [s.stage for s in summary.tasks[0].stages] == ["si", "strmout", "calibre"]


def test_a_recipe_with_the_query_off_is_legal_on_its_own() -> None:
    """It is the dispatch that can be wrong, never the recipe by itself.

    The model used to refuse this object outright, which meant a perfectly
    good LVS-only recipe could not be saved from the form while quantus
    happened to be in its stage list.
    """

    assert _recipe(lvs={"run_qrc_query": False}).lvs.run_qrc_query is False
