"""MainWindow integration tests.

Three groups:

* the window boots off-screen, shows the three redesign screens and nothing
  from the retired tabs;
* it fits inside the 940x560 floor the redesign promised -- the old window's
  effective minimum was 724x1056, which does not shrink on a 1080p screen;
* the wiring between the controller and the screens actually moves data.

Off-screen means ``Qt.WA_DontShowOnScreen`` plus a real ``show()``: a window
that is never shown never lays out, so a size assertion against an unshown
window measures nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QMenu, QMessageBox  # noqa: E402

from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.screens.cells_screen import CellsScreen  # noqa: E402
from auto_ext.ui.screens.project_screen import ProjectScreen  # noqa: E402
from auto_ext.ui.screens.recipes_screen import RecipesScreen  # noqa: E402
from auto_ext.ui.screens.runs_screen import RunsScreen  # noqa: E402
from auto_ext.ui.screens.setup_drawer import SetupDrawer  # noqa: E402
from auto_ext.ui.theme import WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH  # noqa: E402
from auto_ext.ui.widgets.init_wizard import InitProjectWizard  # noqa: E402
from auto_ext.ui.widgets.log_view import LogView  # noqa: E402


@pytest.fixture(autouse=True)
def dialogs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Record every message box instead of showing it, and return the log.

    An unpatched ``QMessageBox`` in a headless test does not fail -- it opens
    a modal and blocks the run until the CI job is killed. Recording by
    default makes an unexpected dialog an assertion instead of a hang, and a
    test that needs a specific answer re-patches on top of this (a test's own
    ``monkeypatch`` runs after the autouse fixture, so it wins).
    """

    log: list[tuple[str, str, str]] = []

    def record(kind: str, default):
        def handler(parent, title, text, *args, **kwargs):
            log.append((kind, title, text))
            return default

        return handler

    for kind, default in (
        ("warning", QMessageBox.Ok),
        ("critical", QMessageBox.Ok),
        ("information", QMessageBox.Ok),
        ("question", QMessageBox.Cancel),
    ):
        monkeypatch.setattr(QMessageBox, kind, record(kind, default))
    return log


