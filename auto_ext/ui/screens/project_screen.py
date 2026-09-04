"""The Project screen: the config directory, as one editable object.

Until this screen existed, ``WorkspaceConfig`` and ``PdkProfile`` were
*wholly* unreachable from the GUI. Every path the flow resolves, every corner
a recipe can name, every deck the LVS stage is pointed at, lived in two YAML
files a user had to open in an editor -- while the Setup drawer, the one place
that told them something was wrong with those files, could only say what to
type somewhere else. See ``docs/refactor/PROJECTS_AND_SETUP.md``.

Why this is a screen and not more of the drawer
-----------------------------------------------
The drawer answers one question -- *can I run right now, and if not, what
exactly do I type?* -- in 520px, with one row per **check**. The two objects
here have 44 leaf fields between them, and a field is not a check: several
checks can hang off one field, and most fields have no check at all. Growing
the drawer to hold them would cost it the property that makes it useful.

So the drawer keeps its job and gains a way out: a fix hint that names a YAML
field emits :attr:`~auto_ext.ui.screens.setup_drawer.SetupDrawer.
edit_field_requested`, and the host brings this screen up scrolled to that
field.

Ownership, exactly as the Recipes screen does it
------------------------------------------------
The screen holds a **working copy** (``model_copy(deep=True)``) and never the
caller's object; it reads and writes no files. Editing stages into the working
copy and announces dirtiness; ``save_requested`` hands both working copies out
and the host persists them through
:meth:`~auto_ext.ui.config_controller.ConfigController.save`, which is the same
path the Recipes screen uses. One dirty-state machine, one save path: the
Recipes screen's "Save staged into a queue and wrote nothing" was one of the
eight defects the first office session found, and two mechanisms would be two
chances to reproduce it.

Where the form comes from
-------------------------
:mod:`auto_ext.ui.project_fields`, which is deliberately Qt-free so the
reachability audit can read it without a display. There is no field list in
this module and there must not be one -- that is the same rule the Recipes
screen follows against the catalog, for the same reason.

Assumptions
-----------
* **A keystroke is an edit; focus-out is only when a mistake is reported.**
  Every control announces itself as it is typed into, because everything
  downstream of an edit -- the staged document, the star in the title,
  ``File -> Save`` writing the new value instead of the old one, the window
  asking before it closes -- hangs off that announcement, and neither the
  Save shortcut nor the title-bar X moves focus. This is the same rule the
  Recipes screen follows, and for the same reason it had to learn it.
* **A validation failure is shown at the control, not at save time.**
  ``set_path`` assigns through the attribute and the models use
  ``validate_assignment=True``, so a bad pattern key or an out-of-range
  ``keep_runs`` raises while the user is still looking at what they typed. The
  control keeps the rejected text (retyping it from scratch is worse than
  seeing it marked) and the working copy keeps the old value.
* **``profile_ids`` and the known-project list are supplied by the host**, not
  scanned here: the screen does no I/O, the set of profiles is a property of
  the config directory the host loaded, and the project list lives in
  ``QSettings``, which :mod:`auto_ext.ui.app` owns.
* Groups render in the order ``project_fields`` declares; a group that gains a
  field later renders at the end rather than not at all.
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.model.pdk import CornerSpec, LvsDeckVariant, PdkProfile
from auto_ext.model.workspace import WorkspaceConfig
from auto_ext.ui import theme
from auto_ext.ui.project_fields import (
    PROFILE_FIELDS,
    PROFILE_GROUP_ORDER,
    WORKSPACE_FIELDS,
    WORKSPACE_GROUP_ORDER,
    FieldKind,
    ProjectField,
    get_path,
    set_path,
)

__all__ = [
    "LABEL_COLUMN_WIDTH",
    "OBJ_FIELD_ERROR",
    "OBJ_GROUP_HEADER",
    "ProjectScreen",
    "UNSET_TEXT",
    "field_editors",
]

#: Width of the label column, so every control on the screen lines up.
LABEL_COLUMN_WIDTH = 168

OBJ_GROUP_HEADER = "projectGroupHeader"
OBJ_FIELD_ERROR = "projectFieldError"

#: What a nullable CHOICE shows for "no answer". Not an empty entry: an empty
#: combo row is indistinguishable from a rendering bug, and the whole point of
#: the field is that unset MEANS something.
UNSET_TEXT = "(unset)"

#: The submodel each TABLE field's rows are made of, by field path. A table
#: builds rows itself when the user adds one, so it has to know the type;
#: keeping the map here rather than on ProjectField keeps that module Qt-free
#: AND model-free.
_TABLE_ROW_TYPES: dict[str, type] = {
    "corners": CornerSpec,
    "lvs_decks.variants": LvsDeckVariant,
}

#: Required columns per row type, used to decide whether a half-filled new row
#: can be committed to the working copy yet.
_TABLE_REQUIRED: dict[str, tuple[str, ...]] = {
    "corners": ("name", "technology_corner"),
    "lvs_decks.variants": ("name", "rules_suffix"),
}


def field_editors() -> tuple[ProjectField, ...]:
    """Every field this screen puts a control in front of, in render order."""

    return tuple(WORKSPACE_FIELDS) + tuple(PROFILE_FIELDS)


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


class _MultiLineEdit(QPlainTextEdit):
    """A plain-text box that also reports focus-out, like ``QLineEdit`` does.

    Focus-out is the moment a *mistake* is worth reporting: half a typed
    ``name = value`` line is not an error message under the control, it is a
    user who has not finished the line. It is emphatically **not** the moment
    an edit becomes real -- see :meth:`FieldRow._on_typed`.
    """

    editingFinished = pyqtSignal()

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        self.editingFinished.emit()


class FieldRow(QWidget):
    """Label, control, help line and an error line that is usually hidden."""

    #: The user changed this field. Carries ``(path, value)``; the value is
    #: already in the control's own type, not text.
    changed = pyqtSignal(str, object)
    #: A TABLE row was taken out. Carries the row's first column, so the
    #: screen can say what went rather than only that something did.
    removed = pyqtSignal(str)

    def __init__(self, spec: ProjectField, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._loading = False
        self._error_text = ""
        self._control: QWidget

        root = QVBoxLayout(self)
        root.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS
        )
        root.setSpacing(theme.SPACE_XXS)

        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.SPACE_MD)

        label = QLabel(spec.label, self)
        label.setFixedWidth(LABEL_COLUMN_WIDTH)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        label.setWordWrap(True)
        line.addWidget(label)

        self._control = self._build_control()
        line.addWidget(self._control, 1)
        root.addLayout(line)

        help_row = QHBoxLayout()
        help_row.setContentsMargins(0, 0, 0, 0)
        help_row.setSpacing(theme.SPACE_MD)
        spacer = QWidget(self)
        spacer.setFixedWidth(LABEL_COLUMN_WIDTH)
        help_row.addWidget(spacer)

        help_text = spec.help
        if spec.unset_means:
            help_text = f"{help_text} Unset: {spec.unset_means}."
        self._help = QLabel(help_text, self)
        self._help.setWordWrap(True)
        self._help.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        help_row.addWidget(self._help, 1)
        root.addLayout(help_row)

        self._error = QLabel("", self)
        self._error.setObjectName(OBJ_FIELD_ERROR)
        self._error.setWordWrap(True)
        self._error.setStyleSheet(
            f"color: {theme.STATUS_FAILED}; font-size: {theme.FONT_SIZE_META}px; "
            f"padding-left: {LABEL_COLUMN_WIDTH + theme.SPACE_MD}px;"
        )
        self._error.hide()
        root.addWidget(self._error)

    # ---- control construction ------------------------------------------

    def _build_control(self) -> QWidget:
        kind = self.spec.kind
        if kind is FieldKind.INT:
            box = QSpinBox(self)
            box.setRange(0, 100000)
            box.valueChanged.connect(self._on_edited)
            return box
        if kind is FieldKind.CHOICE:
            combo = QComboBox(self)
            combo.currentIndexChanged.connect(self._on_edited)
            return combo
        if kind in (FieldKind.LIST, FieldKind.MAPPING):
            edit = _MultiLineEdit(self)
            edit.setMinimumHeight(theme.ROW_HEIGHT * 3)
            edit.setPlaceholderText(
                self.spec.placeholder
                or (
                    f"{self.spec.key_label} = {self.spec.value_label}, one per line"
                    if kind is FieldKind.MAPPING
                    else "one per line"
                )
            )
            edit.setStyleSheet(f"font-family: {theme.FONT_MONO};")
            edit.textChanged.connect(self._on_typed)
            edit.editingFinished.connect(self._on_edited)
            return edit
        if kind is FieldKind.TABLE:
            table = _TableControl(self.spec, self)
            table.changed.connect(self._on_edited)
            table.removed.connect(self.removed)
            return table
        edit = QLineEdit(self)
        edit.setPlaceholderText(self.spec.placeholder)
        # ``textEdited``, not ``textChanged``: it fires for typing and pasting
        # and stays silent for ``setText``, which is how the host loads a
        # value in. See :meth:`_on_typed` for why a keystroke has to announce
        # itself at all.
        edit.textEdited.connect(self._on_edited)
        edit.editingFinished.connect(self._on_edited)
        return edit

    # ---- value -----------------------------------------------------------

    def control(self) -> QWidget:
        return self._control

    def set_choices(self, choices: list[str]) -> None:
        """Replace a CHOICE row's options, keeping the current answer if it survives."""

        if not isinstance(self._control, QComboBox):
            return
        current = self.value()
        self._loading = True
        self._control.clear()
        if self.spec.unset_means:
            self._control.addItem(UNSET_TEXT, None)
        for choice in choices:
            self._control.addItem(choice, choice)
        self._loading = False
        self.set_value(current if current in choices else None)

    def set_value(self, value: Any) -> None:
        """Show ``value`` without emitting :attr:`changed`."""

        self._loading = True
        try:
            control = self._control
            if isinstance(control, QSpinBox):
                control.setValue(int(value or 0))
            elif isinstance(control, QComboBox):
                index = control.findData(value)
                control.setCurrentIndex(index if index >= 0 else 0)
            elif isinstance(control, _TableControl):
                control.set_rows(list(value or []))
            elif isinstance(control, QPlainTextEdit):
                control.setPlainText(self._format_multiline(value))
            elif isinstance(control, QLineEdit):
                control.setText("" if value is None else str(value))
        finally:
            self._loading = False

    def value(self) -> Any:
        """The control's current answer, in the field's own type."""

        control = self._control
        if isinstance(control, QSpinBox):
            return control.value()
        if isinstance(control, QComboBox):
            return control.currentData()
        if isinstance(control, _TableControl):
            return control.rows()
        if isinstance(control, QPlainTextEdit):
            return self._parse_multiline(control.toPlainText())
        if isinstance(control, QLineEdit):
            text = control.text().strip()
            return text or None
        return None

    def _format_multiline(self, value: Any) -> str:
        if self.spec.kind is FieldKind.MAPPING:
            return "\n".join(f"{k} = {v}" for k, v in (value or {}).items())
        return "\n".join(str(item) for item in (value or []))

    def _parse_multiline(self, text: str) -> Any:
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        if self.spec.kind is not FieldKind.MAPPING:
            return lines
        pairs: dict[str, str] = {}
        for line in lines:
            # `=` first because a value is far more likely to contain a colon
            # (a path expression, a library/cell/view) than a name is.
            separator = "=" if "=" in line else (":" if ":" in line else None)
            if separator is None:
                raise ValueError(
                    f"{line!r} is not a {self.spec.key_label}/{self.spec.value_label} "
                    f"pair: write `{self.spec.key_label} = {self.spec.value_label}`"
                )
            name, _, raw = line.partition(separator)
            name = name.strip()
            if not name:
                raise ValueError(f"{line!r} has no {self.spec.key_label}")
            pairs[name] = raw.strip()
        return pairs

    # ---- error -----------------------------------------------------------

    def set_error(self, message: str) -> None:
        """Show ``message`` under the control, or clear it when empty."""

        self._error_text = message
        self._error.setText(message)
        self._error.setVisible(bool(message))

    def error(self) -> str:
        """The message currently under the control, or ``""``.

        Read off a field rather than off ``QLabel.isVisible``: a widget whose
        window has not been shown reports itself invisible whatever its own
        flag says, so the visibility answer would be "no error" for every row
        of a screen that is merely on a background page.
        """

        return self._error_text

    def commit(self) -> None:
        """Announce the control's current answer as an edit.

        The public form of what focus-out and ``editingFinished`` trigger. A
        caller that set text programmatically -- a test, or the derive-from-a-
        file flow -- has no focus change to rely on.
        """

        self._on_edited()

    def _on_typed(self, *_args: Any) -> None:
        """Announce what is in the control now, saying nothing about a bad line.

        The quiet half of :meth:`_on_edited`, for the signals that fire on
        every keystroke. A line the parser cannot read yet is skipped rather
        than reported: ``SETUP_ROOT`` is the first eleven characters of
        ``SETUP_ROOT = /pdk/setup``, not a mistake. Focus-out still runs the
        loud version, so an unfinished line is reported the moment the user
        looks away from it.
        """

        if self._loading:
            return
        try:
            value = self.value()
        except ValueError:
            return
        self.changed.emit(self.spec.path, value)

    def _on_edited(self, *_args: Any) -> None:
        if self._loading:
            return
        try:
            value = self.value()
        except ValueError as exc:
            self.set_error(str(exc))
            return
        self.changed.emit(self.spec.path, value)


