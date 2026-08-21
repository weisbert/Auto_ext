"""The Cells screen: one row per DUT, select some, run them.

This is where the user spends most of the session, so it is one table and
nothing else. Artboards ``1a`` (populated), ``1b`` (a run in flight),
``1i`` (empty) and ``1j`` (1366x768) are the spec.

What the table is *not*
-----------------------
There is no Cartesian preview pane. Expansion happens once, when rows are
added (:func:`auto_ext.model.cells.expand_cells`), and what is stored is
the expanded rows -- so every line on screen is a literal thing that will
run, not a template whose output moves when a list elsewhere grows an
element. The old Tasks tab's generator-plus-preview pair is exactly what
:mod:`auto_ext.model.cells` was written to delete.

Column modes
------------
The same ten physical columns are shown in three arrangements:

``wide`` (``1a``)
    check, library, cell, layout, source, ground, recipe, last run, status
``compact`` (``1j``, concession 2, below :data:`TABLE_COMPACT_BELOW`)
    check, library, cell, views, recipe, last run, status -- ``layout`` and
    ``source`` merge into ``views`` and ``ground`` moves to the row tooltip.
    The cell name is the one elastic column and never truncates first.
``running`` (``1b``)
    check (as a status glyph), library, cell, recipe, stages -- the stage
    chips need the width, and while a run is in flight nothing else on the
    row can change anyway.

Running a batch
---------------
The screen does not own a thread. It builds the same
:class:`~auto_ext.ui.worker.RunWorker` +
:class:`~auto_ext.ui.qt_reporter.QtProgressReporter` +
:class:`~auto_ext.core.progress.CancelToken` trio the Run tab has always
used, one run at a time, and turns the reporter's signals into row state.
Log paths come from the reporter's ``run_dir_ready`` event rather than
being recomputed from a task id: since S1 the logs live under
``runs/<run_id>/logs/`` and the run id is not derivable from the row.

Assumptions
-----------
* **The per-row recipe binding has no home in the model yet.**
  :class:`~auto_ext.model.cells.CellEntry` carries identity and per-DUT
  settings only, and the schema deliberately keeps "which recipe" out of
  it. Artboard ``1a`` nevertheless shows a ``recipe`` column, so the screen
  holds the binding as screen state
  (:meth:`CellsScreen.recipe_bindings`) and hands it out whole. When a
  persistent home appears, that accessor pair is the seam to move.
* :data:`TABLE_COMPACT_BELOW` and
  :data:`~auto_ext.ui.widgets.run_bar.RUN_BAR_COMPACT_BELOW` are measured
  on the widget's own width. Artboard ``1j`` lists its concessions in
  order but names no breakpoints; these are chosen so that all of them
  have happened by the 940px window floor and none of them has happened at
  the 1280px width ``1a`` is drawn at.
* Rows are matched to runnable tasks by
  :attr:`~auto_ext.model.cells.CellEntry.key`, which is spelled exactly
  like the legacy ``task_id`` (``library__cell__layout__source``) --
  ``cells.py`` says so explicitly and ``core/config.py`` builds the id the
  same way. That equality is what lets the new table drive the old runner
  without a translation layer.
* Amber status text on the white table body uses
  :data:`~auto_ext.ui.theme.WARNING_TEXT_ON_WHITE`, which theme.py defines
  for exactly this ("amber as text on white"); the fill-weight
  ``#d69016`` is kept for glyphs and chips.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from PyQt5.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QPoint,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QBrush, QColor, QFont, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.progress import CancelToken
from auto_ext.core.runner import STAGE_ORDER
from auto_ext.model.cells import CellBook, CellEntry
from auto_ext.ui import theme
from auto_ext.ui.qt_reporter import QtProgressReporter
from auto_ext.ui.widgets.run_bar import RunBar, StageChipStrip, apply_families
from auto_ext.ui.worker import RunWorker

__all__ = [
    "COLUMN_TITLES",
    "COL_CELL",
    "COL_CHECK",
    "COL_GROUND",
    "COL_LAST_RUN",
    "COL_LAYOUT",
    "COL_LIBRARY",
    "COL_RECIPE",
    "COL_SOURCE",
    "COL_STAGES",
    "COL_STATUS",
    "COL_VIEWS",
    "CellsScreen",
    "EMPTY_STATE_WIDTH",
    "MODE_COMPACT",
    "MODE_RUNNING",
    "MODE_WIDE",
    "RecipeDelegate",
    "RowStatus",
    "RunRequest",
    "StatusColorDelegate",
    "TABLE_COMPACT_BELOW",
    "TOOLBAR_COMPACT_BELOW",
    "install_cells_page",
]

#: Screen width below which layout/source/ground merge into ``views``.
TABLE_COMPACT_BELOW = 1060
#: Screen width below which the toolbar drops to short button labels.
TOOLBAR_COMPACT_BELOW = 960

MODE_WIDE = "wide"
MODE_COMPACT = "compact"
MODE_RUNNING = "running"

COL_CHECK = 0
COL_LIBRARY = 1
COL_CELL = 2
COL_LAYOUT = 3
COL_SOURCE = 4
COL_GROUND = 5
COL_VIEWS = 6
COL_RECIPE = 7
COL_LAST_RUN = 8
COL_STATUS = 9
COL_STAGES = 10

COLUMN_TITLES = (
    "",
    "library",
    "cell",
    "layout",
    "source",
    "ground",
    "views",
    "recipe",
    "last run",
    "status",
    "stages",
)

#: Column -> :class:`CellEntry` field, for the columns the user may retype.
EDITABLE_FIELDS = {
    COL_LIBRARY: "library",
    COL_CELL: "cell",
    COL_LAYOUT: "layout_view",
    COL_SOURCE: "source_view",
    COL_GROUND: "ground_net",
}

#: Column -> width, straight off the artboard. ``COL_CELL`` stretches
#: instead. The artboard is a CSS grid whose cells are flush against each
#: other with one 8px inset on the row; a Qt item carries the design's
#: :data:`~auto_ext.ui.theme.CELL_PADDING_H` *inside* its own width, so
#: :func:`_applied_width` adds it back before the column is sized.
#: Without that, ``schematic`` does not fit the 74px ``source`` column.
_WIDE_WIDTHS = {
    COL_CHECK: 26,
    COL_LIBRARY: 128,
    COL_LAYOUT: 62,
    COL_SOURCE: 74,
    COL_GROUND: 62,
    COL_RECIPE: 138,
    COL_LAST_RUN: 122,
    COL_STATUS: 84,
}
_COMPACT_WIDTHS = {
    COL_CHECK: 24,
    COL_LIBRARY: 112,
    COL_VIEWS: 96,
    COL_RECIPE: 130,
    COL_LAST_RUN: 118,
    COL_STATUS: 76,
}
_RUNNING_WIDTHS = {
    COL_CHECK: 26,
    COL_LIBRARY: 116,
    COL_RECIPE: 126,
    COL_STAGES: 352,
}

_MODE_COLUMNS = {
    MODE_WIDE: (
        COL_CHECK,
        COL_LIBRARY,
        COL_CELL,
        COL_LAYOUT,
        COL_SOURCE,
        COL_GROUND,
        COL_RECIPE,
        COL_LAST_RUN,
        COL_STATUS,
    ),
    MODE_COMPACT: (
        COL_CHECK,
        COL_LIBRARY,
        COL_CELL,
        COL_VIEWS,
        COL_RECIPE,
        COL_LAST_RUN,
        COL_STATUS,
    ),
    MODE_RUNNING: (COL_CHECK, COL_LIBRARY, COL_CELL, COL_RECIPE, COL_STAGES),
}

OBJ_TOOLBAR = "cellsToolbar"
OBJ_TABLE = "cellsTable"
OBJ_EMPTY = "cellsEmptyState"
OBJ_EMPTY_TITLE = "cellsEmptyTitle"
OBJ_EMPTY_BODY = "cellsEmptyBody"
OBJ_EMPTY_NOTE = "cellsEmptyNote"
OBJ_FILTER = "cellsFilter"

#: Reading width of the empty-state panel, from artboard ``1i``.
EMPTY_STATE_WIDTH = 560

_LONG_LABELS = {
    "add": "Add cell",
    "duplicate": "Duplicate",
    "remove": "Remove",
    "import": "Import from tasks.yaml",
}
_SHORT_LABELS = {"add": "Add", "duplicate": "Dup", "remove": "Rm", "import": "Import"}


class RowStatus(NamedTuple):
    """What the ``last run`` / ``status`` pair says about one row.

    ``code`` is one of the four failure codes (``LIC`` / ``CFG`` / ``LVS``
    / ``CRS``); it is printed, not merely coloured, because two of the four
    share a hue on purpose.
    """

    status: str = "pending"
    text: str = "never run"
    when: str = ""
    code: str | None = None


class RunRequest(NamedTuple):
    """Everything a dispatch needs, as the screen understood it."""

    keys: tuple[str, ...]
    stages: tuple[str, ...]
    jobs: int
    dry_run: bool
    continue_on_lvs_fail: bool
    recipe_override: str | None


@dataclass
class _LiveRun:
    """Bookkeeping for the batch currently in flight."""

    keys: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    started: set[str] = field(default_factory=set)
    finished: dict[str, str] = field(default_factory=dict)
    run_dirs: dict[str, Path] = field(default_factory=dict)


def _status_text_color(status: str, code: str | None = None) -> str:
    """Text colour for the status column, on the white table body."""

    if code is not None:
        colour = theme.FAILURE_CODE_COLOR.get(code)
        if colour == theme.STATUS_WARNING:
            return theme.WARNING_TEXT_ON_WHITE
        if colour is not None:
            return colour
    if status in ("cancelled", "warning"):
        return theme.WARNING_TEXT_ON_WHITE
    return theme.status_color(status)


class StatusColorDelegate(QStyledItemDelegate):
    """Keep a row's status colour when the row is selected.

    Artboard ``1a`` selects the three rows that are about to be re-run and
    keeps red, green and amber on them -- seeing *why* you are re-running
    them is the point. Qt does not do that by itself: a selected cell is
    painted with ``QPalette.HighlightedText``, the model's
    ``ForegroundRole`` only ever reaches ``QPalette.Text``, and the
    application stylesheet pins the highlighted colour outright, so every
    status on a selected row would collapse into one.

    So: paint the selection fill, then hand the cell to the base delegate
    with the selected bit cleared, which puts the text back on the
    ``Text`` role the foreground actually reaches.
    """

    def paint(self, painter, option, index) -> None:
        brush = index.data(Qt.ForegroundRole)
        if brush is None or not (option.state & QStyle.State_Selected):
            super().paint(painter, option, index)
            return
        painter.save()
        painter.fillRect(option.rect, QColor(theme.ACCENT_SELECTION))
        unselected = QStyleOptionViewItem(option)
        unselected.state &= ~QStyle.State_Selected
        super().paint(painter, unselected, index)
        painter.restore()

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - Qt naming
        super().initStyleOption(option, index)
        value = index.data(Qt.ForegroundRole)
        if value is not None:
            option.palette.setBrush(QPalette.HighlightedText, QBrush(value))


class RecipeDelegate(StatusColorDelegate):
    """Combo-box editor for the ``recipe`` column.

    Writes straight through :meth:`CellsScreen.set_recipe_binding` rather
    than through the item's text, so the binding map stays the one answer
    to "what recipe does this row run" no matter how the cell was edited.
    """

    def __init__(self, screen: "CellsScreen") -> None:
        super().__init__(screen)
        self._screen = screen

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt naming
        editor = QComboBox(parent)
        editor.addItem("—", None)
        for recipe_id, name in self._screen.recipe_choices():
            editor.addItem(name, recipe_id)
        return editor

    def setEditorData(self, editor, index) -> None:  # noqa: N802 - Qt naming
        key = self._screen.key_at_row(index.row())
        current = self._screen.recipe_bindings().get(key) if key else None
        position = editor.findData(current) if current is not None else 0
        editor.setCurrentIndex(max(position, 0))

    def setModelData(self, editor, model, index) -> None:  # noqa: N802 - Qt naming
        key = self._screen.key_at_row(index.row())
        if key:
            data = editor.currentData()
            self._screen.set_recipe_binding(key, data if isinstance(data, str) else None)


class _EmptyState(QWidget):
    """The guidance panel of artboard ``1i``.

    Lives inside the table's viewport, not in the screen's layout, for two
    reasons: the header row has to stay visible so the shape of the table
    is obvious before there is any data in it, and an overlay contributes
    nothing to the screen's minimum size.
    """

    add_clicked = pyqtSignal()
    import_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_EMPTY)
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(theme.SPACE_XXL, theme.SPACE_XXL, theme.SPACE_XXL, theme.SPACE_XXL)
        # A panel that takes the width it is offered up to 560px, rather than a
        # bare layout that collapses to its widest child -- otherwise the prose
        # wraps at the width of the button row and reads like a poem.
        panel = QWidget(self)
        panel.setMaximumWidth(EMPTY_STATE_WIDTH)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.SPACE_XL)
        outer.addStretch(1)
        # AlignVCenter, or the word-wrapped labels absorb the spare height
        # and the paragraph drifts away from its heading.
        outer.addWidget(panel, 0, Qt.AlignVCenter)
        outer.addStretch(1)
        self._panel = panel

        title = QLabel("No cells yet.", panel)
        title.setObjectName(OBJ_EMPTY_TITLE)
        body = QLabel(
            "A cell is one thing you extract: a library, a cell name, its layout "
            "and schematic views, and the ground net. Add one and pick a recipe — "
            "that pair is the whole run.",
            panel,
        )
        body.setObjectName(OBJ_EMPTY_BODY)
        body.setWordWrap(True)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACE_MD)
        self._add = QPushButton(_LONG_LABELS["add"], panel)
        self._add.setProperty("primary", True)
        self._add.clicked.connect(self.add_clicked)
        self._import = QPushButton(_LONG_LABELS["import"], panel)
        self._import.clicked.connect(self.import_clicked)
        buttons.addWidget(self._add)
        buttons.addWidget(self._import)
        buttons.addStretch(1)

        note = QLabel(
            "Import reads the existing config — one row per expanded task.",
            panel,
        )
        note.setObjectName(OBJ_EMPTY_NOTE)
        note.setWordWrap(True)

        self._hint = QLabel("", panel)
        self._hint.setObjectName(OBJ_EMPTY_BODY)
        self._hint.setWordWrap(True)
        self._hint.hide()

        column.addWidget(title)
        column.addWidget(body)
        column.addLayout(buttons)
        column.addWidget(note)
        column.addWidget(self._hint)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Hold the panel at its reading width, or as much as there is.

        Stretch factors alone cannot express "560px when there is room,
        otherwise everything left over" -- a stretch of 0 collapses the
        panel onto its widest child, which is the button row, and the
        paragraph then wraps at button width.
        """

        super().resizeEvent(event)
        available = max(self.width() - 2 * theme.SPACE_XXL, 1)
        self._panel.setFixedWidth(min(EMPTY_STATE_WIDTH, available))

    def set_hint(self, text: str) -> None:
        self._hint.setText(text)
        self._hint.setVisible(bool(text))

    def add_button(self) -> QPushButton:
        return self._add

    def import_button(self) -> QPushButton:
        return self._import


