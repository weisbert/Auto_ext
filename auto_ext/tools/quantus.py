"""Quantus (QRC) parasitic extraction via Tcl-style command file.

Invocation: ``qrc -cmd <rendered.cmd>`` with cwd = workarea.

Quantus writes one of two output shapes and the rendered command file says
which (``docs/refactor/05-catalog-critique.md`` item A5):

``output_db -type dspf``
    A real file, whose path ``output_setup -file_name`` states. Recorded in
    :attr:`~auto_ext.tools.base.ToolResult.artifact_paths`.

``output_db -type extracted_view``
    A cellview inside the DFII library -- ``output_db -view_name`` names the
    view, and ``input_db -design_cell_name`` supplies the library and cell.
    There is no file. Recorded in
    :attr:`~auto_ext.tools.base.ToolResult.cellviews` as a
    :class:`~auto_ext.tools.base.CellViewRef`, because writing a fabricated
    path into the run record would be a lie the GUI then offers to open.

The command-file grammar this module parses is the one the bundled
``templates/quantus/*.cmd.j2`` produce: full-line ``#`` comments, one command
per logical line, ``\\``-continued across physical lines, options as
``-name value...`` with values optionally double-quoted and optionally
sitting on the following continuation line.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from auto_ext.tools.base import CellViewRef, Tool, ToolResult, argv_path

logger = logging.getLogger(__name__)

# One token: a double-quoted run (quotes stripped) or a bare run of
# non-space. Deliberately not shlex: the Quantus format has no escape
# character, and shlex's POSIX backslash handling would mangle the Windows
# paths that show up in tests.
_RE_TOKEN = re.compile(r'"([^"]*)"|(\S+)')

# An option is a bare (unquoted) token of the form -<letter>... . The letter
# requirement keeps a negative number like ``-1.0`` a value, not an option.
_RE_OPTION = re.compile(r"^-[A-Za-z]")

#: ``output_db -type`` value for the file-shaped output.
OUTPUT_TYPE_DSPF = "dspf"
#: ``output_db -type`` value for the DFII-cellview-shaped output.
OUTPUT_TYPE_EXTRACTED_VIEW = "extracted_view"


@dataclass(frozen=True)
class QuantusCommand:
    """One command block of a Quantus ``.cmd`` file."""

    name: str
    #: Option name (with its leading dash) -> its values, in file order.
    #: A repeated option keeps its first occurrence; the templates never
    #: repeat one, and silently taking the last would hide the mistake.
    options: dict[str, tuple[str, ...]]

    def value(self, option: str) -> str | None:
        """First value of ``option``, or ``None`` when absent or valueless."""

        values = self.options.get(option)
        return values[0] if values else None


def _tokenize(logical_line: str) -> list[tuple[str, bool]]:
    """Split one logical line into ``(text, was_quoted)`` tokens."""

    tokens: list[tuple[str, bool]] = []
    for m in _RE_TOKEN.finditer(logical_line):
        quoted = m.group(1) is not None
        tokens.append((m.group(1) if quoted else m.group(2), quoted))
    return tokens


def _logical_lines(text: str) -> list[str]:
    """Drop full-line comments and join ``\\``-continued physical lines."""

    joined: list[str] = []
    buffer = ""
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1].strip() + " "
            continue
        buffer += stripped.strip()
        if buffer.strip():
            joined.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        joined.append(buffer.strip())
    return joined


def parse_quantus_commands(text: str) -> tuple[QuantusCommand, ...]:
    """Parse a rendered Quantus command file into its command blocks."""

    commands: list[QuantusCommand] = []
    for line in _logical_lines(text):
        tokens = _tokenize(line)
        if not tokens:
            continue
        name, name_quoted = tokens[0]
        if name_quoted or _RE_OPTION.match(name):
            logger.debug("quantus: skipping line with no command name: %s", line)
            continue

        options: dict[str, tuple[str, ...]] = {}
        current: str | None = None
        values: list[str] = []
        for token, quoted in tokens[1:]:
            if not quoted and _RE_OPTION.match(token):
                if current is not None and current not in options:
                    options[current] = tuple(values)
                current = token
                values = []
            elif current is not None:
                values.append(token)
        if current is not None and current not in options:
            options[current] = tuple(values)

        commands.append(QuantusCommand(name=name, options=options))
    return tuple(commands)


@dataclass(frozen=True)
class QuantusOutputs:
    """What a rendered Quantus command file says it will produce."""

    #: ``output_db -type`` verbatim, or ``None`` when there is no output_db.
    output_type: str | None = None
    #: Set only for the ``dspf`` shape.
    dspf_path: Path | None = None
    #: Set only for the ``extracted_view`` shape, and only when the library
    #: and cell could also be determined.
    extracted_view: CellViewRef | None = None
    #: ``output_db -view_name``, kept even when the full triple could not be
    #: assembled, so a diagnostic can still name the view.
    view_name: str | None = None


def _design_cell_identity(commands: tuple[QuantusCommand, ...]) -> tuple[str, str] | None:
    """``(library, cell)`` from ``input_db -design_cell_name``, or ``None``.

    The directive holds three whitespace-separated words in the order
    ``<cell> <layout_view> <library>`` (see ``templates/quantus/ext.cmd.j2``).
    Any other arity means we cannot tell which word is which, so nothing is
    returned rather than a guess.
    """
    for command in commands:
        if command.name != "input_db":
            continue
        raw = command.value("-design_cell_name")
        if not raw:
            continue
        words = raw.split()
        if len(words) != 3:
            logger.warning(
                "quantus: -design_cell_name %r is not '<cell> <view> <library>'; "
                "cannot identify the extracted cellview",
                raw,
            )
            return None
        return words[2], words[0]
    return None


def quantus_outputs(cmd_file: Path) -> QuantusOutputs:
    """Read the declared outputs out of a rendered Quantus ``.cmd`` file.

    Returns an all-``None`` :class:`QuantusOutputs` when the file is
    unreadable or declares no ``output_db``. A relative ``-file_name`` is
    anchored on the command file's own directory.
    """
    try:
        text = cmd_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("quantus: cannot read command file %s: %s", cmd_file, exc)
        return QuantusOutputs()

    commands = parse_quantus_commands(text)
    output_db = next((c for c in commands if c.name == "output_db"), None)
    if output_db is None:
        return QuantusOutputs()

    output_type = output_db.value("-type")
    view_name = output_db.value("-view_name")

    if output_type == OUTPUT_TYPE_DSPF:
        setup = next((c for c in commands if c.name == "output_setup"), None)
        file_name = setup.value("-file_name") if setup is not None else None
        if not file_name:
            logger.warning(
                "quantus: output_db -type dspf but no output_setup -file_name in %s",
                cmd_file,
            )
            return QuantusOutputs(output_type=output_type, view_name=view_name)
        dspf = Path(file_name)
        if not dspf.is_absolute():
            dspf = cmd_file.parent / dspf
        return QuantusOutputs(
            output_type=output_type, dspf_path=dspf, view_name=view_name
        )

    if output_type == OUTPUT_TYPE_EXTRACTED_VIEW:
        identity = _design_cell_identity(commands)
        if identity is None or not view_name:
            logger.warning(
                "quantus: output_db -type extracted_view in %s but the "
                "library/cell/view triple is incomplete (view=%r)",
                cmd_file,
                view_name,
            )
            return QuantusOutputs(output_type=output_type, view_name=view_name)
        library, cell = identity
        return QuantusOutputs(
            output_type=output_type,
            extracted_view=CellViewRef(library=library, cell=cell, view=view_name),
            view_name=view_name,
        )

    return QuantusOutputs(output_type=output_type, view_name=view_name)


class QuantusTool(Tool):
    name = "quantus"
    executable = "qrc"

    def build_argv(self, input_path: Path, context: dict[str, Any]) -> list[str]:
        return [self.executable, "-cmd", str(input_path)]

    def parse_result(self, result: ToolResult) -> ToolResult:
        """Record the DSPF file or the extracted cellview, per the ``.cmd``."""

        cmd_file = argv_path(result, "-cmd")
        if cmd_file is None or not cmd_file.is_file():
            return result

        outputs = quantus_outputs(cmd_file)
        diagnostics = dict(result.diagnostics)
        if outputs.output_type:
            diagnostics["quantus_output_type"] = outputs.output_type
        if outputs.output_type == OUTPUT_TYPE_EXTRACTED_VIEW and (
            outputs.extracted_view is None
        ):
            # The view was declared but we could not name its library/cell.
            # Say so rather than dropping the fact on the floor.
            diagnostics["quantus_extracted_view_incomplete"] = outputs.view_name

        return replace(result, diagnostics=diagnostics).with_artifacts(
            [outputs.dspf_path] if outputs.dspf_path is not None else [],
            cellviews=(
                [outputs.extracted_view] if outputs.extracted_view is not None else []
            ),
        )
