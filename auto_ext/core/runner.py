"""Task + stage execution driver (serial or parallel), and the run recorder.

Loads the resolved env once (fail-fast), instantiates each :class:`Tool`
once, and iterates tasks × stages in the canonical order:
``si`` → ``strmout`` → ``calibre`` → ``quantus`` → ``jivaro``.

Every task executed here produces a **Run**: an immutable directory under
``<auto_ext_root>/runs/<run_id>/`` (see :mod:`auto_ext.model.run`) holding
the rendered inputs, the stage logs, the archived evidence and a
``run.json`` :class:`~auto_ext.model.run.RunRecord`. The directory is
claimed with ``mkdir(exist_ok=False)`` before the first stage runs, so a
rerun can never overwrite the previous one — which the old
``logs/task_<id>/<stage>.log`` layout did on every single rerun::

    runs/20260821T143205Z_inv-ext/
      run.json          skeleton at start, finalized once at the end
      events.jsonl      appended as stages start and finish
      rendered/         si.env, calibre_lvs.qci, ext.cmd, default.xml
      logs/             si.log, strmout.log, calibre.log, ...
      results/          lvs.report + lvs_summary.json rescued from the workarea
      work/             parallel-isolation cwd (serial runs have none)

Two execution modes:

- **Serial** (default): tasks run one at a time, cwd = ``workarea``.
  ``si.env`` is swapped into ``workarea/si.env`` via
  :func:`serial_workdir` for the duration of the ``si`` stage.
- **Parallel** (``max_workers >= 2``): each task gets its own workdir at
  ``runs/<run_id>/work/`` with symlinks to ``workarea/cds.lib``
  and ``workarea/.cdsinit``. All stages for that task run with
  ``cwd = work_dir``; the rendered ``si.env`` is written directly into
  ``work_dir`` with no shared-file mutation. Tasks are dispatched via a
  :class:`concurrent.futures.ThreadPoolExecutor`.

The Cadence workspace (``extraction_output_dir``) is *not* part of a run's
identity — it is shared, reusable, and rewritten by the next run of that
cell. Two tasks reusing one workspace sequentially is legal; using it
concurrently is not, and :func:`auto_ext.core.workdir.workspace_lock`
enforces that with an advisory lock file rather than a preflight veto.

Failure handling (identical in both modes):

- Stage raises :class:`AutoExtError` → that stage is marked failed,
  remaining stages for the task are skipped, runner continues with the
  next task (or the other workers, in parallel mode).
- ``calibre`` stage returning ``success=False``: if ``task.continue_on_lvs_fail``
  is True, log a warning and proceed to the next stage. Otherwise skip
  remaining stages for this task (same as a generic failure).
- Any other stage returning ``success=False``: skip remaining stages for
  this task.
- ``jivaro`` stage is silently skipped (not failed) when
  ``task.jivaro.enabled`` is False.

Observability:

- ``reporter`` (optional :class:`ProgressReporter`) receives lifecycle
  events at run / task / stage boundaries, including synthetic
  start+end pairs for every skipped stage so UI trees stay consistent.
  Reporter exceptions are logged and swallowed — a buggy reporter must
  never tear down a running subprocess. A reporter that also implements
  :class:`~auto_ext.core.progress.RunAwareReporter` additionally receives
  each task's run directory and its finalized record.
- Persisting the run (``run.json`` / ``events.jsonl`` / the archived
  evidence) is treated the same way: an I/O failure there is logged and
  swallowed, because losing the bookkeeping must never abort an EDA run
  that is otherwise fine.
- ``cancel_token`` (optional :class:`CancelToken`) is checked before
  each stage and forwarded into :func:`run_subprocess`; when set
  mid-subprocess, the in-flight EDA process is terminated (SIGTERM
  with a 10s grace, then SIGKILL) and the stage is marked
  :attr:`StageStatus.CANCELLED`.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import socket
import time
import uuid
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from auto_ext import __version__ as AUTO_EXT_VERSION
from auto_ext.core.config import ProjectConfig, TaskConfig
from auto_ext.core.env import (
    EnvResolution,
    derive_parent_dir_from_env_candidates,
    discover_required_vars,
    resolve_env,
    resolve_path_expr,
    substitute_env,
)
from auto_ext.core.errors import AutoExtError, ConfigError, WorkdirError
from auto_ext.core.manifest import load_manifest, resolve_knob_values
from auto_ext.core.progress import (
    CancelToken,
    NullReporter,
    ProgressReporter,
    StageStatus,
    TaskStatus,
)
from auto_ext.core.run_store import (
    append_event,
    list_runs,
    read_record,
    write_batch,
    write_record,
)
from auto_ext.core.template import resolve_template_path
from auto_ext.core.workdir import (
    place_si_env_in_parallel_dir,
    prepare_parallel_workdir,
    serial_workdir,
    workspace_lock,
)
from auto_ext.model.run import (
    RUN_TIMESTAMP_FORMAT,
    DutSnapshot,
    EnvBinding,
    JivaroSnapshot,
    JsonScalar,
    LvsResult,
    RecipeSnapshot,
    RunBatch,
    RunPaths,
    RunRecord,
    RunResults,
    StageRecord,
    allocate_run_dir,
    make_run_slug,
    parse_run_id,
    run_paths,
    slugify,
    utcnow,
)
from auto_ext.tools.base import Tool, ToolResult
from auto_ext.tools.calibre import CalibreTool
from auto_ext.tools.jivaro import JivaroTool
from auto_ext.tools.quantus import QuantusTool
from auto_ext.tools.si import SiTool
from auto_ext.tools.strmout import StrmoutTool

logger = logging.getLogger(__name__)


STAGE_ORDER: tuple[str, ...] = ("si", "strmout", "calibre", "quantus", "jivaro")

_TOOL_REGISTRY: dict[str, type[Tool]] = {
    "si": SiTool,
    "strmout": StrmoutTool,
    "calibre": CalibreTool,
    "quantus": QuantusTool,
    "jivaro": JivaroTool,
}

#: Name of the archived Calibre LVS report inside ``runs/<run_id>/results/``.
LVS_REPORT_NAME = "lvs.report"
#: Name of the derived LVS summary written next to it.
LVS_SUMMARY_NAME = "lvs_summary.json"
#: Canonical name of the archived si control file inside ``rendered/``.
#: The rendered file itself is named after its template (``default.env``);
#: this copy makes the run directory self-describing and matches what gets
#: published into the Cadence workspace.
SI_ENV_ARCHIVE_NAME = "si.env"


# ---- result types ----------------------------------------------------------


@dataclass
class StageResult:
    """Per-stage outcome.

    ``status`` is a :class:`StageStatus`; string comparisons (``== "passed"``)
    continue to work because ``StageStatus`` is a :class:`~enum.StrEnum`.

    ``record`` carries the persisted :class:`~auto_ext.model.run.StageRecord`
    for this stage — timings, argv, exit code, run-relative log and rendered
    paths, workarea artifacts and tool diagnostics. It is the same object that
    lands in ``run.json``; the loose fields above stay for callers that only
    ever wanted the status.
    """

    stage: str
    status: StageStatus
    tool_result: ToolResult | None = None
    error: str | None = None
    record: StageRecord | None = None


@dataclass
class TaskResult:
    """One task's outcome, plus the Run it produced.

    ``run_dir`` is available as soon as the run directory is claimed (before
    the first stage), which is what a consumer needs to tail a log while the
    run is still going. ``record`` only appears once the run is finalized.
    """

    task_id: str
    stages: list[StageResult] = field(default_factory=list)
    overall: TaskStatus = TaskStatus.PENDING
    run_dir: Path | None = None
    record: RunRecord | None = None


@dataclass
class RunSummary:
    """The whole dispatch: one :class:`TaskResult` per task.

    ``runs`` is the list the GUI and the CLI read to render outcomes; each
    entry is a finalized :class:`~auto_ext.model.run.RunRecord` and can be
    re-read from disk later via :func:`auto_ext.core.run_store.read_record`.
    """

    tasks: list[TaskResult] = field(default_factory=list)
    #: Set when the dispatch covered more than one task; the index file lives
    #: at ``runs/batches/<batch_id>.json``.
    batch_id: str | None = None
    runs_root: Path | None = None

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tasks if t.overall == TaskStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tasks if t.overall == TaskStatus.FAILED)

    @property
    def cancelled(self) -> int:
        return sum(1 for t in self.tasks if t.overall == TaskStatus.CANCELLED)

    @property
    def runs(self) -> list[RunRecord]:
        """Finalized run records, in task submission order."""

        return [t.record for t in self.tasks if t.record is not None]

    @property
    def run_dirs(self) -> list[Path]:
        """Run directories, in task submission order, including unfinalized ones."""

        return [t.run_dir for t in self.tasks if t.run_dir is not None]


@dataclass
class _TaskExecCtx:
    """Per-task execution context: where stages run and how si.env is placed.

    ``parallel=False``: ``cwd`` is the shared workarea; si uses
    :func:`serial_workdir` to swap si.env in/out.
    ``parallel=True``: ``cwd`` is the run's isolated ``work/`` dir; si.env
    is copied directly into it.
    """

    cwd: Path
    run_dir: Path
    paths: RunPaths
    parallel: bool


# ---- entry point -----------------------------------------------------------


def run_tasks(
    project: ProjectConfig,
    tasks: list[TaskConfig],
    *,
    stages: list[str],
    auto_ext_root: Path,
    workarea: Path,
    verbose: bool = False,
    dry_run: bool = False,
    cli_knobs: dict[str, dict[str, Any]] | None = None,
    max_workers: int | None = None,
    reporter: ProgressReporter | None = None,
    cancel_token: CancelToken | None = None,
) -> RunSummary:
    """Execute the stage × task matrix, serial or parallel.

    Pre-flight:

    - Validates ``stages`` (must be a non-empty subset of :data:`STAGE_ORDER`).
    - If ``jivaro`` is among ``stages``, every task with ``jivaro.enabled=True``
      must have ``out_file`` set, else :class:`ConfigError`.
    - Checks whether tasks share a resolved ``extraction_output_dir``. In
      serial that is legal (the workspace is reused, not contended) and only
      logged; in parallel it is refused, because two concurrent tasks writing
      one svdb corrupt each other.
    - Discovers env vars from every template in use and resolves them
      (override → shell → missing); any missing raises
      :class:`auto_ext.core.errors.EnvResolutionError` before any
      subprocess starts.

    ``cli_knobs`` is the ``{stage: {name: str}}`` dict parsed from
    ``--knob`` options; values are still strings here and are coerced at
    render time per :class:`auto_ext.core.manifest.KnobSpec`.

    ``max_workers`` gates the execution mode: ``None`` or ``<= 1`` runs
    serially (cwd = ``workarea``, si.env swapped via
    :func:`serial_workdir`); ``>= 2`` runs tasks on a thread pool, each
    task isolated under its own ``runs/<run_id>/work/``.

    ``reporter`` / ``cancel_token`` default to a :class:`NullReporter`
    and a fresh :class:`CancelToken` that is never set — same blocking
    behavior as pre-Phase-5 callers.
    """
    _validate_stages(stages)
    _validate_tasks(tasks, stages)

    if reporter is None:
        reporter = NullReporter()
    if cancel_token is None:
        cancel_token = CancelToken()

    required_env = _discover_env_vars(project, tasks, auto_ext_root=auto_ext_root)
    resolution = resolve_env(required_env, project.env_overrides)
    resolved_env = resolution.require()

    parallel = max_workers is not None and max_workers >= 2

    # The workspace-sharing check needs resolved env so ``${WORK_ROOT}`` is
    # gone before ``str.format`` runs (Python would otherwise interpret
    # ``{WORK_ROOT}`` as a missing format key). Runs after env resolution
    # but before any subprocess; env errors are more fundamental anyway.
    _validate_task_outputs(tasks, project, resolved_env, parallel=parallel)

    subprocess_env: dict[str, str] = {**os.environ, **project.env_overrides}

    tool_instances: dict[str, Tool] = {name: cls() for name, cls in _TOOL_REGISTRY.items()}
    tool_paths = _resolve_tool_paths(tool_instances, subprocess_env)

    cli_knobs = cli_knobs or {}
    runs_root = Path(auto_ext_root) / "runs"
    started_at = utcnow()
    batch_id = _new_batch_id(started_at) if len(tasks) > 1 else None

    summary = RunSummary(batch_id=batch_id, runs_root=runs_root)
    _safe_call(reporter, "on_run_start", len(tasks), list(stages))

    def _submit(task: TaskConfig) -> TaskResult:
        return _run_single_task(
            project=project,
            task=task,
            stages=stages,
            auto_ext_root=auto_ext_root,
            runs_root=runs_root,
            workarea=workarea,
            resolution=resolution,
            resolved_env=resolved_env,
            subprocess_env=subprocess_env,
            tools=tool_instances,
            tool_paths=tool_paths,
            cli_knobs=cli_knobs,
            verbose=verbose,
            dry_run=dry_run,
            parallel=parallel,
            max_workers=max_workers or 1,
            batch_id=batch_id,
            reporter=reporter,
            cancel_token=cancel_token,
        )

    if not parallel:
        for task in tasks:
            summary.tasks.append(_submit(task))
    else:
        logger.info("parallel mode: max_workers=%d across %d tasks", max_workers, len(tasks))
        results_by_id: dict[str, TaskResult] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_submit, task): task for task in tasks}
            for fut in as_completed(futures):
                task = futures[fut]
                result = fut.result()
                results_by_id[task.task_id] = result
        # Preserve the original task submission order in the summary so
        # callers see deterministic output regardless of completion order.
        summary.tasks = [results_by_id[t.task_id] for t in tasks]

    if batch_id is not None:
        _write_batch_index(runs_root, batch_id, started_at, summary, max_workers or 1)

    logger.info(
        "run complete: %d/%d passed, %d failed, %d cancelled",
        summary.passed,
        summary.total,
        summary.failed,
        summary.cancelled,
    )
    _safe_call(reporter, "on_run_end", summary)
    return summary


# ---- run directory / record lookup -----------------------------------------


def latest_run_record_for(auto_ext_root: Path, task: TaskConfig) -> RunRecord | None:
    """Return the most recent :class:`RunRecord` for ``task``'s DUT, or ``None``.

    "Same DUT" is the four identity axes the old ``task_id`` encoded —
    library, cell, layout view, source view. Runs are enumerated from
    ``<auto_ext_root>/runs/``; unreadable directories are skipped by
    :func:`auto_ext.core.run_store.list_runs` rather than raising, so a
    corrupt history never breaks a lookup.
    """

    runs_root = Path(auto_ext_root) / "runs"
    key = f"{task.library}__{task.cell}__{task.lvs_layout_view}__{task.lvs_source_view}"
    for entry in list_runs(runs_root):  # newest first
        if entry.dut_key != key:
            continue
        try:
            return read_record(entry.run_dir)
        except AutoExtError as exc:
            logger.warning("%s: cannot read run record (%s)", entry.run_dir, exc)
            continue
    return None


def rendered_path_for(
    auto_ext_root: Path,
    task: TaskConfig,
    stage: str,
    project: ProjectConfig,
    *,
    record: RunRecord | None = None,
) -> Path | None:
    """Return where the runner *wrote* this stage's rendered template.

    Read straight out of :attr:`~auto_ext.model.run.StageRecord.rendered_path`
    in the run record — deliberately not recomputed. The GUI's "Open rendered
    template" action used to mirror the runner's path arithmetic, which meant
    two implementations of one convention that could (and did) drift apart.
    Now there is one: whatever the runner recorded.

    ``record`` defaults to the newest run of ``task``'s DUT
    (:func:`latest_run_record_for`).

    Returns ``None`` when:

    - ``stage`` is not a known stage, or is ``strmout`` (the strmout tool has
      ``has_template=False``: it consumes ``output_dir`` / ``layer_map``
      directly and produces no rendered input file);
    - the DUT has never been run, so there is no record to read;
    - the recorded stage produced no rendered file (skipped, cancelled, or a
      render that failed before writing).

    ``project`` is unused at runtime — template resolution now happens once,
    inside the run — but is kept in the signature so existing callers do not
    have to change.
    """
    if stage not in STAGE_ORDER or stage == "strmout":
        return None
    if record is None:
        record = latest_run_record_for(auto_ext_root, task)
    if record is None:
        return None
    stage_record = record.stage(stage)
    if stage_record is None or stage_record.rendered_path is None:
        return None
    base = (
        Path(record.run_dir)
        if record.run_dir
        else Path(auto_ext_root) / "runs" / record.run_id
    )
    return base / stage_record.rendered_path


# ---- per-task / per-stage --------------------------------------------------


def _run_single_task(
    *,
    project: ProjectConfig,
    task: TaskConfig,
    stages: list[str],
    auto_ext_root: Path,
    runs_root: Path,
    workarea: Path,
    resolution: EnvResolution,
    resolved_env: dict[str, str],
    subprocess_env: dict[str, str],
    tools: dict[str, Tool],
    tool_paths: dict[str, str],
    cli_knobs: dict[str, dict[str, Any]],
    verbose: bool,
    dry_run: bool,
    parallel: bool = False,
    max_workers: int,
    batch_id: str | None,
    reporter: ProgressReporter,
    cancel_token: CancelToken,
) -> TaskResult:
    dut = DutSnapshot.from_task_config(task)
    recipe_id = _derive_recipe_id(task)

    # The slug only reads ``recipe_id``, so a minimal snapshot names the
    # directory; the full snapshot needs the render context, which in turn
    # may need the run id, so it is built once the directory exists.
    run_dir = allocate_run_dir(runs_root, make_run_slug(dut, RecipeSnapshot(recipe_id=recipe_id)))
    run_id = run_dir.name
    _, slug = parse_run_id(run_id)
    paths = run_paths(run_dir)
    created_at = utcnow()

    task_result = TaskResult(task_id=task.task_id, run_dir=run_dir)
    _safe_optional_call(reporter, "on_run_dir", task.task_id, run_dir)

    try:
        context = _build_context(project, task, resolved_env, run_slug=slug, run_id=run_id)
        recipe = _recipe_snapshot(
            project, task, context, cli_knobs, auto_ext_root=auto_ext_root, recipe_id=recipe_id
        )
    except AutoExtError:
        # A configuration error here (an unknown dspf_out_path format key, say)
        # means nothing about this run can be described, so there is nothing
        # worth recording. Give the directory back before re-raising rather
        # than leaving an empty run for the history list to warn about
        # forever; it is still untouched, so removing it is safe.
        shutil.rmtree(run_dir, ignore_errors=True)
        raise

    # Each run owns runs/<run_id>/work, so there is nothing to contend over.
    # A symlink failure (no Developer Mode on Windows, a workarea missing
    # cds.lib) is this task's failure, not the dispatch's: the run directory
    # is already claimed, so it has to end up with a record explaining itself
    # rather than being left behind as an empty orphan.
    setup_error: str | None = None
    cwd = workarea
    if parallel:
        try:
            cwd = prepare_parallel_workdir(run_dir, workarea)
        except AutoExtError as exc:
            setup_error = f"cannot prepare the parallel work dir: {exc}"

    exec_ctx = _TaskExecCtx(cwd=cwd, run_dir=run_dir, paths=paths, parallel=parallel)
    active_stages = [s for s in STAGE_ORDER if s in stages]

    base_fields: dict[str, Any] = {
        "run_id": run_id,
        "slug": slug,
        "created_at": created_at,
        "batch_id": batch_id,
        "dut": dut,
        "recipe": recipe,
        "requested_stages": active_stages,
        "dry_run": dry_run,
        "continue_on_lvs_fail": task.continue_on_lvs_fail,
        "max_workers": max_workers,
        "workspace_dir": str(context["output_dir"]),
        "intermediate_dir": _opt_str(context.get("intermediate_dir")),
        "dspf_path": _opt_str(context.get("dspf_out_path")),
        "workarea": str(workarea),
        "run_dir": str(run_dir),
        "work_dir": str(cwd) if parallel and setup_error is None else None,
        "env": EnvBinding.from_resolution(resolution),
        "context": _jsonable_context(context),
        "tools": tool_paths,
        "host": _hostname(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        "auto_ext_version": AUTO_EXT_VERSION,
        "python_version": platform.python_version(),
    }

    _safe_store(write_record, run_dir, RunRecord(**base_fields))
    _safe_store(
        append_event,
        run_dir,
        {
            "event": "run_start",
            "run_id": run_id,
            "task_id": task.task_id,
            "stages": active_stages,
            "dry_run": dry_run,
            "parallel": parallel,
        },
    )

    _safe_call(reporter, "on_task_start", task.task_id, active_stages)

    # A dry run must not touch the Cadence workspace at all, so it neither
    # creates the directory nor takes the lock.
    lock = (
        workspace_lock(Path(context["output_dir"]), run_id)
        if not dry_run and setup_error is None
        else _NullContext()
    )
    try:
        if setup_error is not None:
            raise WorkdirError(setup_error)
        with lock:
            _execute_stages(
                task=task,
                project=project,
                stages=active_stages,
                task_result=task_result,
                exec_ctx=exec_ctx,
                context=context,
                resolved_env=resolved_env,
                subprocess_env=subprocess_env,
                tools=tools,
                cli_knobs=cli_knobs,
                dry_run=dry_run,
                cancel_token=cancel_token,
                auto_ext_root=auto_ext_root,
                reporter=reporter,
                run_dir=run_dir,
            )
    except AutoExtError as exc:
        # Two things raise out here: the work-dir setup above, and the
        # workspace lock when another run owns the Cadence workspace. Both are
        # recorded as a failed run rather than tearing down the whole dispatch.
        logger.error("task %s: %s", task.task_id, exc)
        _append_synthetic_stage(
            task_result,
            reporter,
            task.task_id,
            active_stages[0] if active_stages else "si",
            StageStatus.FAILED,
            str(exc),
            run_dir=run_dir,
        )

    task_result.overall = _compute_overall(task_result)
    if verbose:
        print(f"[task {task.task_id}] {task_result.overall}")

    record = _finalize_run(
        run_dir=run_dir,
        paths=paths,
        base_fields=base_fields,
        task_result=task_result,
    )
    task_result.record = record
    if record is not None:
        _safe_optional_call(reporter, "on_task_record", task.task_id, record)

    _safe_call(reporter, "on_task_end", task.task_id, task_result.overall)
    return task_result


def _execute_stages(
    *,
    task: TaskConfig,
    project: ProjectConfig,
    stages: list[str],
    task_result: TaskResult,
    exec_ctx: _TaskExecCtx,
    context: dict[str, Any],
    resolved_env: dict[str, str],
    subprocess_env: dict[str, str],
    tools: dict[str, Tool],
    cli_knobs: dict[str, dict[str, Any]],
    dry_run: bool,
    cancel_token: CancelToken,
    auto_ext_root: Path,
    reporter: ProgressReporter,
    run_dir: Path,
) -> None:
    """Walk the stage list, appending a :class:`StageResult` for each."""

    abort = False
    cancel_seen = False  # once set: first stage marked CANCELLED, rest SKIPPED

    for stage in stages:
        # Pre-stage cancel check: short-circuit before any rendering or
        # subprocess spawn.
        if not cancel_seen and cancel_token.is_cancelled():
            # First stage hit by cancel → CANCELLED; subsequent → SKIPPED.
            _append_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.CANCELLED, "run cancelled by user", run_dir=run_dir,
            )
            cancel_seen = True
            continue

        if cancel_seen:
            _append_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.SKIPPED, "aborted after cancellation", run_dir=run_dir,
            )
            continue

        if stage == "jivaro" and not task.jivaro.enabled:
            _append_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.SKIPPED, "jivaro disabled for task", run_dir=run_dir,
            )
            continue

        if abort:
            _append_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.SKIPPED, "aborted after earlier stage failure",
                run_dir=run_dir,
            )
            continue

        _safe_call(reporter, "on_stage_start", task.task_id, stage)
        _safe_store(append_event, run_dir, {"event": "stage_start", "stage": stage})
        sr = _run_single_stage(
            stage=stage,
            project=project,
            task=task,
            tool=tools[stage],
            exec_ctx=exec_ctx,
            context=context,
            resolved_env=resolved_env,
            subprocess_env=subprocess_env,
            cli_knobs=cli_knobs,
            dry_run=dry_run,
            cancel_token=cancel_token,
            auto_ext_root=auto_ext_root,
        )
        # If the subprocess was hard-killed by cancel, reclassify FAILED
        # as CANCELLED so the summary distinguishes "user stopped us"
        # from "the tool errored".
        if sr.status == StageStatus.FAILED and cancel_token.is_cancelled():
            error = sr.error or "stage terminated by user cancellation"
            record = sr.record
            if record is not None:
                record = record.model_copy(
                    update={"status": StageStatus.CANCELLED, "error": error}
                )
            sr = StageResult(
                stage=sr.stage,
                status=StageStatus.CANCELLED,
                tool_result=sr.tool_result,
                error=error,
                record=record,
            )
            cancel_seen = True

        task_result.stages.append(sr)
        _safe_store(append_event, run_dir, _stage_end_event(sr))
        _safe_call(reporter, "on_stage_end", task.task_id, stage, sr.status, sr.error)

        if sr.status == StageStatus.FAILED:
            if stage == "calibre" and task.continue_on_lvs_fail:
                logger.warning(
                    "task %s: calibre failed but continue_on_lvs_fail=True; proceeding",
                    task.task_id,
                )
            else:
                abort = True
        elif sr.status == StageStatus.CANCELLED:
            cancel_seen = True


def _append_synthetic_stage(
    task_result: TaskResult,
    reporter: ProgressReporter,
    task_id: str,
    stage: str,
    status: StageStatus,
    reason: str,
    *,
    run_dir: Path,
) -> None:
    """Append a skipped/cancelled :class:`StageResult` and emit both events.

    Both the StageResult bookkeeping and the ``on_stage_start`` /
    ``on_stage_end`` pair happen here so callers don't accidentally
    emit one without the other — a GUI tree that sees ``on_stage_start``
    without an end gets stuck on "running" forever.

    The synthetic stage still gets a :class:`StageRecord`: a run whose
    ``stages`` list silently omitted everything that was skipped would be
    unreadable six months later, when "did jivaro not run, or did it not get
    recorded?" is exactly the question being asked.
    """
    _safe_call(reporter, "on_stage_start", task_id, stage)
    moment = utcnow()
    record = StageRecord(
        key=stage,
        stage=stage,
        status=status,
        started_at=moment,
        ended_at=moment,
        duration_s=0.0,
        error=reason if status in (StageStatus.CANCELLED, StageStatus.FAILED) else None,
        skip_reason=reason,
    )
    result = StageResult(stage=stage, status=status, error=reason, record=record)
    task_result.stages.append(result)
    _safe_store(append_event, run_dir, _stage_end_event(result))
    _safe_call(reporter, "on_stage_end", task_id, stage, status, reason)


def _run_single_stage(
    *,
    stage: str,
    project: ProjectConfig,
    task: TaskConfig,
    tool: Tool,
    exec_ctx: _TaskExecCtx,
    context: dict[str, Any],
    resolved_env: dict[str, str],
    subprocess_env: dict[str, str],
    cli_knobs: dict[str, dict[str, Any]],
    dry_run: bool,
    cancel_token: CancelToken,
    auto_ext_root: Path,
) -> StageResult:
    log_path = exec_ctx.paths.logs / f"{stage}.log"
    started_at = utcnow()
    started_perf = time.perf_counter()

    def _finish(
        status: StageStatus,
        *,
        tool_result: ToolResult | None = None,
        error: str | None = None,
        rendered: Path | None = None,
        argv: list[str] | None = None,
        logged: bool = False,
    ) -> StageResult:
        record = StageRecord(
            key=stage,
            stage=stage,
            status=status,
            started_at=started_at,
            ended_at=utcnow(),
            duration_s=round(time.perf_counter() - started_perf, 3),
            argv=list(argv or []),
            cwd=str(exec_ctx.cwd),
            exit_code=_exit_code_of(tool_result),
            log_path=_run_relative(exec_ctx.run_dir, log_path) if logged else None,
            rendered_path=_run_relative(exec_ctx.run_dir, rendered),
            artifacts=[str(p) for p in (tool_result.artifact_paths if tool_result else [])],
            details=_jsonable_diagnostics(tool_result),
            error=error,
        )
        return StageResult(
            stage=stage,
            status=status,
            tool_result=tool_result,
            error=error,
            record=record,
        )

    rendered_path: Path
    if tool.has_template:
        template_path = _resolve_template_path(task, stage, auto_ext_root=auto_ext_root)
        if template_path is None:
            return _finish(
                StageStatus.FAILED,
                error=(
                    f"no template configured for {stage}: neither project.templates.{stage} "
                    f"nor task.templates.{stage} is set"
                ),
            )
        try:
            manifest = load_manifest(template_path)
            stage_knobs = resolve_knob_values(
                manifest,
                project_knobs=project.knobs.get(stage, {}),
                task_knobs=task.knobs.get(stage, {}),
                cli_knobs=cli_knobs.get(stage, {}),
            )
            rendered_path = tool.render_template(
                template_path=template_path,
                context=context,
                env=resolved_env,
                out_path=exec_ctx.paths.rendered / template_path.stem,
                knobs=stage_knobs,
            )
        except AutoExtError as exc:
            return _finish(StageStatus.FAILED, error=f"render failed: {exc}")
        if stage == "si":
            _archive_si_env(rendered_path, exec_ctx.paths.rendered)
        rendered_record: Path | None = rendered_path
    else:
        # strmout consumes output_dir / layer_map directly; build_argv still
        # wants a path, but there is no rendered file to point a record at.
        rendered_path = exec_ctx.paths.rendered
        rendered_record = None

    if dry_run:
        return _finish(StageStatus.DRY_RUN, rendered=rendered_record)

    argv = tool.build_argv(rendered_path, context)

    try:
        if stage == "si":
            if exec_ctx.parallel:
                # Parallel: each run owns its cwd, so si.env is placed
                # directly inside it with no cleanup contention.
                place_si_env_in_parallel_dir(exec_ctx.cwd, rendered_path)
                raw = tool.run(
                    argv, cwd=exec_ctx.cwd, env=subprocess_env,
                    log_path=log_path, cancel_token=cancel_token,
                )
            else:
                # Serial: swap rendered si.env into workarea/si.env for
                # the stage, clean up on exit so tasks don't step on
                # each other (even sequentially).
                with serial_workdir(exec_ctx.cwd, rendered_path):
                    raw = tool.run(
                        argv, cwd=exec_ctx.cwd, env=subprocess_env,
                        log_path=log_path, cancel_token=cancel_token,
                    )
            # Publish rendered si.env into output_dir only on success.
            # On a failed or cancelled si, leaving a stale si.env where
            # Quantus (or a retry) would read it is worse than the
            # missing-file error Quantus would throw on retry.
            if raw.success:
                _publish_si_env_to_output_dir(
                    rendered_path, Path(context["output_dir"])
                )
        else:
            raw = tool.run(
                argv, cwd=exec_ctx.cwd, env=subprocess_env,
                log_path=log_path, cancel_token=cancel_token,
            )
        result = tool.parse_result(raw)
    except AutoExtError as exc:
        return _finish(
            StageStatus.FAILED, error=str(exc), rendered=rendered_record,
            argv=argv, logged=log_path.exists(),
        )

    status = StageStatus.PASSED if result.success else StageStatus.FAILED
    return _finish(
        status, tool_result=result, rendered=rendered_record, argv=argv,
        logged=log_path.exists(),
    )


# ---- run record assembly ---------------------------------------------------


def _derive_recipe_id(task: TaskConfig) -> str:
    """Name the effective configuration for the run directory.

    S1 has no ``Recipe`` object yet, so the closest stand-in for "which
    configuration is this" is the template that shapes the extraction. The
    first configured stage template wins, in the order the stages differ
    most: quantus, then calibre, then si, then jivaro. ``ext.cmd.j2``
    becomes ``ext``; a task with no templates at all becomes ``adhoc``,
    which still produces a well-formed slug.
    """

    for stage in ("quantus", "calibre", "si", "jivaro"):
        raw = getattr(task.templates, stage, None)
        if not raw:
            continue
        base = Path(str(raw)).name.split(".")[0]
        if base:
            return slugify(base, max_len=28)
    return "adhoc"


def _recipe_snapshot(
    project: ProjectConfig,
    task: TaskConfig,
    context: dict[str, Any],
    cli_knobs: dict[str, dict[str, Any]],
    *,
    auto_ext_root: Path,
    recipe_id: str,
) -> RecipeSnapshot:
    """Freeze the effective configuration of this run.

    Everything needed to explain the rendered files afterwards: which
    template each stage used, the merged knob values, the jivaro settings,
    the ``dspf_out_path`` *expression* (its resolved value is
    :attr:`RunRecord.dspf_path`) and the resolved ``project.paths`` entries.
    Editing ``project.yaml`` tomorrow cannot rewrite this.

    Knob resolution is best-effort: a template whose manifest cannot be read
    is left out of ``knobs`` rather than aborting here, because the same
    failure is about to be reported properly — with its stage attached — when
    that stage renders.
    """

    templates: dict[str, str] = {}
    knobs: dict[str, dict[str, JsonScalar]] = {}
    for stage in ("si", "calibre", "quantus", "jivaro"):
        template_path = _resolve_template_path(task, stage, auto_ext_root=auto_ext_root)
        if template_path is None:
            continue
        templates[stage] = str(template_path)
        try:
            manifest = load_manifest(template_path)
            values = resolve_knob_values(
                manifest,
                project_knobs=project.knobs.get(stage, {}),
                task_knobs=task.knobs.get(stage, {}),
                cli_knobs=cli_knobs.get(stage, {}),
            )
        except AutoExtError as exc:
            logger.debug("recipe snapshot: no knobs for %s (%s)", stage, exc)
            continue
        if values:
            knobs[stage] = {k: _scalar(v) for k, v in values.items()}

    return RecipeSnapshot(
        recipe_id=recipe_id,
        name=task.label,
        templates=templates,
        knobs=knobs,
        jivaro=JivaroSnapshot(
            enabled=task.jivaro.enabled,
            frequency_limit=task.jivaro.frequency_limit,
            error_max=task.jivaro.error_max,
        ),
        dspf_out_path=task.dspf_out_path or project.dspf_out_path,
        paths={k: str(context[k]) for k in project.paths if k in context},
    )


def _finalize_run(
    *,
    run_dir: Path,
    paths: RunPaths,
    base_fields: dict[str, Any],
    task_result: TaskResult,
) -> RunRecord | None:
    """Rescue the evidence, write the final ``run.json``, and return it.

    Two things happen here that cannot happen earlier:

    - **Evidence is archived.** The Cadence workspace holds gigabytes of
      intermediates and is rewritten by the next run of the same cell; the
      run directory keeps the small, decisive artifacts. Concretely: the
      Calibre LVS report is copied to ``results/lvs.report`` and a derived
      ``results/lvs_summary.json`` is written beside it. The rendered inputs
      need no rescue — under this layout they were written into
      ``runs/<run_id>/rendered/`` in the first place — and the workarea paths
      of everything too large to copy are recorded in
      :attr:`StageRecord.artifacts`.
    - **The record becomes immutable.** ``run.json`` is rewritten once, with
      ``overwrite=True``, and never touched again.

    Returns ``None`` only if the record could not be assembled at all; a
    failure to *write* it is logged and the in-memory record still returned,
    because the caller (and the GUI) can still use it.
    """

    stage_records = [s.record for s in task_result.stages if s.record is not None]
    lvs = _archive_lvs(paths, task_result)
    ended_at = utcnow()

    try:
        record = RunRecord(
            **base_fields,
            ended_at=ended_at,
            overall=task_result.overall,
            stages=stage_records,
            results=RunResults(lvs=lvs),
            cancelled_by=("user" if task_result.overall == TaskStatus.CANCELLED else None),
        )
    except Exception:  # noqa: BLE001 — a broken record must not kill the run
        logger.exception("cannot assemble run record for %s", run_dir)
        return None

    _safe_store(write_record, run_dir, record, overwrite=True)
    _safe_store(
        append_event,
        run_dir,
        {
            "event": "run_end",
            "run_id": record.run_id,
            "overall": str(record.overall),
            "duration_s": record.duration_s,
        },
    )
    return record


def _archive_lvs(paths: RunPaths, task_result: TaskResult) -> LvsResult | None:
    """Copy the Calibre LVS report into ``results/`` and build the typed result.

    ``core/checks.py`` parses the report in detail — banner, discrepancy
    count, CELL SUMMARY fallback — and until now that landed in
    ``ToolResult.diagnostics["lvs_report"]`` where nothing read it. It becomes
    :attr:`RunRecord.results.lvs`, and the report itself is copied out of the
    workarea before the next run of this cell overwrites it.
    """

    report = None
    for sr in task_result.stages:
        if sr.stage != "calibre" or sr.tool_result is None:
            continue
        candidate = sr.tool_result.diagnostics.get("lvs_report")
        if candidate is not None:
            report = candidate
            break
    if report is None:
        return None

    archived: str | None = None
    source = Path(str(getattr(report, "source", "")))
    if source.is_file():
        try:
            paths.results.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, paths.results / LVS_REPORT_NAME)
            archived = f"{paths.results.name}/{LVS_REPORT_NAME}"
        except OSError as exc:
            logger.warning("cannot archive LVS report %s: %s", source, exc)

    try:
        lvs = LvsResult.from_lvs_report(report, archived_path=archived)
    except Exception:  # noqa: BLE001 — diagnostics is an open bag
        logger.exception("cannot build LvsResult from %r", report)
        return None

    try:
        paths.results.mkdir(parents=True, exist_ok=True)
        (paths.results / LVS_SUMMARY_NAME).write_text(
            lvs.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    except OSError as exc:
        logger.warning("cannot write %s: %s", paths.results / LVS_SUMMARY_NAME, exc)
    return lvs


def _archive_si_env(rendered_path: Path, rendered_dir: Path) -> None:
    """Keep a copy of the si control file under its canonical name.

    The rendered file is named after its template (``default.env``), but what
    si reads — and what gets published into the Cadence workspace to dodge
    Quantus LBRCXM-756 — is ``si.env``. Archiving it under that name makes
    the run directory readable without knowing which template produced it.
    """

    if rendered_path.name == SI_ENV_ARCHIVE_NAME:
        return
    try:
        shutil.copy2(rendered_path, rendered_dir / SI_ENV_ARCHIVE_NAME)
    except OSError as exc:
        logger.warning("cannot archive si.env in %s: %s", rendered_dir, exc)


def _new_batch_id(moment: datetime) -> str:
    """``<timestamp>_batch-<random>`` — same shape as a run id.

    The random tail is what keeps two dispatches started in the same second
    from writing over each other's index file.
    """

    return f"{moment.strftime(RUN_TIMESTAMP_FORMAT)}_batch-{uuid.uuid4().hex[:6]}"


def _write_batch_index(
    runs_root: Path,
    batch_id: str,
    created_at: datetime,
    summary: RunSummary,
    max_workers: int,
) -> None:
    """Write ``runs/batches/<batch_id>.json`` listing this dispatch's runs."""

    run_ids = [t.record.run_id for t in summary.tasks if t.record is not None]
    _safe_store(
        write_batch,
        runs_root,
        RunBatch(
            batch_id=batch_id,
            created_at=created_at,
            ended_at=utcnow(),
            run_ids=run_ids,
            max_workers=max_workers,
        ),
    )


