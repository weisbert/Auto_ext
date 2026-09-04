"""The Recipe object and its two contracts with the rest of the system.

The two load-bearing tests here are
:func:`test_every_recipe_field_has_a_catalog_row` and
:func:`test_every_recipe_owned_catalog_row_has_a_field`: together they pin the
Recipe model and ``auto_ext/catalog/options.yaml`` to each other in both
directions, with a short, explicit list of intentional exceptions. Adding a
field without a catalog row, or a catalog row naming a field that does not
exist, fails here.

The rest covers the promises the object itself makes: it is portable (no PDK
literal, no cell identity, no machine property inside it), it round-trips
through YAML with its comments, and it can still produce the S1
``RecipeSnapshot`` the runner writes today.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_ext.catalog.spec import Owner, builtin_catalog
from auto_ext.core.errors import ConfigError
from auto_ext.core.patch_models import BaseFingerprint, PatchHunk, TemplatePatch
from auto_ext.model.common import Stage
from auto_ext.model.recipe import (
    CATALOG_EXEMPT_FIELDS,
    PROFILE_FALLBACK_FIELDS,
    RECIPE_SCHEMA_VERSION,
    ExtractType,
    MetalFill,
    OutputKind,
    Recipe,
    RecipeRef,
    ResourceProfile,
    dump_recipe_yaml,
    load_recipe,
    load_recipe_with_raw,
    recipe_field_paths,
    recipe_from_catalog,
    save_recipe,
)
from auto_ext.model.run import RecipeSnapshot

_SHA = "0" * 64


def make_recipe(**over: object) -> Recipe:
    fields: dict[str, object] = {"recipe_id": "rc-coupled-typical", "name": "RC coupled, typical"}
    fields.update(over)
    return Recipe(**fields)  # type: ignore[arg-type]


def make_patch(*, stage: Stage = Stage.QUANTUS, template_id: str = "quantus/ext.cmd.j2",
               hunks: int = 1, enabled: bool = True) -> TemplatePatch:
    return TemplatePatch(
        stage=stage,
        template_id=template_id,
        base=BaseFingerprint(
            template_sha256=_SHA,
            masked_sha256=_SHA,
            captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        ),
        hunks=[
            PatchHunk(
                id=f"{i:08x}",
                before="              -max_via_array_size \"auto\" \\\n",
                after="              -max_via_array_size \"4\" \\\n",
                enabled=enabled,
                intent="this project needs a bounded via array",
            )
            for i in range(hunks)
        ],
    )


def _get(obj: object, path: str) -> object:
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


# ---- shape -------------------------------------------------------------------


def test_a_default_recipe_is_grouped_and_complete() -> None:
    recipe = make_recipe()
    assert recipe.schema_version == RECIPE_SCHEMA_VERSION
    assert recipe.extraction.extract[0].type is ExtractType.RC_COUPLED
    assert len(recipe.extraction.extract) == 1, "one rule unless asked otherwise"
    assert recipe.extraction.metal_fill is MetalFill.VIRTUAL
    assert recipe.output.emit == [OutputKind.EXTRACTED_VIEW]
    assert recipe.lvs.deck_variant == "wodio"
    assert recipe.policy.continue_on_lvs_fail is False
    assert recipe.patches == []


def test_unknown_fields_are_rejected_not_swallowed() -> None:
    with pytest.raises(ValidationError):
        make_recipe(coupling_cap_threshold_absolute=0.5)
    with pytest.raises(ValidationError):
        Recipe.model_validate(
            {"recipe_id": "x", "name": "x", "extraction": {"typo_here": 1}}
        )


def test_recipe_id_must_be_a_slug() -> None:
    for bad in ("Not A Slug", "../escape", "", "UPPER"):
        with pytest.raises(ValidationError):
            make_recipe(recipe_id=bad)


def test_a_recipe_refuses_a_stage_list_outright() -> None:
    """The field is gone, so passing one is a typo and must read as one.

    ``Base`` forbids extra keys, which is what turns "this recipe still
    declares stages" from a value that silently does nothing into an error
    naming the key. Files on disk are the exception and are migrated instead
    -- see :func:`test_a_recipe_that_still_declares_stages_loads_without_them`.
    """

    with pytest.raises(ValidationError, match="stages"):
        make_recipe(stages=[Stage.SI, Stage.CALIBRE])


def test_emit_must_name_at_least_one_output_form() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        make_recipe(output={"emit": []})
    with pytest.raises(ValidationError, match="duplicates"):
        make_recipe(output={"emit": ["dspf", "dspf"]})


def test_unticking_every_xy_class_is_refused_rather_than_written_as_a_bare_option() -> None:
    """The DSPF form's checkbox list can be emptied in one click.

    ``-output_xy`` is emitted outside the loop that writes its values, so an
    empty list used to produce a bare ``-output_xy \\`` followed straight by
    the next option -- an option with no operand, in a file Quantus reads
    hours after the user clicked. Its two siblings, ``extraction.extract`` and
    ``output.emit``, have refused an empty list from the start; this one was
    the odd one out.
    """

    with pytest.raises(ValidationError, match="output_xy"):
        make_recipe(output={"dspf": {"output_xy": []}})
    assert make_recipe(output={"dspf": {"output_xy": ["MOS"]}}).output.dspf.output_xy == [
        "MOS"
    ]


def test_a_fracture_length_under_five_is_refused_here_and_not_by_quantus() -> None:
    """The tool errors on the whole command file, hours after the click.

    A tight RF transmission line invites exactly the number that is refused:
    the vendor floor is 5 (microns or squares, whichever unit is selected) and
    below it Quantus rejects the deck outright. The literal ``infinite`` is
    the LVS-input default and stays legal; 100 is what the manual recommends
    for long transmission lines, and 50 is the accuracy floor it warns about.
    """

    with pytest.raises(ValidationError, match="5"):
        make_recipe(extraction={"max_fracture_length": "3"})
    for good in ("infinite", "5", "50", "100"):
        recipe = make_recipe(extraction={"max_fracture_length": good})
        assert recipe.extraction.max_fracture_length == good
    with pytest.raises(ValidationError, match="infinite"):
        make_recipe(extraction={"max_fracture_length": "as long as it takes"})


def test_the_coupling_threshold_is_bounded_by_the_documented_femtofarad_range() -> None:
    """0.01 was never 10 mF; the unit is femtofarads and the range is 0 to 100.

    The catalog carried ``unit: F`` and a note saying the default was
    physically impossible, which made the one knob that decides how many
    coupling caps survive look untrustworthy. It is not: 0.01 fF is the
    vendor's own example value, and anything above 100 is out of range.
    """

    assert make_recipe().extraction.coupling_cap_threshold_absolute == 0.01
    assert make_recipe(
        extraction={"coupling_cap_threshold_absolute": 100.0}
    ).extraction.coupling_cap_threshold_absolute == 100.0
    with pytest.raises(ValidationError):
        make_recipe(extraction={"coupling_cap_threshold_absolute": 500.0})
    with pytest.raises(ValidationError):
        make_recipe(extraction={"coupling_cap_threshold_absolute": -1.0})


def test_emit_is_a_list_so_one_run_can_produce_both_forms() -> None:
    # The whole point of removing the single quantus template slot: today a
    # run structurally cannot emit an extracted view and a DSPF together.
    recipe = make_recipe(output={"emit": ["extracted_view", "dspf"]})
    assert recipe.output.emit == [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]


# ---- portability: what a Recipe must NOT contain -----------------------------


def test_a_recipe_carries_no_cell_identity() -> None:
    fields = set(recipe_field_paths())
    for forbidden in ("library", "cell", "layout_view", "source_view", "ground_net", "out_file"):
        assert forbidden not in fields


def test_a_recipe_carries_no_pdk_literal() -> None:
    recipe = make_recipe()
    # The corner is a semantic name resolved through the profile, never the
    # tool literal -- that seam is what lets one recipe move between PDKs.
    assert recipe.extraction.corner is None
    assert "TYPICAL" not in dump_recipe_yaml(recipe)
    assert "assura_tech" not in dump_recipe_yaml(recipe)
    # The parasitic device names are one contract shared with Jivaro and
    # belong to the profile.
    fields = set(recipe_field_paths())
    assert not [f for f in fields if f.endswith(("cap_component", "res_component"))]
    assert not [f for f in fields if f.endswith(("r_model", "c_model", "l_model", "k_model"))]


def test_a_recipe_carries_no_machine_property() -> None:
    # DECISIONS.md #21: carry this recipe from an 8-core box to a 64-core box
    # and nothing in it should need editing.
    fields = set(recipe_field_paths())
    for forbidden in ("cpu", "num_turbo", "run_mt", "run_hyper", "license_wait", "workers"):
        assert not [f for f in fields if forbidden in f], forbidden


def test_resource_settings_live_in_their_own_object() -> None:
    profile = ResourceProfile()
    assert profile.lvs_num_turbo == 2
    assert profile.lvs_run_mt is True
    assert profile.lvs_run_hyper is True
    assert profile.lvs_license_wait_time == 10
    assert profile.reduction_cpu == 1
    assert profile.quantus_cpu_count == 1


def test_resource_catalog_rows_all_map_into_the_resource_profile() -> None:
    catalog = builtin_catalog()
    fields = set(recipe_field_paths(ResourceProfile))
    for opt in catalog.by_owner(Owner.RESOURCES):
        path = opt.context_path
        if path is None or not path.startswith("resources."):
            continue
        assert path[len("resources.") :] in fields, opt.key


# ---- the two directions of the catalog cross-check ---------------------------


def test_every_recipe_field_has_a_catalog_row() -> None:
    catalog = builtin_catalog()
    covered = {o.recipe_field_path for o in catalog.by_owner(Owner.RECIPE)}
    # A ``describes_member`` row covers the COLLECTION its members live in.
    # ``recipe_field_path`` is None for those on purpose -- binding one
    # control to a list would write a scalar over it -- so the coverage
    # question has to be asked of ``context_path`` instead. Two rows describe
    # ``extraction.extract``; one covered field, not two orphans.
    covered |= {
        o.context_path[len("recipe.") :]
        for o in catalog.by_owner(Owner.RECIPE)
        if o.describes_member and o.context_path
    }
    orphans = sorted(set(recipe_field_paths()) - covered - CATALOG_EXEMPT_FIELDS)
    assert orphans == [], (
        "these Recipe fields have no row in options.yaml, so nobody can say "
        f"where they land or how sure we are of them: {orphans}"
    )


def test_every_recipe_owned_catalog_row_has_a_field() -> None:
    catalog = builtin_catalog()
    fields = set(recipe_field_paths())
    missing = sorted(
        opt.recipe_field_path
        for opt in catalog.by_owner(Owner.RECIPE)
        if opt.recipe_field_path is not None and opt.recipe_field_path not in fields
    )
    assert missing == []


def test_recipe_from_catalog_reproduces_todays_values() -> None:
    recipe = recipe_from_catalog()
    # A handful of spot checks straight off the templates.
    assert recipe.netlist.short_res_ohm == 2000.0
    assert recipe.netlist.not_incremental is True
    assert recipe.netlist.renetlist_all is False
    assert recipe.extraction.exclude_floating_nets_limit == 5000
    assert recipe.extraction.temperature_c == 55.0
    assert recipe.extraction.max_fracture_length == "infinite"
    assert recipe.output.common.include_parasitic_res_model == "comment"
    assert recipe.output.dspf.output_xy[0] == "CANONICAL_RES"
    # No views_to_reduce at all since 2026-09-04: the view Jivaro reduces is
    # the cell's out_file, derived in render._recipe_tree. A settable copy was
    # the live Jivaro bug and, kept as an override, the way back to it.
    assert "views_to_reduce" not in type(recipe.reduction).model_fields
    assert recipe.lvs.report_options == "S"


def test_the_only_defaults_that_diverge_from_the_catalog_are_the_profile_fallbacks() -> None:
    catalog = builtin_catalog()
    bare = make_recipe()
    from_catalog = recipe_from_catalog()
    diverged = {
        opt.recipe_field_path
        for opt in catalog.by_owner(Owner.RECIPE)
        if opt.recipe_field_path is not None
        and _get(bare, opt.recipe_field_path) != _get(from_catalog, opt.recipe_field_path)
    }
    # extraction.corner has a catalog row of its own now (extraction_corner,
    # the recipe's semantic half of the corner seam), and its catalog default
    # is null -- the same "ask the profile" the model default means -- so it
    # is a profile fallback that does NOT diverge.
    assert diverged == PROFILE_FALLBACK_FIELDS - {"extraction.corner"}


def test_recipe_from_catalog_accepts_overrides() -> None:
    recipe = recipe_from_catalog(recipe_id="c-only-fast", name="C only")
    assert recipe.recipe_id == "c-only-fast"
    assert recipe.name == "C only"


def test_recipe_from_catalog_reports_a_catalog_that_does_not_fit() -> None:
    catalog = builtin_catalog()
    broken = catalog.model_copy(
        update={
            "options": [
                o.model_copy(update={"default": "not a number"})
                if o.key == "min_res_ohm"
                else o
                for o in catalog.options
            ]
        }
    )
    with pytest.raises(ConfigError, match="does not fit the Recipe model"):
        recipe_from_catalog(catalog=broken)


def test_the_enum_backed_fields_match_their_catalog_choices() -> None:
    catalog = builtin_catalog()
    assert catalog.option("extract_type").choices == [e.value for e in ExtractType]
    assert catalog.option("metal_fill_type").choices == [e.value for e in MetalFill]
    assert catalog.option("output_form").choices == [e.value for e in OutputKind]


# ---- patches ------------------------------------------------------------------


def test_manual_edit_count_is_what_the_ui_shows() -> None:
    recipe = make_recipe(patches=[make_patch(hunks=3)])
    assert recipe.manual_edit_count == 3
    disabled = make_recipe(patches=[make_patch(hunks=3, enabled=False)])
    assert disabled.manual_edit_count == 0


def test_at_most_one_patch_per_generated_file() -> None:
    with pytest.raises(ValidationError, match="one TemplatePatch per"):
        make_recipe(patches=[make_patch(), make_patch()])
    # Two different files is fine.
    recipe = make_recipe(
        patches=[make_patch(), make_patch(template_id="quantus/dspf.cmd.j2")]
    )
    assert len(recipe.patches) == 2


def test_patch_lookup_by_stage_and_template() -> None:
    recipe = make_recipe(patches=[make_patch()])
    assert recipe.patch_for(Stage.QUANTUS, "quantus/ext.cmd.j2") is recipe.patches[0]
    assert recipe.patch_for("quantus", "quantus/ext.cmd.j2") is recipe.patches[0]
    assert recipe.patch_for(Stage.SI, "si/default.env.j2") is None


# ---- fingerprint and reference -------------------------------------------------


def test_content_hash_ignores_bookkeeping_but_not_settings() -> None:
    a = make_recipe()
    b = make_recipe(updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc), version="7")
    assert a.content_sha256() == b.content_sha256()
    c = make_recipe(extraction={"min_res_ohm": 0.002})
    assert c.content_sha256() != a.content_sha256()


def test_ref_carries_the_fingerprint_and_the_path() -> None:
    recipe = make_recipe()
    ref = recipe.ref(source_path="/w/recipes/rc-coupled-typical.yaml")
    assert isinstance(ref, RecipeRef)
    assert ref.recipe_id == recipe.recipe_id
    assert ref.content_sha256 == recipe.content_sha256()
    assert ref.source_path.endswith("rc-coupled-typical.yaml")


def test_a_ref_needs_a_real_digest() -> None:
    with pytest.raises(ValidationError):
        RecipeRef(recipe_id="x", version="1", content_sha256="short")


# ---- forward compatibility with the S1 run record ------------------------------


def test_to_snapshot_produces_a_valid_s1_recipe_snapshot() -> None:
    recipe = recipe_from_catalog(recipe_id="rc-coupled", name="RC coupled")
    snap = recipe.to_snapshot(
        templates={"quantus": "/w/templates/quantus/ext.cmd.j2"},
        dspf_out_path="${WORK_ROOT2}/{cell}.dspf",
        paths={"qrc_deck_dir": "/pdk/qrc"},
    )
    assert isinstance(snap, RecipeSnapshot)
    assert snap.recipe_id == "rc-coupled"
    assert snap.label == "RC coupled"
    assert snap.paths == {"qrc_deck_dir": "/pdk/qrc"}
    assert snap.dspf_out_path == "${WORK_ROOT2}/{cell}.dspf"


def test_to_snapshot_carries_the_seven_legacy_knobs_under_their_old_names() -> None:
    # So a run.json written from a Recipe stays comparable with one written
    # from the knob system it replaces.
    recipe = recipe_from_catalog()
    snap = recipe.to_snapshot()
    assert snap.knobs["calibre"] == {"lvs_variant": "wodio", "connect_by_name": False}
    assert snap.knobs["quantus"] == {
        "exclude_floating_nets_limit": 5000,
        "coupling_cap_threshold_absolute": 0.01,
        "coupling_cap_threshold_relative": 0.001,
        "min_res": 0.001,
        "temperature": 55.0,
    }


def test_to_snapshot_carries_the_reduction_settings() -> None:
    recipe = make_recipe(reduction={"frequency_limit_ghz": 40.0, "error_max_pct": 5.0})
    snap = recipe.to_snapshot(jivaro_enabled=True)
    assert snap.jivaro.frequency_limit == 40.0
    assert snap.jivaro.error_max == 5.0
    # ``enabled`` is the caller's answer now, not the recipe's: whether the
    # reduction ran is a property of the dispatch. The recipe supplies what
    # Jivaro is given once it runs, and nothing else.
    assert snap.jivaro.enabled is True
    assert make_recipe().to_snapshot().jivaro.enabled is False


# ---- YAML round trip -----------------------------------------------------------


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    recipe = recipe_from_catalog(recipe_id="rc-typical", name="RC typical")
    path = tmp_path / "recipes" / "rc-typical.yaml"
    save_recipe(recipe, path)
    loaded = load_recipe(path)
    assert loaded == recipe
    assert loaded.content_sha256() == recipe.content_sha256()


def test_a_recipe_with_patches_round_trips(tmp_path: Path) -> None:
    recipe = make_recipe(patches=[make_patch(hunks=2)])
    path = tmp_path / "with-patches.yaml"
    save_recipe(recipe, path)
    loaded = load_recipe(path)
    assert loaded.manual_edit_count == 2
    assert loaded.patches[0].hunks[0].intent.startswith("this project")


def test_comments_survive_a_write_back(tmp_path: Path) -> None:
    path = tmp_path / "commented.yaml"
    path.write_text(
        "# why this recipe exists\n"
        "schema_version: 1\n"
        "recipe_id: rc-typical\n"
        "name: RC typical\n"
        "extraction:\n"
        "  # the office confirmed 25C for this block\n"
        "  temperature_c: 25.0\n",
        encoding="utf-8",
    )
    recipe, raw = load_recipe_with_raw(path)
    assert recipe.extraction.temperature_c == 25.0

    updated = recipe.model_copy(deep=True)
    updated.extraction.temperature_c = 85.0
    text = dump_recipe_yaml(updated, raw=raw)

    assert "# why this recipe exists" in text
    assert "# the office confirmed 25C for this block" in text
    assert "temperature_c: 85.0" in text


def test_loading_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_recipe(tmp_path / "nope.yaml")


def test_loading_reports_a_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("recipe_id: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML parse error"):
        load_recipe(path)


def test_loading_reports_a_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a mapping"):
        load_recipe(path)


def test_loading_reports_an_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "typo.yaml"
    path.write_text("recipe_id: x\nname: x\ncorner: RCWORST\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="corner"):
        load_recipe(path)


def test_dumped_yaml_is_a_flow_free_document() -> None:
    text = dump_recipe_yaml(make_recipe())
    assert "recipe_id: rc-coupled-typical" in text
    assert "netlist:" in text
    # Block style, so a human can edit it and a diff stays line oriented.
    assert "{" not in text.split("patches:")[0]


# ---- portability: a Recipe belongs to nobody's project ----------------------
#
# Section 3.B.8 of the tests disposition: a Recipe is meant to be shared -- the
# same file, on two people's projects, in two checkouts. Anything in it that
# names *where* rather than *what* breaks that the moment it is copied.


def test_a_recipe_carries_no_filesystem_path() -> None:
    """Not one field of a fully-populated Recipe is a path.

    Checked against ``recipe_from_catalog``, not the bare defaults: a field
    that is empty by default and path-shaped when filled would pass the weaker
    check and fail the user. Anything that looks like a path -- absolute, or
    carrying a separator, or a ``$VAR`` expression -- belongs to the PdkProfile
    (process facts), the workspace (where output lands) or the Cells table.
    """

    recipe = recipe_from_catalog(catalog=builtin_catalog())
    for path in recipe_field_paths():
        # The DSPF name delimiters are single punctuation characters that a
        # netlist reader splits *node names* on -- ``/`` there separates
        # hierarchy levels inside one identifier, not directories. They are
        # the only fields whose legal values overlap path syntax.
        if path.endswith("delimiter"):
            continue
        value = recipe
        for part in path.split("."):
            value = getattr(value, part, None)
            if value is None:
                break
        for text in _strings_in(value):
            assert not text.startswith(("/", "~", "$")), f"{path} = {text!r}"
            assert "/" not in text, f"{path} = {text!r}"
            assert chr(92) not in text, f"{path} = {text!r}"


def _strings_in(value: object) -> list[str]:
    """Every string reachable from ``value`` (scalars, lists, tuples)."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for entry in value for item in _strings_in(entry)]
    return []