@pytest.fixture(autouse=True)
def no_unexpected_modal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make an un-patched ``exec_()`` fail instead of blocking the run.

    Same reason as :func:`dialogs`: a modal opened by code under test has no
    one to close it, so the failure mode is a hung CI job rather than a red
    test. A test that expects a dialog patches ``exec_`` on the concrete
    subclass, which wins over this base-class patch.
    """

    from PyQt5.QtWidgets import QDialog

    def refuse(self) -> int:  # pragma: no cover - the point is it is not called
        raise AssertionError(
            f"{type(self).__name__}.exec_() was called without being patched"
        )

    monkeypatch.setattr(QDialog, "exec_", refuse)


@pytest.fixture
def window(qtbot, isolated_recipe_path: Path) -> MainWindow:
    """An off-screen :class:`MainWindow` with no project loaded."""

    win = MainWindow()
    qtbot.addWidget(win)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    qtbot.waitExposed(win)
    return win


@pytest.fixture
def loaded_window(qtbot, v2_config_dir: Path, isolated_recipe_path: Path) -> MainWindow:
    """An off-screen window with ``v2_config_dir`` loaded."""

    win = MainWindow(
        config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir
    )
    qtbot.addWidget(win)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    qtbot.waitExposed(win)
    return win


def _find_action(window: MainWindow, text_contains: str):
    for menu in window.menuBar().findChildren(QMenu):
        for action in menu.actions():
            if text_contains in action.text():
                return action
    return None


# ---- the page set -----------------------------------------------------------


def test_the_rail_holds_exactly_the_redesign_screens(window: MainWindow) -> None:
    assert window.shell.page_keys() == ["cells", "recipes", "runs", "project"]
    assert isinstance(window.shell.page("cells"), CellsScreen)
    assert isinstance(window.shell.page("recipes"), RecipesScreen)
    assert isinstance(window.shell.page("runs"), RunsScreen)
    # Project came last on purpose: it is the screen a user visits when
    # something is wrong or when a project is new, not the one they work in.
    assert isinstance(window.shell.page("project"), ProjectScreen)


def test_the_retired_tabs_are_gone_from_the_package(window: MainWindow) -> None:
    """Not just unregistered -- unimportable, so nothing can resurrect one."""

    import importlib

    for name in (
        "auto_ext.ui.tabs.project_tab",
        "auto_ext.ui.tabs.tasks_tab",
        "auto_ext.ui.tabs.templates_tab",
        "auto_ext.ui.tabs.run_tab",
        "auto_ext.ui.tabs.runs_tab",
        "auto_ext.ui.templates_view",
        "auto_ext.ui.widgets.knob_editor",
        "auto_ext.ui.widgets.preset_picker",
        "auto_ext.ui.widgets.dspf_out_path_combo",
        "auto_ext.ui.widgets.diff_editor",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)


def test_nav_codes_are_unique_three_letter_labels(window: MainWindow) -> None:
    codes = [window.shell.nav_button(k).code for k in window.shell.page_keys()]
    assert codes == ["CEL", "RCP", "RNS", "PRJ"]
    assert len(set(codes)) == len(codes)


def test_the_setup_drawer_is_mounted_and_toggles(window: MainWindow) -> None:
    assert isinstance(window.shell.setup_widget(), SetupDrawer)
    assert window.shell.is_setup_open() is False
    window.shell.health_badge.clicked.emit()
    assert window.shell.is_setup_open() is True


def test_the_log_view_is_mounted_in_the_run_bars_slot(window: MainWindow) -> None:
    """The Run tab's embedded log viewer moved here; it is not a screen."""

    assert isinstance(window.cells_screen.run_bar.log_widget(), LogView)
    assert window.cells_screen.run_bar.log_widget() is window.log_view


# ---- the size floor ---------------------------------------------------------


def test_the_window_declares_the_940x560_floor(window: MainWindow) -> None:
    assert window.minimumWidth() == WINDOW_MIN_WIDTH == 940
    assert window.minimumHeight() == WINDOW_MIN_HEIGHT == 560


def test_the_window_actually_fits_inside_940x560(loaded_window: MainWindow) -> None:
    """The hard promise of this refactor.

    The old window's minimumSizeHint was 724x1056 -- the Project tab alone
    demanded ~1000px of height, so on a 1080p screen the window could not be
    shrunk below almost the whole display. Every screen is measured with a
    project loaded, because an empty screen is trivially small.
    """

    hint = loaded_window.minimumSizeHint()
    assert hint.width() <= WINDOW_MIN_WIDTH, hint
    assert hint.height() <= WINDOW_MIN_HEIGHT, hint


@pytest.mark.parametrize("page", ["cells", "recipes", "runs"])
def test_no_single_screen_pushes_past_the_floor(
    loaded_window: MainWindow, qtbot, page: str
) -> None:
    """Measured per page: the stack's hint is the max over its children, so a
    single greedy screen would be invisible in the aggregate number above."""

    loaded_window.shell.set_current_page(page)
    qtbot.wait(10)
    hint = loaded_window.shell.page(page).minimumSizeHint()
    assert hint.width() <= 700, (page, hint)
    assert hint.height() <= 400, (page, hint)


def test_the_window_can_be_resized_to_the_floor(
    loaded_window: MainWindow, qtbot
) -> None:
    loaded_window.resize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
    qtbot.wait(10)
    assert loaded_window.width() == WINDOW_MIN_WIDTH
    assert loaded_window.height() == WINDOW_MIN_HEIGHT
    assert loaded_window.shell.is_rail_collapsed() is True


