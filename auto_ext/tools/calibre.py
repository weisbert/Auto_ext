"""Calibre LVS via QCI runset (``.qci``).

Invocation pattern (Phase 3):
    calibre -gui -lvs -runset <rendered.qci> -batch

``parse_result`` applies the strict LVS check from :mod:`auto_ext.core.checks`.
"""

from __future__ import annotations

from auto_ext.tools.base import Tool


class CalibreTool(Tool):
    name = "calibre"
    executable = "calibre"

    def render_template(self, template_path, context, env, out_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def run(self, input_path, cwd, env, log_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def parse_result(self, result):  # type: ignore[override]
        raise NotImplementedError("Phase 3")
