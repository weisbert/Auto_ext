"""Tool plugin ABC.

Every EDA tool (``calibre``, ``quantus``, ``jivaro``, ``si``, ``strmout``)
plugs into the runner by implementing :class:`Tool`. The runner never
imports concrete tools directly; it iterates over the registered subclasses.

Lifecycle per task stage:

1. ``render_template(...)`` materialises the tool-specific config file
   (e.g. ``.qci``, ``.cmd``, ``.xml``, ``si.env``) from the Jinja template
   under ``templates/`` using the task's render context.
2. ``run(...)`` spawns the tool subprocess with the resolved env dict and
   the correct cwd (from ``core.workdir``). Streams stdout/stderr to the
   per-task log.
3. ``parse_result(...)`` inspects stdout/logs/artifacts to return a
   structured :class:`ToolResult` (success/failure + diagnostics).

Implementations land in Phase 3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolResult:
    """Structured outcome of a single tool invocation."""

    success: bool
    stdout_path: Path | None = None
    artifact_paths: list[Path] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """Abstract base class for every EDA tool plugin.

    Subclasses declare the tool name (must match the ``tool`` field used
    in template manifests) and the default executable name. Concrete
    behaviour is filled in during Phase 3.
    """

    #: Short identifier used in templates, configs and logs (e.g. ``"calibre"``).
    name: str = ""

    #: Default command-line executable, resolvable via ``PATH`` on the server.
    executable: str = ""

    @abstractmethod
    def render_template(
        self,
        template_path: Path,
        context: dict[str, Any],
        env: dict[str, str],
        out_path: Path,
    ) -> Path:
        """Render the tool's input file and return the path written."""

    @abstractmethod
    def run(
        self,
        input_path: Path,
        cwd: Path,
        env: dict[str, str],
        log_path: Path,
    ) -> ToolResult:
        """Spawn the tool subprocess and capture output."""

    @abstractmethod
    def parse_result(self, result: ToolResult) -> ToolResult:
        """Post-process outputs (e.g. LVS report parsing) and enrich diagnostics."""
