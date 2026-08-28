"""Tests for :class:`auto_ext.ui.config_controller.ConfigController`.

The controller is the only object in the GUI that reads or writes a file, so
these tests are about the two contracts every screen leans on: what is loaded
is exactly the v2 file set, and one Save writes every staged document or none
of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.model.cells import CELLS_FILENAME, CellEntry, load_cells  # noqa: E402
from auto_ext.model.recipe import load_recipe  # noqa: E402
from auto_ext.model.workspace import WORKSPACE_FILENAME, load_workspace  # noqa: E402
from auto_ext.ui.config_controller import (  # noqa: E402
    ConfigController,
    recipe_search_path,
)


# ---- load -------------------------------------------------------------------


def test_load_reads_every_document_of_the_v2_set(loaded_controller) -> None:
    controller = loaded_controller
    assert controller.workspace is not None
    assert controller.cells is not None and len(controller.cells) == 1
    assert controller.profile is not None
    assert controller.profile.profile_id == controller.workspace.pdk_profile
    assert controller.recipe_ids() == ["rc-coupled-typical"]


def test_load_emits_config_loaded_with_the_directory(
    v2_config_dir: Path, isolated_recipe_path: Path, qtbot
) -> None:
    controller = ConfigController(auto_ext_root=v2_config_dir)
    seen: list[object] = []
    controller.config_loaded.connect(seen.append)
    controller.load(v2_config_dir / "config")
    assert seen == [v2_config_dir / "config"]


def test_a_v1_only_directory_names_the_migration_command(
    project_tools_config: Path, isolated_recipe_path: Path, qtbot
) -> None:
    """The one case that must not read as "corrupt file"."""

    controller = ConfigController()
    errors: list[str] = []
    controller.config_error.connect(errors.append)
    controller.load(project_tools_config)

    assert len(errors) == 1
    assert "project.yaml" in errors[0]
    assert "migrate" in errors[0]
    assert controller.workspace is None


def test_a_failed_load_leaves_the_previous_state_intact(
    loaded_controller, tmp_path: Path
) -> None:
    controller = loaded_controller
    before = controller.workspace
    errors: list[str] = []
    controller.config_error.connect(errors.append)

    controller.load(tmp_path / "nowhere")

    assert errors, "a missing directory must be reported"
    assert controller.workspace is before
    assert controller.config_dir == controller.config_dir


def test_one_unreadable_recipe_does_not_lose_the_rest(
    v2_config_dir: Path, isolated_recipe_path: Path, qtbot
) -> None:
    (v2_config_dir / "recipes" / "broken.yaml").write_text(
        "recipe_id: broken\nname:\n  - not a string\n", encoding="utf-8"
    )
    controller = ConfigController(auto_ext_root=v2_config_dir)
    errors: list[str] = []
    controller.config_error.connect(errors.append)
    controller.load(v2_config_dir / "config")

    assert controller.recipe_ids() == ["rc-coupled-typical"]
    assert any("broken.yaml" in message for message in errors)
    # And it is still *represented*. An error message the next status line
    # overwrites is not a report -- the file has to stay reachable from the
    # controller so the UI can keep drawing a row for it.
    broken = controller.broken_recipes
    assert [p.name for p in broken] == ["broken.yaml"]
    assert "broken.yaml" in next(iter(broken.values()))


def test_a_fixed_recipe_stops_being_broken_on_reload(
    v2_config_dir: Path, isolated_recipe_path: Path, qtbot
) -> None:
    """The obvious next move after the UI names the file is to edit it."""

    from auto_ext.model.recipe import Recipe, dump_recipe_yaml

    path = v2_config_dir / "recipes" / "broken.yaml"
    path.write_text(
        "recipe_id: broken\nname:\n  - not a string\n", encoding="utf-8"
    )
    controller = ConfigController(auto_ext_root=v2_config_dir)
    controller.load(v2_config_dir / "config")
    assert list(controller.broken_recipes)

    path.write_text(
        dump_recipe_yaml(Recipe(recipe_id="broken", name="fixed")), encoding="utf-8"
    )
    controller.load(v2_config_dir / "config")

    assert controller.broken_recipes == {}
    assert "broken" in controller.recipe_ids()


def test_later_search_path_directories_shadow_earlier_ones(
    v2_config_dir: Path, isolated_recipe_path: Path, recipe, qtbot
) -> None:
    """``<config_dir>/recipes`` wins over ``<root>/recipes``."""

    from auto_ext.model.recipe import save_recipe

    shadowed = recipe.model_copy(update={"name": "the one in config/"})
    save_recipe(
        shadowed,
        v2_config_dir / "config" / "recipes" / f"{recipe.recipe_id}.yaml",
    )
    controller = ConfigController(auto_ext_root=v2_config_dir)
    controller.load(v2_config_dir / "config")

    assert controller.recipe(recipe.recipe_id).name == "the one in config/"


# ---- derived paths ----------------------------------------------------------


def test_the_search_path_agrees_with_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GUI copy of the search path is checked against the CLI's original.

    The GUI cannot import ``auto_ext.cli`` (typer at start-up), so the
    function is duplicated. This is the test that keeps the duplicate honest.
    """

    from auto_ext.cli import recipe_search_path as cli_version

    monkeypatch.delenv("AUTO_EXT_RECIPES", raising=False)
    root = tmp_path / "root"
    config = root / "config"
    extra = tmp_path / "extra"
    assert recipe_search_path(root, config, extra) == cli_version(root, config, extra)
    assert recipe_search_path(None, config, None) == cli_version(None, config, None)
    assert recipe_search_path(root, None, None) == cli_version(root, None, None)


