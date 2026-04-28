"""Cross-platform "open with OS default handler" helper.

Used by the Run tab's stage-row context menu to launch the rendered
template (and the Calibre LVS report) in whatever the user has wired up
as the default viewer for that file type.

Pure stdlib — no Qt import — so the dispatch logic is unit-testable in a
headless environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_in_os(path: Path) -> None:
    """Open ``path`` with the OS default handler.

    Platform dispatch:

    - Windows (``sys.platform == "win32"``): :func:`os.startfile`.
    - macOS (``sys.platform == "darwin"``): ``subprocess.Popen(["open", ...])``.
    - Linux/other: ``subprocess.Popen(["xdg-open", ...])``.

    Raises:
        FileNotFoundError: ``path`` does not exist (caller decides how to
            surface this to the user — usually a QMessageBox in the GUI).
        OSError: launcher binary is missing (``xdg-open`` not installed
            on a headless server, ``open`` missing — practically never
            on macOS, but kept symmetric) or the OS handler refused to
            start. Message includes the offending path so the user can
            copy-paste.
    """
    if not path.exists():
        raise FileNotFoundError(str(path))

    if sys.platform == "win32":
        # os.startfile dispatches via ShellExecuteW; raises OSError on
        # "no association" so we don't need to wrap it further.
        os.startfile(str(path))  # type: ignore[attr-defined]
        return

    launcher = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([launcher, str(path)])
    except FileNotFoundError as exc:
        # FileNotFoundError from Popen means the launcher binary itself
        # is missing — distinct from the path-not-found case above.
        # Re-raise as OSError so callers can tell "the file you wanted
        # is gone" (FileNotFoundError) from "your system cannot open
        # files like this" (OSError).
        raise OSError(
            f"{launcher} not found on PATH; cannot open {path}"
        ) from exc
    except OSError as exc:
        raise OSError(f"failed to open {path}: {exc}") from exc
