"""End-to-end journeys, driven the way a user drives them.

The rule that makes this file worth having
------------------------------------------
**A journey test may only touch what a user can touch, and may only assert on
what a user can see.** No private attribute, no controller method, no
``screen._working``, no "assert the signal fired". Click the button; type in
the box; then read the file on disk.

That rule is not stylistic. The Recipes screen's ``Save`` button shipped
staging its recipe into a queue and writing nothing -- so an edit made in the
GUI came back missing on the next launch -- and it shipped with tests around
it that passed, because they asserted ``save_requested`` was emitted and that
the controller had been staged. Both were true. Both are what the bug looks
like. The only assertion that could tell the difference is the one that reads
the bytes afterwards, and nothing in the suite made it.

So the tests here are few, slow and blunt on purpose. They cover the seam
between "the widget did its part" and "the user got what they asked for",
which is exactly the seam a widget test cannot see across.

See ``tests/ui/test_reachability.py`` for the other half of the answer -- can
the user find the control at all -- and ``docs/refactor/UX_VALIDATION.md`` for
the part neither file can automate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")

from auto_ext.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, v2_config_dir: Path, isolated_recipe_path: Path) -> MainWindow:
    win = MainWindow(config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir)
    qtbot.addWidget(win)
    return win


def _sole_recipe_file(v2_config_dir: Path) -> Path:
    files = sorted((v2_config_dir / "recipes").glob("*.yaml"))
    assert files, "the fixture project has no recipe to edit"
    return files[0]


# ---- the journey the office session actually took ---------------------------


def test_change_a_value_press_save_and_the_file_on_disk_has_it(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """"我在GUI里面更改了温度，然后点击保存，结果并没有被写入."

    The whole point is the last two lines: the assertion is against the file,
    not against a signal, a staged document or a dirty flag.
    """

    path = _sole_recipe_file(v2_config_dir)
    screen = window.shell.page("recipes")

    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")

    screen.save_button().click()

    assert "temperature_c: 125.0" in path.read_text(encoding="utf-8")


def test_rename_a_recipe_press_save_and_the_file_on_disk_has_it(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """"recipes的名字也没有改" -- there was no control at all; now there is."""

    path = _sole_recipe_file(v2_config_dir)
    screen = window.shell.page("recipes")

    screen.name_edit().setText("RC coupled 125C")
    screen.name_edit().textEdited.emit("RC coupled 125C")

    screen.save_button().click()

    assert "name: RC coupled 125C" in path.read_text(encoding="utf-8")


def test_saving_leaves_the_screen_clean_and_on_the_same_recipe(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """A Save that works has to *look* like it worked.

    The staging-only bug was doubly invisible: nothing was written, and the
    header went on saying ``unsaved`` afterwards, so the only feedback the
    button gave was none at all.
    """

    screen = window.shell.page("recipes")
    before = screen.current_recipe().recipe_id

    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    assert screen.save_button().isEnabled(), "an edited recipe must offer Save"

    screen.save_button().click()

    assert screen.save_button().isEnabled() is False, "still dirty after a save"
    assert screen.current_recipe().recipe_id == before, "the selection jumped"


def test_an_edit_survives_a_reload_of_the_project(
    window: MainWindow, v2_config_dir: Path, qtbot
) -> None:
    """The user's actual test: "我下次打开还是这样".

    Reloading from disk is the closest thing to relaunching the app that a
    test can do without a second process, and it fails for the same reason a
    relaunch did.
    """

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    screen.save_button().click()

    second = MainWindow(config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir)
    qtbot.addWidget(second)
    reopened = second.shell.page("recipes")

    assert reopened.editor("temperature_c").value() == 125.0


def test_an_unsaved_edit_shows_up_as_unsaved_everywhere(
    window: MainWindow,
) -> None:
    """One dirty state, not two.

    The screen used to be the only thing that knew about an option edit: it
    said "unsaved" while the window title had no star and File -> Save was
    greyed out. Three places claiming to report the same fact, two of them
    wrong, is how an edit gets closed away.
    """

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")

    assert window.controller.is_dirty is True
    assert window.windowTitle().endswith("*")
    assert "unsaved" in window.shell.status_right()


def test_revert_actually_puts_the_old_value_back(window: MainWindow) -> None:
    """Revert reads from disk, not from the queue it is undoing."""

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    before = editor.value()

    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    assert screen.editor("temperature_c").value() == 125.0

    screen.revert_button().click()

    assert screen.editor("temperature_c").value() == before
    assert window.controller.is_dirty is False


# ---- closing the window -----------------------------------------------------


def _quit_action(window: MainWindow):
    from PyQt5.QtWidgets import QMenu

    for menu in window.menuBar().findChildren(QMenu):
        for action in menu.actions():
            if "Quit" in action.text():
                return action
    raise AssertionError("File -> Quit is gone")


def test_quitting_with_unsaved_edits_asks_instead_of_dropping_them(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There was no close guard at all, so the answer was silently "drop".

    Same complaint as the Save button, one step later: the user makes an
    edit, closes the window, and next launch it is not there.
    """

    from PyQt5.QtWidgets import QMessageBox

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    assert window.controller.is_dirty

    asked: list[str] = []

    def fake_question(_parent, title, *_args, **_kwargs):
        asked.append(title)
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))

    _quit_action(window).trigger()

    assert asked, "the window quit without asking"
    assert window.isVisible() is False or window.controller.is_dirty
    assert window.controller.is_dirty, "Cancel must not discard the edit"


