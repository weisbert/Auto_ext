"""Placeholder for the Project editor (arrives in Phase 5.3)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class ProjectTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(
            "Project editor — coming in Phase 5.3.\n\n"
            "This tab will bind ProjectConfig.raw (ruamel CommentedMap) to\n"
            "form fields + env-var resolution panel.",
            self,
        )
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #888; font-size: 14px;")
        layout.addWidget(label)
