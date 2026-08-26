"""The ``NOT ON THIS SCREEN`` search band. Artboard ``J``.

The band that matters. The office report the whole search feature answers was
"I cannot find where to rename the Quantus output view", and the useful reply
is not "no matches" -- it is *that setting is real, it is per cell, and here
is the button that opens it*.

A search that reports nothing for ``out_file`` teaches the user the setting
does not exist. That is worse than an error message, because they stop
looking.
"""

from __future__ import annotations

from collections.abc import Sequence

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QPushButton

from auto_ext.catalog import OptionSpec
from auto_ext.catalog.spec import Screen
from auto_ext.ui import theme
from auto_ext.ui.widgets.option_editor import ElidedLabel, option_label

__all__ = ["OBJ_ELSEWHERE_BAND", "ElsewhereBand"]

OBJ_ELSEWHERE_BAND = theme.OBJ_ELSEWHERE_BAND

#: Where to send someone, per owning screen. Keyed by :class:`Screen` so a new
#: owning screen is one entry rather than a branch in the widget.
_OPEN_LABEL = {
    Screen.CELLS: "Open in Cells",
    Screen.PROJECT: "Open in Project",
}


class ElsewhereBand(QFrame):
    """One line naming the matches that live on another screen, plus Open."""

    navigate_requested = pyqtSignal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_ELSEWHERE_BAND)
        self.setFrameShape(QFrame.NoFrame)

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_XS)

        self._text = ElidedLabel("", parent=self)
        self._text.setStyleSheet(
            f"font-size: {theme.FONT_SIZE_META}px; color: {theme.TEXT_SECONDARY};"
        )
        row.addWidget(self._text, 1)

        self._open = QPushButton("", self)
        self._open.setFlat(True)
        self._open.clicked.connect(self._on_open)
        row.addWidget(self._open, 0)

        self._specs: list[OptionSpec] = []
        self.set_specs([])

    def set_specs(self, specs: Sequence[OptionSpec]) -> None:
        self._specs = list(specs)
        if not self._specs:
            self.setVisible(False)
            self._text.set_full_text("")
            return
        names = ", ".join(option_label(spec) for spec in self._specs)
        self._text.set_full_text(f"NOT ON THIS SCREEN — {names}")
        self._text.setToolTip(self._text.full_text())
        target = self._specs[0].screen
        self._open.setText(_OPEN_LABEL.get(target, f"Open in {target}"))
        # Only when every match agrees on where to go. Sending the user to
        # the Cells page because the FIRST of three matches lives there would
        # be a button that is right by accident.
        one_place = len({spec.screen for spec in self._specs}) == 1
        self._open.setEnabled(one_place)
        self._open.setToolTip(
            "" if one_place else "these matches live on different screens"
        )
        self.setVisible(True)

    def specs(self) -> list[OptionSpec]:
        return list(self._specs)

    def text(self) -> str:
        return self._text.full_text()

    def open_button(self) -> QPushButton:
        return self._open

    def _on_open(self) -> None:
        if not self._specs:
            return
        first = self._specs[0]
        self.navigate_requested.emit(str(first.screen), first.key)
