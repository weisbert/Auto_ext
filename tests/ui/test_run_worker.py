"""Tests for :class:`auto_ext.ui.worker.RunWorker`.

``dry_run=True`` throughout, so no EDA subprocess spawns -- the worker still
exercises the whole ``run_tasks`` path, including reporter event emission and
summary assembly.

The v2 inputs are what the GUI now hands it: ``project`` / ``tasks`` are
adapted from ``workspace.yaml`` + ``cells.yaml`` by
:func:`auto_ext.core.config.project_from_workspace` /
:func:`~auto_ext.core.config.tasks_from_cells`, and ``recipe`` / ``profile``
are required because ``run_tasks`` requires them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.config import (  # noqa: E402
    project_from_workspace,
    tasks_from_cells,
)
from auto_ext.core.progress import CancelToken  # noqa: E402
from auto_ext.core.runner import RunSummary  # noqa: E402
from auto_ext.ui.qt_reporter import QtProgressReporter  # noqa: E402
from auto_ext.ui.worker import RunBatch, RunWorker  # noqa: E402


@pytest.fixture
def worker_inputs(workspace, cell_book, recipe, pdk_profile, sandbox_env, monkeypatch):
    """``(project, tasks, recipe, profile)`` with the env inside the sandbox."""

    for name, value in sandbox_env.items():
        monkeypatch.setenv(name, value)
    return (
        project_from_workspace(workspace),
        tasks_from_cells(cell_book),
        recipe,
        pdk_profile,
    )


def _worker(worker_inputs, *, token: CancelToken, tmp_path: Path, workarea: Path):
    project, tasks, recipe, profile = worker_inputs
    return RunWorker(
        project=project,
        batches=[RunBatch(recipe=recipe, tasks=tasks)],
        stages=["si", "calibre"],
        auto_ext_root=tmp_path / "pr",
        workarea=workarea,
        reporter=QtProgressReporter(),
        cancel_token=token,
        profile=profile,
        dry_run=True,
    )


def test_worker_runs_dry_run_and_emits_finished(
    qtbot, worker_inputs, workarea: Path, tmp_path: Path
) -> None:
    worker = _worker(
        worker_inputs, token=CancelToken(), tmp_path=tmp_path, workarea=workarea
    )

    with qtbot.waitSignal(worker.finished, timeout=15_000):
        worker.start()

    assert isinstance(worker.summary, RunSummary)
    assert worker.summary.total == 1
    assert worker.summary.passed == 1
    assert worker.summary.cancelled == 0


def test_worker_cancel_before_start_yields_cancelled_summary(
    qtbot, worker_inputs, workarea: Path, tmp_path: Path
) -> None:
    token = CancelToken()
    token.cancel()  # pre-cancelled: the first stage should be CANCELLED
    worker = _worker(worker_inputs, token=token, tmp_path=tmp_path, workarea=workarea)

    with qtbot.waitSignal(worker.finished, timeout=15_000):
        worker.start()

    assert worker.summary is not None
    assert worker.summary.cancelled == 1
    assert worker.summary.passed == 0


def test_a_worker_error_is_reported_rather_than_killing_the_thread(
    qtbot, worker_inputs, workarea: Path, tmp_path: Path, monkeypatch
) -> None:
    """``run_tasks`` raising must reach the GUI as a message, not a silent
    dead thread -- the run would otherwise look like it was still going."""

    def explode(*args, **kwargs):
        raise RuntimeError("the deck directory vanished")

    monkeypatch.setattr("auto_ext.ui.worker.run_tasks", explode)
    worker = _worker(
        worker_inputs, token=CancelToken(), tmp_path=tmp_path, workarea=workarea
    )

    with qtbot.waitSignal(worker.error, timeout=15_000) as caught:
        worker.start()

    assert "RuntimeError" in caught.args[0]
    assert "deck directory vanished" in caught.args[0]


def test_the_worker_carries_continue_on_lvs_fail_into_the_run(
    qtbot, worker_inputs, workarea: Path, tmp_path: Path
) -> None:
    """M-133/E-1. RunRequest had the value; nothing between it and the runner did.

    ``CellsScreen._dispatch`` read the run bar's checkbox into ``RunRequest``
    and then built a ``RunWorker`` without it, so the flag stopped there. The
    worker takes it now, which is the half of the wiring that lives outside
    the three files another session is rewriting -- see
    ``scratchpad/handover_runbar.md`` for the caller's side.
    """

    from auto_ext.core.run_store import read_record

    project, tasks, recipe, profile = worker_inputs
    assert recipe.policy.continue_on_lvs_fail is False, "the fixture must differ"

    root = tmp_path / "pr"
    worker = RunWorker(
        project=project,
        batches=[RunBatch(recipe=recipe, tasks=tasks)],
        stages=["si", "calibre"],
        auto_ext_root=root,
        workarea=workarea,
        reporter=QtProgressReporter(),
        cancel_token=CancelToken(),
        profile=profile,
        dry_run=True,
        continue_on_lvs_fail=True,
    )
    with qtbot.waitSignal(worker.finished, timeout=15_000):
        worker.start()

    runs = sorted(
        d for d in (root / "runs").iterdir() if d.is_dir() and d.name != "batches"
    )
    assert [read_record(d).continue_on_lvs_fail for d in runs] == [True] * len(runs)
