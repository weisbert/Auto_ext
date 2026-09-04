"""Cross-platform "open with OS default handler" helper.

Used by the Cells screen's log bar and by the Runs screen's result card to
launch whatever the user has wired up as the default viewer for a file
type: the rendered template, the Calibre LVS report, a stage log, or an
extracted DSPF netlist. All four are plain files, so one entry point
covers them; :func:`open_containing_folder` covers the "and where does
this live?" follow-up question.

No Qt import, so the dispatch logic is unit-testable in a headless
environment. The one non-stdlib import is
:func:`auto_ext.core.handoff.detached_popen_kwargs`, so that the way a
child process is detached is decided in exactly one place for the whole
codebase.

Three properties matter here, and all three are easy to get wrong:

- **The viewer must not be able to block the GUI.** A launcher that
  inherits the GUI's stdout pipe and writes more than a pipe buffer's
  worth blocks forever, with nobody reading. Every stream goes to the
  null device.
- **The viewer must not die with the GUI.** ``xdg-open`` typically execs
  a real application (a PDF viewer, a text editor); if it stays in
  Auto_ext's session it takes a SIGHUP when the owning terminal or ssh
  session goes away. It gets its own session instead.
- **"The file is gone" and "your system cannot open files like this" are
  different problems.** They map to :class:`FileNotFoundError` and
  :class:`OSError` respectively, so the GUI can say something specific.
- **A launcher that starts and then fails must not pass for success.**
  ``xdg-open`` returns 3 for "no application knows how to open this" and 4 for
  "the action failed", and on the deployment target -- RHEL over X11
  forwarding, no desktop session guaranteed -- that is the normal outcome for
  a ``.report`` file. Fire-and-forget turned it into nothing at all happening:
  no window, no error, nothing. The launcher is now given
  :data:`LAUNCH_SETTLE_S` to fail, and the next one is tried when it does.

:func:`can_open` answers the question before the click, so a caller can offer
something else -- the in-app log view -- instead of a control that cannot work.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from auto_ext.core.handoff import detached_popen_kwargs

__all__ = [
    "LAUNCH_SETTLE_S",
    "can_open",
    "file_opener",
    "launcher_names",
    "open_containing_folder",
    "open_in_os",
]

#: Launcher argv prefixes tried in order on Linux/BSD. ``xdg-open`` is the
#: freedesktop standard and is what a desktop session provides; ``gio open``
#: is the GLib equivalent, present on a bare RHEL/CentOS box that never had
#: ``xdg-utils`` installed but does have GNOME libraries. The next launcher is
#: tried when the previous *binary* is missing, and now also when it exits
#: non-zero inside :data:`LAUNCH_SETTLE_S`.
_POSIX_LAUNCHERS: tuple[tuple[str, ...], ...] = (("xdg-open",), ("gio", "open"))

#: macOS ships exactly one, and it is always present.
_MACOS_LAUNCHERS: tuple[tuple[str, ...], ...] = (("open",),)

#: How long a launcher is watched before it is believed.
#:
#: A launcher that hands the file to a viewer and exits does so in
#: milliseconds, so a failure ("no application knows how to open this") is
#: observed almost immediately. A launcher that *is* the viewer's parent --
#: ``xdg-open``'s generic fallback runs the application in the foreground --
#: never exits, and that is a success. So: wait a little, and treat "still
#: running" as "it worked".
#:
#: This is the one place the GUI thread can block, which is why it is short.
LAUNCH_SETTLE_S = 0.4


def launcher_names(platform: str | None = None) -> tuple[str, ...]:
    """The launcher binaries this platform would try, in order.

    Empty on Windows: ``ShellExecuteW`` is part of the OS, so there is nothing
    to look for on ``PATH``. Mirrored by
    :data:`auto_ext.core.health.FILE_OPENERS`, which cannot import this module
    -- ``core`` never imports ``ui`` -- and by ``deploy/doctor.sh``; the tests
    assert the three stay in sync.
    """

    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return ()
    launchers = _MACOS_LAUNCHERS if platform == "darwin" else _POSIX_LAUNCHERS
    return tuple(launcher[0] for launcher in launchers)


def file_opener(platform: str | None = None) -> str | None:
    """The launcher this host would use, resolved on ``PATH``, or ``None``.

    ``""`` on Windows, where the answer is "the shell, always" and there is no
    path to report. ``None`` means this host cannot open a file at all.
    """

    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return ""
    for name in launcher_names(platform):
        found = shutil.which(name)
        if found:
            return found
    return None


def can_open(platform: str | None = None) -> bool:
    """Whether this host has any way of opening a file with its own handler.

    The office server is the case this exists for: no ``xdg-open``, no
    ``gio``, and every "Open ..." button on the results card silently doing
    nothing. A caller that asks first can offer the in-app viewer instead of a
    control that cannot work.
    """

    return file_opener(platform) is not None


def _settle(process: "subprocess.Popen[bytes]", settle_s: float) -> int | None:
    """The launcher's exit status if it finished in time, else ``None``.

    ``None`` is the success case for a launcher that stays up: it is the
    viewer's parent, and waiting for it would be waiting for the user to close
    the document.
    """

    if settle_s <= 0:
        return None
    try:
        return process.wait(timeout=settle_s)
    except subprocess.TimeoutExpired:
        return None


def open_in_os(path: Path, *, settle_s: float = LAUNCH_SETTLE_S) -> None:
    """Open ``path`` with the OS default handler.

    Works for a directory as well as a file: every platform's handler
    opens a directory in the file manager, which is what
    :func:`open_containing_folder` relies on.

    A relative ``path`` is resolved against the current working directory
    before launching. The GUI's cwd is not the user's shell cwd and is not
    guaranteed to stay put, so handing a launcher a relative path is a
    latent bug; absolute paths are also what the error messages need to be
    copy-pasteable.

    Platform dispatch:

    - Windows (``sys.platform == "win32"``): :func:`os.startfile`, which
      goes through ``ShellExecuteW`` and is already fully detached.
    - macOS (``sys.platform == "darwin"``): ``open``.
    - Linux/other: :data:`_POSIX_LAUNCHERS`, in order.

    ``settle_s`` is how long the launcher is watched before it is believed;
    pass ``0`` for the old fire-and-forget behaviour, which cannot report a
    handler that refused.

    Raises:
        FileNotFoundError: ``path`` does not exist (caller decides how to
            surface this to the user -- usually a QMessageBox in the GUI).
        OSError: no launcher binary is available (``xdg-open`` and ``gio``
            both missing on a headless server), the OS handler refused to
            start, or every launcher exited non-zero -- "nothing on this box
            knows how to open a file like this". The message includes the
            offending path so the user can copy-paste it.
    """

    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))

    if sys.platform == "win32":
        # os.startfile dispatches via ShellExecuteW; raises OSError on
        # "no association" so we don't need to wrap it further.
        os.startfile(str(path))  # type: ignore[attr-defined]
        return

    launchers = _MACOS_LAUNCHERS if sys.platform == "darwin" else _POSIX_LAUNCHERS
    tried: list[str] = []
    refused: list[str] = []
    for launcher in launchers:
        tried.append(launcher[0])
        try:
            process = subprocess.Popen(
                [*launcher, str(path)], **detached_popen_kwargs()
            )
        except FileNotFoundError:
            # The launcher binary itself is missing -- distinct from the
            # path-not-found case above, and the reason to try the next one.
            continue
        except OSError as exc:
            raise OSError(f"failed to open {path}: {exc}") from exc
        status = _settle(process, settle_s)
        if status is None or status == 0:
            return
        # It started and gave up: no handler registered for this file type, no
        # session bus, no $DISPLAY. Worth trying the next launcher, and worth
        # saying out loud if that one fails too -- silence here is what made
        # "Open LVS report" look like a dead button on the server.
        refused.append(f"{launcher[0]} exited {status}")

    # Exhausted: raise OSError (never FileNotFoundError) so callers can tell
    # "the file you wanted is gone" from "your system cannot open files like
    # this".
    if refused:
        raise OSError(
            f"nothing on this host could open {path}: {'; '.join(refused)}"
        )
    raise OSError(
        f"none of {', '.join(tried)} was found on PATH; cannot open {path}"
    )


def open_containing_folder(path: Path) -> None:
    """Open the directory that holds ``path`` in the OS file manager.

    The results panel needs this for the artefacts a viewer cannot show:
    the ``svdb/`` database Calibre Interactive wants pointed at, the
    workarea, a run directory the user is about to archive.

    ``path`` may be the file itself (its parent is opened) or a directory
    (it is opened directly). The file is deliberately *not* selected /
    highlighted: doing that needs a per-platform special case
    (``explorer /select,``) whose quoting is unreliable for paths with
    spaces, and an opened folder answers the question either way.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        OSError: as :func:`open_in_os`.
    """

    if not path.is_absolute():
        path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))
    open_in_os(path if path.is_dir() else path.parent)
