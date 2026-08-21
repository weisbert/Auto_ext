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


def test_check_env_all_resolved(project_tools_config: Path) -> None:
    # project_tools_config sets every required var via env_overrides, so
    # check-env should exit 0.
    result = runner.invoke(app, ["check-env", "--config-dir", str(project_tools_config)])
    assert result.exit_code == 0, result.stdout


def test_run_happy_path(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(tmp_path / "pr"),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "1/1 tasks passed" in result.stdout


def test_run_no_progress_flag_suppresses_live_table(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """--no-progress: no RichCLIReporter live table, final summary still there."""
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--no-progress",
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr"),
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
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """Without --no-progress: RichCLIReporter's Run progress table renders.

    CliRunner captures stdout as text; Live falls back to non-animated
    rendering under non-TTY, which still emits the table title.
    """
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr"),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "Run progress" in result.stdout


def test_run_filters_by_task_id_miss_exits_2(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--task", "does-not-exist",
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr"),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 2
    # typer writes errors to stderr; CliRunner by default merges into .stdout.
    assert "not found" in (result.stdout + (result.stderr or ""))


def test_run_stage_filter_restricts_stages(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--stage", "si,calibre",
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr"),
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
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
    wide_console: None,
) -> None:
    """The closing block identifies the run and points at runs/."""
    root = tmp_path / "pr"
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
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
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wide_console: None,
) -> None:
    """A failing LVS surfaces its count and its next action, not just 'failed'.

    The mock Calibre writes ``INCORRECT`` / ``DISCREPANCIES = 3``; before the
    run layer this number was parsed, stashed in ``ToolResult.diagnostics``
    and then read by nobody.
    """
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "calibre")
    root = tmp_path / "pr"

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
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
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
    wide_console: None,
) -> None:
    """Without the mocks on PATH every tool exits 127; say why."""
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--stage", "si",
            "--auto-ext-root", str(tmp_path / "pr"),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 1, result.stdout
    assert "tool-not-found" in result.stdout
    assert "run.sh" in result.stdout


def test_run_then_runs_show_reads_back_the_same_run(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    wide_console: None,
) -> None:
    """``run`` writes the record; ``runs show`` renders it. One wiring test."""
    root = tmp_path / "pr"
    run_result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
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
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--stage", "si,not_a_real_stage",
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr"),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 2


# ---- --knob parsing ------------------------------------------------------


def test_parse_cli_knobs_basic() -> None:
    from auto_ext.cli import _parse_cli_knobs

    out = _parse_cli_knobs(
        ["quantus.temperature=60", "quantus.limit=200", "calibre.flag=true"],
        ("si", "strmout", "calibre", "quantus", "jivaro"),
    )
    assert out == {
        "quantus": {"temperature": "60", "limit": "200"},
        "calibre": {"flag": "true"},
    }


def test_parse_cli_knobs_value_with_equals_kept() -> None:
    from auto_ext.cli import _parse_cli_knobs

    out = _parse_cli_knobs(
        ["quantus.foo=a=b=c"],
        ("si", "strmout", "calibre", "quantus", "jivaro"),
    )
    assert out == {"quantus": {"foo": "a=b=c"}}


def test_parse_cli_knobs_missing_equals_rejected() -> None:
    from auto_ext.cli import _parse_cli_knobs
    from auto_ext.core.errors import ConfigError

    with pytest.raises(ConfigError, match="missing '='"):
        _parse_cli_knobs(
            ["quantus.temperature"], ("quantus",)
        )


def test_parse_cli_knobs_missing_dot_rejected() -> None:
    from auto_ext.cli import _parse_cli_knobs
    from auto_ext.core.errors import ConfigError

    with pytest.raises(ConfigError, match="missing '\\.'"):
        _parse_cli_knobs(["temperature=60"], ("quantus",))


def test_parse_cli_knobs_unknown_stage_rejected() -> None:
    from auto_ext.cli import _parse_cli_knobs
    from auto_ext.core.errors import ConfigError

    with pytest.raises(ConfigError, match="unknown stage"):
        _parse_cli_knobs(["bogus.x=1"], ("quantus",))


