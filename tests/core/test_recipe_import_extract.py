"""The ``extract`` statements, from a deck the user already has.

``extract`` is the one Quantus command that repeats, and the one whose reader
is not the catalog's. :func:`auto_ext.core.recipe_import.extract_rules_from_text`
is a dedicated parser, and until this file it had no direct test at all -- which
is how it came to accept exactly one of the two layouts the vendor's own manual
prints, and to say nothing when it accepted neither.

Four separate failures live here because they are one user's afternoon:

* a hand-written one-line ``extract -selection all -type c_only_coupled`` was
  invisible to the reader, so the recipe silently claimed ``rc_coupled``;
* an ``-type`` spelling the model does not know killed the whole import
  instead of degrading to a note, the way every other enum does;
* a value outside a value set the catalog itself marks ``guess`` was demoted
  to a patch hunk, although DECISIONS #19 says a guessed set is not authority;
* the report listed the extraction type twice, once from the collection and
  once from a readback row that only ever saw the last statement.

Every assertion here is on what the caller of :func:`import_recipe` can see:
the Recipe it returns and the report rows beside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core import recipe_import as ri
from auto_ext.model.common import RenderTarget
from auto_ext.model.recipe import ExtractSelection, ExtractType


@pytest.fixture
def raw_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "raw"


def _import(path: Path, *, recipe_id: str) -> ri.RecipeImportResult:
    return ri.import_recipe(
        [ri.ImportSource.from_path(path, target=RenderTarget.QUANTUS_EXT)],
        recipe_id=recipe_id,
    )


# ---- the reader itself -------------------------------------------------------

#: ``(label, statement text, expected rules)``. The layouts are the vendor's:
#: a statement head on its own line with backslash continuations, and a head
#: that carries its whole statement -- which is what
#: :func:`auto_ext.core.readback.parse_quantus` has always supported for
#: ``input_db -type calibre`` and what the manual's examples use for ``extract``.
EXTRACT_LAYOUTS: list[tuple[str, str, list[dict[str, object]]]] = [
    (
        "continued, quoted -- what this tool writes",
        'extract \\\n              -selection "all" \\\n              -type "rc_coupled"',
        [{"selection": "all", "type": "rc_coupled"}],
    ),
    (
        "one line, unquoted -- what a person types",
        "extract -selection all -type c_only_coupled",
        [{"selection": "all", "type": "c_only_coupled"}],
    ),
    (
        "one line, quoted",
        'extract -selection "all" -type "rc_coupled"',
        [{"selection": "all", "type": "rc_coupled"}],
    ),
    (
        "one line, an operand-bearing selection",
        'extract -selection net "CLK*" -type rc_coupled',
        [{"selection": "net", "selection_arg": "CLK*", "type": "rc_coupled"}],
    ),
    (
        "head carries the first option, the rest continue",
        'extract -selection all \\\n              -type "c_only_coupled"',
        [{"selection": "all", "type": "c_only_coupled"}],
    ),
    (
        "two statements, one line each -- order is the answer",
        "extract -selection all -type c_only_coupled\n"
        'extract -selection nets_file "clk.txt" -type rc_coupled',
        [
            {"selection": "all", "type": "c_only_coupled"},
            {"selection": "nets_file", "selection_arg": "clk.txt", "type": "rc_coupled"},
        ],
    ),
    (
        "two statements, both continued",
        'extract \\\n              -selection "all" \\\n              -type "c_only_coupled"\n'
        'extract \\\n              -selection "net" "CLK*" \\\n              -type "rc_coupled"',
        [
            {"selection": "all", "type": "c_only_coupled"},
            {"selection": "net", "selection_arg": "CLK*", "type": "rc_coupled"},
        ],
    ),
    (
        "extraction_setup is not an extract statement",
        'extraction_setup \\\n              -net_name_space "SCHEMATIC"',
        [],
    ),
    (
        "no extract statement at all",
        "capacitance \\\n              -ground_net \"vss\"",
        [],
    ),
]


@pytest.mark.parametrize(
    ("statement", "expected"),
    [pytest.param(text, rules, id=label) for label, text, rules in EXTRACT_LAYOUTS],
)
def test_extract_rules_are_read_out_of_every_layout_the_vendor_prints(
    statement: str, expected: list[dict[str, object]]
) -> None:
    assert ri.extract_rules_from_text(statement) == expected


# ---- a one-line deck ---------------------------------------------------------


def test_a_one_line_extract_statement_is_not_silently_replaced_by_the_default(
    raw_dir: Path,
) -> None:
    """The symptom: "I imported the .cmd my colleague hand-wrote. It came back
    as rc_coupled. His said c_only_coupled. Nothing warned me." """

    result = _import(raw_dir / "oneline_extract.ext.cmd", recipe_id="oneline")
    rules = result.recipe.extraction.extract
    assert [rule.type for rule in rules] == [ExtractType.C_ONLY_COUPLED]
    assert [rule.selection for rule in rules] == [ExtractSelection.ALL]


def test_two_one_line_extract_statements_keep_their_order(raw_dir: Path) -> None:
    """Whole chip cheap, the clock expensive -- and the last one wins, so a
    reader that reordered them would extract the wrong thing on every net."""

    result = _import(raw_dir / "two_stanza_extract.ext.cmd", recipe_id="two-stanza")
    rules = result.recipe.extraction.extract
    assert [(rule.selection, rule.selection_arg, rule.type) for rule in rules] == [
        (ExtractSelection.ALL, None, ExtractType.C_ONLY_COUPLED),
        (ExtractSelection.NET, "CLK*", ExtractType.RC_COUPLED),
    ]


