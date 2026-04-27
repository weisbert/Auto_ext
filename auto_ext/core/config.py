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

import copy
import logging
from collections import Counter
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
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


class JivaroOverride(BaseModel):
    """Per-cell partial override merged on top of ``TaskSpec.jivaro``.

    Every field is optional; only the set fields displace the spec-level
    default. Used by :attr:`TaskSpec.jivaro_overrides`.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    frequency_limit: float | None = None
    error_max: float | None = None


class ExcludeMatch(BaseModel):
    """Selector dropped from a :class:`TaskSpec` expansion.

    At least one axis field must be set; an empty selector would match every
    combination and is almost certainly a mistake. Match semantics: every
    set field must equal the expanded task's corresponding axis value (AND).
    Unset fields are treated as wildcards.
    """

    model_config = ConfigDict(extra="forbid")

    library: str | None = None
    cell: str | None = None
    lvs_source_view: str | None = None
    lvs_layout_view: str | None = None

    @model_validator(mode="after")
    def _must_set_at_least_one(self) -> "ExcludeMatch":
        if not any(
            v is not None
            for v in (self.library, self.cell, self.lvs_source_view, self.lvs_layout_view)
        ):
            raise ValueError(
                "exclude entry must set at least one of "
                "library / cell / lvs_layout_view / lvs_source_view"
            )
        return self


class TemplatePaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibre: Path | None = None
    quantus: Path | None = None
    jivaro: Path | None = None
    si: Path | None = None

    @field_validator("calibre", "quantus", "jivaro", "si", mode="before")
    @classmethod
    def _normalize_separators(cls, v: Any) -> Any:
        # Project-internal template paths conventionally use POSIX
        # separators. Accept Windows-style backslashes (a YAML edited on
        # the dev box, deployed to Linux) by normalizing here, so the
        # path string parses into a multi-component Path on Linux instead
        # of one literal-backslash filename.
        if isinstance(v, str):
            return v.replace("\\", "/")
        return v


class ProjectConfig(BaseModel):
    """Schema for ``project.yaml``. ``source_path`` and ``raw`` are filled
    in by :func:`load_project` after validation and are excluded from
    serialization so they do not round-trip back into YAML.

    All path-root fields are optional: after sourcing your Cadence/PDK
    setup, the values live in shell env vars (``$WORK_ROOT`` etc.) and
    Auto_ext reads them directly via ``extraction_output_dir`` /
    ``intermediate_dir`` / ``layer_map``. Setting the fields explicitly
    is only useful for the GUI env panel (Phase 5) and for ``migrate``
    to round-trip (Phase 4).
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    #: Display-only; if None, GUI panels will show ``$WORK_ROOT`` from shell.
    work_root: Path | None = None
    #: Display-only; if None, GUI panels will show ``$VERIFY_ROOT`` from shell.
    verify_root: Path | None = None
    #: Display-only; if None, GUI panels will show ``$SETUP_ROOT`` from shell.
    setup_root: Path | None = None

    #: Substituted into template paths like ``/tmpdata/RFIC/rfic_share/<id>/...``.
    #: If None, resolved at render time via ``$USER`` / ``$USERNAME`` / fallback.
    employee_id: str | None = None

    #: Cadence tech library name (e.g. ``HN001``) surfaced in Quantus
    #: ``-technology_name``. Populated by ``init-project`` from
    #: ``aggregate_pdk_tokens``; leave ``None`` for projects that do not
    #: reference a tech name in any template. When ``None``, runner falls
    #: back to auto-derivation from ``tech_name_env_vars``.
    tech_name: str | None = None

    #: Env vars consulted, in order, when ``tech_name`` is ``None``. First
    #: var whose value is non-empty wins; tech name = ``Path(value).parent.name``.
    #: Override per-project if the local PDK uses different env var names.
    tech_name_env_vars: list[str] = Field(
        default_factory=lambda: [
            "PDK_TECH_FILE",
            "PDK_LAYER_MAP_FILE",
            "PDK_DISPLAY_FILE",
        ]
    )

    #: Path expressions referenced by templates as ``[[<key>]]``. Each value
    #: is a string that may mix env-var references (``$X`` / ``${X}`` /
    #: ``$env(X)``) with literal segments, optionally followed by ``|parent``
    #: to take ``Path.parent`` after env substitution. The whole expression
    #: is resolved at render time via :func:`resolve_path_expr` and the
    #: result is exposed in the Jinja context under the same key.
    #:
    #: Canonical entries used by the bundled templates:
    #:   - ``calibre_lvs_dir``: directory holding ``<basename>.<variant>.qcilvs``
    #:     rules files. Typical value: ``$calibre_source_added_place|parent``.
    #:   - ``qrc_deck_dir``: directory holding ``query_cmd`` /
    #:     ``preserveCellList.txt`` for QRC. Usually project-supplied;
    #:     no widely-shared env-var convention.
    #:
    #: Projects can add custom keys (e.g. ``paths.foo: $X/bar``) and any
    #: template ``[[foo]]`` reference picks the value up automatically.
    paths: dict[str, str] = Field(default_factory=dict)

    #: Default refers to the env var set by the PDK setup; override only if
    #: you need a specific file different from ``$PDK_LAYER_MAP_FILE``.
    layer_map: Path = Path("${PDK_LAYER_MAP_FILE}")

    env_overrides: dict[str, str] = Field(default_factory=dict)
    #: Per-task extraction output directory. Env vars (``$X`` / ``${X}`` /
    #: ``$env(X)``) are substituted via :func:`resolve_env`, then Python
    #: ``str.format`` substitutes axis-derived keys. Supported keys:
    #: ``{cell}``, ``{library}``, ``{task_id}``, ``{lvs_layout_view}``,
    #: ``{lvs_source_view}``. Add a discriminator key when you want same-
    #: cell tasks (e.g. two specs with different ``knobs`` for the same
    #: cell) to land in separate dirs.
    extraction_output_dir: str = "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    intermediate_dir: str = "${WORK_ROOT2}"
    templates: TemplatePaths = Field(default_factory=TemplatePaths)

    #: Project-wide knob overrides keyed by stage name, e.g.
    #: ``{"quantus": {"exclude_floating_nets_limit": 100}}``. Values are
    #: validated against the template manifest at render time, not here.
    knobs: dict[str, dict[str, Any]] = Field(default_factory=dict)

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
    #: Per-task knob overrides. Same shape as :attr:`ProjectConfig.knobs`.
    #: Precedence is applied at render time (task > project > manifest).
    knobs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    #: Cartesian-product combinations to drop after expansion. Each entry is
    #: a selector dict (``{cell: AMP2, lvs_layout_view: layout_test}``);
    #: every set field must equal the expanded task's axis value.
    exclude: list[ExcludeMatch] = Field(default_factory=list)
    #: Per-cell overrides layered on top of :attr:`jivaro`. Key is the cell
    #: name from :attr:`cell`; values whose field is ``None`` fall through
    #: to the spec-level default. Cells absent from :attr:`cell` are silently
    #: ignored at expansion time (stale overrides do not break the load).
    jivaro_overrides: dict[str, JivaroOverride] = Field(default_factory=dict)


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
    knobs: dict[str, dict[str, Any]] = Field(default_factory=dict)
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

    tasks, _raw = load_tasks_with_raw(path, project)
    return tasks


