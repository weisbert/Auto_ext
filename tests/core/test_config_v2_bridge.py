"""What :mod:`auto_ext.core.config` does after the v1 mechanism was removed.

Two jobs, and one test file because they are two halves of the same door:

* a ``project.yaml`` / ``tasks.yaml`` that still carries a retired key is
  refused **by name**, with ``auto-ext migrate`` in the message. The point of
  these tests is the wording: ``extra="forbid"`` already stops the load, but a
  pydantic ``extra_forbidden`` chain never says *migrate*, and the user's next
  action is exactly one command.
* ``workspace.yaml`` + ``cells.yaml`` -- the pair that replaced them -- adapts
  into the :class:`~auto_ext.core.config.ProjectConfig` +
  :class:`~auto_ext.core.config.TaskConfig` shapes the runner takes.

The v1 schema itself is tested where it lives now (:mod:`auto_ext.legacy_v1`,
exercised through ``tests/test_migrate.py``); nothing here asserts that a v1
file *parses*, only that this module declines it usefully.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.config import (
    RETIRED_PROJECT_KEYS,
    RETIRED_TASK_KEYS,
    ProjectConfig,
    load_project,
    load_tasks,
    load_v2_config,
    project_from_workspace,
    tasks_from_cells,
)
from auto_ext.core.errors import ConfigError

_MINIMAL_PROJECT = """\
employee_id: alice
extraction_output_dir: "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
"""

_MINIMAL_TASKS = """\
- library: LIB
  cell: inv
  lvs_layout_view: layout
