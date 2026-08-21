"""Schematic netlist generation via ``si -batch`` with per-task ``si.env``.

Invocation: ``si -batch -command netlist`` with cwd = workarea.

``si`` reads ``si.env`` from cwd; :mod:`auto_ext.core.workdir` places the
rendered ``si.env`` in the correct cwd (serial: copies to ``workarea/si.env``;
parallel: writes into ``runs/task_<id>/si.env``). ``input_path`` is the
rendered source of that copy — held for audit, not used in argv.

Artifact recording happens in :meth:`SiTool.run` rather than in
``parse_result``: the netlist path is stated inside ``si.env``
(``simRunDir`` + ``hnlNetlistFileName``), and in serial mode
:func:`~auto_ext.core.workdir.serial_workdir` deletes ``workarea/si.env``
the moment the stage returns. ``run`` is the last place that file is still
on disk.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from auto_ext.core.progress import CancelToken
from auto_ext.tools.base import Tool, ToolResult

logger = logging.getLogger(__name__)

#: ``si.env`` is SKILL: ``key = "value"`` (or a bare value for booleans and
#: numbers). Only the quoted form matters here — both keys we read are paths.
_RE_SI_ENV_ASSIGNMENT = re.compile(
    r'^\s*(\w+)\s*=\s*"([^"]*)"\s*$', re.MULTILINE
)


def si_env_value(si_env: Path, key: str) -> str | None:
    """Read one quoted ``key = "value"`` assignment out of a rendered ``si.env``.

    Returns ``None`` when the file is unreadable or the key is absent. Last
    assignment wins, matching SKILL evaluation order.
    """
    try:
        text = si_env.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    found: str | None = None
    for m in _RE_SI_ENV_ASSIGNMENT.finditer(text):
        if m.group(1) == key:
            found = m.group(2)
    return found


def si_netlist_path(si_env: Path) -> Path | None:
    """The CDL netlist ``si`` will write, per a rendered ``si.env``.

    ``<simRunDir>/<hnlNetlistFileName>`` — the same two directives the
    bundled ``templates/si/default.env.j2`` renders, and the file Calibre
    then reads as ``*lvsSourcePath``. A relative ``simRunDir`` is anchored
    on the ``si.env``'s own directory, which is the cwd ``si`` runs in.

    Returns ``None`` when either directive is missing.
    """
    run_dir = si_env_value(si_env, "simRunDir")
    netlist_name = si_env_value(si_env, "hnlNetlistFileName")
    if not run_dir or not netlist_name:
        return None

    run_dir_path = Path(run_dir)
    if not run_dir_path.is_absolute():
        run_dir_path = si_env.parent / run_dir_path
    return run_dir_path / netlist_name


class SiTool(Tool):
    name = "si"
    executable = "si"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        # -cdslib ./cds.lib is relative to cwd; runner sets cwd=workarea and
        # serial_workdir has already placed cds.lib (via symlink or as-is)
        # there. Parallel (Phase 3.5) will do the same with per-task cwd.
        return [
            self.executable,
            "-batch",
            "-command",
            "netlist",
            "-cdslib",
            "./cds.lib",
        ]

    def run(
        self,
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
        *,
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
        # si refuses to start if `.running` is present ("Simulation is
        # already running in run directory") and does not remove it on
        # normal exit, so the file is the steady-state default rather than
        # a stale-lock anomaly. Strip it unconditionally before each run.
        # This is also why hard-cancel of si mid-run is safe to retry:
        # the next invocation clears whatever .running the terminated si
        # left behind.
        (cwd / ".running").unlink(missing_ok=True)
        result = super().run(
            argv, cwd=cwd, env=env, log_path=log_path, cancel_token=cancel_token
        )

        # Read the netlist location out of the si.env that is in cwd right
        # now; serial mode removes it as soon as this stage returns.
        si_env = cwd / "si.env"
        if not si_env.is_file():
            logger.debug("si: no si.env in %s; no netlist artifact recorded", cwd)
            return result

        netlist = si_netlist_path(si_env)
        if netlist is None:
            logger.debug(
                "si: %s declares no simRunDir/hnlNetlistFileName pair; "
                "no netlist artifact recorded",
                si_env,
            )
            return result

        return result.with_artifacts([netlist])
