"""Placeholder for the Templates tab (arrives in Phase 5.5)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class TemplatesTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(
            "Templates tab — coming in Phase 5.5.\n\n"
            "This tab will list templates referenced by the loaded project and\n"
            "show PlaceholderInventory per file (env vars / jinja vars /\n"
            "hardcoded placeholders).",
            self,
        )
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #888; font-size: 14px;")
        layout.addWidget(label)
