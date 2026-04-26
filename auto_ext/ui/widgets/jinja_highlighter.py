"""Syntax highlighter for the diff editor's preview panes.

Highlights the codebase-specific Jinja delimiters (``[% ... %]`` and
``[[ ... ]]``) so a wall of ``.qci`` or ``.tcl`` text doesn't make
toggle blocks invisible. Optionally tints background of given line
ranges so the diff editor can mark each hunk as on-side / off-side.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from PyQt5.QtCore import QRegularExpression, Qt
from PyQt5.QtGui import (
    QColor,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)


_BLOCK_RE = re.compile(r"\[%[^%]*%\]")
_VAR_RE = re.compile(r"\[\[[^\]]*\]\]")


@dataclass
class _HunkRange:
    on_lines: list[tuple[int, int]] = field(default_factory=list)
    off_lines: list[tuple[int, int]] = field(default_factory=list)


class JinjaHighlighter(QSyntaxHighlighter):
    """Colour ``[% ... %]`` blue and ``[[ ... ]]`` orange.

    ``set_hunk_ranges`` accepts two lists of ``(start_line, end_line)``
    tuples (half-open) — lines in the first list get a pale-green
    background (the on-side branch), lines in the second list get a
    pale-red background (the off-side branch). Both lists are
    end-exclusive (idiomatic Python slice ranges).
    """

    def __init__(self, document: QTextDocument | None = None) -> None:
        super().__init__(document)
        self._block_fmt = QTextCharFormat()
        self._block_fmt.setForeground(QColor("#4060c0"))
        self._block_fmt.setFontWeight(75)  # ~ Bold without importing QFont

        self._var_fmt = QTextCharFormat()
        self._var_fmt.setForeground(QColor("#c06000"))

        self._on_bg = QColor(220, 245, 220)  # pale green
        self._off_bg = QColor(248, 220, 220)  # pale red

        self._ranges = _HunkRange()

    def set_hunk_ranges(
        self,
        on_ranges: list[tuple[int, int]],
        off_ranges: list[tuple[int, int]],
    ) -> None:
        """Replace the per-line tint state and re-highlight the document."""
        self._ranges = _HunkRange(
            on_lines=list(on_ranges),
            off_lines=list(off_ranges),
        )
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 — Qt API
        # Token highlights.
        for m in _BLOCK_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._block_fmt)
        for m in _VAR_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), self._var_fmt)

        # Line-background tint.
        line = self.currentBlock().blockNumber()
        bg: QColor | None = None
        for start, end in self._ranges.on_lines:
            if start <= line < end:
                bg = self._on_bg
                break
        if bg is None:
            for start, end in self._ranges.off_lines:
                if start <= line < end:
                    bg = self._off_bg
                    break
        if bg is not None:
            length = len(text)
            fmt = QTextCharFormat()
            fmt.setBackground(bg)
            # Apply background only — do not clobber existing foreground.
            for i in range(length):
                existing = self.format(i)
                existing.setBackground(bg)
                self.setFormat(i, 1, existing)
            if length == 0:
                # Empty line: still mark with a background so user sees gap.
                fmt2 = QTextCharFormat()
                fmt2.setBackground(bg)
                self.setFormat(0, 0, fmt2)


# Re-export for tests that only need the regex constants.
BLOCK_RE = _BLOCK_RE
VAR_RE = _VAR_RE


__all__ = ["JinjaHighlighter", "BLOCK_RE", "VAR_RE"]


# Silence unused-import linters: keep QRegularExpression / Qt imports
# available for downstream subclasses without a reformatting churn.
_ = QRegularExpression, Qt
