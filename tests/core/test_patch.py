"""Tests for :mod:`auto_ext.core.patch` -- the patch escape hatch.

Covers every scenario in ``docs/refactor/02-patch.md`` section 5 (T1..T15)
plus the mask-grammar, model-validation, unified-diff and reporting surfaces.

The base fixture :data:`TPL` is a frozen snapshot of
``templates/quantus/ext.cmd.j2`` with one deliberate change:
``-technology_library_file "$env(SETUP_ROOT)/assura_tech.lib"`` becomes
``[[qrc_tech_lib]]``. Rationale: T3 (PDK-profile swap) needs that path to be a
Jinja variable, which is what the catalog refactor turns it into anyway. The
snapshot is frozen rather than read from disk so an unrelated template edit
cannot silently change what these tests assert.

Rendering here calls Jinja directly instead of
:func:`auto_ext.core.template.render_template`: the patch engine operates on
post-``substitute_env`` source, and going through the file-based renderer
would drag in the cwd-sensitive ``resolve_template_path`` for no benefit.
"""

from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from io import StringIO
from typing import Any

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

from auto_ext.core.patch import (
    DEFAULT_CTX_LINES,
    SIMILARITY_MIN_MARGIN,
    SIMILARITY_MIN_RATIO,
    BaseFingerprint,
    Binding,
    Fuzz,
    FuzzyPolicy,
    PatchApplyReport,
    PatchConflictError,
    PatchHunk,
    PatchStatus,
    Stage,
    TemplatePatch,
    _line_pattern,
    _remask_edit,
    apply_patch,
    build_stage_report,
    capture_patch,
    condition_vars,
    escape_literal,
    import_udiff,
    mask_escape,
    mask_values,
    masked_context,
    remask_text,
    render_hunk_as_udiff,
    render_masked,
    sha256_text,
    slots_in,
    unmask,
)
from auto_ext.core.template import _make_jinja_env

# --- fixtures ---------------------------------------------------------------

TPL = r"""#--------------------------------------------------------------------------------------

#OPTION COMMAND FILE created by Cadence Extraction Quantus UI Version 18.21-s340

#--------------------------------------------------------------------------------------

capacitance \
              -decoupling_factor 1.0 \
              -ground_net "[[ground_net]]"
extract \
              -selection "all" \
              -type "rc_coupled"
extraction_setup \
              -array_vias_spacing auto \
              -max_fracture_length infinite \
              -max_fracture_length_unit "MICRONS" \
              -max_via_array_size "auto" \
              -parasitic_blocking_device_cells_file "[[qrc_deck_dir]]/preserveCellList.txt" \
              -net_name_space "SCHEMATIC"
filter_cap \
              -exclude_self_cap true \
              -exclude_floating_nets true \
              -exclude_floating_nets_limit [[exclude_floating_nets_limit]]
filter_coupling_cap \
              -coupling_cap_threshold_absolute [[coupling_cap_threshold_absolute]] \
              -coupling_cap_threshold_relative [[coupling_cap_threshold_relative]]
filter_res \
              -merge_parallel_res true \
              -min_res [[min_res]] \
              -remove_dangling_res true
input_db -type calibre \
              -design_cell_name "[[cell]] [[lvs_layout_view]] [[library]]" \
              -device_property_value 7 \
              -run_name "Design" \
              -directory_name "[[output_dir]]/query_output" \
              -format "DFII" \
              -instance_property_value 6 \
              -layer_map_file "[[output_dir]]/query_output/Design.gds.map" \
              -net_property_value 5 \
              -device_properties_file "[[output_dir]]/query_output/Design.props"
output_db -type extracted_view \
              -cap_component "pcapacitor" \
              -cap_property_name "c" \
              -enable_cellview_check false \
              -device_finger_delimiter "@" \
              -cdl_out_map_directory \
              "[[output_dir]]/" \
              -include_cap_model "false" \
              -include_parasitic_cap_model "false" \
              -include_res_model "false" \
              -include_parasitic_res_model "comment" \
              -res_component "presistor" \
              -res_property_name "r" \
              -view_name "[[out_file]]"
output_setup \
              -directory_name "[[output_dir]]/query_output" \
              -temporary_directory_name "Design"
process_technology \
              -technology_corner \
              "TYPICAL" \
              -technology_library_file "[[qrc_tech_lib]]" \
              -technology_name "[[tech_name]]" \
              -temperature \
              [[temperature]]
"""

#: Snapshot of ``templates/calibre/calibre_lvs.qci.j2`` lines 28..44 -- the
#: ``[% if connect_by_name %]`` guard (written flush against the next line
#: because ``trim_blocks`` is off) and the ``*cmnLSFSlaveTbl`` /
#: ``*cmnGridSlaveTbl`` twins that T12-B needs.
QCI = r"""*cmnWarnLayoutOverwrite: 0
*cmnWarnSourceOverwrite: 0
*cmnShowOptions: 1
[% if connect_by_name %]*cmnVConnectNamesState: ALL
[% endif %]*cmnSpecifyLicenseWaitTime: 1
*cmnLicenseWaitTime: 10
*cmnReleaseLicense: 1
*cmnNumTurbo: 2
*cmnRunMT: 1
*cmnRunHyper: 1
*cmnTemplate_RN: [[output_dir]]
*cmnLSFSlaveTbl: {use 1} {totalCpus 1} {minCpus 1} {architecture {{}}} {minMemory {{}}} {resourceOptions {{}}} {submitOptions {{}}}
*cmnGridSlaveTbl: {use 1} {totalCpus 1} {minCpus 1} {architecture {{}}} {minMemory {{}}} {resourceOptions {{}}} {submitOptions {{}}}
*cmnFDILayoutLibrary: [[library]]
*cmnFDILayoutView: [[lvs_layout_view]]
*cmnFDIDEFLayoutPath: [[cell]].def
"""

#: Two byte-identical ``output_db`` blocks, as a recipe that asks for both an
#: extracted view and a DSPF emits. Used by T7 (occurrence disambiguation).
TPL_DUP = r"""extraction_setup \
              -net_name_space "SCHEMATIC"
output_db \
              -cap_component "pcapacitor" \
              -cap_property_name "c" \
              -include_cap_model "false" \
              -res_component "presistor" \
              -view_name "[[out_file]]"
output_db \
              -cap_component "pcapacitor" \
              -cap_property_name "c" \
              -include_cap_model "false" \
              -res_component "presistor" \
              -view_name "[[out_file]]"
output_setup \
              -directory_name "[[output_dir]]"
"""


