"""Side-by-side template diff viewer (Phase 5.6.2).

A read-only meld/vimdiff-style viewer: drop two files, see them
aligned with per-line diff tinting and synchronized scrolling. No
save / no toggle generation — purely for inspection.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from PyQt5.QtGui import QColor, QFont, QTextCursor
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from auto_ext.ui.widgets.drop_zone import DropZone


# Diff tint colors — close to GitHub diff conventions.
_BG_DELETE = QColor("#ffe0e0")    # left-only (red)
_BG_INSERT = QColor("#e0ffe0")    # right-only (green)
_BG_REPLACE = QColor("#fff7d0")   # changed both sides (yellow)
_BG_PADDING = QColor("#f0f0f0")   # alignment padding (light gray)
_BG_DEFAULT = QColor(Qt.transparent)


def _mono_font() -> QFont:
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f


class TemplateDiffViewerDialog(QDialog):
    """Non-modal side-by-side diff viewer for two arbitrary text files."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("模板对比 — Template Diff Viewer")
        self.setModal(False)
        self.resize(1200, 720)

        self._left_path: Path | None = None
        self._right_path: Path | None = None
        self._left_text: str = ""
        self._right_text: str = ""

        # Guard for synchronized scrolling so the slot doesn't recurse.
        self._syncing = False

        self._build_ui()
        self._refresh_diff()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Top row: drop zones + swap button.
        zones_row = QHBoxLayout()

        left_col = QVBoxLayout()
        self._left_zone = DropZone("左侧 — 拖入文件", self)
        self._left_zone.path_dropped.connect(self._on_left_dropped)
        self._left_path_label = QLabel("(未选择文件)", self)
        self._left_path_label.setStyleSheet("color: #888; font-family: monospace;")
        left_col.addWidget(self._left_zone)
        left_col.addWidget(self._left_path_label)

        self._swap_btn = QPushButton("⇄", self)
        self._swap_btn.setToolTip("交换左右两侧")
        self._swap_btn.setMaximumWidth(40)
        self._swap_btn.clicked.connect(self._on_swap)

        right_col = QVBoxLayout()
        self._right_zone = DropZone("右侧 — 拖入文件", self)
        self._right_zone.path_dropped.connect(self._on_right_dropped)
        self._right_path_label = QLabel("(未选择文件)", self)
        self._right_path_label.setStyleSheet("color: #888; font-family: monospace;")
        right_col.addWidget(self._right_zone)
        right_col.addWidget(self._right_path_label)

        zones_row.addLayout(left_col, 1)
        zones_row.addWidget(self._swap_btn)
        zones_row.addLayout(right_col, 1)
        root.addLayout(zones_row)

        # Middle: side-by-side text panes.
        splitter = QSplitter(Qt.Horizontal, self)
        self._left_pane = self._make_pane()
        self._right_pane = self._make_pane()
        splitter.addWidget(self._left_pane)
        splitter.addWidget(self._right_pane)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(splitter, 1)

        # Wire synchronized scrolling.
        self._left_pane.verticalScrollBar().valueChanged.connect(
            self._on_left_scroll
        )
        self._right_pane.verticalScrollBar().valueChanged.connect(
            self._on_right_scroll
        )

        # Bottom: status banner + close button.
        bottom_row = QHBoxLayout()
        self._status_label = QLabel("", self)
        self._status_label.setStyleSheet("color: #555; font-size: 12px;")
        self._status_label.setWordWrap(True)
        bottom_row.addWidget(self._status_label, 1)

        self._close_btn = QPushButton("关闭", self)
        self._close_btn.clicked.connect(self.close)
        bottom_row.addWidget(self._close_btn)
        root.addLayout(bottom_row)

    def _make_pane(self) -> QPlainTextEdit:
        pane = QPlainTextEdit(self)
        pane.setReadOnly(True)
        pane.setFont(_mono_font())
        pane.setLineWrapMode(QPlainTextEdit.NoWrap)
        return pane

    # ---- file load slots --------------------------------------------------

    def _on_left_dropped(self, path: object) -> None:
        self._set_left_path(Path(str(path)))

    def _on_right_dropped(self, path: object) -> None:
        self._set_right_path(Path(str(path)))

    def _set_left_path(self, path: Path) -> None:
        text = self._read_text_or_warn(path)
        if text is None:
            return
        self._left_path = path
        self._left_text = text
        self._left_path_label.setText(str(path))
        self._refresh_diff()

    def _set_right_path(self, path: Path) -> None:
        text = self._read_text_or_warn(path)
        if text is None:
            return
        self._right_path = path
        self._right_text = text
        self._right_path_label.setText(str(path))
        self._refresh_diff()

    def _read_text_or_warn(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "读取失败", f"无法读取 {path}:\n{exc}")
        except UnicodeDecodeError as exc:
            QMessageBox.warning(
                self, "编码错误",
                f"{path} 不是 UTF-8 文本（在字节 {exc.start} 处遇到 "
                f"{exc.reason}）。\n本工具只支持 UTF-8 文本文件；请确认拖入的"
                f"是模板而不是二进制文件。",
            )
        return None

    # ---- swap -------------------------------------------------------------

    def _on_swap(self) -> None:
        self._left_path, self._right_path = self._right_path, self._left_path
        self._left_text, self._right_text = self._right_text, self._left_text
        self._left_path_label.setText(
            str(self._left_path) if self._left_path else "(未选择文件)"
        )
        self._right_path_label.setText(
            str(self._right_path) if self._right_path else "(未选择文件)"
        )
        self._refresh_diff()

    # ---- diff rendering ---------------------------------------------------

    def _refresh_diff(self) -> None:
        left_lines = self._left_text.splitlines() if self._left_text else []
        right_lines = self._right_text.splitlines() if self._right_text else []

        # Build aligned line lists with diff tags (one tag per visible row).
        # tag is "equal" | "delete" | "insert" | "replace" | "padding".
        left_rows: list[tuple[str, str]] = []   # (line, tag)
        right_rows: list[tuple[str, str]] = []

        hunk_count = 0
        diff_lines = 0

        matcher = difflib.SequenceMatcher(a=left_lines, b=right_lines)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    left_rows.append((left_lines[i1 + k], "equal"))
                    right_rows.append((right_lines[j1 + k], "equal"))
                continue

            hunk_count += 1
            if tag == "delete":
                for k in range(i1, i2):
                    left_rows.append((left_lines[k], "delete"))
                    right_rows.append(("", "padding"))
                    diff_lines += 1
            elif tag == "insert":
                for k in range(j1, j2):
                    left_rows.append(("", "padding"))
                    right_rows.append((right_lines[k], "insert"))
                    diff_lines += 1
            elif tag == "replace":
                left_n = i2 - i1
                right_n = j2 - j1
                common = min(left_n, right_n)
                for k in range(common):
                    left_rows.append((left_lines[i1 + k], "replace"))
                    right_rows.append((right_lines[j1 + k], "replace"))
                # Pad shorter side.
                if left_n > right_n:
                    for k in range(common, left_n):
                        left_rows.append((left_lines[i1 + k], "replace"))
                        right_rows.append(("", "padding"))
                elif right_n > left_n:
                    for k in range(common, right_n):
                        left_rows.append(("", "padding"))
                        right_rows.append((right_lines[j1 + k], "replace"))
                diff_lines += max(left_n, right_n)

        self._render_pane(self._left_pane, left_rows)
        self._render_pane(self._right_pane, right_rows)

        if not self._left_text and not self._right_text:
            self._status_label.setText("⓵ 拖入两个文件以查看差异")
        elif not self._left_text or not self._right_text:
            self._status_label.setText("⓶ 等待另一侧文件...")
        elif hunk_count == 0:
            self._status_label.setText("✓ 两侧内容一致 (0 个差异块)")
            self._status_label.setStyleSheet("color: #208020; font-size: 12px;")
        else:
            self._status_label.setText(
                f"ⓘ {diff_lines} 行不同 ({hunk_count} 个差异块)"
            )
            self._status_label.setStyleSheet("color: #555; font-size: 12px;")

    def _render_pane(
        self, pane: QPlainTextEdit, rows: list[tuple[str, str]]
    ) -> None:
        text = "\n".join(line for line, _tag in rows)
        pane.setPlainText(text)

        doc = pane.document()
        cursor = QTextCursor(doc)
        for index, (_line, tag) in enumerate(rows):
            block = doc.findBlockByNumber(index)
            if not block.isValid():
                continue
            block_fmt = block.blockFormat()
            block_fmt.setBackground(_color_for_tag(tag))
            cursor.setPosition(block.position())
            cursor.setBlockFormat(block_fmt)

    # ---- synchronized scrolling ------------------------------------------

    def _on_left_scroll(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._right_pane.verticalScrollBar().setValue(value)
        finally:
            self._syncing = False

    def _on_right_scroll(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._left_pane.verticalScrollBar().setValue(value)
        finally:
            self._syncing = False

    # ---- test helpers -----------------------------------------------------

    def set_left_path_for_tests(self, path: Path) -> None:
        self._set_left_path(path)

    def set_right_path_for_tests(self, path: Path) -> None:
        self._set_right_path(path)


def _color_for_tag(tag: str) -> QColor:
    if tag == "delete":
        return _BG_DELETE
    if tag == "insert":
        return _BG_INSERT
    if tag == "replace":
        return _BG_REPLACE
    if tag == "padding":
        return _BG_PADDING
    return _BG_DEFAULT


__all__ = ["TemplateDiffViewerDialog"]
