"""Shared pytest fixtures for the Auto_ext test suite.

Phase 2 adds fixtures used by the ``tests/core/`` suite: a path to the
static fixtures dir, a temp workarea populated with ``cds.lib`` + ``.cdsinit``,
a temp Auto_ext root, an override dict, a clean-env helper, and a
session-scoped probe that reports whether this host can create symlinks
(Linux always can; Windows only with Developer Mode or Admin).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from auto_ext.core.config import ProjectConfig


@pytest.fixture(scope="session")
def mocks_dir() -> Path:
    """Absolute path to ``tests/mocks/`` (the fake calibre/qrc/jivaro/si/strmout)."""

    return Path(__file__).resolve().parent / "mocks"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Absolute path to ``tests/fixtures/`` (sample yaml / j2 / lvs reports)."""

    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def workarea(tmp_path: Path) -> Path:
    """A temp workarea with empty ``cds.lib`` and ``.cdsinit`` placeholders.

    Mirrors the structure EDA tools expect as cwd: tools read ``cds.lib`` and
    ``si.env``; the file contents are irrelevant for unit tests.
    """

    wa = tmp_path / "workarea"
    wa.mkdir()
    (wa / "cds.lib").write_text("; mock cds.lib\n", encoding="utf-8")
    (wa / ".cdsinit").write_text("; mock .cdsinit\n", encoding="utf-8")
    return wa


@pytest.fixture
def auto_ext_root(tmp_path: Path) -> Path:
    """A temp directory to play the role of the ``Auto_ext/`` project root."""

    root = tmp_path / "Auto_ext"
    root.mkdir()
    return root


@pytest.fixture
def sample_overrides() -> dict[str, str]:
    """Deterministic env-override dict used by template / env tests."""

    return {"WORK_ROOT": "/w", "EMP": "alice", "LIB": "tsmc180"}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Clear env vars that tests under ``tests/core/test_env.py`` exercise.

    Tests then call ``monkeypatch.setenv`` to install the specific shell
    values they need. Restored automatically at teardown.
    """

    for var in (
        "WORK_ROOT",
        "WORK_ROOT2",
        "VERIFY_ROOT",
        "SETUP_ROOT",
        "PDK_LAYER_MAP_FILE",
        "EMP",
        "LIB",
        "FOO",
        "BAR",
        "BAZ",
        "UNDEFINED_X",
        "AUTO_EXT_TEST_VAR",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture(scope="session")
def can_symlink(tmp_path_factory: pytest.TempPathFactory) -> bool:
    """Return True iff this host can create a symlink (skip decorator predicate)."""

    probe = tmp_path_factory.mktemp("symlink_probe")
    src = probe / "src"
    src.write_text("x", encoding="utf-8")
    dst = probe / "dst"
    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def project_config(fixtures_dir: Path) -> "ProjectConfig":
    """A :class:`ProjectConfig` loaded from ``fixtures/project_minimal.yaml``.

    Imported lazily so this fixture can live in the shared conftest even
    before :mod:`auto_ext.core.config` is implemented.
    """

    from auto_ext.core.config import load_project

    return load_project(fixtures_dir / "project_minimal.yaml")
