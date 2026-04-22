"""Jivaro parasitic reduction via XML configuration.

Invocation pattern (Phase 3):
    jivaro -xml <rendered.xml>
"""

from __future__ import annotations

from auto_ext.tools.base import Tool


class JivaroTool(Tool):
    name = "jivaro"
    executable = "jivaro"

    def render_template(self, template_path, context, env, out_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def run(self, input_path, cwd, env, log_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def parse_result(self, result):  # type: ignore[override]
        raise NotImplementedError("Phase 3")