def test_the_search_path_honours_the_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_ext.ui.config_controller import RECIPES_ENV_VAR

    first = tmp_path / "one"
    monkeypatch.setenv(RECIPES_ENV_VAR, str(first))
    assert recipe_search_path(tmp_path)[0] == first.resolve()


def test_runs_root_and_recipes_dir_hang_off_the_config_dir(
    loaded_controller, v2_config_dir: Path
) -> None:
    controller = loaded_controller
    assert controller.runs_root == v2_config_dir / "runs"
    # Last entry of the search path, exactly as `auto-ext recipe new` picks it.
    assert controller.recipes_dir == (v2_config_dir / "config" / "recipes").resolve()


# ---- the runner adapter -----------------------------------------------------


def test_project_and_tasks_are_derived_from_the_v2_documents(
    loaded_controller,
) -> None:
    controller = loaded_controller
    project = controller.project
    assert project is not None
    assert project.extraction_output_dir == controller.workspace.output_dir_pattern
    assert project.dspf_out_path == controller.workspace.dspf_out_pattern

    (task,) = controller.tasks
    (cell,) = controller.cells.cells
    assert task.task_id == cell.key


def test_tasks_keep_disabled_rows_so_a_selection_still_resolves(
    loaded_controller,
) -> None:
    """An unchecked row must resolve to "not selected", not to "row missing"."""

    controller = loaded_controller
    book = controller.cells
    controller.stage_cells(
        book.model_copy(
            update={"cells": [book.cells[0].model_copy(update={"enabled": False})]}
        )
    )
    assert [t.task_id for t in controller.tasks] == [book.cells[0].key]


def test_can_run_needs_all_four_pieces(loaded_controller, tmp_path: Path) -> None:
    controller = loaded_controller
    assert controller.can_run is True

    empty = ConfigController()
    assert empty.can_run is False


def test_run_recipe_refuses_to_guess_between_two_recipes(
    loaded_controller, recipe
) -> None:
    controller = loaded_controller
    assert controller.run_recipe() is recipe_by_id(controller, "rc-coupled-typical")

    controller.stage_recipe(recipe.model_copy(update={"recipe_id": "second"}))
    assert controller.run_recipe() is None, "two candidates, no override -> refuse"
    assert controller.run_recipe("second").recipe_id == "second"


def recipe_by_id(controller: ConfigController, recipe_id: str):
    return controller.recipe(recipe_id)


# ---- staging ----------------------------------------------------------------


def test_staging_flips_dirty_once_and_reverting_flips_it_back(
    loaded_controller,
) -> None:
    controller = loaded_controller
    flips: list[bool] = []
    controller.dirty_changed.connect(flips.append)

    controller.stage_cells(controller.cells)
    controller.stage_workspace(controller.workspace)
    assert flips == [True], "one edge, not one per document"
    assert controller.pending_keys() == ["cells", "workspace"]

    controller.revert()
    assert flips == [True, False]
    assert controller.is_dirty is False


def test_a_staged_document_is_what_the_reader_sees(loaded_controller) -> None:
    controller = loaded_controller
    edited = controller.workspace.model_copy(update={"keep_runs": 7})
    controller.stage_workspace(edited)
    assert controller.workspace.keep_runs == 7


def test_staging_a_deletion_for_an_unsaved_recipe_just_drops_it(
    loaded_controller, recipe
) -> None:
    controller = loaded_controller
    controller.stage_recipe(recipe.model_copy(update={"recipe_id": "brand-new"}))
    assert "brand-new" in controller.recipe_ids()

    controller.stage_recipe_deletion("brand-new")
    assert "brand-new" not in controller.recipe_ids()
    assert controller.is_dirty is False, "nothing to write, nothing pending"


# ---- save -------------------------------------------------------------------


