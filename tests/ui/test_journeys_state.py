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
still shows the new one, closing throws it away without a word, and a pin
typed into the Setup drawer is thrown away by the very Re-check that is the
natural next click.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QLineEdit, QMessageBox, QPushButton  # noqa: E402

from auto_ext.ui.main_window import MainWindow  # noqa: E402

#: A variable no shell exports, so the check for it always starts red.
PROBE_VAR = "AUTO_EXT_PROBE_ROOT"
PROBE_CHECK = "env.auto_ext_probe_root"


@pytest.fixture
def window(qtbot, v2_config_dir: Path, isolated_recipe_path: Path) -> MainWindow:
    win = MainWindow(config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir)
    qtbot.addWidget(win)
    return win


@pytest.fixture
def window_missing_env(
    qtbot,
    v2_config_dir: Path,
    isolated_recipe_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> MainWindow:
    """A window whose project needs one environment variable nobody exports.

    That is the state the pin row exists for, and the fixture project is
    otherwise deliberately healthy -- with no failing ``env.*`` check there
    is no pin box on screen at all.
    """

    from auto_ext.core.profile_discover import read_profile_yaml, write_profile_yaml

    monkeypatch.delenv(PROBE_VAR, raising=False)
    path = next((v2_config_dir / "config" / "profiles").glob("*.yaml"))
    profile = read_profile_yaml(path)
    write_profile_yaml(path, profile.model_copy(update={"required_env": [PROBE_VAR]}))

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


def _pin_row(window: MainWindow, check_id: str):
    """``(line edit, Set button)`` of one Setup check, as rendered."""

    row = window.setup_drawer.row_widget(check_id)
    assert row is not None, f"{check_id} is not on screen"
    edit = next(iter(row.findChildren(QLineEdit)))
    pin = next(b for b in row.findChildren(QPushButton) if b.text() == "Set")
    return edit, pin


def _recheck(window: MainWindow) -> None:
    drawer = window.setup_drawer
    button = next(
        b for b in drawer.findChildren(QPushButton) if b.text() == "Re-check"
    )
    button.click()


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


# ---- the pin nobody could keep ----------------------------------------------


def test_a_path_typed_in_setup_survives_the_recheck_button(
    window_missing_env: MainWindow, qtbot
) -> None:
    """"在 Setup 里填了路径，按 Re-check 想看看好没好，框又空了."""

    window = window_missing_env
    _menu_action(window, "&Setup drawer").trigger()

    edit, _pin = _pin_row(window, PROBE_CHECK)
    qtbot.keyClicks(edit, "/pdk/probe/root")

    _recheck(window)

    edit, _pin = _pin_row(window, PROBE_CHECK)
    assert edit.text() == "/pdk/probe/root"
