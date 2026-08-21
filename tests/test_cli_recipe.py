"""CLI surface for the four C1 contracts: recipe, profile, catalog, patch.

What these tests pin down, beyond "the command runs":

* the recipe search path and its shadowing order, because a recipe that
  silently resolves to the wrong copy is indistinguishable from a wrong
  recipe;
* that ``recipe set`` validates the *whole* result before writing, so a
  rejected assignment cannot leave a half-edited file behind;
* that ``profile health`` maps its verdict onto the exit code the way a
  setup script needs (optional check failing is a warning, not a blocker);
* that ``catalog list``'s filters actually narrow, since the listing is the
  only place a user ever sees the 177 built-in options;
* that ``patch drop`` needs ``--yes`` and removes the patch record along with
  its last hunk.

No assertion depends on a Rich glyph: the Windows console is GBK and a box
character or a tick mark comes back as a question mark there. Words and ids
only.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the console width Rich renders these tables at.

    Rich reads ``COLUMNS`` when stdout is not a terminal, and a captured
    stream reports 80. At 80 a table folds a long cell over two lines and the
    *other* columns of the row land in between the halves, so a key like
    ``coupling_cap_threshold_absolute`` is no longer a substring of the output
    at all. Fixing the width keeps these assertions about the data rather than
    about the terminal; nothing here asserts on a box glyph either, because a
    GBK console renders those as question marks.
    """

    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("LINES", "50")


def squashed(text: str) -> str:
    """``text`` with whitespace and table rules removed.

    For the few assertions on a full temp path, which is long enough to fold
    even at 200 columns.
    """

    return re.sub(r"[\s|+─-╿]+", "", text)


