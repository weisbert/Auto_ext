"""Tests for the catalog-driven template rewriter.

Two halves, and they answer different questions.

**Synthetic templates** (most of this file) pin the *shapes*: one tiny template
per quoting style, per layout, per failure mode, so a broken rewrite says which
rule broke instead of "one of a hundred lines moved". These are the tests that
must stay readable, because the rewriter's whole value is that it refuses
cleanly rather than inserting a placeholder in the wrong place.

**The shipped templates** get invariants only -- never a count of rewrites.
Four other work streams are parameterising those five files as this test runs,
so any assertion of the form "si.env yields 26 rewrites" is a test that fails
on success. What stays true through all of it:

* every landing site is either rewritten or refused with a reason (nothing is
  silently dropped);
* rewriting is idempotent -- a second pass finds its own placeholders and
  reports ``already_parameterised`` rather than nesting one inside another;
* no rewrite ever emits a ``[% %]`` tag on a line of its own, because
  ``trim_blocks`` is off and such a line adds a blank line to the output;
* **applying every rewrite the tool calls ``certain`` and rendering the result
  reproduces the golden baseline byte for byte.** That is the claim the whole
  module rests on, and it is checked against the real files rather than a
  fixture, because a rewriter that is only correct on toy input is worthless.

Rewrites marked ``review`` are excluded from that last one on purpose: "review"
means the tool is telling a person to look, and
``tests/catalog/test_byte_fidelity.py`` is where the person's answer is
recorded once the template actually changes.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePosixPath

import pytest
from jinja2 import TemplateSyntaxError, meta

from auto_ext.catalog import (
    Catalog,
    Confidence,
    Currently,
    Layout,
    OptionSpec,
    OptionType,
    Owner,
    Quoting,
    RenderTargetSpec,
    builtin_catalog,
    default_templates_root,
)
from auto_ext.catalog.parameterise import (
    PARAMETERISE_OWNERS,
    Certainty,
    ParameteriseError,
    Reason,
    Shape,
    audit_pending,
    parameterise,
    pending_options_for,
)
from auto_ext.core.template import make_jinja_env
from auto_ext.model.common import RenderTarget, Stage
from tests.catalog import test_byte_fidelity as fidelity

# ---- synthetic scaffolding ---------------------------------------------------
#
# Real ``RenderTarget`` ids are reused because the enum is closed, but the
# target *specs* below are toys: three lines of made-up syntax, so a test about
# ``skill_bool`` cannot fail because something changed in si.env.


def make_target(
    target: RenderTarget = RenderTarget.SI_ENV,
    *,
    syntax: str = "skill",
    quoting: Quoting = Quoting.DOUBLE,
    layout: Layout = Layout.OWN_LINE,
    indent: int = 0,
    continuation: bool = False,
) -> RenderTargetSpec:
    return RenderTargetSpec(
        id=target,
        stage=Stage.SI,
        template="templates/si/toy.j2",
        template_id="si/toy.j2",
        syntax=syntax,
        quoting=quoting,
        layout=layout,
        indent=indent,
        continuation=continuation,
    )


def make_option(
    key: str,
    *,
    option: str,
    line: int,
    default: object,
    otype: OptionType = OptionType.STR,
    owner: Owner = Owner.RECIPE,
    section: str = "toy",
    target: RenderTarget = RenderTarget.SI_ENV,
    template_var: str | None = None,
    **site_overrides: object,
) -> OptionSpec:
    context_path = (
        f"recipe.toy.{key}" if owner is Owner.RECIPE else f"pdk.{key}"
    )
    site: dict[str, object] = {
        "target": target,
        "section": section,
        "option": option,
        "line": line,
    }
    site.update(site_overrides)
    return OptionSpec(
        key=key,
        template_var=template_var or key,
        context_path=context_path,
        owner=owner,
        type=otype,
        default=default,
        choices_confidence=Confidence.CERTAIN,
        currently=Currently.HARDCODED_LITERAL,
        observed=False,
        lands_in=[site],
        why="a synthetic row that exists only inside this test module",
    )


def make_catalog(spec: RenderTargetSpec, *options: OptionSpec) -> Catalog:
    return Catalog(
        schema_version=1,
        catalog_version="test",
        targets=[spec],
        options=list(options),
    )


def rewrite_one(
    text: str, spec: RenderTargetSpec, option: OptionSpec
) -> tuple[str, object]:
    """Run the rewriter over one synthetic row and return ``(text, outcome)``.

    ``outcome`` is the single Rewrite or Refusal, so a test can assert on it
    without indexing into two tuples every time.
    """

    catalog = make_catalog(spec, option)
    new_text, report = parameterise(text, spec.id, [option], catalog=catalog)
    outcomes = list(report.rewrites) + list(report.refusals)
    assert len(outcomes) == 1, outcomes
    return new_text, outcomes[0]


# ---- quoting styles ----------------------------------------------------------


def test_a_double_quoted_value_keeps_its_quotes_outside_the_placeholder() -> None:
    spec = make_target(quoting=Quoting.DOUBLE)
    opt = make_option("check_scale", option="checkScale", line=1, default="meter")
    text, rewrite = rewrite_one('checkScale = "meter"\n', spec, opt)
    assert text == 'checkScale = "[[check_scale]]"\n'
    assert rewrite.shape is Shape.SCALAR
    assert rewrite.certainty is Certainty.CERTAIN


def test_a_bare_value_gets_a_bare_placeholder() -> None:
    spec = make_target(quoting=Quoting.BARE)
    opt = make_option(
        "short_res", option="shortRES", line=1, default=2000.0, otype=OptionType.FLOAT
    )
    text, rewrite = rewrite_one("shortRES = 2000.0\n", spec, opt)
    assert text == "shortRES = [[short_res]]\n"
    assert rewrite.literal == "2000.0"


def test_a_skill_boolean_becomes_the_quote_t_quote_nil_conditional() -> None:
    spec = make_target(quoting=Quoting.SKILL_BOOL)
    opt = make_option(
        "preserve_res",
        option="preserveRES",
        line=1,
        default=True,
        otype=OptionType.BOOL,
    )
    text, rewrite = rewrite_one("preserveRES = 't\n", spec, opt)
    assert text == """preserveRES = [[ "'t" if preserve_res else "'nil" ]]\n"""
    assert rewrite.shape is Shape.BOOL_LITERAL
    assert rewrite.certainty is Certainty.CERTAIN


def test_a_skill_list_becomes_a_join_guarded_against_the_empty_list() -> None:
    spec = make_target(quoting=Quoting.SKILL_LIST)
    opt = make_option(
        "view_list",
        option="simViewList",
        line=1,
        default=["auCdl", "schematic"],
        otype=OptionType.LIST,
    )
    text, rewrite = rewrite_one("""simViewList = '("auCdl" "schematic")\n""", spec, opt)
    assert text == (
        """simViewList = '([% if view_list %]"[[ view_list | join('" "') ]]"[% endif %])\n"""
    )
    assert rewrite.shape is Shape.LIST_JOINED
    # The guard is inline, so it costs no newline even with trim_blocks off.
    assert text.count("\n") == 1


