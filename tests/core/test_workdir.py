"""Tests for :mod:`auto_ext.core.workdir`."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from auto_ext.core.errors import WorkdirError
from auto_ext.core.workdir import (
    WORKSPACE_LOCK_NAME,
    acquire_workspace_lock,
    cleanup_serial_workdir,
    place_si_env_in_parallel_dir,
    prepare_parallel_workdir,
    prepare_serial_workdir,
    read_workspace_lock,
    release_workspace_lock,
    serial_workdir,
    workspace_lock,
)


def _host_can_symlink() -> bool:
    """Probe whether this host can create a symlink (computed once at import)."""
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "src"
        src.write_text("x", encoding="utf-8")
        dst = Path(d) / "dst"
        try:
            os.symlink(src, dst)
        except (OSError, NotImplementedError):
            return False
        return True


symlink_required = pytest.mark.skipif(
    not _host_can_symlink(),
    reason="symlink creation requires Admin / Developer Mode on Windows",
)


# ---- prepare_serial_workdir ------------------------------------------------


def test_prepare_serial_copies_si_env(workarea: Path, tmp_path: Path) -> None:
    src = tmp_path / "src" / "si.env"
    src.parent.mkdir()
    src.write_text("simOptions = t\n", encoding="utf-8")

    result = prepare_serial_workdir(workarea, src)

    assert result == workarea
    assert (workarea / "si.env").read_text(encoding="utf-8") == "simOptions = t\n"


def test_prepare_serial_overwrites_existing(workarea: Path, tmp_path: Path) -> None:
    (workarea / "si.env").write_text("old content\n", encoding="utf-8")
    src = tmp_path / "new_si.env"
    src.write_text("new content\n", encoding="utf-8")

    prepare_serial_workdir(workarea, src)

    assert (workarea / "si.env").read_text(encoding="utf-8") == "new content\n"


def test_prepare_serial_missing_workarea(tmp_path: Path) -> None:
    src = tmp_path / "si.env"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(WorkdirError, match="workarea"):
        prepare_serial_workdir(tmp_path / "does_not_exist", src)


def test_prepare_serial_missing_source(workarea: Path, tmp_path: Path) -> None:
    with pytest.raises(WorkdirError, match="si.env source"):
        prepare_serial_workdir(workarea, tmp_path / "missing_si.env")


def test_prepare_serial_src_equals_dst_is_noop(workarea: Path) -> None:
    # src and dst are the same file: no error, no-op.
    si_env = workarea / "si.env"
    si_env.write_text("existing\n", encoding="utf-8")
    prepare_serial_workdir(workarea, si_env)
    assert si_env.read_text(encoding="utf-8") == "existing\n"


# ---- cleanup ---------------------------------------------------------------


def test_cleanup_serial_removes_file(workarea: Path, tmp_path: Path) -> None:
    src = tmp_path / "si.env"
    src.write_text("x", encoding="utf-8")
    prepare_serial_workdir(workarea, src)
    assert (workarea / "si.env").exists()

    cleanup_serial_workdir(workarea)
    assert not (workarea / "si.env").exists()


def test_cleanup_serial_is_idempotent(workarea: Path) -> None:
    # No si.env present; cleanup must not raise.
    cleanup_serial_workdir(workarea)
    cleanup_serial_workdir(workarea)


# ---- context manager -------------------------------------------------------


def test_serial_workdir_context_cleans_on_exception(
    workarea: Path, tmp_path: Path
) -> None:
    src = tmp_path / "si.env"
    src.write_text("x", encoding="utf-8")

    with pytest.raises(RuntimeError, match="boom"):
        with serial_workdir(workarea, src) as wa:
            assert (wa / "si.env").exists()
            raise RuntimeError("boom")

    assert not (workarea / "si.env").exists()


def test_serial_workdir_context_cleans_on_success(
    workarea: Path, tmp_path: Path
) -> None:
    src = tmp_path / "si.env"
    src.write_text("x", encoding="utf-8")

    with serial_workdir(workarea, src) as wa:
        assert (wa / "si.env").exists()

    assert not (workarea / "si.env").exists()


# ---- prepare_parallel_workdir ---------------------------------------------
#
# The work dir moved from ``<auto_ext_root>/runs/task_<id>/`` to
# ``<run_dir>/work/``: it belongs to one immutable run rather than to a task
# id that every rerun reuses. The task-id sanitising tests are gone with the
# task id -- the run directory name is validated by ``validate_run_slug``
# upstream, so there is no unsafe string left to sanitise here.


@symlink_required
def test_prepare_parallel_creates_work_dir_inside_run(
    workarea: Path, run_dir: Path
) -> None:
    work_dir = prepare_parallel_workdir(run_dir, workarea)
    assert work_dir == run_dir / "work"
    assert work_dir.is_dir()


@symlink_required
def test_prepare_parallel_symlinks_cds_lib(workarea: Path, run_dir: Path) -> None:
    work_dir = prepare_parallel_workdir(run_dir, workarea)
    link = work_dir / "cds.lib"
    assert link.is_symlink()
    # Symlink target must be absolute so the work dir is relocatable.
    target = Path(os.readlink(link))
    assert target.is_absolute()
    assert target.resolve() == (workarea / "cds.lib").resolve()


@symlink_required
def test_prepare_parallel_symlinks_cdsinit(workarea: Path, run_dir: Path) -> None:
    work_dir = prepare_parallel_workdir(run_dir, workarea)
    link = work_dir / ".cdsinit"
    assert link.is_symlink()
    assert Path(os.readlink(link)).resolve() == (workarea / ".cdsinit").resolve()


@symlink_required
def test_prepare_parallel_leaves_run_siblings_alone(
    workarea: Path, run_dir: Path
) -> None:
    """``work/`` is created next to rendered/ logs/ results/, not over them."""

    (run_dir / "rendered" / "keep.txt").write_text("x", encoding="utf-8")
    prepare_parallel_workdir(run_dir, workarea)
    assert (run_dir / "rendered" / "keep.txt").is_file()
    assert (run_dir / "logs").is_dir()
    assert (run_dir / "results").is_dir()


@symlink_required
def test_prepare_parallel_clears_stale_work_dir(
    workarea: Path, run_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A crash between allocation and preparation leaves a half-built work/."""

    import logging

    stale = run_dir / "work"
    stale.mkdir()
    (stale / "leftover.txt").write_text("from a crashed run", encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="auto_ext.core.workdir")
    work_dir = prepare_parallel_workdir(run_dir, workarea)

    assert work_dir == stale
    assert not (work_dir / "leftover.txt").exists()
    assert any("stale" in m.lower() for m in caplog.messages)