def test_the_same_recipe_file_loads_equal_from_two_project_directories(
    tmp_path: Path,
) -> None:
    """Copy the file into two projects and the two objects are identical.

    Equality is by ``model_dump``, not by ``content_sha256``: the digest
    deliberately ignores bookkeeping, so comparing digests would pass even if
    a project path had leaked into a field the digest skips.
    """

    recipe = recipe_from_catalog(catalog=builtin_catalog(), recipe_id="rc-shared")
    first = tmp_path / "projectA" / "recipes" / "rc-shared.yaml"
    second = tmp_path / "projectB" / "recipes" / "rc-shared.yaml"
    save_recipe(recipe, first)
    second.parent.mkdir(parents=True)
    second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

    assert load_recipe(first).model_dump() == load_recipe(second).model_dump()
    assert load_recipe(first).content_sha256() == load_recipe(second).content_sha256()
    # Neither project's directory name is anywhere in the file.
    text = first.read_text(encoding="utf-8")
    assert "projectA" not in text
    assert str(tmp_path) not in text


# ---------------------------------------------------------------------------
# Loading a file written by an older version
#
# Every test above builds its Recipe in Python, where the three-state fields
# are already ``str``. That is the write-out direction, and it was green
# through the whole 2026-08-25 round while the read-back direction was broken:
# a recipe file written before that date carries YAML *booleans* there, and
# pydantic will not coerce ``bool`` to ``str``. The red-zone library stopped
# loading on 2026-08-28 and the UI showed it as an empty recipe list.
# ---------------------------------------------------------------------------


