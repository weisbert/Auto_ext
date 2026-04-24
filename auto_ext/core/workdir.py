"""Per-task cwd isolation for serial and parallel EDA runs.

Serial mode: the netlister ``si`` reads ``si.env`` from cwd, so we copy
the task's rendered ``si.env`` into ``workarea/`` before the task starts
and delete it after. Use :func:`serial_workdir` as a context manager to
guarantee cleanup on exception.

Parallel mode: each task gets its own cwd at
``<auto_ext_root>/runs/task_<id>/`` with symlinks back to the shared
``cds.lib`` and ``.cdsinit``. The caller writes a task-specific ``si.env``
into the returned dir after preparation. Parallel cleanup is a runner
policy decision (keep-on-fail is common) and is not provided here.

Concurrency caveat: :func:`prepare_serial_workdir` mutates
``workarea/si.env`` in place. Callers must ensure no other task writes
the same file concurrently; locking is a runner-level concern.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from auto_ext.core.errors import WorkdirError

logger = logging.getLogger(__name__)

_TASK_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def prepare_serial_workdir(workarea: Path, si_env_src: Path) -> Path:
    """Copy ``si_env_src`` to ``workarea/si.env`` and return ``workarea``.

    If ``si_env_src`` already *is* ``workarea/si.env`` (same path after
    resolution), this is a no-op. The caller must invoke
    :func:`cleanup_serial_workdir` when the task finishes, or use
    :func:`serial_workdir` as a context manager.
    """

    if not workarea.is_dir():
        raise WorkdirError(f"workarea not a directory: {workarea}")
    if not si_env_src.is_file():
        raise WorkdirError(f"si.env source missing: {si_env_src}")

    dst = workarea / "si.env"

    if dst.exists() and not dst.is_file():
        raise WorkdirError(f"{dst} exists but is not a regular file")

    if si_env_src.resolve() == dst.resolve():
        logger.debug("serial workdir: si.env source already at dst, no-op")
        return workarea

    shutil.copy2(si_env_src, dst)
    logger.debug("serial workdir: copied %s -> %s", si_env_src, dst)
    return workarea


def cleanup_serial_workdir(workarea: Path) -> None:
    """Delete ``workarea/si.env`` if present; no-op if already gone."""

    dst = workarea / "si.env"
    dst.unlink(missing_ok=True)
    logger.debug("serial workdir: cleaned up %s", dst)


@contextmanager
def serial_workdir(workarea: Path, si_env_src: Path) -> Iterator[Path]:
    """Context-managed wrapper: prepare on enter, cleanup on exit (even on error)."""

    prepare_serial_workdir(workarea, si_env_src)
    try:
        yield workarea
    finally:
        cleanup_serial_workdir(workarea)


def place_si_env_in_parallel_dir(task_dir: Path, si_env_src: Path) -> Path:
    """Copy ``si_env_src`` to ``task_dir/si.env`` and return the destination.

    Parallel sibling of :func:`prepare_serial_workdir`. Unlike the serial
    variant, there is no shared mutation — the task_dir is the cleanup
    boundary, so no explicit cleanup helper is invoked in normal flow.
    Kept symmetric with the serial helper for call-site clarity.
    """

    if not task_dir.is_dir():
        raise WorkdirError(f"parallel task_dir not a directory: {task_dir}")
    if not si_env_src.is_file():
        raise WorkdirError(f"si.env source missing: {si_env_src}")

    dst = task_dir / "si.env"
    if dst.exists() and not dst.is_file():
        raise WorkdirError(f"{dst} exists but is not a regular file")

    shutil.copy2(si_env_src, dst)
    logger.debug("parallel workdir: placed %s -> %s", si_env_src, dst)
    return dst


def prepare_parallel_workdir(
    auto_ext_root: Path,
    workarea: Path,
    task_id: str | int,
) -> Path:
    """Create ``<auto_ext_root>/runs/task_<id>/`` with symlinks to workarea files.

    Symlinks ``cds.lib`` and ``.cdsinit`` from ``workarea`` into the task
    dir. The caller writes the task-specific ``si.env`` into the returned
    dir afterwards. If the task dir already exists (e.g. leftover from a
    prior crashed run), it is removed first with a warning.

    Task id is sanitized: any character outside ``[A-Za-z0-9_.-]`` is
    replaced with ``_`` so stray separators do not create nested dirs.
    """

    safe_id = _TASK_ID_UNSAFE.sub("_", str(task_id))
    task_dir = auto_ext_root / "runs" / f"task_{safe_id}"

    if task_dir.exists():
        logger.warning("parallel workdir: removing stale %s", task_dir)
        shutil.rmtree(task_dir)

    task_dir.mkdir(parents=True, exist_ok=False)

    for name in ("cds.lib", ".cdsinit"):
        src = workarea / name
        if not src.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
            raise WorkdirError(f"workarea missing required file: {src}")
        link = task_dir / name
        try:
            os.symlink(src.resolve(), link)
        except OSError as exc:
            shutil.rmtree(task_dir, ignore_errors=True)
            # On Windows, winerror 1314 means "client does not hold the
            # required privilege" (no Admin / Developer Mode).
            if getattr(exc, "winerror", None) == 1314:
                raise WorkdirError(
                    f"symlink creation denied (need Admin / Developer Mode on Windows): {link}"
                ) from exc
            raise WorkdirError(f"failed to create symlink {link} -> {src}: {exc}") from exc
        logger.debug("parallel workdir: symlinked %s -> %s", link, src)

    return task_dir
