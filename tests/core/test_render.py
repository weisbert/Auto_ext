"""Tests for the catalog-driven render pipeline (:mod:`auto_ext.core.render`).

The pipeline replaces ``project.templates`` + ``*.manifest.yaml`` knobs with
Recipe + PdkProfile + catalog, so these tests are mostly about the seams
between those four objects: does a semantic corner become this PDK's literal,
does a catalog row that names a context key the renderer does not build get
caught, and does a setting the template still hardcodes get refused instead of
silently dropped.

Nothing here calls :func:`auto_ext.core.template.resolve_template_path` -- the
new path resolves templates from the catalog, whose ``template_path`` is
derived from ``__file__`` and never from cwd, so the "test cwd is still in the
repo and hits the repo's own templates" trap does not apply.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from auto_ext.catalog import Currently, Owner, builtin_catalog
from auto_ext.core import render
from auto_ext.core.errors import AutoExtError
from auto_ext.core.patch import PatchConflictError, capture_patch, render_masked, sha256_text
from auto_ext.core.patch_models import PatchStatus
from auto_ext.model.common import RenderTarget, Stage
from auto_ext.model.pdk import (
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    ParasiticDeviceContract,
    PdkProfile,
    QrcDeck,
)
from auto_ext.model.recipe import OutputKind, Recipe, ResourceProfile, recipe_from_catalog
from auto_ext.model.run import DutSnapshot

WORK = "/w"

ENV: dict[str, str] = {
    "WORK_ROOT": WORK,
    "WORK_ROOT2": WORK,
    "VERIFY_ROOT": f"{WORK}/fake/verify",
    "SETUP_ROOT": f"{WORK}/fake/setup",
    "PDK_LAYER_MAP_FILE": f"{WORK}/fake/layers.map",
    "calibre_source_added_place": (
        f"{WORK}/fake/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX/empty.cdl"
    ),
}


def make_profile(**overrides: object) -> PdkProfile:
    """A profile shaped like the one office scanning produces for HN001."""

    fields: dict[str, object] = {
        "profile_id": "hn001",
        "display_name": "HN001 22nm",
        "tech_name": "HN001",
        "lvs_decks": LvsDeckSet(
            dir_expr="$calibre_source_added_place|parent",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="wodio",
            runset_version="Ver_Plus_1.0l_0.9",
        ),
        "qrc": QrcDeck(
            dir_expr="$VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CFXXX/QCI_deck",
            runset_version="Ver_Plus_1.0a",
        ),
        "corners": [
            CornerSpec(
                name="typical",
                technology_corner="TYPICAL",
                default_temperature_c=55.0,
                aliases=["nominal"],
            )
        ],
        "default_corner": "typical",
    }
    fields.update(overrides)
    return PdkProfile(**fields)  # type: ignore[arg-type]


def make_dut(**overrides: object) -> DutSnapshot:
    fields: dict[str, object] = {
        "library": "WB_PLL_DCO",
        "cell": "inv",
        "layout_view": "layout",
        "source_view": "schematic",
        "ground_net": "vss",
        "out_file": "av_ext",
    }
    fields.update(overrides)
    return DutSnapshot(**fields)  # type: ignore[arg-type]


def make_run(tmp_path: Path, **overrides: object) -> render.RunFacts:
    fields: dict[str, object] = {
        "run_id": "20260821T143205Z_inv-rc",
        "run_slug": "inv-rc",
        "run_dir": tmp_path / "runs" / "20260821T143205Z_inv-rc",
        "workarea": tmp_path / "workarea",
        "output_dir": f"{WORK}/cds/verify/QCI_PATH_inv",
        "intermediate_dir": WORK,
        "dspf_out_path": f"{WORK}/inv.dspf",
        "started_at": datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc),
        "stages": ("si", "calibre", "quantus"),
    }
    fields.update(overrides)
    return render.RunFacts(**fields)  # type: ignore[arg-type]


@pytest.fixture
def profile() -> PdkProfile:
    return make_profile()


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(recipe_id="rc-coupled-typical", name="RC coupled, typical")


@pytest.fixture
def context(tmp_path: Path, profile: PdkProfile, recipe: Recipe) -> dict[str, object]:
    return render.build_context(
        dut=make_dut(),
        recipe=recipe,
        profile=profile,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )


# ---- corner translation ------------------------------------------------------


def test_corner_name_is_translated_to_the_pdk_literal(
    recipe: Recipe, profile: PdkProfile
) -> None:
    resolved = render.resolve_corner(recipe, profile)
    assert resolved.name == "typical"
    assert resolved.technology_corner == "TYPICAL"
    assert resolved.temperature_c == 55.0
    assert resolved.temperature_source.startswith("pdk.corners[")


def test_corner_alias_resolves_to_the_same_spec(profile: PdkProfile) -> None:
    recipe = Recipe(
        recipe_id="r", name="r", extraction={"corner": "nominal"}
    )
    assert render.resolve_corner(recipe, profile).technology_corner == "TYPICAL"


def test_unknown_corner_is_refused_before_anything_renders(
    profile: PdkProfile, tmp_path: Path
) -> None:
    """The point of the whole seam: a corner this PDK does not define must not
    reach Quantus as a string it will reject hours later."""

    recipe = Recipe(recipe_id="r", name="r", extraction={"corner": "rcworst"})
    with pytest.raises(render.RenderError) as exc:
        render.build_context(
            dut=make_dut(),
            recipe=recipe,
            profile=profile,
            run=make_run(tmp_path),
            resolved_env=ENV,
        )
    assert "rcworst" in str(exc.value)
    assert "hn001" in str(exc.value)
    assert "typical" in str(exc.value)
    # Nothing was written.
    assert not (tmp_path / "rendered").exists()


def test_no_corner_anywhere_is_refused_and_names_both_places(
    recipe: Recipe,
) -> None:
    bare = make_profile(corners=[], default_corner=None)
    with pytest.raises(render.RenderError) as exc:
        render.resolve_corner(recipe, bare)
    assert "recipe.extraction.corner" in str(exc.value)
    assert "default_corner" in str(exc.value)


def test_recipe_temperature_beats_the_corner_default(profile: PdkProfile) -> None:
    recipe = Recipe(recipe_id="r", name="r", extraction={"temperature_c": 27.0})
    resolved = render.resolve_corner(recipe, profile)
    assert resolved.temperature_c == 27.0
    assert resolved.temperature_source == "recipe.extraction.temperature_c"


def test_a_corner_with_no_temperature_anywhere_is_refused(recipe: Recipe) -> None:
    """``-temperature`` has no sane default, so the pipeline refuses rather
    than inventing one."""

    cold = make_profile(
        corners=[CornerSpec(name="typical", technology_corner="TYPICAL")],
        default_corner="typical",
    )
    with pytest.raises(render.RenderError) as exc:
        render.resolve_corner(recipe, cold)
    assert "temperature" in str(exc.value)


def test_render_error_is_an_auto_ext_error() -> None:
    """The runner catches AutoExtError to fail one stage rather than the whole
    dispatch; RenderError has to be inside that net."""

    assert issubclass(render.RenderError, AutoExtError)


# ---- context -----------------------------------------------------------------


def test_context_carries_the_namespaced_tree(context: dict[str, object]) -> None:
    assert context["cell"] == "inv"
    assert context["paths"]["output_dir"] == f"{WORK}/cds/verify/QCI_PATH_inv"  # type: ignore[index]
    assert context["pdk"]["corner"] == "TYPICAL"  # type: ignore[index]
    assert context["recipe"]["extraction"]["min_res_ohm"] == 0.001  # type: ignore[index]
    assert context["run"]["id"] == "20260821T143205Z_inv-rc"  # type: ignore[index]
    assert context["resources"]["lvs_num_turbo"] == 2  # type: ignore[index]
    assert context["env"]["SETUP_ROOT"] == f"{WORK}/fake/setup"  # type: ignore[index]


def test_pdk_paths_are_resolved_and_assembled(context: dict[str, object]) -> None:
    pdk = context["pdk"]  # type: ignore[index]
    assert pdk["lvs_dir"] == (
        f"{WORK}/fake/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX"
    )
    # basename auto-derives from the directory's last segment, as the runner
    # used to do inline.
    assert pdk["lvs_basename"] == "CFXXX"
    assert pdk["lvs_rules_file"].endswith("/CFXXX/CFXXX.wodio.qcilvs")
    assert pdk["qrc_query_cmd"].endswith("/QCI_deck/query_cmd")
    assert pdk["qrc_preserve_cell_list"].endswith("/QCI_deck/preserveCellList.txt")
    assert pdk["tech_library_file"] == f"{WORK}/fake/setup/assura_tech.lib"


def test_every_catalog_row_with_a_context_path_gets_a_flat_alias(
    context: dict[str, object],
) -> None:
    """The shipped templates use the flat names, and the catalog is what
    reserves one name per value. If a row's ``template_var`` were not bound,
    that template would die on StrictUndefined at run time instead of here."""

    catalog = builtin_catalog()
    bound = [o for o in catalog.options if o.context_path is not None]
    assert len(bound) > 100
    for opt in bound:
        assert opt.template_var in context, opt.key


def test_flat_aliases_match_their_namespaced_source(
    context: dict[str, object],
) -> None:
    assert context["min_res"] == context["recipe"]["extraction"]["min_res_ohm"]  # type: ignore[index]
    assert context["output_dir"] == context["paths"]["output_dir"]  # type: ignore[index]
    assert context["calibre_lvs_dir"] == context["pdk"]["lvs_dir"]  # type: ignore[index]
    assert context["cmn_num_turbo"] == context["resources"]["lvs_num_turbo"]  # type: ignore[index]


def test_the_seven_legacy_knobs_are_still_bound_by_their_old_names(
    context: dict[str, object],
) -> None:
    """A recipe-driven render must feed the unmodified templates, so the seven
    knob names the .j2 files reference have to survive verbatim."""

    for name in (
        "lvs_variant",
        "connect_by_name",
        "exclude_floating_nets_limit",
        "coupling_cap_threshold_absolute",
        "coupling_cap_threshold_relative",
        "min_res",
        "temperature",
    ):
        assert name in context, name
    assert context["lvs_variant"] == "wodio"
    assert context["temperature"] == 55.0


def test_cdl_include_alias_is_the_scalar_not_the_list(
    context: dict[str, object],
) -> None:
    """``si.env`` has one ``incFILE`` slot; the profile field is a list."""

    assert context["inc_file"] == context["pdk"]["cdl_include_file"]  # type: ignore[index]
    assert isinstance(context["inc_file"], str)
    assert isinstance(context["pdk"]["cdl_include_files"], list)  # type: ignore[index]


def test_several_cdl_includes_are_refused_with_the_profile_s_own_reason(
    recipe: Recipe, tmp_path: Path
) -> None:
    two = make_profile(cdl_include_files=["$SETUP_ROOT/a.cdl", "$SETUP_ROOT/b.cdl"])
    with pytest.raises(render.RenderError) as exc:
        render.build_context(
            dut=make_dut(),
            recipe=recipe,
            profile=two,
            run=make_run(tmp_path),
            resolved_env=ENV,
        )
    assert "incFILE" in str(exc.value)


def test_enum_values_are_bound_as_their_string_value(
    context: dict[str, object],
) -> None:
    assert context["extract_type"] == "rc_coupled"
    assert context["metal_fill_type"] == "virtual"


def test_a_catalog_row_pointing_at_an_unbuilt_key_is_caught_here(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recipe: Recipe, profile: PdkProfile
) -> None:
    """Catalog/renderer drift must fail with the row's name attached, not as a
    StrictUndefined five stages later."""

    catalog = builtin_catalog()
    broken = catalog.model_copy(
        update={
            "options": [
                *catalog.options,
                catalog.options[0].model_copy(
                    update={
                        "key": "invented_row",
                        "template_var": "invented_row",
                        "context_path": "pdk.no_such_key",
                        "lands_in": [],
                    }
                ),
            ]
        }
    )
    with pytest.raises(render.RenderError) as exc:
        render.build_context(
            dut=make_dut(),
            recipe=recipe,
            profile=profile,
            run=make_run(tmp_path),
            resolved_env=ENV,
            catalog=broken,
        )
    assert "invented_row" in str(exc.value)
    assert "pdk.no_such_key" in str(exc.value)


def test_a_template_var_that_shadows_a_namespace_is_refused(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile
) -> None:
    catalog = builtin_catalog()
    broken = catalog.model_copy(
        update={
            "options": [
                *catalog.options,
                catalog.options[0].model_copy(
                    update={
                        "key": "shadow_row",
                        "template_var": "recipe",
                        "context_path": "cell",
                        "lands_in": [],
                    }
                ),
            ]
        }
    )
    with pytest.raises(render.RenderError) as exc:
        render.build_context(
            dut=make_dut(),
            recipe=recipe,
            profile=profile,
            run=make_run(tmp_path),
            resolved_env=ENV,
            catalog=broken,
        )
    assert "namespace" in str(exc.value)


def test_flatten_context_produces_dotted_json_scalars(
    context: dict[str, object],
) -> None:
    flat = render.flatten_context(context)
    assert flat["recipe.extraction.min_res_ohm"] == 0.001
    assert flat["pdk.corner"] == "TYPICAL"
    assert flat["cell"] == "inv"
    # Lists join rather than nest: the record's field is JsonScalar and its job
    # is a readable diff between two runs.
    assert flat["recipe.netlist.view_list"] == "auCdl schematic"
    for key, value in flat.items():
        assert value is None or isinstance(value, (str, int, float, bool)), key


# ---- planning ----------------------------------------------------------------


def test_default_recipe_plans_one_file_per_stage(recipe: Recipe) -> None:
    plans = render.plan_targets(recipe)
    assert [(p.stage_key, p.target.value) for p in plans] == [
        ("si", "si.env"),
        ("calibre", "lvs.qci"),
        ("quantus", "quantus.ext.cmd"),
        ("jivaro", "jivaro.xml"),
    ]


def test_emitting_both_output_forms_runs_quantus_twice() -> None:
    """``TemplatePaths`` has one quantus slot, so the legacy path structurally
    could not produce an extracted view and a DSPF from one run."""

    recipe = Recipe(
        recipe_id="both",
        name="both",
        output={"emit": [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]},
    )
    plans = render.plan_targets(recipe)
    quantus = [p for p in plans if p.stage is Stage.QUANTUS]
    assert [p.stage_key for p in quantus] == ["quantus.ext", "quantus.dspf"]
    assert [p.target for p in quantus] == [
        RenderTarget.QUANTUS_EXT,
        RenderTarget.QUANTUS_DSPF,
    ]


def test_dspf_only_recipe_plans_the_dspf_target() -> None:
    recipe = Recipe(
        recipe_id="dspf", name="dspf", output={"emit": [OutputKind.DSPF]}
    )
    quantus = [p for p in render.plan_targets(recipe) if p.stage is Stage.QUANTUS]
    assert [(p.stage_key, p.target) for p in quantus] == [
        ("quantus", RenderTarget.QUANTUS_DSPF)
    ]


def test_stage_narrowing_intersects_recipe_and_caller(recipe: Recipe) -> None:
    plans = render.plan_targets(recipe, stages=["calibre", "quantus"])
    assert [p.stage_key for p in plans] == ["calibre", "quantus"]


def test_a_recipe_that_omits_a_stage_never_plans_it() -> None:
    recipe = Recipe(
        recipe_id="lvs-only", name="lvs only", stages=[Stage.SI, Stage.CALIBRE]
    )
    plans = render.plan_targets(recipe, stages=list(s.value for s in Stage))
    assert [p.stage_key for p in plans] == ["si", "calibre"]


def test_every_target_has_a_filename_and_a_stage_key() -> None:
    for target in RenderTarget:
        assert target in render.RENDERED_FILENAMES
        assert target in render.STAGE_KEYS


# ---- representability --------------------------------------------------------


def test_a_default_recipe_is_fully_representable(
    recipe: Recipe, profile: PdkProfile
) -> None:
    """The whole check is only useful if it is silent for the settings that
    reproduce today's files."""

    corner = render.resolve_corner(recipe, profile)
    assert (
        render.check_representable(
            list(RenderTarget),
            recipe=recipe,
            profile=profile,
            resources=ResourceProfile(),
            corner=corner,
        )
        == []
    )