def assert_in_output(needle: str, result) -> None:
    """Assert ``needle`` is in the output, folding-insensitively."""

    assert squashed(needle) in squashed(result.output), result.output


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``~`` and ``$AUTO_EXT_RECIPES`` somewhere empty.

    ``recipe_search_path`` reads both. Without this a recipe the developer
    happens to have in ``~/.auto_ext/recipes`` would join every listing, and
    the shadowing assertions below would depend on the host.
    """

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AUTO_EXT_RECIPES", raising=False)
    return home


@pytest.fixture
def ext_root(tmp_path: Path, isolated_home: Path) -> Path:
    """An Auto_ext root with an empty ``recipes/`` and ``config/profiles/``."""

    root = tmp_path / "root"
    (root / "recipes").mkdir(parents=True)
    (root / "config" / "profiles").mkdir(parents=True)
    return root


def _new_recipe(ext_root: Path, name: str, *extra: str) -> None:
    result = runner.invoke(
        app, ["recipe", "new", name, "--auto-ext-root", str(ext_root), *extra]
    )
    assert result.exit_code == 0, result.output


def _write_profile(ext_root: Path, profile_id: str, **fields) -> Path:
    from auto_ext.core.profile_discover import write_profile_yaml
    from auto_ext.model.pdk import PdkProfile

    payload = {
        "profile_id": profile_id,
        "display_name": fields.pop("display_name", profile_id.upper()),
        **fields,
    }
    path = ext_root / "config" / "profiles" / f"{profile_id}.yaml"
    write_profile_yaml(path, PdkProfile(**payload))
    return path


def _patched_recipe(ext_root: Path, recipe_id: str = "patched"):
    """A recipe carrying one two-hunk patch on the quantus ext.cmd file."""

    from auto_ext.core.patch_models import BaseFingerprint, PatchHunk, TemplatePatch
    from auto_ext.model.recipe import recipe_from_catalog, save_recipe

    patch = TemplatePatch(
        stage="quantus",
        template_id="quantus/ext.cmd.j2",
        base=BaseFingerprint(
            template_sha256="a" * 64,
            masked_sha256="b" * 64,
            catalog_version="2026.08.21",
            profile_id="hn001",
            captured_at=datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc),
        ),
        hunks=[
            PatchHunk(
                id="0123abcd",
                intent="keep the extra netlist",
                before=' -min_res 0.001\n',
                after=' -min_res 0.001\n -extra_netlist "${cell}_extra.sp"\n',
                context_before=" filter_res \\\n",
                context_after=" -merge_parallel_res \\\n",
                captured_values={"cell": "amp2"},
                recorded_start=28,
            ),
            PatchHunk(
                id="beef0001",
                enabled=False,
                intent="disabled experiment",
                before=" -exclude_self_cap \\\n",
                after=" -exclude_self_cap true \\\n",
                context_before=" filter_cap \\\n",
                context_after=" -exclude_floating_nets \\\n",
                recorded_start=21,
            ),
        ],
    )
    recipe = recipe_from_catalog(recipe_id=recipe_id, name=recipe_id)
    recipe.patches = [patch]
    path = ext_root / "recipes" / f"{recipe_id}.yaml"
    save_recipe(recipe, path)
    return path


# ---- recipe -----------------------------------------------------------------


def test_recipe_new_writes_the_catalog_defaults(ext_root: Path) -> None:
    """A fresh recipe reproduces what the shipped templates already emit."""

    from auto_ext.model.recipe import load_recipe, recipe_from_catalog

    result = runner.invoke(
        app,
        ["recipe", "new", "rc-typical", "--auto-ext-root", str(ext_root),
         "--name", "RC coupled"],
    )
    assert result.exit_code == 0, result.output
    path = ext_root / "recipes" / "rc-typical.yaml"
    assert path.is_file()

    written = load_recipe(path)
    baseline = recipe_from_catalog()
    assert written.recipe_id == "rc-typical"
    assert written.name == "RC coupled"
    assert written.extraction.temperature_c == baseline.extraction.temperature_c
    assert written.netlist.model_dump() == baseline.netlist.model_dump()


def test_recipe_new_refuses_to_clobber_without_force(ext_root: Path) -> None:
    _new_recipe(ext_root, "rc-typical")
    again = runner.invoke(
        app, ["recipe", "new", "rc-typical", "--auto-ext-root", str(ext_root)]
    )
    assert again.exit_code == 2
    assert "--force" in again.output

    forced = runner.invoke(
        app, ["recipe", "new", "rc-typical", "--auto-ext-root", str(ext_root), "--force"]
    )
    assert forced.exit_code == 0, forced.output


def test_recipe_new_from_records_the_lineage(ext_root: Path) -> None:
    from auto_ext.model.recipe import load_recipe

    _new_recipe(ext_root, "base")
    runner.invoke(
        app,
        ["recipe", "set", "base", "extraction.min_res_ohm=0.02",
         "--auto-ext-root", str(ext_root)],
    )
    result = runner.invoke(
        app,
        ["recipe", "new", "child", "--from", "base", "--auto-ext-root", str(ext_root)],
    )
    assert result.exit_code == 0, result.output

    child = load_recipe(ext_root / "recipes" / "child.yaml")
    assert child.derived_from == "base"
    assert child.extraction.min_res_ohm == 0.02


def test_recipe_new_rejects_an_id_that_is_not_a_slug(ext_root: Path) -> None:
    """``recipe_id`` becomes a file name, so it has to survive as one."""

    result = runner.invoke(
        app, ["recipe", "new", "Not A Slug", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 2
    assert "cannot create recipe" in result.output


def test_recipe_list_reports_the_search_path_when_empty(ext_root: Path) -> None:
    result = runner.invoke(app, ["recipe", "list", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "no recipe on the search path" in result.output
    assert "recipe new" in result.output


def test_recipe_list_shows_every_recipe(ext_root: Path) -> None:
    _new_recipe(ext_root, "one")
    _new_recipe(ext_root, "two")
    result = runner.invoke(app, ["recipe", "list", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "one" in result.output
    assert "two" in result.output
    assert "2 recipe(s)" in result.output


def test_recipe_list_marks_a_shadowed_copy(ext_root: Path, tmp_path: Path) -> None:
    """The later directory wins and the listing says how many it hides."""

    _new_recipe(ext_root, "shared")
    other = tmp_path / "team-recipes"
    other.mkdir()
    _new_recipe(ext_root, "shared", "--recipes-dir", str(other), "--force")

    result = runner.invoke(
        app,
        ["recipe", "list", "--auto-ext-root", str(ext_root), "--recipes-dir", str(other)],
    )
    assert result.exit_code == 0, result.output
    assert "(+1)" in result.output
    assert "shadowed" in result.output


def test_recipe_show_marks_what_differs_from_the_catalog(ext_root: Path) -> None:
    _new_recipe(ext_root, "rc")
    runner.invoke(
        app,
        ["recipe", "set", "rc", "extraction.min_res_ohm=0.5", "--auto-ext-root", str(ext_root)],
    )
    result = runner.invoke(
        app, ["recipe", "show", "rc", "--auto-ext-root", str(ext_root), "--changed"]
    )
    assert result.exit_code == 0, result.output
    assert_in_output("extraction.min_res_ohm", result)
    assert "0.5" in result.output
    # The catalog default has to be visible next to it, or "differs" is
    # information the user cannot act on.
    assert "0.001" in result.output


def test_recipe_show_yaml_prints_the_file(ext_root: Path) -> None:
    _new_recipe(ext_root, "rc")
    result = runner.invoke(
        app, ["recipe", "show", "rc", "--auto-ext-root", str(ext_root), "--yaml"]
    )
    assert result.exit_code == 0, result.output
    assert "recipe_id: rc" in result.output


def test_recipe_show_unknown_name_lists_the_directories_it_looked_in(
    ext_root: Path,
) -> None:
    result = runner.invoke(
        app, ["recipe", "show", "nope", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 2
    assert "no recipe named 'nope'" in result.output
    assert_in_output(str(ext_root / "recipes"), result)


def test_recipe_set_coerces_yaml_scalars(ext_root: Path) -> None:
    from auto_ext.model.recipe import load_recipe

    _new_recipe(ext_root, "rc")
    result = runner.invoke(
        app,
        [
            "recipe", "set", "rc",
            "extraction.exclude_floating_nets_limit=9000",
            "lvs.connect_by_name=true",
            "netlist.view_list=[auCdl, schematic, veriloga]",
            "--auto-ext-root", str(ext_root),
        ],
    )
    assert result.exit_code == 0, result.output

    recipe = load_recipe(ext_root / "recipes" / "rc.yaml")
    assert recipe.extraction.exclude_floating_nets_limit == 9000
    assert recipe.lvs.connect_by_name is True
    assert recipe.netlist.view_list == ["auCdl", "schematic", "veriloga"]


def test_recipe_set_is_all_or_nothing(ext_root: Path) -> None:
    """A rejected assignment must not leave the earlier ones on disk."""

    from auto_ext.model.recipe import load_recipe

    _new_recipe(ext_root, "rc")
    before = (ext_root / "recipes" / "rc.yaml").read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "recipe", "set", "rc",
            "extraction.min_res_ohm=0.5",
            "extraction.exclude_floating_nets_limit=not-a-number",
            "--auto-ext-root", str(ext_root),
        ],
    )
    assert result.exit_code == 2
    assert "not a valid recipe" in result.output
    assert (ext_root / "recipes" / "rc.yaml").read_text(encoding="utf-8") == before
    assert load_recipe(ext_root / "recipes" / "rc.yaml").extraction.min_res_ohm == 0.001


def test_recipe_set_rejects_an_unknown_field_with_a_suggestion(ext_root: Path) -> None:
    _new_recipe(ext_root, "rc")
    result = runner.invoke(
        app,
        ["recipe", "set", "rc", "extraction.min_res=1", "--auto-ext-root", str(ext_root)],
    )
    assert result.exit_code == 2
    assert "unknown recipe field" in result.output
    assert_in_output("extraction.min_res_ohm", result)


def test_recipe_set_needs_an_equals_sign(ext_root: Path) -> None:
    _new_recipe(ext_root, "rc")
    result = runner.invoke(
        app, ["recipe", "set", "rc", "extraction.min_res_ohm", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 2
    assert "missing '='" in result.output


def test_recipe_set_dry_run_writes_nothing(ext_root: Path) -> None:
    _new_recipe(ext_root, "rc")
    before = (ext_root / "recipes" / "rc.yaml").read_text(encoding="utf-8")
    result = runner.invoke(
        app,
        ["recipe", "set", "rc", "extraction.min_res_ohm=0.5", "--dry-run",
         "--auto-ext-root", str(ext_root)],
    )
    assert result.exit_code == 0, result.output
    assert "0.001 -> 0.5" in result.output
    assert (ext_root / "recipes" / "rc.yaml").read_text(encoding="utf-8") == before


def test_recipe_set_keeps_the_comments_in_the_file(ext_root: Path) -> None:
    """ruamel round-trip: a commented recipe survives an edit."""

    _new_recipe(ext_root, "rc")
    path = ext_root / "recipes" / "rc.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text("# team default, do not delete\n" + text, encoding="utf-8")

    result = runner.invoke(
        app,
        ["recipe", "set", "rc", "extraction.min_res_ohm=0.5", "--auto-ext-root", str(ext_root)],
    )
    assert result.exit_code == 0, result.output
    assert "# team default, do not delete" in path.read_text(encoding="utf-8")


def test_recipe_commands_accept_a_path_instead_of_an_id(
    ext_root: Path, tmp_path: Path
) -> None:
    _new_recipe(ext_root, "rc")
    loose = tmp_path / "loose.yaml"
    loose.write_text(
        (ext_root / "recipes" / "rc.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = runner.invoke(app, ["recipe", "show", str(loose), "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "Recipe rc" in result.output


def test_recipe_search_path_order_is_the_documented_one(
    ext_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """$AUTO_EXT_RECIPES first, --recipes-dir last; later shadows earlier."""

    from auto_ext.cli import recipe_search_path

    env_dir = tmp_path / "env-recipes"
    cli_dir = tmp_path / "cli-recipes"
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    monkeypatch.setenv("AUTO_EXT_RECIPES", str(env_dir))

    path = recipe_search_path(ext_root, config_dir, cli_dir)
    assert path[0] == env_dir.resolve()
    assert path[-1] == cli_dir.resolve()
    assert (ext_root / "recipes").resolve() in path
    assert (config_dir / "recipes").resolve() in path


# ---- profile ----------------------------------------------------------------


def test_profile_list_is_empty_without_a_profile(ext_root: Path) -> None:
    result = runner.invoke(app, ["profile", "list", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "no profile under" in result.output
    assert "profile discover" in result.output


def test_profile_show_renders_the_tables(ext_root: Path) -> None:
    _write_profile(
        ext_root,
        "hn001",
        display_name="HN001 22nm",
        tech_name="HN001",
        corners=[{"name": "typical", "technology_corner": "TYPICAL"}],
        default_corner="typical",
        power_names=["vdd", "vdda"],
        ground_names=["vss"],
    )
    result = runner.invoke(app, ["profile", "show", "hn001", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert_in_output("HN001 22nm", result)
    assert "typical" in result.output
    assert "TYPICAL" in result.output
    assert "vdda" in result.output


def test_profile_show_selects_the_only_profile_without_being_told(
    ext_root: Path,
) -> None:
    _write_profile(ext_root, "hn001")
    result = runner.invoke(app, ["profile", "show", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "Profile hn001" in result.output


def test_profile_show_refuses_to_guess_between_two(ext_root: Path) -> None:
    _write_profile(ext_root, "hn001")
    _write_profile(ext_root, "hn002")
    result = runner.invoke(app, ["profile", "show", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 2
    assert "name a profile" in result.output
    assert "hn002" in result.output


def test_profile_show_yaml_prints_the_file(ext_root: Path) -> None:
    _write_profile(ext_root, "hn001")
    result = runner.invoke(
        app, ["profile", "show", "hn001", "--auto-ext-root", str(ext_root), "--yaml"]
    )
    assert result.exit_code == 0, result.output
    assert "profile_id: hn001" in result.output


def test_profile_discover_reports_gaps_and_writes_nothing_by_default(
    ext_root: Path, clean_env: pytest.MonkeyPatch
) -> None:
    result = runner.invoke(
        app,
        ["profile", "discover", "--profile-id", "hn001", "--no-filesystem",
         "--auto-ext-root", str(ext_root)],
    )
    # Gaps in an empty shell, so exit 1 and no file.
    assert result.exit_code == 1, result.output
    assert "Gaps" in result.output
    assert "nothing written" in result.output
    assert not (ext_root / "config" / "profiles" / "hn001.yaml").exists()


def test_profile_discover_write_creates_a_loadable_profile(
    ext_root: Path, clean_env: pytest.MonkeyPatch
) -> None:
    from auto_ext.core.profile_discover import read_profile_yaml

    result = runner.invoke(
        app,
        ["profile", "discover", "--profile-id", "hn001", "--no-filesystem", "--write",
         "--auto-ext-root", str(ext_root)],
    )
    assert result.exit_code == 1, result.output  # written, but with gaps
    path = ext_root / "config" / "profiles" / "hn001.yaml"
    assert path.is_file()
    assert read_profile_yaml(path).profile_id == "hn001"


def test_profile_discover_will_not_overwrite_without_force(
    ext_root: Path, clean_env: pytest.MonkeyPatch
) -> None:
    _write_profile(ext_root, "hn001", description="hand written")
    result = runner.invoke(
        app,
        ["profile", "discover", "--profile-id", "hn001", "--no-filesystem", "--write",
         "--auto-ext-root", str(ext_root)],
    )
    assert result.exit_code == 2
    assert "--force" in result.output
    text = (ext_root / "config" / "profiles" / "hn001.yaml").read_text(encoding="utf-8")
    assert "hand written" in text


def test_profile_health_exit_code_follows_the_verdict(ext_root: Path) -> None:
    _write_profile(ext_root, "hn001")
    result = runner.invoke(app, ["profile", "health", "hn001", "--auto-ext-root", str(ext_root)])
    # A hollow profile has empty corner and variant tables, so required
    # checks fail and a run must not start.
    assert result.exit_code == 1, result.output
    assert "fail" in result.output
    assert "How to fix" in result.output


def test_profile_health_all_ok_exits_zero(
    ext_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A profile whose every check passes returns 0 and says a run can start."""

    from auto_ext.core import health

    deck = tmp_path / "deck"
    deck.mkdir()
    (deck / "CFXXX.wodio.qcilvs").write_text("rules\n", encoding="utf-8")
    qrc = tmp_path / "qrc"
    qrc.mkdir()
    (qrc / "query_cmd").write_text("cmd\n", encoding="utf-8")
    (qrc / "preserveCellList.txt").write_text("\n", encoding="utf-8")
    tech = tmp_path / "assura_tech.lib"
    tech.write_text("lib\n", encoding="utf-8")
    layers = tmp_path / "layers.map"
    layers.write_text("map\n", encoding="utf-8")
    cdl = tmp_path / "prelude.cdl"
    cdl.write_text("*\n", encoding="utf-8")

    _write_profile(
        ext_root,
        "hn001",
        tech_name="HN001",
        tech_library_file=str(tech),
        layer_map=str(layers),
        cdl_include_files=[str(cdl)],
        lvs_decks={
            "dir_expr": str(deck),
            "basename": "CFXXX",
            "variants": [{"name": "wodio", "rules_suffix": "wodio"}],
            "default_variant": "wodio",
        },
        qrc={"dir_expr": str(qrc)},
        corners=[{"name": "typical", "technology_corner": "TYPICAL"}],
        default_corner="typical",
        power_names=["vdd"],
        ground_names=["vss"],
    )
    # Every EDA binary "found", without running one: a real invocation costs a
    # licence checkout, which is why health.py takes an injectable `which`.
    monkeypatch.setattr(health.shutil, "which", lambda name: f"/opt/eda/bin/{name}")

    result = runner.invoke(app, ["profile", "health", "hn001", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "can start a run" in result.output


def test_profile_health_json_is_a_parsable_report(ext_root: Path) -> None:
    _write_profile(ext_root, "hn001")
    result = runner.invoke(
        app, ["profile", "health", "hn001", "--json", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["profile_id"] == "hn001"
    assert payload["results"]


def test_profile_health_plain_uses_the_shared_formatter(ext_root: Path) -> None:
    _write_profile(ext_root, "hn001")
    result = runner.invoke(
        app, ["profile", "health", "hn001", "--plain", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 1
    assert "[FAIL]" in result.output


def test_profile_health_refreshes_the_cache_file(ext_root: Path) -> None:
    _write_profile(ext_root, "hn001")
    cache = ext_root / "config" / "profiles" / "hn001.health.json"
    assert not cache.exists()
    runner.invoke(app, ["profile", "health", "hn001", "--auto-ext-root", str(ext_root)])
    assert cache.is_file()
    assert json.loads(cache.read_text(encoding="utf-8"))["profile_id"] == "hn001"


def test_profile_health_unknown_id_names_what_exists(ext_root: Path) -> None:
    _write_profile(ext_root, "hn001")
    result = runner.invoke(app, ["profile", "health", "nope", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 2
    assert "no profile at" in result.output
    assert_in_output("hn001", result)


# ---- catalog ----------------------------------------------------------------


def test_catalog_list_shows_the_whole_table_by_default() -> None:
    from auto_ext.catalog import builtin_catalog

    catalog = builtin_catalog()
    result = runner.invoke(app, ["catalog", "list", "--limit", "0"])
    assert result.exit_code == 0, result.output
    assert f"{len(catalog.options)} in" in result.output
    assert "catalog show" in result.output


def test_catalog_list_owner_filter_narrows() -> None:
    from auto_ext.catalog import Owner, builtin_catalog

    expected = len(builtin_catalog().by_owner(Owner.PROFILE))
    result = runner.invoke(app, ["catalog", "list", "--owner", "profile", "--limit", "0"])
    assert result.exit_code == 0, result.output
    assert f"of {expected} matching" in result.output


def test_catalog_list_stage_filter_narrows() -> None:
    from auto_ext.catalog import builtin_catalog

    expected = len(builtin_catalog().by_stage("jivaro"))
    result = runner.invoke(app, ["catalog", "list", "--stage", "jivaro", "--limit", "0"])
    assert result.exit_code == 0, result.output
    assert f"of {expected} matching" in result.output


def test_catalog_list_knobs_today_finds_exactly_the_seven() -> None:
    """The seven manifest knobs are the only values changeable without an edit."""

    from auto_ext.catalog import builtin_catalog

    knobs = builtin_catalog().knobs_today()
    assert len(knobs) == 7
    result = runner.invoke(app, ["catalog", "list", "--knobs-today", "--limit", "0"])
    assert result.exit_code == 0, result.output
    assert "of 7 matching" in result.output
    for option in knobs:
        assert_in_output(option.key, result)


def test_catalog_list_search_matches_the_landing_option_name() -> None:
    result = runner.invoke(app, ["catalog", "list", "--search", "technology_corner", "--limit", "0"])
    assert result.exit_code == 0, result.output
    assert "technology_corner" in result.output


def test_catalog_list_rejects_an_unknown_owner() -> None:
    result = runner.invoke(app, ["catalog", "list", "--owner", "nobody"])
    assert result.exit_code == 2
    assert "unknown owner" in result.output
    assert "recipe" in result.output


def test_catalog_list_section_needs_a_target() -> None:
    result = runner.invoke(app, ["catalog", "list", "--section", "filter_res"])
    assert result.exit_code == 2
    assert "--section needs --target" in result.output


def test_catalog_list_reports_an_empty_match_without_failing() -> None:
    result = runner.invoke(app, ["catalog", "list", "--search", "zzzz-no-such-option"])
    assert result.exit_code == 0, result.output
    assert "no catalog option matches" in result.output


def test_catalog_show_prints_provenance_and_landing_sites() -> None:
    result = runner.invoke(app, ["catalog", "show", "min_res_ohm"])
    assert result.exit_code == 0, result.output
    assert "min_res" in result.output          # template_var
    assert_in_output("recipe.extraction.min_res_ohm", result)
    assert "manifest_knob" in result.output
    assert "filter_res" in result.output       # a landing section
    assert "Why" in result.output


def test_catalog_show_unknown_key_suggests() -> None:
    result = runner.invoke(app, ["catalog", "show", "min_res"])
    assert result.exit_code == 2
    assert "no catalog option named" in result.output
    assert "min_res_ohm" in result.output


def test_catalog_show_surfaces_an_open_question() -> None:
    """The rows with an unanswered question are the office to-do list."""

    from auto_ext.catalog import builtin_catalog

    questioned = builtin_catalog().open_questions()
    assert questioned, "the catalog should still carry open questions"
    result = runner.invoke(app, ["catalog", "show", questioned[0].key])
    assert result.exit_code == 0, result.output
    assert "Open question" in result.output


# ---- patch ------------------------------------------------------------------


def test_patch_list_says_so_when_there_is_none(ext_root: Path) -> None:
    _new_recipe(ext_root, "rc")
    result = runner.invoke(app, ["patch", "list", "rc", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "has no patch" in result.output


def test_patch_list_shows_one_row_per_hunk(ext_root: Path) -> None:
    _patched_recipe(ext_root)
    result = runner.invoke(app, ["patch", "list", "patched", "--auto-ext-root", str(ext_root)])
    assert result.exit_code == 0, result.output
    assert "0123abcd" in result.output
    assert "beef0001" in result.output
    assert_in_output("keep the extra netlist", result)
    assert_in_output("quantus/ext.cmd.j2", result)
    # One of the two is disabled, so the enabled count must not be 2.
    assert "1 enabled hunk(s)" in result.output


def test_patch_show_prints_the_masked_diff(ext_root: Path) -> None:
    """The stored form keeps ``${cell}`` — that is why the edit survives a swap."""

    _patched_recipe(ext_root)
    result = runner.invoke(
        app, ["patch", "show", "patched", "0123abcd", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 0, result.output
    assert "${cell}" in result.output
    assert "-extra_netlist" in result.output
    assert "keep the extra netlist" in result.output
    assert "amp2" in result.output  # the captured value table


def test_patch_show_accepts_a_unique_id_prefix(ext_root: Path) -> None:
    _patched_recipe(ext_root)
    result = runner.invoke(
        app, ["patch", "show", "patched", "0123", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 0, result.output
    assert "Hunk 0123abcd" in result.output


def test_patch_show_unknown_hunk_points_at_the_listing(ext_root: Path) -> None:
    _patched_recipe(ext_root)
    result = runner.invoke(
        app, ["patch", "show", "patched", "ffffffff", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 2
    assert "patch list patched" in result.output


def test_patch_drop_needs_yes(ext_root: Path) -> None:
    from auto_ext.model.recipe import load_recipe

    path = _patched_recipe(ext_root)
    result = runner.invoke(
        app, ["patch", "drop", "patched", "0123abcd", "--auto-ext-root", str(ext_root)]
    )
    assert result.exit_code == 0, result.output
    assert "would remove" in result.output
    assert "re-run with --yes" in result.output
    assert len(load_recipe(path).patches[0].hunks) == 2


def test_patch_drop_removes_one_hunk(ext_root: Path) -> None:
    from auto_ext.model.recipe import load_recipe

    path = _patched_recipe(ext_root)
    result = runner.invoke(
        app,
        ["patch", "drop", "patched", "0123abcd", "--yes", "--auto-ext-root", str(ext_root)],
    )
    assert result.exit_code == 0, result.output
    recipe = load_recipe(path)
    assert [h.id for h in recipe.patches[0].hunks] == ["beef0001"]


def test_patch_drop_removes_the_patch_record_with_its_last_hunk(
    ext_root: Path,
) -> None:
    """An empty patch would still pin a base fingerprint; it has to go too."""

    from auto_ext.model.recipe import load_recipe

    path = _patched_recipe(ext_root)
    for hunk_id in ("0123abcd", "beef0001"):
        result = runner.invoke(
            app,
            ["patch", "drop", "patched", hunk_id, "--yes", "--auto-ext-root", str(ext_root)],
        )
        assert result.exit_code == 0, result.output
    assert load_recipe(path).patches == []


# ---- migrate ----------------------------------------------------------------


def test_migrate_reports_a_missing_implementation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build without ``migrate_v1_to_v2`` says so; it does not traceback."""

    import auto_ext.migrate as migrate_module

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "project.yaml").write_text("work_root: /w\n", encoding="utf-8")
    (config_dir / "tasks.yaml").write_text("[]\n", encoding="utf-8")
    monkeypatch.delattr(migrate_module, "migrate_v1_to_v2", raising=False)

    result = runner.invoke(app, ["migrate", "--config-dir", str(config_dir)])
    assert result.exit_code == 2
    assert "migrate_v1_to_v2 is not available" in result.output


def test_migrate_reports_every_field_and_writes_nothing_by_default(
    v1_config_dir: Path, tmp_path: Path
) -> None:
    """Nothing may vanish silently: every legacy field gets a disposition row."""

    out = tmp_path / "v2"
    result = runner.invoke(
        app,
        ["migrate", "--config-dir", str(v1_config_dir), "--out-root", str(out)],
    )
    # Warnings are expected on a migration this thin, and they mean exit 1.
    assert result.exit_code == 1, result.output
    assert "Field dispositions" in result.output
    for field in (
        "project.yaml:tech_name",
        "project.yaml:layer_map",
        "project.yaml:paths.calibre_lvs_dir",
        "project.yaml:paths.qrc_deck_dir",
        "project.yaml:extraction_output_dir",
        "project.yaml:dspf_out_path",
    ):
        assert_in_output(field, result)
    assert "re-run with --write" in result.output
    assert not out.exists()


def test_migrate_write_produces_the_whole_v2_file_set(
    v1_config_dir: Path, tmp_path: Path
) -> None:
    from auto_ext.core.profile_discover import read_profile_yaml
    from auto_ext.model.cells import load_cells
    from auto_ext.model.recipe import load_recipe
    from auto_ext.model.workspace import load_workspace

    out = tmp_path / "v2"
    result = runner.invoke(
        app,
        ["migrate", "--config-dir", str(v1_config_dir),
         "--out-root", str(out), "--write"],
    )
    assert result.exit_code == 1, result.output

    profiles = sorted((out / "config" / "profiles").glob("*.yaml"))
    recipes = sorted((out / "recipes").glob("*.yaml"))
    assert len(profiles) == 1
    assert len(recipes) == 1
    assert read_profile_yaml(profiles[0]).tech_name == "HN001"
    assert load_recipe(recipes[0]).reduction.enabled is True
    assert len(load_cells(out / "config" / "cells.yaml")) == 1
    assert load_workspace(out / "config" / "workspace.yaml").output_dir_pattern
    assert (out / "config" / "resources.yaml").is_file()


def test_migrate_is_idempotent(v1_config_dir: Path, tmp_path: Path) -> None:
    """A second --write leaves the files alone rather than rewriting them."""

    out = tmp_path / "v2"
    args = [
        "migrate", "--config-dir", str(v1_config_dir),
        "--out-root", str(out), "--write",
    ]
    first = runner.invoke(app, args)
    assert first.exit_code == 1, first.output
    profile = out / "config" / "profiles" / "hn001.yaml"
    before = profile.read_text(encoding="utf-8")

    second = runner.invoke(app, args)
    assert second.exit_code == 1, second.output
    assert "left alone" in second.output
    assert profile.read_text(encoding="utf-8") == before


def test_migrate_plain_uses_the_shared_formatter(
    v1_config_dir: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        ["migrate", "--config-dir", str(v1_config_dir),
         "--out-root", str(tmp_path / "v2"), "--plain"],
    )
    assert result.exit_code == 1, result.output
    assert "=== field dispositions ===" in result.output
    assert "=== needs confirmation" in result.output


def test_migrate_seed_patches_is_refused_not_ignored(
    v1_config_dir: Path, tmp_path: Path
) -> None:
    """``--seed-patches`` needs the C2 renderer; saying so beats a silent no-op."""

    result = runner.invoke(
        app,
        ["migrate", "--config-dir", str(v1_config_dir),
         "--out-root", str(tmp_path / "v2"), "--seed-patches"],
    )
    assert result.exit_code == 2
    assert "seed_patches" in result.output


def test_migrate_rejects_a_broken_legacy_config(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "project.yaml").write_text("templates: not-a-mapping\n", encoding="utf-8")
    (config_dir / "tasks.yaml").write_text("[]\n", encoding="utf-8")
    result = runner.invoke(
        app,
        ["migrate", "--config-dir", str(config_dir), "--out-root", str(tmp_path / "v2")],
    )
    assert result.exit_code == 2
    assert "migration failed" in result.output
