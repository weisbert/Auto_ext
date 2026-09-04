"""The Runs screen (design canvas artboard 1c / 1d / 1e).

Run history on the left, the selected run's card on the right. Every run is a
directory under ``<auto_ext_root>/runs/`` that is written once and never
overwritten, so this screen is a reader: it lists what is there, renders the
one the user picked, and asks the host for anything that would change the
world.

Reading is delegated wholesale to :mod:`auto_ext.core.run_store` --
:func:`~auto_ext.core.run_store.list_runs` never raises on a corrupt or
hand-made directory, so a single bad run cannot empty the history. Writing is
limited to ``annotations.json``: renaming a run and attaching a note change
the user-facing label only, never the directory name and never ``run.json``,
because ``batches/*.json``, ``parent_run_id`` and every relative path inside a
run reference the run by its immutable id.

What it asks the host to do
---------------------------
Two actions leave the screen as signals because the screen owns neither the
run queue nor the Setup drawer: :attr:`RunsScreen.rerun_requested` and
:attr:`RunsScreen.setup_requested`.

:attr:`RunsScreen.handoff_requested` is emitted either way, but the screen
also launches Calibre Interactive itself **when nothing is connected to that
signal**, so a screen used on its own is still fully functional and a host
that wants to own the launch simply connects and takes over. The same
mechanism gates the Setup drawer's "pin this value" row.

Opening a log or an artifact is *not* one of those requests. The screen owns
it end to end -- see :meth:`RunsScreen._open_once` -- because that is where
the answers to "the file is gone", "the launcher refused" and "this host
cannot open files at all" live, and because a screen that opened a file and
then also announced it as a request launched two editors.

Layout contract
---------------
The window floor is 940x560 px, and this screen must fit inside it with room
to spare. The index and the detail live in a splitter (so 250px is a starting
width, not a minimum), the card puts its own detail sections in a scroll area,
and the run rows are painted by a delegate rather than built out of widgets --
a history of several hundred runs must not become several hundred widgets on
an X11-forwarded link.

Assumptions
-----------
* **One row per run, not per batch.** Canvas 1c lists batches ("Run 137",
  3 cells inside).
  :class:`~auto_ext.core.run_store.RunIndexEntry` carries no ``batch_id``, and
  grouping would mean reading every ``run.json`` in the history. The row is
  therefore one run, which is also one DUT -- the grouping is a
  ``run_store`` question, not a UI one.
* **The result column shows the run's own verdict**, and its discrepancy count
  when it has one, because that is what the index really knows. The canvas's
  "3/3" is a per-batch cell tally.
* **Canvas 1e's four-failures-in-one-batch grouping lives inside the card.**
  Classifying every run in the history would mean reading every record; a
  single run's failing stages are already grouped by who has to act, in the
  same order and with the same code chips
  (:func:`auto_ext.ui.widgets.result_card.sort_failure_groups`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from PyQt5.QtCore import QModelIndex, QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QAction,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.errors import AutoExtError
from auto_ext.core.handoff import launch_calibre_interactive
from auto_ext.core.run_store import (
    RunIndexEntry,
    find_previous_run,
    list_runs,
    read_annotations,
    read_record,
    write_annotations,
)
from auto_ext.model.run import RunRecord
from auto_ext.ui import theme
from auto_ext.ui.os_open import open_in_os
from auto_ext.ui.widgets.failure_chip import (
    CHIP_TONE_FAILED,
    CHIP_TONE_MUTED,
    CHIP_TONE_PASSED,
    CHIP_TONE_WARNING,
    Chip,
    PathLabel,
)
from auto_ext.ui.widgets.result_card import (
    ResultCard,
    format_duration,
    format_timestamp,
    tally_text,
)

__all__ = ["ALL_CELLS", "RUN_ROW_HEIGHT", "RunsScreen", "result_text"]

#: Two lines of text plus padding, per canvas 1c.
RUN_ROW_HEIGHT = 40

#: The "no cell filter" entry of the cell combo.
ALL_CELLS = "all cells"

# Item roles on a history row.
_ROLE_RUN_ID = Qt.UserRole
_ROLE_SUBTITLE = Qt.UserRole + 1
_ROLE_RESULT = Qt.UserRole + 2
_ROLE_RESULT_COLOR = Qt.UserRole + 3

#: Overall statuses that mean "this run did not deliver".
_FAILED_STATES = frozenset({"failed", "cancelled"})


def result_text(entry: RunIndexEntry) -> tuple[str, str]:
    """``(text, colour)`` for the right-hand column of a history row.

    The verdict first, then the one number that decides whether the user has
    to look: the discrepancy count. A run that never produced an LVS result
    shows the verdict alone rather than a zero it cannot justify.
    """

    color = theme.status_color(entry.overall)
    glyph = theme.STATUS_GLYPH.get(entry.overall, theme.STATUS_GLYPH["pending"])
    text = f"{glyph} {entry.overall}"
    if entry.lvs_discrepancies is not None:
        text += f"  D={entry.lvs_discrepancies}"
    elif entry.lvs_passed is False:
        text += "  LVS"
    return text, color


class _RunRowDelegate(QStyledItemDelegate):
    """Paints a two-line history row: label + verdict, then time + recipe.

    A delegate rather than one widget per row: the history is unbounded and
    every widget in it would be another X11 surface to repaint.
    """

    def sizeHint(  # noqa: N802 - Qt naming
        self, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QSize:
        return QSize(0, RUN_ROW_HEIGHT)

    def paint(  # noqa: C901 - one straight-line painter, no branching depth
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex
    ) -> None:
        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.State_Selected)

        painter.fillRect(
            rect,
            QColor(theme.ACCENT_SELECTION if selected else theme.SURFACE_CARD),
        )
        if selected:
            painter.fillRect(
                QRect(rect.left(), rect.top(), theme.SELECTED_BAR_WIDTH, rect.height()),
                QColor(theme.ACCENT),
            )
        painter.setPen(QColor(theme.LINE_ROW))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        left = rect.left() + theme.SPACE_MD
        right = rect.right() - theme.SPACE_SM
        top = rect.top() + theme.SPACE_XS

        mono = QFont(painter.font())
        mono.setFamily(theme.FONT_MONO_FAMILIES[0])
        mono.setPixelSize(theme.FONT_SIZE_MONO)
        mono.setBold(selected)

        result = str(index.data(_ROLE_RESULT) or "")
        result_color = str(index.data(_ROLE_RESULT_COLOR) or theme.TEXT_SECONDARY)
        painter.setFont(mono)
        metrics = painter.fontMetrics()
        result_width = metrics.horizontalAdvance(result) if result else 0
        line_height = metrics.height()

        if result:
            painter.setPen(QColor(result_color))
            painter.drawText(
                QRect(right - result_width, top, result_width, line_height),
                int(Qt.AlignRight | Qt.AlignVCenter),
                result,
            )

        title_width = max(0, (right - result_width - theme.SPACE_SM) - left)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        painter.drawText(
            QRect(left, top, title_width, line_height),
            int(Qt.AlignLeft | Qt.AlignVCenter),
            metrics.elidedText(str(index.data(Qt.DisplayRole) or ""), Qt.ElideMiddle, title_width),
        )

        meta = QFont(mono)
        meta.setBold(False)
        meta.setPixelSize(theme.FONT_SIZE_META)
        painter.setFont(meta)
        meta_metrics = painter.fontMetrics()
        painter.setPen(QColor(theme.TEXT_SECONDARY))
        subtitle_width = max(0, right - left)
        painter.drawText(
            QRect(left, top + line_height, subtitle_width, meta_metrics.height()),
            int(Qt.AlignLeft | Qt.AlignVCenter),
            meta_metrics.elidedText(
                str(index.data(_ROLE_SUBTITLE) or ""), Qt.ElideRight, subtitle_width
            ),
        )
        painter.restore()


class RunsScreen(QWidget):
    """Run history browser plus the result card for the selected run."""

    #: Emitted with the selected :class:`~auto_ext.core.run_store.RunIndexEntry`
    #: (or ``None`` when the selection is cleared).
    run_selected = pyqtSignal(object)
    #: Emitted with the absolute :class:`Path` of a stage log the user opened.
    #: **Not emitted while the screen owns opening** -- see :meth:`_open_once`.
    #: Kept because a host may be connected to it and because a future host
    #: that wants to own the launch needs somewhere to connect.
    log_requested = pyqtSignal(object)
    #: Emitted with the absolute :class:`Path` of an artifact the user opened.
    #: Same contract as :attr:`log_requested`.
    artifact_requested = pyqtSignal(object)
    #: Emitted with the :class:`~auto_ext.model.run.RunRecord` to re-open in
    #: Calibre Interactive. When nothing is connected the screen launches it.
    handoff_requested = pyqtSignal(object)
    #: Emitted with the entry the user wants to run again. The screen owns no
    #: run queue, so this is always a request.
    rerun_requested = pyqtSignal(object)
    #: Emitted with a section hint when a failure sends the user to Setup.
    setup_requested = pyqtSignal(str)
    #: Emitted with the ``run_id`` whose ``annotations.json`` was just written.
    annotations_saved = pyqtSignal(str)
    #: Emitted with the one line the shell status bar should show.
    status_message = pyqtSignal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        runs_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._runs_root = Path(runs_root) if runs_root is not None else None
        self._entries: list[RunIndexEntry] = []
        self._visible: list[RunIndexEntry] = []
        self._selected_run_id: str | None = None
        self._loading = False

        self._build_ui()
        self.refresh()

    # ---- public API ---------------------------------------------------

    @property
    def runs_root(self) -> Path | None:
        """The ``runs/`` directory being listed, or ``None``."""

        return self._runs_root

    def set_runs_root(self, runs_root: Path | None) -> None:
        """Point the history at ``runs_root`` and reload."""

        self._runs_root = Path(runs_root) if runs_root is not None else None
        self.refresh()

    @property
    def entries(self) -> list[RunIndexEntry]:
        """The full history as last read, newest first (before filtering)."""

        return list(self._entries)

    @property
    def visible_entries(self) -> list[RunIndexEntry]:
        """The rows the current filters leave on screen."""

        return list(self._visible)

    @property
    def selected_entry(self) -> RunIndexEntry | None:
        """The history row the user has selected, or ``None``."""

        item = self._list.currentItem()
        if item is None or self._list.count() == 0:
            return None
        run_id = item.data(_ROLE_RUN_ID)
        return self._entry_by_id(str(run_id)) if run_id else None

    @property
    def result_card(self) -> ResultCard:
        """The card rendering the selected run."""

        return self._card

    def refresh(self) -> None:
        """Re-read the run history from disk and repopulate the list.

        Keeps the current selection when that run is still present; otherwise
        selects the newest run so the screen is never opened on a blank card.
        """

        root = self._runs_root
        self.set_entries(list_runs(root) if root is not None else [])

    def set_entries(self, entries: Sequence[RunIndexEntry]) -> None:
        """Display ``entries`` without touching disk.

        The injection point for a host that has already listed the history,
        and for tests.
        """

        self._entries = list(entries)
        self._sync_cell_filter()
        self._repopulate()

    def select_run(self, run_id: str) -> bool:
        """Select the row for ``run_id``. Returns False when it is not listed."""

        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(_ROLE_RUN_ID) == run_id:
                self._list.setCurrentItem(item)
                return True
        return False

    def status_text(self) -> str:
        """The status-bar line for this screen (canvas 1c)."""

        total = len(self._entries)
        if total == 0:
            return "no runs recorded yet"
        return f"{total} run{'s' if total != 1 else ''} kept - nothing is ever overwritten"

    def set_cell_filter(self, cell: str) -> None:
        """Restrict the list to one cell, or to :data:`ALL_CELLS`."""

        index = self._cell_filter.findText(cell)
        self._cell_filter.setCurrentIndex(index if index >= 0 else 0)

    def set_failures_only(self, only: bool) -> None:
        """Show only runs that failed or were cancelled."""

        self._failures_only.setChecked(bool(only))

    # ---- construction --------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_index())
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([250, 700])
        self._splitter = splitter
        root.addWidget(splitter)

    def _build_index(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.NoFrame)
        panel.setStyleSheet(
            f"QFrame#runIndex {{ background: {theme.SURFACE_CARD}; border: none; "
            f"border-right: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        panel.setObjectName("runIndex")
        panel.setMinimumWidth(180)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(panel)
        header.setFixedHeight(theme.ROW_HEIGHT)
        header.setFrameShape(QFrame.NoFrame)
        header.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_TABLE_HEADER}; border: none; "
            f"border-bottom: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(theme.SPACE_SM, 0, theme.SPACE_SM, 0)
        header_layout.setSpacing(theme.SPACE_SM)
        for text, stretch, align in (
            ("run", 1, Qt.AlignLeft),
            ("result", 0, Qt.AlignRight),
        ):
            label = QLabel(text, header)
            label.setAlignment(align | Qt.AlignVCenter)
            label.setStyleSheet(
                f"font-size: {theme.FONT_SIZE_META}px; color: {theme.TEXT_SECONDARY};"
            )
            header_layout.addWidget(label, stretch)
        layout.addWidget(header)

        self._list = QListWidget(panel)
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.setItemDelegate(_RunRowDelegate(self._list))
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {theme.SURFACE_CARD}; border: none; "
            "outline: 0; }"
        )
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_menu)
        layout.addWidget(self._list, stretch=1)

        footer = QFrame(panel)
        footer.setFrameShape(QFrame.NoFrame)
        footer.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_TOOLBAR}; border: none; "
            f"border-top: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(theme.SPACE_SM, 2, theme.SPACE_SM, 2)
        footer_layout.setSpacing(theme.SPACE_SM)
        filter_label = QLabel("filter", footer)
        filter_label.setStyleSheet(
            f"font-size: {theme.FONT_SIZE_META}px; color: {theme.TEXT_SECONDARY};"
        )
        self._cell_filter = QComboBox(footer)
        self._cell_filter.addItem(ALL_CELLS)
        self._cell_filter.setToolTip("Show only the runs of one cell.")
        self._cell_filter.currentIndexChanged.connect(lambda _i: self._repopulate())
        self._failures_only = QCheckBox("failures only", footer)
        self._failures_only.setToolTip("Hide the runs that passed.")
        self._failures_only.toggled.connect(lambda _c: self._repopulate())
        footer_layout.addWidget(filter_label)
        footer_layout.addWidget(self._cell_filter, stretch=1)
        footer_layout.addWidget(self._failures_only)
        layout.addWidget(footer)
        return panel

    def _build_detail(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.NoFrame)
        panel.setStyleSheet(f"QFrame#runDetail {{ background: {theme.SURFACE_PAGE}; }}")
        panel.setObjectName("runDetail")
        panel.setMinimumWidth(320)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_detail_header(panel))

        self._stack = QStackedWidget(panel)
        self._card = ResultCard(self._stack)
        self._card.log_requested.connect(self._on_log_requested)
        self._card.artifact_requested.connect(self._on_artifact_requested)
        self._card.handoff_requested.connect(self._on_handoff_requested)
        self._card.rerun_requested.connect(self._on_card_rerun)
        self._card.setup_requested.connect(self.setup_requested.emit)
        self._card.copy_requested.connect(
            lambda text: self.status_message.emit(f"copied: {text}")
        )
        self._card.status_message.connect(self.status_message.emit)

        card_holder = QWidget(self._stack)
        holder_layout = QVBoxLayout(card_holder)
        holder_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        holder_layout.addWidget(self._card)

        self._empty = QLabel("", self._stack)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; padding: {theme.SPACE_XXL}px;"
        )

        self._stack.addWidget(card_holder)
        self._stack.addWidget(self._empty)
        layout.addWidget(self._stack, stretch=1)
        return panel

    def _build_detail_header(self, parent: QWidget) -> QWidget:
        band = QFrame(parent)
        band.setFrameShape(QFrame.NoFrame)
        band.setStyleSheet(
            f"QFrame {{ background: {theme.SURFACE_TOOLBAR}; border: none; "
            f"border-bottom: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        layout = QHBoxLayout(band)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        layout.setSpacing(theme.SPACE_MD)

        self._detail_title = PathLabel(band, mode=Qt.ElideRight)
        self._detail_title.setStyleSheet(
            f"font-size: {theme.FONT_SIZE_TITLE}px; "
            f"font-weight: {theme.FONT_WEIGHT_BOLD}; color: {theme.TEXT_PRIMARY};"
        )
        self._detail_meta = PathLabel(band, mode=Qt.ElideRight)
        # Preferred, not Ignored: both labels should take their natural width
        # when the band has room and fall back to PathLabel's small
        # minimumSizeHint when it does not.
        for label in (self._detail_title, self._detail_meta):
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._tally_chip = Chip("", CHIP_TONE_MUTED, band)
        self._rerun_btn = QPushButton("Re-run this cell", band)
        self._rerun_btn.setToolTip("Queue this cell again with the same recipe.")
        self._rerun_btn.clicked.connect(self._emit_rerun)
        self._refresh_btn = QPushButton("Refresh", band)
        self._refresh_btn.setToolTip("Re-read the runs directory.")
        self._refresh_btn.clicked.connect(self.refresh)

        layout.addWidget(self._detail_title)
        layout.addWidget(self._detail_meta, stretch=1)
        layout.addWidget(self._tally_chip)
        layout.addWidget(self._rerun_btn)
        layout.addWidget(self._refresh_btn)
        return band

    # ---- filtering and population --------------------------------------

    def _entry_by_id(self, run_id: str) -> RunIndexEntry | None:
        for entry in self._entries:
            if entry.run_id == run_id:
                return entry
        return None

    def _sync_cell_filter(self) -> None:
        """Rebuild the cell combo from the history, keeping the selection."""

        wanted = self._cell_filter.currentText() or ALL_CELLS
        cells = sorted({e.cell for e in self._entries if e.cell})
        blocked = self._cell_filter.blockSignals(True)
        try:
            self._cell_filter.clear()
            self._cell_filter.addItem(ALL_CELLS)
            for cell in cells:
                self._cell_filter.addItem(cell)
            index = self._cell_filter.findText(wanted)
            self._cell_filter.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._cell_filter.blockSignals(blocked)

    def _filtered(self) -> list[RunIndexEntry]:
        cell = self._cell_filter.currentText()
        failures_only = self._failures_only.isChecked()
        out = []
        for entry in self._entries:
            if cell and cell != ALL_CELLS and entry.cell != cell:
                continue
            if failures_only and entry.overall not in _FAILED_STATES:
                continue
            out.append(entry)
        return out

    def _empty_text(self) -> str:
        if self._runs_root is None:
            return (
                "No project loaded. Run history is kept in the project's runs/ "
                "directory."
            )
        if self._entries:
            return "No run matches the current filter."
        return (
            f"No runs recorded yet. Each run is archived under {self._runs_root} "
            "with its logs, rendered inputs and results."
        )

    def _repopulate(self) -> None:
        self._visible = self._filtered()

        self._loading = True
        try:
            self._list.clear()
            for entry in self._visible:
                item = QListWidgetItem(
                    ("* " if entry.starred else "") + entry.display_name
                )
                item.setData(_ROLE_RUN_ID, entry.run_id)
                item.setData(
                    _ROLE_SUBTITLE,
                    f"{entry.created_at.strftime('%m-%d %H:%M')} - "
                    f"{entry.recipe_id or entry.slug}",
                )
                text, color = result_text(entry)
                item.setData(_ROLE_RESULT, text)
                item.setData(_ROLE_RESULT_COLOR, color)
                tooltip = [
                    entry.run_id,
                    entry.dut_key,
                    f"recipe {entry.recipe_id}" if entry.recipe_id else "",
                    f"duration {format_duration(entry.duration_s)}",
                ]
                if entry.dry_run:
                    tooltip.append("dry run")
                if entry.tags:
                    tooltip.append("tags: " + ", ".join(entry.tags))
                item.setToolTip("\n".join(t for t in tooltip if t))
                self._list.addItem(item)
        finally:
            self._loading = False

        self.status_message.emit(self.status_text())

        if not self._visible:
            self._empty.setText(self._empty_text())
            self._stack.setCurrentWidget(self._empty)
            self._card.clear()
            self._selected_run_id = None
            self._update_detail_header(None)
            self.run_selected.emit(None)
            return

        # The list was cleared above, so whichever row becomes current here is
        # a genuine selection change and drives the card through the signal.
        if self._selected_run_id is None or not self.select_run(self._selected_run_id):
            self._list.setCurrentRow(0)

    # ---- selection -> card ---------------------------------------------

    def _on_selection_changed(self, *_args: Any) -> None:
        if self._loading:
            return
        entry = self.selected_entry
        if entry is None:
            self._selected_run_id = None
            self._card.clear()
            self._update_detail_header(None)
            self.run_selected.emit(None)
            return

        self._selected_run_id = entry.run_id
        self._stack.setCurrentIndex(0)
        record = self._read_record(entry)
        self._update_detail_header(entry, record)
        if record is None:
            self.run_selected.emit(entry)
            return

        previous = find_previous_run(
            self._runs_root or entry.run_dir.parent, entry, entries=self._entries
        )
        self._card.set_run(
            record,
            run_dir=entry.run_dir,
            annotations=read_annotations(entry.run_dir),
            previous=previous,
        )
        self.run_selected.emit(entry)

    def _read_record(self, entry: RunIndexEntry) -> RunRecord | None:
        try:
            return read_record(entry.run_dir)
        except AutoExtError as exc:
            self._card.show_message(f"{entry.run_id} could not be read: {exc}")
            return None

    def _update_detail_header(
        self, entry: RunIndexEntry | None, record: RunRecord | None = None
    ) -> None:
        if entry is None:
            self._detail_title.set_placeholder("Runs")
            self._detail_meta.set_placeholder(self.status_text())
            self._tally_chip.setVisible(False)
            self._rerun_btn.setEnabled(False)
            return

        self._detail_title.set_placeholder(entry.display_name)
        meta = [
            entry.run_id,
            f"{format_timestamp(entry.created_at)} -> "
            f"{format_timestamp(entry.ended_at)}",
        ]
        if record is not None and record.requested_stages:
            meta.append("stages " + " ".join(record.requested_stages))
        self._detail_meta.set_placeholder(" - ".join(meta))

        self._tally_chip.setVisible(True)
        if record is None:
            self._tally_chip.set_chip_text("unreadable", CHIP_TONE_FAILED)
        else:
            tone = {
                "passed": CHIP_TONE_PASSED,
                "failed": CHIP_TONE_FAILED,
                "cancelled": CHIP_TONE_WARNING,
            }.get(str(record.overall), CHIP_TONE_MUTED)
            self._tally_chip.set_chip_text(tally_text(record.stages), tone)
        self._rerun_btn.setEnabled(True)

    # ---- context menu ---------------------------------------------------

    def _on_list_menu(self, pos: QPoint) -> None:
        """Right-click menu on a history row (deferred popup).

        X11 delivers the context-menu event on button *press*, so a
        synchronous ``exec_()`` is dismissed by the following release and the
        user has to right-click twice. The popup is deferred one event-loop
        tick, which is the rule everywhere in this codebase.
        """

        item = self._list.itemAt(pos)
        if item is None:
            return
        run_id = str(item.data(_ROLE_RUN_ID) or "")
        entry = self._entry_by_id(run_id)
        if entry is None:
            return

        menu = QMenu(self._list)

        act_rename = QAction("Rename...", menu)
        act_rename.triggered.connect(lambda _c=False, e=entry: self._rename(e))
        menu.addAction(act_rename)

        act_note = QAction("Edit note...", menu)
        act_note.triggered.connect(lambda _c=False, e=entry: self._edit_note(e))
        menu.addAction(act_note)

        act_star = QAction("Unstar" if entry.starred else "Star", menu)
        act_star.setToolTip("A starred run survives prune-runs whatever its age.")
        act_star.triggered.connect(lambda _c=False, e=entry: self._toggle_star(e))
        menu.addAction(act_star)

        menu.addSeparator()

        act_open = QAction("Open run directory", menu)
        exists = entry.run_dir.is_dir()
        act_open.setEnabled(exists)
        act_open.setToolTip(
            str(entry.run_dir) if exists else f"Gone: {entry.run_dir}"
        )
        act_open.triggered.connect(
            lambda _c=False, p=entry.run_dir: self._open_path(p)
        )
        menu.addAction(act_open)

        act_copy = QAction("Copy run id", menu)
        act_copy.triggered.connect(lambda _c=False, r=run_id: self._card.copy_text(r))
        menu.addAction(act_copy)

        act_rerun = QAction("Re-run this cell", menu)
        act_rerun.triggered.connect(
            lambda _c=False, e=entry: self.rerun_requested.emit(e)
        )
        menu.addAction(act_rerun)

        global_pos = self._list.viewport().mapToGlobal(pos)
        QTimer.singleShot(0, lambda: menu.exec_(global_pos))

    # ---- annotations -----------------------------------------------------

    def _write_annotations(self, entry: RunIndexEntry, **changes: Any) -> None:
        """Apply ``changes`` to the run's annotations and persist them.

        Only ``annotations.json`` is touched: the directory name and
        ``run.json`` are immutable, so a rename can never break the relative
        paths inside the run or the ``run_id`` references from batches.
        """

        annotations = read_annotations(entry.run_dir)
        try:
            write_annotations(entry.run_dir, annotations.model_copy(update=changes))
        except (AutoExtError, OSError) as exc:
            QMessageBox.warning(
                self,
                "Could not save",
                f"Failed to write annotations for {entry.run_id}:\n{exc}",
            )
            return
        self.annotations_saved.emit(entry.run_id)
        self.refresh()

    def _rename(self, entry: RunIndexEntry) -> None:
        current = read_annotations(entry.run_dir).display_name or entry.display_name
        text, ok = QInputDialog.getText(
            self, "Rename run", f"Display name for {entry.run_id}:", text=current
        )
        if not ok:
            return
        self._write_annotations(entry, display_name=text.strip() or None)

    def _edit_note(self, entry: RunIndexEntry) -> None:
        current = read_annotations(entry.run_dir).note or ""
        text, ok = QInputDialog.getMultiLineText(
            self, "Run note", f"Note for {entry.run_id}:", current
        )
        if not ok:
            return
        self._write_annotations(entry, note=text.strip() or None)

    def _toggle_star(self, entry: RunIndexEntry) -> None:
        self._write_annotations(entry, starred=not entry.starred)

    # ---- card actions ----------------------------------------------------

    def _delegated(self, signal: Any) -> bool:
        """True when a host is listening, so the screen must not act itself."""

        return self.receivers(signal) > 0

    def _on_log_requested(self, path: object) -> None:
        self._open_once(path)

    def _on_artifact_requested(self, path: object) -> None:
        self._open_once(path)

    def _open_once(self, path: object) -> None:
        """Act on one "open this" request from the card -- exactly once.

        The screen opens it and deliberately does **not** re-emit. It used to
        do both, and :class:`~auto_ext.ui.main_window.MainWindow` connects
        ``log_requested`` and ``artifact_requested`` to an opener of its own,
        so every click on a log launched two editors -- the second of them
        through a call site with no ``try``, which takes an unhandled
        exception in a slot to ``qFatal``.

        Opening stays here rather than moving to the host because this is
        where the failure cases are answered: a path that has gone, a launcher
        that refuses, and a server with no file opener at all, which is
        offered the built-in viewer instead (:meth:`show_in_app`). A host that
        wants to know what the user opened has :attr:`status_message`.
        """

        target = Path(str(path)) if path else None
        if target is None:
            return
        self._open_path(target)

    def _on_card_rerun(self, _record: object) -> None:
        self._emit_rerun()

    def _emit_rerun(self) -> None:
        entry = self.selected_entry
        if entry is not None:
            self.rerun_requested.emit(entry)

    def _on_handoff_requested(self, record: object) -> None:
        """Launch Calibre Interactive, unless a host has taken the signal over.

        The plan is rebuilt here rather than reusing the one the card
        pre-flighted: the runset may have been deleted, or the Calibre setup
        script sourced, in the seconds since the card was drawn.
        :func:`~auto_ext.core.handoff.launch_calibre_interactive` never raises
        -- every outcome comes back on the plan.
        """

        if not isinstance(record, RunRecord):
            return
        if self._delegated(self.handoff_requested):
            self.handoff_requested.emit(record)
            return

        plan = launch_calibre_interactive(record)
        if not plan.ok:
            QMessageBox.warning(
                self,
                "Cannot open Calibre Interactive",
                plan.reason or "Calibre Interactive could not be started.",
            )
            return
        lines = [f"Calibre Interactive started (pid {plan.pid}).", "", plan.command_line]
        if plan.warnings:
            lines.extend(["", *plan.warnings])
        self.status_message.emit(f"Calibre Interactive started (pid {plan.pid})")
        QMessageBox.information(self, "Calibre Interactive", "\n".join(lines))

    def _open_path(self, path: object) -> None:
        """Open ``path`` with the OS handler, reporting failures in a dialog."""

        target = Path(str(path))
        try:
            open_in_os(target)
        except FileNotFoundError:
            QMessageBox.warning(
                self, "File not found", f"The path no longer exists:\n{target}"
            )
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not open", f"Failed to open:\n{target}\n\n{exc}"
            )
        else:
            # The screen no longer re-emits what it opened (M-20), so this is
            # how the host still learns of it.
            self.status_message.emit(f"opened: {target}")

    # ---- layout -----------------------------------------------------------

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Capped: the index scrolls and the card scrolls, so neither dictates.

        Qt would otherwise add the index panel's minimum width to the detail
        panel's and hand the window a floor it cannot honour on a 1366x768
        laptop screen.
        """

        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 560), min(hint.height(), 300))
