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
