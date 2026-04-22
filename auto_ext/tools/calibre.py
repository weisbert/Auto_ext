"""Calibre LVS via QCI runset (``.qci``).

Invocation: ``calibre -gui -lvs -runset <rendered.qci> -batch`` with cwd = workarea.

``parse_result`` parses the rendered ``.qci`` for ``*lvsRunDir`` and
``*lvsReportFile`` to locate the LVS report, then applies the strict
check from :mod:`auto_ext.core.checks`. The tool's overall ``success``
requires both a zero exit code AND a clean LVS report.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from auto_ext.core.errors import AutoExtError
from auto_ext.tools.base import Tool, ToolResult

_RE_QCI_FIELD = re.compile(r"^\*(\w+):\s*(.+?)\s*$", re.MULTILINE)


def _qci_field(text: str, name: str) -> str | None:
    for m in _RE_QCI_FIELD.finditer(text):
        if m.group(1) == name:
            return m.group(2).strip()
    return None


class CalibreTool(Tool):
    name = "calibre"
    executable = "calibre"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        return [self.executable, "-gui", "-lvs", "-runset", str(input_path), "-batch"]

    def parse_result(self, result: ToolResult) -> ToolResult:
        from auto_ext.core.checks import parse_lvs_report_detailed

        argv = result.diagnostics.get("argv", [])
        try:
            runset = Path(argv[argv.index("-runset") + 1])
        except (ValueError, IndexError):
            return result

        if not runset.is_file():
            return result

        text = runset.read_text(encoding="utf-8", errors="replace")
        run_dir = _qci_field(text, "lvsRunDir")
        report_name = _qci_field(text, "lvsReportFile")
        if not run_dir or not report_name:
            return result

        report_path = Path(run_dir) / report_name
        if not report_path.is_file():
            return ToolResult(
                success=False,
                stdout_path=result.stdout_path,
                artifact_paths=list(result.artifact_paths),
                diagnostics={
                    **result.diagnostics,
                    "lvs_report_missing": str(report_path),
                },
            )

        try:
            lvs = parse_lvs_report_detailed(report_path)
        except AutoExtError as exc:
            return ToolResult(
                success=False,
                stdout_path=result.stdout_path,
                artifact_paths=list(result.artifact_paths),
                diagnostics={**result.diagnostics, "lvs_parse_error": str(exc)},
            )

        overall = result.success and lvs.passed
        return ToolResult(
            success=overall,
            stdout_path=result.stdout_path,
            artifact_paths=[*result.artifact_paths, report_path],
            diagnostics={**result.diagnostics, "lvs_report": lvs},
        )
