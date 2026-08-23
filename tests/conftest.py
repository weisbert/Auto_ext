"""Shared pytest fixtures for the Auto_ext test suite.

Phase 2 adds fixtures used by the ``tests/core/`` suite: a path to the
static fixtures dir, a temp workarea populated with ``cds.lib`` + ``.cdsinit``,
a temp Auto_ext root, an override dict, a clean-env helper, and a
session-scoped probe that reports whether this host can create symlinks
(Linux always can; Windows only with Developer Mode or Admin).

S1 adds the Run fixtures: ``runs_root``, ``frozen_clock``, ``run_dir`` and the
``make_run_record`` factory. A run's identity is a UTC timestamp, so without a
controllable clock no test can assert a directory name; the clock is injected
by monkeypatching the ``utcnow`` module attribute, never
:class:`datetime.datetime` itself.

Who owns which role
-------------------
``docs/refactor/04-tests-disposition.md`` section 4.1 records that
``project_tools_config`` had grown into three roles at once -- it produced a
config directory, it pinned ``WORK_ROOT`` inside the pytest sandbox, and it
bound the production ``templates/`` tree into ``project.yaml``. Those three
are separated here, so a test asks for exactly the one it needs:

============================  ==========================================
fixture                       the single thing it answers
============================  ==========================================
``workarea``                  "where do tools run" (a sandboxed cwd)
``sandbox_env``               "what do the env vars resolve to" -- and the
                              answer is always inside ``tmp_path``
``templates_root``            "where do the shipped ``.j2`` files live"
``project_tools_config``      "what does a v1 ``config/`` dir look like"
``pdk_profile`` / ``recipe``  the v2 replacements for the halves of
``cell_book`` / ``workspace``   ``project.yaml`` + ``tasks.yaml``
============================  ==========================================

The v2 four are thin wrappers over :mod:`tests.support.v2`, which is where
the builders live so a ``parametrize`` list at module scope can reach them.
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
    from auto_ext.catalog import Catalog
    from auto_ext.core.config import ProjectConfig
    from auto_ext.model.cells import CellBook
    from auto_ext.model.pdk import PdkProfile
    from auto_ext.model.recipe import Recipe
    from auto_ext.model.run import RunRecord, StageRecord
    from auto_ext.model.workspace import WorkspaceConfig


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


#: Names that exist only inside tests -- placeholders in
#: ``tests/core/test_env.py``'s hand-written expressions, and the two
#: :data:`sample_overrides` keys. They are not PDK facts and never come from a
#: profile, so they are the only names spelled out by hand here.
_TEST_LOCAL_ENV_VARS: tuple[str, ...] = (
    "EMP",
    "LIB",
    "FOO",
    "BAR",
    "BAZ",
    "UNDEFINED_X",
    "AUTO_EXT_TEST_VAR",
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Unset every env var this suite's own fixtures bind.

    The PDK half of the list is *derived* from the profile builders rather
    than written out: which variables a technology needs is a discovered
    property of that technology, so a hand-maintained global list would go
    stale the moment a second profile declared a different one (section 4.3 of
    the tests disposition). Tests then ``monkeypatch.setenv`` the specific
    shell values they need; everything is restored at teardown.
    """

    from tests.support.v2 import ENV, OTHER_ENV

    for var in sorted({*ENV, *OTHER_ENV, *_TEST_LOCAL_ENV_VARS}):
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
def sandbox_env(workarea: Path) -> dict[str, str]:
    """Env values that keep every tool output inside the pytest sandbox.

    The one role of ``project_tools_config`` worth keeping on its own: any
    fixture that hands an environment to the flow must root it at ``workarea``
    (which is under ``tmp_path``), or a test run writes into the developer's
    real filesystem. Same variable names as :data:`tests.support.v2.ENV`, real
    sandbox directories instead of the fictional ``/w``.
    """

    wa = workarea.as_posix()
    return {
        "WORK_ROOT": wa,
        "WORK_ROOT2": wa,
        "VERIFY_ROOT": f"{wa}/fake/verify",
        "SETUP_ROOT": f"{wa}/fake/setup",
        "PDK_LAYER_MAP_FILE": f"{wa}/fake/layers.map",
        "calibre_source_added_place": (
            f"{wa}/fake/runset/Calibre_QRC/LVS/Ver_LVS_A/CFXXX/empty.cdl"
        ),
    }


