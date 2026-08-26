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

from scripts.probe_manual import load_queries, main, report, score  # noqa: E402

FF = "\f"


def test_the_questions_are_the_catalogs_own() -> None:
    """Derived, never maintained here.

    A row whose question gets answered stops being asked; one added tomorrow
    is asked tomorrow. Two lists that have to be kept in step are two lists
    that will not be.
    """

    from auto_ext.catalog import builtin_catalog

    open_keys = {opt.key for opt in builtin_catalog().options if opt.question}
    assert {query.key for query in load_queries()} == open_keys
    assert open_keys, "the catalog has no open questions at all -- suspicious"


def test_every_query_carries_something_to_search_for() -> None:
    for query in load_queries():
        assert query.terms, query.key
        assert all(term.strip() for term in query.terms), query.key


def test_the_generated_option_name_is_tried_first() -> None:
    """The manual's syntax tables are keyed on exactly that string.

    The template variable is the fallback, and it is what rows that emit
    nothing yet have to fall back TO -- which is most of the open ones.
    """

    query = next(q for q in load_queries() if q.key == "metal_fill_type")
    assert query.terms[0].startswith("-")


def test_a_syntax_table_outranks_a_passing_mention() -> None:
    table = (
        "extract Command Syntax Input Restrictions\n"
        " -use_field_solver [ none | default_accuracy ] default: none\n"
    )
    prose = "You may wish to consider whether -use_field_solver is appropriate.\n"
    assert score(table, "-use_field_solver") > score(prose, "-use_field_solver")


def test_no_hits_is_reported_as_an_answer_not_as_silence() -> None:
    """"The manual does not mention it" is a FINDING.

    It is how we learned ``-format`` does not belong to the calibre input
    form. A row that silently produced nothing would read as a row the script
    skipped.
    """

    queries = [q for q in load_queries() if q.key == "cap_component"]
    text = report(queries, "a manual that says nothing relevant")
    assert "NO HITS" in text
    assert "cap_component" in text


def test_the_report_refuses_to_write_outside_private(tmp_path: Path) -> None:
    """Vendor manual text must never enter a public repo."""

    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(SystemExit):
        main(["--pdf", str(pdf), "--out", str(tmp_path / "leaked.txt")])


def test_a_missing_pdf_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--pdf", str(tmp_path / "nope.pdf"), "--out", "private/x.txt"])


def test_hits_are_reported_with_their_page(monkeypatch, tmp_path: Path) -> None:
    """Page numbers are the whole point: an answer nobody can go check is a
    rumour."""

    queries = [q for q in load_queries() if q.key == "merge_parallel_via"]
    text = FF.join(
        [
            "page one, nothing here",
            "page two, nothing here",
            "filter_res Command Syntax\n -merge_parallel_via [ true | false ]",
        ]
    )
    out = report(queries, text)
    assert "-- p.3 " in out