def test_every_page_can_be_shown_at_the_floor_without_error(
    loaded_window: MainWindow, qtbot
) -> None:
    """The offscreen boot check: walk the rail with the window at 940x560."""

    loaded_window.resize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
    for key in loaded_window.shell.page_keys():
        loaded_window.shell.set_current_page(key)
        qtbot.wait(10)
        assert loaded_window.shell.current_page_key() == key
        assert loaded_window.shell.current_page().isVisible()
    loaded_window.shell.set_setup_open(True)
    qtbot.wait(10)
    assert loaded_window.setup_drawer.isVisible()


# ---- controller wiring ------------------------------------------------------


def test_loading_a_project_fills_every_screen(loaded_window: MainWindow) -> None:
    window = loaded_window
    controller = window.controller

    assert window.shell.config_path() == str(controller.config_dir)
    assert len(window.cells_screen.cells()) == len(controller.cells)
    assert window.cells_screen.recipe_choices() == [
        ("rc-coupled-typical", "RC coupled, typical")
    ]
    assert window.recipes_screen.current_recipe_id() == "rc-coupled-typical"
    assert window.runs_screen.runs_root == controller.runs_root


def test_editing_the_cell_table_stages_it_on_the_controller(
    loaded_window: MainWindow,
) -> None:
    window = loaded_window
    window.cells_screen.add_cell()
    assert window.controller.pending_keys() == ["cells"]
    assert len(window.controller.cells) == len(window.cells_screen.cells())


def test_dirty_state_reaches_the_title_and_the_status_bar(
    loaded_window: MainWindow,
) -> None:
    window = loaded_window
    assert window.windowTitle() == "Auto_ext"
    window.cells_screen.add_cell()
    assert window.windowTitle().endswith("*")
    assert window.shell.status_right() == "unsaved changes"

    window.controller.revert()
    assert window.windowTitle() == "Auto_ext"
    assert window.shell.status_right() == ""


def test_save_is_disabled_until_something_is_staged(
    loaded_window: MainWindow,
) -> None:
    save = _find_action(loaded_window, "Save")
    assert save is not None and save.isEnabled() is False
    loaded_window.cells_screen.add_cell()
    assert save.isEnabled() is True


def test_save_writes_the_staged_documents(
    loaded_window: MainWindow, v2_config_dir: Path
) -> None:
    from auto_ext.model.cells import CELLS_FILENAME, load_cells

    window = loaded_window
    before = len(load_cells(v2_config_dir / "config" / CELLS_FILENAME))
    window.cells_screen.add_cell()
    assert window.save() is True
    assert len(load_cells(v2_config_dir / "config" / CELLS_FILENAME)) == before + 1
    assert window.controller.is_dirty is False


def test_a_saving_a_recipe_edit_reaches_the_controller(
    loaded_window: MainWindow,
) -> None:
    window = loaded_window
    recipe = window.controller.recipe("rc-coupled-typical")
    window.recipes_screen.save_requested.emit(
        recipe.model_copy(update={"description": "typed in the form"})
    )
    assert window.controller.recipe("rc-coupled-typical").description == (
        "typed in the form"
    )


def test_a_new_recipe_gets_a_unique_id_and_reaches_both_screens(
    loaded_window: MainWindow,
) -> None:
    window = loaded_window
    window.recipes_screen.new_requested.emit()
    ids = window.controller.recipe_ids()
    assert "new-recipe" in ids
    assert ("new-recipe", "New recipe") in window.cells_screen.recipe_choices()

    window.recipes_screen.new_requested.emit()
    assert "new-recipe-2" in window.controller.recipe_ids()


def test_duplicating_a_recipe_records_its_lineage(loaded_window: MainWindow) -> None:
    window = loaded_window
    window.recipes_screen.duplicate_requested.emit("rc-coupled-typical")
    clone = window.controller.recipe("rc-coupled-typical-copy")
    assert clone is not None
    assert clone.derived_from == "rc-coupled-typical"


def test_deleting_a_recipe_stages_the_removal(loaded_window: MainWindow) -> None:
    window = loaded_window
    window.recipes_screen.delete_requested.emit("rc-coupled-typical")
    assert window.controller.recipe_ids() == []
    assert window.controller.pending_keys() == ["recipe:rc-coupled-typical"]