def test_a_deck_with_no_readable_extract_statement_says_so(raw_dir: Path) -> None:
    """A bare ``continue`` is the one outcome a report cannot show: the rules
    fall back to the catalog default and the difference is pinned as a hunk
    that then wins over anything the user edits in the form."""

    text = (raw_dir / "oneline_extract.ext.cmd").read_text(encoding="utf-8")
    without = "\n".join(
        line for line in text.splitlines() if not line.startswith("extract -")
    )
    result = ri.import_recipe(
        [ri.ImportSource(label="no-extract.cmd", text=without, target=RenderTarget.QUANTUS_EXT)],
        recipe_id="no-extract",
    )
    notes = [row.note for row in result.mapped if row.key == "extract_selection"]
    assert notes, "the report must carry a row for the extract statements"
    assert any("no `extract` statement" in note for note in notes)


# ---- an unknown extract type -------------------------------------------------


def test_an_unknown_extract_type_degrades_instead_of_killing_the_import(
    raw_dir: Path,
) -> None:
    """"This deck ran last week." ``metal_fill -type actual`` degrades to a
    note plus a hunk; ``extract -type rcc`` used to abort the whole import,
    because the rules were assigned without passing the plausibility gate."""

    result = _import(raw_dir / "unknown_extract_type.ext.cmd", recipe_id="rcc")
    rows = [row for row in result.mapped if row.key == "extract_selection"]
    assert rows, "the report must say what happened to the extract statements"
    assert all(row.applied_to is None for row in rows)
    assert any("rcc" in row.note for row in rows)
    assert any("rcc" in warning for warning in result.warnings)
    assert result.as_patch, "the unread statement has to survive as a manual edit"


def test_a_value_outside_an_unverified_range_degrades_too(raw_dir: Path) -> None:
    """``exclude_floating_nets_limit`` is bounded [100, 100000] by a range the
    catalog itself marks ``range_verified: false``. An invented bound may not
    be the reason a real deck cannot be imported at all."""

    text = (raw_dir / "oneline_extract.ext.cmd").read_text(encoding="utf-8")
    text = text.replace("-exclude_floating_nets_limit 5000", "-exclude_floating_nets_limit 50")
    result = ri.import_recipe(
        [ri.ImportSource(label="limit.cmd", text=text, target=RenderTarget.QUANTUS_EXT)],
        recipe_id="limit",
    )
    row = next(row for row in result.mapped if row.key == "exclude_floating_nets_limit")
    assert row.applied_to is None
    assert "100" in row.note


# ---- a guessed value set is not authority ------------------------------------


def test_a_value_the_catalog_only_guessed_at_is_accepted_with_a_note(
    raw_dir: Path,
) -> None:
    """``extraction_net_name_space`` ships ``choices_confidence: guess`` and its
    own row admits the case of the members is unverified. DECISIONS #19 as
    revised makes such a set an editable combo -- the form honours that, and
    the importer must not answer differently."""

    text = (raw_dir / "oneline_extract.ext.cmd").read_text(encoding="utf-8")
    text = text.replace('-net_name_space "SCHEMATIC"', '-net_name_space "layout"')
    result = ri.import_recipe(
        [ri.ImportSource(label="ns.cmd", text=text, target=RenderTarget.QUANTUS_EXT)],
        recipe_id="ns",
    )
    row = next(row for row in result.mapped if row.key == "extraction_net_name_space")
    assert row.applied_to == "extraction.net_name_space"
    assert result.recipe.extraction.net_name_space == "layout"
    assert "guess" in row.note
    assert not [hunk for hunk in result.as_patch if "net_name_space" in hunk.summary]


def test_a_value_outside_a_certain_value_set_is_still_refused(raw_dir: Path) -> None:
    """The counterpart: ``extract_type`` is ``choices_confidence: certain`` and
    is a real enum in the model, so a spelling outside it stays a hunk. Only
    an admitted guess is advisory."""

    result = _import(raw_dir / "unknown_extract_type.ext.cmd", recipe_id="rcc-2")
    assert result.recipe.extraction.extract[0].type is ExtractType.RC_COUPLED


# ---- one row per key ---------------------------------------------------------


def test_the_report_names_the_extraction_type_once(raw_dir: Path) -> None:
    """Two stanzas used to produce three rows: the collection, plus a readback
    row per key showing only the LAST stanza's value as if it were the whole
    answer. Two of the three then disagreed with each other."""

    result = _import(raw_dir / "two_stanza_extract.ext.cmd", recipe_id="two-rows")
    for key in ("extract_selection", "extract_type"):
        rows = [row for row in result.mapped if row.key == key]
        assert len(rows) <= 1, f"{key} is reported {len(rows)} times: {rows}"
    landed = [row for row in result.mapped if row.applied_to == "extraction.extract"]
    assert len(landed) == 1
    assert "c_only_coupled" in str(landed[0].value)
    assert "rc_coupled" in str(landed[0].value)


def test_every_reported_note_is_a_string(raw_dir: Path) -> None:
    """``MappedValue.note`` is declared ``str``; ``None`` reached it through the
    extract block and every consumer that formats a report had to survive it."""

    result = _import(raw_dir / "two_stanza_extract.ext.cmd", recipe_id="notes")
    assert all(isinstance(row.note, str) for row in result.mapped)
