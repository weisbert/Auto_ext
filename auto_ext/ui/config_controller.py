"""Shared config state for the GUI screens.

One :class:`ConfigController` per :class:`~auto_ext.ui.main_window.MainWindow`;
the Cells screen, the Recipes screen and the Setup drawer all hold the same
reference, so no two screens can disagree about what is on disk versus what is
pending.

What it owns
------------

The v2 file set described by ``docs/refactor/01-schema.md``::

    <config_dir>/workspace.yaml          -> WorkspaceConfig
    <config_dir>/cells.yaml              -> CellBook
    <config_dir>/profiles/<id>.yaml      -> PdkProfile  (id from workspace.pdk_profile)
    <recipes search path>/<id>.yaml      -> Recipe, one file each

plus the profile's ``<id>.health.json`` cache, read through
:func:`auto_ext.core.health.cached_or_check`.

Edit model
----------

Edits are *staged whole documents*, not dotted-key diffs. Every new screen
hands back a validated pydantic object (``CellBook``, ``Recipe``,
``PdkProfile``, ``WorkspaceConfig``), so staging a partial key path would only
re-open a validation hole the models already closed. The dirty queue is keyed
by document id -- ``workspace``, ``cells``, ``profile``, ``recipe:<id>`` --
and a second stage of the same document replaces the first. One :meth:`save`
writes every staged document, or none of them: all YAML text is rendered in
memory first, so a document that fails to serialize aborts the save before
anything is written.

Save detects external mtime changes since :meth:`load`; callers opt in to
overwrite via ``force=True`` after confirming with the user.

The runner adapter
------------------

:attr:`project` and :attr:`tasks` are what the Run button hands
:func:`auto_ext.core.runner.run_tasks`. They are *derived*, not loaded: the
runner still speaks ``ProjectConfig`` + ``TaskConfig``, and
:func:`auto_ext.core.config.project_from_workspace` /
:func:`~auto_ext.core.config.tasks_from_cells` adapt the two v2 documents into
them. Nothing on disk backs either object, so nothing can disagree with
``workspace.yaml`` / ``cells.yaml``; the day the runner takes the v2 models
directly, both properties go away and no file has to move.

:attr:`tasks` deliberately includes disabled rows (``include_disabled=True``).
The Cells screen resolves a selected row by ``task_id``, and an unchecked row
must resolve to "not selected", not to "row missing". Filtering by
``enabled`` is the screen's job and it already does it.

:attr:`can_run` is the predicate for "pressing Run could actually start
something": it needs a workspace, at least one cell, a profile and a recipe.

Unverified assumptions (all of them, in one place)
--------------------------------------------------

* The recipe search path is reproduced here rather than imported from
  :mod:`auto_ext.cli`, which owns the canonical
  ``auto_ext.cli.recipe_search_path``. The GUI must not import the CLI (that
  would pull typer into GUI start-up), so :func:`recipe_search_path` below is
  a copy; ``tests/ui/test_config_controller.py`` asserts the two agree.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from auto_ext.core.config import (
    ProjectConfig,
    TaskConfig,
    project_from_workspace,
    tasks_from_cells,
)
from auto_ext.core.errors import AutoExtError
from auto_ext.core.health import cached_or_check
from auto_ext.core.profile_discover import profile_to_yaml, read_profile_yaml
from auto_ext.model.cells import (
    CELLS_FILENAME,
    CellBook,
    dump_cells_yaml,
    load_cells_with_raw,
)
from auto_ext.model.pdk import PdkHealthReport, PdkProfile
from auto_ext.model.recipe import Recipe, dump_recipe_yaml, load_recipe_with_raw
from auto_ext.model.workspace import (
    WORKSPACE_FILENAME,
    WorkspaceConfig,
    dump_workspace_yaml,
    load_workspace_with_raw,
)

__all__ = [
    "PROFILES_DIRNAME",
    "RECIPES_DIRNAME",
    "RECIPES_ENV_VAR",
    "ConfigController",
    "recipe_search_path",
]

#: Environment variable that prepends directories to the recipe search path.
#: Same name the CLI uses; ``docs/refactor/01-schema.md`` section 1.2.
RECIPES_ENV_VAR = "AUTO_EXT_RECIPES"

#: Sub-directory of ``config/`` holding ``<profile_id>.yaml``.
PROFILES_DIRNAME = "profiles"

#: Sub-directory of the Auto_ext root holding ``<recipe_id>.yaml``.
RECIPES_DIRNAME = "recipes"


def recipe_search_path(
    auto_ext_root: Path | None = None,
    config_dir: Path | None = None,
    recipes_dir: Path | None = None,
) -> list[Path]:
    """The recipe search path, earliest first; later entries shadow earlier.

    ``$AUTO_EXT_RECIPES`` -> ``~/.auto_ext/recipes`` -> ``<root>/recipes`` ->
    ``<config_dir>/recipes`` -> ``recipes_dir``. Duplicates collapse keeping
    the last occurrence, so naming one directory twice does not make a recipe
    look shadowed by itself.
    """

    candidates: list[Path] = []
    env_value = os.environ.get(RECIPES_ENV_VAR)
    if env_value:
        candidates.extend(Path(part) for part in env_value.split(os.pathsep) if part)
    candidates.append(Path.home() / ".auto_ext" / RECIPES_DIRNAME)
    if auto_ext_root is not None:
        root = Path(auto_ext_root).resolve()
    elif config_dir is not None:
        root = Path(config_dir).resolve().parent
    else:
        root = Path.cwd().resolve()
    candidates.append(root / RECIPES_DIRNAME)
    if config_dir is not None:
        candidates.append(Path(config_dir).resolve() / RECIPES_DIRNAME)
    if recipes_dir is not None:
        candidates.append(Path(recipes_dir))

    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in ordered:
            ordered.remove(resolved)
        ordered.append(resolved)
    return ordered


def _mtime_ns(path: Path | None) -> int:
    """``st_mtime_ns``, or ``0`` for a path that is absent or unreadable."""

    if path is None:
        return 0
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


class ConfigController(QObject):
    """Owner of the loaded v2 documents plus the queue of staged edits."""

    #: Emitted after a successful :meth:`load` / :meth:`reload`. Payload is
    #: the loaded ``config_dir`` (a :class:`pathlib.Path`).
    config_loaded = pyqtSignal(object)
    #: Emitted after a successful :meth:`save`, once the re-load has landed.
    config_saved = pyqtSignal(object)
    #: Emitted on any user-visible failure (load error, mtime conflict,
    #: serialization failure, write failure). Payload is a human message.
    config_error = pyqtSignal(str)
    #: Emitted when :attr:`is_dirty` flips.
    dirty_changed = pyqtSignal(bool)
    #: Emitted with the :class:`PdkHealthReport` (or ``None``) whenever the
    #: profile's health is (re-)evaluated. The Setup drawer listens on this.
    health_changed = pyqtSignal(object)

    def __init__(
        self,
        *,
        auto_ext_root: Path | None = None,
        workarea: Path | None = None,
        recipes_dir: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_dir: Path | None = None
        self._auto_ext_root = auto_ext_root
        self._workarea = workarea
        self._recipes_dir = recipes_dir

        self._workspace: WorkspaceConfig | None = None
        self._workspace_raw: Any = None
        self._cells: CellBook | None = None
        self._cells_raw: Any = None
        self._profile: PdkProfile | None = None
        self._profile_path: Path | None = None
        self._recipes: dict[str, Recipe] = {}
        self._recipe_paths: dict[str, Path] = {}
        self._recipe_raw: dict[str, Any] = {}
        self._health: PdkHealthReport | None = None

        #: ``document id -> object``; ``None`` means "delete this document".
        self._pending: dict[str, Any] = {}
        #: Path -> ``st_mtime_ns`` as observed at load time.
        self._mtimes: dict[Path, int] = {}

    # ---- read-only views ---------------------------------------------

    @property
    def config_dir(self) -> Path | None:
        return self._config_dir

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
    def runs_root(self) -> Path | None:
        """``<auto_ext_root>/runs`` -- what the Runs screen lists."""

        root = self.auto_ext_root
        return None if root is None else root / "runs"

    @property
    def recipes_dir(self) -> Path | None:
        """Where a newly created recipe is written.

        The last entry of the search path, so a new recipe shadows rather
        than collides with anything already found.
        """

        search = recipe_search_path(
            self._auto_ext_root, self._config_dir, self._recipes_dir
        )
        return search[-1] if search else None

    @property
    def workspace(self) -> WorkspaceConfig | None:
        return self._staged("workspace", self._workspace)

    @property
    def cells(self) -> CellBook | None:
        return self._staged("cells", self._cells)

    @property
    def profile(self) -> PdkProfile | None:
        return self._staged("profile", self._profile)

    @property
    def profile_path(self) -> Path | None:
        return self._profile_path

    @property
    def health_report(self) -> PdkHealthReport | None:
        """The last health report, or ``None`` if none has been taken."""

        return self._health

    def recipe_ids(self) -> list[str]:
        """Every known recipe id, sorted, staged deletions removed."""

        ids = set(self._recipes)
        for key, value in self._pending.items():
            if not key.startswith("recipe:"):
                continue
            recipe_id = key.split(":", 1)[1]
            if value is None:
                ids.discard(recipe_id)
            else:
                ids.add(recipe_id)
        return sorted(ids)

    def recipe(self, recipe_id: str) -> Recipe | None:
        """One recipe by id, staged edit first."""

        key = f"recipe:{recipe_id}"
        if key in self._pending:
            return self._pending[key]
        return self._recipes.get(recipe_id)

    @property
    def recipes(self) -> list[Recipe]:
        """Every recipe, in :meth:`recipe_ids` order."""

        found = [self.recipe(rid) for rid in self.recipe_ids()]
        return [r for r in found if r is not None]

    def recipe_path(self, recipe_id: str) -> Path | None:
        """Where ``recipe_id`` lives, or would be written if it is new."""

        known = self._recipe_paths.get(recipe_id)
        if known is not None:
            return known
        target = self.recipes_dir
        return None if target is None else target / f"{recipe_id}.yaml"

    # -- runner adapter --------------------------------------------------

    @property
    def project(self) -> ProjectConfig | None:
        """The workspace, adapted for the runner. See the module docstring."""

        workspace = self.workspace
        if workspace is None:
            return None
        project = project_from_workspace(workspace)
        if self._config_dir is not None:
            from auto_ext.model.workspace import WORKSPACE_FILENAME

            project.source_path = (self._config_dir / WORKSPACE_FILENAME).resolve()
        return project

    @property
    def tasks(self) -> list[TaskConfig]:
        """Every cell as a runner task, disabled rows included."""

        book = self.cells
        if book is None:
            return []
        return tasks_from_cells(book, include_disabled=True)

    @property
    def can_run(self) -> bool:
        """Whether pressing Run could actually start the EDA flow."""

        book = self.cells
        return (
            self.workspace is not None
            and book is not None
            and len(book) > 0
            and self.profile is not None
            and bool(self.recipe_ids())
        )

    def run_recipe(self, recipe_id: str | None = None) -> Recipe | None:
        """The recipe a dispatch should use.

        ``recipe_id`` is the run bar's override. With no override there has
        to be exactly one candidate: ``run_tasks`` takes one recipe for the
        whole batch, so silently picking the alphabetically first one out of
        several would run the wrong settings without saying so.
        """

        if recipe_id:
            return self.recipe(recipe_id)
        loaded = self.recipes
        return loaded[0] if len(loaded) == 1 else None

    # ---- dirty queue --------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        return bool(self._pending)

    def pending_keys(self) -> list[str]:
        """Document ids with a staged edit, sorted. ``recipe:<id>`` included."""

        return sorted(self._pending)

    def _staged(self, key: str, fallback: Any) -> Any:
        return self._pending[key] if key in self._pending else fallback

    def _stage(self, key: str, value: Any) -> None:
        was_dirty = self.is_dirty
        self._pending[key] = value
        if not was_dirty:
            self.dirty_changed.emit(True)

    def stage_workspace(self, workspace: WorkspaceConfig) -> None:
        """Queue a replacement ``workspace.yaml``."""

        self._stage("workspace", workspace)

    def stage_cells(self, cells: CellBook) -> None:
        """Queue a replacement ``cells.yaml``."""

        self._stage("cells", cells)

    def stage_profile(self, profile: PdkProfile) -> None:
        """Queue a replacement ``config/profiles/<id>.yaml``."""

        self._stage("profile", profile)

    def stage_recipe(self, recipe: Recipe) -> None:
        """Queue a new or edited ``recipes/<recipe_id>.yaml``."""

        self._stage(f"recipe:{recipe.recipe_id}", recipe)

    def stage_recipe_deletion(self, recipe_id: str) -> None:
        """Queue the removal of ``recipes/<recipe_id>.yaml``.

        Staging a deletion for a recipe that was never on disk drops the
        pending creation instead of queueing a delete of nothing.
        """

        key = f"recipe:{recipe_id}"
        if recipe_id not in self._recipes:
            if key in self._pending:
                del self._pending[key]
                if not self.is_dirty:
                    self.dirty_changed.emit(False)
            return
        self._stage(key, None)

    def revert(self) -> None:
        """Discard every staged edit; keep the loaded documents."""

        if not self.is_dirty:
            return
        self._pending.clear()
        self.dirty_changed.emit(False)

    # ---- load ---------------------------------------------------------

    def load(self, config_dir: Path) -> None:
        """Load the v2 file set from ``config_dir``.

        On success: replaces every document, clears the staged edits, records
        each file's mtime for later conflict detection, emits
        ``dirty_changed(False)`` (when it was dirty) then :attr:`config_loaded`.
        On any parse / schema / IO failure: emits :attr:`config_error` and
        leaves the controller's previous state untouched, so a mistyped path
        never empties a loaded window.
        """

        config_dir = Path(config_dir)
        workspace_path = config_dir / WORKSPACE_FILENAME
        cells_path = config_dir / CELLS_FILENAME

        if not workspace_path.is_file() and (config_dir / "project.yaml").is_file():
            self.config_error.emit(
                f"{config_dir} holds a v1 project.yaml but no {WORKSPACE_FILENAME}. "
                f"Run `auto-ext migrate` on it, or create a new project from raws."
            )
            return

        try:
            workspace, workspace_raw = load_workspace_with_raw(workspace_path)
            cells, cells_raw = load_cells_with_raw(cells_path)
            profile_path = (
                config_dir / PROFILES_DIRNAME / f"{workspace.pdk_profile}.yaml"
            )
            profile = read_profile_yaml(profile_path)
        except (AutoExtError, OSError) as exc:
            self.config_error.emit(str(exc))
            return

        recipes, recipe_paths, recipe_raw, recipe_errors = self._read_recipes(config_dir)

        was_dirty = self.is_dirty
        self._config_dir = config_dir
        self._workspace = workspace
        self._workspace_raw = workspace_raw
        self._cells = cells
        self._cells_raw = cells_raw
        self._profile = profile
        self._profile_path = profile_path
        self._recipes = recipes
        self._recipe_paths = recipe_paths
        self._recipe_raw = recipe_raw
        self._pending.clear()
        self._health = None

        self._mtimes = {
            workspace_path: _mtime_ns(workspace_path),
            cells_path: _mtime_ns(cells_path),
            profile_path: _mtime_ns(profile_path),
        }
        for path in recipe_paths.values():
            self._mtimes[path] = _mtime_ns(path)

        if was_dirty:
            self.dirty_changed.emit(False)
        self.config_loaded.emit(config_dir)
        for message in recipe_errors:
            self.config_error.emit(message)

    def reload(self) -> None:
        if self._config_dir is not None:
            self.load(self._config_dir)

    def _read_recipes(
        self, config_dir: Path
    ) -> tuple[dict[str, Recipe], dict[str, Path], dict[str, Any], list[str]]:
        """Walk the search path; later directories shadow earlier ones.

        A recipe that fails to load does not fail the whole load: the rest of
        the library is still usable, and the message names the file, which is
        what the user needs in order to fix it.
        """

        recipes: dict[str, Recipe] = {}
        paths: dict[str, Path] = {}
        raws: dict[str, Any] = {}
        errors: list[str] = []
        for directory in recipe_search_path(
            self._auto_ext_root, config_dir, self._recipes_dir
        ):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.yaml")):
                try:
                    recipe, raw = load_recipe_with_raw(path)
                except (AutoExtError, OSError) as exc:
                    errors.append(f"skipped recipe {path}: {exc}")
                    continue
                recipes[recipe.recipe_id] = recipe
                paths[recipe.recipe_id] = path
                raws[recipe.recipe_id] = raw
        return recipes, paths, raws, errors

    # ---- health -------------------------------------------------------

    def refresh_health(self, *, force: bool = False) -> PdkHealthReport | None:
        """Evaluate the loaded profile's checks and emit :attr:`health_changed`.

        Uses the ``<profile>.health.json`` cache unless ``force``; a cache
        that describes a different profile revision is never returned, so a
        stale "you can run" cannot survive a profile edit.
        """

        if self._profile is None or self._profile_path is None:
            self._health = None
            self.health_changed.emit(None)
            return None
        try:
            report, _from_cache = cached_or_check(
                self._profile_path, self._profile, force=force
            )
        except (AutoExtError, OSError) as exc:
            self.config_error.emit(f"health check failed: {exc}")
            self._health = None
            self.health_changed.emit(None)
            return None
        self._health = report
        self.health_changed.emit(report)
        return report

    # ---- save ---------------------------------------------------------

    def has_external_change(self) -> bool:
        """``True`` if any loaded file's mtime moved since :meth:`load`."""

        return any(_mtime_ns(path) != stamp for path, stamp in self._mtimes.items())

    def externally_changed_paths(self) -> list[Path]:
        """Which files moved -- what a conflict dialog should actually name."""

        return sorted(
            path for path, stamp in self._mtimes.items() if _mtime_ns(path) != stamp
        )

    def save(self, *, force: bool = False) -> bool:
        """Write every staged document, then reload.

        Returns ``True`` on success and ``False`` when the save was blocked
        (nothing staged, nothing loaded, or an mtime conflict). Blocking
        conditions emit :attr:`config_error` with a user-facing message;
        callers handle the conflict case by prompting and retrying with
        ``force=True``.
        """

        if self._config_dir is None:
            self.config_error.emit("no config loaded")
            return False
        if not self.is_dirty:
            return False
        if not force and self.has_external_change():
            names = ", ".join(p.name for p in self.externally_changed_paths())
            self.config_error.emit(
                f"config changed on disk since load ({names}). Reload to see the "
                f"external changes, or force-save to overwrite them."
            )
            return False

        # Render everything first: a document that cannot be serialized must
        # not leave the other three half-written.
        writes: list[tuple[Path, str]] = []
        deletes: list[Path] = []
        try:
            for key in sorted(self._pending):
                path, text = self._render_document(key, self._pending[key])
                if text is None:
                    deletes.append(path)
                else:
                    writes.append((path, text))
        except (AutoExtError, OSError, ValueError) as exc:
            self.config_error.emit(str(exc))
            return False

        try:
            for path, text in writes:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
            for path in deletes:
                path.unlink(missing_ok=True)
        except OSError as exc:
            self.config_error.emit(f"write failed: {exc}")
            return False

        config_dir = self._config_dir
        self.load(config_dir)
        self.config_saved.emit(config_dir)
        return True

    def _render_document(self, key: str, value: Any) -> tuple[Path, str | None]:
        """``(path, yaml text)`` for one staged document; text ``None`` deletes."""

        config_dir = self._config_dir
        if config_dir is None:  # pragma: no cover - guarded by save()
            raise ValueError("no config loaded")
        if key == "workspace":
            return (
                config_dir / WORKSPACE_FILENAME,
                dump_workspace_yaml(value, raw=self._workspace_raw),
            )
        if key == "cells":
            return (
                config_dir / CELLS_FILENAME,
                dump_cells_yaml(value, raw=self._cells_raw),
            )
        if key == "profile":
            path = self._profile_path
            if path is None:
                path = config_dir / PROFILES_DIRNAME / f"{value.profile_id}.yaml"
            return (path, profile_to_yaml(value))
        if key.startswith("recipe:"):
            recipe_id = key.split(":", 1)[1]
            path = self.recipe_path(recipe_id)
            if path is None:
                raise ValueError(
                    f"cannot write recipe {recipe_id!r}: no recipes directory "
                    f"could be derived from the loaded config"
                )
            if value is None:
                return (path, None)
            return (path, dump_recipe_yaml(value, raw=self._recipe_raw.get(recipe_id)))
        raise ValueError(f"unknown staged document {key!r}")
