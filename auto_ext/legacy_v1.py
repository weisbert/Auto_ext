"""The v1 file formats, kept read-only so ``auto-ext migrate`` can still read them.

Everything in this module describes files Auto_ext no longer writes and no
longer runs from: the ``project.yaml`` + ``tasks.yaml`` pair with its template
slots, its Cartesian ``exclude`` selectors and its per-task overrides, and the
``<name>.j2.manifest.yaml`` knob sidecars those two layered on top of. The
render path that consumed them is gone (:mod:`auto_ext.core.render` renders
from the catalog now), and :mod:`auto_ext.core.config` refuses a file carrying
any of the retired keys.

One consumer, on purpose: :mod:`auto_ext.migrate`. A migration has to be able
to parse the thing it is migrating away from, so the v1 schema outlives the v1
mechanism -- but only here, only for reading, and only until nobody has an
unmigrated tree left. Nothing under :mod:`auto_ext.core` imports this module,
and nothing here is re-exported from ``core``; that is what keeps "the old
mechanism is deleted" true rather than aspirational.

Two deliberate differences from the v1 originals
------------------------------------------------
* :func:`resolve_knob_values_v1` accepts knob overrides for a template that has
  no sidecar instead of refusing them. v1 refused because an undeclared knob
  could not be typed or range-checked and would have rendered nothing; a
  migration has no such problem -- it is carrying a value the user wrote in
  their own ``project.yaml`` across to a Recipe field, and the catalog owns the
  type on the other side. Refusing here would make a tree whose sidecars have
  already been removed unmigratable, which is the opposite of the point.
* Nothing writes. ``dump_manifest_yaml`` / ``append_knob_to_manifest_yaml`` and
  the ruamel comment-preserving write-back helpers are not carried over.
"""

from __future__ import annotations

import copy
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.errors import ConfigError

logger = logging.getLogger(__name__)

__all__ = [
    "ExcludeMatchV1",
    "JivaroConfigV1",
    "JivaroOverrideV1",
    "KnobSpecV1",
    "ProjectConfigV1",
    "SourceRefV1",
    "TaskConfigV1",
    "TaskSpecV1",
    "TemplateManifestV1",
    "TemplatePathsV1",
    "load_manifest_v1",
    "load_project_v1",
    "load_tasks_v1_with_raw",
    "manifest_path_for_v1",
    "resolve_knob_values_v1",
]


# ---- v1 manifest schema ----------------------------------------------------

#: Render-context keys the v1 runner produced. A knob sharing any of these
#: names silently shadowed the identity value after the merge, so v1 rejected
#: it at manifest load; kept here so a hand-edited sidecar is diagnosed the
#: same way during a migration.
_IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "library",
        "cell",
        "lvs_layout_view",
        "lvs_source_view",
        "ground_net",
        "out_file",
        "task_id",
        "output_dir",
        "intermediate_dir",
        "dspf_out_path",
        "layer_map",
        "employee_id",
        "jivaro_frequency_limit",
        "jivaro_error_max",
        "tech_name",
        "pdk_subdir",
        "project_subdir",
        "lvs_runset_version",
        "qrc_runset_version",
    }
)


class SourceRefV1(BaseModel):
    """Pointer back to the raw EDA-file key a knob was promoted from."""

    model_config = ConfigDict(extra="forbid")

    tool: Literal["calibre", "si", "quantus", "jivaro"]
    key: str


