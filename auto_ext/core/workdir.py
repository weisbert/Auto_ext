"""Per-run cwd isolation, and the Cadence workspace lock.

Serial mode: the netlister ``si`` reads ``si.env`` from cwd, so we copy
the run's rendered ``si.env`` into ``workarea/`` before the run starts
and delete it after. Use :func:`serial_workdir` as a context manager to
guarantee cleanup on exception.

Parallel mode: each run gets its own cwd at ``runs/<run_id>/work/`` with
symlinks back to the shared ``cds.lib`` and ``.cdsinit``. The caller writes a
run-specific ``si.env`` into the returned dir after preparation. Parallel
cleanup is a runner policy decision (keep-on-fail is common) and is not
provided here. Because the directory now hangs off an immutable run
directory rather than off a task id, two runs of the same cell can no longer
land on the same work dir — the collision the old ``runs/task_<id>/`` layout
handled by ``rmtree``-ing whatever was there.

The Cadence workspace (``extraction_output_dir``, i.e.
``${WORK_ROOT}/cds/verify/QCI_PATH_<cell>``) is the opposite kind of
directory: shared, reusable, and rewritten by every run of that cell. Running
one cell there twice in sequence is normal and correct; running it twice *at
the same time* corrupts both. :func:`workspace_lock` is what draws that line
— an advisory lock file naming the run that holds it, so the second run gets
a message instead of silent interleaving.

Concurrency caveat: :func:`prepare_serial_workdir` mutates
``workarea/si.env`` in place. Callers must ensure no other run writes
the same file concurrently; sequencing that is a runner-level concern.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from auto_ext.core.errors import WorkdirError

logger = logging.getLogger(__name__)

#: Advisory lock file dropped inside a Cadence workspace while a run owns it.
WORKSPACE_LOCK_NAME = ".auto_ext.lock"

#: Name of the parallel-isolation cwd inside a run directory. Mirrors
#: :attr:`auto_ext.model.run.RunPaths.work`; kept as a literal here so
#: ``workdir`` does not have to import the model layer.
WORK_DIRNAME = "work"


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
    variant, there is no shared mutation — the work dir is the cleanup
    boundary, so no explicit cleanup helper is invoked in normal flow.
    Kept symmetric with the serial helper for call-site clarity.

    ``task_dir`` is the run's ``work/`` directory; the parameter name is kept
    because it is also the subprocess cwd, which is what the caller cares
    about.
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


def prepare_parallel_workdir(run_dir: Path, workarea: Path) -> Path:
    """Create ``<run_dir>/work/`` with symlinks to the workarea's shared files.

    Symlinks ``cds.lib`` and ``.cdsinit`` from ``workarea`` into the work dir.
    The caller writes the run-specific ``si.env`` into the returned dir
    afterwards.

    ``run_dir`` is a freshly allocated run directory, so ``work/`` normally
    does not exist yet. A leftover (a crash between allocation and this call)
    is removed with a warning rather than merged into: half a symlink farm is
    worse than none.
    """

    work_dir = Path(run_dir) / WORK_DIRNAME

    if work_dir.exists():
        logger.warning("parallel workdir: removing stale %s", work_dir)
        shutil.rmtree(work_dir)

    work_dir.mkdir(parents=True, exist_ok=False)

    for name in ("cds.lib", ".cdsinit"):
        src = workarea / name
        if not src.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
            raise WorkdirError(f"workarea missing required file: {src}")
        link = work_dir / name
        try:
            os.symlink(src.resolve(), link)
        except OSError as exc:
            shutil.rmtree(work_dir, ignore_errors=True)
            # On Windows, winerror 1314 means "client does not hold the
            # required privilege" (no Admin / Developer Mode).
            if getattr(exc, "winerror", None) == 1314:
                raise WorkdirError(
                    f"symlink creation denied (need Admin / Developer Mode on Windows): {link}"
                ) from exc
            raise WorkdirError(f"failed to create symlink {link} -> {src}: {exc}") from exc
        logger.debug("parallel workdir: symlinked %s -> %s", link, src)

    return work_dir


# ---- Cadence workspace lock -------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorkspaceLockInfo:
    """Who currently holds a workspace, as recorded in the lock file."""

    run_id: str
    pid: int
    host: str

    def describe(self) -> str:
        return f"run {self.run_id} (pid {self.pid} on {self.host})"


def _this_host() -> str:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover - gethostname failing is exotic
        return "unknown"


def _process_alive(pid: int) -> bool:
    """Best-effort "is this pid still running" check.

    POSIX uses ``kill(pid, 0)``. Windows must NOT: ``os.kill`` there calls
    ``TerminateProcess`` for any signal that is not a console event, so the
    liveness probe would kill the very process it is asking about. The
    ctypes path opens the process for query only and reads its exit code
    (``STILL_ACTIVE`` == 259) instead.
    """

    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process: it exists, we just may not signal it.
        return True
    except OSError:  # pragma: no cover - defensive
        return True
    return True


def read_workspace_lock(workspace_dir: Path) -> WorkspaceLockInfo | None:
    """Return the lock holder recorded in ``workspace_dir``, or ``None``.

    ``None`` covers both "no lock file" and "lock file we cannot make sense
    of" — the caller treats an unreadable lock as stale, because a corrupt
    lock that nobody can clear would wedge the workspace forever.
    """

    path = Path(workspace_dir) / WORKSPACE_LOCK_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return WorkspaceLockInfo(
            run_id=str(data["run_id"]), pid=int(data["pid"]), host=str(data["host"])
        )
    except (KeyError, TypeError, ValueError):
        return None


def _write_lock(path: Path, run_id: str) -> None:
    payload = json.dumps(
        {"run_id": run_id, "pid": os.getpid(), "host": _this_host()},
        ensure_ascii=False,
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, (payload + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def acquire_workspace_lock(workspace_dir: Path, run_id: str) -> Path:
    """Claim ``<workspace_dir>/.auto_ext.lock`` for ``run_id`` and return it.

    ``O_CREAT | O_EXCL`` is the claim: it is atomic on NTFS and POSIX, so two
    runs racing for the same Cadence workspace cannot both win.

    A lock left behind by a process that is no longer running is stolen with
    a warning — an EDA run killed with SIGKILL must not lock its cell out
    permanently. A lock held by a live process, or by any process on another
    host (where liveness is unknowable), raises :class:`WorkdirError` naming
    the holder: silently serialising behind it would look like a hang, and
    silently proceeding would interleave two Calibre runs in one svdb.
    """

    workspace_dir = Path(workspace_dir)
    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkdirError(f"cannot create workspace {workspace_dir}: {exc}") from exc
    path = workspace_dir / WORKSPACE_LOCK_NAME

    try:
        _write_lock(path, run_id)
        return path
    except FileExistsError:
        pass
    except OSError as exc:
        raise WorkdirError(f"cannot write workspace lock {path}: {exc}") from exc

    holder = read_workspace_lock(workspace_dir)
    if holder is None:
        logger.warning("workspace lock %s is unreadable; treating it as stale", path)
    elif holder.host != _this_host():
        raise WorkdirError(
            f"workspace {workspace_dir} is locked by {holder.describe()}; "
            "this host cannot tell whether that run is still alive. Wait for it, "
            f"or delete {path} if you are sure it is dead."
        )
    elif _process_alive(holder.pid):
        raise WorkdirError(
            f"workspace {workspace_dir} is in use by {holder.describe()}. "
            "Two runs cannot share one Cadence workspace at the same time; add "
            "{run_slug} or {run_id} to project.extraction_output_dir to give "
            "each run its own."
        )
    else:
        logger.warning(
            "workspace lock %s left behind by %s (no longer running); taking it over",
            path,
            holder.describe(),
        )

    # Stale: replace it. Losing the O_EXCL race here means somebody else just
    # took the stale lock, and they win.
    try:
        path.unlink(missing_ok=True)
        _write_lock(path, run_id)
    except FileExistsError as exc:
        raise WorkdirError(
            f"workspace {workspace_dir} was claimed by another run while "
            "reclaiming a stale lock"
        ) from exc
    except OSError as exc:
        raise WorkdirError(f"cannot write workspace lock {path}: {exc}") from exc
    return path


def release_workspace_lock(workspace_dir: Path, run_id: str) -> None:
    """Drop the lock, but only if ``run_id`` still holds it.

    A lock that was stolen from us (we were declared dead, another run took
    over) is left alone: deleting it would hand the workspace to a third run
    while the second is mid-Calibre.
    """

    path = Path(workspace_dir) / WORKSPACE_LOCK_NAME
    holder = read_workspace_lock(workspace_dir)
    if holder is not None and (holder.run_id != run_id or holder.pid != os.getpid()):
        logger.warning(
            "workspace lock %s is held by %s, not by run %s; leaving it in place",
            path,
            holder.describe(),
            run_id,
        )
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - unlink of our own file
        logger.warning("cannot remove workspace lock %s: %s", path, exc)


@contextmanager
def workspace_lock(workspace_dir: Path, run_id: str) -> Iterator[Path]:
    """Context-managed :func:`acquire_workspace_lock` / :func:`release_workspace_lock`."""

    path = acquire_workspace_lock(workspace_dir, run_id)
    try:
        yield path
    finally:
        release_workspace_lock(workspace_dir, run_id)
