"""Task + stage execution driver (serial or parallel).

Loads the resolved env once (fail-fast), instantiates each :class:`Tool`
once, and iterates tasks × stages in the canonical order:
``si`` → ``strmout`` → ``calibre`` → ``quantus`` → ``jivaro``.

Two execution modes:

- **Serial** (default): tasks run one at a time, cwd = ``workarea``.
  ``si.env`` is swapped into ``workarea/si.env`` via
  :func:`serial_workdir` for the duration of the ``si`` stage.
- **Parallel** (``max_workers >= 2``): each task gets its own workdir at
  ``<auto_ext_root>/runs/task_<id>/`` with symlinks to ``workarea/cds.lib``
  and ``workarea/.cdsinit``. All stages for that task run with
  ``cwd = task_dir``; the rendered ``si.env`` is written directly into
  ``task_dir`` with no shared-file mutation. Tasks are dispatched via a
  :class:`concurrent.futures.ThreadPoolExecutor`.

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

Observability (Phase 5.1):

- ``reporter`` (optional :class:`ProgressReporter`) receives lifecycle
  events at run / task / stage boundaries, including synthetic
  start+end pairs for every skipped stage so UI trees stay consistent.
  Reporter exceptions are logged and swallowed — a buggy reporter must
  never tear down a running subprocess.
- ``cancel_token`` (optional :class:`CancelToken`) is checked before
  each stage and forwarded into :func:`run_subprocess`; when set
  mid-subprocess, the in-flight EDA process is terminated (SIGTERM
  with a 10s grace, then SIGKILL) and the stage is marked
  :attr:`StageStatus.CANCELLED`.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_ext.core.config import ProjectConfig, TaskConfig
from auto_ext.core.env import (
    derive_ancestor_dir_from_env_candidates,
    derive_parent_dir_from_env_candidates,
    discover_required_vars,
    resolve_env,
    substitute_env,
)
from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.core.manifest import load_manifest, resolve_knob_values
from auto_ext.core.template import resolve_template_path
from auto_ext.core.progress import (
    CancelToken,
    NullReporter,
    ProgressReporter,
    StageStatus,
    TaskStatus,
)
from auto_ext.core.workdir import (
    place_si_env_in_parallel_dir,
    prepare_parallel_workdir,
    serial_workdir,
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

_UNSAFE_TASK_ID = re.compile(r"[^A-Za-z0-9_.-]")


# ---- result types ----------------------------------------------------------


@dataclass
class StageResult:
    """Per-stage outcome.

    ``status`` is a :class:`StageStatus`; string comparisons (``== "passed"``)
    continue to work because ``StageStatus`` is a :class:`~enum.StrEnum`.
    """

    stage: str
    status: StageStatus
    tool_result: ToolResult | None = None
    error: str | None = None


@dataclass
class TaskResult:
    task_id: str
    stages: list[StageResult] = field(default_factory=list)
    overall: TaskStatus = TaskStatus.PENDING


@dataclass
class RunSummary:
    tasks: list[TaskResult] = field(default_factory=list)

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


@dataclass
class _TaskExecCtx:
    """Per-task execution context: where stages run and how si.env is placed.

    ``parallel=False``: ``cwd`` is the shared workarea; si uses
    :func:`serial_workdir` to swap si.env in/out.
    ``parallel=True``: ``cwd`` is the task's isolated
    ``runs/task_<id>/`` dir; si.env is copied directly into it.
    """

    cwd: Path
    rendered_dir: Path
    log_dir: Path
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
    - Rejects tasks with duplicate ``(library, cell)`` pairs — they would
      share ``extraction_output_dir`` and clobber each other (harmful in
      parallel, misleading in serial).
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
    task isolated under ``<auto_ext_root>/runs/task_<id>/``.

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

    # output_dir collision check needs resolved env so ``${WORK_ROOT}`` is
    # gone before ``str.format`` runs (Python would otherwise interpret
    # ``{WORK_ROOT}`` as a missing format key). Runs after env resolution
    # but before any subprocess; env errors are more fundamental anyway.
    _validate_task_outputs(tasks, project, resolved_env)

    subprocess_env: dict[str, str] = {**os.environ, **project.env_overrides}

    tool_instances: dict[str, Tool] = {name: cls() for name, cls in _TOOL_REGISTRY.items()}

    cli_knobs = cli_knobs or {}
    parallel = max_workers is not None and max_workers >= 2

    summary = RunSummary()
    _safe_call(reporter, "on_run_start", len(tasks), list(stages))

    def _submit(task: TaskConfig) -> TaskResult:
        return _run_single_task(
            project=project,
            task=task,
            stages=stages,
            auto_ext_root=auto_ext_root,
            workarea=workarea,
            resolved_env=resolved_env,
            subprocess_env=subprocess_env,
            tools=tool_instances,
            cli_knobs=cli_knobs,
            verbose=verbose,
            dry_run=dry_run,
            parallel=parallel,
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

    logger.info(
        "run complete: %d/%d passed, %d failed, %d cancelled",
        summary.passed,
        summary.total,
        summary.failed,
        summary.cancelled,
    )
    _safe_call(reporter, "on_run_end", summary)
    return summary


# ---- per-task / per-stage --------------------------------------------------


def _run_single_task(
    *,
    project: ProjectConfig,
    task: TaskConfig,
    stages: list[str],
    auto_ext_root: Path,
    workarea: Path,
    resolved_env: dict[str, str],
    subprocess_env: dict[str, str],
    tools: dict[str, Tool],
    cli_knobs: dict[str, dict[str, Any]],
    verbose: bool,
    dry_run: bool,
    parallel: bool = False,
    reporter: ProgressReporter,
    cancel_token: CancelToken,
) -> TaskResult:
    safe_id = _UNSAFE_TASK_ID.sub("_", task.task_id)
    task_base = auto_ext_root / "runs" / f"task_{safe_id}"
    rendered_dir = task_base / "rendered"
    log_dir = auto_ext_root / "logs" / f"task_{safe_id}"

    if parallel:
        # prepare_parallel_workdir does rmtree-on-exist + fresh symlinks,
        # so a stale task_base from a prior run is handled. It also raises
        # WorkdirError cleanly if symlink creation fails.
        task_dir = prepare_parallel_workdir(auto_ext_root, workarea, task.task_id)
        cwd = task_dir
    else:
        cwd = workarea

    exec_ctx = _TaskExecCtx(
        cwd=cwd, rendered_dir=rendered_dir, log_dir=log_dir, parallel=parallel
    )
    context = _build_context(project, task, resolved_env)

    active_stages = [s for s in STAGE_ORDER if s in stages]
    _safe_call(reporter, "on_task_start", task.task_id, active_stages)

    task_result = TaskResult(task_id=task.task_id)
    abort = False
    cancel_seen = False  # once set: first stage marked CANCELLED, rest SKIPPED

    for stage in active_stages:
        # Pre-stage cancel check: short-circuit before any rendering or
        # subprocess spawn.
        if not cancel_seen and cancel_token.is_cancelled():
            # First stage hit by cancel → CANCELLED; subsequent → SKIPPED.
            _emit_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.CANCELLED, "run cancelled by user",
            )
            cancel_seen = True
            continue

        if cancel_seen:
            _emit_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.SKIPPED, "aborted after cancellation",
            )
            continue

        if stage == "jivaro" and not task.jivaro.enabled:
            _emit_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.SKIPPED, "jivaro disabled for task",
            )
            continue

        if abort:
            _emit_synthetic_stage(
                task_result, reporter, task.task_id, stage,
                StageStatus.SKIPPED, "aborted after earlier stage failure",
            )
            continue

        _safe_call(reporter, "on_stage_start", task.task_id, stage)
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
            sr = StageResult(
                stage=sr.stage,
                status=StageStatus.CANCELLED,
                tool_result=sr.tool_result,
                error=sr.error or "stage terminated by user cancellation",
            )
            cancel_seen = True

        task_result.stages.append(sr)
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

    task_result.overall = _compute_overall(task_result)
    if verbose:
        print(f"[task {task.task_id}] {task_result.overall}")
    _safe_call(reporter, "on_task_end", task.task_id, task_result.overall)
    return task_result


def _emit_synthetic_stage(
    task_result: TaskResult,
    reporter: ProgressReporter,
    task_id: str,
    stage: str,
    status: StageStatus,
    reason: str,
) -> None:
    """Append a skipped/cancelled :class:`StageResult` and emit both events.

    Both the StageResult bookkeeping and the ``on_stage_start`` /
    ``on_stage_end`` pair happen here so callers don't accidentally
    emit one without the other — a GUI tree that sees ``on_stage_start``
    without an end gets stuck on "running" forever.
    """
    _safe_call(reporter, "on_stage_start", task_id, stage)
    task_result.stages.append(StageResult(stage=stage, status=status, error=reason))
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
    log_path = exec_ctx.log_dir / f"{stage}.log"

    rendered_path: Path
    if tool.has_template:
        template_path = _resolve_template_path(task, stage, auto_ext_root=auto_ext_root)
        if template_path is None:
            return StageResult(
                stage=stage,
                status=StageStatus.FAILED,
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
                out_path=exec_ctx.rendered_dir / template_path.stem,
                knobs=stage_knobs,
            )
        except AutoExtError as exc:
            return StageResult(
                stage=stage, status=StageStatus.FAILED, error=f"render failed: {exc}"
            )
    else:
        rendered_path = exec_ctx.rendered_dir

    if dry_run:
        return StageResult(stage=stage, status=StageStatus.DRY_RUN)

    argv = tool.build_argv(rendered_path, context)

    try:
        if stage == "si":
            if exec_ctx.parallel:
                # Parallel: each task owns its cwd, so si.env is placed
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
        return StageResult(stage=stage, status=StageStatus.FAILED, error=str(exc))

    status = StageStatus.PASSED if result.success else StageStatus.FAILED
    return StageResult(stage=stage, status=status, tool_result=result)


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
    project: ProjectConfig, task: TaskConfig, resolved_env: dict[str, str]
) -> dict[str, Any]:
    output_dir = _resolve_output_dir(project, task, resolved_env)
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
    pdk_subdir = project.pdk_subdir or derive_ancestor_dir_from_env_candidates(
        project.pdk_subdir_env_vars, resolved_env, depth=1
    )
    lvs_runset_version = (
        project.runset_versions.lvs
        or derive_ancestor_dir_from_env_candidates(
            project.lvs_runset_version_env_vars, resolved_env, depth=2
        )
    )
    qrc_runset_version = (
        project.runset_versions.qrc
        or derive_ancestor_dir_from_env_candidates(
            project.qrc_runset_version_env_vars, resolved_env, depth=2
        )
    )

    return {
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
        "pdk_subdir": pdk_subdir,
        "project_subdir": project.project_subdir,
        "lvs_runset_version": lvs_runset_version,
        "qrc_runset_version": qrc_runset_version,
    }


def _discover_env_vars(
    project: ProjectConfig,
    tasks: list[TaskConfig],
    *,
    auto_ext_root: Path | None = None,
) -> set[str]:
    sources: list[str] = [
        project.extraction_output_dir,
        project.intermediate_dir,
        str(project.layer_map),
    ]
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
    # Surface env vars consulted by the auto-derive fallback chains so
    # check-env / preflight resolves them up-front (otherwise the env
    # panel would never list them and missing-var errors at render time
    # would be confusing).
    if project.tech_name is None:
        required.update(project.tech_name_env_vars)
    if project.pdk_subdir is None:
        required.update(project.pdk_subdir_env_vars)
    if project.runset_versions.lvs is None:
        required.update(project.lvs_runset_version_env_vars)
    if project.runset_versions.qrc is None:
        required.update(project.qrc_runset_version_env_vars)
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
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rendered_si_env, output_dir / "si.env")


_OUTPUT_DIR_FORMAT_KEYS: tuple[str, ...] = (
    "cell",
    "library",
    "task_id",
    "lvs_layout_view",
    "lvs_source_view",
)


def _resolve_output_dir(
    project: ProjectConfig,
    task: TaskConfig,
    resolved_env: dict[str, str],
) -> str:
    """Substitute env vars + format keys in ``project.extraction_output_dir``.

    Format keys: ``{cell}``, ``{library}``, ``{task_id}``,
    ``{lvs_layout_view}``, ``{lvs_source_view}``. Default pattern uses
    only ``{cell}``; users who want same-cell parallel runs with
    different knobs change the pattern to include another axis (e.g.
    ``QCI_PATH_{cell}_{lvs_layout_view}``) so each task lands in its
    own directory.

    Env vars must be resolved before this runs — Python ``str.format``
    would otherwise treat ``{WORK_ROOT}`` (from an unresolved
    ``${WORK_ROOT}``) as a format field and raise ``KeyError``.
    """
    tpl = substitute_env(project.extraction_output_dir, resolved_env)
    try:
        return tpl.format(
            cell=task.cell,
            library=task.library,
            task_id=task.task_id,
            lvs_layout_view=task.lvs_layout_view,
            lvs_source_view=task.lvs_source_view,
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
) -> None:
    """Reject tasks whose resolved ``output_dir`` collides with another.

    Collision detection is on the **fully substituted** output dir, not
    just the ``(library, cell)`` pair. Users who customise
    ``extraction_output_dir`` to include other axes (``{task_id}``,
    ``{lvs_layout_view}``, etc.) so same-cell tasks land in separate
    dirs are NOT flagged. Harmful in parallel (race), misleading in
    serial (second task silently overwrites). Always enforced.
    """
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for t in tasks:
        out = _resolve_output_dir(project, t, resolved_env)
        prior = seen.get(out)
        if prior is not None:
            collisions.append(
                f"task_ids {prior!r} and {t.task_id!r} both resolve to "
                f"output_dir {out!r}"
            )
        else:
            seen[out] = t.task_id
    if collisions:
        raise ConfigError(
            "duplicate extraction_output_dir(s) across tasks:\n  "
            + "\n  ".join(collisions)
            + "\n\nHint: if these tasks should run independently, change "
            "project.extraction_output_dir to include a discriminator key "
            f"(supported: {list(_OUTPUT_DIR_FORMAT_KEYS)})."
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