def load_tasks_with_raw(
    path: Path, project: ProjectConfig | None = None
) -> tuple[list[TaskConfig], Any]:
    """Same as :func:`load_tasks` but also returns the raw ruamel tree.

    The second element is the outer YAML structure (``CommentedSeq`` for a
    bare-list file, ``CommentedMap`` for the ``tasks:`` wrapped form). Used
    by the Phase 5 GUI to write back spec edits via
    :func:`apply_tasks_edits` while preserving top-level comments.
    """

    data = _load_yaml(path)

    if data is None:
        raise ConfigError(f"{path}: file is empty")

    entries = _tasks_sequence(data, path)

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
                    if _is_excluded(spec.exclude, library, cell, layout, src):
                        continue
                    jivaro = _merge_jivaro_override(
                        spec.jivaro, spec.jivaro_overrides.get(cell)
                    )
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
                            jivaro=jivaro,
                            continue_on_lvs_fail=spec.continue_on_lvs_fail,
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
    excludes: list[ExcludeMatch],
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
    base: JivaroConfig, override: JivaroOverride | None
) -> JivaroConfig:
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


# ---- GUI write-back --------------------------------------------------------


_EDIT_SCALAR_KEYS = frozenset(
    {
        "work_root",
        "verify_root",
        "setup_root",
        "employee_id",
        "tech_name",
        "layer_map",
        "extraction_output_dir",
        "intermediate_dir",
    }
)