def test_run_malformed_knob_exits_2(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--knob", "not-well-formed",
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr"),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 2


def test_run_knob_beats_manifest_default(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    # Ship a sidecar manifest declaring one knob and a templated .j2 that
    # references it. After --knob overrides the manifest default, the
    # rendered output should contain the CLI value, proving end-to-end
    # precedence (manifest -> CLI).
    tpl = tmp_path / "knobby.j2"
    tpl.write_text("value=[[temperature]]\n", encoding="utf-8")
    (tmp_path / "knobby.j2.manifest.yaml").write_text(
        "template: knobby.j2\n"
        "knobs:\n  temperature:\n    type: float\n    default: 55.0\n",
        encoding="utf-8",
    )
    # Re-point project.yaml's quantus template at the knobby template.
    proj = (project_tools_config / "project.yaml").read_text(encoding="utf-8")
    proj = proj.replace(
        f"quantus: {(Path(__file__).resolve().parent.parent / 'templates' / 'quantus' / 'ext.cmd.j2').as_posix()}",
        f"quantus: {tpl.as_posix()}",
    )
    (project_tools_config / "project.yaml").write_text(proj, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--knob", "quantus.temperature=60",
            "--stage", "quantus",
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr"),
            "--workarea", str(workarea),
        ],
    )
    assert result.exit_code == 0, result.stdout

    tasks_yaml = (project_tools_config / "tasks.yaml").read_text(encoding="utf-8")
    assert tasks_yaml  # sanity

    # Find the rendered knobby file and assert it got the CLI value.
    rendered_roots = list((tmp_path / "pr" / "runs").glob("*/rendered/knobby"))
    assert len(rendered_roots) == 1
    assert rendered_roots[0].read_text(encoding="utf-8").strip() == "value=60.0"


# ---- `import` subcommand + smart merge -----------------------------------


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

    manifest_path = output.with_name(output.name + ".manifest.yaml")
    assert manifest_path.is_file()
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "template: imported.qci.j2" in manifest_text
    assert "knobs:" in manifest_text

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


def test_import_fresh_backs_up_existing(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    output.parent.mkdir(parents=True)
    output.write_text("OLD-CONTENT\n", encoding="utf-8")
    manifest_path = output.with_name(output.name + ".manifest.yaml")
    manifest_path.write_text(
        "template: imported.qci.j2\nknobs: {}\n", encoding="utf-8"
    )

    result = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
            "--fresh",
        ],
    )
    assert result.exit_code == 0, result.stdout

    bak_template = output.with_name(output.name + ".bak")
    bak_manifest = manifest_path.with_name(manifest_path.name + ".bak")
    assert bak_template.read_text(encoding="utf-8") == "OLD-CONTENT\n"
    assert bak_manifest.is_file()

    # New content overwrote.
    assert "[[cell]]" in output.read_text(encoding="utf-8")


def test_reimport_preserves_user_knob_substitutes_new_body(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"

    # First import.
    first = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert first.exit_code == 0, first.stdout

    # Promote cmnNumTurbo so the manifest learns a source reference.
    promote = runner.invoke(
        app,
        ["knob", "promote", str(output), "cmnNumTurbo"],
    )
    assert promote.exit_code == 0, promote.stdout

    # Edit the manifest's description to simulate a user tweak that must
    # round-trip through the merge.
    manifest_path = output.with_name(output.name + ".manifest.yaml")
    text = manifest_path.read_text(encoding="utf-8")
    text = text.replace(
        "cmn_num_turbo:",
        "cmn_num_turbo:\n    description: Tuned for overnight runs",
    )
    manifest_path.write_text(text, encoding="utf-8")

    # Re-import with a raw whose cmnNumTurbo default has moved.
    modified_raw = tmp_path / "modified.qci"
    modified_raw.write_text(
        calibre_raw_fixture.read_text(encoding="utf-8").replace(
            "*cmnNumTurbo: 2", "*cmnNumTurbo: 8"
        ),
        encoding="utf-8",
    )
    reimport = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(modified_raw),
            "--output", str(output),
        ],
    )
    assert reimport.exit_code == 0, reimport.stdout

    body = output.read_text(encoding="utf-8")
    assert "*cmnNumTurbo: [[cmn_num_turbo]]" in body

    manifest_text = manifest_path.read_text(encoding="utf-8")
    # Default refreshed from new raw.
    assert "default: 8" in manifest_text
    # User's description edit round-trips.
    assert "Tuned for overnight runs" in manifest_text
    # Smart-merge log mentions the bump.
    assert "default updated" in reimport.stdout


