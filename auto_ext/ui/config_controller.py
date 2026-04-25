"""Shared config state for the GUI tabs.

One :class:`ConfigController` per :class:`MainWindow`; both the Run tab
and the Project tab hold a reference. All edits funnel through here so
the two tabs never disagree about what is on disk vs. pending.

Edits are *staged* (held in memory as a flat dotted-key dict) and only
committed to ``project.yaml`` when :meth:`save` runs. Save detects
external mtime changes since :meth:`load`; callers opt in to overwrite
via ``force=True`` after confirming with the user.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from auto_ext.core.config import (
    ProjectConfig,
    TaskConfig,
    apply_project_edits,
    apply_tasks_edits,
    dump_project_yaml,
    dump_tasks_yaml,
    load_project,
    load_tasks_with_raw,
)
from auto_ext.core.errors import AutoExtError, ConfigError


def _to_plain(obj: Any) -> Any:
    """Coerce ruamel CommentedMap/CommentedSeq to plain dict/list recursively."""
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


class ConfigController(QObject):
    """Owner of ``(config_dir, project, tasks)`` + pending edits.

    The Run tab reads :attr:`project` / :attr:`tasks` directly; the
    Project tab additionally calls :meth:`stage_edits` / :meth:`save` /
    :meth:`revert` to mutate ``project.yaml`` via ruamel roundtrip.
    """

    #: Emitted after a successful :meth:`load` or :meth:`reload`. Payload
    #: is the loaded ``config_dir``.
    config_loaded = pyqtSignal(object)
    #: Emitted after a successful :meth:`save` (after the re-load). Same
    #: payload as :attr:`config_loaded`.
    config_saved = pyqtSignal(object)
    #: Emitted on any user-visible error (load failure, mtime conflict,
    #: apply_project_edits key error, etc.). Payload is a human message.
    config_error = pyqtSignal(str)
    #: Emitted when :attr:`is_dirty` flips.
    dirty_changed = pyqtSignal(bool)

    def __init__(
        self,
        *,
        auto_ext_root: Path | None = None,
        workarea: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_dir: Path | None = None
        self._project: ProjectConfig | None = None
        self._tasks: list[TaskConfig] = []
        self._tasks_raw: Any = None
        self._tasks_path: Path | None = None
        self._pending: dict[str, Any] = {}
        #: spec-level replacement list; None = no pending tasks edits
        self._pending_tasks: list[dict[str, Any]] | None = None
        self._load_mtime_ns: int = 0
        self._tasks_mtime_ns: int = 0
        self._auto_ext_root = auto_ext_root
        self._workarea = workarea

    # ---- read-only views ---------------------------------------------

    @property
    def config_dir(self) -> Path | None:
        return self._config_dir

    @property
    def project(self) -> ProjectConfig | None:
        return self._project

    @property
    def tasks(self) -> list[TaskConfig]:
        return list(self._tasks)

    @property
    def auto_ext_root(self) -> Path | None:
        if self._auto_ext_root is not None:
            return self._auto_ext_root
        return self._config_dir.parent if self._config_dir is not None else None

    @property
    def workarea(self) -> Path | None:
        if self._workarea is not None:
            return self._workarea
        root = self.auto_ext_root
        return root.parent if root is not None else None

    @property
    def is_dirty(self) -> bool:
        return bool(self._pending) or self._pending_tasks is not None

    @property
    def pending_edits(self) -> dict[str, Any]:
        return dict(self._pending)

    @property
    def pending_task_specs(self) -> list[dict[str, Any]] | None:
        return None if self._pending_tasks is None else [dict(s) for s in self._pending_tasks]

    def task_specs_raw(self) -> list[dict[str, Any]]:
        """Current spec dicts: pending edits if staged, else parsed raw.

        Ruamel CommentedMap / CommentedSeq entries are coerced to plain
        ``dict`` / ``list`` so downstream UI code can compare by equality
        and round-trip through json.
        """

        if self._pending_tasks is not None:
            return [dict(s) for s in self._pending_tasks]
        raw = self._tasks_raw
        if raw is None:
            return []
        if isinstance(raw, dict):
            seq = raw.get("tasks", [])
        else:
            seq = raw
        return [_to_plain(entry) for entry in seq]

    def effective_env_overrides(self) -> dict[str, str]:
        """``project.env_overrides`` merged with staged ``env_overrides.*`` edits.

        The env panel reads from this so the user sees the staged state
        before Save lands on disk.
        """

        if self._project is None:
            return {}
        merged = dict(self._project.env_overrides)
        for key, value in self._pending.items():
            if not key.startswith("env_overrides."):
                continue
            name = key.split(".", 1)[1]
            if value is None:
                merged.pop(name, None)
            else:
                merged[name] = value
        return merged

    # ---- load / save -------------------------------------------------

    def load(self, config_dir: Path) -> None:
        """Load ``project.yaml`` + ``tasks.yaml`` from ``config_dir``.

        On success: resets pending edits, records the project.yaml
        mtime for later conflict detection, emits
        :attr:`dirty_changed(False)` then :attr:`config_loaded`. On any
        parse / schema / IO error: emits :attr:`config_error` and leaves
        the controller's previous state untouched.
        """

        try:
            project = load_project(config_dir / "project.yaml")
            tasks_path = config_dir / "tasks.yaml"
            tasks, tasks_raw = load_tasks_with_raw(tasks_path, project=project)
        except (AutoExtError, OSError) as exc:
            self.config_error.emit(str(exc))
            return

        was_dirty = self.is_dirty
        self._config_dir = config_dir
        self._project = project
        self._tasks = tasks
        self._tasks_raw = tasks_raw
        self._tasks_path = tasks_path
        self._pending.clear()
        self._pending_tasks = None
        self._load_mtime_ns = (
            project.source_path.stat().st_mtime_ns
            if project.source_path is not None and project.source_path.exists()
            else 0
        )
        self._tasks_mtime_ns = (
            tasks_path.stat().st_mtime_ns if tasks_path.exists() else 0
        )
        if was_dirty:
            self.dirty_changed.emit(False)
        self.config_loaded.emit(config_dir)

    def reload(self) -> None:
        if self._config_dir is not None:
            self.load(self._config_dir)

    def stage_edits(self, edits: dict[str, Any]) -> None:
        """Merge ``edits`` into the pending-edits dict.

        Value ``None`` marks a key for deletion at save time. A later
        ``stage_edits`` call with the same key overwrites the earlier
        staged value.
        """

        if not edits:
            return
        was_dirty = self.is_dirty
        self._pending.update(edits)
        if not was_dirty and self.is_dirty:
            self.dirty_changed.emit(True)

    def stage_tasks_edits(self, specs: list[dict[str, Any]]) -> None:
        """Stage a spec-level replacement list for ``tasks.yaml``.

        Unlike :meth:`stage_edits` which accumulates diffs, this stores a
        complete list of TaskSpec dicts — the TasksTab re-emits the full
        spec table whenever anything changes. ``None``-equal-to-on-disk
        is handled by :meth:`save` (no-op when specs round-trip equal).
        """

        was_dirty = self.is_dirty
        self._pending_tasks = [dict(s) for s in specs]
        if not was_dirty and self.is_dirty:
            self.dirty_changed.emit(True)

    def revert(self) -> None:
        """Discard pending edits; keep loaded ``project`` / ``tasks``."""

        if not self.is_dirty:
            return
        self._pending.clear()
        self._pending_tasks = None
        self.dirty_changed.emit(False)

    def has_external_change(self) -> bool:
        """Return ``True`` if ``project.yaml`` or ``tasks.yaml`` moved on disk."""

        if self._project is not None and self._project.source_path is not None:
            path = self._project.source_path
            current = path.stat().st_mtime_ns if path.exists() else 0
            if current != self._load_mtime_ns:
                return True
        if self._tasks_path is not None:
            current = (
                self._tasks_path.stat().st_mtime_ns if self._tasks_path.exists() else 0
            )
            if current != self._tasks_mtime_ns:
                return True
        return False

    def save(self, *, force: bool = False) -> bool:
        """Apply pending edits, write ``project.yaml`` / ``tasks.yaml``, reload.

        Returns ``True`` on success, ``False`` if the save was blocked
        (nothing to save, no config loaded, mtime conflict). On blocking
        errors ``config_error`` is emitted with a user-facing message;
        callers handle the mtime-conflict case by prompting and retrying
        with ``force=True``.
        """

        if self._project is None or self._config_dir is None:
            self.config_error.emit("no config loaded")
            return False
        if self._project.source_path is None:
            self.config_error.emit("project has no source_path")
            return False
        if not self.is_dirty:
            return False

        project_path = self._project.source_path
        tasks_path = self._tasks_path

        if not force and self.has_external_change():
            self.config_error.emit(
                f"config changed on disk since load. Reload to see external "
                f"changes, or force-save to overwrite them."
            )
            return False

        # Prepare both yaml texts in memory first so we only start writing
        # when both are valid.
        project_text: str | None = None
        tasks_text: str | None = None
        try:
            if self._pending:
                apply_project_edits(self._project.raw, self._pending)
                project_text = dump_project_yaml(self._project)
            if self._pending_tasks is not None:
                if self._tasks_raw is None:
                    raise ConfigError("tasks.yaml has no raw tree; was it loaded?")
                apply_tasks_edits(self._tasks_raw, self._pending_tasks)
                tasks_text = dump_tasks_yaml(self._tasks_raw)
        except ConfigError as exc:
            self.config_error.emit(str(exc))
            return False

        try:
            if project_text is not None:
                project_path.write_text(project_text, encoding="utf-8")
            if tasks_text is not None and tasks_path is not None:
                tasks_path.write_text(tasks_text, encoding="utf-8")
        except OSError as exc:
            self.config_error.emit(f"write failed: {exc}")
            return False

        # Re-parse so downstream sees a fresh pydantic model; load()
        # also clears pending + emits dirty_changed(False) + config_loaded.
        self.load(self._config_dir)
        self.config_saved.emit(self._config_dir)
        return True