def test_a_config_error_lands_in_the_status_bar_not_a_dialog(
    window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under X11 forwarding a modal costs a round trip; only actions get one."""

    def explode(*args, **kwargs):  # pragma: no cover - the point is it is not called
        raise AssertionError("a background load error must not open a dialog")

    monkeypatch.setattr(QMessageBox, "warning", explode)
    window.controller.load(tmp_path / "missing")

    assert window.errors, "the error was recorded"
    assert window.shell.status_left().startswith("error - ")


def test_an_explicit_reload_failure_does_open_a_dialog(
    loaded_window: MainWindow, monkeypatch: pytest.MonkeyPatch, v2_config_dir: Path
) -> None:
    """There the user pressed something and is waiting for an answer."""

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda parent, title, text, *a, **k: shown.append((title, text)),
    )
    (v2_config_dir / "config" / "workspace.yaml").write_text(
        "pdk_profile: [not, a, slug]\n", encoding="utf-8"
    )
    _find_action(loaded_window, "Reload").trigger()

    assert shown and "Could not load" in shown[0][0]


# ---- the run lifecycle ------------------------------------------------------


def test_a_finished_run_refreshes_the_history(
    loaded_window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = loaded_window
    calls: list[int] = []
    monkeypatch.setattr(
        type(window.runs_screen), "refresh", lambda self: calls.append(1)
    )
    window.cells_screen.run_finished.emit(None)

    assert calls == [1]
    assert window.shell.status_left() == "idle"


def test_a_started_run_says_so_in_the_status_bar(loaded_window: MainWindow) -> None:
    window = loaded_window
    window.cells_screen.run_requested.emit(object())
    assert window.shell.status_left() == "running"


def test_the_log_path_the_screen_publishes_reaches_the_viewer(
    loaded_window: MainWindow, tmp_path: Path
) -> None:
    log = tmp_path / "runs" / "r1" / "logs" / "calibre.log"
    loaded_window.cells_screen.log_path_changed.emit(log)
    assert loaded_window.log_view.path == log


def test_the_run_bars_follow_checkbox_drives_the_viewer(
    loaded_window: MainWindow,
) -> None:
    window = loaded_window
    window.cells_screen.run_bar.set_follows_current_stage(False)
    assert window.log_view.follows() is False
    window.cells_screen.run_bar.set_follows_current_stage(True)
    assert window.log_view.follows() is True


def test_a_rerun_request_navigates_to_the_cell(loaded_window: MainWindow) -> None:
    window = loaded_window
    window.shell.set_current_page("runs")
    key = window.controller.cells.cells[0].key

    class _Entry:
        task_id = key

    window.runs_screen.rerun_requested.emit(_Entry())

    assert window.shell.current_page_key() == "cells"
    assert window.cells_screen.selected_keys() == (key,)


# ---- the setup drawer -------------------------------------------------------


def test_a_failure_that_points_at_setup_opens_the_drawer(
    loaded_window: MainWindow,
) -> None:
    window = loaded_window
    window.runs_screen.setup_requested.emit("env.WORK_ROOT")
    assert window.shell.is_setup_open() is True


def test_pinning_an_override_stages_the_profile_rather_than_writing_it(
    loaded_window: MainWindow,
) -> None:
    """A health row must not be able to rewrite the PDK profile behind a Save."""

    window = loaded_window
    window.setup_drawer.override_requested.emit("WORK_ROOT", "/w/real")

    assert window.controller.pending_keys() == ["profile"]
    assert window.controller.profile.env_overrides["WORK_ROOT"] == "/w/real"
    assert window.controller.is_dirty is True


def test_the_drawer_offers_the_pin_control_because_someone_listens(
    loaded_window: MainWindow,
) -> None:
    assert loaded_window.setup_drawer.can_pin_overrides() is True


def test_recheck_forces_a_fresh_health_report(
    loaded_window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[bool] = []
    monkeypatch.setattr(
        type(loaded_window.controller),
        "refresh_health",
        lambda self, *, force=False: seen.append(force),
    )
    loaded_window.setup_drawer.recheck_requested.emit()
    assert seen == [True]


def _with_inserted_line(rendered: str, line: str = "; typed by hand") -> str:
    """Insert ``line`` after the first line of ``rendered``.

    Not appended at the end: a stored edit is placed by the lines around it,
    so an insertion past the last line has no context after it and the patch
    format refuses it. That refusal has its own test below.
    """

    lines = rendered.splitlines(keepends=True)
    lines.insert(1, line + "\n")
    return "".join(lines)


# ---- the escape hatch -------------------------------------------------------


@pytest.fixture
def picks_first_file(monkeypatch: pytest.MonkeyPatch):
    """Answer the "which generated file?" modal with the first entry.

    The button opens four files' worth of choice now (si.env, lvs.qci,
    quantus.ext.cmd, jivaro.xml); before it silently took si.env. Stubbed
    rather than driven for the same reason ``RenderedFileEditor.exec_`` is:
    an unanswered modal hangs the run instead of failing the test.
    """

    from auto_ext.ui.main_window import MainWindow

    seen: list[list[str]] = []

    def fake_ask(self, _recipe, labels, current):
        seen.append(list(labels))
        return current

    monkeypatch.setattr(MainWindow, "_ask_which_plan", fake_ask)
    return seen


def test_edit_rendered_asks_which_of_the_generated_files_to_edit(
    loaded_window: MainWindow, profile_env, picks_first_file, monkeypatch
) -> None:
    """It used to open si.env and offer no way to reach the other three."""

    from auto_ext.ui.widgets import rendered_editor

    monkeypatch.setattr(
        rendered_editor.RenderedFileEditor, "exec_", lambda self: self.reject() or 0
    )
    loaded_window.recipes_screen.edit_rendered_requested.emit("rc-coupled-typical")

    assert picks_first_file, "the user was never asked which file"
    offered = picks_first_file[-1]
    assert len(offered) == 4
    assert any("si.env" in label for label in offered)
    assert any("quantus.ext.cmd" in label for label in offered)
    assert any("lvs.qci" in label for label in offered)
    assert any("jivaro.xml" in label for label in offered)


def test_edit_rendered_does_not_ask_when_there_is_only_one_file(
    loaded_window: MainWindow, profile_env, picks_first_file, monkeypatch
) -> None:
    """One target, no modal: there is nothing to choose."""

    from auto_ext.ui.widgets import rendered_editor

    monkeypatch.setattr(
        rendered_editor.RenderedFileEditor, "exec_", lambda self: self.reject() or 0
    )
    from auto_ext.model.common import Stage

    window = loaded_window
    recipe = window.controller.recipe("rc-coupled-typical")
    # model_copy skips validation, so the enum has to be the real one: a raw
    # "jivaro" string reaches the renderer and blows up on ``stage.value``.
    window.controller.stage_recipe(
        recipe.model_copy(update={"stages": [Stage.JIVARO]})
    )

    window.recipes_screen.edit_rendered_requested.emit("rc-coupled-typical")

    assert picks_first_file == [], "a single target must not raise a chooser"


def test_edit_rendered_without_the_pdk_environment_refuses_and_names_the_vars(
    loaded_window: MainWindow, dialogs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a machine without the PDK loaded this is the expected outcome.

    It has to be a refusal, not a render: an unset variable substitutes as
    the empty string, so a preview would succeed and show a file full of
    paths like /cds/verify/QCI_PATH_inv -- and the edit captured against
    it would be anchored to nonsense.

    The fixture profile pins its variables in ``env_overrides`` so the rest
    of the suite can render; this test takes them away, which is what an
    engineer who has not sourced the PDK setup actually has.
    """

    window = loaded_window
    profile = window.controller.profile
    for name in profile.env_overrides:
        monkeypatch.delenv(name, raising=False)
    window.controller.stage_profile(profile.model_copy(update={"env_overrides": {}}))

    window.recipes_screen.edit_rendered_requested.emit("rc-coupled-typical")

    assert dialogs, "a refusal must be visible"
    _kind, title, message = dialogs[-1]
    assert "environment" in title
    assert "WORK_ROOT" in message
    # The profile edit above is the only thing staged: nothing was captured.
    assert window.controller.pending_keys() == ["profile"]


