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
from auto_ext.catalog import Owner, builtin_catalog  # noqa: E402
from auto_ext.ui.screens.cells_screen import EDITABLE_FIELDS, RECIPE_FIELD  # noqa: E402
from auto_ext.ui.screens.recipes_screen import (  # noqa: E402
    member_specs,
    pointer_specs,
    recipe_specs,
)

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
CATALOG_UNREACHABLE: dict[str, str] = {
    # -- Calibre LVS supply naming. One office question, three rows.
    "lvs_extra_power_names": (
        "no Recipe field and no template hole. Blocked on the office question "
        "behind lvs_connect_by_name_nets: until we know which of the three "
        "connect-by-name settings the site actually runs, a control here "
        "would invite a supply list that changes nothing"
    ),
    "lvs_extra_ground_names": (
        "the ground half of lvs_extra_power_names, and blocked on the same "
        "question. Shipping one of the pair without the other would read as "
        "'grounds are handled elsewhere', which is false"
    ),
    "lvs_connect_by_name_nets": (
        "the office question itself -- which connect-by-name mode the flow "
        "wants -- is unanswered, and this row is the one that would have to "
        "encode the answer. A guess here changes what LVS considers shorted"
    ),
    # -- Quantus ``extract``. Five rows that would each need a rule field.
    "use_field_solver": (
        "the accuracy lever, and the catalog's own question asks what an "
        "omitted line gives us today. Modelling it before that is answered "
        "would let a user pick a level believing the current runs are at a "
        "different one"
    ),
    "extract_via_cap": (
        "the manual round has not returned the default, and the catalog's "
        "question asks whether it interacts with -use_field_solver. A "
        "check box whose unticked state is not the tool's default is a "
        "control that silently changes the extraction"
    ),
    "extract_gate_diffusion_fringing_cap": (
        "same round, same reason, plus the open question of whether the PDK "
        "device model already carries this term -- ticking it could count "
        "fringing capacitance twice"
    ),
    "inductance_nets_file": (
        "gates the rlc and rlck extract types, and we do not yet know the "
        "file format or whether it is mandatory. Offering the path before "
        "the types are usable would be a control that leads nowhere"
    ),
    "substrate_nets_file": (
        "as inductance_nets_file, for the substrate_only and "
        "decoupled_to_substrate types. Both belong with the extract-rule "
        "sub-form when they land, not as a scalar beside it"
    ),
    # -- Quantus ``extract -selection`` and ``global_nets``.
    "selection_layers": (
        "layer-based net selection is a second selection mode the extract "
        "rule sub-form does not model. It needs a rule field, not a row"
    ),
    "selection_dividing_layers_type": (
        "meaningless without selection_layers -- the catalog's own note says "
        "one without the other says nothing -- so the pair lands together"
    ),
    "global_nets_nets": (
        "decides what '-selection all' actually covers. The catalog's "
        "question asks how the four global_nets options interact and how "
        "they relate to the LVS power/ground names; four controls wired on a "
        "guess about that is four ways to change the netlist by accident"
    ),
    "global_nets_file": "the file form of global_nets_nets; same question, same wait",
    "global_nets_import_from_lvs": (
        "the third of the same four, and the one that decides whether the "
        "recipe's list is used at all"
    ),
    "global_nets_force": "the fourth of the same four",
    # -- Quantus ``filter_*``.
    "exclude_floating_decoupling_factor": (
        "only meaningful alongside exclude_floating_nets_limit, and its "
        "advisory 0-1 range is unverified. A number that redistributes "
        "capacitance is the wrong place to ship a guessed bound"
    ),
    "merge_parallel_via": (
        "the catalog asks for its documented default and which flow it "
        "applies to. It changes the extracted resistor count, so a wrong "
        "default here is a wrong netlist that still runs"
    ),
    "min_res_centering": (
        "the catalog does not yet know whether it is a boolean or an enum, "
        "which is the one thing a control has to know first"
    ),
    "disable_subnodes": (
        "decides whether split-net subnodes reach the DSPF, and so what can "
        "be back-annotated at all. Wired to nothing today; it belongs beside "
        "sub_node_char when it lands"
    ),
    # -- strmout. No template at all: the stage is argv, not a rendered file.
    "strmout_hier_depth": (
        "strmout takes argv rather than a rendered file, so there is no "
        "template hole to open. The catalog records that we are relying on "
        "its defaults without anybody having chosen them, and the office "
        "question about the manual flow is unanswered"
    ),
    "strmout_convert_dot": "as strmout_hier_depth: argv, and the same unanswered question",
    "strmout_case": "as strmout_hier_depth: argv, and the same unanswered question",
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
    # "里面有很多参数你是选择的是 blank 填写" -- stages was the named case.
    assert "stages" in bound
    # "没有地方可以改 ext 输出的文件名称" -- per DUT, so it is a cell column.
    assert "out_file" in set(EDITABLE_FIELDS.values())


def test_the_fallbacks_that_only_existed_in_the_model_are_visible_now() -> None:
    """A default of ``None`` that means something has to say what.

    Two recipe fields resolve elsewhere when left unset -- the corner from the
    profile, the view to reduce from the DUT -- and ``temperature_c`` takes
    the corner's suggestion. All three were reachable only by hand-editing
    YAML, because the form had no way to express "unset" and no way to say
    what unset would do.
    """

    from auto_ext.catalog import builtin_catalog

    catalog = builtin_catalog()
    for key in ("extraction_corner", "reduction_views_to_reduce", "temperature_c"):
        spec = catalog.option(key)
        assert spec.nullable is True, key
        assert spec.placeholder, f"{key}: unset means something and must say what"
