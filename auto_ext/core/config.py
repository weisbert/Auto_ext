"""Load and validate ``project.yaml`` + ``tasks.yaml``.

Uses ``ruamel.yaml`` in roundtrip mode so comments survive for the GUI
write-back path (Phase 5). Schemas are declared with pydantic v2 and
``extra="forbid"`` so unknown fields fail loudly rather than being
silently ignored.

List-valued task fields (``library``, ``cell``, ``lvs_layout_view``,
``lvs_source_view``) are auto-expanded via nested loops in that fixed
order so ``task_id`` assignment is reproducible.
"""

from __future__ import annotations

import logging
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.errors import ConfigError

logger = logging.getLogger(__name__)


# ---- pydantic models -------------------------------------------------------


class JivaroConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    frequency_limit: float | None = None
    error_max: float | None = None


class TemplatePaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibre: Path | None = None
    quantus: Path | None = None
    jivaro: Path | None = None
    si: Path | None = None


class ProjectConfig(BaseModel):
    """Schema for ``project.yaml``. ``source_path`` and ``raw`` are filled
    in by :func:`load_project` after validation and are excluded from
    serialization so they do not round-trip back into YAML.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    work_root: Path
    verify_root: Path
    setup_root: Path
    employee_id: str
    layer_map: Path
    env_overrides: dict[str, str] = Field(default_factory=dict)
    extraction_output_dir: str = "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    intermediate_dir: str = "${WORK_ROOT2}"
    templates: TemplatePaths = Field(default_factory=TemplatePaths)

    source_path: Path | None = Field(default=None, exclude=True)
    raw: Any = Field(default=None, exclude=True)


class TaskSpec(BaseModel):
    """Raw ``tasks.yaml`` entry before expansion. List-valued fields are
    allowed on the expandable axes; scalar values are accepted and treated
    as single-element lists during expansion.
    """

    model_config = ConfigDict(extra="forbid")

    library: str | list[str]
    cell: str | list[str]
    lvs_source_view: str | list[str] = "schematic"
    lvs_layout_view: str | list[str]
    templates: TemplatePaths = Field(default_factory=TemplatePaths)
    ground_net: str = "vss"
    out_file: str | None = None
    jivaro: JivaroConfig = Field(default_factory=JivaroConfig)
    continue_on_lvs_fail: bool = False


class TaskConfig(BaseModel):
    """A fully scalarized, project-defaults-merged task. Immutable once built."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    library: str
    cell: str
    lvs_source_view: str
    lvs_layout_view: str
    templates: TemplatePaths
    ground_net: str
    out_file: str | None
    jivaro: JivaroConfig
    continue_on_lvs_fail: bool
    spec_index: int
    expansion_index: int


# ---- loaders ---------------------------------------------------------------


def load_project(path: Path) -> ProjectConfig:
    """Load ``project.yaml`` via ruamel.yaml and validate with pydantic.

    Raises :class:`ConfigError` on any parse or schema failure. The returned
    model has ``source_path`` set to ``path.resolve()`` and ``raw`` set to
    the original CommentedMap (for Phase 5 GUI write-back).
    """

    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at top level, got {type(data).__name__}")

    try:
        project = ProjectConfig.model_validate(_plain(data))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    project.source_path = path.resolve()
    project.raw = data
    logger.info("loaded project.yaml: %s", path)
    return project


def load_tasks(path: Path, project: ProjectConfig | None = None) -> list[TaskConfig]:
    """Load ``tasks.yaml``, expand list-valued fields, apply project defaults.

    The top level may be either a bare list of task entries or a mapping
    with a single ``tasks`` key whose value is the list.

    Raises :class:`ConfigError` on parse or schema failure. Task order is
    preserved (list order in the YAML, then inner expansion order:
    ``library`` -> ``cell`` -> ``lvs_layout_view`` -> ``lvs_source_view``).
    """

    data = _load_yaml(path)

    if data is None:
        raise ConfigError(f"{path}: file is empty")

    if isinstance(data, dict):
        if "tasks" not in data:
            raise ConfigError(f"{path}: mapping at top level must have a 'tasks' key")
        entries = data["tasks"]
    elif isinstance(data, list):
        entries = data
    else:
        raise ConfigError(
            f"{path}: expected a list or mapping at top level, got {type(data).__name__}"
        )

    if not isinstance(entries, list):
        raise ConfigError(f"{path}: 'tasks' must be a list")
    if not entries:
        raise ConfigError(f"{path}: tasks list is empty")

    tasks: list[TaskConfig] = []
    for spec_index, entry in enumerate(entries):
        try:
            spec = TaskSpec.model_validate(_plain(entry))
        except ValidationError as exc:
            raise ConfigError(f"{path} [entry #{spec_index}]: {exc}") from exc

        tasks.extend(_expand_spec(spec, spec_index, project, path))

    _warn_on_duplicate_task_ids(tasks)

    logger.info("expanded %d task specs -> %d subtasks", len(entries), len(tasks))
    return tasks


