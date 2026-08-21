"""Calibre LVS via QCI runset (``.qci``).

Invocation: ``calibre -gui -lvs -runset <rendered.qci> -batch`` with cwd = workarea.

``parse_result`` parses the rendered ``.qci`` for the output directives it
declares -- ``*lvsRunDir`` plus ``*lvsReportFile`` / ``*lvsSpiceFile`` /
``*lvsERCDatabase`` / ``*lvsERCSummaryFile`` -- then applies the strict check
from :mod:`auto_ext.core.checks` to the report. The tool's overall ``success``
requires both a zero exit code AND a clean LVS report.

Only the LVS report is treated as *required*. The runset declares the ERC
files unconditionally while nothing in this flow ever switches ERC on (see
``docs/refactor/05-catalog-critique.md`` item B1), so those are recorded when
they happen to exist and never reported as a missing output -- otherwise every
successful LVS run would look like it had lost two files.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from auto_ext.core.errors import AutoExtError
from auto_ext.tools.base import Tool, ToolResult, argv_path

_RE_QCI_FIELD = re.compile(r"^\*(\w+):\s*(.+?)\s*$", re.MULTILINE)

#: ``.qci`` directive -> logical output name. ``lvsReportFile`` is the one
#: output this flow depends on; the rest are best-effort extras.
_OUTPUT_DIRECTIVES: dict[str, str] = {
    "lvsReportFile": "report",
    "lvsSpiceFile": "spice",
    "lvsERCDatabase": "erc_database",
    "lvsERCSummaryFile": "erc_summary",
}


def _qci_field(text: str, name: str) -> str | None:
    for m in _RE_QCI_FIELD.finditer(text):
        if m.group(1) == name:
            return m.group(2).strip()
    return None


def calibre_declared_outputs(runset: Path) -> dict[str, Path]:
    """Map logical output name -> declared path, from a rendered ``.qci``.

    Keys are drawn from :data:`_OUTPUT_DIRECTIVES`; a key is present only
    when the runset carries that directive. Nothing is checked against the
    filesystem -- these are the paths Calibre *said* it would write.

    Returns an empty mapping when ``runset`` is not a readable file or does
    not declare ``*lvsRunDir`` (without a run dir the file names have no
    anchor).

    A relative ``lvsRunDir`` inside the ``.qci`` is anchored on
    ``runset.parent`` (the rendered file's directory) -- production runsets
    always carry an absolute path here, but rendered fixtures sometimes use
    relative ones, and anchoring on the runset's dir is the only sensible
    default.
    """
    if not runset.is_file():
        return {}

    text = runset.read_text(encoding="utf-8", errors="replace")
    run_dir = _qci_field(text, "lvsRunDir")
    if not run_dir:
        return {}

    run_dir_path = Path(run_dir)
    if not run_dir_path.is_absolute():
        run_dir_path = runset.parent / run_dir_path

    outputs: dict[str, Path] = {}
    for directive, name in _OUTPUT_DIRECTIVES.items():
        value = _qci_field(text, directive)
        if value:
            outputs[name] = run_dir_path / value
    return outputs


def lvs_report_path_from_runset(runset: Path) -> Path | None:
    """Parse ``runset`` (a rendered ``.qci`` file) for the LVS report path.

    Returns the resolved path to ``lvsReportFile`` (joined with
    ``lvsRunDir``) when both directives are present and the report file
    exists on disk. Returns ``None`` for any of:

    - ``runset`` itself is missing or not a regular file,
    - the ``.qci`` lacks ``*lvsRunDir`` or ``*lvsReportFile``,
    - the resolved report file does not exist.
    """
    report_path = calibre_declared_outputs(runset).get("report")
    if report_path is None or not report_path.is_file():
        return None
    return report_path


class CalibreTool(Tool):
    name = "calibre"
    executable = "calibre"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        return [self.executable, "-gui", "-lvs", "-runset", str(input_path), "-batch"]

    def parse_result(self, result: ToolResult) -> ToolResult:
        from auto_ext.core.checks import parse_lvs_report_detailed

        runset = argv_path(result, "-runset")
        if runset is None or not runset.is_file():
            return result

        outputs = calibre_declared_outputs(runset)
        report_path = outputs.get("report")
        if report_path is None:
            # The runset declares no report at all: nothing to check and
            # nothing to record. Hand the raw result straight back.
            return result

        # ERC + spice outputs, recorded only when actually written. They go
        # through the pre-filtered path so a never-enabled ERC run does not
        # look like a tool that lost its outputs.
        extras = [
            path
            for name, path in outputs.items()
            if name != "report" and path.is_file()
        ]

        if not report_path.is_file():
            return replace(
                result,
                success=False,
                diagnostics={
                    **result.diagnostics,
                    "lvs_report_missing": str(report_path),
                },
            ).with_artifacts(extras)

        try:
            lvs = parse_lvs_report_detailed(report_path)
        except AutoExtError as exc:
            return replace(
                result,
                success=False,
                diagnostics={**result.diagnostics, "lvs_parse_error": str(exc)},
            ).with_artifacts([report_path, *extras])

        overall = result.success and lvs.passed
        return replace(
            result,
            success=overall,
            diagnostics={**result.diagnostics, "lvs_report": lvs},
        ).with_artifacts([report_path, *extras])
