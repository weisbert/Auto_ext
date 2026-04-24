"""Placeholder for the Tasks editor (arrives in Phase 5.4)."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget


class TasksTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        label = QLabel(
            "Tasks editor — coming in Phase 5.4.\n\n"
            "This tab will let you add/remove/edit TaskSpec entries and\n"
            "preview Cartesian expansion of list-valued fields.",
            self,
        )
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #888; font-size: 14px;")
        layout.addWidget(label)
