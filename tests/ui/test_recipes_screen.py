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
* **the import entry hands over, it does not write.** ``Import...`` opens
  :class:`~auto_ext.ui.widgets.recipe_import_dialog.RecipeImportDialog` and
  confirming emits ``recipe_imported``; the screen's own library is asserted
  unchanged, because the host is still the only thing that touches disk.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtWidgets import QDialog, QMenu  # noqa: E402

from auto_ext.catalog import (  # noqa: E402
    Currently,
    Owner,
    Screen,
    Tier,
    builtin_catalog,
)
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
    DENSITY_ALL,
    DENSITY_COMMON,
    FLOW_TOOL,
    RECIPE_LIST_WIDTH,
    RecipesScreen,
    frozen_specs,
    form_layout,
    _set_path,
    import_status_text,
    recipe_specs,
)
from auto_ext.ui.widgets.option_editor import (  # noqa: E402
    BoolOptionEditor,
    ChoiceOptionEditor,
    FreeChoiceOptionEditor,
    MultiChoiceOptionEditor,
    NumberOptionEditor,
    TextOptionEditor,
)
from auto_ext.ui.widgets.patch_strip import HunkState  # noqa: E402
from auto_ext.ui.widgets.recipe_import_dialog import RecipeImportDialog  # noqa: E402

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


def test_level_one_is_the_tool_in_pipeline_order(qtbot) -> None:
    """Artboard ``M`` section 2.

    The old grouping was the first component of the Recipe field path --
    ``extraction`` / ``output`` / ``netlist`` -- which is the shape of our
    data model and of nothing the user has ever seen. They think in tools,
    and when a run fails the manual in their hand is that tool's.
    """

    tools = [tool.tool for tool in form_layout()]
    assert tools == ["si", "calibre", "quantus", "jivaro", FLOW_TOOL]
    assert [tool.label for tool in form_layout()][:3] == ["si", "Calibre LVS", "Quantus"]


def test_a_row_landing_in_two_files_is_drawn_once(qtbot) -> None:
    """Twenty-three Quantus rows write both command files.

    Drawing them twice would ask the user which copy is the real one.
    """

    keys = [spec.key for tool in form_layout() for spec in tool.specs]
    assert len(keys) == len(set(keys))
    assert set(keys) == {spec.key for spec in recipe_specs()}


def test_level_two_is_the_generated_files_own_section(qtbot) -> None:
    quantus = next(tool for tool in form_layout() if tool.tool == "quantus")
    labels = [section.label for section in quantus.sections]
    # From the section map, artboard L: renamed, merged and ordered.
    assert "extract" in labels
    assert "capacitance" in labels
    assert "extraction setup" in labels
    # Merged: filter_cap and filter_coupling_cap share capacitance's heading,
    # so the raw names never appear.
    assert "filter cap" not in labels
    # Ordered by the map, not alphabetically or by catalog order.
    orders = [section.order for section in quantus.sections]
    assert orders == sorted(orders)


def test_output_db_splits_by_the_format_it_writes(qtbot) -> None:
    """Artboard ``L``'s only ``split_by`` user, and section 5.3 of the brief.

    The vendor documents four DIFFERENT option sets under the one name
    ``output_db``. A single heading would promise the rows under it are
    interchangeable, and they are not.
    """

    quantus = next(tool for tool in form_layout() if tool.tool == "quantus")
    split = [s for s in quantus.sections if s.label.startswith("output_db")]
    assert len(split) > 1
    labels = {s.label for s in split}
    assert "output_db — dspf" in labels
    assert "output_db — extracted view" in labels
    assert "output_db — every format" in labels

    dspf = next(s for s in split if s.label.endswith("dspf"))
    assert all(spec.requires_emit == ["dspf"] for spec in dspf.specs)


def test_rows_with_no_landing_site_collect_under_flow(qtbot) -> None:
    flow = next(tool for tool in form_layout() if tool.tool == FLOW_TOOL)
    assert not any(spec.lands_in for spec in flow.specs)
    # The five decisions about the run rather than lines in a file.
    assert {"extraction_corner", "stages", "reduction_enabled"} <= {
        spec.key for spec in flow.specs
    }


