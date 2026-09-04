"""Journeys for the state a user has entered and not saved yet.

Same rule as ``tests/ui/test_journeys.py`` -- *touch only what a user can
touch, assert only what a user can see* -- pointed at one family of defects:
**edits that exist on screen and nowhere else**.

Everything downstream of an edit hangs off the moment the screen announces
it: the staged document, the star in the title, whether ``File -> Save``
writes the new value or the old one, whether closing the window asks. A
screen that only announces on focus-out therefore has a whole class of user
actions -- the keyboard ones -- that reach nothing at all, and every symptom
of that looks like a different bug: Save writes the previous value, the box
still shows the new one, closing throws it away without a word.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QMessageBox  # noqa: E402

from auto_ext.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, v2_config_dir: Path, isolated_recipe_path: Path) -> MainWindow:
    win = MainWindow(config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir)
    qtbot.addWidget(win)
    return win


def _menu_action(window: MainWindow, text: str):
    """The menu item a user would pick, by the label they read."""

    for top in window.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        for item in menu.actions():
            if item.text() == text:
                return item
    raise AssertionError(f"no menu item {text!r}")


# ---- the keyboard save -------------------------------------------------------


def test_type_a_path_press_ctrl_s_and_the_file_on_disk_has_it(
    window: MainWindow, v2_config_dir: Path, qtbot
) -> None:
    """"输入 DSPF 路径后按 Ctrl+S，写进去的是旧值，框里还是新值."

    The keystroke never leaves the box, because pressing the Save shortcut is
    not a reason to move focus. Nothing else in this journey is unusual: one
    field, one shortcut, one file.
    """

    control = window.project_screen.row("dspf_out_pattern").control()
    control.selectAll()
    qtbot.keyClicks(control, "${WORK_ROOT2}/{cell}_typed.dspf")

    _menu_action(window, "&Save").trigger()

    assert window.errors == [], window.errors
    text = (v2_config_dir / "config" / "workspace.yaml").read_text(encoding="utf-8")
    assert "{cell}_typed.dspf" in text
    # and the screen still shows what was saved, not a value that only the
    # box ever knew about
    assert (
        window.project_screen.row("dspf_out_pattern").value()
        == "${WORK_ROOT2}/{cell}_typed.dspf"
    )


def test_type_a_path_then_close_and_the_window_asks_first(
    window: MainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of it: closing threw the keystrokes away in silence."""

    asked: list[str] = []

    def _question(_parent, title, text, *_a, **_k):
        asked.append(title)
        return QMessageBox.Discard

    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))

    control = window.project_screen.row("intermediate_dir").control()
    control.selectAll()
    qtbot.keyClicks(control, "${WORK_ROOT2}/{run_slug}")

    assert window.request_close() is True
    assert asked == ["Unsaved changes"], "closing discarded the typed value"