# parent → allowed children, or None for "arbitrary child keys"
_EDIT_NESTED_KEYS: dict[str, frozenset[str] | None] = {
    "env_overrides": None,  # env var names are arbitrary
    "paths": None,  # path keys are user-extensible
    "templates": frozenset({"calibre", "quantus", "jivaro", "si"}),
}

# Stages allowed as the middle segment of a ``knobs.<stage>.<name>`` edit.
# Hard-coded here to keep ``core/config`` runner-free; must mirror
# ``runner.STAGE_ORDER``. If a new stage is added there, mirror it here.
_KNOB_STAGES: frozenset[str] = frozenset({"si", "calibre", "quantus", "jivaro"})


def apply_project_edits(raw: Any, edits: dict[str, Any]) -> None:
    """Mutate a ruamel ``CommentedMap`` in place per ``edits``.

    Keys are flat (``tech_name``), dotted for the known nested mappings
    (``runset_versions.lvs``, ``env_overrides.FOO``, ``templates.calibre``),
    or three-segment for project-level knob overrides
    (``knobs.<stage>.<name>``). A value of ``None`` removes the key; any
    other value overwrites. Comments attached to existing keys survive;
    newly-introduced keys appear without leading comments (expected — the
    dump is user-driven).

    Deleting the last child of a nested mapping also prunes the parent,
    so ``env_overrides: {}`` does not linger after every override is
    cleared. The same cascading prune applies through both levels of a
    ``knobs.<stage>.<name>`` delete.

    Raises :class:`ConfigError` on unknown keys to catch typos before
    they disappear silently into the YAML.
    """

    if raw is None:
        raise ConfigError("apply_project_edits: raw CommentedMap is None")

    for key, value in edits.items():
        # Normalize Windows backslashes in template path strings so the
        # YAML on disk stays POSIX-style regardless of the OS the GUI
        # was saved on. Mirrors the load-side TemplatePaths validator.
        if (
            key.startswith("templates.")
            and isinstance(value, str)
            and "\\" in value
        ):
            value = value.replace("\\", "/")
        parts = key.split(".")
        if len(parts) == 1:
            if key not in _EDIT_SCALAR_KEYS:
                raise ConfigError(f"apply_project_edits: unknown key {key!r}")
            if value is None:
                raw.pop(key, None)
            else:
                raw[key] = value
        elif len(parts) == 2:
            parent, child = parts
            if parent not in _EDIT_NESTED_KEYS:
                raise ConfigError(f"apply_project_edits: unknown key {key!r}")
            allowed = _EDIT_NESTED_KEYS[parent]
            if allowed is not None and child not in allowed:
                raise ConfigError(
                    f"apply_project_edits: unknown nested key {key!r} "
                    f"(allowed under {parent!r}: {sorted(allowed)})"
                )
            _apply_nested_edit(raw, parent, child, value)
        elif len(parts) == 3:
            parent, stage, name = parts
            if parent != "knobs":
                raise ConfigError(
                    f"apply_project_edits: unknown key {key!r} "
                    "(only knobs.<stage>.<name> is supported as a 3-level key)"
                )
            if stage not in _KNOB_STAGES:
                raise ConfigError(
                    f"apply_project_edits: unknown knob stage in {key!r} "
                    f"(allowed: {sorted(_KNOB_STAGES)})"
                )
            _apply_doubly_nested_edit(raw, parent, stage, name, value)
        else:
            raise ConfigError(
                f"apply_project_edits: too many dotted segments in {key!r} "
                "(max 3: knobs.<stage>.<name>)"
            )