def test_an_xml_attribute_value_is_replaced_inside_the_quotes() -> None:
    spec = make_target(
        RenderTarget.JIVARO_XML, syntax="xml", quoting=Quoting.XML_ATTR, layout=Layout.PACKED
    )
    opt = make_option(
        "log_level",
        option="logVerboseLevel",
        line=1,
        default="trace",
        target=RenderTarget.JIVARO_XML,
    )
    text, _ = rewrite_one('<logVerboseLevel value="trace"/>\n', spec, opt)
    assert text == '<logVerboseLevel value="[[log_level]]"/>\n'


def test_an_xml_boolean_uses_single_quotes_so_the_attribute_stays_readable() -> None:
    spec = make_target(
        RenderTarget.JIVARO_XML, syntax="xml", quoting=Quoting.XML_ATTR, layout=Layout.PACKED
    )
    opt = make_option(
        "reduce_floating",
        option="reduceFloatingNets",
        line=1,
        default=False,
        otype=OptionType.BOOL,
        target=RenderTarget.JIVARO_XML,
    )
    text, _ = rewrite_one('<reduceFloatingNets value="false"/>\n', spec, opt)
    assert text == (
        "<reduceFloatingNets value=\"[[ 'true' if reduce_floating else 'false' ]]\"/>\n"
    )


