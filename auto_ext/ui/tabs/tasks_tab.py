"""Tasks tab: editor for ``tasks.yaml`` with cartesian-expansion preview.

Reads/writes via the shared :class:`ConfigController`. Each ``TaskSpec``
is represented as a plain dict mirror of the yaml shape; the user
mutates axis fields (list-valued) via :class:`TagListEdit` chips and
per-cell jivaro via a small sub-table. Every edit re-runs
:func:`_expand_spec` in-memory so the bottom pane always shows what the
current (possibly unsaved) state would produce.

The preview's per-row checkbox maps to the spec's ``exclude`` list:
unchecking a row appends a partial-selector entry; re-checking removes
any matching entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.config import (
    ExcludeMatch,
    JivaroOverride,
    TaskSpec,
    _expand_spec,
)
from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.core.manifest import (
    TemplateManifest,
    current_knob_value,
    load_manifest,
)
from auto_ext.core.template import resolve_template_path
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.models import EXCLUDED_ROW_COLOR
from auto_ext.ui.widgets.knob_editor import KnobEditor
from auto_ext.ui.widgets.tag_list_edit import TagListEdit

if TYPE_CHECKING:
    from auto_ext.ui.tabs.run_tab import RunTab


_AXIS_FIELDS = ("library", "cell", "lvs_layout_view", "lvs_source_view")

_JIVARO_TRI_STATES = ["(inherit)", "true", "false"]

_KNOB_STAGES: tuple[str, ...] = ("si", "calibre", "quantus", "jivaro")


class TasksTab(QWidget):
    """TaskSpec CRUD + cartesian preview, bound to a ConfigController."""

    #: Emitted when the user asks to swap to the Log tab — currently
    #: unused (Log tab lands via RunTab). Reserved for Phase 5.5+.
    log_requested = pyqtSignal(Path)

    def __init__(
        self,
        controller: ConfigController,
        run_tab: "RunTab",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._run_tab = run_tab
        self._specs: list[dict[str, Any]] = []
        self._current_index: int = -1
        #: True while we're rebuilding the editor from a spec dict —
        #: suppresses signals that would otherwise feed back into
        #: ``_specs`` and double-stage edits.
        self._populating = False
        #: Defer preview refresh so rapid edits (typing) coalesce.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._refresh_preview)

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

        splitter = QSplitter(Qt.Vertical, self)
        splitter.addWidget(self._build_editor_pane())
        splitter.addWidget(self._build_preview_pane())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, stretch=1)

    def _build_editor_pane(self) -> QWidget:
        container = QWidget(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        # Left: spec list + add/remove/up/down.
        left = QWidget(container)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Specs:", left))

        self._spec_list = QListWidget(left)
        self._spec_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._spec_list.currentRowChanged.connect(self._on_spec_row_changed)
        left_layout.addWidget(self._spec_list, stretch=1)

        btn_row = QHBoxLayout()
        self._add_spec_btn = QPushButton("+", left)
        self._add_spec_btn.setToolTip("Add a new TaskSpec")
        self._add_spec_btn.clicked.connect(self._on_add_spec)
        self._remove_spec_btn = QPushButton("−", left)
        self._remove_spec_btn.setToolTip("Remove the selected TaskSpec")
        self._remove_spec_btn.clicked.connect(self._on_remove_spec)
        self._up_spec_btn = QPushButton("↑", left)
        self._up_spec_btn.clicked.connect(lambda: self._move_spec(-1))
        self._down_spec_btn = QPushButton("↓", left)
        self._down_spec_btn.clicked.connect(lambda: self._move_spec(1))
        for b in (
            self._add_spec_btn,
            self._remove_spec_btn,
            self._up_spec_btn,
            self._down_spec_btn,
        ):
            b.setFixedWidth(32)
            btn_row.addWidget(b)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)

        # Right: editor for the selected spec.
        right = QScrollArea(container)
        right.setWidgetResizable(True)
        self._editor_body = QWidget(right)
        self._editor_layout = QVBoxLayout(self._editor_body)
        right.setWidget(self._editor_body)

        self._build_editor_widgets()

        layout.addWidget(left, stretch=1)
        layout.addWidget(right, stretch=3)
        return container

    def _build_editor_widgets(self) -> None:
        # Axis group: four TagListEdit widgets.
        axis_box = QGroupBox("Axes (cartesian expansion)", self._editor_body)
        axis_form = QFormLayout(axis_box)
        self._axis_widgets: dict[str, TagListEdit] = {}
        for field in _AXIS_FIELDS:
            w = TagListEdit(add_prompt=f"Add {field}")
            w.values_changed.connect(
                lambda vals, f=field: self._on_axis_changed(f, vals)
            )
            self._axis_widgets[field] = w
            axis_form.addRow(QLabel(field + ":"), w)
        self._editor_layout.addWidget(axis_box)

        # Scalar group: ground_net / out_file / continue_on_lvs_fail.
        scalar_box = QGroupBox("Scalars", self._editor_body)
        scalar_form = QFormLayout(scalar_box)
        self._ground_net_edit = QLineEdit(scalar_box)
        self._ground_net_edit.editingFinished.connect(
            lambda: self._on_scalar_edited("ground_net", self._ground_net_edit.text())
        )
        scalar_form.addRow("ground_net:", self._ground_net_edit)
        self._out_file_edit = QLineEdit(scalar_box)
        self._out_file_edit.setPlaceholderText("(unset)")
        self._out_file_edit.editingFinished.connect(
            lambda: self._on_scalar_edited("out_file", self._out_file_edit.text())
        )
        scalar_form.addRow("out_file:", self._out_file_edit)
        self._continue_lvs_check = QCheckBox("continue_on_lvs_fail", scalar_box)
        self._continue_lvs_check.toggled.connect(
            lambda checked: self._on_scalar_edited("continue_on_lvs_fail", checked)
        )
        scalar_form.addRow(self._continue_lvs_check)
        self._editor_layout.addWidget(scalar_box)

        # jivaro default group.
        jivaro_box = QGroupBox("jivaro (spec default)", self._editor_body)
        jivaro_form = QFormLayout(jivaro_box)
        self._jivaro_enabled = QCheckBox("enabled", jivaro_box)
        self._jivaro_enabled.toggled.connect(
            lambda c: self._on_jivaro_default_edited("enabled", c)
        )
        jivaro_form.addRow(self._jivaro_enabled)
        self._jivaro_freq = QLineEdit(jivaro_box)
        self._jivaro_freq.setPlaceholderText("(unset)")
        self._jivaro_freq.editingFinished.connect(
            lambda: self._on_jivaro_default_edited(
                "frequency_limit", self._jivaro_freq.text()
            )
        )
        jivaro_form.addRow("frequency_limit:", self._jivaro_freq)
        self._jivaro_err = QLineEdit(jivaro_box)
        self._jivaro_err.setPlaceholderText("(unset)")
        self._jivaro_err.editingFinished.connect(
            lambda: self._on_jivaro_default_edited("error_max", self._jivaro_err.text())
        )
        jivaro_form.addRow("error_max:", self._jivaro_err)
        self._editor_layout.addWidget(jivaro_box)

        # Per-task knob overrides — fold by default. Knobs are usually
        # tuned at the project layer; this section is for "this task
        # needs different values than the rest of the project".
        knobs_box = QGroupBox(
            "knobs (advanced — per-task overrides)", self._editor_body
        )
        knobs_box.setCheckable(True)
        knobs_box.setChecked(False)
        knobs_box.setToolTip(
            "Per-task knob overrides on top of the project layer.\n"
            "\n"
            "Precedence (low → high):\n"
            "  manifest.default < project.knobs < task.knobs < --knob CLI\n"
            "\n"
            "Use this when one spec needs different knob values than the\n"
            "project default — e.g. one task wants a tighter\n"
            "exclude_floating_nets_limit while the rest of the project\n"
            "uses the default. To run the same cell with two different\n"
            "configs side by side, also customise\n"
            "extraction_output_dir to include {lvs_layout_view} or\n"
            "{task_id} so each task lands in its own dir.\n"
            "\n"
            "Most projects leave this folded — auto-expands when loaded."
        )
        kb_outer = QVBoxLayout(knobs_box)
        self._knobs_form_host = QWidget(knobs_box)
        self._knobs_form_layout = QVBoxLayout(self._knobs_form_host)
        self._knobs_form_layout.setContentsMargins(0, 0, 0, 0)
        kb_outer.addWidget(self._knobs_form_host)
        self._knobs_form_host.setVisible(False)
        knobs_box.toggled.connect(self._knobs_form_host.setVisible)
        self._knobs_box = knobs_box
        # (stage, knob_name) -> KnobEditor for the currently rendered spec.
        self._task_knob_editors: dict[tuple[str, str], KnobEditor] = {}
        self._editor_layout.addWidget(knobs_box)

        # Per-cell jivaro override table — uncommon, fold by default so
        # casual users don't have to mentally model "what is this".
        override_box = QGroupBox(
            "jivaro_overrides (advanced — per-cell tweaks)", self._editor_body
        )
        override_box.setCheckable(True)
        override_box.setChecked(False)
        override_box.setToolTip(
            "Per-cell overrides on top of this spec's jivaro defaults.\n"
            "\n"
            "Use this when most cells in the cell axis share config but one\n"
            "or two need different jivaro.enabled / frequency_limit /\n"
            "error_max — saves splitting one spec into many.\n"
            "\n"
            "Example:\n"
            "  cell: [INV1, AMP2, BUF3]\n"
            "  jivaro: {enabled: true, frequency_limit: 14}\n"
            "  jivaro_overrides:\n"
            "    AMP2: {enabled: false}      # AMP2 crashes jivaro, skip it\n"
            "\n"
            "Most projects leave this folded — auto-expands when loaded."
        )
        ov_layout = QVBoxLayout(override_box)
        self._override_table = QTableWidget(override_box)
        self._override_table.setColumnCount(5)
        self._override_table.setHorizontalHeaderLabels(
            ["cell", "enabled", "frequency_limit", "error_max", "clear"]
        )
        self._override_table.verticalHeader().setVisible(False)
        self._override_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._override_table.setSelectionMode(QAbstractItemView.NoSelection)
        h = self._override_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        ov_layout.addWidget(self._override_table)
        # Folding: hide the table when the box is unchecked. Qt's default
        # behaviour for a checkable QGroupBox only disables children
        # (still visible) — we want true fold so the editor is compact.
        self._override_table.setVisible(False)
        override_box.toggled.connect(self._override_table.setVisible)
        self._override_box = override_box
        self._editor_layout.addWidget(override_box)

        self._editor_layout.addStretch()

    def _build_preview_pane(self) -> QWidget:
        box = QGroupBox("Cartesian expansion preview", self)
        layout = QVBoxLayout(box)
        hint = QLabel(
            "Uncheck a row to exclude that combination from the expansion. "
            "Rows reflect unsaved edits; click Save to persist.",
            box,
        )
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        self._preview_table = QTableWidget(box)
        self._preview_table.setColumnCount(7)
        self._preview_table.setHorizontalHeaderLabels(
            [
                "include",
                "task_id",
                "library",
                "cell",
                "lvs_layout_view",
                "lvs_source_view",
                "jivaro.enabled",
            ]
        )
        self._preview_table.verticalHeader().setVisible(False)
        self._preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._preview_table.setSelectionMode(QAbstractItemView.NoSelection)
        h = self._preview_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        for c in range(2, 7):
            h.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        layout.addWidget(self._preview_table, stretch=1)
        return box

    # ---- data <-> UI -------------------------------------------------

    def _on_config_loaded(self, config_dir: object) -> None:
        path = Path(config_dir) if config_dir is not None else None
        self._config_label.setText(str(path) if path else "(no config loaded)")
        self._specs = self._controller.task_specs_raw()
        self._populate_spec_list()
        if self._specs:
            self._spec_list.setCurrentRow(0)
        else:
            self._current_index = -1
            self._clear_editor()
            self._refresh_preview()
        self._on_dirty_changed(self._controller.is_dirty)

    def _populate_spec_list(self) -> None:
        self._spec_list.blockSignals(True)
        self._spec_list.clear()
        for i, spec in enumerate(self._specs):
            self._spec_list.addItem(QListWidgetItem(_summarize_spec(i, spec)))
        self._spec_list.blockSignals(False)

    def _on_spec_row_changed(self, row: int) -> None:
        self._current_index = row
        if 0 <= row < len(self._specs):
            self._populate_editor(self._specs[row])
        else:
            self._clear_editor()
        self._refresh_preview()

    def _populate_editor(self, spec: dict[str, Any]) -> None:
        self._populating = True
        try:
            for field in _AXIS_FIELDS:
                v = spec.get(field, [])
                if isinstance(v, str):
                    values = [v]
                elif isinstance(v, list):
                    values = [str(x) for x in v]
                else:
                    values = []
                self._axis_widgets[field].set_values(values)

            self._ground_net_edit.setText(spec.get("ground_net", "vss"))
            self._out_file_edit.setText(spec.get("out_file") or "")
            self._continue_lvs_check.setChecked(bool(spec.get("continue_on_lvs_fail", False)))

            j = spec.get("jivaro") or {}
            self._jivaro_enabled.setChecked(bool(j.get("enabled", False)))
            self._jivaro_freq.setText(_fmt_num(j.get("frequency_limit")))
            self._jivaro_err.setText(_fmt_num(j.get("error_max")))

            self._rebuild_override_table(spec)
            self._rebuild_knobs_form(spec)
        finally:
            self._populating = False

    def _clear_editor(self) -> None:
        self._populating = True
        try:
            for w in self._axis_widgets.values():
                w.set_values([])
            self._ground_net_edit.setText("")
            self._out_file_edit.setText("")
            self._continue_lvs_check.setChecked(False)
            self._jivaro_enabled.setChecked(False)
            self._jivaro_freq.setText("")
            self._jivaro_err.setText("")
            self._override_table.setRowCount(0)
            self._clear_knobs_form()
        finally:
            self._populating = False

    def _rebuild_override_table(self, spec: dict[str, Any]) -> None:
        cell_axis = spec.get("cell", [])
        if isinstance(cell_axis, str):
            cell_axis = [cell_axis]
        overrides = spec.get("jivaro_overrides") or {}
        # Auto-track the fold to match the loaded spec: expand when the
        # spec actually uses overrides (so the data isn't silently
        # hidden), fold otherwise (so a freshly selected spec without
        # any overrides doesn't show an empty table inviting confusion).
        # User toggles after load are honoured until the next spec swap.
        want_expanded = bool(overrides)
        if self._override_box.isChecked() != want_expanded:
            self._override_box.blockSignals(True)
            try:
                self._override_box.setChecked(want_expanded)
                self._override_table.setVisible(want_expanded)
            finally:
                self._override_box.blockSignals(False)
        # Union: cells from axis + cells already in overrides (covers
        # stale entries so user can clear them).
        all_cells: list[str] = list(cell_axis)
        for k in overrides:
            if k not in all_cells:
                all_cells.append(k)

        self._override_table.setRowCount(len(all_cells))
        for row, cell_name in enumerate(all_cells):
            ov = overrides.get(cell_name) or {}
            name_item = QTableWidgetItem(cell_name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            if cell_name not in cell_axis:
                name_item.setForeground(QBrush(QColor("#c83232")))
                name_item.setToolTip(
                    f"'{cell_name}' is not in the cell axis — stale override, "
                    f"click 'clear' to remove"
                )
            self._override_table.setItem(row, 0, name_item)

            enabled_combo = QComboBox()
            enabled_combo.addItems(_JIVARO_TRI_STATES)
            enabled_combo.setCurrentIndex(_enabled_to_index(ov.get("enabled")))
            enabled_combo.currentIndexChanged.connect(
                lambda idx, c=cell_name: self._on_override_enabled_changed(c, idx)
            )
            self._override_table.setCellWidget(row, 1, enabled_combo)

            freq_edit = QLineEdit(_fmt_num(ov.get("frequency_limit")))
            freq_edit.setPlaceholderText("(inherit)")
            freq_edit.editingFinished.connect(
                lambda c=cell_name, e=freq_edit: self._on_override_num_changed(
                    c, "frequency_limit", e.text()
                )
            )
            self._override_table.setCellWidget(row, 2, freq_edit)

            err_edit = QLineEdit(_fmt_num(ov.get("error_max")))
            err_edit.setPlaceholderText("(inherit)")
            err_edit.editingFinished.connect(
                lambda c=cell_name, e=err_edit: self._on_override_num_changed(
                    c, "error_max", e.text()
                )
            )
            self._override_table.setCellWidget(row, 3, err_edit)

            clear_btn = QPushButton("clear")
            clear_btn.setEnabled(cell_name in overrides)
            clear_btn.clicked.connect(
                lambda _=False, c=cell_name: self._on_override_cleared(c)
            )
            self._override_table.setCellWidget(row, 4, clear_btn)

    # ---- per-task knobs ---------------------------------------------

    def _clear_knobs_form(self) -> None:
        """Drop every per-stage subwidget and clear the editor lookup."""
        while self._knobs_form_layout.count() > 0:
            item = self._knobs_form_layout.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._task_knob_editors.clear()

    def _rebuild_knobs_form(self, spec: dict[str, Any]) -> None:
        """Re-render KnobEditor rows for the loaded spec.

        Walks the four stages, resolves each one's template against the
        controller's auto_ext_root + workarea, loads the manifest, and
        renders one editor per declared knob. The editor's
        ``is_default`` reflects whether this **task** overrides the
        knob; the ``(default: <X>)`` hint shows the effective lower-
        layer fallback (project layer or manifest default).
        """
        self._clear_knobs_form()

        project = self._controller.project
        if project is None:
            return

        task_knobs = spec.get("knobs") or {}
        # Auto-track fold to spec content: any task knob set → expand.
        has_task_knobs = any(task_knobs.get(s) for s in _KNOB_STAGES)
        if self._knobs_box.isChecked() != has_task_knobs:
            self._knobs_box.blockSignals(True)
            try:
                self._knobs_box.setChecked(has_task_knobs)
                self._knobs_form_host.setVisible(has_task_knobs)
            finally:
                self._knobs_box.blockSignals(False)

        any_section_added = False
        for stage in _KNOB_STAGES:
            section = self._build_knob_section(spec, stage, task_knobs)
            if section is None:
                continue
            self._knobs_form_layout.addWidget(section)
            any_section_added = True

        if not any_section_added:
            hint = QLabel(
                "(no manifest knobs declared by any bound template)",
                self._knobs_form_host,
            )
            hint.setStyleSheet("color: #888; font-style: italic;")
            self._knobs_form_layout.addWidget(hint)

    def _build_knob_section(
        self,
        spec: dict[str, Any],
        stage: str,
        task_knobs: dict[str, dict[str, Any]],
    ) -> QGroupBox | None:
        """Return a per-stage QGroupBox of KnobEditors, or None if the
        stage has no template / manifest / declared knobs."""
        manifest = self._manifest_for_stage(spec, stage)
        if manifest is None or not manifest.knobs:
            return None

        project = self._controller.project
        assert project is not None

        section = QGroupBox(stage, self._knobs_form_host)
        form = QFormLayout(section)
        stage_task_layer = task_knobs.get(stage) or {}

        for knob_name, knob_spec in manifest.knobs.items():
            try:
                lower_value, _provenance = current_knob_value(
                    manifest, project.knobs, stage, knob_name
                )
            except ConfigError:
                # Stale knob in project layer that's no longer in the
                # manifest. Skip rather than break the whole section;
                # the project layer's stale entry is its own problem
                # that the project tab is not yet reporting either.
                continue

            has_task_override = knob_name in stage_task_layer
            shown_value = (
                stage_task_layer[knob_name] if has_task_override else lower_value
            )
            editor = KnobEditor(knob_name, knob_spec, section)
            editor.set_value(
                shown_value,
                is_default=not has_task_override,
                default_hint=lower_value,
            )
            editor.value_changed.connect(
                lambda name, val, s=stage: self._on_task_knob_changed(s, name, val)
            )
            label = QLabel(knob_name + ":", section)
            if knob_spec.description:
                label.setToolTip(knob_spec.description)
            form.addRow(label, editor)
            self._task_knob_editors[(stage, knob_name)] = editor

        return section

    def _manifest_for_stage(
        self, spec: dict[str, Any], stage: str
    ) -> TemplateManifest | None:
        """Resolve this spec's template path for ``stage`` and load its
        manifest. Returns None on missing template / unreadable manifest."""
        project = self._controller.project
        if project is None:
            return None
        # spec-level template overrides project-level
        spec_tp_raw = (spec.get("templates") or {}).get(stage)
        if spec_tp_raw:
            tp_input = Path(spec_tp_raw)
        else:
            proj_tp = getattr(project.templates, stage, None)
            if proj_tp is None:
                return None
            tp_input = Path(proj_tp)
        tp = resolve_template_path(
            tp_input,
            auto_ext_root=self._controller.auto_ext_root,
            workarea=self._controller.workarea,
        )
        if not tp.is_file():
            return None
        try:
            return load_manifest(tp)
        except (AutoExtError, OSError):
            return None

    def _on_task_knob_changed(
        self, stage: str, knob_name: str, value: Any
    ) -> None:
        def mutate(spec: dict[str, Any]) -> None:
            knobs = dict(spec.get("knobs") or {})
            stage_layer = dict(knobs.get(stage) or {})
            if value is None:
                stage_layer.pop(knob_name, None)
            else:
                stage_layer[knob_name] = value
            if stage_layer:
                knobs[stage] = stage_layer
            else:
                knobs.pop(stage, None)
            if knobs:
                spec["knobs"] = knobs
            else:
                spec.pop("knobs", None)

        self._mutate_current_spec(mutate)
        # Re-render so (default: <X>) hint + reset-button state stay
        # in sync with the new spec content.
        spec = self._current_spec()
        if spec is not None:
            self._rebuild_knobs_form(spec)

    # ---- editor edits -> _specs -------------------------------------

    def _current_spec(self) -> dict[str, Any] | None:
        if 0 <= self._current_index < len(self._specs):
            return self._specs[self._current_index]
        return None

    def _mutate_current_spec(self, mutator) -> None:
        if self._populating:
            return
        spec = self._current_spec()
        if spec is None:
            return
        mutator(spec)
        self._specs[self._current_index] = spec
        item = self._spec_list.item(self._current_index)
        if item is not None:
            item.setText(_summarize_spec(self._current_index, spec))
        self._stage_and_schedule_refresh()

    def _on_axis_changed(self, field: str, values: list[str]) -> None:
        def mutate(spec: dict[str, Any]) -> None:
            if len(values) == 1:
                spec[field] = values[0]
            else:
                spec[field] = list(values)
            # If cell axis changed, rebuild override table (new cells may
            # appear, old ones may go stale).
            if field == "cell" and not self._populating:
                self._rebuild_override_table(spec)

        self._mutate_current_spec(mutate)

    def _on_scalar_edited(self, key: str, value: Any) -> None:
        def mutate(spec: dict[str, Any]) -> None:
            if isinstance(value, str):
                stripped = value.strip()
                if key == "out_file":
                    if stripped:
                        spec[key] = stripped
                    else:
                        spec.pop(key, None)
                else:
                    spec[key] = stripped or spec.get(key, "vss")
            else:
                spec[key] = value

        self._mutate_current_spec(mutate)

    def _on_jivaro_default_edited(self, subkey: str, value: Any) -> None:
        def mutate(spec: dict[str, Any]) -> None:
            j = dict(spec.get("jivaro") or {})
            if subkey == "enabled":
                j["enabled"] = bool(value)
            else:
                text = value.strip() if isinstance(value, str) else ""
                parsed = _parse_num(text)
                if parsed is None:
                    j.pop(subkey, None)
                else:
                    j[subkey] = parsed
            spec["jivaro"] = j

        self._mutate_current_spec(mutate)

    def _on_override_enabled_changed(self, cell_name: str, index: int) -> None:
        def mutate(spec: dict[str, Any]) -> None:
            overrides = dict(spec.get("jivaro_overrides") or {})
            entry = dict(overrides.get(cell_name) or {})
            if index == 0:
                entry.pop("enabled", None)
            else:
                entry["enabled"] = index == 1  # "true" → True, "false" → False
            if entry:
                overrides[cell_name] = entry
            else:
                overrides.pop(cell_name, None)
            if overrides:
                spec["jivaro_overrides"] = overrides
            else:
                spec.pop("jivaro_overrides", None)

        self._mutate_current_spec(mutate)
        # Refresh only this row so the clear button reflects new state.
        spec = self._current_spec()
        if spec is not None:
            self._rebuild_override_table(spec)

    def _on_override_num_changed(
        self, cell_name: str, subkey: str, text: str
    ) -> None:
        parsed = _parse_num(text.strip())

        def mutate(spec: dict[str, Any]) -> None:
            overrides = dict(spec.get("jivaro_overrides") or {})
            entry = dict(overrides.get(cell_name) or {})
            if parsed is None:
                entry.pop(subkey, None)
            else:
                entry[subkey] = parsed
            if entry:
                overrides[cell_name] = entry
            else:
                overrides.pop(cell_name, None)
            if overrides:
                spec["jivaro_overrides"] = overrides
            else:
                spec.pop("jivaro_overrides", None)

        self._mutate_current_spec(mutate)

    def _on_override_cleared(self, cell_name: str) -> None:
        def mutate(spec: dict[str, Any]) -> None:
            overrides = dict(spec.get("jivaro_overrides") or {})
            overrides.pop(cell_name, None)
            if overrides:
                spec["jivaro_overrides"] = overrides
            else:
                spec.pop("jivaro_overrides", None)

        self._mutate_current_spec(mutate)
        spec = self._current_spec()
        if spec is not None:
            self._rebuild_override_table(spec)

    # ---- add / remove / reorder --------------------------------------

    def _on_add_spec(self) -> None:
        new_spec = {
            "library": "",
            "cell": "",
            "lvs_layout_view": "layout",
            "lvs_source_view": "schematic",
            "ground_net": "vss",
            "jivaro": {"enabled": False},
        }
        self._specs.append(new_spec)
        self._populate_spec_list()
        self._spec_list.setCurrentRow(len(self._specs) - 1)
        self._stage_and_schedule_refresh()

    def _on_remove_spec(self) -> None:
        if self._current_index < 0 or self._current_index >= len(self._specs):
            return
        if len(self._specs) == 1:
            QMessageBox.warning(
                self,
                "Cannot remove",
                "tasks.yaml must contain at least one spec.",
            )
            return
        del self._specs[self._current_index]
        self._populate_spec_list()
        new_row = min(self._current_index, len(self._specs) - 1)
        self._spec_list.setCurrentRow(new_row)
        self._stage_and_schedule_refresh()

    def _move_spec(self, delta: int) -> None:
        i = self._current_index
        j = i + delta
        if i < 0 or j < 0 or j >= len(self._specs):
            return
        self._specs[i], self._specs[j] = self._specs[j], self._specs[i]
        self._populate_spec_list()
        self._spec_list.setCurrentRow(j)
        self._stage_and_schedule_refresh()

    # ---- preview -----------------------------------------------------

    def _stage_and_schedule_refresh(self) -> None:
        self._controller.stage_tasks_edits(self._specs)
        self._refresh_timer.start()

    def _refresh_preview(self) -> None:
        self._preview_table.setRowCount(0)
        spec_dict = self._current_spec()
        if spec_dict is None:
            return
        try:
            spec = TaskSpec.model_validate(spec_dict)
            tasks = _expand_spec(spec, self._current_index, None, Path("<preview>"))
        except (ConfigError, Exception) as exc:  # noqa: BLE001
            self._preview_table.setRowCount(1)
            err = QTableWidgetItem(f"(invalid spec: {exc})")
            err.setForeground(QBrush(QColor("#c83232")))
            self._preview_table.setItem(0, 0, err)
            self._preview_table.setSpan(0, 0, 1, 7)
            return

        excludes = list(spec.exclude)
        # Build the full cartesian (ignoring exclude) for UX — excluded
        # rows still render, just unchecked + greyed out.
        included_task_ids = {t.task_id for t in tasks}
        full = self._full_cartesian(spec)
        self._preview_table.setRowCount(len(full))
        mono = _mono_font()
        for row, combo in enumerate(full):
            is_included = combo["task_id"] in included_task_ids

            check = QCheckBox()
            check.setChecked(is_included)
            check.toggled.connect(
                lambda checked, c=combo: self._on_preview_toggled(c, checked)
            )
            self._preview_table.setCellWidget(row, 0, check)

            cells = [
                combo["task_id"],
                combo["library"],
                combo["cell"],
                combo["lvs_layout_view"],
                combo["lvs_source_view"],
                _jivaro_summary_for(spec, combo["cell"]),
            ]
            for col_offset, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFont(mono if col_offset == 0 else QFont())
                if not is_included:
                    item.setForeground(QBrush(QColor(EXCLUDED_ROW_COLOR)))
                    f = item.font()
                    f.setStrikeOut(True)
                    item.setFont(f)
                self._preview_table.setItem(row, 1 + col_offset, item)
        _ = excludes  # silence lint; selectors inspected via included_task_ids

    def _full_cartesian(self, spec: TaskSpec) -> list[dict[str, str]]:
        libs = spec.library if isinstance(spec.library, list) else [spec.library]
        cells = spec.cell if isinstance(spec.cell, list) else [spec.cell]
        layouts = (
            spec.lvs_layout_view
            if isinstance(spec.lvs_layout_view, list)
            else [spec.lvs_layout_view]
        )
        sources = (
            spec.lvs_source_view
            if isinstance(spec.lvs_source_view, list)
            else [spec.lvs_source_view]
        )
        out: list[dict[str, str]] = []
        for lib in libs:
            for cell in cells:
                for layout in layouts:
                    for src in sources:
                        out.append(
                            {
                                "task_id": f"{lib}__{cell}__{layout}__{src}",
                                "library": lib,
                                "cell": cell,
                                "lvs_layout_view": layout,
                                "lvs_source_view": src,
                            }
                        )
        return out

    def _on_preview_toggled(self, combo: dict[str, str], checked: bool) -> None:
        def mutate(spec: dict[str, Any]) -> None:
            excludes = list(spec.get("exclude") or [])
            if checked:
                excludes = [
                    e for e in excludes if not _selector_matches(e, combo)
                ]
            else:
                if not any(_selector_matches(e, combo) for e in excludes):
                    excludes.append(_minimal_selector(spec, combo))
            if excludes:
                spec["exclude"] = excludes
            else:
                spec.pop("exclude", None)

        self._mutate_current_spec(mutate)

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


# ---- module helpers --------------------------------------------------


def _summarize_spec(index: int, spec: dict[str, Any]) -> str:
    def _count(v: Any) -> int:
        if isinstance(v, list):
            return len(v)
        return 1 if v else 0

    libs = _count(spec.get("library"))
    cells = _count(spec.get("cell"))
    layouts = _count(spec.get("lvs_layout_view"))
    return (
        f"#{index}  "
        f"{_axis_first(spec.get('library'))} × "
        f"{_axis_first(spec.get('cell'))}  "
        f"({libs}×{cells}×{layouts})"
    )


def _axis_first(v: Any) -> str:
    if isinstance(v, list):
        if not v:
            return "(empty)"
        return f"[{v[0]}{',…' if len(v) > 1 else ''}]"
    return str(v) if v else "(empty)"


def _fmt_num(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _parse_num(text: str) -> float | None:
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _enabled_to_index(v: Any) -> int:
    if v is None:
        return 0
    if v is True:
        return 1
    return 2


def _jivaro_summary_for(spec: TaskSpec, cell: str) -> str:
    ov = spec.jivaro_overrides.get(cell)
    if ov is None or ov.enabled is None:
        return str(spec.jivaro.enabled)
    return f"{ov.enabled}*"


def _selector_matches(selector: Any, combo: dict[str, str]) -> bool:
    if isinstance(selector, ExcludeMatch):
        items = {
            "library": selector.library,
            "cell": selector.cell,
            "lvs_layout_view": selector.lvs_layout_view,
            "lvs_source_view": selector.lvs_source_view,
        }
    elif isinstance(selector, dict):
        items = selector
    else:
        return False
    for key, value in items.items():
        if value is None:
            continue
        if combo.get(key) != value:
            return False
    return True


def _minimal_selector(spec: dict[str, Any], combo: dict[str, str]) -> dict[str, str]:
    """Pick the smallest selector that uniquely picks this combo.

    For axes where the spec has a single value, omit the key (it is
    implied). For multi-valued axes, pin the value.
    """
    selector: dict[str, str] = {}
    for field in _AXIS_FIELDS:
        v = spec.get(field)
        if isinstance(v, list) and len(v) > 1:
            selector[field] = combo[field]
    if not selector:
        # Everything was single-valued — pin cell as a fallback so the
        # selector is not empty (ExcludeMatch forbids empty).
        selector["cell"] = combo["cell"]
    return selector


def _mono_font() -> QFont:
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f