def test_edit_rendered_stores_the_edit_on_the_recipe(
    loaded_window: MainWindow, monkeypatch: pytest.MonkeyPatch, profile_env, picks_first_file, dialogs
) -> None:
    """The whole escape hatch, end to end: render, edit, capture, stage."""

    from auto_ext.ui.widgets import rendered_editor

    def fake_exec(self) -> int:
        self.set_text(_with_inserted_line(self.edited_text()))
        self._store_button.click()
        return 1

    monkeypatch.setattr(rendered_editor.RenderedFileEditor, "exec_", fake_exec)

    window = loaded_window
    assert window.controller.recipe("rc-coupled-typical").manual_edit_count == 0

    window.recipes_screen.edit_rendered_requested.emit("rc-coupled-typical")

    assert dialogs == [], dialogs
    updated = window.controller.recipe("rc-coupled-typical")
    assert updated.manual_edit_count == 1
    # Written, not merely staged: the screen reloads from the controller right
    # after this and would otherwise go back to showing "saved" with its Save
    # button disabled, i.e. claim the edit was safe while it was not.
    assert window.controller.pending_keys() == []
    assert window.controller.is_dirty is False
    assert "manual edit" in window.shell.status_left()


def test_edit_rendered_with_no_change_stores_nothing(
    loaded_window: MainWindow, monkeypatch: pytest.MonkeyPatch, profile_env, picks_first_file
) -> None:
    from auto_ext.ui.widgets import rendered_editor

    monkeypatch.setattr(
        rendered_editor.RenderedFileEditor,
        "exec_",
        lambda self: self.accept() or 1,
    )
    window = loaded_window
    window.recipes_screen.edit_rendered_requested.emit("rc-coupled-typical")

    assert window.controller.is_dirty is False


