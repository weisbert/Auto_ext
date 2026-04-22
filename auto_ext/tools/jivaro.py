"""Jivaro parasitic reduction via XML configuration.

Invocation: ``jivaro -xml <rendered.xml>`` with cwd = workarea.

Runner enforces that ``task.out_file`` is set whenever ``task.jivaro.enabled``
is True — the jivaro template's ``<inputView value="library/cell/out_file"/>``
would otherwise render to ``library/cell/None``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_ext.tools.base import Tool


class JivaroTool(Tool):
    name = "jivaro"
    executable = "jivaro"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        return [self.executable, "-xml", str(input_path)]
