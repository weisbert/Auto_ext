"""Log viewer: tails the currently-selected stage log.

Uses :class:`QFileSystemWatcher` to react to writes and a manual offset
so we only append new bytes rather than rereading the whole file on
each change notification. The QFileSystemWatcher on some filesystems
(e.g. network-mounted /data) can miss events — a 1-second QTimer also
polls as a fallback so the log stays current even without OS
notifications.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QFileSystemWatcher, QTimer, Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class LogTab(QWidget):
    """Live-tail one stage log at a time.

    Call :meth:`set_active_log` from outside (e.g. when the user clicks
    a stage row in the Run tab's status tree) to switch the displayed
    file. ``None`` clears the view.
    """

    _FALLBACK_POLL_MS = 1000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._path: Path | None = None
        self._offset = 0

        self._header = QLabel("(no log selected)", self)
        self._header.setStyleSheet("font-family: monospace; color: #666;")

        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(20_000)
        self._view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._view.setStyleSheet(
            "QPlainTextEdit { font-family: Consolas, 'DejaVu Sans Mono', monospace; "
            "font-size: 11px; background: #1e1e1e; color: #e0e0e0; }"
        )

        self._follow = QCheckBox("Follow tail", self)
        self._follow.setChecked(True)

        header_row = QHBoxLayout()
        header_row.addWidget(self._header, stretch=1)
        header_row.addWidget(self._follow, stretch=0)

        layout = QVBoxLayout(self)
        layout.addLayout(header_row)
        layout.addWidget(self._view)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        self._poll = QTimer(self)
        self._poll.setInterval(self._FALLBACK_POLL_MS)
        self._poll.timeout.connect(self._poll_tick)

    # ---- public API ---------------------------------------------------

    def set_active_log(self, path: Path | None) -> None:
        """Switch to ``path`` (or clear the view when ``None``).

        Creates the file's parent directory if absent so
        :class:`QFileSystemWatcher` has a target to watch before the
        first log line is written.
        """
        # Stop watching the previous path.
        if self._path is not None:
            files = self._watcher.files()
            if files:
                self._watcher.removePaths(files)
        self._path = path
        self._offset = 0
        self._view.clear()
        if path is None:
            self._header.setText("(no log selected)")
            self._poll.stop()
            return

        self._header.setText(str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        # QFileSystemWatcher won't attach to a non-existent file; create
        # it empty so the first write fires a change event.
        if not path.exists():
            path.touch()
        self._watcher.addPath(str(path))
        self._append_new_content()
        self._poll.start()

    # ---- event handlers ----------------------------------------------

    def _on_file_changed(self, path_str: str) -> None:
        self._append_new_content()
        # Some editors replace files via rename; watcher detaches when
        # that happens, so re-attach defensively.
        if self._path is not None and str(self._path) not in self._watcher.files():
            self._watcher.addPath(str(self._path))

    def _poll_tick(self) -> None:
        self._append_new_content()

    # ---- internals ----------------------------------------------------

    def _append_new_content(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        if size < self._offset:
            # File was truncated (e.g. a new run reusing the same path).
            self._view.clear()
            self._offset = 0
        if size == self._offset:
            return
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError:
            return
        if not chunk:
            return
        # appendPlainText adds a trailing newline; insertPlainText doesn't.
        # Preserve the file's own line endings by using insertPlainText at
        # the doc end.
        cursor = self._view.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(chunk)
        if self._follow.isChecked():
            self._view.moveCursor(cursor.End)
            self._view.ensureCursorVisible()
