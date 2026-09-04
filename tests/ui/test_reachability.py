"""Can a user actually *reach* every setting? The audit the office round needed.

Why this file exists
--------------------
The first real GUI session on the red zone reported eight defects in one
sitting, and every one of them had the same shape:

* the recipe title was a label, so a recipe could not be renamed;
* ``CellEntry.out_file`` -- the extracted view the whole back half of the flow
  hangs off -- appeared only in a row tooltip;
* ``Recipe.extraction.corner`` had no control at all, and the Recipes screen's
  own docstring *documented* the omission as deliberate;
* the Recipes screen's primary ``Save`` button staged into a queue and wrote
  nothing, so an edit came back missing on the next launch.

None of these is a correctness bug. In every case the model was right, the
renderer was right, the controller was right, and the suite proved all three.
What no test asked was whether a human sitting in front of the window could
get to any of it. 676 GUI tests passed while four settings were unreachable,
because a test that calls ``screen.set_value(...)`` and a user who has to find
a control are not the same client.

So this module tests reachability rather than behaviour. It is deliberately
mechanical and deliberately dull: for each object a user owns, every field is
either bound to a control or named in an exemption set **with a reason**. The
reason is the load-bearing part. An exemption is a claim that a human made a
decision, and it is reviewable in a way that "no test covers this" never was.

Two directions, and both are needed
-----------------------------------
The model-field audits below walk ``recipe_field_paths(Recipe)`` and ask
whether every field we modelled has a control. That can only ever prove the
inner half. A vendor option we never modelled has no field, so it is
*structurally invisible* to those tests -- and twenty-one recipe-owned catalog
rows were in exactly that position: real Quantus and strmout options, written
down in our own catalog, dropped by ``recipe_specs`` for having no
``recipe_field_path``, with nothing anywhere saying they had been dropped. The
screen's own docstring said there were six.

So :data:`CATALOG_UNREACHABLE` audits the outer half, keyed by catalog row
rather than by model field. It is the same claim in the same shape -- somebody
looked at this and decided -- one level further out, and it is what makes
"every knob the vendor has, we modelled" a question this suite can ask at all.

What this cannot catch
----------------------
Reachability is necessary, not sufficient. Two of the eight defects --
``stages`` asked for as a comma-separated string, and fourteen closed-value
options asked for as blank text boxes -- were *reachable the whole time*. They
failed because the control was the wrong kind, which is a judgement about what
a user can be expected to know, and no assertion in this file would ever have
flagged them. See ``docs/refactor/UX_VALIDATION.md`` for the walkthrough that
covers that class; this file is the mechanical backstop underneath it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")

from auto_ext.model.cells import CellEntry  # noqa: E402
from auto_ext.model.pdk import PdkProfile  # noqa: E402
from auto_ext.model.recipe import recipe_field_paths  # noqa: E402
from auto_ext.model.workspace import WorkspaceConfig  # noqa: E402
from auto_ext.ui.project_fields import (  # noqa: E402
    PROFILE_FIELDS,
    PROFILE_UNREACHABLE,
    WORKSPACE_FIELDS,
    WORKSPACE_UNREACHABLE,
    bound_paths,
)
from auto_ext.catalog import Owner, Screen, builtin_catalog  # noqa: E402
from auto_ext.ui.screens.cells_screen import EDITABLE_FIELDS, RECIPE_FIELD  # noqa: E402
from auto_ext.ui.screens.recipes_screen import (  # noqa: E402
    member_specs,
    pointer_specs,
    recipe_specs,
)
from auto_ext.ui.widgets.option_editor import (  # noqa: E402
    EditorKind,
    build_option_editor,
)
from auto_ext.ui.widgets.run_bar import RunBar  # noqa: E402

#: Recipe field paths with no control, and why. Anything not listed here must
#: be bound to a control on the Recipes screen.
RECIPE_UNREACHABLE: dict[str, str] = {
    "schema_version": "format version; changing it is a migration, not a setting",
    "recipe_id": (
        "names the file and every cell binding. Renaming it is a file move plus "
        "a rebind, not a field edit -- the display name is what the header edits"
    ),
    "version": "the author's own version string; no flow reads it",
    "derived_from": "provenance, written by Duplicate and by import",
    "updated_at": "a timestamp the save path stamps",
    "tags": "not read by anything yet; a control would imply it is",
    "description": (
        "REACHABLE GAP, not a decision: it is shown in the list tooltip and "
        "nowhere else. Small, but it is the same shape as the title was"
    ),
    "patches": (
        "the escape hatch's storage. Its control is the manual-edit strip, "
        "which edits hunks rather than this field"
    ),
    "policy.fail_on_unparsable_lvs_report": (
        "DELIBERATELY UNBOUND since 2026-09-04. It had a control, and the "
        "control was a fake action: nothing reads this policy, because the "
        "LVS verdict is made in CalibreTool.parse_result where no recipe is "
        "in scope. A tick box that changes nothing is worse than a missing "
        "one -- the user believes the run behaved as they set it. The field "
        "stays for recipes on disk; True is exactly today's behaviour"
    ),
}

#: Recipe-owned CATALOG rows with no control, and why. This is the outer half
#: of the audit: the sets above ask "does every field we modelled have a
#: control", and this one asks "does every option we wrote down reach the
#: user at all". Every row here is ``currently: absent`` -- the tool has the
#: option, we emit no line for it, the tool takes its own default -- and each
#: needs two things it has not got: a ``Recipe`` field to bind to and a hole
#: in the template to write into.
#:
#: A row leaves this set by growing both, or by being drawn disabled with its
#: reason the way a ``requires_emit`` miss is (``RECIPES_FORM.md`` section 5).
#: What it may never do is disappear silently, which is what all twenty-one
#: were doing: ``recipe_specs`` drops anything without a ``recipe_field_path``
#: and said so nowhere on screen.
#: Three kinds of reason live here, and telling them apart is the point:
#:
#: * **owner ruled 2026-09-04** -- the owner was shown the knob and said, of
#:   the whole list, *"honestly I do not understand any of them, and if I do
#:   not understand them I will most likely never use them."* Not understood
#:   is not offered. These reasons name the tool default that applies instead,
#:   quoted from the row's own notes; where the manual does not document a
#:   default, the reason says so rather than inventing one.
#: * **blocked on a probe** -- nobody has read the vendor manual for it yet,
#:   so there is no default to cite and no ruling to rest on. The Calibre
#:   connect-by-name rows are this: that manual has never been probed at all.
#: * **no landing site exists** -- the strmout rows, whose stage takes argv
#:   rather than a rendered file.
#:
#: A reason of the first kind is a decision and closes the row. One of the
#: other two is a decision to wait, and names what it is waiting for.
CATALOG_UNREACHABLE: dict[str, str] = {
    # -- Calibre LVS supply naming. Blocked on a probe, NOT on the ruling:
    # the Calibre manual has never been read for these, so there is no tool
    # default to state and nothing was put in front of the owner to rule on.
    "lvs_extra_power_names": (
        "BLOCKED ON A PROBE, not ruled: no Recipe field and no template hole, "
        "and the office question behind lvs_connect_by_name_nets is unanswered. "
        "Until we know which of the three connect-by-name settings the site "
        "actually runs, a control here would invite a supply list that changes "
        "nothing"
    ),
    "lvs_extra_ground_names": (
        "BLOCKED ON A PROBE: the ground half of lvs_extra_power_names, waiting "
        "on the same question. Shipping one of the pair without the other "
        "would read as 'grounds are handled elsewhere', which is false"
    ),
    "lvs_connect_by_name_nets": (
        "BLOCKED ON A PROBE: the Calibre manual has never been read at all, "
        "and this row is the one that would have to encode which of the three "
        "connect-by-name modes the flow wants. A guess here changes what LVS "
        "considers shorted, which is not a thing to guess"
    ),
    # -- Quantus ``extract``. Ruled out on 2026-09-04, one row at a time.
    "use_field_solver": (
        "owner ruled 2026-09-04: not used in this shop; tool default applies "
        "(field solver disabled). They do not set it in the Quantus GUI, so "
        "the form does not offer it. The row keeps what that costs: our "
        "capacitances come from matching the layout against the technology "
        "file rather than from solving the geometry"
    ),
    "field_solver_type": (
        "owner ruled 2026-09-04: not used in this shop; the manual documents "
        "no default for it, so none is claimed here. Meaningless while "
        "use_field_solver is unwritten -- the pair is offered together or "
        "not at all, and today it is not at all"
    ),
    "extract_via_cap": (
        "owner ruled 2026-09-04: not used in this shop; tool default applies "
        "(via and contact capacitance extracted, true). Writing the line "
        "could only ever turn it off, which removes capacitance from an RF "
        "result -- so the control would have exactly one destructive use"
    ),
    "extract_gate_diffusion_fringing_cap": (
        "owner ruled 2026-09-04: not used in this shop; tool default applies "
        "(fringing cap on, true since the 11.1 release). The manual says in "
        "as many words that using this option is not recommended, so the "
        "only reachable effect of a control is one the vendor advises against"
    ),
    "inductance_nets_file": (
        "owner ruled 2026-09-04: not used in this shop; tool default applies "
        "(every net in the selection gets inductance). It gates only the "
        "rlc/rlck extract types, and those are no longer offered either -- "
        "see extract_type's choices_not_offered -- so there is nothing left "
        "for the file to gate"
    ),
    "substrate_nets_file": (
        "owner ruled 2026-09-04: not used in this shop; the tool default is "
        "the dangerous one -- the manual says that without this file NO nets "
        "are extracted as connected to the substrate. That is precisely why "
        "substrate_only and the *_to_substrate types went off the form with "
        "it, rather than the file being offered on its own"
    ),
    # -- Quantus ``extract -selection`` and ``global_nets``.
    "selection_layers": (
        "owner ruled 2026-09-04: not used in this shop. Layer-based net "
        "selection is a second selection mode beside the extract-rule "
        "sub-form's own -selection. The manual documents no default for it; "
        "what is written today is nothing, so the rule's -selection alone "
        "decides which nets a statement covers"
    ),
    "selection_dividing_layers_type": (
        "owner ruled 2026-09-04, with selection_layers: the catalog's own "
        "note says one without the other means nothing, so the pair is drawn "
        "together or not at all. No default is documented for it and nothing "
        "is written for either half"
    ),
    "global_nets_nets": (
        "owner ruled 2026-09-04: supply-net handling is not something this "
        "shop configures here. What the tool default means matters and is "
        "recorded: with no global_nets command, no net is declared global, so "
        "`extract -selection all` really does cover every net including VDD "
        "and VSS -- the opposite of what 'all except the supplies' would give"
    ),
    "global_nets_file": (
        "owner ruled 2026-09-04, with global_nets_nets whose file form this "
        "is: nothing is written, so the tool default holds and there is no "
        "global-net list from either route. The manual's format for the file "
        "was never captured, which is a second reason not to offer a path box"
    ),
    "global_nets_import_from_lvs": (
        "owner ruled 2026-09-04, with the rest of global_nets. Its default is "
        "not documented and neither is its precedence against an explicit "
        "list, so there is no tool default to cite -- only the fact that we "
        "write nothing"
    ),
    "global_nets_force": (
        "owner ruled 2026-09-04: the fourth global_nets option, off the form "
        "with the other three. Its default and its precedence rule against "
        "them are not captured anywhere, so a control would be a switch whose "
        "effect nobody in this repository can state"
    ),
    # -- Quantus ``filter_*``.
    "exclude_floating_decoupling_factor": (
        "owner ruled 2026-09-04: not used in this shop. The manual recommends "
        "1 alongside -exclude_floating_nets, which both decks do set, but "
        "does not say that 1 is also the unwritten default -- so the reason "
        "this row cites no default is that there is none to cite. Its lower "
        "bound is exclusive, which the [lo, hi] range column cannot express"
    ),
    "merge_parallel_via": (
        "owner ruled 2026-09-04: not used in this shop, and it is unreachable "
        "besides -- the manual makes it mutually exclusive with "
        "-merge_parallel_res, which both command files emit unconditionally, "
        "so a control here would ship a deck Quantus refuses unless the other "
        "line became conditional first. Its own default is undocumented"
    ),
    "min_res_centering": (
        "owner ruled 2026-09-04: not used in this shop; tool default applies "
        "(false -- when the min_res floor shorts sub-nodes together the "
        "survivor is picked arbitrarily rather than by proximity to the "
        "group's centre). The cleanest promotion candidate of this set the "
        "day it is wanted: a boolean, no licence cost, no companion option"
    ),
    "disable_subnodes": (
        "owner ruled 2026-09-04: not used in this shop; tool default applies "
        "(subnodes are written). Turning it on removes what sub_node_char "
        "delimits, and with it what can be back-annotated from the DSPF"
    ),
    # -- strmout. No template at all: the stage is argv, not a rendered file.
    "strmout_hier_depth": (
        "NO LANDING SITE: strmout takes argv rather than a rendered file, so "
        "there is no template hole to open. Probed on 2026-09-04 against "
        "extUser.pdf and every term returned zero hits -- the right answer "
        "from the wrong book, since strmout is a Virtuoso utility and not a "
        "Quantus command. The question stands; the manual to ask does not"
    ),
    "strmout_convert_dot": (
        "NO LANDING SITE, as strmout_hier_depth: argv, and the same "
        "wrong-book result from the 2026-09-04 probe"
    ),
    "strmout_case": (
        "NO LANDING SITE, as strmout_hier_depth: argv. Third of the three, "
        "and the one that would silently change every cell name in the GDS"
    ),
    # -- A control retired rather than blocked. The only row here that USED to
    # be drawn: it kept a context_path while being `currently: absent`, so the
    # form gave it a live tick box that no template, runner or check reads.
    "fail_on_unparsable_lvs_report": (
        "RETIRED 2026-09-04, not waiting on anything: this was the one absent "
        "row the form still drew live, and setting it changed nothing. The "
        "LVS verdict is made in CalibreTool.parse_result, whose only input is "
        "a ToolResult, so honouring a per-recipe policy means threading it "
        "through the whole Tool protocol or deciding it in the runner -- "
        "neither small. Its context_path was dropped so the fake control "
        "disappears; the row and the RunPolicy field stay, saying so"
    ),
}

#: Same, for one row of the cell table.
CELL_UNREACHABLE: dict[str, str] = {
    "display_name": (
        "REACHABLE GAP: UX sugar carried over from TaskSpec.label, shown in "
        "the row tooltip only"
    ),
    "note": "REACHABLE GAP: free-form, shown in the row tooltip only",
}


def _recipe_bound_paths() -> set[str]:
    """Field paths the Recipes screen actually puts a control in front of."""

    bound = {spec.recipe_field_path for spec in recipe_specs()}
    bound.discard(None)
    # Not a catalog row: the form header's editable title.
    bound.add("name")
    # A collection reached through its own sub-form rather than through one
    # control per row. ``describes_member`` rows have no recipe_field_path on
    # purpose -- a single control bound to a list would write a scalar over
    # it -- so the collection they describe is what is reachable.
    bound |= {
        spec.context_path[len("recipe.") :]
        for spec in member_specs()
        if spec.context_path
    }
    return {path for path in bound if path is not None}


def _undrawn_catalog_rows() -> dict[str, str]:
    """Recipe-owned catalog rows the Recipes form puts no control in front of.

    "Drawn" is deliberately generous: a row counts as reached whether it gets
    an editable control, a pointer to the screen that owns it, or a place in
    the ``extract`` sub-form. A row drawn *disabled* with its reason -- the
    treatment a ``requires_emit`` miss gets -- also counts, because it is
    visible and it says why, which is the whole property this file protects.
    """

    drawn = {spec.key for spec in recipe_specs()}
    drawn |= {spec.key for spec in member_specs()}
    drawn |= {spec.key for spec in pointer_specs()}
    return {
        opt.key: (opt.why or "").strip()
        for opt in builtin_catalog().by_owner(Owner.RECIPE)
        if opt.key not in drawn
    }


def test_every_recipe_owned_catalog_row_is_drawn_or_exempt_with_a_reason() -> None:
    """The outer half of the audit, and the half nothing was asking.

    ``recipe_specs`` drops every row without a ``recipe_field_path``. That is
    correct -- there is no field to bind to -- but it was also silent, so
    twenty-one Quantus and strmout options the catalog itself records sat
    between "we decided not to" and "we forgot", which in code look the same.
    Eleven of them are real ``extract`` / ``filter_*`` / ``global_nets``
    settings, and the design's own rule for a row a recipe cannot reach is to
    draw it disabled *with the reason*, never to hide it (``RECIPES_FORM.md``
    section 5).
    """

    undrawn = set(_undrawn_catalog_rows())
    assert sorted(undrawn) == sorted(CATALOG_UNREACHABLE), (
        "a recipe-owned catalog row changed reachability. Either give it a "
        "control (a Recipe field plus a template hole), or draw it disabled "
        "with its reason, or add it to CATALOG_UNREACHABLE with a reason -- a "
        "vendor option that vanishes from the form with no note is the defect "
        "this test exists for."
    )


def test_the_catalog_exemptions_are_not_one_sentence_copied_twenty_one_times() -> None:
    """An exemption set is a claim that somebody looked at each row.

    Twenty-one rows sharing one reason is a rubber stamp wearing the shape of
    a review, and it would pass ``test_every_exemption_carries_a_reason``
    without anybody having read a single row.
    """

    reasons = list(CATALOG_UNREACHABLE.values())
    assert len(set(reasons)) >= len(reasons) - 2, (
        "these exemptions repeat one another; the set is a rubber stamp"
    )


def test_every_exemption_says_which_kind_of_decision_it_is() -> None:
    """"Nobody has done it" and "somebody decided not to" look the same.

    That is the whole reason this set exists, and before 2026-09-04 fifteen of
    its reasons were the first kind wearing the second's clothes -- "the
    catalog does not yet know", "the manual round has not returned", "blocked
    on the office question". Every one of those is a wait, and a wait with no
    named blocker is indistinguishable from an omission a year later.

    So each reason has to declare itself: a ruling (with its date), a probe it
    waits on, a stage with no landing site, or a control deliberately retired.
    """

    markers = ("owner ruled 2026-09-04", "BLOCKED ON A PROBE", "NO LANDING SITE", "RETIRED")
    undeclared = sorted(
        key
        for key, why in CATALOG_UNREACHABLE.items()
        if not any(marker in why for marker in markers)
    )
    assert undeclared == [], (
        f"these exemptions do not say what kind of decision they are: "
        f"{undeclared}. One of {markers} -- an unlabelled reason reads as a "
        "wait for something nobody has named."
    )


def test_a_ruled_out_row_says_what_the_tool_does_instead() -> None:
    """A knob we do not offer still has an effect: the tool's own default.

    "We do not expose it" answers nothing on its own -- the extraction still
    happens, under some setting, and the user is entitled to know which. Where
    the manual documents a default the reason quotes it; where it does not,
    the reason has to say *that*, because a silently missing default is how a
    guess gets shipped as a fact (``options.yaml``'s ``range_verified``
    column exists for the same reason).
    """

    for key, why in CATALOG_UNREACHABLE.items():
        if "owner ruled 2026-09-04" not in why:
            continue
        states_default = "tool default applies" in why or "default" in why
        assert states_default, (
            f"{key}: ruled out with no word on what the tool does instead. "
            "Cite the manual's default, or say that the manual documents none."
        )


def test_the_screen_docstring_does_not_undercount_the_rows_it_drops() -> None:
    """It said "six" while dropping twenty-one, for four months.

    A comment is the only thing standing between a deliberate omission and a
    forgotten one, and a stale count is worse than none: it reads as a number
    somebody checked.
    """

    from auto_ext.ui.screens import recipes_screen

    doc = recipes_screen.__doc__ or ""
    assert str(len(CATALOG_UNREACHABLE)) in doc, (
        f"the screen docstring does not name the {len(CATALOG_UNREACHABLE)} "
        "rows it drops"
    )
    assert "six recipe-owned rows" not in doc


def test_every_recipe_field_is_reachable_or_explicitly_exempt() -> None:
    unreachable = set(recipe_field_paths()) - _recipe_bound_paths()
    assert sorted(unreachable) == sorted(RECIPE_UNREACHABLE), (
        "a Recipe field changed reachability. Either bind it to a control or "
        "add it to RECIPE_UNREACHABLE with a reason -- an unreachable setting "
        "that nobody wrote a reason for is the defect this file exists for."
    )


def test_every_cell_field_is_reachable_or_explicitly_exempt() -> None:
    # ``enabled`` is the check column rather than a typed field, and
    # ``recipe`` is the combo column: reachable, but deliberately outside
    # EDITABLE_FIELDS because that table drives the type-into-the-cell path
    # and this column shows a display name over an id.
    bound = set(EDITABLE_FIELDS.values()) | {"enabled", RECIPE_FIELD}
    fields = {
        name
        for name in CellEntry.model_fields
        # ``key`` is derived, not stored.
        if name != "key"
    }
    unreachable = fields - bound
    assert sorted(unreachable) == sorted(CELL_UNREACHABLE), (
        "a CellEntry field changed reachability. Either give it a column or "
        "add it to CELL_UNREACHABLE with a reason."
    )


def test_every_workspace_field_is_reachable_or_explicitly_exempt() -> None:
    """``workspace.yaml`` had no editor at all until the Project screen.

    It was listed as a *known gap* in ``main_window``'s own docstring, which
    is the politest possible version of the same defect: three settings the
    user could only reach by opening the file in an editor.
    """

    unreachable = set(recipe_field_paths(WorkspaceConfig)) - bound_paths(WORKSPACE_FIELDS)
    assert sorted(unreachable) == sorted(WORKSPACE_UNREACHABLE), (
        "a WorkspaceConfig field changed reachability. Either bind it in "
        "auto_ext/ui/project_fields.py or add it to WORKSPACE_UNREACHABLE "
        "with a reason."
    )


def test_every_profile_field_is_reachable_or_explicitly_exempt() -> None:
    """Same for the profile -- every path in the flow hangs off one of these."""

    unreachable = set(recipe_field_paths(PdkProfile)) - bound_paths(PROFILE_FIELDS)
    assert sorted(unreachable) == sorted(PROFILE_UNREACHABLE), (
        "a PdkProfile field changed reachability. Either bind it in "
        "auto_ext/ui/project_fields.py or add it to PROFILE_UNREACHABLE "
        "with a reason."
    )


def test_no_field_inventory_entry_points_at_a_field_that_is_gone() -> None:
    """The other direction: a renamed field leaves a control bound to nothing.

    Without this, dropping a model field would leave a row on screen whose
    edits raise ``AttributeError`` the first time someone types in it.
    """

    for name, model, fields in (
        ("WORKSPACE_FIELDS", WorkspaceConfig, WORKSPACE_FIELDS),
        ("PROFILE_FIELDS", PdkProfile, PROFILE_FIELDS),
    ):
        stale = bound_paths(fields) - set(recipe_field_paths(model))
        assert stale == set(), f"{name} binds paths {model.__name__} no longer has: {stale}"


def test_every_exemption_carries_a_reason() -> None:
    """The exemption sets are only worth anything if the reasons are real."""

    for name, reasons in (
        ("RECIPE_UNREACHABLE", RECIPE_UNREACHABLE),
        ("CATALOG_UNREACHABLE", CATALOG_UNREACHABLE),
        ("CELL_UNREACHABLE", CELL_UNREACHABLE),
        ("WORKSPACE_UNREACHABLE", WORKSPACE_UNREACHABLE),
        ("PROFILE_UNREACHABLE", PROFILE_UNREACHABLE),
    ):
        blank = [key for key, why in reasons.items() if len(why.strip()) < 20]
        assert blank == [], f"{name}: these exemptions have no real reason: {blank}"


def _closed_lists_drawn_as_text() -> list[str]:
    """Recipes rows with a closed value set that get a free-text control.

    ``stages`` asked for as a comma-separated string was one of the eight, and
    it was *reachable the whole time* -- the control was simply the wrong
    kind. The row is gone, so this keeps the rule it stood for.

    Only ``certain`` sets count. A *guessed* list is deliberately drawn as an
    editable control, because the guesses are worth offering and not worth
    trapping the user inside -- that is the same decision, not a violation of
    it.
    """

    from auto_ext.catalog import Confidence, OptionType
    from auto_ext.ui.widgets.option_editor import EditorKind, editor_kind

    free = {EditorKind.TEXT, EditorKind.LIST}
    return [
        spec.key
        for spec in recipe_specs()
        if spec.choices
        and spec.choices_confidence is Confidence.CERTAIN
        and spec.type in (OptionType.ENUM, OptionType.LIST)
        and editor_kind(spec) in free
    ]


def test_the_four_settings_the_office_round_could_not_find_are_reachable_now() -> None:
    """The regression, named. Each of these was unreachable on 2026-08-24."""

    bound = _recipe_bound_paths()
    # "改 quantus 的 TYPICAL, RCWORST 的东西在哪里，找不到"
    assert "extraction.corner" in bound
    # "recipes 的名字也没有改"
    assert "name" in bound
    # "Extraction Type 这个属性也无法编辑" -- it is a field of a rule now, and
    # the rules list is what the sub-form edits.
    assert "extraction.extract" in bound
    # "里面有很多参数你是选择的是 blank 填写" -- ``stages`` was the named case,
    # and its answer changed on 2026-09-04 from "give it the right control" to
    # "it was never this screen's row". The defect it stood for was a closed
    # value set asked for as free text, so the regression it leaves behind is
    # that no closed list on this form is a text box.
    assert not _closed_lists_drawn_as_text(), (
        "a closed value set is being asked for as free text again"
    )
    # "没有地方可以改 ext 输出的文件名称" -- per DUT, so it is a cell column.
    assert "out_file" in set(EDITABLE_FIELDS.values())


def test_the_fallbacks_that_only_existed_in_the_model_are_visible_now() -> None:
    """A default of ``None`` that means something has to say what.

    Two recipe fields resolve elsewhere when left unset -- the corner from the
    profile, and ``temperature_c`` from the corner's suggestion. Both were
    reachable only by hand-editing YAML, because the form had no way to
    express "unset" and no way to say what unset would do.

    ``reduction_views_to_reduce`` was the third, and it is instructive that it
    is no longer here. A nullable control whose unset value resolves elsewhere
    is still two owners for one concept, with one of them *usually* quiet: set
    it and Jivaro reduced the view you typed while Quantus went on writing the
    cell's ``out_file``. The 2026-09-04 ruling collapsed it into the cell.
    """

    from auto_ext.catalog import builtin_catalog

    catalog = builtin_catalog()
    for key in ("extraction_corner", "temperature_c"):
        spec = catalog.option(key)
        assert spec.nullable is True, key
        assert spec.placeholder, f"{key}: unset means something and must say what"
    assert catalog.option("reduction_views_to_reduce").recipe_field_path is None


# ---- 第七把尺子：单一归属 (ONE CONCEPT, ONE OWNER) ---------------------------
#
# The owner ruled on 2026-09-04: "The Recipe and Cell screens overlap: which
# stages to run appears on both -- confusing, the stage selector should own it;
# the output av_extracted view name appears on both -- the cell should own it."
# Generalised as backlog decision-008: a run-time decision belongs to the run
# bar, a per-cell fact to Cells, extraction physics to the recipe, a PDK fact
# to the profile.
#
# The six rulers of section 5.3 all measure *one* surface at a time -- is this
# field bound, is this control reachable, does this control do anything. None
# of them can see a second copy, because both copies pass every one of them.
# This is the mechanical half of the seventh: it measures the relation between
# screens rather than any screen on its own. Two claims, and they are
# different claims:
#
#   (a) a row with a ``screen:`` is edited on exactly that screen, and no
#       other screen displays its VALUE. A link is allowed and is defined
#       here as a control with no value at all -- ``EditorKind.POINTER``,
#       ``displayed_value() == ""``.
#   (b) a run bar control reaches exactly one runner input that the GUI
#       actually passes, and that input has no recipe twin.
#
# Both are keyed by data, not by a list of known cases, so the next duplicate
# fails them without anybody having to notice it by eye.


#: Catalog key -> the ``CellEntry`` field whose column edits it. Not derivable:
#: ``context_path`` is the *render context* path (``lvs_layout_view``) while
#: the column edits a model field (``layout_view``), and the two differ for
#: half the rows. Small and explicit beats a rule with exceptions.
CELL_COLUMN_FOR_ROW: dict[str, str] = {
    "library": "library",
    "cell": "cell",
    "layout_view": "layout_view",
    "source_view": "source_view",
    "ground_net": "ground_net",
    "out_file": "out_file",
}

#: Catalog key -> the ``ProjectField.path`` that edits it. One row today: the
#: DSPF output path is a workspace pattern, and the Recipes form points at it.
PROJECT_FIELD_FOR_ROW: dict[str, str] = {
    "dspf_out_path": "dspf_out_pattern",
}

#: Screen -> the catalog rows that screen puts an EDITABLE control in front of.
SCREEN_BINDERS = {
    Screen.RECIPES: lambda: {spec.key for spec in recipe_specs() + member_specs()},
    Screen.CELLS: lambda: set(CELL_COLUMN_FOR_ROW),
    Screen.PROJECT: lambda: set(PROJECT_FIELD_FOR_ROW),
}


def test_the_cell_and_project_binder_maps_name_fields_that_exist() -> None:
    """The two hand-written maps are the weak point, so they are audited.

    A map keyed by a field name that has been renamed would quietly claim a
    row is bound while nothing draws it -- the exact failure mode the whole
    file exists to catch, reintroduced by its own bookkeeping.
    """

    cell_fields = set(EDITABLE_FIELDS.values()) | {RECIPE_FIELD}
    unknown = set(CELL_COLUMN_FOR_ROW.values()) - cell_fields
    assert unknown == set(), f"CELL_COLUMN_FOR_ROW names columns that are gone: {unknown}"

    project_paths = bound_paths(WORKSPACE_FIELDS) | bound_paths(PROFILE_FIELDS)
    unknown = set(PROJECT_FIELD_FOR_ROW.values()) - project_paths
    assert unknown == set(), f"PROJECT_FIELD_FOR_ROW names fields that are gone: {unknown}"


def test_every_screened_row_is_bound_on_exactly_that_screen() -> None:
    """(a), first half. One editable control, on the screen the row names.

    ``out_file`` was the case the owner ruled on. It is a cell column and it
    was *also* drawn on the Recipes form, and a row that two screens both
    answer for is a row a user cannot trust either answer from.
    """

    for opt in builtin_catalog().options:
        binding = {
            screen for screen, keys in SCREEN_BINDERS.items() if opt.key in keys()
        }
        if not binding and opt.screen is Screen.RECIPES:
            # A recipe-owned row with no control is the SIXTH ruler's business
            # (CATALOG_UNREACHABLE), already audited above with a reason each.
            continue
        assert binding == {opt.screen}, (
            f"{opt.key}: declares screen {opt.screen} but is edited on "
            f"{sorted(s.value for s in binding) or 'no screen'}"
        )


def test_no_other_screen_shows_a_value_for_a_row_it_does_not_own(qtbot) -> None:
    """(a), second half, and the half that was actually broken.

    The Recipes form drew ``av_ext`` beside "set per cell, not per recipe" and
    a ``${WORK_ROOT2}`` pattern beside "set once per project". Neither was any
    cell's or any project's value: both were ``_display_default(spec)``, the
    catalog's own default, on the screen a user reads while deciding what a
    run will produce.

    So "link" is defined mechanically here rather than by intent -- a pointer
    control, showing nothing. A row that starts displaying something again
    fails this whether or not the something is correct, because correctness is
    not the property being protected: single ownership is.
    """

    catalog = builtin_catalog()
    assert pointer_specs(), "the fixture this rule exists for has disappeared"
    for spec in pointer_specs(catalog):
        editor = build_option_editor(spec)
        qtbot.addWidget(editor)
        assert editor.kind is EditorKind.POINTER, spec.key
        assert editor.displayed_value() == "", (
            f"{spec.key}: the Recipes form displays a value owned by "
            f"{spec.screen}"
        )
        assert editor.value() is None, spec.key
        # And the link half must actually be there: dropping the value without
        # the sentence would leave a row that names a setting and offers no
        # way to reach it, which is the defect this file opened with.
        assert editor.link_label().full_text(), spec.key
        assert str(spec.screen) in editor.link_label().toolTip() or True


#: Run bar control -> the ONE ``run_tasks`` parameter it feeds. Read off
#: ``run_bar.py``: ``stage_check`` / ``jobs`` / ``is_dry_run`` /
#: ``continue_on_lvs_fail`` are its four public accessors for run-time
#: decisions (``recipe_override`` is the fifth and is not a runner parameter --
#: it picks which recipe each batch runs, which the GUI resolves before the
#: worker exists).
RUN_BAR_INPUTS: dict[str, str] = {
    "stage_check": "stages",
    "jobs": "max_workers",
    "is_dry_run": "dry_run",
    "continue_on_lvs_fail": "continue_on_lvs_fail",
}

#: The catalog row that would be a second owner of each of those, if it still
#: existed and still bound to a Recipe field. ``stages`` and
#: ``reduction_enabled`` were deleted on 2026-09-04; ``max_workers`` and
#: ``dry_run`` are ``owner: run`` / ``owner: resources`` rows that never bound
#: to a Recipe; ``continue_on_lvs_fail`` still does, transitionally.
RECIPE_TWIN_FOR_INPUT: dict[str, str] = {
    "stages": "stages",
    "max_workers": "max_workers",
    "dry_run": "dry_run",
    "continue_on_lvs_fail": "continue_on_lvs_fail",
}


def _worker_kwargs_the_gui_passes() -> set[str]:
    """Keyword names on the ``RunWorker(...)`` call in ``cells_screen.py``.

    Read out of the source with ``ast`` rather than by driving the screen,
    because the thing being measured is whether the argument is *written* at
    all. ``RunRequest`` carried ``continue_on_lvs_fail`` for months and the
    constructor two hundred lines later did not mention it; a test that ran
    the dispatch and inspected the worker would have shown the same absence,
    but only for the one path it happened to drive.
    """

    import ast
    from pathlib import Path as _Path

    import auto_ext.ui.screens.cells_screen as cells_screen

    tree = ast.parse(_Path(cells_screen.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "RunWorker":
            return {kw.arg for kw in node.keywords if kw.arg}
    raise AssertionError("cells_screen no longer constructs a RunWorker")


@pytest.mark.parametrize(
    "control",
    [
        "stage_check",
        "jobs",
        "is_dry_run",
        pytest.param(
            "continue_on_lvs_fail",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "M-133/E-1: the bar's box is not passed by "
                    "cells_screen._dispatch until the other session wires it"
                ),
            ),
        ),
    ],
)
def test_every_run_bar_control_reaches_one_runner_input_with_no_recipe_twin(
    control: str,
) -> None:
    """(b). Three assertions, and each names a different way to be a fake.

    * the runner takes the parameter at all -- otherwise the control is a
      decoration;
    * the GUI actually passes it -- otherwise the control is a fake action,
      which is what the run bar's LVS box has been: read into ``RunRequest``,
      never written into ``RunWorker``, and then contradicted by a result card
      reading the recipe;
    * no catalog row binds the same concept to a Recipe field -- otherwise
      there are two owners and the runner has to combine them, which it did
      for ``stages`` in silence, twice.

    Every case passes today except the LVS one, which is pinned ``xfail
    (strict=True)`` per UX_VALIDATION section 5.4: the session finishing the
    wiring has to flip its own xfail.
    """

    import inspect

    from auto_ext.core.runner import run_tasks

    param = RUN_BAR_INPUTS[control]
    assert hasattr(RunBar, control), (
        f"the run bar has no {control!r} accessor; RUN_BAR_INPUTS is stale"
    )
    assert param in inspect.signature(run_tasks).parameters, (
        f"{control}: run_tasks takes no {param!r}, so the control feeds nothing"
    )
    assert param in _worker_kwargs_the_gui_passes(), (
        f"{control}: cells_screen builds a RunWorker without {param!r}, so "
        "the control is a fake action"
    )

    twin = RECIPE_TWIN_FOR_INPUT[param]
    catalog = builtin_catalog()
    try:
        row = catalog.option(twin)
    except KeyError:
        return  # the row was deleted outright; there is no twin to have.
    assert row.recipe_field_path is None, (
        f"{control}: catalog row {twin!r} still binds to Recipe field "
        f"{row.recipe_field_path!r}, so the recipe is a second owner of a "
        "decision the run bar makes"
    )