def test_a_quoted_quantus_boolean_leaves_its_quotes_on_the_line() -> None:
    """``-include_cap_model "false"`` is a quoted *string*, so the quotes belong
    to the argument and only the word goes inside the expression."""

    spec = make_target(
        RenderTarget.QUANTUS_EXT,
        syntax="quantus_cmd",
        quoting=Quoting.DOUBLE,
        layout=Layout.INLINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "include_cap_model",
        option="-include_cap_model",
        line=2,
        default=False,
        otype=OptionType.BOOL,
        section="output_db",
        target=RenderTarget.QUANTUS_EXT,
    )
    source = 'output_db -type dspf \\\n              -include_cap_model "false" \\\n'
    text, _ = rewrite_one(source, spec, opt)
    assert text == (
        "output_db -type dspf \\\n"
        "              -include_cap_model \"[[ 'true' if include_cap_model else 'false' ]]\" \\\n"
    )


def test_a_bare_quantus_boolean_keeps_its_lowercase_words() -> None:
    spec = make_target(
        RenderTarget.QUANTUS_EXT,
        syntax="quantus_cmd",
        quoting=Quoting.BARE,
        layout=Layout.INLINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "exclude_self_cap",
        option="-exclude_self_cap",
        line=2,
        default=True,
        otype=OptionType.BOOL,
        section="filter_cap",
        target=RenderTarget.QUANTUS_EXT,
    )
    source = "filter_cap \\\n              -exclude_self_cap true \\\n"
    text, _ = rewrite_one(source, spec, opt)
    assert text == (
        "filter_cap \\\n"
        '              -exclude_self_cap [[ "true" if exclude_self_cap else "false" ]] \\\n'
    )


def test_a_calibre_boolean_keeps_the_runsets_one_and_zero() -> None:
    spec = make_target(
        RenderTarget.LVS_QCI, syntax="calibre_runset", quoting=Quoting.BARE
    )
    opt = make_option(
        "svdb_cci",
        option="*lvsSVDBcci",
        line=1,
        default=True,
        otype=OptionType.BOOL,
        target=RenderTarget.LVS_QCI,
    )
    text, _ = rewrite_one("*lvsSVDBcci: 1\n", spec, opt)
    assert text == '*lvsSVDBcci: [[ "1" if svdb_cci else "0" ]]\n'


def test_a_non_canonical_boolean_spelling_is_reproduced_and_flagged() -> None:
    """``templates/si/default.env.j2:6`` writes a bare ``nil``. Normalising it
    to ``'nil`` would be tidier and would change the rendered file, so the
    rewrite reproduces what is there and says so."""

    spec = make_target(quoting=Quoting.SKILL_BOOL)
    opt = make_option(
        "renetlist_all",
        option="simReNetlistAll",
        line=1,
        default=False,
        otype=OptionType.BOOL,
    )
    text, rewrite = rewrite_one("simReNetlistAll = nil\n", spec, opt)
    assert text == """simReNetlistAll = [[ "'t" if renetlist_all else "nil" ]]\n"""
    assert rewrite.certainty is Certainty.REVIEW
    assert "canonical" in (rewrite.note or "")


# ---- layouts -----------------------------------------------------------------


def test_a_value_on_the_next_line_is_replaced_there_not_on_the_option_line() -> None:
    spec = make_target(
        RenderTarget.QUANTUS_EXT,
        syntax="quantus_cmd",
        quoting=Quoting.DOUBLE,
        layout=Layout.VALUE_ON_NEXT_LINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "technology_corner",
        option="-technology_corner",
        line=2,
        default="TYPICAL",
        owner=Owner.PROFILE,
        section="process_technology",
        target=RenderTarget.QUANTUS_EXT,
    )
    source = (
        "process_technology \\\n"
        "              -technology_corner \\\n"
        '              "TYPICAL" \\\n'
        '              -technology_name "HN001"\n'
    )
    text, rewrite = rewrite_one(source, spec, opt)
    assert text == (
        "process_technology \\\n"
        "              -technology_corner \\\n"
        '              "[[technology_corner]]" \\\n'
        '              -technology_name "HN001"\n'
    )
    assert rewrite.shape is Shape.SCALAR_NEXT_LINE
    assert rewrite.line == 3  # the value line, not the option line


