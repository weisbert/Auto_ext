"""Quantus (QRC) parasitic extraction via Tcl-style command file.

Invocation pattern (Phase 3):
    qrc -cmd <rendered.cmd>
"""

from __future__ import annotations

from auto_ext.tools.base import Tool


class QuantusTool(Tool):
    name = "quantus"
    executable = "qrc"

    def render_template(self, template_path, context, env, out_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def run(self, input_path, cwd, env, log_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def parse_result(self, result):  # type: ignore[override]
        raise NotImplementedError("Phase 3")
