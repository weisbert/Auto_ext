"""Tests for :mod:`auto_ext.core.recipe_import` -- user's own files -> Recipe.

The inputs are real: every case starts from the *shipped* templates rendered
through :mod:`auto_ext.core.render`, so a test file is byte-for-byte what a run
of this tool would put in front of a user, and one hand-written fixture
(``tests/fixtures/raw/gui_export.ext.cmd``) stands in for a file this tool did
not produce at all, while a second one
(``tests/fixtures/raw/handwritten_rcworst.ext.cmd``) stands in for a file a
person typed: a whole number where the tool writes a float, a corner nobody
has heard of, and a statement the catalog does not model.

Four properties are what these tests are for:

**Nothing is lost.** A value the catalog models lands in the Recipe (or, when
it is a PDK fact, in the derived PdkProfile); a value it does not model lands
in a patch; between the two the import round-trips to the byte. Every target
has a test that says so.

**Nothing is in two places.** The counterpart of the above, and the sharper
rule: a value that landed is never *also* a patch hunk.
:func:`test_a_value_that_landed_in_the_recipe_or_the_profile_is_never_also_pinned_by_a_patch`
is the guard -- a hunk holding a modelled value pins the literal, so the field
the user edits stops changing anything.

**Nothing is frozen.** The patch format is masked, so a hunk captured from a
file that names ``INV1`` has to render ``vco_core`` for the next DUT.
:func:`test_a_captured_hunk_follows_the_cell` is the reason the masked format
exists and is the test that would catch its loss.

**Nothing is guessed.** A Quantus command file is ext or dspf by what
``output_db -type`` says, never by its name; an unrecognisable file is an error,
not an empty recipe.

**Nothing is written.** :func:`import_recipe` is a dry run; the file appears
only when :func:`write_imported_recipe` is called.

``resolve_template_path`` is never reached from here (the catalog resolves
templates from ``__file__``), so the "test cwd is still in the repo" trap that
``tests/test_migrate.py`` guards against does not apply.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from auto_ext.catalog import Catalog, Layout, builtin_catalog
from auto_ext.core import recipe_import as ri
from auto_ext.core import render
from auto_ext.core.errors import AutoExtError
from auto_ext.core.readback import composite_sites, parse_quantus, parse_skill, parse_xml
from auto_ext.model.common import RenderTarget, Stage
from auto_ext.model.pdk import CornerSpec, PdkProfile
from auto_ext.model.recipe import OutputKind, Recipe, load_recipe, recipe_from_catalog
from auto_ext.model.run import DutSnapshot
from tests.support.v2 import ENV, make_dut, make_profile, make_run

CELL = "INV1"
LIBRARY = "INV_LIB"


# ---- the sample files --------------------------------------------------------


def _render_all(
    tmp_path: Path,
    *,
    cell: str = CELL,
    library: str = LIBRARY,
    recipe: Recipe | None = None,
    profile: PdkProfile | None = None,
) -> dict[RenderTarget, str]:
    """What this tool writes today, for every target, as the user would see it."""

    use_recipe = recipe or recipe_from_catalog(
        recipe_id="shipped",
        name="shipped",
        output={"emit": [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]},
    )
    use_profile = profile or make_profile()
    context = render.build_context(
        dut=make_dut(cell=cell, library=library),
        recipe=use_recipe,
        profile=use_profile,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )
    return {
        plan.target: render.render_one(
            plan,
            context=context,
            recipe=use_recipe,
            profile=use_profile,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
            write=False,
        ).text
        for plan in render.plan_targets(use_recipe)
    }


@pytest.fixture
def shipped(tmp_path: Path) -> dict[RenderTarget, str]:
    return _render_all(tmp_path)


def _sources(texts: dict[RenderTarget, str]) -> list[ri.ImportSource]:
    return [ri.ImportSource(label=f"{t.value}", text=text) for t, text in texts.items()]


@pytest.fixture
def gui_export(fixtures_dir: Path) -> Path:
    """A Quantus ext.cmd this tool did not render: other version banner, other
    PDK paths, other cell, four values changed."""

    return fixtures_dir / "raw" / "gui_export.ext.cmd"


@pytest.fixture
def handwritten(fixtures_dir: Path) -> Path:
    """A Quantus ext.cmd somebody typed: 125 C, RCWORST, and an rf_analysis
    statement the catalog has never heard of.

    Every one of those three is a different fate -- a Recipe field, a
    PdkProfile corner, and a patch hunk -- which is why one file can carry the
    whole invariant.
    """

    return fixtures_dir / "raw" / "handwritten_rcworst.ext.cmd"


# ---- target detection --------------------------------------------------------


def test_every_render_target_is_recognised_from_its_content(
    shipped: dict[RenderTarget, str],
) -> None:
    for target, text in shipped.items():
        assert ri.detect_target(text, label=target.value) is target


def test_the_two_quantus_forms_are_told_apart_by_output_db_type(
    shipped: dict[RenderTarget, str],
) -> None:
    """The file name is a lie waiting to happen: the GUI writes what the dialog
    said, and running an extracted_view command file as a dspf run wastes an
    afternoon before anything looks wrong."""

    assert (
        ri.detect_target(shipped[RenderTarget.QUANTUS_EXT], label="dspf.cmd")
        is RenderTarget.QUANTUS_EXT
    )
    assert (
        ri.detect_target(shipped[RenderTarget.QUANTUS_DSPF], label="ext.cmd")
        is RenderTarget.QUANTUS_DSPF
    )


def test_the_dspf_only_sections_are_what_separate_the_two(
    shipped: dict[RenderTarget, str],
) -> None:
    """Belt and braces on the discriminator itself: if these ever appear in
    both files the content rule above is decorative."""

    ext = parse_quantus(shipped[RenderTarget.QUANTUS_EXT])
    dspf = parse_quantus(shipped[RenderTarget.QUANTUS_DSPF])
    assert ext[("output_db", "-type")].values == ("extracted_view",)
    assert dspf[("output_db", "-type")].values == ("dspf",)
    assert ("metal_fill", "-type") in dspf and ("metal_fill", "-type") not in ext
    assert ("output_db", "-view_name") in ext and ("output_db", "-view_name") not in dspf


def test_a_quantus_file_with_no_output_db_section_refuses_to_guess(
    shipped: dict[RenderTarget, str],
) -> None:
    truncated = shipped[RenderTarget.QUANTUS_EXT].split("input_db")[0]
    with pytest.raises(ri.RecipeImportError) as exc:
        ri.detect_target(truncated, label="half.cmd")
    assert "output_db -type" in str(exc.value)
    assert "explicitly" in str(exc.value)


def test_an_unrelated_file_is_refused_rather_than_half_imported() -> None:
    junk = "# notes\n\nalpha = 1\nbeta = 2\ngamma = 3\n" * 8
    with pytest.raises(ri.RecipeImportError) as exc:
        ri.import_recipe([ri.ImportSource(label="NOTES.md", text=junk)], recipe_id="junk")
    message = str(exc.value)
    assert "does not look like any file this tool generates" in message
    # The report has to say what was tried, not just that it failed.
    for target in RenderTarget:
        assert f"{target.value}=" in message


def test_the_import_error_is_an_auto_ext_error() -> None:
    """The CLI catches AutoExtError to fail one command rather than traceback."""

    assert issubclass(ri.RecipeImportError, AutoExtError)


def test_two_files_claiming_the_same_target_are_refused(
    shipped: dict[RenderTarget, str],
) -> None:
    text = shipped[RenderTarget.SI_ENV]
    with pytest.raises(ri.RecipeImportError, match="both si.env"):
        ri.import_recipe(
            [
                ri.ImportSource(label="mine.env", text=text),
                ri.ImportSource(label="theirs.env", text=text),
            ],
            recipe_id="two-si",
        )


def test_a_caller_may_force_the_target(shipped: dict[RenderTarget, str]) -> None:
    result = ri.import_recipe(
        [
            ri.ImportSource(
                label="unnamed.cmd",
                text=shipped[RenderTarget.QUANTUS_EXT],
                target=RenderTarget.QUANTUS_EXT,
            )
        ],
        recipe_id="forced",
    )
    assert result.sources[0].forced is True
    assert result.targets == (RenderTarget.QUANTUS_EXT,)


# ---- importing each target ---------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        RenderTarget.SI_ENV,
        RenderTarget.LVS_QCI,
        RenderTarget.QUANTUS_EXT,
        RenderTarget.QUANTUS_DSPF,
        RenderTarget.JIVARO_XML,
    ],
)
def test_each_target_imports_on_its_own_and_round_trips(
    shipped: dict[RenderTarget, str], target: RenderTarget
) -> None:
    """Give it one file and it produces a recipe that renders that file back.

    Zero hunks is the strong form: everything in the file was either a value
    the catalog models or a literal the template already writes.
    """

    result = ri.import_recipe(
        [ri.ImportSource(label=target.value, text=shipped[target])], recipe_id="one-file"
    )
    assert result.targets == (target,)
    assert result.hunk_count == 0
    assert result.unmodelled_ratio == 0.0
    assert result.roundtrip[target].identical, result.roundtrip[target].diff
    assert result.warnings == ()


def test_all_five_targets_import_into_one_recipe(shipped: dict[RenderTarget, str]) -> None:
    result = ri.import_recipe(_sources(shipped), recipe_id="all-five")
    assert set(result.targets) == set(shipped)
    assert [stage.value for stage in result.recipe.stages] == [
        "si",
        "calibre",
        "quantus",
        "jivaro",
    ]
    assert result.recipe.output.emit == [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]
    assert result.clean_roundtrip, [t.diff for t in result.roundtrip.values()]
    assert result.hunk_count == 0
    assert result.summary().endswith("round trip clean")


def test_only_the_imported_stages_end_up_in_the_recipe(
    shipped: dict[RenderTarget, str],
) -> None:
    result = ri.import_recipe(
        [
            ri.ImportSource(label="a.env", text=shipped[RenderTarget.SI_ENV]),
            ri.ImportSource(label="b.xml", text=shipped[RenderTarget.JIVARO_XML]),
        ],
        recipe_id="two-stages",
    )
    assert result.recipe.stages == [Stage.SI, Stage.JIVARO]


def test_one_quantus_file_selects_the_matching_output_kind(
    shipped: dict[RenderTarget, str],
) -> None:
    ext = ri.import_recipe(
        [ri.ImportSource(label="a.cmd", text=shipped[RenderTarget.QUANTUS_EXT])],
        recipe_id="ext-only",
    )
    dspf = ri.import_recipe(
        [ri.ImportSource(label="b.cmd", text=shipped[RenderTarget.QUANTUS_DSPF])],
        recipe_id="dspf-only",
    )
    assert ext.recipe.output.emit == [OutputKind.EXTRACTED_VIEW]
    assert dspf.recipe.output.emit == [OutputKind.DSPF]


def test_crlf_input_is_normalised_rather_than_reported_as_all_changed(
    shipped: dict[RenderTarget, str],
) -> None:
    """A file that went through Windows must not diff on every single line."""

    windows = shipped[RenderTarget.SI_ENV].replace("\n", "\r\n")
    result = ri.import_recipe(
        [ri.ImportSource(label="si.env", text=windows)], recipe_id="crlf"
    )
    assert result.sources[0].crlf is True
    assert result.hunk_count == 0


# ---- what lands in the recipe -------------------------------------------------


def test_values_the_catalog_models_land_in_the_recipe_fields(gui_export: Path) -> None:
    result = ri.import_recipe([gui_export], recipe_id="gui-export")
    extraction = result.recipe.extraction
    assert extraction.min_res_ohm == 0.005
    assert extraction.temperature_c == 85.0
    assert extraction.exclude_floating_nets_limit == 8000
    assert extraction.coupling_cap_threshold_absolute == 0.02
    assert extraction.coupling_cap_threshold_relative == 0.005


def test_every_mapped_value_says_which_file_and_line_it_came_from(
    shipped: dict[RenderTarget, str],
) -> None:
    result = ri.import_recipe(_sources(shipped), recipe_id="provenance")
    assert result.mapped
    for value in result.mapped:
        assert value.source in {f.label for f in result.sources}
        assert value.site.target in result.targets
        assert value.origin in ("literal", "variable")
        if value.applied_to is None:
            assert value.note, value.key
    applied = {value.key: value for value in result.mapped if value.applied_to}
    assert applied["min_res_ohm"].site.option == "-min_res"
    assert applied["min_res_ohm"].site.line == 29
    assert applied["min_res_ohm"].source == RenderTarget.QUANTUS_EXT.value


def test_a_composite_line_does_not_poison_the_row_it_shares(
    shipped: dict[RenderTarget, str],
) -> None:
    """``*lvsRulesFile`` is a directory, a basename, a variant and a pattern on
    one line. Reading it with the generic rule would set
    ``lvs.deck_variant = "/pdk/.../CFXXX.wodio.qcilvs"`` -- a value that is
    wrong rather than missing, which is the worst kind."""

    result = ri.import_recipe(
        [ri.ImportSource(label="lvs.qci", text=shipped[RenderTarget.LVS_QCI])],
        recipe_id="composite",
    )
    assert result.recipe.lvs.deck_variant == "wodio"
    literal = next(
        value
        for value in result.mapped
        if value.key == "lvs_deck_variant" and value.origin == "literal"
    )
    assert literal.applied_to is None
    assert "belongs to the whole line" in literal.note
    solved = next(
        value
        for value in result.mapped
        if value.key == "lvs_deck_variant" and value.origin == "variable"
    )
    assert solved.applied_to == "lvs.deck_variant"


def test_connect_by_name_is_recovered_from_the_line_being_there(
    tmp_path: Path,
) -> None:
    """``*cmnVConnectNamesState: ALL`` is a boolean spelled as a word inside an
    ``[% if %]``; neither reader can see it, so presence is the value."""

    on = _render_all(
        tmp_path,
        recipe=recipe_from_catalog(
            recipe_id="on", name="on", lvs={"connect_by_name": True}
        ),
    )
    off = _render_all(tmp_path)
    imported_on = ri.import_recipe(
        [ri.ImportSource(label="on.qci", text=on[RenderTarget.LVS_QCI])], recipe_id="cbn-on"
    )
    imported_off = ri.import_recipe(
        [ri.ImportSource(label="off.qci", text=off[RenderTarget.LVS_QCI])],
        recipe_id="cbn-off",
    )
    assert imported_on.recipe.lvs.connect_by_name is True
    assert imported_off.recipe.lvs.connect_by_name is False
    assert imported_on.hunk_count == 0
    assert imported_on.clean_roundtrip


def test_the_dut_is_read_out_of_the_files(shipped: dict[RenderTarget, str]) -> None:
    result = ri.import_recipe(_sources(shipped), recipe_id="dut")
    assert result.dut.cell == CELL
    assert result.dut.library == LIBRARY
    assert result.dut.out_file == "av_ext"
    assert result.dut.ground_net == "vss"


def test_env_references_are_solved_out_of_the_users_own_file(gui_export: Path) -> None:
    """``$env(SETUP_ROOT)/assura_tech.lib`` in the template against an absolute
    path in the export gives back SETUP_ROOT. Without it the machine's path
    would land in a patch, and a recipe carrying one is not portable."""

    result = ri.import_recipe([gui_export], recipe_id="env-solved")
    assert result.resolved_env["SETUP_ROOT"] == "/pdk/hn001/setup"
    assert result.profile.qrc.dir_expr == "/pdk/hn001/QCI_deck"
    assert result.profile.tech_name == "HN001"
    assert not any("SETUP_ROOT" in hunk.summary for hunk in result.as_patch)


def test_a_row_that_could_not_be_read_carries_a_reason(
    shipped: dict[RenderTarget, str],
) -> None:
    result = ri.import_recipe(_sources(shipped), recipe_id="unread")
    assert result.unread
    assert all(reason for reason in result.unread.values())


# ---- what becomes a patch ------------------------------------------------------


def _edit(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


def test_a_hand_edited_file_keeps_its_edits_as_hunks(
    shipped: dict[RenderTarget, str],
) -> None:
    """Three edits, three fates: a modelled knob becomes a Recipe field, an
    unmodelled line and an inserted line become hunks, and nothing is lost."""

    edited = _edit(
        shipped[RenderTarget.QUANTUS_EXT], "-min_res 0.001", "-min_res 0.002"
    )
    edited = _edit(
        edited,
        '              -run_name "Design" \\\n',
        '              -run_name "Design" \\\n              -extra_netlist "extra.sp" \\\n',
    )
    edited = _edit(
        edited,
        '-temporary_directory_name "Design"',
        '-temporary_directory_name "Scratch"',
    )

    result = ri.import_recipe(
        [ri.ImportSource(label="edited.cmd", text=edited)], recipe_id="edited"
    )
    assert result.recipe.extraction.min_res_ohm == 0.002
    assert result.hunk_count == 2
    stored = " ".join(hunk.summary for hunk in result.as_patch)
    assert "extra.sp" in stored
    assert "Scratch" in stored
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


def test_the_patch_re_applies_on_top_of_the_recipe(
    shipped: dict[RenderTarget, str],
) -> None:
    """The hunks are on the Recipe, so rendering it -- not the import -- is
    what has to put the edits back."""

    edited = _edit(
        shipped[RenderTarget.QUANTUS_EXT],
        '-temporary_directory_name "Design"',
        '-temporary_directory_name "Scratch"',
    )
    result = ri.import_recipe(
        [ri.ImportSource(label="edited.cmd", text=edited)], recipe_id="reapply"
    )
    assert result.recipe.patches
    assert result.recipe.manual_edit_count == 1
    again = result.rerender()[RenderTarget.QUANTUS_EXT]
    assert '-temporary_directory_name "Scratch"' in again
    assert again == edited


def test_a_value_the_template_used_to_freeze_now_reaches_the_recipe_field(
    shipped: dict[RenderTarget, str],
) -> None:
    """``-decoupling_factor`` was the example of a row only a hunk could carry.

    It is a ``[[var]]`` now, so the honest place for the user's 2.5 is the
    Recipe field itself -- no hunk, nothing masked, and the value is editable
    in the GUI afterwards. This test used to assert the opposite; the
    assertion moved rather than being deleted so the upgrade is on the record.
    """

    edited = _edit(
        shipped[RenderTarget.QUANTUS_EXT], "-decoupling_factor 1.0", "-decoupling_factor 2.5"
    )
    result = ri.import_recipe(
        [ri.ImportSource(label="edited.cmd", text=edited)], recipe_id="hardcoded"
    )
    assert result.recipe.extraction.decoupling_factor == 2.5
    assert result.hunk_count == 0
    assert "-decoupling_factor 2.5" in result.rerender()[RenderTarget.QUANTUS_EXT]
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


def test_a_value_the_template_still_hardcodes_is_kept_as_a_hunk(
    shipped: dict[RenderTarget, str],
) -> None:
    """The rules-file *pattern* is the last row the templates still freeze.

    ``calibre_lvs.qci.j2`` line 1 spells ``.qcilvs`` out, so a PDK that names
    its decks anything else cannot be described by a profile field and the
    difference has nowhere to go but a hunk. Nothing may be dropped on the
    floor: the value must survive the round trip even when no field can hold
    it. When that line is parameterised this test is the one that has to
    change, and :mod:`tests.catalog.test_catalog` says so in the same breath.
    """

    edited = _edit(shipped[RenderTarget.LVS_QCI], ".qcilvs", ".rules")
    result = ri.import_recipe(
        [ri.ImportSource(label="edited.qci", text=edited)], recipe_id="frozen"
    )
    assert result.hunk_count == 1
    assert [hunk.at_line for hunk in result.as_patch] == [1]
    assert ".rules" in result.rerender()[RenderTarget.LVS_QCI]
    assert result.roundtrip[RenderTarget.LVS_QCI].identical


def test_a_captured_hunk_follows_the_cell(shipped: dict[RenderTarget, str]) -> None:
    """The whole reason the patch format is masked.

    The user's file names ``INV1``; their added line names ``INV1``; the stored
    hunk must say ``${cell}`` and the recipe must render ``vco_core_extra.sp``
    for the next DUT. Freezing ``INV1`` here would not fail -- it would quietly
    extract the wrong netlist for every other cell, which is why this test is
    not optional.
    """

    edited = _edit(
        shipped[RenderTarget.QUANTUS_EXT],
        '              -run_name "Design" \\\n',
        f'              -run_name "Design" \\\n'
        f'              -extra_netlist "{CELL}_extra.sp" \\\n',
    )
    result = ri.import_recipe(
        [ri.ImportSource(label="edited.cmd", text=edited)], recipe_id="masked"
    )
    stored = result.recipe.patches[0].hunks[0].after
    assert "${cell}" in stored
    assert CELL not in stored

    other = DutSnapshot(
        library=LIBRARY,
        cell="vco_core",
        layout_view="layout",
        source_view="schematic",
        ground_net="vss",
        out_file="av_ext",
    )
    moved = result.rerender(dut=other)[RenderTarget.QUANTUS_EXT]
    assert '-extra_netlist "vco_core_extra.sp"' in moved
    assert "INV1" not in moved


def test_the_gui_export_needs_only_its_version_banner_as_a_hunk(
    gui_export: Path,
) -> None:
    """A file this tool never rendered: different Quantus version, different
    PDK, different cell, five different values. Everything but the banner
    comment is understood, and the banner is kept rather than dropped."""

    result = ri.import_recipe([gui_export], recipe_id="gui-export")
    assert result.hunk_count == 1
    assert "19.14-s012" in result.as_patch[0].summary
    assert result.unmodelled_ratio < 0.05
    assert not result.high_unmodelled
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


# ---- the invariant: landed OR patched, never both ------------------------------


def _hunk_lines(result: ri.RecipeImportResult) -> set[str]:
    """Every non-blank line any stored hunk carries, on either side."""

    return {
        line.strip()
        for patch in result.recipe.patches
        for hunk in patch.hunks
        for line in (hunk.before + hunk.after).splitlines()
        if line.strip()
    }


def _lines_behind(value: ri.MappedValue, text: str, catalog: Catalog) -> list[str]:
    """The user's own lines that carry ``value``.

    The directive's line, plus the one after it for the two Quantus options
    that put their value on the next line (``-technology_corner``,
    ``-temperature``) -- which is exactly where both halves of this bug lived.
    """

    if value.site.line is None:
        return []
    spec = catalog.target(value.site.target)
    site = next(
        (
            candidate
            for candidate in catalog.option(value.key).lands_in
            if candidate.target is value.site.target
            and candidate.section == value.site.section
            and candidate.option == value.site.option
        ),
        None,
    )
    next_line = (
        site is not None and site.render(spec).layout is Layout.VALUE_ON_NEXT_LINE
    )
    start = value.site.line - 1
    span = 2 if next_line else 1
    return [line for line in text.splitlines()[start : start + span] if line.strip()]


def test_a_value_that_landed_in_the_recipe_or_the_profile_is_never_also_pinned_by_a_patch(
    handwritten: Path,
) -> None:
    """The one rule that makes an imported recipe editable at all.

    A patch hunk stores a literal. A Recipe field stores a value the user can
    change. Store the same line as both and the patch wins every render: the
    field is on the screen, it accepts a new number, and the file keeps the
    old one. That is the "you can change it but it does nothing" failure the
    whole parameterisation round exists to remove, and it came back in through
    the importer -- ``-temperature 125`` read into
    ``recipe.extraction.temperature_c`` *and* pinned as a hunk, because the
    baseline rendered the same number as ``125.0``.

    So: every line the import claims to understand must be absent from every
    hunk. What is left in the patch is what the catalog does not model -- here
    a whole ``rf_analysis`` statement and the tool's own version banner, which
    is ``owner: fixed`` and belongs to no object at all.
    """

    catalog = builtin_catalog()
    text = handwritten.read_text(encoding="utf-8")
    result = ri.import_recipe([handwritten], recipe_id="invariant")

    landed = {value.key: value for value in result.mapped if value.applied_to}
    # Both halves of the bug are values that must be on the landed side.
    assert "temperature_c" in landed, "125 C was understood and then placed nowhere"
    assert "technology_corner" in landed, "RCWORST was understood and then placed nowhere"
    assert landed["temperature_c"].landed_in == "recipe.extraction.temperature_c"
    assert landed["technology_corner"].landed_in == (
        "profile.corners.rcworst.technology_corner"
    )

    pinned = _hunk_lines(result)
    for key, value in landed.items():
        for line in _lines_behind(value, text, catalog):
            assert line.strip() not in pinned, f"{key} is both a field and a patch: {line!r}"

    # ...and what is left in the patch is only what nothing models.
    assert result.hunk_count == 2
    summaries = [hunk.summary for hunk in result.as_patch]
    assert any("rf_analysis" in summary for summary in summaries)
    assert any("Quantus UI Version" in summary for summary in summaries)
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


def test_a_field_read_out_of_a_file_can_still_be_changed_afterwards(
    handwritten: Path,
) -> None:
    """The invariant above, stated as the thing the user notices.

    Import a file extracting at 125 C, then ask for 85 C. A patch pinning the
    literal would render 125 anyway; a live field renders 85.
    """

    result = ri.import_recipe([handwritten], recipe_id="editable")
    assert "\n              125\n" in result.rerender()[RenderTarget.QUANTUS_EXT]

    warmer = result.recipe.model_copy(deep=True)
    warmer.extraction.temperature_c = 85.0
    moved = replace(result, recipe=warmer).rerender()[RenderTarget.QUANTUS_EXT]
    assert "\n              85.0\n" in moved
    assert "125" not in moved


# ---- numbers keep the spelling the file used -----------------------------------


def test_a_number_is_rendered_back_with_the_spelling_the_file_used(
    handwritten: Path,
) -> None:
    """``125`` read back and written out is ``125``, not ``125.0``.

    One byte, and it decides whether the importer sees a difference where
    there is none. The value is still a plain float to everything that reads
    it -- the assertion below compares it to a number, not to a string.
    """

    result = ri.import_recipe([handwritten], recipe_id="spelling")
    assert result.recipe.extraction.temperature_c == 125.0
    assert f"{result.recipe.extraction.temperature_c}" == "125"

    rendered = result.rerender()[RenderTarget.QUANTUS_EXT]
    assert "              -temperature \\\n              125\n" in rendered
    assert rendered == handwritten.read_text(encoding="utf-8")


def test_a_number_nobody_spelled_still_renders_the_way_it_always_did(
    tmp_path: Path,
) -> None:
    """The other half of the symmetry, and the reason it cannot be a global rule.

    ``tests/catalog/test_byte_fidelity.py`` pins five files rendered from
    catalog defaults, and two of the numbers in them are integral: the shipped
    templates write ``-decoupling_factor 1.0`` and ``-temperature 55.0``.
    Trimming ``.0`` off every whole float would round-trip a hand-written
    ``125`` and move those two, so only a number that arrived *with* a
    spelling keeps one.
    """

    shipped = _render_all(tmp_path)[RenderTarget.QUANTUS_EXT]
    assert "              -decoupling_factor 1.0 \\\n" in shipped
    assert "              -temperature \\\n              55.0\n" in shipped


# ---- the corner becomes a profile fact, not a hunk ------------------------------


def test_a_corner_the_baseline_does_not_know_is_added_to_the_derived_profile(
    handwritten: Path,
) -> None:
    """``RCWORST`` is a PDK fact, and the derived profile is ours to teach.

    The recipe may only ever name a corner semantically -- that seam is what
    makes it portable -- so a literal the profile does not bind has nowhere to
    go but a patch, where it stops being a corner at all. Deriving the profile
    from the same files means the corner table can simply grow one entry, and
    the recipe selects it by name.
    """

    result = ri.import_recipe([handwritten], recipe_id="corner")

    assert result.derived_profile
    assert [(c.name, c.technology_corner) for c in result.profile.corners] == [
        ("rcworst", "RCWORST")
    ]
    assert result.profile.default_corner == "rcworst"
    assert result.recipe.extraction.corner == "rcworst"
    assert not any("RCWORST" in hunk.summary for hunk in result.as_patch)
    assert any("gained a corner" in warning for warning in result.warnings)

    # A corner is a name plus a literal, so swapping the profile swaps the
    # literal -- which a hunk holding "RCWORST" could never do.
    other = result.profile.model_copy(deep=True)
    other.corners = [
        CornerSpec(name="rcworst", technology_corner="RC_WORST_CASE")
    ]
    moved = result.rerender(profile=other)[RenderTarget.QUANTUS_EXT]
    assert '"RC_WORST_CASE"' in moved
    assert "RCWORST" not in moved


def test_a_profile_the_caller_supplied_is_never_extended_behind_their_back(
    handwritten: Path,
) -> None:
    """A real profile is a PDK fact somebody checked. One file does not amend it.

    The corner is missing, and the import says so twice -- in the warnings and
    on the row itself -- naming the corners the profile does have and what it
    costs to leave it alone. Nothing is silently added, and nothing is
    silently dropped either: the difference is still a hunk, so the file round
    trips.
    """

    supplied = make_profile()
    result = ri.import_recipe(
        [handwritten], recipe_id="supplied", profile=supplied, resolved_env=dict(ENV)
    )

    assert not result.derived_profile
    assert [c.technology_corner for c in result.profile.corners] == ["TYPICAL"]
    assert result.recipe.extraction.corner is None
    refused = next(value for value in result.mapped if value.key == "technology_corner")
    assert refused.applied_to is None
    assert "defines no corner with that -technology_corner" in refused.note
    assert any("Add it to the profile" in warning for warning in result.warnings)
    assert any("RCWORST" in hunk.summary for hunk in result.as_patch)
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


def test_a_corner_the_supplied_profile_does_bind_lands_in_the_recipe(
    handwritten: Path,
) -> None:
    """The other side of the same rule: a profile that knows RCWORST gets used.

    Nothing is added to the profile here either -- the corner was already
    there. What lands is the recipe's *choice* of it, which is the only half
    the recipe is allowed to hold.
    """

    knows = make_profile(
        corners=[
            CornerSpec(name="typical", technology_corner="TYPICAL"),
            CornerSpec(name="rc_worst", technology_corner="RCWORST"),
        ]
    )
    result = ri.import_recipe(
        [handwritten], recipe_id="knows", profile=knows, resolved_env=dict(ENV)
    )

    assert result.recipe.extraction.corner == "rc_worst"
    landed = next(value for value in result.mapped if value.key == "technology_corner")
    assert landed.landed_in == "recipe.extraction.corner"
    assert not any("RCWORST" in hunk.summary for hunk in result.as_patch)


@pytest.mark.parametrize(
    ("written", "line"),
    [
        ("1e-18", "              -min_res 1e-18 \\\n"),
        ("1E-18", "              -min_res 1E-18 \\\n"),
        ("0.001", "              -min_res 0.001 \\\n"),
        ("1e-3", "              -min_res 1e-3 \\\n"),
        (".5", "              -min_res .5 \\\n"),
        ("2", "              -min_res 2 \\\n"),
    ],
)
def test_every_way_of_writing_one_number_survives_the_round_trip(
    handwritten: Path, written: str, line: str
) -> None:
    """``1e-3`` and ``0.001`` are the same float and not the same file.

    Whichever the user typed is what comes back out, because the alternative
    is an import that reports a difference nobody made and stores it as a
    patch.
    """

    text = handwritten.read_text(encoding="utf-8").replace(
        "-min_res 0.005", f"-min_res {written}"
    )
    result = ri.import_recipe(
        [ri.ImportSource(label="numbers.cmd", text=text)], recipe_id="numbers"
    )

    assert result.recipe.extraction.min_res_ohm == float(written)
    assert line in result.rerender()[RenderTarget.QUANTUS_EXT]
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


# ---- the rest of the profile-owned rows ----------------------------------------


def test_a_parasitic_device_name_lands_in_the_profile_with_its_other_half(
    handwritten: Path,
) -> None:
    """``-res_component`` is a PDK fact too, and it does not travel alone.

    Quantus names the device and Jivaro binds a model of the same cell; the
    profile refuses to hold one without the other. Importing the quantus file
    on its own therefore moves both, which changes no rendered byte -- the
    file that would show the model is not part of this import -- and keeps the
    value out of a patch.
    """

    text = handwritten.read_text(encoding="utf-8").replace(
        '-res_component "presistor"', '-res_component "rppolywo"'
    )
    result = ri.import_recipe(
        [ri.ImportSource(label="devices.cmd", text=text)], recipe_id="devices"
    )

    assert result.profile.parasitics.res_component == "rppolywo"
    assert result.profile.parasitics.res_model == "analogLib/rppolywo/symbol"
    assert not any("rppolywo" in hunk.summary for hunk in result.as_patch)
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


def test_two_files_that_disagree_about_a_device_are_reported_not_reconciled(
    shipped: dict[RenderTarget, str],
) -> None:
    """The user's own broken contract is not the importer's to fix.

    Quantus writing ``rppolywo`` while Jivaro reads ``analogLib/presistor``
    means Jivaro extracts nothing. Guessing which half is right would hide it;
    both rows stay at the catalog default, the report says why, and the two
    differences stay visible as hunks.
    """

    edited = dict(shipped)
    edited[RenderTarget.QUANTUS_EXT] = _edit(
        shipped[RenderTarget.QUANTUS_EXT],
        '-res_component "presistor"',
        '-res_component "rppolywo"',
    )
    result = ri.import_recipe(_sources(edited), recipe_id="disagree")

    assert result.profile.parasitics.res_component == "presistor"
    assert any("do not form a contract" in warning for warning in result.warnings)
    assert any("rppolywo" in hunk.summary for hunk in result.as_patch)
    assert result.roundtrip[RenderTarget.QUANTUS_EXT].identical


# ---- honesty about how much was understood -------------------------------------


def test_a_file_far_from_the_catalog_warns_about_the_ratio(
    shipped: dict[RenderTarget, str],
) -> None:
    """A runset from a much older Calibre: the lines we know are still there,
    but half the file is options this catalog has never heard of. Importing it
    is allowed; believing the import without looking is not."""

    lines = shipped[RenderTarget.LVS_QCI].splitlines()
    legacy = "\n".join(
        [lines[0], *[f"*lvsLegacyOption{i}: value{i}" for i in range(40)], *lines[1:]]
    ) + "\n"

    result = ri.import_recipe(
        [ri.ImportSource(label="legacy.qci", text=legacy)], recipe_id="legacy"
    )
    assert result.high_unmodelled
    assert result.unmodelled_ratio > 0.25
    assert any("manual edits" in warning for warning in result.warnings)
    assert any("fork" in warning for warning in result.warnings)
    # Still lossless: the forty unknown lines are in the patch, not gone.
    assert result.roundtrip[RenderTarget.LVS_QCI].identical
    assert "*lvsLegacyOption39: value39" in result.rerender()[RenderTarget.LVS_QCI]


def test_the_warn_ratio_is_the_callers_to_set(shipped: dict[RenderTarget, str]) -> None:
    # auCdlDefNetlistProc is owner=fixed: no recipe field can hold it, so an
    # edit to it is unmodelled by construction and stays that way as more rows
    # are parameterised. A row the catalog does model would simply be read into
    # the recipe and leave nothing for the ratio to measure.
    edited = _edit(
        shipped[RenderTarget.SI_ENV],
        'auCdlDefNetlistProc = "ansCdlSubcktCall"',
        'auCdlDefNetlistProc = "hnlPrintSubcktCall"',
    )
    strict = ri.import_recipe(
        [ri.ImportSource(label="si.env", text=edited)], recipe_id="strict", warn_ratio=0.0
    )
    lax = ri.import_recipe(
        [ri.ImportSource(label="si.env", text=edited)], recipe_id="lax", warn_ratio=0.9
    )
    assert strict.high_unmodelled and not lax.high_unmodelled
    assert strict.unmodelled_ratio == lax.unmodelled_ratio


def test_the_round_trip_reports_a_difference_instead_of_hiding_it(
    shipped: dict[RenderTarget, str],
) -> None:
    """Whitespace-only edits are deliberately dropped by ``capture_patch`` (a
    stored hunk that only adds a trailing space would block the stage forever).
    That is a real, if tiny, loss, and the round trip is what surfaces it."""

    trailing = shipped[RenderTarget.SI_ENV].replace(
        'simSimulator = "auCdl"', 'simSimulator = "auCdl"   '
    )
    result = ri.import_recipe(
        [ri.ImportSource(label="si.env", text=trailing)], recipe_id="whitespace"
    )
    trip = result.roundtrip[RenderTarget.SI_ENV]
    assert not trip.identical
    assert not result.clean_roundtrip
    assert "simSimulator" in trip.diff
    assert any("does not reproduce" in warning for warning in result.warnings)


# ---- the baseline objects --------------------------------------------------------


def test_a_supplied_profile_is_used_instead_of_a_derived_one(
    shipped: dict[RenderTarget, str],
) -> None:
    result = ri.import_recipe(
        _sources(shipped), recipe_id="given-profile", profile=make_profile(), dut=make_dut(
            cell=CELL, library=LIBRARY
        ),
        resolved_env=ENV,
    )
    assert result.derived_profile is False
    assert result.profile.profile_id == "hn001"
    assert result.hunk_count == 0
    assert result.clean_roundtrip


def test_the_derived_profile_says_it_is_not_a_discovered_pdk(
    shipped: dict[RenderTarget, str],
) -> None:
    result = ri.import_recipe(_sources(shipped), recipe_id="derived")
    assert result.derived_profile is True
    assert result.profile.hand_edited is True
    assert result.profile.discovered_from == ["auto_ext.core.recipe_import"]
    assert "Not a discovered PDK" in (result.profile.description or "")


# ---- dry run and writing ----------------------------------------------------------


def test_import_writes_nothing(tmp_path: Path, shipped: dict[RenderTarget, str]) -> None:
    before = sorted(p.name for p in tmp_path.iterdir())
    ri.import_recipe(_sources(shipped), recipe_id="dry-run")
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_write_imported_recipe_saves_a_loadable_recipe(
    tmp_path: Path, shipped: dict[RenderTarget, str]
) -> None:
    result = ri.import_recipe(_sources(shipped), recipe_id="saved-one")
    path = ri.write_imported_recipe(result, tmp_path / "recipes")
    assert path.name == "saved-one.yaml"
    assert load_recipe(path).content_sha256() == result.recipe.content_sha256()


def test_writing_twice_refuses_unless_asked(
    tmp_path: Path, shipped: dict[RenderTarget, str]
) -> None:
    result = ri.import_recipe(_sources(shipped), recipe_id="saved-twice")
    ri.write_imported_recipe(result, tmp_path)
    with pytest.raises(ri.RecipeImportError, match="already exists"):
        ri.write_imported_recipe(result, tmp_path)
    assert ri.write_imported_recipe(result, tmp_path, overwrite=True).exists()


def test_a_path_that_cannot_be_read_says_so(tmp_path: Path) -> None:
    with pytest.raises(ri.RecipeImportError, match="cannot read"):
        ri.import_recipe([tmp_path / "nope.cmd"], recipe_id="missing")


def test_no_files_at_all_is_an_error() -> None:
    with pytest.raises(ri.RecipeImportError, match="no files"):
        ri.import_recipe([], recipe_id="empty")


# ---- the variable solver ------------------------------------------------------------


def test_the_solver_splits_a_line_that_carries_three_values(
    shipped: dict[RenderTarget, str], templates_root: Path, catalog: Catalog
) -> None:
    """``inputView value="[[library]]/[[cell]]/[[out_file]]"`` is one line and
    three catalog rows; the template is the only thing that knows where the
    boundaries are."""

    template = (templates_root / "jivaro" / "default.xml.j2").read_text(encoding="utf-8")
    solved = ri.solve_template_vars(
        template,
        shipped[RenderTarget.JIVARO_XML],
        target=RenderTarget.JIVARO_XML,
        catalog=catalog,
    )
    assert solved.values["var:library"] == LIBRARY
    assert solved.values["var:cell"] == CELL
    assert solved.values["var:out_file"] == "av_ext"
    assert solved.conflicts == []


def test_the_solver_reports_a_file_that_disagrees_with_itself(
    shipped: dict[RenderTarget, str], templates_root: Path, catalog: Catalog
) -> None:
    """``si.env`` names the cell twice. A file where the two differ is the
    user's business, but taking one silently would put the wrong cell into the
    baseline and from there into every captured hunk."""

    template = (templates_root / "si" / "default.env.j2").read_text(encoding="utf-8")
    mangled = shipped[RenderTarget.SI_ENV].replace(
        f'hnlNetlistFileName = "{CELL}.src.net"', 'hnlNetlistFileName = "OTHER.src.net"'
    )
    solved = ri.solve_template_vars(
        template, mangled, target=RenderTarget.SI_ENV, catalog=catalog
    )
    assert solved.values["var:cell"] == CELL
    assert any("var:cell" in conflict for conflict in solved.conflicts)


def test_a_landing_site_with_no_hole_is_left_to_the_literal_reader(
    shipped: dict[RenderTarget, str], templates_root: Path, catalog: Catalog
) -> None:
    """Two shapes the solver must decline, for two different reasons.

    ``auCdlDefNetlistProc`` is a plain literal: there is no hole, so there is
    nothing to solve. ``preserveRES`` is the harder one -- its slot holds a
    Jinja *expression*, ``[[ "'t" if preserve_res else "'nil" ]]``, and an
    expression has no single value to read back. Treating it as a hole reads
    the expression's first word as the variable name, which is how sixteen
    si.env booleans briefly became sixteen readings of a variable called
    ``'t``. Both shapes belong to the literal reader, which knows the row's
    type and can turn ``'t`` back into ``True``.
    """

    template = (templates_root / "si" / "default.env.j2").read_text(encoding="utf-8")
    solved = ri.solve_template_vars(
        template, shipped[RenderTarget.SI_ENV], target=RenderTarget.SI_ENV, catalog=catalog
    )
    assert "var:netlist_short_res" not in solved.values
    assert not [name for name in solved.values if name.startswith("var:'")]
    assert "var:auCdlDefNetlistProc" not in solved.values
    assert "var:preserve_res" not in solved.values
    assert solved.conflicts == []
    # ...and the value is not lost: the literal reader recovers it, typed.
    result = ri.import_recipe(
        [ri.ImportSource(label="si.env", text=shipped[RenderTarget.SI_ENV])],
        recipe_id="literal-reader",
    )
    assert result.recipe.netlist.preserve_res is True


def test_score_targets_ranks_the_right_file_first(
    shipped: dict[RenderTarget, str], catalog: Catalog
) -> None:
    quantus = {RenderTarget.QUANTUS_EXT, RenderTarget.QUANTUS_DSPF}
    for target, text in shipped.items():
        scores = ri.score_targets(text, catalog=catalog)
        # The two quantus forms share most of their sites, which is exactly why
        # detect_target does not decide between them on the score.
        if target in quantus:
            assert scores[0].target in quantus
        else:
            assert scores[0].target is target
        assert scores[0].coverage > ri.MIN_SITE_COVERAGE
        others = [s for s in scores[1:] if s.target is not target and s.target not in quantus]
        assert all(s.hits == 0 for s in others), [(s.target.value, s.hits) for s in others]


# ---- the read-back module the importer shares with the migration -----------------


def test_the_parsers_record_the_line_a_directive_was_found_on(
    shipped: dict[RenderTarget, str],
) -> None:
    """Provenance for the report. The Quantus layouts that put the value on the
    next line record the *option's* line, which is the one a user looks for."""

    skill = parse_skill(shipped[RenderTarget.SI_ENV])
    assert skill[("", "simLibName")].line == 1
    quantus = parse_quantus(shipped[RenderTarget.QUANTUS_EXT])
    assert quantus[("process_technology", "-technology_corner")].line == 59
    assert quantus[("process_technology", "-technology_corner")].values == ("TYPICAL",)
    jivaro = parse_xml(shipped[RenderTarget.JIVARO_XML])
    assert jivaro[("", "inputView")].line == 2