class KnobSpecV1(BaseModel):
    """One knob declaration inside a :class:`TemplateManifestV1`."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["int", "float", "str", "bool"]
    default: Any
    description: str | None = None
    range: tuple[Any, Any] | None = None
    choices: list[Any] | None = None
    unit: str | None = None
    source: SourceRefV1 | None = None

    @model_validator(mode="after")
    def _validate(self) -> "KnobSpecV1":
        self.default = _coerce_typed(self.default, self.type, "default")
        if self.range is not None:
            if self.type not in ("int", "float"):
                raise ValueError(
                    f"range is only valid for int or float knobs (got type={self.type!r})"
                )
            low = _coerce_typed(self.range[0], self.type, "range[0]")
            high = _coerce_typed(self.range[1], self.type, "range[1]")
            if low > high:
                raise ValueError(f"range low {low} > high {high}")
            self.range = (low, high)
            if not (low <= self.default <= high):
                raise ValueError(
                    f"default {self.default} is outside range [{low}, {high}]"
                )
        if self.choices is not None:
            if self.type != "str":
                raise ValueError(
                    f"choices is only valid for str knobs (got type={self.type!r})"
                )
            if self.range is not None:
                raise ValueError("choices and range are mutually exclusive")
            if len(self.choices) == 0:
                raise ValueError("choices must contain at least one value")
            coerced = [
                _coerce_typed(c, self.type, f"choices[{i}]")
                for i, c in enumerate(self.choices)
            ]
            if len(set(coerced)) != len(coerced):
                raise ValueError(f"choices contains duplicates: {coerced!r}")
            self.choices = coerced
            if self.default not in coerced:
                raise ValueError(
                    f"default {self.default!r} is not in choices {coerced!r}"
                )
        return self


class TemplateManifestV1(BaseModel):
    """Sidecar metadata for one v1 template. ``template`` is the .j2 filename."""

    model_config = ConfigDict(extra="forbid")

    template: str
    description: str | None = None
    knobs: dict[str, KnobSpecV1] = Field(default_factory=dict)


def manifest_path_for_v1(template_path: Path) -> Path:
    """Return the sidecar path for ``template_path`` (``<name>.manifest.yaml``)."""

    return template_path.with_name(template_path.name + ".manifest.yaml")


def load_manifest_v1(template_path: Path) -> TemplateManifestV1 | None:
    """Load the v1 sidecar manifest for ``template_path``, or ``None``.

    ``None`` means "no sidecar" -- either the template never had knobs, or the
    tree has already had its sidecars removed. Both are ordinary during a
    migration. A sidecar that exists but is malformed still raises
    :class:`ConfigError`: a half-readable manifest would silently drop the
    user's own defaults out of the migrated Recipe.
    """

    path = manifest_path_for_v1(template_path)
    if not path.is_file():
        return None

    yaml = YAML(typ="rt")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.load(fh)
    except YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error: {exc}") from exc

    if data is None:
        raise ConfigError(f"{path}: manifest is empty")
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: expected a mapping at top level, got {type(data).__name__}"
        )

    try:
        manifest = TemplateManifestV1.model_validate(_plain(data))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    if manifest.template != template_path.name:
        raise ConfigError(
            f"{path}: 'template' field {manifest.template!r} does not match "
            f"sidecar filename {template_path.name!r}"
        )

    for knob_name in manifest.knobs:
        if knob_name in _IDENTITY_KEYS:
            raise ConfigError(
                f"{path}: knob {knob_name!r} collides with reserved identity variable"
            )
        if "." in knob_name:
            raise ConfigError(
                f"{path}: knob name {knob_name!r} must not contain a dot"
            )

    return manifest


def resolve_knob_values_v1(
    manifest: TemplateManifestV1 | None,
    project_knobs: dict[str, Any],
    task_knobs: dict[str, Any],
) -> dict[str, Any]:
    """Merge ``manifest default < project.knobs < task.knobs`` into a flat dict.

    The ``--knob`` layer v1 had on top is not represented: it was per-run and
    therefore has nothing to migrate into.

    With no manifest the two override layers are taken verbatim -- see the
    module docstring for why that is a widening rather than a bug. With one,
    every value is type-coerced and range/choice-checked exactly as v1 did, so
    a sidecar that still exists still catches a typo in ``project.yaml``.
    """

    if manifest is None:
        merged: dict[str, Any] = dict(project_knobs)
        merged.update(task_knobs)
        return merged

    result: dict[str, Any] = {
        name: spec.default for name, spec in manifest.knobs.items()
    }

    for layer_name, layer in (("project", project_knobs), ("task", task_knobs)):
        for knob_name, raw in layer.items():
            if knob_name not in manifest.knobs:
                raise ConfigError(
                    f"{layer_name} knob {knob_name!r} is not declared in the "
                    f"manifest for {manifest.template}; known knobs: "
                    f"{sorted(manifest.knobs)}"
                )
            spec = manifest.knobs[knob_name]
            label = f"{layer_name} knob {knob_name}"
            try:
                value = _coerce_typed(raw, spec.type, label)
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            if spec.range is not None:
                low, high = spec.range
                if not (low <= value <= high):
                    raise ConfigError(
                        f"{label}={value} is outside allowed range [{low}, {high}]"
                    )
            if spec.choices is not None and value not in spec.choices:
                raise ConfigError(
                    f"{label}={value!r} is not in allowed choices {spec.choices!r}"
                )
            result[knob_name] = value

    return result


def _coerce_typed(value: Any, type_name: str, label: str) -> Any:
    """Coerce a natively-typed YAML value to the knob's declared type."""

    if type_name == "bool":
        if isinstance(value, bool):
            return value
        raise ValueError(f"{label}: expected bool, got {type(value).__name__}")
    if type_name == "int":
        if isinstance(value, bool):
            raise ValueError(f"{label}: expected int, got bool")
        if isinstance(value, int):
            return value
        raise ValueError(f"{label}: expected int, got {type(value).__name__}")
    if type_name == "float":
        if isinstance(value, bool):
            raise ValueError(f"{label}: expected float, got bool")
        if isinstance(value, (int, float)):
            return float(value)
        raise ValueError(f"{label}: expected float, got {type(value).__name__}")
    if type_name == "str":
        if isinstance(value, str):
            return value
        raise ValueError(f"{label}: expected str, got {type(value).__name__}")
    raise ValueError(f"{label}: unknown knob type {type_name!r}")


