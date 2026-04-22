"""Schematic netlist generation via ``si -batch`` with per-task ``si.env``.

Invocation: ``si -batch -command netlist`` with cwd = workarea.

``si`` reads ``si.env`` from cwd; :mod:`auto_ext.core.workdir` places the
rendered ``si.env`` in the correct cwd (serial: copies to ``workarea/si.env``;
parallel: writes into ``runs/task_<id>/si.env``). ``input_path`` is the
rendered source of that copy — held for audit, not used in argv.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_ext.tools.base import Tool


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
