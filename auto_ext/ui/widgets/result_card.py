"""One run's outcome, rendered as a card.

Displays exactly the information the run layer already computes but that
nothing showed the user before:

- ``RunRecord.stages`` -- per-stage status, wall-clock duration, the archived
  log path and ``ToolResult.artifact_paths`` (previously computed and dropped),
- ``RunRecord.results.lvs`` -- the parsed Calibre banner, the discrepancy count,
  the CELL SUMMARY rows and the archived report copy (previously buried in
  ``ToolResult.diagnostics["lvs_report"]`` and read by nobody),
- ``StageRecord.details["failure"]`` -- the
  :class:`~auto_ext.core.failure_class.FailureVerdict` the runner recorded,
  grouped here by class so each class shows its ``next_action`` once.

Nothing here re-derives a verdict the core already reached: classification
comes from :mod:`auto_ext.core.failure_class`, the Calibre Interactive
pre-flight from :mod:`auto_ext.core.handoff`, and CELL SUMMARY parsing from
:mod:`auto_ext.core.checks`. This module owns presentation only.

Layout contract
---------------
The card must never push a large minimum height onto the tab hosting it (the
Project tab already pins the main window at 1056 px, which does not fit on a
1080p screen). Everything below the short header therefore lives inside a
vertical :class:`QSplitter` whose lower half is a :class:`QScrollArea`, so
``minimumSizeHint().height()`` stays independent of how many stages, failures
or artifacts the run produced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, NamedTuple, Sequence

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.checks import (
    CELL_CORRECT,
    CELL_INCORRECT,
    CellSummaryRow,
    LvsReport,
    parse_lvs_report_detailed,
)
from auto_ext.core.errors import AutoExtError
from auto_ext.core.failure_class import (
    Confidence,
    FailureClass,
    FailureVerdict,
    classify_failure,
    next_action_for,
)
from auto_ext.core.handoff import HandoffPlan, plan_calibre_handoff
from auto_ext.core.progress import StageStatus
from auto_ext.core.run_store import RunIndexEntry
from auto_ext.model.run import LvsResult, RunAnnotations, RunRecord, StageRecord
from auto_ext.ui.models import STAGE_DISPLAY, STATUS_COLOR, TASK_DISPLAY

# ---- semantic colors (single source: auto_ext.ui.models.STATUS_COLOR) -------

#: Green: the thing succeeded.
COLOR_PASS = STATUS_COLOR[str(StageStatus.PASSED)]
#: Red: the thing failed.
COLOR_FAIL = STATUS_COLOR[str(StageStatus.FAILED)]
#: Amber: succeeded-with-caveats / interrupted / user deviated.
COLOR_WARN = STATUS_COLOR[str(StageStatus.CANCELLED)]
#: Grey: deliberately not done, or nothing to say.
COLOR_MUTED = STATUS_COLOR[str(StageStatus.SKIPPED)]

_MONO = "font-family: Consolas, 'DejaVu Sans Mono', monospace;"

#: ``StageRecord.details`` key holding ``FailureVerdict.as_dict()``.
DETAILS_FAILURE_KEY = "failure"

#: Group key used for stages the user cancelled. ``FailureClass`` has no member
#: for this on purpose: a cancellation is not a diagnosis.
CANCELLED_KEY = "cancelled"

CANCELLED_TITLE = "Cancelled"

CANCELLED_NEXT_ACTION = (
    "The run was cancelled, so the record stops here. The workarea may hold a "
    "half-written database - re-run this stage before trusting anything "
    "downstream of it."
)

#: Human titles for the classes :mod:`auto_ext.core.failure_class` assigns.
FAILURE_CLASS_TITLES: dict[str, str] = {
    FailureClass.LICENSE_UNAVAILABLE.value: "License unavailable",
    FailureClass.ENVIRONMENT.value: "Environment",
    FailureClass.LVS_MISMATCH.value: "LVS mismatch",
    FailureClass.TOOL_CRASH.value: "Tool crash",
    FailureClass.UNKNOWN.value: "Unclassified",
}

# Item roles on the stage tree.
_ROLE_KIND = Qt.UserRole
_ROLE_PATH = Qt.UserRole + 1
_ROLE_RENDERED = Qt.UserRole + 2

_KIND_STAGE = "stage"
_KIND_ARTIFACT = "artifact"


# ---- pure helpers -----------------------------------------------------------


def format_duration(seconds: float | None) -> str:
    """Human-readable wall clock. ``None`` and negatives render as ``"-"``."""

    if seconds is None or seconds < 0:
        return "-"
    if seconds < 10:
        return f"{seconds:.2f} s"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


def format_timestamp(moment: datetime | None) -> str:
    """UTC, second resolution, no ``T`` separator. ``None`` -> ``"-"``."""

    if moment is None:
        return "-"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def stage_tally(stages: Sequence[StageRecord]) -> tuple[int, int]:
    """``(passed, attempted)`` where attempted excludes SKIPPED stages.

    A skipped stage was never attempted, so counting it in the denominator
    would make an aborted run look worse than it is; the skipped count is
    reported separately by :func:`tally_text`.
    """

    attempted = [s for s in stages if s.status != StageStatus.SKIPPED]
    passed = [s for s in attempted if s.status == StageStatus.PASSED]
    return len(passed), len(attempted)


def tally_text(stages: Sequence[StageRecord]) -> str:
    """``"3/4 stages passed - 1 failed - 1 skipped"``."""

    passed, attempted = stage_tally(stages)
    parts = [f"{passed}/{attempted} stages passed"]
    for status, word in (
        (StageStatus.FAILED, "failed"),
        (StageStatus.CANCELLED, "cancelled"),
        (StageStatus.DRY_RUN, "dry run"),
        (StageStatus.SKIPPED, "skipped"),
    ):
        n = sum(1 for s in stages if s.status == status)
        if n:
            parts.append(f"{n} {word}")
    return " - ".join(parts)


# ---- CELL SUMMARY -----------------------------------------------------------


def parse_cell_summary_line(line: str) -> CellSummaryRow | None:
    """Inverse of :meth:`auto_ext.core.checks.CellSummaryRow.as_line`.

    ``LvsResult.cell_summary`` archives each CELL SUMMARY row as the flat
    string ``"<result> <layout> <source>"``; this reads one back. Anything
    that is not a three-token row with a known verdict yields ``None`` -- in
    particular the bare ``"INCORRECT"`` verdict a caller may have stored, which
    carries no cell name at all (see :func:`unnamed_mismatch_count`).
    """

    parts = str(line).split()
    if len(parts) != 3:
        return None
    verdict = parts[0].upper()
    if verdict not in (CELL_CORRECT, CELL_INCORRECT):
        return None
    return CellSummaryRow(result=verdict, layout=parts[1], source=parts[2])


def read_cell_summary(report: Path | None) -> list[str]:
    """Re-read the CELL SUMMARY rows out of an archived LVS report.

    Used only when the record itself carries no ``cell_summary`` (an older
    run, or one written before the rows were archived). Any unreadable or
    unclassifiable report yields ``[]`` -- a card must never fail to draw
    because a report went missing.
    """

    if report is None or not report.is_file():
        return []
    try:
        return parse_lvs_report_detailed(report).cell_summary_lines()
    except (AutoExtError, OSError):
        return []


def mismatched_cells(rows: Iterable[str]) -> list[str]:
    """Layout-side names of the INCORRECT rows, de-duplicated, order kept."""

    seen: set[str] = set()
    out: list[str] = []
    for line in rows:
        row = parse_cell_summary_line(line)
        if row is None or row.passed:
            continue
        if row.name not in seen:
            seen.add(row.name)
            out.append(row.name)
    return out


def unnamed_mismatch_count(rows: Iterable[str]) -> int:
    """Number of INCORRECT rows that carry no cell name."""

    return sum(1 for row in rows if str(row).strip().upper() == CELL_INCORRECT)


def cell_summary_rows(lvs: LvsResult | None, report: Path | None) -> list[str]:
    """The run's CELL SUMMARY rows: archived first, re-read from disk second."""

    if lvs is None:
        return []
    if lvs.cell_summary:
        return list(lvs.cell_summary)
    return read_cell_summary(report)