# ---- v1 config schema ------------------------------------------------------


class JivaroConfigV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    frequency_limit: float | None = None
    error_max: float | None = None


class JivaroOverrideV1(BaseModel):
    """Per-cell partial override merged on top of ``TaskSpecV1.jivaro``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    frequency_limit: float | None = None
    error_max: float | None = None


class ExcludeMatchV1(BaseModel):
    """Selector dropped from a :class:`TaskSpecV1` Cartesian expansion."""

    model_config = ConfigDict(extra="forbid")

    library: str | None = None
    cell: str | None = None
    lvs_source_view: str | None = None
    lvs_layout_view: str | None = None

    @model_validator(mode="after")
    def _must_set_at_least_one(self) -> "ExcludeMatchV1":
        if not any(
            v is not None
            for v in (
                self.library,
                self.cell,
                self.lvs_source_view,
                self.lvs_layout_view,
            )
        ):
            raise ValueError(
                "exclude entry must set at least one of "
                "library / cell / lvs_layout_view / lvs_source_view"
            )
        return self


class TemplatePathsV1(BaseModel):
    """The four per-stage template slots. Replaced by the catalog's targets."""

    model_config = ConfigDict(extra="forbid")

    calibre: Path | None = None
    quantus: Path | None = None
    jivaro: Path | None = None
    si: Path | None = None

    @field_validator("calibre", "quantus", "jivaro", "si", mode="before")
    @classmethod
    def _normalize_separators(cls, v: Any) -> Any:
        # A YAML edited on a Windows dev box and deployed to Linux would
        # otherwise parse into one literal-backslash filename.
        if isinstance(v, str):
            return v.replace("\\", "/")
        return v