def _stage_end_event(sr: StageResult) -> dict[str, Any]:
    record = sr.record
    return {
        "event": "stage_end",
        "stage": sr.stage,
        "status": str(sr.status),
        "duration_s": record.duration_s if record else None,
        "exit_code": record.exit_code if record else None,
        "error": sr.error,
    }


def _run_relative(run_dir: Path, path: Path | None) -> str | None:
    """POSIX path of ``path`` relative to ``run_dir``, or ``None``.

    Paths outside the run directory return ``None`` rather than a ``..``
    chain: :class:`StageRecord` rejects those on purpose, so that a run
    directory stays self-contained when it is copied elsewhere.
    """

    if path is None:
        return None
    try:
        return path.relative_to(run_dir).as_posix()
    except ValueError:
        logger.debug("%s is outside run dir %s; not recorded", path, run_dir)
        return None


def _exit_code_of(result: ToolResult | None) -> int | None:
    if result is None:
        return None
    value = result.diagnostics.get("exit_code")
    return value if isinstance(value, int) else None


def _scalar(value: Any) -> JsonScalar:
    """Coerce to something a JSON scalar field accepts."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _jsonable(value: Any) -> Any:
    """Recursively coerce ``value`` into JSON-safe types.

    ``ToolResult.diagnostics`` is an open bag: it carries ints, argv lists,
    :class:`~pathlib.Path` objects and — from ``CalibreTool.parse_result`` —
    a whole :class:`~auto_ext.core.checks.LvsReport` dataclass. All of it goes
    into :attr:`StageRecord.details` verbatim, which means all of it has to
    survive ``json.dumps``.
    """

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


def _jsonable_diagnostics(result: ToolResult | None) -> dict[str, Any]:
    if result is None:
        return {}
    return {str(k): _jsonable(v) for k, v in result.diagnostics.items()}


def _jsonable_context(context: dict[str, Any]) -> dict[str, JsonScalar]:
    return {str(k): _scalar(v) for k, v in context.items()}


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _hostname() -> str | None:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover - gethostname failing is exotic
        return None


def _resolve_tool_paths(
    tools: Mapping[str, Tool], subprocess_env: Mapping[str, str]
) -> dict[str, str]:
    """Record which binary each stage would actually invoke.

    Resolved through the same ``PATH`` the subprocess will see, so the record
    answers "which calibre was this?" months later, when the module
    environment has moved on. An unresolvable name is stored bare rather than
    omitted — "we looked for `qrc` and found nothing" is itself the answer.
    """

    resolved: dict[str, str] = {}
    for name, tool in tools.items():
        executable = getattr(tool, "executable", None)
        if not executable:
            continue
        found = shutil.which(executable, path=subprocess_env.get("PATH"))
        resolved[name] = found or executable
    return resolved


class _NullContext:
    """Stand-in for the workspace lock when a dry run must not touch anything."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return False


