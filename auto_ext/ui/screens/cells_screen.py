"""The Cells screen: one row per DUT, tick some, run them.

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

The check column is the run set
------------------------------
Two questions get asked of this table and they are not the same question:
*which rows am I pointing at* (edit, duplicate, remove, park, export) and
*which rows do I want to run*. The highlight answers the first, the
checkboxes answer the second, and nothing writes across.

Until 2026-09-04 they were one thing: :meth:`CellsScreen._on_selection_changed`
repainted the whole check column from the selection, and clicking a box
selected its row. The column therefore carried no information -- it was the
highlight drawn twice -- and because a plain left click is
``ClearAndSelect``, opening a row or double-clicking one to retype its cell
name silently emptied a batch the user had built one box at a time. That is
the defect this split fixes; the run set now survives clicks, edits, the
filter, a rename (the tick travels with the row) and the run itself.

What that costs is that ticking has to be reachable four ways, because a
26px box is not a batch-building tool: the box itself, **Space** on every
highlighted row (so ``Ctrl+A`` then ``Space`` is "run everything"), the
check-all box in the header (:class:`CheckHeader`, visible rows only), and
the context menu's *Check rows* / *Clear all checks*.

:meth:`CellsScreen.run_keys` -- ticked *and* not parked -- is the one answer
to "what would Run run": :meth:`~CellsScreen.run_request` builds from it and
the run bar counts it, so the number on the button is the number of cells
that will start.

Column modes
------------
The same eleven physical columns are shown in three arrangements:

``wide`` (``1a``)
    check, library, cell, layout, source, ground, out view, recipe, last run,
    status
``compact`` (``1j``, concession 2, below :data:`TABLE_COMPACT_BELOW`)
    check, library, cell, views, out view, recipe, last run, status --
    ``layout`` and ``source`` merge into ``views`` and ``ground`` moves to the
    row tooltip. The cell name is the one elastic column and never truncates
    first.

``out view`` is not on artboard ``1a``. It is :attr:`CellEntry.out_file`, the
extracted-view name Quantus writes with ``-view_name`` and Jivaro then reads
as ``inputView`` -- the whole back half of the flow hangs off it, and until
this column existed it appeared only in the row tooltip and could not be
typed anywhere in the GUI. It stays in ``running`` mode's hidden set like
every other editable column.
``running`` (``1b``)
    **whichever of the two above is in force, plus ``stages``.** Not a
    layout of its own: ``1b`` drew a five-column table because a run used to
    lock the rows, and with the lock gone that same table would have taken
    ``layout`` / ``source`` / ``ground`` / ``views`` / ``out view`` /
    ``last run`` away for however long Calibre runs -- six of the eight
    columns the user can type into, at the moment they most want them. The
    idle width class keeps tracking the window during a run
    (:meth:`CellsScreen.set_idle_column_mode`), and where the composed set
    does not fit, the table scrolls sideways.

    The check column is a *check* column here too. It used to be redrawn as
    a status glyph, which stripped ``ItemIsUserCheckable`` and made the run
    set unbuildable for the whole run; the glyph belongs in ``status``,
    which both idle layouts already carry.

Running a batch
---------------
The screen does not own a thread. It builds the same
:class:`~auto_ext.ui.worker.RunWorker` +
:class:`~auto_ext.ui.qt_reporter.QtProgressReporter` +
:class:`~auto_ext.core.progress.CancelToken` trio the Run tab has always
used and turns the reporter's signals into row state. Log paths come from
the reporter's ``run_dir_ready`` event rather than being recomputed from a
task id: since S1 the logs live under ``runs/<run_id>/logs/`` and the run
id is not derivable from the row.

**Nothing is locked while a run is in flight.** Until 2026-09-05 the first
press of Run put the screen into a modal state: five disabled toolbar
buttons, ``NoEditTriggers``, a check column with no checkboxes in it, six
row commands that returned in silence, and a Run button that was not
disabled but *gone*. The user's report is the whole argument against it:
after starting one simulation they still want to change things and throw
the next one in, the way Cadence lets them -- because a Calibre/Quantus
job is hours long and the person who started it has more work to queue
behind it, not less. (Their words are quoted, in their own language, in
``tests/ui/test_second_run.py``.)

So Run means **enqueue a job**, always. :meth:`CellsScreen.start_run`
snapshots :meth:`~CellsScreen.run_request` and resolves its batches at
*press* time, then appends a ``_Job`` to :attr:`CellsScreen._queue`; later
edits to the table cannot move what an already-pressed job will run.
Exactly one :class:`~auto_ext.ui.worker.RunWorker` is ever in flight (see
that module for why: two Quantus batches racing double the licence draw),
and the next job is dispatched from a zero-timer once the previous one's
``finished`` slot has returned, never from inside it. The same row may sit
in two jobs at once; run directories already de-collide.

Assumptions
-----------
* **The per-row recipe binding is a field of the row.**
  :attr:`~auto_ext.model.cells.CellEntry.recipe` holds it, so it is saved to
  ``cells.yaml``, survives a rename or a duplicate without bookkeeping, and
  is what :meth:`CellsScreen._recipe_batches` groups the dispatch by.

  It was screen state until 2026-08-25 -- a column the user could fill that
  changed nothing, was never read by the dispatch, and was lost on exit --
  and this paragraph described that as a standing assumption. That is how a
  known defect becomes permanent: nobody re-reads a section headed
  "Assumptions" looking for open work. If something here stops being true
  again, it belongs in the backlog and in a failing test, not here.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from PyQt5.QtCore import (
    QEvent,
    QItemSelection,
    QItemSelectionModel,
    QPoint,
    QRect,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QBrush, QColor, QFont, QKeySequence, QPalette
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QAction,
    QComboBox,
    QFileDialog,
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
    QStyleOptionButton,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.progress import CancelToken
from auto_ext.core.errors import AutoExtError, EnvResolutionError
from auto_ext.core.runner import STAGE_ORDER, preflight
from auto_ext.model.cells import CellBook, CellEntry
from auto_ext.ui import theme
from auto_ext.ui.qt_reporter import QtProgressReporter
from auto_ext.ui.widgets.run_bar import RunBar, StageChipStrip, apply_families
from auto_ext.ui.worker import RunBatch, RunWorker

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
    "COL_OUT_VIEW",
    "COL_VIEWS",
    "CellsScreen",
    "CheckHeader",
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

#: How long :meth:`CellsScreen.cancel_run` waits for the runner to come back
#: before it offers Cancel again and says so. The kill sequence is SIGTERM ->
#: 10s grace -> SIGKILL, so a shorter deadline would re-arm the button while
#: a perfectly well-behaved stop is still on time.
CANCEL_DEADLINE_MS = 15_000

COL_CHECK = 0
COL_LIBRARY = 1
COL_CELL = 2
COL_LAYOUT = 3
COL_SOURCE = 4
COL_GROUND = 5
COL_VIEWS = 6
#: The extracted view this DUT produces. Per DUT, not per recipe -- it is
#: Quantus ``-view_name`` and Jivaro ``inputView``, and it used to be visible
#: only in the row tooltip, so the one name the whole back half of the flow
#: hangs off could not be typed anywhere in the GUI.
COL_OUT_VIEW = 7
COL_RECIPE = 8
COL_LAST_RUN = 9
COL_STATUS = 10
COL_STAGES = 11

COLUMN_TITLES = (
    "",
    "library",
    "cell",
    "layout",
    "source",
    "ground",
    "views",
    "out view",
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
    COL_OUT_VIEW: "out_file",
}

#: ``recipe`` is a field of :class:`~auto_ext.model.cells.CellEntry` like the
#: six above, but deliberately NOT in :data:`EDITABLE_FIELDS`: that table
#: drives the type-into-the-cell path, and this column shows a recipe's
#: display *name* while the field holds its *id*. Committing the visible text
#: would write "RC default" where ``rc-default`` belongs. It is edited only
#: through :class:`RecipeDelegate`, which hands
#: :meth:`CellsScreen.set_recipe_binding` the id behind the chosen item.
RECIPE_FIELD = "recipe"

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
    COL_OUT_VIEW: 78,
    COL_RECIPE: 138,
    COL_LAST_RUN: 122,
    COL_STATUS: 84,
}
_COMPACT_WIDTHS = {
    COL_CHECK: 24,
    COL_LIBRARY: 112,
    COL_VIEWS: 96,
    COL_OUT_VIEW: 74,
    COL_RECIPE: 130,
    COL_LAST_RUN: 118,
    COL_STATUS: 76,
}
#: What ``running`` adds on top of whichever idle layout is in force. It is
#: an *addition*, not a replacement: a run can now last hours with more
#: presses queued behind it, so a layout that hid six of the eight editable
#: columns for the duration would put the table out of reach for exactly as
#: long as the user most wants it.
_RUNNING_EXTRA_WIDTHS = {COL_STAGES: 352}
_RUNNING_EXTRA_COLUMNS = (COL_STAGES,)

_MODE_WIDTHS = {MODE_WIDE: _WIDE_WIDTHS, MODE_COMPACT: _COMPACT_WIDTHS}

_MODE_COLUMNS = {
    MODE_WIDE: (
        COL_CHECK,
        COL_LIBRARY,
        COL_CELL,
        COL_LAYOUT,
        COL_SOURCE,
        COL_GROUND,
        COL_OUT_VIEW,
        COL_RECIPE,
        COL_LAST_RUN,
        COL_STATUS,
    ),
    MODE_COMPACT: (
        COL_CHECK,
        COL_LIBRARY,
        COL_CELL,
        COL_VIEWS,
        COL_OUT_VIEW,
        COL_RECIPE,
        COL_LAST_RUN,
        COL_STATUS,
    ),
}

#: Every mode :meth:`CellsScreen.set_column_mode` accepts. ``running`` has no
#: entry in :data:`_MODE_COLUMNS` because it is not a layout of its own --
#: see :func:`_columns_for`.
MODES = (MODE_WIDE, MODE_COMPACT, MODE_RUNNING)


def _columns_for(mode: str, idle_mode: str = MODE_WIDE) -> tuple[int, ...]:
    """Which columns ``mode`` shows, in column-index order.

    ``running`` is the *current idle layout* plus the stage chips, not a
    layout of its own. It used to be its own five-column set, which meant a
    run took ``layout`` / ``source`` / ``ground`` / ``views`` / ``out view``
    / ``last run`` off the table until it ended -- six of the eight columns
    the user can type into, for however long Calibre takes. That was
    defensible while a run locked the table anyway; with the table live and
    a queue behind it, it is the same defect the running state used to be.

    Where the composed set does not fit, the table scrolls sideways. That is
    the degradation this screen's sizing note already names, and it is the
    right one: a column the user cannot scroll to is worse than a column
    they have to.
    """

    if mode != MODE_RUNNING:
        return _MODE_COLUMNS[mode]
    base = _MODE_COLUMNS.get(idle_mode, _MODE_COLUMNS[MODE_WIDE])
    return tuple(sorted(set(base) | set(_RUNNING_EXTRA_COLUMNS)))


def _widths_for(mode: str, idle_mode: str = MODE_WIDE) -> dict[int, int]:
    """Artboard widths for ``mode``; ``running`` adds the chip column."""

    if mode != MODE_RUNNING:
        return _MODE_WIDTHS[mode]
    return {
        **_MODE_WIDTHS.get(idle_mode, _WIDE_WIDTHS),
        **_RUNNING_EXTRA_WIDTHS,
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

#: Both tables must carry a key for every entry of ``self._buttons``: the
#: fold loop in ``resizeEvent`` walks the buttons and indexes the table, so a
#: missing key is a KeyError on resize rather than a long label. "Save" is
#: four characters and has nothing to fold to, so it is the same in both.
_LONG_LABELS = {
    "add": "Add cell",
    "duplicate": "Duplicate",
    "remove": "Remove",
    "import": "Import from tasks.yaml",
    "save": "Save",
}
_SHORT_LABELS = {
    "add": "Add",
    "duplicate": "Dup",
    "remove": "Rm",
    "import": "Import",
    "save": "Save",
}


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
    #: Set only by "Export GDS": a standalone GDS for software outside this
    #: flow. When set, ``stages`` is always exactly ``("strmout",)`` -- the
    #: LVS layout file is never relocated, a second one is written instead.
    layout_export_path: str | None = None


@dataclass
class _LiveRun:
    """Bookkeeping for one job -- what its rows have reached so far.

    One per :class:`_Job`, not one per screen: the table stays editable
    while a job runs, so a row that is added mid-run rebuilds every stage
    strip in the table and the strips have to be repainted from this
    record rather than from whatever Qt happened to be showing.
    """

    keys: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    started: set[str] = field(default_factory=set)
    finished: dict[str, str] = field(default_factory=dict)
    run_dirs: dict[str, Path] = field(default_factory=dict)
    #: task id -> stage -> status, so a table reload can redraw the chips.
    stage_status: dict[str, dict[str, str]] = field(default_factory=dict)


class _Resolved(NamedTuple):
    """Everything a press validated, frozen at the moment it was pressed.

    The batches are the obvious half. The other five are the *environment*
    the run happens in, and they are snapshotted for the same reason: the
    Recipes screen's Reload button and ``File -> Open`` are both one click
    away and neither is blocked while a run is going (the screen stays free
    on purpose). A queued job that re-read the controller when its turn came
    would run against whatever workarea the reload left behind, or against
    ``project=None`` and die with a bare "Run failed".
    """

    batches: list[RunBatch]
    project: Any
    auto_ext_root: Any
    workarea: Any
    profile: Any
    resources: Any


@dataclass
class _Job:
    """One press of Run, resolved at press time and waiting its turn.

    ``resolved`` is built when the button is pressed, not when the job
    reaches the front of the queue: the user pressed Run over a table and a
    config they could see, and an edit -- or a reload -- made while the job
    waits must not silently change what that press asked for.
    """

    request: RunRequest
    resolved: _Resolved
    live: _LiveRun
    #: What the rows said before this job marked them "queued", so dropping
    #: the job puts the table back rather than blanking rows that had a
    #: result on them.
    previous: dict[str, RowStatus] = field(default_factory=dict)


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


class CheckHeader(QHeaderView):
    """The header of the check column, with a check-all box in it.

    The check column is the run set (see the module docstring), so it needs
    the one control every check column has: a box in the header that checks
    everything, clears everything, and shows ``partial`` in between. Without
    it "run all 40 rows" is forty clicks, and there is nothing on screen that
    says the column is a set the user builds rather than a redraw of the
    selection highlight.

    Qt has no such thing, so it is painted here: ``paintSection`` draws the
    ordinary section first (the stylesheet still owns the background) and
    then a check indicator centred in it, and a press inside that section is
    consumed rather than being passed on as a sort/resize gesture.
    """

    check_all_requested = pyqtSignal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self._check_state = Qt.Unchecked
        self._check_shown = True

    def check_state(self) -> int:
        return self._check_state

    def set_check_state(self, state: int) -> None:
        if state == self._check_state:
            return
        self._check_state = state
        self.updateSection(COL_CHECK)

    def set_check_shown(self, shown: bool) -> None:
        """Hide the box while a run is in flight -- the column is glyphs then."""

        if bool(shown) == self._check_shown:
            return
        self._check_shown = bool(shown)
        self.updateSection(COL_CHECK)

    def is_check_shown(self) -> bool:
        return self._check_shown

    def _indicator_rect(self, rect: QRect) -> QRect:
        style = self.style()
        option = QStyleOptionButton()
        option.initFrom(self)
        side = min(
            style.pixelMetric(QStyle.PM_IndicatorWidth, option, self),
            rect.width() - 2,
            rect.height() - 2,
        )
        side = max(side, 1)
        return QRect(
            rect.x() + (rect.width() - side) // 2,
            rect.y() + (rect.height() - side) // 2,
            side,
            side,
        )

    def paintSection(self, painter, rect, logical_index) -> None:  # noqa: N802
        super().paintSection(painter, rect, logical_index)
        if logical_index != COL_CHECK or not self._check_shown:
            return
        option = QStyleOptionButton()
        option.initFrom(self)
        option.rect = self._indicator_rect(rect)
        option.state = QStyle.State_Enabled
        if self._check_state == Qt.Checked:
            option.state |= QStyle.State_On
        elif self._check_state == Qt.PartiallyChecked:
            option.state |= QStyle.State_NoChange
        else:
            option.state |= QStyle.State_Off
        # The clip, or nothing appears. Once any stylesheet is in force --
        # this screen sets one -- the painter reaches ``paintSection`` with
        # an *empty* clip region: QStyleSheetStyle installs its own clip
        # around the section it draws and hands the painter on without one,
        # so ``super()`` paints and everything after it is clipped away. It
        # fails silently and only on the styled screen, which is why the
        # test below asserts pixels rather than state.
        painter.save()
        painter.setClipRect(rect)
        self.style().drawPrimitive(
            QStyle.PE_IndicatorCheckBox, option, painter, self
        )
        painter.restore()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if (
            self._check_shown
            and event.button() == Qt.LeftButton
            and self.logicalIndexAt(event.pos()) == COL_CHECK
        ):
            self.check_all_requested.emit(self._check_state != Qt.Checked)
            event.accept()
            return
        super().mousePressEvent(event)


_EMPTY_TITLE = "No cells yet."
_EMPTY_BODY = (
    "A cell is one thing you extract: a library, a cell name, its layout "
    "and schematic views, and the ground net. Add one and pick a recipe — "
    "that pair is the whole run."
)
_LOAD_FAILED_TITLE = "The project could not be loaded."


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

        title = QLabel(_EMPTY_TITLE, panel)
        title.setObjectName(OBJ_EMPTY_TITLE)
        body = QLabel(_EMPTY_BODY, panel)
        body.setObjectName(OBJ_EMPTY_BODY)
        body.setWordWrap(True)
        self._title = title
        self._body = body

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
        self._note = note

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

    def set_load_error(self, text: str | None) -> None:
        """Say the project failed to load, and why -- or go back to the guide.

        A rejected ``workspace.yaml`` left the table empty under "No cells
        yet." and an invitation to add one, while the reason sat in the first
        line of the status bar. The empty table is a consequence of the error,
        so the error is what this panel says, in full and selectable.
        """

        failed = bool(text)
        self._title.setText(_LOAD_FAILED_TITLE if failed else _EMPTY_TITLE)
        self._body.setText(text if failed else _EMPTY_BODY)
        self._body.setTextInteractionFlags(
            Qt.TextSelectableByMouse if failed else Qt.NoTextInteraction
        )
        for widget in (self._add, self._import, self._note):
            widget.setVisible(not failed)

    def title_text(self) -> str:
        return self._title.text()

    def body_text(self) -> str:
        return self._body.text()

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
    ``save_requested()``
        Save was pressed. The screen still writes nothing itself; the host
        does, and calls :meth:`set_unsaved` back to say what happened.
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
    checked_changed = pyqtSignal(object)
    run_requested = pyqtSignal(object)
    run_finished = pyqtSignal(object)
    import_requested = pyqtSignal()
    save_requested = pyqtSignal()
    edit_rejected = pyqtSignal(str)
    status_message = pyqtSignal(str)
    log_path_changed = pyqtSignal(object)
    open_log_requested = pyqtSignal(object)
    #: ``Path`` -- the run directory of a row's latest result, from the row
    #: menu's "Show last result". The host opens it on the Runs screen.
    result_requested = pyqtSignal(object)

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
        self._statuses: dict[str, RowStatus] = {}
        #: row key -> run directory of its newest result, live or from history.
        self._result_dirs: dict[str, Path] = {}
        self._recipe_choices: list[tuple[str, str]] = []
        self._row_keys: list[str] = []
        #: The run set. Independent of the selection highlight -- see the
        #: module docstring. Only the check column, the header box, Space
        #: and the context menu write to it.
        self._checked: set[str] = set()
        self._mode = MODE_WIDE
        self._idle_mode = MODE_WIDE
        self._compact_toolbar = False
        self._syncing = False
        self._worker: RunWorker | None = None
        #: The message of the job in flight's worker error, until it retires.
        self._worker_error: str | None = None
        #: How the last job ended, as the status line said it.
        self._outcome: str | None = None
        self._reporter: QtProgressReporter | None = None
        #: The job in flight, and the presses waiting behind it. Exactly one
        #: worker at a time; see the module docstring.
        self._current: _Job | None = None
        self._queue: list[_Job] = []
        #: A worker whose ``finished`` has arrived but whose C++ object may
        #: still be winding down. Held here so that clearing ``_worker``
        #: inside the worker's own slot is never the drop of its last
        #: reference; cleared from the zero-timer that dispatches the next
        #: job.
        self._retiring: list[RunWorker] = []
        #: The worker the last Cancel was aimed at, and a serial that says
        #: *which* Cancel. The worker alone is not enough: a deadline armed
        #: for job 1 outlives job 1, and would otherwise report job 2's
        #: perfectly fresh Cancel as stalled.
        self._cancelling: RunWorker | None = None
        self._cancel_serial = 0
        self._empty_live = _LiveRun()

        self._build_ui()
        self.setStyleSheet(_CELLS_QSS)
        self._reload_table()

    @property
    def _live(self) -> _LiveRun:
        """Bookkeeping for the job in flight, or an empty one when idle."""

        return self._current.live if self._current is not None else self._empty_live

    def _waiting_suffix(self) -> str:
        """`` · N runs waiting``, or nothing when the queue is empty.

        "waiting" and not "queued" on purpose: the run panel's counts line
        already says "N queued" about *tasks inside the current job*, and two
        different numbers under one word is how a status bar stops being
        read at all.
        """

        count = len(self._queue)
        if not count:
            return ""
        return f" · {count} run{'' if count == 1 else 's'} waiting"

    def _say(self, message: str) -> None:
        """Emit one status line, with the waiting-run count kept on the end.

        Every line this screen emits goes through here. The host writes
        "running · N runs waiting" when a run starts and is then overwritten
        by the next stage event, the next edit, the next filter -- so for the
        hours the user actually watches, the queue depth would be visible for
        one frame and then never again unless it travels with every line.
        """

        self.status_message.emit(f"{message}{self._waiting_suffix()}")

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
        self.run_bar.drop_queued_requested.connect(self.drop_queued_jobs)
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

        # Save, on the screen the edit happens on. The cell table has no
        # explicit commit moment -- you type into a cell and move on -- so
        # before this the ONLY way to write cells.yaml was File -> Save, and
        # the only sign anything was pending was a star in the title bar. The
        # Recipes screen has meant "write it" since the office round found the
        # same gap there; two screens that persist differently is worse than
        # either rule on its own.
        save = QPushButton("Save", bar)
        save.setProperty("primary", "true")
        save.setEnabled(False)
        save.clicked.connect(self.save_requested)
        layout.addWidget(save)
        self._buttons["save"] = save

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
        # Before the labels: setHorizontalHeader keeps the model's header
        # data, but the section sizes below are set on whichever header the
        # table ends up owning.
        table.setHorizontalHeader(CheckHeader(table))
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

        header.check_all_requested.connect(self._on_check_all_requested)

        table.itemChanged.connect(self._on_item_changed)
        table.itemSelectionChanged.connect(self._on_selection_changed)
        table.customContextMenuRequested.connect(self._on_context_menu)

        # Space checks (or unchecks) every highlighted row: the bridge
        # between "the rows I am pointing at" and "the rows I will run",
        # and the only reason a 40-row batch is not 40 clicks on 26px
        # boxes. An event filter rather than a QShortcut, because a
        # shortcut is dispatched through the application's shortcut map
        # and needs the window to be active -- while a cell editor, being
        # a child widget, keeps its own spaces either way.
        table.installEventFilter(self)
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
        """Replace the whole table. Statuses for rows that survived the swap
        are kept; the rest are dropped. The recipe binding needs no such
        bookkeeping -- it is a field of the row, so it travels with it."""

        self._book = book
        keys = set(book.keys)
        self._statuses = {k: v for k, v in self._statuses.items() if k in keys}
        self._result_dirs = {k: v for k, v in self._result_dirs.items() if k in keys}
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
        keys = set(self._book.keys)
        self._checked &= keys
        # Prune here and not only in ``set_cells``: every in-place edit goes
        # through ``_apply_book``, which used to leave the status of a row
        # the user had just removed or renamed in the dict. A row that later
        # took the same key inherited a result it never produced.
        self._statuses = {k: v for k, v in self._statuses.items() if k in keys}
        self._result_dirs = {k: v for k, v in self._result_dirs.items() if k in keys}
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
        self._refresh_header_check()
        self._refresh_empty_state()
        self._refresh_run_bar()
        # Every row was rebuilt, stage strips included; a job in flight has
        # to get its chips back or a mid-run Add blanks the run on screen.
        self._repaint_live_strips()

    def _render_row(self, row: int, entry: CellEntry) -> None:
        key = entry.key
        # A real check box in every mode, running included: the run set is
        # what the user builds for the *next* press, and a job in flight is
        # no reason to take the tool away.
        check = QTableWidgetItem()
        check.setData(Qt.UserRole, key)
        check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
        check.setCheckState(Qt.Checked if key in self._checked else Qt.Unchecked)
        self._table.setItem(row, COL_CHECK, check)

        values = {
            COL_LIBRARY: entry.library,
            COL_CELL: entry.cell,
            COL_LAYOUT: entry.layout_view,
            COL_SOURCE: entry.source_view,
            COL_GROUND: entry.ground_net,
            COL_VIEWS: f"{entry.layout_view}/{entry.source_view}",
            COL_OUT_VIEW: entry.out_file or "",
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
        entry = self._book.entry(key) if key in set(self._book.keys) else None
        recipe_id = entry.recipe if entry is not None else None
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

    def set_unsaved(self, unsaved: bool) -> None:
        """Enable Save iff there is something to write.

        The screen cannot know this on its own: it stages through
        ``cells_changed`` and the host owns both the queue and the file, so
        the host is the only thing that can tell a pending edit from a saved
        one.
        """

        self._buttons["save"].setEnabled(bool(unsaved))

    def save_button(self) -> QPushButton:
        return self._buttons["save"]

    def set_empty_state_hint(self, text: str) -> None:
        """One extra line under the empty state (a Setup verdict, say)."""

        self._empty.set_hint(text)

    def set_load_error(self, text: str | None) -> None:
        """Show why the project failed to load where the table would be."""

        self._empty.set_load_error(text)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if watched is self._table.viewport() and event.type() == QEvent.Resize:
            if self._empty.isVisible():
                self._empty.setGeometry(self._table.viewport().rect())
        if (
            watched is self._table
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Space, Qt.Key_Select)
            and not event.modifiers()
            and self._table.state() != QAbstractItemView.EditingState
        ):
            self.toggle_checks_on_selection()
            return True
        return super().eventFilter(watched, event)

    def focus_column_for(self, option_key: str) -> bool:
        """Put the cursor on the column a Recipes pointer row points at.

        The catalog keys of the per-cell settings (``out_file``,
        ``ground_net``) are spelled exactly like the ``CellEntry`` fields, so
        this is :func:`_column_of_field` and nothing else -- no second table
        mapping catalog rows to columns, which would be a table that can
        disagree with the first one.

        Widens the table if the mode in force hides that column: arriving on
        a page where the thing you were sent to find is not drawn is worse
        than arriving on a wider table than you left. Returns whether it
        found a column.
        """

        try:
            column = _column_of_field(option_key)
        except KeyError:
            return False
        if column not in self._visible_columns():
            self.set_column_mode(MODE_WIDE)
        row = 0 if self._table.rowCount() else -1
        if row >= 0:
            self._table.setCurrentCell(row, column)
            item = self._table.item(row, column)
            if item is not None:
                self._table.scrollToItem(item)
        self._table.setFocus(Qt.OtherFocusReason)
        return True

    # ---- column modes ----------------------------------------------------

    def column_mode(self) -> str:
        return self._mode

    def set_column_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"unknown column mode {mode!r}")
        if mode == self._mode:
            return
        self._mode = mode
        if mode != MODE_RUNNING:
            self._idle_mode = mode
        self._apply_mode(mode)

    def set_idle_column_mode(self, mode: str) -> None:
        """Choose the width class ``running`` composes itself from.

        ``running`` is the idle layout plus the chip column, so the width
        class has to keep tracking the window while a job is in flight --
        otherwise a run that starts wide stays wide through every resize
        until it ends, which with a queue behind it can be the whole
        session.
        """

        if mode not in (MODE_WIDE, MODE_COMPACT):
            raise ValueError(f"{mode!r} is not an idle column mode")
        if mode == self._idle_mode:
            return
        self._idle_mode = mode
        if self._mode == MODE_RUNNING:
            self._apply_mode(MODE_RUNNING)

    def _visible_columns(self) -> tuple[int, ...]:
        return _columns_for(self._mode, self._idle_mode)

    def _apply_mode(self, mode: str) -> None:
        shown = _columns_for(mode, self._idle_mode)
        widths = _widths_for(mode, self._idle_mode)
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
        """Redraw the boxes from :attr:`_checked`. Mode-independent.

        It used to swap the boxes for status glyphs in ``MODE_RUNNING``,
        stripping ``ItemIsUserCheckable`` for the length of the run. That is
        half of what made the second Run unreachable: the one column the run
        set is built in went dead exactly when the user wanted to build the
        next batch.
        """

        self._syncing = True
        try:
            for row, key in enumerate(self._row_keys):
                item = self._table.item(row, COL_CHECK)
                if item is None:
                    continue
                item.setText("")
                item.setFlags(
                    Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable
                )
                item.setCheckState(Qt.Checked if key in self._checked else Qt.Unchecked)
        finally:
            self._syncing = False
        self._refresh_header_check()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        width = event.size().width()
        compact_toolbar = width < TOOLBAR_COMPACT_BELOW
        if compact_toolbar != self._compact_toolbar:
            self._compact_toolbar = compact_toolbar
            labels = _SHORT_LABELS if compact_toolbar else _LONG_LABELS
            for name, button in self._buttons.items():
                button.setText(labels[name])
        if not self.auto_compact:
            return
        wanted = MODE_COMPACT if width < TABLE_COMPACT_BELOW else MODE_WIDE
        if self._mode == MODE_RUNNING:
            # A run no longer freezes the width class: it composes its
            # layout from the idle one, so the idle one has to keep moving.
            self.set_idle_column_mode(wanted)
        else:
            self.set_column_mode(wanted)

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
        """The highlight moved. It says nothing about what will run.

        Repainting the check column from here is exactly the defect this
        screen shipped with until 2026-09-04: a plain left click is
        ``ClearAndSelect``, so opening a row -- or double-clicking one to
        retype its cell name -- threw away a batch the user had spent
        forty clicks building. The two things are separate now; the only
        thing the highlight still drives is which rows Duplicate, Remove,
        Enable/Disable and Export act on.
        """

        if self._syncing:
            return
        self._refresh_run_bar()
        self.selection_changed.emit(self.selected_keys())

    def _refresh_run_bar(self) -> None:
        run_keys = self.run_keys()
        self.run_bar.set_run_count(len(run_keys))
        bindings = self.recipe_bindings()
        distinct = {bindings.get(key) for key in run_keys}
        distinct.discard(None)
        self.run_bar.set_per_row_summary(len(distinct))
        keys = self.selected_keys()
        has_rows = bool(self._row_keys)
        self._buttons["duplicate"].setEnabled(bool(keys))
        self._buttons["remove"].setEnabled(bool(keys))
        # Not conditioned on a run any more, and Save is not touched here at
        # all: its enabled state is ``set_unsaved``'s and nothing else's.
        self._buttons["add"].setEnabled(True)
        self._buttons["import"].setEnabled(True)
        if not has_rows:
            self._buttons["duplicate"].setEnabled(False)
            self._buttons["remove"].setEnabled(False)

    # ---- the run set -----------------------------------------------------

    def checked_keys(self) -> tuple[str, ...]:
        """Every ticked row, in table order.

        Table order, not click order, because ``_recipe_batches`` groups the
        dispatch by first appearance and a batch that reordered itself
        depending on which box was clicked first would make two identical
        run sets produce two different log orders.
        """

        return tuple(key for key in self._row_keys if key in self._checked)

    def run_keys(self) -> tuple[str, ...]:
        """What pressing Run would actually run: ticked *and* not parked.

        The run bar counts these, so ``Run 3 cells`` is three cells. It used
        to count the highlight, which meant the number on the button
        included rows that ``run_request`` then dropped for being disabled.
        """

        return tuple(key for key in self.checked_keys() if self._book.entry(key).enabled)

    def set_checked_keys(self, keys: Iterable[str]) -> None:
        """Replace the run set with exactly ``keys`` (the ones that exist)."""

        wanted = {key for key in keys if key in set(self._row_keys)}
        if wanted == self._checked:
            return
        self._checked = wanted
        self._repaint_check_column()
        self._refresh_run_bar()
        self.checked_changed.emit(self.checked_keys())

    def set_checked_for(self, keys: Iterable[str], checked: bool) -> None:
        """Tick or clear ``keys``, leaving every other row alone."""

        wanted = {key for key in keys if key in set(self._row_keys)}
        if not wanted:
            return
        updated = self._checked | wanted if checked else self._checked - wanted
        self.set_checked_keys(updated)

    def toggle_checks_on_selection(self) -> None:
        """Space: tick every highlighted row, or clear them if all are ticked."""

        keys = self.selected_keys()
        if not keys:
            return
        self.set_checked_for(keys, not all(key in self._checked for key in keys))

    def clear_checks(self) -> None:
        self.set_checked_keys(())

    def check_all(self) -> None:
        """Tick every row the filter is showing.

        Visible rows only: with a filter typed, "check all" that also ticked
        the rows it is hiding would build a batch the user cannot see.
        """

        self.set_checked_for(self.visible_keys(), True)

    def _header_check_state(self) -> int:
        rows = self.visible_keys()
        if not rows:
            return Qt.Unchecked
        ticked = sum(1 for key in rows if key in self._checked)
        if ticked == 0:
            return Qt.Unchecked
        if ticked == len(rows):
            return Qt.Checked
        return Qt.PartiallyChecked

    def _refresh_header_check(self) -> None:
        header = self._table.horizontalHeader()
        if not isinstance(header, CheckHeader):
            return
        # Shown in every mode: check-all is how a batch of forty is built,
        # and a run in flight is not a reason to hide it.
        header.set_check_shown(True)
        header.set_check_state(self._header_check_state())

    def _on_check_all_requested(self, check: bool) -> None:
        if check:
            self.check_all()
        else:
            self.set_checked_for(self.visible_keys(), False)

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
        """One box clicked. It moves the run set and nothing else.

        In particular it does not touch the selection: clicking a box used
        to select the row too, so a box was both halves of a coupling that
        no longer exists.
        """

        key = self.key_at_row(item.row())
        if key is None:
            return
        checked = item.checkState() == Qt.Checked
        if (key in self._checked) == checked:
            return
        if checked:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        self._refresh_header_check()
        self._refresh_run_bar()
        self.checked_changed.emit(self.checked_keys())

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
        # ``or ""`` on both sides: out_file is the one editable field that is
        # nullable, and CellEntry normalises "" back to None, so a cleared box
        # must compare equal to None rather than stage an identical book.
        if (getattr(entry, field_name) or "") == value:
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
                    item.setText(
                        self._recipe_name(key)
                        if field_name == RECIPE_FIELD
                        else (getattr(entry, field_name) or "")
                    )
            finally:
                self._syncing = False
            self.edit_rejected.emit(_first_line(exc))
            self._say(f"edit refused: {_first_line(exc)}")
            return
        if replacement.key != key:
            if key in self._statuses:
                self._statuses[replacement.key] = self._statuses.pop(key)
            if key in self._result_dirs:
                self._result_dirs[replacement.key] = self._result_dirs.pop(key)
            # The tick belongs to the row, not to the spelling of its key:
            # retyping a cell name must not quietly drop it out of the batch.
            if key in self._checked:
                self._checked.discard(key)
                self._checked.add(replacement.key)
        self._apply_book(book)

    # ---- row commands ----------------------------------------------------

    def add_cell(self, entry: CellEntry | None = None) -> str | None:
        """Append a row, tick it, and start editing its cell name.

        Returns its key. **The new row joins the run set**, which reverses
        the rule this screen shipped with. A row is added in order to run
        it -- ticking the new row and pressing Run was the user doing by
        hand, every time, the one thing Add could have done for them --
        and the highlight it also takes is only about which row Duplicate
        and Remove would act on. Adding without ticking left the two
        disagreeing: the new row was the only one highlighted and none of
        it was in the batch.
        """

        candidate = entry if entry is not None else self._blank_entry()
        try:
            book = self._book.with_added([candidate])
        except Exception as exc:  # noqa: BLE001 - pydantic raises many shapes
            self.edit_rejected.emit(_first_line(exc))
            return None
        self._apply_book(book)
        self.set_checked_for([candidate.key], True)
        row = self.row_of_key(candidate.key)
        if row is not None:
            self.set_selected_keys([candidate.key])
            self._table.setCurrentCell(row, COL_CELL)
            self._table.editItem(self._table.item(row, COL_CELL))
        return candidate.key

    def _blank_entry(self) -> CellEntry:
        """A fresh row that is already runnable.

        It inherits a recipe -- the highlighted row's, else the last row's,
        else none -- because a blank ``recipe=None`` is indistinguishable at
        the dispatch from a row bound to a recipe the user deleted, and the
        refusal that is right for the second ("nothing to guess from") threw
        away the whole batch for the first. The refusal itself stays: a row
        that genuinely cannot be resolved still stops the run and is named.
        """

        recipe = self._inherited_recipe()
        existing = set(self._book.keys)
        index = len(self._book) + 1
        while True:
            candidate = CellEntry(
                library="library",
                cell=f"cell_{index}",
                layout_view="layout",
                source_view="schematic",
                recipe=recipe,
            )
            if candidate.key not in existing:
                return candidate
            index += 1

    def _inherited_recipe(self) -> str | None:
        """The recipe a brand-new row should start life bound to."""

        for key in self.selected_keys():
            entry = self._book.entry(key)
            if entry.recipe:
                return entry.recipe
        for entry in reversed(list(self._book)):
            if entry.recipe:
                return entry.recipe
        return None

    def duplicate_selected(self) -> tuple[str, ...]:
        """Copy every selected row, tick the copies, rename until keys are free.

        Ticked for the same reason :meth:`add_cell`'s row is: a duplicate is
        made to be run, usually right next to the row it came from.
        """

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
        self._apply_book(self._book.with_added(copies))
        new_keys = tuple(c.key for c in copies)
        self.set_checked_for(new_keys, True)
        self.set_selected_keys(new_keys)
        return new_keys

    def remove_selected(self) -> tuple[str, ...]:
        """Drop every selected row."""

        keys = set(self.selected_keys())
        if not keys:
            return ()
        remaining = [entry for entry in self._book if entry.key not in keys]
        for key in keys:
            self._statuses.pop(key, None)
            self._result_dirs.pop(key, None)
            self._checked.discard(key)
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
        """Row key -> recipe id, for the rows that name one.

        Derived from the table, not held beside it. It used to be a dict on
        this screen: unsaved, unread by the dispatch, and needing its own
        bookkeeping every time a row was renamed, duplicated or removed.
        :attr:`~auto_ext.model.cells.CellEntry.recipe` made all of that go
        away -- a field of the row travels with the row.
        """

        return {entry.key: entry.recipe for entry in self._book if entry.recipe}

    def set_recipe_binding(self, key: str, recipe_id: str | None) -> None:
        """Bind one row to a recipe. An ordinary cell edit, staged and saved.

        Goes through :meth:`_commit_edit` like every other editable column,
        so it marks the table unsaved and reaches ``cells.yaml`` on Save.
        ``None`` clears the binding: ``CellEntry`` normalises ``""`` back to
        ``None``, which is what ``exclude_none`` then keeps out of the file.
        """

        row = self.row_of_key(key)
        if row is None:
            return
        self._commit_edit(row, "recipe", recipe_id or "")

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

    def set_history(self, entries: Sequence[Any]) -> None:
        """Fill ``last run`` / ``status`` from the run history, newest first.

        The columns used to know only what this window had dispatched since it
        opened, so a cell run yesterday -- or from the CLI a minute ago --
        read ``never run``. ``entries`` are
        :class:`~auto_ext.core.run_store.RunIndexEntry` rows; the first one per
        row key wins. A row the job in flight is still working on keeps its
        live status: the history cannot know more than the reporter does.
        """

        newest: dict[str, Any] = {}
        for entry in entries:
            newest.setdefault(entry.dut_key, entry)
        for key in self._book.keys:
            entry = newest.get(key)
            if entry is None or self._is_live(key):
                continue
            status = str(entry.overall)
            text = status
            if entry.dry_run and status == "passed":
                status, text = "dry_run", "dry run"
            code = "LVS" if status == "failed" and entry.lvs_passed is False else None
            self._result_dirs[key] = Path(entry.run_dir)
            self.set_row_status(key, status, text=text, when=_when(entry.created_at), code=code)

    def _is_live(self, key: str) -> bool:
        """In the job in flight and not finished yet."""

        return (
            self._worker is not None
            and key in self._live.keys
            and key not in self._live.finished
        )

    def result_dir(self, key: str) -> Path | None:
        """The run directory of ``key``'s newest known result, or ``None``."""

        return self._result_dirs.get(key)

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
        """Hide the rows that do not match. A view, never a selector.

        The filter does not touch the run set: a ticked row that scrolls out
        of the filter still runs. That is the honest half of a defect this
        used to have both halves of -- ``selectedIndexes()`` skips hidden
        rows, so filtering silently shrank a run the button was still
        offering to make ("Run 3 cells" dispatching one). Now the button
        counts the ticks, which the filter cannot move, and the hidden ones
        are said out loud instead of being dropped.
        """

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
        self._refresh_header_check()
        hidden = self.hidden_checked_count()
        if hidden:
            noun = "cell" if hidden == 1 else "cells"
            self._say(
                f"filter hides {hidden} checked {noun} — they are still in the run"
            )

    def hidden_checked_count(self) -> int:
        """Ticked rows the filter is currently hiding. They still run."""

        return sum(
            1
            for row, key in enumerate(self._row_keys)
            if key in self._checked and self._table.isRowHidden(row)
        )

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
        # Nothing here is conditioned on a run being in flight: the table is
        # live for the whole run, so the menu is too.
        menu = QMenu(self._table)

        act_add = QAction(_LONG_LABELS["add"], menu)
        act_add.triggered.connect(self.add_cell)
        menu.addAction(act_add)

        act_duplicate = QAction("Duplicate", menu)
        act_duplicate.triggered.connect(self.duplicate_selected)
        act_duplicate.setEnabled(bool(keys))
        menu.addAction(act_duplicate)

        act_remove = QAction("Remove", menu)
        act_remove.triggered.connect(self.remove_selected)
        act_remove.setEnabled(bool(keys))
        menu.addAction(act_remove)

        menu.addSeparator()
        all_enabled = all(self._book.entry(k).enabled for k in keys) if keys else False
        toggle_text = "Disable rows" if all_enabled else "Enable rows"
        act_toggle = QAction(toggle_text, menu)
        act_toggle.setEnabled(bool(keys))
        act_toggle.triggered.connect(
            lambda _checked=False, keys=keys, enable=not all_enabled: self.set_enabled_for(
                keys, enable
            )
        )
        menu.addAction(act_toggle)

        menu.addSeparator()
        all_checked = all(key in self._checked for key in keys) if keys else False
        act_check = QAction("Uncheck rows" if all_checked else "Check rows", menu)
        act_check.setEnabled(bool(keys))
        act_check.setShortcut(QKeySequence(Qt.Key_Space))
        act_check.triggered.connect(
            lambda _checked=False, keys=keys, on=not all_checked: self.set_checked_for(
                keys, on
            )
        )
        menu.addAction(act_check)

        act_clear = QAction("Clear all checks", menu)
        act_clear.setEnabled(bool(self._checked))
        act_clear.triggered.connect(lambda _checked=False: self.clear_checks())
        menu.addAction(act_clear)

        menu.addSeparator()
        result = self._result_dirs.get(keys[0]) if len(keys) == 1 else None
        act_result = QAction("Show last result", menu)
        act_result.setEnabled(result is not None)
        if result is None:
            act_result.setToolTip(
                "Select one row that has run" if len(keys) != 1 else "This row has not run yet"
            )
        act_result.triggered.connect(
            lambda _checked=False, path=result: self.result_requested.emit(path)
        )
        menu.addAction(act_result)

        menu.addSeparator()
        act_export = QAction("Export GDS…", menu)
        act_export.setEnabled(bool(keys))
        act_export.setToolTip(
            "Write a standalone GDS for software outside this flow. "
            "The layout file Calibre reads is not moved."
        )
        act_export.triggered.connect(
            lambda _checked=False, keys=keys: self.export_gds(keys)
        )
        menu.addAction(act_export)

        menu.addSeparator()
        act_copy = QAction("Copy row key", menu)
        act_copy.setEnabled(bool(keys))
        act_copy.triggered.connect(lambda _checked=False, keys=keys: _copy_keys(keys))
        menu.addAction(act_copy)

        global_pos = self._table.viewport().mapToGlobal(pos)
        QTimer.singleShot(0, lambda: menu.exec_(global_pos))

    # ---- running ---------------------------------------------------------

    def is_running(self) -> bool:
        """A job is in flight *or* waiting behind one.

        The window's close guard reads this, and a press the user made and
        has not seen run is as much "still going" as the worker itself.
        """

        return self._worker is not None or bool(self._queue)

    def last_outcome(self) -> str | None:
        """How the last job ended (``"idle — 2/3 passed"``, ``"run failed: ..."``)."""

        return self._outcome

    def queued_jobs(self) -> int:
        """How many Run presses are waiting behind the one in flight."""

        return len(self._queue)

    def run_request(self) -> RunRequest:
        """What pressing Run right now would ask for."""

        keys = self.run_keys()
        return RunRequest(
            keys=keys,
            stages=self.run_bar.selected_stages(),
            jobs=self.run_bar.jobs(),
            dry_run=self.run_bar.is_dry_run(),
            continue_on_lvs_fail=self.run_bar.continue_on_lvs_fail(),
            recipe_override=self.run_bar.recipe_override(),
        )

    def export_gds(self, keys: tuple[str, ...] | None = None) -> None:
        """Write a standalone GDS for software outside this flow.

        Deliberately NOT a relocation of the layout file Calibre reads. That
        file is a producer/consumer contract -- strmout writes it, the LVS
        runset names the same value as ``*lvsLayoutPaths`` -- so this runs
        strmout a second time to a second destination and leaves the first
        one alone. The stage set is pinned to ``strmout`` here for the same
        reason the runner refuses anything else: there must be no combination
        of clicks that points LVS at a file nobody wrote.

        Multi-row exports get ``{cell}`` appended to the chosen directory
        rather than a single filename, because one fixed name would have each
        cell silently overwrite the last.
        """
        keys = tuple(keys) if keys else self.selected_keys()
        keys = tuple(k for k in keys if self._book.entry(k).enabled)
        if not keys:
            return

        if len(keys) == 1:
            cell = self._book.entry(keys[0]).cell
            chosen, _ = QFileDialog.getSaveFileName(
                self, "Export GDS", f"{cell}.gds", "GDS (*.gds *.db);;All files (*)"
            )
            target = chosen
        else:
            # A directory, not a filename: {cell} is what keeps N exports
            # from collapsing onto one file.
            directory = QFileDialog.getExistingDirectory(
                self, f"Export GDS for {len(keys)} cells"
            )
            target = f"{directory}/{{cell}}.gds" if directory else ""

        if not target:
            return

        request = RunRequest(
            keys=keys,
            stages=("strmout",),
            jobs=1,
            dry_run=False,
            continue_on_lvs_fail=False,
            recipe_override=self.run_bar.recipe_override(),
            layout_export_path=target,
        )
        if self._controller is not None:
            self._enqueue(request)
        self.run_requested.emit(request)

    def start_run(self) -> None:
        """Enqueue a job for the run set as it stands right now.

        Never a refusal. Pressing Run while a job is in flight appends a
        second one behind it -- that is the whole gesture the user asked
        for -- and the request is snapshotted here, so an edit made while
        the job waits cannot change what this press asked to run.

        ``run_requested`` fires either way, so a host that wants to own the
        dispatch can connect to it and construct the screen without a
        controller. It fires *after* the enqueue, so a host reading
        :meth:`queued_jobs` from the slot sees the press it is being told
        about.
        """

        request = self.run_request()
        if not request.keys or not request.stages:
            return
        if self._controller is not None:
            self._enqueue(request)
        self.run_requested.emit(request)

    def _recipe_batches(
        self, request: RunRequest, by_id: dict, resolve
    ) -> tuple[list[RunBatch], list[str]]:
        """Group the requested rows into one batch per recipe.

        Returns ``(batches, unresolved)``. ``unresolved`` names the rows that
        could not be given a recipe at all; when it is non-empty the caller
        refuses the run rather than dispatching the rest, because a partial
        batch is the shape that gets silently re-run in full later.

        Three sources, in order:

        1. **the run bar's override** -- one batch, every row, this run only;
        2. **the row's own ``recipe``**, which is what the recipe column
           writes and ``cells.yaml`` now stores;
        3. **the library**, but only when it holds exactly one recipe. With
           two there is nothing to infer and guessing produces
           plausible-looking parasitics from the wrong settings.

        Batch order follows first appearance in the selection, so a run whose
        rows all share a recipe behaves exactly like the single-recipe
        dispatch this replaced.
        """

        if request.recipe_override:
            recipe = resolve(request.recipe_override)
            if recipe is None:
                return [], list(request.keys)
            return [RunBatch(recipe=recipe, tasks=[by_id[k] for k in request.keys])], []

        bindings = self.recipe_bindings()
        order: list[str] = []
        grouped: dict[str, list] = {}
        unresolved: list[str] = []
        cache: dict[str, Any] = {}
        for key in request.keys:
            recipe_id = bindings.get(key)
            recipe = resolve(recipe_id) if recipe_id else resolve(None)
            if recipe is None:
                unresolved.append(key)
                continue
            if recipe.recipe_id not in grouped:
                grouped[recipe.recipe_id] = []
                order.append(recipe.recipe_id)
                cache[recipe.recipe_id] = recipe
            grouped[recipe.recipe_id].append(by_id[key])
        if unresolved:
            return [], unresolved
        return [RunBatch(recipe=cache[rid], tasks=grouped[rid]) for rid in order], []

    def _resolve_batches(self, request: RunRequest) -> _Resolved | None:
        """Turn a request into a run this screen could start today.

        Every refusal dialog fires here, which is at *press* time: the user
        is looking at the table and the config they built the request from,
        so "these rows name no recipe" points at rows still on the screen.
        Returning ``None`` means the press produced no job.

        What comes back is the batches *and* the five controller values the
        worker needs, captured here rather than read again when the job
        starts -- see :class:`_Resolved`.
        """

        controller = self._controller
        project = getattr(controller, "project", None)
        if project is None:
            QMessageBox.warning(self, "No config", "Load a config directory first.")
            return None
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
            return None
        auto_ext_root = controller.auto_ext_root
        workarea = controller.workarea
        if auto_ext_root is None or workarea is None:
            QMessageBox.critical(
                self,
                "Paths unresolved",
                "auto_ext_root and workarea could not be derived. "
                "Pass --auto-ext-root / --workarea to the gui command.",
            )
            return None

        # ``run_tasks`` takes one recipe and one profile per call. The
        # profile is the workspace's; the recipes come from the rows, and
        # rows wanting different ones become different batches (see
        # ``RunWorker``). The run bar's override, when set, replaces all of
        # them for this run only -- which is what its hint has always said.
        profile = getattr(controller, "profile", None)
        if profile is None:
            QMessageBox.warning(
                self,
                "No PDK profile",
                "This run has no PDK profile, so there are no process "
                "literals to extract with.\n\n"
                "workspace.yaml names the profile; check that "
                "config/profiles/ holds a file with that id.",
            )
            return None

        resolve = getattr(controller, "run_recipe", None)
        if not callable(resolve):
            return None
        batches, unresolved = self._recipe_batches(request, by_id, resolve)
        if unresolved:
            names = "\n".join(f"  {key}" for key in unresolved[:8])
            more = (
                ""
                if len(unresolved) <= 8
                else f"\n  ... and {len(unresolved) - 8} more"
            )
            QMessageBox.warning(
                self,
                "No recipe for some rows",
                "These rows do not name a recipe, and the library holds "
                "more than one, so there is nothing to guess from:\n\n"
                f"{names}{more}\n\n"
                "Pick one in each row's recipe column, or choose one in "
                "the run bar to use for this run only.",
            )
            return None
        if not batches:
            return None
        resources = getattr(controller, "resources", None)
        refusal = self._preflight_refusal(request, batches, project, profile, resources)
        if refusal is not None:
            title, text = refusal
            QMessageBox.warning(self, title, text)
            self._say(f"not started: {_first_line(text)}")
            return None
        return _Resolved(
            batches=batches,
            project=project,
            auto_ext_root=auto_ext_root,
            workarea=workarea,
            profile=profile,
            resources=getattr(controller, "resources", None),
        )

    def _preflight_refusal(
        self,
        request: RunRequest,
        batches: list[RunBatch],
        project: Any,
        profile: Any,
        resources: Any,
    ) -> tuple[str, str] | None:
        """``(title, text)`` when the runner would refuse this press, else ``None``.

        The same checks ``run_tasks`` makes before any process starts, made
        here, at press time. They used to surface only from the worker
        thread: a small "Run failed: EnvResolutionError" box, rows left
        reading "queued" and a status line saying "idle" -- three answers, and
        the right one the least visible.
        """

        for batch in batches:
            try:
                preflight(
                    project,
                    list(batch.tasks),
                    stages=list(request.stages),
                    recipe=batch.recipe,
                    profile=profile,
                    max_workers=request.jobs if request.jobs >= 2 else None,
                    resources=resources,
                    layout_export_path=request.layout_export_path,
                )
            except EnvResolutionError as exc:
                return (
                    "Environment not set up",
                    f"{exc}\n\nNothing was started. Source the Cadence / PDK "
                    "setup that exports these variables and restart Auto_ext, "
                    "or pin them in the Setup drawer (top right).",
                )
            except AutoExtError as exc:
                return ("Run refused", f"{exc}\n\nNothing was started.")
        return None

    def _enqueue(self, request: RunRequest) -> _Job | None:
        """Resolve a press into a job and put it at the back of the queue."""

        resolved = self._resolve_batches(request)
        if resolved is None:
            return None
        job = _Job(
            request=request,
            resolved=resolved,
            live=_LiveRun(keys=request.keys, stages=request.stages),
        )
        # Rows the job in flight is already reporting on keep their live
        # state; the rest are told they are spoken for.
        running_keys = set(self._live.keys)
        for key in request.keys:
            if key in running_keys:
                continue
            job.previous[key] = self.row_status(key)
            self.set_row_status(key, "pending", text="queued")
        self._queue.append(job)
        # The rows a waiting job spoke for say "queued" in the status column;
        # without this their chip column still reads an em dash, so the same
        # row answers the same question two different ways.
        self._repaint_live_strips()
        self._refresh_run_panel()
        self._pump()
        return job

    def _pump(self) -> None:
        """Start the next job, if there is one and nothing is in flight."""

        if self._worker is not None or not self._queue:
            self._refresh_run_panel()
            return
        self._start_job(self._queue.pop(0))

    def _start_job(self, job: _Job) -> None:
        """Build the worker trio for ``job`` and set it going.

        Every input comes off the job, not off the controller: the job was
        validated when the button went down and the controller may have been
        reloaded since (see :class:`_Resolved`).
        """

        reporter = QtProgressReporter()
        reporter.run_started.connect(self._on_run_started)
        reporter.task_started.connect(self._on_task_started)
        reporter.stage_started.connect(self._on_stage_started)
        reporter.stage_finished.connect(self._on_stage_finished)
        reporter.task_finished.connect(self._on_task_finished)
        reporter.run_dir_ready.connect(self._on_run_dir_ready)
        token = CancelToken()

        request = job.request
        resolved = job.resolved
        self._reporter = reporter
        self._current = job
        self._worker = RunWorker(
            project=resolved.project,
            batches=resolved.batches,
            stages=list(request.stages),
            auto_ext_root=resolved.auto_ext_root,
            workarea=resolved.workarea,
            reporter=reporter,
            cancel_token=token,
            profile=resolved.profile,
            resources=resolved.resources,
            max_workers=request.jobs if request.jobs >= 2 else None,
            dry_run=request.dry_run,
            continue_on_lvs_fail=request.continue_on_lvs_fail,
            layout_export_path=request.layout_export_path,
        )
        self._worker.error.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_done)
        # M-134: the QThread's C++ object outlives the Python reference by
        # exactly one trip through the event loop, which is what a plain
        # ``self._worker = None`` in the finished slot did not give it.
        self._worker.finished.connect(self._worker.deleteLater)
        self._enter_running_state(job)
        self._worker.start()

    def cancel_run(self) -> None:
        """Stop the job in flight. The queue behind it is left alone."""

        worker = self._worker
        if worker is None:
            return
        worker.request_cancel()
        self._cancelling = worker
        self._cancel_serial += 1
        serial = self._cancel_serial
        self.run_bar.mark_cancelling()
        self._say("cancelling — the runner stops at its next check")
        # M-133: a cancel that never comes back used to leave the screen with
        # neither Run nor Cancel. Nothing here can make a stuck subprocess
        # return, but the user is owed the sentence and the button.
        #
        self._arm_cancel_deadline(serial)

    def _arm_cancel_deadline(self, serial: int) -> None:
        """One deadline per press, owned by this screen.

        The serial is what makes the deadline belong to *this* press. A timer
        already ticking cannot be recalled, so a 15s deadline armed for job 1
        still fires long after job 1 stopped and job 2 started; a guard that
        only asked "is the worker I cancelled still in flight" would then call
        job 2's five-second-old Cancel stalled. Pressing Cancel twice on one
        worker has the same shape and the same answer: only the newest press
        owns the deadline.

        A ``QTimer`` parented to the screen rather than ``QTimer.singleShot``,
        because a free-standing 15s timer outlives the widget it was armed
        for -- and a deadline that fires into a destroyed run bar is a
        RuntimeError on a dead C++ button, which is not a sentence anyone can
        act on.
        """

        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_cancel_deadline(serial))
        timer.timeout.connect(timer.deleteLater)
        timer.start(CANCEL_DEADLINE_MS)

    def _on_cancel_deadline(self, serial: int) -> None:
        if serial != self._cancel_serial:
            return  # a later Cancel owns the deadline now
        if self._worker is None or self._worker is not self._cancelling:
            return  # it came back, or a different job is in flight now
        self.run_bar.mark_cancel_stalled()
        self._say(
            "the runner has not come back since Cancel — it is still inside "
            "a tool call; press Cancel again, or leave it and keep working"
        )

    def drop_queued_jobs(self) -> tuple[str, ...]:
        """Throw away every job waiting behind the one in flight.

        Returns the row keys that stopped being spoken for. The running job
        is untouched -- that is :meth:`cancel_run`'s job, and one control
        that meant both would make "stop" ambiguous at the worst moment.
        """

        if not self._queue:
            return ()
        dropped, self._queue = self._queue, []
        count = len(dropped)
        still_queued = set(self._live.keys)
        restored: set[str] = set()
        for job in reversed(dropped):
            for key, previous in job.previous.items():
                if key in still_queued:
                    continue
                self.set_row_status(
                    key,
                    previous.status,
                    text=previous.text,
                    when=previous.when,
                    code=previous.code,
                )
                restored.add(key)
        self._repaint_live_strips()
        self._refresh_run_panel()
        noun = "run" if count == 1 else "runs"
        self._say(f"dropped {count} waiting {noun}")
        return tuple(sorted(restored))

    def stop_run_and_wait(self, timeout_ms: int = 5000) -> bool:
        """Drop the queue, cancel the run in flight, block until it is gone.

        For the window's ``closeEvent`` only. ``cancel_run`` returns
        immediately and the runner notices at its next check; letting the
        process exit in that window destroys the QThread's C++ object while
        it is still running, which is the orphan-thread crash rather than a
        clean stop. Returns whether the thread finished in time.
        """

        self._queue = []
        self._refresh_run_panel()
        worker = self._worker
        if worker is None:
            return True
        self.cancel_run()
        return bool(worker.wait(timeout_ms))

    def _enter_running_state(self, job: _Job) -> None:
        """Show the live panel for ``job``. Nothing on the screen goes dead.

        What this method used to do -- ``NoEditTriggers``, five disabled
        toolbar buttons, a check column redrawn as glyphs -- is the whole of
        the user's 2026-09-04 report and is gone. The only thing a run now
        changes is the column layout and the panel under the table.
        """

        request = job.request
        self.set_column_mode(MODE_RUNNING)
        self.run_bar.set_running(True)
        noun = "cell" if len(request.keys) == 1 else "cells"
        self.run_bar.set_run_label(f"Run {len(request.keys)} {noun}")
        self.run_bar.set_counts(queued=len(request.keys))
        self.run_bar.set_log_path(None)
        self._reveal_run_panel()
        self._repaint_live_strips()
        for key in request.keys:
            self.set_row_status(key, "pending", text="queued")
        self._refresh_run_panel()
        self._say(
            f"running {len(request.keys)} {noun} — the table stays live, and "
            "Run queues another job behind this one"
        )

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
        bar = max(
            self.run_bar.sizeHint().height(), self.run_bar.minimumSizeHint().height()
        )
        if bar <= 0 or bar >= total:
            return
        self._splitter.setSizes([total - bar, bar])

    def _refresh_run_panel(self) -> None:
        """Panel visible while anything is running or waiting; depth on it.

        This is also the one place that notices the queue has emptied by a
        route other than a worker finishing -- Drop queued, or a close that
        threw the queue away. Hiding the panel without leaving the running
        state left the table in ``running`` mode with 26px rows and the
        splitter still at two fifths, over a screen with no run on it.
        """

        live = self._worker is not None or bool(self._queue)
        self.run_bar.set_queued_jobs(len(self._queue))
        if not live and self.run_bar.is_running():
            self._leave_running_state()
            return
        self.run_bar.set_running(live)

    def _repaint_live_strips(self) -> None:
        """Redraw every stage strip from the jobs, not from what Qt had.

        The table is editable during a run, and any add / remove / rename
        rebuilds every row -- which used to be impossible and so never had to
        be survivable. Each strip is re-derived here: the job in flight wins,
        then a queued job, and a row in neither is dashed out.
        """

        if self._current is None and not self._queue:
            return
        queued: dict[str, _Job] = {}
        for job in self._queue:
            for key in job.request.keys:
                queued.setdefault(key, job)
        live = self._current.live if self._current is not None else None
        for key in self._row_keys:
            strip = self.stage_strip(key)
            if strip is None:
                continue
            if live is not None and key in live.keys:
                strip.set_stages(live.stages or STAGE_ORDER)
                if key in live.started:
                    strip.set_placeholder(None)
                    strip.set_statuses(live.stage_status.get(key, {}))
                else:
                    strip.set_placeholder("queued")
            elif key in queued:
                strip.set_stages(queued[key].request.stages or STAGE_ORDER)
                strip.set_placeholder("queued")
            else:
                strip.set_placeholder("—")

    def _leave_running_state(self) -> None:
        self.run_bar.set_running(False)
        self.run_bar.set_queued_jobs(0)
        self._restore_table_space()
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
        self._live.stage_status.setdefault(task_id, {})[stage] = "running"
        strip = self.stage_strip(task_id)
        if strip is not None:
            strip.set_status(stage, "running")
        if self.run_bar.follows_current_stage():
            path = self._stage_log_path(task_id, stage)
            self.run_bar.set_log_path(path)
            if path is not None:
                self.log_path_changed.emit(path)
        self._say(f"running — {task_id} / {stage}")

    def _on_stage_finished(
        self, task_id: str, stage: str, status: str, error: object
    ) -> None:
        self._live.stage_status.setdefault(task_id, {})[stage] = status
        strip = self.stage_strip(task_id)
        if strip is not None:
            strip.set_status(stage, status)
            if error:
                strip.setToolTip(str(error))

    def _on_task_finished(self, task_id: str, status: str) -> None:
        self._live.finished[task_id] = status
        run_dir = self._live.run_dirs.get(task_id)
        if run_dir is not None:
            self._result_dirs[task_id] = run_dir
        # M-29: the time, not an em dash. A run that ended a second ago is the
        # one row whose "last run" the user is certain to read.
        self.set_row_status(
            task_id, status, text=status, when=_when(datetime.now(timezone.utc))
        )
        self._update_counts()

    def _on_worker_error(self, message: str) -> None:
        """Report a run that raised, without stopping the queue behind it.

        Deliberately not ``QMessageBox.critical``. That static spins a
        nested event loop until the user clicks OK, and the events it
        processes include the worker's own ``finished`` and the zero-timer
        behind it -- so the next job would start, and print its stage lines
        into a run panel sitting behind an error dialog nobody has read yet.
        A modeless box says the same sentence and lets the queue move.
        """

        self._worker_error = message
        box = QMessageBox(QMessageBox.Critical, "Run failed", message, QMessageBox.Ok, self)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.setModal(False)
        box.show()

    def _on_worker_done(self) -> None:
        """Retire the job that just ended; the next one starts off a timer.

        ``self._worker`` is handed to :attr:`_retiring` rather than simply
        cleared: dropping the last Python reference to a ``QThread`` from
        inside that thread's own ``finished`` slot is M-134, the
        "QThread: Destroyed while thread is still running" crash. The next
        job is dispatched from :meth:`_advance` on a zero-timer, so no
        worker is ever constructed inside another worker's slot either.
        """

        worker = self._worker
        summary = worker.summary if worker is not None else None
        finished = dict(self._live.finished)
        error, self._worker_error = self._worker_error, None
        # Rows the job spoke for and never reported on: the dispatch raised
        # before reaching them. Left alone they read "queued" forever under an
        # idle status line.
        for key in self._live.keys:
            if key not in finished:
                self.set_row_status(key, "failed", text="not run")
        self._disconnect_reporter(self._reporter)
        self._worker = None
        self._reporter = None
        self._current = None
        self._cancelling = None
        if worker is not None:
            self._retiring.append(worker)
        if self._queue:
            self._refresh_run_panel()
        else:
            self._leave_running_state()
        passed = sum(1 for status in finished.values() if status == "passed")
        if error is not None:
            self._outcome = f"run failed: {_first_line(error)}"
        elif self._queue:
            # Not "idle": there is a press the user made and has not seen
            # run, and ``_say`` puts the count of them on the end.
            self._outcome = f"{passed}/{len(finished)} passed"
        else:
            self._outcome = f"idle — {passed}/{len(finished)} passed"
        self._say(self._outcome)
        self.run_finished.emit(summary)
        QTimer.singleShot(0, self._advance)

    def _advance(self) -> None:
        """One event-loop tick after a run ended: let go, then start the next."""

        self._retiring.clear()
        self._pump()

    @staticmethod
    def _disconnect_reporter(reporter: QtProgressReporter | None) -> None:
        """Unhook a retired reporter from this screen's slots.

        Today nothing is left to emit -- ``run_tasks`` joins its thread pool
        inside a ``with`` block, so every worker thread is gone before
        ``finished`` reaches us. That is a property of the runner, not of
        this screen, and a retired reporter that could still reach the slots
        of the *next* job's bookkeeping is not a dependency worth keeping.
        """

        if reporter is None:
            return
        for signal in (
            reporter.run_started,
            reporter.task_started,
            reporter.stage_started,
            reporter.stage_finished,
            reporter.task_finished,
            reporter.run_dir_ready,
        ):
            try:
                signal.disconnect()
            except TypeError:
                pass  # nothing was connected

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


def _when(moment: datetime) -> str:
    """``MM-DD HH:MM``, on the same clock as the Runs list's subtitles.

    Both columns name a run the user will then look for on the Runs screen,
    so they must read the same instant the same way.
    """

    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%m-%d %H:%M")


def _mono_font() -> QFont:
    font = QFont()
    apply_families(font, theme.FONT_MONO_FAMILIES)
    font.setPixelSize(theme.FONT_SIZE_MONO)
    return font


def _column_of_field(field_name: str) -> int:
    if field_name == RECIPE_FIELD:
        return COL_RECIPE
    for column, name in EDITABLE_FIELDS.items():
        if name == field_name:
            return column
    raise KeyError(field_name)


def _first_line(exc: object) -> str:
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
