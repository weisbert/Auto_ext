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

import pathlib

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
from auto_ext.core.readback import parse_by_syntax
from auto_ext.core.template import scan_placeholders
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


#: The only ranges transcribed out of a vendor manual rather than invented.
#: A range on this list is enforced by the form's validator, so adding a key
#: here means "I read the bound in the manual", not "it looks about right".
VERIFIED_RANGES = {"coupling_cap_threshold_absolute"}


def test_only_the_ranges_read_out_of_a_manual_claim_to_be_verified(
    catalog: Catalog,
) -> None:
    # Every other range in this file was invented as a guard rail, and the
    # form paints those without enforcing them. Flipping one to verified is a
    # deliberate act that needs a datasheet behind it and a line here.
    assert {o.key for o in catalog.options if o.range_verified} == VERIFIED_RANGES
    assert catalog.unverified_ranges(), "the invented ranges are still all unverified"


def test_guessed_value_sets_never_become_dropdowns(catalog: Catalog) -> None:
    # DECISIONS.md #19: free text plus a good default beats a combo box that
    # is half invalid.
    for opt in catalog.options:
        if opt.choices_confidence is Confidence.GUESS:
            assert opt.free_input, opt.key


def test_every_option_says_why_it_exists(catalog: Catalog) -> None:
    assert [o.key for o in catalog.options if not o.why.strip()] == []


def test_a_placeholder_is_a_sentence_and_not_a_leaked_code_comment(
    catalog: Catalog,
) -> None:
    """The form already sets a placeholder off; the text must not do it again.

    ``parasitic_blocking_device_cells_type`` reached a first-time user as
    ``unset - (omitted -- the tool takes white)``: the form supplies the
    "unset" and the dash, and the catalog then added its own brackets and a
    double hyphen, so a parenthesis sat inside a parenthesis and the whole
    thing read like a comment somebody forgot to delete. Two rules, both
    mechanical: no wrapping brackets, and no ``--``.
    """

    bracketed = [
        o.key
        for o in catalog.options
        if o.placeholder and o.placeholder.startswith("(") and o.placeholder.endswith(")")
    ]
    assert bracketed == [], (
        f"{bracketed}: the form writes 'unset - <placeholder>' already, so a "
        "placeholder in brackets renders as a parenthesis inside a parenthesis"
    )
    dashed = [o.key for o in catalog.options if o.placeholder and "--" in o.placeholder]
    assert dashed == [], f"{dashed}: '--' is comment punctuation, not prose"


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
        # ``reduction_enabled`` was the fourth. The row is gone entirely since
        # 2026-09-04: whether the reduction runs is whether the jivaro stage
        # was requested, and the run bar owns that.
        "reduction_frequency_limit_ghz",
        "reduction_error_max_pct",
    ):
        assert catalog.option(key).currently is not Currently.MANIFEST_KNOB, key


def test_only_one_row_can_still_decide_whether_the_reduction_runs(
    catalog: Catalog,
) -> None:
    """The pair that used to AND silently is a single row now.

    Until 2026-09-04 ``reduction_enabled`` and ``stages`` were two independent
    switches over one outcome: the runner reduced only when the flag was true
    AND ``jivaro`` was listed, and the two rows sat in different sections of
    the same form, so a user ticked the stage, left the flag alone, and got no
    reduction and no complaint. The mitigation was prose -- each ``why`` named
    the other row -- which is the shape this project stopped accepting. Both
    rows are gone; ``requested_stages`` is what says whether jivaro runs.
    """

    for gone in ("reduction_enabled", "stages"):
        with pytest.raises(KeyError):
            catalog.option(gone)
    requested = catalog.option("requested_stages")
    assert requested.owner is Owner.RUN
    assert requested.context_path == "run.stages"


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
    # ``max_fracture_length_unit`` is the live specimen: the line is
    # certainly in the file we ship (observed), while the CASE of its two
    # members is not yet confirmed against the tool (likely, not certain).
    # ``extract_type`` used to stand here and no longer can -- its value set
    # was settled from the vendor syntax table, which is exactly the move
    # from guess to certain this column exists to record.
    opt = catalog.option("max_fracture_length_unit")
    assert opt.observed is True
    assert opt.choices_confidence is not Confidence.CERTAIN


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