def _v1_recipe_text() -> str:
    """The shape every recipe on disk had before 2026-08-25."""

    return (
        "schema_version: 1\n"
        "recipe_id: rc-v1\n"
        "name: from before the three-state change\n"
        "extraction:\n"
        "  extract_type: rc_coupled\n"
        "  selection: all\n"
        "output:\n"
        "  common:\n"
        "    include_cap_model: false\n"
        "    include_parasitic_cap_model: true\n"
        "    include_res_model: false\n"
    )


def test_v1_yaml_booleans_load_as_three_state_strings(tmp_path: Path) -> None:
    from auto_ext.model.recipe import load_recipe_with_raw

    path = tmp_path / "rc-v1.yaml"
    path.write_text(_v1_recipe_text(), encoding="utf-8")

    recipe, _raw = load_recipe_with_raw(path)

    assert recipe.output.common.include_cap_model == "false"
    assert recipe.output.common.include_parasitic_cap_model == "true"
    assert recipe.output.common.include_res_model == "false"
    # Untouched by the file, so it keeps the model default rather than
    # being dragged along by the migration.
    assert recipe.output.common.include_parasitic_res_model == "comment"


def test_v1_upgrade_survives_a_save(tmp_path: Path) -> None:
    """The comment tree has to follow, or the next save re-breaks the file."""

    from auto_ext.model.recipe import dump_recipe_yaml, load_recipe_with_raw

    path = tmp_path / "rc-v1.yaml"
    path.write_text(_v1_recipe_text(), encoding="utf-8")

    recipe, raw = load_recipe_with_raw(path)
    path.write_text(dump_recipe_yaml(recipe, raw=raw), encoding="utf-8")

    again, _ = load_recipe_with_raw(path)
    assert again.output.common.include_cap_model == "false"
    assert again.output.common.include_parasitic_cap_model == "true"


