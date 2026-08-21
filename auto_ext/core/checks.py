"""Calibre LVS report parser with strict pass/fail classification.

Strict criterion:
- Banner ``INCORRECT`` is authoritative: result is fail regardless of counts.
- Banner ``CORRECT`` plus ``DISCREPANCIES = 0`` is a pass.
- Banner ``CORRECT`` plus non-zero ``DISCREPANCIES`` is fail with a WARNING.
- Banner ``CORRECT`` without a ``DISCREPANCIES`` line: some Calibre versions
  (e.g. v2019.2) omit the count on clean passes, using the CELL SUMMARY table
  as the authoritative record instead. Fall back to scanning CELL SUMMARY: if
  every row reads CORRECT we pass; if the table is absent or empty we treat
  the report as truncated and fail.
- No banner at all -> :class:`CheckError` (report is too malformed to classify).

``parse_lvs_report`` is a thin wrapper over :func:`parse_lvs_report_detailed`
so the runner can surface the structured result (banner, discrepancy count,
CELL SUMMARY rows, source path) in logs and the GUI without re-parsing.

The CELL SUMMARY table is scanned on *every* report, not only on the v2019.2
fallback path. Pass/fail classification is unchanged by that -- the table is
still consulted for the verdict only when the ``DISCREPANCIES`` line is
absent -- but :attr:`LvsReport.cells` is what lets the GUI answer "which
sub-cells did not match" instead of only "the run failed".

:func:`compare_discrepancies` turns this run's report plus the previous run's
count into a trend (improved / unchanged / regressed / no baseline). It is a
pure function: finding the previous run is
:func:`auto_ext.core.run_store.find_previous_run`'s job.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from auto_ext.core.errors import CheckError

logger = logging.getLogger(__name__)


# Word-boundary banner regexes. Negative lookbehind rejects the "IN" of
# INCORRECT so CORRECT doesn't also match inside INCORRECT; negative
# lookahead rejects trailing uppercase letters just in case.
_RE_BANNER_CORRECT = re.compile(r"(?<![A-Z])CORRECT(?![A-Z])")
_RE_BANNER_INCORRECT = re.compile(r"(?<![A-Z])INCORRECT(?![A-Z])")

# "DISCREPANCIES = N" — newer Calibre prints this for passes and fails; older
# versions (e.g. v2019.2) omit it on clean passes, hence the CELL SUMMARY
# fallback below.
_RE_DISCREPANCIES = re.compile(r"DISCREPANCIES\s*=\s*(\d+)", re.IGNORECASE)

# CELL SUMMARY: detect the section header, then scan its rows. Each row is a
# three-column line "<result> <layout> <source>" where result is CORRECT or
# INCORRECT. The intra-row separators are ``[ \t]`` rather than ``\s`` on
# purpose: ``\s`` also matches a newline, so under re.MULTILINE the previous
# pattern could stitch three consecutive one-token lines into a phantom row.
_RE_CELL_SUMMARY_HEADER = re.compile(r"CELL\s+SUMMARY", re.IGNORECASE)
_RE_CELL_SUMMARY_ROW = re.compile(
    r"^[ \t]*(CORRECT|INCORRECT)[ \t]+(\S+)[ \t]+(\S+)[ \t\r]*$",
    re.IGNORECASE | re.MULTILINE,
)

#: Verdict string of a CELL SUMMARY row that compared clean.
CELL_CORRECT = "CORRECT"
#: Verdict string of a CELL SUMMARY row that did not compare clean.
CELL_INCORRECT = "INCORRECT"


@dataclass(frozen=True)
class CellSummaryRow:
    """One row of Calibre's CELL SUMMARY table.

    ``layout`` and ``source`` are the two cell names Calibre compared; they
    are normally identical, and differ only when the runset maps a layout
    cell onto a differently-named schematic cell.
    """

    result: str
    layout: str
    source: str

    @property
    def passed(self) -> bool:
        """True when this row reads ``CORRECT``."""

        return self.result == CELL_CORRECT

    @property
    def name(self) -> str:
        """The cell name to show in a UI: the layout side of the pair."""

        return self.layout

    def as_line(self) -> str:
        """Render back to one flat line: ``"<result> <layout> <source>"``.

        This is the string form written into
        :attr:`auto_ext.model.run.LvsResult.cell_summary` (typed
        ``list[str]``), chosen so the archived value stays greppable and
        re-parsable.
        """

        return f"{self.result} {self.layout} {self.source}"


def _parse_cell_summary(text: str) -> list[CellSummaryRow] | None:
    """Extract CELL SUMMARY rows.

    Returns ``None`` if no CELL SUMMARY section is present (report likely
    truncated); an empty list if the header exists but no rows parse; else
    one :class:`CellSummaryRow` per matched row, in file order.
    """
    header = _RE_CELL_SUMMARY_HEADER.search(text)
    if header is None:
        return None
    return [
        CellSummaryRow(result=m.group(1).upper(), layout=m.group(2), source=m.group(3))
        for m in _RE_CELL_SUMMARY_ROW.finditer(text[header.end() :])
    ]


@dataclass(frozen=True)
class LvsReport:
    """Structured view of a parsed LVS report."""

    passed: bool
    banner: str | None
    discrepancies: int | None
    source: Path
    #: CELL SUMMARY rows in file order. Empty when the table is absent or
    #: carries no parsable row — check :attr:`cell_summary_present` to tell
    #: "no table at all" from "table present but empty".
    cells: tuple[CellSummaryRow, ...] = ()
    #: True when a ``CELL SUMMARY`` section header was found, regardless of
    #: how many rows parsed out of it.
    cell_summary_present: bool = False

    @property
    def mismatched_cells(self) -> tuple[str, ...]:
        """Layout-side names of every ``INCORRECT`` CELL SUMMARY row.

        This is the "which sub-cells did not match" list for the GUI. It is
        empty when the report carries no CELL SUMMARY table, which is *not*
        the same as "nothing mismatched" — cross-check :attr:`passed`.
        """

        return tuple(row.layout for row in self.cells if not row.passed)

    @property
    def matched_cells(self) -> tuple[str, ...]:
        """Layout-side names of every ``CORRECT`` CELL SUMMARY row."""

        return tuple(row.layout for row in self.cells if row.passed)

    @property
    def incorrect_cell_count(self) -> int:
        """How many CELL SUMMARY rows read ``INCORRECT``.

        Deliberately *not* a discrepancy count: one mismatching cell can
        carry many discrepancies, and Calibre counts the two separately.
        """

        return len(self.mismatched_cells)

    @property
    def effective_discrepancies(self) -> int | None:
        """The discrepancy count, or ``None`` when the report states none.

        Returns the parsed ``DISCREPANCIES = N`` value when Calibre printed
        it. When it did not (v2019.2 clean-pass format) but the CELL SUMMARY
        table is present, non-empty and every row reads ``CORRECT``, the
        count is 0 — a fact the table states, not an inference. Every other
        shape returns ``None`` rather than guessing; in particular a failing
        report without a count does *not* report its INCORRECT row count as
        a discrepancy count (see :attr:`incorrect_cell_count`).
        """

        if self.discrepancies is not None:
            return self.discrepancies
        if self.cell_summary_present and self.cells and all(c.passed for c in self.cells):
            return 0
        return None

    def cell_summary_lines(self) -> list[str]:
        """Flat ``list[str]`` form of :attr:`cells`, for archiving in ``run.json``."""

        return [row.as_line() for row in self.cells]


def parse_lvs_report(report: Path) -> bool:
    """Return True iff the LVS report indicates a clean pass.

    Raises :class:`CheckError` if the report is too malformed to classify
    (missing banner, unreadable file, etc.).
    """

    return parse_lvs_report_detailed(report).passed


def parse_lvs_report_detailed(report: Path) -> LvsReport:
    """Parse and classify an LVS report, returning the structured view."""

    if not report.is_file():
        raise CheckError(f"LVS report missing: {report}")

    text = report.read_text(encoding="utf-8", errors="replace")

    has_incorrect = bool(_RE_BANNER_INCORRECT.search(text))
    has_correct = bool(_RE_BANNER_CORRECT.search(text))

    if has_incorrect:
        banner: str | None = "INCORRECT"
    elif has_correct:
        banner = "CORRECT"
    else:
        banner = None

    m = _RE_DISCREPANCIES.search(text)
    discrepancies = int(m.group(1)) if m else None

    # Scanned unconditionally: the rows are the per-sub-cell detail the GUI
    # shows on a failure. Only the *verdict* below still treats the table as
    # a fallback signal.
    rows = _parse_cell_summary(text)

    if banner is None:
        raise CheckError(f"no LVS banner found; report truncated? source={report}")

    if banner == "INCORRECT":
        passed = False
    elif discrepancies is None:
        if rows and all(r.passed for r in rows):
            logger.info(
                "LVS report %s: banner CORRECT, no DISCREPANCIES line, "
                "CELL SUMMARY has %d row(s) all CORRECT; treating as pass",
                report.name,
                len(rows),
            )
            passed = True
        else:
            logger.warning(
                "LVS report %s has banner CORRECT but no DISCREPANCIES count "
                "and no usable CELL SUMMARY; report may be truncated; "
                "treating as fail",
                report.name,
            )
            passed = False
    elif discrepancies > 0:
        logger.warning(
            "LVS report %s has banner CORRECT but %d discrepancies; treating as fail",
            report.name,
            discrepancies,
        )
        passed = False
    else:
        passed = True

    logger.info(
        "LVS report %s: banner=%s disc=%s cells=%d incorrect=%d -> pass=%s",
        report.name,
        banner,
        discrepancies,
        len(rows or ()),
        sum(1 for r in (rows or ()) if not r.passed),
        passed,
    )

    return LvsReport(
        passed=passed,
        banner=banner,
        discrepancies=discrepancies,
        source=report.resolve(),
        cells=tuple(rows or ()),
        cell_summary_present=rows is not None,
    )


# ---- "vs. last time" comparison --------------------------------------------


class DiscrepancyTrend(str, Enum):
    """How this run's discrepancy count moved against the previous run."""

    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"
    #: No previous run of this DUT is on record.
    NO_BASELINE = "no_baseline"
    #: One of the two counts is not stated by its report, so no honest
    #: comparison exists. Calibre v2019.2 omits the count on a clean pass.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiscrepancyDelta:
    """Result of :func:`compare_discrepancies`."""

    trend: DiscrepancyTrend
    current: int | None
    previous: int | None
    #: ``current - previous`` when both counts are known, else ``None``.
    delta: int | None
    #: One English line, ready for a status bar or a run-list column.
    summary: str

    @property
    def comparable(self) -> bool:
        """True when both counts were known and :attr:`delta` is meaningful."""

        return self.delta is not None

    def as_dict(self) -> dict[str, object]:
        """JSON-safe form, for stashing in a run record's details."""

        return {
            "trend": self.trend.value,
            "current": self.current,
            "previous": self.previous,
            "delta": self.delta,
            "summary": self.summary,
        }


