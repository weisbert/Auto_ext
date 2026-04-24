"""Top-level :class:`QMainWindow` with 5 tabs.

Owns the :class:`RunTab` and :class:`LogTab`; wires the Run tab's
``stage_selected`` signal into the Log tab so clicking a stage in the
status tree switches the log viewer.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QMainWindow, QTabWidget

from auto_ext.ui.tabs.log_tab import LogTab
from auto_ext.ui.tabs.project_tab import ProjectTab
from auto_ext.ui.tabs.run_tab import RunContext, RunTab
from auto_ext.ui.tabs.tasks_tab import TasksTab
from auto_ext.ui.tabs.templates_tab import TemplatesTab


class MainWindow(QMainWindow):
    def __init__(
        self,
        *,
        config_dir: Path | None = None,
        auto_ext_root: Path | None = None,
        workarea: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Auto_ext")
        self.resize(1280, 800)

        self._ctx = RunContext(
            config_dir=config_dir,
            auto_ext_root=auto_ext_root,
            workarea=workarea,
        )

        tabs = QTabWidget(self)
        self._run_tab = RunTab(self._ctx, tabs)
        self._log_tab = LogTab(tabs)
        self._project_tab = ProjectTab(tabs)
        self._tasks_tab = TasksTab(tabs)
        self._templates_tab = TemplatesTab(tabs)

        tabs.addTab(self._run_tab, "Run")
        tabs.addTab(self._log_tab, "Log")
        tabs.addTab(self._project_tab, "Project")
        tabs.addTab(self._tasks_tab, "Tasks")
        tabs.addTab(self._templates_tab, "Templates")

        self.setCentralWidget(tabs)
        self._tabs = tabs

        # Run tab selects a stage → Log tab switches the file + focus
        # jumps to the Log tab so the user sees it without manual nav.
        self._run_tab.stage_selected.connect(self._on_stage_selected)

    def _on_stage_selected(self, log_path: Path | None) -> None:
        self._log_tab.set_active_log(log_path)
        if log_path is not None:
            self._tabs.setCurrentWidget(self._log_tab)
