"""Run tab: pick tasks/stages, start a run, watch live status.

Owns:

- the QListWidget of loadable tasks (checkable),
- the stage checkbox row (all 5 EDA stages),
- the Jobs spinbox (1 = serial, ≥ 2 = parallel),
- Run / Cancel buttons,
- a QTreeWidget showing live per-task / per-stage status that updates
  in response to :class:`QtProgressReporter` signals,
- the :class:`RunWorker` lifecycle (one at a time).

Config state (``config_dir`` / ``project`` / ``tasks``) lives on the
shared :class:`ConfigController` so the Project tab sees the same
truth. The Open / Reload buttons on the top bar drive the controller;
the tab listens on ``config_loaded`` / ``config_saved`` to refresh its
task list.

Emits :attr:`stage_selected` when the user clicks a stage row so the
Log tab can switch to that stage's log file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.progress import CancelToken
from auto_ext.core.runner import STAGE_ORDER
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.models import STAGE_DISPLAY, STATUS_COLOR, TASK_DISPLAY
from auto_ext.ui.qt_reporter import QtProgressReporter
from auto_ext.ui.worker import RunWorker


_UNSAFE_TASK_ID = re.compile(r"[^A-Za-z0-9_.-]")


class RunTab(QWidget):
    """Task picker + live status tree + Run/Cancel."""

    #: Emitted with the absolute path of a stage log file when the user
    #: selects a stage row. Main window wires this into LogTab.
    stage_selected = pyqtSignal(object)
    #: Emitted when the empty-state banner's "新建项目" button is clicked.
    #: MainWindow connects this to its ``_open_init_wizard`` slot.
    request_init_wizard = pyqtSignal()
    #: Emitted whenever a worker is spawned or finishes. Payload is the
    #: new ``is_worker_active()`` value. ProjectTab listens on this so
    #: its Save button can re-evaluate its enabled state when the run
    #: ends — staging an edit while a run is in flight latched Save in
    #: the disabled state until something else nudged dirty_changed.
    worker_state_changed = pyqtSignal(bool)

    def __init__(
        self, controller: ConfigController, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._worker: RunWorker | None = None
        self._reporter: QtProgressReporter | None = None

        # Map of (task_id, stage) → QTreeWidgetItem for fast status updates.
        self._stage_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._task_items: dict[str, QTreeWidgetItem] = {}

        self._build_ui()

        controller.config_loaded.connect(self._on_config_loaded)
        controller.config_saved.connect(self._on_config_loaded)
        controller.config_error.connect(self._on_config_error)

        if controller.project is not None:
            self._on_config_loaded(controller.config_dir)
        else:
            self._empty_banner.setVisible(True)

    # ---- public helpers ----------------------------------------------

    def is_worker_active(self) -> bool:
        """True while a :class:`RunWorker` is in flight. Other tabs use
        this to disable destructive actions (e.g. save while running).
        """

        return self._worker is not None

    # ---- UI construction ---------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Empty-state banner: visible only when no config is loaded.
        self._empty_banner = QFrame(self)
        self._empty_banner.setFrameShape(QFrame.StyledPanel)
        self._empty_banner.setStyleSheet(
            "QFrame { background: #fffae0; border: 1px solid #d8c060; "
            "border-radius: 4px; padding: 8px; }"
        )
        banner_row = QHBoxLayout(self._empty_banner)
        banner_label = QLabel(
            "ⓘ 还没有加载项目。", self._empty_banner
        )
        banner_open_btn = QPushButton("打开现有项目…", self._empty_banner)
        banner_open_btn.clicked.connect(self._browse_config_dir)
        banner_new_btn = QPushButton("新建项目…", self._empty_banner)
        banner_new_btn.clicked.connect(self.request_init_wizard.emit)
        banner_row.addWidget(banner_label, 1)
        banner_row.addWidget(banner_open_btn)
        banner_row.addWidget(banner_new_btn)
        root.addWidget(self._empty_banner)

        # Top bar: config dir path + reload + jobs
        top = QHBoxLayout()
        self._config_label = QLabel("(no config loaded)", self)
        self._config_label.setStyleSheet("font-family: monospace; color: #444;")
        browse = QPushButton("Open config dir...", self)
        browse.clicked.connect(self._browse_config_dir)
        reload_btn = QPushButton("Reload", self)
        reload_btn.clicked.connect(self._controller.reload)

        top.addWidget(QLabel("Config:", self))
        top.addWidget(self._config_label, stretch=1)
        top.addWidget(browse)
        top.addWidget(reload_btn)
        top.addWidget(QLabel("Jobs:", self))
        self._jobs_spin = QSpinBox(self)
        self._jobs_spin.setRange(1, 64)
        self._jobs_spin.setValue(1)
        top.addWidget(self._jobs_spin)

        root.addLayout(top)

        # Splitter: left pane (selectors) | right pane (status tree)
        splitter = QSplitter(Qt.Horizontal, self)

        left = QWidget(self)
        lleft = QVBoxLayout(left)
        lleft.setContentsMargins(0, 0, 0, 0)

        tasks_box = QGroupBox("Tasks", left)
        tb = QVBoxLayout(tasks_box)
        self._task_list = QListWidget(tasks_box)
        self._task_list.setSelectionMode(QListWidget.NoSelection)
        tb.addWidget(self._task_list)
        lleft.addWidget(tasks_box, stretch=1)

        stages_box = QGroupBox("Stages", left)
        sb = QVBoxLayout(stages_box)
        self._stage_checks: dict[str, QCheckBox] = {}
        for stage in STAGE_ORDER:
            cb = QCheckBox(stage, stages_box)
            cb.setChecked(True)
            self._stage_checks[stage] = cb
            sb.addWidget(cb)
        lleft.addWidget(stages_box)

        # Dry run toggle + Run / Cancel
        self._dry_run_check = QCheckBox("Dry run (render only, no subprocesses)", left)
        lleft.addWidget(self._dry_run_check)

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("▶ Run", left)
        self._run_btn.clicked.connect(self._start_run)
        self._cancel_btn = QPushButton("✕ Cancel", left)
        self._cancel_btn.clicked.connect(self._cancel_run)
        self._cancel_btn.setEnabled(False)
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(self._cancel_btn)
        lleft.addLayout(btn_row)

        splitter.addWidget(left)

        right = QWidget(self)
        lright = QVBoxLayout(right)
        lright.setContentsMargins(0, 0, 0, 0)
        lright.addWidget(QLabel("Live status", right))
        self._status_tree = QTreeWidget(right)
        self._status_tree.setHeaderLabels(["task / stage", "status"])
        self._status_tree.setColumnWidth(0, 360)
        self._status_tree.itemClicked.connect(self._on_tree_click)
        lright.addWidget(self._status_tree)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 800])

        root.addWidget(splitter, stretch=1)

    # ---- controller wiring -------------------------------------------

    def _browse_config_dir(self) -> None:
        start = str(self._controller.config_dir or Path.cwd())
        path = QFileDialog.getExistingDirectory(
            self,
            "Select config dir (containing project.yaml + tasks.yaml)",
            start,
        )
        if path:
            self._controller.load(Path(path))

    def _on_config_loaded(self, config_dir: object) -> None:
        path = Path(config_dir) if config_dir is not None else None
        self._config_label.setText(str(path) if path else "(no config loaded)")
        self._empty_banner.setVisible(path is None)

        # Preserve user's check/uncheck selections across reloads: task_ids
        # that appeared before keep their state; task_ids that are brand
        # new default to Unchecked so the user opts in explicitly (changed
        # in Phase 5.4 — previously new ids defaulted to Checked, which
        # surprise-ran cells users had just added mid-edit).
        previously_checked: set[str] = set()
        previously_unchecked: set[str] = set()
        for i in range(self._task_list.count()):
            item = self._task_list.item(i)
            if item.checkState() == Qt.Checked:
                previously_checked.add(item.text())
            else:
                previously_unchecked.add(item.text())

        self._task_list.clear()
        for t in self._controller.tasks:
            lw_item = QListWidgetItem(t.task_id, self._task_list)
            lw_item.setFlags(lw_item.flags() | Qt.ItemIsUserCheckable)
            if t.task_id in previously_checked:
                state = Qt.Checked
            elif t.task_id in previously_unchecked:
                state = Qt.Unchecked
            elif not previously_checked and not previously_unchecked:
                # Very first load — default to Checked so a fresh open
                # does not leave an empty selection for the user.
                state = Qt.Checked
            else:
                state = Qt.Unchecked
            lw_item.setCheckState(state)

    def _on_config_error(self, message: str) -> None:
        QMessageBox.critical(self, "Config error", message)

    # ---- run lifecycle ------------------------------------------------

    def _selected_tasks(self) -> list[Any]:
        want: set[str] = set()
        for i in range(self._task_list.count()):
            item = self._task_list.item(i)
            if item.checkState() == Qt.Checked:
                want.add(item.text())
        return [t for t in self._controller.tasks if t.task_id in want]

    def _selected_stages(self) -> list[str]:
        return [s for s in STAGE_ORDER if self._stage_checks[s].isChecked()]

    def _start_run(self) -> None:
        if self._worker is not None:
            return  # one run at a time
        if self._controller.project is None:
            QMessageBox.warning(self, "No config", "Load a config dir first.")
            return

        if self._controller.is_dirty:
            choice = QMessageBox.question(
                self,
                "Unsaved project edits",
                "The Project tab has unsaved edits that will NOT be used by "
                "this run (the loaded project.yaml is used instead).\n\n"
                "Save first, or continue anyway?",
                QMessageBox.Save | QMessageBox.Cancel | QMessageBox.Ignore,
                QMessageBox.Save,
            )
            if choice == QMessageBox.Save:
                if not self._controller.save():
                    return
            elif choice == QMessageBox.Cancel:
                return

        tasks = self._selected_tasks()
        if not tasks:
            QMessageBox.warning(self, "No tasks", "Check at least one task.")
            return
        stages = self._selected_stages()
        if not stages:
            QMessageBox.warning(self, "No stages", "Check at least one stage.")
            return

        jobs = self._jobs_spin.value()
        dry_run = self._dry_run_check.isChecked()

        ae_root = self._controller.auto_ext_root
        workarea = self._controller.workarea
        if ae_root is None or workarea is None:
            QMessageBox.critical(
                self,
                "Paths unresolved",
                "auto_ext_root and workarea could not be derived. "
                "Pass --auto-ext-root / --workarea to the gui command.",
            )
            return

        # Fresh reporter + token per run: reporter keeps per-run state,
        # token is single-shot.
        reporter = QtProgressReporter()
        reporter.run_started.connect(self._on_run_started)
        reporter.task_started.connect(self._on_task_started)
        reporter.stage_started.connect(self._on_stage_started)
        reporter.stage_finished.connect(self._on_stage_finished)
        reporter.task_finished.connect(self._on_task_finished)
        reporter.run_finished.connect(self._on_run_finished)

        token = CancelToken()

        self._reporter = reporter
        self._worker = RunWorker(
            project=self._controller.project,
            tasks=tasks,
            stages=stages,
            auto_ext_root=ae_root,
            workarea=workarea,
            reporter=reporter,
            cancel_token=token,
            max_workers=jobs if jobs >= 2 else None,
            dry_run=dry_run,
        )
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_done)

        self._run_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._reset_status_tree(tasks, stages)
        self._worker.start()
        self.worker_state_changed.emit(True)

    def _cancel_run(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self._cancel_btn.setEnabled(False)
            self._cancel_btn.setText("cancelling...")

    # ---- status tree management --------------------------------------

    def _reset_status_tree(self, tasks: list[Any], stages: list[str]) -> None:
        self._status_tree.clear()
        self._stage_items.clear()
        self._task_items.clear()
        for t in tasks:
            parent = QTreeWidgetItem([t.task_id, TASK_DISPLAY["pending"]])
            self._tint(parent, "pending")
            self._status_tree.addTopLevelItem(parent)
            self._task_items[t.task_id] = parent
            for stage in stages:
                child = QTreeWidgetItem([stage, STAGE_DISPLAY[""]])
                parent.addChild(child)
                self._stage_items[(t.task_id, stage)] = child
            parent.setExpanded(True)

    def _tint(self, item: QTreeWidgetItem, status: str) -> None:
        color = STATUS_COLOR.get(status)
        if color is None:
            return
        item.setForeground(1, QColor(color))

    # ---- reporter slots (all called on the GUI thread) ---------------

    def _on_run_started(self, total_tasks: int, stages: list[str]) -> None:
        pass  # tree already reset in _start_run

    def _on_task_started(self, task_id: str, stages: list[str]) -> None:
        item = self._task_items.get(task_id)
        if item is not None:
            item.setText(1, "running")
            self._tint(item, "running")

    def _on_stage_started(self, task_id: str, stage: str) -> None:
        item = self._stage_items.get((task_id, stage))
        if item is not None:
            item.setText(1, STAGE_DISPLAY["running"])
            self._tint(item, "running")

    def _on_stage_finished(
        self, task_id: str, stage: str, status: str, error: object
    ) -> None:
        item = self._stage_items.get((task_id, stage))
        if item is None:
            return
        item.setText(1, STAGE_DISPLAY.get(status, status))
        self._tint(item, status)
        if error:
            item.setToolTip(1, str(error))

    def _on_task_finished(self, task_id: str, status: str) -> None:
        item = self._task_items.get(task_id)
        if item is not None:
            item.setText(1, TASK_DISPLAY.get(status, status))
            self._tint(item, status)

    def _on_run_finished(self, summary: Any) -> None:
        pass  # _on_worker_done handles the UI teardown

    # ---- worker completion --------------------------------------------

    def _on_worker_error(self, msg: str) -> None:
        QMessageBox.critical(self, "Run failed", msg)

    def _on_worker_done(self) -> None:
        self._worker = None
        self._reporter = None
        self._run_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("✕ Cancel")
        self.worker_state_changed.emit(False)

    # ---- stage row click → log switch --------------------------------

    def _on_tree_click(self, item: QTreeWidgetItem, column: int) -> None:
        # Only stage rows (children of task rows) select a log file.
        parent = item.parent()
        if parent is None:
            self.stage_selected.emit(None)
            return
        task_id = parent.text(0)
        stage = item.text(0)

        ae_root = self._controller.auto_ext_root
        if ae_root is None:
            return
        safe_id = _UNSAFE_TASK_ID.sub("_", task_id)
        log_path = ae_root / "logs" / f"task_{safe_id}" / f"{stage}.log"
        self.stage_selected.emit(log_path)
