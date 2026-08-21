"""Smoke tests for :mod:`auto_ext.cli` ``run`` and ``check-env`` commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0"


def test_check_env_all_resolved(v2_config_dir: Path, mocks_on_path: Path) -> None:
    """A green profile plus the tools on PATH is the "yes, go" answer.

    The env values come from the profile's ``env_overrides`` -- the field that
    absorbed ``project.yaml``'s ``env_overrides`` -- so nothing here depends on
    the developer's shell.
    """

    result = runner.invoke(app, ["check-env", "--auto-ext-root", str(v2_config_dir)])
    assert result.exit_code == 0, result.stdout
    assert "this shell can start a run" in result.stdout


def test_run_happy_path(
    v2_config_dir: Path,
    workarea: Path,
    mocks_on_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--auto-ext-root", str(v2_config_dir),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "1/1 tasks passed" in result.stdout


def test_run_no_progress_flag_suppresses_live_table(
    v2_config_dir: Path,
    workarea: Path,
) -> None:
    """--no-progress: no RichCLIReporter live table, final summary still there."""
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
            "--auto-ext-root", str(v2_config_dir),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # Final summary always prints.
    assert "1/1 tasks passed" in result.stdout
    # The live reporter's title "Run progress" should NOT appear; only
    # the static summary's "Run summary" title.
    assert "Run progress" not in result.stdout
    assert "Run summary" in result.stdout


def test_run_with_progress_default_renders_live_table(
    v2_config_dir: Path,
    workarea: Path,
) -> None:
    """Without --no-progress: RichCLIReporter's Run progress table renders.

    CliRunner captures stdout as text; Live falls back to non-animated
    rendering under non-TTY, which still emits the table title.
    """
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--dry-run",
            "--auto-ext-root", str(v2_config_dir),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Run progress" in result.stdout


def test_run_filters_by_task_id_miss_exits_2(
    v2_config_dir: Path,
    workarea: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--task", "does-not-exist",
            "--dry-run",
            "--auto-ext-root", str(v2_config_dir),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 2
    # typer writes errors to stderr; CliRunner by default merges into .stdout.
    assert "not found" in (result.stdout + (result.stderr or ""))


def test_run_stage_filter_restricts_stages(
    v2_config_dir: Path,
    workarea: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--stage", "si,calibre",
            "--dry-run",
            "--auto-ext-root", str(v2_config_dir),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout
    # stages column in the summary table should show only si and calibre.
    assert "si:d" in result.stdout  # dry_run -> 'd'
    assert "calibre:d" in result.stdout
    assert "quantus:d" not in result.stdout


# ---- run summary: run id, LVS, failure class ------------------------------


@pytest.fixture
def wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Rich from wrapping an asserted sentence across two lines."""
    monkeypatch.setenv("COLUMNS", "200")


def test_run_summary_names_the_run_and_where_the_records_went(
    v2_config_dir: Path,
    workarea: Path,
    wide_console: None,
) -> None:
    """The closing block identifies the run and points at runs/."""
    root = v2_config_dir
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--dry-run",
            "--auto-ext-root", str(root),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout

    run_dirs = [p for p in (root / "runs").iterdir() if (p / "run.json").is_file()]
    assert len(run_dirs) == 1
    run_id = run_dirs[0].name

    assert run_id in result.stdout
    # The DUT label sits under the run id in the same column.
    assert "inv · " in result.stdout
    assert "run records written to" in result.stdout
    assert f"auto-ext runs show {run_id}" in result.stdout


def test_run_summary_reports_lvs_discrepancies_and_a_failure_class(
    v2_config_dir: Path,
    workarea: Path,
    mocks_on_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wide_console: None,
) -> None:
    """A failing LVS surfaces its count and its next action, not just 'failed'.

    The mock Calibre writes ``INCORRECT`` / ``DISCREPANCIES = 3``; before the
    run layer this number was parsed, stashed in ``ToolResult.diagnostics``
    and then read by nobody.
    """
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "calibre")
    root = v2_config_dir

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--auto-ext-root", str(root),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 1, result.stdout

    assert "1 stage failure(s):" in result.stdout
    assert "lvs-mismatch" in result.stdout
    assert "3 discrepancy(ies)" in result.stdout
    assert "next:" in result.stdout
    # The discrepancy count also reaches the summary table's LVS column.
    assert "✗ 3" in result.stdout


def test_run_summary_reports_a_missing_binary_as_such(
    v2_config_dir: Path,
    workarea: Path,
    wide_console: None,
) -> None:
    """Without the mocks on PATH every tool exits 127; say why."""
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--stage", "si",
            "--auto-ext-root", str(v2_config_dir),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 1, result.stdout
    assert "tool-not-found" in result.stdout
    assert "run.sh" in result.stdout