# ---- helpers ---------------------------------------------------------------


def _safe_call(reporter: ProgressReporter, method: str, *args: Any) -> None:
    """Invoke ``reporter.<method>(*args)``, logging and swallowing exceptions.

    A reporter that raises must never abort a running subprocess — this
    is especially important for the Qt reporter during UI development,
    where a slot raising could otherwise tear down an expensive EDA run.
    """
    try:
        getattr(reporter, method)(*args)
    except Exception:  # noqa: BLE001 — intentional broad catch
        logger.exception("reporter.%s raised; ignoring", method)


def _safe_optional_call(reporter: ProgressReporter, method: str, *args: Any) -> None:
    """Like :func:`_safe_call`, but silent when the reporter lacks the method.

    The run-layer events (:class:`~auto_ext.core.progress.RunAwareReporter`)
    are optional; a reporter written before they existed must not produce a
    logged AttributeError on every single stage.
    """
    if not hasattr(reporter, method):
        return
    _safe_call(reporter, method, *args)


def _safe_store(fn: Any, *args: Any, **kwargs: Any) -> None:
    """Run a run-store write, logging and swallowing storage failures.

    Bookkeeping is worth a lot, but not as much as the multi-hour extraction
    it is describing: a full disk must not abort a Calibre run that is
    otherwise going fine.
    """
    try:
        fn(*args, **kwargs)
    except (AutoExtError, OSError):
        logger.exception("run store: %s failed; continuing", getattr(fn, "__name__", fn))