def test_the_catalog_s_own_default_recipe_is_representable(
    profile: PdkProfile,
) -> None:
    built = recipe_from_catalog()
    corner = render.resolve_corner(built, profile)
    assert (
        render.check_representable(
            list(RenderTarget),
            recipe=built,
            profile=profile,
            resources=ResourceProfile(),
            corner=corner,
        )
        == []
    )


def test_a_hardcoded_setting_the_user_changed_is_reported(
    profile: PdkProfile,
) -> None:
    changed = Recipe(
        recipe_id="r", name="r", extraction={"decoupling_factor": 0.5}
    )
    corner = render.resolve_corner(changed, profile)
    found = render.check_representable(
        [RenderTarget.QUANTUS_EXT],
        recipe=changed,
        profile=profile,
        resources=ResourceProfile(),
        corner=corner,
    )
    assert [f.option_key for f in found] == ["decoupling_factor"]
    assert found[0].wanted == 0.5
    assert found[0].template_literal == 1.0
    assert "decoupling_factor" in found[0].describe()


def test_a_hardcoded_setting_is_only_reported_for_the_files_that_carry_it(
    profile: PdkProfile,
) -> None:
    """``metal_fill`` is a dspf.cmd line; ext.cmd has no metal_fill section."""

    changed = Recipe(recipe_id="r", name="r", extraction={"metal_fill": "none"})
    corner = render.resolve_corner(changed, profile)
    assert (
        render.check_representable(
            [RenderTarget.QUANTUS_EXT],
            recipe=changed,
            profile=profile,
            resources=ResourceProfile(),
            corner=corner,
        )
        == []
    )
    assert [
        f.option_key
        for f in render.check_representable(
            [RenderTarget.QUANTUS_DSPF],
            recipe=changed,
            profile=profile,
            resources=ResourceProfile(),
            corner=corner,
        )
    ] == ["metal_fill_type"]


