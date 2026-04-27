"""Tests for :mod:`auto_ext.ui.app` — QSettings persistence of last config_dir."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QSettings  # noqa: E402

from auto_ext.ui.app import (  # noqa: E402
    _LAST_CONFIG_KEY,
    _QSETTINGS_APP,
    _QSETTINGS_ORG,
    _read_last_config_dir,
    _write_last_config_dir,
)


@pytest.fixture
def isolated_qsettings(qapp, tmp_path: Path):
    """Redirect QSettings to a fresh tmp dir so tests can't pollute the
    developer's real ``~/.config/Auto_ext/Auto_ext.conf`` (or its
    Windows registry equivalent)."""
    qapp.setOrganizationName(_QSETTINGS_ORG)
    qapp.setApplicationName(_QSETTINGS_APP)
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(
        QSettings.IniFormat, QSettings.UserScope, str(tmp_path / "qsettings")
    )
    # Clear any leakage from a previous test that mutated default scope.
    QSettings().remove(_LAST_CONFIG_KEY)
    yield


def test_read_returns_none_when_unset(isolated_qsettings) -> None:
    assert _read_last_config_dir() is None


def test_write_then_read_round_trip(
    isolated_qsettings, project_tools_config: Path
) -> None:
    _write_last_config_dir(project_tools_config)
    assert _read_last_config_dir() == project_tools_config.resolve()


def test_read_ignores_stale_nonexistent_path(
    isolated_qsettings, tmp_path: Path
) -> None:
    """A previously valid config_dir that's been deleted should not
    crash the launch — read returns None and run_gui falls through
    to the empty-state banner."""
    _write_last_config_dir(tmp_path / "moved_or_deleted")
    assert _read_last_config_dir() is None


def test_read_ignores_dir_without_project_yaml(
    isolated_qsettings, tmp_path: Path
) -> None:
    """A directory that exists but no longer carries a project.yaml is
    treated as stale (e.g. user wiped the contents but kept the dir)."""
    bare = tmp_path / "bare_dir"
    bare.mkdir()
    _write_last_config_dir(bare)
    assert _read_last_config_dir() is None


def test_write_resolves_to_absolute(
    isolated_qsettings, project_tools_config: Path, monkeypatch
) -> None:
    """Even if a relative-ish path comes in, the persisted entry is the
    absolute resolved form so it survives cwd changes between sessions."""
    monkeypatch.chdir(project_tools_config.parent)
    _write_last_config_dir(Path(project_tools_config.name))
    settings = QSettings()
    stored = settings.value(_LAST_CONFIG_KEY)
    assert Path(stored).is_absolute()
    assert Path(stored) == project_tools_config.resolve()


def test_write_is_a_noop_for_none(isolated_qsettings) -> None:
    """The signal payload is loosely typed (object); a None payload
    must not write anything."""
    _write_last_config_dir(None)
    assert _read_last_config_dir() is None


def test_subsequent_write_overwrites(
    isolated_qsettings, project_tools_config: Path, tmp_path: Path
) -> None:
    """Loading a different project.yaml replaces the persisted entry,
    not appends — the user's current pick should always win."""
    other = tmp_path / "other_config"
    other.mkdir()
    (other / "project.yaml").write_text("{}\n", encoding="utf-8")

    _write_last_config_dir(project_tools_config)
    _write_last_config_dir(other)
    assert _read_last_config_dir() == other.resolve()
