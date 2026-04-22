"""Per-task cwd isolation for serial and parallel execution.

- Serial (``parallel_max == 1``): copy per-task ``si.env`` to ``workarea/si.env``;
  subprocess ``cwd = workarea``; delete after task completes.
- Parallel (``parallel_max > 1``): per-task dir at ``Auto_ext/runs/task_<id>/``,
  symlink ``cds.lib`` + ``.cdsinit`` in, write task-specific ``si.env``,
  subprocess ``cwd = runs/task_<id>/``. calibre/qrc outputs use absolute
  paths so cwd does not affect output location.

Implementation lands in Phase 2.
"""

from __future__ import annotations

from pathlib import Path


def prepare_serial_workdir(workarea: Path, si_env_src: Path) -> Path:
    """Prepare workarea for a serial task. Phase 2."""

    raise NotImplementedError


def prepare_parallel_workdir(auto_ext_root: Path, workarea: Path, task_id: str | int) -> Path:
    """Create ``runs/task_<id>/`` with symlinks. Phase 2."""

    raise NotImplementedError