def test_run_then_runs_show_reads_back_the_same_run(
    v2_config_dir: Path,
    workarea: Path,
    mocks_on_path: Path,
    wide_console: None,
) -> None:
    """``run`` writes the record; ``runs show`` renders it. One wiring test."""
    root = v2_config_dir
    run_result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--auto-ext-root", str(root),
            "--workarea", str(workarea),
        ],
    )
    assert run_result.exit_code == 0, run_result.stdout

    listed = runner.invoke(app, ["runs", "list", "--auto-ext-root", str(root)])
    assert listed.exit_code == 0, listed.stdout
    assert "inv · " in listed.stdout

    shown = runner.invoke(
        app, ["runs", "show", "latest", "--auto-ext-root", str(root)]
    )
    assert shown.exit_code == 0, shown.stdout
    assert "WB_PLL_DCO / inv / layout vs schematic" in shown.stdout
    assert "Stages" in shown.stdout
    assert "logs/calibre.log" in shown.stdout
    assert "LVS" in shown.stdout
    assert "CORRECT" in shown.stdout


def test_migrate_needs_a_config_dir() -> None:
    """``migrate`` reads a legacy config pair; there is no default for it."""

    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 2
    assert "--config-dir" in (result.output or "")


def test_run_unknown_stage_exits_2(
    v2_config_dir: Path,
    workarea: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(v2_config_dir / "config"),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--stage", "si,not_a_real_stage",
            "--dry-run",
            "--auto-ext-root", str(v2_config_dir),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 2


@pytest.fixture
def calibre_raw_fixture() -> Path:
    return (
        Path(__file__).resolve().parent
        / "fixtures"
        / "raw"
        / "calibre_sample.qci"
    )


def test_import_happy_path(calibre_raw_fixture: Path, tmp_path: Path) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"

    result = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert result.exit_code == 0, result.stdout

    body = output.read_text(encoding="utf-8")
    assert "[[cell]]" in body
    assert "[[library]]" in body
    # Identity literal fully removed.
    assert "INV1" not in body

    review = output.with_name(output.name + ".review.md")
    assert review.is_file()
    review_text = review.read_text(encoding="utf-8")
    assert "tool:" in review_text and "calibre" in review_text


def test_import_missing_tool_exits_2(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "import",
            "--input", str(calibre_raw_fixture),
            "--output", str(tmp_path / "out.j2"),
        ],
    )
    assert result.exit_code == 2


def test_import_unknown_tool_exits_2(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "import",
            "--tool", "bogus",
            "--input", str(calibre_raw_fixture),
            "--output", str(tmp_path / "out.j2"),
        ],
    )
    assert result.exit_code == 2


