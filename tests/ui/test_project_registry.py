"""The known-project list: switching projects is a pick, not a hunt.

The registry generalises the single ``last_config_dir`` the launcher already
kept, and it inherits that entry's most important property: it is a
convenience with no authority. Every test here is really about one of two
things -- that the list stays usable (ordered, deduplicated, bounded) and that
it can never cost the user anything (a moved directory is dropped, not
resurrected as an error banner; forgetting an entry does not touch the
directory).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QSettings  # noqa: E402

from auto_ext.model.workspace import WORKSPACE_FILENAME  # noqa: E402
from auto_ext.ui.app import _QSETTINGS_APP, _QSETTINGS_ORG  # noqa: E402
from auto_ext.ui.project_registry import (  # noqa: E402
    MAX_PROJECTS,
    PROJECTS_KEY,
    forget_project,
    known_projects,
    remember_project,
)


@pytest.fixture
def isolated_qsettings(qapp, tmp_path: Path):
    """A fresh QSettings store, so nothing here can see or touch the real one."""

    qapp.setOrganizationName(_QSETTINGS_ORG)
    qapp.setApplicationName(_QSETTINGS_APP)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(
        QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "qsettings")
    )
    QSettings().remove(PROJECTS_KEY)
    yield


def make_project(root: Path, name: str) -> Path:
    """A directory that looks enough like a project to be offered."""

    config = root / name / "config"
    config.mkdir(parents=True)
    (config / WORKSPACE_FILENAME).write_text("schema_version: 1\n", encoding="utf-8")
    return config


def test_nothing_remembered_is_an_empty_list(isolated_qsettings) -> None:
    assert known_projects() == []


def test_a_remembered_project_comes_back(isolated_qsettings, tmp_path: Path) -> None:
    config = make_project(tmp_path, "alpha")
    remember_project(config)
    assert [entry.path for entry in known_projects()] == [config.resolve()]


def test_the_most_recent_project_is_first(isolated_qsettings, tmp_path: Path) -> None:
    first = make_project(tmp_path, "alpha")
    second = make_project(tmp_path, "beta")
    remember_project(first)
    remember_project(second)
    assert [e.path for e in known_projects()] == [second.resolve(), first.resolve()]


def test_reopening_a_project_moves_it_rather_than_duplicating_it(
    isolated_qsettings, tmp_path: Path
) -> None:
    first = make_project(tmp_path, "alpha")
    second = make_project(tmp_path, "beta")
    remember_project(first)
    remember_project(second)
    remember_project(first)
    assert [e.path for e in known_projects()] == [first.resolve(), second.resolve()]


def test_the_list_is_bounded(isolated_qsettings, tmp_path: Path) -> None:
    """A picker stops being useful long before it stops fitting on screen."""

    for index in range(MAX_PROJECTS + 3):
        remember_project(make_project(tmp_path, f"p{index}"))
    entries = known_projects()
    assert len(entries) == MAX_PROJECTS
    # the oldest fell off, not the newest
    assert entries[0].path.parent.name == f"p{MAX_PROJECTS + 2}"


def test_shrinking_the_list_leaves_no_tail_behind(
    isolated_qsettings, tmp_path: Path
) -> None:
    """QSettings arrays have no truncate; a shorter write must still replace."""

    kept = make_project(tmp_path, "kept")
    dropped = make_project(tmp_path, "dropped")
    remember_project(kept)
    remember_project(dropped)
    forget_project(dropped)
    assert [e.path for e in known_projects()] == [kept.resolve()]


def test_a_project_that_moved_away_is_dropped_not_reported(
    isolated_qsettings, tmp_path: Path
) -> None:
    """The same permissiveness the single remembered path already had."""

    import shutil

    config = make_project(tmp_path, "gone")
    remember_project(config)
    shutil.rmtree(config.parent)
    assert known_projects() == []


def test_a_directory_that_can_no_longer_be_loaded_is_not_offered(
    isolated_qsettings, tmp_path: Path
) -> None:
    """A picker of ways to produce an error banner is worse than no picker."""

    config = make_project(tmp_path, "broken")
    remember_project(config)
    (config / WORKSPACE_FILENAME).unlink()
    assert known_projects() == []


def test_dropping_a_stale_entry_does_not_rewrite_the_store(
    isolated_qsettings, tmp_path: Path
) -> None:
    """An unmounted project must come back when it is mounted again.

    Reading is not a repair pass: if the read pruned the store, a project on a
    network share would be forgotten forever by one launch taken while the
    share was down.
    """

    import shutil

    alive = make_project(tmp_path, "alive")
    away = make_project(tmp_path, "away")
    remember_project(alive)
    remember_project(away)

    moved = tmp_path / "elsewhere"
    shutil.move(str(away.parent), str(moved))
    assert [e.path for e in known_projects()] == [alive.resolve()]

    shutil.move(str(moved), str(away.parent))
    assert [e.path for e in known_projects()] == [away.resolve(), alive.resolve()]


def test_forgetting_a_project_never_touches_the_directory(
    isolated_qsettings, tmp_path: Path
) -> None:
    config = make_project(tmp_path, "alpha")
    remember_project(config)
    assert forget_project(config) is True
    assert known_projects() == []
    assert (config / WORKSPACE_FILENAME).is_file()


def test_forgetting_something_that_was_never_there_says_so(
    isolated_qsettings, tmp_path: Path
) -> None:
    assert forget_project(tmp_path / "never") is False


def test_remembering_none_is_a_noop(isolated_qsettings) -> None:
    """``config_loaded`` carries ``object``; the payload may be anything."""

    remember_project(None)
    assert known_projects() == []


def test_the_display_name_is_the_install_not_the_config_dir(
    isolated_qsettings, tmp_path: Path
) -> None:
    """Every config directory is called ``config``, which identifies nothing."""

    config = make_project(tmp_path, "Auto_ext_pro")
    remember_project(config)
    assert known_projects()[0].name == "Auto_ext_pro"


def test_a_config_dir_with_its_own_name_keeps_it(
    isolated_qsettings, tmp_path: Path
) -> None:
    """Not everyone calls it ``config``, and the rule must not hide that."""

    directory = tmp_path / "install" / "site_config"
    directory.mkdir(parents=True)
    (directory / WORKSPACE_FILENAME).write_text("schema_version: 1\n", encoding="utf-8")
    remember_project(directory)
    assert known_projects()[0].name == "site_config"