def test_one_value_per_line_becomes_a_hugging_for_loop() -> None:
    """The shape the whole "trim_blocks is off" rule exists for: eight value
    lines collapse into two, and ``[% endfor %]`` shares the line that follows
    them so the render does not grow a blank line."""

    spec = make_target(
        RenderTarget.QUANTUS_DSPF,
        syntax="quantus_cmd",
        quoting=Quoting.DOUBLE,
        layout=Layout.VALUE_PER_LINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "output_xy",
        option="-output_xy",
        line=2,
        default=["DIODE", "MOS"],
        otype=OptionType.LIST,
        section="output_db",
        target=RenderTarget.QUANTUS_DSPF,
    )
    source = (
        "output_db -type dspf \\\n"
        "              -output_xy \\\n"
        '              "DIODE" \\\n'
        '              "MOS" \\\n'
        '              -sub_node_char "#"\n'
    )
    text, rewrite = rewrite_one(source, spec, opt)
    assert text == (
        "output_db -type dspf \\\n"
        "              -output_xy \\\n"
        '[% for _item in output_xy %]              "[[ _item ]]" \\\n'
        '[% endfor %]              -sub_node_char "#"\n'
    )
    assert rewrite.shape is Shape.LIST_PER_LINE
    assert rewrite.certainty is Certainty.REVIEW


def test_the_generated_loop_renders_back_to_the_lines_it_replaced() -> None:
    """The point of the hugging form, proved by rendering rather than argued."""

    spec = make_target(
        RenderTarget.QUANTUS_DSPF,
        syntax="quantus_cmd",
        quoting=Quoting.DOUBLE,
        layout=Layout.VALUE_PER_LINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "output_xy",
        option="-output_xy",
        line=1,
        default=["DIODE", "MOS"],
        otype=OptionType.LIST,
        section="output_db",
        target=RenderTarget.QUANTUS_DSPF,
    )
    source = (
        "              -output_xy \\\n"
        '              "DIODE" \\\n'
        '              "MOS" \\\n'
        '              -sub_node_char "#"\n'
    )
    text, _ = rewrite_one(source, spec, opt)
    rendered = make_jinja_env().from_string(text).render(output_xy=["DIODE", "MOS"])
    assert rendered == source


def test_an_optional_line_is_wrapped_in_the_hugging_if_endif_form() -> None:
    """``templates/calibre/calibre_lvs.qci.j2:31-32`` is the reference, and the
    only correct shape: a tag on a line of its own emits a blank line."""

    spec = make_target(
        RenderTarget.LVS_QCI, syntax="calibre_runset", quoting=Quoting.BARE
    )
    opt = make_option(
        "connect_by_name",
        option="*cmnVConnectNamesState",
        line=1,
        default=True,
        otype=OptionType.BOOL,
        target=RenderTarget.LVS_QCI,
        optional=True,
    )
    source = "*cmnVConnectNamesState: ALL\n*cmnSpecifyLicenseWaitTime: 1\n"
    text, rewrite = rewrite_one(source, spec, opt)
    assert text == (
        "[% if connect_by_name %]*cmnVConnectNamesState: ALL\n"
        "[% endif %]*cmnSpecifyLicenseWaitTime: 1\n"
    )
    assert rewrite.shape is Shape.BOOL_OPTIONAL_LINE

    jenv = make_jinja_env()
    assert jenv.from_string(text).render(connect_by_name=True) == source
    # False must remove the line without leaving a blank one behind.
    assert (
        jenv.from_string(text).render(connect_by_name=False)
        == "*cmnSpecifyLicenseWaitTime: 1\n"
    )