def test_a_knob_backed_setting_is_never_reported(profile: PdkProfile) -> None:
    """The seven knobs are real ``[[var]]`` bindings, so changing one is
    expressible and must not trip the check."""

    changed = Recipe(recipe_id="r", name="r", extraction={"min_res_ohm": 0.05})
    corner = render.resolve_corner(changed, profile)
    assert (
        render.check_representable(
            list(RenderTarget),
            recipe=changed,
            profile=profile,
            resources=ResourceProfile(),
            corner=corner,
        )
        == []
    )


def test_a_profile_owned_hardcoded_value_is_reported(recipe: Recipe) -> None:
    other = make_profile(
        parasitics=ParasiticDeviceContract(
            res_component="myres",
            cap_component="pcapacitor",
            res_model="analogLib/myres/symbol",
        )
    )
    corner = render.resolve_corner(recipe, other)
    found = render.check_representable(
        list(RenderTarget),
        recipe=recipe,
        profile=other,
        resources=ResourceProfile(),
        corner=corner,
    )
    assert sorted(f.option_key for f in found) == [
        "parasitic_res_model",
        "res_component",
    ]


def test_a_non_typical_corner_is_reported_because_the_template_froze_it(
    recipe: Recipe,
) -> None:
    """The corner translation works, but ``ext.cmd.j2`` still types
    ``"TYPICAL"``. Reporting it is the honest answer: the alternative is
    resolving RCWORST and writing TYPICAL anyway."""

    worst = make_profile(
        corners=[
            CornerSpec(
                name="rcworst",
                technology_corner="RCWORST",
                default_temperature_c=125.0,
            )
        ],
        default_corner="rcworst",
    )
    corner = render.resolve_corner(recipe, worst)
    assert corner.technology_corner == "RCWORST"
    found = render.check_representable(
        [RenderTarget.QUANTUS_EXT],
        recipe=recipe,
        profile=worst,
        resources=ResourceProfile(),
        corner=corner,
    )
    assert [f.option_key for f in found] == ["technology_corner"]


