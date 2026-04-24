"""Tests for :class:`auto_ext.ui.worker.RunWorker`.

Uses ``dry_run=True`` so no EDA subprocesses spawn — the worker still
exercises the full run_tasks path including reporter event emission
and summary assembly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.config import load_project, load_tasks  # noqa: E402
from auto_ext.core.progress import CancelToken  # noqa: E402
from auto_ext.core.runner import RunSummary  # noqa: E402
from auto_ext.ui.qt_reporter import QtProgressReporter  # noqa: E402
from auto_ext.ui.worker import RunWorker  # noqa: E402


def _load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


def test_worker_runs_dry_run_and_emits_finished(
    qtbot, project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    project, tasks = _load(project_tools_config)
    reporter = QtProgressReporter()
    token = CancelToken()
    worker = RunWorker(
        project=project,
        tasks=tasks,
        stages=["si", "calibre"],
        auto_ext_root=tmp_path / "pr",
        workarea=workarea,
        reporter=reporter,
        cancel_token=token,
        dry_run=True,
    )

    with qtbot.waitSignal(worker.finished, timeout=15_000):
        worker.start()

    assert worker.summary is not None
    assert isinstance(worker.summary, RunSummary)
    assert worker.summary.total == 1
    assert worker.summary.passed == 1
    assert worker.summary.cancelled == 0


def test_worker_cancel_before_start_yields_cancelled_summary(
    qtbot, project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    project, tasks = _load(project_tools_config)
    reporter = QtProgressReporter()
    token = CancelToken()
    token.cancel()  # pre-cancelled: first stage should be CANCELLED

    worker = RunWorker(
        project=project,
        tasks=tasks,
        stages=["si", "calibre"],
        auto_ext_root=tmp_path / "pr",
        workarea=workarea,
        reporter=reporter,
        cancel_token=token,
        dry_run=True,
    )

    with qtbot.waitSignal(worker.finished, timeout=15_000):
        worker.start()

    assert worker.summary is not None
    assert worker.summary.cancelled == 1
    assert worker.summary.passed == 0
