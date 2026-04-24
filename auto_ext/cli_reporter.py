"""Rich-based :class:`ProgressReporter` implementation for the CLI.

Lives outside ``auto_ext/core/`` so the core package stays importable
on hosts without ``rich`` (e.g. if the Linux server ever drops the
dev-wheel bundle). The GUI's ``QtProgressReporter`` sits in
``auto_ext/ui/`` for the same reason vs. PyQt5.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.table import Table

from auto_ext.core.progress import StageStatus, TaskStatus

if TYPE_CHECKING:
    from auto_ext.core.runner import RunSummary


#: Sentinel status for "stage has started but not finished yet". Not a
#: :class:`StageStatus` because the enum is reserved for terminal states
#: that the runner actually assigns.
_RUNNING = "running"

_STAGE_CELL: dict[str, str] = {
    _RUNNING: "[cyan]▶ run[/]",
    StageStatus.PASSED: "[green]✓ pass[/]",
    StageStatus.FAILED: "[red]✗ fail[/]",
    StageStatus.SKIPPED: "[dim]– skip[/]",
    StageStatus.CANCELLED: "[yellow]■ canc[/]",
    StageStatus.DRY_RUN: "[blue]… dry[/]",
}

_TASK_OVERALL: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "[dim]pending[/]",
    TaskStatus.PASSED: "[green]passed[/]",
    TaskStatus.FAILED: "[red]failed[/]",
    TaskStatus.CANCELLED: "[yellow]cancelled[/]",
}


class RichCLIReporter:
    """Live :class:`rich.table.Table` reporter for ``auto-ext run``.

    One row per task, one column per stage. Re-renders on every event.
    Thread-safe: :class:`rich.live.Live` serialises updates internally,
    and per-event state mutations are guarded by an explicit lock so
    parallel-mode events from multiple worker threads don't race.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._lock = threading.Lock()
        self._stages_order: list[str] = []
        self._task_order: list[str] = []
        self._stage_status: dict[str, dict[str, str | StageStatus]] = {}
        self._task_overall: dict[str, TaskStatus] = {}
        self._live: Live | None = None

    # ---- ProgressReporter methods -------------------------------------

    def on_run_start(self, total_tasks: int, stages: list[str]) -> None:
        with self._lock:
            self._stages_order = list(stages)
            self._stage_status.clear()
            self._task_overall.clear()
            self._task_order.clear()
        # Live.start() is not reentrant; if a prior run didn't clean up,
        # swallow the double-start rather than crash the CLI.
        self._live = Live(
            self._render_table(),
            console=self._console,
            refresh_per_second=8,
            transient=False,
        )
        self._live.start()

    def on_task_start(self, task_id: str, stages: list[str]) -> None:
        with self._lock:
            if task_id not in self._task_order:
                self._task_order.append(task_id)
            self._stage_status[task_id] = {s: "" for s in stages}
            self._task_overall[task_id] = TaskStatus.PENDING
        self._refresh()

    def on_stage_start(self, task_id: str, stage: str) -> None:
        with self._lock:
            self._stage_status.setdefault(task_id, {})[stage] = _RUNNING
        self._refresh()

    def on_stage_end(
        self,
        task_id: str,
        stage: str,
        status: StageStatus,
        error: str | None = None,
    ) -> None:
        with self._lock:
            self._stage_status.setdefault(task_id, {})[stage] = status
        self._refresh()

    def on_task_end(self, task_id: str, status: TaskStatus) -> None:
        with self._lock:
            self._task_overall[task_id] = status
        self._refresh()

    def on_run_end(self, summary: RunSummary) -> None:
        # Final paint so the live table shows all-final statuses before
        # we stop Live. The CLI's ``_print_summary`` adds a static table
        # after us — we don't duplicate that here.
        self._refresh()
        if self._live is not None:
            self._live.stop()
            self._live = None

    # ---- internals ----------------------------------------------------

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render_table())

    def _render_table(self) -> Table:
        table = Table(title="Run progress", expand=False)
        table.add_column("task_id", style="cyan", no_wrap=True)
        table.add_column("overall")
        for stage in self._stages_order:
            table.add_column(stage, justify="center")
        for task_id in self._task_order:
            stages = self._stage_status.get(task_id, {})
            overall = self._task_overall.get(task_id, TaskStatus.PENDING)
            row: list[str] = [task_id, _TASK_OVERALL[overall]]
            for stage in self._stages_order:
                v = stages.get(stage, "")
                row.append(_STAGE_CELL.get(v, "") if v else "[dim]·[/]")
            table.add_row(*row)
        return table