def profile_a(cell: str = "pll_top") -> dict[str, Any]:
    """Render context for the tsmc22ull profile."""
    return {
        "cell": cell,
        "library": "ANALOG_LIB",
        "lvs_layout_view": "layout",
        "out_file": f"{cell}_ext",
        "output_dir": f"/work/{cell}",
        "ground_net": "VSS",
        "qrc_deck_dir": "/pdk/tsmc22ull/qrc",
        "qrc_tech_lib": "/pdk/tsmc22ull/setup/assura_tech.lib",
        "tech_name": "tsmc22ull_1p10m",
        "temperature": 25,
        "exclude_floating_nets_limit": 5000,
        "coupling_cap_threshold_absolute": "0.01f",
        "coupling_cap_threshold_relative": "0.001",
        "min_res": "0.001",
    }


def profile_b(cell: str = "pll_top") -> dict[str, Any]:
    """Same recipe, different PDK profile (T3)."""
    ctx = profile_a(cell)
    ctx.update(
        qrc_deck_dir="/pdk/tsmc16ffc/qrc",
        qrc_tech_lib="/pdk/tsmc16ffc/setup/assura_tech.lib",
        tech_name="tsmc16ffc_1p13m",
    )
    return ctx


def render(source: str, context: dict[str, Any]) -> str:
    return _make_jinja_env().from_string(source).render(**context)


