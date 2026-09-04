"""One run's outcome, rendered as a card (design canvas artboards 1c / 1d / 1e).

The card answers one question in one screen: *do I have to re-check this by
hand?* For an RFIC verification engineer the answer is normally "yes" -- the
habit is to re-open Calibre Interactive after every batch LVS "just in case",
because a batch verdict is not something you sign off on. This card is built
to make that habit unnecessary, and where it is not, to make it one click:

* the **LVS banner** and the **discrepancy count** are shown as the two
  headline numbers, mono 20/700, exactly as the canvas draws them, with the
  banner marked as the authoritative one (:mod:`auto_ext.core.checks` treats a
  clean count under an ``INCORRECT`` banner as a failure, and says so here),
* the count is shown **against the previous run of the same DUT**, because
  "3 discrepancies, down from 17" is a different decision from "3
  discrepancies" (:func:`auto_ext.core.checks.compare_discrepancies` over
  :func:`auto_ext.core.run_store.find_previous_run`),
* the **CELL SUMMARY** names the sub-cells that did not match, so the user
  knows where to look before opening anything,
* **Open in Calibre Interactive** re-opens the exact frozen runset this run
  used (:mod:`auto_ext.core.handoff`), and the command line it will run is
  printed underneath so it can also be pasted into a shell,
* every failure is labelled with its three-letter code and grouped by **who
  has to act** (:mod:`auto_ext.ui.widgets.failure_chip`), so an environment
  problem is never mistaken for a layout problem.

Nothing here re-derives a verdict the core already reached: classification
comes from :mod:`auto_ext.core.failure_class`, the Calibre Interactive
pre-flight from :mod:`auto_ext.core.handoff`, LVS parsing and the discrepancy
comparison from :mod:`auto_ext.core.checks`. This module owns presentation
only.

Layout contract
---------------
The card must never push a large minimum height onto the screen hosting it:
the window floor is 940x560 px. Everything below the fixed header therefore
lives inside a :class:`QScrollArea`, the per-stage log tree is collapsed by
default, and every path is a
:class:`~auto_ext.ui.widgets.failure_chip.PathLabel` that elides instead of
widening. ``minimumSizeHint()`` is asserted in the tests and is independent
of how many stages, failures or outputs the run produced.

Assumptions
-----------
Collected here rather than scattered through the code.

* **The canvas draws a per-discrepancy table (class / net / detail / seen in);
  this card cannot.** ``LvsReport`` carries the banner, the count and the
  CELL SUMMARY rows -- Calibre's per-discrepancy detail is never parsed by
  :mod:`auto_ext.core.checks`. The card shows the sub-cell list it does have
  and puts the report one click away rather than inventing rows.
* **The canvas draws an Extraction band (corner / temp / type / net counts);
  this card cannot.** ``RunRecord`` records none of those four as data. The
  recipe label is shown instead, and the gap is a ``RecipeSnapshot`` /
  ``RunResults`` question, not a UI one.
* **Arrows are ASCII.** The canvas draws the delta as ``17 -> 3`` with a
  U+2192 arrow; the project glyph whitelist does not list it, so the card
  uses ``->`` and keeps only the whitelisted ``▼`` / ``▴`` triangles.
* **"Manual edits applied"** counts hunks whose status is ``clean`` or
  ``shifted`` -- the two that really changed the rendered file. ``absorbed``,
  ``noop`` and ``disabled`` hunks changed nothing and are not advertised.
* **A stage the record never mentions** is reported as "not started" when the
  run requested it and "off in recipe" when it did not. A record with an
  empty ``requested_stages`` (written before that field was populated) gets
  no chip for it at all, rather than a guessed one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, NamedTuple, Sequence

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.checks import (
    CELL_CORRECT,
    CELL_INCORRECT,
    CellSummaryRow,
    DiscrepancyDelta,
    DiscrepancyTrend,
    LvsReport,
    compare_discrepancies,
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
from auto_ext.core.patch_models import PatchStatus
from auto_ext.core.progress import StageStatus, TaskStatus
from auto_ext.core.run_store import RunIndexEntry
from auto_ext.model.run import LvsResult, RunAnnotations, RunRecord, StageRecord
from auto_ext.ui import theme
from auto_ext.ui.models import STAGE_DISPLAY, TASK_DISPLAY
from auto_ext.ui.widgets.failure_chip import (
    ACTOR_SUBTITLES,
    ACTOR_TITLES,
    CHIP_TONE_FAILED,
    CHIP_TONE_MUTED,
    CHIP_TONE_PASSED,
    CHIP_TONE_PLAIN,
    CHIP_TONE_WARNING,
    CODE_CANCELLED,
    CODE_CONFIG,
    CODE_CRASH,
    CODE_LICENSE,
    CODE_LVS,
    CODE_UNKNOWN,
    Chip,
    FailureChip,
    PathLabel,
    actor_for,
    code_for,
    sort_key,
)

# ---- semantic colors (single source: auto_ext.ui.theme, itself derived from
# ---- auto_ext.ui.models.STATUS_COLOR) --------------------------------------

#: Green: the thing succeeded.
COLOR_PASS = theme.STATUS_PASSED
#: Red: the thing failed.
COLOR_FAIL = theme.STATUS_FAILED
#: Amber: succeeded-with-caveats / interrupted / user deviated.
COLOR_WARN = theme.STATUS_WARNING
#: Grey: deliberately not done, or nothing to say.
COLOR_MUTED = theme.STATUS_SKIPPED

_MONO = f"font-family: {theme.FONT_MONO};"

#: ``StageRecord.details`` key holding ``FailureVerdict.as_dict()``.
DETAILS_FAILURE_KEY = "failure"

#: Group key used for stages the user cancelled. ``FailureClass`` has no member
#: for this on purpose: a cancellation is not a diagnosis. Must equal
#: :data:`auto_ext.ui.widgets.failure_chip.CANCELLED_KEY`.
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

#: The five-stage pipeline, in execution order. Used to lay the stage chips
#: out and to decide which stage never started.
CANONICAL_STAGES: tuple[str, ...] = ("si", "strmout", "calibre", "quantus", "jivaro")

#: Card header fill per overall verdict (canvas 1c / 1d). Two very light
#: washes of the status hue; every other verdict keeps the neutral toolbar
#: surface, because amber and blue washes at this size read as noise.
HEADER_TINT: dict[str, str] = {
    str(TaskStatus.PASSED): "#f2f7f2",
    str(TaskStatus.FAILED): "#fbf2f2",
}

#: Improvement / regression markers. Both are on the project glyph whitelist.
GLYPH_DOWN = "▼"
GLYPH_UP = "▴"

#: Disclosure markers for the "log per stage" toggle.
GLYPH_COLLAPSED = "▾"
GLYPH_EXPANDED = "▴"

#: Minimum width of the two headline columns of the LVS band (canvas 1d draws
#: them at 232px). Their content is fixed-length, so this only stops the two
#: captions from wrapping into four lines each in a narrow card.
LVS_COLUMN_WIDTH = 180

#: How long the LVS band stays washed after "Show N discrepancies". Long
#: enough to be seen after the eye has moved, short enough that it cannot be
#: mistaken for a persistent state.
LVS_HIGHLIGHT_MS = 1600

#: Hunk statuses that really altered the rendered file.
APPLIED_PATCH_STATUSES: frozenset[PatchStatus] = frozenset(
    {PatchStatus.CLEAN, PatchStatus.SHIFTED}
)

# Item roles on the stage tree.
_ROLE_KIND = Qt.UserRole
_ROLE_PATH = Qt.UserRole + 1
_ROLE_RENDERED = Qt.UserRole + 2

_KIND_STAGE = "stage"
_KIND_ARTIFACT = "artifact"


# ---- formatting -------------------------------------------------------------


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

    @property
    def code(self) -> str:
        """The three-letter code for this group."""

        return code_for(self.key)

    @property
    def actor(self) -> str:
        """Which "who has to act" bucket this group belongs to."""

        return actor_for(self.code)


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


def sort_failure_groups(groups: Sequence[FailureGroup]) -> list[FailureGroup]:
    """Order failure groups by *who has to act* (canvas 1e).

    Environment problems the user can clear without touching the design come
    first, the design and tool problems that need a decision come next, and
    the ones nothing could classify come last. The order inside a bucket is
    :data:`auto_ext.ui.widgets.failure_chip.CODE_ORDER`; groups that tie are
    left in first-seen order, which :func:`sorted` preserves.
    """

    return sorted(groups, key=lambda g: sort_key(g.code))


# ---- per-class actions ------------------------------------------------------

#: Re-run this cell. The card only asks; the host owns the run queue.
ACTION_RERUN = "rerun"
#: Open the Setup drawer so the user can fix a path or an env var.
ACTION_OPEN_SETUP = "open_setup"
#: Copy the verdict's evidence line (a log line, or a resolved path).
ACTION_COPY_EVIDENCE = "copy_evidence"
#: Hand this run's frozen runset to Calibre Interactive.
ACTION_OPEN_CALIBRE = "open_calibre"
#: Scroll the card to the LVS band.
ACTION_SHOW_DISCREPANCIES = "show_discrepancies"
#: Open the failing stage's archived log.
ACTION_OPEN_LOG = "open_log"


class FailureAction(NamedTuple):
    """One button on a failure row. ``primary`` gets the accent fill."""

    action_id: str
    label: str
    primary: bool


def failure_actions(
    code: str,
    *,
    log_name: str = "the stage log",
    discrepancies: int | None = None,
) -> tuple[FailureAction, ...]:
    """The two buttons canvas 1e draws for ``code``, in order.

    Every action is one this card can actually perform or ask for; the canvas
    also sketches "Check license queue" and "Re-run without jivaro", which
    would need a license query and a recipe edit that this codebase does not
    have. The substitutes keep the same shape: one primary action that moves
    the user forward, one secondary that gets the evidence out of the app.
    """

    if code == CODE_LICENSE:
        return (
            FailureAction(ACTION_RERUN, "Retry this cell", True),
            FailureAction(ACTION_COPY_EVIDENCE, "Copy the log line", False),
        )
    if code == CODE_CONFIG:
        return (
            FailureAction(ACTION_OPEN_SETUP, "Fix in Setup", True),
            FailureAction(ACTION_COPY_EVIDENCE, "Copy the path", False),
        )
    if code == CODE_LVS:
        label = (
            f"Show {discrepancies} discrepancies"
            if discrepancies
            else "Show the LVS detail"
        )
        return (
            FailureAction(ACTION_OPEN_CALIBRE, "Open in Calibre Interactive", True),
            FailureAction(ACTION_SHOW_DISCREPANCIES, label, False),
        )
    if code == CODE_CRASH:
        return (
            FailureAction(ACTION_OPEN_LOG, f"Open {log_name}", True),
            FailureAction(ACTION_RERUN, "Retry this cell", False),
        )
    if code == CODE_CANCELLED:
        return (
            FailureAction(ACTION_RERUN, "Retry this cell", True),
            FailureAction(ACTION_OPEN_LOG, f"Open {log_name}", False),
        )
    return (
        FailureAction(ACTION_OPEN_LOG, f"Open {log_name}", True),
        FailureAction(ACTION_COPY_EVIDENCE, "Copy the log line", False),
    )


# ---- stage chips ------------------------------------------------------------


class StageChip(NamedTuple):
    """One pill in the stage strip: ``calibre + cross``, ``jivaro - off in recipe``."""

    stage: str
    text: str
    tone: str
    tooltip: str


_STAGE_TONE: dict[str, str] = {
    str(StageStatus.PASSED): CHIP_TONE_PASSED,
    str(StageStatus.FAILED): CHIP_TONE_FAILED,
    str(StageStatus.CANCELLED): CHIP_TONE_WARNING,
    str(StageStatus.DRY_RUN): CHIP_TONE_PLAIN,
    str(StageStatus.SKIPPED): CHIP_TONE_MUTED,
}

#: Worst-wins ranking when one tool ran under several stage keys
#: (``quantus.ext`` / ``quantus.dspf``): the chip must not report a pass while
#: a sibling key failed.
_STAGE_SEVERITY: dict[str, int] = {
    str(StageStatus.PASSED): 0,
    str(StageStatus.DRY_RUN): 1,
    str(StageStatus.SKIPPED): 2,
    str(StageStatus.CANCELLED): 3,
    str(StageStatus.FAILED): 4,
}


def stage_chips(record: RunRecord) -> list[StageChip]:
    """One chip per pipeline tool, in execution order (canvas 1c / 1d strip).

    Several ``StageRecord`` entries can share a tool name; the worst status
    wins so the strip never claims a pass a sibling key contradicts. Tools the
    record never mentions are reported as "not started" when the run asked for
    them and "off in recipe" when it did not -- see the module Assumptions for
    why a record with no ``requested_stages`` gets neither.
    """

    worst: dict[str, StageRecord] = {}
    order: list[str] = []
    for stage in record.stages:
        name = stage.stage
        if name not in worst:
            order.append(name)
            worst[name] = stage
            continue
        current = _STAGE_SEVERITY.get(str(worst[name].status), 0)
        candidate = _STAGE_SEVERITY.get(str(stage.status), 0)
        if candidate > current:
            worst[name] = stage

    requested = list(record.requested_stages)
    listed = [s for s in CANONICAL_STAGES if s in worst]
    listed += [s for s in order if s not in CANONICAL_STAGES]

    chips: list[StageChip] = []
    for name in listed:
        stage = worst[name]
        status = str(stage.status)
        if status == str(StageStatus.SKIPPED):
            reason = stage.skip_reason or "skipped"
            text = f"{name} {theme.STATUS_GLYPH['skipped']} {reason}"
        else:
            glyph = theme.STATUS_GLYPH.get(status, theme.STATUS_GLYPH["pending"])
            text = f"{name} {glyph}"
        tooltip_bits = [STAGE_DISPLAY.get(status, status)]
        if stage.duration_s is not None:
            tooltip_bits.append(format_duration(stage.duration_s))
        if stage.exit_code is not None:
            tooltip_bits.append(f"exit code {stage.exit_code}")
        if stage.error:
            tooltip_bits.append(stage.error)
        chips.append(
            StageChip(
                stage=name,
                text=text,
                tone=_STAGE_TONE.get(status, CHIP_TONE_MUTED),
                tooltip=" - ".join(tooltip_bits),
            )
        )

    if not requested:
        return chips

    for name in CANONICAL_STAGES:
        if name in worst:
            continue
        if name in requested:
            text = f"{name} {theme.STATUS_GLYPH['skipped']} not started"
            tip = "This run asked for the stage but stopped before reaching it."
        else:
            text = f"{name} {theme.STATUS_GLYPH['skipped']} off in recipe"
            tip = "The recipe did not include this stage."
        chips.append(StageChip(stage=name, text=text, tone=CHIP_TONE_MUTED, tooltip=tip))
    return chips


def stop_reason_text(record: RunRecord) -> str:
    """``"stopped at calibre - continue_on_lvs_fail is off"``, or ``""``.

    Only produced when a stage failed *and* something after it did not run:
    a run whose last stage failed simply ended, which the tally already says.
    """

    failed: StageRecord | None = None
    for stage in record.stages:
        if stage.status == StageStatus.FAILED:
            failed = stage
            break
    if failed is None:
        return ""

    index = record.stages.index(failed)
    stalled = any(
        s.status == StageStatus.SKIPPED for s in record.stages[index + 1 :]
    ) or any(
        s in record.requested_stages
        for s in CANONICAL_STAGES
        if s not in {st.stage for st in record.stages}
    )
    if not stalled:
        return ""

    text = f"stopped at {failed.stage}"
    if failed.stage == "calibre" and not record.continue_on_lvs_fail:
        text += " - continue_on_lvs_fail is off"
    return text


def applied_patch_count(record: RunRecord) -> int:
    """How many manual template edits really changed a rendered file."""

    return sum(
        1
        for report in record.patch_reports
        for outcome in report.outcomes
        if outcome.status in APPLIED_PATCH_STATUSES
    )


# ---- comparison against the previous run of the same DUT --------------------


def discrepancy_delta_text(
    current: int | None, previous: int | None
) -> tuple[str, str]:
    """``(text, color)`` describing the change in discrepancy count.

    The sentence form, for the caption under the headline numbers and for
    anything that greps the card. :func:`delta_chip_text` is the compact form
    that sits next to the number itself.
    """

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


def delta_chip_text(delta: DiscrepancyDelta) -> tuple[str, str]:
    """``(text, color)`` for the compact delta beside the discrepancy number.

    Canvas 1d draws ``17 -> 3 v14`` in green next to the big ``3``. The
    verdict is :func:`auto_ext.core.checks.compare_discrepancies`'s; this only
    renders it. The two "no honest comparison" trends render as words rather
    than as an empty chip, because a blank there reads as a missing value.
    """

    if delta.trend is DiscrepancyTrend.IMPROVED:
        assert delta.delta is not None
        return (
            f"{delta.previous} -> {delta.current} {GLYPH_DOWN}{abs(delta.delta)}",
            COLOR_PASS,
        )
    if delta.trend is DiscrepancyTrend.REGRESSED:
        assert delta.delta is not None
        return (
            f"{delta.previous} -> {delta.current} {GLYPH_UP}{delta.delta}",
            COLOR_FAIL,
        )
    if delta.trend is DiscrepancyTrend.UNCHANGED:
        return (f"unchanged at {delta.current}", theme.WARNING_TEXT_ON_WHITE)
    if delta.trend is DiscrepancyTrend.NO_BASELINE:
        return ("first run of this cell", theme.TEXT_SECONDARY)
    return ("not comparable", theme.TEXT_SECONDARY)


# ---- path helpers -----------------------------------------------------------


def resolve_run_relative(run_dir: Path | None, relative: str | None) -> Path | None:
    """Join a run-relative POSIX path (``"logs/calibre.log"``) onto ``run_dir``."""

    if run_dir is None or not relative:
        return None
    return Path(run_dir) / PurePosixPath(relative)


def stage_log_name(stage: StageRecord) -> str:
    """The file name of a stage's log, for a button that opens exactly it.

    The recorded name when there is one; otherwise the name the runner would
    have written (``logs/<key>.log``). Never ``<tool>.log``: several stage
    records can share a tool -- a retried ``calibre``, ``quantus.ext`` beside
    ``quantus.dspf`` -- and two buttons carrying one name for two files is
    what sent the user to the wrong log.
    """

    if stage.log_path:
        return PurePosixPath(stage.log_path).name
    return f"{stage.key}.log"


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


#: Object name shared by every card band, so one scoped QSS rule styles them
#: all. Scoped on purpose: a selector-less ``setStyleSheet`` on a container
#: applies to every descendant too, which is how a primary button ends up with
#: a white fill and white text.
OBJ_CARD_BAND = "resultCardBand"


def _band(parent: QWidget) -> QFrame:
    """A white card band with a hairline rule underneath it."""

    frame = QFrame(parent)
    frame.setObjectName(OBJ_CARD_BAND)
    frame.setFrameShape(QFrame.NoFrame)
    frame.setStyleSheet(
        f"QFrame#{OBJ_CARD_BAND} {{ background: {theme.SURFACE_CARD}; "
        f"border: none; border-bottom: 1px solid {theme.LINE_ROW}; }}"
    )
    return frame


def _caption(text: str, parent: QWidget, color: str = theme.TEXT_SECONDARY) -> QLabel:
    """An 11px secondary caption, the smallest type the design allows."""

    label = QLabel(text, parent)
    label.setStyleSheet(f"color: {color}; font-size: {theme.FONT_SIZE_META}px;")
    label.setWordWrap(True)
    return label


def _hero(text: str, parent: QWidget, color: str) -> QLabel:
    """The one oversized string on the card: mono 20/700."""

    label = QLabel(text, parent)
    label.setStyleSheet(
        f"{_MONO} font-size: {theme.FONT_SIZE_MONO_HERO}px; "
        f"font-weight: {theme.FONT_WEIGHT_BOLD}; color: {color};"
    )
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class ResultCard(QWidget):
    """The result of one run: header, stage strip, LVS, failures, outputs."""

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
    #: Emitted with the displayed record when the user asks to run this cell
    #: again. The card owns no run queue, so this is a request, not an action.
    rerun_requested = pyqtSignal(object)
    #: Emitted with a section hint (``"Paths"``, ``""``) when a configuration
    #: failure sends the user to the Setup drawer.
    setup_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._record: RunRecord | None = None
        self._run_dir: Path | None = None
        self._annotations: RunAnnotations | None = None
        self._previous: RunIndexEntry | None = None
        self._plan: HandoffPlan | None = None
        self._delta: DiscrepancyDelta | None = None
        self._groups: list[FailureGroup] = []
        self._stage_logs_open = False

        self._build_ui()

        # Owned by the card, not a bare ``QTimer.singleShot``: a timer parented
        # here dies with the widget, so a card closed while the wash is up
        # cannot fire into a deleted C++ object.
        self._lvs_highlight_timer = QTimer(self)
        self._lvs_highlight_timer.setSingleShot(True)
        self._lvs_highlight_timer.timeout.connect(self._clear_lvs_highlight)

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

    @property
    def discrepancy_delta(self) -> DiscrepancyDelta | None:
        """This run's discrepancy count against the previous run of the DUT."""

        return self._delta

    @property
    def failure_groups(self) -> list[FailureGroup]:
        """The run's failures, already ordered by who has to act."""

        return list(self._groups)

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
        # Computed once: classifying a stage can read its archived log, and
        # both the header chip and the failures band want the same answer.
        self._groups = sort_failure_groups(
            group_failures(record, run_dir=self._run_dir)
        )

        self._placeholder.setVisible(False)
        self._body.setVisible(True)
        self._fill_header()
        self._fill_stage_strip()
        self._fill_stages()
        self._fill_lvs()
        # Before _fill_failures: an LVS failure row carries an "Open in
        # Calibre Interactive" button whose enabled state is the plan's.
        self._fill_handoff()
        self._fill_failures()
        self._fill_artifacts()
        self._update_log_button()

    def clear(self) -> None:
        """Drop the displayed record and show the neutral placeholder."""

        self._record = None
        self._run_dir = None
        self._annotations = None
        self._previous = None
        self._plan = None
        self._delta = None
        self._groups = []
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

    def show_lvs_detail(self) -> None:
        """The "Show N discrepancies" action: put the LVS band under the eye.

        Scrolling alone cannot be the whole effect of a labelled button. The
        band is the *first* widget in the details area, so on a card that fits
        its window the scroll range toward it is zero and nothing at all
        moves -- the button reads as dead exactly when the card is easiest to
        read. The scroll is kept for the case where it does help, and the band
        is washed with the selection tint for :data:`LVS_HIGHLIGHT_MS` so the
        destination is visible either way.
        """

        self._scroll.ensureWidgetVisible(self._lvs_band)
        self._lvs_band.setStyleSheet(
            f"QFrame#{OBJ_CARD_BAND} {{ background: {theme.ACCENT_TINT}; "
            f"border: none; border-bottom: 1px solid {theme.LINE_ROW}; "
            f"border-left: {theme.SELECTED_BAR_WIDTH}px solid {theme.ACCENT}; }}"
        )
        self._lvs_highlight_timer.start(LVS_HIGHLIGHT_MS)

    def lvs_detail_highlighted(self) -> bool:
        """True while the LVS band is washed by :meth:`show_lvs_detail`."""

        return self._lvs_highlight_timer.isActive()

    def _clear_lvs_highlight(self) -> None:
        self._lvs_band.setStyleSheet(self._lvs_band_qss)

    # ---- construction -------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._placeholder = QLabel("", self)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        root.addWidget(self._placeholder)

        self._body = QFrame(self)
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.setStyleSheet(
            f"QFrame#resultCardBody {{ background: {theme.SURFACE_CARD}; "
            f"border: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        self._body.setObjectName("resultCardBody")
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addWidget(self._body, stretch=1)

        body.addWidget(self._build_header())
        body.addWidget(self._build_stage_strip())
        body.addWidget(self._build_stage_tree())
        body.addWidget(self._build_scroll(), stretch=1)

    # -- header ---------------------------------------------------------

    def _build_header(self) -> QWidget:
        """The tinted band at the top of the card (canvas 1c / 1d)."""

        header = QFrame(self._body)
        header.setObjectName("resultCardHeader")
        header.setFrameShape(QFrame.NoFrame)
        self._header = header

        layout = QVBoxLayout(header)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_XS + 1, theme.SPACE_MD, theme.SPACE_XS + 1
        )
        layout.setSpacing(theme.SPACE_XXS)

        title_row = QHBoxLayout()
        title_row.setSpacing(theme.SPACE_SM)
        self._glyph = QLabel("", header)
        self._glyph.setStyleSheet(f"font-weight: {theme.FONT_WEIGHT_BOLD};")
        # Every header label wraps or elides. A non-wrapping QLabel reports its
        # whole rendered text width as its *minimum*, which is how a long cell
        # path ends up dictating the window's minimum width.
        self._title = QLabel("", header)
        self._title.setStyleSheet(
            f"font-weight: {theme.FONT_WEIGHT_SEMIBOLD}; "
            f"font-size: {theme.FONT_SIZE_SECTION}px;"
        )
        self._title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._title.setWordWrap(True)
        self._recipe_chip = Chip("", CHIP_TONE_PLAIN, header, mono=False)
        self._code_chip = FailureChip(CODE_UNKNOWN, header)
        self._badge = QLabel("", header)
        self._badge.setStyleSheet(f"font-weight: {theme.FONT_WEIGHT_BOLD};")
        self._duration = QLabel("", header)
        self._duration.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_META}px; "
            f"color: {theme.TEXT_SECONDARY};"
        )
        title_row.addWidget(self._glyph)
        title_row.addWidget(self._title, stretch=1)
        title_row.addWidget(self._recipe_chip)
        title_row.addWidget(self._code_chip)
        title_row.addWidget(self._badge)
        title_row.addWidget(self._duration)
        layout.addLayout(title_row)

        self._subtitle = QLabel("", header)
        self._subtitle.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_META}px; "
            f"color: {theme.TEXT_SECONDARY};"
        )
        self._subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._subtitle.setWordWrap(True)
        layout.addWidget(self._subtitle)

        self._meta = QLabel("", header)
        self._meta.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_META}px; "
            f"color: {theme.TEXT_SECONDARY};"
        )
        self._meta.setWordWrap(True)
        layout.addWidget(self._meta)

        self._note = QLabel("", header)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-style: italic;"
        )
        layout.addWidget(self._note)
        return header

    # -- stage strip ----------------------------------------------------

    def _build_stage_strip(self) -> QWidget:
        strip = _band(self._body)
        strip.setMinimumHeight(theme.STAGE_CHIP_ROW_HEIGHT)
        self._stage_strip = strip

        outer = QVBoxLayout(strip)
        outer.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS
        )
        outer.setSpacing(theme.SPACE_XXS)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_XS)
        self._stage_chip_layout = row
        outer.addLayout(row)

        note_row = QHBoxLayout()
        note_row.setSpacing(theme.SPACE_SM)
        self._stop_reason = _caption("", strip)
        self._log_toggle = QPushButton(f"log per stage {GLYPH_COLLAPSED}", strip)
        self._log_toggle.setFlat(True)
        self._log_toggle.setCursor(Qt.PointingHandCursor)
        self._log_toggle.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; "
            f"color: {theme.ACCENT}; padding: 0px; text-align: left; "
            f"font-size: {theme.FONT_SIZE_META}px; }}"
        )
        # Not connected directly: ``clicked`` carries a bool that Qt would
        # bind to ``show``, so every click would ask for False.
        self._log_toggle.clicked.connect(lambda _c=False: self.toggle_stage_logs())
        note_row.addWidget(self._stop_reason, stretch=1)
        note_row.addWidget(self._log_toggle)
        outer.addLayout(note_row)
        return strip

    def _build_stage_tree(self) -> QWidget:
        pane = _band(self._body)
        self._stage_pane = pane
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS
        )
        layout.setSpacing(theme.SPACE_XS)

        self._stage_tree = QTreeWidget(pane)
        self._stage_tree.setHeaderLabels(["stage", "status", "duration", "log"])
        self._stage_tree.setColumnWidth(0, 160)
        self._stage_tree.setColumnWidth(1, 100)
        self._stage_tree.setColumnWidth(2, 80)
        self._stage_tree.setRootIsDecorated(True)
        self._stage_tree.setUniformRowHeights(True)
        self._stage_tree.setMinimumHeight(theme.ROW_HEIGHT * 3)
        self._stage_tree.itemDoubleClicked.connect(self._on_stage_double_click)
        self._stage_tree.itemSelectionChanged.connect(self._update_log_button)
        self._stage_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._stage_tree.customContextMenuRequested.connect(self._on_stage_menu)
        layout.addWidget(self._stage_tree, stretch=1)

        log_row = QHBoxLayout()
        log_row.setSpacing(theme.SPACE_SM)
        self._open_log_btn = QPushButton("Open log", pane)
        self._open_log_btn.setEnabled(False)
        self._open_log_btn.clicked.connect(self._emit_selected_log)
        hint = _caption(
            "Double-click a stage row for its log; expand a row for the "
            "workarea artifacts it produced.",
            pane,
        )
        log_row.addWidget(self._open_log_btn)
        log_row.addWidget(hint, stretch=1)
        layout.addLayout(log_row)

        # Collapsed by default: canvas 1c/1d draw the card without it, and a
        # tree that is not on screen cannot contribute a minimum height.
        self._stage_logs_open = False
        pane.setVisible(False)
        return pane

    def toggle_stage_logs(self, show: bool | None = None) -> None:
        """Show or hide the per-stage log tree. No argument flips it."""

        visible = (not self._stage_logs_open) if show is None else bool(show)
        self._stage_logs_open = visible
        self._stage_pane.setVisible(visible)
        glyph = GLYPH_EXPANDED if visible else GLYPH_COLLAPSED
        self._log_toggle.setText(f"log per stage {glyph}")

    def stage_logs_visible(self) -> bool:
        """True when the per-stage log tree is expanded.

        Tracked in a flag rather than read back from ``isVisible()``: a child
        of a window that has not been shown yet reports invisible whatever it
        was told, which would make the state unreadable before the first
        ``show()`` -- the same trap :class:`auto_ext.ui.shell.Shell` documents
        for its drawer.
        """

        return self._stage_logs_open

    # -- scrolling detail -----------------------------------------------

    def _build_scroll(self) -> QWidget:
        scroll = QScrollArea(self._body)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll = scroll

        details = QWidget(scroll)
        details.setObjectName("resultCardDetails")
        details.setStyleSheet(
            f"QWidget#resultCardDetails {{ background: {theme.SURFACE_CARD}; }}"
        )
        layout = QVBoxLayout(details)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._details_layout = layout

        layout.addWidget(self._build_lvs_band(details))
        layout.addWidget(self._build_failures(details))
        layout.addWidget(self._build_artifacts(details))
        layout.addWidget(self._build_actions(details))
        layout.addStretch(1)

        scroll.setWidget(details)
        return scroll

    def _build_lvs_band(self, parent: QWidget) -> QWidget:
        band = _band(parent)
        self._lvs_band = band
        #: The plain sheet :meth:`_clear_lvs_highlight` restores.
        self._lvs_band_qss = band.styleSheet()
        outer = QVBoxLayout(band)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)

        # -- column 1: the banner, which is the authoritative verdict
        banner_col = QWidget(band)
        banner_col.setObjectName("lvsBannerColumn")
        banner_col.setMinimumWidth(LVS_COLUMN_WIDTH)
        banner_col.setStyleSheet(
            f"QWidget#lvsBannerColumn {{ border-right: 1px solid {theme.LINE_ROW}; }}"
        )
        banner_layout = QVBoxLayout(banner_col)
        banner_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        banner_layout.setSpacing(theme.SPACE_XXS + 1)
        banner_layout.addWidget(_caption("LVS banner", banner_col))
        self._lvs_banner = _hero("", banner_col, theme.TEXT_SECONDARY)
        banner_layout.addWidget(self._lvs_banner)
        banner_layout.addWidget(
            _caption("authoritative - counts ignored", banner_col, theme.TEXT_DISABLED)
        )
        columns.addWidget(banner_col)

        # -- column 2: the count, with its delta against the previous run
        count_col = QWidget(band)
        count_col.setObjectName("lvsCountColumn")
        count_col.setMinimumWidth(LVS_COLUMN_WIDTH)
        count_col.setStyleSheet(
            f"QWidget#lvsCountColumn {{ border-right: 1px solid {theme.LINE_ROW}; }}"
        )
        count_layout = QVBoxLayout(count_col)
        count_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        count_layout.setSpacing(theme.SPACE_XXS + 1)
        count_layout.addWidget(_caption("DISCREPANCIES", count_col))
        count_row = QHBoxLayout()
        count_row.setSpacing(theme.SPACE_MD)
        self._lvs_count = _hero("", count_col, theme.TEXT_SECONDARY)
        self._lvs_delta = QLabel("", count_col)
        self._lvs_delta.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_SECTION}px;"
        )
        count_row.addWidget(self._lvs_count)
        count_row.addWidget(self._lvs_delta)
        count_row.addStretch(1)
        count_layout.addLayout(count_row)
        # The caption every existing consumer greps: it restates the delta as
        # one sentence and names the run it was measured against.
        self._lvs_body = QLabel("", count_col)
        self._lvs_body.setWordWrap(True)
        self._lvs_body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lvs_body.setStyleSheet(f"font-size: {theme.FONT_SIZE_META}px;")
        count_layout.addWidget(self._lvs_body)
        columns.addWidget(count_col, stretch=1)

        # -- column 3: which sub-cells matched and which did not
        cells_col = QWidget(band)
        cells_layout = QVBoxLayout(cells_col)
        cells_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        cells_layout.setSpacing(theme.SPACE_XXS + 1)
        cells_layout.addWidget(_caption("CELL SUMMARY", cells_col))
        self._cell_summary = QLabel("", cells_col)
        self._cell_summary.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_MONO}px;"
        )
        self._cell_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._cell_summary.setWordWrap(True)
        self._cell_summary.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        cells_layout.addWidget(self._cell_summary, stretch=1)
        columns.addWidget(cells_col, stretch=2)

        outer.addLayout(columns)
        return band

    def _build_failures(self, parent: QWidget) -> QWidget:
        group = _band(parent)
        self._failures_group = group
        layout = QVBoxLayout(group)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        layout.setSpacing(theme.SPACE_SM)
        self._failures_layout = layout
        return group

    def _build_artifacts(self, parent: QWidget) -> QWidget:
        group = _band(parent)
        self._artifacts_group = group
        outer = QVBoxLayout(group)
        outer.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        outer.setSpacing(theme.SPACE_XS)
        outer.addWidget(_caption("Artifacts", group))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.SPACE_SM)
        grid.setVerticalSpacing(theme.SPACE_XS)
        grid.setColumnMinimumWidth(0, 86)
        grid.setColumnStretch(1, 1)
        self._artifacts_layout = grid
        outer.addLayout(grid)
        return group

    def _build_actions(self, parent: QWidget) -> QWidget:
        band = QFrame(parent)
        band.setFrameShape(QFrame.NoFrame)
        band.setStyleSheet(f"QFrame {{ background: {theme.SURFACE_PAGE}; border: none; }}")
        self._actions_band = band
        outer = QVBoxLayout(band)
        outer.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        outer.setSpacing(theme.SPACE_XS)

        row = QHBoxLayout()
        row.setSpacing(theme.SPACE_SM)
        self._handoff_btn = QPushButton("Open this run in Calibre Interactive", band)
        self._handoff_btn.setProperty("primary", True)
        self._handoff_btn.clicked.connect(self._emit_handoff)
        self._report_btn = QPushButton("Open LVS report", band)
        self._report_btn.clicked.connect(self._open_lvs_report)
        self._calibre_log_btn = QPushButton("Open calibre.log", band)
        self._calibre_log_btn.clicked.connect(self._open_calibre_log)
        self._rerun_btn = QPushButton("Re-run this cell", band)
        self._rerun_btn.clicked.connect(self._emit_rerun)
        row.addWidget(self._handoff_btn)
        row.addWidget(self._report_btn)
        row.addWidget(self._calibre_log_btn)
        row.addStretch(1)
        row.addWidget(self._rerun_btn)
        outer.addLayout(row)

        # The exact command line and the exact runset, printed so the user can
        # verify the hand-off without trusting the button -- and paste it.
        # Each prefix is its own label: folded into the elided text it would
        # be the first thing an ElideLeft threw away.
        self._launch_line = PathLabel(band, mode=Qt.ElideRight)
        self._launch_row = self._mono_row("launch", self._launch_line, band)
        outer.addWidget(self._launch_row)

        self._runset_line = PathLabel(band, mode=Qt.ElideLeft)
        # A PathLabel with a path styles itself as a link and takes the
        # pointing-hand cursor, so it must actually open something -- the same
        # wiring the artifact grid uses.
        self._runset_line.clicked.connect(self.artifact_requested)
        self._runset_note = QLabel("", band)
        self._runset_note.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_META}px; "
            f"color: {theme.TEXT_DISABLED};"
        )
        self._runset_row = self._mono_row(
            "runset", self._runset_line, band, trailing=self._runset_note
        )
        outer.addWidget(self._runset_row)
        return band

    @staticmethod
    def _mono_row(
        prefix: str,
        value: QWidget,
        parent: QWidget,
        *,
        trailing: QWidget | None = None,
    ) -> QWidget:
        """``prefix  <elided value>  <note>`` on one mono line."""

        row = QWidget(parent)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)
        label = QLabel(prefix, row)
        label.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_META}px; "
            f"color: {theme.TEXT_SECONDARY};"
        )
        layout.addWidget(label)
        value.setParent(row)
        layout.addWidget(value, stretch=1)
        if trailing is not None:
            trailing.setParent(row)
            layout.addWidget(trailing)
        return row

    # ---- header -------------------------------------------------------

    def _fill_header(self) -> None:
        record = self._record
        assert record is not None
        annots = self._annotations
        name = (annots.display_name if annots else None) or record.default_display_name
        self._title.setText(name)

        overall = str(record.overall)
        color = theme.status_color(overall)
        self._badge.setText(TASK_DISPLAY.get(overall, overall).upper())
        self._badge.setStyleSheet(f"font-weight: bold; color: {color};")
        self._glyph.setText(theme.STATUS_GLYPH.get(overall, theme.STATUS_GLYPH["pending"]))
        self._glyph.setStyleSheet(
            f"font-weight: {theme.FONT_WEIGHT_BOLD}; color: {color};"
        )
        # The header band is tinted by the verdict; the 3px left bar on the
        # card body carries the same colour (canvas 1c / 1d).
        tint = HEADER_TINT.get(overall, theme.SURFACE_TOOLBAR)
        self._header.setStyleSheet(
            f"QFrame#resultCardHeader {{ background: {tint}; border: none; "
            f"border-bottom: 1px solid {theme.LINE_PANEL}; }}"
        )
        self._body.setStyleSheet(
            f"QFrame#resultCardBody {{ background: {theme.SURFACE_CARD}; "
            f"border: 1px solid {theme.LINE_STRUCTURAL}; "
            f"border-left: {theme.SELECTED_BAR_WIDTH}px solid {color}; }}"
        )

        dut = record.dut
        self._subtitle.setText(
            f"{dut.library} / {dut.cell} - layout {dut.layout_view} vs "
            f"{dut.source_view} - recipe {record.recipe.label}"
        )
        self._recipe_chip.setText(record.recipe.label)
        self._duration.setText(format_duration(record.duration_s))

        code = self._headline_code()
        self._code_chip.setVisible(code is not None)
        if code is not None:
            self._code_chip.set_code(code)

        meta = [format_timestamp(record.created_at), tally_text(record.stages)]
        if record.dry_run:
            meta.append("dry run")
        applied = applied_patch_count(record)
        if applied:
            meta.append(f"{applied} manual edit{'s' if applied != 1 else ''} applied")
        self._meta.setText(" - ".join(meta))

        note = (annots.note if annots else None) or ""
        self._note.setText(note)
        self._note.setVisible(bool(note))

    def _headline_code(self) -> str | None:
        """The one code worth putting in the header, or ``None`` on success.

        When a run failed several ways the header shows the group that the
        "who has to act" order puts last -- the one that will still be there
        after the environment has been fixed.
        """

        return self._groups[-1].code if self._groups else None

    # ---- stage strip ---------------------------------------------------

    def _fill_stage_strip(self) -> None:
        record = self._record
        assert record is not None
        _clear_layout(self._stage_chip_layout)
        for chip_data in stage_chips(record):
            chip = Chip(chip_data.text, chip_data.tone, self._stage_strip)
            chip.setToolTip(chip_data.tooltip)
            self._stage_chip_layout.addWidget(chip)
        self._stage_chip_layout.addStretch(1)

        reason = stop_reason_text(record)
        self._stop_reason.setText(reason)
        self._stop_reason.setVisible(bool(reason))

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
            color = theme.status_color(status)
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
                child = QTreeWidgetItem([Path(artifact).name, "artifact", "", artifact])
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

    def stage_log_path(self, stage_name: str) -> Path | None:
        """The archived log of the last stage run by ``stage_name``, if any."""

        record = self._record
        if record is None:
            return None
        for stage in reversed(record.stages):
            if stage.stage != stage_name:
                continue
            path = resolve_run_relative(self._run_dir, stage.log_path)
            if path is not None and path.is_file():
                return path
        return None

    def _fill_lvs(self) -> None:
        # First, before any branch can skip it: the delta belongs to the run
        # being drawn. Left standing, the previous run's count came back as a
        # "Show 17 discrepancies" button on a run that never reached LVS, and
        # out of the public ``discrepancy_delta`` property as well.
        self._delta = None
        record = self._record
        assert record is not None
        lvs = record.results.lvs
        rows = cell_summary_rows(lvs, self.lvs_report_path())
        report = as_lvs_report(lvs, rows)

        if lvs is None:
            self._lvs_banner.setText("not run")
            self._lvs_banner.setStyleSheet(
                f"{_MONO} font-size: {theme.FONT_SIZE_MONO_HERO}px; "
                f"font-weight: {theme.FONT_WEIGHT_BOLD}; color: {theme.TEXT_DISABLED};"
            )
            self._lvs_count.setText("-")
            self._set_hero_color(self._lvs_count, theme.TEXT_DISABLED)
            self._lvs_delta.setText("")
            self._cell_summary.setText("")
        else:
            color = COLOR_PASS if lvs.passed else COLOR_FAIL
            self._lvs_banner.setText(lvs.banner or "(no banner)")
            self._set_hero_color(self._lvs_banner, color)

            count = report.effective_discrepancies if report is not None else None
            if count is None:
                self._lvs_count.setText("?")
                self._set_hero_color(self._lvs_count, theme.TEXT_DISABLED)
                self._lvs_count.setToolTip(
                    "This report states no DISCREPANCIES count. Calibre v2019.2 "
                    "omits it on a clean pass; a truncated report omits it too."
                )
            else:
                self._lvs_count.setText(str(count))
                self._set_hero_color(
                    self._lvs_count, COLOR_PASS if count == 0 else COLOR_FAIL
                )
                self._lvs_count.setToolTip("")

            previous_count = (
                self._previous.lvs_discrepancies if self._previous is not None else None
            )
            self._delta = compare_discrepancies(
                report if report is not None else count, previous_count
            )
            text, delta_color = delta_chip_text(self._delta)
            self._lvs_delta.setText(text)
            self._lvs_delta.setStyleSheet(
                f"{_MONO} font-size: {theme.FONT_SIZE_SECTION}px; color: {delta_color};"
            )
            self._lvs_delta.setToolTip(self._delta.summary)
            self._cell_summary.setText(self._cell_summary_html(rows))

        self._lvs_body.setText(self._comparison_html())
        self._lvs_body.setToolTip(
            f"previous run {self._previous.run_id}  "
            f"{format_timestamp(self._previous.created_at)}"
            if self._previous is not None
            else ""
        )

    @staticmethod
    def _set_hero_color(label: QLabel, color: str) -> None:
        label.setStyleSheet(
            f"{_MONO} font-size: {theme.FONT_SIZE_MONO_HERO}px; "
            f"font-weight: {theme.FONT_WEIGHT_BOLD}; color: {color};"
        )

    def _cell_summary_html(self, rows: Sequence[str]) -> str:
        """The CELL SUMMARY block: INCORRECT rows first, then CORRECT ones.

        Mismatching sub-cells lead because they are what the user opens the
        card for; the matching ones stay visible so a report with one bad cell
        out of forty does not read as a total failure.
        """

        parsed = [row for row in (parse_cell_summary_line(r) for r in rows) if row]
        if not parsed:
            unnamed = unnamed_mismatch_count(rows)
            if unnamed:
                return (
                    f"<span style='color:{COLOR_FAIL}'>{unnamed} INCORRECT row(s); "
                    "the report carries no cell names.</span>"
                )
            return (
                f"<span style='color:{theme.TEXT_DISABLED}'>no CELL SUMMARY table in "
                "this report</span>"
            )

        bad = [row for row in parsed if not row.passed]
        good = [row for row in parsed if row.passed]
        lines: list[str] = []
        for row in bad:
            lines.append(
                f"<span style='color:{COLOR_FAIL};font-weight:{theme.FONT_WEIGHT_BOLD}'>"
                f"INCORRECT</span>&nbsp; {row.layout}"
                + (f" &nbsp;{row.source}" if row.source != row.layout else "")
            )
        for row in good[: max(0, 6 - len(bad))]:
            lines.append(
                f"<span style='color:{COLOR_PASS}'>CORRECT</span>&nbsp;&nbsp; "
                f"{row.layout}"
            )
        hidden = len(good) - max(0, 6 - len(bad))
        if hidden > 0:
            lines.append(
                f"<span style='color:{theme.TEXT_DISABLED}'>+{hidden} more CORRECT"
                "</span>"
            )
        return "<br>".join(lines)

    def _comparison_html(self) -> str:
        """The caption under the numbers: this run against the previous one."""

        record = self._record
        assert record is not None
        parts: list[str] = []
        if record.results.lvs is None:
            parts.append(
                f"<span style='color:{theme.TEXT_DISABLED}'>No LVS result recorded "
                "for this run.</span>"
            )
        previous = self._previous
        if previous is None:
            parts.append(
                f"<span style='color:{theme.TEXT_DISABLED}'>No earlier run of this "
                "cell to compare against.</span>"
            )
            return " ".join(parts)
        current = record.results.lvs.discrepancies if record.results.lvs else None
        text, color = discrepancy_delta_text(current, previous.lvs_discrepancies)
        parts.append(
            "vs previous run (%s): <span style='color:%s'>%s</span>"
            % (previous.created_at.strftime("%m-%d %H:%M"), color, text)
        )
        return " ".join(parts)

    # ---- failures -----------------------------------------------------

    def _fill_failures(self) -> None:
        _clear_layout(self._failures_layout)
        groups = self._groups
        if not groups:
            self._failures_group.setVisible(False)
            return
        self._failures_group.setVisible(True)

        seen_actors: set[str] = set()
        for group in groups:
            actor = group.actor
            if actor not in seen_actors:
                seen_actors.add(actor)
                self._failures_layout.addWidget(
                    self._actor_heading(actor, groups)
                )
            for index, stage in enumerate(group.stages):
                verdict = group.verdicts[index] if index < len(group.verdicts) else None
                self._failures_layout.addWidget(
                    self._failure_row(group, stage, verdict)
                )

    def _actor_heading(self, actor: str, groups: Sequence[FailureGroup]) -> QWidget:
        """``Not your layout - environment   2 stages - re-runnable as-is``."""

        stages = sum(len(g.stages) for g in groups if g.actor == actor)
        row = QWidget(self._failures_group)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, theme.SPACE_XXS, 0, 0)
        layout.setSpacing(theme.SPACE_SM)
        title = QLabel(ACTOR_TITLES.get(actor, actor), row)
        title.setStyleSheet(f"font-weight: {theme.FONT_WEIGHT_BOLD};")
        meta = PathLabel(row, mode=Qt.ElideRight)
        meta.set_placeholder(
            f"{stages} stage{'s' if stages != 1 else ''} - "
            f"{ACTOR_SUBTITLES.get(actor, '')}"
        )
        rule = QFrame(row)
        rule.setFrameShape(QFrame.HLine)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {theme.LINE_PANEL}; border: none;")
        layout.addWidget(title)
        layout.addWidget(meta, stretch=2)
        layout.addWidget(rule, stretch=1)
        return row

    def _failure_row(
        self,
        group: FailureGroup,
        stage: StageRecord,
        verdict: FailureVerdict | None,
    ) -> QWidget:
        """One failure: code rail, what happened, evidence, two buttons."""

        code = group.code
        style_color = COLOR_FAIL if group.actor != "environment" else COLOR_WARN
        row = QFrame(self._failures_group)
        row.setFrameShape(QFrame.NoFrame)
        row.setStyleSheet(
            f"QFrame#failureRow {{ background: {theme.SURFACE_CARD}; "
            f"border: 1px solid {theme.LINE_STRUCTURAL}; "
            f"border-left: {theme.SELECTED_BAR_WIDTH}px solid {style_color}; }}"
        )
        row.setObjectName("failureRow")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(FailureChip(code, row, stretch=True))

        middle = QWidget(row)
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        middle_layout.setSpacing(theme.SPACE_XXS + 2)

        head = QHBoxLayout()
        head.setSpacing(theme.SPACE_MD)
        title = QLabel(group.title, middle)
        title.setStyleSheet(
            f"{_MONO} font-weight: {theme.FONT_WEIGHT_BOLD};"
        )
        where = _caption(
            f"{stage.key}"
            + (f" - exit {stage.exit_code}" if stage.exit_code is not None else "")
            + (f" - {format_duration(stage.duration_s)}" if stage.duration_s else ""),
            middle,
        )
        head.addWidget(title)
        head.addWidget(where, stretch=1)
        middle_layout.addLayout(head)

        reason = (verdict.reason if verdict else "") or stage.error or stage.skip_reason
        if reason:
            body = QLabel(reason, middle)
            body.setWordWrap(True)
            middle_layout.addWidget(body)

        if verdict is not None and verdict.evidence:
            evidence = QLabel(verdict.evidence, middle)
            evidence.setWordWrap(True)
            evidence.setStyleSheet(
                f"{_MONO} font-size: {theme.FONT_SIZE_META}px; "
                f"color: {theme.TEXT_SECONDARY}; background: {theme.SURFACE_PAGE}; "
                f"border: 1px solid {theme.LINE_ROW}; padding: 3px 6px;"
            )
            middle_layout.addWidget(evidence)

        action_text = (verdict.next_action if verdict else None) or group.next_action
        next_step = QLabel(f"Next: {action_text}", middle)
        next_step.setWordWrap(True)
        next_step.setStyleSheet(
            f"color: {theme.WARNING_TEXT_ON_WHITE}; "
            f"font-size: {theme.FONT_SIZE_META}px;"
        )
        middle_layout.addWidget(next_step)
        layout.addWidget(middle, stretch=1)

        buttons = QWidget(row)
        buttons.setObjectName("failureRowActions")
        buttons.setStyleSheet(
            f"QWidget#failureRowActions {{ border-left: 1px solid {theme.LINE_ROW}; }}"
        )
        button_layout = QVBoxLayout(buttons)
        button_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        button_layout.setSpacing(theme.SPACE_XS + 1)
        button_layout.addStretch(1)
        for action in failure_actions(
            code,
            # The recorded file, not the tool name: the runner writes
            # ``logs/<stage key>.log``, so a retried calibre stage and a
            # ``quantus.dspf`` step each name their own log rather than
            # borrowing a sibling's.
            log_name=stage_log_name(stage),
            discrepancies=self._delta.current if self._delta else None,
        ):
            button_layout.addWidget(self._action_button(action, buttons, stage, verdict))
        button_layout.addStretch(1)
        layout.addWidget(buttons)
        return row

    def _action_button(
        self,
        action: FailureAction,
        parent: QWidget,
        stage: StageRecord,
        verdict: FailureVerdict | None,
    ) -> QPushButton:
        button = QPushButton(action.label, parent)
        if action.primary:
            button.setProperty("primary", True)
        button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        if action.action_id == ACTION_RERUN:
            button.setToolTip("Queue this cell again with the same recipe.")
            button.clicked.connect(self._emit_rerun)
        elif action.action_id == ACTION_OPEN_SETUP:
            button.setToolTip("Open the Setup drawer and re-run the checks.")
            button.clicked.connect(lambda _c=False: self.setup_requested.emit("Paths"))
        elif action.action_id == ACTION_COPY_EVIDENCE:
            evidence = (verdict.evidence if verdict else None) or ""
            button.setEnabled(bool(evidence))
            button.setToolTip(
                evidence or "This verdict carries no evidence line to copy."
            )
            button.clicked.connect(lambda _c=False, t=evidence: self.copy_text(t))
        elif action.action_id == ACTION_OPEN_CALIBRE:
            plan = self._plan
            button.setEnabled(bool(plan is not None and plan.ok))
            button.setToolTip(self._handoff_tooltip())
            button.clicked.connect(self._emit_handoff)
        elif action.action_id == ACTION_SHOW_DISCREPANCIES:
            button.setToolTip("Scroll to the LVS banner, count and CELL SUMMARY.")
            button.clicked.connect(lambda _c=False: self.show_lvs_detail())
        else:  # ACTION_OPEN_LOG
            log_path = resolve_run_relative(self._run_dir, stage.log_path)
            live = log_path is not None and log_path.is_file()
            button.setEnabled(live)
            button.setToolTip(
                str(log_path) if live else "This stage archived no log in the run."
            )
            if live:
                button.clicked.connect(
                    lambda _c=False, p=log_path: self.log_requested.emit(p)
                )
        return button

    # ---- outputs ------------------------------------------------------

    def _fill_artifacts(self) -> None:
        record = self._record
        assert record is not None
        _clear_layout(self._artifacts_layout)
        row = 0

        row = self._add_output_row(
            row,
            "output dir",
            record.workspace_dir,
            open_path=Path(record.workspace_dir),
        )

        dut = record.dut
        if dut.out_file:
            triple = f"{dut.library} / {dut.cell} / {dut.out_file}"
            row = self._add_output_row(row, "extracted", triple, copy_text=triple)
        else:
            row = self._add_output_row(
                row,
                "extracted",
                "(no out_file configured)",
                reason="This run's DUT names no extracted view.",
            )

        report = self.lvs_report_path()
        lvs = record.results.lvs
        if report is not None:
            row = self._add_output_row(row, "lvs report", str(report), open_path=report)
        elif lvs is not None and lvs.source_path:
            row = self._add_output_row(
                row,
                "lvs report",
                lvs.source_path,
                reason="Gone - a later run of this cell overwrote the workarea copy.",
            )
        else:
            row = self._add_output_row(
                row, "lvs report", "(none)", reason="This run produced no LVS report."
            )

        rendered = self._rendered_paths()
        if rendered:
            for label, path, relative in rendered:
                row = self._add_output_row(
                    row, label, relative, open_path=path if path.is_file() else None
                )

        dspf = Path(record.dspf_path) if record.dspf_path else None
        if dspf is not None:
            row = self._add_output_row(row, "dspf", str(dspf), open_path=dspf)

        if self._run_dir is not None:
            row = self._add_output_row(
                row, "run dir", str(self._run_dir), open_path=self._run_dir
            )

    def _rendered_paths(self) -> list[tuple[str, Path, str]]:
        """``(label, absolute, run-relative)`` for every archived rendered input."""

        record = self._record
        if record is None or self._run_dir is None:
            return []
        out: list[tuple[str, Path, str]] = []
        for stage in record.stages:
            if not stage.rendered_path:
                continue
            path = resolve_run_relative(self._run_dir, stage.rendered_path)
            if path is None:
                continue
            out.append((f"rendered {stage.stage}", path, stage.rendered_path))
        return out

    def _add_output_row(
        self,
        row: int,
        label: str,
        value: str,
        *,
        open_path: Path | None = None,
        copy_text: str | None = None,
        reason: str = "",
    ) -> int:
        grid = self._artifacts_layout
        name = QLabel(label, self._artifacts_group)
        name.setStyleSheet(
            f"color: {theme.TEXT_DISABLED}; font-size: {theme.FONT_SIZE_META}px;"
        )
        grid.addWidget(name, row, 0, alignment=Qt.AlignTop)

        path_label = PathLabel(self._artifacts_group)
        if open_path is not None and open_path.exists():
            path_label.set_path(open_path, text=value)
            path_label.clicked.connect(self.artifact_requested.emit)
        elif open_path is not None:
            path_label.set_placeholder(
                value, reason=reason or f"Not on this host: {open_path}"
            )
        else:
            path_label.set_placeholder(value, reason=reason)
        grid.addWidget(path_label, row, 1)

        if copy_text is not None:
            button = QPushButton("Copy", self._artifacts_group)
            button.setToolTip("Copy the library / cell / view triple.")
            button.clicked.connect(lambda _c=False, t=copy_text: self.copy_text(t))
            grid.addWidget(button, row, 2, alignment=Qt.AlignTop)
        return row + 1

    # ---- Calibre Interactive -------------------------------------------

    def _handoff_tooltip(self) -> str:
        plan = self._plan
        if plan is None:
            return "No run selected."
        if plan.ok:
            return "\n".join([plan.command_line, *plan.warnings])
        return plan.reason or "Calibre Interactive cannot be opened."

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
        self._handoff_btn.setToolTip(self._handoff_tooltip())
        # An LVS failure row already carries "Open in Calibre Interactive",
        # wired to this very method. Two buttons with almost the same words on
        # one card is a question ("do they differ?"), not an affordance, so the
        # card-level one steps aside for the row that has the context. It comes
        # back for every other run, which is where it is the only offer.
        self._handoff_btn.setVisible(
            not any(group.code == CODE_LVS for group in self._groups)
        )

        if plan is not None and plan.argv:
            self._launch_line.set_placeholder(plan.command_line)
            self._launch_row.setVisible(True)
        else:
            self._launch_row.setVisible(False)

        if plan is not None and plan.runset is not None:
            exists = plan.runset.is_file()
            self._runset_line.set_path(
                plan.runset if exists else None,
                text=str(plan.runset),
                reason="The archived runset is gone, so the hand-off cannot start.",
            )
            self._runset_note.setText(
                "(exact file this run used - frozen)" if exists else "(missing)"
            )
            self._runset_row.setVisible(True)
        else:
            self._runset_row.setVisible(False)

        report = self.lvs_report_path()
        self._report_btn.setEnabled(report is not None)
        self._report_btn.setToolTip(
            str(report) if report is not None else "This run archived no LVS report."
        )

        calibre_log = self.stage_log_path("calibre")
        self._calibre_log_btn.setEnabled(calibre_log is not None)
        # The label names the file this button really opens. When calibre ran
        # twice in one run this control resolves the *last* attempt while the
        # failure row resolves its own stage, and two buttons reading "Open
        # calibre.log" then opened two different files.
        self._calibre_log_btn.setText(
            f"Open {calibre_log.name}" if calibre_log is not None else "Open calibre.log"
        )
        self._calibre_log_btn.setToolTip(
            str(calibre_log)
            if calibre_log is not None
            else "This run archived no calibre log."
        )

    def _open_lvs_report(self) -> None:
        report = self.lvs_report_path()
        if report is not None:
            self.artifact_requested.emit(report)

    def _open_calibre_log(self) -> None:
        path = self.stage_log_path("calibre")
        if path is not None:
            self.log_requested.emit(path)

    def _emit_handoff(self) -> None:
        record = self.handoff_record()
        if record is not None:
            self.handoff_requested.emit(record)

    def _emit_rerun(self) -> None:
        record = self.handoff_record()
        if record is not None:
            self.rerun_requested.emit(record)

    # ---- layout -------------------------------------------------------

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Deliberately small: the card scrolls, it does not push the window.

        Qt would otherwise add up the header, the stage strip, the LVS band
        and whatever the scroll area's *widget* wants, and a run with four
        failure rows would alone exceed the 560px window floor.
        """

        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), 320), min(hint.height(), 220))
