"""Serial task + stage execution driver.

Loads the resolved env once (fail-fast), instantiates each :class:`Tool`
once, and iterates tasks × stages in the canonical order:
``si`` → ``strmout`` → ``calibre`` → ``quantus`` → ``jivaro``. Tasks run
one at a time (Phase 3 is serial-only; parallel is Phase 3.5).

Failure handling:

- Stage raises :class:`AutoExtError` → that stage is marked failed,
  remaining stages for the task are skipped, runner continues with the
  next task.
- ``calibre`` stage returning ``success=False``: if ``task.continue_on_lvs_fail``
  is True, log a warning and proceed to the next stage. Otherwise skip
  remaining stages for this task (same as a generic failure).
- Any other stage returning ``success=False``: skip remaining stages for
  this task.
- ``jivaro`` stage is silently skipped (not failed) when
  ``task.jivaro.enabled`` is False.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_ext.core.config import ProjectConfig, TaskConfig
from auto_ext.core.env import (
    derive_parent_dir_from_env_candidates,
    discover_required_vars,
    resolve_env,
    substitute_env,
)
from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.core.manifest import load_manifest, resolve_knob_values
from auto_ext.core.workdir import serial_workdir
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
    """Per-stage outcome. ``status`` ∈ {passed, failed, skipped, dry_run}."""

    stage: str
    status: str
    tool_result: ToolResult | None = None
    error: str | None = None


@dataclass
class TaskResult:
    task_id: str
    stages: list[StageResult] = field(default_factory=list)
    overall: str = "pending"  # passed | failed


@dataclass
class RunSummary:
    tasks: list[TaskResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tasks if t.overall == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tasks if t.overall == "failed")


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
) -> RunSummary:
    """Execute the stage × task matrix serially.

    Pre-flight:

    - Validates ``stages`` (must be a non-empty subset of :data:`STAGE_ORDER`).
    - If ``jivaro`` is among ``stages``, every task with ``jivaro.enabled=True``
      must have ``out_file`` set, else :class:`ConfigError`.
    - Discovers env vars from every template in use and resolves them
      (override → shell → missing); any missing raises
      :class:`auto_ext.core.errors.EnvResolutionError` before any
      subprocess starts.

    ``cli_knobs`` is the ``{stage: {name: str}}`` dict parsed from
    ``--knob`` options; values are still strings here and are coerced at
    render time per :class:`auto_ext.core.manifest.KnobSpec`.
    """
    _validate_stages(stages)
    _validate_tasks(tasks, stages)

    required_env = _discover_env_vars(project, tasks)
    resolution = resolve_env(required_env, project.env_overrides)
    resolved_env = resolution.require()

    subprocess_env: dict[str, str] = {**os.environ, **project.env_overrides}

    tool_instances: dict[str, Tool] = {name: cls() for name, cls in _TOOL_REGISTRY.items()}

    cli_knobs = cli_knobs or {}

    summary = RunSummary()
    for task in tasks:
        summary.tasks.append(
            _run_single_task(
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
            )
        )

    logger.info(
        "run complete: %d/%d passed, %d failed",
        summary.passed,
        summary.total,
        summary.failed,
    )
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
) -> TaskResult:
    safe_id = _UNSAFE_TASK_ID.sub("_", task.task_id)
    rendered_dir = auto_ext_root / "runs" / f"task_{safe_id}" / "rendered"
    log_dir = auto_ext_root / "logs" / f"task_{safe_id}"
    context = _build_context(project, task, resolved_env)

    task_result = TaskResult(task_id=task.task_id)
    abort = False

    for stage in STAGE_ORDER:
        if stage not in stages:
            continue

        if stage == "jivaro" and not task.jivaro.enabled:
            task_result.stages.append(
                StageResult(stage=stage, status="skipped", error="jivaro disabled for task")
            )
            continue

        if abort:
            task_result.stages.append(
                StageResult(stage=stage, status="skipped", error="aborted after earlier stage failure")
            )
            continue

        sr = _run_single_stage(
            stage=stage,
            project=project,
            task=task,
            tool=tools[stage],
            rendered_dir=rendered_dir,
            log_dir=log_dir,
            workarea=workarea,
            context=context,
            resolved_env=resolved_env,
            subprocess_env=subprocess_env,
            cli_knobs=cli_knobs,
            dry_run=dry_run,
        )
        task_result.stages.append(sr)

        if sr.status == "failed":
            if stage == "calibre" and task.continue_on_lvs_fail:
                logger.warning(
                    "task %s: calibre failed but continue_on_lvs_fail=True; proceeding",
                    task.task_id,
                )
            else:
                abort = True

    task_result.overall = _compute_overall(task_result)
    if verbose:
        print(f"[task {task.task_id}] {task_result.overall}")
    return task_result


def _run_single_stage(
    *,
    stage: str,
    project: ProjectConfig,
    task: TaskConfig,
    tool: Tool,
    rendered_dir: Path,
    log_dir: Path,
    workarea: Path,
    context: dict[str, Any],
    resolved_env: dict[str, str],
    subprocess_env: dict[str, str],
    cli_knobs: dict[str, dict[str, Any]],
    dry_run: bool,
) -> StageResult:
    log_path = log_dir / f"{stage}.log"

    rendered_path: Path
    if tool.has_template:
        template_path = _resolve_template_path(task, stage)
        if template_path is None:
            return StageResult(
                stage=stage,
                status="failed",
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
                out_path=rendered_dir / template_path.stem,
                knobs=stage_knobs,
            )
        except AutoExtError as exc:
            return StageResult(stage=stage, status="failed", error=f"render failed: {exc}")
    else:
        rendered_path = rendered_dir

    if dry_run:
        return StageResult(stage=stage, status="dry_run")

    argv = tool.build_argv(rendered_path, context)

    try:
        if stage == "si":
            with serial_workdir(workarea, rendered_path):
                raw = tool.run(argv, cwd=workarea, env=subprocess_env, log_path=log_path)
        else:
            raw = tool.run(argv, cwd=workarea, env=subprocess_env, log_path=log_path)
        result = tool.parse_result(raw)
    except AutoExtError as exc:
        return StageResult(stage=stage, status="failed", error=str(exc))

    status = "passed" if result.success else "failed"
    return StageResult(stage=stage, status=status, tool_result=result)


# ---- helpers ---------------------------------------------------------------


def _resolve_template_path(task: TaskConfig, stage: str) -> Path | None:
    """Return the template path for this task's stage.

    ``TaskConfig.templates`` has fields named after the four templated
    tools (``si``, ``calibre``, ``quantus``, ``jivaro``). Phase 2's
    ``_merge_templates`` already collapsed project-level defaults into
    the task's copy, so a single attribute lookup suffices.
    """
    return getattr(task.templates, stage, None)


def _build_context(
    project: ProjectConfig, task: TaskConfig, resolved_env: dict[str, str]
) -> dict[str, Any]:
    output_dir_tpl = substitute_env(project.extraction_output_dir, resolved_env)
    intermediate_tpl = substitute_env(project.intermediate_dir, resolved_env)
    output_dir = output_dir_tpl.format(cell=task.cell, library=task.library)
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
        "pdk_subdir": project.pdk_subdir,
        "project_subdir": project.project_subdir,
        "lvs_runset_version": project.runset_versions.lvs,
        "qrc_runset_version": project.runset_versions.qrc,
    }


def _discover_env_vars(project: ProjectConfig, tasks: list[TaskConfig]) -> set[str]:
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
            try:
                sources.append(Path(tp).read_text(encoding="utf-8"))
            except OSError as exc:
                raise ConfigError(f"cannot read template {tp}: {exc}") from exc
    required = discover_required_vars(sources)
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


def _compute_overall(task_result: TaskResult) -> str:
    for s in task_result.stages:
        if s.status == "failed":
            return "failed"
    return "passed"