def test_a_resources_value_the_template_hardcodes_is_reported(
    recipe: Recipe, profile: PdkProfile
) -> None:
    corner = render.resolve_corner(recipe, profile)
    found = render.check_representable(
        [RenderTarget.LVS_QCI],
        recipe=recipe,
        profile=profile,
        resources=ResourceProfile(lvs_num_turbo=16),
        corner=corner,
    )
    assert [f.option_key for f in found] == ["lvs_num_turbo"]


def test_an_empty_profile_table_reads_as_not_stated(recipe: Recipe) -> None:
    """A freshly discovered profile has no supply-name table yet; the template
    still writes the full list, so the render is byte-identical to today's and
    refusing it would make discovery useless."""

    fresh = make_profile(power_names=[], ground_names=[])
    corner = render.resolve_corner(recipe, fresh)
    assert (
        render.check_representable(
            [RenderTarget.LVS_QCI],
            recipe=recipe,
            profile=fresh,
            resources=ResourceProfile(),
            corner=corner,
        )
        == []
    )


def test_every_checkable_catalog_row_can_be_read(
    recipe: Recipe, profile: PdkProfile
) -> None:
    """Inventory and renderer stay in sync: adding a hardcoded row to
    options.yaml without teaching this module to read it must fail loudly, and
    today nothing does."""

    corner = render.resolve_corner(recipe, profile)
    checkable = [
        o
        for o in builtin_catalog().options
        if o.currently is Currently.HARDCODED_LITERAL
        and o.owner in (Owner.RECIPE, Owner.PROFILE, Owner.RESOURCES)
    ]
    assert len(checkable) > 50
    for opt in checkable:
        render.declared_value(
            opt,
            recipe=recipe,
            profile=profile,
            resources=ResourceProfile(),
            corner=corner,
        )