def test_the_jivaro_view_bug_is_fixed_by_derivation_not_by_a_second_literal(
    catalog: Catalog,
) -> None:
    """The catalog's most suspicious row, resolved 2026-08-24.

    ``viewsToReduce`` used to be the literal ``av_extracted`` while
    ``inputView`` and Quantus ``-view_name`` both used ``out_file`` (``av_ext``
    in both shipped task tables), so Jivaro was pointed at a view that does not
    exist. The fix is a derivation, not a corrected literal: after an
    extraction the view to reduce IS the extraction output, so the row now
    defaults to null and resolves against the DUT. It stays settable for a
    standalone Jivaro run over a view an earlier run produced.
    """

    views = catalog.option("reduction_views_to_reduce")
    assert views.default is None
    assert not views.question, "the question this row carried has been answered"
    assert catalog.option("out_file").default == "av_ext"
    # RETIRED 2026-09-04, one step further than the 2026-08-24 fix. Null-plus-
    # derivation still left a control that, typed into, won for Jivaro alone
    # while Quantus went on writing out_file -- the same bug, one keystroke
    # away. ``owner: fixed`` removes the control; the row keeps its
    # context_path so the template variable still resolves off the render
    # tree, which render._recipe_tree now fills from the cell unconditionally.
    assert views.owner is Owner.FIXED
    assert views.recipe_field_path is None


def test_the_coupling_threshold_is_femtofarads_with_the_vendors_own_range(
    catalog: Catalog,
) -> None:
    """The row this test used to pin the wrong answer for.

    It asserted ``unit: F`` and demanded an open question, on the reasoning
    that 0.01 F is 10 mF and therefore impossible. The impossible half was the
    unit: the manual gives the range as 0 to 100 femtofarads and works its
    example at 3 fF, so 0.01 fF -- also the value in the vendor's own sample
    deck -- was right all along. A test that locks in a wrong answer is worse
    than no test, which is why this one is replaced rather than deleted.
    """

    opt = catalog.option("coupling_cap_threshold_absolute")
    assert opt.default == 0.01
    assert opt.unit == "fF"
    assert opt.range == (0.0, 100.0)
    assert opt.range_verified, "this bound is transcribed, not invented"
    assert not opt.question, "the unit question is answered"


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