def test_a_stored_edit_survives_a_save_and_reload(
    loaded_window: MainWindow, monkeypatch: pytest.MonkeyPatch, profile_env, picks_first_file
) -> None:
    from auto_ext.ui.widgets import rendered_editor

    def fake_exec(self) -> int:
        self.set_text(_with_inserted_line(self.edited_text()))
        self._store_button.click()
        return 1

    monkeypatch.setattr(rendered_editor.RenderedFileEditor, "exec_", fake_exec)

    window = loaded_window
    window.recipes_screen.edit_rendered_requested.emit("rc-coupled-typical")
    # No explicit save: storing the edit is what writes it now, and a second
    # save has nothing left to do.
    assert window.save() is False
    assert window.controller.is_dirty is False

    window.controller.reload()
    reloaded = window.controller.recipe("rc-coupled-typical")
    assert reloaded.manual_edit_count == 1
    assert reloaded.patches[0].hunks[0].after.strip() == "; typed by hand"


# ---- the init wizard --------------------------------------------------------


def test_the_file_menu_opens_the_wizard(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[InitProjectWizard] = []
    monkeypatch.setattr(
        InitProjectWizard, "exec_", lambda self: opened.append(self) or 0
    )
    action = _find_action(window, "New project")
    assert action is not None
    action.trigger()
    assert len(opened) == 1


def test_the_cells_empty_state_opens_the_wizard(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[InitProjectWizard] = []
    monkeypatch.setattr(
        InitProjectWizard, "exec_", lambda self: opened.append(self) or 0
    )
    window.cells_screen.import_requested.emit()
    assert len(opened) == 1


@pytest.mark.parametrize(
    "answer, wizard_opens, still_dirty",
    [
        (QMessageBox.Save, True, False),
        (QMessageBox.Discard, True, False),
        (QMessageBox.Cancel, False, True),
    ],
)
def test_opening_the_wizard_with_pending_edits_asks_first(
    loaded_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    answer,
    wizard_opens: bool,
    still_dirty: bool,
) -> None:
    window = loaded_window
    window.cells_screen.add_cell()
    assert window.controller.is_dirty is True

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer)
    opened: list[InitProjectWizard] = []
    monkeypatch.setattr(
        InitProjectWizard, "exec_", lambda self: opened.append(self) or 0
    )

    _find_action(window, "New project").trigger()

    assert bool(opened) is wizard_opens
    assert window.controller.is_dirty is still_dirty


def test_an_edit_the_patch_format_cannot_anchor_is_refused_in_plain_words(
    loaded_window: MainWindow,
    monkeypatch: pytest.MonkeyPatch,
    profile_env,
    picks_first_file,
    dialogs,
) -> None:
    """A line appended past the end of the file has no context after it.

    The refusal comes from the patch layer as a pydantic validation dump; the
    window must not show that to an RFIC engineer.
    """

    from auto_ext.ui.widgets import rendered_editor

    monkeypatch.setattr(
        rendered_editor.RenderedFileEditor,
        "exec_",
        lambda self: (
            self.set_text(self.edited_text() + "; appended at the very end\n"),
            self._store_button.click(),
            1,
        )[-1],
    )
    loaded_window.recipes_screen.edit_rendered_requested.emit("rc-coupled-typical")

    assert dialogs, "the refusal must be visible"
    _kind, title, message = dialogs[-1]
    assert title == "Could not store the edit"
    assert "validation error" not in message
    assert "above the final line" in message
    assert loaded_window.controller.is_dirty is False


# ---- the Project screen -----------------------------------------------------


def test_a_loaded_project_reaches_the_project_screen(loaded_window: MainWindow) -> None:
    screen = loaded_window.project_screen
    controller = loaded_window.controller
    assert screen.workspace() == controller.workspace
    assert screen.profile() == controller.profile
    # loading is not an edit, here as well as on the screen alone
    assert screen.is_dirty() is False
    assert controller.is_dirty is False


def test_the_profile_picker_lists_the_directorys_profiles(
    loaded_window: MainWindow,
) -> None:
    combo = loaded_window.project_screen.row("pdk_profile").control()
    listed = {combo.itemText(i) for i in range(combo.count())}
    controller = loaded_window.controller
    on_disk = {p.stem for p in controller.profiles_dir.glob("*.yaml")}
    assert on_disk <= listed
    assert controller.workspace.pdk_profile in listed


def test_saving_the_project_screen_writes_the_profile(
    loaded_window: MainWindow, profile_env
) -> None:
    """End to end: type, Save, and the YAML on disk says so."""

    screen = loaded_window.project_screen
    row = screen.row("display_name")
    row.control().setText("Renamed by the Project screen")
    row.commit()
    assert screen.is_dirty() is True

    screen._save_btn.click()

    assert loaded_window.errors == [], loaded_window.errors
    assert loaded_window.controller.is_dirty is False
    text = loaded_window.controller.profile_path.read_text(encoding="utf-8")
    assert "Renamed by the Project screen" in text


def test_a_profile_only_edit_leaves_workspace_yaml_alone(
    loaded_window: MainWindow, profile_env
) -> None:
    """Rewriting a file nobody edited is how a round trip loses its comments."""

    controller = loaded_window.controller
    workspace_path = controller.config_dir / "workspace.yaml"
    before = workspace_path.read_bytes()
    mtime = workspace_path.stat().st_mtime_ns

    row = loaded_window.project_screen.row("display_name")
    row.control().setText("Renamed again")
    row.commit()
    loaded_window.project_screen._save_btn.click()

    assert loaded_window.errors == [], loaded_window.errors
    assert workspace_path.read_bytes() == before
    assert workspace_path.stat().st_mtime_ns == mtime


def test_reverting_the_project_screen_restores_what_is_on_disk(
    loaded_window: MainWindow,
) -> None:
    screen = loaded_window.project_screen
    loaded = loaded_window.controller.profile.display_name

    row = screen.row("display_name")
    row.control().setText("about to be thrown away")
    row.commit()
    assert screen.is_dirty() is True

    screen._revert_btn.click()

    assert screen.is_dirty() is False
    assert screen.profile().display_name == loaded
    assert screen.row("display_name").value() == loaded


def test_an_env_pinned_from_setup_survives_a_project_revert(
    loaded_window: MainWindow,
) -> None:
    """Both write into the same object, so Revert has to mean both.

    Pinning stages a profile on the controller; the screen holds a working
    copy. A Revert that only re-pushed would leave the staged pin behind and
    the window would still be dirty with nothing on screen to show for it.
    """

    screen = loaded_window.project_screen
    on_disk = dict(screen.profile().env_overrides)

    loaded_window.setup_drawer.override_requested.emit("SETUP_ROOT", "/pinned")
    assert loaded_window.controller.is_dirty is True
    assert screen.profile().env_overrides["SETUP_ROOT"] == "/pinned"
    assert screen.is_dirty() is True

    screen._revert_btn.click()

    assert loaded_window.controller.is_dirty is False
    assert screen.is_dirty() is False
    assert screen.profile().env_overrides == on_disk


def test_a_pin_from_the_setup_drawer_shows_up_on_the_project_screen(
    loaded_window: MainWindow, profile_env
) -> None:
    """The two write paths must agree about what the profile now says."""

    loaded_window.setup_drawer.override_requested.emit("SETUP_ROOT", "/pinned")
    loaded_window.save()

    assert loaded_window.errors == [], loaded_window.errors
    row = loaded_window.project_screen.row("env_overrides")
    assert row.value().get("SETUP_ROOT") == "/pinned"


def test_the_setup_drawer_can_open_the_field_a_check_is_about(
    loaded_window: MainWindow,
) -> None:
    loaded_window.shell.set_setup_open(True)
    loaded_window.setup_drawer.edit_field_requested.emit("lvs_decks.dir_expr")

    assert loaded_window.shell.current_page_key() == "project"
    assert loaded_window.shell.is_setup_open() is False


def test_a_field_the_project_screen_cannot_show_is_reported(
    loaded_window: MainWindow,
) -> None:
    """A drifted CHECK_FIELDS entry must be loud, not a no-op."""

    loaded_window.setup_drawer.edit_field_requested.emit("nope.not.a.field")
    assert "nope.not.a.field" in loaded_window.shell.status_left()


def test_switching_project_from_the_picker_loads_it(
    loaded_window: MainWindow, tmp_path: Path, v2_config_dir: Path
) -> None:
    """The picker is a second door onto the same load path as File -> Open."""

    import shutil

    other = tmp_path / "other_project"
    shutil.copytree(v2_config_dir, other)
    other_config = other / "config"

    loaded_window.set_known_projects(
        [
            ("this", str(v2_config_dir / "config")),
            ("other", str(other_config)),
        ]
    )
    loaded_window.project_screen.project_chosen.emit(str(other_config))

    assert loaded_window.errors == [], loaded_window.errors
    assert loaded_window.controller.config_dir == other_config


def test_switching_project_with_unsaved_edits_asks_first(
    loaded_window: MainWindow, tmp_path: Path, v2_config_dir: Path, dialogs
) -> None:
    """Same guard as File -> Open: the question fixture answers Cancel."""

    row = loaded_window.project_screen.row("display_name")
    row.control().setText("unsaved")
    row.commit()
    assert loaded_window.controller.is_dirty is True

    before = loaded_window.controller.config_dir
    loaded_window.project_screen.project_chosen.emit(str(tmp_path / "nowhere"))

    assert loaded_window.controller.config_dir == before
    assert any(kind == "question" for kind, _t, _m in dialogs)
