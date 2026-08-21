"""The run bar: what you press to start a batch, and what it becomes once
the batch is in flight.

Two states, one widget, drawn from artboards ``1a`` and ``1b``::

    idle (1a)
    +--------------------------------------------------------------+
    | 3 cells selected . recipe for this run [per row (2) v]  hint  |
    | stages [x]si [x]strmout [x]calibre [x]quantus [ ]jivaro       |  [Run 3 cells]
    |        | [ ]dry run [ ]continue on LVS fail | jobs [2]        |
    +--------------------------------------------------------------+

    running (1b)
    +--------------------------------------------------------------+ 28px
    | > Run 138   3 passed . 1 failed . 1 running . 2 queued        |
    |                              jobs 2  [Cancel run] [Collapse]  |
    +--------------------------------------------------------------+ 24px
    | logs/.../quantus.log            [x] follow  [Open in editor]  |
    +--------------------------------------------------------------+
    |  (log slot -- empty until set_log_widget())                   |
    +--------------------------------------------------------------+

**No progress bar and no ETA, on purpose.** The user said the wall-clock
cost of a run is not something they steer by; a bar that fills at a rate
nothing can predict is a lie with a smooth animation on it. What replaces
it is a count of what has actually finished and a live log.

The bar owns *intent* (which stages, how many jobs, dry run, which recipe
overrides the per-row choice) and *presentation of progress*. It owns no
threads: :class:`~auto_ext.ui.screens.cells_screen.CellsScreen` starts the
:class:`~auto_ext.ui.worker.RunWorker` and feeds this widget.

Responsive behaviour (artboard ``1j``, concession 4): below
:data:`RUN_BAR_COMPACT_BELOW` the stage checkbox row folds into a
``stages 4/5`` menu button that also carries dry-run and
continue-on-LVS-fail, and the whole bar becomes one line. The Run button
never shrinks. Widget state lives on the widgets themselves and the menu
is rebuilt from them every time it opens, so the two forms cannot drift.

Assumptions
-----------
* :data:`RUN_BAR_COMPACT_BELOW` is measured on the bar's own width.
  Artboard ``1j`` shows the folded form at a 1366px window but does not
  name a breakpoint; the value here is chosen so that the fold has
  happened by the time the window reaches its 940px floor and has not
  happened at the 1280px width artboard ``1a`` is drawn at.
* The log slot is a container, not a viewer. ``1b`` draws a dark log pane
  inside the run panel; which widget tails the file is the host's choice
  (:meth:`RunBar.set_log_widget`), because a log viewer already exists and
  duplicating one here would give the app two.
* ``Run 138`` in ``1b`` is a batch label the run layer supplies; the bar
  prints whatever :meth:`RunBar.set_run_label` is given and derives
  nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.runner import STAGE_ORDER
from auto_ext.ui import theme

__all__ = [
    "CHIP_GAP",
    "CHIP_PADDING_H",
    "OBJ_RUN_BAR",
    "RUN_BAR_COMPACT_BELOW",
    "ElidedLabel",
    "RunBar",
    "StageChipStrip",
    "apply_families",
    "chip_palette",
]

#: Bar width below which the stage row folds into a menu button.
RUN_BAR_COMPACT_BELOW = 900

#: Chip strip horizontal gap and inner padding, from artboard ``1b``.
CHIP_GAP = 3
CHIP_PADDING_H = 5

OBJ_RUN_BAR = "runBar"
OBJ_RUN_BAR_IDLE = "runBarIdle"
OBJ_RUN_PANEL_HEADER = "runPanelHeader"
OBJ_RUN_PANEL_LOG_BAR = "runPanelLogBar"
OBJ_RUN_PANEL_LOG_SLOT = "runPanelLogSlot"
OBJ_RUN_SELECTION = "runBarSelection"
OBJ_RUN_HINT = "runBarHint"
OBJ_RUN_META = "runBarMeta"
OBJ_RUN_MONO = "runBarMono"
OBJ_RUN_BUTTON = "runBarRunButton"
OBJ_RUN_GLYPH = "runPanelGlyph"
OBJ_RUN_TITLE = "runPanelTitle"
OBJ_RUN_COUNTS = "runPanelCounts"
OBJ_RUN_LOG_PATH = "runPanelLogPath"
OBJ_SEPARATOR = "barSeparator"


def apply_families(font: QFont, families: Sequence[str]) -> QFont:
    """Set ``families`` on ``font``, preferring Qt 5.13+'s real fallback list.

    ``QFont.setFamilies`` keeps every name as a separate fallback entry.
    Where it is missing the first name is used alone, which on CentOS 7 is
    DejaVu -- present by definition -- so the degradation is invisible.
    """

    names = list(families)
    setter = getattr(font, "setFamilies", None)
    if setter is not None:
        setter(names)
    else:  # pragma: no cover - PyQt5 < 5.13 only
        font.setFamily(names[0])
    return font


def _font(*, mono: bool = False, size: int = theme.FONT_SIZE_BODY, bold: bool = False) -> QFont:
    font = QFont()
    apply_families(font, theme.FONT_MONO_FAMILIES if mono else theme.FONT_SANS_FAMILIES)
    font.setPixelSize(size)
    font.setWeight(QFont.Bold if bold else QFont.Normal)
    return font


def chip_palette(status: str) -> tuple[str | None, str, str]:
    """``(fill, border, text)`` colours for a stage chip in ``status``.

    ``fill`` is ``None`` for the outline-only chips (pending, skipped,
    dry run) so the table's row background shows through and a queued row
    stays visually quiet. The accent is never returned: a chip states an
    outcome, and outcomes have their own scale.
    """

    if status == "passed":
        return theme.STATUS_FILL["passed"], theme.STATUS_LINE["passed"], theme.STATUS_PASSED
    if status == "failed":
        return theme.STATUS_FILL["failed"], theme.STATUS_LINE["failed"], theme.STATUS_FAILED
    if status == "running":
        return theme.STATUS_RUNNING, theme.STATUS_RUNNING, theme.ACCENT_ON
    if status == "cancelled":
        return (
            theme.STATUS_FILL["warning"],
            theme.STATUS_LINE["warning"],
            theme.WARNING_TEXT_ON_WHITE,
        )
    return None, theme.LINE_PANEL, theme.TEXT_DISABLED


class ElidedLabel(QLabel):
    """A label that elides instead of demanding width.

    The shell has a private one for the config path; this is the same idea
    made public for the screens, because every long string in this app
    (a cell name, a log path, a hint) has to be allowed to shrink or the
    940px window floor cannot be met.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        mode: Qt.TextElideMode = Qt.ElideRight,
    ) -> None:
        super().__init__(text, parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, self._mode, max(self.width(), 0)))


