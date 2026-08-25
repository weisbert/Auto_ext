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

``run_tasks`` takes one recipe for a whole call, but a batch can hold DUTs
that want different ones -- an RF top level and a bias cell are not asking
Quantus the same question. So the worker takes a list of :class:`RunBatch`
and makes one ``run_tasks`` call per recipe, in order, merging the summaries.
Grouping happens above (``CellsScreen._dispatch``), because that is where the
rows and the run bar's override live.

Batches run one after another rather than side by side. ``max_workers``
parallelises tasks *within* a call, and two Quantus batches racing would
double the licence draw against a ceiling the site actually has (``ceil(N/2)``
licences for N CPUs). Sequential is also what makes cancellation simple: the
shared token stops the batch in flight and the loop then declines to start
the next.

``profile`` is required -- it supplies the process literals -- and so is a
non-empty batch list. Neither has a fallback: a run started against the wrong
recipe produces plausible-looking parasitics, which is the failure this whole
rewrite exists to make impossible.
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence
from typing import Any

from dataclasses import dataclass

from PyQt5.QtCore import QThread, pyqtSignal

from auto_ext.core.progress import CancelToken
from auto_ext.core.runner import RunSummary, run_tasks
from auto_ext.model.run import RunRecord


@dataclass(frozen=True)
class RunBatch:
    """One ``run_tasks`` call: the tasks that share a recipe.

    ``tasks`` keeps the order the caller selected the rows in, so a run whose
    rows all name the same recipe is indistinguishable from the old
    single-recipe dispatch.
    """

    recipe: Any
    tasks: list[Any]


class RunWorker(QThread):
    """Off-thread executor for ``run_tasks``. One-shot; re-instantiate per run."""

    #: Emitted with an error string if ``run_tasks`` raised before returning.
    error = pyqtSignal(str)

    def __init__(
        self,
        *,
        project: Any,
        batches: Sequence[RunBatch],
        stages: list[str],
        auto_ext_root: Path,
        workarea: Path,
        reporter: Any,
        cancel_token: CancelToken,
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
        self._batches = list(batches)
        if not self._batches:
            raise ValueError("a run needs at least one batch")
        self._stages = stages
        self._auto_ext_root = auto_ext_root
        self._workarea = workarea
        self._reporter = reporter
        self._cancel_token = cancel_token
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
        """One ``run_tasks`` call per batch, in order, merged into one summary.

        A batch that raises stops the loop: the recipes after it were chosen
        for rows the user expected to run *with* the ones already done, and
        finishing half a dispatch while reporting an error is the shape that
        makes someone re-run the whole thing anyway.
        """

        merged = RunSummary()
        try:
            for index, batch in enumerate(self._batches):
                # Only *between* batches. A run cancelled before it started
                # still goes through ``run_tasks`` once, because that is what
                # records the tasks as CANCELLED; skipping the first call
                # would report an empty dispatch instead of a cancelled one.
                if index and self._cancel_token.is_cancelled():
                    break
                summary = run_tasks(
                    self._project,
                    batch.tasks,
                    stages=self._stages,
                    auto_ext_root=self._auto_ext_root,
                    workarea=self._workarea,
                    recipe=batch.recipe,
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
                merged.tasks.extend(summary.tasks)
                # Each call writes its own batch index when it covered more
                # than one task. The first one names the dispatch; the GUI
                # reads ``tasks``, and the CLI reads the index files.
                if merged.batch_id is None:
                    merged.batch_id = summary.batch_id
                if merged.runs_root is None:
                    merged.runs_root = summary.runs_root
        except Exception as exc:  # noqa: BLE001 — keep thread from dying silently
            self._summary = merged
            self.error.emit(f"{type(exc).__name__}: {exc}")
            return
        self._summary = merged
