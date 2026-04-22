"""Calibre LVS report parser with strict pass/fail classification.

Strict criterion:
- Banner ``INCORRECT`` is authoritative: result is fail regardless of counts.
- Banner ``CORRECT`` plus ``DISCREPANCIES = 0`` is the only true pass.
- Banner ``CORRECT`` plus non-zero or missing ``DISCREPANCIES`` counts as fail
  with a WARNING (the report is internally inconsistent; refusing to pass it
  is safer than trusting the banner).
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

# "DISCREPANCIES = N" — appears in both passing (= 0) and failing reports,
# so we must parse the count, not just the presence.
_RE_DISCREPANCIES = re.compile(r"DISCREPANCIES\s*=\s*(\d+)", re.IGNORECASE)


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
        logger.warning(
            "LVS report %s has banner CORRECT but no DISCREPANCIES count; "
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
