"""Tests for :mod:`auto_ext.core.progress` and its runner integration.

Covers:
- ``ProgressReporter`` event sequencing for single-task pass, stage-
  failure abort, jivaro-disabled synthetic skip, parallel ordering.
- :class:`CancelToken` semantics: between-stages cancellation,
  mid-subprocess cancellation (via the :func:`run_subprocess` drain
  loop), reclassification of the killed stage as ``CANCELLED``.
- Reporter exception isolation: a reporter that raises must not abort
  the run.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.progress import (
    CancelToken,
    NullReporter,
    ProgressReporter,
    StageStatus,
    TaskStatus,
)
from auto_ext.core.runner import run_tasks


# ---- SpyReporter + helpers -------------------------------------------------


@dataclass
class SpyReporter:
    """ProgressReporter that records every call in order of arrival."""

    events: list[tuple] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def on_run_start(self, total_tasks: int, stages: list[str]) -> None:
        with self._lock:
            self.events.append(("run_start", total_tasks, tuple(stages)))

    def on_task_start(self, task_id: str, stages: list[str]) -> None:
        with self._lock:
            self.events.append(("task_start", task_id, tuple(stages)))

    def on_stage_start(self, task_id: str, stage: str) -> None:
        with self._lock:
            self.events.append(("stage_start", task_id, stage))

    def on_stage_end(
        self,
        task_id: str,
        stage: str,
        status: StageStatus,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self.events.append(("stage_end", task_id, stage, status))

    def on_task_end(self, task_id: str, status: TaskStatus) -> None:
        with self._lock:
            self.events.append(("task_end", task_id, status))

    def on_run_end(self, summary: Any) -> None:
        with self._lock:
            self.events.append(("run_end", summary.passed, summary.failed, summary.cancelled))


def _load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


# ---- runtime_checkable ProtocolCompat -------------------------------------


def test_spy_reporter_is_a_progress_reporter() -> None:
    """SpyReporter (and our other built-ins) pass the Protocol check."""
    assert isinstance(SpyReporter(), ProgressReporter)
    assert isinstance(NullReporter(), ProgressReporter)


# ---- Event sequencing ------------------------------------------------------


def test_single_task_dry_run_emits_full_event_sequence(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Happy-path sequence: run_start → task_start → stage_start/end × N → task_end → run_end."""
    project, tasks = _load(project_tools_config)
    reporter = SpyReporter()

    run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
        reporter=reporter,
    )

    task_id = tasks[0].task_id
    expected = [
        ("run_start", 1, ("si", "calibre")),
        ("task_start", task_id, ("si", "calibre")),
        ("stage_start", task_id, "si"),
        ("stage_end", task_id, "si", StageStatus.DRY_RUN),
        ("stage_start", task_id, "calibre"),
        ("stage_end", task_id, "calibre", StageStatus.DRY_RUN),
        ("task_end", task_id, TaskStatus.PASSED),
        ("run_end", 1, 0, 0),
    ]
    assert reporter.events == expected


