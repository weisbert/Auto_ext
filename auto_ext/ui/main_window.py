"""Top-level :class:`QMainWindow` with 5 tabs.

Owns the shared :class:`ConfigController` so the Run, Runs and Project
tabs see the same loaded ``project.yaml`` + ``tasks.yaml``. The log
viewer used to live in its own top-level tab; Feature #4 embedded it
under the Run tab's status tree (RunTab owns its own :class:`LogTab`
and wires ``stage_selected`` straight into it), so the standalone
"Log" tab is gone.

S1 added the :class:`RunsTab` immediately after Run: the Run tab is
"what is happening now", the Runs tab is "what happened before". They
are adjacent because that is the order the user moves through them --
a run finishes, and the next thing wanted is its result card. The
worker-lifecycle signal drives that hand-off: when the Run tab's
worker ends, the history is re-read so the run that just finished is
already listed.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.tabs.project_tab import ProjectTab
from auto_ext.ui.tabs.run_tab import RunTab
from auto_ext.ui.tabs.runs_tab import RunsTab
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
        self._runs_tab = RunsTab(self._controller, tabs)
        self._project_tab = ProjectTab(self._controller, self._run_tab, tabs)
        self._tasks_tab = TasksTab(self._controller, self._run_tab, tabs)
        self._templates_tab = TemplatesTab(self._controller, self._run_tab, tabs)

        tabs.addTab(self._run_tab, "Run")
        tabs.addTab(self._runs_tab, "Runs")
        tabs.addTab(self._project_tab, "Project")
        tabs.addTab(self._tasks_tab, "Tasks")
        tabs.addTab(self._templates_tab, "Templates")

        self.setCentralWidget(tabs)
        self._tabs = tabs

        self._build_menus()

        self._run_tab.request_init_wizard.connect(self._open_init_wizard)

        # A run that just ended is exactly the one the user wants to look
        # at, so re-read the history the moment the worker releases.
        self._run_tab.worker_state_changed.connect(self._on_worker_state_changed)

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

    def _on_worker_state_changed(self, active: bool) -> None:
        """Refresh the run history when a run finishes.

        Only on the falling edge: while a worker is in flight the run
        directory is still being written, and re-reading it every time a
        stage starts would fight the user's selection for no benefit.
        """

        if not active:
            self._runs_tab.refresh()

    def _on_dirty_changed(self, dirty: bool) -> None:
        suffix = " *" if dirty else ""
        self.setWindowTitle(f"{self._TITLE_BASE}{suffix}")