def test_an_unwired_profile_row_raises_rather_than_passing_unchecked(
    recipe: Recipe, profile: PdkProfile
) -> None:
    catalog = builtin_catalog()
    row = catalog.option("power_names").model_copy(
        update={"key": "invented_profile_row"}
    )
    with pytest.raises(render.RenderError) as exc:
        render.declared_value(
            row,
            recipe=recipe,
            profile=profile,
            resources=ResourceProfile(),
            corner=render.resolve_corner(recipe, profile),
        )
    assert "_PROFILE_DECLARED" in str(exc.value)


# ---- rendering ---------------------------------------------------------------


def _render_all(
    tmp_path: Path,
    recipe: Recipe,
    profile: PdkProfile,
    context: dict[str, object],
) -> dict[str, render.RenderedFile]:
    out: dict[str, render.RenderedFile] = {}
    for plan in render.plan_targets(recipe):
        out[plan.stage_key] = render.render_one(
            plan,
            context=context,
            recipe=recipe,
            profile=profile,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
        )
    return out


def test_every_production_template_renders_from_a_recipe(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile, context: dict[str, object]
) -> None:
    files = _render_all(tmp_path, recipe, profile, context)
    assert sorted(p.name for p in (tmp_path / "rendered").iterdir()) == [
        "ext.cmd",
        "jivaro.xml",
        "lvs.qci",
        "si.env",
    ]
    assert 'simLibName = "WB_PLL_DCO"' in files["si"].text
    assert files["calibre"].text.splitlines()[0].endswith("/CFXXX.wodio.qcilvs")
    assert '-min_res 0.001' in files["quantus"].text
    assert '<inputView value="WB_PLL_DCO/inv/av_ext"/>' in files["jivaro"].text