def test_the_control_type_follows_the_catalog(qtbot) -> None:
    screen = _screen(qtbot)
    assert isinstance(screen.editor("min_res_ohm"), NumberOptionEditor)
    assert isinstance(screen.editor("exclude_self_cap"), BoolOptionEditor)
    # A closed value set -> a closed drop-down.
    assert isinstance(screen.editor("lvs_deck_variant"), ChoiceOptionEditor)
    # A guessed one -> still a drop-down, but editable: the guesses are worth
    # offering, and they are not worth trapping the user inside.
    simulator = screen.editor("netlist_simulator")
    assert isinstance(simulator, FreeChoiceOptionEditor)
    assert simulator.combo().isEditable() is True
    # A closed LIST -> one check box per member, not a comma-separated string.
    stages = screen.editor("stages")
    assert isinstance(stages, MultiChoiceOptionEditor)
    assert list(stages.check_boxes()) == [
        "si",
        "strmout",
        "calibre",
        "quantus",
        "jivaro",
    ]


def test_unconfirmed_rows_are_marked(qtbot) -> None:
    screen = _screen(qtbot)
    marked = set(screen.needs_confirmation_keys())
    expected = {spec.key for spec in recipe_specs() if spec.question}
    assert marked == expected
    assert marked, "the catalog claims every value has been confirmed"


# ---- rows the template still freezes -------------------------------------


def test_the_shipped_catalog_leaves_no_form_row_unsettable() -> None:
    """The state the parameterisation round was for, asserted on the form.

    Every row on this page is a row the user can actually change. The count is
    pinned catalog-wide in
    ``tests/catalog/test_catalog.py::test_no_owned_row_is_left_hardcoded``;
    this is the same fact stated where a user meets it.
    """

    assert frozen_specs() == []


def test_a_frozen_row_reaches_the_form_disabled_rather_than_missing(qtbot) -> None:
    """What the page does the day a row is added before its template hole.

    Not hidden: a field that vanishes reads as "this tool has no such
    setting", which is false and is the misunderstanding the catalog exists to
    end. Not editable either: the user would fill it in and find out at run
    time. Shown, disabled, and marked.
    """

    full = builtin_catalog()
    options = [
        opt.model_copy(update={"currently": Currently.HARDCODED_LITERAL})
        if opt.key == "decoupling_factor"
        else opt
        for opt in full.options
    ]
    screen = _screen(qtbot, full.model_copy(update={"options": options}))
    screen.set_recipes([make_recipe()])

    assert screen.frozen_option_keys() == ["decoupling_factor"]
    editor = screen.editor("decoupling_factor")
    assert editor is not None, "the row must still be on the page"
    assert editor.is_frozen is True
    assert editor.control().isEnabled() is False


def test_a_recipe_that_sets_a_frozen_row_is_named_on_the_status_line(qtbot) -> None:
    """The refusal, said on the page instead of by the runner.

    ``check_representable`` would fail this recipe's quantus stage. The screen
    knows the same thing from the same catalog column, so it says so while the
    user is still looking at the form.
    """

    full = builtin_catalog()
    options = [
        opt.model_copy(update={"currently": Currently.HARDCODED_LITERAL})
        if opt.key == "decoupling_factor"
        else opt
        for opt in full.options
    ]
    screen = _screen(qtbot, full.model_copy(update={"options": options}))

    recipe = make_recipe()
    recipe.extraction.decoupling_factor = 0.5
    screen.set_recipes([recipe])

    assert screen.frozen_overrides() == {"decoupling_factor": 0.5}
    status = screen.status_text()
    assert "decoupling_factor" in status
    assert "hardcode" in status
    # ...and the field still shows the literal the run would write.
    assert screen.editor("decoupling_factor").value() == 1.0


def test_a_recipe_that_agrees_with_the_literal_is_not_flagged(qtbot) -> None:
    full = builtin_catalog()
    options = [
        opt.model_copy(update={"currently": Currently.HARDCODED_LITERAL})
        if opt.key == "decoupling_factor"
        else opt
        for opt in full.options
    ]
    screen = _screen(qtbot, full.model_copy(update={"options": options}))
    screen.set_recipes([make_recipe()])

    assert screen.frozen_option_keys() == ["decoupling_factor"]
    assert screen.frozen_overrides() == {}
    assert "hardcode" not in screen.status_text()


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


# ---- the import entry ----------------------------------------------------
#
# The button is on the screen; what the dialog *shows* is asserted in
# tests/ui/test_recipe_import_dialog.py. What is asserted here is the seam:
# the entry exists, it opens a dialog that knows the library it is joining,
# and confirming hands the result out instead of writing it.


