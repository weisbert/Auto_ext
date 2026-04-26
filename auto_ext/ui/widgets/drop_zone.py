"""Reusable bordered drop area emitting ``path_dropped(Path)`` on a
single local-file drop. Shared by diff_editor.py and template_diff_viewer.py.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


class DropZone(QFrame):
    """Bordered drop area that emits ``path_dropped(Path)`` on a single
    local-file drop. Visual style toggles between normal and active
    while a drag is hovering."""

    path_dropped = pyqtSignal(object)  # Path

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self._normal_style = (
            "QFrame { border: 2px dashed #888; min-height: 40px; "
            "background: #f8f8f8; }"
        )
        self._active_style = (
            "QFrame { border: 2px dashed #2080d0; min-height: 40px; "
            "background: #e8f0fa; }"
        )
        self.setStyleSheet(self._normal_style)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        self._label = QLabel(label, self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: #666;")
        layout.addWidget(self._label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData() else []
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.setStyleSheet(self._active_style)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 — Qt API
        self.setStyleSheet(self._normal_style)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.setStyleSheet(self._normal_style)
        urls = event.mimeData().urls() if event.mimeData() else []
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.path_dropped.emit(Path(urls[0].toLocalFile()))
        else:
            event.ignore()

    def set_caption(self, text: str) -> None:
        self._label.setText(text)


__all__ = ["DropZone"]
