"""Qt item models + status helpers shared across tabs.

Small enough to not need a separate models/ package yet; expand to a
subpackage when a second non-trivial model shows up.
"""

from __future__ import annotations

from auto_ext.core.progress import StageStatus, TaskStatus

#: Mapping of status string → human display text. The Qt reporter
#: emits ``str(StageStatus.*)``, so keys are plain strings.
STAGE_DISPLAY: dict[str, str] = {
    "": "·",
    "running": "▶ running",
    str(StageStatus.PASSED): "✓ passed",
    str(StageStatus.FAILED): "✗ failed",
    str(StageStatus.SKIPPED): "– skipped",
    str(StageStatus.CANCELLED): "■ cancelled",
    str(StageStatus.DRY_RUN): "… dry run",
}

TASK_DISPLAY: dict[str, str] = {
    str(TaskStatus.PENDING): "pending",
    str(TaskStatus.PASSED): "passed",
    str(TaskStatus.FAILED): "failed",
    str(TaskStatus.CANCELLED): "cancelled",
}

#: HTML color hints per status — keep the UI readable without an icon font.
STATUS_COLOR: dict[str, str] = {
    "running": "#0080ff",
    str(StageStatus.PASSED): "#2e8b2e",
    str(StageStatus.FAILED): "#c83232",
    str(StageStatus.SKIPPED): "#888888",
    str(StageStatus.CANCELLED): "#d69016",
    str(StageStatus.DRY_RUN): "#5070b0",
    str(TaskStatus.PENDING): "#888888",
    str(TaskStatus.PASSED): "#2e8b2e",
    str(TaskStatus.FAILED): "#c83232",
    str(TaskStatus.CANCELLED): "#d69016",
}


#: Env-resolution source → display label used by the Project tab's env
#: panel. Keys match :data:`auto_ext.core.env.EnvSource` string values.
ENV_SOURCE_DISPLAY: dict[str, str] = {
    "override": "⇄ override",
    "shell": "✓ shell",
    "missing": "✗ missing",
}

#: Env-resolution source → HTML color, same palette family as
#: :data:`STATUS_COLOR`. ``override`` reuses the amber tone reserved for
#: "user deliberately deviating"; ``missing`` uses the same red as FAILED.
ENV_SOURCE_COLOR: dict[str, str] = {
    "override": "#d69016",
    "shell": "#2e8b2e",
    "missing": "#c83232",
}

#: Foreground color for TasksTab preview rows that have been excluded
#: via the include checkbox. Grey matches :data:`STATUS_COLOR` for
#: SKIPPED so excluded combinations read as "intentionally out".
EXCLUDED_ROW_COLOR: str = "#888888"