def _import_dialog(qtbot, screen, monkeypatch) -> RecipeImportDialog:
    """Click Import and return the dialog, without entering a modal loop."""

    monkeypatch.setattr(RecipeImportDialog, "exec_", lambda self: QDialog.Rejected)
    screen.import_button().click()
    dialog = screen.import_dialog()
    assert dialog is not None
    qtbot.addWidget(dialog)
    return dialog


def test_the_recipe_toolbar_offers_an_import_entry(qtbot) -> None:
    screen = _screen(qtbot)
    button = screen.import_button()
    assert button.text() == "Import…"
    assert button.isEnabled() is True, "importing needs no selection"

    screen.set_recipes([])
    assert screen.import_button().isEnabled() is True


def test_the_import_entry_does_not_squeeze_the_artboard_row(qtbot) -> None:
    """Artboard ``1f`` draws three buttons in a 214px panel and they fill it.

    A fourth on the same row would be clipped, so the new action sits on a
    second row: the drawn row is untouched and nothing loses its label.
    """

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    screen.show()
    qtbot.waitExposed(screen)

    drawn = (screen.new_button(), screen.duplicate_button(), screen.delete_button())
    assert len({button.y() for button in drawn}) == 1, "the drawn row broke apart"
    assert screen.import_button().y() > drawn[0].y()
    assert screen.import_button().sizeHint().width() <= RECIPE_LIST_WIDTH


def test_the_import_button_opens_a_dialog_that_knows_the_library(
    qtbot, monkeypatch
) -> None:
    """The id it proposes may not collide with a recipe already on disk."""

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe("rc-typical-55c", "RC typical 55C")])

    dialog = _import_dialog(qtbot, screen, monkeypatch)
    assert isinstance(dialog, RecipeImportDialog)
    assert dialog.page() == "files"
    dialog.name_edit().setText("RC typical 55C")
    assert dialog.recipe_id() == "rc-typical-55c-2"


def test_reopening_the_import_starts_from_an_empty_dialog(
    qtbot, monkeypatch, fixtures_dir
) -> None:
    """A dialog that reopens holding the last file is how one gets imported twice."""

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])

    first = _import_dialog(qtbot, screen, monkeypatch)
    first.add_paths([fixtures_dir / "raw" / "gui_export.ext.cmd"])
    assert len(first.file_rows()) == 1

    second = _import_dialog(qtbot, screen, monkeypatch)
    assert second is not first
    assert second.file_rows() == []


def test_confirming_the_import_hands_the_result_to_the_host(
    qtbot, monkeypatch, fixtures_dir, tmp_path
) -> None:
    """The screen writes no files -- it says what happened and hands it over."""

    screen = _screen(qtbot)
    library = [make_recipe()]
    screen.set_recipes(library)
    dialog = _import_dialog(qtbot, screen, monkeypatch)
    assert dialog.add_paths([fixtures_dir / "raw" / "gui_export.ext.cmd"]) == 1
    assert dialog.analyse() is True

    with qtbot.waitSignal(screen.recipe_imported, timeout=2000) as blocker:
        dialog.import_button().click()

    result = blocker.args[0]
    assert result is dialog.result_object()
    assert result.recipe.recipe_id != "rc-typical-55c"
    assert [recipe.recipe_id for recipe in screen.recipes()] == ["rc-typical-55c"], (
        "the screen adopted the imported recipe instead of handing it out"
    )
    assert list(tmp_path.rglob("*.yaml")) == []


def test_the_status_line_carries_all_three_import_numbers(
    qtbot, monkeypatch, fixtures_dir
) -> None:
    """Including the one the user will not remember being told: the defaults."""

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    dialog = _import_dialog(qtbot, screen, monkeypatch)
    dialog.add_paths([fixtures_dir / "raw" / "gui_export.ext.cmd"])
    assert dialog.analyse() is True

    seen: list[str] = []
    screen.status_changed.connect(seen.append)
    dialog.import_button().click()

    line = seen[-1]
    result = dialog.result_object()
    assert f"{result.applied_count} values" in line
    assert f"{result.hunk_count} manual edit" in line
    assert "left at the catalog default" in line
    assert import_status_text(result) == line


# ---- the two densities ---------------------------------------------------


