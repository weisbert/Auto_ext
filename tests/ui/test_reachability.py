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
from auto_ext.ui.screens.cells_screen import EDITABLE_FIELDS  # noqa: E402
from auto_ext.ui.screens.recipes_screen import recipe_specs  # noqa: E402

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
    return {path for path in bound if path is not None}


def test_every_recipe_field_is_reachable_or_explicitly_exempt() -> None:
    unreachable = set(recipe_field_paths()) - _recipe_bound_paths()
    assert sorted(unreachable) == sorted(RECIPE_UNREACHABLE), (
        "a Recipe field changed reachability. Either bind it to a control or "
        "add it to RECIPE_UNREACHABLE with a reason -- an unreachable setting "
        "that nobody wrote a reason for is the defect this file exists for."
    )


def test_every_cell_field_is_reachable_or_explicitly_exempt() -> None:
    # ``enabled`` is the check column rather than a typed field.
    bound = set(EDITABLE_FIELDS.values()) | {"enabled"}
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
    # "Extraction Type 这个属性也无法编辑"
    assert "extraction.extract_type" in bound
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
