"""One-shot migration from legacy ``Run_ext.txt`` Key:Value config to ``tasks.yaml``.

Implemented in Phase 4. Phase 1 ships the module so imports resolve.
"""

from __future__ import annotations

from pathlib import Path


def migrate_run_ext(source: Path, out: Path) -> None:
    """Parse a legacy ``Run_ext.txt`` and write ``tasks.yaml``.

    Not implemented in Phase 1. See plan Phase 4 for the field mapping.
    """

    raise NotImplementedError("migrate_run_ext is implemented in Phase 4")
