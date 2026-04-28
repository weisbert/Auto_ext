"""Top-level :class:`QMainWindow` with 4 tabs (Feature #4).

Owns the shared :class:`ConfigController` so the Run and Project tabs
see the same loaded ``project.yaml`` + ``tasks.yaml``. The log viewer
used to live in its own top-level tab; Feature #4 embedded it under
the Run tab's status tree (RunTab owns its own :class:`LogTab` and
wires ``stage_selected`` straight into it), so the standalone "Log"
tab is gone.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.tabs.project_tab import ProjectTab
from auto_ext.ui.tabs.run_tab import RunTab
from auto_ext.ui.tabs.tasks_tab import TasksTab
from auto_ext.ui.tabs.templates_tab import TemplatesTab


class MainWindow(QMainWindow):
    _TITLE_BASE = "Auto_ext"

    def __init__(
        self,
        *,
        config_dir: Path | None = None,
        auto_ext_root: Path | None = None,
        workarea: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(self._TITLE_BASE)
        self.resize(1280, 800)

        self._controller = ConfigController(
            auto_ext_root=auto_ext_root,
            workarea=workarea,
            parent=self,
        )
        self._controller.dirty_changed.connect(self._on_dirty_changed)

        tabs = QTabWidget(self)
        self._run_tab = RunTab(self._controller, tabs)
        self._project_tab = ProjectTab(self._controller, self._run_tab, tabs)
        self._tasks_tab = TasksTab(self._controller, self._run_tab, tabs)
        self._templates_tab = TemplatesTab(self._controller, self._run_tab, tabs)

        tabs.addTab(self._run_tab, "Run")
        tabs.addTab(self._project_tab, "Project")
        tabs.addTab(self._tasks_tab, "Tasks")
        tabs.addTab(self._templates_tab, "Templates")

        self.setCentralWidget(tabs)
        self._tabs = tabs

        self._build_menus()

        self._run_tab.request_init_wizard.connect(self._open_init_wizard)

        # Templates tab cloned a new .j2 → Tasks tab should refresh
        # its per-stage template combos so the new file appears
        # without a manual re-select / restart (Feature #1).
        self._templates_tab.templates_changed.connect(
            self._tasks_tab.refresh_template_combos
        )

        if config_dir is not None:
            self._controller.load(config_dir)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_action = file_menu.addAction("&New project from raws…")
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._open_init_wizard)

    def _open_init_wizard(self) -> None:
        from auto_ext.ui.widgets.init_wizard import InitProjectWizard

        if self._controller.is_dirty:
            choice = QMessageBox.question(
                self,
                "Unsaved changes",
                "The current project has unsaved changes. Save them first?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if choice == QMessageBox.Cancel:
                return
            if choice == QMessageBox.Save:
                if not self._controller.save():
                    return

        dlg = InitProjectWizard(controller=self._controller, parent=self)
        dlg.accepted_with_load.connect(self._controller.load)
        dlg.exec_()

    def _on_dirty_changed(self, dirty: bool) -> None:
        suffix = " *" if dirty else ""
        self.setWindowTitle(f"{self._TITLE_BASE}{suffix}")
