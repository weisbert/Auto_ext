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
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from auto_ext.core.handoff import detached_popen_kwargs

__all__ = ["open_containing_folder", "open_in_os"]

#: Launcher argv prefixes tried in order on Linux/BSD. ``xdg-open`` is the
#: freedesktop standard and is what a desktop session provides; ``gio open``
#: is the GLib equivalent, present on a bare RHEL/CentOS box that never had
#: ``xdg-utils`` installed but does have GNOME libraries. The next launcher is
#: tried only when the previous *binary* is missing -- a launcher that starts
#: and then fails to find a handler exits after we have stopped watching.
_POSIX_LAUNCHERS: tuple[tuple[str, ...], ...] = (("xdg-open",), ("gio", "open"))

#: macOS ships exactly one, and it is always present.
_MACOS_LAUNCHERS: tuple[tuple[str, ...], ...] = (("open",),)


def open_in_os(path: Path) -> None:
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

    Raises:
        FileNotFoundError: ``path`` does not exist (caller decides how to
            surface this to the user -- usually a QMessageBox in the GUI).
        OSError: no launcher binary is available (``xdg-open`` and ``gio``
            both missing on a headless server), or the OS handler refused
            to start. The message includes the offending path so the user
            can copy-paste it.
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
    for launcher in launchers:
        tried.append(launcher[0])
        try:
            subprocess.Popen([*launcher, str(path)], **detached_popen_kwargs())
        except FileNotFoundError:
            # The launcher binary itself is missing -- distinct from the
            # path-not-found case above, and the reason to try the next one.
            continue
        except OSError as exc:
            raise OSError(f"failed to open {path}: {exc}") from exc
        else:
            return

    # Exhausted: re-raise as OSError (never FileNotFoundError) so callers can
    # tell "the file you wanted is gone" from "your system cannot open files
    # like this".
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
