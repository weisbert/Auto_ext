"""Tests for :class:`auto_ext.ui.widgets.preset_picker.PresetPickerDialog`."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402

from auto_ext.core.diff_template import compute_toggle  # noqa: E402
from auto_ext.core.preset import save_preset  # noqa: E402
from auto_ext.ui.widgets.preset_picker import PresetPickerDialog  # noqa: E402


# ---- fixtures --------------------------------------------------------------


def _scaffold(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "Auto_ext"
    target = root / "templates" / "calibre" / "wiodio.qci.j2"
    target.parent.mkdir(parents=True)
    target.write_text("a\nb\nC\nd\ne\n", encoding="utf-8")
    presets_dir = root / "templates" / "presets"
    presets_dir.mkdir(parents=True)
    return {"root": root, "target": target, "presets_dir": presets_dir}


def _make_preset(presets_dir: Path, slug: str, applicable_tool: str | None = None) -> None:
    toggle = compute_toggle("a\nb\nC\nd\ne\n", "a\nb\nX\nd\ne\n", slug)
    save_preset(
        toggle, slug, presets_dir=presets_dir,
        description=f"{slug} preset", applicable_tool=applicable_tool,
    )


# ---- tests ----------------------------------------------------------------


def test_picker_lists_only_valid_presets(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    _make_preset(scaf["presets_dir"], "alpha")
    _make_preset(scaf["presets_dir"], "beta")
    # Break a third preset's meta.yaml.
    _make_preset(scaf["presets_dir"], "gamma")
    (scaf["presets_dir"] / "gamma" / "meta.yaml").write_text(
        ":: not yaml :: [unbalanced\n", encoding="utf-8"
    )
    dlg = PresetPickerDialog(scaf["target"], "calibre", scaf["presets_dir"])
    qtbot.addWidget(dlg)
    visible_slugs = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert "alpha" in visible_slugs
    assert "beta" in visible_slugs
    assert "gamma" not in visible_slugs


def test_select_preset_updates_preview(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    _make_preset(scaf["presets_dir"], "alpha")
    dlg = PresetPickerDialog(scaf["target"], "calibre", scaf["presets_dir"])
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)
    assert "alpha" in dlg._meta_label.text()
    assert "[% if alpha %]" in dlg._snippet_view.toPlainText()


def test_apply_with_matching_anchors_enables_save(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    _make_preset(scaf["presets_dir"], "alpha")
    dlg = PresetPickerDialog(scaf["target"], "calibre", scaf["presets_dir"])
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)
    assert dlg._save_overwrite_btn.isEnabled() is True
    assert "[% if alpha %]" in dlg._result_view.toPlainText()


def test_apply_with_missing_anchor_disables_save(qtbot, tmp_path: Path) -> None:
    scaf = _scaffold(tmp_path)
    _make_preset(scaf["presets_dir"], "alpha")
    # Replace target with content that lacks the on-side anchor.
    scaf["target"].write_text("totally\ndifferent\ncontent\n", encoding="utf-8")
    dlg = PresetPickerDialog(scaf["target"], "calibre", scaf["presets_dir"])
    qtbot.addWidget(dlg)
    dlg._list.setCurrentRow(0)
    assert dlg._save_overwrite_btn.isEnabled() is False
    assert "anchor" in dlg._status_label.text().lower() or "✗" in dlg._status_label.text()


def test_filter_by_applicable_tool_greys_incompatible(
    qtbot, tmp_path: Path
) -> None:
    scaf = _scaffold(tmp_path)
    _make_preset(scaf["presets_dir"], "calibre_only", applicable_tool="calibre")
    _make_preset(scaf["presets_dir"], "quantus_only", applicable_tool="quantus")
    dlg = PresetPickerDialog(scaf["target"], "calibre", scaf["presets_dir"])
    qtbot.addWidget(dlg)
    # Find the quantus_only row — should be non-selectable.
    quantus_item = None
    calibre_item = None
    for i in range(dlg._list.count()):
        item = dlg._list.item(i)
        if item.text() == "quantus_only":
            quantus_item = item
        elif item.text() == "calibre_only":
            calibre_item = item
    assert quantus_item is not None
    assert calibre_item is not None
    assert not (quantus_item.flags() & Qt.ItemIsSelectable)
    assert calibre_item.flags() & Qt.ItemIsSelectable