def test_v1_extract_scalars_still_become_a_one_rule_list(tmp_path: Path) -> None:
    from auto_ext.model.recipe import load_recipe_with_raw

    path = tmp_path / "rc-v1.yaml"
    path.write_text(_v1_recipe_text(), encoding="utf-8")

    recipe, _ = load_recipe_with_raw(path)
    assert [(r.selection.value, r.type.value) for r in recipe.extraction.extract] == [
        ("all", "rc_coupled")
    ]


@pytest.mark.parametrize(
    "line, dead_value",
    [
        ("  extract_type: c_only\n", "c_only"),
        ("  metal_fill: actual\n", "actual"),
    ],
)
def test_removed_enum_members_fail_loudly_rather_than_being_guessed(
    tmp_path: Path, line: str, dead_value: str
) -> None:
    """The 2026-08-25 round deleted two members with no unambiguous successor.

    ``c_only`` split into coupled / decoupled / decoupled_to_substrate and
    ``actual`` into floating / grounded -- picking one for the user would be
    choosing an extraction physics nobody asked for. So unlike the boolean
    coercion, these are deliberately *not* migrated. The value of the test is
    that the refusal stays a decision instead of decaying into an oversight.
    """

    from auto_ext.model.recipe import load_recipe_with_raw

    text = _v1_recipe_text().replace("  extract_type: rc_coupled\n", "")
    text = text.replace("extraction:\n", "extraction:\n" + line)
    path = tmp_path / "rc-dead.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_recipe_with_raw(path)
    # Naming the value and the permitted set is what makes it fixable.
    assert dead_value in str(excinfo.value)


