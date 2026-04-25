"""Templates tab: path picker + placeholder inventory + project knob overrides.

Three panes (top to bottom):

1. Bound-templates path picker (4 rows, one per tool slot). Empty value
   means the slot is unset — :func:`apply_project_edits` deletes the
   key when ``None`` is staged.
2. Discovered-templates list. Bound entries appear first in tool order
   then any unreferenced ``*.j2`` under ``<auto_ext_root>/templates/``.
3. Right pane sub-tabs for the selected template: ``Inventory``
   (read-only :class:`PlaceholderInventory` viewer with status-coloured
   rows) and ``Knobs`` (one :class:`KnobEditor` per manifest knob, or a
   placeholder hint if no manifest sidecar exists).

All edits funnel through the shared :class:`ConfigController` via
``stage_edits({"templates.<tool>": ...})`` and
``stage_edits({"knobs.<stage>.<name>": ...})``; Save / Revert / dirty
tracking mirror the Project + Tasks tabs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.env import resolve_env
from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.core.manifest import (
    TemplateManifest,
    current_knob_value,
    load_manifest,
)
from auto_ext.core.runner import _discover_env_vars
from auto_ext.core.template import (
    PlaceholderInventory,
    resolve_template_path,
    scan_placeholders,
)
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.models import ENV_SOURCE_COLOR
from auto_ext.ui.templates_view import (
    PlaceholderStatus,
    TemplateEntry,
    collect_template_entries,
    env_var_status,
    jinja_variable_status,
    literal_placeholder_status,
    user_defined_status,
)
from auto_ext.ui.widgets.knob_editor import KnobEditor

if TYPE_CHECKING:
    from auto_ext.ui.tabs.run_tab import RunTab


_TEMPLATE_TOOLS: tuple[str, ...] = ("si", "calibre", "quantus", "jivaro")

#: Foreground colours for the Inventory viewer. ``override`` reuses the
#: amber from :data:`ENV_SOURCE_COLOR`. ``info`` is grey to read as
#: "informational, no verdict".
_STATUS_COLOR: dict[PlaceholderStatus, str] = {
    "ok": "#2e8b2e",
    "override": ENV_SOURCE_COLOR["override"],
    "missing": "#c83232",
    "info": "#888888",
}


class TemplatesTab(QWidget):
    """Path picker + inventory + project knob override editor."""

    #: Emitted when the user selects a different template in the list.
    #: Phase 5.6 (diff editor) will subscribe; for 5.5 nothing wires it.
    current_template_changed = pyqtSignal(object)  # Path | None

    def __init__(
        self,
        controller: ConfigController,
        run_tab: "RunTab",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._run_tab = run_tab

        self._entries: list[TemplateEntry] = []
        self._selected_path: Path | None = None
        self._populating = False

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._refresh_inventory_and_knobs)

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

        # Path picker for the 4 bound slots.
        paths_box = QGroupBox("project.templates", self)
        form = QFormLayout(paths_box)
        self._path_edits: dict[str, QLineEdit] = {}
        for tool in _TEMPLATE_TOOLS:
            line = QLineEdit(paths_box)
            line.setPlaceholderText("(unset)")
            line.editingFinished.connect(lambda t=tool: self._on_path_edited(t))
            self._path_edits[tool] = line

            wrapper = QWidget(paths_box)
            hb = QHBoxLayout(wrapper)
            hb.setContentsMargins(0, 0, 0, 0)
            hb.addWidget(line)
            browse = QPushButton("…", wrapper)
            browse.setMaximumWidth(28)
            browse.clicked.connect(lambda _=False, t=tool: self._on_browse_clicked(t))
            hb.addWidget(browse)
            form.addRow(QLabel(f"{tool}:", paths_box), wrapper)
        root.addWidget(paths_box)

        # Splitter: left = template list, right = inventory + knobs sub-tabs.
        splitter = QSplitter(Qt.Horizontal, self)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Templates:", left))
        self._list = QListWidget(left)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.currentRowChanged.connect(self._on_list_row_changed)
        left_layout.addWidget(self._list, stretch=1)
        splitter.addWidget(left)

        right = QTabWidget(splitter)
        self._inventory_table = QTableWidget(right)
        self._inventory_table.setColumnCount(3)
        self._inventory_table.setHorizontalHeaderLabels(["kind", "name", "status"])
        self._inventory_table.verticalHeader().setVisible(False)
        self._inventory_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._inventory_table.setSelectionMode(QAbstractItemView.NoSelection)
        h = self._inventory_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        right.addTab(self._inventory_table, "Inventory")

        knobs_pane = QWidget(right)
        knobs_layout = QVBoxLayout(knobs_pane)
        self._knobs_banner = QLabel("", knobs_pane)
        self._knobs_banner.setStyleSheet("color: #c83232; font-size: 11px;")
        self._knobs_banner.setWordWrap(True)
        self._knobs_banner.hide()
        knobs_layout.addWidget(self._knobs_banner)

        self._knobs_form_host = QWidget(knobs_pane)
        self._knobs_form = QFormLayout(self._knobs_form_host)
        knobs_layout.addWidget(self._knobs_form_host, stretch=1)
        right.addTab(knobs_pane, "Knobs")
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, stretch=1)

        # Empty-state hint shown when no project / no templates dir.
        self._empty_hint = QLabel(
            "No templates discovered. Create a project via "
            "`auto_ext init-project ...` and point project.templates "
            "at the rendered .j2 files.",
            self,
        )
        self._empty_hint.setStyleSheet("color: #888; font-size: 12px;")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.hide()
        root.addWidget(self._empty_hint)

    # ---- population --------------------------------------------------

    def _on_config_loaded(self, config_dir: object) -> None:
        path = Path(config_dir) if config_dir is not None else None
        self._config_label.setText(str(path) if path else "(no config loaded)")
        self._populating = True
        try:
            self._refresh_path_edits()
            self._refresh_template_list()
        finally:
            self._populating = False
        self._on_dirty_changed(self._controller.is_dirty)

    def _refresh_path_edits(self) -> None:
        project = self._controller.project
        for tool, line in self._path_edits.items():
            value = ""
            if project is not None:
                p = getattr(project.templates, tool, None)
                if p is not None:
                    value = str(p)
            line.setText(value)

    def _refresh_template_list(self) -> None:
        self._entries = collect_template_entries(
            self._controller.project,
            self._controller.auto_ext_root,
            self._controller.workarea,
        )
        previously_selected = self._selected_path
        self._list.blockSignals(True)
        self._list.clear()
        for entry in self._entries:
            label = self._format_entry_label(entry)
            item = QListWidgetItem(label)
            if not entry.in_project:
                item.setForeground(QBrush(QColor("#888888")))
                item.setToolTip(f"Discovered under templates/, not bound: {entry.path}")
            self._list.addItem(item)
        self._list.blockSignals(False)

        new_index = -1
        if previously_selected is not None:
            for i, e in enumerate(self._entries):
                if e.path == previously_selected:
                    new_index = i
                    break
        if new_index < 0 and self._entries:
            new_index = 0

        if not self._entries:
            self._selected_path = None
            self._empty_hint.show()
            self._inventory_table.setRowCount(0)
            self._clear_knobs_form()
            self._knobs_banner.setText(
                "Load a project to see manifest knobs."
            )
            self._knobs_banner.show()
            self.current_template_changed.emit(None)
            return

        self._empty_hint.hide()
        self._list.setCurrentRow(new_index)

    @staticmethod
    def _format_entry_label(entry: TemplateEntry) -> str:
        prefix = f"[{entry.tool}]" if entry.tool is not None else "[unused]"
        return f"{prefix:<10} {entry.path}"

    def _on_list_row_changed(self, row: int) -> None:
        if 0 <= row < len(self._entries):
            entry = self._entries[row]
            self._selected_path = entry.path
            self.current_template_changed.emit(entry.path)
        else:
            self._selected_path = None
            self.current_template_changed.emit(None)
        self._refresh_timer.start()

    # ---- inventory + knobs refresh -----------------------------------

    def _refresh_inventory_and_knobs(self) -> None:
        self._inventory_table.clearSpans()
        self._inventory_table.setRowCount(0)
        self._clear_knobs_form()
        self._knobs_banner.hide()
        self._knobs_banner.setText("")

        path = self._resolved_selected_path()
        if path is None:
            return
        if not path.is_file():
            self._knobs_banner.setText(f"Template file not found: {path}")
            self._knobs_banner.show()
            return

        try:
            inventory = scan_placeholders(path)
        except AutoExtError as exc:
            self._knobs_banner.setText(f"Scan failed: {exc}")
            self._knobs_banner.show()
            return

        try:
            manifest = load_manifest(path)
        except ConfigError as exc:
            manifest = None
            self._knobs_banner.setText(f"Manifest error: {exc}")
            self._knobs_banner.show()

        self._populate_inventory_table(inventory, manifest)
        self._populate_knobs_form(manifest)

    def _resolved_selected_path(self) -> Path | None:
        if self._selected_path is None:
            return None
        return resolve_template_path(
            self._selected_path,
            auto_ext_root=self._controller.auto_ext_root,
            workarea=self._controller.workarea,
        )

    def _populate_inventory_table(
        self,
        inventory: PlaceholderInventory,
        manifest: TemplateManifest | None,
    ) -> None:
        project = self._controller.project
        resolution = None
        if project is not None and inventory.env_vars:
            try:
                required = _discover_env_vars(
                    project,
                    self._controller.tasks,
                    auto_ext_root=self._controller.auto_ext_root,
                )
            except AutoExtError:
                required = set(inventory.env_vars)
            effective = self._controller.effective_env_overrides()
            resolution = resolve_env(required | inventory.env_vars, effective)

        rows: list[tuple[str, str, PlaceholderStatus]] = []
        for name in sorted(inventory.env_vars):
            status: PlaceholderStatus = (
                env_var_status(name, resolution) if resolution is not None else "missing"
            )
            rows.append(("env_var", name, status))
        for name in sorted(inventory.literal_placeholders):
            rows.append(("literal", name, literal_placeholder_status(name)))
        for name in sorted(inventory.user_defined):
            rows.append(("user_defined", name, user_defined_status(name)))
        for name in sorted(inventory.jinja_variables):
            rows.append(("jinja", name, jinja_variable_status(name, manifest)))

        self._inventory_table.setRowCount(len(rows))
        if not rows:
            return
        mono = _mono_font()
        for r, (kind, name, status) in enumerate(rows):
            kind_item = QTableWidgetItem(kind)
            self._inventory_table.setItem(r, 0, kind_item)

            name_item = QTableWidgetItem(name)
            name_item.setFont(mono)
            self._inventory_table.setItem(r, 1, name_item)

            status_item = QTableWidgetItem(status)
            color = _STATUS_COLOR.get(status)
            if color is not None:
                status_item.setForeground(QBrush(QColor(color)))
            self._inventory_table.setItem(r, 2, status_item)

    def _populate_knobs_form(self, manifest: TemplateManifest | None) -> None:
        self._clear_knobs_form()
        stage = self._stage_for_selected_path()

        if manifest is None:
            hint = QLabel(
                "(no manifest sidecar — `auto_ext knob suggest` / "
                "`auto_ext knob promote` to add knobs)",
                self._knobs_form_host,
            )
            hint.setStyleSheet("color: #888; font-style: italic;")
            self._knobs_form.addRow(hint)
            return
        if not manifest.knobs:
            hint = QLabel("(manifest declares no knobs)", self._knobs_form_host)
            hint.setStyleSheet("color: #888; font-style: italic;")
            self._knobs_form.addRow(hint)
            return
        if stage is None:
            hint = QLabel(
                "(this template is not bound to a tool slot — knobs "
                "stage is unknown; bind it via project.templates above)",
                self._knobs_form_host,
            )
            hint.setStyleSheet("color: #888; font-style: italic;")
            self._knobs_form.addRow(hint)
            return

        project_knobs = self._effective_project_knobs()
        for knob_name, spec in manifest.knobs.items():
            try:
                value, provenance = current_knob_value(
                    manifest, project_knobs, stage, knob_name
                )
            except ConfigError as exc:
                self._knobs_banner.setText(
                    f"{self._knobs_banner.text()}\n{exc}".strip()
                )
                self._knobs_banner.show()
                continue
            editor = KnobEditor(knob_name, spec, self._knobs_form_host)
            editor.set_value(value, is_default=(provenance == "default"))
            editor.value_changed.connect(
                lambda name, val, s=stage: self._on_knob_changed(s, name, val)
            )
            label = QLabel(knob_name + ":", self._knobs_form_host)
            if spec.description:
                label.setToolTip(spec.description)
            self._knobs_form.addRow(label, editor)

    def _clear_knobs_form(self) -> None:
        while self._knobs_form.rowCount() > 0:
            self._knobs_form.removeRow(0)

    def _stage_for_selected_path(self) -> str | None:
        if self._selected_path is None:
            return None
        for entry in self._entries:
            if entry.path == self._selected_path and entry.tool is not None:
                return entry.tool
        return None

    def _effective_project_knobs(self) -> dict[str, dict[str, Any]]:
        """Project knobs merged with any pending ``knobs.<stage>.<name>`` edits."""
        project = self._controller.project
        merged: dict[str, dict[str, Any]] = {}
        if project is not None:
            for stage, knobs in project.knobs.items():
                merged[stage] = dict(knobs)
        for key, value in self._controller.pending_edits.items():
            parts = key.split(".")
            if len(parts) != 3 or parts[0] != "knobs":
                continue
            _, stage, name = parts
            stage_layer = merged.setdefault(stage, {})
            if value is None:
                stage_layer.pop(name, None)
                if not stage_layer:
                    merged.pop(stage, None)
            else:
                stage_layer[name] = value
        return merged

    # ---- edits → controller staging ---------------------------------

    def _on_path_edited(self, tool: str) -> None:
        if self._populating:
            return
        text = self._path_edits[tool].text().strip()
        value: Any = None if text == "" else text
        self._controller.stage_edits({f"templates.{tool}": value})
        self._refresh_template_list()

    def _on_browse_clicked(self, tool: str) -> None:
        line = self._path_edits[tool]
        current = line.text().strip()
        start_dir: str
        if current:
            candidate = Path(current)
            if not candidate.is_absolute() and self._controller.workarea is not None:
                candidate = self._controller.workarea / candidate
            start_dir = str(candidate.parent if candidate.parent.exists() else Path.cwd())
        else:
            root = self._controller.auto_ext_root
            if root is not None and (root / "templates" / tool).is_dir():
                start_dir = str(root / "templates" / tool)
            else:
                start_dir = str(Path.cwd())
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {tool} template", start_dir, "Jinja templates (*.j2)"
        )
        if not path:
            return
        # Store relative to workarea when possible to match project.yaml convention.
        chosen = Path(path)
        workarea = self._controller.workarea
        if workarea is not None:
            try:
                rel = chosen.relative_to(workarea)
                line.setText(str(rel).replace(os.sep, "/"))
            except ValueError:
                line.setText(str(chosen))
        else:
            line.setText(str(chosen))
        self._on_path_edited(tool)

    def _on_knob_changed(self, stage: str, name: str, value: Any) -> None:
        if self._populating:
            return
        self._controller.stage_edits({f"knobs.{stage}.{name}": value})
        self._refresh_timer.start()

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
        if self._controller.has_external_change():
            choice = QMessageBox.question(
                self,
                "External change detected",
                "Config files changed on disk since they were loaded. "
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