def test_a_fragment_of_a_longer_slot_replaces_only_the_fragment() -> None:
    spec = make_target(
        RenderTarget.QUANTUS_EXT,
        syntax="quantus_cmd",
        quoting=Quoting.DOUBLE,
        layout=Layout.INLINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "preserve_cell_list_name",
        option="-parasitic_blocking_device_cells_file",
        line=1,
        default="preserveCellList.txt",
        owner=Owner.PROFILE,
        section="extraction_setup",
        target=RenderTarget.QUANTUS_EXT,
    )
    source = (
        '              -parasitic_blocking_device_cells_file '
        '"[[qrc_deck_dir]]/preserveCellList.txt" \\\n'
    )
    text, rewrite = rewrite_one(source, spec, opt)
    assert text == (
        "              -parasitic_blocking_device_cells_file "
        '"[[qrc_deck_dir]]/[[preserve_cell_list_name]]" \\\n'
    )
    assert rewrite.shape is Shape.FRAGMENT
    assert rewrite.certainty is Certainty.REVIEW
    # The note has to name the trap, because the tool cannot check it: the
    # placeholder is only right if the context binds the file name and not the
    # whole path.
    assert "context_path" in (rewrite.note or "")


# ---- refusals ----------------------------------------------------------------


def test_a_directive_that_is_nowhere_in_the_file_is_refused() -> None:
    spec = make_target()
    opt = make_option("nope", option="simNothingHere", line=1, default="x")
    text, refusal = rewrite_one("simSimulator = \"auCdl\"\n", spec, opt)
    assert text == 'simSimulator = "auCdl"\n'
    assert refusal.reason is Reason.ANCHOR_NOT_FOUND
    assert "simNothingHere" in refusal.detail


def test_a_directive_on_several_lines_is_refused_rather_than_guessed() -> None:
    """Inserting a placeholder in the wrong one of two candidates is worse than
    leaving the row hardcoded, so ambiguity is a hard stop."""

    spec = make_target(
        RenderTarget.QUANTUS_EXT,
        syntax="quantus_cmd",
        quoting=Quoting.BARE,
        layout=Layout.INLINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "metal_fill_type",
        option="-type",
        line=99,  # past the end: forces the search path
        default="virtual",
        section="metal_fill",
        target=RenderTarget.QUANTUS_EXT,
    )
    source = "metal_fill \\\n              -type virtual \\\n              -type extra\n"
    text, refusal = rewrite_one(source, spec, opt)
    assert text == source
    assert refusal.reason is Reason.ANCHOR_AMBIGUOUS
    assert "[2, 3]" in refusal.detail


def test_a_slot_holding_something_else_is_refused_and_shows_both_values() -> None:
    spec = make_target(quoting=Quoting.DOUBLE)
    opt = make_option("check_scale", option="checkScale", line=1, default="meter")
    text, refusal = rewrite_one('checkScale = "micron"\n', spec, opt)
    assert text == 'checkScale = "micron"\n'
    assert refusal.reason is Reason.LITERAL_NOT_FOUND
    assert '"micron"' in refusal.detail and '"meter"' in refusal.detail


def test_a_row_with_no_default_is_refused_before_anything_is_searched() -> None:
    spec = make_target(quoting=Quoting.DOUBLE)
    opt = make_option("no_default", option="checkScale", line=1, default=None)
    _, refusal = rewrite_one('checkScale = "meter"\n', spec, opt)
    assert refusal.reason is Reason.NO_DEFAULT


def test_a_structural_row_is_refused() -> None:
    spec = make_target(quoting=Quoting.NONE)
    opt = make_option(
        "header",
        option="checkScale",
        line=1,
        default="meter",
        otype=OptionType.STRUCTURAL,
    )
    _, refusal = rewrite_one('checkScale = "meter"\n', spec, opt)
    assert refusal.reason is Reason.UNSUPPORTED_SHAPE


