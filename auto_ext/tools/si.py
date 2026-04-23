"""Schematic netlist generation via ``si -batch`` with per-task ``si.env``.

Invocation: ``si -batch -command netlist`` with cwd = workarea.

``si`` reads ``si.env`` from cwd; :mod:`auto_ext.core.workdir` places the
rendered ``si.env`` in the correct cwd (serial: copies to ``workarea/si.env``;
parallel: writes into ``runs/task_<id>/si.env``). ``input_path`` is the
rendered source of that copy — held for audit, not used in argv.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from auto_ext.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class SiTool(Tool):
    name = "si"
    executable = "si"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        # -cdslib ./cds.lib is relative to cwd; runner sets cwd=workarea and
        # serial_workdir has already placed cds.lib (via symlink or as-is)
        # there. Parallel (Phase 3.5) will do the same with per-task cwd.
        return [
            self.executable,
            "-batch",
            "-command",
            "netlist",
            "-cdslib",
            "./cds.lib",
        ]

    def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
    ) -> ToolResult:
        # si refuses to start if `.running` is present ("Simulation is
        # already running in run directory") and does not remove it on
        # normal exit, so the file is the steady-state default rather than
        # a stale-lock anomaly. Strip it unconditionally before each run.
        (cwd / ".running").unlink(missing_ok=True)
        return super().run(argv, cwd=cwd, env=env, log_path=log_path)