"""


def _write(directory: Path, project: str, tasks: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "project.yaml").write_text(project, encoding="utf-8")
    (directory / "tasks.yaml").write_text(tasks, encoding="utf-8")
    return directory


# ---- the v1 door -------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "block"),
    [
        ("templates", "templates:\n  si: templates/si/default.env.j2\n"),
        ("knobs", "knobs:\n  quantus:\n    temperature: 25.0\n"),
        ("paths", "paths:\n  calibre_lvs_dir: $X|parent\n"),
        (
            "tech_name_env_vars",
            "tech_name_env_vars:\n  - PDK_TECH_FILE\n",
        ),
    ],
)
def test_a_retired_project_key_is_refused_by_name(
    tmp_path: Path, key: str, block: str
) -> None:
    config = _write(tmp_path, _MINIMAL_PROJECT + block, _MINIMAL_TASKS)

    with pytest.raises(ConfigError) as excinfo:
        load_project(config / "project.yaml")

    message = str(excinfo.value)
    assert key in message, "the offending key has to be named"
    assert "v1 format" in message
    assert "auto-ext migrate" in message
    assert str(config) in message, "the command has to be copy-pasteable"


@pytest.mark.parametrize(
    ("key", "block"),
    [
        ("templates", "  templates:\n    si: templates/si/default.env.j2\n"),
        ("knobs", "  knobs:\n    quantus:\n      temperature: 25.0\n"),
        ("label", "  label: the amp\n"),
        ("exclude", "  exclude:\n    - cell: inv\n"),
        (
            "jivaro_overrides",
            "  jivaro_overrides:\n    inv:\n      enabled: false\n",
        ),
        ("dspf_out_path", '  dspf_out_path: "${WORK_ROOT2}/{cell}.dspf"\n'),
    ],
)
def test_a_retired_task_key_is_refused_by_name(
    tmp_path: Path, key: str, block: str
) -> None:
    config = _write(tmp_path, _MINIMAL_PROJECT, _MINIMAL_TASKS + block)

    with pytest.raises(ConfigError) as excinfo:
        load_tasks(config / "tasks.yaml")

    message = str(excinfo.value)
    assert key in message
    assert "entry #0" in message, "with several entries, say which one"
    assert "auto-ext migrate" in message


def test_the_refusal_lists_every_retired_key_the_file_carries(tmp_path: Path) -> None:
    """One trip through the migration, not four rounds of whack-a-mole."""

    config = _write(
        tmp_path,
        _MINIMAL_PROJECT
        + "templates:\n  si: a.j2\n"
        + "knobs:\n  quantus:\n    temperature: 25.0\n"
        + "paths:\n  calibre_lvs_dir: $X|parent\n",
        _MINIMAL_TASKS,
    )

    with pytest.raises(ConfigError) as excinfo:
        load_project(config / "project.yaml")

    message = str(excinfo.value)
    for key in ("templates", "knobs", "paths"):
        assert key in message


def test_the_refusal_says_where_each_setting_went(tmp_path: Path) -> None:
    config = _write(
        tmp_path, _MINIMAL_PROJECT + "knobs:\n  quantus:\n    temperature: 25.0\n",
        _MINIMAL_TASKS,
    )

    with pytest.raises(ConfigError) as excinfo:
        load_project(config / "project.yaml")

    assert RETIRED_PROJECT_KEYS["knobs"] in str(excinfo.value)


def test_the_task_refusal_says_where_each_setting_went(tmp_path: Path) -> None:
    config = _write(tmp_path, _MINIMAL_PROJECT, _MINIMAL_TASKS + "  label: amp\n")

    with pytest.raises(ConfigError) as excinfo:
        load_tasks(config / "tasks.yaml")

    assert RETIRED_TASK_KEYS["label"] in str(excinfo.value)


def test_an_ordinary_typo_is_not_dressed_up_as_a_migration(tmp_path: Path) -> None:
    """``templaets:`` is a typo, not a v1 file. Sending the user through a
    migration for it would waste their afternoon."""

    config = _write(
        tmp_path, _MINIMAL_PROJECT + "templaets:\n  si: a.j2\n", _MINIMAL_TASKS
    )

    with pytest.raises(ConfigError) as excinfo:
        load_project(config / "project.yaml")

    message = str(excinfo.value)
    assert "auto-ext migrate" not in message
    assert "templaets" in message


def test_a_reduced_pair_still_loads(tmp_path: Path) -> None:
    config = _write(tmp_path, _MINIMAL_PROJECT, _MINIMAL_TASKS)

    project = load_project(config / "project.yaml")
    tasks = load_tasks(config / "tasks.yaml", project=project)

    assert project.employee_id == "alice"
    assert [t.task_id for t in tasks] == ["LIB__inv__layout__schematic"]
    assert tasks[0].display_name is None


def test_the_retired_key_tables_and_the_schema_do_not_overlap() -> None:
    """A key cannot be both accepted and reported as retired."""

    from auto_ext.core.config import TaskSpec

    assert not (set(RETIRED_PROJECT_KEYS) & set(ProjectConfig.model_fields))
    assert not (set(RETIRED_TASK_KEYS) & set(TaskSpec.model_fields))


# ---- the v2 bridge -----------------------------------------------------------


def test_workspace_supplies_the_three_path_patterns() -> None:
    from auto_ext.model.workspace import WorkspaceConfig

    workspace = WorkspaceConfig(
        pdk_profile="hn001",
        output_dir_pattern="${WORK_ROOT}/w/{cell}_{recipe}",
        intermediate_dir="${WORK_ROOT2}/i",
        dspf_out_pattern="${WORK_ROOT2}/{cell}.dspf",
    )

    project = project_from_workspace(workspace)

    assert project.extraction_output_dir == "${WORK_ROOT}/w/{cell}_{recipe}"
    assert project.intermediate_dir == "${WORK_ROOT2}/i"
    assert project.dspf_out_path == "${WORK_ROOT2}/{cell}.dspf"


def test_workspace_does_not_smuggle_in_pdk_facts() -> None:
    """``layer_map`` / ``tech_name`` / ``env_overrides`` come from the profile.

    Filling them here from a second source is how two sources of truth start,
    and the render context would then have a silent winner.
    """

    from auto_ext.model.workspace import WorkspaceConfig

    project = project_from_workspace(WorkspaceConfig(pdk_profile="hn001"))

    assert project.tech_name is None
    assert project.env_overrides == {}


def test_cells_become_tasks_keyed_like_the_old_task_id() -> None:
    from auto_ext.model.cells import CellBook, CellEntry

    book = CellBook(
        cells=[
            CellEntry(
                library="LIB",
                cell="inv",
                layout_view="layout",
                ground_net="gnd",
                out_file="av_ext",
                display_name="the inverter",
            )
        ]
    )

    tasks = tasks_from_cells(book)

    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == "LIB__inv__layout__schematic"
    assert task.ground_net == "gnd"
    assert task.out_file == "av_ext"
    assert task.display_name == "the inverter"


def test_a_disabled_row_is_left_out() -> None:
    from auto_ext.model.cells import CellBook, CellEntry

    book = CellBook(
        cells=[
            CellEntry(library="LIB", cell="a", layout_view="layout"),
            CellEntry(library="LIB", cell="b", layout_view="layout", enabled=False),
        ]
    )

    assert [t.cell for t in tasks_from_cells(book)] == ["a"]
    assert [t.cell for t in tasks_from_cells(book, include_disabled=True)] == ["a", "b"]


def test_the_recipe_owns_reduction_and_the_lvs_policy() -> None:
    """Both fields survive on ``TaskConfig`` but nothing fills them from cells:
    the runner reads ``recipe.reduction.enabled`` and
    ``recipe.policy.continue_on_lvs_fail`` instead."""

    from auto_ext.model.cells import CellBook, CellEntry

    book = CellBook(cells=[CellEntry(library="LIB", cell="a", layout_view="layout")])
    task = tasks_from_cells(book)[0]

    assert task.jivaro.enabled is False
    assert task.continue_on_lvs_fail is False


def test_load_v2_config_reads_the_repository_sample() -> None:
    """The migrated ``examples/demo`` tree, end to end through the bridge."""

    demo = Path(__file__).resolve().parents[2] / "examples" / "demo" / "config"

    project, book = load_v2_config(demo)
    tasks = tasks_from_cells(book)

    assert project.extraction_output_dir.startswith("${WORK_ROOT}")
    assert [t.task_id for t in tasks] == [
        "DEMO_LIB__inv__layout__schematic",
        "DEMO_LIB__inv__layout_test__schematic",
    ]


def test_a_missing_half_of_the_pair_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(
        "schema_version: 1\npdk_profile: hn001\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError, match="cells.yaml"):
        load_v2_config(tmp_path)


# ---- the CLI reaches the same door -------------------------------------------


def test_run_against_a_v1_directory_prints_the_migration_command(tmp_path: Path) -> None:
    """The wiring, not the wording: ``auto-ext run`` must surface the refusal
    rather than turning it into "workspace.yaml not found"."""

    from typer.testing import CliRunner

    from auto_ext.cli import app

    config = _write(
        tmp_path, _MINIMAL_PROJECT + "templates:\n  si: a.j2\n", _MINIMAL_TASKS
    )
    result = CliRunner().invoke(
        app, ["run", "--config-dir", str(config), "--recipe", "anything"]
    )

    assert result.exit_code == 2
    assert "v1 format" in result.output
    assert "auto-ext migrate" in result.output


def test_run_against_an_empty_directory_says_which_two_files_it_wanted(
    tmp_path: Path,
) -> None:
    from typer.testing import CliRunner

    from auto_ext.cli import app

    empty = tmp_path / "nothing"
    empty.mkdir()
    result = CliRunner().invoke(
        app, ["run", "--config-dir", str(empty), "--recipe", "anything"]
    )

    assert result.exit_code == 2
    assert "workspace.yaml" in result.output
    assert "auto-ext migrate" in result.output