def test_the_recipe_that_shipped_before_the_change_still_loads(tmp_path: Path) -> None:
    """The regression, in the exact shape the red zone hit it.

    ``recipes/rc-typical-55c.yaml`` is the file the package installs, so it is
    the one sitting in every deployed ``config/recipes`` directory.
    """

    from auto_ext.model.recipe import load_recipe_with_raw

    path = tmp_path / "rc-typical-55c.yaml"
    path.write_text(
        _v1_recipe_text().replace(
            "    include_res_model: false\n",
            "    include_res_model: false\n    include_parasitic_res_model: comment\n",
        ),
        encoding="utf-8",
    )
    recipe, _ = load_recipe_with_raw(path)
    assert recipe.output.common.include_parasitic_res_model == "comment"


# ---- ONE CONCEPT, ONE OWNER (2026-09-04) -------------------------------------
# The owner's ruling: "which stages to run appears on both screens -- the stage
# selector should own it". Generalised in backlog decision-008 to one rule --
# a run-time decision belongs to the run bar, a per-cell fact to Cells,
# extraction physics to the recipe, a PDK fact to the profile. Four Recipe
# fields lost their claim that day, and the three real recipes on the red-zone
# disk still carry two of them, so the retirement has to be a load-time
# migration and not a hand edit somebody is asked to remember.