def test_import_backs_up_an_existing_template(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    output.parent.mkdir(parents=True)
    output.write_text("OLD-CONTENT\n", encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert result.exit_code == 0, result.stdout

    # Backing up is unconditional now. It used to sit behind --fresh, which
    # meant "ignore the sidecar manifest and re-import from scratch"; with
    # manifests gone every import is a fresh one, so the flag selected the
    # only behaviour left and was dropped rather than kept as a no-op.
    bak_template = output.with_name(output.name + ".bak")
    assert bak_template.read_text(encoding="utf-8") == "OLD-CONTENT\n"

    # New content overwrote.
    assert "[[cell]]" in output.read_text(encoding="utf-8")


@pytest.fixture
def migrated_v2(v1_config_dir: Path, tmp_path: Path) -> Path:
    """A written v2 file set (profile + recipe + cells + workspace + resources).

    Returns the root that holds ``config/`` and ``recipes/``, which is also
    what ``--auto-ext-root`` resolves both of them from.
    """

    out = tmp_path / "v2"
    result = runner.invoke(
        app,
        ["migrate", "--config-dir", str(v1_config_dir),
         "--out-root", str(out), "--write"],
    )
    # Exit 1 is the "there are warnings" verdict, not a failure.
    assert result.exit_code in (0, 1), result.output
    return out


def _only_recipe_id(root: Path) -> str:
    recipes = sorted((root / "recipes").glob("*.yaml"))
    assert len(recipes) == 1, recipes
    return recipes[0].stem


def test_run_recipe_renders_through_the_catalog(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """The recipe path renders every target the recipe asks for.

    ``lvs.qci`` proves the profile is in play (the deck path and the supply
    name tables are profile state), ``si.env`` proves the netlist settings
    reached a file no knob ever controlled.
    """

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", _only_recipe_id(migrated_v2),
            "--profile", "hn001",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "1/1 tasks passed" in result.output
    assert "recipe rc-typical-55c" in result.output
    assert "profile hn001" in result.output

    rendered = sorted((migrated_v2 / "runs").glob("*/rendered/*"))
    names = {path.name for path in rendered}
    assert {"si.env", "lvs.qci", "ext.cmd"} <= names

    lvs = next(p for p in rendered if p.name == "lvs.qci").read_text(encoding="utf-8")
    assert "CFXXX.wodio.qcilvs" in lvs
    assert "*lvsPowerNames:" in lvs

    si_env = next(p for p in rendered if p.name == "si.env").read_text(encoding="utf-8")
    assert 'simCellName = "inv"' in si_env
    assert "shortRES = 2000.0" in si_env


def test_run_recipe_values_reach_the_rendered_file(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """Editing a recipe field changes the generated files.

    Three different mechanisms in one run: a Quantus value the templates read
    as a variable, a temperature that used to be a manifest knob, and a
    conditional line in the Calibre runset. All three now come out of one
    recipe file.
    """

    recipe_id = _only_recipe_id(migrated_v2)
    edit = runner.invoke(
        app,
        [
            "recipe", "set", recipe_id,
            "extraction.min_res_ohm=0.02",
            "extraction.temperature_c=70.0",
            "lvs.connect_by_name=true",
            "--auto-ext-root", str(migrated_v2),
        ],
    )
    assert edit.exit_code == 0, edit.output

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", recipe_id,
            "--profile", "hn001",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output

    rendered = sorted((migrated_v2 / "runs").glob("*/rendered/*"))
    ext_cmd = next(p for p in rendered if p.name == "ext.cmd").read_text(encoding="utf-8")
    assert "0.02" in ext_cmd
    assert "70" in ext_cmd
    lvs = next(p for p in rendered if p.name == "lvs.qci").read_text(encoding="utf-8")
    assert "*cmnVConnectNamesState: ALL" in lvs


def test_run_recipe_refuses_a_value_the_template_hardcodes(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """A recipe value with nowhere to land fails the stage, loudly.

    ``netlist.short_res_ohm`` is still a literal in ``default.env.j2``, so
    honouring the recipe is impossible; writing the old value anyway and
    reporting success is the exact failure mode this refactor exists to kill.
    """

    recipe_id = _only_recipe_id(migrated_v2)
    edit = runner.invoke(
        app,
        ["recipe", "set", recipe_id, "netlist.short_res_ohm=1500.0",
         "--auto-ext-root", str(migrated_v2)],
    )
    assert edit.exit_code == 0, edit.output

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", recipe_id,
            "--profile", "hn001",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "hardcode" in result.output
    assert "0/1 tasks passed" in result.output


def test_run_with_a_recipe_and_no_profile_anywhere_is_refused(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
    tmp_path: Path,
) -> None:
    """Half a configuration is the silent-wrong-file case; it has to be loud.

    ``--profile`` is optional only because a root with exactly one profile
    answers the question for you. Point ``--auto-ext-root`` at a root with
    none and there is nothing to answer it with: the corner the recipe names
    has no literal, so the run has to stop and say where profiles come from.
    """

    empty_root = tmp_path / "no-profiles"
    (empty_root / "recipes").mkdir(parents=True)
    recipe_id = _only_recipe_id(migrated_v2)
    (empty_root / "recipes" / f"{recipe_id}.yaml").write_text(
        (migrated_v2 / "recipes" / f"{recipe_id}.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(empty_root),
            "--workarea", str(workarea),
            "--recipe", recipe_id,
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "profile" in result.output
    assert "profile discover" in result.output


def test_run_without_a_recipe_is_refused(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """A run renders from the catalog, and only a Recipe says what to render.

    This used to be "a profile without a recipe is refused" -- one branch of a
    runtime pairing check, back when both were optional and omitting both meant
    "render through project.templates". With the legacy path gone the flag is
    simply required, and the message has to name the command that lists what
    is available rather than only saying no.
    """

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--profile", "hn001",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "--recipe is required" in result.output
    assert "auto-ext recipe list" in result.output
    # The missing-flag error must not be buried under a health report nobody
    # asked for, so the health table stays off when the pair is incomplete.
    assert "Profile health" not in result.output


def test_run_profile_health_blocks_the_start(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """The default is to refuse: an hour of EDA time beats a wrong deck path."""

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", _only_recipe_id(migrated_v2),
            "--profile", "hn001",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "refusing to start" in result.output
    assert "--no-health-check" in result.output
    # The report itself has to be on screen, or "refusing" is unactionable.
    assert "How to fix" in result.output


def test_run_recipe_stage_filter_intersects(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """``--stage`` narrows the recipe's stage list rather than replacing it."""

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", _only_recipe_id(migrated_v2),
            "--profile", "hn001",
            "--no-health-check",
            "--no-progress",
            "--stage", "si",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    names = {p.name for p in (migrated_v2 / "runs").glob("*/rendered/*")}
    assert names == {"si.env"}


def test_run_recipe_unknown_name_lists_the_search_path(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", "no-such-recipe",
            "--profile", "hn001",
            "--no-health-check",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "no recipe named 'no-such-recipe'" in result.output


def test_there_is_no_second_render_path_left_to_fall_back_to(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """No flags used to mean "render through project.templates". Now it means no.

    This is the test that used to assert the two paths coexisted, by looking
    for ``calibre_lvs.qci`` -- the legacy path named a rendered file after its
    template's stem. Nothing is written at all now, and that absence is the
    point: a fallback that quietly rendered from a different source is exactly
    the silent-wrong-file class this round removed.
    """

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "--recipe is required" in result.output
    assert list((migrated_v2 / "runs").glob("*/rendered/*")) == []


def test_run_recipe_resources_file_is_picked_up(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", _only_recipe_id(migrated_v2),
            "--profile", "hn001",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "resources local" in result.output


def test_run_recipe_rejects_an_unreadable_resources_file(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
    tmp_path: Path,
) -> None:
    bad = tmp_path / "resources.yaml"
    bad.write_text("lvs_num_turbo: -3\n", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", _only_recipe_id(migrated_v2),
            "--profile", "hn001",
            "--resources", str(bad),
            "--no-health-check",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "lvs_num_turbo" in result.output


# ---- check-env: a thin wrapper over profile health -------------------------


def test_check_env_uses_the_profile_when_there_is_one(migrated_v2: Path) -> None:
    """With a profile it becomes the health report, exit code and all."""

    result = runner.invoke(app, ["check-env", "--auto-ext-root", str(migrated_v2)])
    assert result.exit_code == 1, result.output
    assert "Profile health: hn001" in result.output
    assert "How to fix" in result.output
    # The env table the old command printed is still there.
    assert "Env resolution" in result.output


def test_check_env_named_profile(migrated_v2: Path) -> None:
    result = runner.invoke(
        app, ["check-env", "--profile", "hn001", "--auto-ext-root", str(migrated_v2)]
    )
    assert result.exit_code == 1, result.output
    assert "Profile health: hn001" in result.output


def test_check_env_without_a_profile_says_where_profiles_come_from(
    project_tools_config: Path, tmp_path: Path
) -> None:
    """No profile is not "everything is fine"; it is "there is nothing to check".

    ``check-env`` used to fall back to scanning ``project.yaml`` for ``${X}``
    references. That scan read a file the render path no longer consults, so a
    green verdict from it meant nothing -- worse than no verdict. The command
    now refuses and names the two ways to get a profile.
    """

    result = runner.invoke(
        app,
        ["check-env", "--config-dir", str(project_tools_config),
         "--auto-ext-root", str(tmp_path / "empty-root")],
    )
    assert result.exit_code == 2, result.output
    assert "nothing to check" in result.output
    assert "profile discover" in result.output
    assert "migrate" in result.output


def test_check_env_reports_an_env_var_the_profile_needs_and_nothing_sets(
    v2_config_dir: Path, mocks_on_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The "missing vars" verdict survived; its source moved to the profile.

    A path expression that resolves to nothing used to be found by scanning
    ``project.yaml``. It is a profile field now, so the check that finds it is
    a health row -- and it still has to make the command exit non-zero and
    name the variable, or a run starts against a deck path that is half a
    string.
    """

    profile_path = v2_config_dir / "config" / "profiles" / "hn001.yaml"
    text = profile_path.read_text(encoding="utf-8")
    profile_path.write_text(
        text.replace("PDK_LAYER_MAP_FILE:", "NOT_SET_ANYWHERE_UNUSED:"), encoding="utf-8"
    )
    monkeypatch.delenv("PDK_LAYER_MAP_FILE", raising=False)

    result = runner.invoke(app, ["check-env", "--auto-ext-root", str(v2_config_dir)])
    assert result.exit_code == 1, result.output
    assert "PDK_LAYER_MAP_FILE" in result.output


def test_check_env_with_nothing_to_check_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["check-env", "--auto-ext-root", str(tmp_path / "nowhere")])
    assert result.exit_code == 2
    assert "nothing to check" in result.output
    assert "profile discover" in result.output
