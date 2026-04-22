"""Phase 1 sanity checks.

These exist purely to catch the packaging from regressing before any
real logic lands. Once Phase 2 arrives, prune or fold them into the
real module-level test files.
"""

from __future__ import annotations

import auto_ext
from auto_ext.tools.base import Tool, ToolResult


def test_package_version_present() -> None:
    assert isinstance(auto_ext.__version__, str)
    assert auto_ext.__version__


def test_tool_abc_is_abstract() -> None:
    # Instantiating the ABC directly must fail; concrete tools override the three methods.
    import pytest

    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract]


def test_tool_result_defaults() -> None:
    r = ToolResult(success=True)
    assert r.success is True
    assert r.stdout_path is None
    assert r.artifact_paths == []
    assert r.diagnostics == {}


def test_mocks_dir_fixture(mocks_dir) -> None:
    # All five mock binaries exist in the fixture's reported location.
    expected = {"calibre", "qrc", "jivaro", "si", "strmout"}
    present = {p.name for p in mocks_dir.iterdir() if p.is_file()}
    missing = expected - present
    assert not missing, f"missing mock scripts: {missing}"