# ---------------------------------------------------------------------------
# Table control
# ---------------------------------------------------------------------------


class _TableControl(QWidget):
    """A list of submodels as a table, with Add / Remove.

    A half-filled new row is *not* an error and is not pushed into the working
    copy: a corner needs both a handle and a literal, and a user types them one
    at a time. The row is kept on screen and the table simply reports the rows
    that are complete, so typing the second column completes it rather than
    having to re-enter the first.

    Remove is enable-managed against the selection. It used to be lit at all
    times and to ``return`` on ``currentRow() == -1``, so the first press --
    the one a user makes before selecting anything -- did nothing and said
    nothing, which is indistinguishable from a broken button.
    """

    changed = pyqtSignal()
    #: A row was taken out; carries its first column, or ``""`` when blank.
    removed = pyqtSignal(str)

    def __init__(self, spec: ProjectField, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        self._row_type = _TABLE_ROW_TYPES[spec.path]
        self._required = _TABLE_REQUIRED[spec.path]
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(theme.SPACE_XS)

        self._table = QTableWidget(0, len(spec.columns), self)
        self._table.setHorizontalHeaderLabels(
            [column.replace("_", " ") for column in spec.columns]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        header = self._table.horizontalHeader()
        for index in range(len(spec.columns)):
            header.setSectionResizeMode(index, QHeaderView.Stretch)
        self._table.setMinimumHeight(theme.ROW_HEIGHT * 4)
        self._table.itemChanged.connect(self._on_item_changed)
        # Both, because the two move independently: clicking a cell changes
        # the current cell and the selection, while a programmatic
        # ``setCurrentCell(-1, -1)`` changes only the first.
        self._table.itemSelectionChanged.connect(self._refresh_buttons)
        self._table.currentCellChanged.connect(self._refresh_buttons)
        root.addWidget(self._table)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(theme.SPACE_XS)
        self._add = QPushButton("Add", self)
        self._add.clicked.connect(self._on_add)
        self._remove = QPushButton("Remove", self)
        self._remove.clicked.connect(self._on_remove)
        buttons.addWidget(self._add)
        buttons.addWidget(self._remove)
        buttons.addStretch(1)
        root.addLayout(buttons)

        self._refresh_buttons()

    def set_rows(self, rows: list[Any]) -> None:
        self._loading = True
        try:
            self._table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, column in enumerate(self.spec.columns):
                    value = getattr(row, column, None)
                    item = QTableWidgetItem("" if value is None else str(value))
                    self._table.setItem(r, c, item)
        finally:
            self._loading = False

    def rows(self) -> list[Any]:
        """Every COMPLETE row, as submodel instances.

        Incomplete rows are skipped rather than raising: they are a user
        mid-typing, not a mistake.
        """

        built: list[Any] = []
        for r in range(self._table.rowCount()):
            values: dict[str, Any] = {}
            for c, column in enumerate(self.spec.columns):
                item = self._table.item(r, c)
                text = item.text().strip() if item is not None else ""
                if text:
                    values[column] = text
            if any(not values.get(name) for name in self._required):
                continue
            built.append(self._row_type(**values))
        return built

    def row_count(self) -> int:
        """Rows on screen, complete or not."""

        return self._table.rowCount()

    def table(self) -> QTableWidget:
        return self._table

    def remove_button(self) -> QPushButton:
        """The Remove control, so a caller can ask whether it is live."""

        return self._remove

    def _refresh_buttons(self, *_args: Any) -> None:
        self._remove.setEnabled(self._table.currentRow() >= 0)
        self._remove.setToolTip(
            ""
            if self._remove.isEnabled()
            else "Select a row first; Remove acts on the selected one."
        )

    def _on_add(self) -> None:
        row = self._table.rowCount()
        self._loading = True
        try:
            self._table.insertRow(row)
            for c in range(len(self.spec.columns)):
                self._table.setItem(row, c, QTableWidgetItem(""))
        finally:
            self._loading = False
        self._table.setCurrentCell(row, 0)
        self._table.editItem(self._table.item(row, 0))

    def _on_remove(self) -> None:
        row = self._table.currentRow()
        if row < 0:  # pragma: no cover - the button is disabled in this state
            return
        item = self._table.item(row, 0)
        label = item.text().strip() if item is not None else ""
        self._table.removeRow(row)
        self._refresh_buttons()
        # ``changed`` first: it is what puts the new row list in the working
        # copy, and ``removed`` is the sentence about what that did.
        self.changed.emit()
        self.removed.emit(label)

    def _on_item_changed(self, _item: QTableWidgetItem) -> None:
        if self._loading:
            return
        self.changed.emit()


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class ProjectScreen(QWidget):
    """One project's ``workspace.yaml`` and ``profiles/<id>.yaml``, editable."""

    #: The working copies started or stopped differing from what was loaded.
    dirty_changed = pyqtSignal(bool)
    #: One sentence for the shell's status bar.
    status_changed = pyqtSignal(str)
    #: One accepted edit. Carries ``(WorkspaceConfig | None, PdkProfile | None)``
    #: -- only the objects that differ from what was loaded.
    #:
    #: Every edit is announced, not only the ones Save is pressed on. The
    #: Recipes screen learned this the hard way: while the screen was the only
    #: thing that knew about an edit, the window title had no star, File ->
    #: Save was greyed out, and closing threw the edit away with nothing to
    #: warn about. It is also what lets the Setup drawer's pin and this screen
    #: edit one profile instead of two.
    edited = pyqtSignal(object, object)
    #: Persist what is staged. Carries the same pair as :attr:`edited`.
    save_requested = pyqtSignal(object, object)
    #: Throw the working copies away and reload from the host.
    revert_requested = pyqtSignal()
    #: The user wants to open a project this screen has not been told about.
    open_project_requested = pyqtSignal()
    #: The user picked a known project. Carries its config directory as a str.
    project_chosen = pyqtSignal(str)
    #: The user wants this project's environment read out of files it produced.
    #: The host owns the dialog: it needs a file chooser and the profile, and
    #: this screen does no I/O.
    env_import_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workspace: WorkspaceConfig | None = None
        self._profile: PdkProfile | None = None
        self._workspace_original: WorkspaceConfig | None = None
        self._profile_original: PdkProfile | None = None
        self._rows: dict[str, FieldRow] = {}
        self._profile_ids: list[str] = []
        self._known: list[tuple[str, str]] = []
        self._config_dir: str = ""
        self._dirty = False
        self._loading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_body(), 1)

        self._refresh_enabled()
        self._refresh_status()

    # ---- construction ----------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QFrame(self)
        header.setFrameShape(QFrame.NoFrame)
        header.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_TOOLBAR}; "
            f"border-bottom: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        layout = QHBoxLayout(header)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        layout.setSpacing(theme.SPACE_SM)

        self._title = QLabel("No project loaded", header)
        self._title.setStyleSheet(
            f"font-size: {theme.FONT_SIZE_TITLE}px; "
            f"font-weight: {theme.FONT_WEIGHT_BOLD};"
        )
        layout.addWidget(self._title)

        # Switching a project is picking one, not navigating to it again.
        # Hidden until there is more than the current project to pick from: a
        # one-item picker is a control that cannot do anything.
        self._project_picker = QComboBox(header)
        self._project_picker.setToolTip("Switch to another project you have opened.")
        self._project_picker.activated.connect(self._on_project_picked)
        self._project_picker.hide()
        layout.addWidget(self._project_picker)
        layout.addStretch(1)

        self._open_btn = QPushButton("Open project...", header)
        self._open_btn.setToolTip("Open a config directory that is not in the list.")
        self._open_btn.clicked.connect(self.open_project_requested.emit)
        layout.addWidget(self._open_btn)

        self._read_env_btn = QPushButton("Read environment from a file...", header)
        self._read_env_btn.setToolTip(
            "Recover this project's environment values from a runset, .cmd or "
            "si.env it generated -- including the ones the shell cannot supply."
        )
        self._read_env_btn.clicked.connect(self.env_import_requested.emit)
        layout.addWidget(self._read_env_btn)

        self._revert_btn = QPushButton("Revert", header)
        self._revert_btn.clicked.connect(self.revert_requested.emit)
        layout.addWidget(self._revert_btn)

        self._save_btn = QPushButton("Save", header)
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._on_save)
        layout.addWidget(self._save_btn)
        return header

    def _build_body(self) -> QWidget:
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget(self._scroll)
        body.setStyleSheet(f"background: {theme.SURFACE_PAGE};")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, theme.SPACE_XL)
        layout.setSpacing(0)

        layout.addWidget(self._section("Project", WORKSPACE_GROUP_ORDER, WORKSPACE_FIELDS))
        layout.addWidget(self._section("PDK profile", PROFILE_GROUP_ORDER, PROFILE_FIELDS))
        layout.addStretch(1)
        self._scroll.setWidget(body)
        return self._scroll

    def _section(
        self, title: str, group_order: tuple[str, ...], fields: tuple[ProjectField, ...]
    ) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._banner(title))

        ordered = list(group_order)
        for spec in fields:
            if spec.group not in ordered:
                ordered.append(spec.group)
        for group in ordered:
            in_group = [spec for spec in fields if spec.group == group]
            if not in_group:
                continue
            layout.addWidget(self._group_header(group))
            for spec in in_group:
                row = FieldRow(spec, panel)
                row.changed.connect(self._on_field_changed)
                row.removed.connect(
                    lambda label, path=spec.path: self._on_row_removed(path, label)
                )
                self._rows[spec.path] = row
                layout.addWidget(row)
        return panel

    def _banner(self, title: str) -> QWidget:
        label = QLabel(title, self)
        label.setStyleSheet(
            f"background: {theme.SURFACE_TABLE_HEADER}; color: {theme.TEXT_PRIMARY}; "
            f"font-size: {theme.FONT_SIZE_SECTION}px; "
            f"font-weight: {theme.FONT_WEIGHT_BOLD}; "
            f"border-top: 1px solid {theme.LINE_STRUCTURAL}; "
            f"border-bottom: 1px solid {theme.LINE_STRUCTURAL}; "
            f"padding: {theme.SPACE_SM}px {theme.SPACE_MD}px;"
        )
        return label

    def _group_header(self, title: str) -> QWidget:
        label = QLabel(title, self)
        label.setObjectName(OBJ_GROUP_HEADER)
        label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px; "
            f"font-weight: {theme.FONT_WEIGHT_SEMIBOLD}; "
            f"border-bottom: 1px solid {theme.LINE_ROW}; "
            f"padding: {theme.SPACE_SM}px {theme.SPACE_MD}px {theme.SPACE_XS}px;"
        )
        return label

    # ---- public API ------------------------------------------------------

    def set_project(
        self,
        *,
        workspace: WorkspaceConfig | None,
        profile: PdkProfile | None,
        config_dir: str = "",
        profile_ids: list[str] | None = None,
    ) -> None:
        """Load a project. Replaces the working copies and clears dirtiness."""

        self._config_dir = config_dir
        self._profile_ids = list(profile_ids or [])
        self._workspace_original = workspace
        self._profile_original = profile
        self._workspace = workspace.model_copy(deep=True) if workspace else None
        self._profile = profile.model_copy(deep=True) if profile else None
        self._set_dirty(False)
        self._reload_rows()
        self._refresh_header()
        # Re-sync the switcher's selection: the list is unchanged but which
        # entry is the current one is not.
        self.set_known_projects(self._known)
        self._refresh_enabled()
        self._refresh_status()

    def set_known_projects(self, projects: list[tuple[str, str]]) -> None:
        """Fill the switcher. ``projects`` is ``(display name, config dir)``.

        The host supplies the list for the same reason it supplies
        ``profile_ids``: the screen does no I/O and knows nothing outside the
        one project it was handed.
        """

        self._known = list(projects)
        picker = self._project_picker
        picker.blockSignals(True)
        try:
            picker.clear()
            for name, path in self._known:
                picker.addItem(name, path)
                picker.setItemData(picker.count() - 1, path, Qt.ToolTipRole)
            index = picker.findData(self._config_dir)
            picker.setCurrentIndex(index if index >= 0 else -1)
        finally:
            picker.blockSignals(False)
        # One entry is the project already open: nothing to switch to.
        picker.setVisible(len(self._known) > 1)

    def known_projects(self) -> list[tuple[str, str]]:
        """What the switcher currently offers."""

        return list(self._known)

    def workspace(self) -> WorkspaceConfig | None:
        """The working copy, or None when no project is loaded."""

        return self._workspace

    def profile(self) -> PdkProfile | None:
        return self._profile

    def is_dirty(self) -> bool:
        return self._dirty

    def row(self, path: str) -> FieldRow | None:
        """The row rendering ``path``, for tests and for :meth:`scroll_to`."""

        return self._rows.get(path)

    def field_paths(self) -> list[str]:
        """Every field path this screen renders, in display order."""

        return [spec.path for spec in field_editors()]

    def scroll_to(self, path: str) -> bool:
        """Bring ``path``'s row into view. False when there is no such row."""

        row = self._rows.get(path)
        if row is None:
            return False
        self._scroll.ensureWidgetVisible(row)
        row.setFocus()
        return True

    def errors(self) -> dict[str, str]:
        """Field path -> the validation message currently shown under it."""

        return {path: row.error() for path, row in self._rows.items() if row.error()}

    def apply_edit(self, path: str, value: Any) -> bool:
        """Set one field as if the user had typed it. False when there is no row.

        The seam for anything that edits the project from outside this screen:
        the Setup drawer's "pin this value", and the import that reads a
        project's own environment back out of a file it produced. Routing
        those through here rather than staging a separate copy on the host is
        what keeps ONE working copy of the profile -- two would disagree the
        moment either was reverted.
        """

        row = self._rows.get(path)
        if row is None:
            return False
        row.set_value(value)
        row.commit()
        return True

    # ---- editing ---------------------------------------------------------

    def _reload_rows(self) -> None:
        self._loading = True
        try:
            for row in self._rows.values():
                row.set_error("")
            self._refresh_choices()
            for spec in WORKSPACE_FIELDS:
                row = self._rows[spec.path]
                if self._workspace is None:
                    row.set_value(None)
                    row.setEnabled(False)
                    continue
                row.setEnabled(True)
                row.set_value(get_path(self._workspace, spec.path))
            for spec in PROFILE_FIELDS:
                row = self._rows[spec.path]
                if self._profile is None:
                    row.set_value(None)
                    row.setEnabled(False)
                    continue
                row.setEnabled(True)
                row.set_value(get_path(self._profile, spec.path))
        finally:
            self._loading = False

    def _refresh_choices(self) -> None:
        """Push the computed option sets into the CHOICE rows.

        Every one of them depends on the object being edited (or on the config
        directory around it), which is why ``ProjectField`` names a source
        instead of carrying a literal list.
        """

        sources: dict[str, list[str]] = {
            "profile_ids": list(self._profile_ids),
            "corners": [c.name for c in self._profile.corners] if self._profile else [],
            "deck_variants": (
                [v.name for v in self._profile.lvs_decks.variants] if self._profile else []
            ),
        }
        for spec in field_editors():
            if spec.choices_from is None:
                continue
            self._rows[spec.path].set_choices(sources.get(spec.choices_from, []))

    def _on_field_changed(self, path: str, value: Any) -> None:
        if self._loading:
            return
        is_workspace = self._owns(WORKSPACE_FIELDS, path)
        target = self._workspace if is_workspace else self._profile
        if target is None:
            return
        row = self._rows[path]

        # Try it on a copy, then adopt the copy. A *field* validator rejects
        # before assigning, but a MODEL validator -- which is what refuses to
        # drop the corner `default_corner` points at -- runs after, and
        # pydantic does not roll the assignment back when it raises. Editing
        # the live working copy would therefore leave it holding exactly the
        # state the model just declared invalid, while the row said so.
        trial = target.model_copy(deep=True)
        try:
            set_path(trial, path, value)
        except Exception as exc:  # pydantic ValidationError and anything it wraps
            row.set_error(_first_message(exc))
            self._refresh_enabled()
            self._refresh_status()
            return
        if is_workspace:
            self._workspace = trial
        else:
            self._profile = trial
        row.set_error("")
        # A table edit can change what a CHOICE row may answer -- adding a
        # corner is exactly how a user makes it selectable as the default.
        if row.spec.kind is FieldKind.TABLE:
            self._loading = True
            try:
                self._refresh_choices()
            finally:
                self._loading = False
        self._set_dirty(self._compute_dirty())
        # Not only through _set_dirty: an invalid field has to take Save out
        # of service even when dirtiness itself did not move, which is the
        # usual case -- something else was already edited.
        self._refresh_enabled()
        self._refresh_status()
        self.edited.emit(self._changed_workspace(), self._changed_profile())

    def _on_row_removed(self, path: str, label: str) -> None:
        """Say what a table Remove took out, and that it is not gone for good.

        The row leaves the screen the instant the button is pressed, and the
        only other thing that changes is a status line reading "unsaved
        changes" -- which is what every other edit says too. Nothing has
        reached the disk, so the honest sentence names both halves.
        """

        row = self._rows.get(path)
        if row is None or row.error():
            # The model refused the shorter list (removing the corner
            # ``default_corner`` points at does exactly that). The error
            # under the control is the message that matters; do not paint
            # over it with a report of a removal that did not take.
            return
        what = label or "the selected row"
        self.status_changed.emit(
            f"removed {what} from {row.spec.label} - not written yet, "
            f"Revert puts it back"
        )

    @staticmethod
    def _owns(fields: tuple[ProjectField, ...], path: str) -> bool:
        return any(spec.path == path for spec in fields)

    def _compute_dirty(self) -> bool:
        return bool(self._changed_workspace() or self._changed_profile())

    def _changed_workspace(self) -> WorkspaceConfig | None:
        if self._workspace is None or self._workspace_original is None:
            return None
        return self._workspace if self._workspace != self._workspace_original else None

    def _changed_profile(self) -> PdkProfile | None:
        if self._profile is None or self._profile_original is None:
            return None
        return self._profile if self._profile != self._profile_original else None

    def _set_dirty(self, dirty: bool) -> None:
        if dirty == self._dirty:
            return
        self._dirty = dirty
        self._refresh_enabled()
        self.dirty_changed.emit(dirty)

    def _on_save(self) -> None:
        # Both guards are states :meth:`_refresh_enabled` has already taken
        # the button out of service for; they stay as the belt to that
        # braces, not as the place the refusal is communicated.
        if not self._dirty or self.errors():
            return
        self.save_requested.emit(self._changed_workspace(), self._changed_profile())

    # ---- chrome ----------------------------------------------------------

    def _on_project_picked(self, index: int) -> None:
        """A pick that is not a change is not a switch."""

        chosen = self._project_picker.itemData(index)
        if not chosen or chosen == self._config_dir:
            return
        self.project_chosen.emit(str(chosen))

    def _refresh_header(self) -> None:
        if self._workspace is None and self._profile is None:
            self._title.setText("No project loaded")
            self._title.setToolTip("")
            return
        name = self._config_dir or "project"
        self._title.setText(name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or name)
        self._title.setToolTip(self._config_dir)

    def _refresh_enabled(self) -> None:
        loaded = self._workspace is not None or self._profile is not None
        # A field the model rejected is a state, not an event: Save cannot
        # write while one is on screen, so it says so by going out of service
        # and naming the field. It used to stay lit and refuse inside the
        # click handler, re-emitting the status line the edit had already
        # emitted -- a press that changed nothing a user could see.
        errors = self.errors()
        self._save_btn.setEnabled(loaded and self._dirty and not errors)
        if errors:
            path, message = next(iter(errors.items()))
            self._save_btn.setToolTip(f"Fix {path} first: {message}")
        else:
            self._save_btn.setToolTip("")
        self._revert_btn.setEnabled(loaded and self._dirty)
        # Reading the environment back needs a profile to invert expressions
        # against; without one there is nothing to match the file's paths to.
        self._read_env_btn.setEnabled(self._profile is not None)

    def _refresh_status(self) -> None:
        if self._workspace is None and self._profile is None:
            self.status_changed.emit("no project loaded")
            return
        errors = self.errors()
        if errors:
            first = next(iter(errors.items()))
            self.status_changed.emit(f"{first[0]}: {first[1]}")
            return
        self.status_changed.emit(
            "unsaved changes" if self._dirty else "project matches what is on disk"
        )


def _first_message(exc: Exception) -> str:
    """The one sentence of a pydantic error worth showing next to a control.

    A ValidationError stringifies to a multi-line report with the model name,
    the field, a URL and the input value. Under a text box that is noise; the
    message alone is the part the user can act on.
    """

    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            first = errors()[0]
        except (IndexError, TypeError):
            return str(exc)
        message = str(first.get("msg", "")).strip()
        # pydantic prefixes custom ValueErrors with "Value error, ".
        prefix = "Value error, "
        if message.startswith(prefix):
            message = message[len(prefix) :]
        return message or str(exc)
    return str(exc)
