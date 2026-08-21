"""Rich rendering for the CLI: live progress + run-history views.

Two jobs, one module, both Rich-only:

* :class:`RichCLIReporter` — the :class:`~auto_ext.core.progress.ProgressReporter`
  that paints the live table during ``auto-ext run``, plus the ``on_run_dir``
  half of :class:`~auto_ext.core.progress.RunAwareReporter` so the table can
  name the run directory while the run is still going.
* The run views — the tables and blocks behind ``auto-ext run``'s closing
  summary and the ``auto-ext runs list`` / ``runs show`` commands, plus
  :func:`classify_stage_failure`, which turns a failed stage into a named
  failure class and the next thing to try.

Lives outside ``auto_ext/core/`` so the core package stays importable
on hosts without ``rich`` (e.g. if the Linux server ever drops the
dev-wheel bundle). The GUI's ``QtProgressReporter`` sits in
``auto_ext/ui/`` for the same reason vs. PyQt5.

One LVS outcome, three shapes: :class:`auto_ext.core.checks.LvsReport` while
a run is still in memory, :class:`auto_ext.model.run.LvsResult` once it is a
record, and a plain ``dict`` when that record is read back out of
``run.json``. :class:`LvsView` normalises all three so the views below never
have to care which one they were handed.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

from auto_ext.core.progress import StageStatus, TaskStatus

if TYPE_CHECKING:
    from auto_ext.core.run_store import RunIndexEntry
    from auto_ext.core.runner import RunSummary
    from auto_ext.model.run import RunAnnotations, RunRecord


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

#: Rich style per status string, shared by every view in this module.
STATUS_STYLE: dict[str, str] = {
    "passed": "green",
    "failed": "red",
    "cancelled": "yellow",
    "skipped": "dim",
    "dry_run": "blue",
    "pending": "dim",
}

#: Placeholder for "this value does not exist", everywhere.
EMPTY = "[dim]-[/]"


# ---- shared formatting -----------------------------------------------------


def style_status(status: Any) -> str:
    """Return ``status`` wrapped in its Rich style (unknown -> plain)."""

    text = str(status)
    style = STATUS_STYLE.get(text)
    return f"[{style}]{text}[/]" if style else text


def format_duration(seconds: float | None) -> str:
    """Human-readable wall clock: ``12.3s`` / ``4m12s`` / ``1h04m``."""

    if seconds is None:
        return "-"
    seconds = max(seconds, 0.0)
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s"


def format_elapsed(seconds: float) -> str:
    """Compact live counter for a stage that is still running."""

    total = max(int(seconds), 0)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 100:
        return f"{minutes}:{secs:02d}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}"


def as_utc(moment: datetime | None) -> datetime | None:
    """Return ``moment`` as a UTC-aware datetime (naive input is read as UTC)."""

    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_when(moment: datetime | None, *, full: bool = False) -> str:
    """UTC wall clock. Short by default — the run id carries the full stamp."""

    utc = as_utc(moment)
    if utc is None:
        return "-"
    return utc.strftime("%Y-%m-%d %H:%M:%SZ" if full else "%m-%d %H:%M")


# ---- LVS -------------------------------------------------------------------


@dataclass(frozen=True)
class LvsView:
    """One LVS outcome, whatever shape it arrived in.

    :meth:`from_any` accepts :class:`auto_ext.core.checks.LvsReport` (live
    run), :class:`auto_ext.model.run.LvsResult` (record) and the plain
    ``dict`` that comes back out of ``run.json``.
    """

    passed: bool | None = None
    banner: str | None = None
    discrepancies: int | None = None
    source_path: str | None = None
    archived_path: str | None = None
    #: CELL SUMMARY rows in ``"<verdict> <layout> <source>"`` form — the flat
    #: shape :attr:`auto_ext.model.run.LvsResult.cell_summary` archives.
    cell_summary: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """True when nothing at all is known about LVS for this run."""

        return (
            self.passed is None
            and self.banner is None
            and self.discrepancies is None
            and not self.cell_summary
        )

    @property
    def mismatched_cells(self) -> tuple[str, ...]:
        """Layout-side names of the CELL SUMMARY rows that read ``INCORRECT``."""

        out: list[str] = []
        for line in self.cell_summary:
            parts = line.split()
            if parts and parts[0].upper() == "INCORRECT":
                out.append(parts[1] if len(parts) > 1 else line)
        return tuple(out)

    @property
    def effective_discrepancies(self) -> int | None:
        """The count Calibre stated, or 0 when an all-CORRECT table says so.

        Mirrors :attr:`auto_ext.core.checks.LvsReport.effective_discrepancies`
        for the flattened form: v2019.2 omits ``DISCREPANCIES`` on a clean
        pass and lets the CELL SUMMARY table carry the fact instead.
        """

        if self.discrepancies is not None:
            return self.discrepancies
        if self.cell_summary and not self.mismatched_cells:
            return 0
        return None

    @classmethod
    def from_any(cls, obj: Any) -> LvsView | None:
        """Normalise ``obj``; ``None`` in, ``None`` out."""

        if obj is None:
            return None
        if isinstance(obj, cls):
            return obj

        if isinstance(obj, Mapping):

            def read(name: str) -> Any:
                return obj.get(name)

        else:

            def read(name: str) -> Any:
                return getattr(obj, name, None)

        # checks.LvsReport spells the report path ``source`` (a Path) and
        # keeps its rows as CellSummaryRow objects behind cell_summary_lines().
        source = read("source_path")
        if source is None:
            source = read("source")
        rows = read("cell_summary")
        if rows is None:
            to_lines = read("cell_summary_lines")
            rows = to_lines() if callable(to_lines) else ()

        passed = read("passed")
        discrepancies = read("discrepancies")
        archived = read("archived_path")
        return cls(
            passed=passed if isinstance(passed, bool) else None,
            banner=read("banner"),
            discrepancies=discrepancies if isinstance(discrepancies, int) else None,
            source_path=str(source) if source is not None else None,
            archived_path=str(archived) if archived is not None else None,
            cell_summary=tuple(str(r) for r in rows or ()),
        )


def format_lvs(lvs: LvsView | None) -> str:
    """One compact cell: ``✓ 0`` / ``✗ 12`` / ``-``."""

    if lvs is None or lvs.empty:
        return EMPTY
    count = lvs.effective_discrepancies
    if lvs.passed:
        return f"[green]✓ {0 if count is None else count}[/]"
    return f"[red]✗{'' if count is None else f' {count}'}[/]"


# ---- failure classification -------------------------------------------------


@dataclass(frozen=True)
class FailureDiagnosis:
    """Why a stage ended badly, and the next thing to try.

    ``failure_class`` is a stable slug so the same wording can be grepped for
    across the CLI, the GUI and the logs; ``detail`` restates the evidence and
    ``next_action`` is one concrete step.
    """

    stage: str
    failure_class: str
    detail: str
    next_action: str


def _tool_name(details: Mapping[str, Any]) -> str | None:
    argv = details.get("argv")
    if isinstance(argv, Sequence) and not isinstance(argv, (str, bytes)) and argv:
        return str(argv[0])
    return None


def classify_stage_failure(
    *,
    stage: str,
    status: Any,
    error: str | None = None,
    details: Mapping[str, Any] | None = None,
    log_path: str | None = None,
    lvs: LvsView | None = None,
    run_id: str | None = None,
) -> FailureDiagnosis | None:
    """Classify one bad stage, or return ``None`` if it was not one.

    Everything this reads was already being computed and then dropped:
    ``ToolResult.diagnostics`` (``exit_code`` / ``argv`` / ``lvs_parse_error``
    / ``lvs_report_missing``), the parsed LVS report, and the runner's error
    string. Ordering is most-specific-first, so an LVS mismatch never
    degrades into "calibre exited 1".
    """

    status_text = str(status)
    if status_text not in (StageStatus.FAILED, StageStatus.CANCELLED):
        return None

    bag: Mapping[str, Any] = details or {}
    err = (error or "").strip()
    log_ref = log_path or f"the {stage} log"

    if status_text == StageStatus.CANCELLED:
        return FailureDiagnosis(
            stage=stage,
            failure_class="cancelled",
            detail=err or "stopped by user cancellation",
            next_action=(
                f"nothing is wrong with the configuration; re-run this task to redo "
                f"{stage} — a cancelled stage leaves no usable output behind"
            ),
        )

    missing = bag.get("lvs_report_missing")
    if missing:
        return FailureDiagnosis(
            stage=stage,
            failure_class="lvs-report-missing",
            detail=f"the runset declares {missing} but Calibre never wrote it",
            next_action=(
                "check *lvsRunDir / *lvsReportFile in the rendered runset, then "
                f"read {log_ref}"
            ),
        )

    parse_error = bag.get("lvs_parse_error")
    if parse_error:
        return FailureDiagnosis(
            stage=stage,
            failure_class="lvs-report-unparsable",
            detail=str(parse_error),
            next_action=(
                "the report carries no CORRECT/INCORRECT banner, so Calibre most "
                f"likely died before finishing it — read {log_ref}"
            ),
        )

    if lvs is not None and lvs.passed is False:
        count = lvs.effective_discrepancies
        if count:
            detail = f"LVS {lvs.banner or 'failed'}: {count} discrepancy(ies)"
        elif lvs.banner == "CORRECT":
            detail = (
                "banner CORRECT but no discrepancy count and no usable CELL "
                "SUMMARY — the report looks truncated"
            )
        else:
            detail = f"LVS {lvs.banner or 'failed'}"
        bad_cells = lvs.mismatched_cells
        if bad_cells:
            detail += f"; cells {', '.join(bad_cells[:5])}"
            if len(bad_cells) > 5:
                detail += f" (+{len(bad_cells) - 5} more)"
        report = lvs.archived_path or lvs.source_path or "the LVS report"
        compare = (
            f"; `auto-ext runs show {run_id}` prints the discrepancy delta "
            "against the previous run of this cell"
            if run_id
            else ""
        )
        return FailureDiagnosis(
            stage=stage,
            failure_class="lvs-mismatch",
            detail=detail,
            next_action=f"open {report}{compare}",
        )

    if err.startswith("render failed"):
        return FailureDiagnosis(
            stage=stage,
            failure_class="render-failed",
            detail=err,
            next_action=(
                "fix the template or the knob value — no subprocess was spawned, "
                "so the workarea is untouched"
            ),
        )

    if "no template configured" in err:
        return FailureDiagnosis(
            stage=stage,
            failure_class="no-template",
            detail=err,
            next_action=(
                f"set project.templates.{stage} in project.yaml, or the per-task "
                f"templates.{stage} override"
            ),
        )

    exit_code = bag.get("exit_code")
    if exit_code == 127:
        tool = _tool_name(bag) or stage
        return FailureDiagnosis(
            stage=stage,
            failure_class="tool-not-found",
            detail=f"{tool!r} is not on PATH (exit 127)",
            next_action=(
                "source the site setup so the EDA binaries resolve (./run.sh on "
                "the Linux server), then re-run"
            ),
        )

    if isinstance(exit_code, int) and exit_code != 0:
        return FailureDiagnosis(
            stage=stage,
            failure_class="tool-error",
            detail=err or f"{stage} exited {exit_code}",
            next_action=f"read {log_ref}",
        )

    return FailureDiagnosis(
        stage=stage,
        failure_class="stage-error",
        detail=err or "failed without a diagnostic",
        next_action=f"read {log_ref}",
    )


# ---- end-of-run summary -----------------------------------------------------


@dataclass(frozen=True)
class SummaryRow:
    """One task's outcome as ``auto-ext run`` closes.

    Assembled by the CLI from the in-memory ``TaskResult`` (always present)
    plus the run this invocation wrote, so the summary degrades to exactly
    the old three columns rather than to an error when no run record reached
    us.
    """

    task_id: str
    overall: str
    stages: tuple[tuple[str, str], ...] = ()
    run_id: str | None = None
    display_name: str | None = None
    lvs: LvsView | None = None
    failures: tuple[FailureDiagnosis, ...] = ()

    @property
    def label(self) -> str:
        return self.display_name or self.task_id


def build_summary_table(rows: Sequence[SummaryRow]) -> Table:
    """The closing table of ``auto-ext run``.

    The ``run`` column stacks the run id over the task label instead of
    spending a fifth column on it: a run id plus a
    ``library__cell__view__view`` task id do not fit side by side in 80
    columns.
    """

    table = Table(title="Run summary")
    table.add_column("run", style="cyan")
    table.add_column("overall")
    table.add_column("stages")
    table.add_column("LVS", justify="right")

    for row in rows:
        ident = Text()
        if row.run_id:
            ident.append(row.run_id, style="cyan")
            ident.append("\n")
            ident.append(row.label, style="dim")
        else:
            ident.append(row.label, style="cyan")
        stages_str = " ".join(
            f"{stage}:{str(status)[0]}" for stage, status in row.stages
        )
        table.add_row(
            ident,
            style_status(row.overall),
            stages_str,
            format_lvs(row.lvs),
        )
    return table


def print_failure_notes(console: Console, rows: Sequence[SummaryRow]) -> None:
    """Print the failure class + next action for every bad stage in ``rows``."""

    diagnoses = [(row, d) for row in rows for d in row.failures]
    if not diagnoses:
        return
    console.print()
    console.print(f"[bold]{len(diagnoses)} stage failure(s):[/]")
    for row, diag in diagnoses:
        console.print(
            f"  [cyan]{row.label}[/] / [bold]{diag.stage}[/] — "
            f"[magenta]{diag.failure_class}[/]: {diag.detail}"
        )
        console.print(f"    [dim]next:[/] {diag.next_action}")


# ---- run history views ------------------------------------------------------


def _entry_name(entry: RunIndexEntry) -> Text:
    name = Text()
    if entry.starred:
        name.append("★ ", style="yellow")
    name.append(entry.display_name)
    if entry.tags:
        name.append(f" [{','.join(entry.tags)}]", style="dim")
    return name


def build_runs_table(
    entries: Sequence[RunIndexEntry], *, title: str = "Run history"
) -> Table:
    """The table behind ``auto-ext runs list``, newest first."""

    table = Table(title=title)
    table.add_column("when (UTC)", no_wrap=True)
    table.add_column("run_id", style="cyan", no_wrap=True)
    table.add_column("name")
    table.add_column("cell")
    table.add_column("status")
    table.add_column("LVS", justify="right")

    for entry in entries:
        status = style_status(entry.overall)
        if entry.dry_run:
            status += " [blue](dry)[/]"
        table.add_row(
            format_when(entry.created_at),
            entry.run_id,
            _entry_name(entry),
            entry.cell,
            status,
            format_lvs(
                LvsView(passed=entry.lvs_passed, discrepancies=entry.lvs_discrepancies)
            ),
        )
    return table


def _kv_table() -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", no_wrap=True)
    table.add_column(overflow="fold")
    return table


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def print_run_header(
    console: Console,
    record: RunRecord,
    *,
    run_dir: Path | str,
    annotations: RunAnnotations | None = None,
) -> None:
    """Identity, configuration and location of one run."""

    name = (
        annotations.display_name if annotations else None
    ) or record.default_display_name
    if annotations and annotations.starred:
        name = f"★ {name}"
    if annotations and annotations.tags:
        name += f"  [{','.join(annotations.tags)}]"

    console.print(f"[bold cyan]{record.run_id}[/]  {style_status(record.overall)}")
    grid = _kv_table()
    grid.add_row("name", name)
    grid.add_row(
        "dut",
        f"{record.dut.library} / {record.dut.cell} / "
        f"{record.dut.layout_view} vs {record.dut.source_view}",
    )
    grid.add_row("recipe", f"{record.recipe.label} (version {record.recipe.version})")
    grid.add_row("started", format_when(record.created_at, full=True))
    grid.add_row(
        "ended",
        f"{format_when(record.ended_at, full=True)}   "
        f"took {format_duration(record.duration_s)}",
    )
    if record.requested_stages:
        grid.add_row("stages", ", ".join(record.requested_stages))
    grid.add_row(
        "options",
        f"dry-run {_yes_no(record.dry_run)}   "
        f"continue-on-lvs-fail {_yes_no(record.continue_on_lvs_fail)}   "
        f"workers {record.max_workers}",
    )
    grid.add_row("run dir", str(run_dir))
    grid.add_row("workspace", record.workspace_dir)
    if record.dspf_path:
        grid.add_row("dspf", record.dspf_path)
    if record.batch_id:
        grid.add_row("batch", record.batch_id)
    if record.parent_run_id:
        grid.add_row("rerun of", record.parent_run_id)
    if annotations and annotations.note:
        grid.add_row("note", annotations.note)
    console.print(grid)


def build_stages_table(record: RunRecord) -> Table:
    """Per-stage status, duration, exit code and where the files landed."""

    table = Table(title="Stages")
    table.add_column("stage", style="cyan", no_wrap=True)
    table.add_column("status")
    table.add_column("duration", justify="right")
    table.add_column("exit", justify="right")
    table.add_column("log")
    table.add_column("rendered")

    for stage in record.stages:
        table.add_row(
            stage.key,
            style_status(stage.status),
            format_duration(stage.duration_s),
            EMPTY if stage.exit_code is None else str(stage.exit_code),
            stage.log_path or EMPTY,
            stage.rendered_path or EMPTY,
        )
    return table


def print_lvs_block(console: Console, lvs: LvsView) -> None:
    """The structured LVS outcome, promoted out of ``diagnostics``."""

    console.print()
    console.print("[bold]LVS[/]")
    grid = _kv_table()
    grid.add_row("verdict", "[green]passed[/]" if lvs.passed else "[red]failed[/]")
    grid.add_row("banner", lvs.banner or "-")
    count = lvs.effective_discrepancies
    grid.add_row("discrepancies", "-" if count is None else str(count))
    if lvs.cell_summary:
        bad = lvs.mismatched_cells
        summary = f"{len(lvs.cell_summary)} row(s)"
        if bad:
            summary += f", INCORRECT: {', '.join(bad)}"
        grid.add_row("cell summary", summary)
    if lvs.archived_path:
        grid.add_row("report", lvs.archived_path)
    if lvs.source_path:
        grid.add_row("source", lvs.source_path)
    console.print(grid)


def print_comparison(
    console: Console,
    *,
    current: RunRecord,
    current_lvs: LvsView | None,
    previous: RunIndexEntry | None,
) -> None:
    """"How did this cell do last time" — the point of matching on DUT only.

    The discrepancy sentence comes from
    :func:`auto_ext.core.checks.compare_discrepancies` so the CLI, the GUI and
    the logs all word the trend identically.
    """

    from auto_ext.core.checks import DiscrepancyTrend, compare_discrepancies

    console.print()
    current_count = current_lvs.effective_discrepancies if current_lvs else None

    if previous is None:
        console.print("[bold]vs previous run[/]")
        console.print(
            f"  [dim]{compare_discrepancies(current_count, None).summary}[/]"
        )
        return

    console.print(
        f"[bold]vs previous run[/] [cyan]{previous.run_id}[/] "
        f"({format_when(previous.created_at)} UTC)"
    )
    grid = _kv_table()
    grid.add_row("status", f"{previous.overall} -> {current.overall}")

    delta = compare_discrepancies(current_count, previous.lvs_discrepancies)
    if delta.trend is DiscrepancyTrend.NO_BASELINE:
        # A previous run exists; it just never stated a count. Saying "no
        # previous run on record" here would be a lie.
        line = (
            "the previous run states no discrepancy count, so there is nothing "
            "to compare against"
        )
    elif delta.trend is DiscrepancyTrend.IMPROVED:
        line = f"[green]{delta.summary}[/]"
    elif delta.trend is DiscrepancyTrend.REGRESSED:
        line = f"[red]{delta.summary}[/]"
    else:
        line = delta.summary
    grid.add_row("discrepancies", line)
    grid.add_row(
        "duration",
        f"{format_duration(previous.duration_s)} -> "
        f"{format_duration(current.duration_s)}",
    )
    console.print(grid)


def print_artifacts(console: Console, record: RunRecord) -> None:
    """Absolute workarea paths this run produced, grouped by stage."""

    produced = [(s.key, s.artifacts) for s in record.stages if s.artifacts]
    if not produced:
        return
    console.print()
    console.print("[bold]Artifacts[/]")
    console.print(
        "[dim]  in the workarea — the next run of this cell overwrites them[/]"
    )
    for key, artifacts in produced:
        for path in artifacts:
            console.print(f"  [cyan]{key}[/]  {path}")


def run_failures(
    record: RunRecord,
    *,
    run_dir: Path | str | None = None,
    suggest_compare: bool = True,
) -> list[FailureDiagnosis]:
    """Classify every failed / cancelled stage of ``record``.

    Given ``run_dir``, the run-relative paths a record stores (the stage log,
    the archived LVS report) are made absolute first: a ``next_action`` is
    read on its own, away from any header that says where the run lives, so
    ``results/lvs.report`` alone would not be openable.

    ``suggest_compare=False`` drops the "run ``runs show`` to see the delta"
    half of the LVS next action. ``runs show`` passes it, because it has
    already printed that delta a few lines above.
    """

    lvs = LvsView.from_any(record.results.lvs)
    if lvs is not None and lvs.archived_path and run_dir is not None:
        lvs = replace(lvs, archived_path=str(Path(run_dir) / lvs.archived_path))
    out: list[FailureDiagnosis] = []
    for stage in record.stages:
        log_path: str | None = stage.log_path
        if log_path and run_dir is not None:
            log_path = str(Path(run_dir) / log_path)
        diag = classify_stage_failure(
            stage=stage.key,
            status=stage.status,
            error=stage.error,
            details=stage.details,
            log_path=log_path,
            lvs=lvs if stage.stage == "calibre" else None,
            run_id=record.run_id if suggest_compare else None,
        )
        if diag is not None:
            out.append(diag)
    return out


def print_diagnoses(console: Console, diagnoses: Sequence[FailureDiagnosis]) -> None:
    """The failure-class block used by ``runs show``."""

    if not diagnoses:
        return
    console.print()
    console.print("[bold]Diagnosis[/]")
    for diag in diagnoses:
        console.print(
            f"  [bold]{diag.stage}[/] — [magenta]{diag.failure_class}[/]: "
            f"{diag.detail}"
        )
        console.print(f"    [dim]next:[/] {diag.next_action}")


def print_skips(console: Console, record: RunRecord) -> None:
    """Why the stages that did not run did not run."""

    skipped = [
        s for s in record.stages if str(s.status) == StageStatus.SKIPPED and s.skip_reason
    ]
    if not skipped:
        return
    console.print()
    console.print("[bold]Skipped[/]")
    for stage in skipped:
        console.print(f"  [dim]{stage.key}[/] — {stage.skip_reason}")


# ---- live progress reporter -------------------------------------------------


class _LiveView:
    """Lazy renderable so :class:`rich.live.Live`'s auto-refresh re-renders.

    ``Live`` re-draws whatever object it holds on a timer; handing it a
    finished :class:`~rich.table.Table` would freeze the elapsed counters
    between events, and an EDA stage can go minutes without emitting one.
    """

    def __init__(self, reporter: RichCLIReporter) -> None:
        self._reporter = reporter

    def __rich__(self) -> Table:
        return self._reporter.render_table()


@dataclass
class _StageCell:
    """Live state of one (task, stage) square of the progress table."""

    status: str | StageStatus = ""
    started_at: float | None = None
    duration_s: float | None = None


class RichCLIReporter:
    """Live :class:`rich.table.Table` reporter for ``auto-ext run``.

    One row per task, one column per stage. A running stage counts its
    elapsed seconds; finished stages keep their measured duration — the same
    number the run record persists as ``StageRecord.duration_s``.

    Also implements ``on_run_dir`` of
    :class:`~auto_ext.core.progress.RunAwareReporter`, which fires before the
    first stage of a task: the run id goes under the task id in the first
    column, so a user watching a multi-hour extraction can tail
    ``runs/<run_id>/logs/<stage>.log`` in another shell while it is still
    going. ``on_task_record`` is deliberately *not* implemented — the runner
    also hands the finalized record back on ``TaskResult.record``, and the
    closing summary reads it from there rather than keeping a second copy
    that could disagree.

    Thread-safe: :class:`rich.live.Live` serialises updates internally,
    and per-event state mutations are guarded by an explicit lock so
    parallel-mode events from multiple worker threads don't race.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._lock = threading.Lock()
        self._stages_order: list[str] = []
        self._task_order: list[str] = []
        self._cells: dict[str, dict[str, _StageCell]] = {}
        self._task_overall: dict[str, TaskStatus] = {}
        self._run_ids: dict[str, str] = {}
        self._live: Live | None = None
        self._view = _LiveView(self)

    # ---- ProgressReporter methods -------------------------------------

    def on_run_start(self, total_tasks: int, stages: list[str]) -> None:
        with self._lock:
            self._stages_order = list(stages)
            self._cells.clear()
            self._task_overall.clear()
            self._task_order.clear()
            self._run_ids.clear()
        # Live.start() is not reentrant; if a prior run didn't clean up,
        # swallow the double-start rather than crash the CLI.
        self._live = Live(
            self._view,
            console=self._console,
            refresh_per_second=8,
            transient=False,
        )
        self._live.start()

    def on_task_start(self, task_id: str, stages: list[str]) -> None:
        with self._lock:
            if task_id not in self._task_order:
                self._task_order.append(task_id)
            self._cells[task_id] = {s: _StageCell() for s in stages}
            self._task_overall[task_id] = TaskStatus.PENDING
        self._refresh()

    def on_stage_start(self, task_id: str, stage: str) -> None:
        with self._lock:
            cell = self._cells.setdefault(task_id, {}).setdefault(stage, _StageCell())
            cell.status = _RUNNING
            cell.started_at = time.monotonic()
            cell.duration_s = None
        self._refresh()

    def on_stage_end(
        self,
        task_id: str,
        stage: str,
        status: StageStatus,
        error: str | None = None,
    ) -> None:
        with self._lock:
            cell = self._cells.setdefault(task_id, {}).setdefault(stage, _StageCell())
            cell.status = status
            if cell.started_at is not None:
                cell.duration_s = time.monotonic() - cell.started_at
            cell.started_at = None
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

    # ---- RunAwareReporter ---------------------------------------------

    def on_run_dir(self, task_id: str, run_dir: Path) -> None:
        with self._lock:
            self._run_ids[task_id] = Path(run_dir).name
        self._refresh()

    # ---- internals ----------------------------------------------------

    def stage_durations(self, task_id: str) -> dict[str, float]:
        """Measured seconds per finished stage of ``task_id``.

        Read by the CLI when no run record reached it, so a summary still
        carries real timings instead of blanks.
        """

        with self._lock:
            return {
                stage: cell.duration_s
                for stage, cell in self._cells.get(task_id, {}).items()
                if cell.duration_s is not None
            }

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.refresh()

    def render_table(self) -> Table:
        """Build the progress table from the current state. Thread-safe."""

        table = Table(title="Run progress", expand=False)
        table.add_column("task_id", style="cyan", no_wrap=True)
        table.add_column("overall")
        for stage in self._stages_order:
            table.add_column(stage, justify="center")
        now = time.monotonic()
        with self._lock:
            for task_id in self._task_order:
                stages = self._cells.get(task_id, {})
                overall = self._task_overall.get(task_id, TaskStatus.PENDING)
                ident = Text(task_id, style="cyan")
                run_id = self._run_ids.get(task_id)
                if run_id:
                    ident.append(f"\n{run_id}", style="dim")
                row: list[str | Text] = [ident, _TASK_OVERALL[overall]]
                for stage in self._stages_order:
                    row.append(self._cell_text(stages.get(stage), now))
                table.add_row(*row)
        return table

    @staticmethod
    def _cell_text(cell: _StageCell | None, now: float) -> str:
        if cell is None or not cell.status:
            return "[dim]·[/]"
        if cell.status == _RUNNING and cell.started_at is not None:
            return f"[cyan]▶ {format_elapsed(now - cell.started_at)}[/]"
        return _STAGE_CELL.get(cell.status, "")


__all__ = [
    "EMPTY",
    "STATUS_STYLE",
    "FailureDiagnosis",
    "LvsView",
    "RichCLIReporter",
    "SummaryRow",
    "as_utc",
    "build_runs_table",
    "build_stages_table",
    "build_summary_table",
    "classify_stage_failure",
    "format_duration",
    "format_elapsed",
    "format_lvs",
    "format_when",
    "print_artifacts",
    "print_comparison",
    "print_diagnoses",
    "print_failure_notes",
    "print_lvs_block",
    "print_run_header",
    "print_skips",
    "run_failures",
    "style_status",
]
