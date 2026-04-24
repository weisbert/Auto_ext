"""QThread wrapper around :func:`auto_ext.core.runner.run_tasks`.

The worker runs the blocking ``run_tasks`` call off the GUI thread.
Progress events come out via the attached :class:`QtProgressReporter`;
cancellation flips a shared :class:`CancelToken` which the runner
checks before each stage and forwards into :func:`run_subprocess` so
in-flight EDA tools are terminated (SIGTERM → 10s grace → SIGKILL).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.QtCore import QThread, pyqtSignal

from auto_ext.core.progress import CancelToken
from auto_ext.core.runner import RunSummary, run_tasks


class RunWorker(QThread):
    """Off-thread executor for ``run_tasks``. One-shot; re-instantiate per run."""

    #: Emitted with an error string if ``run_tasks`` raised before returning.
    error = pyqtSignal(str)

    def __init__(
        self,
        *,
        project: Any,
        tasks: list[Any],
        stages: list[str],
        auto_ext_root: Path,
        workarea: Path,
        reporter: Any,
        cancel_token: CancelToken,
        max_workers: int | None = None,
        dry_run: bool = False,
    ) -> None:
        super().__init__()
        self._project = project
        self._tasks = tasks
        self._stages = stages
        self._auto_ext_root = auto_ext_root
        self._workarea = workarea
        self._reporter = reporter
        self._cancel_token = cancel_token
        self._max_workers = max_workers
        self._dry_run = dry_run
        self._summary: RunSummary | None = None

    @property
    def summary(self) -> RunSummary | None:
        """The final :class:`RunSummary`, once :meth:`run` has returned."""
        return self._summary

    def request_cancel(self) -> None:
        """Flip the shared cancel flag. The runner sees it on its next check."""
        self._cancel_token.cancel()

    def run(self) -> None:  # QThread entry point
        try:
            self._summary = run_tasks(
                self._project,
                self._tasks,
                stages=self._stages,
                auto_ext_root=self._auto_ext_root,
                workarea=self._workarea,
                max_workers=self._max_workers,
                dry_run=self._dry_run,
                reporter=self._reporter,
                cancel_token=self._cancel_token,
            )
        except Exception as exc:  # noqa: BLE001 — keep thread from dying silently
            self.error.emit(f"{type(exc).__name__}: {exc}")