def as_lvs_report(lvs: LvsResult | None, rows: Sequence[str]) -> LvsReport | None:
    """Rebuild the ``checks`` view of the result so the classifier can use it."""

    if lvs is None:
        return None
    cells = tuple(
        row for row in (parse_cell_summary_line(line) for line in rows) if row
    )
    return LvsReport(
        passed=lvs.passed,
        banner=lvs.banner,
        discrepancies=lvs.discrepancies,
        source=Path(lvs.source_path or ""),
        cells=cells,
        cell_summary_present=bool(cells),
    )


# ---- failure grouping -------------------------------------------------------


class FailureGroup(NamedTuple):
    """One failure class, the stages in it, and the single next action."""

    key: str
    title: str
    next_action: str
    stages: list[StageRecord]
    verdicts: list[FailureVerdict]


def verdict_from_details(details: dict[str, Any] | None) -> FailureVerdict | None:
    """Read back a :meth:`FailureVerdict.as_dict` bag from ``StageRecord.details``.

    Returns ``None`` when the key is absent or the payload is not the shape
    ``failure_class.py`` writes -- a malformed bag must degrade to "classify it
    here", never to a traceback in a paint path.
    """

    raw = (details or {}).get(DETAILS_FAILURE_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        failure_class = FailureClass(str(raw["failure_class"]))
        confidence = Confidence(str(raw.get("confidence", Confidence.NONE.value)))
    except (KeyError, ValueError):
        return None
    reason = raw.get("reason")
    evidence = raw.get("evidence")
    signature_id = raw.get("signature_id")
    next_action = raw.get("next_action")
    return FailureVerdict(
        failure_class=failure_class,
        confidence=confidence,
        reason=str(reason) if reason else "",
        next_action=str(next_action) if next_action else next_action_for(failure_class),
        evidence=str(evidence) if evidence else None,
        signature_id=str(signature_id) if signature_id else None,
    )


def stage_verdict(
    stage: StageRecord,
    record: RunRecord | None = None,
    *,
    run_dir: Path | None = None,
) -> FailureVerdict:
    """The verdict for one failed stage: recorded if present, else classified.

    The runner is expected to store its verdict under
    ``StageRecord.details["failure"]``; that value always wins, because it was
    reached with facts (a live ``LvsReport``, a render error) that the record
    does not necessarily keep. Only when it is absent does this fall back to
    :func:`auto_ext.core.failure_class.classify_failure` over what the record
    does carry -- including the archived stage log, so a signature match is
    still possible months later.
    """

    recorded = verdict_from_details(stage.details)
    if recorded is not None:
        return recorded

    lvs = record.results.lvs if record is not None else None
    report: LvsReport | None = None
    if lvs is not None and stage.stage == "calibre":
        report = as_lvs_report(lvs, cell_summary_rows(lvs, None))

    return classify_failure(
        stage=stage.stage,
        exit_code=stage.exit_code,
        lvs=report,
        log_path=resolve_run_relative(run_dir, stage.log_path),
    )


def group_failures(
    record: RunRecord, *, run_dir: Path | None = None
) -> list[FailureGroup]:
    """Failing / cancelled stages bucketed by failure class, first-seen order.

    Cancelled stages get their own bucket rather than being classified: the
    exit code of a killed process describes the kill, not the problem.
    """

    order: list[str] = []
    stages: dict[str, list[StageRecord]] = {}
    verdicts: dict[str, list[FailureVerdict]] = {}
    titles: dict[str, str] = {}
    actions: dict[str, str] = {}

    for stage in record.stages:
        if stage.status == StageStatus.CANCELLED:
            key, title, action = CANCELLED_KEY, CANCELLED_TITLE, CANCELLED_NEXT_ACTION
            verdict = None
        elif stage.status == StageStatus.FAILED:
            verdict = stage_verdict(stage, record, run_dir=run_dir)
            key = verdict.failure_class.value
            title = FAILURE_CLASS_TITLES.get(key, key.replace("_", " ").capitalize())
            action = next_action_for(verdict.failure_class)
        else:
            continue

        if key not in stages:
            order.append(key)
            stages[key] = []
            verdicts[key] = []
            titles[key] = title
            actions[key] = action
        stages[key].append(stage)
        if verdict is not None:
            verdicts[key].append(verdict)

    return [
        FailureGroup(
            key=key,
            title=titles[key],
            next_action=actions[key],
            stages=stages[key],
            verdicts=verdicts[key],
        )
        for key in order
    ]


# ---- comparison against the previous run of the same DUT --------------------


def discrepancy_delta_text(
    current: int | None, previous: int | None
) -> tuple[str, str]:
    """``(text, color)`` describing the change in discrepancy count."""

    if current is None and previous is None:
        return ("no discrepancy count on either run", COLOR_MUTED)
    if previous is None:
        return ("no count recorded on the previous run", COLOR_MUTED)
    if current is None:
        return (f"previous run reported {previous}", COLOR_MUTED)
    if current < previous:
        return (f"{previous} -> {current} (down {previous - current})", COLOR_PASS)
    if current > previous:
        return (f"{previous} -> {current} (up {current - previous})", COLOR_FAIL)
    return (f"unchanged at {current}", COLOR_WARN)


# ---- path helpers -----------------------------------------------------------


def resolve_run_relative(run_dir: Path | None, relative: str | None) -> Path | None:
    """Join a run-relative POSIX path (``"logs/calibre.log"``) onto ``run_dir``."""

    if run_dir is None or not relative:
        return None
    return Path(run_dir) / PurePosixPath(relative)


def _clear_layout(layout: Any) -> None:
    """Delete every child widget/layout so a section can be rebuilt."""

    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            _clear_layout(child)
            child.deleteLater()


class ResultCard(QWidget):
    """The result of one run: tally, per-stage rows, LVS, failures, outputs."""

    #: Emitted with the absolute :class:`Path` of a stage log the user asked
    #: to view. The host decides whether that means a viewer or the OS handler.
    log_requested = pyqtSignal(object)
    #: Emitted with the absolute :class:`Path` of an artifact / rendered file
    #: / directory the user asked to open.
    artifact_requested = pyqtSignal(object)
    #: Emitted with the displayed :class:`~auto_ext.model.run.RunRecord` when
    #: the user presses "Open in Calibre Interactive". The payload is the
    #: record with ``run_dir`` filled in, ready for
    #: :func:`auto_ext.core.handoff.launch_calibre_interactive`.
    handoff_requested = pyqtSignal(object)
    #: Emitted with the text that was just put on the clipboard.
    copy_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._record: RunRecord | None = None
        self._run_dir: Path | None = None
        self._annotations: RunAnnotations | None = None
        self._previous: RunIndexEntry | None = None
        self._plan: HandoffPlan | None = None

        self._build_ui()
        self.clear()

    # ---- public API ---------------------------------------------------

    @property
    def record(self) -> RunRecord | None:
        """The currently displayed record, or ``None``."""

        return self._record

    @property
    def run_dir(self) -> Path | None:
        """Absolute run directory backing the displayed record."""

        return self._run_dir

    @property
    def handoff_plan(self) -> HandoffPlan | None:
        """The pre-flighted Calibre Interactive plan for the displayed run."""

        return self._plan

    def set_run(
        self,
        record: RunRecord,
        *,
        run_dir: Path | None = None,
        annotations: RunAnnotations | None = None,
        previous: RunIndexEntry | None = None,
    ) -> None:
        """Display ``record``.

        ``run_dir`` resolves the run-relative ``log_path`` / ``rendered_path``
        / ``archived_path`` values; it falls back to ``record.run_dir``.
        ``previous`` is the most recent earlier run of the same DUT
        (:func:`auto_ext.core.run_store.find_previous_run`), used for the
        discrepancy comparison.
        """

        self._record = record
        if run_dir is not None:
            self._run_dir = Path(run_dir)
        elif record.run_dir:
            self._run_dir = Path(record.run_dir)
        else:
            self._run_dir = None
        self._annotations = annotations
        self._previous = previous

        self._placeholder.setVisible(False)
        self._body.setVisible(True)
        self._fill_header()
        self._fill_stages()
        self._fill_lvs()
        self._fill_failures()
        self._fill_artifacts()
        self._fill_handoff()
        self._update_log_button()

    def clear(self) -> None:
        """Drop the displayed record and show the neutral placeholder."""

        self._record = None
        self._run_dir = None
        self._annotations = None
        self._previous = None
        self._plan = None
        self.show_message("Select a run on the left to see its result.")

    def show_message(self, text: str) -> None:
        """Replace the card body with a single centered line of guidance.

        The stage table is emptied too: a hidden body holding the previous
        run's rows is a trap for anything that reads the tree without checking
        which record is displayed.
        """

        self._stage_tree.clear()
        self._placeholder.setText(text)
        self._placeholder.setVisible(True)
        self._body.setVisible(False)

    def handoff_record(self) -> RunRecord | None:
        """The displayed record with ``run_dir`` guaranteed when known.

        ``core.handoff`` resolves the archived runset through
        ``record.run_dir``; a record loaded from a directory that was moved
        since it was written would otherwise fall back to the workarea path.
        """

        record = self._record
        if record is None:
            return None
        if not record.run_dir and self._run_dir is not None:
            return record.model_copy(update={"run_dir": str(self._run_dir)})
        return record

    # ---- construction -------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._placeholder = QLabel("", self)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet(f"color: {COLOR_MUTED};")
        root.addWidget(self._placeholder)

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._body, stretch=1)

        # -- header ------------------------------------------------------
        # Every header label wraps. A non-wrapping QLabel reports its whole
        # rendered text width as its *minimum*, which is how a long cell path
        # or a five-part tally line ends up dictating the main window's
        # minimum width. Wrapping drops that minimum to the widest single word.
        title_row = QHBoxLayout()
        self._title = QLabel("", self._body)
        self._title.setStyleSheet("font-weight: bold; font-size: 14px;")
        self._title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._title.setWordWrap(True)
        self._badge = QLabel("", self._body)
        self._badge.setStyleSheet("font-weight: bold;")
        title_row.addWidget(self._title, stretch=1)
        title_row.addWidget(self._badge, stretch=0)
        body.addLayout(title_row)

        self._subtitle = QLabel("", self._body)
        self._subtitle.setStyleSheet(f"{_MONO} color: #444;")
        self._subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._subtitle.setWordWrap(True)
        body.addWidget(self._subtitle)

        self._meta = QLabel("", self._body)
        self._meta.setStyleSheet("color: #444;")
        self._meta.setWordWrap(True)
        body.addWidget(self._meta)

        self._note = QLabel("", self._body)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"color: {COLOR_MUTED}; font-style: italic;")
        body.addWidget(self._note)

        # -- splitter: stage table on top, everything else scrolls -------
        splitter = QSplitter(Qt.Vertical, self._body)

        stages_pane = QWidget(splitter)
        stages_layout = QVBoxLayout(stages_pane)
        stages_layout.setContentsMargins(0, 0, 0, 0)

        self._stage_tree = QTreeWidget(stages_pane)
        self._stage_tree.setHeaderLabels(["stage", "status", "duration", "log"])
        self._stage_tree.setColumnWidth(0, 180)
        self._stage_tree.setColumnWidth(1, 110)
        self._stage_tree.setColumnWidth(2, 90)
        self._stage_tree.itemDoubleClicked.connect(self._on_stage_double_click)
        self._stage_tree.itemSelectionChanged.connect(self._update_log_button)
        self._stage_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._stage_tree.customContextMenuRequested.connect(self._on_stage_menu)
        stages_layout.addWidget(self._stage_tree, stretch=1)

        log_row = QHBoxLayout()
        self._open_log_btn = QPushButton("Open log", stages_pane)
        self._open_log_btn.setEnabled(False)
        self._open_log_btn.clicked.connect(self._emit_selected_log)
        hint = QLabel(
            "Double-click a stage row for its log; expand a row for the "
            "workarea artifacts it produced.",
            stages_pane,
        )
        hint.setStyleSheet(f"color: {COLOR_MUTED};")
        hint.setWordWrap(True)
        log_row.addWidget(self._open_log_btn)
        log_row.addWidget(hint, stretch=1)
        stages_layout.addLayout(log_row)

        splitter.addWidget(stages_pane)

        scroll = QScrollArea(splitter)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        details = QWidget(scroll)
        self._details_layout = QVBoxLayout(details)
        self._details_layout.setContentsMargins(0, 0, 0, 0)

        self._lvs_group = QGroupBox("LVS", details)
        lvs_layout = QVBoxLayout(self._lvs_group)
        self._lvs_body = QLabel("", self._lvs_group)
        self._lvs_body.setWordWrap(True)
        self._lvs_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lvs_layout.addWidget(self._lvs_body)
        self._details_layout.addWidget(self._lvs_group)

        self._failures_group = QGroupBox("Failures", details)
        self._failures_layout = QVBoxLayout(self._failures_group)
        self._details_layout.addWidget(self._failures_group)

        self._artifacts_group = QGroupBox("Outputs", details)
        self._artifacts_layout = QGridLayout(self._artifacts_group)
        self._artifacts_layout.setColumnStretch(1, 1)
        self._details_layout.addWidget(self._artifacts_group)

        self._handoff_btn = QPushButton("Open in Calibre Interactive", details)
        self._handoff_btn.clicked.connect(self._emit_handoff)
        self._details_layout.addWidget(self._handoff_btn)

        self._details_layout.addStretch(1)
        scroll.setWidget(details)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        self._splitter = splitter

        body.addWidget(splitter, stretch=1)

    # ---- header -------------------------------------------------------

    def _fill_header(self) -> None:
        record = self._record
        assert record is not None
        annots = self._annotations
        name = (annots.display_name if annots else None) or record.default_display_name
        self._title.setText(name)

        overall = str(record.overall)
        self._badge.setText(TASK_DISPLAY.get(overall, overall).upper())
        self._badge.setStyleSheet(
            f"font-weight: bold; color: {STATUS_COLOR.get(overall, COLOR_MUTED)};"
        )

        dut = record.dut
        self._subtitle.setText(
            f"{dut.library} / {dut.cell} - layout {dut.layout_view} vs "
            f"{dut.source_view} - recipe {record.recipe.label}"
        )

        meta = [
            format_timestamp(record.created_at),
            format_duration(record.duration_s),
            tally_text(record.stages),
        ]
        if record.dry_run:
            meta.append("dry run")
        self._meta.setText(" - ".join(meta))

        note = (annots.note if annots else None) or ""
        self._note.setText(note)
        self._note.setVisible(bool(note))

    # ---- stage table --------------------------------------------------

    def _fill_stages(self) -> None:
        record = self._record
        assert record is not None
        tree = self._stage_tree
        tree.clear()
        for stage in record.stages:
            log_path = resolve_run_relative(self._run_dir, stage.log_path)
            rendered = resolve_run_relative(self._run_dir, stage.rendered_path)
            status = str(stage.status)
            item = QTreeWidgetItem(
                [
                    stage.key,
                    STAGE_DISPLAY.get(status, status),
                    format_duration(stage.duration_s),
                    stage.log_path or "-",
                ]
            )
            item.setData(0, _ROLE_KIND, _KIND_STAGE)
            if log_path is not None:
                item.setData(0, _ROLE_PATH, str(log_path))
            if rendered is not None:
                item.setData(0, _ROLE_RENDERED, str(rendered))
            color = STATUS_COLOR.get(status)
            if color:
                item.setForeground(1, QColor(color))
            tooltip_bits = []
            if stage.skip_reason:
                tooltip_bits.append(stage.skip_reason)
            if stage.error:
                tooltip_bits.append(stage.error)
            if stage.exit_code is not None:
                tooltip_bits.append(f"exit code {stage.exit_code}")
            if tooltip_bits:
                item.setToolTip(1, "\n".join(tooltip_bits))
            for artifact in stage.artifacts:
                child = QTreeWidgetItem(
                    [Path(artifact).name, "artifact", "", artifact]
                )
                child.setData(0, _ROLE_KIND, _KIND_ARTIFACT)
                child.setData(0, _ROLE_PATH, artifact)
                child.setForeground(1, QColor(COLOR_MUTED))
                child.setToolTip(0, artifact)
                item.addChild(child)
            tree.addTopLevelItem(item)

    def _selected_stage_log(self) -> Path | None:
        items = self._stage_tree.selectedItems()
        if not items:
            return None
        item = items[0]
        if item.data(0, _ROLE_KIND) != _KIND_STAGE:
            return None
        raw = item.data(0, _ROLE_PATH)
        if not raw:
            return None
        path = Path(str(raw))
        return path if path.is_file() else None

    def _update_log_button(self) -> None:
        path = self._selected_stage_log()
        self._open_log_btn.setEnabled(path is not None)
        self._open_log_btn.setToolTip(
            str(path) if path is not None else "Select a stage that produced a log."
        )

    def _emit_selected_log(self) -> None:
        path = self._selected_stage_log()
        if path is not None:
            self.log_requested.emit(path)

    def _on_stage_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        raw = item.data(0, _ROLE_PATH)
        if not raw:
            return
        path = Path(str(raw))
        if item.data(0, _ROLE_KIND) == _KIND_ARTIFACT:
            self.artifact_requested.emit(path)
            return
        if path.is_file():
            self.log_requested.emit(path)

    def _on_stage_menu(self, pos: QPoint) -> None:
        """Right-click menu on a stage or artifact row (deferred popup)."""

        item = self._stage_tree.itemAt(pos)
        if item is None:
            return
        kind = item.data(0, _ROLE_KIND)
        menu = QMenu(self._stage_tree)

        if kind == _KIND_ARTIFACT:
            raw = item.data(0, _ROLE_PATH)
            path = Path(str(raw)) if raw else None
            act_open = QAction("Open artifact", menu)
            if path is None or not path.exists():
                act_open.setEnabled(False)
                act_open.setToolTip(
                    "The workarea copy is gone - a later run of this cell "
                    "overwrote it."
                )
            else:
                act_open.setToolTip(str(path))
                act_open.triggered.connect(
                    lambda _c=False, p=path: self.artifact_requested.emit(p)
                )
            menu.addAction(act_open)
            self._add_copy_action(menu, str(raw) if raw else "")
        else:
            raw_log = item.data(0, _ROLE_PATH)
            log_path = Path(str(raw_log)) if raw_log else None
            act_log = QAction("Open log file", menu)
            if log_path is None or not log_path.is_file():
                act_log.setEnabled(False)
                act_log.setToolTip("This stage produced no log.")
            else:
                act_log.setToolTip(str(log_path))
                act_log.triggered.connect(
                    lambda _c=False, p=log_path: self.log_requested.emit(p)
                )
            menu.addAction(act_log)

            raw_rendered = item.data(0, _ROLE_RENDERED)
            rendered = Path(str(raw_rendered)) if raw_rendered else None
            act_rendered = QAction("Open rendered input", menu)
            if rendered is None or not rendered.is_file():
                act_rendered.setEnabled(False)
                act_rendered.setToolTip("This stage archived no rendered input.")
            else:
                act_rendered.setToolTip(str(rendered))
                act_rendered.triggered.connect(
                    lambda _c=False, p=rendered: self.artifact_requested.emit(p)
                )
            menu.addAction(act_rendered)
            self._add_copy_action(menu, str(raw_log) if raw_log else "")

        # X11 delivers the context-menu event on button *press*, so a
        # synchronous exec_() is dismissed by the following release and the
        # user has to right-click twice. Defer the popup one event-loop tick.
        global_pos = self._stage_tree.viewport().mapToGlobal(pos)
        QTimer.singleShot(0, lambda: menu.exec_(global_pos))

    def _add_copy_action(self, menu: QMenu, text: str) -> None:
        action = QAction("Copy path", menu)
        if not text:
            action.setEnabled(False)
        else:
            action.triggered.connect(lambda _c=False, t=text: self.copy_text(t))
        menu.addAction(action)

    def copy_text(self, text: str) -> None:
        """Put ``text`` on the clipboard and announce it via :attr:`copy_requested`."""

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.copy_requested.emit(text)

    # ---- LVS ----------------------------------------------------------

    def lvs_report_path(self) -> Path | None:
        """The readable LVS report: the run-dir archive, else the workarea copy."""

        record = self._record
        if record is None or record.results.lvs is None:
            return None
        lvs = record.results.lvs
        archived = resolve_run_relative(self._run_dir, lvs.archived_path)
        if archived is not None and archived.is_file():
            return archived
        if lvs.source_path:
            source = Path(lvs.source_path)
            if source.is_file():
                return source
        return None

    def _fill_lvs(self) -> None:
        record = self._record
        assert record is not None
        lvs = record.results.lvs
        if lvs is None:
            self._lvs_body.setText(
                "<span style='color:%s'>No LVS result recorded for this run."
                "</span>" % COLOR_MUTED
            )
            return

        color = COLOR_PASS if lvs.passed else COLOR_FAIL
        verdict = "passed" if lvs.passed else "failed"
        head = [
            "<b style='color:%s'>LVS %s</b>" % (color, verdict),
            "banner <b>%s</b>" % (lvs.banner or "(none)"),
        ]
        if lvs.discrepancies is None:
            head.append("discrepancies <b>not reported</b>")
        else:
            disc_color = COLOR_PASS if lvs.discrepancies == 0 else COLOR_FAIL
            head.append(
                "discrepancies <b style='color:%s'>%d</b>"
                % (disc_color, lvs.discrepancies)
            )
        html = [" | ".join(head)]

        rows = cell_summary_rows(lvs, self.lvs_report_path())
        names = mismatched_cells(rows)
        if names:
            html.append(
                "<div>Mismatched cells (%d): <span style='%s color:%s'>%s</span>"
                "</div>" % (len(names), _MONO, COLOR_FAIL, ", ".join(names))
            )
        else:
            unnamed = unnamed_mismatch_count(rows)
            if unnamed:
                html.append(
                    "<div><span style='color:%s'>%d CELL SUMMARY row(s) "
                    "INCORRECT; the report carries no cell names.</span></div>"
                    % (COLOR_FAIL, unnamed)
                )
            elif rows:
                html.append(
                    "<div><span style='color:%s'>CELL SUMMARY: all %d row(s) "
                    "CORRECT.</span></div>" % (COLOR_PASS, len(rows))
                )

        html.append(self._comparison_html())
        self._lvs_body.setText("".join(html))

    def _comparison_html(self) -> str:
        record = self._record
        assert record is not None
        previous = self._previous
        if previous is None:
            return (
                "<div><span style='color:%s'>No earlier run of this cell to "
                "compare against.</span></div>" % COLOR_MUTED
            )
        current = record.results.lvs.discrepancies if record.results.lvs else None
        text, color = discrepancy_delta_text(current, previous.lvs_discrepancies)
        return (
            "<div>vs previous run <span style='%s'>%s</span> (%s): "
            "<span style='color:%s'>%s</span></div>"
            % (
                _MONO,
                previous.run_id,
                format_timestamp(previous.created_at),
                color,
                text,
            )
        )

    # ---- failures -----------------------------------------------------

    def _fill_failures(self) -> None:
        record = self._record
        assert record is not None
        _clear_layout(self._failures_layout)
        groups = group_failures(record, run_dir=self._run_dir)
        if not groups:
            self._failures_group.setVisible(False)
            return
        self._failures_group.setVisible(True)
        for group in groups:
            count = len(group.stages)
            head = QLabel(
                "<b style='color:%s'>%s</b> (%d stage%s)"
                % (COLOR_FAIL, group.title, count, "" if count == 1 else "s"),
                self._failures_group,
            )
            head.setWordWrap(True)
            self._failures_layout.addWidget(head)

            for index, stage in enumerate(group.stages):
                verdict = (
                    group.verdicts[index] if index < len(group.verdicts) else None
                )
                detail = (
                    (verdict.reason if verdict else "")
                    or stage.error
                    or stage.skip_reason
                    or ""
                )
                suffix = f" - {detail}" if detail else ""
                row = QLabel(f"{stage.key}{suffix}", self._failures_group)
                row.setWordWrap(True)
                row.setStyleSheet(f"{_MONO} color: #444; margin-left: 12px;")
                self._failures_layout.addWidget(row)
                if verdict is not None and verdict.next_action != group.next_action:
                    # A signature overrode the class default: show it here so
                    # the more specific advice is not lost under the group's.
                    override = QLabel(
                        f"Next: {verdict.next_action}", self._failures_group
                    )
                    override.setWordWrap(True)
                    override.setStyleSheet(
                        f"color: {COLOR_WARN}; margin-left: 24px;"
                    )
                    self._failures_layout.addWidget(override)

            action = QLabel(f"Next: {group.next_action}", self._failures_group)
            action.setWordWrap(True)
            action.setStyleSheet(f"color: {COLOR_WARN}; margin-left: 12px;")
            self._failures_layout.addWidget(action)

    # ---- outputs ------------------------------------------------------

    def _fill_artifacts(self) -> None:
        record = self._record
        assert record is not None
        _clear_layout(self._artifacts_layout)
        row = 0

        dspf = Path(record.dspf_path) if record.dspf_path else None
        row = self._add_output_row(
            row,
            "DSPF",
            str(dspf) if dspf else "(not configured for this run)",
            open_path=dspf,
        )

        dut = record.dut
        if dut.out_file:
            triple = f"{dut.library} / {dut.cell} / {dut.out_file}"
            row = self._add_output_row(
                row, "Extracted view", triple, copy_text=triple
            )
        else:
            row = self._add_output_row(
                row, "Extracted view", "(no out_file configured)"
            )

        report = self.lvs_report_path()
        lvs = record.results.lvs
        if report is not None:
            row = self._add_output_row(
                row, "LVS report", str(report), open_path=report
            )
        elif lvs is not None and lvs.source_path:
            row = self._add_output_row(
                row,
                "LVS report",
                f"{lvs.source_path} (gone - overwritten by a later run)",
            )
        else:
            row = self._add_output_row(row, "LVS report", "(none)")

        row = self._add_output_row(
            row,
            "Workarea",
            record.workspace_dir,
            open_path=Path(record.workspace_dir),
        )
        if self._run_dir is not None:
            row = self._add_output_row(
                row, "Run directory", str(self._run_dir), open_path=self._run_dir
            )

    def _add_output_row(
        self,
        row: int,
        label: str,
        value: str,
        *,
        open_path: Path | None = None,
        copy_text: str | None = None,
    ) -> int:
        grid = self._artifacts_layout
        name = QLabel(label, self._artifacts_group)
        name.setStyleSheet("color: #444;")
        grid.addWidget(name, row, 0, alignment=Qt.AlignTop)

        value_label = QLabel(value, self._artifacts_group)
        value_label.setWordWrap(True)
        value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value_label.setStyleSheet(_MONO)
        grid.addWidget(value_label, row, 1)

        if open_path is not None:
            button = QPushButton("Open", self._artifacts_group)
            exists = open_path.exists()
            button.setEnabled(exists)
            button.setToolTip(
                str(open_path) if exists else f"Not on this host: {open_path}"
            )
            button.clicked.connect(
                lambda _c=False, p=open_path: self.artifact_requested.emit(p)
            )
            grid.addWidget(button, row, 2, alignment=Qt.AlignTop)
        elif copy_text is not None:
            button = QPushButton("Copy", self._artifacts_group)
            button.setToolTip("Copy the library / cell / view triple.")
            button.clicked.connect(lambda _c=False, t=copy_text: self.copy_text(t))
            grid.addWidget(button, row, 2, alignment=Qt.AlignTop)
        return row + 1

    # ---- Calibre Interactive -------------------------------------------

    def _fill_handoff(self) -> None:
        """Pre-flight the hand-off and set the button state from the plan.

        :func:`auto_ext.core.handoff.plan_calibre_handoff` never raises and
        reports every blocking problem at once, so the disabled tooltip always
        states the actual reason: no calibre stage, an archived runset that has
        been deleted, a workarea that is gone, or calibre not being on PATH.
        """

        record = self.handoff_record()
        plan = plan_calibre_handoff(record) if record is not None else None
        self._plan = plan

        enabled = plan is not None and plan.ok
        self._handoff_btn.setEnabled(bool(enabled))
        if plan is None:
            self._handoff_btn.setToolTip("No run selected.")
            return
        if enabled:
            lines = [plan.command_line, *plan.warnings]
        else:
            lines = [plan.reason or "Calibre Interactive cannot be opened."]
        self._handoff_btn.setToolTip("\n".join(lines))

    def _emit_handoff(self) -> None:
        record = self.handoff_record()
        if record is not None:
            self.handoff_requested.emit(record)