def test_the_rendered_files_are_named_after_the_artifact_not_the_template(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile, context: dict[str, object]
) -> None:
    files = _render_all(tmp_path, recipe, profile, context)
    assert files["si"].out_path.name == "si.env"
    assert files["calibre"].out_path.name == "lvs.qci"


def test_no_env_reference_survives_into_any_rendered_file(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile, context: dict[str, object]
) -> None:
    """Hard rule: si and jivaro do not expand ``$VAR`` inside string values."""

    from auto_ext.core.env import discover_required_vars

    for rendered in _render_all(tmp_path, recipe, profile, context).values():
        assert discover_required_vars([rendered.text]) == set()
    text = (tmp_path / "rendered" / "si.env").read_text(encoding="utf-8")
    assert "$calibre_source_added_place" not in text
    assert text.count("$") == 0


def test_the_optional_connect_by_name_line_keeps_the_hugging_form(
    tmp_path: Path, profile: PdkProfile, context: dict[str, object]
) -> None:
    """``trim_blocks`` is off, so an ordinary ``[% if %]`` block would leave a
    blank line the real export does not have."""

    off = _render_all(tmp_path, Recipe(recipe_id="a", name="a"), profile, context)
    lines = off["calibre"].text.splitlines()
    assert "*cmnShowOptions: 1" in lines
    i = lines.index("*cmnShowOptions: 1")
    assert lines[i + 1] == "*cmnSpecifyLicenseWaitTime: 1"

    on_ctx = render.build_context(
        dut=make_dut(),
        recipe=Recipe(recipe_id="b", name="b", lvs={"connect_by_name": True}),
        profile=profile,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )
    on = render.render_one(
        render.plan_targets(Recipe(recipe_id="b", name="b"))[1],
        context=on_ctx,
        recipe=Recipe(recipe_id="b", name="b", lvs={"connect_by_name": True}),
        profile=profile,
        resolved_env=ENV,
        out_dir=tmp_path / "on",
    )
    lines = on.text.splitlines()
    i = lines.index("*cmnShowOptions: 1")
    assert lines[i + 1] == "*cmnVConnectNamesState: ALL"
    assert lines[i + 2] == "*cmnSpecifyLicenseWaitTime: 1"


def test_both_quantus_forms_render_to_different_files(
    tmp_path: Path, profile: PdkProfile
) -> None:
    both = Recipe(
        recipe_id="both",
        name="both",
        output={"emit": [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]},
    )
    ctx = render.build_context(
        dut=make_dut(),
        recipe=both,
        profile=profile,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )
    files = {
        plan.stage_key: render.render_one(
            plan,
            context=ctx,
            recipe=both,
            profile=profile,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
        )
        for plan in render.plan_targets(both)
        if plan.stage is Stage.QUANTUS
    }
    assert files["quantus.ext"].out_path.name == "ext.cmd"
    assert files["quantus.dspf"].out_path.name == "dspf.cmd"
    assert "-type extracted_view" in files["quantus.ext"].text
    assert "-type dspf" in files["quantus.dspf"].text
    assert f'-file_name "{WORK}/inv.dspf"' in files["quantus.dspf"].text


def test_rendering_refuses_a_setting_the_template_hardcodes(
    tmp_path: Path, profile: PdkProfile
) -> None:
    changed = Recipe(
        recipe_id="r", name="r", extraction={"decoupling_factor": 0.5}
    )
    ctx = render.build_context(
        dut=make_dut(),
        recipe=changed,
        profile=profile,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )
    plan = [p for p in render.plan_targets(changed) if p.stage is Stage.QUANTUS][0]
    with pytest.raises(render.RenderError) as exc:
        render.render_one(
            plan,
            context=ctx,
            recipe=changed,
            profile=profile,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
        )
    message = str(exc.value)
    assert "decoupling_factor" in message
    assert "Recipe.patches" in message
    assert not (tmp_path / "rendered").exists()


