"""Shared pytest fixtures for the Auto_ext test suite.

Phase 2 adds fixtures used by the ``tests/core/`` suite: a path to the
static fixtures dir, a temp workarea populated with ``cds.lib`` + ``.cdsinit``,
a temp Auto_ext root, an override dict, a clean-env helper, and a
session-scoped probe that reports whether this host can create symlinks
(Linux always can; Windows only with Developer Mode or Admin).
"""

from __future__ import annotations

import os
import shutil
import sys
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


@pytest.fixture(scope="session")
def templates_root() -> Path:
    """Absolute path to the production templates dir (``Auto_ext/templates/``)."""

    return Path(__file__).resolve().parent.parent / "templates"


@pytest.fixture
def mocks_on_path(
    mocks_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Put the 5 mock EDA binaries on ``PATH``.

    Linux: prepend ``mocks_dir`` directly and ensure exec permission.
    Windows: generate ``.bat`` shims that invoke ``bash`` on the mock
    scripts (git-bash resolves Windows-style paths). Skips if bash is
    unavailable on Windows.
    """
    names = ("calibre", "qrc", "jivaro", "si", "strmout")

    if sys.platform == "win32":
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash required on Windows for mock integration tests")
        shim_dir = tmp_path / "mock_shims"
        shim_dir.mkdir()
        for name in names:
            mock = mocks_dir / name
            shim = shim_dir / f"{name}.bat"
            # %* passes through all argv. Paths are quoted in case of spaces.
            shim.write_text(
                f'@"{bash}" "{mock}" %*\r\n',
                encoding="utf-8",
            )
        monkeypatch.setenv(
            "PATH", f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        )
        return shim_dir
    for name in names:
        p = mocks_dir / name
        p.chmod(p.stat().st_mode | 0o111)
    monkeypatch.setenv(
        "PATH", f"{mocks_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    )
    return mocks_dir


@pytest.fixture
def project_tools_config(
    tmp_path: Path, workarea: Path, templates_root: Path
) -> Path:
    """Write a project.yaml that points at the real production templates.

    Uses ``workarea`` (the pytest-provided temp dir) for WORK_ROOT so the
    mocks' outputs stay within the test sandbox. Returns the config dir.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    wa_posix = workarea.as_posix()

    (config_dir / "project.yaml").write_text(
        f"""\
work_root: {wa_posix}
verify_root: {wa_posix}/fake/verify
setup_root: {wa_posix}/fake/setup
employee_id: alice
layer_map: {wa_posix}/fake/layers.map
env_overrides:
  WORK_ROOT: {wa_posix}
  WORK_ROOT2: {wa_posix}
  VERIFY_ROOT: {wa_posix}/fake/verify
  SETUP_ROOT: {wa_posix}/fake/setup
  PDK_LAYER_MAP_FILE: {wa_posix}/fake/layers.map
extraction_output_dir: "${{WORK_ROOT}}/cds/verify/QCI_PATH_{{cell}}"
intermediate_dir: "${{WORK_ROOT2}}"
templates:
  si: {(templates_root / 'si' / 'default.env.j2').as_posix()}
  calibre: {(templates_root / 'calibre' / 'wiodio_noConnectByNetName.qci.j2').as_posix()}
  quantus: {(templates_root / 'quantus' / 'ext.cmd.j2').as_posix()}
  jivaro: {(templates_root / 'jivaro' / 'default.xml.j2').as_posix()}
""",
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        """\
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
  jivaro:
    enabled: true
    frequency_limit: 14
    error_max: 2
""",
        encoding="utf-8",
    )
    return config_dir


@pytest.fixture
def project_config(fixtures_dir: Path) -> "ProjectConfig":
    """A :class:`ProjectConfig` loaded from ``fixtures/project_minimal.yaml``.

    Imported lazily so this fixture can live in the shared conftest even
    before :mod:`auto_ext.core.config` is implemented.
    """

    from auto_ext.core.config import load_project

    return load_project(fixtures_dir / "project_minimal.yaml")
