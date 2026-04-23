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
    assert "quantus" not in result.stdout or "quantus:d" not in result.stdout


def test_migrate_still_stubbed() -> None:
    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 2


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
    rendered_roots = list((tmp_path / "pr" / "runs").glob("task_*/rendered/knobby"))
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
    rendered = list((tmp_path / "pr1" / "runs").glob("task_*/rendered/knobby"))[0]
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
    rendered = list((tmp_path / "pr2" / "runs").glob("task_*/rendered/knobby"))[0]
    assert rendered.read_text(encoding="utf-8").strip() == "value=80.0"
