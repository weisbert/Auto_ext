"""Signal-emission tests for :class:`auto_ext.ui.qt_reporter.QtProgressReporter`.

Uses pytest-qt's ``qtbot`` fixture. Skipped gracefully when PyQt5 or
pytest-qt are not installed (e.g. Linux CI without the dev wheel bundle).
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.ui.qt_reporter import QtProgressReporter  # noqa: E402


def test_on_stage_start_emits_stage_started(qtbot) -> None:
    reporter = QtProgressReporter()
    with qtbot.waitSignal(reporter.stage_started, timeout=1000) as blocker:
        reporter.on_stage_start("t1", "si")
    assert blocker.args == ["t1", "si"]


def test_on_stage_end_emits_status_as_string(qtbot) -> None:
    reporter = QtProgressReporter()
    with qtbot.waitSignal(reporter.stage_finished, timeout=1000) as blocker:
        reporter.on_stage_end("t1", "calibre", StageStatus.FAILED, "boom")
    assert blocker.args[0] == "t1"
    assert blocker.args[1] == "calibre"
    assert blocker.args[2] == "failed"
    assert blocker.args[3] == "boom"


def test_on_task_end_emits_status_as_string(qtbot) -> None:
    reporter = QtProgressReporter()
    with qtbot.waitSignal(reporter.task_finished, timeout=1000) as blocker:
        reporter.on_task_end("t1", TaskStatus.CANCELLED)
    assert blocker.args == ["t1", "cancelled"]


def test_run_started_and_run_finished_emit(qtbot) -> None:
    reporter = QtProgressReporter()
    with qtbot.waitSignal(reporter.run_started, timeout=1000) as start:
        reporter.on_run_start(3, ["si", "calibre"])
    assert start.args == [3, ["si", "calibre"]]

    # Simulate a RunSummary-like object.
    class Fake:
        passed = 2
        failed = 1
        cancelled = 0

    with qtbot.waitSignal(reporter.run_finished, timeout=1000) as done:
        reporter.on_run_end(Fake())
    assert isinstance(done.args[0], Fake)


def test_emission_from_worker_thread_delivered_on_main(qtbot) -> None:
    """A signal emitted from a non-GUI thread marshals through the event loop.

    Without QueuedConnection the slot would fire on the worker thread,
    which is unsafe for widget updates. Qt's AutoConnection picks
    QueuedConnection when signaler and receiver live on different
    threads, so we just verify the slot is invoked from the main thread.
    """
    reporter = QtProgressReporter()
    received_threads: list[int] = []

    def slot(task_id: str, stage: str) -> None:
        received_threads.append(threading.get_ident())

    reporter.stage_started.connect(slot)
    main_tid = threading.get_ident()

    def fire() -> None:
        reporter.on_stage_start("t1", "si")

    with qtbot.waitSignal(reporter.stage_started, timeout=2000):
        threading.Thread(target=fire, daemon=True).start()

    assert len(received_threads) == 1
    assert received_threads[0] == main_tid
