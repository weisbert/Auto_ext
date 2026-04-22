"""Schematic netlist generation via ``si -batch`` with per-task ``si.env``.

Invocation pattern (Phase 3):
    si -batch -command netlist

Note: ``si`` reads ``si.env`` from cwd. :mod:`auto_ext.core.workdir` is
responsible for placing the task-specific ``si.env`` in the correct place
(serial: copy to workarea; parallel: write to ``runs/task_<id>/``).
"""

from __future__ import annotations

from auto_ext.tools.base import Tool


class SiTool(Tool):
    name = "si"
    executable = "si"

    def render_template(self, template_path, context, env, out_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def run(self, input_path, cwd, env, log_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def parse_result(self, result):  # type: ignore[override]
        raise NotImplementedError("Phase 3")
