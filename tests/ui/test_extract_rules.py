"""The repeating ``extract`` sub-form. Artboards ``F1``-``F3``.

What is under test is a capability, not a widget: until this existed, the
vendor's own RF downgrade pattern -- whole chip at capacitance only, the nets
that matter at RC -- could not be expressed anywhere in this tool. The model
change alone did not deliver it; a list nobody can edit is still unreachable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.catalog import builtin_catalog  # noqa: E402
from auto_ext.model.recipe import ExtractRule, recipe_from_catalog  # noqa: E402
from auto_ext.ui.screens.recipes_screen import RecipesScreen  # noqa: E402
from auto_ext.ui.widgets.extract_rules import ExtractRulesEditor  # noqa: E402


def _editor(qtbot) -> ExtractRulesEditor:
    cat = builtin_catalog()
    widget = ExtractRulesEditor(
        selection_spec=cat.option("extract_selection"),
        type_spec=cat.option("extract_type"),
        field_path="extraction.extract",
    )
    qtbot.addWidget(widget)
    return widget


def _screen(qtbot) -> RecipesScreen:
    screen = RecipesScreen()
    qtbot.addWidget(screen)
    screen.set_recipes([recipe_from_catalog(recipe_id="r", name="R")])
    return screen


# ---- the operand -------------------------------------------------------------


def test_the_operand_box_appears_only_for_members_that_take_one(qtbot) -> None:
    """``all`` takes nothing; the other three take a pattern or a path.

    A combo that offered all four with no way to say *which* net produced a
    command line the tool rejects -- three quarters of the option unusable,
    and nothing on screen to explain why.
    """

    widget = _editor(qtbot)
    row = widget.rows()[0]

    row.selection_combo().setCurrentText("all")
    assert row.arg_edit().isVisibleTo(widget) is False

    row.selection_combo().setCurrentText("nets_file")
    assert row.arg_edit().isVisibleTo(widget) is True
    assert "file" in row.arg_edit().placeholderText()

    row.selection_combo().setCurrentText("net")
    assert "pattern" in row.arg_edit().placeholderText()


def test_switching_back_to_all_clears_the_operand(qtbot) -> None:
    """Otherwise the model refuses the rule over a field nobody can see."""

    widget = _editor(qtbot)
    row = widget.rows()[0]
    row.selection_combo().setCurrentText("nets_file")
    row.arg_edit().setText("clk.txt")

    row.selection_combo().setCurrentText("all")

    assert widget.value()[0] == {"selection": "all", "type": "rc_coupled"}
    assert ExtractRule(**widget.value()[0]).selection_arg is None


# ---- the list ----------------------------------------------------------------


def test_the_vendors_own_downgrade_pattern_is_expressible(qtbot) -> None:
    """The whole reason the list exists.

        extract -selection all             -type c_only_coupled
        extract -selection nets_file "clk" -type rc_coupled
    """

    widget = _editor(qtbot)
    first = widget.rows()[0]
    first.selection_combo().setCurrentText("all")
    first.type_combo().setCurrentText("c_only_coupled")

    widget.add_button().click()
    second = widget.rows()[1]
    second.selection_combo().setCurrentText("nets_file")
    second.arg_edit().setText("clk.txt")
    second.type_combo().setCurrentText("rc_coupled")

    assert widget.value() == [
        {"selection": "all", "type": "c_only_coupled"},
        {"selection": "nets_file", "type": "rc_coupled", "selection_arg": "clk.txt"},
    ]


def test_order_is_editable_and_visible(qtbot) -> None:
    """Order IS the semantics: the last rule wins for any net it covers.

    A list whose order the user can neither see nor change is a list whose
    meaning they cannot predict.
    """

    widget = _editor(qtbot)
    widget.add_button().click()
    widget.rows()[0].type_combo().setCurrentText("c_only_coupled")
    widget.rows()[1].type_combo().setCurrentText("r_only")

    widget.rows()[1]._up.click()

    assert [rule["type"] for rule in widget.value()] == ["r_only", "c_only_coupled"]
    assert "later rule overrides an earlier one" in widget.note_text()


def test_the_last_rule_cannot_be_removed(qtbot) -> None:
    """A recipe with no extract statement runs Quantus and extracts nothing.

    That reports as a successful extraction of a cell with no parasitics,
    which is the worst shape a wrong answer can take.
    """

    widget = _editor(qtbot)
    assert len(widget.rows()) == 1
    widget.rows()[0]._remove.click()
    assert len(widget.rows()) == 1
    assert widget.rows()[0]._remove.isEnabled() is False

    widget.add_button().click()
    assert widget.rows()[0]._remove.isEnabled() is True


def test_a_value_this_build_does_not_offer_is_still_shown(qtbot) -> None:
    """Snapping to the first entry would rewrite the recipe on open."""

    widget = _editor(qtbot)
    widget.set_value([{"selection": "all", "type": "some_future_type"}])
    assert widget.rows()[0].type_combo().currentText() == "some_future_type"


# ---- on the screen -----------------------------------------------------------


def test_the_screen_draws_one_sub_form_for_the_two_member_rows(qtbot) -> None:
    """``extract_selection`` and ``extract_type`` describe the same collection.

    Two controls would ask which of them is the real one -- the same "a row is
    drawn once" rule the rest of the form keeps, one level up.
    """

    screen = _screen(qtbot)
    assert screen.extract_rules_editor() is not None
    assert screen.editor("extract_type") is None
    assert screen.editor("extract_selection") is None


def test_editing_the_sub_form_reaches_the_recipe_and_marks_it_dirty(qtbot) -> None:
    screen = _screen(qtbot)
    widget = screen.extract_rules_editor()

    widget.add_button().click()
    widget.rows()[1].selection_combo().setCurrentText("net")
    widget.rows()[1].arg_edit().setText("clk*")
    widget.rows()[1].arg_edit().textEdited.emit("clk*")

    rules = screen.current_recipe().extraction.extract
    assert [r.selection.value for r in rules] == ["all", "net"]
    assert rules[1].selection_arg == "clk*"
    assert screen.is_dirty() is True


def test_a_rule_the_model_refuses_is_reported_and_rolled_back(qtbot) -> None:
    """An edit that silently does nothing is the failure this screen removes."""

    screen = _screen(qtbot)
    widget = screen.extract_rules_editor()
    said: list[str] = []
    screen.status_changed.connect(said.append)

    before = list(screen.current_recipe().extraction.extract)
    # ``net`` with no pattern: the model refuses it, and it must not land.
    widget.rows()[0].selection_combo().setCurrentText("net")

    assert screen.current_recipe().extraction.extract == before
    assert said and "refused" in said[-1]


def test_loading_another_recipe_repaints_the_sub_form(qtbot) -> None:
    screen = _screen(qtbot)
    other = recipe_from_catalog(recipe_id="two", name="Two")
    other.extraction.extract = [
        ExtractRule(selection="all", type="c_only_coupled"),
        ExtractRule(selection="nets_file", selection_arg="clk.txt", type="rc_coupled"),
    ]
    screen.set_recipes([screen.recipes()[0], other])

    screen.select_recipe("two")

    widget = screen.extract_rules_editor()
    assert len(widget.rows()) == 2
    assert widget.rows()[1].arg_edit().text() == "clk.txt"