def test_stage_failure_emits_synthetic_skipped_for_remaining(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When calibre fails, quantus + jivaro emit start+end(SKIPPED) pairs.

    GUI trees rely on this: an ``on_stage_start`` without a matching
    ``on_stage_end`` leaves the stage stuck at "running".
    """
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "calibre")
    project, tasks = _load(project_tools_config)
    reporter = SpyReporter()

    run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        reporter=reporter,
    )

    # Every stage must have a paired start + end.
    starts = [e for e in reporter.events if e[0] == "stage_start"]
    ends = [e for e in reporter.events if e[0] == "stage_end"]
    assert len(starts) == len(ends) == 5

    end_status = {e[2]: e[3] for e in ends}
    assert end_status["si"] == StageStatus.PASSED
    assert end_status["strmout"] == StageStatus.PASSED
    assert end_status["calibre"] == StageStatus.FAILED
    assert end_status["quantus"] == StageStatus.SKIPPED
    assert end_status["jivaro"] == StageStatus.SKIPPED

    # Task end must be FAILED (any stage failure ⇒ failed task).
    task_end = next(e for e in reporter.events if e[0] == "task_end")
    assert task_end[2] == TaskStatus.FAILED


def test_jivaro_disabled_emits_synthetic_skipped_pair(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    project, tasks = _load(project_tools_config)
    tasks = [t.model_copy(update={"jivaro": t.jivaro.model_copy(update={"enabled": False})}) for t in tasks]
    reporter = SpyReporter()

    run_tasks(
        project,
        tasks,
        stages=["si", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
        reporter=reporter,
    )

    # Both a start and an end for jivaro even though no work ran.
    task_id = tasks[0].task_id
    jivaro_events = [e for e in reporter.events if len(e) >= 3 and e[2] == "jivaro"]
    kinds = [e[0] for e in jivaro_events]
    assert kinds == ["stage_start", "stage_end"]
    assert jivaro_events[1][3] == StageStatus.SKIPPED


# ---- Parallel ordering ----------------------------------------------------


def test_parallel_per_task_event_sequence_is_ordered(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    can_symlink: bool,
) -> None:
    """In parallel mode, events for each task_id must be in per-task order.

    Interleaving across task_ids is expected and fine; what the GUI
    requires is that for a single task, ``stage_start(si)`` precedes
    ``stage_end(si)``, which precedes ``stage_start(strmout)``, etc.
    """
    if not can_symlink:
        pytest.skip("parallel mode requires symlink support")

    project, task = _load(project_tools_config)
    # Build a second task with a different cell so they don't collide.
    t0 = task[0]
    t1 = t0.model_copy(update={"task_id": "lib2__inv__layout__schematic", "library": "lib2"})
    tasks = [t0, t1]
    reporter = SpyReporter()

    run_tasks(
        project,
        tasks,
        stages=["si", "strmout"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        max_workers=2,
        reporter=reporter,
    )

    for task_id in (t0.task_id, t1.task_id):
        per_task = [e for e in reporter.events if len(e) > 1 and e[1] == task_id]
        # Expect: task_start, stage_start(si), stage_end(si), stage_start(strmout),
        #         stage_end(strmout), task_end
        kinds = [e[0] for e in per_task]
        assert kinds == [
            "task_start",
            "stage_start",
            "stage_end",
            "stage_start",
            "stage_end",
            "task_end",
        ], f"unexpected per-task sequence for {task_id}: {kinds}"


# ---- Cancellation ----------------------------------------------------------


def test_cancel_between_stages_marks_remaining_cancelled(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Cancel fires after stage 1 ends; stage 2 emits CANCELLED, stage 3 SKIPPED."""
    project, tasks = _load(project_tools_config)
    token = CancelToken()

    # A reporter that flips the cancel flag on the first ``on_stage_end``.
    class CancelOnFirstEnd(SpyReporter):
        _fired: bool = False

        def on_stage_end(self, task_id, stage, status, error=None):
            super().on_stage_end(task_id, stage, status, error)
            if not self._fired:
                self._fired = True
                token.cancel()

    reporter = CancelOnFirstEnd()
    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
        reporter=reporter,
        cancel_token=token,
    )

    assert summary.cancelled == 1
    assert summary.failed == 0
    stage_status = {s.stage: s.status for s in summary.tasks[0].stages}
    # si ran (dry_run). strmout is the first hit by cancel → CANCELLED.
    # calibre is after, marked SKIPPED.
    assert stage_status["si"] == StageStatus.DRY_RUN
    assert stage_status["strmout"] == StageStatus.CANCELLED
    assert stage_status["calibre"] == StageStatus.SKIPPED
    assert summary.tasks[0].overall == TaskStatus.CANCELLED


def test_cancel_before_any_stage_marks_first_as_cancelled(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """Cancel set before run_tasks starts dispatching: first stage = CANCELLED."""
    project, tasks = _load(project_tools_config)
    token = CancelToken()
    token.cancel()
    reporter = SpyReporter()

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
        reporter=reporter,
        cancel_token=token,
    )

    stage_status = {s.stage: s.status for s in summary.tasks[0].stages}
    assert stage_status == {"si": StageStatus.CANCELLED, "calibre": StageStatus.SKIPPED}
    assert summary.tasks[0].overall == TaskStatus.CANCELLED


def test_reporter_exception_does_not_abort_run(
    project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    """A reporter that raises must not propagate out of run_tasks.

    Phase 5 contract: reporter call sites are wrapped in ``_safe_call``
    so a buggy Qt slot can't tear down an EDA run.
    """

    class BadReporter(SpyReporter):
        def on_stage_start(self, task_id, stage):
            super().on_stage_start(task_id, stage)
            raise RuntimeError("oh no")

    project, tasks = _load(project_tools_config)
    reporter = BadReporter()

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
        reporter=reporter,
    )

    # Run still completed; all stages ended.
    assert summary.total == 1
    ends = [e for e in reporter.events if e[0] == "stage_end"]
    assert len(ends) == 2
    assert summary.tasks[0].overall == TaskStatus.PASSED  # dry_run alone = pass


# ---- run_subprocess-level cancel (mid-subprocess kill) ---------------------


def test_run_subprocess_cancels_mid_drain(tmp_path: Path) -> None:
    """run_subprocess terminates a running subprocess when the token fires.

    Spawns a Python process that sleeps far longer than the test's
    budget, starts a thread that fires the token after a short delay,
    and asserts the call returns promptly with a non-zero exit.
    """
    import sys

    from auto_ext.tools.base import run_subprocess

    token = CancelToken()
    log_path = tmp_path / "logs" / "sleep.log"
    # On Windows, terminate() is TerminateProcess (hard kill) — either way
    # the drain loop must exit within the CANCEL_GRACE_SECONDS window.
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]

    def fire():
        time.sleep(0.8)
        token.cancel()

    threading.Thread(target=fire, daemon=True).start()

    start = time.time()
    exit_code = run_subprocess(
        argv,
        cwd=tmp_path,
        env={"PATH": str(Path(sys.executable).parent)},
        log_path=log_path,
        cancel_token=token,
    )
    elapsed = time.time() - start

    # Must not have waited the full 30s. Allow generous budget: 0.8s fire
    # delay + up to ~11s grace + overhead = <15s.
    assert elapsed < 15.0, f"cancel took too long: {elapsed}s"
    assert exit_code != 0
    assert "CANCELLED" in log_path.read_text(encoding="utf-8")