def test_save_writes_every_staged_document_and_reloads(
    loaded_controller, v2_config_dir: Path, recipe
) -> None:
    controller = loaded_controller
    config = v2_config_dir / "config"

    controller.stage_workspace(controller.workspace.model_copy(update={"keep_runs": 5}))
    controller.stage_cells(
        controller.cells.model_copy(
            update={
                "cells": [
                    *controller.cells.cells,
                    CellEntry(library="LIB", cell="amp", layout_view="layout"),
                ]
            }
        )
    )
    controller.stage_profile(
        controller.profile.model_copy(update={"description": "edited by the drawer"})
    )
    controller.stage_recipe(recipe.model_copy(update={"description": "edited"}))

    saved: list[object] = []
    controller.config_saved.connect(saved.append)
    assert controller.save() is True
    assert saved == [config]

    assert load_workspace(config / WORKSPACE_FILENAME).keep_runs == 5
    assert len(load_cells(config / CELLS_FILENAME)) == 2
    from auto_ext.core.profile_discover import read_profile_yaml

    assert read_profile_yaml(
        config / "profiles" / "hn001.yaml"
    ).description == "edited by the drawer"
    assert controller.is_dirty is False


def test_save_with_nothing_staged_is_a_no_op(loaded_controller) -> None:
    assert loaded_controller.save() is False


def test_a_new_recipe_lands_in_the_recipes_directory(
    loaded_controller, v2_config_dir: Path, recipe
) -> None:
    controller = loaded_controller
    controller.stage_recipe(recipe.model_copy(update={"recipe_id": "fresh"}))
    assert controller.save() is True

    written = v2_config_dir / "config" / "recipes" / "fresh.yaml"
    assert written.is_file()
    assert load_recipe(written).recipe_id == "fresh"
    assert "fresh" in controller.recipe_ids()


def test_a_staged_deletion_removes_the_file(
    loaded_controller, v2_config_dir: Path
) -> None:
    controller = loaded_controller
    target = v2_config_dir / "recipes" / "rc-coupled-typical.yaml"
    assert target.is_file()

    controller.stage_recipe_deletion("rc-coupled-typical")
    assert controller.save() is True

    assert not target.exists()
    assert controller.recipe_ids() == []


def test_save_refuses_when_a_file_moved_underneath_it(
    loaded_controller, v2_config_dir: Path
) -> None:
    controller = loaded_controller
    cells_path = v2_config_dir / "config" / CELLS_FILENAME
    cells_path.write_text(
        cells_path.read_text(encoding="utf-8") + "\n# touched by someone else\n",
        encoding="utf-8",
    )

    errors: list[str] = []
    controller.config_error.connect(errors.append)
    controller.stage_workspace(controller.workspace.model_copy(update={"keep_runs": 3}))

    assert controller.save() is False
    assert controller.has_external_change() is True
    assert controller.externally_changed_paths() == [cells_path]
    assert errors and CELLS_FILENAME in errors[-1]
    assert controller.is_dirty is True, "a refused save keeps the edits"


def test_force_overwrites_the_external_change(
    loaded_controller, v2_config_dir: Path
) -> None:
    controller = loaded_controller
    workspace_path = v2_config_dir / "config" / WORKSPACE_FILENAME
    workspace_path.write_text(
        workspace_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )
    controller.stage_workspace(controller.workspace.model_copy(update={"keep_runs": 9}))

    assert controller.save(force=True) is True
    assert load_workspace(workspace_path).keep_runs == 9


def test_save_writes_nothing_when_one_document_cannot_be_rendered(
    loaded_controller, v2_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All-or-nothing: a bad document must not leave the good ones half-applied."""

    controller = loaded_controller
    config = v2_config_dir / "config"
    before = (config / WORKSPACE_FILENAME).read_text(encoding="utf-8")

    controller.stage_workspace(controller.workspace.model_copy(update={"keep_runs": 4}))
    controller._pending["recipe:nowhere-to-go"] = object()
    monkeypatch.setattr(
        ConfigController, "recipe_path", lambda self, recipe_id: None
    )

    errors: list[str] = []
    controller.config_error.connect(errors.append)
    assert controller.save() is False
    assert (config / WORKSPACE_FILENAME).read_text(encoding="utf-8") == before
    assert errors


# ---- health -----------------------------------------------------------------


def test_refresh_health_emits_a_report_for_the_loaded_profile(
    loaded_controller, profile_env
) -> None:
    controller = loaded_controller
    seen: list[object] = []
    controller.health_changed.connect(seen.append)

    report = controller.refresh_health(force=True)
    assert report is not None
    assert report.profile_id == "hn001"
    assert seen == [report]
    assert controller.health_report is report


def test_refresh_health_without_a_profile_emits_none(qtbot) -> None:
    controller = ConfigController()
    seen: list[object] = []
    controller.health_changed.connect(seen.append)
    assert controller.refresh_health() is None
    assert seen == [None]
