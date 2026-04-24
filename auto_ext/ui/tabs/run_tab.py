"""Run tab: pick tasks/stages, start a run, watch live status.

Owns:

- the QListWidget of loadable tasks (checkable),
- the stage checkbox row (all 5 EDA stages),
- the Jobs spinbox (1 = serial, ≥ 2 = parallel),
- Run / Cancel buttons,
- a QTreeWidget showing live per-task / per-stage status that updates
  in response to :class:`QtProgressReporter` signals,
- the :class:`RunWorker` lifecycle (one at a time).

Emits :attr:`stage_selected` when the user clicks a stage row so the
Log tab can switch to that stage's log file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
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

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.errors import AutoExtError
from auto_ext.core.progress import CancelToken
from auto_ext.core.runner import STAGE_ORDER
from auto_ext.ui.models import STAGE_DISPLAY, STATUS_COLOR, TASK_DISPLAY
from auto_ext.ui.qt_reporter import QtProgressReporter
from auto_ext.ui.worker import RunWorker


_UNSAFE_TASK_ID = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class RunContext:
    """Defaults passed in from the main window (CLI mirrors these)."""

    config_dir: Path | None = None
    auto_ext_root: Path | None = None
    workarea: Path | None = None


class RunTab(QWidget):
    """Task picker + live status tree + Run/Cancel."""

    #: Emitted with the absolute path of a stage log file when the user
    #: selects a stage row. Main window wires this into LogTab.
    stage_selected = pyqtSignal(object)

    def __init__(self, ctx: RunContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._project: Any | None = None
        self._tasks: list[Any] = []
        self._worker: RunWorker | None = None
        self._reporter: QtProgressReporter | None = None

        # Map of (task_id, stage) → QTreeWidgetItem for fast status updates.
        self._stage_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._task_items: dict[str, QTreeWidgetItem] = {}

        self._build_ui()
        if ctx.config_dir is not None:
            self._load_config(ctx.config_dir)

    # ---- UI construction ---------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Top bar: config dir path + reload + jobs
        top = QHBoxLayout()
        self._config_label = QLabel(
            str(self._ctx.config_dir) if self._ctx.config_dir else "(no config loaded)",
            self,
        )
        self._config_label.setStyleSheet("font-family: monospace; color: #444;")
        browse = QPushButton("Open config dir...", self)
        browse.clicked.connect(self._browse_config_dir)
        reload_btn = QPushButton("Reload", self)
        reload_btn.clicked.connect(self._reload)

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

    # ---- config loading ----------------------------------------------

    def _browse_config_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Select config dir (containing project.yaml + tasks.yaml)",
            str(self._ctx.config_dir or Path.cwd()),
        )
        if path:
            self._load_config(Path(path))

    def _reload(self) -> None:
        if self._ctx.config_dir is not None:
            self._load_config(self._ctx.config_dir)

    def _load_config(self, config_dir: Path) -> None:
        try:
            project = load_project(config_dir / "project.yaml")
            tasks = load_tasks(config_dir / "tasks.yaml", project=project)
        except AutoExtError as exc:
            QMessageBox.critical(self, "Config error", str(exc))
            return
        except OSError as exc:
            QMessageBox.critical(self, "Config error", str(exc))
            return

        self._ctx.config_dir = config_dir
        self._project = project
        self._tasks = tasks
        self._config_label.setText(str(config_dir))

        self._task_list.clear()
        for t in tasks:
            item = QListWidgetItem(t.task_id, self._task_list)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)

    # ---- run lifecycle ------------------------------------------------

    def _selected_tasks(self) -> list[Any]:
        want: set[str] = set()
        for i in range(self._task_list.count()):
            item = self._task_list.item(i)
            if item.checkState() == Qt.Checked:
                want.add(item.text())
        return [t for t in self._tasks if t.task_id in want]

    def _selected_stages(self) -> list[str]:
        return [s for s in STAGE_ORDER if self._stage_checks[s].isChecked()]

    def _start_run(self) -> None:
        if self._worker is not None:
            return  # one run at a time
        if self._project is None:
            QMessageBox.warning(self, "No config", "Load a config dir first.")
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

        ae_root = self._ctx.auto_ext_root or (self._ctx.config_dir.parent if self._ctx.config_dir else None)
        workarea = self._ctx.workarea or (ae_root.parent if ae_root else None)
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
            project=self._project,
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

    # ---- stage row click → log switch --------------------------------

    def _on_tree_click(self, item: QTreeWidgetItem, column: int) -> None:
        # Only stage rows (children of task rows) select a log file.
        parent = item.parent()
        if parent is None:
            self.stage_selected.emit(None)
            return
        task_id = parent.text(0)
        stage = item.text(0)

        ae_root = self._ctx.auto_ext_root or (self._ctx.config_dir.parent if self._ctx.config_dir else None)
        if ae_root is None:
            return
        safe_id = _UNSAFE_TASK_ID.sub("_", task_id)
        log_path = ae_root / "logs" / f"task_{safe_id}" / f"{stage}.log"
        self.stage_selected.emit(log_path)