class StageChipStrip(QWidget):
    """The per-cell stage chips of artboard ``1b``, painted in one widget.

    One custom-painted widget rather than five child widgets per row: a
    table of 30 rows would otherwise carry 150 widgets, and every style
    recalculation would repaint all of them over the X11 link.

    Statuses are the reporter's own strings (``passed`` / ``failed`` /
    ``running`` / ``cancelled`` / ``skipped`` / ``dry_run``); anything
    unknown, including the empty string, renders as the pending outline.
    """

    def __init__(
        self,
        stages: Sequence[str] = STAGE_ORDER,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._stages: tuple[str, ...] = tuple(stages)
        self._statuses: dict[str, str] = {}
        self._placeholder: str | None = None
        self.setFont(_font(size=theme.FONT_SIZE_META))
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    # -- state ------------------------------------------------------------

    def stages(self) -> tuple[str, ...]:
        return self._stages

    def set_stages(self, stages: Sequence[str]) -> None:
        self._stages = tuple(stages)
        self._statuses = {k: v for k, v in self._statuses.items() if k in self._stages}
        self.updateGeometry()
        self.update()

    def set_status(self, stage: str, status: str) -> None:
        self._statuses[stage] = status
        self._placeholder = None
        self.update()

    def set_statuses(self, statuses: Mapping[str, str]) -> None:
        self._statuses = dict(statuses)
        self._placeholder = None
        self.update()

    def statuses(self) -> dict[str, str]:
        return dict(self._statuses)

    def clear_statuses(self) -> None:
        self._statuses.clear()
        self.update()

    def set_placeholder(self, text: str | None) -> None:
        """Show one neutral chip instead of the stage row (``"queued"``)."""

        self._placeholder = text
        self.updateGeometry()
        self.update()

    def placeholder(self) -> str | None:
        return self._placeholder

    # -- rendering --------------------------------------------------------

    def chip_texts(self) -> list[str]:
        """What the strip currently spells out, left to right."""

        if self._placeholder is not None:
            return [self._placeholder]
        texts = []
        for stage in self._stages:
            status = self._statuses.get(stage, "")
            glyph = theme.STATUS_GLYPH.get(status, theme.STATUS_GLYPH["skipped"])
            texts.append(f"{stage} {glyph}")
        return texts

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        metrics = QFontMetrics(self.font())
        width = 0
        for text in self.chip_texts():
            width += metrics.horizontalAdvance(text) + 2 * CHIP_PADDING_H + 2 + CHIP_GAP
        return QSize(max(width - CHIP_GAP, 0), theme.STAGE_CHIP_ROW_HEIGHT - 8)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, self.sizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setFont(self.font())
        metrics = QFontMetrics(self.font())
        height = min(self.height(), metrics.height() + 2)
        top = max((self.height() - height) // 2, 0)
        x = 0
        if self._placeholder is not None:
            pairs = [(self._placeholder, "")]
        else:
            pairs = [
                (f"{stage} {theme.STATUS_GLYPH.get(self._statuses.get(stage, ''), theme.STATUS_GLYPH['skipped'])}",
                 self._statuses.get(stage, ""))
                for stage in self._stages
            ]
        for text, status in pairs:
            width = metrics.horizontalAdvance(text) + 2 * CHIP_PADDING_H + 2
            if x >= self.width():
                break
            fill, border, fg = chip_palette(status)
            if fill is not None:
                painter.fillRect(x, top, width, height, QColor(fill))
            painter.setPen(QPen(QColor(border)))
            painter.drawRect(x, top, width - 1, height - 1)
            painter.setPen(QPen(QColor(fg)))
            painter.drawText(
                x + CHIP_PADDING_H,
                top,
                width - 2 * CHIP_PADDING_H,
                height,
                int(Qt.AlignLeft | Qt.AlignVCenter),
                text,
            )
            x += width + CHIP_GAP
        painter.end()


def _separator(parent: QWidget | None = None) -> QFrame:
    """A 1px vertical rule. Always parented: an unparented widget that gets
    ``show()`` called on it becomes a top-level window."""

    line = QFrame(parent)
    line.setObjectName(OBJ_SEPARATOR)
    line.setFrameShape(QFrame.NoFrame)
    line.setFixedWidth(1)
    line.setFixedHeight(18)
    return line


class RunBar(QFrame):
    """Recipe choice + Run, which becomes the live run panel.

    Signals
    -------
    ``run_requested()``
        The Run button was pressed and there is a selection to run. The
        host decides what "run" means; the bar changes nothing by itself.
    ``cancel_requested()``
        Cancel was pressed while running.
    ``collapse_toggled(bool)``
        The Collapse button flipped the log half of the panel. The payload
        is the new *collapsed* state.
    ``stages_changed(object)``
        Tuple of stage names, in :data:`STAGE_ORDER` order.
    ``jobs_changed(int)``
    ``recipe_override_changed(object)``
        The recipe id chosen for this run, or ``None`` for "per row".
    ``follow_changed(bool)``
        The "follow current stage" checkbox.
    ``open_log_requested(object)``
        "Open in editor" was pressed; payload is the current
        :class:`~pathlib.Path` or ``None``.
    """

    run_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    collapse_toggled = pyqtSignal(bool)
    stages_changed = pyqtSignal(object)
    jobs_changed = pyqtSignal(int)
    recipe_override_changed = pyqtSignal(object)
    follow_changed = pyqtSignal(bool)
    open_log_requested = pyqtSignal(object)

    #: Set ``False`` to drive :meth:`set_compact` yourself.
    auto_compact: bool = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_RUN_BAR)
        self.setFrameShape(QFrame.NoFrame)

        self._selection_count = 0
        self._per_row_summary = 0
        self._compact = False
        self._running = False
        self._collapsed = False
        self._log_path: Path | None = None
        self._log_widget: QWidget | None = None
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._idle = self._build_idle()
        self._panel = self._build_panel()
        outer.addWidget(self._idle)
        outer.addWidget(self._panel)
        self._panel.hide()

        self.setStyleSheet(_RUN_BAR_QSS)
        self._relayout()
        self._refresh_idle_text()

    # ---- construction ---------------------------------------------------

    def _build_idle(self) -> QWidget:
        idle = QWidget(self)
        idle.setObjectName(OBJ_RUN_BAR_IDLE)
        root = QHBoxLayout(idle)
        root.setContentsMargins(theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD)
        root.setSpacing(theme.SPACE_LG)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(theme.SPACE_XS)
        self._row1 = QHBoxLayout()
        self._row1.setContentsMargins(0, 0, 0, 0)
        self._row1.setSpacing(theme.SPACE_SM)
        self._row2_holder = QWidget(idle)
        self._row2 = QHBoxLayout(self._row2_holder)
        self._row2.setContentsMargins(0, 0, 0, 0)
        self._row2.setSpacing(theme.SPACE_LG)
        left.addLayout(self._row1)
        left.addWidget(self._row2_holder)
        # Hug the top: the bar lives in a splitter, and a user who drags the
        # handle down should get empty space, not two rows drifting apart.
        left.addStretch(1)
        root.addLayout(left, 1)

        self._selection_label = QLabel(idle)
        self._selection_label.setObjectName(OBJ_RUN_SELECTION)
        self._dot = QLabel(theme.STATUS_GLYPH["pending"], idle)
        self._dot.setObjectName(OBJ_RUN_HINT)
        self._recipe_caption = QLabel("recipe for this run", idle)
        self._recipe_caption.setObjectName(OBJ_RUN_META)
        self._recipe_combo = QComboBox(idle)
        self._recipe_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._recipe_combo.setMinimumContentsLength(8)
        self._recipe_combo.currentIndexChanged.connect(self._on_recipe_changed)
        self._hint = ElidedLabel(
            "override applies to this run only — row recipes are untouched", idle
        )
        self._hint.setObjectName(OBJ_RUN_HINT)
        self._hint.set_full_text(self._hint.text())

        self._stages_caption = QLabel("stages", idle)
        self._stage_checks: dict[str, QCheckBox] = {}
        for stage in STAGE_ORDER:
            box = QCheckBox(stage, idle)
            box.setChecked(stage != "jivaro")
            box.toggled.connect(self._on_stage_toggled)
            self._stage_checks[stage] = box
        self._dry_run = QCheckBox("dry run", idle)
        self._continue_on_lvs = QCheckBox("continue on LVS fail", idle)
        self._sep1 = _separator(idle)
        self._sep2 = _separator(idle)
        self._sep3 = _separator(idle)
        self._jobs_caption = QLabel("jobs", idle)
        self._jobs_caption.setObjectName(OBJ_RUN_META)
        self._jobs_spin = QSpinBox(idle)
        self._jobs_spin.setRange(1, 16)
        self._jobs_spin.setValue(1)
        self._jobs_spin.valueChanged.connect(self._on_jobs_changed)

        self._stages_button = QToolButton(idle)
        self._stages_button.setPopupMode(QToolButton.InstantPopup)
        self._stages_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._stages_menu = QMenu(self._stages_button)
        self._stages_menu.aboutToShow.connect(self._rebuild_stages_menu)
        self._stages_button.setMenu(self._stages_menu)

        self._run_button = QPushButton(idle)
        self._run_button.setObjectName(OBJ_RUN_BUTTON)
        self._run_button.setProperty("primary", True)
        self._run_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._run_button.clicked.connect(self._on_run_clicked)
        root.addWidget(self._run_button, 0, Qt.AlignVCenter)
        return idle

    def _build_panel(self) -> QWidget:
        panel = QWidget(self)
        root = QVBoxLayout(panel)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(panel)
        header.setObjectName(OBJ_RUN_PANEL_HEADER)
        header.setFrameShape(QFrame.NoFrame)
        header.setFixedHeight(28)
        head = QHBoxLayout(header)
        head.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, 0)
        head.setSpacing(theme.SPACE_LG)
        self._panel_glyph = QLabel(theme.STATUS_GLYPH["running"], header)
        self._panel_glyph.setObjectName(OBJ_RUN_GLYPH)
        self._panel_title = QLabel("Run", header)
        self._panel_title.setObjectName(OBJ_RUN_TITLE)
        self._panel_counts = ElidedLabel("", header)
        self._panel_counts.setObjectName(OBJ_RUN_COUNTS)
        self._panel_jobs = QLabel("", header)
        self._panel_jobs.setObjectName(OBJ_RUN_MONO)
        self._cancel_button = QPushButton("Cancel run", header)
        self._cancel_button.clicked.connect(self.cancel_requested)
        self._collapse_button = QPushButton("Collapse", header)
        self._collapse_button.clicked.connect(self._on_collapse_clicked)
        head.addWidget(self._panel_glyph)
        head.addWidget(self._panel_title)
        head.addWidget(self._panel_counts, 1)
        head.addWidget(self._panel_jobs)
        head.addWidget(self._cancel_button)
        head.addWidget(self._collapse_button)

        log_bar = QFrame(panel)
        log_bar.setObjectName(OBJ_RUN_PANEL_LOG_BAR)
        log_bar.setFrameShape(QFrame.NoFrame)
        log_bar.setFixedHeight(24)
        bar = QHBoxLayout(log_bar)
        bar.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, 0)
        bar.setSpacing(theme.SPACE_MD)
        self._log_label = ElidedLabel("", log_bar, mode=Qt.ElideMiddle)
        self._log_label.setObjectName(OBJ_RUN_LOG_PATH)
        self._follow_check = QCheckBox("follow current stage", log_bar)
        self._follow_check.setChecked(True)
        self._follow_check.toggled.connect(self.follow_changed)
        self._open_log_button = QPushButton("Open in editor", log_bar)
        self._open_log_button.clicked.connect(
            lambda: self.open_log_requested.emit(self._log_path)
        )
        bar.addWidget(self._log_label, 1)
        bar.addWidget(self._follow_check)
        bar.addWidget(self._open_log_button)

        slot = QWidget(panel)
        slot.setObjectName(OBJ_RUN_PANEL_LOG_SLOT)
        slot_layout = QVBoxLayout(slot)
        slot_layout.setContentsMargins(0, 0, 0, 0)
        slot_layout.setSpacing(0)
        self._log_slot = slot

        root.addWidget(header)
        root.addWidget(log_bar)
        root.addWidget(slot, 1)
        # An empty container is not a log pane. The slot appears when a
        # viewer is mounted; until then the panel is exactly its two
        # strips and the table above keeps the height.
        slot.hide()
        self._panel_log_bar = log_bar
        self._set_log_path_text(None)
        return panel

    # ---- layout modes ---------------------------------------------------

    def _clear(self, layout: QHBoxLayout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _relayout(self) -> None:
        """Re-place the idle widgets for the current width class.

        Both forms use the same widget instances, so there is exactly one
        copy of every piece of run intent and nothing to keep in sync.
        """

        self._clear(self._row1)
        self._clear(self._row2)
        wide_only = [
            self._dot,
            self._recipe_caption,
            self._hint,
            self._stages_caption,
            self._dry_run,
            self._continue_on_lvs,
            self._sep1,
            self._sep2,
            *self._stage_checks.values(),
        ]
        if self._compact:
            for widget in wide_only:
                widget.hide()
            self._stages_button.show()
            self._row2_holder.hide()
            self._row1.addWidget(self._selection_label)
            self._row1.addWidget(self._recipe_combo)
            self._row1.addWidget(self._sep3)
            self._sep3.show()
            self._row1.addWidget(self._stages_button)
            self._row1.addWidget(self._jobs_caption)
            self._row1.addWidget(self._jobs_spin)
            self._row1.addStretch(1)
        else:
            for widget in wide_only:
                widget.show()
            self._stages_button.hide()
            self._sep3.hide()
            self._row2_holder.show()
            self._row1.addWidget(self._selection_label)
            self._row1.addWidget(self._dot)
            self._row1.addWidget(self._recipe_caption)
            self._row1.addWidget(self._recipe_combo)
            self._row1.addWidget(self._hint, 1)
            self._row2.addWidget(self._stages_caption)
            for box in self._stage_checks.values():
                self._row2.addWidget(box)
            self._row2.addWidget(self._sep1)
            self._row2.addWidget(self._dry_run)
            self._row2.addWidget(self._continue_on_lvs)
            self._row2.addWidget(self._sep2)
            self._row2.addWidget(self._jobs_caption)
            self._row2.addWidget(self._jobs_spin)
            self._row2.addStretch(1)
        self._refresh_stages_button()

    def set_compact(self, compact: bool) -> None:
        if bool(compact) == self._compact:
            return
        self._compact = bool(compact)
        self._relayout()
        self._refresh_idle_text()

    def is_compact(self) -> bool:
        return self._compact

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self.auto_compact:
            self.set_compact(event.size().width() < RUN_BAR_COMPACT_BELOW)

    # ---- selection / run button ----------------------------------------

    def set_selection_count(self, count: int) -> None:
        self._selection_count = max(int(count), 0)
        self._refresh_idle_text()

    def selection_count(self) -> int:
        return self._selection_count

    def _refresh_idle_text(self) -> None:
        count = self._selection_count
        if self._compact:
            self._selection_label.setText(f"{count} selected")
        else:
            noun = "cell" if count == 1 else "cells"
            self._selection_label.setText(f"{count} {noun} selected")
        self._selection_label.setEnabled(count > 0)
        if count:
            noun = "cell" if count == 1 else "cells"
            self._run_button.setText(f"Run {count} {noun}")
        else:
            self._run_button.setText("Run")
        self._run_button.setEnabled(bool(count) and bool(self.selected_stages()))

    def run_button_text(self) -> str:
        return self._run_button.text()

    def run_button(self) -> QPushButton:
        return self._run_button

    def _on_run_clicked(self) -> None:
        if self._selection_count and self.selected_stages():
            self.run_requested.emit()

    # ---- recipe override -------------------------------------------------

    def set_recipe_choices(self, choices: Sequence[tuple[str, str]]) -> None:
        """Populate the override combo with ``(recipe_id, name)`` pairs.

        The first entry is always "per row", which means *do not* override:
        each row runs whatever recipe it is bound to.
        """

        previous = self.recipe_override()
        self._syncing = True
        try:
            self._recipe_combo.clear()
            self._recipe_combo.addItem(self._per_row_text(), None)
            for recipe_id, name in choices:
                self._recipe_combo.addItem(name, recipe_id)
        finally:
            self._syncing = False
        if previous is not None:
            self.set_recipe_override(previous)

    def set_per_row_summary(self, distinct_recipes: int) -> None:
        """Update the ``per row (2 recipes)`` count shown on the first item."""

        self._per_row_summary = max(int(distinct_recipes), 0)
        if self._recipe_combo.count():
            self._recipe_combo.setItemText(0, self._per_row_text())

    def _per_row_text(self) -> str:
        count = self._per_row_summary
        if not count:
            return "per row"
        noun = "recipe" if count == 1 else "recipes"
        return f"per row ({count} {noun})"

    def recipe_override(self) -> str | None:
        if not self._recipe_combo.count():
            return None
        data = self._recipe_combo.currentData()
        return data if isinstance(data, str) else None

    def set_recipe_override(self, recipe_id: str | None) -> None:
        index = 0 if recipe_id is None else self._recipe_combo.findData(recipe_id)
        if index < 0:
            index = 0
        self._recipe_combo.setCurrentIndex(index)

    def recipe_combo(self) -> QComboBox:
        return self._recipe_combo

    def _on_recipe_changed(self, _index: int) -> None:
        if not self._syncing:
            self.recipe_override_changed.emit(self.recipe_override())

    # ---- stages / jobs / flags -------------------------------------------

    def selected_stages(self) -> tuple[str, ...]:
        return tuple(s for s in STAGE_ORDER if self._stage_checks[s].isChecked())

    def set_selected_stages(self, stages: Sequence[str]) -> None:
        wanted = set(stages)
        self._syncing = True
        try:
            for stage, box in self._stage_checks.items():
                box.setChecked(stage in wanted)
        finally:
            self._syncing = False
        self._after_stage_change()

    def stage_check(self, stage: str) -> QCheckBox:
        return self._stage_checks[stage]

    def _on_stage_toggled(self, _checked: bool) -> None:
        if not self._syncing:
            self._after_stage_change()

    def _after_stage_change(self) -> None:
        self._refresh_stages_button()
        self._refresh_idle_text()
        self.stages_changed.emit(self.selected_stages())

    def _refresh_stages_button(self) -> None:
        chosen = len(self.selected_stages())
        self._stages_button.setText(f"stages {chosen}/{len(STAGE_ORDER)} ▾")

    def stages_button(self) -> QToolButton:
        return self._stages_button

    def _rebuild_stages_menu(self) -> None:
        """Rebuild the folded stage menu from the checkboxes, every time.

        The checkboxes stay the single source of truth, so a menu action
        can never hold a stale answer, and the two forms of the bar cannot
        disagree about what will run.
        """

        self._stages_menu.clear()
        for stage, box in self._stage_checks.items():
            action = QAction(stage, self._stages_menu)
            action.setCheckable(True)
            action.setChecked(box.isChecked())
            action.toggled.connect(box.setChecked)
            self._stages_menu.addAction(action)
        self._stages_menu.addSeparator()
        for box in (self._dry_run, self._continue_on_lvs):
            action = QAction(box.text(), self._stages_menu)
            action.setCheckable(True)
            action.setChecked(box.isChecked())
            action.toggled.connect(box.setChecked)
            self._stages_menu.addAction(action)

    def stages_menu(self) -> QMenu:
        return self._stages_menu

    def is_dry_run(self) -> bool:
        return self._dry_run.isChecked()

    def set_dry_run(self, value: bool) -> None:
        self._dry_run.setChecked(bool(value))

    def continue_on_lvs_fail(self) -> bool:
        return self._continue_on_lvs.isChecked()

    def set_continue_on_lvs_fail(self, value: bool) -> None:
        self._continue_on_lvs.setChecked(bool(value))

    def jobs(self) -> int:
        return self._jobs_spin.value()

    def set_jobs(self, jobs: int) -> None:
        self._jobs_spin.setValue(int(jobs))

    def _on_jobs_changed(self, value: int) -> None:
        self._panel_jobs.setText(f"jobs {value}")
        self.jobs_changed.emit(value)

    # ---- running panel ---------------------------------------------------

    def set_running(self, running: bool) -> None:
        """Swap between the idle bar and the live run panel."""

        self._running = bool(running)
        self._idle.setVisible(not self._running)
        self._panel.setVisible(self._running)
        if self._running:
            self._panel_jobs.setText(f"jobs {self.jobs()}")
            self._cancel_button.setEnabled(True)
            self._cancel_button.setText("Cancel run")

    def is_running(self) -> bool:
        return self._running

    def set_run_label(self, text: str) -> None:
        self._panel_title.setText(text)

    def run_label(self) -> str:
        return self._panel_title.text()

    def set_counts(
        self,
        *,
        passed: int = 0,
        failed: int = 0,
        running: int = 0,
        queued: int = 0,
    ) -> None:
        parts = [
            f"{passed} passed",
            f"{failed} failed",
            f"{running} running",
            f"{queued} queued",
        ]
        self._panel_counts.set_full_text(" · ".join(parts))

    def counts_text(self) -> str:
        return self._panel_counts.full_text()

    def mark_cancelling(self) -> None:
        """Cancel was accepted; the runner stops at its next check."""

        self._cancel_button.setEnabled(False)
        self._cancel_button.setText("cancelling…")

    def cancel_button(self) -> QPushButton:
        return self._cancel_button

    def collapse_button(self) -> QPushButton:
        return self._collapse_button

    def _on_collapse_clicked(self) -> None:
        self.set_collapsed(not self._collapsed)
        self.collapse_toggled.emit(self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """Hide (or restore) the log half of the run panel."""

        self._collapsed = bool(collapsed)
        self._panel_log_bar.setVisible(not self._collapsed)
        self._log_slot.setVisible(not self._collapsed and self._log_widget is not None)
        self._collapse_button.setText("Expand" if self._collapsed else "Collapse")

    def is_collapsed(self) -> bool:
        return self._collapsed

    # ---- log slot --------------------------------------------------------

    def set_log_path(self, path: Path | str | None) -> None:
        self._log_path = Path(path) if path is not None else None
        self._set_log_path_text(self._log_path)

    def _set_log_path_text(self, path: Path | None) -> None:
        self._log_label.set_full_text("" if path is None else str(path))
        self._open_log_button.setEnabled(path is not None)

    def log_path(self) -> Path | None:
        return self._log_path

    def follows_current_stage(self) -> bool:
        return self._follow_check.isChecked()

    def set_follows_current_stage(self, value: bool) -> None:
        self._follow_check.setChecked(bool(value))

    @property
    def log_container(self) -> QWidget:
        """The empty box under the log bar. Put a log viewer in it."""

        return self._log_slot

    def set_log_widget(self, widget: QWidget | None) -> QWidget | None:
        """Mount ``widget`` in the log slot, returning whatever it replaced."""

        previous = self._log_widget
        if previous is not None:
            self._log_slot.layout().removeWidget(previous)
            previous.setParent(None)
        self._log_widget = widget
        if widget is not None:
            self._log_slot.layout().addWidget(widget)
        self._log_slot.setVisible(widget is not None and not self._collapsed)
        return previous

    def log_widget(self) -> QWidget | None:
        return self._log_widget

    # ---- sizing ----------------------------------------------------------

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Never demand more width than the Run button plus a little.

        Everything left of the button elides, folds or scrolls; letting the
        idle row's natural width become a hard minimum is precisely how the
        old tabs pushed the window past 1000px.
        """

        hint = super().minimumSizeHint()
        floor = self._run_button.sizeHint().width() + 6 * theme.SPACE_XXL
        return QSize(min(hint.width(), floor), hint.height())


_RUN_BAR_QSS = f"""
QFrame#{OBJ_RUN_BAR} {{
    background: {theme.SURFACE_TOOLBAR};
    border: none;
    border-top: 1px solid {theme.LINE_STRUCTURAL};
}}
QFrame#{OBJ_RUN_PANEL_HEADER} {{
    background: {theme.SURFACE_TOOLBAR};
    border: none;
    border-bottom: 1px solid {theme.LINE_STRUCTURAL};
}}
QFrame#{OBJ_RUN_PANEL_LOG_BAR} {{
    background: {theme.SURFACE_TABLE_HEADER};
    border: none;
    border-bottom: 1px solid {theme.LINE_STRUCTURAL};
}}
QFrame#{OBJ_SEPARATOR} {{
    background: {theme.LINE_SEPARATOR};
    border: none;
}}
QLabel#{OBJ_RUN_SELECTION} {{
    font-weight: {theme.FONT_WEIGHT_SEMIBOLD};
}}
QLabel#{OBJ_RUN_SELECTION}:disabled {{
    font-weight: {theme.FONT_WEIGHT_NORMAL};
    color: {theme.TEXT_DISABLED};
}}
QLabel#{OBJ_RUN_HINT} {{
    font-size: {theme.FONT_SIZE_META}px;
    color: {theme.TEXT_DISABLED};
}}
QLabel#{OBJ_RUN_META} {{
    color: {theme.TEXT_SECONDARY};
}}
QLabel#{OBJ_RUN_MONO}, QLabel#{OBJ_RUN_COUNTS} {{
    font-family: {theme.FONT_MONO};
    font-size: {theme.FONT_SIZE_META}px;
    color: {theme.TEXT_SECONDARY};
}}
QLabel#{OBJ_RUN_LOG_PATH} {{
    font-family: {theme.FONT_MONO};
    font-size: {theme.FONT_SIZE_META}px;
    color: {theme.TEXT_PRIMARY};
}}
QLabel#{OBJ_RUN_TITLE} {{
    font-weight: {theme.FONT_WEIGHT_SEMIBOLD};
}}
QLabel#{OBJ_RUN_GLYPH} {{
    color: {theme.STATUS_RUNNING};
    font-weight: {theme.FONT_WEIGHT_BOLD};
}}
QToolButton {{
    background: {theme.SURFACE_PAGE};
    border: 1px solid {theme.LINE_STRUCTURAL};
    border-radius: {theme.RADIUS_BUTTON}px;
    padding: {theme.BUTTON_PADDING_V}px {theme.BUTTON_PADDING_H}px;
}}
QToolButton:focus {{
    border: {theme.FOCUS_BORDER_WIDTH}px solid {theme.FOCUS_BORDER_COLOR};
}}
QToolButton::menu-indicator {{
    image: none;
    width: 0px;
}}
"""
