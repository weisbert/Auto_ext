"""Tests for :class:`auto_ext.ui.widgets.template_diff_viewer.TemplateDiffViewerDialog`."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QMimeData, QPointF, QUrl, Qt  # noqa: E402
from PyQt5.QtGui import QDropEvent  # noqa: E402

from auto_ext.ui.widgets.template_diff_viewer import (  # noqa: E402
    TemplateDiffViewerDialog,
    _BG_DELETE,
    _BG_INSERT,
    _BG_PADDING,
    _BG_REPLACE,
)


# ---- helpers ---------------------------------------------------------------


def _make_dialog(qtbot) -> TemplateDiffViewerDialog:
    dlg = TemplateDiffViewerDialog()
    qtbot.addWidget(dlg)
    return dlg


def _drop_file(zone, path: Path) -> None:
    """Synthesize a QDropEvent so the drop zone signal fires."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    pos = QPointF(zone.rect().center())
    event = QDropEvent(pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    zone.dropEvent(event)


def _block_backgrounds(pane) -> list[str]:
    """Return the per-block background color names (hex) for a pane."""
    doc = pane.document()
    out: list[str] = []
    block = doc.firstBlock()
    while block.isValid():
        out.append(block.blockFormat().background().color().name())
        block = block.next()
    return out


# ---- tests -----------------------------------------------------------------


def test_dialog_constructs(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    assert dlg._left_pane.toPlainText() == ""
    assert dlg._right_pane.toPlainText() == ""
    assert dlg._left_path is None
    assert dlg._right_path is None
    assert "Drop two files" in dlg._status_label.text()


def test_drop_two_files_shows_side_by_side(qtbot, tmp_path: Path) -> None:
    a = tmp_path / "a.j2"
    a.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    b = tmp_path / "b.j2"
    b.write_text("alpha\nBETA\ngamma\n", encoding="utf-8")
    dlg = _make_dialog(qtbot)
    _drop_file(dlg._left_zone, a)
    _drop_file(dlg._right_zone, b)
    assert "alpha" in dlg._left_pane.toPlainText()
    assert "BETA" in dlg._right_pane.toPlainText()
    assert dlg._left_path == a
    assert dlg._right_path == b


def test_diff_lines_get_colored(qtbot, tmp_path: Path) -> None:
    # Three distinct hunks separated by "common" anchors so difflib
    # produces one delete, one replace, and one insert opcode.
    a = tmp_path / "a.j2"
    a.write_text(
        "common1\n"
        "delete-me\n"        # hunk 1: delete (only on left)
        "common2\n"
        "replace-left\n"     # hunk 2: replace
        "common3\n"
        "common4\n",
        encoding="utf-8",
    )
    b = tmp_path / "b.j2"
    b.write_text(
        "common1\n"
        "common2\n"
        "replace-right\n"    # hunk 2: replace
        "common3\n"
        "insert-me\n"        # hunk 3: insert (only on right)
        "common4\n",
        encoding="utf-8",
    )
    dlg = _make_dialog(qtbot)
    _drop_file(dlg._left_zone, a)
    _drop_file(dlg._right_zone, b)

    left_bgs = _block_backgrounds(dlg._left_pane)
    right_bgs = _block_backgrounds(dlg._right_pane)
    # Both panes must have the same number of visible rows (alignment).
    assert len(left_bgs) == len(right_bgs)
    # Expect at least one row each tinted red (delete), green (insert),
    # yellow (replace), and at least one padding gray on each side.
    assert _BG_DELETE.name() in left_bgs
    assert _BG_INSERT.name() in right_bgs
    assert _BG_REPLACE.name() in left_bgs
    assert _BG_REPLACE.name() in right_bgs
    assert _BG_PADDING.name() in left_bgs
    assert _BG_PADDING.name() in right_bgs


def test_swap_button_exchanges_panes(qtbot, tmp_path: Path) -> None:
    a = tmp_path / "a.j2"
    a.write_text("AAA\n", encoding="utf-8")
    b = tmp_path / "b.j2"
    b.write_text("BBB\n", encoding="utf-8")
    dlg = _make_dialog(qtbot)
    _drop_file(dlg._left_zone, a)
    _drop_file(dlg._right_zone, b)
    assert dlg._left_path == a
    assert dlg._right_path == b
    dlg._swap_btn.click()
    assert dlg._left_path == b
    assert dlg._right_path == a
    assert "BBB" in dlg._left_pane.toPlainText()
    assert "AAA" in dlg._right_pane.toPlainText()


def test_synchronized_scrolling(qtbot, tmp_path: Path) -> None:
    # Long enough to force a scrollbar.
    big_left = "\n".join(f"left-line-{i}" for i in range(200)) + "\n"
    big_right = "\n".join(f"right-line-{i}" for i in range(200)) + "\n"
    a = tmp_path / "a.j2"
    a.write_text(big_left, encoding="utf-8")
    b = tmp_path / "b.j2"
    b.write_text(big_right, encoding="utf-8")
    dlg = _make_dialog(qtbot)
    dlg.resize(600, 200)  # squeeze the viewport so a scrollbar appears
    dlg.show()
    qtbot.waitExposed(dlg)
    _drop_file(dlg._left_zone, a)
    _drop_file(dlg._right_zone, b)

    left_sb = dlg._left_pane.verticalScrollBar()
    right_sb = dlg._right_pane.verticalScrollBar()
    # Pick a value the bar can reach.
    target = max(1, left_sb.maximum() // 2)
    left_sb.setValue(target)
    assert abs(right_sb.value() - left_sb.value()) <= 1


def test_status_banner_shows_hunk_count(qtbot, tmp_path: Path) -> None:
    # Three distinct diff blocks: a delete, a replace, an insert.
    a = tmp_path / "a.j2"
    a.write_text(
        "same1\n"
        "delete-only\n"   # block 1: delete
        "same2\n"
        "old\n"           # block 2: replace
        "same3\n",
        encoding="utf-8",
    )
    b = tmp_path / "b.j2"
    b.write_text(
        "same1\n"
        "same2\n"
        "new\n"           # block 2: replace
        "same3\n"
        "insert-only\n",  # block 3: insert
        encoding="utf-8",
    )
    dlg = _make_dialog(qtbot)
    _drop_file(dlg._left_zone, a)
    _drop_file(dlg._right_zone, b)
    text = dlg._status_label.text()
    assert "3 diff blocks" in text


def test_non_utf8_file_shows_warning(qtbot, tmp_path: Path, monkeypatch) -> None:
    bad = tmp_path / "binary.bin"
    bad.write_bytes(b"prefix\n\xff\xfe\x00invalid utf8\n")

    warnings: list[tuple[str, str]] = []
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kw: warnings.append((args[1], args[2])) or QMessageBox.Ok,
    )

    dlg = _make_dialog(qtbot)
    _drop_file(dlg._left_zone, bad)

    # Bad file must NOT have been adopted as the left side.
    assert dlg._left_path is None
    assert dlg._left_text == ""
    assert warnings, "expected a QMessageBox.warning to have fired"
    title, body = warnings[-1]
    assert "Encoding error" in title or "UTF-8" in body


def test_close_button_closes_dialog(qtbot) -> None:
    dlg = _make_dialog(qtbot)
    dlg.show()
    qtbot.waitExposed(dlg)
    assert dlg.isVisible() is True
    dlg._close_btn.click()
    assert dlg.isVisible() is False