def _resolve_template_path(
    task: TaskConfig, stage: str, *, auto_ext_root: Path | None = None
) -> Path | None:
    """Return the template path for this task's stage.

    ``TaskConfig.templates`` has fields named after the four templated
    tools (``si``, ``calibre``, ``quantus``, ``jivaro``). Phase 2's
    ``_merge_templates`` already collapsed project-level defaults into
    the task's copy, so a single attribute lookup suffices.

    Relative paths are resolved via :func:`resolve_template_path` so
    auto_ext-root-relative entries work without requiring the deploy
    directory name in every ``project.templates`` value.
    """
    raw = getattr(task.templates, stage, None)
    if raw is None:
        return None
    return resolve_template_path(raw, auto_ext_root=auto_ext_root)


def _build_context(
    project: ProjectConfig,
    task: TaskConfig,
    resolved_env: dict[str, str],
    *,
    run_slug: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the Jinja render context for one task.

    ``run_slug`` / ``run_id`` are not context keys — they are format keys
    available to ``extraction_output_dir`` for users who want one Cadence
    workspace per run. They are passed through to :func:`_resolve_output_dir`
    and nowhere else, so the Jinja context stays exactly the set that
    :data:`auto_ext.core.manifest._IDENTITY_KEYS` and the GUI's
    ``jinja_variable_status`` know about.
    """
    output_dir = _resolve_output_dir(
        project, task, resolved_env, run_slug=run_slug, run_id=run_id
    )
    intermediate_tpl = substitute_env(project.intermediate_dir, resolved_env)
    intermediate_dir = intermediate_tpl.format(cell=task.cell, library=task.library)
    layer_map = substitute_env(str(project.layer_map), resolved_env)

    employee_id = (
        project.employee_id
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "unknown"
    )

    tech_name = project.tech_name or derive_parent_dir_from_env_candidates(
        project.tech_name_env_vars, resolved_env
    )

    ctx: dict[str, Any] = {
        "library": task.library,
        "cell": task.cell,
        "lvs_source_view": task.lvs_source_view,
        "lvs_layout_view": task.lvs_layout_view,
        "ground_net": task.ground_net,
        "out_file": task.out_file,
        "task_id": task.task_id,
        "output_dir": output_dir,
        "intermediate_dir": intermediate_dir,
        "layer_map": layer_map,
        "employee_id": employee_id,
        "jivaro_frequency_limit": task.jivaro.frequency_limit,
        "jivaro_error_max": task.jivaro.error_max,
        "tech_name": tech_name,
    }

    # Resolve every project.paths.* entry and expose it under the same
    # key in the Jinja context. Auto-derive ``calibre_lvs_basename`` from
    # ``calibre_lvs_dir`` (PDK convention: rules-file basename = LVS
    # subdir basename); user can override by setting paths.calibre_lvs_basename
    # explicitly when their PDK breaks the convention.
    for key, expr in project.paths.items():
        ctx[key] = resolve_path_expr(expr, resolved_env)

    if "calibre_lvs_dir" in ctx and "calibre_lvs_basename" not in ctx:
        ctx["calibre_lvs_basename"] = PurePosixPath(ctx["calibre_lvs_dir"]).name

    # dspf_out_path: resolve last so its value can reference any of the
    # other path tokens (output_dir, intermediate_dir, layer_map,
    # paths.* entries) via ``${X}`` syntax. Per-task override beats the
    # project default.
    ctx["dspf_out_path"] = _resolve_dspf_out_path(project, task, resolved_env, ctx)

    return ctx


# Synthetic context tokens that ``dspf_out_path`` may reference via ``${X}``.
# These are *not* shell env vars — the runner injects them into the
# substitute_env env dict at render time. Excluded from env discovery so
# resolve_env does not log "missing" warnings for them.
_PATH_TOKEN_NAMES: frozenset[str] = frozenset({
    "output_dir",
    "intermediate_dir",
    "layer_map",
    "calibre_lvs_dir",
    "calibre_lvs_basename",
    "qrc_deck_dir",
})


def _build_path_token_env(
    resolved_env: dict[str, str], ctx_so_far: dict[str, Any]
) -> dict[str, str]:
    """Merge resolved env vars with already-resolved path-context values.

    Used by :func:`_resolve_dspf_out_path` (and the GUI preview helper)
    so a ``dspf_out_path`` value like ``${output_dir}/{cell}.dspf`` can
    reach the resolved ``output_dir`` string through the same
    :func:`substitute_env` machinery that handles ordinary env vars.
    Path-token entries win over env-var entries on a name collision so
    ``${output_dir}`` always picks the runner-resolved value rather than
    a stray shell var with the same name.
    """
    merged: dict[str, str] = dict(resolved_env)
    for key in _PATH_TOKEN_NAMES:
        v = ctx_so_far.get(key)
        if v is not None:
            merged[key] = str(v)
    # Surface every project.paths.* entry too — users can add custom keys.
    for key, value in ctx_so_far.items():
        if isinstance(value, str) and key not in merged:
            merged[key] = value
    return merged


_DSPF_FORMAT_KEYS: frozenset[str] = frozenset({"cell", "library", "task_id"})

# Match ``{name}`` / ``${name}`` so we can selectively escape the ones
# that are not in :data:`_DSPF_FORMAT_KEYS` before invoking str.format.
# Identifier-only — keeps the pattern unambiguous against legitimate
# format-spec slots like ``{cell:>20}``.
_DSPF_BRACE_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Detect surviving env references after :func:`substitute_env` to
# build the ``unresolved: $X[, $Y]`` annotation. Patterns mirror
# :mod:`auto_ext.core.env`'s ``_RE_ENV_BRACE`` / ``_RE_ENV_TCL`` /
# ``_RE_ENV_BARE`` but we duplicate them here so this helper stays
# self-contained (``env.py`` keeps those names private).
_DSPF_UNRESOLVED_BRACE = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DSPF_UNRESOLVED_TCL = re.compile(r"(?<!\$)\$env\(([A-Za-z_][A-Za-z0-9_]*)\)")
_DSPF_UNRESOLVED_BARE = re.compile(
    r"(?<!\$)\$(?!env\()([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_])"
)


def resolve_dspf_path(
    raw: str,
    extended_env: dict[str, str],
    *,
    cell: str,
    library: str,
    task_id: str,
) -> tuple[str, str | None]:
    """Two-phase resolve a ``dspf_out_path`` template — shared by runner + GUI.

    Step 1: :func:`substitute_env` against ``extended_env`` (which
    callers compose by layering path tokens / project paths on top of
    ``resolved_env``).

    Step 2: pre-escape unresolved ``${X}`` brace pairs so they don't
    poison ``str.format``; then format with ``cell`` / ``library`` /
    ``task_id``.

    Returns ``(text, error_msg_or_None)``:

    - On full success: ``(resolved_path, None)``.
    - When some env reference (``${X}``, ``$env(X)``, or bare ``$X``)
      is unresolved: ``(best_effort_path, "unresolved: $X[, $Y]")``.
      ``text`` still went through .format (so format keys resolve
      around the literal ``$X``).
    - When a truly unknown ``{X}`` format key (no ``$`` prefix) is
      present: ``(safe_template_after_escape, "unknown format key {X}")``.

    Callers that need fail-fast behaviour (the runner) wrap this and
    raise :class:`ConfigError` on either error class. The GUI surfaces
    both inline in the preview label.
    """
    if not raw:
        return "", "empty"

    after_env = substitute_env(raw, extended_env)

    # Collect each surviving env-reference identifier; deduplicate so
    # ``${X}/$X/$env(X)`` produces a single ``$X`` annotation. Order is
    # stable (sorted) for predictable error messages in tests.
    unresolved_names: set[str] = set()
    for pat in (
        _DSPF_UNRESOLVED_BRACE,
        _DSPF_UNRESOLVED_TCL,
        _DSPF_UNRESOLVED_BARE,
    ):
        unresolved_names.update(pat.findall(after_env))
    unresolved = [f"${n}" for n in sorted(unresolved_names)]

    def _escape_unknown(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in _DSPF_FORMAT_KEYS:
            return m.group(0)
        # Was this brace pair part of an unresolved ``${X}``? If so,
        # restore the literal by doubling the braces so str.format emits
        # them verbatim. Otherwise pass through and let .format raise.
        start = m.start()
        if start > 0 and after_env[start - 1] == "$":
            return "{{" + name + "}}"
        return m.group(0)

    safe = _DSPF_BRACE_PATTERN.sub(_escape_unknown, after_env)

    try:
        formatted = safe.format(cell=cell, library=library, task_id=task_id)
    except KeyError as exc:
        return safe, f"unknown format key {{{exc.args[0]}}}"
    except (IndexError, ValueError) as exc:
        return safe, f"format error: {exc}"

    if unresolved:
        return formatted, f"unresolved: {', '.join(unresolved)}"
    return formatted, None


def _resolve_dspf_out_path(
    project: ProjectConfig,
    task: TaskConfig,
    resolved_env: dict[str, str],
    ctx_so_far: dict[str, Any],
) -> str:
    """Resolve ``dspf_out_path`` (per-task override > project default).

    Thin wrapper over :func:`resolve_dspf_path`. Unresolved ``${X}`` /
    ``$env(X)`` / bare ``$X`` references pass through verbatim
    (matches :func:`substitute_env` semantics — by the time we get
    here, ``_discover_env_vars`` + ``resolve_env.require()`` would
    have already raised on truly missing vars). Only an unknown
    ``{X}`` format key (no ``$`` prefix) raises :class:`ConfigError`.
    """
    raw = task.dspf_out_path or project.dspf_out_path
    extended_env = _build_path_token_env(resolved_env, ctx_so_far)
    text, error = resolve_dspf_path(
        raw,
        extended_env,
        cell=task.cell,
        library=task.library,
        task_id=task.task_id,
    )
    if error is None or error.startswith("unresolved:"):
        return text
    if error.startswith("unknown format key"):
        # Mirror the previous wording for backwards-compatible test
        # assertions: "uses unknown format key 'X'; supported: ...".
        key = error.removeprefix("unknown format key {").rstrip("}")
        raise ConfigError(
            f"dspf_out_path uses unknown format key {key!r}; "
            "supported: cell, library, task_id"
        )
    raise ConfigError(f"dspf_out_path {error}")


def _discover_env_vars(
    project: ProjectConfig,
    tasks: list[TaskConfig],
    *,
    auto_ext_root: Path | None = None,
) -> set[str]:
    sources: list[str] = [
        project.extraction_output_dir,
        project.intermediate_dir,
        project.dspf_out_path,
        str(project.layer_map),
    ]
    # paths.* values typically reference $X env vars; surface them so
    # check-env / preflight catches missing ones up-front.
    sources.extend(project.paths.values())
    # Per-task dspf_out_path overrides may reference yet more env vars.
    for task in tasks:
        if task.dspf_out_path is not None:
            sources.append(task.dspf_out_path)
    seen: set[Path] = set()
    for task in tasks:
        for stage in ("si", "calibre", "quantus", "jivaro"):
            tp = getattr(task.templates, stage, None)
            if tp is None or tp in seen:
                continue
            seen.add(tp)
            resolved = resolve_template_path(tp, auto_ext_root=auto_ext_root)
            try:
                sources.append(resolved.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ConfigError(f"cannot read template {tp}: {exc}") from exc
    required = discover_required_vars(sources)
    # ``dspf_out_path`` (and friends) may reference synthetic path tokens
    # like ``${output_dir}`` that are injected by the runner at render
    # time, not real shell vars. Strip them so resolve_env does not log
    # "missing" warnings for tokens that will be supplied later.
    # ``project.paths.*`` keys are also valid synthetic tokens.
    required -= _PATH_TOKEN_NAMES
    required -= set(project.paths.keys())
    # tech_name auto-derive still walks env-var candidates when unset.
    if project.tech_name is None:
        required.update(project.tech_name_env_vars)
    return required


def _validate_stages(stages: list[str]) -> None:
    if not stages:
        raise ConfigError("stages list is empty")
    unknown = set(stages) - set(STAGE_ORDER)
    if unknown:
        raise ConfigError(
            f"unknown stage(s): {sorted(unknown)}; valid: {list(STAGE_ORDER)}"
        )


def _validate_tasks(tasks: list[TaskConfig], stages: list[str]) -> None:
    if not tasks:
        raise ConfigError("no tasks to run")
    if "jivaro" in stages:
        for t in tasks:
            if t.jivaro.enabled and t.out_file is None:
                raise ConfigError(
                    f"task {t.task_id}: jivaro enabled but out_file is not set "
                    "(jivaro inputView renders to library/cell/out_file)"
                )


def _publish_si_env_to_output_dir(rendered_si_env: Path, output_dir: Path) -> None:
    """Copy the rendered ``si.env`` into ``output_dir`` after a successful si run.

    Quantus errors with LBRCXM-756 when its ``-cdl_out_map_directory``
    (``= output_dir``) is missing ``si.env``. si writes the netlist +
    ``map/`` + ``ihnl/`` to ``simRunDir = output_dir`` but not a copy
    of its own control file, so the runner stages it over. The caller
    (:func:`_run_single_stage`) only invokes this on ``raw.success``:
    publishing on failure or cancel would leave stale state for the
    next Quantus run or retry.

    The run's own archive copy lives at ``rendered/si.env``
    (:func:`_archive_si_env`) — this one is overwritten by the next run of
    the cell, that one is not.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rendered_si_env, output_dir / "si.env")


