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
        if "New project" in btn.text():
            new_btn = btn
            break
    assert new_btn is not None, "New project button missing in empty-state banner"
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


# ---- Feature #4: Log tab merged into Run tab ---------------------------


def test_feature4_main_window_has_four_tabs(qtbot) -> None:
    """Feature #4 dropped the standalone Log tab in favour of an embedded
    LogTab inside RunTab. MainWindow now exposes 4 tabs, not 5.
    """
    window = MainWindow()
    qtbot.addWidget(window)

    assert window._tabs.count() == 4
    titles = [window._tabs.tabText(i) for i in range(window._tabs.count())]
    assert "Log" not in titles, (
        "Standalone Log tab must be removed; the log viewer lives "
        "inside the Run tab now."
    )
    # All four expected tabs are still present.
    assert set(titles) == {"Run", "Project", "Tasks", "Templates"}


def test_feature4_run_tab_owns_embedded_log_tab(qtbot) -> None:
    """RunTab now owns its own LogTab and wires stage_selected straight
    into ``set_active_log``. MainWindow no longer holds a separate
    LogTab reference."""
    from auto_ext.ui.tabs.log_tab import LogTab

    window = MainWindow()
    qtbot.addWidget(window)

    assert hasattr(window._run_tab, "_log_tab")
    assert isinstance(window._run_tab._log_tab, LogTab)
    # MainWindow no longer holds a top-level _log_tab attribute.
    assert not hasattr(window, "_log_tab")


def test_feature4_stage_selected_drives_embedded_log_tab(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """Emitting ``stage_selected`` from the Run tab must route the path
    into the embedded LogTab's ``set_active_log``. The connection lives
    inside RunTab._build_ui — this test pins it so a future refactor
    can't silently disconnect the two."""
    from auto_ext.ui.config_controller import ConfigController
    from auto_ext.ui.tabs.run_tab import RunTab

    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)

    task_id = controller.tasks[0].task_id
    log_path = ae_root / "logs" / f"task_{task_id}" / "calibre.log"

    # No log selected yet.
    assert tab._log_tab._path is None

    # Emit and check that the embedded LogTab took the path.
    tab.stage_selected.emit(log_path)
    assert tab._log_tab._path == log_path


# ---- TaskSpec.label → LogTab header rendering (Features #2 + #4) ---------


def test_log_tab_header_includes_label_when_set(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """End-to-end: when a labelled spec is loaded and stage_selected
    fires, the embedded LogTab header reads ``"<label> — <path>"``.
    RunTab's internal slot threads the display value via
    :meth:`RunTab.display_for_log_path` so the public
    ``stage_selected`` signal payload stays a bare ``Path``."""
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "  label: Pretty Display\n",
        encoding="utf-8",
    )
    ae_root = tmp_path / "pr"
    window = MainWindow(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    qtbot.addWidget(window)
    window._controller.load(project_tools_config)

    task_id = "WB_PLL_DCO__inv__layout__schematic"
    log_path = ae_root / "logs" / f"task_{task_id}" / "calibre.log"
    window._run_tab.stage_selected.emit(log_path)

    header = window._run_tab._log_tab._header.text()
    assert "Pretty Display" in header
    assert "calibre.log" in header


def test_log_tab_header_uses_task_id_when_label_unset(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """No label → header shows the canonical task_id verbatim
    (existing behaviour unchanged)."""
    ae_root = tmp_path / "pr"
    window = MainWindow(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    qtbot.addWidget(window)
    window._controller.load(project_tools_config)

    task_id = "WB_PLL_DCO__inv__layout__schematic"
    log_path = ae_root / "logs" / f"task_{task_id}" / "calibre.log"
    window._run_tab.stage_selected.emit(log_path)

    header = window._run_tab._log_tab._header.text()
    assert task_id in header


def test_log_tab_set_active_log_display_id_default_none(qtbot, tmp_path: Path) -> None:
    """``LogTab.set_active_log`` keeps the legacy 1-arg shape: when
    ``display_id`` is omitted/None, the header is just the path."""
    from auto_ext.ui.tabs.log_tab import LogTab

    log = LogTab()
    qtbot.addWidget(log)
    p = tmp_path / "out.log"
    log.set_active_log(p)
    assert log._header.text() == str(p)
    # Now with an explicit display_id, the header gains the prefix.
    log.set_active_log(p, "FANCY")
    header = log._header.text()
    assert header.startswith("FANCY — ")
    assert str(p) in header