def test_reimport_leaves_user_defined_knob_alone(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    first = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert first.exit_code == 0, first.stdout

    # Add a user-defined knob manually (no source).
    manifest_path = output.with_name(output.name + ".manifest.yaml")
    manifest_path.write_text(
        "template: imported.qci.j2\n"
        "knobs:\n"
        "  hand_rolled:\n"
        "    type: int\n"
        "    default: 99\n",
        encoding="utf-8",
    )

    reimport = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert reimport.exit_code == 0, reimport.stdout

    body = output.read_text(encoding="utf-8")
    assert "[[hand_rolled]]" not in body
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "hand_rolled" in manifest_text
    assert "default: 99" in manifest_text
    assert "user-defined" in reimport.stdout


# ---- `knob suggest` / `knob promote` -------------------------------------


def test_knob_suggest_lists_candidates(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    imp = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert imp.exit_code == 0

    result = runner.invoke(app, ["knob", "suggest", str(output)])
    assert result.exit_code == 0, result.stdout
    assert "cmnNumTurbo" in result.stdout
    assert "cmn_num_turbo" in result.stdout


def test_knob_promote_rewrites_template_and_manifest(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    imp = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert imp.exit_code == 0

    result = runner.invoke(
        app,
        ["knob", "promote", str(output), "cmnNumTurbo"],
    )
    assert result.exit_code == 0, result.stdout

    body = output.read_text(encoding="utf-8")
    assert "*cmnNumTurbo: [[cmn_num_turbo]]" in body

    manifest_path = output.with_name(output.name + ".manifest.yaml")
    text = manifest_path.read_text(encoding="utf-8")
    assert "cmn_num_turbo:" in text
    assert "tool: calibre" in text
    assert "key: cmnNumTurbo" in text
    assert "default: 2" in text


def test_knob_promote_type_and_name_overrides(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    imp = runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    assert imp.exit_code == 0

    result = runner.invoke(
        app,
        [
            "knob", "promote", str(output),
            "cmnRunHyper",
            "--type", "int",
            "--name", "hyper_enabled",
        ],
    )
    assert result.exit_code == 0, result.stdout
    manifest_text = output.with_name(
        output.name + ".manifest.yaml"
    ).read_text(encoding="utf-8")
    assert "hyper_enabled:" in manifest_text
    assert "type: int" in manifest_text
    # Not bool: override forced int even though the heuristic said bool.
    assert "default: 1" in manifest_text


def test_knob_promote_name_with_multiple_keys_rejected(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    result = runner.invoke(
        app,
        [
            "knob", "promote", str(output),
            "cmnNumTurbo", "cmnLicenseWaitTime",
            "--name", "combined",
        ],
    )
    assert result.exit_code == 2


def test_knob_promote_unknown_key_rejected(
    calibre_raw_fixture: Path, tmp_path: Path
) -> None:
    output = tmp_path / "templates" / "calibre" / "imported.qci.j2"
    runner.invoke(
        app,
        [
            "import",
            "--tool", "calibre",
            "--input", str(calibre_raw_fixture),
            "--output", str(output),
        ],
    )
    result = runner.invoke(
        app,
        ["knob", "promote", str(output), "doesNotExist"],
    )
    assert result.exit_code == 2


def test_run_knob_layering_project_task_cli(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    # project.yaml sets 60, tasks.yaml sets 70, --knob sets 80. Final = 80.
    tpl = tmp_path / "knobby.j2"
    tpl.write_text("value=[[temperature]]\n", encoding="utf-8")
    (tmp_path / "knobby.j2.manifest.yaml").write_text(
        "template: knobby.j2\n"
        "knobs:\n  temperature:\n    type: float\n    default: 55.0\n",
        encoding="utf-8",
    )

    # Replace the quantus template and add project-level knob.
    proj_text = (project_tools_config / "project.yaml").read_text(encoding="utf-8")
    proj_text = proj_text.replace(
        f"quantus: {(Path(__file__).resolve().parent.parent / 'templates' / 'quantus' / 'ext.cmd.j2').as_posix()}",
        f"quantus: {tpl.as_posix()}",
    )
    proj_text += "knobs:\n  quantus:\n    temperature: 60.0\n"
    (project_tools_config / "project.yaml").write_text(proj_text, encoding="utf-8")

    # Add task-level knob.
    (project_tools_config / "tasks.yaml").write_text(
        """\
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
  knobs:
    quantus:
      temperature: 70.0
  jivaro:
    enabled: true
    frequency_limit: 14
    error_max: 2
""",
        encoding="utf-8",
    )

    # Project only -> 60.
    res60 = runner.invoke(
        app,
        [
            "run", "--config-dir", str(project_tools_config),
            "--stage", "quantus", "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr1"),
            "--workarea", str(workarea),
        ],
    )
    assert res60.exit_code == 0, res60.stdout
    # Task beats project -> 70. (Both project and task are set; task wins.)
    rendered = list((tmp_path / "pr1" / "runs").glob("*/rendered/knobby"))[0]
    assert rendered.read_text(encoding="utf-8").strip() == "value=70.0"

    # CLI beats task -> 80.
    res80 = runner.invoke(
        app,
        [
            "run", "--config-dir", str(project_tools_config),
            "--knob", "quantus.temperature=80",
            "--stage", "quantus", "--dry-run",
            "--auto-ext-root", str(tmp_path / "pr2"),
            "--workarea", str(workarea),
        ],
    )
    assert res80.exit_code == 0, res80.stdout
    rendered = list((tmp_path / "pr2" / "runs").glob("*/rendered/knobby"))[0]
    assert rendered.read_text(encoding="utf-8").strip() == "value=80.0"


# ---- run --recipe / --profile (the catalog render path) --------------------
#
# ``--recipe`` is the only switch between the two render paths, so these tests
# exercise it end to end rather than mocking the runner: migrate the legacy
# config pair into a v2 file set, then run against what came out. That also
# means a change to either side of the seam — the CLI's argument handling or
# the runner's pipeline contract — shows up here.


@pytest.fixture
def migrated_v2(project_tools_config: Path, tmp_path: Path) -> Path:
    """A written v2 file set (profile + recipe + cells + workspace + resources).

    Returns the root that holds ``config/`` and ``recipes/``, which is also
    what ``--auto-ext-root`` resolves both of them from.
    """

    out = tmp_path / "v2"
    result = runner.invoke(
        app,
        ["migrate", "--config-dir", str(project_tools_config),
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


def test_run_recipe_without_a_profile_is_refused(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """Half a configuration is the silent-wrong-file case; it has to be loud."""

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", _only_recipe_id(migrated_v2),
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "needs a pdk profile" in result.output


def test_run_profile_without_a_recipe_is_refused(
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
            "--profile", "hn001",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "without a recipe" in result.output
    # The pairing error must not be buried under a health report nobody asked
    # for, so the health table stays off when the pair is incomplete.
    assert "Profile health" not in result.output


def test_run_recipe_and_knob_together_are_refused(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """The recipe path has no knob layer, so --knob would be a silent no-op."""

    result = runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(project_tools_config),
            "--auto-ext-root", str(migrated_v2),
            "--workarea", str(workarea),
            "--recipe", _only_recipe_id(migrated_v2),
            "--profile", "hn001",
            "--knob", "quantus.temperature=60",
            "--dry-run",
        ],
    )
    assert result.exit_code == 2
    assert "recipe set" in result.output


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


def test_run_without_recipe_still_uses_the_legacy_path(
    project_tools_config: Path,
    workarea: Path,
    migrated_v2: Path,
) -> None:
    """The two paths coexist: no flags, no catalog, template names unchanged."""

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
    assert result.exit_code == 0, result.output
    names = {p.name for p in (migrated_v2 / "runs").glob("*/rendered/*")}
    # The legacy path names a rendered file after its template stem, so the
    # calibre file is calibre_lvs.qci, not the catalog target's lvs.qci.
    assert "calibre_lvs.qci" in names
    assert "lvs.qci" not in names


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


def test_check_env_legacy_path_when_no_profile_exists(
    project_tools_config: Path, tmp_path: Path
) -> None:
    """An unmigrated project still gets the old env scan and the old verdict."""

    result = runner.invoke(
        app,
        ["check-env", "--config-dir", str(project_tools_config),
         "--auto-ext-root", str(tmp_path / "empty-root")],
    )
    assert result.exit_code == 0, result.output
    assert "Env resolution" in result.output
    assert "legacy env scan" in result.output


def test_check_env_legacy_path_reports_missing_vars(tmp_path: Path) -> None:
    from auto_ext.core import runner as runner_module

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    (config_dir / "project.yaml").write_text(
        'extraction_output_dir: "${NOT_SET_ANYWHERE}/x"\n', encoding="utf-8"
    )
    (config_dir / "tasks.yaml").write_text(
        "- library: L\n  cell: c\n  lvs_layout_view: layout\n", encoding="utf-8"
    )
    assert runner_module is not None  # the legacy scan still lives there

    result = runner.invoke(
        app,
        ["check-env", "--config-dir", str(config_dir),
         "--auto-ext-root", str(tmp_path / "empty-root")],
    )
    assert result.exit_code == 1
    assert "missing vars" in result.output


def test_check_env_with_nothing_to_check_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["check-env", "--auto-ext-root", str(tmp_path / "nowhere")])
    assert result.exit_code == 2
    assert "nothing to check" in result.output
    assert "profile discover" in result.output
