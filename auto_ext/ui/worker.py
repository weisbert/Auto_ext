"""QThread wrapper around :func:`auto_ext.core.runner.run_tasks`.

The worker runs the blocking ``run_tasks`` call off the GUI thread.
Progress events come out via the attached :class:`QtProgressReporter`;
cancellation flips a shared :class:`CancelToken` which the runner
checks before each stage and forwards into :func:`run_subprocess` so
in-flight EDA tools are terminated (SIGTERM → 10s grace → SIGKILL).

Each task the worker runs produces a Run directory under
``<auto_ext_root>/runs/``; :attr:`RunWorker.records` exposes the finalized
:class:`~auto_ext.model.run.RunRecord` list once the thread has finished,
and the attached reporter emits each one as it is written.

``recipe`` and ``profile`` are required by ``run_tasks`` -- the recipe says
what to extract, the profile supplies the process literals -- so they are
required here too. They are keyword arguments with no default rather than
optional-with-a-fallback: a run started against the wrong recipe produces
plausible-looking parasitics, which is the failure this whole rewrite exists
to make impossible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.QtCore import QThread, pyqtSignal

from auto_ext.core.progress import CancelToken
from auto_ext.core.runner import RunSummary, run_tasks
from auto_ext.model.run import RunRecord


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
        recipe: Any,
        profile: Any,
        resources: Any = None,
        catalog: Any = None,
        templates_root: Path | None = None,
        max_workers: int | None = None,
        dry_run: bool = False,
        layout_export_path: str | None = None,
    ) -> None:
        super().__init__()
        self._project = project
        self._tasks = tasks
        self._stages = stages
        self._auto_ext_root = auto_ext_root
        self._workarea = workarea
        self._reporter = reporter
        self._cancel_token = cancel_token
        self._recipe = recipe
        self._profile = profile
        self._resources = resources
        self._catalog = catalog
        self._templates_root = templates_root
        self._max_workers = max_workers
        self._dry_run = dry_run
        self._layout_export_path = layout_export_path
        self._summary: RunSummary | None = None

    @property
    def summary(self) -> RunSummary | None:
        """The final :class:`RunSummary`, once :meth:`run` has returned."""
        return self._summary

    @property
    def records(self) -> list[RunRecord]:
        """Finalized run records, in task order. Empty until the run ends.

        Same objects as ``summary.runs``; exposed here so a caller holding
        only the worker does not have to reach through the summary.
        """
        return list(self._summary.runs) if self._summary is not None else []

    @property
    def run_dirs(self) -> list[Path]:
        """Run directories for this dispatch, including ones still in flight."""
        return list(self._summary.run_dirs) if self._summary is not None else []

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
                recipe=self._recipe,
                profile=self._profile,
                resources=self._resources,
                catalog=self._catalog,
                templates_root=self._templates_root,
                max_workers=self._max_workers,
                dry_run=self._dry_run,
                layout_export_path=self._layout_export_path,
                reporter=self._reporter,
                cancel_token=self._cancel_token,
            )
        except Exception as exc:  # noqa: BLE001 — keep thread from dying silently
            self.error.emit(f"{type(exc).__name__}: {exc}")
