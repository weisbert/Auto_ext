"""GDS export via ``strmout``.

No ``.j2`` template: argv is assembled directly from task context.
``has_template`` is False so the runner skips the render step entirely;
``input_path`` passed to :meth:`build_argv` is a sentinel and is ignored.

Output file is named ``<cell>.calibre.db`` to match the Calibre LVS runset
template's ``*lvsLayoutPaths`` field. The content is still GDSII; Calibre
auto-detects layout format from file magic bytes rather than the suffix.

Because there is no rendered input file to read outputs back out of, the
argv *is* the declaration: ``-strmFile`` names the one artifact this stage
produces.

Export mode
-----------
``context["layout_export_path"]``, when set, redirects ``-strmFile`` to that
path instead. It exists for one purpose: handing a GDS to software outside
this flow.

It is NOT a way to relocate the LVS layout file. That file is a
producer/consumer contract -- this tool writes it, Calibre reads it back as
``*lvsLayoutPaths`` -- and the two sides are the same catalog value, so
moving one without the other silently breaks LVS. The runner therefore
refuses an export whose stage set is anything but ``strmout`` alone: an
export is a second, separate invocation producing a second, separate file,
and the LVS path is left exactly where it was.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_ext.tools.base import Tool, ToolResult, argv_path


class StrmoutTool(Tool):
    name = "strmout"
    executable = "strmout"
    has_template = False

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        library = context["library"]
        cell = context["cell"]
        layout_view = context["lvs_layout_view"]
        output_dir = context["output_dir"]
        layer_map = context["layer_map"]
        # Export mode: an explicit destination replaces the default, and
        # only ever on a strmout-only dispatch (enforced in the runner).
        export = context.get("layout_export_path")
        layout_out = (
            Path(export) if export else Path(output_dir) / f"{cell}.calibre.db"
        )
        return [
            self.executable,
            "-library", library,
            "-topCell", cell,
            "-view", layout_view,
            "-strmFile", str(layout_out),
            "-layerMap", str(layer_map),
        ]

    def parse_result(self, result: ToolResult) -> ToolResult:
        """Record the GDS stream file named by ``-strmFile``."""

        stream_file = argv_path(result, "-strmFile")
        if stream_file is None:
            return result
        return result.with_artifacts([stream_file])