class CellsScreen(QWidget):
    """The cell table, its toolbar, and the run bar under it.

    Signals
    -------
    ``cells_changed(object)``
        A new :class:`~auto_ext.model.cells.CellBook` after any add,
        remove or in-place edit. The screen never writes to disk; whoever
        owns the file listens here.
    ``selection_changed(object)``
        Tuple of selected row keys.
    ``run_requested(object)``
        A :class:`RunRequest`, emitted whenever Run is pressed -- including
        when the screen goes on to dispatch it itself.
    ``run_finished(object)``
        The :class:`~auto_ext.core.runner.RunSummary`, or ``None`` if the
        worker died before producing one.
    ``import_requested()``
        "Import from tasks.yaml" was pressed. Reading the old file is the
        host's job; the screen only asks.
    ``edit_rejected(str)``
        An in-place edit was refused by the model (duplicate row, empty
        field). The cell has already been put back.
    ``status_message(str)``
        One line for the shell's status bar.
    ``log_path_changed(object)``
        The stage log the run is following, as a :class:`~pathlib.Path`.
    ``open_log_requested(object)``
        The user pressed "Open in editor". Distinct from
        ``log_path_changed``, which only says where the follow moved to.
    """

    cells_changed = pyqtSignal(object)
    selection_changed = pyqtSignal(object)
    run_requested = pyqtSignal(object)
    run_finished = pyqtSignal(object)
    import_requested = pyqtSignal()
    edit_rejected = pyqtSignal(str)
    status_message = pyqtSignal(str)
    log_path_changed = pyqtSignal(object)
    open_log_requested = pyqtSignal(object)

    #: Set ``False`` to drive :meth:`set_column_mode` yourself.
    auto_compact: bool = True

    def __init__(
        self,
        controller: Any = None,
        *,
        book: CellBook | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._book = book if book is not None else CellBook()
        self._bindings: dict[str, str] = {}
        self._statuses: dict[str, RowStatus] = {}
        self._recipe_choices: list[tuple[str, str]] = []
        self._row_keys: list[str] = []
        self._mode = MODE_WIDE
        self._idle_mode = MODE_WIDE
        self._compact_toolbar = False
        self._syncing = False
        self._worker: RunWorker | None = None
        self._reporter: QtProgressReporter | None = None
        self._live = _LiveRun()

        self._build_ui()
        self.setStyleSheet(_CELLS_QSS)
        self._reload_table()

    # ---- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        self._splitter = QSplitter(Qt.Vertical, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)

        self._table = self._build_table()
        self._splitter.addWidget(self._table)

        self.run_bar = RunBar(self)
        self.run_bar.run_requested.connect(self.start_run)
        self.run_bar.cancel_requested.connect(self.cancel_run)
        self.run_bar.open_log_requested.connect(self.open_log_requested)
        self.run_bar.stages_changed.connect(lambda _s: self._refresh_run_bar())
        self._splitter.addWidget(self.run_bar)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        # Give the bottom pane exactly the idle bar's natural height: an
        # arbitrary number here would show as a slab of empty toolbar grey.
        self._splitter.setSizes([10_000, self.run_bar.sizeHint().height()])
        root.addWidget(self._splitter, 1)

        self._empty = _EmptyState(self._table.viewport())
        self._empty.add_clicked.connect(lambda: self.add_cell())
        self._empty.import_clicked.connect(self.import_requested)
        self._table.viewport().installEventFilter(self)
        self._empty.hide()

    def _build_toolbar(self) -> QWidget:
        bar = QFrame(self)
        bar.setObjectName(OBJ_TOOLBAR)
        bar.setFrameShape(QFrame.NoFrame)
        bar.setFixedHeight(theme.TOOLBAR_HEIGHT)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, 0)
        layout.setSpacing(theme.SPACE_SM)

        self._buttons: dict[str, QPushButton] = {}
        for name, slot in (
            # Wrapped: ``clicked`` carries a bool, and ``add_cell(False)``
            # would read that bool as an entry.
            ("add", lambda: self.add_cell()),
            ("duplicate", lambda: self.duplicate_selected()),
            ("remove", lambda: self.remove_selected()),
        ):
            button = QPushButton(_LONG_LABELS[name], bar)
            button.clicked.connect(slot)
            layout.addWidget(button)
            self._buttons[name] = button

        separator = QFrame(bar)
        separator.setObjectName("barSeparator")
        separator.setFrameShape(QFrame.NoFrame)
        separator.setFixedWidth(1)
        separator.setFixedHeight(18)
        layout.addWidget(separator)

        button = QPushButton(_LONG_LABELS["import"], bar)
        button.clicked.connect(self.import_requested)
        layout.addWidget(button)
        self._buttons["import"] = button

        layout.addStretch(1)
        self._filter = QLineEdit(bar)
        self._filter.setObjectName(OBJ_FILTER)
        self._filter.setPlaceholderText("filter")
        self._filter.setClearButtonEnabled(True)
        self._filter.setFixedWidth(190)
        self._filter.setMinimumWidth(0)
        self._filter.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)
        return bar

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(COLUMN_TITLES), self)
        table.setObjectName(OBJ_TABLE)
        table.setHorizontalHeaderLabels(list(COLUMN_TITLES))
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        table.setShowGrid(False)
        table.setWordWrap(False)
        table.setCornerButtonEnabled(False)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.setMinimumSize(0, 0)
        table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        table.setItemDelegate(StatusColorDelegate(self))
        table.setItemDelegateForColumn(COL_RECIPE, RecipeDelegate(self))

        header = table.horizontalHeader()
        header.setFixedHeight(theme.TABLE_HEADER_HEIGHT)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(20)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        rows = table.verticalHeader()
        rows.setVisible(False)
        # Order matters: the default section size is clamped by the minimum,
        # and the style's default minimum (30px here) is above the design's
        # 24px row. Lower the floor first, then set the size.
        rows.setMinimumSectionSize(1)
        rows.setSectionResizeMode(QHeaderView.Fixed)
        rows.setDefaultSectionSize(theme.ROW_HEIGHT)

        table.itemChanged.connect(self._on_item_changed)
        table.itemSelectionChanged.connect(self._on_selection_changed)
        table.customContextMenuRequested.connect(self._on_context_menu)
        return table

    @property
    def table(self) -> QTableWidget:
        return self._table

    @property
    def empty_state(self) -> _EmptyState:
        return self._empty

    @property
    def splitter(self) -> QSplitter:
        return self._splitter

    def toolbar_button(self, name: str) -> QPushButton:
        return self._buttons[name]

    def filter_edit(self) -> QLineEdit:
        return self._filter

    # ---- book ------------------------------------------------------------

    def cells(self) -> CellBook:
        return self._book

    def set_cells(self, book: CellBook) -> None:
        """Replace the whole table. Bindings and statuses for rows that
        survived the swap are kept; the rest are dropped."""

        self._book = book
        keys = set(book.keys)
        self._bindings = {k: v for k, v in self._bindings.items() if k in keys}
        self._statuses = {k: v for k, v in self._statuses.items() if k in keys}
        self._reload_table()
        self.cells_changed.emit(self._book)

    def key_at_row(self, row: int) -> str | None:
        if 0 <= row < len(self._row_keys):
            return self._row_keys[row]
        return None

    def row_of_key(self, key: str) -> int | None:
        try:
            return self._row_keys.index(key)
        except ValueError:
            return None

    def _apply_book(self, book: CellBook) -> bool:
        """Swap in a validated book and repaint. Returns ``True`` always;
        validation failures are raised by the caller's ``CellBook(...)``."""

        self._book = book
        self._reload_table()
        self.cells_changed.emit(self._book)
        return True

    # ---- rendering -------------------------------------------------------

    def _reload_table(self) -> None:
        selected = set(self.selected_keys())
        self._syncing = True
        try:
            self._table.clearContents()
            self._row_keys = list(self._book.keys)
            self._table.setRowCount(len(self._row_keys))
            for row, entry in enumerate(self._book):
                self._render_row(row, entry)
        finally:
            self._syncing = False
        self._apply_mode(self._mode)
        self._apply_filter(self._filter.text())
        if selected:
            self.set_selected_keys([k for k in self._row_keys if k in selected])
        self._refresh_empty_state()
        self._refresh_run_bar()

    def _render_row(self, row: int, entry: CellEntry) -> None:
        key = entry.key
        check = QTableWidgetItem()
        check.setData(Qt.UserRole, key)
        if self._mode == MODE_RUNNING:
            status = self._statuses.get(key, RowStatus())
            check.setText(theme.STATUS_GLYPH.get(status.status, theme.STATUS_GLYPH["pending"]))
            check.setForeground(QColor(_status_text_color(status.status, status.code)))
            check.setTextAlignment(_TEXT_ALIGN)
            check.setData(Qt.CheckStateRole, None)
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        else:
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Unchecked)
        self._table.setItem(row, COL_CHECK, check)

        values = {
            COL_LIBRARY: entry.library,
            COL_CELL: entry.cell,
            COL_LAYOUT: entry.layout_view,
            COL_SOURCE: entry.source_view,
            COL_GROUND: entry.ground_net,
            COL_VIEWS: f"{entry.layout_view}/{entry.source_view}",
            COL_RECIPE: self._recipe_name(key),
        }
        for column, text in values.items():
            item = QTableWidgetItem(text)
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            if column in EDITABLE_FIELDS or column == COL_RECIPE:
                flags |= Qt.ItemIsEditable
            item.setFlags(flags)
            item.setTextAlignment(_TEXT_ALIGN)
            if column != COL_RECIPE:
                item.setFont(_mono_font())
            if not entry.enabled:
                item.setForeground(QColor(theme.TEXT_DISABLED))
            self._table.setItem(row, column, item)

        status = self._statuses.get(key, RowStatus())
        when = QTableWidgetItem(status.when or "—")
        when.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        when.setTextAlignment(_TEXT_ALIGN)
        when.setFont(_mono_font())
        when.setForeground(
            QColor(theme.TEXT_SECONDARY if status.when else theme.TEXT_DISABLED)
        )
        self._table.setItem(row, COL_LAST_RUN, when)
        self._table.setItem(row, COL_STATUS, self._status_item(status))

        strip = StageChipStrip(self._live.stages or STAGE_ORDER)
        strip.set_placeholder("queued")
        self._table.setCellWidget(row, COL_STAGES, strip)

        tooltip = _row_tooltip(entry, self._recipe_name(key), status)
        for column in range(len(COLUMN_TITLES)):
            item = self._table.item(row, column)
            if item is not None:
                item.setToolTip(tooltip)

    def _status_item(self, status: RowStatus) -> QTableWidgetItem:
        glyph = theme.STATUS_GLYPH.get(status.status, theme.STATUS_GLYPH["pending"])
        label = f"{status.code} {status.text}" if status.code else status.text
        item = QTableWidgetItem(f"{glyph} {label}")
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        item.setTextAlignment(_TEXT_ALIGN)
        item.setFont(_mono_font())
        item.setForeground(QColor(_status_text_color(status.status, status.code)))
        return item

    def _recipe_name(self, key: str) -> str:
        recipe_id = self._bindings.get(key)
        if recipe_id is None:
            return "—"
        for candidate, name in self._recipe_choices:
            if candidate == recipe_id:
                return name
        return recipe_id

    def _refresh_empty_state(self) -> None:
        empty = self._table.rowCount() == 0
        self._empty.setVisible(empty)
        if empty:
            self._empty.setGeometry(self._table.viewport().rect())
            self._empty.raise_()
        # 1i makes Add the primary action while there is nothing to act on.
        button = self._buttons["add"]
        if bool(button.property("primary")) != empty:
            button.setProperty("primary", empty)
            button.style().unpolish(button)
            button.style().polish(button)

    def set_empty_state_hint(self, text: str) -> None:
        """One extra line under the empty state (a Setup verdict, say)."""

        self._empty.set_hint(text)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if watched is self._table.viewport() and event.type() == QEvent.Resize:
            if self._empty.isVisible():
                self._empty.setGeometry(self._table.viewport().rect())
        return super().eventFilter(watched, event)

    # ---- column modes ----------------------------------------------------

    def column_mode(self) -> str:
        return self._mode

    def set_column_mode(self, mode: str) -> None:
        if mode not in _MODE_COLUMNS:
            raise ValueError(f"unknown column mode {mode!r}")
        if mode == self._mode:
            return
        was_running = self._mode == MODE_RUNNING
        self._mode = mode
        if mode != MODE_RUNNING:
            self._idle_mode = mode
        self._apply_mode(mode)
        if was_running or mode == MODE_RUNNING:
            self._repaint_check_column()

    def _apply_mode(self, mode: str) -> None:
        shown = _MODE_COLUMNS[mode]
        widths = {
            MODE_WIDE: _WIDE_WIDTHS,
            MODE_COMPACT: _COMPACT_WIDTHS,
            MODE_RUNNING: _RUNNING_WIDTHS,
        }[mode]
        header = self._table.horizontalHeader()
        for column in range(len(COLUMN_TITLES)):
            self._table.setColumnHidden(column, column not in shown)
        for column, width in widths.items():
            header.setSectionResizeMode(column, QHeaderView.Interactive)
            self._table.setColumnWidth(column, _applied_width(column, width))
        header.setSectionResizeMode(COL_CELL, QHeaderView.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(
            theme.STAGE_CHIP_ROW_HEIGHT if mode == MODE_RUNNING else theme.ROW_HEIGHT
        )

    def _repaint_check_column(self) -> None:
        selected = set(self.selected_keys())
        self._syncing = True
        try:
            for row, key in enumerate(self._row_keys):
                item = self._table.item(row, COL_CHECK)
                if item is None:
                    continue
                if self._mode == MODE_RUNNING:
                    status = self._statuses.get(key, RowStatus())
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    # Qt draws a check indicator for anything carrying a
                    # CheckStateRole, flags or no flags. Clear it or the
                    # glyph shares its cell with a dead checkbox.
                    item.setData(Qt.CheckStateRole, None)
                    item.setText(
                        theme.STATUS_GLYPH.get(status.status, theme.STATUS_GLYPH["pending"])
                    )
                    item.setForeground(
                        QColor(_status_text_color(status.status, status.code))
                    )
                else:
                    item.setText("")
                    item.setFlags(
                        Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                    )
                    item.setCheckState(Qt.Checked if key in selected else Qt.Unchecked)
        finally:
            self._syncing = False

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        width = event.size().width()
        compact_toolbar = width < TOOLBAR_COMPACT_BELOW
        if compact_toolbar != self._compact_toolbar:
            self._compact_toolbar = compact_toolbar
            labels = _SHORT_LABELS if compact_toolbar else _LONG_LABELS
            for name, button in self._buttons.items():
                button.setText(labels[name])
        if self.auto_compact and self._mode != MODE_RUNNING:
            self.set_column_mode(MODE_COMPACT if width < TABLE_COMPACT_BELOW else MODE_WIDE)

    # ---- selection -------------------------------------------------------

    def selected_keys(self) -> tuple[str, ...]:
        rows = sorted({index.row() for index in self._table.selectedIndexes()})
        return tuple(self._row_keys[row] for row in rows if row < len(self._row_keys))

    def set_selected_keys(self, keys: Iterable[str]) -> None:
        """Select exactly ``keys``.

        Built as one :class:`QItemSelection` rather than a loop of
        ``selectRow``: under ``ExtendedSelection`` each ``selectRow`` is a
        ClearAndSelect, so a loop would leave only the last row selected.
        """

        wanted = set(keys)
        model = self._table.selectionModel()
        if model is None:
            return
        selection = QItemSelection()
        last_column = self._table.columnCount() - 1
        source = self._table.model()
        for row, key in enumerate(self._row_keys):
            if key in wanted:
                selection.select(source.index(row, 0), source.index(row, last_column))
        model.select(
            selection, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows
        )

    def _on_selection_changed(self) -> None:
        if self._syncing:
            return
        self._repaint_check_column()
        self._refresh_run_bar()
        self.selection_changed.emit(self.selected_keys())

    def _refresh_run_bar(self) -> None:
        keys = self.selected_keys()
        self.run_bar.set_selection_count(len(keys))
        distinct = {self._bindings.get(key) for key in keys}
        distinct.discard(None)
        self.run_bar.set_per_row_summary(len(distinct))
        has_rows = bool(self._row_keys)
        self._buttons["duplicate"].setEnabled(bool(keys))
        self._buttons["remove"].setEnabled(bool(keys))
        self._buttons["add"].setEnabled(self._worker is None)
        self._buttons["import"].setEnabled(self._worker is None)
        if not has_rows:
            self._buttons["duplicate"].setEnabled(False)
            self._buttons["remove"].setEnabled(False)

    # ---- editing ---------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._syncing:
            return
        column = item.column()
        if column == COL_CHECK:
            self._on_check_toggled(item)
            return
        field_name = EDITABLE_FIELDS.get(column)
        if field_name is None:
            return
        self._commit_edit(item.row(), field_name, item.text().strip())

    def _on_check_toggled(self, item: QTableWidgetItem) -> None:
        row = item.row()
        checked = item.checkState() == Qt.Checked
        already = row in {index.row() for index in self._table.selectedIndexes()}
        if checked == already:
            return
        model = self._table.selectionModel()
        if model is None:
            return
        flag = QItemSelectionModel.Select if checked else QItemSelectionModel.Deselect
        self._syncing = True
        try:
            model.select(
                self._table.model().index(row, 0), flag | QItemSelectionModel.Rows
            )
        finally:
            self._syncing = False
        self._refresh_run_bar()
        self.selection_changed.emit(self.selected_keys())

    def _commit_edit(self, row: int, field_name: str, value: str) -> None:
        """Apply one in-place edit, or put the old text back and say why.

        Validation is the model's: a rebuilt :class:`CellEntry` catches an
        emptied field and a rebuilt :class:`CellBook` catches the duplicate
        row that a rename can create. Nothing is second-guessed here.
        """

        key = self.key_at_row(row)
        if key is None:
            return
        entry = self._book.entry(key)
        if getattr(entry, field_name) == value:
            return
        payload = entry.model_dump()
        payload[field_name] = value
        try:
            replacement = CellEntry(**payload)
            entries = [replacement if e.key == key else e for e in self._book]
            book = CellBook(schema_version=self._book.schema_version, cells=entries)
        except Exception as exc:  # noqa: BLE001 - pydantic raises many shapes
            self._syncing = True
            try:
                item = self._table.item(row, _column_of_field(field_name))
                if item is not None:
                    item.setText(getattr(entry, field_name))
            finally:
                self._syncing = False
            self.edit_rejected.emit(_first_line(exc))
            self.status_message.emit(f"edit refused: {_first_line(exc)}")
            return
        if replacement.key != key:
            if key in self._bindings:
                self._bindings[replacement.key] = self._bindings.pop(key)
            if key in self._statuses:
                self._statuses[replacement.key] = self._statuses.pop(key)
        self._apply_book(book)

    # ---- row commands ----------------------------------------------------

    def add_cell(self, entry: CellEntry | None = None) -> str | None:
        """Append a row and start editing its cell name. Returns its key."""

        if self._worker is not None:
            return None
        candidate = entry if entry is not None else self._blank_entry()
        try:
            book = self._book.with_added([candidate])
        except Exception as exc:  # noqa: BLE001 - pydantic raises many shapes
            self.edit_rejected.emit(_first_line(exc))
            return None
        self._apply_book(book)
        row = self.row_of_key(candidate.key)
        if row is not None:
            self.set_selected_keys([candidate.key])
            self._table.setCurrentCell(row, COL_CELL)
            self._table.editItem(self._table.item(row, COL_CELL))
        return candidate.key

    def _blank_entry(self) -> CellEntry:
        existing = set(self._book.keys)
        index = len(self._book) + 1
        while True:
            candidate = CellEntry(
                library="library",
                cell=f"cell_{index}",
                layout_view="layout",
                source_view="schematic",
            )
            if candidate.key not in existing:
                return candidate
            index += 1

    def duplicate_selected(self) -> tuple[str, ...]:
        """Copy every selected row, renaming the copy until its key is free."""

        if self._worker is not None:
            return ()
        keys = self.selected_keys()
        if not keys:
            return ()
        taken = set(self._book.keys)
        copies: list[CellEntry] = []
        for key in keys:
            entry = self._book.entry(key)
            index = 1
            while True:
                suffix = "_copy" if index == 1 else f"_copy{index}"
                candidate = CellEntry(**{**entry.model_dump(), "cell": f"{entry.cell}{suffix}"})
                if candidate.key not in taken:
                    break
                index += 1
            taken.add(candidate.key)
            copies.append(candidate)
            if key in self._bindings:
                self._bindings[candidate.key] = self._bindings[key]
        self._apply_book(self._book.with_added(copies))
        new_keys = tuple(c.key for c in copies)
        self.set_selected_keys(new_keys)
        return new_keys

    def remove_selected(self) -> tuple[str, ...]:
        """Drop every selected row."""

        if self._worker is not None:
            return ()
        keys = set(self.selected_keys())
        if not keys:
            return ()
        remaining = [entry for entry in self._book if entry.key not in keys]
        for key in keys:
            self._bindings.pop(key, None)
            self._statuses.pop(key, None)
        self._apply_book(
            CellBook(schema_version=self._book.schema_version, cells=remaining)
        )
        return tuple(sorted(keys))

    def set_enabled_for(self, keys: Iterable[str], enabled: bool) -> None:
        """Park rows (``enabled=False``) or bring them back.

        The other half of the old ``exclude``: a parked row keeps its place
        in the table and stays out of every batch.
        """

        wanted = set(keys)
        if not wanted:
            return
        entries = [
            CellEntry(**{**entry.model_dump(), "enabled": enabled})
            if entry.key in wanted
            else entry
            for entry in self._book
        ]
        self._apply_book(
            CellBook(schema_version=self._book.schema_version, cells=entries)
        )

    # ---- recipes ---------------------------------------------------------

    def recipe_choices(self) -> list[tuple[str, str]]:
        return list(self._recipe_choices)

    def set_recipe_choices(self, choices: Sequence[tuple[str, str]]) -> None:
        """``(recipe_id, display name)`` pairs for the column and the bar."""

        self._recipe_choices = [(str(a), str(b)) for a, b in choices]
        self.run_bar.set_recipe_choices(self._recipe_choices)
        self._reload_table()

    def recipe_bindings(self) -> dict[str, str]:
        """Row key -> recipe id. See the module's Assumptions."""

        return dict(self._bindings)

    def set_recipe_bindings(self, bindings: dict[str, str]) -> None:
        self._bindings = {k: v for k, v in bindings.items() if k in set(self._book.keys)}
        self._reload_table()

    def set_recipe_binding(self, key: str, recipe_id: str | None) -> None:
        if recipe_id is None:
            self._bindings.pop(key, None)
        else:
            self._bindings[key] = recipe_id
        row = self.row_of_key(key)
        if row is not None:
            self._syncing = True
            try:
                item = self._table.item(row, COL_RECIPE)
                if item is not None:
                    item.setText(self._recipe_name(key))
            finally:
                self._syncing = False
        self._refresh_run_bar()

    # ---- statuses --------------------------------------------------------

    def row_status(self, key: str) -> RowStatus:
        return self._statuses.get(key, RowStatus())

    def set_row_status(
        self,
        key: str,
        status: str,
        *,
        text: str | None = None,
        when: str = "",
        code: str | None = None,
    ) -> None:
        """Set the ``last run`` / ``status`` pair for one row."""

        record = RowStatus(status=status, text=text or status, when=when, code=code)
        self._statuses[key] = record
        row = self.row_of_key(key)
        if row is None:
            return
        self._syncing = True
        try:
            self._table.setItem(row, COL_STATUS, self._status_item(record))
            when_item = self._table.item(row, COL_LAST_RUN)
            if when_item is not None:
                when_item.setText(record.when or "—")
                when_item.setForeground(
                    QColor(theme.TEXT_SECONDARY if record.when else theme.TEXT_DISABLED)
                )
            cell = self._table.item(row, COL_CELL)
            if cell is not None:
                font = cell.font()
                font.setBold(record.status == "running")
                cell.setFont(font)
            check = self._table.item(row, COL_CHECK)
            if check is not None and self._mode == MODE_RUNNING:
                check.setText(
                    theme.STATUS_GLYPH.get(record.status, theme.STATUS_GLYPH["pending"])
                )
                check.setForeground(QColor(_status_text_color(record.status, record.code)))
        finally:
            self._syncing = False

    def set_row_statuses(self, statuses: dict[str, RowStatus]) -> None:
        for key, record in statuses.items():
            self.set_row_status(
                key,
                record.status,
                text=record.text,
                when=record.when,
                code=record.code,
            )

    def stage_strip(self, key: str) -> StageChipStrip | None:
        row = self.row_of_key(key)
        if row is None:
            return None
        widget = self._table.cellWidget(row, COL_STAGES)
        return widget if isinstance(widget, StageChipStrip) else None

    # ---- filter ----------------------------------------------------------

    def set_filter_text(self, text: str) -> None:
        self._filter.setText(text)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row, key in enumerate(self._row_keys):
            if not needle:
                self._table.setRowHidden(row, False)
                continue
            entry = self._book.entry(key)
            haystack = " ".join(
                (
                    entry.library,
                    entry.cell,
                    entry.layout_view,
                    entry.source_view,
                    entry.ground_net,
                    entry.display_name or "",
                    self._recipe_name(key),
                )
            ).lower()
            self._table.setRowHidden(row, needle not in haystack)

    def visible_keys(self) -> tuple[str, ...]:
        return tuple(
            key for row, key in enumerate(self._row_keys) if not self._table.isRowHidden(row)
        )

    # ---- context menu ----------------------------------------------------

    def _on_context_menu(self, pos: QPoint) -> None:
        """Right-click menu for the table.

        X11 delivers the context-menu event on button *press*; a synchronous
        ``exec_()`` is dismissed by the following release, which reads to
        the user as "I have to right-click twice". Defer the popup one
        event-loop tick.
        """

        row = self._table.rowAt(pos.y())
        keys = self.selected_keys()
        if row >= 0 and row < len(self._row_keys):
            key = self._row_keys[row]
            if key not in keys:
                self.set_selected_keys([key])
                keys = (key,)
        menu = QMenu(self._table)
        running = self._worker is not None

        act_add = QAction(_LONG_LABELS["add"], menu)
        act_add.triggered.connect(self.add_cell)
        act_add.setEnabled(not running)
        menu.addAction(act_add)

        act_duplicate = QAction("Duplicate", menu)
        act_duplicate.triggered.connect(self.duplicate_selected)
        act_duplicate.setEnabled(bool(keys) and not running)
        menu.addAction(act_duplicate)

        act_remove = QAction("Remove", menu)
        act_remove.triggered.connect(self.remove_selected)
        act_remove.setEnabled(bool(keys) and not running)
        menu.addAction(act_remove)

        menu.addSeparator()
        all_enabled = all(self._book.entry(k).enabled for k in keys) if keys else False
        toggle_text = "Disable rows" if all_enabled else "Enable rows"
        act_toggle = QAction(toggle_text, menu)
        act_toggle.setEnabled(bool(keys) and not running)
        act_toggle.triggered.connect(
            lambda _checked=False, keys=keys, enable=not all_enabled: self.set_enabled_for(
                keys, enable
            )
        )
        menu.addAction(act_toggle)

        menu.addSeparator()
        act_copy = QAction("Copy row key", menu)
        act_copy.setEnabled(bool(keys))
        act_copy.triggered.connect(lambda _checked=False, keys=keys: _copy_keys(keys))
        menu.addAction(act_copy)

        global_pos = self._table.viewport().mapToGlobal(pos)
        QTimer.singleShot(0, lambda: menu.exec_(global_pos))

    # ---- running ---------------------------------------------------------

    def is_running(self) -> bool:
        return self._worker is not None

    def run_request(self) -> RunRequest:
        """What pressing Run right now would ask for."""

        keys = tuple(k for k in self.selected_keys() if self._book.entry(k).enabled)
        return RunRequest(
            keys=keys,
            stages=self.run_bar.selected_stages(),
            jobs=self.run_bar.jobs(),
            dry_run=self.run_bar.is_dry_run(),
            continue_on_lvs_fail=self.run_bar.continue_on_lvs_fail(),
            recipe_override=self.run_bar.recipe_override(),
        )

    def start_run(self) -> None:
        """Announce the request, then dispatch it if we have a controller.

        ``run_requested`` fires either way, so a host that wants to own the
        dispatch can connect to it and construct the screen without a
        controller.
        """

        if self._worker is not None:
            return
        request = self.run_request()
        if not request.keys or not request.stages:
            return
        self.run_requested.emit(request)
        if self._controller is None:
            return
        self._dispatch(request)

    def _dispatch(self, request: RunRequest) -> None:
        controller = self._controller
        project = getattr(controller, "project", None)
        if project is None:
            QMessageBox.warning(self, "No config", "Load a config directory first.")
            return
        by_id = {task.task_id: task for task in controller.tasks}
        missing = [key for key in request.keys if key not in by_id]
        if missing:
            QMessageBox.warning(
                self,
                "Rows not runnable",
                "These rows have no loaded task:\n\n"
                + "\n".join(missing)
                + "\n\nReload the config, or remove the rows.",
            )
            return
        auto_ext_root = controller.auto_ext_root
        workarea = controller.workarea
        if auto_ext_root is None or workarea is None:
            QMessageBox.critical(
                self,
                "Paths unresolved",
                "auto_ext_root and workarea could not be derived. "
                "Pass --auto-ext-root / --workarea to the gui command.",
            )
            return

        # ``run_tasks`` takes one recipe and one profile for the whole batch
        # (the recipe says what to extract, the profile supplies the process
        # literals). The run bar's override picks the recipe; with no override
        # the controller only answers when there is exactly one candidate,
        # because running the wrong settings produces plausible parasitics.
        recipe = None
        resolver = getattr(controller, "run_recipe", None)
        if callable(resolver):
            recipe = resolver(request.recipe_override)
        profile = getattr(controller, "profile", None)
        if recipe is None or profile is None:
            QMessageBox.warning(
                self,
                "Nothing to run with",
                "A run needs one recipe and one PDK profile.\n\n"
                "Pick a recipe in the run bar (or keep exactly one in the "
                "library), and make sure config/profiles/ holds the profile "
                "workspace.yaml names.",
            )
            return

        tasks = [by_id[key] for key in request.keys]
        reporter = QtProgressReporter()
        reporter.run_started.connect(self._on_run_started)
        reporter.task_started.connect(self._on_task_started)
        reporter.stage_started.connect(self._on_stage_started)
        reporter.stage_finished.connect(self._on_stage_finished)
        reporter.task_finished.connect(self._on_task_finished)
        reporter.run_dir_ready.connect(self._on_run_dir_ready)
        token = CancelToken()

        self._reporter = reporter
        self._live = _LiveRun(keys=request.keys, stages=request.stages)
        self._worker = RunWorker(
            project=project,
            tasks=tasks,
            stages=list(request.stages),
            auto_ext_root=auto_ext_root,
            workarea=workarea,
            reporter=reporter,
            cancel_token=token,
            recipe=recipe,
            profile=profile,
            resources=getattr(controller, "resources", None),
            max_workers=request.jobs if request.jobs >= 2 else None,
            dry_run=request.dry_run,
        )
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_done)
        self._enter_running_state(request)
        self._worker.start()

    def cancel_run(self) -> None:
        if self._worker is None:
            return
        self._worker.request_cancel()
        self.run_bar.mark_cancelling()
        self.status_message.emit("cancelling — the runner stops at its next check")

    def _enter_running_state(self, request: RunRequest) -> None:
        self.set_column_mode(MODE_RUNNING)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for button in self._buttons.values():
            button.setEnabled(False)
        self.run_bar.set_running(True)
        self.run_bar.set_run_label(f"Run {len(request.keys)} cells")
        self.run_bar.set_counts(queued=len(request.keys))
        self.run_bar.set_log_path(None)
        self._reveal_run_panel()
        for key in self._row_keys:
            strip = self.stage_strip(key)
            if strip is None:
                continue
            strip.set_stages(request.stages)
            if key in request.keys:
                strip.set_placeholder("queued")
            else:
                strip.set_placeholder("—")
        for key in request.keys:
            self.set_row_status(key, "pending", text="queued")
        self.status_message.emit("running — edits are locked while a run is in flight")

    def _reveal_run_panel(self) -> None:
        """Give the run panel room, once, when a run starts.

        Concession 5 of artboard ``1j`` is that the panel opens *over* the
        table on a splitter the user drags -- so this fires exactly once,
        at the reveal, and never fights a handle the user has moved.
        """

        if self.run_bar.log_widget() is None:
            return  # two thin strips; there is nothing to make room for
        sizes = self._splitter.sizes()
        total = sum(sizes) or self._splitter.height()
        if total <= 0 or len(sizes) != 2:
            return
        if sizes[1] >= total // 3:
            return
        top = total * 2 // 5
        self._splitter.setSizes([top, total - top])

    def _restore_table_space(self) -> None:
        """Hand the height back to the table when the run panel goes away."""

        sizes = self._splitter.sizes()
        total = sum(sizes) or self._splitter.height()
        if total <= 0 or len(sizes) != 2:
            return
        bar = max(self.run_bar.sizeHint().height(), self.run_bar.minimumSizeHint().height())
        if bar <= 0 or bar >= total:
            return
        self._splitter.setSizes([total - bar, bar])

    def _leave_running_state(self) -> None:
        self._worker = None
        self._reporter = None
        self.run_bar.set_running(False)
        self._restore_table_space()
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self.set_column_mode(self._idle_mode)
        self._refresh_run_bar()

    def _update_counts(self) -> None:
        finished = self._live.finished
        passed = sum(1 for status in finished.values() if status == "passed")
        failed = sum(1 for status in finished.values() if status not in ("passed",))
        running = len(self._live.started) - len(finished)
        queued = len(self._live.keys) - len(self._live.started)
        self.run_bar.set_counts(
            passed=passed, failed=failed, running=max(running, 0), queued=max(queued, 0)
        )

    # ---- reporter slots (all on the GUI thread) --------------------------

    def _on_run_started(self, _total: int, stages: list) -> None:
        self._live.stages = tuple(stages)
        self._update_counts()

    def _on_task_started(self, task_id: str, _stages: list) -> None:
        self._live.started.add(task_id)
        strip = self.stage_strip(task_id)
        if strip is not None:
            strip.set_placeholder(None)
            strip.clear_statuses()
        self.set_row_status(task_id, "running", text="running")
        self._update_counts()

    def _on_run_dir_ready(self, task_id: str, run_dir: object) -> None:
        if isinstance(run_dir, (str, Path)):
            self._live.run_dirs[task_id] = Path(run_dir)

    def _on_stage_started(self, task_id: str, stage: str) -> None:
        strip = self.stage_strip(task_id)
        if strip is not None:
            strip.set_status(stage, "running")
        if self.run_bar.follows_current_stage():
            path = self._stage_log_path(task_id, stage)
            self.run_bar.set_log_path(path)
            if path is not None:
                self.log_path_changed.emit(path)
        self.status_message.emit(f"running — {task_id} / {stage}")

    def _on_stage_finished(
        self, task_id: str, stage: str, status: str, error: object
    ) -> None:
        strip = self.stage_strip(task_id)
        if strip is not None:
            strip.set_status(stage, status)
            if error:
                strip.setToolTip(str(error))

    def _on_task_finished(self, task_id: str, status: str) -> None:
        self._live.finished[task_id] = status
        self.set_row_status(task_id, status, text=status)
        self._update_counts()

    def _on_worker_error(self, message: str) -> None:
        QMessageBox.critical(self, "Run failed", message)

    def _on_worker_done(self) -> None:
        summary = self._worker.summary if self._worker is not None else None
        self._leave_running_state()
        finished = self._live.finished
        passed = sum(1 for status in finished.values() if status == "passed")
        self.status_message.emit(f"idle — {passed}/{len(finished)} passed")
        self.run_finished.emit(summary)

    def _stage_log_path(self, task_id: str, stage: str) -> Path | None:
        """``runs/<run_id>/logs/<stage>.log``, once the run dir is known.

        The run directory arrives on the reporter's ``run_dir_ready``
        event. It is not derivable from the row key -- run identity is a
        UTC timestamp -- so before that event there is no log path to give.
        """

        run_dir = self._live.run_dirs.get(task_id)
        if run_dir is None:
            return None
        return run_dir / "logs" / f"{stage}.log"

    # ---- sizing ----------------------------------------------------------
    #
    # There is deliberately no ``minimumSizeHint`` override here. The screen
    # must never be why the 940x560 window floor fails, and the way to
    # guarantee that is for every piece to genuinely shrink -- the toolbar
    # swaps to short labels, the table scrolls, long strings elide, the run
    # bar folds -- not for the screen to advertise a number its contents
    # cannot honour. ``test_cells_screen.py`` pins the derived minimum, so a
    # future widget that quietly demands 400px of its own fails a test
    # instead of pushing the window past a 1366x768 laptop screen.


