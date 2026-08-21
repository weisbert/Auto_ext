"""Shared pytest fixtures for the Auto_ext test suite.

Phase 2 adds fixtures used by the ``tests/core/`` suite: a path to the
static fixtures dir, a temp workarea populated with ``cds.lib`` + ``.cdsinit``,
a temp Auto_ext root, an override dict, a clean-env helper, and a
session-scoped probe that reports whether this host can create symlinks
(Linux always can; Windows only with Developer Mode or Admin).

S1 adds the Run fixtures at the bottom of this file: ``runs_root``,
``frozen_clock``, ``run_dir`` and the ``make_run_record`` factory. A run's
identity is a UTC timestamp, so without a controllable clock no test can
assert a directory name; the clock is injected by monkeypatching the
``utcnow`` module attribute, never :class:`datetime.datetime` itself.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from auto_ext.core.config import ProjectConfig
    from auto_ext.model.run import RunRecord, StageRecord


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
tech_name: HN001
paths:
  calibre_lvs_dir: $calibre_source_added_place|parent
  qrc_deck_dir: $VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CFXXX/QCI_deck
layer_map: {wa_posix}/fake/layers.map
env_overrides:
  WORK_ROOT: {wa_posix}
  WORK_ROOT2: {wa_posix}
  VERIFY_ROOT: {wa_posix}/fake/verify
  SETUP_ROOT: {wa_posix}/fake/setup
  PDK_LAYER_MAP_FILE: {wa_posix}/fake/layers.map
  calibre_source_added_place: {wa_posix}/fake/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX/empty.cdl
extraction_output_dir: "${{WORK_ROOT}}/cds/verify/QCI_PATH_{{cell}}"
intermediate_dir: "${{WORK_ROOT2}}"
dspf_out_path: "${{WORK_ROOT2}}/{{cell}}.dspf"
templates:
  si: {(templates_root / 'si' / 'default.env.j2').as_posix()}
  calibre: {(templates_root / 'calibre' / 'calibre_lvs.qci.j2').as_posix()}
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


# ---- Run fixtures (S1) -------------------------------------------------------

#: Default instant for :class:`FrozenClock`. Arbitrary but fixed, and chosen
#: to render as ``20260821T143205Z`` so expected directory names can be
#: written out literally in tests.
FROZEN_START = datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc)


class FrozenClock:
    """A controllable stand-in for ``auto_ext.model.run.utcnow``.

    Callable, so it can be dropped straight over the ``utcnow`` module
    attribute. Time only moves when a test moves it::

        run_a = allocate_run_dir(runs_root, "amp2-ext")
        frozen_clock.tick(1)                 # advance one second
        run_b = allocate_run_dir(runs_root, "amp2-ext")
    """

    def __init__(self, start: datetime = FROZEN_START) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def now(self) -> datetime:
        """Current frozen instant (UTC-aware)."""

        return self._now

    def tick(self, seconds: float = 1.0) -> datetime:
        """Advance the clock and return the new instant."""

        self._now = self._now + timedelta(seconds=seconds)
        return self._now

    def set(self, moment: datetime) -> datetime:
        """Jump the clock to ``moment`` and return it."""

        self._now = moment
        return self._now

    def stamp(self) -> str:
        """The current instant formatted the way a run directory names it."""

        from auto_ext.model.run import RUN_TIMESTAMP_FORMAT

        return self._now.strftime(RUN_TIMESTAMP_FORMAT)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> FrozenClock:
    """Freeze the run layer's clock at :data:`FROZEN_START`.

    Patches the ``utcnow`` attribute of every module that reads it, so both
    ``allocate_run_dir`` and ``annotations.updated_at`` follow the same
    controllable time.
    """

    clock = FrozenClock()
    monkeypatch.setattr("auto_ext.model.run.utcnow", clock)
    monkeypatch.setattr("auto_ext.core.run_store.utcnow", clock, raising=False)
    return clock


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    """An empty ``runs/`` root, the parent of every run directory."""

    root = tmp_path / "runs"
    root.mkdir()
    return root


@pytest.fixture
def run_dir(runs_root: Path, frozen_clock: FrozenClock) -> Path:
    """One allocated run directory (with ``rendered/``, ``logs/``, ``results/``).

    Named ``<FROZEN_START stamp>_amp2-ext``; the clock is not advanced, so a
    test that allocates another run without ticking exercises the same-second
    collision path.
    """

    from auto_ext.model.run import allocate_run_dir

    return allocate_run_dir(runs_root, "amp2-ext")


@pytest.fixture
def make_run_record(frozen_clock: FrozenClock) -> Any:
    """Factory for a valid :class:`~auto_ext.model.run.RunRecord`.

    Every argument is optional; unnamed extras are forwarded to the model, so
    a test can set anything the contract exposes::

        rec = make_run_record(cell="amp2", overall=TaskStatus.FAILED)
        rec = make_run_record(run_dir=some_dir, stages=[stage], results=res)

    ``run_id`` / ``slug`` are derived from ``run_dir`` when given, otherwise
    from the frozen clock and the DUT + recipe, so the record always satisfies
    the ``<timestamp>_<slug>`` identity rule.
    """

    from auto_ext.model.run import (
        RUN_TIMESTAMP_FORMAT,
        DutSnapshot,
        RecipeSnapshot,
        RunRecord,
        TaskStatus,
        make_run_slug,
        parse_run_id,
    )

    def _make(
        *,
        library: str = "WB_PLL_DCO",
        cell: str = "amp2",
        layout_view: str = "layout",
        source_view: str = "schematic",
        ground_net: str = "vss",
        out_file: str | None = "av_extracted",
        recipe_id: str = "ext",
        recipe_name: str | None = None,
        recipe: Any = None,
        dut: Any = None,
        created_at: datetime | None = None,
        run_dir: Path | None = None,
        run_id: str | None = None,
        slug: str | None = None,
        overall: Any = TaskStatus.PASSED,
        stages: "list[StageRecord] | None" = None,
        workspace_dir: str | None = None,
        **fields: Any,
    ) -> "RunRecord":
        dut_obj = dut or DutSnapshot(
            library=library,
            cell=cell,
            layout_view=layout_view,
            source_view=source_view,
            ground_net=ground_net,
            out_file=out_file,
        )
        recipe_obj = recipe or RecipeSnapshot(recipe_id=recipe_id, name=recipe_name)
        created = created_at or frozen_clock.now()

        if run_dir is not None:
            run_id = Path(run_dir).name
            fields.setdefault("run_dir", str(run_dir))
        if run_id is None:
            stamp = created.astimezone(timezone.utc).strftime(RUN_TIMESTAMP_FORMAT)
            run_id = f"{stamp}_{slug or make_run_slug(dut_obj, recipe_obj)}"
        _, derived_slug = parse_run_id(run_id)

        return RunRecord(
            run_id=run_id,
            slug=derived_slug,
            created_at=created,
            overall=overall,
            dut=dut_obj,
            recipe=recipe_obj,
            stages=list(stages or []),
            workspace_dir=workspace_dir or f"/work/cds/verify/QCI_PATH_{dut_obj.cell}",
            **fields,
        )

    return _make
