"""The projects this user has opened, so switching is a pick and not a hunt.

A project is a config directory (``docs/refactor/PROJECTS_AND_SETUP.md`` §1)
and nothing about it is stored here that is not already on disk: this is a
list of directories the user has actually opened, most recently first. It is a
**convenience, not a source of truth**. Any directory can still be opened by
path -- ``--config-dir``, or the file dialog -- without ever appearing here,
and an entry whose directory has moved or been deleted is dropped silently at
read time rather than failing the launch. That is the same rule
:func:`auto_ext.ui.app._read_last_config_dir` already applies to the single
remembered path this list generalises.

Why not a file on disk
----------------------
Where would it go? Not in the config directory -- a project cannot hold the
list of other projects. Not in the install directory -- ``deploy.sh`` swaps
that out, and the list is per user, not per install. ``~/.auto_ext/`` is the
remaining candidate and is exactly what :class:`QSettings` already manages,
in the same store as the last-opened path, so the two cannot disagree.

Storage shape: one ``QSettings`` array (``projects``), each entry a
``path`` string and an ISO-8601 ``opened`` timestamp. Order in the store is
the order returned; :func:`remember` moves a repeat visit to the front.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import QSettings

from auto_ext.model.common import utcnow
from auto_ext.model.workspace import WORKSPACE_FILENAME

__all__ = [
    "MAX_PROJECTS",
    "PROJECTS_KEY",
    "KnownProject",
    "forget_project",
    "known_projects",
    "remember_project",
]

logger = logging.getLogger(__name__)

#: The ``QSettings`` array this module owns.
PROJECTS_KEY = "projects"

#: How many entries survive. A picker is only useful while it can be read at a
#: glance, and a user who works on more projects than this reaches the oldest
#: through the file dialog, which never stopped working.
MAX_PROJECTS = 12


@dataclass(frozen=True)
class KnownProject:
    """One entry of the list."""

    path: Path
    #: ISO-8601, as written. Kept as text: it is only ever displayed and
    #: compared for ordering, and parsing it would give a reason to fail on a
    #: value some other build wrote.
    opened: str = ""

    @property
    def name(self) -> str:
        """What to show in a picker.

        The config directory is nearly always called ``config``, so its own
        name identifies nothing -- the parent is the install, which is what
        the user calls the project.
        """

        if self.path.name in ("config", "") and self.path.parent.name:
            return self.path.parent.name
        return self.path.name or str(self.path)


def _valid(path: Path) -> bool:
    """Same permissiveness as the single last-opened path.

    ``workspace.yaml`` rather than mere existence: a directory that can no
    longer be loaded must not be offered, or the picker becomes a list of
    ways to produce an error banner.
    """

    return path.is_dir() and (path / WORKSPACE_FILENAME).is_file()


def _read_all(settings: QSettings) -> list[KnownProject]:
    """Every stored entry, valid or not, in stored order."""

    entries: list[KnownProject] = []
    size = settings.beginReadArray(PROJECTS_KEY)
    try:
        for index in range(size):
            settings.setArrayIndex(index)
            raw = settings.value("path", "")
            if not raw or not isinstance(raw, str):
                continue
            opened = settings.value("opened", "")
            entries.append(
                KnownProject(Path(raw), opened if isinstance(opened, str) else "")
            )
    finally:
        settings.endArray()
    return entries


def _write_all(settings: QSettings, entries: list[KnownProject]) -> None:
    # A shorter array leaves the old tail behind, and QSettings has no
    # "truncate": remove the key first so the store holds exactly this list.
    settings.remove(PROJECTS_KEY)
    settings.beginWriteArray(PROJECTS_KEY, len(entries))
    try:
        for index, entry in enumerate(entries):
            settings.setArrayIndex(index)
            settings.setValue("path", str(entry.path))
            settings.setValue("opened", entry.opened)
    finally:
        settings.endArray()


def known_projects(*, settings: QSettings | None = None) -> list[KnownProject]:
    """Projects that still exist and still load, most recently opened first.

    Dropping the rest is not a repair pass: nothing is written back here, so a
    directory that is merely unmounted right now reappears in the picker once
    it is mounted again.
    """

    store = settings if settings is not None else QSettings()
    return [entry for entry in _read_all(store) if _valid(entry.path)]


def remember_project(config_dir: Path | str | None, *, settings: QSettings | None = None) -> None:
    """Record ``config_dir`` as the most recently opened project.

    Idempotent per directory: a repeat visit moves the existing entry to the
    front rather than adding a second one. Called on every successful load, so
    it must never raise -- a list of recent projects is not worth failing a
    launch over.
    """

    if config_dir is None:
        return
    try:
        path = Path(config_dir).resolve()
    except (TypeError, OSError) as exc:
        logger.warning("could not resolve project dir for QSettings: %r (%s)", config_dir, exc)
        return

    store = settings if settings is not None else QSettings()
    kept = [entry for entry in _read_all(store) if entry.path != path]
    kept.insert(0, KnownProject(path, utcnow().isoformat()))
    _write_all(store, kept[:MAX_PROJECTS])


def forget_project(config_dir: Path | str, *, settings: QSettings | None = None) -> bool:
    """Drop one entry. Returns whether there was one to drop.

    Removing a project from the list never touches the directory: this list
    owns nothing.
    """

    try:
        path = Path(config_dir).resolve()
    except (TypeError, OSError):
        return False
    store = settings if settings is not None else QSettings()
    entries = _read_all(store)
    kept = [entry for entry in entries if entry.path != path]
    if len(kept) == len(entries):
        return False
    _write_all(store, kept)
    return True
