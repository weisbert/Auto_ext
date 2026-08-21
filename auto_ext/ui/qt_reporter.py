"""Qt signal fan-out for :class:`auto_ext.core.progress.ProgressReporter`.

The runner calls ``ProgressReporter`` methods from whichever thread a
task happens to be running on (main thread in serial, worker threads
in parallel). This class turns each call into a Qt signal emit; Qt's
default ``AutoConnection`` upgrades to ``QueuedConnection`` when the
receiver slot lives in a different thread, marshaling the call onto
the GUI event loop safely.

Does not implement the Protocol via inheritance — PyQt5's QObject
metaclass clashes with ``typing.Protocol``. Structural conformance is
enough for the runner, which only does duck-typing on the method
names.

Implements both halves of the reporter contract: the original
:class:`~auto_ext.core.progress.ProgressReporter` events and the run-layer
:class:`~auto_ext.core.progress.RunAwareReporter` ones. The latter are what
a GUI now needs to find a log file at all: logs live under
``runs/<run_id>/logs/`` and the run id is not derivable from a task id, so
the directory has to be handed over rather than recomputed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from auto_ext.core.progress import StageStatus, TaskStatus


class QtProgressReporter(QObject):
    """Emits runner lifecycle events as Qt signals.

    Signals (all string args safe to marshal across threads; ``object``
    args are picklable-adjacent — :class:`RunSummary` is a plain
    dataclass):

    - ``run_started(int total_tasks, list stages)``
    - ``task_started(str task_id, list stages)``
    - ``stage_started(str task_id, str stage)``
    - ``stage_finished(str task_id, str stage, str status, object error)``
    - ``task_finished(str task_id, str status)``
    - ``run_finished(object summary)``
    - ``run_dir_ready(str task_id, object run_dir)`` — a :class:`pathlib.Path`,
      emitted as soon as the run directory is claimed, i.e. before the first
      stage produces anything. This is the signal a log viewer needs: stage
      logs are at ``<run_dir>/logs/<stage>.log``.
    - ``task_record(str task_id, object record)`` — the finalized
      :class:`~auto_ext.model.run.RunRecord` for that task.

    ``status`` is emitted as a plain string (``str(StageStatus.PASSED)``
    etc.) to keep slot signatures Qt-introspectable.
    """

    run_started = pyqtSignal(int, list)
    task_started = pyqtSignal(str, list)
    stage_started = pyqtSignal(str, str)
    stage_finished = pyqtSignal(str, str, str, object)
    task_finished = pyqtSignal(str, str)
    run_finished = pyqtSignal(object)
    run_dir_ready = pyqtSignal(str, object)
    task_record = pyqtSignal(str, object)

    def on_run_start(self, total_tasks: int, stages: list[str]) -> None:
        self.run_started.emit(total_tasks, list(stages))

    def on_task_start(self, task_id: str, stages: list[str]) -> None:
        self.task_started.emit(task_id, list(stages))

    def on_stage_start(self, task_id: str, stage: str) -> None:
        self.stage_started.emit(task_id, stage)

    def on_stage_end(
        self,
        task_id: str,
        stage: str,
        status: StageStatus,
        error: str | None = None,
    ) -> None:
        self.stage_finished.emit(task_id, stage, str(status), error)

    def on_task_end(self, task_id: str, status: TaskStatus) -> None:
        self.task_finished.emit(task_id, str(status))

    def on_run_end(self, summary: Any) -> None:
        self.run_finished.emit(summary)

    def on_run_dir(self, task_id: str, run_dir: Path) -> None:
        self.run_dir_ready.emit(task_id, run_dir)

    def on_task_record(self, task_id: str, record: Any) -> None:
        self.task_record.emit(task_id, record)
