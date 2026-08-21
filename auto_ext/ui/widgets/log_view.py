"""Live tail of one stage log.

Was ``auto_ext/ui/tabs/log_tab.py``. It was never a tab -- it was a text pane
the Run tab embedded -- and the tab package is gone, so it lives with the
other widgets now and lost the chrome the run bar already carries: the path
label, the follow checkbox and the "Open in editor" button are all on
:class:`~auto_ext.ui.widgets.run_bar.RunBar`, which is what mounts this
widget. What is left is the tailer.

Uses :class:`QFileSystemWatcher` to react to writes and a manual byte offset
so only new bytes are appended rather than the whole file being re-read on
every change notification. The watcher misses events on some filesystems (a
network-mounted ``/data`` among them), so a 1-second timer polls as well.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QFileSystemWatcher, QTimer
from PyQt5.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from auto_ext.ui import theme

__all__ = ["LogView"]

#: Lines kept in the document. Beyond this the oldest scroll out, which is
#: what stops an EDA tool that logs a million lines from eating the session.
MAX_BLOCKS = 20_000


class LogView(QWidget):
    """Tail one log file at a time. ``set_active_log(None)`` clears it."""

    _FALLBACK_POLL_MS = 1000

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._path: Path | None = None
        self._offset = 0
        self._follow = True

        self._view = QPlainTextEdit(self)
        self._view.setReadOnly(True)
        self._view.setMaximumBlockCount(MAX_BLOCKS)
        self._view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._view.setStyleSheet(
            "QPlainTextEdit {"
            f" font-family: {theme.FONT_MONO};"
            f" font-size: {theme.FONT_SIZE_MONO}px;"
            f" background: {theme.SURFACE_LOG};"
            f" color: {theme.TEXT_LOG};"
            " border: none; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._view)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)

        self._poll = QTimer(self)
        self._poll.setInterval(self._FALLBACK_POLL_MS)
        self._poll.timeout.connect(self._append_new_content)

    # ---- public API ---------------------------------------------------

    @property
    def path(self) -> Path | None:
        """The file being tailed, or ``None``."""

        return self._path

    def text(self) -> str:
        """Everything currently displayed."""

        return self._view.toPlainText()

    def set_follow(self, follow: bool) -> None:
        """Whether new content scrolls the view. Driven by the run bar."""

        self._follow = bool(follow)
        if self._follow:
            self._scroll_to_end()

    def follows(self) -> bool:
        return self._follow

    def set_active_log(self, path: Path | None) -> None:
        """Switch to ``path``, or clear the view when ``None``.

        Creates the file's parent directory and touches the file when it does
        not exist yet: :class:`QFileSystemWatcher` refuses to attach to a
        missing path, and a stage log does not exist until the tool writes
        its first line -- which is exactly the moment the user wants to see.
        """

        watched = self._watcher.files()
        if watched:
            self._watcher.removePaths(watched)
        self._path = Path(path) if path is not None else None
        self._offset = 0
        self._view.clear()
        if self._path is None:
            self._poll.stop()
            return

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not self._path.exists():
                self._path.touch()
        except OSError:
            # An unwritable run directory is the runner's problem to report;
            # the viewer just shows nothing rather than raising into Qt.
            self._poll.stop()
            return
        self._watcher.addPath(str(self._path))
        self._append_new_content()
        self._poll.start()

    # ---- internals ----------------------------------------------------

    def _on_file_changed(self, _path_str: str) -> None:
        self._append_new_content()
        # Some tools replace the file via rename, which detaches the watcher.
        if self._path is not None and str(self._path) not in self._watcher.files():
            self._watcher.addPath(str(self._path))

    def _append_new_content(self) -> None:
        if self._path is None:
            return
        try:
            size = self._path.stat().st_size
        except OSError:
            return
        if size < self._offset:
            # Truncated -- a re-run reusing the path. Start over.
            self._view.clear()
            self._offset = 0
        if size == self._offset:
            return
        try:
            with self._path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._offset)
                chunk = handle.read()
                self._offset = handle.tell()
        except OSError:
            return
        if not chunk:
            return
        # insertPlainText at the end preserves the file's own line endings;
        # appendPlainText would add one of its own.
        cursor = self._view.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(chunk)
        if self._follow:
            self._scroll_to_end()

    def _scroll_to_end(self) -> None:
        cursor = self._view.textCursor()
        cursor.movePosition(cursor.End)
        self._view.setTextCursor(cursor)
        self._view.ensureCursorVisible()