def test_every_conditional_line_is_written_in_the_hugging_form(catalog: Catalog) -> None:
    # trim_blocks is off, so a conditional line must be written in the hugging
    # form or it leaves a blank line behind. Any new optional site has to copy
    # templates/calibre/calibre_lvs.qci.j2:31-32 exactly.
    #
    # parasitic_blocking_device_cells_type joined on 2026-09-04. It is the
    # first optional site inside a BACKSLASH-CONTINUED statement, which is why
    # it is asserted rather than merely counted: its own line has to end in a
    # continuation and the line the [% endif %] hugs has to stay the last one
    # in the statement, or the deck Quantus reads is truncated mid-command.
    optional = [
        (opt.key, site.target, site.line)
        for opt in catalog.options
        for site in opt.lands_in
        if site.optional
    ]
    assert sorted(optional) == sorted(
        [
            ("lvs_connect_by_name", RenderTarget.LVS_QCI, 31),
            ("parasitic_blocking_device_cells_type", RenderTarget.QUANTUS_EXT, 19),
            ("parasitic_blocking_device_cells_type", RenderTarget.QUANTUS_DSPF, 19),
        ]
    )

    for name in ("ext", "dspf"):
        lines = (
            pathlib.Path(f"templates/quantus/{name}.cmd.j2")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        opened, closed = lines[18], lines[19]
        assert opened.startswith("[% if parasitic_blocking_device_cells_type %]"), name
        assert opened.rstrip().endswith("\\"), f"{name}: the guarded line must continue"
        assert closed.startswith("[% endif %]"), name
        assert not closed.rstrip().endswith("\\"), (
            f"{name}: -net_name_space is the last line of extraction_setup and "
            "must not continue into filter_cap"
        )


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


def test_the_two_namespace_lists_never_drift() -> None:
    """``spec._CONTEXT_NAMESPACES`` mirrors ``render._NAMESPACE_ROOTS``.

    The catalog must stay importable without Jinja, so it cannot import the
    renderer to ask -- the same constraint that made ``Stage`` be declared
    twice. Declaring it twice is fine; letting the two drift is not, and a
    template walking a namespace the audit has not heard of gets reported as
    an unclaimed variable rather than as what it is.
    """

    from auto_ext.catalog.spec import _CONTEXT_NAMESPACES
    from auto_ext.core.render import _NAMESPACE_ROOTS

    assert set(_CONTEXT_NAMESPACES) == set(_NAMESPACE_ROOTS)


def test_catalog_and_templates_agree(catalog: Catalog) -> None:
    """Catalog rows and template variables must match, in both directions.

    Forwards: a row claiming a value arrives as ``[[var]]`` must be able to
    point at it. Backwards: a ``[[var]]`` in a template must be claimed by a
    row for that file -- which is what makes "added a variable, forgot the
    catalog row" fail here rather than at the next render.
    """

    audit = audit_template_vars(catalog)
    assert audit.ok, "\n" + audit.describe()


def test_a_row_that_says_it_reaches_a_template_has_to_name_which_one(
    catalog: Catalog,
) -> None:
    """The hole the two-way audit could not see.

    Both existing directions walk ``opt.targets``, which is derived from
    ``lands_in``. A row with NO landing site therefore has an empty loop in
    the forwards direction and contributes nothing to the backwards one, so a
    ``currently: jinja_var`` row whose variable appears in no template at all
    -- the strongest claim the column can make -- was the one shape that
    slipped through both. ``intermediate_dir`` sat in exactly that state:
    catalog says "reaches a template as [[intermediate_dir]]", no template has
    ever referenced it.
    """

    audit = audit_template_vars(catalog)
    assert audit.no_landing_site == [], "\n" + audit.describe()


def test_the_landing_site_audit_catches_a_row_it_should(catalog: Catalog) -> None:
    """The audit above is only worth its line if it can fail.

    A synthetic row, because the shipped catalog no longer has one.
    """

    row = OptionSpec.model_validate(
        _minimal_option(
            key="ghost",
            template_var="ghost",
            currently="jinja_var",
            observed=False,
            source_ref=None,
        )
    )
    broken = catalog.model_copy(update={"options": [*catalog.options, row]})
    audit = audit_template_vars(broken)
    assert audit.no_landing_site == [("ghost", "ghost")]
    assert not audit.ok
    assert "ghost" in audit.describe()


# ---- the gap audit: a row nobody can set ------------------------------------
#
# ``owner`` says who holds the value; ``currently`` says how it reaches the
# file. A row that is recipe- or profile-owned *and* ``hardcoded_literal`` is
# the combination that cannot work: the object has a field, the GUI draws a
# control, and the template writes its own literal regardless. Eighty-four
# rows were in that state before the parameterisation round.
#
# The number below is the whole point of this test. It is not a summary of
# what the catalog happens to say -- it is a budget, and adding a row without
# a template hole spends it.

#: Rows whose value a user owns but which the shipped templates still write as
#: a literal. One entry, with the reason it is still here.
STILL_HARDCODED: dict[str, str] = {
    "lvs_rules_filename_pattern": (
        "calibre_lvs.qci.j2 line 1 is "
        "'*lvsRulesFile: [[calibre_lvs_dir]]/[[calibre_lvs_basename]]"
        ".[[lvs_variant]].qcilvs' -- the '.' and the '.qcilvs' between the "
        "three holes ARE the pattern, so parameterising it means turning "
        "four values into one expression rather than dropping a [[var]] into "
        "a slot. Doing that naively collapses the line to a single hole and "
        "breaks the importer, which needs all three to recover deck dir, "
        "basename and variant from a user's own .qci. The shape that works "
        "keeps all four: "
        "'[[calibre_lvs_dir]]/[[ lvs_rules_filename_pattern.format("
        "basename=calibre_lvs_basename, suffix=lvs_variant) ]]', which needs "
        "render.py to expose pdk.lvs_decks.filename_pattern in the context."
    ),
}


def test_no_owned_row_is_left_hardcoded(catalog: Catalog) -> None:
    """A row the user owns must be a hole in the template, not a literal.

    This is the tripwire for the next person who adds a catalog row: declaring
    it recipe- or profile-owned without parameterising its landing site gives
    the GUI a field that does nothing and the renderer a value it has to
    refuse. Both halves are real behaviour --
    :func:`auto_ext.ui.widgets.option_editor.template_freezes` disables the
    field and ``check_representable`` refuses the stage -- so nothing is
    silently wrong; it is just a promise the tool cannot keep, and the honest
    place to notice is here.

    Ideally this dict is empty. Every entry has to say what specifically
    blocks it, because "not done yet" is how the previous eighty-four got
    there.
    """

    stuck = {
        opt.key
        for opt in catalog.options
        if opt.currently is Currently.HARDCODED_LITERAL
        and opt.owner in (Owner.RECIPE, Owner.PROFILE)
    }
    assert stuck == set(STILL_HARDCODED), (
        "the set of unsettable rows moved. Parameterised one? Delete its entry "
        "from STILL_HARDCODED. Added one? Parameterise its landing site, or "
        "add it here with the reason it cannot be."
    )
    assert len(stuck) == 1
    for key, reason in STILL_HARDCODED.items():
        assert len(reason) > 80, f"{key}: give the actual blocker, not a label"
        assert catalog.option(key).lands_in, f"{key}: a frozen row must say where"


def test_no_recipe_owned_row_is_hardcoded_at_all(catalog: Catalog) -> None:
    """The stricter half, stated separately because it is separately true.

    Nothing on the Recipes form is unsettable. The one row left is
    profile-owned and reaches a user through a PDK profile file, not through
    the form, so a user editing a recipe cannot meet a dead field at all.
    """

    assert [
        opt.key
        for opt in catalog.by_owner(Owner.RECIPE)
        if opt.currently is Currently.HARDCODED_LITERAL
    ] == []


def test_the_rows_that_stay_literal_are_owned_by_something_else(catalog: Catalog) -> None:
    """The rest of the ``hardcoded_literal`` population, and why it is fine.

    ``fixed`` rows are literals by definition -- ``auCdlDefNetlistProc`` is not
    a setting. ``run`` rows are per-run values the runner computes. ``resources``
    rows are the machine's, not the recipe's (DECISIONS.md #21), and they are
    the population ``check_representable`` still has real work to do on: a
    ``ResourceProfile`` naming sixteen turbo cores against a template that
    writes two is refused rather than quietly ignored.
    """

    by_owner: dict[Owner, list[str]] = {}
    for opt in catalog.options:
        if opt.currently is Currently.HARDCODED_LITERAL:
            by_owner.setdefault(opt.owner, []).append(opt.key)

    assert set(by_owner) == {Owner.FIXED, Owner.RUN, Owner.RESOURCES, Owner.PROFILE}
    assert sorted(by_owner[Owner.RESOURCES]) == [
        "lvs_license_wait_time",
        "lvs_num_turbo",
        "lvs_run_hyper",
        "lvs_run_mt",
        "reduction_cpu",
    ]
    assert by_owner[Owner.PROFILE] == list(STILL_HARDCODED)


def test_every_parameterised_row_really_has_its_hole(catalog: Catalog) -> None:
    """The other direction, and the one that makes the flip meaningful.

    Flipping ``currently`` to ``jinja_var`` without putting the placeholder in
    the ``.j2`` would empty ``STILL_HARDCODED`` and change nothing about what
    the tool writes. ``audit_template_vars`` reads the templates, so a row
    claiming a hole it does not have is caught here rather than at a render.
    """

    claimed = [opt for opt in catalog.options if opt.expected_in_templates]
    assert len(claimed) > 90, len(claimed)
    audit = audit_template_vars(catalog)
    assert audit.missing_in_template == [], "\n" + audit.describe()


def test_no_landing_site_points_past_the_end_of_its_template(catalog: Catalog) -> None:
    """A ``line`` hint is a hint, and a hint that is out of range is a lie.

    Cheap, universal, and it catches the class that actually happened: folding
    ``-output_xy`` from seven lines into a loop shortened ``dspf.cmd.j2`` by
    seven, and four hints ended up naming lines the file does not have.
    """

    lengths = {
        spec.id: len(spec.template_path.read_text(encoding="utf-8").splitlines())
        for spec in catalog.targets
    }
    for opt in catalog.options:
        for site in opt.lands_in:
            if site.target is None or site.line is None:
                continue
            assert 1 <= site.line <= lengths[site.target], (
                f"{opt.key}: {site.target.value} line {site.line}, but the "
                f"template has {lengths[site.target]} lines"
            )


def test_every_line_hint_the_parser_can_check_is_right(catalog: Catalog) -> None:
    """The exact version, for the sites where the parser and the catalog agree
    on how to name a line.

    Only the Quantus family qualifies: its ``section`` is the command name the
    parser also keys on, so ``(section, option)`` is the same tuple on both
    sides. The other three targets group their rows into catalog-logical
    sections (``identity``, ``lvs_layout``) that the parser does not know
    about, and inventing a mapping here would be a second source of truth --
    which is the thing this file exists to prevent. Those are covered by the
    range check above and by ``audit_template_vars``.
    """

    checked = 0
    for spec in catalog.targets:
        text = spec.template_path.read_text(encoding="utf-8")
        parsed = {key: raw.line for key, raw in parse_by_syntax(spec.syntax, text).items()}
        for opt in catalog.options:
            for site in opt.lands_in:
                if site.target is not spec.id or site.line is None:
                    continue
                actual = parsed.get((site.section, site.option))
                if actual is None:
                    continue
                assert actual == site.line, (
                    f"{opt.key}: catalog says {spec.id.value} line {site.line}, "
                    f"the template has {site.option} on line {actual}"
                )
                checked += 1
    assert checked > 50, f"only {checked} sites were comparable; the test is thin"


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


def test_the_audit_catches_a_template_variable_nobody_declared(repo_root: Path) -> None:
    # The direction that matters for the future: a catalog that declares none of
    # si.env's variables must report every single one. [[library]], [[cell]],
    # [[lvs_source_view]] and [[output_dir]] have always been there; the rest
    # arrived as catalog rows were parameterised out of their literals, so the
    # expectation is read off the template instead of being a list that goes
    # stale the next time a row moves.
    audit = audit_template_vars(Catalog.model_validate(_catalog_payload()))
    reported = {var for _target, var in audit.missing_in_catalog}
    assert {"cell", "library", "lvs_source_view", "output_dir"} <= reported
    # Two references that are not a bare [[var]]: a SKILL boolean expression and
    # a list joined by a filter. Both still count as a variable this catalog
    # fails to claim.
    assert {"preserve_res", "sim_view_list"} <= reported
    si_env = repo_root / "templates" / "si" / "default.env.j2"
    assert reported == set(scan_placeholders(si_env).jinja_variables)
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