def lines_of(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def index_of(text: str, needle: str) -> int:
    """0-indexed line number of the first line containing ``needle``."""
    for i, line in enumerate(lines_of(text)):
        if needle in line:
            return i
    raise AssertionError(f"{needle!r} not found")


def insert_at(text: str, at: int, block: str) -> str:
    out = lines_of(text)
    out[at:at] = lines_of(block)
    return "".join(out)


def reindent(text: str, old: int = 14, new: int = 8) -> str:
    out = []
    for line in lines_of(text):
        if line.startswith(" " * old):
            line = " " * new + line[old:]
        out.append(line)
    return "".join(out)


def capture(
    source: str,
    context: dict[str, Any],
    edited: str,
    *,
    mask: bool = True,
    ctx_lines: int = DEFAULT_CTX_LINES,
    keep_literal: tuple[str, ...] = (),
    existing: TemplatePatch | None = None,
    intents: dict[int, str] | None = None,
) -> TemplatePatch:
    """capture_patch with the boilerplate filled in.

    ``mask=False`` disables masking entirely (the masked render *is* the real
    render). That is the counter-factual T1 needs: it reproduces exactly what
    a unified-diff-style store would do.
    """
    real = render(source, context)
    return capture_patch(
        template_source=source,
        template_sha256=sha256_text(source),
        stage="quantus",
        template_id="quantus/ext.cmd",
        profile_id="tsmc22ull",
        catalog_version="2026.08",
        base_real=real,
        base_masked=render_masked(source, context) if mask else real,
        edited_real=edited,
        values=mask_values(source, context) if mask else {},
        intents=intents,
        keep_literal=keep_literal,
        ctx_lines=ctx_lines,
        existing=existing,
    )


def manual_patch(*hunks: PatchHunk, on_fuzzy: FuzzyPolicy = FuzzyPolicy.BLOCK) -> TemplatePatch:
    """A TemplatePatch with a fingerprint that can never hit the fast path."""
    return TemplatePatch(
        stage=Stage.QUANTUS,
        template_id="quantus/ext.cmd",
        base=BaseFingerprint(
            template_sha256="0" * 64,
            masked_sha256="0" * 64,
            captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        ),
        hunks=list(hunks),
        on_fuzzy=on_fuzzy,
    )


DIRNAME_OLD = '              -directory_name "/work/pll_top/query_output" \\\n'
DIRNAME_NEW = '              -directory_name "/work/pll_top/query_output_v2" \\\n'
CORNER_OLD = '              "TYPICAL" \\\n'
CORNER_NEW = '              "CBEST" \\\n'


def dirname_patch(**kwargs: Any) -> TemplatePatch:
    """Patch that retargets ``input_db``'s query-output directory.

    Deliberately anchored on a line whose *twin* exists in ``output_setup``:
    the duplicate is what makes the similarity tier's margin guard fire in the
    unmasked counter-factual.
    """
    ctx = profile_a("pll_top")
    edited = render(TPL, ctx).replace(DIRNAME_OLD, DIRNAME_NEW, 1)
    return capture(TPL, ctx, edited, **kwargs)


def corner_patch(**kwargs: Any) -> TemplatePatch:
    """Patch that forces the extraction corner to CBEST."""
    ctx = profile_a("pll_top")
    edited = render(TPL, ctx).replace(CORNER_OLD, CORNER_NEW, 1)
    return capture(TPL, ctx, edited, **kwargs)


def only(report: PatchApplyReport):
    assert len(report.resolutions) == 1, report.summary()
    return report.resolutions[0]


# --- mask grammar (T13) -----------------------------------------------------


def test_escape_literal_unmask_is_identity_on_random_text() -> None:
    rng = random.Random(20260821)
    alphabet = string.printable.replace("\r", "")
    for _ in range(300):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        assert unmask(escape_literal(text), {}) == text


def test_escape_literal_survives_dollar_forms() -> None:
    for text in ("A$B", "$$", "${cell}", "$env(FOO)", "cost: $5 and $$"):
        assert unmask(escape_literal(text), {"cell": "X"}) == text


def test_mask_escape_keeps_known_slots_and_escapes_everything_else() -> None:
    raw = 'net "A$B" ${cell} ${unknown} $$ tail'
    escaped = mask_escape(raw, {"cell"})
    assert escaped == 'net "A$$B" ${cell} $${unknown} $$$$ tail'
    assert unmask(escaped, {"cell": "pll_top"}) == 'net "A$B" pll_top ${unknown} $$ tail'


def test_mask_escape_round_trips_a_literal_double_dollar() -> None:
    # The failure this guards: a raw ``$$`` in a tool file collapsing to ``$``
    # because the mask grammar read it as an escape.
    raw = "*lvsPowerNames: VDD$$AUX\n"
    assert unmask(mask_escape(raw, set()), {}) == raw


def test_line_pattern_compiles_escaped_dollar_as_a_literal() -> None:
    pattern = _line_pattern("A$$B\n", {}, frozenset(), normalized=False)
    assert pattern.fullmatch("A$B\n")
    assert not pattern.fullmatch("A$$B\n")


def test_slots_in_ignores_escapes() -> None:
    assert slots_in("$$ ${cell} ${out_file} $$") == {"cell", "out_file"}


def test_hunk_with_dollar_round_trips_through_ruamel() -> None:
    hunk = PatchHunk(
        id="a1b2c3d4",
        before='*lvsPowerNames: VDD$$AUX ${cell}\n',
        after='*lvsPowerNames: VDD$$AUX ${cell} ${library}\n',
        context_before="head\n",
        context_after="tail\n",
    )
    yaml = YAML()
    buf = StringIO()
    yaml.dump(hunk.model_dump(mode="json"), buf)
    restored = PatchHunk.model_validate(yaml.load(StringIO(buf.getvalue())))
    assert restored.before == hunk.before
    assert restored.after == hunk.after


# --- masked rendering and alignment (T14) -----------------------------------


def test_condition_vars_finds_the_if_guard() -> None:
    assert "connect_by_name" in condition_vars(QCI)


def test_condition_vars_covers_for_and_inline_conditionals() -> None:
    source = "[% for item in items %][[item]]\n[% endfor %][[a if flag else b]]\n"
    found = condition_vars(source)
    assert {"items", "flag"} <= found


def test_masked_context_never_masks_a_condition_variable() -> None:
    ctx = {
        "connect_by_name": "yes",
        "output_dir": "/work/pll_top",
        "library": "ANALOG_LIB",
        "lvs_layout_view": "layout",
        "cell": "pll_top",
    }
    masked = masked_context(QCI, ctx)
    assert masked["connect_by_name"] == "yes"
    assert masked["output_dir"] == "${output_dir}"
    assert "connect_by_name" not in mask_values(QCI, ctx)


def test_masked_context_skips_short_values_bools_and_non_scalars() -> None:
    ctx = {
        "short": "ab",
        "flag": True,
        "nothing": None,
        "listy": ["a", "b"],
        "number": 5000,
        "text": "abcdef",
    }
    masked = masked_context("[[short]][[number]][[text]]", ctx)
    assert masked["short"] == "ab"
    assert masked["flag"] is True
    assert masked["nothing"] is None
    assert masked["listy"] == ["a", "b"]
    assert masked["number"] == "${number}"
    assert masked["text"] == "${text}"


def test_t14a_multiline_value_is_bound_literally_and_capture_still_works() -> None:
    source = 'note "[[note]]"\n-min_res [[min_res]]\n'
    ctx = {"note": "line one\nline two", "min_res": "0.001"}
    assert masked_context(source, ctx)["note"] == "line one\nline two"
    assert "note" not in mask_values(source, ctx)

    real = render(source, ctx)
    edited = real.replace("-min_res 0.001", "-min_res 0.005")
    patch = capture_patch(
        template_source=source,
        template_sha256=sha256_text(source),
        stage="quantus",
        template_id="quantus/ext.cmd",
        profile_id=None,
        catalog_version=None,
        base_real=real,
        base_masked=render_masked(source, ctx),
        edited_real=edited,
        values=mask_values(source, ctx),
    )
    assert patch.hunks[0].before == "-min_res ${min_res}\n"
    # The multi-line value stays literal in the neighbouring context.
    assert "line one" in "".join(h.context_before for h in patch.hunks)


def test_t14b_misaligned_renders_are_rejected() -> None:
    source = "[% if flag %]extra line\n[% endif %]-min_res [[min_res]]\n"
    ctx = {"flag": False, "min_res": "0.001"}
    real = render(source, ctx)
    # Deliberately bypass masked_context: bind the condition variable to a
    # truthy token string, exactly the mistake condition_vars() prevents.
    bad_masked = render(source, {"flag": "${flag}", "min_res": "${min_res}"})
    assert len(lines_of(bad_masked)) != len(lines_of(real))

    with pytest.raises(ValueError, match="not line-aligned"):
        capture_patch(
            template_source=source,
            template_sha256=sha256_text(source),
            stage="quantus",
            template_id="quantus/ext.cmd",
            profile_id=None,
            catalog_version=None,
            base_real=real,
            base_masked=bad_masked,
            edited_real=real.replace("0.001", "0.005"),
            values={"min_res": "0.001", "flag": "False"},
        )


def test_render_masked_is_line_aligned_with_the_real_render() -> None:
    ctx = profile_a()
    assert len(lines_of(render(TPL, ctx))) == len(lines_of(render_masked(TPL, ctx)))


# --- T1: swapping the cell --------------------------------------------------


def test_t1_masked_patch_survives_a_cell_swap_via_the_fast_path() -> None:
    patch = dirname_patch()
    hunk = patch.hunks[0]
    assert hunk.before == '              -directory_name "${output_dir}/query_output" \\\n'
    assert hunk.after == '              -directory_name "${output_dir}/query_output_v2" \\\n'
    assert "${cell}" in hunk.context_before

    ctx2 = profile_a("vco_core")
    base2, masked2 = render(TPL, ctx2), render_masked(TPL, ctx2)
    report = apply_patch(base2, patch, mask_values(TPL, ctx2), base_masked_text=masked2)

    assert report.fast_path is True
    res = only(report)
    assert res.status is PatchStatus.CLEAN
    assert res.fuzz.is_clean
    assert '/work/vco_core/query_output_v2' in report.patched_text
    assert "pll_top" not in report.patched_text
    assert report.blocking is False


def test_t1_counterfactual_unmasked_capture_is_lost_after_a_cell_swap() -> None:
    """The executable proof of "why not unified diff".

    Same edit, same bases -- only the masking is turned off, which is what a
    unified diff stores. The patch dies on the very next DUT.
    """
    patch = dirname_patch(mask=False)
    assert patch.hunks[0].before == DIRNAME_OLD  # frozen cell name

    ctx2 = profile_a("vco_core")
    report = apply_patch(render(TPL, ctx2), patch, mask_values(TPL, ctx2))
    res = only(report)
    assert res.status is PatchStatus.LOST
    assert report.blocking is True
    assert report.patched_text == render(TPL, ctx2)
    assert res.nearest is not None
    assert "Anchor is gone" in res.message


# --- T2: pure line-number drift ---------------------------------------------


def test_t2_new_directive_above_the_hunk_only_shifts_it() -> None:
    patch = dirname_patch()
    old_start = patch.hunks[0].recorded_start
    added = (
        "# catalog 2026.09: decoupling defaults\n"
        "# see PDK release note 4.2\n"
        "capacitance \\\n"
        "              -decoupling_factor 1.4\n"
    )
    ctx = profile_a("pll_top")
    base = insert_at(render(TPL, ctx), 6, added)
    masked = insert_at(render_masked(TPL, ctx), 6, added)

    report = apply_patch(base, patch, mask_values(TPL, ctx), base_masked_text=masked)
    assert report.fast_path is False
    res = only(report)
    assert res.status is PatchStatus.CLEAN
    assert res.fuzz.is_clean
    assert res.start == old_start + 4
    assert res.tried[0].startswith("ctx=3 binding=exact norm=0")
    assert res.tried[0].endswith("-> 1")


# --- T3: swapping the PDK profile -------------------------------------------


def test_t3_profile_swap_hits_the_exact_rung() -> None:
    patch = corner_patch()
    hunk = patch.hunks[0]
    assert "${tech_name}" in hunk.context_after
    assert "${qrc_tech_lib}" in hunk.context_after

    ctx_b = profile_b("pll_top")
    report = apply_patch(render(TPL, ctx_b), patch, mask_values(TPL, ctx_b))
    res = only(report)

    # The very first rung -- full context, every slot bound to its NEW value.
    assert res.tried[0] == "ctx=3 binding=exact norm=0 -> 1"
    assert res.status is PatchStatus.CLEAN
    assert res.fuzz.is_clean
    assert res.fuzz.binding is Binding.EXACT
    assert '              "CBEST" \\\n' in report.patched_text


def test_t3_captured_values_still_show_the_old_profile() -> None:
    patch = corner_patch()
    hunk = patch.hunks[0]
    ctx_b = profile_b("pll_top")
    current = mask_values(TPL, ctx_b)
    drifted = {
        name: (hunk.captured_values[name], current[name])
        for name in hunk.captured_values
        if current.get(name) != hunk.captured_values[name]
    }
    assert drifted["tech_name"] == ("tsmc22ull_1p10m", "tsmc16ffc_1p13m")
    assert drifted["qrc_tech_lib"][0].startswith("/pdk/tsmc22ull/")


# --- T4: the catalog parameterises a literal --------------------------------


def test_t4_parameterised_literal_is_lost_not_mis_applied() -> None:
    patch = corner_patch()
    assert patch.hunks[0].before == CORNER_OLD  # literal at capture time

    new_tpl = TPL.replace('              "TYPICAL" \\', '              "[[corner]]" \\')
    ctx = profile_a("pll_top")
    ctx["corner"] = "RCWORST"
    base = render(new_tpl, ctx)
    assert '              "RCWORST" \\\n' in base

    report = apply_patch(base, patch, mask_values(new_tpl, ctx))
    res = only(report)
    assert res.status is PatchStatus.LOST
    assert report.blocking is True
    # Nothing is written: "rather LOST than wrong" is the whole point.
    assert report.patched_text == base
    assert "CBEST" not in report.patched_text
    assert res.nearest is not None
    assert res.nearest[2] < SIMILARITY_MIN_RATIO
    assert "Anchor is gone" in res.message
    assert f"line {res.nearest[0] + 1}" in res.message


def test_t4_no_ladder_rung_mis_matches_the_new_corner_line() -> None:
    """Guard test: every rung must report zero sites, including the loosest."""
    patch = corner_patch()
    new_tpl = TPL.replace('              "TYPICAL" \\', '              "[[corner]]" \\')
    ctx = profile_a("pll_top")
    ctx["corner"] = "RCWORST"
    report = apply_patch(render(new_tpl, ctx), patch, mask_values(new_tpl, ctx))
    res = only(report)
    ladder = [line for line in res.tried if line.startswith("ctx=")]
    assert ladder, res.tried
    assert all(line.endswith("-> 0") for line in ladder)


# --- T5: the catalog adopts the edit ----------------------------------------


def test_t5_absorbed_patch_changes_nothing_and_asks_to_be_deleted() -> None:
    patch = corner_patch()
    new_tpl = TPL.replace('"TYPICAL"', '"CBEST"')
    ctx = profile_a("pll_top")
    base = render(new_tpl, ctx)

    report = apply_patch(base, patch, mask_values(new_tpl, ctx))
    res = only(report)
    assert res.status is PatchStatus.ABSORBED
    assert report.patched_text == base
    assert report.blocking is False
    assert "adopted" in res.message
    assert "-- absorbed probe (needle = after) --" in res.tried


def test_t5_absorbed_probe_does_not_run_while_before_is_still_present() -> None:
    patch = corner_patch()
    ctx = profile_a("vco_core")
    report = apply_patch(render(TPL, ctx), patch, mask_values(TPL, ctx))
    res = only(report)
    assert res.status is PatchStatus.CLEAN
    assert "-- absorbed probe (needle = after) --" not in res.tried


# --- T6: the tool re-exported with a different indent -----------------------


def test_t6_whitespace_normalised_match_rebases_the_indent() -> None:
    patch = corner_patch()
    ctx = profile_a("pll_top")
    base = reindent(render(TPL, ctx))

    report = apply_patch(base, patch, mask_values(TPL, ctx))
    res = only(report)
    assert res.status is PatchStatus.SHIFTED
    assert res.fuzz.normalized is True
    assert res.fuzz.dropped_context == 0
    assert res.tried[0] == "ctx=3 binding=exact norm=0 -> 0"

    # The replacement follows the NEW indent, not the captured one.
    assert '        "CBEST" \\\n' in report.patched_text
    assert '              "CBEST"' not in report.patched_text
    indents = {
        len(line) - len(line.lstrip(" "))
        for line in lines_of(report.patched_text)
        if line.startswith(" ")
    }
    assert indents == {8}


# --- T7: repeated structures ------------------------------------------------


def dup_patch(*, ctx_lines: int = 2) -> tuple[TemplatePatch, dict[str, Any]]:
    ctx = {"out_file": "pll_top_ext", "output_dir": "/work/pll_top"}
    real = render(TPL_DUP, ctx)
    target = '              -include_cap_model "false" \\\n'
    first = real.index(target)
    second = real.index(target, first + 1)
    edited = real[:second] + '              -include_cap_model "true" \\\n' + real[
        second + len(target) :
    ]
    patch = capture_patch(
        template_source=TPL_DUP,
        template_sha256=sha256_text(TPL_DUP),
        stage="quantus",
        template_id="quantus/ext.cmd",
        profile_id="tsmc22ull",
        catalog_version="2026.08",
        base_real=real,
        base_masked=render_masked(TPL_DUP, ctx),
        edited_real=edited,
        values=mask_values(TPL_DUP, ctx),
        ctx_lines=ctx_lines,
    )
    return patch, ctx


def test_t7a_recorded_occurrence_disambiguates_and_leaves_a_trace() -> None:
    patch, ctx = dup_patch()
    hunk = patch.hunks[0]
    assert (hunk.occurrence_index, hunk.occurrence_count) == (1, 2)

    base = render(TPL_DUP, ctx)
    report = apply_patch(base, patch, mask_values(TPL_DUP, ctx))
    res = only(report)
    assert res.status is PatchStatus.SHIFTED  # never CLEAN: the ordinal was used
    assert res.fuzz.by_occurrence is True
    assert res.start == hunk.recorded_start == 11

    # Only the SECOND block changed.
    rows = lines_of(report.patched_text)
    assert rows[11] == '              -include_cap_model "true" \\\n'
    assert rows[5] == '              -include_cap_model "false" \\\n'
    assert report.patched_text.count('-include_cap_model "true"') == 1


def test_t7b_missing_occurrence_data_is_ambiguous() -> None:
    patch, ctx = dup_patch()
    stale = patch.hunks[0].model_copy(
        update={"occurrence_index": None, "occurrence_count": None}
    )
    report = apply_patch(
        render(TPL_DUP, ctx), manual_patch(stale), mask_values(TPL_DUP, ctx)
    )
    res = only(report)
    assert res.status is PatchStatus.AMBIGUOUS
    assert len(res.candidates) == 2
    assert report.blocking is True
    assert report.patched_text == render(TPL_DUP, ctx)


def test_t7b_changed_occurrence_count_is_ambiguous() -> None:
    patch, ctx = dup_patch()
    rows = lines_of(render(TPL_DUP, ctx))
    third = "".join(rows[:8] + rows[2:8] + rows[8:])  # a third identical block
    assert third.count("output_db \\\n") == 3

    report = apply_patch(third, patch, mask_values(TPL_DUP, ctx))
    res = only(report)
    assert res.status is PatchStatus.AMBIGUOUS
    assert len(res.candidates) == 3
    assert report.blocking is True
    assert report.patched_text == third


# --- T8: two hunks landing on the same region -------------------------------


OVERLAP_BASE = (
    "filter_cap \\\n"
    "              -exclude_self_cap true \\\n"
    "              -exclude_floating_nets true \\\n"
    "              -exclude_floating_nets_limit 5000 \\\n"
    "              -remove_dangling_res true\n"
    "filter_res \\\n"
    "              -min_res 0.001\n"
)


def test_t8_overlapping_hunks_block_and_neither_is_applied() -> None:
    rows = lines_of(OVERLAP_BASE)
    hunk_a = PatchHunk(
        id="aaaaaaaa",
        before="".join(rows[1:3]),
        after="              -exclude_self_cap false \\\n",
        context_before=rows[0],
        context_after="".join(rows[3:5]),
    )
    hunk_b = PatchHunk(
        id="bbbbbbbb",
        before="".join(rows[2:4]),
        after="              -exclude_floating_nets false \\\n",
        context_before="".join(rows[:2]),
        context_after=rows[4],
    )
    report = apply_patch(OVERLAP_BASE, manual_patch(hunk_a, hunk_b), {})

    assert [r.status for r in report.resolutions] == [
        PatchStatus.OVERLAP,
        PatchStatus.OVERLAP,
    ]
    assert report.patched_text == OVERLAP_BASE
    assert report.blocking is True
    for res in report.resolutions:
        assert "Overlaps another manual edit" in res.message
        assert "lines 2-3" in res.message and "lines 3-4" in res.message


# --- T9: pure insertion -----------------------------------------------------


INSERT_AFTER = "              -device_property_value 7 \\\n"
EXTRA_NETLIST = '              -extra_netlist "pll_top_extra.sp" \\\n'


def insertion_patch(**kwargs: Any) -> TemplatePatch:
    ctx = profile_a("pll_top")
    edited = render(TPL, ctx).replace(
        INSERT_AFTER, INSERT_AFTER + EXTRA_NETLIST, 1
    )
    return capture(TPL, ctx, edited, **kwargs)


def test_t9a_insertion_between_intact_anchors_applies_and_follows_the_cell() -> None:
    patch = insertion_patch()
    hunk = patch.hunks[0]
    assert hunk.before == ""
    assert hunk.after == '              -extra_netlist "${cell}_extra.sp" \\\n'
    assert hunk.context_before and hunk.context_after

    ctx2 = profile_a("vco_core")
    report = apply_patch(render(TPL, ctx2), patch, mask_values(TPL, ctx2))
    res = only(report)
    assert res.status is PatchStatus.CLEAN
    assert res.start == res.end
    rows = lines_of(report.patched_text)
    assert rows[res.start] == '              -extra_netlist "vco_core_extra.sp" \\\n'
    assert rows[res.start - 1] == INSERT_AFTER
    assert "pll_top" not in report.patched_text


def test_t9b_insertion_loses_its_anchor_when_a_line_lands_between_them() -> None:
    patch = insertion_patch()
    ctx2 = profile_a("vco_core")
    base = render(TPL, ctx2).replace(
        INSERT_AFTER, INSERT_AFTER + '              -device_property_units "um" \\\n', 1
    )
    report = apply_patch(base, patch, mask_values(TPL, ctx2))
    res = only(report)
    assert res.status is PatchStatus.LOST
    assert report.patched_text == base
    # ctx=0 is never tried while searching for an empty `before`: with no
    # needle and no context there is nothing left to anchor on. (The ABSORBED
    # probe that runs afterwards has a non-empty needle, so it does use ctx=0.)
    before_probe = res.tried[: res.tried.index("-- absorbed probe (needle = after) --")]
    assert before_probe
    assert all("ctx=0" not in line for line in before_probe)


def test_t9_insertion_without_both_anchors_is_rejected_by_the_model() -> None:
    with pytest.raises(ValidationError, match="pure-insertion hunk needs BOTH"):
        PatchHunk(id="deadbeef", before="", after="x\n", context_before="a\n")


# --- T10: the `after` side follows the current values -----------------------


def test_t10_after_side_variables_track_the_new_render() -> None:
    patch = dirname_patch()
    ctx2 = profile_a("vco_core")
    report = apply_patch(render(TPL, ctx2), patch, mask_values(TPL, ctx2))
    assert "/work/vco_core/query_output_v2" in report.patched_text
    assert "/work/pll_top" not in report.patched_text


def test_t10_remask_edit_masks_known_values_and_keeps_the_rest_literal() -> None:
    values = {"output_dir": "/work/pll_top", "cell": "pll_top"}
    before = ['              -directory_name "${output_dir}/query_output" \\\n']
    out = _remask_edit(
        ['              -directory_name "/work/pll_top/patched/" \\\n'], before, values
    )
    assert out == ['              -directory_name "${output_dir}/patched/" \\\n']

    # A value the user typed that is not any slot's value stays literal.
    corner = _remask_edit(['              "CBEST" \\\n'], before, values)
    assert corner == ['              "CBEST" \\\n']


def test_remask_never_eats_a_token_it_just_inserted() -> None:
    # ``ell`` is a substring of the token ``${cell}``; sentinel substitution
    # is what stops it from producing ``${c${view}}``.
    values = {"cell": "pll_top", "view": "ell"}
    out = remask_text(["x pll_top y\n"], values)
    assert out == ["x ${cell} y\n"]


def test_keep_literal_freezes_a_value_the_user_meant_as_a_constant() -> None:
    ctx = profile_a("pll_top")
    edited = render(TPL, ctx).replace(
        DIRNAME_OLD, '              -directory_name "/work/pll_top/shared" \\\n', 1
    )
    following = capture(TPL, ctx, edited)
    frozen = capture(TPL, ctx, edited, keep_literal=("output_dir",))
    assert following.hunks[0].after == '              -directory_name "${output_dir}/shared" \\\n'
    assert frozen.hunks[0].after == '              -directory_name "/work/pll_top/shared" \\\n'

    ctx2 = profile_a("vco_core")
    values2 = mask_values(TPL, ctx2)
    base2 = render(TPL, ctx2)
    assert "/work/vco_core/shared" in apply_patch(base2, following, values2).patched_text
    assert "/work/pll_top/shared" in apply_patch(base2, frozen, values2).patched_text


# --- T11: the knob caught up with the patch ---------------------------------


TPL_TEMP = "process_technology \\\n              -temperature [[temperature]]\n"


def test_t11_converged_values_make_the_hunk_a_noop() -> None:
    ctx = {"temperature": 125}
    real = render(TPL_TEMP, ctx)
    edited = real.replace("-temperature 125", "-temperature 185")
    patch = capture_patch(
        template_source=TPL_TEMP,
        template_sha256=sha256_text(TPL_TEMP),
        stage="quantus",
        template_id="quantus/ext.cmd",
        profile_id=None,
        catalog_version=None,
        base_real=real,
        base_masked=render_masked(TPL_TEMP, ctx),
        edited_real=edited,
        values=mask_values(TPL_TEMP, ctx),
    )
    assert patch.hunks[0].before == "              -temperature ${temperature}\n"
    assert patch.hunks[0].after == "              -temperature 185\n"

    # The recipe knob is later set to 185; the base now emits it by itself.
    later = {"temperature": 185}
    base = render(TPL_TEMP, later)
    report = apply_patch(base, patch, mask_values(TPL_TEMP, later))
    res = only(report)
    assert res.status is PatchStatus.NOOP
    assert report.patched_text == base
    assert report.blocking is False
    assert "can be deleted" in res.message


def test_t11_the_same_patch_still_applies_before_the_knob_catches_up() -> None:
    ctx = {"temperature": 125}
    real = render(TPL_TEMP, ctx)
    patch = capture_patch(
        template_source=TPL_TEMP,
        template_sha256=sha256_text(TPL_TEMP),
        stage="quantus",
        template_id="quantus/ext.cmd",
        profile_id=None,
        catalog_version=None,
        base_real=real,
        base_masked=render_masked(TPL_TEMP, ctx),
        edited_real=real.replace("-temperature 125", "-temperature 185"),
        values=mask_values(TPL_TEMP, ctx),
    )
    other = {"temperature": 150}
    report = apply_patch(render(TPL_TEMP, other), patch, mask_values(TPL_TEMP, other))
    assert only(report).status is PatchStatus.CLEAN
    assert "-temperature 185" in report.patched_text


# --- T12: the similarity tier's two edges -----------------------------------


def test_t12a_a_renamed_directive_is_review_and_blocks_by_default() -> None:
    ctx = profile_a("pll_top")
    edited = render(TPL, ctx).replace(
        "              -exclude_floating_nets_limit 5000",
        "              -exclude_floating_nets_limit 2000",
    )
    patch = capture(TPL, ctx, edited)

    renamed_tpl = TPL.replace(
        "-exclude_floating_nets_limit", "-exclude_floating_net_limit"
    )
    base = render(renamed_tpl, ctx)
    report = apply_patch(base, patch, mask_values(renamed_tpl, ctx))
    res = only(report)

    assert res.status is PatchStatus.REVIEW
    assert res.fuzz.similarity is not None
    assert res.fuzz.similarity >= 0.95
    assert res.start is not None
    # Applied, so the editor can preview the merge. The stored `after` still
    # carries the OLD directive spelling -- that is exactly why a human has to
    # look at it before this runs.
    assert "-exclude_floating_nets_limit 2000" in report.patched_text
    assert "-exclude_floating_net_limit 5000" not in report.patched_text
    # ...but the run is refused unless the recipe opted in.
    assert report.blocking is True
    assert report.blocking_under(FuzzyPolicy.BLOCK) is True
    assert report.blocking_under(FuzzyPolicy.ACCEPT) is False


def test_t12b_near_identical_twins_are_rejected_by_the_margin_guard() -> None:
    twin = (
        "*cmnLSFSlaveTbl: {use 1} {totalCpus 1} {minCpus 1} {architecture {}} "
        "{minMemory {}} {resourceOptions {}} {submitOptions {}}\n"
    )
    grid = twin.replace("*cmnLSFSlaveTbl", "*cmnGridSlaveTbl")
    base_old = "*cmnRunHyper: 1\n" + twin + grid + "*cmnFDILayoutView: layout\n"
    hunk = PatchHunk(
        id="cccccccc",
        before=twin,
        after=twin.replace("{totalCpus 1}", "{totalCpus 8}"),
        context_before="*cmnRunHyper: 1\n",
        context_after=grid,
    )
    # The catalog bumps totalCpus in BOTH twins: the anchor no longer matches
    # anywhere and the two candidates are within a couple of percent.
    base_new = base_old.replace("{totalCpus 1}", "{totalCpus 2}")

    report = apply_patch(base_new, manual_patch(hunk), {})
    res = only(report)
    assert res.status is PatchStatus.LOST
    assert res.nearest is not None
    best_ratio = res.nearest[2]
    assert best_ratio >= SIMILARITY_MIN_RATIO  # similar enough...
    assert report.patched_text == base_new  # ...but rejected on margin
    assert "{totalCpus 8}" not in report.patched_text
    assert report.blocking is True


def test_similarity_margin_constant_is_the_documented_one() -> None:
    assert (SIMILARITY_MIN_RATIO, SIMILARITY_MIN_MARGIN) == (0.80, 0.10)


# --- T15: fast path and slow path must agree --------------------------------


def test_t15_fast_and_slow_paths_produce_identical_bytes() -> None:
    patch = dirname_patch()
    ctx = profile_a("vco_core")
    base, masked, values = render(TPL, ctx), render_masked(TPL, ctx), mask_values(TPL, ctx)

    fast = apply_patch(base, patch, values, base_masked_text=masked)
    slow = apply_patch(base, patch, values)

    assert fast.fast_path is True
    assert slow.fast_path is False
    assert fast.patched_text == slow.patched_text
    assert all(r.fuzz.is_clean for r in fast.resolutions)
    assert [r.status for r in fast.resolutions] == [r.status for r in slow.resolutions]


def test_fast_path_verifies_recorded_offsets_before_trusting_them() -> None:
    patch = dirname_patch()
    rotten = patch.model_copy(
        update={"hunks": [patch.hunks[0].model_copy(update={"recorded_start": 3})]}
    )
    ctx = profile_a("vco_core")
    base, masked, values = render(TPL, ctx), render_masked(TPL, ctx), mask_values(TPL, ctx)

    report = apply_patch(base, rotten, values, base_masked_text=masked)
    assert report.fast_path is False  # fell back to the ladder
    assert only(report).status is PatchStatus.CLEAN
    assert report.patched_text == apply_patch(base, patch, values).patched_text


def test_fast_path_handles_multiple_hunks_in_declaration_order() -> None:
    ctx = profile_a("pll_top")
    edited = render(TPL, ctx).replace(DIRNAME_OLD, DIRNAME_NEW, 1).replace(
        CORNER_OLD, CORNER_NEW, 1
    )
    patch = capture(TPL, ctx, edited)
    assert len(patch.hunks) == 2

    ctx2 = profile_a("vco_core")
    base, masked, values = (
        render(TPL, ctx2),
        render_masked(TPL, ctx2),
        mask_values(TPL, ctx2),
    )
    fast = apply_patch(base, patch, values, base_masked_text=masked)
    slow = apply_patch(base, patch, values)
    assert fast.fast_path is True
    assert fast.patched_text == slow.patched_text
    assert [r.hunk_id for r in fast.resolutions] == [h.id for h in patch.hunks]
    assert "/work/vco_core/query_output_v2" in fast.patched_text
    assert '              "CBEST" \\\n' in fast.patched_text


# --- disabled / unresolved slots --------------------------------------------


def test_disabled_hunk_is_recorded_but_not_applied() -> None:
    patch = dirname_patch()
    off = patch.model_copy(
        update={"hunks": [patch.hunks[0].model_copy(update={"enabled": False})]}
    )
    ctx = profile_a("vco_core")
    base, masked, values = render(TPL, ctx), render_masked(TPL, ctx), mask_values(TPL, ctx)

    for kwargs in ({"base_masked_text": masked}, {}):
        report = apply_patch(base, off, values, **kwargs)
        res = only(report)
        assert res.status is PatchStatus.DISABLED
        assert report.patched_text == base
        assert report.blocking is False
    assert off.enabled_count == 0


def test_unresolved_slot_in_after_is_always_blocking() -> None:
    hunk = PatchHunk(
        id="dddddddd",
        before="              -min_res 0.001\n",
        after='              -min_res ${nonexistent}\n',
        context_before="filter_res \\\n",
        context_after="              -remove_dangling_res true\n",
    )
    base = (
        "filter_res \\\n"
        "              -min_res 0.001\n"
        "              -remove_dangling_res true\n"
    )
    report = apply_patch(base, manual_patch(hunk), {})
    res = only(report)
    assert res.unresolved_slots == ["nonexistent"]
    assert res.blocking is True
    assert res.blocking_under(FuzzyPolicy.ACCEPT) is True
    assert report.blocking_under(FuzzyPolicy.ACCEPT) is True


# --- unified diff: display and import ---------------------------------------


def test_render_hunk_as_udiff_materialises_with_the_current_values() -> None:
    patch = dirname_patch()
    ctx = profile_a("vco_core")
    text = render_hunk_as_udiff(patch.hunks[0], mask_values(TPL, ctx))
    assert "-              -directory_name \"/work/vco_core/query_output\" \\" in text
    assert "+              -directory_name \"/work/vco_core/query_output_v2\" \\" in text


def test_render_hunk_as_udiff_can_show_the_masked_form() -> None:
    patch = dirname_patch()
    text = render_hunk_as_udiff(patch.hunks[0], {})
    assert "${output_dir}" in text


def test_import_udiff_masks_a_pasted_patch() -> None:
    diff = (
        "--- a/ext.cmd\n"
        "+++ b/ext.cmd\n"
        "@@ -30,7 +30,7 @@\n"
        ' input_db -type calibre \\\n'
        '               -design_cell_name "pll_top layout ANALOG_LIB" \\\n'
        "               -device_property_value 7 \\\n"
        '-              -run_name "Design" \\\n'
        '+              -run_name "pll_top_run" \\\n'
        '               -directory_name "/work/pll_top/query_output" \\\n'
        '               -format "DFII" \\\n'
        "               -instance_property_value 6 \\\n"
    )
    values = mask_values(TPL, profile_a("pll_top"))
    hunks = import_udiff(diff, values)
    assert len(hunks) == 1
    hunk = hunks[0]
    assert hunk.before == '              -run_name "Design" \\\n'
    assert hunk.after == '              -run_name "${cell}_run" \\\n'
    assert "${cell}" in hunk.context_before
    assert "${output_dir}" in hunk.context_after
    assert hunk.occurrence_index is None and hunk.occurrence_count is None
    assert hunk.captured_values["cell"] == "pll_top"


def test_imported_hunk_applies_to_another_cell() -> None:
    diff = (
        "@@ -30,5 +30,5 @@\n"
        ' input_db -type calibre \\\n'
        '               -design_cell_name "pll_top layout ANALOG_LIB" \\\n'
        "               -device_property_value 7 \\\n"
        '-              -run_name "Design" \\\n'
        '+              -run_name "pll_top_run" \\\n'
        '               -directory_name "/work/pll_top/query_output" \\\n'
        '               -format "DFII" \\\n'
        "               -instance_property_value 6 \\\n"
    )
    hunks = import_udiff(diff, mask_values(TPL, profile_a("pll_top")))
    ctx2 = profile_a("vco_core")
    report = apply_patch(
        render(TPL, ctx2), manual_patch(*hunks), mask_values(TPL, ctx2)
    )
    assert only(report).status is PatchStatus.CLEAN
    assert '-run_name "vco_core_run"' in report.patched_text


def test_import_udiff_splits_separate_change_runs_in_one_body() -> None:
    diff = (
        "@@ -1,7 +1,7 @@\n"
        " head\n"
        "-one\n"
        "+ONE\n"
        " middle\n"
        " middle2\n"
        "-two\n"
        "+TWO\n"
        " tail\n"
    )
    hunks = import_udiff(diff, {})
    assert [h.before for h in hunks] == ["one\n", "two\n"]
    assert hunks[0].context_after == "middle\nmiddle2\n"
    assert hunks[1].context_before == "middle\nmiddle2\n"


def test_import_udiff_rejects_text_with_no_hunks() -> None:
    with pytest.raises(ValueError, match="no hunks"):
        import_udiff("not a diff at all\n", {})


def test_import_udiff_rejects_an_unanchorable_insertion() -> None:
    diff = "@@ -1,0 +1,1 @@\n+brand new\n"
    with pytest.raises(ValueError, match="cannot be anchored"):
        import_udiff(diff, {})


# --- models -----------------------------------------------------------------


def test_hunk_rejects_crlf() -> None:
    with pytest.raises(ValidationError, match="LF line endings only"):
        PatchHunk(id="11111111", before="a\r\n", after="b\n")


def test_hunk_rejects_being_empty_on_both_sides() -> None:
    with pytest.raises(ValidationError, match="empty on both sides"):
        PatchHunk(id="11111111", before="", after="")


def test_hunk_rejects_an_out_of_range_occurrence_index() -> None:
    with pytest.raises(ValidationError, match="occurrence_index out of range"):
        PatchHunk(
            id="11111111",
            before="a\n",
            after="b\n",
            occurrence_index=2,
            occurrence_count=2,
        )


def test_hunk_id_must_be_eight_hex_digits() -> None:
    with pytest.raises(ValidationError):
        PatchHunk(id="not-hex!", before="a\n", after="b\n")


def test_patch_rejects_duplicate_hunk_ids() -> None:
    hunk = PatchHunk(id="22222222", before="a\n", after="b\n")
    with pytest.raises(ValidationError, match="duplicate hunk id"):
        manual_patch(hunk, hunk.model_copy())


def test_patch_rejects_an_unknown_stage_and_a_malformed_template_id() -> None:
    with pytest.raises(ValidationError):
        TemplatePatch(
            stage="voodoo",
            template_id="quantus/ext.cmd",
            base=BaseFingerprint(
                template_sha256="0" * 64,
                masked_sha256="0" * 64,
                captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            ),
        )
    with pytest.raises(ValidationError):
        TemplatePatch(
            stage=Stage.SI,
            template_id="Quantus/ext cmd",
            base=BaseFingerprint(
                template_sha256="0" * 64,
                masked_sha256="0" * 64,
                captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            ),
        )


def test_patch_models_forbid_extra_keys() -> None:
    with pytest.raises(ValidationError):
        PatchHunk(id="33333333", before="a\n", after="b\n", surprise=1)


def test_fingerprint_requires_real_hashes() -> None:
    with pytest.raises(ValidationError):
        BaseFingerprint(
            template_sha256="deadbeef",
            masked_sha256="0" * 64,
            captured_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )


def test_hunk_lookup_and_counts() -> None:
    patch = dirname_patch()
    assert patch.hunk(patch.hunks[0].id) is patch.hunks[0]
    assert patch.hunk("ffffffff") is None
    assert patch.enabled_count == 1


# --- capture bookkeeping ----------------------------------------------------


def test_capture_records_the_fingerprint_and_the_intent() -> None:
    ctx = profile_a("pll_top")
    patch = dirname_patch(intents={0: "point query_output at the v2 tree"})
    assert patch.base.template_sha256 == sha256_text(TPL)
    assert patch.base.masked_sha256 == sha256_text(render_masked(TPL, ctx))
    assert patch.base.profile_id == "tsmc22ull"
    assert patch.base.catalog_version == "2026.08"
    assert patch.base.captured_at.tzinfo is not None
    assert patch.hunks[0].intent == "point query_output at the v2 tree"
    assert patch.on_fuzzy is FuzzyPolicy.BLOCK


def test_recapture_preserves_hunk_identity_intent_and_enabled_flag() -> None:
    first = dirname_patch(intents={0: "why this exists"})
    kept = first.model_copy(
        update={"hunks": [first.hunks[0].model_copy(update={"enabled": False})]},
    )
    second = dirname_patch(existing=kept)
    assert second.hunks[0].id == first.hunks[0].id
    assert second.hunks[0].intent == "why this exists"
    assert second.hunks[0].enabled is False


def test_capture_marks_a_tail_anchored_hunk() -> None:
    ctx = profile_a("pll_top")
    real = render(TPL, ctx)
    assert real.endswith("              25\n")
    edited = real[: -len("              25\n")] + "              85\n"

    patch = capture(TPL, ctx, edited)
    hunk = patch.hunks[0]
    assert hunk.before == "              25\n"
    assert hunk.context_after == ""
    assert hunk.anchored_at_tail is True
    assert hunk.anchored_at_head is False

    # A tail-anchored hunk still resolves against a file that grew a header.
    grown = insert_at(real, 0, "# new banner\n")
    report = apply_patch(grown, patch, mask_values(TPL, ctx))
    assert only(report).status is PatchStatus.CLEAN
    assert report.patched_text.endswith("              85\n")


def test_capture_normalises_crlf_input() -> None:
    ctx = profile_a("pll_top")
    real = render(TPL, ctx)
    edited = real.replace(DIRNAME_OLD, DIRNAME_NEW, 1)
    patch = capture_patch(
        template_source=TPL,
        template_sha256=sha256_text(TPL),
        stage=Stage.QUANTUS,
        template_id="quantus/ext.cmd",
        profile_id=None,
        catalog_version=None,
        base_real=real.replace("\n", "\r\n"),
        base_masked=render_masked(TPL, ctx).replace("\n", "\r\n"),
        edited_real=edited.replace("\n", "\r\n"),
        values=mask_values(TPL, ctx),
    )
    assert "\r" not in patch.hunks[0].before + patch.hunks[0].after


def test_apply_normalises_crlf_input() -> None:
    patch = dirname_patch()
    ctx = profile_a("vco_core")
    report = apply_patch(
        render(TPL, ctx).replace("\n", "\r\n"), patch, mask_values(TPL, ctx)
    )
    assert only(report).status is PatchStatus.CLEAN
    assert "\r" not in report.patched_text


# --- reporting --------------------------------------------------------------


def test_fuzz_describe_is_readable() -> None:
    assert Fuzz().describe() == "exact match"
    assert Fuzz(dropped_context=1).describe() == "context shortened by 1 line"
    described = Fuzz(
        dropped_context=2,
        binding=Binding.ALL_WILD,
        normalized=True,
        by_occurrence=True,
        similarity=0.84,
    ).describe()
    for fragment in (
        "context shortened by 2 lines",
        "all variable slots relaxed",
        "whitespace differences ignored",
        "disambiguated by recorded occurrence",
        "similarity match 84%",
    ):
        assert fragment in described


def test_report_summary_counts_and_worst_status() -> None:
    patch = dirname_patch()
    ctx = profile_a("vco_core")
    report = apply_patch(render(TPL, ctx), patch, mask_values(TPL, ctx))
    assert report.summary() == "1 manual edit: clean=1"
    assert report.worst_status is PatchStatus.CLEAN
    assert report.resolution(patch.hunks[0].id) is not None
    assert report.resolution("ffffffff") is None


def test_build_stage_report_archives_everything_run_json_needs() -> None:
    patch = dirname_patch(intents={0: "v2 tree"})
    ctx = profile_a("vco_core")
    values = mask_values(TPL, ctx)
    report = apply_patch(render(TPL, ctx), patch, values)
    stage_report = build_stage_report(patch, report, values)

    assert stage_report.stage is Stage.QUANTUS
    assert stage_report.template_id == "quantus/ext.cmd"
    assert stage_report.blocked is False
    outcome = stage_report.outcomes[0]
    assert outcome.hunk_id == patch.hunks[0].id
    assert outcome.intent == "v2 tree"
    assert outcome.status is PatchStatus.CLEAN
    assert outcome.fuzz == "exact match"
    assert outcome.start_line == report.resolutions[0].start + 1
    assert "/work/vco_core/query_output_v2" in outcome.udiff
    # Round-trips through JSON, which is how run.json stores it.
    assert stage_report.model_validate(stage_report.model_dump(mode="json"))


def test_build_stage_report_marks_a_blocked_stage() -> None:
    patch = dirname_patch(mask=False)
    ctx = profile_a("vco_core")
    values = mask_values(TPL, ctx)
    report = apply_patch(render(TPL, ctx), patch, values)
    assert build_stage_report(patch, report, values).blocked is True


def test_patch_conflict_error_carries_the_report() -> None:
    patch = dirname_patch(mask=False)
    ctx = profile_a("vco_core")
    report = apply_patch(render(TPL, ctx), patch, mask_values(TPL, ctx))
    error = PatchConflictError(report, template_id="quantus/ext.cmd")
    assert error.report is report
    assert "quantus/ext.cmd" in str(error)
    assert "lost=1" in str(error)
