"""End-to-end CLI smoke tests: ``init-project`` -> ``check-env`` -> ``run --dry-run``.

These tests fill a gap that single-stage unit tests cannot cover: the
seams between CLI subcommands. Concretely, the dspf_out_path bug class
(bugs 1+2) lived precisely at such seams — both halves passed their unit
tests but the stitched-together pipeline broke on a real config. This
file walks a fresh project from raw EDA exports through to rendered
templates on disk, asserting at every seam.

Mock policy: EDA subprocess calls are not exercised. ``--dry-run``
is used for the one place a runtime stage would otherwise spawn
``si``/``calibre``/``qrc``/``jivaro``. As a defensive net, the stub
:func:`_e2e_patch_run_subprocess` patches
:func:`auto_ext.tools.base.run_subprocess` to a recording no-op.

Self-contained fixtures: no ``conftest.py`` is touched. Raw EDA
fixtures are read from ``tests/fixtures/raw/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app


# ---- self-contained fixtures (NO conftest changes) -----------------------


@pytest.fixture
def _e2e_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def _e2e_raw_dir() -> Path:
    """Path to the shipped projectA raw EDA samples."""
    return Path(__file__).resolve().parent / "fixtures" / "raw"


@pytest.fixture
def _e2e_workarea(tmp_path: Path) -> Path:
    """Realistic workarea with the cds.lib + .cdsinit placeholders the
    EDA tools expect at cwd. Mirrors the conftest workarea fixture
    inline to keep this file self-contained.
    """
    wa = tmp_path / "workarea"
    wa.mkdir()
    (wa / "cds.lib").write_text("; mock cds.lib\n", encoding="utf-8")
    (wa / ".cdsinit").write_text("; mock .cdsinit\n", encoding="utf-8")
    return wa


@pytest.fixture
def _e2e_env(
    _e2e_workarea: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Set every env var init-project's templates reference to point
    inside ``tmp_path``. Returns the mapping for assertions.
    """
    wa = _e2e_workarea.as_posix()
    env = {
        "WORK_ROOT": wa,
        "WORK_ROOT2": wa,
        "VERIFY_ROOT": f"{wa}/verify",
        "SETUP_ROOT": f"{wa}/setup",
        "PDK_LAYER_MAP_FILE": f"{wa}/layers.map",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


@pytest.fixture
def _e2e_patch_run_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Defensive subprocess stub. ``--dry-run`` shortcircuits before
    :func:`run_subprocess` would be invoked, but stubbing protects
    against any future drift. Same pattern as
    ``tests/tools/test_tools.py::_stub_si_subprocess``.
    """
    import auto_ext.tools.base as base

    calls: list[dict[str, Any]] = []

    def _fake(argv, cwd, env, log_path, *, cancel_token=None) -> int:
        calls.append(
            {"argv": list(argv), "cwd": cwd, "log_path": log_path}
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("e2e-stub\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(base, "run_subprocess", _fake)
    return calls


def _e2e_invoke_init(
    runner: CliRunner, raw_dir: Path, out_root: Path
) -> Any:
    """Invoke ``auto-ext init-project`` against the projectA raw fixtures."""
    return runner.invoke(
        app,
        [
            "init-project",
            "--raw-calibre", str(raw_dir / "calibre_sample.qci"),
            "--raw-si", str(raw_dir / "si_sample.env"),
            "--raw-quantus", str(raw_dir / "quantus_sample.cmd"),
            "--raw-jivaro", str(raw_dir / "jivaro_sample.xml"),
            "--output-config-dir", str(out_root / "config"),
            "--output-templates-dir", str(out_root / "templates"),
        ],
    )


# ---- step 1: init-project ------------------------------------------------


def test_e2e_init_project_writes_complete_skeleton(
    _e2e_runner: CliRunner,
    _e2e_raw_dir: Path,
    tmp_path: Path,
) -> None:
    """``init-project`` emits a populated config + 4 templates, with no
    Phase-5.6.5-removed keys (``pdk_subdir`` / ``runset_versions``)
    leaking in.
    """
    result = _e2e_invoke_init(_e2e_runner, _e2e_raw_dir, tmp_path)
    assert result.exit_code == 0, result.output

    cfg = tmp_path / "config"
    assert (cfg / "project.yaml").is_file()
    assert (cfg / "tasks.yaml").is_file()

    project_yaml = (cfg / "project.yaml").read_text(encoding="utf-8")
    # Removed in Phase 5.6.5 — must NOT appear.
    assert "pdk_subdir" not in project_yaml
    assert "runset_versions" not in project_yaml
    # paths.* schema took its place.
    assert "paths:" in project_yaml
    assert "calibre_lvs_dir" in project_yaml
    assert "qrc_deck_dir" in project_yaml

    from auto_ext.core.config import load_project, load_tasks

    project = load_project(cfg / "project.yaml")
    tasks = load_tasks(cfg / "tasks.yaml", project=project)
    assert project.tech_name == "HN001"
    assert "calibre_lvs_dir" in project.paths
    assert "qrc_deck_dir" in project.paths
    # One task per (cell, library) pair (one cell in this fixture).
    assert len(tasks) == 1
    assert (tasks[0].library, tasks[0].cell) == ("INV_LIB", "INV1")


# ---- step 2: check-env ---------------------------------------------------


def test_e2e_check_env_reports_all_resolved(
    _e2e_runner: CliRunner,
    _e2e_raw_dir: Path,
    _e2e_env: dict[str, str],
    tmp_path: Path,
) -> None:
    """After init-project + the 5 env vars set, ``check-env`` exits 0
    and reports every required var on the ``shell`` source.
    """
    init = _e2e_invoke_init(_e2e_runner, _e2e_raw_dir, tmp_path)
    assert init.exit_code == 0, init.output

    result = _e2e_runner.invoke(
        app, ["check-env", "--config-dir", str(tmp_path / "config")]
    )
    assert result.exit_code == 0, result.output
    # The output is a Rich table; the var names must surface.
    for var in ("WORK_ROOT", "WORK_ROOT2", "VERIFY_ROOT"):
        assert var in result.output, f"check-env did not list {var}"


# ---- step 3: run --dry-run -----------------------------------------------


def test_e2e_dry_run_renders_templates_to_disk(
    _e2e_runner: CliRunner,
    _e2e_raw_dir: Path,
    _e2e_env: dict[str, str],
    _e2e_workarea: Path,
    _e2e_patch_run_subprocess: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    """``run --dry-run`` walks every stage with a template, writes the
    rendered output to ``rendered/``, and never spawns a subprocess.

    Asserts that no Jinja artifact (``[[X]]`` / ``${X}``) survives
    in the rendered output for the placeholders this fixture is
    expected to substitute.
    """
    init = _e2e_invoke_init(_e2e_runner, _e2e_raw_dir, tmp_path)
    assert init.exit_code == 0, init.output

    run_root = tmp_path / "run_root"
    run_result = _e2e_runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(tmp_path / "config"),
            "--dry-run",
            "--auto-ext-root", str(run_root),
            "--workarea", str(_e2e_workarea),
        ],
    )
    assert run_result.exit_code == 0, run_result.output
    assert "1/1 tasks passed" in run_result.output

    rendered = (
        run_root / "runs"
        / "task_INV_LIB__INV1__layout__schematic" / "rendered"
    )
    qci = rendered / "imported.qci"
    env_out = rendered / "imported.env"
    cmd = rendered / "imported.cmd"
    xml = rendered / "imported.xml"
    assert qci.is_file(), "calibre .qci was not rendered"
    assert env_out.is_file(), "si .env was not rendered"
    assert cmd.is_file(), "quantus .cmd was not rendered"
    assert xml.is_file(), "jivaro .xml was not rendered"

    # Identity placeholders must be substituted.
    qci_body = qci.read_text(encoding="utf-8")
    assert "INV1" in qci_body
    assert "INV_LIB" in qci_body
    # Common Jinja2 / placeholder syntaxes that must not survive substitution.
    for artifact in ("[[cell]]", "[[library]]", "[[lvs_layout_view]]"):
        assert artifact not in qci_body, (
            f"unrendered placeholder {artifact!r} leaked into qci output"
        )

    # Subprocess stub must not have been called (dry-run elides exec).
    assert _e2e_patch_run_subprocess == [], (
        f"dry-run unexpectedly invoked subprocess {len(_e2e_patch_run_subprocess)} time(s)"
    )


# ---- step 5: dspf cross-path equivalence (audit's highest-leverage check) ----


def test_e2e_dspf_path_runner_and_gui_helper_agree(
    _e2e_runner: CliRunner,
    _e2e_raw_dir: Path,
    _e2e_env: dict[str, str],
    _e2e_workarea: Path,
    tmp_path: Path,
) -> None:
    """The dspf bug class (bugs 1+2) hid in this exact equivalence:
    runner._build_context renders dspf_out_path one way, the GUI
    preview renders it another, and the two paths diverged. Both
    must now route through the shared ``resolve_dspf_path``; assert
    they produce byte-identical output for the post-init project.
    """
    init = _e2e_invoke_init(_e2e_runner, _e2e_raw_dir, tmp_path)
    assert init.exit_code == 0, init.output

    from auto_ext.core.config import load_project, load_tasks
    from auto_ext.core.env import resolve_env
    from auto_ext.core.runner import (
        _build_context,
        _build_path_token_env,
        _discover_env_vars,
        resolve_dspf_path,
    )
    from auto_ext.ui.widgets.dspf_out_path_combo import resolve_dspf_template

    project = load_project(tmp_path / "config" / "project.yaml")
    tasks = load_tasks(tmp_path / "config" / "tasks.yaml", project=project)
    task = tasks[0]

    required = _discover_env_vars(
        project, tasks, auto_ext_root=tmp_path / "config"
    )
    resolution = resolve_env(required, project.env_overrides)
    resolved_env = resolution.resolved

    # Path A: through the runner's _build_context — same code path the
    # production ``run`` command takes.
    ctx = _build_context(project, task, resolved_env)
    runner_dspf = ctx["dspf_out_path"]

    # Path B: through the GUI's resolve_dspf_template — same code path
    # the DspfOutPathCombo preview takes.
    extended_env = _build_path_token_env(resolved_env, ctx)
    gui_dspf, gui_err = resolve_dspf_template(
        project.dspf_out_path,
        extended_env,
        cell=task.cell,
        library=task.library,
        task_id=task.task_id,
    )
    assert gui_err is None, f"GUI helper reported error: {gui_err}"
    assert runner_dspf == gui_dspf, (
        f"dspf cross-path divergence:\n  runner: {runner_dspf!r}\n  gui:    {gui_dspf!r}"
    )

    # Path C: the shared core helper directly.
    core_dspf, core_err = resolve_dspf_path(
        project.dspf_out_path,
        extended_env,
        cell=task.cell,
        library=task.library,
        task_id=task.task_id,
    )
    assert core_err is None
    assert core_dspf == runner_dspf

    # Sanity: the resolved value contains the cell name and the
    # workarea root, with no surviving ``${X}`` env-ref artifact.
    assert task.cell in runner_dspf
    assert _e2e_workarea.as_posix() in runner_dspf
    assert "${" not in runner_dspf and "$WORK_ROOT" not in runner_dspf


# ---- step 6: failure mode -- missing env var -----------------------------


def test_e2e_check_env_fails_when_env_var_missing(
    _e2e_runner: CliRunner,
    _e2e_raw_dir: Path,
    _e2e_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Unset ``WORK_ROOT2`` after init; ``check-env`` must exit nonzero
    and name the missing variable in its output. This is the primary
    user-visible failure mode that init-project's setup feeds into.
    """
    init = _e2e_invoke_init(_e2e_runner, _e2e_raw_dir, tmp_path)
    assert init.exit_code == 0, init.output

    monkeypatch.delenv("WORK_ROOT2", raising=False)

    result = _e2e_runner.invoke(
        app, ["check-env", "--config-dir", str(tmp_path / "config")]
    )
    assert result.exit_code != 0, (
        "check-env should fail when WORK_ROOT2 is unset; got exit 0"
    )
    assert "WORK_ROOT2" in result.output, (
        "check-env did not name the missing var WORK_ROOT2 in its output"
    )


def test_e2e_run_dry_run_fails_when_env_var_missing(
    _e2e_runner: CliRunner,
    _e2e_raw_dir: Path,
    _e2e_env: dict[str, str],
    _e2e_workarea: Path,
    _e2e_patch_run_subprocess: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``run --dry-run`` is fail-fast on a missing required env var —
    even though dry-run skips subprocess execution, the resolution
    happens in ``_discover_env_vars`` -> ``resolve_env.require()``
    upstream.
    """
    init = _e2e_invoke_init(_e2e_runner, _e2e_raw_dir, tmp_path)
    assert init.exit_code == 0, init.output

    monkeypatch.delenv("VERIFY_ROOT", raising=False)

    run_result = _e2e_runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(tmp_path / "config"),
            "--dry-run",
            "--auto-ext-root", str(tmp_path / "run_root"),
            "--workarea", str(_e2e_workarea),
        ],
    )
    # Either the runner aborts (exit 2 from AutoExtError) or stages
    # individually fail. We accept either; the contract is "not silent
    # success".
    assert run_result.exit_code != 0, (
        "run --dry-run with missing VERIFY_ROOT unexpectedly succeeded:\n"
        + run_result.output
    )