def test_an_optional_line_at_the_end_of_the_file_is_refused() -> None:
    """There is no following line for ``[% endif %]`` to hug, and putting the
    tag on its own line is exactly the mistake this module exists to avoid."""

    spec = make_target(
        RenderTarget.LVS_QCI, syntax="calibre_runset", quoting=Quoting.BARE
    )
    opt = make_option(
        "connect_by_name",
        option="*cmnVConnectNamesState",
        line=1,
        default=True,
        otype=OptionType.BOOL,
        target=RenderTarget.LVS_QCI,
        optional=True,
    )
    _, refusal = rewrite_one("*cmnVConnectNamesState: ALL", spec, opt)
    assert refusal.reason is Reason.UNSUPPORTED_SHAPE
    assert "endif" in refusal.detail


def test_value_lines_with_mismatched_shapes_are_refused() -> None:
    spec = make_target(
        RenderTarget.QUANTUS_DSPF,
        syntax="quantus_cmd",
        quoting=Quoting.DOUBLE,
        layout=Layout.VALUE_PER_LINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "output_xy",
        option="-output_xy",
        line=1,
        default=["DIODE", "MOS"],
        otype=OptionType.LIST,
        section="output_db",
        target=RenderTarget.QUANTUS_DSPF,
    )
    source = (
        "              -output_xy \\\n"
        '              "DIODE" \\\n'
        '    "MOS" \\\n'  # different indent: one loop body cannot make both
        '              -sub_node_char "#"\n'
    )
    _, refusal = rewrite_one(source, spec, opt)
    assert refusal.reason is Reason.UNSUPPORTED_SHAPE
    assert "indent" in refusal.detail


def test_a_line_number_that_drifted_is_found_by_search_and_reported() -> None:
    spec = make_target(quoting=Quoting.DOUBLE)
    opt = make_option("check_scale", option="checkScale", line=1, default="meter")
    source = "simSimulator = \"auCdl\"\ncheckScale = \"meter\"\n"
    text, rewrite = rewrite_one(source, spec, opt)
    assert text == 'simSimulator = "auCdl"\ncheckScale = "[[check_scale]]"\n'
    assert rewrite.line == 2
    assert rewrite.catalog_line == 1
    assert rewrite.certainty is Certainty.REVIEW
    assert "update lands_in" in (rewrite.note or "")


def test_a_value_that_cannot_be_spelled_as_a_jinja_literal_raises() -> None:
    """The one condition the module treats as a programming error rather than a
    refusal: a boolean token carrying both quote characters."""

    from auto_ext.catalog.parameterise import _quote

    with pytest.raises(ParameteriseError):
        _quote("""a"b'c""", inside_double_quotes=False)


# ---- idempotence -------------------------------------------------------------


def test_a_second_pass_finds_its_own_work_and_changes_nothing() -> None:
    spec = make_target(quoting=Quoting.DOUBLE)
    opt = make_option("check_scale", option="checkScale", line=1, default="meter")
    once, _ = rewrite_one('checkScale = "meter"\n', spec, opt)
    twice, refusal = rewrite_one(once, spec, opt)
    assert twice == once
    assert refusal.reason is Reason.ALREADY_PARAMETERISED


def test_a_second_pass_over_a_boolean_does_not_nest_the_conditional() -> None:
    spec = make_target(quoting=Quoting.SKILL_BOOL)
    opt = make_option(
        "preserve_res",
        option="preserveRES",
        line=1,
        default=True,
        otype=OptionType.BOOL,
    )
    once, _ = rewrite_one("preserveRES = 't\n", spec, opt)
    twice, refusal = rewrite_one(once, spec, opt)
    assert twice == once
    assert refusal.reason is Reason.ALREADY_PARAMETERISED


def test_a_second_pass_over_a_loop_does_not_nest_it() -> None:
    spec = make_target(
        RenderTarget.QUANTUS_DSPF,
        syntax="quantus_cmd",
        quoting=Quoting.DOUBLE,
        layout=Layout.VALUE_PER_LINE,
        indent=14,
        continuation=True,
    )
    opt = make_option(
        "output_xy",
        option="-output_xy",
        line=1,
        default=["DIODE", "MOS"],
        otype=OptionType.LIST,
        section="output_db",
        target=RenderTarget.QUANTUS_DSPF,
    )
    source = (
        "              -output_xy \\\n"
        '              "DIODE" \\\n'
        '              "MOS" \\\n'
        '              -sub_node_char "#"\n'
    )
    once, _ = rewrite_one(source, spec, opt)
    twice, refusal = rewrite_one(once, spec, opt)
    assert twice == once
    assert refusal.reason is Reason.ALREADY_PARAMETERISED


