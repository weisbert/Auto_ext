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
source path) in logs and the GUI without re-parsing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
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

# CELL SUMMARY fallback: detect the section header, then scan its rows. Each
# row is a three-column line "<result> <layout> <source>" where result is
# CORRECT or INCORRECT. Used only when the DISCREPANCIES line is absent.
_RE_CELL_SUMMARY_HEADER = re.compile(r"CELL\s+SUMMARY", re.IGNORECASE)
_RE_CELL_SUMMARY_ROW = re.compile(
    r"^\s*(CORRECT|INCORRECT)\s+\S+\s+\S+\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _cell_summary_results(text: str) -> list[str] | None:
    """Extract CELL SUMMARY row verdicts (``CORRECT`` / ``INCORRECT``).

    Returns ``None`` if no CELL SUMMARY section is present (report likely
    truncated); an empty list if the header exists but no rows parse; else
    one uppercase verdict per matched row.
    """
    header = _RE_CELL_SUMMARY_HEADER.search(text)
    if header is None:
        return None
    return [m.upper() for m in _RE_CELL_SUMMARY_ROW.findall(text[header.end() :])]


@dataclass(frozen=True)
class LvsReport:
    """Structured view of a parsed LVS report."""

    passed: bool
    banner: str | None
    discrepancies: int | None
    source: Path


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

    if banner is None:
        raise CheckError(f"no LVS banner found; report truncated? source={report}")

    if banner == "INCORRECT":
        passed = False
    elif discrepancies is None:
        rows = _cell_summary_results(text)
        if rows and all(r == "CORRECT" for r in rows):
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
        "LVS report %s: banner=%s disc=%s -> pass=%s",
        report.name,
        banner,
        discrepancies,
        passed,
    )

    return LvsReport(
        passed=passed,
        banner=banner,
        discrepancies=discrepancies,
        source=report.resolve(),
    )
