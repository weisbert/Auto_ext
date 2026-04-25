"""Project tab: form editor for ``project.yaml`` + env resolution panel.

Reads and writes via the shared :class:`ConfigController`. Form fields
map to flat / dotted edit keys that :func:`apply_project_edits`
understands; all edits are staged in the controller and committed by a
single :meth:`ConfigController.save`.

The env panel re-runs :func:`auto_ext.core.runner._discover_env_vars`
+ :func:`auto_ext.core.env.resolve_env` after every load and every
env-override stage so the user sees the pending state before Save
lands on disk.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.env import resolve_env
from auto_ext.core.errors import AutoExtError
from auto_ext.core.runner import _discover_env_vars
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.models import ENV_SOURCE_COLOR, ENV_SOURCE_DISPLAY

if TYPE_CHECKING:
    from auto_ext.ui.tabs.run_tab import RunTab


# (form_key, display_label, group_name). Order drives widget placement.
_FIELDS: list[tuple[str, str, str]] = [
    ("work_root", "work_root", "Identity"),
    ("verify_root", "verify_root", "Identity"),
    ("setup_root", "setup_root", "Identity"),
    ("employee_id", "employee_id", "Identity"),
    ("tech_name", "tech_name", "PDK"),
    ("pdk_subdir", "pdk_subdir", "PDK"),
    ("project_subdir", "project_subdir", "PDK"),
    ("runset_versions.lvs", "runset_versions.lvs", "PDK"),
    ("runset_versions.qrc", "runset_versions.qrc", "PDK"),
    ("layer_map", "layer_map", "Output"),
    ("extraction_output_dir", "extraction_output_dir", "Output"),
    ("intermediate_dir", "intermediate_dir", "Output"),
]

_DIR_FIELDS = {"work_root", "verify_root", "setup_root"}
_FILE_FIELDS = {"layer_map"}


class ProjectTab(QWidget):
    """Form editor + env resolution panel bound to a ConfigController."""

    def __init__(
        self,
        controller: ConfigController,
        run_tab: "RunTab",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._run_tab = run_tab

        self._fields: dict[str, QLineEdit] = {}
        self._original_values: dict[str, str] = {}
        # True while we're rebuilding fields from ProjectConfig — prevents
        # editingFinished from feeding spurious edits back into the
        # controller.
        self._populating = False

        self._build_ui()

        controller.config_loaded.connect(self._on_config_loaded)
        controller.config_error.connect(self._on_config_error)
        controller.dirty_changed.connect(self._on_dirty_changed)

        if controller.project is not None:
            self._on_config_loaded(controller.config_dir)

    # ---- UI construction ---------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self._config_label = QLabel("(no config loaded)", self)
        self._config_label.setStyleSheet("font-family: monospace; color: #444;")
        self._dirty_label = QLabel("", self)
        self._dirty_label.setStyleSheet("color: #d69016; font-weight: bold;")
        self._save_btn = QPushButton("💾 Save", self)
        self._save_btn.clicked.connect(self._on_save_clicked)
        self._save_btn.setEnabled(False)
        self._revert_btn = QPushButton("↶ Revert", self)
        self._revert_btn.clicked.connect(self._controller.revert)
        self._revert_btn.setEnabled(False)

        top.addWidget(QLabel("Config:", self))
        top.addWidget(self._config_label, stretch=1)
        top.addWidget(self._dirty_label)
        top.addWidget(self._save_btn)
        top.addWidget(self._revert_btn)
        root.addLayout(top)

        groups: dict[str, QFormLayout] = {}
        for group_name in ("Identity", "PDK", "Output"):
            box = QGroupBox(group_name, self)
            form = QFormLayout(box)
            groups[group_name] = form
            root.addWidget(box)

        for key, label, group_name in _FIELDS:
            line = QLineEdit(self)
            line.setPlaceholderText("(unset)")
            self._fields[key] = line
            line.editingFinished.connect(lambda k=key: self._on_field_edited(k))

            if key in _DIR_FIELDS or key in _FILE_FIELDS:
                wrapper = QWidget(self)
                hb = QHBoxLayout(wrapper)
                hb.setContentsMargins(0, 0, 0, 0)
                hb.addWidget(line)
                browse = QPushButton("…", wrapper)
                browse.setMaximumWidth(28)
                browse.clicked.connect(lambda _=False, k=key: self._browse_path(k))
                hb.addWidget(browse)
                groups[group_name].addRow(QLabel(label + ":", self), wrapper)
            else:
                groups[group_name].addRow(QLabel(label + ":", self), line)

        templates_box = QGroupBox("Templates (read-only; editor lands in 5.5)", self)
        tform = QFormLayout(templates_box)
        self._templates_summary = QLabel("(no config)", self)
        self._templates_summary.setStyleSheet("font-family: monospace; color: #666;")
        tform.addRow("templates:", self._templates_summary)
        root.addWidget(templates_box)

        env_box = QGroupBox("Environment resolution", self)
        env_layout = QVBoxLayout(env_box)
        hint = QLabel(
            "Rows reflect staged overrides immediately; click Save to persist.",
            self,
        )
        hint.setStyleSheet("color: #666; font-size: 11px;")
        env_layout.addWidget(hint)

        self._env_table = QTableWidget(self)
        self._env_table.setColumnCount(6)
        self._env_table.setHorizontalHeaderLabels(
            ["var", "source", "value", "shell value", "", ""]
        )
        self._env_table.verticalHeader().setVisible(False)
        self._env_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._env_table.setSelectionMode(QTableWidget.NoSelection)
        header = self._env_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        env_layout.addWidget(self._env_table)
        root.addWidget(env_box, stretch=1)

    # ---- population --------------------------------------------------

    def _on_config_loaded(self, config_dir: object) -> None:
        self._populating = True
        try:
            path = Path(config_dir) if config_dir is not None else None
            self._config_label.setText(str(path) if path else "(no config loaded)")
            project = self._controller.project
            self._original_values.clear()

            for key, line in self._fields.items():
                value = self._read_field_value(project, key)
                line.setText(value)
                self._original_values[key] = value

            if project is None:
                self._templates_summary.setText("(no config)")
            else:
                t = project.templates
                self._templates_summary.setText(
                    "\n".join(
                        f"{name}={getattr(t, name) or '—'}"
                        for name in ("si", "calibre", "quantus", "jivaro")
                    )
                )

            self._refresh_env_table()
            # Fresh load clears dirty; sync button state defensively in
            # case no dirty_changed signal fired (was_dirty=False path).
            self._on_dirty_changed(self._controller.is_dirty)
        finally:
            self._populating = False

    @staticmethod
    def _read_field_value(project: Any, key: str) -> str:
        if project is None:
            return ""
        if key.startswith("runset_versions."):
            attr = key.split(".", 1)[1]
            v = getattr(project.runset_versions, attr, None)
            return "" if v is None else str(v)
        v = getattr(project, key, None)
        return "" if v is None else str(v)

    # ---- field edit → stage ------------------------------------------

    def _on_field_edited(self, key: str) -> None:
        if self._populating:
            return
        text = self._fields[key].text().strip()
        value: Any = None if text == "" else text
        self._controller.stage_edits({key: value})

    def _browse_path(self, key: str) -> None:
        line = self._fields[key]
        current = line.text()
        start = current if current and Path(current).exists() else str(Path.cwd())
        if key in _FILE_FIELDS:
            path, _ = QFileDialog.getOpenFileName(self, f"Select {key}", start)
        else:
            path = QFileDialog.getExistingDirectory(self, f"Select {key}", start)
        if path:
            line.setText(path)
            self._on_field_edited(key)

    # ---- env panel ---------------------------------------------------

    def _refresh_env_table(self) -> None:
        project = self._controller.project
        if project is None:
            self._env_table.setRowCount(0)
            return

        try:
            required = _discover_env_vars(
                project,
                self._controller.tasks,
                auto_ext_root=self._controller.auto_ext_root,
            )
        except AutoExtError as exc:
            self._env_table.clearSpans()
            self._env_table.setRowCount(1)
            err = QTableWidgetItem(f"(discover error: {exc})")
            err.setForeground(QBrush(QColor("#c83232")))
            self._env_table.setItem(0, 0, err)
            self._env_table.setSpan(0, 0, 1, 6)
            return

        self._env_table.clearSpans()
        effective = self._controller.effective_env_overrides()
        resolution = resolve_env(required, effective)
        shell_lookup = {v: os.environ.get(v, "") for v in required}

        names = sorted(resolution.resolved)
        self._env_table.setRowCount(len(names))
        mono = _mono_font()
        for row, name in enumerate(names):
            source = resolution.sources[name]
            value = resolution.resolved[name]
            shell_value = shell_lookup.get(name, "")

            var_item = QTableWidgetItem(name)
            var_item.setFont(mono)
            self._env_table.setItem(row, 0, var_item)

            src_item = QTableWidgetItem(ENV_SOURCE_DISPLAY.get(source, source))
            color = ENV_SOURCE_COLOR.get(source)
            if color is not None:
                src_item.setForeground(QBrush(QColor(color)))
            self._env_table.setItem(row, 1, src_item)

            val_item = QTableWidgetItem(value)
            val_item.setFont(mono)
            self._env_table.setItem(row, 2, val_item)

            shell_item = QTableWidgetItem(
                shell_value if source == "override" else ""
            )
            shell_item.setFont(mono)
            shell_item.setForeground(QBrush(QColor("#888888")))
            self._env_table.setItem(row, 3, shell_item)

            override_btn = QPushButton("Override")
            override_btn.clicked.connect(
                lambda _=False, n=name: self._on_override(n)
            )
            self._env_table.setCellWidget(row, 4, override_btn)

            clear_btn = QPushButton("Clear")
            clear_btn.setEnabled(name in effective)
            clear_btn.clicked.connect(
                lambda _=False, n=name: self._on_clear_override(n)
            )
            self._env_table.setCellWidget(row, 5, clear_btn)

    def _on_override(self, name: str) -> None:
        current = self._controller.effective_env_overrides().get(name, "")
        text, ok = QInputDialog.getText(
            self,
            f"Override {name}",
            f"New value for {name}:",
            QLineEdit.Normal,
            current,
        )
        if not ok:
            return
        self._controller.stage_edits({f"env_overrides.{name}": text})
        self._refresh_env_table()

    def _on_clear_override(self, name: str) -> None:
        self._controller.stage_edits({f"env_overrides.{name}": None})
        self._refresh_env_table()

    # ---- save / dirty ------------------------------------------------

    def _on_save_clicked(self) -> None:
        if self._run_tab.is_worker_active():
            QMessageBox.warning(
                self,
                "Run in progress",
                "Save is disabled while a run is active. Cancel the run or "
                "wait for it to finish.",
            )
            return
        if self._controller.save():
            return
        # save() emitted config_error with the reason; if it was a mtime
        # conflict, offer force-save.
        if self._controller.has_external_change():
            choice = QMessageBox.question(
                self,
                "External change detected",
                "project.yaml changed on disk since it was loaded. "
                "Overwrite with the current edits?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if choice == QMessageBox.Yes:
                self._controller.save(force=True)

    def _on_dirty_changed(self, dirty: bool) -> None:
        self._dirty_label.setText("● unsaved" if dirty else "")
        running = self._run_tab.is_worker_active()
        self._save_btn.setEnabled(dirty and not running)
        self._revert_btn.setEnabled(dirty)

    def _on_config_error(self, message: str) -> None:
        if self.isVisible():
            QMessageBox.warning(self, "Config error", message)


def _mono_font() -> QFont:
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f