# ---- internals -------------------------------------------------------------


def _load_yaml(path: Path) -> Any:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    yaml = YAML(typ="rt")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.load(fh)
    except YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error: {exc}") from exc


def _plain(obj: Any) -> Any:
    """Convert a ruamel CommentedMap/CommentedSeq tree to plain dicts/lists.

    Pydantic accepts the commented variants (they subclass dict/list), but
    dumping to plain containers makes debug prints and equality checks
    behave naturally in tests.
    """

    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def _scalarize(value: str | list[str], field: str, spec_index: int, source: Path) -> list[str]:
    if isinstance(value, list):
        if not value:
            raise ConfigError(
                f"{source} [entry #{spec_index}]: field '{field}' is an empty list"
            )
        return list(value)
    return [value]


def _merge_templates(spec_tp: TemplatePaths, project: ProjectConfig | None) -> TemplatePaths:
    if project is None:
        return spec_tp
    proj_tp = project.templates
    return TemplatePaths(
        calibre=spec_tp.calibre or proj_tp.calibre,
        quantus=spec_tp.quantus or proj_tp.quantus,
        jivaro=spec_tp.jivaro or proj_tp.jivaro,
        si=spec_tp.si or proj_tp.si,
    )


def _expand_spec(
    spec: TaskSpec,
    spec_index: int,
    project: ProjectConfig | None,
    source: Path,
) -> list[TaskConfig]:
    libs = _scalarize(spec.library, "library", spec_index, source)
    cells = _scalarize(spec.cell, "cell", spec_index, source)
    layouts = _scalarize(spec.lvs_layout_view, "lvs_layout_view", spec_index, source)
    sources = _scalarize(spec.lvs_source_view, "lvs_source_view", spec_index, source)

    merged_templates = _merge_templates(spec.templates, project)

    result: list[TaskConfig] = []
    expansion_index = 0
    for library in libs:
        for cell in cells:
            for layout in layouts:
                for src in sources:
                    result.append(
                        TaskConfig(
                            task_id=f"{library}__{cell}__{layout}__{src}",
                            library=library,
                            cell=cell,
                            lvs_source_view=src,
                            lvs_layout_view=layout,
                            templates=merged_templates,
                            ground_net=spec.ground_net,
                            out_file=spec.out_file,
                            jivaro=spec.jivaro,
                            continue_on_lvs_fail=spec.continue_on_lvs_fail,
                            spec_index=spec_index,
                            expansion_index=expansion_index,
                        )
                    )
                    expansion_index += 1
    return result


def _warn_on_duplicate_task_ids(tasks: list[TaskConfig]) -> None:
    counts = Counter(t.task_id for t in tasks)
    dupes = [tid for tid, n in counts.items() if n > 1]
    if dupes:
        logger.warning("duplicate task_id(s) after expansion: %s", sorted(dupes))


def dump_project_yaml(project: ProjectConfig) -> str:
    """Serialize a :class:`ProjectConfig`'s original comment tree back to YAML.

    Available only when ``project.raw`` is present (set by :func:`load_project`).
    Used by the Phase 5 GUI write-back path. Not used by Phase 2 tests; here
    so the ``raw`` field has a concrete consumer documented.
    """

    if project.raw is None:
        raise ConfigError("project has no raw CommentedMap; was it loaded via load_project?")
    yaml = YAML(typ="rt")
    buf = StringIO()
    yaml.dump(project.raw, buf)
    return buf.getvalue()