def test_quitting_clean_asks_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PyQt5.QtWidgets import QMessageBox

    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.Cancel),
    )

    assert window.request_close() is True
    assert asked == []


def test_quitting_during_a_run_asks_and_stops_the_thread(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quitting mid-run used to drop the last reference to a live QThread."""

    from PyQt5.QtWidgets import QMessageBox

    from auto_ext.ui.screens.cells_screen import CellsScreen

    stopped: list[bool] = []
    monkeypatch.setattr(CellsScreen, "is_running", lambda self: True)
    monkeypatch.setattr(
        CellsScreen,
        "stop_run_and_wait",
        lambda self, timeout_ms=5000: stopped.append(True) or True,
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Close)
    )

    assert window.request_close() is True
    assert stopped == [True], "the run was never actually stopped"


def test_a_run_the_user_will_not_abandon_keeps_the_window_open(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PyQt5.QtWidgets import QMessageBox

    from auto_ext.ui.screens.cells_screen import CellsScreen

    monkeypatch.setattr(CellsScreen, "is_running", lambda self: True)
    monkeypatch.setattr(
        CellsScreen,
        "stop_run_and_wait",
        lambda self, timeout_ms=5000: pytest.fail("the run must not be touched"),
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel)
    )

    assert window.request_close() is False


# ---- the cell-side journey --------------------------------------------------


def test_type_an_extracted_view_into_the_cells_table_and_it_reaches_the_file(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """"没有更改ext输出的文件名称的" -- out_file was tooltip-only."""

    from auto_ext.ui.screens.cells_screen import COL_OUT_VIEW

    cells = window.shell.page("cells")
    table = cells.table
    item = table.item(0, COL_OUT_VIEW)
    assert item is not None, "the extracted view has no cell to type into"

    item.setText("av_ext_125c")
    # The screen's own Save, not File -> Save: the cell table had no Save at
    # all, so the only way to write cells.yaml was a menu the user had no
    # reason to open.
    assert cells.save_button().isEnabled(), "an edited table must offer Save"
    cells.save_button().click()

    text = (v2_config_dir / "config" / "cells.yaml").read_text(encoding="utf-8")
    assert "av_ext_125c" in text
    assert cells.save_button().isEnabled() is False
