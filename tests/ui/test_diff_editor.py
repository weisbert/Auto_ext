"""Tests for :class:`auto_ext.ui.widgets.diff_editor.DiffEditorDialog`."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QMimeData, QPoint, QPointF, QUrl, Qt  # noqa: E402
from PyQt5.QtGui import QDropEvent  # noqa: E402

from auto_ext.core.manifest import load_manifest, manifest_path_for  # noqa: E402
from auto_ext.ui.widgets.diff_editor import DiffEditorDialog  # noqa: E402
from auto_ext.ui.widgets.jinja_highlighter import (  # noqa: E402
    JinjaHighlighter,
    BLOCK_RE,
    VAR_RE,
)


# ---- fixtures --------------------------------------------------------------


def _scaffold(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "Auto_ext"
    templates = root / "templates" / "calibre"
    templates.mkdir(parents=True)
    target = templates / "wiodio.qci.j2"
    target.write_text(
        "*lvsAbortOnSupplyError: 0\n"
        "*lvsConnectByName: 1\n"
        "*cmnWarnLayoutOverwrite: 0\n",
        encoding="utf-8",
    )
    on_raw = tmp_path / "on.qci"
    on_raw.write_text(
        "*lvsAbortOnSupplyError: 0\n"
        "*lvsConnectByName: 1\n"
        "*cmnWarnLayoutOverwrite: 0\n",
        encoding="utf-8",
    )
    off_raw = tmp_path / "off.qci"
    off_raw.write_text(
        "*lvsAbortOnSupplyError: 0\n"
        "*cmnWarnLayoutOverwrite: 0\n",
        encoding="utf-8",
    )
    return {
        "root": root,
        "target": target,
        "on": on_raw,
        "off": off_raw,
    }


def _make_dialog(qtbot, scaf: dict[str, Path]) -> DiffEditorDialog:
    dlg = DiffEditorDialog(scaf["target"], "calibre", scaf["root"])
    qtbot.addWidget(dlg)
    return dlg


def _drop_file(zone, path: Path) -> None:
    """Synthesize a QDropEvent so the drop zone signal fires."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    pos = QPointF(zone.rect().center())
    event = QDropEvent(pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    zone.dropEvent(event)


# ---- shape tests -----------------------------------------------------------


def test_dialog_constructs(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    assert dlg._toggle is None
    assert dlg._save_overwrite_btn.isEnabled() is False
    assert dlg._save_as_btn.isEnabled() is False
    assert dlg._save_preset_btn.isEnabled() is False


def test_drop_two_files_triggers_recompute(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("connect_by_net_name")
    _drop_file(dlg._on_zone, scaf["on"])
    _drop_file(dlg._off_zone, scaf["off"])
    assert dlg._toggle is not None
    assert dlg._save_overwrite_btn.isEnabled() is True
    assert "[% if connect_by_net_name %]" in dlg._right_preview.toPlainText()


def test_swap_button_exchanges_paths_and_flips_default(
    qtbot, tmp_path: Path
) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    assert dlg._on_value is True
    dlg._swap_btn.click()
    assert dlg._on_value is False
    assert dlg._on_path == scaf["off"]
    assert dlg._off_path == scaf["on"]
    assert dlg._default_label.text() == "OFF"


def test_invalid_toggle_name_disables_save(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    # The QRegExpValidator filters keystrokes, but setText bypasses it
    # for tests. An empty name leaves save buttons off via _refresh_state.
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    dlg._toggle_name_edit.setText("")
    dlg._refresh_state()
    assert dlg._save_overwrite_btn.isEnabled() is False


def test_identical_inputs_show_error(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["on"])
    assert "identical" in dlg._status_label.text() or "✗" in dlg._status_label.text()
    assert dlg._save_overwrite_btn.isEnabled() is False


def test_overlap_strict_mode_disables_save(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    # Pre-toggle the target so it already contains a [% if %] block.
    scaf["target"].write_text(
        "[% if other %]X\n[% endif %]\n*lvsAbortOnSupplyError: 0\n"
        "*lvsConnectByName: 1\n*cmnWarnLayoutOverwrite: 0\n",
        encoding="utf-8",
    )
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg._allow_existing_toggles_check.setChecked(False)
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    # Strict mode + existing block → recompute fails on apply step.
    assert dlg._save_overwrite_btn.isEnabled() is False
    assert "✗" in dlg._status_label.text() or "strict" in dlg._status_label.text().lower()


def test_save_overwrite_writes_file_and_appends_manifest(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("connect_by_net_name")
    dlg._description_edit.setText("Calibre LVS connect by net name")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    # Auto-confirm the overwrite dialog.
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)

    dlg._on_save_overwrite()

    written = scaf["target"].read_text(encoding="utf-8")
    assert "[% if connect_by_net_name %]" in written
    # Manifest got the knob.
    m = load_manifest(scaf["target"])
    assert m is not None
    assert "connect_by_net_name" in m.knobs
    assert m.knobs["connect_by_net_name"].type == "bool"
    assert m.knobs["connect_by_net_name"].default is True


def test_save_overwrite_creates_bak(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    scaf = _scaffold(tmp_path)
    original = scaf["target"].read_text(encoding="utf-8")
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Yes)
    dlg._on_save_overwrite()
    bak = scaf["target"].with_name(scaf["target"].name + ".bak")
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == original


def test_save_as_writes_to_chosen_path(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    chosen = tmp_path / "saved_as.j2"
    from PyQt5.QtWidgets import QFileDialog
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        lambda *a, **kw: (str(chosen), "Jinja templates (*.j2)"),
    )
    dlg._on_save_as()
    assert chosen.is_file()
    assert "[% if k %]" in chosen.read_text(encoding="utf-8")
    # Original target untouched.
    assert "[% if k %]" not in scaf["target"].read_text(encoding="utf-8")


def test_save_as_preset_creates_preset_dir(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    scaf = _scaffold(tmp_path)
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    monkeypatch.setattr(
        DiffEditorDialog, "_prompt_for_preset_slug",
        lambda self: ("my_preset", True),
    )
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: QMessageBox.Ok)
    dlg._on_save_preset()
    preset_dir = scaf["root"] / "templates" / "presets" / "my_preset"
    assert (preset_dir / "meta.yaml").is_file()
    assert (preset_dir / "on.txt").is_file()
    assert (preset_dir / "off.txt").is_file()
    assert (preset_dir / "snippet.j2").is_file()


def test_cancel_writes_nothing(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    original = scaf["target"].read_text(encoding="utf-8")
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    dlg.reject()
    assert scaf["target"].read_text(encoding="utf-8") == original
    assert not manifest_path_for(scaf["target"]).exists()


# ---- highlighter ----------------------------------------------------------


def test_highlighter_assigns_format_to_block_and_var_tokens(
    qtbot, tmp_path: Path
) -> None:
    from PyQt5.QtWidgets import QPlainTextEdit
    edit = QPlainTextEdit()
    qtbot.addWidget(edit)
    text = "before [% if k %] inner [[var]] after"
    edit.setPlainText(text)
    h = JinjaHighlighter(edit.document())
    h.rehighlight()
    block = edit.document().firstBlock()
    layout = block.layout()
    formats = layout.formats()
    # The highlighter emits formats only on tokens; ensure at least 2
    # format runs covering [% if k %] and [[var]].
    starts = [f.start for f in formats]
    assert text.find("[% if k %]") in starts
    assert text.find("[[var]]") in starts
    # Sanity-check the regex constants are reachable.
    assert BLOCK_RE.search(text) is not None
    assert VAR_RE.search(text) is not None


# ---- large diff banner ---------------------------------------------------


def test_large_diff_warning_surfaces_in_status(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    # Replace the raws with mostly-different content to trip the >50% threshold.
    scaf["on"].write_text("a\nb\nc\nd\ne\nf\n", encoding="utf-8")
    scaf["off"].write_text("A\nB\nC\nD\nE\nF\n", encoding="utf-8")
    scaf["target"].write_text("a\nb\nc\nd\ne\nf\n", encoding="utf-8")
    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(scaf["off"])
    assert "差异过大" in dlg._status_label.text()
    # Save buttons stay enabled — large diff is non-fatal.
    assert dlg._save_overwrite_btn.isEnabled() is True


# ---- non-utf8 raw handling ------------------------------------------------


def test_set_off_path_handles_non_utf8_file_gracefully(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Dropping a binary / non-UTF-8 file used to silently swallow the
    UnicodeDecodeError (only OSError was caught), leaving the UI in a
    state where 'nothing happened'. The fix surfaces a QMessageBox and
    leaves _off_text empty so the dialog stays in 'waiting for raws'."""
    scaf = _scaffold(tmp_path)
    bad = tmp_path / "binary.dspf"
    bad.write_bytes(b"valid ascii prefix\n\xff\xfe\x00invalid utf8\n")

    warnings: list[tuple[str, str]] = []
    from PyQt5.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *args, **kw: warnings.append((args[1], args[2])) or QMessageBox.Ok,
    )

    dlg = _make_dialog(qtbot, scaf)
    dlg._toggle_name_edit.setText("k")
    dlg.set_on_text_for_tests(scaf["on"])
    dlg.set_off_text_for_tests(bad)

    assert dlg._off_text == ""
    assert dlg._toggle is None
    assert dlg._save_overwrite_btn.isEnabled() is False
    assert warnings, "expected a QMessageBox.warning to have fired"
    title, body = warnings[-1]
    assert "编码错误" in title or "UTF-8" in body
