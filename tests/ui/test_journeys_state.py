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
natural next click, and is invisible to the verdict even when it survives.

The last one here is the mirror image: a save that fails half way leaves the
files it *did* write looking, to the very next question the window asks,
like somebody else's edit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QLineEdit, QMessageBox, QPushButton  # noqa: E402

from auto_ext.model.pdk import CheckStatus  # noqa: E402
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


def test_a_pinned_variable_makes_the_check_pass_on_the_next_recheck(
    window_missing_env: MainWindow, qtbot
) -> None:
    """"按提示 pin 了变量，按 Re-check，还是说变量缺失."

    The pin is staged, not written -- pressing Set must not rewrite the PDK
    profile behind the user's back. But the verdict read the *loaded* profile
    directly, so the one edit the drawer exists to make was the one edit it
    could not see, and Re-check kept reporting the variable missing until the
    whole project had been saved.
    """

    window = window_missing_env
    _menu_action(window, "&Setup drawer").trigger()

    drawer = window.setup_drawer
    report = drawer.report()
    assert report is not None
    before = {r.check_id: r.status for r in report.results}
    assert before[PROBE_CHECK] is CheckStatus.FAIL

    edit, pin = _pin_row(window, PROBE_CHECK)
    qtbot.keyClicks(edit, "/pdk/probe/root")
    pin.click()

    _recheck(window)

    report = drawer.report()
    assert report is not None
    after = {r.check_id: r.status for r in report.results}
    assert after[PROBE_CHECK] is CheckStatus.OK, after
    # the row is no longer a problem row, so it no longer offers a pin box
    row = drawer.row_widget(PROBE_CHECK)
    assert row is not None
    assert row.findChildren(QLineEdit) == []


# ---- the write error that claimed to be a conflict ---------------------------


def test_a_refused_write_is_reported_as_a_write_error_not_a_conflict(
    window: MainWindow, v2_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"保存失败，却说'别人改了我的文件'；一半写进去了一半没有."

    Two documents staged, the second one impossible to write. The first was
    already on disk when the failure arrived, which made the window's own
    "did anything move underneath us?" question answer yes -- about our own
    writing -- and the user was handed an overwrite-the-other-person's-edits
    dialog for what was a permission error.
    """

    warnings: list[tuple[str, str]] = []
    questions: list[str] = []

    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _p, title, text, *a, **k: warnings.append((title, text))),
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda _p, title, *a, **k: (
                questions.append(title),
                QMessageBox.Cancel,
            )[1]
        ),
    )

    profile_path = window.controller.profile_path
    profile_before = profile_path.read_bytes()
    recipe_path = next((v2_config_dir / "recipes").glob("*.yaml"))
    recipe_before = recipe_path.read_bytes()

    # one edit on each of two documents: the profile writes first (the queue
    # is written in id order), the recipe refuses.
    row = window.project_screen.row("display_name")
    row.control().setText("Renamed while the disk says no")
    row.commit()
    recipes = window.shell.page("recipes")
    editor = recipes.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")

    real_write_text = Path.write_text

    def refuse_the_recipe(self, *args, **kwargs):
        if self == recipe_path:
            raise PermissionError(13, "Permission denied", str(self))
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", refuse_the_recipe)

    _menu_action(window, "&Save").trigger()

    assert questions == [], "a write error was reported as an external conflict"
    assert warnings, "the write failure was never shown"
    title, text = warnings[-1]
    assert title == "Save failed"
    assert "write failed" in text
    assert profile_path.read_bytes() == profile_before
    assert recipe_path.read_bytes() == recipe_before