@pytest.fixture
def project_tools_config(
    tmp_path: Path, workarea: Path, sandbox_env: dict[str, str]
) -> Path:
    """A **reduced** ``config/`` directory: ``project.yaml`` + ``tasks.yaml``.

    What is left of the v1 pair once the retired keys are gone: no
    ``templates:``, no ``paths:``, no ``knobs:``, no per-task ``jivaro:``.
    ``load_project`` / ``load_tasks`` accept exactly this, so it is what the
    runner tests hand ``run_tasks`` -- together with a Recipe and a PdkProfile,
    which is where everything this file no longer carries now lives.

    For the *full* v1 pair (the thing ``auto-ext migrate`` reads) ask for
    ``v1_config_dir``. Splitting the two is the point: one fixture that is
    simultaneously "what the loader accepts" and "what the migration converts"
    cannot describe both once the schema has moved.

    Env values come from ``sandbox_env``, so every path the mocks write to is
    under ``tmp_path``. Returns the config dir.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    wa_posix = workarea.as_posix()
    overrides = "\n".join(f"  {k}: {v}" for k, v in sandbox_env.items())

    (config_dir / "project.yaml").write_text(
        f"""\
work_root: {wa_posix}
verify_root: {wa_posix}/fake/verify
setup_root: {wa_posix}/fake/setup
employee_id: alice
tech_name: HN001
layer_map: {wa_posix}/fake/layers.map
env_overrides:
{overrides}
extraction_output_dir: "${{WORK_ROOT}}/cds/verify/QCI_PATH_{{cell}}"
intermediate_dir: "${{WORK_ROOT2}}"
dspf_out_path: "${{WORK_ROOT2}}/{{cell}}.dspf"
""",
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        """\
- library: EXAMPLE_LIB
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
""",
        encoding="utf-8",
    )
    return config_dir


@pytest.fixture
def v1_config_dir(
    tmp_path: Path,
    workarea: Path,
    templates_root: Path,
    sandbox_env: dict[str, str],
) -> Path:
    """The **full** v1 ``config/`` pair, as ``auto-ext migrate`` finds it.

    Everything the reduced schema refuses is here on purpose: the four
    ``templates:`` slots, the ``paths:`` vocabulary, ``tech_name_env_vars``,
    a project-level ``knobs:`` block and a per-task ``jivaro:`` block. A
    migration test that started from a file with those keys already removed
    would prove nothing -- and a profile migrated from a ``project.yaml`` with
    no ``paths:`` comes out with no deck directories, which is how "the health
    report has 9 blocking checks" happens two steps later.

    Templates are bound to the archived v1 tree under
    ``examples/legacy/templates`` (bodies **and** their knob sidecars), not to
    the shipped ``templates/``, which no longer carries sidecars.
    """

    config_dir = tmp_path / "v1_config"
    config_dir.mkdir()
    wa_posix = workarea.as_posix()
    overrides = "\n".join(f"  {k}: {v}" for k, v in sandbox_env.items())
    legacy = templates_root.parent / "examples" / "legacy" / "templates"

    (config_dir / "project.yaml").write_text(
        f"""\
work_root: {wa_posix}
verify_root: {wa_posix}/fake/verify
setup_root: {wa_posix}/fake/setup
employee_id: alice
tech_name: HN001
paths:
  calibre_lvs_dir: $calibre_source_added_place|parent
  qrc_deck_dir: $VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_QRC_B/CFXXX/QCI_deck
layer_map: {wa_posix}/fake/layers.map
env_overrides:
{overrides}
extraction_output_dir: "${{WORK_ROOT}}/cds/verify/QCI_PATH_{{cell}}"
intermediate_dir: "${{WORK_ROOT2}}"
dspf_out_path: "${{WORK_ROOT2}}/{{cell}}.dspf"
templates:
  si: {(legacy / 'si' / 'default.env.j2').as_posix()}
  calibre: {(legacy / 'calibre' / 'calibre_lvs.qci.j2').as_posix()}
  quantus: {(legacy / 'quantus' / 'ext.cmd.j2').as_posix()}
  jivaro: {(legacy / 'jivaro' / 'default.xml.j2').as_posix()}
""",
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        """\
- library: EXAMPLE_LIB
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


# ---- v2 object-model fixtures ------------------------------------------------
#
# The three-way split ``project.yaml`` + ``tasks.yaml`` became: a PdkProfile
# (process facts, discovered), a Recipe (render parameters, portable) and a
# CellBook (which DUTs). ``workspace`` carries the two path patterns that are
# neither. Builders live in :mod:`tests.support.v2` so a ``parametrize`` list
# can reach them; these fixtures are the convenience wrapper.