def test_a_none_valued_reference_is_refused_rather_than_stringified(
    tmp_path: Path, recipe: Recipe
) -> None:
    """A profile with no LVS deck dir would otherwise write
    ``None/None.wodio.qcilvs`` into the runset."""

    hollow = make_profile(lvs_decks=LvsDeckSet())
    ctx = render.build_context(
        dut=make_dut(),
        recipe=recipe,
        profile=hollow,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )
    plan = [p for p in render.plan_targets(recipe) if p.stage is Stage.CALIBRE][0]
    with pytest.raises(render.RenderError) as exc:
        render.render_one(
            plan,
            context=ctx,
            recipe=recipe,
            profile=hollow,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
        )
    assert "calibre_lvs_dir" in str(exc.value)
    assert "None" in str(exc.value)


def test_an_unresolved_env_var_is_refused_before_rendering(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile, context: dict[str, object]
) -> None:
    thin = {k: v for k, v in ENV.items() if k != "SETUP_ROOT"}
    plan = [p for p in render.plan_targets(recipe) if p.stage is Stage.QUANTUS][0]
    with pytest.raises(render.RenderError) as exc:
        render.render_one(
            plan,
            context=context,
            recipe=recipe,
            profile=profile,
            resolved_env=thin,
            out_dir=tmp_path / "rendered",
        )
    assert "SETUP_ROOT" in str(exc.value)


def test_write_false_renders_without_touching_the_disk(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile, context: dict[str, object]
) -> None:
    plan = render.plan_targets(recipe)[0]
    rendered = render.render_one(
        plan,
        context=context,
        recipe=recipe,
        profile=profile,
        resolved_env=ENV,
        out_dir=tmp_path / "rendered",
        write=False,
    )
    assert rendered.text
    assert not rendered.out_path.exists()


def test_templates_root_override_renders_from_a_temporary_tree(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile, context: dict[str, object]
) -> None:
    root = tmp_path / "templates"
    (root / "si").mkdir(parents=True)
    (root / "si" / "default.env.j2").write_text(
        'simCellName = "[[cell]]"\n', encoding="utf-8"
    )
    plan = render.plan_targets(recipe)[0]
    rendered = render.render_one(
        plan,
        context=context,
        recipe=recipe,
        profile=profile,
        resolved_env=ENV,
        out_dir=tmp_path / "rendered",
        templates_root=root,
    )
    assert rendered.text == 'simCellName = "inv"\n'


# ---- patches -----------------------------------------------------------------


def _patch_for(
    recipe: Recipe, profile: PdkProfile, context: dict[str, object], edit: object
):
    """Capture a patch against the si.env render, using the same masked twin
    the apply path will build."""

    plan = render.plan_targets(recipe)[0]
    source = plan.spec.template_path.read_text(encoding="utf-8")
    from auto_ext.core.env import substitute_env

    substituted = substitute_env(source, ENV)
    from auto_ext.core.patch import mask_values
    from auto_ext.core.template import make_jinja_env

    base_real = make_jinja_env().from_string(substituted).render(**dict(context))
    base_masked = render_masked(substituted, context)
    values = mask_values(substituted, context)
    return capture_patch(
        template_source=substituted,
        template_sha256=sha256_text(source),
        stage=Stage.SI,
        template_id=plan.spec.template_id,
        profile_id=profile.profile_id,
        catalog_version=builtin_catalog().catalog_version,
        base_real=base_real,
        base_masked=base_masked,
        edited_real=edit,  # type: ignore[arg-type]
        values=values,
    )


def test_a_recipe_patch_is_applied_and_reported(
    tmp_path: Path, profile: PdkProfile, context: dict[str, object]
) -> None:
    recipe = Recipe(recipe_id="r", name="r")
    plan = render.plan_targets(recipe)[0]
    base = render.render_one(
        plan,
        context=context,
        recipe=recipe,
        profile=profile,
        resolved_env=ENV,
        out_dir=tmp_path / "base",
    ).text
    edited = base.replace('checkScale = "meter"', 'checkScale = "micron"')
    patch = _patch_for(recipe, profile, context, edited)
    patched_recipe = Recipe(recipe_id="r", name="r", patches=[patch])

    rendered = render.render_one(
        plan,
        context=context,
        recipe=patched_recipe,
        profile=profile,
        resolved_env=ENV,
        out_dir=tmp_path / "rendered",
    )
    assert 'checkScale = "micron"' in rendered.text
    assert 'checkScale = "meter"' in rendered.base_text
    report = rendered.patch_report
    assert report is not None
    assert report.template_id == "si/default.env.j2"
    assert [o.status for o in report.outcomes] == [PatchStatus.CLEAN]
    assert report.blocked is False


