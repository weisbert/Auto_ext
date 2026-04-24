"""Progress reporting protocol + cancellation token.

The runner (:mod:`auto_ext.core.runner`) is the single caller: it emits
lifecycle events through :class:`ProgressReporter` at run / task / stage
boundaries and checks :class:`CancelToken` before each stage. Defaults
preserve the pre-Phase-5 blocking behavior — callers that pass nothing
get :class:`NullReporter` and a fresh :class:`CancelToken` that is never
set.

Concrete reporter implementations live outside ``core/``:

- :mod:`auto_ext.cli_reporter` — :class:`RichCLIReporter` for the CLI.
- :mod:`auto_ext.ui.qt_reporter` — ``QtProgressReporter`` that fans events
  out as Qt signals onto the GUI event loop.

Kept Qt-free and Rich-free on purpose — ``core/`` must stay importable
on headless boxes with neither installed.
"""

from __future__ import annotations

import threading
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from auto_ext.core.runner import RunSummary


class StageStatus(StrEnum):
    """Terminal status of a single stage.

    ``StrEnum`` so existing call sites that compare to ``"passed"`` / ``"failed"``
    / ``"skipped"`` / ``"dry_run"`` keep working byte-for-byte.
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    DRY_RUN = "dry_run"


class TaskStatus(StrEnum):
    """Status of a task. ``PENDING`` is pre-terminal; the rest are final."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@runtime_checkable
class ProgressReporter(Protocol):
    """Event sink for run / task / stage lifecycle.

    Every method is invoked by the runner; implementations are free to
    no-op any subset. Methods may be called from worker threads in
    parallel mode; implementations are responsible for their own
    thread-safety (Qt signals handle cross-thread marshaling; Rich's
    :class:`rich.live.Live` is thread-safe).
    """

    def on_run_start(self, total_tasks: int, stages: list[str]) -> None: ...

    def on_task_start(self, task_id: str, stages: list[str]) -> None: ...

    def on_stage_start(self, task_id: str, stage: str) -> None: ...

    def on_stage_end(
        self,
        task_id: str,
        stage: str,
        status: StageStatus,
        error: str | None = None,
    ) -> None: ...

    def on_task_end(self, task_id: str, status: TaskStatus) -> None: ...

    def on_run_end(self, summary: RunSummary) -> None: ...


class NullReporter:
    """No-op :class:`ProgressReporter`. Default when the caller passes nothing."""

    def on_run_start(self, total_tasks: int, stages: list[str]) -> None:
        pass

    def on_task_start(self, task_id: str, stages: list[str]) -> None:
        pass

    def on_stage_start(self, task_id: str, stage: str) -> None:
        pass

    def on_stage_end(
        self,
        task_id: str,
        stage: str,
        status: StageStatus,
        error: str | None = None,
    ) -> None:
        pass

    def on_task_end(self, task_id: str, status: TaskStatus) -> None:
        pass

    def on_run_end(self, summary: RunSummary) -> None:
        pass


class CancelToken:
    """Shared cross-thread cancellation flag.

    One per :func:`run_tasks` call. The runner checks
    :meth:`is_cancelled` before each stage and threads the token into
    :func:`run_subprocess`, which polls it while draining stdout so a
    silent subprocess (Quantus can go minutes without output) still
    terminates within a bounded delay.

    The underlying :class:`threading.Event` makes
    :meth:`cancel` idempotent and thread-safe.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Signal cancellation. Idempotent."""
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float) -> bool:
        """Block up to ``timeout`` seconds; return True iff cancelled."""
        return self._event.wait(timeout)
