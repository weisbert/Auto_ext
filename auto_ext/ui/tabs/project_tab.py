"""Project tab: form editor for ``project.yaml`` + env resolution panel.

Reads and writes via the shared :class:`ConfigController`. Form fields
map to flat / dotted edit keys that :func:`apply_project_edits`
understands; all edits are staged in the controller and committed by a
single :meth:`ConfigController.save`.

The env panel re-runs :func:`auto_ext.core.runner._discover_env_vars`
+ :func:`auto_ext.core.env.resolve_env` after every load and every
env-override stage so the user sees the pending state before Save
lands on disk.

Phase 5.6.5 replaces the per-segment PDK fields (``pdk_subdir`` /
``project_subdir`` / ``runset_versions.{lvs,qrc}``) with a single
``Paths`` group bound to ``project.paths``. Each entry is a free-form
path expression resolved at render time via
:func:`auto_ext.core.env.resolve_path_expr`. The group also surfaces
"Used by" — every template line that references the path's key — so
users can see at a glance which templates each path drives.
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

from auto_ext.core.config import ProjectConfig
from auto_ext.core.env import (
    derive_parent_dir_from_env_candidates,
    resolve_env,
    resolve_path_expr,
)
from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.core.runner import _discover_env_vars
from auto_ext.core.template import (
    VarReference,
    collect_var_references,
    resolve_template_path,
)
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
    ("layer_map", "layer_map", "Output"),
    ("extraction_output_dir", "extraction_output_dir", "Output"),
    ("intermediate_dir", "intermediate_dir", "Output"),
]

_DIR_FIELDS = {"work_root", "verify_root", "setup_root"}
_FILE_FIELDS = {"layer_map"}

#: form_key → shell env-var the field shadows. Identity fields fall through
#: to ``$<VAR>`` from the resolved env (overrides → shell) at runtime.
_SHELL_VAR_FOR_FIELD: dict[str, str] = {
    "work_root": "WORK_ROOT",
    "verify_root": "VERIFY_ROOT",
    "setup_root": "SETUP_ROOT",
}


def _hint_for_field(
    key: str, project: ProjectConfig, effective_env: dict[str, str]
) -> str:
    """Return a one-line hint describing what runtime would use if ``key``
    is left unset in ``project.yaml``.

    Pure (no Qt). Used as the line edit's placeholder + tooltip so the
    user sees the live fallback value, not just an opaque ``(unset)``.
    ``effective_env`` is :meth:`ConfigController.effective_env_overrides`
    merged so staged-but-unsaved overrides are reflected immediately.
    """
    shell_var = _SHELL_VAR_FOR_FIELD.get(key)
    if shell_var is not None:
        v = effective_env.get(shell_var) or os.environ.get(shell_var)
        if v:
            return f"(shell ${shell_var}: {v})"
        return f"(shell ${shell_var}: ✗ unset)"

    if key == "employee_id":
        u = os.environ.get("USER") or os.environ.get("USERNAME")
        return f"(shell $USER: {u})" if u else "(fallback: 'unknown')"

    if key == "tech_name":
        candidates = list(project.tech_name_env_vars)
        resolution = resolve_env(set(candidates), effective_env)
        derived = derive_parent_dir_from_env_candidates(
            candidates, resolution.resolved
        )
        if derived:
            return f"(auto-derived: {derived})"
        return f"(no candidate resolved from {candidates})"

    if key == "layer_map":
        return "(default: ${PDK_LAYER_MAP_FILE})"
    if key == "extraction_output_dir":
        return "(default: ${WORK_ROOT}/cds/verify/QCI_PATH_{cell})"
    if key == "intermediate_dir":
        return "(default: ${WORK_ROOT2})"

    return "(unset)"


def _path_resolved_preview(expr: str, effective_env: dict[str, str]) -> str:
    """Resolve ``expr`` for display; never raise.

    Surface filter errors / env misses inline rather than crashing the
    panel — this is read-only preview text.
    """
    try:
        return resolve_path_expr(expr, effective_env)
    except ConfigError as exc:
        return f"(error: {exc})"


#: Static "What is this field, and where do I find the value?" reference
#: shown in QLineEdit tooltips. Hover the field in GUI -> see this text.
#: Live-derived hints (auto-derived / shell value) are appended at the
#: top by ``_refresh_hints``; this map is the static documentation half.
_FIELD_DOCS: dict[str, str] = {
    "work_root": (
        "Workarea root (parent of Auto_ext_pro/). EDA cwd.\n"
        "Source: $WORK_ROOT (set by your project setup script).\n"
        "Docs: docs/CONFIG_GLOSSARY.md#work_root"
    ),
    "verify_root": (
        "Calibre/QRC runset root.\n"
        "Source: $VERIFY_ROOT.\n"
        "Docs: docs/CONFIG_GLOSSARY.md#verify_root"
    ),
    "setup_root": (
        "Cadence assura_tech.lib root.\n"
        "Source: $SETUP_ROOT.\n"
        "Docs: docs/CONFIG_GLOSSARY.md#setup_root"
    ),
    "employee_id": (
        "Your employee/user id; substituted into [[employee_id]] in templates.\n"
        "Source: $USER (auto-derived).\n"
        "Docs: docs/CONFIG_GLOSSARY.md#employee_id"
    ),
    "tech_name": (
        "Cadence tech library name (e.g. HN001).\n"
        "Auto-derived from parent dir of $PDK_TECH_FILE / $PDK_LAYER_MAP_FILE / $PDK_DISPLAY_FILE.\n"
        "Docs: docs/CONFIG_GLOSSARY.md#tech_name"
    ),
    "layer_map": (
        "GDS layer-map file used by strmout.\n"
        "Default: ${PDK_LAYER_MAP_FILE} (resolved at run time).\n"
        "Docs: docs/CONFIG_GLOSSARY.md#layer_map"
    ),
    "extraction_output_dir": (
        "Per-task output dir pattern. Substituted: $X env vars first,\n"
        "then Python str.format keys: {cell} {library} {task_id}\n"
        "{lvs_layout_view} {lvs_source_view}.\n"
        "Default: ${WORK_ROOT}/cds/verify/QCI_PATH_{cell}\n"
        "Docs: docs/CONFIG_GLOSSARY.md#extraction_output_dir"
    ),
    "intermediate_dir": (
        "Cwd for serial EDA invocations + temp si.env staging.\n"
        "Default: ${WORK_ROOT2}\n"
        "Docs: docs/CONFIG_GLOSSARY.md#intermediate_dir"
    ),
}


_PATHS_FIELD_DOCS: dict[str, str] = {
    "calibre_lvs_dir": (
        "Directory holding Calibre LVS rules files\n"
        "(``<basename>.<variant>.qcilvs``). Used by:\n"
        "  templates/calibre/calibre_lvs.qci.j2  (*lvsRulesFile)\n"
        "Typical value: $calibre_source_added_place|parent\n"
        "Docs: docs/CONFIG_GLOSSARY.md#paths"
    ),
    "qrc_deck_dir": (
        "Directory holding QCI_deck artefacts (query_cmd / preserveCellList.txt).\n"
        "Used by:\n"
        "  templates/calibre/calibre_lvs.qci.j2  (*lvsPostTriggers)\n"
        "  templates/quantus/{ext,dspf}.cmd.j2   (-parasitic_blocking_*)\n"
        "Typical value: $VERIFY_ROOT/runset/Calibre_QRC/QRC/<runset>/<pdk>/QCI_deck\n"
        "Docs: docs/CONFIG_GLOSSARY.md#paths"
    ),
}


def _full_tooltip(key: str, live_hint: str) -> str:
    """Compose the rich tooltip: live-derived hint + static field docs."""
    static = _FIELD_DOCS.get(key, "")
    if not static:
        return live_hint
    return f"{live_hint}\n\n{static}"


def _path_tooltip(key: str, live_resolved: str, used_by: list[VarReference]) -> str:
    """Tooltip for a paths.<key> field: resolved preview + used-by list +
    static doc snippet (for the canonical keys)."""
    parts: list[str] = [f"resolves to: {live_resolved}"]
    if used_by:
        parts.append("\nUsed by:")
        for ref in used_by:
            parts.append(
                f"  {ref.template_path.name}:{ref.line_no}  {ref.line_excerpt}"
            )
    else:
        parts.append("\n(not referenced by any current template)")
    static = _PATHS_FIELD_DOCS.get(key)
    if static:
        parts.append("")
        parts.append(static)
    return "\n".join(parts)


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
        # paths.<key> → (line edit, used-by label) for the dynamic Paths group.
        self._path_fields: dict[str, QLineEdit] = {}
        self._path_used_by_labels: dict[str, QLabel] = {}
        # True while we're rebuilding fields from ProjectConfig — prevents
        # editingFinished from feeding spurious edits back into the
        # controller.
        self._populating = False
        # Auto-save edits as soon as a field commits (focus-out / Enter /
        # button click). The explicit Save button stays around for force-
        # save during run / external-conflict resolution. Tests that want
        # to inspect the staged-but-unsaved state flip this to False.
        self._autosave_enabled = True

        self._build_ui()

        controller.config_loaded.connect(self._on_config_loaded)
        controller.config_error.connect(self._on_config_error)
        controller.dirty_changed.connect(self._on_dirty_changed)
        # When a run starts/ends, re-evaluate the Save button: dirty
        # edits made while a run was in flight latched Save in the
        # disabled state because _on_dirty_changed read is_worker_active()
        # at toggle time and nobody re-fired the signal afterwards.
        run_tab.worker_state_changed.connect(self._on_worker_state_changed)

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

        # Paths group: dynamic key/value rows for project.paths. Each row
        # carries an edit field plus a "Used by" annotation derived from
        # scanning the configured templates.
        paths_box = QGroupBox(
            "Paths (project.paths — referenced as [[key]] in templates)", self
        )
        self._paths_form = QFormLayout(paths_box)
        paths_btn_row = QHBoxLayout()
        add_path_btn = QPushButton("+ Add path", self)
        add_path_btn.clicked.connect(self._on_add_path_clicked)
        paths_btn_row.addStretch(1)
        paths_btn_row.addWidget(add_path_btn)
        self._paths_form.addRow(paths_btn_row)
        root.addWidget(paths_box)

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

            self._rebuild_paths_rows(project)

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
            self._refresh_hints()
            # Fresh load clears dirty; sync button state defensively in
            # case no dirty_changed signal fired (was_dirty=False path).
            self._on_dirty_changed(self._controller.is_dirty)
        finally:
            self._populating = False

    def _rebuild_paths_rows(self, project: ProjectConfig | None) -> None:
        """Tear down any existing per-path widgets and rebuild from
        ``project.paths``."""
        # Drop existing rows. Skip the trailing button row (last row).
        # QFormLayout exposes rows by index; remove from end so indices
        # don't shift.
        while self._paths_form.rowCount() > 1:
            self._paths_form.removeRow(0)
        self._path_fields.clear()
        self._path_used_by_labels.clear()

        if project is None or not project.paths:
            return

        used_by_index = self._collect_used_by_index(project)
        for key in sorted(project.paths):
            self._add_path_row(
                key, project.paths[key], used_by_index.get(key, [])
            )

    def _collect_used_by_index(
        self, project: ProjectConfig
    ) -> dict[str, list[VarReference]]:
        """Scan every configured template for ``[[X]]`` references and
        index them by var name. Templates that don't resolve (missing
        file, etc.) are skipped silently."""
        template_paths: list[Path] = []
        seen: set[Path] = set()
        for stage in ("si", "calibre", "quantus", "jivaro"):
            tp = getattr(project.templates, stage, None)
            if tp is None:
                continue
            resolved = resolve_template_path(
                tp,
                auto_ext_root=self._controller.auto_ext_root,
                workarea=self._controller.workarea,
            )
            if resolved in seen:
                continue
            seen.add(resolved)
            template_paths.append(resolved)
        refs = collect_var_references(template_paths)
        index: dict[str, list[VarReference]] = {}
        for ref in refs:
            index.setdefault(ref.var_name, []).append(ref)
        return index

    def _add_path_row(
        self, key: str, value: str, used_by: list[VarReference]
    ) -> None:
        line = QLineEdit(self)
        line.setText(value)
        line.editingFinished.connect(
            lambda k=key: self._on_path_field_edited(k)
        )
        self._path_fields[key] = line

        used_by_label = QLabel(self)
        used_by_label.setStyleSheet("color: #666; font-size: 11px;")
        used_by_label.setWordWrap(True)
        self._path_used_by_labels[key] = used_by_label

        # Compose row widget: line edit + remove button + used-by below.
        row_widget = QWidget(self)
        vbox = QVBoxLayout(row_widget)
        vbox.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(line, stretch=1)
        remove_btn = QPushButton("−", self)
        remove_btn.setMaximumWidth(28)
        remove_btn.setToolTip(f"Remove paths.{key} entry")
        remove_btn.clicked.connect(
            lambda _=False, k=key: self._on_remove_path_clicked(k)
        )
        top.addWidget(remove_btn)
        vbox.addLayout(top)
        vbox.addWidget(used_by_label)

        # Insert before the trailing button row.
        insert_at = max(0, self._paths_form.rowCount() - 1)
        self._paths_form.insertRow(insert_at, QLabel(f"{key}:", self), row_widget)

        # Initial used-by + tooltip render — gets refreshed by _refresh_hints
        # whenever effective_env changes.
        self._refresh_path_row(key, used_by)

    def _refresh_path_row(
        self, key: str, used_by: list[VarReference]
    ) -> None:
        line = self._path_fields.get(key)
        if line is None:
            return
        effective = self._controller.effective_env_overrides()
        expr = line.text() or self._controller.project.paths.get(key, "")
        resolved = _path_resolved_preview(expr, effective)
        line.setPlaceholderText(resolved)
        line.setToolTip(_path_tooltip(key, resolved, used_by))
        label = self._path_used_by_labels.get(key)
        if label is not None:
            if used_by:
                lines = [
                    f"↳ {ref.template_path.name}:{ref.line_no}  {ref.line_excerpt}"
                    for ref in used_by
                ]
                label.setText("\n".join(lines))
            else:
                label.setText("↳ (no template references this path)")

    def _refresh_hints(self) -> None:
        """Recompute placeholder + tooltip per field from the current env state."""
        project = self._controller.project
        if project is None:
            for line in self._fields.values():
                line.setPlaceholderText("(no config)")
                line.setToolTip("")
            return
        effective = self._controller.effective_env_overrides()
        for key, line in self._fields.items():
            hint = _hint_for_field(key, project, effective)
            line.setPlaceholderText(hint)
            line.setToolTip(_full_tooltip(key, hint))

        # Refresh paths rows too — staged env edits change resolved values.
        used_by_index = self._collect_used_by_index(project)
        for key in self._path_fields:
            self._refresh_path_row(key, used_by_index.get(key, []))

    @staticmethod
    def _read_field_value(project: Any, key: str) -> str:
        if project is None:
            return ""
        v = getattr(project, key, None)
        return "" if v is None else str(v)

    # ---- field edit → stage ------------------------------------------

    def _on_field_edited(self, key: str) -> None:
        if self._populating:
            return
        text = self._fields[key].text().strip()
        value: Any = None if text == "" else text
        self._controller.stage_edits({key: value})
        self._maybe_autosave()

    def _on_path_field_edited(self, key: str) -> None:
        if self._populating:
            return
        line = self._path_fields.get(key)
        if line is None:
            return
        text = line.text().strip()
        value: Any = None if text == "" else text
        self._controller.stage_edits({f"paths.{key}": value})
        # Refresh resolved preview after staging.
        used_by_index = self._collect_used_by_index(self._controller.project)
        self._refresh_path_row(key, used_by_index.get(key, []))
        self._maybe_autosave()

    def _on_add_path_clicked(self) -> None:
        if self._controller.project is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "Add path",
            "Path key (the [[name]] templates will reference):",
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self._controller.project.paths or name in self._path_fields:
            QMessageBox.warning(
                self,
                "Path exists",
                f"paths.{name} already exists. Edit it directly above.",
            )
            return
        # Stage the new key with an empty value but DO NOT autosave: an
        # empty path expression isn't useful on disk, and the row's
        # editingFinished will autosave once the user actually fills it.
        self._controller.stage_edits({f"paths.{name}": ""})
        used_by_index = self._collect_used_by_index(self._controller.project)
        self._add_path_row(name, "", used_by_index.get(name, []))

    def _on_remove_path_clicked(self, key: str) -> None:
        self._controller.stage_edits({f"paths.{key}": None})
        line = self._path_fields.pop(key, None)
        if line is not None:
            self._path_used_by_labels.pop(key, None)
            # Find and remove the matching row by walking the form.
            for r in range(self._paths_form.rowCount() - 1):
                label_item = self._paths_form.itemAt(r, QFormLayout.LabelRole)
                if label_item is None:
                    continue
                lbl = label_item.widget()
                if isinstance(lbl, QLabel) and lbl.text() == f"{key}:":
                    self._paths_form.removeRow(r)
                    break
        self._maybe_autosave()

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
        self._refresh_hints()
        self._maybe_autosave()

    def _on_clear_override(self, name: str) -> None:
        self._controller.stage_edits({f"env_overrides.{name}": None})
        self._refresh_env_table()
        self._refresh_hints()
        self._maybe_autosave()

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

    def _on_worker_state_changed(self, _running: bool) -> None:
        """Run started or finished — recompute Save button state.

        Edits staged while a run was in flight left Save disabled (because
        the worker_active gate). Without this hook the button would stay
        disabled forever after the run ended (until something else nudged
        ``dirty_changed``).
        """
        self._on_dirty_changed(self._controller.is_dirty)

    def _maybe_autosave(self) -> None:
        """Auto-save the staged edit if conditions allow.

        Triggered after every staging point (field edit, env-override
        toggle, paths add/remove). Skips when:
          - autosave disabled (test-only override via ``_autosave_enabled``)
          - no edits actually staged (e.g. value unchanged)
          - a run is in flight (Save would clobber YAML during a run
            that may be reading templates/output paths from it)
          - project.yaml changed on disk since load (let the user
            consciously force-save through the warning dialog)

        The explicit Save button stays around for these skip cases and
        for users who prefer a keystroke-free workflow.
        """
        if not self._autosave_enabled:
            return
        if not self._controller.is_dirty:
            return
        if self._run_tab.is_worker_active():
            return
        if self._controller.has_external_change():
            return
        self._controller.save()

    def _on_config_error(self, message: str) -> None:
        if self.isVisible():
            QMessageBox.warning(self, "Config error", message)


def _mono_font() -> QFont:
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f
