"""Shared pytest fixtures for the Auto_ext test suite.

Real fixtures (temp workdir, mock-binary PATH, fake project/tasks config)
are added when the corresponding core/tools code lands. Phase 1 only
exposes the ``mocks_dir`` fixture so future tests can locate the
shell-mock binaries by absolute path.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def mocks_dir() -> Path:
    """Absolute path to ``tests/mocks/`` (the fake calibre/qrc/jivaro/si/strmout)."""

    return Path(__file__).resolve().parent / "mocks"