_OUTPUT_DIR_FORMAT_KEYS: tuple[str, ...] = (
    "cell",
    "library",
    "task_id",
    "lvs_layout_view",
    "lvs_source_view",
    "run_slug",
    "run_id",
)


def _resolve_output_dir(
    project: ProjectConfig,
    task: TaskConfig,
    resolved_env: dict[str, str],
    *,
    run_slug: str | None = None,
    run_id: str | None = None,
) -> str:
    """Substitute env vars + format keys in ``project.extraction_output_dir``.

    Format keys: ``{cell}``, ``{library}``, ``{task_id}``,
    ``{lvs_layout_view}``, ``{lvs_source_view}``, ``{run_slug}``, ``{run_id}``.
    The default pattern uses only ``{cell}``, which means every run of that
    cell reuses one Cadence workspace — correct, and cheap, since the
    workspace holds gigabytes of regenerable intermediates. A user who wants
    hard isolation instead (keep two parameter sweeps of one cell side by
    side, run them concurrently) writes ``QCI_PATH_{cell}_{run_slug}`` or
    ``..._{run_id}`` and gets a fresh workspace per run.

    ``run_slug`` / ``run_id`` are only known once the run directory is
    claimed. A pattern that references them while they are unavailable
    raises :class:`ConfigError` rather than quietly formatting an empty
    string into the path.

    Env vars must be resolved before this runs — Python ``str.format``
    would otherwise treat ``{WORK_ROOT}`` (from an unresolved
    ``${WORK_ROOT}``) as a format field and raise ``KeyError``.
    """
    tpl = substitute_env(project.extraction_output_dir, resolved_env)

    missing = [
        name
        for name, value in (("run_slug", run_slug), ("run_id", run_id))
        if value is None and f"{{{name}}}" in tpl
    ]
    if missing:
        raise ConfigError(
            f"extraction_output_dir references {missing} but no run has been "
            "allocated in this context"
        )

    try:
        return tpl.format(
            cell=task.cell,
            library=task.library,
            task_id=task.task_id,
            lvs_layout_view=task.lvs_layout_view,
            lvs_source_view=task.lvs_source_view,
            run_slug=run_slug or "",
            run_id=run_id or "",
        )
    except KeyError as exc:
        raise ConfigError(
            f"extraction_output_dir uses unknown format key {exc.args[0]!r}; "
            f"supported: {list(_OUTPUT_DIR_FORMAT_KEYS)}"
        ) from exc


