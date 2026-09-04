"""Whole decks the user already owns, and the four ways one was lost.

The four have one shape: the reader recovered the value and something after it
threw the value away. A runset that does not run the QRC query said it did; a
runset asking for eight turbo cores had the eight read, printed in the report
and then dropped on the floor; a Jivaro XML produced a recipe that lists the
reduction stage and disables it in the same breath; and two classes of real
file were refused with one sentence that named neither reason.

The assertions are on the object :func:`auto_ext.core.recipe_import.import_recipe`
hands back -- the Recipe, the resources beside it, the report rows, and the
error text a refusal puts in front of the user.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core import recipe_import as ri
from auto_ext.model.common import RenderTarget, Stage

#: The ``*lvsPostTriggers`` line as this tool writes it, and the same line with
#: the third trigger taken off -- which is exactly what a site that does not
#: want the query_output dump edits out by hand.
WITH_QUERY = (
    "*lvsPostTriggers: {{rm -rf %d/query_output} process 1} "
    "{{mkdir %d/query_output} process 1} {{calibre -query_input "
    "/w/fake/verify/runset/Calibre_QRC/QRC/Ver_QRC_B/CFXXX/QCI_deck/query_cmd "
    "-query svdb } process 1}"
)
WITHOUT_QUERY = (
    "*lvsPostTriggers: {{rm -rf %d/query_output} process 1} "
    "{{mkdir %d/query_output} process 1}"
)


@pytest.fixture
def golden_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "golden"


@pytest.fixture
def raw_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "raw"


def _qci(golden_dir: Path, *, replace: tuple[str, str] | None = None) -> str:
    text = (golden_dir / "lvs.qci").read_text(encoding="utf-8")
    if replace is not None:
        old, new = replace
        assert old in text, f"the golden runset no longer carries {old!r}"
        text = text.replace(old, new)
    return text


def _import_qci(text: str, *, recipe_id: str) -> ri.RecipeImportResult:
    return ri.import_recipe(
        [ri.ImportSource(label="lvs.qci", text=text, target=RenderTarget.LVS_QCI)],
        recipe_id=recipe_id,
    )


# ---- a runset that does not run the QRC query --------------------------------


def test_a_runset_without_the_query_trigger_imports_as_not_running_it(
    golden_dir: Path,
) -> None:
    """"I imported a runset that doesn't run the QRC query. The tool says it
    does, and unticking the box does nothing."

    The value was recovered correctly and then discarded, because the line it
    is read from is shared with three other catalog rows and shared lines are
    refused wholesale. A row with a ``SPECIAL_READBACK`` handler is the one
    kind that takes only its own share of such a line."""

    result = _import_qci(
        _qci(golden_dir, replace=(WITH_QUERY, WITHOUT_QUERY)), recipe_id="no-query"
    )
    assert result.recipe.lvs.run_qrc_query is False
    row = next(row for row in result.mapped if row.key == "run_qrc_query")
    assert row.applied_to == "lvs.run_qrc_query"
    assert not [hunk for hunk in result.as_patch if "lvsPostTriggers" in hunk.summary]


def test_a_runset_that_does_run_the_query_still_imports_as_running_it(
    golden_dir: Path,
) -> None:
    """The other half of the same reading -- a presence rule that always said
    True would have passed the test above by accident."""

    result = _import_qci(_qci(golden_dir), recipe_id="with-query")
    assert result.recipe.lvs.run_qrc_query is True


# ---- resource-owned values ---------------------------------------------------


def test_the_turbo_core_count_in_a_runset_is_not_thrown_away(
    golden_dir: Path,
) -> None:
    """"My runset says 8 turbo cores." It was read, printed in the report, and
    dropped: the result object had nowhere to put a resource-owned value."""

    result = _import_qci(
        _qci(golden_dir, replace=("*cmnNumTurbo: 2", "*cmnNumTurbo: 8")),
        recipe_id="turbo",
    )
    assert result.resources.lvs_num_turbo == 8
    row = next(row for row in result.mapped if row.key == "lvs_num_turbo")
    assert "resources.lvs_num_turbo" in row.note


def test_the_resources_a_deck_says_nothing_about_keep_the_caller_s_own(
    golden_dir: Path,
) -> None:
    """A file that does not mention a setting is not a file that sets it to the
    default -- the caller's machine profile has to survive the import."""

    from auto_ext.model.recipe import ResourceProfile

    mine = ResourceProfile(resource_id="mine", max_workers=6)
    result = ri.import_recipe(
        [
            ri.ImportSource(
                label="lvs.qci", text=_qci(golden_dir), target=RenderTarget.LVS_QCI
            )
        ],
        recipe_id="mine",
        resources=mine,
    )
    assert result.resources.max_workers == 6
    assert result.resources.resource_id == "mine"


# ---- the jivaro stage --------------------------------------------------------