def test_a_patch_that_cannot_be_placed_blocks_the_render(
    tmp_path: Path, profile: PdkProfile, context: dict[str, object]
) -> None:
    recipe = Recipe(recipe_id="r", name="r")
    plan = render.plan_targets(recipe)[0]
    base = render.render_one(
        plan,
        context=context,
        recipe=recipe,
        profile=profile,
        resolved_env=ENV,
        out_dir=tmp_path / "base",
    ).text
    edited = base.replace('checkScale = "meter"', 'checkScale = "micron"')
    patch = _patch_for(recipe, profile, context, edited)
    # Rewrite the hunk so it anchors on a line no render will ever contain.
    lost = patch.model_copy(
        update={
            "hunks": [
                patch.hunks[0].model_copy(
                    update={
                        "before": 'nothingLikeThis = "gone"\n',
                        "context_before": "",
                        "context_after": "",
                    }
                )
            ]
        }
    )
    with pytest.raises(PatchConflictError) as exc:
        render.render_one(
            plan,
            context=context,
            recipe=Recipe(recipe_id="r", name="r", patches=[lost]),
            profile=profile,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
        )
    assert exc.value.template_id == "si/default.env.j2"
    assert not (tmp_path / "rendered").exists()


def test_no_patch_means_no_report(
    tmp_path: Path, recipe: Recipe, profile: PdkProfile, context: dict[str, object]
) -> None:
    rendered = render.render_one(
        render.plan_targets(recipe)[0],
        context=context,
        recipe=recipe,
        profile=profile,
        resolved_env=ENV,
        out_dir=tmp_path / "rendered",
    )
    assert rendered.patch_report is None
    assert rendered.text == rendered.base_text


# ---- env discovery -----------------------------------------------------------


def test_required_env_vars_covers_templates_and_profile(profile: PdkProfile) -> None:
    required = render.required_env_vars(profile)
    # From the templates:
    assert "SETUP_ROOT" in required
    # From the profile's own path expressions:
    assert "calibre_source_added_place" in required
    assert "VERIFY_ROOT" in required
    assert "PDK_LAYER_MAP_FILE" in required


def test_required_env_vars_adds_tech_name_candidates_only_when_unset() -> None:
    named = make_profile(tech_name="HN001")
    unnamed = make_profile(tech_name=None)
    assert "PDK_TECH_FILE" not in render.required_env_vars(named)
    assert "PDK_TECH_FILE" in render.required_env_vars(unnamed)


def test_required_env_vars_honours_the_profile_s_declared_list() -> None:
    declared = make_profile(required_env=["MY_OWN_VAR"])
    assert "MY_OWN_VAR" in render.required_env_vars(declared)


def test_required_env_vars_includes_extra_sources(profile: PdkProfile) -> None:
    required = render.required_env_vars(
        profile, extra_sources=["${WORK_ROOT}/cds/{cell}"]
    )
    assert "WORK_ROOT" in required


def test_an_lvs_variant_the_profile_does_not_define_is_refused(
    tmp_path: Path,
) -> None:
    """Same seam as the corner: the recipe names a variant, the profile owns
    which variants exist."""

    recipe = Recipe(recipe_id="r", name="r", lvs={"deck_variant": "widio"})
    with pytest.raises(render.RenderError) as exc:
        render.build_context(
            dut=make_dut(),
            recipe=recipe,
            profile=make_profile(),
            run=make_run(tmp_path),
            resolved_env=ENV,
        )
    assert "widio" in str(exc.value)
    assert "wodio" in str(exc.value)


def test_an_undiscovered_variant_table_does_not_block_the_render(
    tmp_path: Path, recipe: Recipe
) -> None:
    """A profile scanned before anyone ran ``ls`` on the deck directory has an
    empty variant table. The rules-file path is still assembled from the
    recipe's chosen suffix, exactly as the template did before -- refusing here
    would make discovery useless, and the health check already reports the
    empty table."""

    fresh = make_profile(
        lvs_decks=LvsDeckSet(dir_expr="$calibre_source_added_place|parent")
    )
    ctx = render.build_context(
        dut=make_dut(),
        recipe=recipe,
        profile=fresh,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )
    assert ctx["pdk"]["lvs_rules_file"] is None
    rendered = render.render_one(
        [p for p in render.plan_targets(recipe) if p.stage is Stage.CALIBRE][0],
        context=ctx,
        recipe=recipe,
        profile=fresh,
        resolved_env=ENV,
        out_dir=tmp_path / "rendered",
    )
    assert rendered.text.splitlines()[0].endswith("/CFXXX.wodio.qcilvs")
