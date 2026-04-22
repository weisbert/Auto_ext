"""GDS export via ``strmout``.

No ``.j2`` template in most cases -- GUI/CLI assembles the argv directly
from task fields. The Tool ABC is still a convenient fit because the
render step becomes a no-op (just stash the generated argv for the log).

Implementation lands in Phase 3.
"""

from __future__ import annotations

from auto_ext.tools.base import Tool


class StrmoutTool(Tool):
    name = "strmout"
    executable = "strmout"

    def render_template(self, template_path, context, env, out_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def run(self, input_path, cwd, env, log_path):  # type: ignore[override]
        raise NotImplementedError("Phase 3")

    def parse_result(self, result):  # type: ignore[override]
        raise NotImplementedError("Phase 3")