def _as_count(value: LvsReport | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, LvsReport):
        return value.effective_discrepancies
    return int(value)


def compare_discrepancies(
    current: LvsReport | int | None,
    previous: LvsReport | int | None = None,
) -> DiscrepancyDelta:
    """Compare this run's discrepancy count against the previous run's.

    Pure function -- it does no I/O and does not go looking for the previous
    run. Callers get the baseline from
    :func:`auto_ext.core.run_store.find_previous_run`, which matches on the
    four DUT identity axes, and pass either its
    ``RunIndexEntry.lvs_discrepancies`` (an ``int | None``) or a full
    :class:`LvsReport` re-parsed from the archived report.

    Both arguments accept an :class:`LvsReport` (the count is taken from
    :attr:`LvsReport.effective_discrepancies`), a bare ``int``, or ``None``.

    Precedence: "no previous run on record" is reported ahead of "this run's
    count is unknown", because it is the more actionable statement -- the
    first run of a cell has nothing to compare against no matter how well
    its report parsed.
    """

    cur = _as_count(current)
    prev = _as_count(previous)

    if prev is None:
        summary = (
            "No previous run on record for this DUT."
            if cur is None
            else f"No previous run on record for this DUT; {cur} discrepancies this run."
        )
        return DiscrepancyDelta(
            trend=DiscrepancyTrend.NO_BASELINE,
            current=cur,
            previous=None,
            delta=None,
            summary=summary,
        )

    if cur is None:
        return DiscrepancyDelta(
            trend=DiscrepancyTrend.UNKNOWN,
            current=None,
            previous=prev,
            delta=None,
            summary=(
                "This run's report states no discrepancy count, so it cannot be "
                f"compared against the previous {prev}."
            ),
        )

    delta = cur - prev
    if delta < 0:
        trend = DiscrepancyTrend.IMPROVED
        summary = f"Discrepancies fell from {prev} to {cur} ({delta})."
    elif delta == 0:
        trend = DiscrepancyTrend.UNCHANGED
        summary = f"Discrepancies unchanged at {cur}."
    else:
        trend = DiscrepancyTrend.REGRESSED
        summary = f"Discrepancies rose from {prev} to {cur} (+{delta})."

    return DiscrepancyDelta(
        trend=trend, current=cur, previous=prev, delta=delta, summary=summary
    )