#: Every cell reads left to right. A stylesheet ``::item`` rule routes item
#: painting through QStyleSheetStyle, which centres text unless told
#: otherwise, and a centred column of paths is unreadable.
_TEXT_ALIGN = int(Qt.AlignLeft | Qt.AlignVCenter)


def _applied_width(column: int, artboard_width: int) -> int:
    """The artboard width plus the cell padding Qt puts inside the column.

    ``COL_CHECK`` holds a checkbox indicator the style centres itself, and
    ``COL_STAGES`` holds a widget that paints its own insets, so neither
    takes the text padding.
    """

    if column in (COL_CHECK, COL_STAGES):
        return artboard_width
    return artboard_width + 2 * theme.CELL_PADDING_H


def _mono_font() -> QFont:
    font = QFont()
    apply_families(font, theme.FONT_MONO_FAMILIES)
    font.setPixelSize(theme.FONT_SIZE_MONO)
    return font


def _column_of_field(field_name: str) -> int:
    for column, name in EDITABLE_FIELDS.items():
        if name == field_name:
            return column
    raise KeyError(field_name)


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("For further information"):
            return line
    return text or type(exc).__name__


def _row_tooltip(entry: CellEntry, recipe_name: str, status: RowStatus) -> str:
    """Everything the compact column set had to drop, in one tooltip."""

    lines = [entry.key]
    if entry.display_name:
        lines.append(f"display name: {entry.display_name}")
    lines += [
        f"ground net: {entry.ground_net}",
        f"recipe: {recipe_name}",
    ]
    if entry.out_file:
        lines.append(f"extracted view: {entry.out_file}")
    if not entry.enabled:
        lines.append("disabled — stays out of every batch")
    if entry.note:
        lines.append(entry.note)
    if status.when:
        lines.append(f"last run: {status.when}")
    return "\n".join(lines)