def _apply_nested_edit(raw: Any, parent: str, child: str, value: Any) -> None:
    if value is None:
        if parent in raw and isinstance(raw[parent], dict) and child in raw[parent]:
            del raw[parent][child]
            if not raw[parent]:
                del raw[parent]
        return
    if parent not in raw or not isinstance(raw[parent], dict):
        raw[parent] = {}
    raw[parent][child] = value


def _apply_doubly_nested_edit(
    raw: Any, parent: str, child: str, grandchild: str, value: Any
) -> None:
    """Two-level set/delete with cascading prune.

    On delete (``value is None``), removes ``raw[parent][child][grandchild]``
    and prunes ``child`` if it becomes empty, then ``parent`` if that
    leaves it empty. On set, creates intermediate mappings as needed.
    """
    if value is None:
        if (
            parent in raw
            and isinstance(raw[parent], dict)
            and child in raw[parent]
            and isinstance(raw[parent][child], dict)
            and grandchild in raw[parent][child]
        ):
            del raw[parent][child][grandchild]
            if not raw[parent][child]:
                del raw[parent][child]
            if not raw[parent]:
                del raw[parent]
        return
    if parent not in raw or not isinstance(raw[parent], dict):
        raw[parent] = {}
    if child not in raw[parent] or not isinstance(raw[parent][child], dict):
        raw[parent][child] = {}
    raw[parent][child][grandchild] = value


def dump_tasks_yaml(raw: Any) -> str:
    """Serialize a tasks.yaml raw tree back to YAML text.

    ``raw`` is the tree returned by :func:`load_tasks_with_raw` (either a
    ruamel ``CommentedSeq`` or a ``CommentedMap`` wrapping a ``tasks:`` key).
    Symmetric with :func:`dump_project_yaml` for the Phase 5 GUI write-back.
    """

    if raw is None:
        raise ConfigError("dump_tasks_yaml: raw is None")
    yaml = YAML(typ="rt")
    buf = StringIO()
    yaml.dump(raw, buf)
    return buf.getvalue()


def apply_tasks_edits(raw: Any, specs: list[dict[str, Any]]) -> None:
    """Replace the tasks sequence in ``raw`` with ``specs`` at spec granularity.

    ``raw`` is the tree from :func:`load_tasks_with_raw`; it is mutated in
    place. ``specs`` is a list of fully-formed TaskSpec dicts (validated
    upstream by constructing ``TaskSpec(**spec)`` before calling).

    Semantics:
    - overlapping indexes: ``seq[i]`` overwritten by ``specs[i]``. Inline
      comments on the old entry's scalar fields are lost; top-level
      container comments (file preamble, inter-spec blank lines) survive.
    - ``i >= len(seq)``: ``specs[i]`` appended.
    - ``i >= len(specs)``: trailing entries in ``seq`` are popped.

    Raises :class:`ConfigError` if ``specs`` is empty (tasks.yaml cannot
    round-trip to an empty file — the loader rejects it on next read).
    """

    if raw is None:
        raise ConfigError("apply_tasks_edits: raw is None")
    if not specs:
        raise ConfigError("apply_tasks_edits: specs list is empty")

    if isinstance(raw, dict):
        if "tasks" not in raw:
            raise ConfigError("apply_tasks_edits: raw mapping has no 'tasks' key")
        seq = raw["tasks"]
    elif isinstance(raw, list):
        seq = raw
    else:
        raise ConfigError(
            f"apply_tasks_edits: unsupported raw type {type(raw).__name__}"
        )

    for i, spec in enumerate(specs):
        if i < len(seq):
            seq[i] = spec
        else:
            seq.append(spec)
    while len(seq) > len(specs):
        seq.pop()
