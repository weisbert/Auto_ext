"""The built-in parameter catalog: schema, self-consistency, and the audit.

The load-bearing test in here is :func:`test_catalog_and_templates_agree`.
It walks every ``[[var]]`` in the five shipped templates and every catalog row
that claims one, in both directions, so adding a template variable without a
catalog row (or renaming one out from under a row) fails immediately instead
of turning up months later as a value that quietly stopped being written.

Everything else guards a specific way the previous system rotted: a knob whose
default was outside its own range, a "confidence" column that meant two things
at once, a range somebody invented that later got read as a spec.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_ext.catalog.spec import (
    BUILTIN_CATALOG_PATH,
    Catalog,
    CatalogError,
    Confidence,
    Currently,
    LandingSite,
    Layout,
    OptionSpec,
    OptionType,
    Owner,
    Quoting,
    RenderTargetSpec,
    audit_template_vars,
    builtin_catalog,
    default_templates_root,
    load_catalog,
)
from auto_ext.model.common import RenderTarget, Stage


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return builtin_catalog()


@pytest.fixture(scope="module")
def repo_root() -> Path:
    # Derived from the package location, never from cwd: the suite runs with
    # cwd inside the repository, and a cwd-relative path can pass by finding
    # the repository's own copy of a file it was never pointed at.
    return default_templates_root().parent


# ---- loading ----------------------------------------------------------------


def test_builtin_catalog_loads_and_validates(catalog: Catalog) -> None:
    assert catalog.schema_version == 1
    assert catalog.catalog_version
    assert len(catalog.targets) == 5
    assert len(catalog.options) > 150


def test_every_target_names_a_template_that_exists(catalog: Catalog) -> None:
    for target in catalog.targets:
        assert target.template_path.is_file(), target.template


def test_load_catalog_reports_a_missing_file() -> None:
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(Path("no-such-catalog.yaml"))


def test_load_catalog_reports_a_broken_file(tmp_path: Path) -> None:
    broken = tmp_path / "options.yaml"
    broken.write_text("schema_version: 1\ntargets: [\n", encoding="utf-8")
    with pytest.raises(CatalogError, match="YAML parse error"):
        load_catalog(broken)


# ---- identity and uniqueness -------------------------------------------------


def test_keys_and_template_vars_are_unique(catalog: Catalog) -> None:
    keys = [o.key for o in catalog.options]
    tvars = [o.template_var for o in catalog.options]
    assert len(keys) == len(set(keys))
    assert len(tvars) == len(set(tvars))


def _minimal_option(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "key": "sample",
        "template_var": "sample",
        "owner": "fixed",
        "type": "str",
        "default": "x",
        "choices_confidence": "certain",
        "currently": "hardcoded_literal",
        "observed": True,
        "source_ref": "templates/si/default.env.j2:1",
        "why": "because",
    }
    base.update(over)
    return base


def _catalog_payload(*options: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "catalog_version": "test",
        "targets": [
            {
                "id": "si.env",
                "stage": "si",
                "template": "templates/si/default.env.j2",
                "template_id": "si/default.env.j2",
                "syntax": "skill",
                "quoting": "double",
                "layout": "own_line",
                "indent": 0,
                "continuation": False,
            }
        ],
        "options": list(options),
    }


def test_duplicate_template_var_is_rejected() -> None:
    payload = _catalog_payload(
        _minimal_option(key="a", template_var="dup"),
        _minimal_option(key="b", template_var="dup"),
    )
    with pytest.raises(ValidationError, match="duplicate template_var"):
        Catalog.model_validate(payload)


def test_duplicate_key_is_rejected() -> None:
    payload = _catalog_payload(
        _minimal_option(key="dup", template_var="a"),
        _minimal_option(key="dup", template_var="b"),
    )
    with pytest.raises(ValidationError, match="duplicate option keys"):
        Catalog.model_validate(payload)


def test_unknown_render_target_is_rejected() -> None:
    payload = _catalog_payload(
        _minimal_option(
            lands_in=[{"target": "jivaro.xml", "section": "s", "option": "o"}]
        )
    )
    with pytest.raises(ValidationError, match="unknown render target"):
        Catalog.model_validate(payload)


def test_a_target_less_site_must_name_its_stage() -> None:
    payload = _catalog_payload(
        _minimal_option(lands_in=[{"section": "argv", "option": "-topCell"}])
    )
    with pytest.raises(ValidationError, match="must name its stage"):
        Catalog.model_validate(payload)


# ---- the schema rules that stopped the last system from rotting -------------


def test_choices_are_only_for_enum_or_list() -> None:
    with pytest.raises(ValidationError, match="choices is only meaningful"):
        OptionSpec.model_validate(_minimal_option(type="str", choices=["a", "b"]))


def test_an_enum_must_list_its_choices() -> None:
    with pytest.raises(ValidationError, match="must list its choices"):
        OptionSpec.model_validate(_minimal_option(type="enum", default="a"))


def test_an_enum_default_must_be_one_of_its_choices() -> None:
    with pytest.raises(ValidationError, match="is not in"):
        OptionSpec.model_validate(
            _minimal_option(type="enum", choices=["a", "b"], default="c")
        )


def test_range_is_only_for_numbers() -> None:
    with pytest.raises(ValidationError, match="range is only meaningful"):
        OptionSpec.model_validate(_minimal_option(type="str", range=(0, 1)))


def test_a_default_outside_its_own_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="outside"):
        OptionSpec.model_validate(
            _minimal_option(type="int", default=500, range=(1, 64))
        )


def test_range_verified_needs_a_range_to_verify() -> None:
    with pytest.raises(ValidationError, match="no range to verify"):
        OptionSpec.model_validate(_minimal_option(range_verified=True))


def test_observed_and_source_ref_must_agree() -> None:
    # An observed row has to say which file and line it was read from...
    with pytest.raises(ValidationError, match="observed"):
        OptionSpec.model_validate(_minimal_option(observed=True, source_ref=None))
    # ...and only an observed row may claim one.
    with pytest.raises(ValidationError, match="observed"):
        OptionSpec.model_validate(_minimal_option(observed=False))
    OptionSpec.model_validate(
        _minimal_option(observed=False, source_ref=None, currently="absent", default=None)
    )


def test_a_live_recipe_option_must_bind_to_a_recipe_path() -> None:
    with pytest.raises(ValidationError, match="recipe.[*] context path"):
        OptionSpec.model_validate(
            _minimal_option(owner="recipe", context_path="pdk.something")
        )


def test_unknown_columns_are_rejected_not_swallowed() -> None:
    with pytest.raises(ValidationError):
        OptionSpec.model_validate(_minimal_option(confidence="certain"))


# ---- the promises the data itself makes -------------------------------------


def test_no_range_in_the_shipped_catalog_claims_to_be_verified(catalog: Catalog) -> None:
    # Every range in this file was invented as a guard rail. Flipping one to
    # verified is a deliberate act that needs a datasheet behind it.
    assert [o.key for o in catalog.options if o.range_verified] == []
    assert catalog.unverified_ranges(), "ranges exist, they are simply all unverified"


def test_guessed_value_sets_never_become_dropdowns(catalog: Catalog) -> None:
    # DECISIONS.md #19: free text plus a good default beats a combo box that
    # is half invalid.
    for opt in catalog.options:
        if opt.choices_confidence is Confidence.GUESS:
            assert opt.free_input, opt.key


def test_every_option_says_why_it_exists(catalog: Catalog) -> None:
    assert [o.key for o in catalog.options if not o.why.strip()] == []


def test_every_observed_option_points_at_a_real_line(
    catalog: Catalog, repo_root: Path
) -> None:
    problems: list[str] = []
    for opt in catalog.options:
        if not opt.source_ref:
            continue
        ref = opt.source_ref
        name, _, tail = ref.rpartition(":")
        try:
            line = int(tail)
        except ValueError:
            name, line = ref, None
        path = repo_root / name
        if not path.is_file():
            problems.append(f"{opt.key}: {ref} does not exist")
            continue
        if line is not None:
            count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
            if line > count:
                problems.append(f"{opt.key}: {ref} but the file has {count} lines")
    assert problems == []


def test_the_seven_knobs_that_exist_today_are_exactly_these(catalog: Catalog) -> None:
    # Not five. The two quantus manifests declare the same five, and the
    # calibre manifest adds two more; si and jivaro declare none.
    assert sorted(o.key for o in catalog.knobs_today()) == [
        "coupling_cap_threshold_absolute",
        "coupling_cap_threshold_relative",
        "exclude_floating_nets_limit",
        "lvs_connect_by_name",
        "lvs_deck_variant",
        "min_res_ohm",
        "temperature_c",
    ]


def test_owner_split_covers_all_six_objects(catalog: Catalog) -> None:
    counts = catalog.owner_counts()
    assert set(counts) == {o.value for o in Owner}
    assert all(n > 0 for n in counts.values()), counts
    assert sum(counts.values()) == len(catalog.options)


# ---- the corrections the draft catalogs needed ------------------------------


def test_ground_net_is_a_cell_column_defaulting_to_vss(catalog: Catalog) -> None:
    # The draft said "gnd!" and marked it certain; TaskSpec and both shipped
    # tasks.yaml files say vss.
    opt = catalog.option("ground_net")
    assert opt.default == "vss"
    assert opt.owner is Owner.CELLS


def test_resource_settings_are_not_recipe_settings(catalog: Catalog) -> None:
    # DECISIONS.md #21: a Recipe carrying a core count has to be edited when
    # it moves to a bigger machine, which defeats portability.
    for key in (
        "lvs_num_turbo",
        "lvs_run_mt",
        "lvs_run_hyper",
        "lvs_license_wait_time",
        "reduction_cpu",
        "quantus_cpu_count",
    ):
        assert catalog.option(key).owner is Owner.RESOURCES, key


def test_the_four_rows_the_draft_called_knobs_are_not_knobs(catalog: Catalog) -> None:
    # They travel a different configuration path (ProjectConfig / TaskSpec),
    # so deleting the knob machinery will not touch them -- a different
    # migration with a different risk.
    for key in (
        "dspf_out_path",
        "reduction_enabled",
        "reduction_frequency_limit_ghz",
        "reduction_error_max_pct",
    ):
        assert catalog.option(key).currently is not Currently.MANIFEST_KNOB, key


def test_lvs_deck_basename_has_no_invented_pdk_default(catalog: Catalog) -> None:
    # The draft shipped "CFXXX", which is one export's value, not a default.
    opt = catalog.option("lvs_deck_basename")
    assert opt.default is None
    assert opt.owner is Owner.PROFILE


def test_catalog_key_and_template_var_may_differ_and_the_mapping_is_recorded(
    catalog: Catalog,
) -> None:
    for key, template_var in (
        ("lvs_deck_variant", "lvs_variant"),
        ("lvs_connect_by_name", "connect_by_name"),
        ("min_res_ohm", "min_res"),
        ("temperature_c", "temperature"),
        ("reduction_frequency_limit_ghz", "jivaro_frequency_limit"),
        ("reduction_error_max_pct", "jivaro_error_max"),
    ):
        assert catalog.option(key).template_var == template_var
        assert catalog.by_template_var(template_var) is catalog.option(key)


def test_observation_and_confidence_are_separate_columns(catalog: Catalog) -> None:
    # The draft's single "confidence" column had to say both "this line is
    # certainly hardcoded" and "these are certainly the legal values" at once.
    opt = catalog.option("extract_type")
    assert opt.observed is True
    assert opt.choices_confidence is Confidence.GUESS


def test_the_parasitic_device_contract_lives_on_the_profile(catalog: Catalog) -> None:
    # Quantus names two of them, Jivaro four; change one side only and Jivaro
    # cannot read what Quantus wrote.
    for key in (
        "cap_component",
        "res_component",
        "parasitic_res_model",
        "parasitic_cap_model",
        "parasitic_ind_model",
        "parasitic_mutual_model",
    ):
        assert catalog.option(key).owner is Owner.PROFILE, key


def test_the_two_dut_serialisations_are_both_recorded(catalog: Catalog) -> None:
    # Space separated, cell first for Quantus; slash separated, library first
    # for Jivaro. One shared render helper would produce a broken file.
    assert catalog.option("input_db_design_cell_name").default == "{cell} {layout_view} {library}"
    assert catalog.option("jivaro_input_view").default == "{library}/{cell}/{out_file}"


def test_the_suspected_jivaro_view_bug_is_recorded_not_silently_fixed(
    catalog: Catalog,
) -> None:
    views = catalog.option("reduction_views_to_reduce")
    assert views.default == "av_extracted"
    assert catalog.option("out_file").default == "av_ext"
    assert views.question, "the mismatch must stay flagged as an open question"


def test_the_impossible_coupling_threshold_is_flagged(catalog: Catalog) -> None:
    opt = catalog.option("coupling_cap_threshold_absolute")
    assert opt.default == 0.01
    assert opt.unit == "F"
    assert opt.question, "0.01 F is 10 mF; this cannot ship as an unquestioned fact"


# ---- rendering rules ---------------------------------------------------------


def test_a_site_inherits_its_target_defaults(catalog: Catalog) -> None:
    opt = catalog.option("max_via_array_size")
    site = opt.lands_in[0]
    rule = site.render(catalog.target(site.target))
    assert rule.quoting is Quoting.DOUBLE
    assert rule.layout is Layout.INLINE
    assert rule.indent == 14
    assert rule.continuation is True


def test_a_site_may_override_the_default_and_the_difference_is_real(
    catalog: Catalog,
) -> None:
    # ext.cmd:14 writes `auto` bare and ext.cmd:17 writes `"auto"` quoted. The
    # catalog transcribes both rather than tidying them into agreement,
    # because the patch baseline diffs against the generated bytes.
    bare = catalog.option("array_vias_spacing").lands_in[0]
    quoted = catalog.option("max_via_array_size").lands_in[0]
    assert bare.render(catalog.target(bare.target)).quoting is Quoting.BARE
    assert quoted.render(catalog.target(quoted.target)).quoting is Quoting.DOUBLE


def test_split_line_options_are_recorded_as_such(catalog: Catalog) -> None:
    for key in ("temperature_c", "technology_corner", "cdl_out_map_directory"):
        opt = catalog.option(key)
        site = opt.lands_in[0]
        assert site.render(catalog.target(site.target)).layout is Layout.VALUE_ON_NEXT_LINE, key
    xy = catalog.option("output_xy").lands_in[0]
    assert xy.render(catalog.target(xy.target)).layout is Layout.VALUE_PER_LINE


def test_skill_booleans_are_recorded_as_skill_booleans(catalog: Catalog) -> None:
    site = catalog.option("netlist_preserve_all").lands_in[0]
    assert site.render(catalog.target(site.target)).quoting is Quoting.SKILL_BOOL


def test_the_only_conditional_line_is_connect_by_name(catalog: Catalog) -> None:
    # trim_blocks is off, so a conditional line must be written in the hugging
    # form or it leaves a blank line behind. Any new optional site has to copy
    # templates/calibre/calibre_lvs.qci.j2:31-32 exactly.
    optional = [
        (opt.key, site.target, site.line)
        for opt in catalog.options
        for site in opt.lands_in
        if site.optional
    ]
    assert optional == [("lvs_connect_by_name", RenderTarget.LVS_QCI, 31)]


def test_render_defaults_apply_to_a_target_less_site(catalog: Catalog) -> None:
    site = LandingSite(stage=Stage.STRMOUT, section="argv", option="-topCell")
    rule = site.render(None)
    assert rule.quoting is Quoting.NONE
    assert rule.indent == 0
    assert rule.continuation is False


# ---- queries -----------------------------------------------------------------


def test_emission_order_follows_the_file_not_the_logic(catalog: Catalog) -> None:
    rows = catalog.emission_order(RenderTarget.SI_ENV)
    lines = [line for line, _opt, _site in rows]
    assert lines == sorted(lines)
    order = {opt.key: line for line, opt, _site in rows}
    # si exported checkCAPPERI after the diode group instead of with the
    # capacitor group. Regrouping it would diff against every real si.env.
    assert order["netlist_check_cap_peri"] > order["netlist_check_dio_peri"]
    assert order["netlist_check_cap_val"] < order["netlist_preserve_dio"]


def test_lookup_by_owner_stage_target_and_section(catalog: Catalog) -> None:
    assert catalog.option("temperature_c") in catalog.by_owner(Owner.RECIPE)
    assert catalog.option("layer_map") in catalog.by_stage(Stage.STRMOUT)
    assert catalog.option("dspf_subtype") in catalog.by_target(RenderTarget.QUANTUS_DSPF)
    section = catalog.by_section(RenderTarget.QUANTUS_EXT, "filter_coupling_cap")
    assert {o.key for o in section} == {
        "coupling_cap_threshold_absolute",
        "coupling_cap_threshold_relative",
    }


def test_quantus_sections_are_the_command_names(catalog: Catalog) -> None:
    assert catalog.sections_of(RenderTarget.QUANTUS_EXT) == (
        "header",
        "capacitance",
        "extract",
        "extraction_setup",
        "filter_cap",
        "filter_coupling_cap",
        "filter_res",
        "input_db",
        "output_db",
        "output_setup",
        "process_technology",
    )


def test_unknown_key_lookups_raise(catalog: Catalog) -> None:
    with pytest.raises(KeyError):
        catalog.option("no_such_option")
    assert catalog.by_template_var("no_such_var") is None


# ---- the self-check ----------------------------------------------------------


def test_catalog_and_templates_agree(catalog: Catalog) -> None:
    """Catalog rows and template variables must match, in both directions.

    Forwards: a row claiming a value arrives as ``[[var]]`` must be able to
    point at it. Backwards: a ``[[var]]`` in a template must be claimed by a
    row for that file -- which is what makes "added a variable, forgot the
    catalog row" fail here rather than at the next render.
    """

    audit = audit_template_vars(catalog)
    assert audit.ok, "\n" + audit.describe()


def test_the_audit_catches_a_row_the_template_does_not_back() -> None:
    payload = _catalog_payload(
        _minimal_option(
            key="invented",
            template_var="not_in_any_template",
            currently="jinja_var",
            lands_in=[{"target": "si.env", "section": "identity", "option": "x"}],
        )
    )
    audit = audit_template_vars(Catalog.model_validate(payload))
    assert ("invented", "not_in_any_template", "si.env") in audit.missing_in_template
    assert not audit.ok
    assert "does not reference it" in audit.describe()


def test_the_audit_catches_a_template_variable_nobody_declared() -> None:
    # The direction that matters for the future: si.env really does use
    # [[library]], [[cell]], [[lvs_source_view]] and [[output_dir]], and a
    # catalog that declares none of them must fail.
    audit = audit_template_vars(Catalog.model_validate(_catalog_payload()))
    assert sorted(var for _target, var in audit.missing_in_catalog) == [
        "cell",
        "library",
        "lvs_source_view",
        "output_dir",
    ]
    assert "no catalog row claims that variable" in audit.describe()


def test_the_audit_reports_an_unreadable_template(tmp_path: Path) -> None:
    audit = audit_template_vars(
        Catalog.model_validate(_catalog_payload()), templates_root=tmp_path
    )
    assert audit.unreadable_templates == ["templates/si/default.env.j2"]
    assert not audit.ok


# ---- cross-module invariants -------------------------------------------------


def test_stage_enum_matches_the_patch_layers_copy() -> None:
    # auto_ext.core.patch_models declares a structurally identical Stage for
    # the patch layer. Collapsing the two is a C2 job; until then, drift here.
    from auto_ext.core.patch_models import Stage as PatchStage

    assert [s.value for s in Stage] == [s.value for s in PatchStage]


def test_stage_enum_matches_the_runners_stage_order() -> None:
    from auto_ext.core.runner import STAGE_ORDER as RUNNER_STAGE_ORDER

    assert [s.value for s in Stage] == list(RUNNER_STAGE_ORDER)


def test_render_targets_cover_every_shipped_template(catalog: Catalog) -> None:
    shipped = sorted(p.as_posix() for p in default_templates_root().rglob("*.j2"))
    declared = sorted(t.template_path.as_posix() for t in catalog.targets)
    assert declared == shipped


def test_the_builtin_path_is_not_cwd_relative() -> None:
    assert BUILTIN_CATALOG_PATH.is_absolute()
    assert BUILTIN_CATALOG_PATH.is_file()


def test_target_specs_expose_patch_compatible_template_ids(catalog: Catalog) -> None:
    from auto_ext.core.patch_models import TemplatePatch

    import re

    field = TemplatePatch.model_fields["template_id"]
    pattern = next(m.pattern for m in field.metadata if hasattr(m, "pattern"))

    for target in catalog.targets:
        assert re.match(pattern, target.template_id), target.template_id


def test_render_target_spec_rejects_a_malformed_template_id() -> None:
    with pytest.raises(ValidationError):
        RenderTargetSpec.model_validate(
            {
                "id": "si.env",
                "stage": "si",
                "template": "templates/si/default.env.j2",
                "template_id": "not a template id",
                "syntax": "skill",
                "quoting": "double",
                "layout": "own_line",
                "indent": 0,
                "continuation": False,
            }
        )


def test_option_type_enum_covers_every_type_used_in_the_data(catalog: Catalog) -> None:
    used = {o.type for o in catalog.options}
    assert used <= set(OptionType)
    assert OptionType.STRUCTURAL in used, "structural rows carry the plumbing facts"