def test_composite_sites_names_every_shared_line(catalog: Catalog) -> None:
    shared = composite_sites(catalog)
    assert (RenderTarget.LVS_QCI, "lvs_rules", "*lvsRulesFile") in shared
    assert (RenderTarget.JIVARO_XML, "general", "inputView") in shared
    assert (RenderTarget.QUANTUS_EXT, "input_db", "-design_cell_name") in shared
    # A line only one row lands on must not be in there, or every value the
    # importer reads would be refused as ambiguous.
    assert (RenderTarget.SI_ENV, "identity", "simCellName") not in shared
    for keys in shared.values():
        assert len(keys) > 1


def test_the_migration_still_gets_the_same_read_back() -> None:
    """The read-back moved into ``core/readback.py`` for the importer to share.
    ``auto_ext.migrate`` re-exports it, and its own tests pin the values; this
    only pins that the two spellings are the same function."""

    from auto_ext import migrate
    from auto_ext.core import readback

    assert migrate._parse_quantus is readback.parse_quantus
    assert migrate._parse_skill is readback.parse_skill
    assert migrate._parse_calibre is readback.parse_calibre
    assert migrate._parse_xml is readback.parse_xml
    assert migrate.TemplateReadBack is readback.TemplateReadBack


def test_the_catalog_default_owner_set_is_unchanged() -> None:
    from auto_ext.catalog import Owner
    from auto_ext.core.readback import DEFAULT_READBACK_OWNERS

    assert DEFAULT_READBACK_OWNERS == (Owner.RECIPE, Owner.PROFILE, Owner.RESOURCES)


def test_an_unknown_syntax_is_refused_by_name() -> None:
    from auto_ext.core.readback import ReadBackError, parse_by_syntax

    with pytest.raises(ReadBackError, match="verilog"):
        parse_by_syntax("verilog", "module top; endmodule\n")


def test_builtin_catalog_is_the_default_everywhere(
    shipped: dict[RenderTarget, str],
) -> None:
    """Passing the catalog explicitly and letting it default must agree, or the
    importer has two configurations and only one of them is tested."""

    with_default = ri.import_recipe(_sources(shipped), recipe_id="default-cat")
    with_explicit = ri.import_recipe(
        _sources(shipped), recipe_id="default-cat", catalog=builtin_catalog()
    )
    assert with_default.recipe.content_sha256() == with_explicit.recipe.content_sha256()