def test_common_shows_only_the_common_tier(qtbot) -> None:
    """Artboard ``M`` section 3.

    Hiding is only allowable because nothing becomes unreachable: one
    always-visible toggle, and search that covers the whole catalog.
    """

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])

    assert screen.density() == DENSITY_COMMON
    shown = set(screen.visible_option_keys())
    every = set(screen.option_keys())
    assert shown < every
    assert len(shown) == sum(
        1 for spec in recipe_specs() if spec.tier is Tier.COMMON
    )
    # The settings a person changes from job to job are all there.
    assert {"extract_type", "temperature_c", "lvs_deck_variant"} <= shown
    # And the ones nobody has ever touched are not.
    assert "sub_node_char" not in shown

    screen.set_density(DENSITY_ALL)
    assert set(screen.visible_option_keys()) == every


def test_a_non_default_value_is_never_hidden(qtbot) -> None:
    """Rule 2, and the reason the split is safe.

    A Common view that omits a non-default value is a form lying about what
    the run will do. Tier does not get a vote.
    """

    recipe = make_recipe()
    spec = next(s for s in recipe_specs() if s.key == "sub_node_char")
    assert spec.tier is not Tier.COMMON
    _set_path(recipe, spec.recipe_field_path, "%")

    screen = _screen(qtbot)
    screen.set_recipes([recipe])

    assert "sub_node_char" in screen.promoted_keys()
    assert "sub_node_char" in screen.visible_option_keys()


def test_a_row_never_changes_parent_between_modes(qtbot) -> None:
    """What the toggle's "keep the focused row" behaviour depends on."""

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    common = {key: screen._section_of[key] for key in screen.visible_option_keys()}
    screen.set_density(DENSITY_ALL)
    for key, section in common.items():
        assert screen._section_of[key] == section


def test_a_tool_with_nothing_common_still_says_so(qtbot) -> None:
    """Artboard ``M`` section 5. si has no Common rows at all.

    A tool that vanished would read as a stage that is not being run, which
    is a different and much more alarming claim than "nothing here needs you".
    """

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    header = screen._tool_headers["si"].text()
    assert header.startswith("si")
    assert "all at the catalog default" in header


# ---- requires_emit -------------------------------------------------------


def test_rows_for_a_format_this_recipe_skips_are_disabled_not_hidden(qtbot) -> None:
    """Section 5.3 of the brief, and a correctness bug rather than a cosmetic one.

    ``output_db`` documents four DIFFERENT option sets, one per format.
    Rendering a dspf-only option into an extracted_view run writes a command
    file the tool rejects, hours into the job.
    """

    recipe = make_recipe()
    recipe.output.emit = ["extracted_view"]
    screen = _screen(qtbot)
    screen.set_recipes([recipe])

    assert screen.emitted_formats() == {"extracted_view"}
    off = screen.inapplicable_keys()
    assert "sub_node_char" in off, "a dspf-only row should be greyed"
    assert screen.editor("sub_node_char").isEnabled() is False
    # Disabled, NOT hidden: the option exists and the tool accepts it.
    screen.set_density(DENSITY_ALL)
    assert "sub_node_char" in screen.visible_option_keys()

    recipe.output.emit = ["extracted_view", "dspf"]
    screen.set_recipes([recipe])
    assert "sub_node_char" not in screen.inapplicable_keys()
    assert screen.editor("sub_node_char").isEnabled() is True


# ---- search --------------------------------------------------------------


def test_search_covers_the_whole_catalog_from_either_mode(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])
    assert screen.density() == DENSITY_COMMON

    keys = {spec.key for spec in screen.search_matches("delimiter")}
    assert "busbit_delimiter" in keys, "search must reach rows Common is hiding"
    assert "busbit_delimiter" not in screen.visible_option_keys()

    # It matches on the model path and on the generated option name too, not
    # only on the label -- those are what the user has in front of them when
    # they are reading a recipe file or a tool manual.
    assert "sub_node_char" in {
        spec.key for spec in screen.search_matches("output.dspf.sub_node")
    }
    assert "min_res_ohm" in {spec.key for spec in screen.search_matches("-min_res")}


def test_search_answers_for_settings_that_live_on_another_screen(qtbot) -> None:
    """Defect 7. The office report was "I cannot find where to rename the view".

    ``out_file`` is per-cell and belongs on the Cells screen. Returning
    nothing would teach the user the setting does not exist.
    """

    screen = _screen(qtbot)
    screen.set_recipes([make_recipe()])

    matches = screen.search_matches("out_file")
    elsewhere = [spec for spec in matches if spec.screen is Screen.CELLS]
    assert [spec.key for spec in elsewhere] == ["out_file"]

    screen.search_field().setText("out_file")
    assert "Cells screen" in screen._density_note.full_text()