def _validate_task_outputs(
    tasks: list[TaskConfig],
    project: ProjectConfig,
    resolved_env: dict[str, str],
    *,
    parallel: bool,
) -> None:
    """Decide what to do about tasks that share one Cadence workspace.

    This check used to reject *any* two tasks resolving to the same
    ``extraction_output_dir``, because back then the workspace was also the
    run's identity: two configurations of one cell would overwrite each
    other's logs and rendered inputs with no trace. That is no longer true —
    each run owns ``runs/<run_id>/`` — so:

    - **Serial**: sharing is legal and expected. Two recipes for one cell
      reuse the workspace one after the other, which is exactly how a
      parameter sweep should behave. Logged at info level, never refused.
    - **Parallel**: sharing is refused. Two Calibre runs writing one ``svdb``
      concurrently corrupt both, and silently serialising them behind a lock
      would look like a hang. The fix is in the message: add a discriminator
      key to the pattern.

    Cross-*process* contention (a second Auto_ext started from another shell)
    is not visible here at all; that is what
    :func:`auto_ext.core.workdir.workspace_lock` covers at run time.

    ``{run_slug}`` / ``{run_id}`` in the pattern are resolved per task with
    the slug that task's run will actually get, and a unique placeholder for
    the run id — a pattern keyed on ``{run_id}`` can never collide, since no
    two runs share one.
    """
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for index, t in enumerate(tasks):
        dut = DutSnapshot.from_task_config(t)
        slug = make_run_slug(dut, RecipeSnapshot(recipe_id=_derive_recipe_id(t)))
        out = _resolve_output_dir(
            project, t, resolved_env, run_slug=slug, run_id=f"@pending-{index}"
        )
        prior = seen.get(out)
        if prior is not None:
            collisions.append(
                f"task_ids {prior!r} and {t.task_id!r} both resolve to "
                f"output_dir {out!r}"
            )
        else:
            seen[out] = t.task_id

    if not collisions:
        return

    if not parallel:
        logger.info(
            "%d task(s) share a Cadence workspace with an earlier task; "
            "they will reuse it in sequence:\n  %s",
            len(collisions),
            "\n  ".join(collisions),
        )
        return

    raise ConfigError(
        "duplicate extraction_output_dir(s) across concurrently dispatched tasks:\n  "
        + "\n  ".join(collisions)
        + "\n\nHint: these tasks would write the same Cadence workspace at the "
        "same time. Run them serially (--jobs 1), or add a discriminator key to "
        f"project.extraction_output_dir (supported: {list(_OUTPUT_DIR_FORMAT_KEYS)})."
    )


def _compute_overall(task_result: TaskResult) -> TaskStatus:
    """Collapse per-stage statuses into an overall task status.

    Precedence: any CANCELLED stage → CANCELLED; else any FAILED →
    FAILED; else PASSED. SKIPPED and DRY_RUN alone don't count as
    failures.
    """
    for s in task_result.stages:
        if s.status == StageStatus.CANCELLED:
            return TaskStatus.CANCELLED
    for s in task_result.stages:
        if s.status == StageStatus.FAILED:
            return TaskStatus.FAILED
    return TaskStatus.PASSED