def test_two_rows_on_one_line_are_both_applied() -> None:
    """``calibre_lvs.qci.j2:26`` carries two catalog rows, so the rewriter has
    to see its own previous edit rather than the original line."""

    spec = make_target(
        RenderTarget.LVS_QCI, syntax="calibre_runset", quoting=Quoting.BARE
    )
    first = make_option(
        "alpha", option="*lvsPostTriggers", line=1, default="one",
        target=RenderTarget.LVS_QCI,
    )
    second = make_option(
        "beta", option="*lvsPostTriggers", line=1, default="two",
        target=RenderTarget.LVS_QCI,
    )
    catalog = make_catalog(spec, first, second)
    text, report = parameterise(
        "*lvsPostTriggers: {one} {two}\n", spec.id, [first, second], catalog=catalog
    )
    assert text == "*lvsPostTriggers: {[[alpha]]} {[[beta]]}\n"
    assert len(report.rewrites) == 2


# ---- the audit ---------------------------------------------------------------


def test_the_audit_lists_exactly_the_owned_and_still_hardcoded_rows() -> None:
    """No count is pinned: four work streams are closing these rows as this
    test runs, and the only assertion that survives that is the definition."""

    catalog = builtin_catalog()
    audit = audit_pending(catalog)
    expected = {
        opt.key
        for opt in catalog.options
        if opt.owner in PARAMETERISE_OWNERS
        and opt.currently is Currently.HARDCODED_LITERAL
    }
    assert {opt.key for opt in audit.options} == expected
    assert audit.total == len(expected)


def test_the_audit_groups_a_row_under_every_file_it_lands_in() -> None:
    catalog = builtin_catalog()
    audit = audit_pending(catalog)
    for target, rows in audit.by_target.items():
        for opt in rows:
            assert target in opt.targets
    for opt in audit.options:
        for site in opt.lands_in:
            if site.target is None:
                continue
            assert opt.key in {row.key for row in audit.by_target[site.target]}


def test_the_audit_counts_sites_not_rows() -> None:
    """A row landing in both Quantus files is one row and two pieces of work."""

    audit = audit_pending()
    assert audit.site_count >= audit.total


def test_pending_options_for_agrees_with_the_audit() -> None:
    audit = audit_pending()
    for target in RenderTarget:
        assert pending_options_for(target) == audit.by_target.get(target, ())


# ---- the shipped templates ---------------------------------------------------


def shipped_source(target: RenderTarget) -> str:
    spec = builtin_catalog().target(target)
    return spec.template_path.read_text(encoding="utf-8")


ALL_TARGETS = list(RenderTarget)
TARGET_IDS = [t.value for t in ALL_TARGETS]


@pytest.mark.parametrize("target", ALL_TARGETS, ids=TARGET_IDS)
def test_every_landing_site_is_either_rewritten_or_refused(
    target: RenderTarget,
) -> None:
    """Nothing falls between the two lists. The report is the work list, so a
    site that appears in neither is a site nobody knows is unfinished."""

    catalog = builtin_catalog()
    rows = pending_options_for(target, catalog)
    sites = sum(
        1 for opt in rows for site in opt.lands_in if site.target == target
    )
    _, report = parameterise(shipped_source(target), target, catalog=catalog)
    assert len(report.rewrites) + len(report.refusals) == sites


@pytest.mark.parametrize("target", ALL_TARGETS, ids=TARGET_IDS)
def test_rewriting_a_shipped_template_is_idempotent(target: RenderTarget) -> None:
    catalog = builtin_catalog()
    once, _ = parameterise(shipped_source(target), target, catalog=catalog)
    twice, second = parameterise(once, target, catalog=catalog)
    assert twice == once
    assert not second.rewrites


