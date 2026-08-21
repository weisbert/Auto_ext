"""Read/write the run history under ``<auto_ext_root>/runs/``.

One file per concern inside ``runs/<run_id>/``:

======================  ===========  =========================================
file                    mutability   written by
======================  ===========  =========================================
``run.json``            write-once   :func:`write_record` (skeleton, then
                                     finalize with ``overwrite=True``)
``events.jsonl``        append-only  :func:`append_event`, during the run
``annotations.json``    mutable      :func:`write_annotations`, whenever the
                                     user renames / notes / tags the run
======================  ===========  =========================================

Two invariants this module exists to hold:

* **Never half a file.** ``run.json`` and ``annotations.json`` are written to
  a sibling temp file and moved into place with :func:`os.replace`, which is
  atomic on NTFS and POSIX alike. A crash mid-write leaves the previous
  content, never a truncated JSON document.
* **Never blow up on the history list.** A run directory may be missing its
  ``run.json``, hold a truncated one, or be a directory the user created by
  hand. :func:`list_runs` skips it with a warning; one bad directory must not
  take the Runs tab down with it.

``runs/batches/`` and the ``runs/latest`` convenience symlink are part of the
layout, so both are skipped when enumerating runs.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.model.run import (
    RUN_SCHEMA_VERSION,
    DutSnapshot,
    RunAnnotations,
    RunBatch,
    RunRecord,
    parse_run_id,
    run_paths,
    utcnow,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BATCHES_DIRNAME",
    "LATEST_LINK_NAME",
    "RunIndexEntry",
    "RunStoreError",
    "append_event",
    "find_previous_run",
    "list_runs",
    "prune_runs",
    "read_annotations",
    "read_batch",
    "read_events",
    "read_record",
    "write_annotations",
    "write_batch",
    "write_record",
]

#: Subdirectory of ``runs/`` holding batch index files. Not a run.
BATCHES_DIRNAME = "batches"
#: Best-effort POSIX symlink to the newest run. Not a run.
LATEST_LINK_NAME = "latest"

_SKIP_DIRNAMES = frozenset({BATCHES_DIRNAME, LATEST_LINK_NAME})
_TMP_SUFFIX = ".tmp"

#: ``{from_version: upgrader}``. Empty until a v2 record needs migrating.
_RUN_UPGRADERS: dict[int, Any] = {}


class RunStoreError(AutoExtError):
    """A run file could not be read or written (I/O, malformed JSON, overwrite)."""


# ---- low-level helpers -------------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` atomically, UTF-8, LF line endings."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + _TMP_SUFFIX)
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise RunStoreError(f"cannot write {path}: {exc}") from exc
    return path


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RunStoreError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunStoreError(f"{path} is not valid JSON: {exc}") from exc


def _as_run_dir(path: Path) -> Path:
    """Accept either a run directory or the ``run.json`` inside it."""

    path = Path(path)
    return path.parent if path.name == "run.json" else path


# ---- run.json ----------------------------------------------------------------


def write_record(run_dir: Path, record: RunRecord, *, overwrite: bool = False) -> Path:
    """Write ``record`` to ``<run_dir>/run.json`` and return that path.

    ``overwrite=False`` (the default) refuses to touch an existing
    ``run.json``: history is immutable, and silently replacing a finished
    record is exactly the failure mode the run layer exists to prevent. The
    runner writes the ``PENDING`` skeleton with the default, then finalizes
    once with ``overwrite=True``.
    """

    target = run_paths(run_dir).record
    if target.exists() and not overwrite:
        raise RunStoreError(
            f"{target} already exists; a run record is immutable "
            "(pass overwrite=True only to finalize the run that owns it)"
        )
    return _atomic_write_text(target, record.model_dump_json(indent=2) + "\n")


def read_record(path: Path) -> RunRecord:
    """Load a :class:`RunRecord` from a run directory or a ``run.json`` path.

    Raises :class:`RunStoreError` for missing / unreadable / malformed JSON and
    :class:`~auto_ext.core.errors.ConfigError` when the schema version is one
    this build cannot handle.
    """

    record_path = run_paths(_as_run_dir(path)).record
    data = _read_json(record_path)
    if not isinstance(data, dict):
        raise RunStoreError(f"{record_path}: expected a JSON object, got {type(data).__name__}")
    return _validate_record(record_path, data)


def _validate_record(record_path: Path, data: dict[str, Any]) -> RunRecord:
    try:
        version = int(data.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{record_path}: schema_version is not an integer") from exc
    while version < RUN_SCHEMA_VERSION:
        upgrader = _RUN_UPGRADERS.get(version)
        if upgrader is None:
            raise ConfigError(
                f"{record_path}: run schema v{version} has no upgrader; "
                "this record predates the run layer"
            )
        data = upgrader(data)
        version = int(data["schema_version"])
    if version > RUN_SCHEMA_VERSION:
        raise ConfigError(
            f"{record_path}: run schema v{version} is newer than this build "
            f"(v{RUN_SCHEMA_VERSION}); upgrade Auto_ext to read it"
        )
    try:
        return RunRecord.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and anything it wraps
        raise RunStoreError(f"{record_path}: not a valid run record: {exc}") from exc


# ---- events.jsonl ------------------------------------------------------------


def append_event(run_dir: Path, event: Mapping[str, Any]) -> None:
    """Append one JSON object to ``<run_dir>/events.jsonl``.

    Adds an ``at`` UTC timestamp when the caller did not supply one. The line
    is written and flushed in a single ``open(..., "a")``, so concurrent
    appends of a sub-buffer-size line do not interleave on either platform.
    """

    payload: dict[str, Any] = dict(event)
    payload.setdefault("at", utcnow().isoformat())
    try:
        line = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise RunStoreError(f"event is not JSON-serializable: {exc}") from exc
    path = run_paths(run_dir).events
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
    except OSError as exc:
        raise RunStoreError(f"cannot append to {path}: {exc}") from exc


def read_events(run_dir: Path) -> list[dict[str, Any]]:
    """Return the events of a run, oldest first.

    Missing file -> empty list. A line that is not a JSON object is skipped
    with a warning: a run killed mid-write leaves a truncated last line, and
    that must not cost the caller the events that preceded it.
    """

    path = run_paths(run_dir).events
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise RunStoreError(f"cannot read {path}: {exc}") from exc
    events: list[dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("%s:%d: skipping unparsable event line", path, lineno)
            continue
        if not isinstance(obj, dict):
            logger.warning("%s:%d: skipping non-object event line", path, lineno)
            continue
        events.append(obj)
    return events


# ---- annotations.json --------------------------------------------------------


def read_annotations(run_dir: Path) -> RunAnnotations:
    """Return the run's annotations, or empty defaults when absent.

    A corrupt ``annotations.json`` also yields defaults plus a warning: user
    labels are decoration, and losing them must never hide the run itself.
    """

    path = run_paths(run_dir).annots
    if not path.exists():
        return RunAnnotations()
    try:
        data = _read_json(path)
        return RunAnnotations.model_validate(data)
    except (RunStoreError, ValueError) as exc:
        logger.warning("%s: unreadable annotations, using defaults (%s)", path, exc)
        return RunAnnotations()


def write_annotations(run_dir: Path, annotations: RunAnnotations) -> Path:
    """Write ``annotations.json`` atomically. ``run.json`` is never touched.

    Stamps :attr:`RunAnnotations.updated_at` with the current time.
    """

    stamped = annotations.model_copy(update={"updated_at": utcnow()})
    return _atomic_write_text(
        run_paths(run_dir).annots, stamped.model_dump_json(indent=2) + "\n"
    )


# ---- batches -----------------------------------------------------------------


def write_batch(runs_root: Path, batch: RunBatch) -> Path:
    """Write ``runs/batches/<batch_id>.json`` atomically."""

    target = Path(runs_root) / BATCHES_DIRNAME / f"{batch.batch_id}.json"
    return _atomic_write_text(target, batch.model_dump_json(indent=2) + "\n")


def read_batch(runs_root: Path, batch_id: str) -> RunBatch:
    """Load ``runs/batches/<batch_id>.json``."""

    path = Path(runs_root) / BATCHES_DIRNAME / f"{batch_id}.json"
    data = _read_json(path)
    try:
        return RunBatch.model_validate(data)
    except Exception as exc:
        raise RunStoreError(f"{path}: not a valid batch index: {exc}") from exc


# ---- the index ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunIndexEntry:
    """One row of the run history, cheap enough to build for every directory.

    Read straight out of the ``run.json`` document without constructing a
    :class:`RunRecord`, and deliberately without touching ``stages`` -- a
    history list of hundreds of runs should not pay for per-stage validation.
    Call :func:`read_record` when the full record is actually needed.
    """

    run_id: str
    run_dir: Path
    created_at: datetime
    ended_at: datetime | None
    overall: str
    slug: str
    library: str
    cell: str
    layout_view: str
    source_view: str
    recipe_id: str
    dry_run: bool
    lvs_passed: bool | None
    lvs_discrepancies: int | None
    display_name: str
    tags: tuple[str, ...]
    starred: bool

    @property
    def dut_key(self) -> str:
        """The old ``task_id`` string, for display and for grouping."""

        return f"{self.library}__{self.cell}__{self.layout_view}__{self.source_view}"

    @property
    def pinned(self) -> bool:
        """Starred or tagged: :func:`prune_runs` keeps it regardless of age."""

        return self.starred or bool(self.tags)

    @property
    def duration_s(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.created_at).total_seconds()


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _index_entry(run_dir: Path) -> RunIndexEntry | None:
    """Build an index row, or return ``None`` if this directory is not a run."""

    record_path = run_paths(run_dir).record
    if not record_path.is_file():
        logger.warning("%s: no run.json, skipping", run_dir)
        return None
    try:
        data = _read_json(record_path)
    except RunStoreError as exc:
        logger.warning("%s: skipping unreadable run record (%s)", run_dir, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("%s: run.json is not a JSON object, skipping", run_dir)
        return None

    created_at = _parse_dt(data.get("created_at"))
    if created_at is None:
        # Fall back to the timestamp encoded in the directory name so a record
        # with a mangled created_at still sorts sensibly instead of vanishing.
        try:
            created_at, _ = parse_run_id(run_dir.name)
        except AutoExtError:
            logger.warning("%s: run.json has no usable created_at, skipping", run_dir)
            return None

    dut = data.get("dut")
    if not isinstance(dut, dict) or not dut.get("cell"):
        logger.warning("%s: run.json has no dut section, skipping", run_dir)
        return None
    recipe = data.get("recipe") if isinstance(data.get("recipe"), dict) else {}
    results = data.get("results") if isinstance(data.get("results"), dict) else {}
    lvs = results.get("lvs") if isinstance(results.get("lvs"), dict) else {}

    annotations = read_annotations(run_dir)
    run_id = str(data.get("run_id") or run_dir.name)
    slug = str(data.get("slug") or "")
    cell = str(dut.get("cell"))
    recipe_id = str(recipe.get("recipe_id") or "")
    fallback_name = f"{cell} · {recipe.get('name') or recipe_id}".rstrip(" ·")

    return RunIndexEntry(
        run_id=run_id,
        run_dir=run_dir,
        created_at=created_at,
        ended_at=_parse_dt(data.get("ended_at")),
        overall=str(data.get("overall") or "pending"),
        slug=slug,
        library=str(dut.get("library") or ""),
        cell=cell,
        layout_view=str(dut.get("layout_view") or ""),
        source_view=str(dut.get("source_view") or ""),
        recipe_id=recipe_id,
        dry_run=bool(data.get("dry_run", False)),
        lvs_passed=lvs.get("passed") if isinstance(lvs.get("passed"), bool) else None,
        lvs_discrepancies=(
            lvs.get("discrepancies") if isinstance(lvs.get("discrepancies"), int) else None
        ),
        display_name=annotations.display_name or fallback_name,
        tags=tuple(annotations.tags),
        starred=annotations.starred,
    )


def list_runs(runs_root: Path, limit: int | None = None) -> list[RunIndexEntry]:
    """Return the run history, newest first.

    Skips (with a warning) anything that is not a readable run: a hand-made
    directory, a run whose ``run.json`` is missing, truncated, or not valid
    JSON, and the ``batches/`` / ``latest`` layout entries. A missing
    ``runs_root`` is an empty history, not an error.

    ``limit`` caps the number of rows returned, after sorting.
    """

    runs_root = Path(runs_root)
    if not runs_root.is_dir():
        return []
    entries: list[RunIndexEntry] = []
    for child in sorted(runs_root.iterdir()):
        if child.name in _SKIP_DIRNAMES or not child.is_dir():
            continue
        entry = _index_entry(child)
        if entry is not None:
            entries.append(entry)
    # Newest first; run_id breaks ties so same-second runs keep a stable order.
    entries.sort(key=lambda e: (e.created_at, e.run_id), reverse=True)
    if limit is not None:
        return entries[: max(limit, 0)]
    return entries


def find_previous_run(
    runs_root: Path,
    dut: DutSnapshot | RunIndexEntry,
    *,
    before: datetime | str | None = None,
    entries: Iterable[RunIndexEntry] | None = None,
) -> RunIndexEntry | None:
    """Return the most recent earlier run of the same DUT, or ``None``.

    "Same DUT" is the four identity axes -- library, cell, layout view, source
    view -- exactly the tuple the old ``task_id`` encoded. The recipe is
    deliberately not part of the match: the question this answers is "how did
    this cell do last time", which is what makes a discrepancy delta readable.

    ``before`` accepts a timestamp or a ``run_id``; when omitted, and ``dut``
    is itself an index entry, that entry's own run is the cut-off. Pass
    ``entries`` to reuse a listing instead of re-scanning the directory.
    """

    if isinstance(dut, RunIndexEntry):
        key = dut.dut_key
        if before is None:
            before = dut.created_at
    else:
        key = f"{dut.library}__{dut.cell}__{dut.layout_view}__{dut.source_view}"

    cutoff: datetime | None
    if isinstance(before, str):
        cutoff, _ = parse_run_id(before)
    else:
        cutoff = before
    if cutoff is not None and cutoff.tzinfo is None:
        # Stored timestamps are always UTC-aware; a naive cut-off is read as
        # UTC rather than raising on the comparison below.
        cutoff = cutoff.replace(tzinfo=timezone.utc)

    rows = list(entries) if entries is not None else list_runs(runs_root)
    for entry in rows:  # already newest first
        if entry.dut_key != key:
            continue
        if cutoff is not None and entry.created_at >= cutoff:
            continue
        if isinstance(dut, RunIndexEntry) and entry.run_id == dut.run_id:
            continue
        return entry
    return None


def prune_runs(runs_root: Path, keep: int, *, dry_run: bool = False) -> list[str]:
    """Delete all but the ``keep`` newest runs. Returns the removed run ids.

    Manual entry point -- nothing calls it on a timer. Three classes of run are
    never deleted, and they do not consume a ``keep`` slot either:

    * pinned runs (starred, or carrying any tag) -- the user said "keep this";
    * unfinished runs (``overall == "pending"``) -- one of them may be running
      right now, and deleting a live run's log directory is a data-loss bug;
    * anything :func:`list_runs` could not parse -- if we cannot tell what a
      directory is, we do not remove it.

    ``keep <= 0`` means "unlimited" (the ``WorkspaceConfig.keep_runs`` default)
    and removes nothing. ``dry_run=True`` reports what would go without
    touching the disk.
    """

    if keep <= 0:
        return []
    kept = 0
    removed: list[str] = []
    for entry in list_runs(runs_root):  # newest first
        if entry.pinned or entry.overall == "pending":
            continue
        if kept < keep:
            kept += 1
            continue
        if not dry_run:
            try:
                shutil.rmtree(entry.run_dir)
            except OSError as exc:
                logger.warning("%s: cannot prune (%s)", entry.run_dir, exc)
                continue
        removed.append(entry.run_id)
    return removed
