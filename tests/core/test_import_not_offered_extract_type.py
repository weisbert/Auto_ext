"""Importing a colleague's RLCK deck: degrade, and say so in one line.

The deck exists. Somebody in this office ran ``extract -type rlck_coupled``
against a real cell, and their ``.cmd`` file is a legitimate thing to hand this
tool. Three answers were available and two of them are wrong:

* **accept it** -- the recipe then claims an extraction the templates cannot
  render, which is the silent success the 2026-09-04 ruling refused;
* **refuse the file** -- one unoffered member kills an import that read
  eighty other options correctly, which is what ``extract -type rcc`` used to
  do before the degrading path existed;
* **degrade and report** -- the rules fall back to the catalog default, the
  user's own statement survives as a manual edit, and the report says which
  statement, why, and what it became.

Only the third leaves the user able to act. This file pins the report line,
because the line *is* the fix: a degrade nobody is told about is
indistinguishable from the accept case a week later.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core import recipe_import as ri
from auto_ext.model.common import RenderTarget
from auto_ext.model.recipe import ExtractType


@pytest.fixture
def rlck_deck(fixtures_dir: Path) -> str:
    """The shipped one-line-extract deck, retyped to ``rlck_coupled``.

    Built by substitution rather than as a second fixture file so it stays
    provably identical to the deck the other import tests read -- the only
    difference under test is the one word.
    """

    text = (fixtures_dir / "raw" / "oneline_extract.ext.cmd").read_text(encoding="utf-8")
    assert "-type c_only_coupled" in text
    return text.replace("-type c_only_coupled", "-type rlck_coupled")


def _import(text: str, *, recipe_id: str) -> ri.RecipeImportResult:
    return ri.import_recipe(
        [ri.ImportSource(label="rlck.cmd", text=text, target=RenderTarget.QUANTUS_EXT)],
        recipe_id=recipe_id,
    )


def test_an_rlck_deck_imports_as_the_catalog_default_instead_of_failing(
    rlck_deck: str,
) -> None:
    """The import completes and the recipe holds a type the tool can render."""

    result = _import(rlck_deck, recipe_id="rlck-deck")
    assert result.recipe.extraction.extract[0].type is ExtractType.RC_COUPLED


def test_the_report_names_the_member_the_reason_and_what_it_became(
    rlck_deck: str,
) -> None:
    """The four things a user needs to decide whether the degrade was right.

    Which statement, which value, why it is not offered here, and what the
    recipe now says instead. Anything less and the only way to find out is to
    diff the rendered deck against the file they started from.
    """

    result = _import(rlck_deck, recipe_id="rlck-report")
    rows = [row for row in result.mapped if row.key == "extract_selection"]
    assert rows, "the report must carry a row for the extract statements"
    note = rows[0].note

    assert "extract statement 1" in note
    assert "rlck_coupled" in note
    assert "is not offered here" in note
    assert "-ind_component" in note, "the report must name the missing contract"
    assert "2026-09-04" in note, "the report must cite the ruling"
    assert "imported as 'rc_coupled'" in note
    assert "edit if that is wrong" in note

    assert rows[0].applied_to is None, (
        "a row that claims it landed on a field the recipe does not hold is "
        "worse than one that says it did not land"
    )


def test_the_users_own_statement_survives_as_a_manual_edit(rlck_deck: str) -> None:
    """Degrading may not throw the file away.

    The difference between the catalog default and what the author wrote goes
    where every other unmodelled difference in this importer goes -- into the
    patch -- so the deck can still be reproduced byte for byte.
    """

    result = _import(rlck_deck, recipe_id="rlck-patch")
    assert result.as_patch, "the unoffered statement has to survive somewhere"
    assert any("rlck_coupled" in warning for warning in result.warnings), (
        "a degrade the warnings do not mention is a silent degrade"
    )