def test_prepare_parallel_missing_cds_lib(run_dir: Path, tmp_path: Path) -> None:
    # workarea exists but is missing cds.lib.
    broken = tmp_path / "broken_workarea"
    broken.mkdir()
    (broken / ".cdsinit").write_text("x", encoding="utf-8")
    # No cds.lib.

    with pytest.raises(WorkdirError, match="cds.lib"):
        prepare_parallel_workdir(run_dir, broken)

    # And the work dir must not be left lying around.
    assert not (run_dir / "work").exists()


# ---- place_si_env_in_parallel_dir -----------------------------------------


def test_place_si_env_copies_into_task_dir(tmp_path: Path) -> None:
    task_dir = tmp_path / "task_1"
    task_dir.mkdir()
    src = tmp_path / "rendered" / "si.env"
    src.parent.mkdir()
    src.write_text("simOptions = parallel\n", encoding="utf-8")

    dst = place_si_env_in_parallel_dir(task_dir, src)

    assert dst == task_dir / "si.env"
    assert dst.read_text(encoding="utf-8") == "simOptions = parallel\n"


def test_place_si_env_missing_task_dir(tmp_path: Path) -> None:
    src = tmp_path / "si.env"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(WorkdirError, match="parallel task_dir"):
        place_si_env_in_parallel_dir(tmp_path / "does_not_exist", src)


def test_place_si_env_missing_source(tmp_path: Path) -> None:
    task_dir = tmp_path / "task_1"
    task_dir.mkdir()
    with pytest.raises(WorkdirError, match="si.env source"):
        place_si_env_in_parallel_dir(task_dir, tmp_path / "missing.env")