class ProjectConfigV1(BaseModel):
    """The v1 ``project.yaml``, all fourteen keys."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    work_root: Path | None = None
    verify_root: Path | None = None
    setup_root: Path | None = None
    employee_id: str | None = None
    tech_name: str | None = None
    tech_name_env_vars: list[str] = Field(
        default_factory=lambda: [
            "PDK_TECH_FILE",
            "PDK_LAYER_MAP_FILE",
            "PDK_DISPLAY_FILE",
        ]
    )
    paths: dict[str, str] = Field(default_factory=dict)
    layer_map: Path = Path("${PDK_LAYER_MAP_FILE}")
    env_overrides: dict[str, str] = Field(default_factory=dict)
    extraction_output_dir: str = "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    intermediate_dir: str = "${WORK_ROOT2}"
    dspf_out_path: str = "${WORK_ROOT2}/{cell}.dspf"
    templates: TemplatePathsV1 = Field(default_factory=TemplatePathsV1)
    knobs: dict[str, dict[str, Any]] = Field(default_factory=dict)

    source_path: Path | None = Field(default=None, exclude=True)
    raw: Any = Field(default=None, exclude=True)


class TaskSpecV1(BaseModel):
    """One raw ``tasks.yaml`` entry, before Cartesian expansion."""

    model_config = ConfigDict(extra="forbid")

    library: str | list[str]
    cell: str | list[str]
    lvs_source_view: str | list[str] = "schematic"
    lvs_layout_view: str | list[str]
    templates: TemplatePathsV1 = Field(default_factory=TemplatePathsV1)
    ground_net: str = "vss"
    out_file: str | None = None
    label: str | None = None
    jivaro: JivaroConfigV1 = Field(default_factory=JivaroConfigV1)
    continue_on_lvs_fail: bool = False
    dspf_out_path: str | None = None
    knobs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    exclude: list[ExcludeMatchV1] = Field(default_factory=list)
    jivaro_overrides: dict[str, JivaroOverrideV1] = Field(default_factory=dict)


class TaskConfigV1(BaseModel):
    """One fully scalarized v1 task, project defaults merged in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    library: str
    cell: str
    lvs_source_view: str
    lvs_layout_view: str
    templates: TemplatePathsV1
    ground_net: str
    out_file: str | None
    label: str | None = None
    jivaro: JivaroConfigV1
    continue_on_lvs_fail: bool
    dspf_out_path: str | None = None
    knobs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    spec_index: int
    expansion_index: int


# ---- v1 loaders ------------------------------------------------------------


def load_project_v1(path: Path) -> ProjectConfigV1:
    """Load a v1 ``project.yaml``. Raises :class:`ConfigError` on any failure."""

    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: expected a mapping at top level, got {type(data).__name__}"
        )

    try:
        project = ProjectConfigV1.model_validate(_plain(data))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    project.source_path = path.resolve()
    project.raw = data
    return project


def load_tasks_v1_with_raw(
    path: Path, project: ProjectConfigV1 | None = None
) -> tuple[list[TaskConfigV1], Any]:
    """Load a v1 ``tasks.yaml``, expand its axes, and return the raw tree too.

    The raw tree is what :mod:`auto_ext.migrate` re-reads to recover the
    ``exclude`` selectors, which expansion has already resolved away by the
    time the :class:`TaskConfigV1` list exists.
    """

    data = _load_yaml(path)

    if data is None:
        raise ConfigError(f"{path}: file is empty")

    entries = _tasks_sequence(data, path)

    if not entries:
        raise ConfigError(f"{path}: tasks list is empty")

    tasks: list[TaskConfigV1] = []
    for spec_index, entry in enumerate(entries):
        try:
            spec = TaskSpecV1.model_validate(_plain(entry))
        except ValidationError as exc:
            raise ConfigError(f"{path} [entry #{spec_index}]: {exc}") from exc

        tasks.extend(_expand_spec(spec, spec_index, project, path))

    counts = Counter(t.task_id for t in tasks)
    dupes = [tid for tid, n in counts.items() if n > 1]
    if dupes:
        logger.warning("duplicate task_id(s) after expansion: %s", sorted(dupes))

    return tasks, data


