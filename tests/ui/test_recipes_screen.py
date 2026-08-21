"""Recipes screen: the form is generated, the escape hatch is visible, it fits.

The three claims worth defending:

* **generated, not written.** ``test_the_form_is_the_catalog`` builds the
  screen against a cut-down catalog and asserts the form is exactly that cut
  -- in both directions. A hand-written field list passes the first half of
  that test and fails the second.
* **the working copy is a copy.** Every edit lands on a deep copy, so a
  half-finished change cannot leak into the library the host still holds.
* **it fits.** Artboard ``1j`` fixes the window floor at 940x560. The old
  Project tab alone demanded 1001px of height; this screen is asserted well
  under both halves of the budget so the floor is actually reachable.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtWidgets import QMenu  # noqa: E402

from auto_ext.catalog import Owner, builtin_catalog  # noqa: E402
from auto_ext.core.patch_models import (  # noqa: E402
    BaseFingerprint,
    HunkOutcome,
    PatchHunk,
    PatchStatus,
    Stage,
    StagePatchReport,
    TemplatePatch,
)
from auto_ext.model.recipe import Recipe, recipe_from_catalog  # noqa: E402
from auto_ext.ui.screens.recipes_screen import (  # noqa: E402
    GROUP_ORDER,
    RecipesScreen,
    grouped_specs,
    recipe_specs,
)
from auto_ext.ui.widgets.option_editor import (  # noqa: E402
    BoolOptionEditor,
    ChoiceOptionEditor,
    NumberOptionEditor,
    TextOptionEditor,
)
from auto_ext.ui.widgets.patch_strip import HunkState  # noqa: E402

_SHA = "0" * 64
_CAPTURED = datetime(2026, 8, 14, 9, 30, tzinfo=timezone.utc)


def make_recipe(recipe_id: str = "rc-typical-55c", name: str = "RC typical 55C") -> Recipe:
    return recipe_from_catalog(recipe_id=recipe_id, name=name)


def make_patch(hunk_ids: tuple[str, ...] = ("0000000a",)) -> TemplatePatch:
    return TemplatePatch(
        stage=Stage.QUANTUS,
        template_id="quantus/ext.cmd.j2",
        base=BaseFingerprint(
            template_sha256=_SHA, masked_sha256=_SHA, captured_at=_CAPTURED
        ),
        hunks=[
            PatchHunk(
                id=hunk_id,
                before="              -decoupling_factor 1.0 \\\n",
                after="              -decoupling_factor 0.8 \\\n",
                context_before="  capacitance \\\n",
                context_after='              -ground_net "${ground_net}"\n',
                intent="lower for the DCO tank",
            )
            for hunk_id in hunk_ids
        ],
    )


def _screen(qtbot, catalog=None) -> RecipesScreen:
    screen = RecipesScreen(catalog)
    qtbot.addWidget(screen)
    return screen


# ---- the sizing contract -------------------------------------------------


def test_the_screen_fits_inside_the_window_floor(qtbot) -> None:
    """Artboard ``1j``: nothing may demand more than 940x560 on its own.

    The Recipes screen is the densest page in the application -- ninety
    catalog rows, a library list and the escape hatch -- so if any screen is
    going to blow the floor it is this one.
    """

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.show()
    qtbot.waitExposed(screen)

    hint = screen.minimumSizeHint()
    assert hint.width() <= 700, f"the screen demands {hint.width()}px of width"
    assert hint.height() <= 400, f"the screen demands {hint.height()}px of height"


def test_it_still_fits_with_the_escape_hatch_open(qtbot) -> None:
    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.patches = [make_patch(("0000000a", "0000000b"))]
    screen.set_recipes([recipe])
    screen.show()
    qtbot.waitExposed(screen)
    screen.patch_strip.set_expanded(True)

    hint = screen.minimumSizeHint()
    assert hint.width() <= 700
    assert hint.height() <= 400


# ---- the form is the catalog ---------------------------------------------


def test_the_form_is_the_catalog(qtbot) -> None:
    """Cut the catalog down and the form is exactly the cut.

    Both directions matter. A hand-written field list would still show the
    rows it was told about (first assertion) and would keep showing the ones
    the catalog no longer has (second).
    """

    full = builtin_catalog()
    keys = ["min_res_ohm", "netlist_simulator", "lvs_deck_variant"]
    subset = [opt for opt in full.options if opt.key in keys]
    assert len(subset) == len(keys), "the fixture keys moved"

    small = full.model_copy(update={"options": subset})
    screen = _screen(qtbot, small)

    assert sorted(screen.option_keys()) == sorted(keys)
    for gone in ("metal_fill_type", "reduction_enabled"):
        assert screen.editor(gone) is None


def test_a_new_catalog_row_becomes_a_new_form_row(qtbot) -> None:
    full = builtin_catalog()
    without = full.model_copy(
        update={"options": [o for o in full.options if o.key != "min_res_ohm"]}
    )
    assert _screen(qtbot, without).editor("min_res_ohm") is None
    assert _screen(qtbot, full).editor("min_res_ohm") is not None


def test_every_recipe_bound_catalog_row_is_on_the_form(qtbot) -> None:
    screen = _screen(qtbot)
    expected = {spec.key for spec in recipe_specs()}
    assert set(screen.option_keys()) == expected
    assert len(expected) > 50, "the catalog shrank unexpectedly"


def test_rows_that_bind_to_nothing_are_left_out() -> None:
    """The ``absent`` proposals name no Recipe field, so there is nothing to edit."""

    catalog = builtin_catalog()
    unbound = [
        opt
        for opt in catalog.by_owner(Owner.RECIPE)
        if opt.recipe_field_path is None
    ]
    assert unbound, "the fixture this rule exists for has disappeared"
    shown = {spec.key for spec in recipe_specs(catalog)}
    assert not (shown & {opt.key for opt in unbound})


def test_groups_follow_the_recipe_field_path(qtbot) -> None:
    groups = dict(grouped_specs())
    assert {"extraction", "output", "lvs", "reduction", "netlist"} <= set(groups)
    for spec in groups["extraction"]:
        assert (spec.recipe_field_path or "").startswith("extraction.")


def test_group_order_follows_the_artboard(qtbot) -> None:
    names = [name for name, _specs in grouped_specs()]
    known = [name for name in names if name in GROUP_ORDER]
    assert known == [name for name in GROUP_ORDER if name in names]
    assert names[:3] == ["extraction", "output", "lvs"]


def test_a_group_header_names_the_files_its_options_land_in(qtbot) -> None:
    screen = _screen(qtbot)
    header = screen.group("extraction").header_text()
    assert header.startswith("Extraction")
    assert "quantus/ext.cmd.j2" in header


def test_the_control_type_follows_the_catalog(qtbot) -> None:
    screen = _screen(qtbot)
    assert isinstance(screen.editor("min_res_ohm"), NumberOptionEditor)
    assert isinstance(screen.editor("exclude_self_cap"), BoolOptionEditor)
    # The only enum in the catalog whose value set is better than a guess.
    assert isinstance(screen.editor("lvs_deck_variant"), ChoiceOptionEditor)
    # ... and one whose value set is a guess, which must stay free text.
    assert isinstance(screen.editor("netlist_simulator"), TextOptionEditor)


def test_unconfirmed_rows_are_marked(qtbot) -> None:
    screen = _screen(qtbot)
    marked = set(screen.needs_confirmation_keys())
    expected = {spec.key for spec in recipe_specs() if spec.question}
    assert marked == expected
    assert marked, "the catalog claims every value has been confirmed"


# ---- selection and loading -----------------------------------------------


def test_selecting_a_recipe_loads_its_values(qtbot) -> None:
    screen = _screen(qtbot)
    first = make_recipe("rc-typical-55c", "RC typical 55C")
    second = make_recipe("lvs-only", "LVS only")
    second.extraction.min_res_ohm = 0.5

    screen.set_recipes([first, second])
    assert screen.current_recipe_id() == "rc-typical-55c"
    assert screen.is_dirty() is False

    with qtbot.waitSignal(screen.recipe_selected, timeout=1000) as blocker:
        screen.select_recipe("lvs-only")
    assert blocker.args == ["lvs-only"]
    assert screen.editor("min_res_ohm").value() == pytest.approx(0.5)
    assert screen.is_dirty() is False, "loading a recipe marked it dirty"


def test_an_empty_library_disables_the_form(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([])
    assert screen.current_recipe() is None
    assert screen.save_button().isEnabled() is False
    assert screen.delete_button().isEnabled() is False


def test_the_list_badges_the_recipes_that_carry_manual_edits(qtbot) -> None:
    screen = _screen(qtbot)
    plain = make_recipe("plain", "Plain")
    edited = make_recipe("edited", "Edited")
    edited.patches = [make_patch(("0000000a", "0000000b"))]
    screen.set_recipes([plain, edited])

    assert screen.recipe_list.topLevelItem(0).text(1) == ""
    assert screen.recipe_list.topLevelItem(1).text(1) == "2"


# ---- editing -------------------------------------------------------------


def test_editing_a_field_writes_to_the_working_copy_only(qtbot) -> None:
    """The library object the host handed in must survive an abandoned edit."""

    screen = _screen(qtbot)
    original = make_recipe()
    screen.set_recipes([original])

    editor = screen.editor("min_res_ohm")
    editor.line_edit().setText("0.25")
    editor.line_edit().textEdited.emit("0.25")

    assert screen.current_recipe().extraction.min_res_ohm == pytest.approx(0.25)
    assert original.extraction.min_res_ohm != pytest.approx(0.25)


def test_editing_marks_the_screen_dirty_and_names_the_field(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])

    with qtbot.waitSignal(screen.dirty_changed, timeout=1000) as blocker:
        screen.editor("exclude_self_cap").check_box().setChecked(False)
    assert blocker.args == [True]

    assert screen.is_dirty() is True
    assert screen.changed_field_paths() == ["extraction.exclude_self_cap"]
    assert "1 field changed, not saved" in screen.status_text()


def test_two_edits_are_counted_as_two(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.editor("exclude_self_cap").check_box().setChecked(False)
    screen.editor("merge_parallel_res").check_box().setChecked(False)
    assert len(screen.changed_field_paths()) == 2
    assert "2 fields changed, not saved" in screen.status_text()


def test_a_value_the_model_rejects_is_marked_and_not_applied(qtbot) -> None:
    """The recipe keeps the old value, so the screen has to say the text is not it."""

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    editor = screen.editor("exclude_floating_nets_limit")
    before = screen.current_recipe().extraction.exclude_floating_nets_limit

    editor.line_edit().setText("3")
    editor.line_edit().textEdited.emit("3")

    assert editor.is_invalid() is True
    assert screen.current_recipe().extraction.exclude_floating_nets_limit == before


def test_saving_hands_out_the_working_copy(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.editor("exclude_self_cap").check_box().setChecked(False)

    with qtbot.waitSignal(screen.save_requested, timeout=1000) as blocker:
        screen.save_button().click()
    saved = blocker.args[0]
    assert isinstance(saved, Recipe)
    assert saved.extraction.exclude_self_cap is False


def test_save_and_revert_are_only_offered_when_there_is_something_to_do(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    assert screen.save_button().isEnabled() is False
    assert screen.revert_button().isEnabled() is False

    screen.editor("exclude_self_cap").check_box().setChecked(False)
    assert screen.save_button().isEnabled() is True
    assert screen.revert_button().isEnabled() is True


def test_revert_puts_the_loaded_values_back(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.editor("exclude_self_cap").check_box().setChecked(False)

    with qtbot.waitSignal(screen.revert_requested, timeout=1000) as blocker:
        screen.revert_button().click()
    assert blocker.args == ["rc-typical-55c"]
    assert screen.is_dirty() is False
    assert screen.editor("exclude_self_cap").value() is True


def test_library_buttons_name_the_current_recipe(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])

    with qtbot.waitSignal(screen.new_requested, timeout=1000):
        screen.new_button().click()
    with qtbot.waitSignal(screen.duplicate_requested, timeout=1000) as dup:
        screen.duplicate_button().click()
    assert dup.args == ["rc-typical-55c"]
    with qtbot.waitSignal(screen.delete_requested, timeout=1000) as delete:
        screen.delete_button().click()
    assert delete.args == ["rc-typical-55c"]


# ---- the escape hatch on the screen --------------------------------------


def test_the_strip_shows_the_selected_recipe_s_edits(qtbot) -> None:
    screen = _screen(qtbot)
    plain = make_recipe("plain", "Plain")
    edited = make_recipe("edited", "Edited")
    edited.patches = [make_patch(("0000000a", "0000000b"))]
    screen.set_recipes([plain, edited])

    assert screen.patch_strip.hunk_count() == 0
    screen.select_recipe("edited")
    assert screen.patch_strip.hunk_count() == 2
    assert screen.patch_strip.summary_text() == "This recipe has 2 manual edits"


def test_a_run_report_reaches_the_rows(qtbot) -> None:
    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.patches = [make_patch()]
    screen.set_recipes([recipe])
    screen.set_patch_reports(
        [
            StagePatchReport(
                stage=Stage.QUANTUS,
                template_id="quantus/ext.cmd.j2",
                outcomes=[
                    HunkOutcome(
                        hunk_id="0000000a", status=PatchStatus.ABSORBED, start_line=8
                    )
                ],
            )
        ]
    )
    assert screen.patch_strip.state_of("0000000a") is HunkState.NOOP


def test_reverting_a_hunk_drops_it_from_the_working_copy(qtbot) -> None:
    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.patches = [make_patch(("0000000a", "0000000b"))]
    screen.set_recipes([recipe])

    with qtbot.waitSignal(screen.patch_revert_requested, timeout=1000) as blocker:
        screen.patch_strip.hunk_rows()[0].revert_button().click()
    assert blocker.args == ["quantus", "quantus/ext.cmd.j2", "0000000a"]

    assert screen.current_recipe().manual_edit_count == 1
    assert screen.patch_strip.hunk_count() == 1
    assert screen.is_dirty() is True
    assert recipe.manual_edit_count == 2, "the library object was mutated"


def test_dropping_the_last_hunk_drops_the_file_with_it(qtbot) -> None:
    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.patches = [make_patch()]
    screen.set_recipes([recipe])
    screen.patch_strip.hunk_rows()[0].revert_button().click()
    assert screen.current_recipe().patches == []


def test_reverting_every_hunk_clears_the_escape_hatch(qtbot) -> None:
    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.patches = [make_patch(("0000000a", "0000000b"))]
    screen.set_recipes([recipe])
    screen.patch_strip.set_expanded(True)

    with qtbot.waitSignal(screen.patch_revert_all_requested, timeout=1000) as blocker:
        screen.patch_strip.revert_all_button().click()
    assert blocker.args == ["rc-typical-55c"]
    assert screen.current_recipe().patches == []
    assert screen.patch_strip.is_expanded() is False


def test_edit_rendered_names_the_recipe(qtbot) -> None:
    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.patches = [make_patch()]
    screen.set_recipes([recipe])
    with qtbot.waitSignal(screen.edit_rendered_requested, timeout=1000) as blocker:
        screen.patch_strip.edit_button().click()
    assert blocker.args == ["rc-typical-55c"]


def test_opening_the_hatch_folds_the_form_into_one_line(qtbot) -> None:
    """Artboard ``1g``: the diff owns the height, the options become a summary."""

    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.extraction.min_res_ohm = 0.25
    recipe.patches = [make_patch()]
    screen.set_recipes([recipe])
    screen.show()
    qtbot.waitExposed(screen)

    assert screen.options_summary_bar().isVisible() is False
    screen.patch_strip.set_expanded(True)
    assert screen.options_summary_bar().isVisible() is True
    assert "min_res_ohm 0.25" in screen.options_summary_text()

    screen.patch_strip.set_expanded(False)
    assert screen.options_summary_bar().isVisible() is False


def test_clicking_the_summary_line_brings_the_form_back(qtbot) -> None:
    screen = _screen(qtbot)
    recipe = make_recipe()
    recipe.patches = [make_patch()]
    screen.set_recipes([recipe])
    screen.show()
    qtbot.waitExposed(screen)
    screen.patch_strip.set_expanded(True)

    qtbot.mouseClick(screen.options_summary_bar(), Qt.LeftButton)
    assert screen.patch_strip.is_expanded() is False
    assert screen.options_summary_bar().isVisible() is False


def test_an_untouched_recipe_summarises_as_defaults(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    assert screen.options_summary_text() == "all catalog defaults"


# ---- chrome --------------------------------------------------------------


def test_the_header_says_how_many_cells_use_the_recipe(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.set_usage_counts({"rc-typical-55c": 5})
    assert "used by 5 cells" in screen.header_meta_text()
    assert "last edited" in screen.header_meta_text()

    screen.set_usage_counts({"rc-typical-55c": 1})
    assert "used by 1 cell" in screen.header_meta_text()


def test_the_status_line_reports_the_selection(qtbot) -> None:
    screen = _screen(qtbot)
    with qtbot.waitSignal(screen.status_changed, timeout=1000):
        screen.set_recipes([make_recipe()])
    assert screen.status_text() == "recipe RC typical 55C — saved"


def test_the_recipe_list_context_menu_is_deferred_one_tick(qtbot, monkeypatch) -> None:
    """X11 delivers the context-menu event on *press*.

    A synchronous ``exec_()`` is dismissed by the release that follows, and
    the user has to right-click twice. Every menu in this application is
    deferred one event-loop tick for that reason.
    """

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.show()
    qtbot.waitExposed(screen)

    calls: list[object] = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: calls.append(a))

    rect = screen.recipe_list.visualItemRect(screen.recipe_list.topLevelItem(0))
    screen.recipe_list.customContextMenuRequested.emit(rect.center())
    assert calls == [], "the menu popped synchronously inside the press handler"

    qtbot.wait(10)
    assert len(calls) == 1


def test_the_context_menu_ignores_empty_space(qtbot, monkeypatch) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.show()
    qtbot.waitExposed(screen)

    calls: list[object] = []
    monkeypatch.setattr(QMenu, "exec_", lambda self, *a, **k: calls.append(a))
    screen.recipe_list.customContextMenuRequested.emit(QPoint(5, 5000))
    qtbot.wait(10)
    assert calls == []


def test_the_selected_recipe_is_reachable_from_the_list_widget(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe("a", "A"), make_recipe("b", "B")])
    screen.recipe_list.setCurrentItem(screen.recipe_list.topLevelItem(1))
    assert screen.current_recipe_id() == "b"
    assert screen.recipe_list.currentItem().data(0, Qt.UserRole) == "b"
