"""Jivaro parasitic reduction via XML configuration.

Invocation: ``jivaro -xml <rendered.xml>`` with cwd = workarea.

Runner enforces that ``task.out_file`` is set whenever ``task.jivaro.enabled``
is True — the jivaro template's ``<inputView value="library/cell/out_file"/>``
would otherwise render to ``library/cell/None``.

Like Quantus' extracted view, Jivaro's output is a DFII cellview and not a
file: ``<outputView value="av_extracted_red"/>`` names a view inside the same
library/cell the ``<inputView>`` points at. It is recorded as a
:class:`~auto_ext.tools.base.CellViewRef`, never as a path.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from auto_ext.tools.base import CellViewRef, Tool, ToolResult, argv_path

logger = logging.getLogger(__name__)


def _view_value(root: ElementTree.Element, tag: str) -> str | None:
    element = root.find(f".//{tag}")
    if element is None:
        return None
    value = element.get("value")
    return value.strip() if value else None


def jivaro_output_view(config: Path) -> CellViewRef | None:
    """The reduced cellview a rendered Jivaro XML config will write.

    ``<inputView value="lib/cell/view"/>`` supplies the library and cell;
    ``<outputView value="view"/>`` supplies the view name. An ``outputView``
    that already carries its own ``lib/cell/view`` triple is taken as-is.

    Returns ``None`` when the file is unreadable, is not well-formed XML, or
    does not carry both elements in a usable shape -- an incomplete triple
    is not worth guessing at.
    """
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("jivaro: cannot read config %s: %s", config, exc)
        return None

    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        logger.warning("jivaro: config %s is not well-formed XML: %s", config, exc)
        return None

    output_view = _view_value(root, "outputView")
    if not output_view:
        return None

    parts = output_view.split("/")
    if len(parts) == 3:
        library, cell, view = parts
        return CellViewRef(library=library, cell=cell, view=view)

    input_view = _view_value(root, "inputView")
    if not input_view:
        return None
    input_parts = input_view.split("/")
    if len(input_parts) != 3:
        logger.warning(
            "jivaro: inputView %r in %s is not 'library/cell/view'; "
            "cannot name the reduced cellview",
            input_view,
            config,
        )
        return None

    return CellViewRef(library=input_parts[0], cell=input_parts[1], view=output_view)


class JivaroTool(Tool):
    name = "jivaro"
    executable = "jivaro"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        return [self.executable, "-xml", str(input_path)]

    def parse_result(self, result: ToolResult) -> ToolResult:
        """Record the reduced cellview named by the rendered XML config."""

        config = argv_path(result, "-xml")
        if config is None or not config.is_file():
            return result

        view = jivaro_output_view(config)
        if view is None:
            return result

        diagnostics = {**result.diagnostics, "jivaro_output_view": view.key}
        return replace(result, diagnostics=diagnostics).with_artifacts(
            cellviews=[view]
        )
