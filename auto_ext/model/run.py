"""Run objects: the immutable record of one execution.

A **Run** is one DUT executed with one effective configuration. It owns a
directory under ``<auto_ext_root>/runs/`` whose name is its identity::

    runs/20260821T143205Z_amp2-ext/
      run.json          # RunRecord, rewritten once at finalize, then immutable
      events.jsonl      # append-only during the run
      annotations.json  # the only mutable file: display_name / note / tags
      rendered/         # si.env, lvs.qci, ext.cmd, ...
      logs/             # si.log, strmout.log, calibre.log, ...
      results/          # lvs.report copy + derived summaries
      work/             # parallel-isolation cwd (created by workdir.py)

What this replaces:

* ``task_id = f"{library}__{cell}__{layout}__{src}"`` as an identity. Two
  specs for the same cell collide; a run directory never does.
* ``<root>/logs/task_<id>/<stage>.log`` opened with ``"w"`` -- a rerun
  overwrote the previous log. The directory is new every time now, so the
  ``"w"`` open is harmless and history is preserved for free.
* ``StageResult`` / ``TaskResult`` / ``RunSummary`` as the only carriers of
  outcome -- in-memory dataclasses that died with the process.

Write protocol (this is what makes "never overwrite" true):

1. Start: :func:`allocate_run_dir` claims a directory with
   ``mkdir(exist_ok=False)`` -- directory creation *is* the lock.
2. During: events are appended to ``events.jsonl``; a skeleton ``run.json``
   with ``overall=PENDING`` may be written once.
3. Finalize: the complete record is written to a temp file and moved over
   ``run.json`` with ``os.replace`` (see :mod:`auto_ext.core.run_store`).
4. Rename / annotate: only ``annotations.json`` changes. ``run.json`` and the
   directory name are never touched, because :attr:`StageRecord.log_path` and
   friends are relative to the directory and batches reference it by name.

Relationship to ``docs/refactor/01-schema.md``:

* This module is the S1 subset of section 1.3. ``Recipe`` / ``PdkProfile`` /
  ``CellEntry`` do not exist yet, so :class:`RecipeSnapshot` and
  :class:`DutSnapshot` stand in with the same shape and field names, and the
  shared ``Base`` / ``Frozen`` bases live here instead of ``model/common.py``.
* Section 1.3 spells the end-of-stage timestamp ``finished_at``; the field is
  named ``ended_at`` here, and ``finished_at`` remains available as a
  read-only alias property on :class:`StageRecord` and :class:`RunRecord`.
* Section 1.3's ``StageRecord.diagnostics`` (scalar-valued) is widened to
  :attr:`StageRecord.details` (arbitrary JSON) so a parsed LVS report can be
  carried verbatim.

Enum reuse: :class:`~auto_ext.core.progress.StageStatus` and
:class:`~auto_ext.core.progress.TaskStatus` are imported, never redefined, so
``record.overall == "passed"`` keeps working exactly as it does in the runner.
This module must not import :mod:`auto_ext.core.runner` -- the runner is the
one that will import *this*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from auto_ext.core.errors import AutoExtError
from auto_ext.core.progress import StageStatus, TaskStatus

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the import graph acyclic
    from auto_ext.core.checks import LvsReport
    from auto_ext.core.config import TaskConfig
    from auto_ext.core.env import EnvResolution

__all__ = [
    "MAX_SAME_SECOND_RUNS",
    "RUN_SCHEMA_VERSION",
    "RUN_TIMESTAMP_FORMAT",
    "Base",
    "DutSnapshot",
    "EnvBinding",
    "Frozen",
    "JivaroSnapshot",
    "JsonScalar",
    "LvsResult",
    "RecipeSnapshot",
    "RunAnnotations",
    "RunBatch",
    "RunIdError",
    "RunPaths",
    "RunRecord",
    "RunResults",
    "StageRecord",
    "StageStatus",
    "TaskStatus",
    "allocate_run_dir",
    "make_run_slug",
    "parse_run_id",
    "run_paths",
    "slugify",
    "utcnow",
    "validate_run_slug",
]

#: ``run.json`` schema version. 1 is reserved for "did not exist" -- the old
#: world had no run record at all, so there is nothing to upgrade from.
RUN_SCHEMA_VERSION = 2

#: ISO 8601 *basic* format in UTC. No colons (illegal on NTFS, quoting pain on
#: POSIX), lexicographic order == chronological order, and the ``Z`` states the
#: zone outright so a workarea shared across timezones is unambiguous.
RUN_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

#: How many runs may share one wall-clock second for one slug before
#: :func:`allocate_run_dir` gives up. The first gets the bare name, the rest
#: get ``-2`` .. ``-999``.
MAX_SAME_SECOND_RUNS = 999

#: Values allowed in the flattened Jinja context snapshot.
JsonScalar = str | int | float | bool | None

_TIMESTAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
_RUN_ID_RE = re.compile(r"^(?P<ts>\d{8}T\d{6}Z)_(?P<slug>.+)$")
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_SLUG_ALLOWED = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_ABS_WINDOWS_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


class RunIdError(AutoExtError):
    """A run directory could not be allocated, or a slug is unsafe as a path."""


def utcnow() -> datetime:
    """Timezone-aware "now" in UTC.

    The single injection point for time in the run layer: tests monkeypatch
    this module attribute (see the ``frozen_clock`` fixture) rather than
    patching :class:`datetime.datetime` itself.
    """

    return datetime.now(timezone.utc)


class Base(BaseModel):
    """Base for editable objects: unknown keys are an error, never swallowed."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Frozen(BaseModel):
    """Base for record objects: unknown keys are an error, fields are read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_run_relative(value: str | None) -> str | None:
    """Validate a run-dir-relative POSIX path (or ``None``)."""

    if value is None:
        return value
    if not value:
        raise ValueError("must be a non-empty relative POSIX path, or None")
    if "\\" in value:
        raise ValueError(f"{value!r}: use POSIX separators, this path is run-dir relative")
    if value.startswith("/") or _ABS_WINDOWS_RE.match(value):
        raise ValueError(f"{value!r}: must be relative to the run directory, not absolute")
    if ".." in value.split("/"):
        raise ValueError(f"{value!r}: must not escape the run directory")
    return value


# ---- snapshots of what was run ---------------------------------------------


class DutSnapshot(Frozen):
    """The device under test, copied into the record at start time.

    Same shape as the ``CellEntry`` of schema doc section 1.4, minus the
    table-editing fields (``enabled`` / ``note``), so S2 can swap the type in
    without touching call sites. Note that the view fields drop the ``lvs_``
    prefix that :class:`~auto_ext.core.config.TaskConfig` carries.
    """

    library: str
    cell: str
    layout_view: str
    source_view: str = "schematic"
    ground_net: str = "vss"
    out_file: str | None = None
    #: Display sugar carried over from ``TaskSpec.label``.
    display_name: str | None = None

    @property
    def key(self) -> str:
        """The old ``task_id`` string. Display only -- it never enters a path."""

        return f"{self.library}__{self.cell}__{self.layout_view}__{self.source_view}"

    @classmethod
    def from_task_config(cls, task: TaskConfig) -> DutSnapshot:
        """Build from a fully merged :class:`~auto_ext.core.config.TaskConfig`."""

        return cls(
            library=task.library,
            cell=task.cell,
            layout_view=task.lvs_layout_view,
            source_view=task.lvs_source_view,
            ground_net=task.ground_net,
            out_file=task.out_file,
            display_name=task.label,
        )


class JivaroSnapshot(Frozen):
    """Effective Jivaro settings for this run. S2 successor: ``Recipe.reduction``."""

    enabled: bool = False
    frequency_limit: float | None = None
    error_max: float | None = None


class RecipeSnapshot(Frozen):
    """The effective configuration this run was executed with.

    S1 stand-in for the ``Recipe`` of schema doc section 1.2, which does not
    exist yet. It is a *snapshot*, not a reference: everything needed to
    explain (and, with the same templates, reproduce) the rendered files is
    inlined here, so editing ``project.yaml`` afterwards cannot rewrite
    history.

    Field origins, and where each one goes in S2:

    ==================  ==========================================  ====================
    field               S1 source                                   S2 successor
    ==================  ==========================================  ====================
    ``recipe_id``       caller-chosen label (e.g. template stem)    ``Recipe.recipe_id``
    ``name``            caller-chosen display name                  ``Recipe.name``
    ``version``         ``"0"``; S1 has no versioned recipes        ``Recipe.version``
    ``templates``       resolved ``TemplatePaths``, per stage       catalog + patches
    ``knobs``           ``resolve_knob_values`` output, per stage   typed Recipe fields
    ``jivaro``          ``TaskConfig.jivaro``                       ``Recipe.reduction``
    ``dspf_out_path``   effective ``dspf_out_path`` expression      ``Recipe.output``
    ``paths``           resolved ``ProjectConfig.paths`` entries    ``PdkProfile``
    ==================  ==========================================  ====================

    Callers should set ``recipe_id`` to something recognisable (the quantus
    template stem, say) because it is half of the run directory name; the
    ``"adhoc"`` default only guarantees the slug stays well formed.
    """

    recipe_id: str = "adhoc"
    name: str | None = None
    version: str = "0"
    #: Stage name -> absolute path of the template that was rendered.
    templates: dict[str, str] = Field(default_factory=dict)
    #: Stage name -> merged knob values (manifest < project < task < CLI).
    knobs: dict[str, dict[str, JsonScalar]] = Field(default_factory=dict)
    jivaro: JivaroSnapshot = Field(default_factory=JivaroSnapshot)
    #: The dspf output *expression* in force for this run (the project value,
    #: or the per-task override). The resolved absolute path is
    #: :attr:`RunRecord.dspf_path`.
    dspf_out_path: str | None = None
    #: Resolved ``project.paths`` entries this run actually consumed, e.g.
    #: ``{"calibre_lvs_dir": "/pdk/.../LVS", "qrc_deck_dir": "/pdk/.../QCI_deck"}``.
    paths: dict[str, str] = Field(default_factory=dict)

    @property
    def label(self) -> str:
        """Human-facing name: :attr:`name` when set, else :attr:`recipe_id`."""

        return self.name or self.recipe_id


# ---- process and results ----------------------------------------------------


class StageRecord(Frozen):
    """One stage of one run. Supersedes ``runner.StageResult``.

    ``log_path`` / ``rendered_path`` are POSIX paths **relative to the run
    directory**, so the whole directory can be moved or shipped elsewhere and
    stay self-consistent. ``artifacts`` are absolute because they live in the
    Cadence workarea, outside the run directory, and will be overwritten by
    the next run of the same cell.
    """

    #: Unique within the run. Normally the stage name; when one stage runs
    #: twice (quantus emitting both an extracted view and a DSPF) the keys are
    #: ``"quantus.ext"`` / ``"quantus.dspf"`` while ``stage`` stays ``quantus``.
    key: str = Field(min_length=1)
    #: Stage name as the runner knows it (``auto_ext.core.runner.STAGE_ORDER``).
    stage: str = Field(min_length=1)
    status: StageStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    #: Wall-clock seconds. Nothing measured this before.
    duration_s: float | None = None
    #: argv as handed to the subprocess (``Tool.build_argv``).
    argv: list[str] = Field(default_factory=list)
    #: cwd of the subprocess: the shared workarea (serial) or ``<run>/work``.
    cwd: str | None = None
    exit_code: int | None = None
    #: e.g. ``"logs/calibre.log"``. Replaces ``logs/task_<id>/<stage>.log``.
    log_path: str | None = None
    #: e.g. ``"rendered/lvs.qci"``. Replaces ``runs/task_<id>/rendered/<stem>``.
    rendered_path: str | None = None
    #: Absolute paths produced in the Cadence workarea (svdb, query_output,
    #: ``<cell>.calibre.db``, dspf, ...). Carried over from
    #: ``ToolResult.artifact_paths``, which nothing consumed before.
    artifacts: list[str] = Field(default_factory=list)
    #: Open bag for everything else about this stage: ``ToolResult.diagnostics``
    #: verbatim (``exit_code`` / ``argv`` / ``lvs_report`` / ``lvs_parse_error``
    #: / ``lvs_report_missing``) plus failure classification. The *typed* LVS
    #: outcome belongs in :attr:`RunRecord.results`, not here.
    details: dict[str, JsonValue] = Field(default_factory=dict)
    error: str | None = None
    #: Why a SKIPPED stage was skipped, verbatim from the runner's synthetic
    #: stage emitter ("jivaro disabled for task", "aborted after earlier stage
    #: failure", ...).
    skip_reason: str | None = None

    @field_validator("log_path", "rendered_path")
    @classmethod
    def _relative_posix(cls, v: str | None) -> str | None:
        return _check_run_relative(v)

    @property
    def finished_at(self) -> datetime | None:
        """Alias for :attr:`ended_at` (the name used in schema doc section 1.3)."""

        return self.ended_at


class LvsResult(Frozen):
    """The parsed LVS outcome, promoted to a first-class result.

    ``core/checks.py`` already computes all of this; until now it was stuffed
    into ``ToolResult.diagnostics["lvs_report"]`` and read by nobody.
    """

    passed: bool
    #: ``"CORRECT"`` / ``"INCORRECT"`` / ``None`` when only CELL SUMMARY matched.
    banner: str | None = None
    discrepancies: int | None = None
    #: Absolute path of the report in the Cadence workarea. The next LVS run of
    #: this cell overwrites it.
    source_path: str | None = None
    #: Run-dir relative path of the archived copy, ``"results/lvs.report"``.
    #: A copy, never a symlink, precisely because the source is overwritten.
    archived_path: str | None = None
    #: CELL SUMMARY rows, when the fallback parse path was taken.
    cell_summary: list[str] = Field(default_factory=list)

    @field_validator("archived_path")
    @classmethod
    def _relative_posix(cls, v: str | None) -> str | None:
        return _check_run_relative(v)

    @classmethod
    def from_lvs_report(
        cls, report: LvsReport, *, archived_path: str | None = None
    ) -> LvsResult:
        """Build from :class:`auto_ext.core.checks.LvsReport`."""

        return cls(
            passed=report.passed,
            banner=report.banner,
            discrepancies=report.discrepancies,
            source_path=str(report.source),
            archived_path=archived_path,
        )


class RunResults(Frozen):
    """Structured outcomes worth reading back months later."""

    lvs: LvsResult | None = None


class EnvBinding(Frozen):
    """One environment variable, its value, and where it came from.

    Flattened from ``env.EnvResolution.resolved`` + ``.sources``.
    """

    name: str
    value: str
    source: str = Field(pattern=r"^(override|shell|missing)$")

    @classmethod
    def from_resolution(cls, resolution: EnvResolution) -> list[EnvBinding]:
        """Flatten an :class:`~auto_ext.core.env.EnvResolution` into bindings.

        Sorted by name so two runs of the same configuration produce the same
        ``run.json`` bytes in this section.
        """

        return [
            cls(name=name, value=resolution.resolved.get(name, ""), source=source)
            for name, source in sorted(resolution.sources.items())
        ]


# ---- the record --------------------------------------------------------------


class RunRecord(Frozen):
    """Everything in ``runs/<run_id>/run.json``.

    JSON-clean by construction: no ``Path``, no sets, no tuple keys. Dump with
    ``record.model_dump_json(indent=2)``, or let
    :func:`auto_ext.core.run_store.write_record` do it atomically.
    """

    schema_version: int = RUN_SCHEMA_VERSION

    # ---- identity ----
    #: The directory name, e.g. ``20260821T143205Z_amp2-ext``.
    run_id: str
    #: The part of :attr:`run_id` after the timestamp, including any ``-2``
    #: same-second disambiguator.
    slug: str
    created_at: datetime
    ended_at: datetime | None = None
    #: Converges exactly like ``runner._compute_overall``: any CANCELLED ->
    #: CANCELLED, else any FAILED -> FAILED, else PASSED. Stays ``PENDING``
    #: while the run is in flight.
    overall: TaskStatus = TaskStatus.PENDING
    #: Membership in a batch; ``runs/batches/<batch_id>.json`` lists the members.
    batch_id: str | None = None
    #: Rerun lineage: the run this one was launched from.
    parent_run_id: str | None = None

    # ---- what was run (snapshots, not references) ----
    dut: DutSnapshot
    recipe: RecipeSnapshot

    # ---- runtime choices for this invocation ----
    #: Stages actually scheduled (CLI ``--stage`` / the Run tab checkboxes).
    requested_stages: list[str] = Field(default_factory=list)
    dry_run: bool = False
    continue_on_lvs_fail: bool = False
    max_workers: int = Field(default=1, ge=1)

    # ---- where it ran ----
    #: The Cadence workarea for this cell: ``ProjectConfig.extraction_output_dir``
    #: resolved. Mutable, reusable, overwritten by the next run -- it carries no
    #: identity any more, the run directory does.
    workspace_dir: str
    intermediate_dir: str | None = None
    #: Resolved ``dspf_out_path`` for this run, when DSPF output is configured.
    dspf_path: str | None = None
    #: CLI ``--workarea``: the shared cwd used by serial runs.
    workarea: str | None = None
    #: Absolute path of ``runs/<run_id>``.
    run_dir: str | None = None
    #: Parallel isolation cwd (``<run_dir>/work``); ``None`` in serial mode.
    work_dir: str | None = None

    # ---- resolution results ----
    env: list[EnvBinding] = Field(default_factory=list)
    #: The flattened Jinja context that produced the rendered files. This lived
    #: only inside ``runner._build_context``'s return value before.
    context: dict[str, JsonScalar] = Field(default_factory=dict)

    # ---- process and outcome ----
    stages: list[StageRecord] = Field(default_factory=list)
    results: RunResults = Field(default_factory=RunResults)

    # ---- provenance ----
    #: Resolved absolute path of each EDA binary, keyed by tool name.
    tools: dict[str, str] = Field(default_factory=dict)
    tool_versions: dict[str, str] = Field(default_factory=dict)
    host: str | None = None
    user: str | None = None
    auto_ext_version: str | None = None
    python_version: str | None = None
    cancelled_by: str | None = None

    @model_validator(mode="after")
    def _check_identity(self) -> RunRecord:
        m = _RUN_ID_RE.match(self.run_id)
        if m is None:
            raise ValueError(
                f"run_id {self.run_id!r} must look like <timestamp>_<slug> "
                "(timestamp is %Y%m%dT%H%M%SZ), e.g. 20260821T143205Z_amp2-ext"
            )
        if m.group("slug") != self.slug:
            expected = m.group("slug")
            raise ValueError(
                f"slug {self.slug!r} does not match run_id {self.run_id!r} "
                f"(expected {expected!r})"
            )
        keys = [s.key for s in self.stages]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate stage keys: {keys}")
        return self

    @property
    def finished_at(self) -> datetime | None:
        """Alias for :attr:`ended_at` (the name used in schema doc section 1.3)."""

        return self.ended_at

    @property
    def duration_s(self) -> float | None:
        """Wall-clock seconds from start to finish, or ``None`` while running."""

        if self.ended_at is None:
            return None
        return (self.ended_at - self.created_at).total_seconds()

    @property
    def dut_label(self) -> str:
        """The old ``task_id``. Display only; it never enters a path."""

        return self.dut.key

    @property
    def default_display_name(self) -> str:
        """Shown in run lists unless ``annotations.display_name`` overrides it."""

        return f"{self.dut.cell} · {self.recipe.label}"

    def stage(self, key: str) -> StageRecord | None:
        """Return the stage recorded under ``key``, or ``None``."""

        for s in self.stages:
            if s.key == key:
                return s
        return None


class RunAnnotations(Base):
    """``runs/<run_id>/annotations.json`` -- the only mutable file in a run.

    Renaming a run in the GUI writes here. The directory name, ``run.json``,
    and every relative path inside it stay exactly as they were.
    """

    display_name: str | None = None
    note: str | None = None
    tags: list[str] = Field(default_factory=list)
    starred: bool = False
    #: Resolved through the module-level :func:`utcnow` at call time (not bound
    #: at class creation) so the frozen clock in tests applies here too.
    updated_at: datetime = Field(default_factory=lambda: utcnow())

    @property
    def pinned(self) -> bool:
        """True when the user marked this run as worth keeping.

        :func:`auto_ext.core.run_store.prune_runs` never deletes a pinned run.
        """

        return self.starred or bool(self.tags)


class RunBatch(Base):
    """``runs/batches/<batch_id>.json`` -- the index of one bulk launch.

    Takes over the persistence job of ``runner.RunSummary``.
    """

    batch_id: str
    created_at: datetime
    ended_at: datetime | None = None
    label: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    max_workers: int = Field(default=1, ge=1)


# ---- identity: slug, directory name, layout ----------------------------------


def slugify(text: str, *, max_len: int = 24) -> str:
    """Reduce ``text`` to ``[a-z0-9-]``, collapsing runs of anything else.

    Never returns an empty string (``"x"`` is the fallback), so a cell named
    entirely out of punctuation still produces a usable directory name. Path
    separators, ``..``, drive colons and shell metacharacters all collapse to
    ``-`` here; :func:`validate_run_slug` is the enforcing gate.
    """

    s = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    return s[:max_len].rstrip("-") or "x"


def validate_run_slug(slug: str) -> str:
    """Return ``slug`` unchanged, or raise :class:`RunIdError`.

    The slug is user-derived (a cell name today; recipe names and free-text
    notes later) and it is concatenated into a directory name, so it is
    validated as a path component rather than trusted:

    * not empty, no leading or trailing whitespace;
    * no path separator, no ``..``, no ``:``;
    * nothing outside ``[a-z0-9._-]``, and it must start alphanumeric;
    * no trailing dot or space -- Windows strips those silently, which would
      let two different slugs land on one directory;
    * not a Windows reserved device name (``CON``, ``PRN``, ``AUX``, ``NUL``,
      ``COM1``-``COM9``, ``LPT1``-``LPT9``), with or without an extension.
    """

    if not slug:
        raise RunIdError("run slug must not be empty")
    if slug != slug.strip():
        raise RunIdError(f"run slug {slug!r} must not have leading/trailing whitespace")
    for sep in ("/", "\\"):
        if sep in slug:
            raise RunIdError(f"run slug {slug!r} must not contain a path separator")
    if ":" in slug:
        raise RunIdError(f"run slug {slug!r} must not contain ':'")
    if slug in (".", "..") or slug.startswith(".."):
        raise RunIdError(f"run slug {slug!r} must not be a parent-directory reference")
    if slug[-1] in ". ":
        raise RunIdError(f"run slug {slug!r} must not end with a dot or a space")
    if not _SLUG_ALLOWED.match(slug):
        raise RunIdError(
            f"run slug {slug!r} must match {_SLUG_ALLOWED.pattern} "
            "(lowercase alphanumerics, dot, dash, underscore; starting alphanumeric)"
        )
    if slug.split(".")[0] in _WINDOWS_DEVICE_NAMES:
        raise RunIdError(f"run slug {slug!r} is a reserved device name on Windows")
    return slug


def make_run_slug(dut: DutSnapshot, recipe: RecipeSnapshot) -> str:
    """``<cell>-<recipe_id>``, each half slugified and truncated.

    Deliberately not library- or view-qualified: the directory name only has
    to be recognisable at a glance, ``run.json`` holds the full identity.
    """

    return validate_run_slug(
        f"{slugify(dut.cell, max_len=24)}-{slugify(recipe.recipe_id, max_len=28)}"
    )


def parse_run_id(run_id: str) -> tuple[datetime, str]:
    """Split ``run_id`` into its UTC timestamp and its slug.

    Raises :class:`RunIdError` when ``run_id`` is not in the canonical
    ``<timestamp>_<slug>`` form. Keeping this parseable is why a same-second
    collision appends to the *slug* (``..._amp2-ext-2``), never to the
    timestamp.
    """

    m = _RUN_ID_RE.match(run_id)
    if m is None:
        raise RunIdError(
            f"{run_id!r} is not a run id: expected <timestamp>_<slug> "
            "with timestamp %Y%m%dT%H%M%SZ"
        )
    stamp = datetime.strptime(m.group("ts"), RUN_TIMESTAMP_FORMAT).replace(
        tzinfo=timezone.utc
    )
    return stamp, m.group("slug")


@dataclass(frozen=True, slots=True)
class RunPaths:
    """Every path inside a run directory. The one source of truth for layout.

    Replaces ``runner._task_run_dirs``, and with it the habit of recomputing
    path arithmetic in the GUI: the GUI reads :attr:`StageRecord.log_path` /
    :attr:`StageRecord.rendered_path` instead, so it cannot drift from what the
    runner actually wrote.
    """

    root: Path
    record: Path
    events: Path
    annots: Path
    rendered: Path
    logs: Path
    results: Path
    work: Path


def run_paths(run_dir: Path) -> RunPaths:
    """Pure function: the layout of ``run_dir``. Creates nothing."""

    run_dir = Path(run_dir)
    return RunPaths(
        root=run_dir,
        record=run_dir / "run.json",
        events=run_dir / "events.jsonl",
        annots=run_dir / "annotations.json",
        rendered=run_dir / "rendered",
        logs=run_dir / "logs",
        results=run_dir / "results",
        work=run_dir / "work",
    )


def allocate_run_dir(runs_root: Path, slug: str, *, now: datetime | None = None) -> Path:
    """Claim a fresh run directory under ``runs_root`` and return it.

    ``mkdir(exist_ok=False)`` is atomic on both NTFS and POSIX, so directory
    creation *is* the lock: two threads (or two processes) racing on the same
    second land on different directories with no coordination. An existing
    directory is never reused -- that is the whole point of an immutable run,
    and the reverse of the old ``prepare_parallel_workdir`` behaviour.

    ``now`` is converted to UTC; naive input is assumed to be UTC already.
    Same-second collisions append ``-2`` .. ``-<MAX_SAME_SECOND_RUNS>`` to the
    slug, never to the timestamp.

    Creates ``rendered/``, ``logs/`` and ``results/`` inside the new directory.
    ``work/`` is left to :mod:`auto_ext.core.workdir`, which only parallel runs
    need.

    Raises :class:`RunIdError` for an unsafe slug, or after
    :data:`MAX_SAME_SECOND_RUNS` collisions.
    """

    validate_run_slug(slug)
    moment = now if now is not None else utcnow()
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    ts = moment.strftime(RUN_TIMESTAMP_FORMAT)
    if not _TIMESTAMP_RE.match(ts):  # pragma: no cover - defensive
        raise RunIdError(f"timestamp {ts!r} does not match {RUN_TIMESTAMP_FORMAT}")

    runs_root = Path(runs_root)
    for n in range(1, MAX_SAME_SECOND_RUNS + 1):
        name = f"{ts}_{slug}" if n == 1 else f"{ts}_{slug}-{n}"
        candidate = runs_root / name
        try:
            candidate.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        paths = run_paths(candidate)
        for sub in (paths.rendered, paths.logs, paths.results):
            sub.mkdir()
        return candidate
    raise RunIdError(
        f"cannot allocate a run directory in {runs_root}: "
        f"{ts}_{slug} already has {MAX_SAME_SECOND_RUNS} same-second siblings"
    )
