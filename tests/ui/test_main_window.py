"""MainWindow integration tests (Phase 5.7).

First test file at the MainWindow level. Two cases pin the init-wizard
entry points: the File menu action, and the RunTab empty-state banner
button. Three more cases cover the Q5 dirty-controller branch
(Save / Discard / Cancel) when the user opens the wizard while the
project has unsaved edits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QMessageBox, QPushButton  # noqa: E402

from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.widgets.init_wizard import InitProjectWizard  # noqa: E402


def _find_action(window: MainWindow, text_contains: str):
    for menu in window.menuBar().findChildren(type(window.menuBar().addMenu("__"))):
        for action in menu.actions():
            if text_contains in action.text():
                return action
    # Fallback: walk all actions on the menu bar tree.
    for action in window.menuBar().actions():
        sub = action.menu()
        if sub is None:
            continue
        for a in sub.actions():
            if text_contains in a.text():
                return a
    return None


def test_main_window_menu_new_project_opens_wizard(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    opened: list[InitProjectWizard] = []

    real_exec = InitProjectWizard.exec_

    def fake_exec(self):
        opened.append(self)
        # Don't actually start the modal event loop — just record + close.
        return 0

    monkeypatch.setattr(InitProjectWizard, "exec_", fake_exec)

    action = _find_action(window, "New project")
    assert action is not None, "File → New project action missing"
    action.trigger()

    assert len(opened) == 1
    assert isinstance(opened[0], InitProjectWizard)


def test_main_window_run_tab_banner_button_opens_wizard(
    qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = MainWindow()
    qtbot.addWidget(window)

    opened: list[InitProjectWizard] = []

    def fake_exec(self):
        opened.append(self)
        return 0

    monkeypatch.setattr(InitProjectWizard, "exec_", fake_exec)

    # No config loaded → banner is visible (not isHidden — Qt only sets
    # isVisible after the widget is actually shown on screen).
    run_tab = window._run_tab
    assert not run_tab._empty_banner.isHidden()
    new_btn = None
    for btn in run_tab._empty_banner.findChildren(QPushButton):
        if "新建" in btn.text():
            new_btn = btn
            break
    assert new_btn is not None, "新建项目 button missing in empty-state banner"
    new_btn.click()

    assert len(opened) == 1


# ---- Q5 dirty-controller branch ------------------------------------------


def _make_window_with_dirty_controller(
    qtbot, project_tools_config: Path
) -> MainWindow:
    """Build a MainWindow whose controller has at least one staged edit.

    Uses the existing ``project_tools_config`` fixture (loads a real
    project.yaml + tasks.yaml) and then calls ``stage_edits`` to flip
    ``is_dirty`` to True without touching disk.
    """
    window = MainWindow()
    qtbot.addWidget(window)
    window._controller.load(project_tools_config)
    window._controller.stage_edits({"employee_id": "bob"})
    assert window._controller.is_dirty
    return window


def test_main_window_open_wizard_dirty_save(
    qtbot, monkeypatch: pytest.MonkeyPatch, project_tools_config: Path
) -> None:
    window = _make_window_with_dirty_controller(qtbot, project_tools_config)

    save_calls: list[bool] = []
    monkeypatch.setattr(
        type(window._controller),
        "save",
        lambda self, **kw: (save_calls.append(True) or True),
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.Save),
    )

    opened: list[InitProjectWizard] = []
    monkeypatch.setattr(
        InitProjectWizard,
        "exec_",
        lambda self: (opened.append(self) or 0),
    )

    window._open_init_wizard()

    assert save_calls == [True], "controller.save must run when user picks Save"
    assert len(opened) == 1, "wizard must open after a successful save"


def test_main_window_open_wizard_dirty_discard(
    qtbot, monkeypatch: pytest.MonkeyPatch, project_tools_config: Path
) -> None:
    window = _make_window_with_dirty_controller(qtbot, project_tools_config)

    save_calls: list[bool] = []
    monkeypatch.setattr(
        type(window._controller),
        "save",
        lambda self, **kw: (save_calls.append(True) or True),
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.Discard),
    )

    opened: list[InitProjectWizard] = []
    monkeypatch.setattr(
        InitProjectWizard,
        "exec_",
        lambda self: (opened.append(self) or 0),
    )

    window._open_init_wizard()

    assert save_calls == [], "Discard must NOT call controller.save"
    assert len(opened) == 1, "wizard must still open after Discard"


def test_main_window_open_wizard_dirty_cancel(
    qtbot, monkeypatch: pytest.MonkeyPatch, project_tools_config: Path
) -> None:
    window = _make_window_with_dirty_controller(qtbot, project_tools_config)

    save_calls: list[bool] = []
    monkeypatch.setattr(
        type(window._controller),
        "save",
        lambda self, **kw: (save_calls.append(True) or True),
    )

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.Cancel),
    )

    opened: list[InitProjectWizard] = []
    monkeypatch.setattr(
        InitProjectWizard,
        "exec_",
        lambda self: (opened.append(self) or 0),
    )

    window._open_init_wizard()

    assert save_calls == [], "Cancel must NOT save"
    assert opened == [], "Cancel must NOT open the wizard"
