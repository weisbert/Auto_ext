"""CLI surface for ``recipe import`` and ``recipe export``.

The engine is tested in ``tests/core/test_recipe_import.py``; nothing here
re-checks what it recovers. These tests are about the two things a command
adds on top of a library call, and both are things a user gets burned by:

**The report cannot lose a value.** ``recipe import`` prints four sections --
read into the recipe, kept as a manual edit, left at the catalog default,
warnings -- and a value that appears in none of them has vanished silently.
The section a value lands in is asserted here, and so is the rule that no
value appears in two of them at once (``lvs_connect_by_name`` used to, because
the literal reader cannot read it and the presence reader can).

**Nothing is written until it is asked for.** The default is a dry run, an
existing file is never clobbered without ``--force``, and the exit code says
whether a human should look: 1 with warnings, 0 without, 2 when the files
cannot become a recipe at all.

For ``recipe export`` the property is round-tripping: what comes out is a
recipe file the receiving side can load, patches intact, carrying the catalog
version its hunks were captured against -- and re-exporting an export replaces
that header rather than stacking a second one on top of it.

Inputs are real. Every sample file is the *shipped* template rendered through
``auto_ext.core.render``, so it is byte-for-byte what a run of this tool puts
in front of a user, plus ``tests/fixtures/raw/gui_export.ext.cmd``, which this
tool never produced.

No assertion depends on a Rich glyph: the Windows console is GBK and a box
character comes back as a question mark there. Words, ids and numbers only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app
from auto_ext.model.common import RenderTarget

runner = CliRunner()

CELL = "INV1"
LIBRARY = "INV_LIB"

#: Sample file names on disk. Deliberately not the target ids: the target is
#: decided by content, and a fixture named after the answer could not prove it.
FILENAMES = {
    RenderTarget.SI_ENV: "si.env",
    RenderTarget.LVS_QCI: "run.qci",
    RenderTarget.QUANTUS_EXT: "extract.cmd",
    RenderTarget.QUANTUS_DSPF: "netlist.cmd",
    RenderTarget.JIVARO_XML: "reduce.xml",
}


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the width Rich renders these tables at.

    Rich reads ``COLUMNS`` when stdout is not a terminal and a captured stream
    reports 80. At 80 the four-column tables fold a cell over several lines and
    the neighbouring columns land between the halves, so a key like
    ``coupling_cap_threshold_absolute`` stops being a substring of the output.
    """

    monkeypatch.setenv("COLUMNS", "220")
    monkeypatch.setenv("LINES", "80")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``~`` and ``$AUTO_EXT_RECIPES`` somewhere empty.

    ``recipe_search_path`` reads both, and its last entry is where an import
    writes. Without this a developer's own ``~/.auto_ext/recipes`` would be the
    write target of every ``--write`` below.
    """

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AUTO_EXT_RECIPES", raising=False)
    return home


def squashed(text: str) -> str:
    """``text`` with whitespace and table rules removed.

    A long path or a folded key is only a substring of the output once the
    fold is taken back out.
    """

    return re.sub(r"[\s|+─-╿]+", "", text)


def assert_in_output(needle: str, result) -> None:
    assert squashed(needle) in squashed(result.output), result.output


def assert_not_in_output(needle: str, result) -> None:
    assert squashed(needle) not in squashed(result.output), result.output


# ---- the sample files --------------------------------------------------------


@pytest.fixture(scope="session")
def generated(tmp_path_factory: pytest.TempPathFactory) -> dict[RenderTarget, str]:
    """What this tool writes today for every target, as the user would see it."""

    from auto_ext.core import render
    from auto_ext.model.recipe import OutputKind, recipe_from_catalog
    from tests.support.v2 import ENV, make_dut, make_profile, make_run

    work = tmp_path_factory.mktemp("generated")
    recipe = recipe_from_catalog(
        recipe_id="shipped",
        name="shipped",
        output={"emit": [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]},
    )
    profile = make_profile()
    context = render.build_context(
        dut=make_dut(cell=CELL, library=LIBRARY),
        recipe=recipe,
        profile=profile,
        run=make_run(work),
        resolved_env=ENV,
    )
    return {
        plan.target: render.render_one(
            plan,
            context=context,
            recipe=recipe,
            profile=profile,
            resolved_env=ENV,
            out_dir=work / "rendered",
            write=False,
        ).text
        for plan in render.plan_targets(recipe)
    }


@pytest.fixture
def samples(tmp_path: Path, generated: dict[RenderTarget, str]) -> dict[RenderTarget, Path]:
    """The five generated files on disk, under names that hide their target."""

    directory = tmp_path / "mine"
    directory.mkdir()
    written: dict[RenderTarget, Path] = {}
    for target, text in generated.items():
        path = directory / FILENAMES[target]
        path.write_text(text, encoding="utf-8", newline="\n")
        written[target] = path
    return written


@pytest.fixture
def ext_root(tmp_path: Path) -> Path:
    """An Auto_ext root with an empty ``recipes/`` and ``config/profiles/``."""

    root = tmp_path / "root"
    (root / "recipes").mkdir(parents=True)
    (root / "config" / "profiles").mkdir(parents=True)
    return root


@pytest.fixture
def gui_export(fixtures_dir: Path) -> Path:
    """A Quantus ext.cmd this tool did not render: other version banner, other
    PDK paths, other cell, changed values."""

    return fixtures_dir / "raw" / "gui_export.ext.cmd"


def _import(*args: str, expect: int | None = None):
    result = runner.invoke(app, ["recipe", "import", *args])
    if expect is not None:
        assert result.exit_code == expect, result.output
    return result


# ---- import: the happy path --------------------------------------------------


def test_the_five_generated_files_import_and_round_trip(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """The whole flow's own output, read back. Anything less than a byte-exact
    round trip here would mean the import cannot even understand this tool."""

    result = _import(
        *[str(p) for p in samples.values()], "--auto-ext-root", str(ext_root), expect=0
    )
    assert "every target re-renders byte for byte" in result.output
    for target in samples:
        assert target.value in result.output
    assert "0 hunk(s)" in result.output
    assert "nothing: the catalog explains every line of every file." in result.output


def test_the_dry_run_writes_nothing(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    _import(*[str(p) for p in samples.values()], "--auto-ext-root", str(ext_root), expect=0)
    assert list((ext_root / "recipes").glob("*.yaml")) == []


def test_the_dry_run_names_the_file_it_would_have_written(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        "--as",
        "would-be",
        "--auto-ext-root",
        str(ext_root),
    )
    assert "nothing written; re-run with --write" in result.output
    assert_in_output(str(ext_root / "recipes" / "would-be.yaml"), result)


def test_write_saves_a_recipe_that_loads_back(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    from auto_ext.model.recipe import load_recipe

    _import(
        *[str(p) for p in samples.values()],
        "--as",
        "inv1-rc",
        "--write",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    path = ext_root / "recipes" / "inv1-rc.yaml"
    recipe = load_recipe(path)
    assert recipe.recipe_id == "inv1-rc"
    assert [stage.value for stage in recipe.stages] == ["si", "calibre", "quantus", "jivaro"]


def test_write_refuses_to_clobber_without_force(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    args = [
        str(samples[RenderTarget.QUANTUS_EXT]),
        "--as",
        "twice",
        "--write",
        "--auto-ext-root",
        str(ext_root),
    ]
    _import(*args, expect=0)
    again = _import(*args, expect=2)
    assert "already exists" in again.output
    assert "--force" in again.output
    _import(*args, "--force", expect=0)


def test_recipes_dir_wins_over_the_search_path(
    samples: dict[RenderTarget, Path], ext_root: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "shared"
    _import(
        str(samples[RenderTarget.JIVARO_XML]),
        "--as",
        "over-there",
        "--write",
        "--auto-ext-root",
        str(ext_root),
        "--recipes-dir",
        str(elsewhere),
        expect=0,
    )
    assert (elsewhere / "over-there.yaml").is_file()
    assert list((ext_root / "recipes").glob("*.yaml")) == []


# ---- import: naming ----------------------------------------------------------


def test_without_as_the_recipe_is_named_after_the_cell(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """The name comes out of the file's content, not its path: these files are
    about INV1 and that is what the user will look for in `recipe list`."""

    result = _import(
        str(samples[RenderTarget.QUANTUS_EXT]),
        "--write",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert (ext_root / "recipes" / "imported-inv1.yaml").is_file()
    assert "imported-inv1" in result.output


def test_a_file_with_no_cell_in_it_is_named_after_its_target(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """A dspf.cmd carries no cell name at all, so guessing one would be a lie;
    the target is at least true."""

    _import(
        str(samples[RenderTarget.QUANTUS_DSPF]),
        "--write",
        "--auto-ext-root",
        str(ext_root),
    )
    assert (ext_root / "recipes" / "imported-quantus-dspf-cmd.yaml").is_file()


def test_as_and_name_set_the_id_and_the_display_name(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    from auto_ext.model.recipe import load_recipe

    _import(
        str(samples[RenderTarget.SI_ENV]),
        "--as",
        "netlist-only",
        "--name",
        "Netlist only, for the digital block",
        "--write",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    recipe = load_recipe(ext_root / "recipes" / "netlist-only.yaml")
    assert recipe.recipe_id == "netlist-only"
    assert recipe.name == "Netlist only, for the digital block"


def test_an_id_that_is_not_a_slug_is_refused(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        "--as",
        "Not A Slug",
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "import failed" in result.output


# ---- import: the four sections ----------------------------------------------


def test_a_value_read_into_the_recipe_is_shown_with_the_field_it_landed_in(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.QUANTUS_EXT]), "--auto-ext-root", str(ext_root), expect=0
    )
    assert "Read into the recipe" in result.output
    assert_in_output("min_res_ohm", result)
    assert_in_output("extraction.min_res_ohm", result)
    assert_in_output("quantus.ext.cmd: filter_res -min_res line", result)


def test_a_value_the_recipe_cannot_hold_is_shown_where_it_actually_went(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """Two fates for a value with no Recipe field, and the report names both.

    ``technology_library_file`` is the PDK's value, not the recipe's -- and it
    is not lost for that: the derived PdkProfile takes it, and the first
    section says which field. Reporting it as "left at the catalog default"
    was the sentence that used to hide the corner bug, where a value the
    importer *did* understand was quietly frozen into a patch instead.

    ``qrc_deck_dir`` is the one this file genuinely cannot place: it shares a
    physical line with the preserve-cell-list name, so what the literal reader
    found there belongs to the line rather than to the row. That row stands
    against the catalog default with the reason attached, which is what the
    third section is for.
    """

    result = _import(
        str(samples[RenderTarget.QUANTUS_EXT]), "--auto-ext-root", str(ext_root), expect=0
    )
    assert "Read into the recipe or the profile" in result.output
    assert_in_output("technology_library_file", result)
    assert_in_output("profile.tech_library_file", result)

    assert "Left at the catalog default" in result.output
    assert_in_output("qrc_deck_dir", result)
    assert_in_output("belongs to the whole line, not to this row", result)
    assert_not_in_output("the template writes this as a literal", result)


def test_a_row_the_template_really_does_freeze_still_says_so(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """The other reason has not gone away, and must not be reported as the
    profile's fault. ``lvs_rules_filename_pattern`` is the one row the shipped
    templates still spell out, and it is what the sentence above was written
    for."""

    result = _import(
        str(samples[RenderTarget.LVS_QCI]), "--auto-ext-root", str(ext_root), expect=0
    )
    assert_in_output("lvs_rules_filename_pattern", result)


def test_a_value_that_is_already_the_default_is_counted_not_listed(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    quiet = _import(
        str(samples[RenderTarget.QUANTUS_EXT]), "--auto-ext-root", str(ext_root), expect=0
    )
    assert "further value(s) were read and already match the catalog default" in quiet.output
    assert_not_in_output("netlist_simulator", quiet)

    loud = _import(
        str(samples[RenderTarget.QUANTUS_EXT]),
        "--show-defaults",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    # ``output_form`` and not ``extract_type``: the two describes_member rows
    # are reported once, by the reader that owns the whole ordered list, and
    # no longer a second time as a scalar showing only the last statement.
    assert_in_output("output_form", loud)
    assert "further value(s) were read and already match" not in loud.output


def test_no_value_is_reported_in_two_sections_at_once(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """``lvs_connect_by_name`` is the awkward one: the literal reader cannot
    read it (it is a bool spelled as the presence of a line), so it is in
    ``unread``, while the presence reader does read it and puts it in the
    recipe. Printing both rows would tell the user two contradictory things."""

    result = _import(
        str(samples[RenderTarget.LVS_QCI]), "--auto-ext-root", str(ext_root), expect=0
    )
    assert squashed(result.output).count("lvs_connect_by_name") == 1
    assert_in_output("lvs.connect_by_name", result)
    assert_not_in_output("*cmnVConnectNamesState not found in the file", result)


def test_a_key_that_cannot_be_read_is_listed_with_the_reason(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """Both quantus files answer ``output_form`` differently and one Recipe
    field cannot hold both. Not an error, but not silence either."""

    result = _import(
        str(samples[RenderTarget.QUANTUS_EXT]),
        str(samples[RenderTarget.QUANTUS_DSPF]),
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert_in_output("output_form", result)
    assert_in_output("(not readable)", result)
    assert_in_output("one Recipe field cannot hold both", result)


def test_what_the_catalog_does_not_model_becomes_a_reported_hunk(
    gui_export: Path, ext_root: Path
) -> None:
    """A file this tool never wrote carries its own version banner. That line
    belongs to no catalog row, so it has to survive as a manual edit."""

    result = _import(str(gui_export), "--auto-ext-root", str(ext_root), expect=0)
    assert "Kept as manual edits (1 hunk(s))" in result.output
    assert_in_output("quantus.ext.cmd", result)
    assert_in_output("Version 19.14-s012", result)
    assert "-1/+1" in result.output


def test_the_values_a_hand_written_file_changed_reach_the_recipe(
    gui_export: Path, ext_root: Path
) -> None:
    from auto_ext.model.recipe import load_recipe

    _import(
        str(gui_export), "--as", "dco", "--write", "--auto-ext-root", str(ext_root), expect=0
    )
    recipe = load_recipe(ext_root / "recipes" / "dco.yaml")
    assert recipe.extraction.min_res_ohm == 0.005
    assert recipe.extraction.temperature_c == 85.0
    assert recipe.manual_edit_count == 1


def test_a_patch_too_big_to_be_a_patch_warns_and_exits_one(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    """Read an ext.cmd as if it were a dspf.cmd and a third of it becomes a
    manual edit. The command must not call that a success."""

    result = _import(
        str(samples[RenderTarget.QUANTUS_EXT]),
        "--target",
        "quantus.dspf.cmd",
        "--as",
        "wrong-way-round",
        "--auto-ext-root",
        str(ext_root),
        expect=1,
    )
    assert "Warnings" in result.output
    assert "had to be kept as manual edits" in result.output
    assert "a patch that large is a fork" in result.output


def test_the_warning_threshold_is_settable(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    quiet = _import(
        str(samples[RenderTarget.QUANTUS_EXT]),
        "--target",
        "quantus.dspf.cmd",
        "--warn-ratio",
        "0.9",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "a patch that large is a fork" not in quiet.output


# ---- import: which file is which target -------------------------------------


def test_the_target_comes_from_the_content_not_the_file_name(
    samples: dict[RenderTarget, Path], ext_root: Path, tmp_path: Path
) -> None:
    """The GUI writes whatever the dialog said, so the name is a lie waiting to
    happen; running an extracted_view command file as a dspf run wastes an
    afternoon before anything looks wrong."""

    lying = tmp_path / "definitely_the_dspf_one.cmd"
    lying.write_text(
        samples[RenderTarget.QUANTUS_EXT].read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    result = _import(str(lying), "--auto-ext-root", str(ext_root), expect=0)
    assert "quantus.ext.cmd" in result.output
    assert "content" in result.output
    assert_not_in_output("quantus.dspf.cmd", result)


def test_target_forces_the_answer_and_the_report_says_so(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.QUANTUS_EXT]),
        "--target",
        "quantus.dspf.cmd",
        "--auto-ext-root",
        str(ext_root),
    )
    assert "--target" in result.output
    assert "quantus.dspf.cmd" in result.output


def test_target_binds_to_one_file_by_its_bare_name(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        str(samples[RenderTarget.QUANTUS_EXT]),
        "--target",
        f"{FILENAMES[RenderTarget.QUANTUS_EXT]}=quantus.ext.cmd",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "quantus.ext.cmd" in result.output
    assert "--target" in result.output


def test_target_binds_to_one_file_by_its_full_path(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    path = samples[RenderTarget.LVS_QCI]
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        str(path),
        "--target",
        f"{path}=lvs.qci",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "lvs.qci" in result.output


def test_a_bare_target_needs_a_file_when_there_are_several(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        str(samples[RenderTarget.LVS_QCI]),
        "--target",
        "lvs.qci",
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "with more than one file" in result.output
    assert "--target <file>=lvs.qci" in result.output


def test_an_unknown_target_lists_the_five(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        "--target",
        "quantus.cmd",
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "unknown --target 'quantus.cmd'" in result.output
    for target in RenderTarget:
        assert target.value in result.output


def test_a_target_naming_no_input_file_lists_the_inputs(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        "--target",
        "somewhere_else.qci=lvs.qci",
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "no input file matches" in result.output
    assert_in_output(str(samples[RenderTarget.SI_ENV]), result)


def test_an_unrecognisable_file_is_an_error_that_shows_its_workings(
    tmp_path: Path, ext_root: Path
) -> None:
    """Guessing here would produce an empty recipe that looks like a success."""

    mystery = tmp_path / "notes.txt"
    mystery.write_text("just some notes\nabout the block\n", encoding="utf-8")
    result = _import(str(mystery), "--auto-ext-root", str(ext_root), expect=2)
    assert "does not look like any file this tool generates" in result.output
    for target in RenderTarget:
        assert target.value in result.output


def test_two_files_of_the_same_target_is_an_error(
    samples: dict[RenderTarget, Path], ext_root: Path, tmp_path: Path
) -> None:
    twin = tmp_path / "copy.cmd"
    twin.write_text(
        samples[RenderTarget.QUANTUS_EXT].read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    result = _import(
        str(samples[RenderTarget.QUANTUS_EXT]),
        str(twin),
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "one import produces at most one file per target" in result.output


# ---- import: the argument surface -------------------------------------------


def test_no_file_at_all_says_how_to_name_one(ext_root: Path) -> None:
    result = _import("--auto-ext-root", str(ext_root), expect=2)
    assert "name at least one file to import" in result.output


def test_a_missing_file_is_refused_before_anything_else(ext_root: Path, tmp_path: Path) -> None:
    result = _import(str(tmp_path / "nope.cmd"), "--auto-ext-root", str(ext_root))
    assert result.exit_code == 2
    assert "nope.cmd" in result.output


def test_the_file_flag_is_interchangeable_with_the_positional_form(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        "--file",
        str(samples[RenderTarget.LVS_QCI]),
        "-f",
        str(samples[RenderTarget.JIVARO_XML]),
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "files" in result.output
    for target in (RenderTarget.SI_ENV, RenderTarget.LVS_QCI, RenderTarget.JIVARO_XML):
        assert target.value in result.output


def test_a_crlf_file_is_read_as_lf_and_the_report_says_which(
    generated: dict[RenderTarget, str], ext_root: Path, tmp_path: Path
) -> None:
    """A file that came off a Windows share must not report every line as
    changed; the report has to admit the newline was normalised."""

    windows = tmp_path / "from_windows.qci"
    windows.write_bytes(
        generated[RenderTarget.LVS_QCI].replace("\n", "\r\n").encode("utf-8")
    )
    result = _import(str(windows), "--auto-ext-root", str(ext_root), expect=0)
    assert "CRLF" in result.output
    assert "0 hunk(s)" in result.output


@pytest.fixture
def real_profile(ext_root: Path) -> Path:
    """A PdkProfile on disk, shaped like the one office scanning produces."""

    from auto_ext.core.profile_discover import write_profile_yaml
    from tests.support.v2 import make_profile

    path = ext_root / "config" / "profiles" / "hn001.yaml"
    write_profile_yaml(path, make_profile())
    return path


def test_importing_against_a_real_profile_says_so(
    samples: dict[RenderTarget, Path], ext_root: Path, real_profile: Path
) -> None:
    """Without ``--profile`` the baseline is a scaffold the importer invented,
    and the report must not let that be mistaken for a discovered PDK."""

    result = _import(
        str(samples[RenderTarget.LVS_QCI]),
        "--profile",
        "hn001",
        "--env",
        "VERIFY_ROOT=/w/fake/verify",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "hn001" in result.output
    assert "derived from the files" not in result.output
    assert "the baseline profile was derived from these files" not in result.output
    assert "VERIFY_ROOT" in result.output


def test_an_env_var_only_the_profile_needs_comes_from_the_shell(
    samples: dict[RenderTarget, Path],
    ext_root: Path,
    real_profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``VERIFY_ROOT`` appears in the profile's qrc path expression and in no
    template, so no imported file can answer it; the shell is the only source
    left, and on the office server it is set."""

    monkeypatch.setenv("VERIFY_ROOT", "/w/fake/verify")
    result = _import(
        str(samples[RenderTarget.LVS_QCI]),
        "--profile",
        "hn001",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "VERIFY_ROOT" in result.output


def test_a_var_the_templates_reference_is_not_taken_from_the_shell(
    samples: dict[RenderTarget, Path],
    ext_root: Path,
    real_profile: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SETUP_ROOT`` is solved out of the user's own file. Preferring this
    machine's value would put this machine's paths into the baseline, and from
    there into a stored hunk that travels with the recipe."""

    monkeypatch.setenv("SETUP_ROOT", "/somewhere/else/entirely")
    monkeypatch.setenv("calibre_source_added_place", "/somewhere/else/entirely/empty.cdl")
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        "--profile",
        "hn001",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "0 hunk(s)" in result.output
    assert_not_in_output("/somewhere/else/entirely", result)


def test_a_missing_env_var_says_how_to_bind_it(
    samples: dict[RenderTarget, Path], ext_root: Path, real_profile: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.LVS_QCI]),
        "--profile",
        "hn001",
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "VERIFY_ROOT" in result.output
    assert "--env NAME=VALUE" in result.output


def test_env_needs_a_value(samples: dict[RenderTarget, Path], ext_root: Path) -> None:
    result = _import(
        str(samples[RenderTarget.SI_ENV]),
        "--env",
        "SETUP_ROOT",
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "expected NAME=VALUE" in result.output


def test_without_a_profile_the_scaffold_is_labelled_a_scaffold(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.LVS_QCI]), "--auto-ext-root", str(ext_root), expect=0
    )
    assert "derived from the files" in result.output
    assert "not discovered" in result.output


def test_an_unknown_profile_exits_two(
    samples: dict[RenderTarget, Path], ext_root: Path
) -> None:
    result = _import(
        str(samples[RenderTarget.LVS_QCI]),
        "--profile",
        "nosuch",
        "--auto-ext-root",
        str(ext_root),
        expect=2,
    )
    assert "no profile at" in result.output


def test_an_imported_recipe_joins_the_library(gui_export: Path, ext_root: Path) -> None:
    """The end of the journey: what the import wrote is an ordinary recipe, so
    the commands that already exist can see it and its manual edits."""

    _import(
        str(gui_export), "--as", "dco", "--write", "--auto-ext-root", str(ext_root), expect=0
    )
    listed = runner.invoke(app, ["recipe", "list", "--auto-ext-root", str(ext_root)])
    assert listed.exit_code == 0, listed.output
    assert "dco" in listed.output

    shown = runner.invoke(app, ["recipe", "show", "dco", "--auto-ext-root", str(ext_root)])
    assert shown.exit_code == 0, shown.output
    assert_in_output("extraction.min_res_ohm", shown)

    patches = runner.invoke(app, ["patch", "list", "dco", "--auto-ext-root", str(ext_root)])
    assert patches.exit_code == 0, patches.output
    assert "quantus" in patches.output


# ---- export ------------------------------------------------------------------


def _export(*args: str, expect: int | None = None):
    result = runner.invoke(app, ["recipe", "export", *args])
    if expect is not None:
        assert result.exit_code == expect, result.output
    return result


@pytest.fixture
def imported(samples: dict[RenderTarget, Path], ext_root: Path) -> Path:
    """One recipe on disk, imported from the five files, with one patch."""

    runner.invoke(
        app,
        [
            "recipe",
            "import",
            *[str(p) for p in samples.values()],
            "--as",
            "shareable",
            "--write",
            "--auto-ext-root",
            str(ext_root),
        ],
    )
    path = ext_root / "recipes" / "shareable.yaml"
    assert path.is_file()
    return path


def test_export_to_stdout_is_a_recipe_the_other_side_can_load(
    imported: Path, ext_root: Path, tmp_path: Path
) -> None:
    from auto_ext.model.recipe import load_recipe

    result = _export("shareable", "--auto-ext-root", str(ext_root), expect=0)
    landed = tmp_path / "colleague" / "shareable.yaml"
    landed.parent.mkdir()
    landed.write_text(result.stdout, encoding="utf-8")
    assert load_recipe(landed).recipe_id == "shareable"


def test_export_to_stdout_keeps_its_notes_off_stdout(imported: Path, ext_root: Path) -> None:
    """``recipe export foo > foo.yaml`` has to produce a clean file."""

    result = _export("shareable", "--auto-ext-root", str(ext_root), expect=0)
    assert "exported shareable from" in result.stderr
    assert "exported shareable from" not in result.stdout


def test_the_export_header_carries_the_catalog_version(
    imported: Path, ext_root: Path
) -> None:
    from auto_ext.catalog import builtin_catalog

    result = _export("shareable", "--auto-ext-root", str(ext_root), expect=0)
    header = result.stdout.splitlines()
    assert header[0].startswith("# --- auto-ext recipe export")
    assert "# format: auto-ext/recipe-export/1" in header
    assert f"# catalog-version: {builtin_catalog().catalog_version}" in header
    assert "# recipe: shareable" in header


def test_the_export_says_a_recipe_needs_no_pdk_to_travel(
    imported: Path, ext_root: Path
) -> None:
    """The reason the export is just the file: a Recipe is PDK-independent, so
    there is nothing to inline alongside it."""

    result = _export("shareable", "--auto-ext-root", str(ext_root), expect=0)
    assert "PDK-independent" in result.stdout
    assert "recipes/ directory" in result.stdout


def test_the_export_admits_what_masking_does_not_cover(
    imported: Path, ext_root: Path
) -> None:
    """The header must not promise a scrubbed file. Masking covers the landing
    sites the catalog models; a hunk captured from a line it does not model
    keeps that line, absolute paths included, and somebody about to send this
    to another company needs to be told so."""

    result = _export("shareable", "--auto-ext-root", str(ext_root), expect=0)
    assert "does NOT model keeps" in result.stdout
    assert "patch show" in result.stdout


def test_export_writes_where_out_points(imported: Path, ext_root: Path, tmp_path: Path) -> None:
    destination = tmp_path / "outbox" / "for-wang.yaml"
    result = _export(
        "shareable", "--out", str(destination), "--auto-ext-root", str(ext_root), expect=0
    )
    assert destination.is_file()
    assert "wrote" in result.output
    assert "catalog version" in result.output


def test_out_may_be_a_directory(imported: Path, ext_root: Path, tmp_path: Path) -> None:
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    _export("shareable", "--out", str(outbox), "--auto-ext-root", str(ext_root), expect=0)
    assert (outbox / "shareable.yaml").is_file()


def test_export_refuses_to_clobber_without_force(
    imported: Path, ext_root: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "for-wang.yaml"
    destination.write_text("do not lose me\n", encoding="utf-8")
    refused = _export(
        "shareable", "--out", str(destination), "--auto-ext-root", str(ext_root), expect=2
    )
    assert "already exists" in refused.output
    assert destination.read_text(encoding="utf-8") == "do not lose me\n"
    _export(
        "shareable",
        "--out",
        str(destination),
        "--force",
        "--auto-ext-root",
        str(ext_root),
        expect=0,
    )
    assert "recipe_id: shareable" in destination.read_text(encoding="utf-8")


def test_the_patches_travel_with_the_recipe(
    gui_export: Path, ext_root: Path, tmp_path: Path
) -> None:
    """The point of the export: a colleague gets the manual edits too, and they
    are still masked, so nothing about this cell is frozen into them."""

    from auto_ext.model.recipe import load_recipe

    runner.invoke(
        app,
        [
            "recipe",
            "import",
            str(gui_export),
            "--as",
            "dco",
            "--write",
            "--auto-ext-root",
            str(ext_root),
        ],
    )
    destination = tmp_path / "dco.yaml"
    _export("dco", "--out", str(destination), "--auto-ext-root", str(ext_root), expect=0)

    landed = load_recipe(destination)
    assert landed.manual_edit_count == 1
    assert landed.patches[0].hunks[0].after.strip()


def test_re_exporting_an_export_replaces_the_header(
    imported: Path, ext_root: Path, tmp_path: Path
) -> None:
    """Otherwise a file passed between three people grows a header per hop."""

    once = tmp_path / "once.yaml"
    _export("shareable", "--out", str(once), "--auto-ext-root", str(ext_root), expect=0)
    twice = tmp_path / "twice.yaml"
    _export(str(once), "--out", str(twice), expect=0)

    text = twice.read_text(encoding="utf-8")
    assert text.count("# --- auto-ext recipe export") == 1
    assert text.count("# format: auto-ext/recipe-export/1") == 1
    assert [line for line in text.splitlines() if not line.startswith("#")][0] == (
        "schema_version: 1"
    )


def test_export_notes_hunks_captured_against_another_catalog(
    ext_root: Path, tmp_path: Path
) -> None:
    """The whole reason the version is in the header: a hunk anchored to a line
    the local catalog no longer renders will not apply, and the receiving side
    should find that out before a run rather than during one."""

    from datetime import datetime, timezone

    from auto_ext.core.patch_models import BaseFingerprint, PatchHunk, TemplatePatch
    from auto_ext.model.recipe import recipe_from_catalog, save_recipe

    recipe = recipe_from_catalog(recipe_id="ancient", name="ancient")
    recipe.patches = [
        TemplatePatch(
            stage="quantus",
            template_id="quantus/ext.cmd.j2",
            base=BaseFingerprint(
                template_sha256="a" * 64,
                masked_sha256="b" * 64,
                catalog_version="2019.01.01",
                profile_id="hn001",
                captured_at=datetime(2019, 1, 1, tzinfo=timezone.utc),
            ),
            hunks=[
                PatchHunk(
                    id="0123abcd",
                    intent="keep the extra netlist",
                    before=" -min_res 0.001\n",
                    after=' -min_res 0.001\n -extra_netlist "${cell}_extra.sp"\n',
                    recorded_start=28,
                )
            ],
        )
    ]
    save_recipe(recipe, ext_root / "recipes" / "ancient.yaml")

    destination = tmp_path / "ancient.yaml"
    result = _export(
        "ancient", "--out", str(destination), "--auto-ext-root", str(ext_root), expect=0
    )
    assert "2019.01.01" in result.output
    assert "# patch-catalog-versions: 2019.01.01" in destination.read_text(encoding="utf-8")


def test_export_of_an_unknown_recipe_lists_where_it_looked(ext_root: Path) -> None:
    result = _export("nosuch", "--auto-ext-root", str(ext_root), expect=2)
    assert "no recipe named 'nosuch'" in result.output
    assert_in_output(str(ext_root / "recipes"), result)


def test_export_accepts_a_path_instead_of_an_id(imported: Path, tmp_path: Path) -> None:
    destination = tmp_path / "by-path.yaml"
    _export(str(imported), "--out", str(destination), expect=0)
    assert "recipe_id: shareable" in destination.read_text(encoding="utf-8")