def _retired_recipe_text() -> str:
    """A recipe written before the 2026-09-04 ownership ruling.

    ``stages`` is deliberately *narrower* than the full order: a narrowed set
    was an intent, so the log line has to name it rather than dropping it in
    silence.
    """

    return (
        "schema_version: 1\n"
        "recipe_id: rc-owned-elsewhere\n"
        "name: written before the ownership ruling\n"
        "# which steps this recipe intends to run\n"
        "stages:\n"
        "- si\n"
        "- calibre\n"
        "reduction:\n"
        "  enabled: true\n"
        "  views_to_reduce: av_extracted\n"
        "  output_view_suffix: _red\n"
    )


def test_a_recipe_that_still_declares_stages_loads_without_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``stages`` moved to the run bar, so the Recipe must not carry it.

    The field is dropped on load -- the 2026-08-28 precedent, in place and
    without stopping to ask -- and *silent means "does not stop to ask", not
    "leaves no trace"*: one line names the file and the set that was dropped,
    because a narrower set was somebody's intent.
    """

    import logging

    path = tmp_path / "rc-owned-elsewhere.yaml"
    path.write_text(_retired_recipe_text(), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="auto_ext.model.recipe"):
        recipe, raw = load_recipe_with_raw(path)

    assert not hasattr(recipe, "stages"), (
        "Recipe still carries stages; the run bar owns which stages run"
    )
    assert "stages" not in raw, (
        "the comment-carrying tree still has stages, so the next save writes "
        "it straight back and the upgrade un-happens on every load"
    )
    line = "\n".join(caplog.messages)
    assert "stages" in line and "si, calibre" in line, (
        f"the dropped set was not named in the log: {line!r}"
    )


def test_the_two_jivaro_fields_the_ruling_retired_are_dropped_on_load(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``reduction.enabled`` and ``reduction.views_to_reduce`` go the same way.

    ``enabled`` was half of an AND nobody could see -- the stage had to be
    ticked *and* the flag set, and either one false skipped the reduction in
    silence. ``views_to_reduce`` was a live second copy of the cell's
    ``out_file``: typed non-null it still won for Jivaro alone, which is
    exactly the "Jivaro pointed at a view that does not exist" bug the
    2026-08-24 ruling closed.
    """

    import logging

    path = tmp_path / "rc-owned-elsewhere.yaml"
    path.write_text(_retired_recipe_text(), encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="auto_ext.model.recipe"):
        recipe, raw = load_recipe_with_raw(path)

    assert not hasattr(recipe.reduction, "enabled")
    assert not hasattr(recipe.reduction, "views_to_reduce")
    # The parameters Jivaro genuinely owns stay put.
    assert recipe.reduction.output_view_suffix == "_red"
    assert "enabled" not in raw["reduction"]
    assert "views_to_reduce" not in raw["reduction"]
    line = "\n".join(caplog.messages)
    assert "reduction.enabled" in line and "reduction.views_to_reduce" in line


def test_the_retirement_survives_a_save(tmp_path: Path) -> None:
    """The 2026-08-28 non-negotiable: the raw tree has to follow the payload."""

    path = tmp_path / "rc-owned-elsewhere.yaml"
    path.write_text(_retired_recipe_text(), encoding="utf-8")

    recipe, raw = load_recipe_with_raw(path)
    text = dump_recipe_yaml(recipe, raw=raw)

    assert "stages:" not in text
    assert "views_to_reduce" not in text
    assert "\n  enabled:" not in text
    path.write_text(text, encoding="utf-8")
    again, _ = load_recipe_with_raw(path)
    assert again.reduction.output_view_suffix == "_red"


def test_the_resource_profile_no_longer_carries_max_workers() -> None:
    """A persisted field nothing read: the ruler's degenerate case.

    ``--jobs`` / the run bar's spin box is the only thing that has ever set
    parallelism -- grep proved nothing resolves ``resources.max_workers`` --
    so the field was a third copy with **zero** owners.
    """

    assert "max_workers" not in ResourceProfile.model_fields
