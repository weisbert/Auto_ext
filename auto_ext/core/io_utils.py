"""Tiny FS helpers shared by cli + core orchestrators.

Phase 5.7 lifted ``backup_if_exists`` out of ``cli.py`` so the wizard's
``core/init_project.py`` can call it without an import cycle.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def backup_if_exists(path: Path) -> Path | None:
    """Copy ``path`` to ``<path>.bak`` if it exists; return the .bak path.

    Returns ``None`` if the source does not exist (no backup needed).
    Uses :func:`shutil.copy2` so timestamps + mode are preserved.
    """
    if not path.exists():
        return None
    bak = path.with_name(path.name + ".bak")
    shutil.copy2(path, bak)
    return bak


__all__ = ["backup_if_exists"]
