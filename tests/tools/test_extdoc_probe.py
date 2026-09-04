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

    Both specimens are picked by predicate rather than named. This test used
    to name ``-use_field_solver`` as its example of a name nobody shares, and
    went red the day somebody read the manual and deleted that row's
    ``question:`` -- an answered question is progress, not a regression. Every
    row the table carries is an open question, so which rows exist changes
    constantly; what must not change is that a shared name is qualified by its
    statement and an unshared one is not.
    """

    from auto_ext.catalog import builtin_catalog

    from scripts.refresh_extdoc_fields import OVERRIDE

    catalog = builtin_catalog()
    # Counted per row, the way build_table counts it.
    seen: dict[str, int] = {}
    for opt in catalog.options:
        for name in {site.option for site in opt.lands_in if site.option}:
            seen[name] = seen.get(name, 0) + 1

    table, _ = build_table()
    # OVERRIDE rows have their terms replaced wholesale, so they say nothing
    # about how a derived term is spelled.
    asked = [opt for opt in catalog.options if opt.question and opt.key not in OVERRIDE]

    shared = {
        (site.section, site.option)
        for opt in asked
        for site in opt.lands_in
        if site.option and seen.get(site.option, 0) > 1
    }
    assert shared, "no open question lands on an option name that two rows share"
    for section, option in sorted(shared):
        assert repr(f"{section} {option}") in table
        assert repr(option) not in table, f"{option} is shared and must not be searched bare"

    # ...and one that is NOT shared keeps its bare name.
    bare = {
        site.option
        for opt in asked
        for site in opt.lands_in
        if site.option and seen.get(site.option, 0) == 1
    }
    assert bare, "no open question lands on an option name of its own"
    for option in sorted(bare):
        assert repr(option) in table


def test_a_row_that_emits_nothing_gets_a_term_the_manual_has_seen() -> None:
    """Its fallback would be `-{template_var}` -- a name we invented."""

    table, _ = build_table()
    assert "'global_nets'" in table
    assert "'-global_nets_nets'" not in table


def test_the_python_2_guards_are_still_in_place() -> None:
    """The script prints Chinese, and the red-zone box's bare ``python`` is 2.7.

    Two lines make that combination work, and both were dropped once already
    when the script was recovered from a session scratchpad:

    * ``unicode_literals`` -- without it, ``cmd_ask`` joins a byte str holding
      the field table's Chinese with the unicode read back out of the text
      cache, and py2 tries to decode the former as ASCII.
    * ``_force_utf8_stdio`` -- the red-zone shell is usually ``LANG=C``, so the
      first Chinese thing printed raises ``UnicodeEncodeError``. On py3 too.

    Neither can be caught by running this suite, which is py3 under a UTF-8
    locale -- hence checking the source rather than the behaviour.
    """

    src = (REPO_ROOT / "scripts" / "extdoc_probe.py").read_text(encoding="utf-8")

    assert "from __future__ import unicode_literals" in src
    assert "_force_utf8_stdio()" in src
    # The guards are pointless if the literals they protect are gone, so pin
    # the reason too: this file is expected to carry non-ASCII.
    assert any(ord(ch) > 0x2FFF for ch in src), "no CJK left -- guards may be moot"


def test_binary_regexes_stayed_bytes_under_unicode_literals() -> None:
    """``unicode_literals`` does not touch prefixed literals -- but a later
    edit that drops a ``b`` prefix would break PDF parsing on both pythons,
    silently, only in the no-poppler fallback path nobody runs locally."""

    src = (REPO_ROOT / "scripts" / "extdoc_probe.py").read_text(encoding="utf-8")

    body = src.split("def _pure_python", 1)[0]
    for marker in ("stream\r?\n", "FlateDecode"):
        assert 'br"' in body or "br'" in body, marker
    assert "_TJ_ITEM = re.compile(br" in src
