"""Children of Auto_ext get the caller's environment, not run.sh's.

``run.sh`` points ``PYTHONPATH`` at ``<install>/_vendor``, sets
``PYTHONSAFEPATH`` and (for ``gui``/``test``) prepends PyQt5's Qt5 to
``LD_LIBRARY_PATH`` -- all for Auto_ext's own interpreter. si / calibre / qrc /
jivaro, Calibre Interactive and xdg-open inherit the environment, so without
:mod:`auto_ext.core.child_env` any Python they start would import Auto_ext's
cp311 pydantic, setuptools, typing_extensions ... ahead of its own.

Three layers here: the helper itself; every spawn site actually using it (a real
child process where that is cheap, a recording ``Popen`` where the spawn is
detached); and, when this suite is itself started through ``./run.sh test``, a
live check that a real child does not see ``_vendor``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from auto_ext.core import child_env as child_env_mod
from auto_ext.core import handoff
from auto_ext.core.child_env import (
    CALLER_PREFIX,
    RESTORED_VARS,
    child_env,
    inherited_child_env,
)
from tests.core.test_handoff import lvs_record, runset  # noqa: F401  (fixtures)

INSTALL_PP = "/inst/Auto_ext_pro:/inst/Auto_ext_pro/_vendor"


def _launched(**caller: str | None) -> dict[str, str]:
    """An environment the way run.sh leaves it.

    ``caller`` maps each of RESTORED_VARS to what the caller had (``None`` =
    unset); the live values are what run.sh changed them to.
    """

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent/home",
        "PYTHONPATH": INSTALL_PP,
        "PYTHONSAFEPATH": "1",
        "LD_LIBRARY_PATH": "/sys/PyQt5/Qt5/lib:/caller/lib",
    }
    for var in RESTORED_VARS:
        value = caller.get(var)
        env[f"{CALLER_PREFIX}{var}"] = value or ""
        env[f"{CALLER_PREFIX}{var}_SET"] = "0" if value is None else "1"
    return env


@pytest.fixture
def no_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """os.environ as on the dev box / under plain pytest: no run.sh markers.

    Needed explicitly because this suite also runs under ``./run.sh test``,
    where the markers are real.
    """

    for key in list(os.environ):
        if key.startswith(CALLER_PREFIX):
            monkeypatch.delenv(key)


@pytest.fixture
def run_sh_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """os.environ as run.sh leaves it, for a caller with no PYTHONPATH."""

    for key, value in _launched(LD_LIBRARY_PATH="/caller/lib").items():
        if key in ("PATH", "HOME"):
            continue
        monkeypatch.setenv(key, value)


# ---- the helper ---------------------------------------------------------------


def test_without_markers_the_environment_is_unchanged() -> None:
    """Windows dev venv, the test suite, a direct ``python -m auto_ext``."""

    base = {"PATH": "/usr/bin", "PYTHONPATH": "/mine", "LD_LIBRARY_PATH": "/x"}
    out = child_env(base)
    assert out == base
    assert out is not base, "a copy, so the caller can lay overrides on top"


def test_a_variable_the_caller_had_is_restored_to_its_value() -> None:
    env = child_env(_launched(PYTHONPATH="/caller/pp", LD_LIBRARY_PATH="/caller/lib"))
    assert env["PYTHONPATH"] == "/caller/pp"
    assert env["LD_LIBRARY_PATH"] == "/caller/lib"


def test_a_variable_the_caller_did_not_have_is_removed() -> None:
    env = child_env(_launched())
    for var in RESTORED_VARS:
        assert var not in env, var


def test_a_variable_set_to_empty_stays_set_to_empty() -> None:
    """Set-but-empty and unset are different things to the child."""

    env = child_env(_launched(PYTHONPATH=""))
    assert env["PYTHONPATH"] == ""


def test_the_markers_never_reach_the_child() -> None:
    env = child_env(_launched(PYTHONPATH="/caller/pp"))
    assert not [k for k in env if k.startswith(CALLER_PREFIX)]


def test_everything_else_is_passed_through() -> None:
    env = child_env(_launched())
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == "/nonexistent/home"


def test_it_is_idempotent_so_a_later_override_survives() -> None:
    """The runner lays a profile's env_overrides on top, and run_subprocess
    applies child_env again. The second pass must not undo the override."""

    once = {**child_env(_launched()), "PYTHONPATH": "/profile/override"}
    assert child_env(once) == once


def test_it_defaults_to_os_environ(run_sh_markers: None) -> None:
    env = child_env()
    assert "PYTHONPATH" not in env
    assert "PYTHONSAFEPATH" not in env
    assert env["LD_LIBRARY_PATH"] == "/caller/lib"


def test_inherited_child_env_is_none_without_markers(no_markers: None) -> None:
    """``None`` is what os_open / launch_detached passed before: plain inherit."""

    assert inherited_child_env() is None


def test_inherited_child_env_cleans_with_markers(run_sh_markers: None) -> None:
    env = inherited_child_env()
    assert env is not None and "PYTHONPATH" not in env


def test_run_sh_and_the_helper_restore_the_same_variables() -> None:
    """run.sh records exactly the variables child_env restores -- a variable
    run.sh starts changing without recording it would leak to every child."""

    text = (Path(__file__).resolve().parents[2] / "run.sh").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("for _auto_ext_var in "))
    recorded = line.split(" in ", 1)[1].split(";", 1)[0].split()
    assert tuple(recorded) == RESTORED_VARS


# ---- every spawn site --------------------------------------------------------

_PRINT_ENV = (
    "import json, os; print(json.dumps({k: os.environ.get(k) for k in "
    "('PYTHONPATH', 'PYTHONSAFEPATH', 'LD_LIBRARY_PATH', "
    "'AUTO_EXT_CALLER_PYTHONPATH_SET')}))"
)


def _child_sees(log_path: Path) -> dict[str, Any]:
    last = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.startswith("{")]
    return json.loads(last[-1])


def test_a_tool_started_by_run_subprocess_gets_the_callers_environment(
    tmp_path: Path,
) -> None:
    """A real child process: every EDA stage goes through run_subprocess."""

    from auto_ext.tools.base import run_subprocess

    env = {**os.environ, **_launched(LD_LIBRARY_PATH="/caller/lib")}
    env["PATH"] = os.environ.get("PATH", "")
    log = tmp_path / "child.log"
    rc = run_subprocess([sys.executable, "-c", _PRINT_ENV], tmp_path, env, log)
    assert rc == 0, log.read_text(encoding="utf-8")
    seen = _child_sees(log)
    assert seen["PYTHONPATH"] is None
    assert seen["PYTHONSAFEPATH"] is None
    assert seen["LD_LIBRARY_PATH"] == "/caller/lib"
    assert seen["AUTO_EXT_CALLER_PYTHONPATH_SET"] is None


def test_calibre_interactive_gets_the_callers_environment(
    lvs_record: Any,
) -> None:
    env = handoff.handoff_env(lvs_record, _launched(PYTHONPATH="/caller/pp"))
    assert env["PYTHONPATH"] == "/caller/pp"
    assert "PYTHONSAFEPATH" not in env
    assert env["WORK_ROOT"] == "/work/alice", "the recorded bindings still go on top"


def test_a_refused_handoff_plan_carries_the_callers_environment_too(
    make_run_record: Any, run_dir: Path
) -> None:
    record = make_run_record(run_dir=run_dir, stages=[])
    plan = handoff.plan_calibre_handoff(record, environ=_launched())
    assert plan.reasons, "no calibre stage: the plan is a refusal"
    assert "PYTHONPATH" not in plan.env


class _Recorder:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> object:
        self.kwargs.append(kwargs)

        class _P:
            pid = 4242

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def poll(self) -> int:
                return 0

        return _P()


def test_launch_detached_cleans_an_explicit_environment(
    workarea: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = _Recorder()
    monkeypatch.setattr(handoff.subprocess, "Popen", popen)
    handoff.launch_detached(["calibre"], workarea, _launched())
    assert "PYTHONPATH" not in popen.kwargs[0]["env"]


def test_launch_detached_cleans_the_inherited_environment(
    workarea: Path, monkeypatch: pytest.MonkeyPatch, run_sh_markers: None
) -> None:
    popen = _Recorder()
    monkeypatch.setattr(handoff.subprocess, "Popen", popen)
    handoff.launch_detached(["calibre"], workarea)
    env = popen.kwargs[0]["env"]
    assert env is not None and "PYTHONPATH" not in env
    assert env["LD_LIBRARY_PATH"] == "/caller/lib"


def test_launch_detached_still_just_inherits_without_markers(
    workarea: Path, monkeypatch: pytest.MonkeyPatch, no_markers: None
) -> None:
    popen = _Recorder()
    monkeypatch.setattr(handoff.subprocess, "Popen", popen)
    handoff.launch_detached(["calibre"], workarea)
    assert popen.kwargs[0]["env"] is None


def test_the_desktop_opener_gets_the_callers_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_sh_markers: None
) -> None:
    from auto_ext.ui import os_open

    target = tmp_path / "lvs.rep"
    target.write_text("x", encoding="utf-8")
    popen = _Recorder()
    monkeypatch.setattr(os_open.sys, "platform", "linux")
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)
    os_open.open_in_os(target)
    env = popen.kwargs[0]["env"]
    assert env is not None and "PYTHONPATH" not in env


def test_the_desktop_opener_still_just_inherits_without_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_markers: None
) -> None:
    from auto_ext.ui import os_open

    target = tmp_path / "lvs.rep"
    target.write_text("x", encoding="utf-8")
    popen = _Recorder()
    monkeypatch.setattr(os_open.sys, "platform", "linux")
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)
    os_open.open_in_os(target)
    assert popen.kwargs[0]["env"] is None


def test_no_spawn_site_bypasses_the_helper() -> None:
    """Every module that starts a process must build its env via child_env.

    A grep, deliberately blunt: a new ``Popen`` in a module that never imports
    child_env is exactly the leak this file exists to prevent.
    """

    pkg = Path(child_env_mod.__file__).resolve().parents[1]
    offenders = []
    for path in pkg.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        spawns = any(
            needle in text
            for needle in ("subprocess.Popen(", "subprocess.run(", "QProcess", "os.exec", "os.spawn")
        )
        if spawns and "child_env" not in text:
            offenders.append(str(path.relative_to(pkg)))
    assert not offenders, f"spawns a process without child_env: {offenders}"


# ---- live: this suite started through ./run.sh test ---------------------------


@pytest.mark.skipif(
    f"{CALLER_PREFIX}PYTHONPATH_SET" not in os.environ,
    reason="not started through ./run.sh -- nothing to check live",
)
def test_live_a_child_of_this_process_does_not_see_vendor(tmp_path: Path) -> None:
    """The server check: under ``./run.sh test`` this process really has
    run.sh's PYTHONPATH (with _vendor when installed). A real child started the
    way the EDA tools are must see the caller's original value instead."""

    from auto_ext.tools.base import run_subprocess

    log = tmp_path / "child.log"
    rc = run_subprocess([sys.executable, "-c", _PRINT_ENV], tmp_path, dict(os.environ), log)
    assert rc == 0, log.read_text(encoding="utf-8")
    seen = _child_sees(log)
    for var in RESTORED_VARS:
        if os.environ[f"{CALLER_PREFIX}{var}_SET"] == "1":
            assert seen[var] == os.environ[f"{CALLER_PREFIX}{var}"], var
        else:
            assert seen[var] is None, var
    assert "_vendor" not in (seen["PYTHONPATH"] or "")


def test_subprocess_module_is_still_the_one_patched_above() -> None:
    """Guard for the recorder tests: they patch ``<module>.subprocess.Popen``."""

    assert handoff.subprocess is subprocess