def _tasks_sequence(data: Any, path: Path) -> Any:
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
    return entries


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
    """Convert a ruamel CommentedMap/CommentedSeq tree to plain dicts/lists."""

    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def _scalarize(
    value: str | list[str], field: str, spec_index: int, source: Path
) -> list[str]:
    if isinstance(value, list):
        if not value:
            raise ConfigError(
                f"{source} [entry #{spec_index}]: field '{field}' is an empty list"
            )
        return list(value)
    return [value]


def _merge_templates(
    spec_tp: TemplatePathsV1, project: ProjectConfigV1 | None
) -> TemplatePathsV1:
    if project is None:
        return spec_tp
    proj_tp = project.templates
    return TemplatePathsV1(
        calibre=spec_tp.calibre or proj_tp.calibre,
        quantus=spec_tp.quantus or proj_tp.quantus,
        jivaro=spec_tp.jivaro or proj_tp.jivaro,
        si=spec_tp.si or proj_tp.si,
    )


def _expand_spec(
    spec: TaskSpecV1,
    spec_index: int,
    project: ProjectConfigV1 | None,
    source: Path,
) -> list[TaskConfigV1]:
    libs = _scalarize(spec.library, "library", spec_index, source)
    cells = _scalarize(spec.cell, "cell", spec_index, source)
    layouts = _scalarize(spec.lvs_layout_view, "lvs_layout_view", spec_index, source)
    sources = _scalarize(spec.lvs_source_view, "lvs_source_view", spec_index, source)

    merged_templates = _merge_templates(spec.templates, project)

    result: list[TaskConfigV1] = []
    expansion_index = 0
    for library in libs:
        for cell in cells:
            for layout in layouts:
                for src in sources:
                    if _is_excluded(spec.exclude, library, cell, layout, src):
                        continue
                    jivaro = _merge_jivaro_override(
                        spec.jivaro, spec.jivaro_overrides.get(cell)
                    )
                    result.append(
                        TaskConfigV1(
                            task_id=f"{library}__{cell}__{layout}__{src}",
                            library=library,
                            cell=cell,
                            lvs_source_view=src,
                            lvs_layout_view=layout,
                            templates=merged_templates,
                            ground_net=spec.ground_net,
                            out_file=spec.out_file,
                            label=spec.label,
                            jivaro=jivaro,
                            continue_on_lvs_fail=spec.continue_on_lvs_fail,
                            dspf_out_path=spec.dspf_out_path,
                            knobs=copy.deepcopy(spec.knobs),
                            spec_index=spec_index,
                            expansion_index=expansion_index,
                        )
                    )
                    expansion_index += 1
    if not result and (libs and cells and layouts and sources):
        raise ConfigError(
            f"{source} [entry #{spec_index}]: exclude list dropped every "
            f"combination; spec produces zero tasks"
        )
    return result


def _is_excluded(
    excludes: list[ExcludeMatchV1],
    library: str,
    cell: str,
    layout: str,
    source: str,
) -> bool:
    for match in excludes:
        if match.library is not None and match.library != library:
            continue
        if match.cell is not None and match.cell != cell:
            continue
        if match.lvs_layout_view is not None and match.lvs_layout_view != layout:
            continue
        if match.lvs_source_view is not None and match.lvs_source_view != source:
            continue
        return True
    return False


def _merge_jivaro_override(
    base: JivaroConfigV1, override: JivaroOverrideV1 | None
) -> JivaroConfigV1:
    if override is None:
        return base
    update: dict[str, Any] = {}
    if override.enabled is not None:
        update["enabled"] = override.enabled
    if override.frequency_limit is not None:
        update["frequency_limit"] = override.frequency_limit
    if override.error_max is not None:
        update["error_max"] = override.error_max
    if not update:
        return base
    return base.model_copy(update=update)
