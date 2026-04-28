"""Concurrency invariants between :class:`ConfigController`, the Run
worker, and the GUI tabs.

These invariants are not covered by single-handler unit tests:

1. ``ConfigController.has_external_change`` mtime detection (positive
   and negative cases) — the autosave skip condition turns on it.
2. :class:`QtProgressReporter` does not drop signals under an emit
   storm — the runner may emit hundreds of ``stage_finished`` events
   for parallel runs and the live status tree must see all of them.
3. Window close while a worker is in flight should propagate
   cancellation into the worker (``request_cancel`` → CancelToken).
   Auto_ext does not currently install a ``closeEvent`` handler on
   :class:`MainWindow`; this test exercises the smaller invariant —
   the public ``request_cancel`` slot flips the shared token, which
   is the hook a future ``closeEvent`` would call.
4. Two tabs staging edits in close succession must merge into
   ``controller.pending_edits`` without one stomping the other —
   ``stage_edits`` (project keys) and ``stage_tasks_edits`` (tasks
   replacement list) live in independent buckets.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.progress import CancelToken  # noqa: E402
from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.qt_reporter import QtProgressReporter  # noqa: E402
from auto_ext.ui.worker import RunWorker  # noqa: E402


# ---- Invariant 1: has_external_change mtime detection -----------------


def test_gui_concurrency_has_external_change_detects_mtime_bump(
    qtbot, project_tools_config: Path
) -> None:
    """External rewrite of project.yaml after load() must flip the flag.

    Inverse: a fresh load with no further filesystem activity reports
    no change.
    """
    controller = ConfigController()
    controller.load(project_tools_config)
    assert controller.has_external_change() is False

    project_path = project_tools_config / "project.yaml"
    # Force mtime forward — using ns precision because two writes inside
    # the same FS tick can land in the same mtime bucket on Windows NTFS.
    st = project_path.stat()
    os.utime(
        project_path,
        ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000),
    )

    assert controller.has_external_change() is True


def test_gui_concurrency_has_external_change_detects_tasks_yaml_bump(
    qtbot, project_tools_config: Path
) -> None:
    """Bumping tasks.yaml mtime alone is also detected — the flag spans
    both files. Without this the autosave skip would miss tasks-only
    external edits."""
    controller = ConfigController()
    controller.load(project_tools_config)
    assert controller.has_external_change() is False

    tasks_path = project_tools_config / "tasks.yaml"
    st = tasks_path.stat()
    os.utime(
        tasks_path,
        ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000),
    )
    assert controller.has_external_change() is True


def test_gui_concurrency_has_external_change_ignores_internal_save(
    qtbot, project_tools_config: Path
) -> None:
    """``save()`` rewrites project.yaml but tracks the new mtime, so
    has_external_change must return False after a successful save."""
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_edits({"tech_name": "HN_INTERNAL"})

    assert controller.save() is True
    # The save() path calls load() at the end, which refreshes the
    # tracked mtime; the file we just wrote is what's on disk now.
    assert controller.has_external_change() is False


# ---- Invariant 2: emit storm does not lose signals --------------------


def test_gui_concurrency_worker_emit_storm_does_not_lose_signals(
    qtbot,
) -> None:
    """Fire 100 stage_finished events synchronously and assert all 100
    arrive at the slot. The runner emits one per (task, stage) pair so
    a parallel run on dozens of cells can easily push past 100."""
    reporter = QtProgressReporter()
    received: list[tuple[str, str, str]] = []

    def slot(task_id: str, stage: str, status: str, _err: object) -> None:
        received.append((task_id, stage, status))

    reporter.stage_finished.connect(slot)

    for i in range(100):
        reporter.stage_finished.emit(f"t{i}", "si", "passed", None)

    # Signals emitted from the GUI thread to a slot connected on the
    # same thread use DirectConnection by default → delivery is
    # synchronous, so the count must match immediately.
    assert len(received) == 100
    assert received[0] == ("t0", "si", "passed")
    assert received[99] == ("t99", "si", "passed")


def test_gui_concurrency_worker_emit_storm_cross_thread_delivers_all(
    qtbot,
) -> None:
    """Same emit storm but from a worker thread → AutoConnection
    upgrades to QueuedConnection; pytest-qt's event loop drains all
    100 before waitUntil exits."""
    reporter = QtProgressReporter()
    received: list[str] = []
    reporter.stage_finished.connect(
        lambda tid, _stg, _st, _e: received.append(tid)
    )

    def fire_storm() -> None:
        for i in range(100):
            reporter.stage_finished.emit(f"t{i}", "si", "passed", None)

    thread = threading.Thread(target=fire_storm, daemon=True)
    thread.start()
    thread.join(timeout=5.0)
    qtbot.waitUntil(lambda: len(received) == 100, timeout=5_000)
    assert sorted(received) == sorted(f"t{i}" for i in range(100))


# ---- Invariant 3: close-during-run aborts the worker ------------------


def test_gui_concurrency_close_window_aborts_worker(
    qtbot, project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Closing the window during a run should propagate cancellation
    into the worker. Auto_ext does not currently install a
    ``closeEvent`` on :class:`MainWindow`, so this test exercises the
    smallest possible slice: ``request_cancel`` flips the shared
    CancelToken, the worker's run() loop observes it, and the thread
    exits cleanly within a bounded timeout.

    Limitation: the actual ``closeEvent`` wiring is out of scope (it
    would need a production change in main_window.py which agent δ is
    forbidden from touching). When that wiring is added, change this
    test to call ``main_window.close()`` instead of
    ``worker.request_cancel()`` directly.
    """
    from auto_ext.core.config import load_project, load_tasks

    project = load_project(project_tools_config / "project.yaml")
    tasks = load_tasks(project_tools_config / "tasks.yaml", project=project)
    reporter = QtProgressReporter()
    token = CancelToken()
    worker = RunWorker(
        project=project,
        tasks=tasks,
        stages=["si", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "pr",
        workarea=workarea,
        reporter=reporter,
        cancel_token=token,
        dry_run=True,
    )

    worker.start()
    # Pretend the user just closed the window: the close handler should
    # call worker.request_cancel(). Confirm the token flipped.
    worker.request_cancel()
    assert token.is_cancelled() is True

    # Worker thread must wind down within a bounded time (no orphan
    # QThread). 15s is generous — dry_run cancellation in real runs
    # finishes in tens of milliseconds.
    qtbot.waitUntil(lambda: worker.isFinished(), timeout=15_000)
    assert worker.isFinished() is True


# ---- Invariant 4: cross-tab pending edits merge without stomping ------


def test_gui_concurrency_cross_tab_pending_edits_merge(
    qtbot, project_tools_config: Path
) -> None:
    """The Project tab stages flat-key edits and the Tasks tab stages
    a tasks-replacement list; they live in independent buckets. After
    both stage in quick succession both are observable via
    ``pending_edits`` / ``pending_task_specs`` with no clobbering."""
    controller = ConfigController()
    controller.load(project_tools_config)

    # User types in Project tab field then immediately tabs over to
    # Tasks and stages a tasks-row edit before the project autosave
    # would have flushed (autosave skipped here — controller direct).
    controller.stage_edits({"tech_name": "HN_PROJ_EDIT"})
    controller.stage_tasks_edits(
        [
            {
                "library": "L_TASK_EDIT",
                "cell": "c_task",
                "lvs_layout_view": "lay",
            }
        ]
    )

    assert controller.is_dirty is True
    assert controller.pending_edits == {"tech_name": "HN_PROJ_EDIT"}
    specs = controller.pending_task_specs
    assert specs is not None
    assert specs[0]["library"] == "L_TASK_EDIT"
    assert specs[0]["cell"] == "c_task"

    # Reverse interleave should also merge — Tasks first, Project second.
    controller.revert()
    controller.stage_tasks_edits(
        [
            {
                "library": "L_FIRST",
                "cell": "c_first",
                "lvs_layout_view": "lay",
            }
        ]
    )
    controller.stage_edits({"tech_name": "HN_SECOND"})
    assert controller.pending_edits == {"tech_name": "HN_SECOND"}
    specs2 = controller.pending_task_specs
    assert specs2 is not None
    assert specs2[0]["library"] == "L_FIRST"


def test_gui_concurrency_repeated_project_edits_merge_keys(
    qtbot, project_tools_config: Path
) -> None:
    """Two consecutive ``stage_edits`` calls touching different keys
    must merge — neither call should overwrite keys it didn't touch.
    This is the per-tab analog of the cross-tab merge invariant."""
    controller = ConfigController()
    controller.load(project_tools_config)

    controller.stage_edits({"tech_name": "HN_X"})
    controller.stage_edits({"employee_id": "bob"})

    assert controller.pending_edits == {
        "tech_name": "HN_X",
        "employee_id": "bob",
    }