@pytest.mark.parametrize("target", ALL_TARGETS, ids=TARGET_IDS)
def test_a_rewritten_template_is_still_valid_jinja(target: RenderTarget) -> None:
    text, _ = parameterise(shipped_source(target), target)
    try:
        make_jinja_env().parse(text)
    except TemplateSyntaxError as exc:  # pragma: no cover - failure path
        pytest.fail(f"{target.value} no longer parses after rewriting: {exc}")


#: A line that is nothing but a Jinja statement. With ``trim_blocks`` off, the
#: newline after the tag survives into the output.
_LONE_TAG = re.compile(r"^\s*\[%.*%\]\s*$")


@pytest.mark.parametrize("target", ALL_TARGETS, ids=TARGET_IDS)
def test_no_rewrite_puts_a_statement_tag_on_a_line_of_its_own(
    target: RenderTarget,
) -> None:
    """The rule that costs a blank line every time it is broken."""

    text, _ = parameterise(shipped_source(target), target)
    offenders = [
        (i + 1, line) for i, line in enumerate(text.split("\n")) if _LONE_TAG.match(line)
    ]
    assert not offenders, (
        f"{target.value} would emit a blank line at {offenders}: trim_blocks is "
        "off, so every tag must share its line with the text it governs"
    )


def _jinja_names(source: str) -> set[str]:
    """Undeclared Jinja names in ``source``; loop variables are excluded, being
    declared by the ``[% for %]`` that binds them."""

    return set(meta.find_undeclared_variables(make_jinja_env().parse(source)))


@pytest.mark.parametrize("target", ALL_TARGETS, ids=TARGET_IDS)
def test_every_placeholder_a_rewrite_introduces_is_a_catalog_variable(
    target: RenderTarget,
) -> None:
    """A rewrite must never invent a name the render context does not bind.

    The loop variable is the one exception, and it is bound by the ``[% for %]``
    the same rewrite emits, which is why Jinja does not report it as undeclared.
    """

    catalog = builtin_catalog()
    source = shipped_source(target)
    text, report = parameterise(source, target, catalog=catalog)
    if not report.changed:
        pytest.skip(f"{target.value} has nothing left to rewrite")

    introduced = _jinja_names(text) - _jinja_names(source)
    for name in introduced:
        row = catalog.by_template_var(name)
        assert row is not None, f"{target.value} would reference unknown [[{name}]]"
        assert target in row.targets, (
            f"{target.value} would reference [[{name}]], whose catalog row "
            f"{row.key!r} does not claim this file"
        )


def test_applying_every_certain_rewrite_still_renders_the_golden_baseline(
    tmp_path: Path,
) -> None:
    """The claim the module rests on, checked against the real five files.

    Only the rewrites the tool calls :attr:`Certainty.CERTAIN` are applied --
    "review" is the tool saying a person has to decide, and this test is not
    that person. What it proves is that where the tool claims certainty it is
    right: the placeholder landed in the slot the literal occupied, and the
    value now flowing through it is the value the template used to write.
    """

    catalog = builtin_catalog()
    root = tmp_path / "templates"
    shutil.copytree(default_templates_root(), root)

    applied = 0
    for spec in catalog.targets:
        path = root / PurePosixPath(spec.template).relative_to("templates")
        source = path.read_text(encoding="utf-8")
        _, report = parameterise(source, spec.id, catalog=catalog)
        certain = {
            r.option_key for r in report.rewrites if r.certainty is Certainty.CERTAIN
        }
        if not certain:
            continue
        rows = [
            opt for opt in pending_options_for(spec.id, catalog) if opt.key in certain
        ]
        text, second = parameterise(source, spec.id, rows, catalog=catalog)
        applied += len(second.rewrites)
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    if not applied:
        pytest.skip("every certain rewrite has already been applied to the templates")

    for target, text in fidelity.render_all(templates_root=root).items():
        expected = fidelity.read_golden(target)
        assert text == expected, (
            f"a rewrite the tool called certain moved {target.value}:\n"
            f"{fidelity.diff(expected, text, target)}"
        )