def test_prepare_parallel_symlink_denied_raises_workdir_error(
    workarea: Path, run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate Windows winerror 1314 on os.symlink."""

    def _denied(*args: object, **kwargs: object) -> None:
        err = OSError("A required privilege is not held by the client")
        err.winerror = 1314  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(os, "symlink", _denied)

    with pytest.raises(WorkdirError, match="Developer Mode"):
        prepare_parallel_workdir(run_dir, workarea)

    # Work dir must be cleaned up on failure.
    assert not (run_dir / "work").exists()


# ---- workspace lock --------------------------------------------------------
#
# The Cadence workspace is shared and reusable: running one cell there twice
# in sequence is correct. Only concurrent use is a problem, and that is what
# these tests pin down -- the preflight no longer refuses same-workspace
# tasks outright.


def test_workspace_lock_creates_the_workspace_and_the_file(tmp_path: Path) -> None:
    workspace = tmp_path / "QCI_PATH_amp2"
    assert not workspace.exists()

    path = acquire_workspace_lock(workspace, "20260821T143205Z_amp2-ext")

    assert path == workspace / WORKSPACE_LOCK_NAME
    assert path.is_file()
    holder = read_workspace_lock(workspace)
    assert holder is not None
    assert holder.run_id == "20260821T143205Z_amp2-ext"
    assert holder.pid == os.getpid()


def test_workspace_lock_released_by_its_owner(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    acquire_workspace_lock(workspace, "run-a")
    release_workspace_lock(workspace, "run-a")
    assert not (workspace / WORKSPACE_LOCK_NAME).exists()
    assert read_workspace_lock(workspace) is None


def test_workspace_lock_context_manager_releases_on_exception(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    with pytest.raises(RuntimeError):
        with workspace_lock(workspace, "run-a"):
            raise RuntimeError("stage blew up")
    assert not (workspace / WORKSPACE_LOCK_NAME).exists()


def test_workspace_lock_sequential_reuse_is_fine(tmp_path: Path) -> None:
    """Two recipes for one cell, one after the other. This is the case the
    old ``_validate_task_outputs`` used to reject outright."""

    workspace = tmp_path / "ws"
    with workspace_lock(workspace, "run-a"):
        pass
    with workspace_lock(workspace, "run-b"):
        holder = read_workspace_lock(workspace)
        assert holder is not None
        assert holder.run_id == "run-b"


def test_workspace_lock_refuses_when_held_by_a_live_process(tmp_path: Path) -> None:
    """The holder pid is this very process, so it is unmistakably alive."""

    workspace = tmp_path / "ws"
    acquire_workspace_lock(workspace, "20260821T143205Z_amp2-ext")

    with pytest.raises(WorkdirError, match="in use by run 20260821T143205Z_amp2-ext"):
        acquire_workspace_lock(workspace, "20260821T143210Z_amp2-ext")


def test_workspace_lock_refusal_names_the_escape_hatch(tmp_path: Path) -> None:
    """The message has to say how to get unstuck, or the user just deletes
    the lock file and races anyway."""

    workspace = tmp_path / "ws"
    acquire_workspace_lock(workspace, "run-a")
    with pytest.raises(WorkdirError) as excinfo:
        acquire_workspace_lock(workspace, "run-b")
    message = str(excinfo.value)
    assert "{run_slug}" in message
    assert "extraction_output_dir" in message


def test_workspace_lock_steals_a_lock_from_a_dead_process(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A run killed with SIGKILL must not lock its cell out forever."""

    import json
    import logging
    import socket

    workspace = tmp_path / "ws"
    workspace.mkdir()
    # PID 0 can never be a live user process, and _process_alive rejects it
    # without touching the OS.
    (workspace / WORKSPACE_LOCK_NAME).write_text(
        json.dumps({"run_id": "dead-run", "pid": 0, "host": socket.gethostname()}),
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING, logger="auto_ext.core.workdir")
    acquire_workspace_lock(workspace, "live-run")

    holder = read_workspace_lock(workspace)
    assert holder is not None
    assert holder.run_id == "live-run"
    assert any("dead-run" in m for m in caplog.messages)


def test_workspace_lock_refuses_a_lock_from_another_host(tmp_path: Path) -> None:
    """Liveness is unknowable across hosts, so the conservative answer is no."""

    import json

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / WORKSPACE_LOCK_NAME).write_text(
        json.dumps({"run_id": "remote-run", "pid": 4321, "host": "some-other-box"}),
        encoding="utf-8",
    )

    with pytest.raises(WorkdirError, match="some-other-box"):
        acquire_workspace_lock(workspace, "local-run")


def test_workspace_lock_treats_a_corrupt_lock_as_stale(tmp_path: Path) -> None:
    """A lock nobody can parse is a lock nobody could ever clear."""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / WORKSPACE_LOCK_NAME).write_text("{not json", encoding="utf-8")

    assert read_workspace_lock(workspace) is None
    acquire_workspace_lock(workspace, "run-a")
    holder = read_workspace_lock(workspace)
    assert holder is not None
    assert holder.run_id == "run-a"


def test_workspace_lock_release_leaves_someone_elses_lock_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """We were declared dead and another run took over; releasing must not
    hand the workspace to a third run mid-Calibre."""

    import json
    import logging
    import socket

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / WORKSPACE_LOCK_NAME).write_text(
        json.dumps(
            {"run_id": "successor", "pid": os.getpid(), "host": socket.gethostname()}
        ),
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING, logger="auto_ext.core.workdir")
    release_workspace_lock(workspace, "predecessor")

    assert (workspace / WORKSPACE_LOCK_NAME).is_file()
    assert any("successor" in m for m in caplog.messages)
