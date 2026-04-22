"""Tests for :mod:`auto_ext.core.workdir`."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from auto_ext.core.errors import WorkdirError
from auto_ext.core.workdir import (
    cleanup_serial_workdir,
    prepare_parallel_workdir,
    prepare_serial_workdir,
    serial_workdir,
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


@symlink_required
def test_prepare_parallel_creates_task_dir(
    workarea: Path, auto_ext_root: Path
) -> None:
    task_dir = prepare_parallel_workdir(auto_ext_root, workarea, 7)
    assert task_dir == auto_ext_root / "runs" / "task_7"
    assert task_dir.is_dir()


@symlink_required
def test_prepare_parallel_symlinks_cds_lib(
    workarea: Path, auto_ext_root: Path
) -> None:
    task_dir = prepare_parallel_workdir(auto_ext_root, workarea, "demo")
    link = task_dir / "cds.lib"
    assert link.is_symlink()
    # Symlink target must be absolute so the task dir is relocatable.
    target = Path(os.readlink(link))
    assert target.is_absolute()
    assert target.resolve() == (workarea / "cds.lib").resolve()


@symlink_required
def test_prepare_parallel_symlinks_cdsinit(
    workarea: Path, auto_ext_root: Path
) -> None:
    task_dir = prepare_parallel_workdir(auto_ext_root, workarea, "demo")
    link = task_dir / ".cdsinit"
    assert link.is_symlink()
    assert Path(os.readlink(link)).resolve() == (workarea / ".cdsinit").resolve()


@symlink_required
def test_prepare_parallel_sanitizes_task_id(
    workarea: Path, auto_ext_root: Path
) -> None:
    # Non-safe characters collapse to underscores; no nested dirs.
    task_dir = prepare_parallel_workdir(auto_ext_root, workarea, "foo/bar baz")
    assert task_dir.name == "task_foo_bar_baz"
    assert task_dir.parent == auto_ext_root / "runs"


@symlink_required
def test_prepare_parallel_accepts_int_task_id(
    workarea: Path, auto_ext_root: Path
) -> None:
    task_dir = prepare_parallel_workdir(auto_ext_root, workarea, 42)
    assert task_dir.name == "task_42"


@symlink_required
def test_prepare_parallel_reuses_stale_dir(
    workarea: Path, auto_ext_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    stale = auto_ext_root / "runs" / "task_1"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("from previous run", encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="auto_ext.core.workdir")
    task_dir = prepare_parallel_workdir(auto_ext_root, workarea, 1)

    assert task_dir == stale
    assert not (task_dir / "leftover.txt").exists()
    assert any("stale" in m.lower() for m in caplog.messages)


def test_prepare_parallel_missing_cds_lib(
    auto_ext_root: Path, tmp_path: Path
) -> None:
    # workarea exists but is missing cds.lib.
    broken = tmp_path / "broken_workarea"
    broken.mkdir()
    (broken / ".cdsinit").write_text("x", encoding="utf-8")
    # No cds.lib.

    with pytest.raises(WorkdirError, match="cds.lib"):
        prepare_parallel_workdir(auto_ext_root, broken, 1)

    # And the task_dir must not be left lying around.
    assert not (auto_ext_root / "runs" / "task_1").exists()


def test_prepare_parallel_symlink_denied_raises_workdir_error(
    workarea: Path, auto_ext_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate Windows winerror 1314 on os.symlink."""

    def _denied(*args: object, **kwargs: object) -> None:
        err = OSError("A required privilege is not held by the client")
        err.winerror = 1314  # type: ignore[attr-defined]
        raise err

    monkeypatch.setattr(os, "symlink", _denied)

    with pytest.raises(WorkdirError, match="Developer Mode"):
        prepare_parallel_workdir(auto_ext_root, workarea, 1)

    # Task dir must be cleaned up on failure.
    assert not (auto_ext_root / "runs" / "task_1").exists()
