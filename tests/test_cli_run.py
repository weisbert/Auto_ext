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
