"""``scripts/probe_manual.py`` -- asking the manual what the catalog does not know.

The point of the script is that its question list cannot drift from the
catalog's. These tests are mostly about that: a probe whose queries were
maintained by hand would quietly stop asking about a row somebody added, and
nobody would notice, because "no output" and "no such question" look the same.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.refresh_extdoc_fields import build_table  # noqa: E402

FF = "\f"


def test_the_frozen_question_table_matches_the_catalog() -> None:
    """``extdoc_probe.py`` carries a FROZEN question list, and must.

    It runs in the red zone: no checkout to import, and the interpreter on
    PATH is Python 2.7. Frozen is not hand-maintained though -- the copy
    recovered from the August session was still asking twelve questions that
    had been answered and missed sixteen rows that had opened since. This
    guard says so instead of letting them drift for another four months.
    """

    from scripts.refresh_extdoc_fields import main as refresh

    assert refresh(["--check"]) == 0, (
        "run scripts/refresh_extdoc_fields.py and commit the result"
    )


def test_every_open_question_is_asked_with_something_to_search_for() -> None:
    from auto_ext.catalog import builtin_catalog

    table, count = build_table()
    open_keys = {opt.key for opt in builtin_catalog().options if opt.question}
    assert count == len(open_keys)
    for key in open_keys:
        assert repr(key) in table, key


def test_an_ambiguous_option_name_is_qualified_by_its_statement() -> None:
    """`-type` belongs to extract, metal_fill, input_db and output_db alike.

    Searching it bare finds all four and answers none.
    """

    table, _ = build_table()
    assert "'metal_fill -type'" in table
    assert "'output_db -type'" in table
    # ...and one that is NOT shared keeps its bare name.
    assert "'-use_field_solver'" in table


def test_a_row_that_emits_nothing_gets_a_term_the_manual_has_seen() -> None:
    """Its fallback would be `-{template_var}` -- a name we invented."""

    table, _ = build_table()
    assert "'global_nets'" in table
    assert "'-global_nets_nets'" not in table