def test_importing_the_jivaro_xml_enables_the_stage_it_declares(
    golden_dir: Path,
) -> None:
    """"I imported the Jivaro XML and the reduction stage doesn't run."

    ``recipe.stages`` says the stage is part of the flow;
    ``recipe.reduction.enabled`` is what the runner actually gates on. Setting
    one without the other is a recipe that declares a stage it has disabled."""

    text = (golden_dir / "jivaro.xml").read_text(encoding="utf-8")
    result = ri.import_recipe(
        [ri.ImportSource(label="jivaro.xml", text=text, target=RenderTarget.JIVARO_XML)],
        recipe_id="jivaro",
    )
    assert Stage.JIVARO in result.recipe.stages
    assert result.recipe.reduction.enabled is True


def test_a_deck_without_a_jivaro_file_does_not_enable_reduction(
    golden_dir: Path,
) -> None:
    """The counterpart: nothing about importing a Quantus deck says the user
    owns a Jivaro licence."""

    result = _import_qci(_qci(golden_dir), recipe_id="no-jivaro")
    assert Stage.JIVARO not in result.recipe.stages
    assert result.recipe.reduction.enabled is False


# ---- files the importer refuses ----------------------------------------------


def test_a_column_zero_legacy_deck_is_refused_by_name(raw_dir: Path) -> None:
    """"Bring me your existing deck and I'll import it -- except it won't."

    Every option line at column 0 with no continuation is a real layout: a
    Quantus command file section header is decided by indentation, so each
    ``-option value`` becomes its own section and its value is discarded. That
    may well be the right refusal, but the message has to say which fact about
    the file decided it."""

    with pytest.raises(ri.RecipeImportError) as exc:
        ri.import_recipe(
            [ri.ImportSource.from_path(raw_dir / "legacy_column0.ext.cmd")],
            recipe_id="legacy",
        )
    message = str(exc.value)
    assert "column 0" in message
    assert "-selection" in message or "-decoupling_factor" in message


def test_a_deck_spelling_out_an_env_var_says_which_one(raw_dir: Path) -> None:
    """The second refusal: a file carrying ``$env(SETUP_ROOT)`` cannot be
    rendered back, because this tool writes decks with every path resolved. The
    error has to name the variables and how to supply them, not report a
    renderer's rule about si and jivaro."""

    with pytest.raises(ri.RecipeImportError) as exc:
        ri.import_recipe(
            [ri.ImportSource.from_path(raw_dir / "quantus_sample.cmd")],
            recipe_id="envy",
        )
    message = str(exc.value)
    assert "SETUP_ROOT" in message and "VERIFY_ROOT" in message
    assert "--env" in message


def test_a_deck_spelling_out_an_env_var_imports_once_the_value_is_supplied(
    raw_dir: Path,
) -> None:
    """And the refusal has to be one the user can act on: naming the values is
    all it takes, because ``$env(SETUP_ROOT)/x`` and the path it expands to are
    the same deck."""

    result = ri.import_recipe(
        [ri.ImportSource.from_path(raw_dir / "quantus_sample.cmd")],
        recipe_id="envy-ok",
        resolved_env={"SETUP_ROOT": "/w/fake/setup", "VERIFY_ROOT": "/w/fake/verify"},
    )
    assert result.recipe.extraction.extract[0].type.value == "rc_coupled"
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical, result.roundtrip[
        RenderTarget.QUANTUS_EXT
    ].diff


def test_left_at_the_default_never_names_a_key_the_recipe_holds(
    golden_dir: Path,
) -> None:
    """One answer to "what stayed at the catalog default", for every surface.

    A key the literal reader could not read is not thereby a key the import
    left alone. ``*cmnVConnectNamesState: ALL`` is a boolean spelled as a word,
    so the reader files it under ``unread`` -- and the presence reader gets it
    right and puts it in the recipe. Listing it under "left at the catalog
    default" contradicts the section above, which says where it went. The CLI
    filtered such keys out; the import dialog did not, which is two answers to
    one question about one import.
    """

    text = _qci(
        golden_dir,
        replace=("*lvsReportOptions: S", "*cmnVConnectNamesState: ALL\n*lvsReportOptions: S"),
    )
    result = _import_qci(text, recipe_id="agree")
    landed = {row.key for row in result.mapped if row.applied_to}
    assert "lvs_connect_by_name" in result.unread
    assert "lvs_connect_by_name" in landed
    assert "lvs_connect_by_name" not in result.left_at_default
    assert not set(result.left_at_default) & landed
    assert set(result.left_at_default) == set(result.unread) - landed


def test_the_env_solver_does_not_answer_a_reference_with_itself(
    raw_dir: Path,
) -> None:
    """``$env(SETUP_ROOT)/assura_tech.lib`` matched against itself used to bind
    ``SETUP_ROOT = "$env(SETUP_ROOT)"``. That is not a value; it is the
    question, and holding it made the import believe the variable was known."""

    solutions, _notes = ri.solve_env_from_file(
        (raw_dir / "quantus_sample.cmd").read_text(encoding="utf-8"),
        target=RenderTarget.QUANTUS_EXT,
        profile=None,
    )
    for solution in solutions:
        assert "$" not in solution.value, f"{solution.name} was bound to {solution.value!r}"