def _copy_keys(keys: Sequence[str]) -> None:
    from PyQt5.QtWidgets import QApplication

    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText("\n".join(keys))


def install_cells_page(
    shell: Any,
    screen: CellsScreen | None = None,
    *,
    key: str = "cells",
    label: str = "Cells",
    code: str = "CEL",
    controller: Any = None,
) -> CellsScreen:
    """Register a :class:`CellsScreen` as a page on ``shell``.

    Uses only the shell's published API (:meth:`Shell.add_page` /
    :meth:`Shell.set_page_count`) and keeps the nav item's count in step
    with the table, so the screen owns its own registration and no host
    module has to know how many rows there are.
    """

    screen = screen if screen is not None else CellsScreen(controller)
    shell.add_page(key, label, screen, code=code, count=len(screen.cells()))
    screen.cells_changed.connect(
        lambda book, _key=key: shell.set_page_count(_key, len(book))
    )
    return screen


_CELLS_QSS = f"""
QFrame#{OBJ_TOOLBAR} {{
    background: {theme.SURFACE_TOOLBAR};
    border: none;
    border-bottom: 1px solid {theme.LINE_STRUCTURAL};
}}
QFrame#barSeparator {{
    background: {theme.LINE_SEPARATOR};
    border: none;
}}
QLineEdit#{OBJ_FILTER} {{
    font-family: {theme.FONT_MONO};
    font-size: {theme.FONT_SIZE_META}px;
}}
QTableWidget#{OBJ_TABLE} {{
    background: {theme.SURFACE_CARD};
    border: none;
    outline: 0;
}}
QTableWidget#{OBJ_TABLE}::item {{
    border-bottom: 1px solid {theme.LINE_ROW};
    padding: {theme.CELL_PADDING_V}px {theme.CELL_PADDING_H}px;
}}
QTableWidget#{OBJ_TABLE}::item:selected {{
    background: {theme.ACCENT_SELECTION};
}}
QWidget#{OBJ_EMPTY} {{
    background: {theme.SURFACE_CARD};
}}
QLabel#{OBJ_EMPTY_TITLE} {{
    font-size: {theme.FONT_SIZE_TITLE}px;
    font-weight: {theme.FONT_WEIGHT_SEMIBOLD};
}}
QLabel#{OBJ_EMPTY_BODY} {{
    color: {theme.TEXT_SECONDARY};
}}
QLabel#{OBJ_EMPTY_NOTE} {{
    font-family: {theme.FONT_MONO};
    font-size: {theme.FONT_SIZE_META}px;
    color: {theme.TEXT_SECONDARY};
    border: 1px solid {theme.LINE_PANEL};
    background: {theme.SURFACE_PAGE};
    padding: {theme.SPACE_MD}px {theme.SPACE_LG}px;
}}
"""
