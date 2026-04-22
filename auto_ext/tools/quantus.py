"""Quantus (QRC) parasitic extraction via Tcl-style command file.

Invocation: ``qrc -cmd <rendered.cmd>`` with cwd = workarea.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_ext.tools.base import Tool


class QuantusTool(Tool):
    name = "quantus"
    executable = "qrc"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        return [self.executable, "-cmd", str(input_path)]
