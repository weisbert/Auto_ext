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
"""

from __future__ import annotations

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

    ``status`` is emitted as a plain string (``str(StageStatus.PASSED)``
    etc.) to keep slot signatures Qt-introspectable.
    """

    run_started = pyqtSignal(int, list)
    task_started = pyqtSignal(str, list)
    stage_started = pyqtSignal(str, str)
    stage_finished = pyqtSignal(str, str, str, object)
    task_finished = pyqtSignal(str, str)
    run_finished = pyqtSignal(object)

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