@pytest.fixture(scope="session")
def catalog() -> "Catalog":
    """The built-in option catalog. Session-scoped: it is read-only and
    parsing ``options.yaml`` for every test costs more than the whole file."""

    from auto_ext.catalog import builtin_catalog

    return builtin_catalog()


@pytest.fixture
def pdk_profile() -> "PdkProfile":
    """A complete :class:`~auto_ext.model.pdk.PdkProfile` (HN001)."""

    from tests.support.v2 import make_profile

    return make_profile()


@pytest.fixture
def pdk_tree(tmp_path: Path) -> dict[str, Path]:
    """A real (small) PDK directory tree under ``tmp_path``.

    Anchors: ``root`` / ``lvs`` / ``qrc`` / ``setup``. Pair it with
    ``healthy_profile``, which points at exactly these directories.
    """

    from tests.support.v2 import make_pdk_tree

    return make_pdk_tree(tmp_path / "pdk")


@pytest.fixture
def healthy_profile(pdk_tree: dict[str, Path]) -> "PdkProfile":
    """A profile over ``pdk_tree`` whose every health check is green.

    Unlike ``pdk_profile``, none of its paths are env expressions, so a test
    that runs the flow does not also have to arrange a shell.
    """

    from tests.support.v2 import make_healthy_profile

    return make_healthy_profile(pdk_tree)


@pytest.fixture
def other_pdk_profile() -> "PdkProfile":
    """A second technology, for "is this Recipe actually portable" tests."""

    from tests.support.v2 import make_other_profile

    return make_other_profile()


@pytest.fixture
def recipe() -> "Recipe":
    """A :class:`~auto_ext.model.recipe.Recipe` at its schema defaults."""

    from tests.support.v2 import make_recipe

    return make_recipe()


@pytest.fixture
def cell_book() -> "CellBook":
    """A one-row :class:`~auto_ext.model.cells.CellBook`."""

    from tests.support.v2 import make_cell_book

    return make_cell_book()


@pytest.fixture
def workspace() -> "WorkspaceConfig":
    """A :class:`~auto_ext.model.workspace.WorkspaceConfig` bound to ``hn001``."""

    from tests.support.v2 import make_workspace

    return make_workspace()


@pytest.fixture
def profile_env(
    pdk_profile: "PdkProfile", monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Put the profile's env vars in the process environment, and return them.

    The counterpart of ``clean_env``: instead of a hand-written list of names,
    the set is whatever :data:`tests.support.v2.ENV` binds, which is exactly
    what ``make_profile``'s path expressions reference. A test that wants a
    var *missing* deletes it from the environment after asking for this.
    """

    from tests.support.v2 import ENV

    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return dict(ENV)


@pytest.fixture
def v2_config_dir(
    tmp_path: Path,
    healthy_profile: "PdkProfile",
    recipe: "Recipe",
    cell_book: "CellBook",
    workspace: "WorkspaceConfig",
) -> Path:
    """A written v2 tree: ``config/`` + ``recipes/``, returning the **root**.

    Layout, matching what the CLI resolves ``--auto-ext-root`` against::

        <root>/config/workspace.yaml
        <root>/config/cells.yaml
        <root>/config/profiles/<profile_id>.yaml
        <root>/recipes/<recipe_id>.yaml

    The profile is ``healthy_profile``, not ``pdk_profile``: a tree written
    for an end-to-end command has to pass the health gate, and the gate stats
    real directories. Pair it with ``mocks_on_path`` for the tool checks.

    Everything is inside ``tmp_path``. Returns the root rather than the
    ``config/`` dir because ``recipes/`` is its sibling, and a test that only
    wants the config dir writes ``root / "config"``.
    """

    from auto_ext.core.profile_discover import write_profile_yaml
    from auto_ext.model.cells import CELLS_FILENAME, save_cells
    from auto_ext.model.recipe import save_recipe
    from auto_ext.model.workspace import WORKSPACE_FILENAME, save_workspace

    root = tmp_path / "v2"
    config = root / "config"
    config.mkdir(parents=True)
    bound = workspace.model_copy(update={"pdk_profile": healthy_profile.profile_id})
    save_workspace(bound, config / WORKSPACE_FILENAME)
    save_cells(cell_book, config / CELLS_FILENAME)
    write_profile_yaml(
        config / "profiles" / f"{healthy_profile.profile_id}.yaml", healthy_profile
    )
    save_recipe(recipe, root / "recipes" / f"{recipe.recipe_id}.yaml")
    return root


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
        library: str = "EXAMPLE_LIB",
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
