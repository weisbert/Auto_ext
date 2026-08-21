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
from auto_ext.model.common import STAGE_ORDER, Stage
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
    assert recipe.stages == list(STAGE_ORDER)
    assert recipe.extraction.extract_type is ExtractType.RC_COUPLED
    assert recipe.extraction.metal_fill is MetalFill.VIRTUAL
    assert recipe.output.emit == [OutputKind.EXTRACTED_VIEW]
    assert recipe.lvs.deck_variant == "wodio"
    assert recipe.reduction.enabled is False
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


def test_stages_must_be_non_empty_and_unique() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        make_recipe(stages=[])
    with pytest.raises(ValidationError, match="duplicates"):
        make_recipe(stages=[Stage.SI, Stage.SI])
    assert make_recipe(stages=[Stage.SI, Stage.CALIBRE]).stages == [Stage.SI, Stage.CALIBRE]


def test_emit_must_name_at_least_one_output_form() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        make_recipe(output={"emit": []})
    with pytest.raises(ValidationError, match="duplicates"):
        make_recipe(output={"emit": ["dspf", "dspf"]})


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
    assert recipe.reduction.views_to_reduce == "av_extracted"
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
    # extraction.corner is in PROFILE_FALLBACK_FIELDS too but has no catalog
    # row of its own (the literal belongs to the profile), so it cannot show
    # up as a divergence here.
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
    recipe = make_recipe(
        reduction={"enabled": True, "frequency_limit_ghz": 40.0, "error_max_pct": 5.0}
    )
    snap = recipe.to_snapshot()
    assert snap.jivaro.enabled is True
    assert snap.jivaro.frequency_limit == 40.0
    assert snap.jivaro.error_max == 5.0


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
