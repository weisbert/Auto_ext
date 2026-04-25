"""Chip-style editor for a list of short string values.

Used by TasksTab for the four cartesian axes (``library`` / ``cell`` /
``lvs_layout_view`` / ``lvs_source_view``). Each value renders as a
``[text ×]`` button — clicking the × removes it; a trailing ``[+]``
button opens a text-input dialog for adding a new value. Duplicates and
empty strings are silently rejected so the caller never has to sanitize
the emitted list.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QPushButton,
    QWidget,
)


class TagListEdit(QWidget):
    """Editable list of unique non-empty strings.

    Emits :attr:`values_changed` whenever the internal list mutates.
    Callers should treat the emitted list as read-only; use
    :meth:`set_values` to push state back in from outside.
    """

    #: New list after every mutation. Never empty on ``+`` — empty on
    #: ``×`` only when the list was length 1 before (caller must guard
    #: against this downstream if the axis is required).
    values_changed = pyqtSignal(list)

    def __init__(
        self,
        values: list[str] | None = None,
        *,
        add_prompt: str = "Add value",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._values: list[str] = list(values or [])
        self._add_prompt = add_prompt

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self._add_btn = QPushButton("+", self)
        self._add_btn.setFixedWidth(28)
        self._add_btn.setToolTip(f"{add_prompt} ...")
        self._add_btn.clicked.connect(self._on_add)

        self._chip_buttons: list[QPushButton] = []
        self._rebuild()

    # ---- public API --------------------------------------------------

    def values(self) -> list[str]:
        return list(self._values)

    def set_values(self, values: list[str]) -> None:
        """Replace the contents silently (no ``values_changed`` emit)."""
        self._values = [v for v in values if isinstance(v, str) and v]
        # Drop duplicates preserving order.
        seen: set[str] = set()
        uniq: list[str] = []
        for v in self._values:
            if v not in seen:
                uniq.append(v)
                seen.add(v)
        self._values = uniq
        self._rebuild()

    # ---- slots -------------------------------------------------------

    def _on_add(self) -> None:
        text, ok = QInputDialog.getText(
            self, self._add_prompt, f"{self._add_prompt}:"
        )
        if not ok:
            return
        value = text.strip()
        if not value or value in self._values:
            return
        self._values.append(value)
        self._rebuild()
        self.values_changed.emit(list(self._values))

    def _on_remove(self, value: str) -> None:
        if value not in self._values:
            return
        self._values.remove(value)
        self._rebuild()
        self.values_changed.emit(list(self._values))

    # ---- rendering ---------------------------------------------------

    def _rebuild(self) -> None:
        # Remove old chip buttons (keep the add button at the tail).
        for btn in self._chip_buttons:
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._chip_buttons = []
        # Remove add btn so we can append chips before it.
        self._layout.removeWidget(self._add_btn)

        for value in self._values:
            chip = QPushButton(f"{value} ×", self)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setToolTip(f"Remove '{value}'")
            chip.clicked.connect(lambda _checked=False, v=value: self._on_remove(v))
            self._layout.addWidget(chip)
            self._chip_buttons.append(chip)

        self._layout.addWidget(self._add_btn)
