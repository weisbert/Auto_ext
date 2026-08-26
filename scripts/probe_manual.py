#!/usr/bin/env python3
"""Ask a vendor PDF the questions the catalog is still carrying.

Every catalog row with a ``question:`` is a thing nobody here knows. Some of
those are site decisions no manual can answer -- "do your schematics declare
their own globals" -- but most are documented facts we simply have not looked
up, and looking them up needs no licence, no run and nobody's time but the
reader's.

This script turns "go ask the office" into "run this, paste the output back".

WHY THE QUERIES COME FROM THE CATALOG
-------------------------------------
They are not a list maintained here. The script reads ``options.yaml``, takes
every row that still has an open question, and builds the search terms from
that row's own ``template_var`` and its landing sites. So a question that gets
answered and deleted stops being asked, a question added tomorrow is asked
tomorrow, and the two can never drift -- which is the same rule the form and
the renderer already follow.

WHERE IT RUNS
-------------
On the machine that has the PDF; here that is the office Linux box, which is
also the red zone. So:

* the PDF path is an ARGUMENT. Nothing in this file names a site path -- that
  is what ``redzone_scan.sh`` is for, and a script that hard-coded the
  manual's location could never be committed.
* output goes under ``private/``, which is gitignored, because vendor manual
  text must not enter a public repo.
* the shell there is csh and bare ``python`` is 2.7, which fails on the
  type hints below. Set ``PYTHON`` to the box's python3.11 -- the same
  override ``run.sh`` and ``install_offline.sh`` already take, and the
  reason none of them name an absolute path either::

      setenv PYTHON <abs path to python3.11>
      $PYTHON scripts/probe_manual.py \\
          --pdf <path to the manual> --out private/pdf_answers/round5.txt

TEXT EXTRACTION
---------------
Tries ``pdftotext`` first (poppler; usually present, and its ``-layout`` mode
keeps the option tables readable, which matters because the answers ARE
tables). Falls back to ``pypdf`` / ``PyPDF2`` if either imports. Says plainly
what to do when neither is available rather than producing empty output that
looks like "the manual does not mention it".

The extracted text is cached beside the output, because the manual is 700-odd
pages and a second question should not cost a second extraction.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Page break in ``pdftotext`` output.
_FORM_FEED = "\f"

#: How much of a hit to show. Enough for a syntax-table entry with its
#: restriction column, which is where the answers live; short enough that
#: twenty questions stay readable in one file.
_WINDOW = 700

#: Hits per question. Two was the previous rounds' budget and it proved
#: right -- the third hit was almost always another example, not another fact.
_HITS = 3


@dataclass
class Query:
    """One open question and the strings that would answer it."""

    key: str
    question: str
    terms: list[str] = field(default_factory=list)


def load_queries(only: set[str] | None = None) -> list[Query]:
    """Every catalog row still carrying a question, with its search terms.

    Terms, in the order they are tried:

    1. the generated option name (``-use_field_solver``) -- the strongest
       signal, because the manual's syntax tables are keyed on exactly that;
    2. the template variable, for rows whose option name we do not know
       because the templates do not emit them yet (``currently: absent`` --
       and those are precisely the rows with the most open questions);
    3. the catalog key, as a last resort.
    """

    from auto_ext.catalog import builtin_catalog

    out: list[Query] = []
    for opt in builtin_catalog().options:
        if not opt.question:
            continue
        if only and opt.key not in only:
            continue
        terms: list[str] = []
        for site in opt.lands_in:
            if site.option and site.option not in terms:
                terms.append(site.option)
        guess = f"-{opt.template_var}"
        if guess not in terms:
            terms.append(guess)
        if opt.template_var not in terms:
            terms.append(opt.template_var)
        out.append(Query(key=opt.key, question=opt.question.strip(), terms=terms))
    return out


def extract_text(pdf: Path, cache: Path) -> str:
    """The whole manual as text, one form feed per page. Cached."""

    if cache.is_file() and cache.stat().st_mtime >= pdf.stat().st_mtime:
        return cache.read_text(encoding="utf-8", errors="replace")

    text = _via_pdftotext(pdf) or _via_pypdf(pdf)
    if text is None:
        raise SystemExit(
            "cannot read the PDF: neither `pdftotext` nor pypdf/PyPDF2 is "
            "available.\n"
            "  - pdftotext is part of poppler-utils; try `which pdftotext` "
            "and any Cadence or system bin directory on PATH\n"
            "  - or add a pypdf wheel to the offline bundle and run "
            "scripts/install_offline.sh\n"
            "Refusing to continue: empty output would read as 'the manual "
            "does not mention it', which is the opposite of the truth."
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def _via_pdftotext(pdf: Path) -> str | None:
    exe = shutil.which("pdftotext")
    if exe is None:
        return None
    # -layout keeps the option tables in columns. Without it the restriction
    # column ("LVS input only") lands on a different line from the option it
    # restricts, which is exactly the fact most of these questions turn on.
    result = subprocess.run(
        [exe, "-layout", "-enc", "UTF-8", str(pdf), "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def _via_pypdf(pdf: Path) -> str | None:
    reader_cls = None
    for module, name in (("pypdf", "PdfReader"), ("PyPDF2", "PdfReader")):
        try:
            reader_cls = getattr(__import__(module), name)
            break
        except Exception:  # noqa: BLE001 - any import problem is "not available"
            continue
    if reader_cls is None:
        return None
    reader = reader_cls(str(pdf))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            pages.append("")
    return _FORM_FEED.join(pages)


def score(window: str, term: str) -> int:
    """How likely this hit is to be the ANSWER rather than a mention.

    A syntax table entry scores far above a prose mention, because the tables
    are where defaults and restrictions live. The signals are the ones those
    tables actually contain.
    """

    points = 0
    lowered = window.lower()
    if "syntax" in lowered:
        points += 8
    if "restriction" in lowered or "restricted to" in lowered:
        points += 8
    if "default" in lowered:
        points += 6
    if re.search(r"\[\s*true\s*\|\s*false", lowered):
        points += 5
    if "|" in window:  # an alternation, i.e. a value set
        points += 3
    # A line that STARTS with the option is the table row; one that mentions
    # it mid-sentence is prose about it.
    for line in window.splitlines():
        if line.strip().startswith(term):
            points += 6
            break
    return points


def search(text: str, term: str) -> list[tuple[int, int, str]]:
    """``(page, score, window)`` for every occurrence of ``term``."""

    hits: list[tuple[int, int, str]] = []
    for number, page in enumerate(text.split(_FORM_FEED), start=1):
        start = 0
        while True:
            at = page.find(term, start)
            if at < 0:
                break
            window = page[max(0, at - 120) : at + _WINDOW]
            hits.append((number, score(window, term), window))
            start = at + len(term)
    return hits


def report(queries: list[Query], text: str) -> str:
    lines: list[str] = [
        f"# manual probe -- {len(queries)} open catalog questions",
        "# generated by scripts/probe_manual.py from options.yaml",
        "# vendor manual text: keep this file under private/ (gitignored)",
        "",
    ]
    for query in queries:
        lines.append(f"===== {query.key} =====")
        lines.append(f"Q: {query.question}")
        found_any = False
        for term in query.terms:
            hits = search(text, term)
            if not hits:
                continue
            found_any = True
            hits.sort(key=lambda h: (-h[1], h[0]))
            lines.append(f"  [{term}] {len(hits)} hit(s), best {_HITS}:")
            for page, points, window in hits[:_HITS]:
                lines.append(f"  -- p.{page} (score {points}) --")
                lines.append(window.strip())
            break
        if not found_any:
            # Said out loud. "No hits" is a real answer -- it is how we learned
            # that -format does not belong to the calibre input form -- and it
            # must not look like the script skipped the row.
            lines.append(
                f"  NO HITS for any of {query.terms!r}. Either the option is "
                "spelled differently in this manual, or it belongs to another "
                "tool's manual, or the question is not a manual question."
            )
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pdf",
        required=True,
        help="path to the vendor manual (NOT stored here -- red-zone rule)",
    )
    parser.add_argument(
        "--out",
        default="private/pdf_answers/probe.txt",
        help="where to write the report; must stay under private/",
    )
    parser.add_argument(
        "--keys",
        default="",
        help="comma-separated catalog keys to ask about (default: all open)",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    if "private" not in out.parts:
        parser.error(
            "--out must be under private/: the report quotes vendor manual "
            "text, which must never enter a public repo"
        )

    pdf = Path(args.pdf).expanduser()
    if not pdf.is_file():
        parser.error(f"no such PDF: {pdf}")

    only = {k.strip() for k in args.keys.split(",") if k.strip()} or None
    queries = load_queries(only)
    if not queries:
        print("no open questions match; nothing to ask", file=sys.stderr)
        return 0

    text = extract_text(pdf, out.with_suffix(".cache.txt"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(queries, text), encoding="utf-8")
    print(f"{len(queries)} question(s) asked; report at {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
