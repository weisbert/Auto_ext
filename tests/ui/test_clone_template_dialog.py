"""Tests for the Copy-template flow (Feature #1, 2026-04-28).

Two layers:

* :class:`CloneTemplateDialog` smoke (constructs, save writes the
  right pane to disk).
* :class:`TemplatesTab._on_copy_template` integration: monkeypatching
  ``QInputDialog.getText`` and walking the click path produces a new
  ``.j2`` + manifest sidecar on disk and surfaces it in the Tasks
  tab's per-stage combos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs.run_tab import RunTab  # noqa: E402
from auto_ext.ui.tabs.tasks_tab import TasksTab  # noqa: E402
from auto_ext.ui.tabs.templates_tab import TemplatesTab  # noqa: E402
from auto_ext.ui.widgets.diff_editor import (  # noqa: E402
    CloneTemplateDialog,
    open_for_save_as_new,
)


# ---- fixtures --------------------------------------------------------------


def _scaffold_project(tmp_path: Path) -> tuple[Path, Path]:
    """Same shape as test_templates_tab._scaffold_project."""
    auto_ext_root = tmp_path / "Auto_ext"
    config_dir = auto_ext_root / "config"
    config_dir.mkdir(parents=True)
    templates = auto_ext_root / "templates"
    (templates / "calibre").mkdir(parents=True)
    (templates / "quantus").mkdir()
    (templates / "si").mkdir()
    (templates / "jivaro").mkdir()

    calibre_tpl = templates / "calibre" / "calibre_lvs.qci.j2"
    calibre_tpl.write_text(
        "*lvsAbortOnSupplyError: 0\n"
        "*lvsConnectByName: 1\n",
        encoding="utf-8",
    )
    (templates / "calibre" / "calibre_lvs.qci.j2.manifest.yaml").write_text(
        "template: calibre_lvs.qci.j2\n"
        "knobs:\n"
        "  connect_by_name:\n"
        "    type: bool\n"
        "    default: true\n",
        encoding="utf-8",
    )

    quantus_tpl = templates / "quantus" / "ext.cmd.j2"
    quantus_tpl.write_text(
        "temperature [[temperature]]\n",
        encoding="utf-8",
    )
    (templates / "quantus" / "ext.cmd.j2.manifest.yaml").write_text(
        "template: ext.cmd.j2\n"
        "knobs:\n"
        "  temperature:\n"
        "    type: float\n"
        "    default: 55.0\n",
        encoding="utf-8",
    )

    (config_dir / "project.yaml").write_text(
        "tech_name: HN001\n"
        "templates:\n"
        f"  calibre: {calibre_tpl}\n"
        f"  quantus: {quantus_tpl}\n",
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: C\n"
        "  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    return config_dir, auto_ext_root


def _make_tab(qtbot, config_dir: Path, auto_ext_root: Path) -> tuple[
    TemplatesTab, TasksTab, ConfigController
]:
    controller = ConfigController(
        auto_ext_root=auto_ext_root, workarea=auto_ext_root.parent,
    )
    run_tab = RunTab(controller)
    tasks_tab = TasksTab(controller, run_tab)
    templates_tab = TemplatesTab(controller, run_tab)
    # Wire templates_changed -> tasks_tab.refresh_template_combos like
    # MainWindow does.
    templates_tab.templates_changed.connect(tasks_tab.refresh_template_combos)
    qtbot.addWidget(run_tab)
    qtbot.addWidget(tasks_tab)
    qtbot.addWidget(templates_tab)
    controller.load(config_dir)
    return templates_tab, tasks_tab, controller


# ---- CloneTemplateDialog smoke --------------------------------------------


def test_clone_dialog_loads_source_and_dest_text(qtbot, tmp_path: Path) -> None:
    src = tmp_path / "src.qci.j2"
    src.write_text("source body\n", encoding="utf-8")
    dest = tmp_path / "dest.qci.j2"
    dest.write_text("source body\n", encoding="utf-8")

    dlg = CloneTemplateDialog(src, dest)
    qtbot.addWidget(dlg)
    assert dlg._left_pane.isReadOnly()
    assert not dlg._right_pane.isReadOnly()
    assert dlg._left_pane.toPlainText() == "source body\n"
    assert dlg._right_pane.toPlainText() == "source body\n"
    assert dlg.saved is False
    assert dlg.dest_path == dest


def test_clone_dialog_save_writes_right_pane(qtbot, tmp_path: Path) -> None:
    src = tmp_path / "src.qci.j2"
    src.write_text("ORIGINAL\n", encoding="utf-8")
    dest = tmp_path / "dest.qci.j2"
    dest.write_text("ORIGINAL\n", encoding="utf-8")

    dlg = CloneTemplateDialog(src, dest)
    qtbot.addWidget(dlg)
    dlg.set_right_text_for_tests("EDITED\n")
    dlg._on_save()  # bypass exec_()
    assert dlg.saved is True
    assert dest.read_text(encoding="utf-8") == "EDITED\n"
    # Source untouched.
    assert src.read_text(encoding="utf-8") == "ORIGINAL\n"


def test_open_for_save_as_new_factory(qtbot, tmp_path: Path) -> None:
    src = tmp_path / "src.qci.j2"
    src.write_text("body\n", encoding="utf-8")
    dest = tmp_path / "dest.qci.j2"
    dest.write_text("body\n", encoding="utf-8")
    dlg = open_for_save_as_new(src, dest)
    qtbot.addWidget(dlg)
    assert isinstance(dlg, CloneTemplateDialog)


# ---- Templates tab Copy template integration ------------------------------


def test_copy_template_button_exists_and_enabled(qtbot, tmp_path: Path) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _, _ = _make_tab(qtbot, cfg, root)
    assert tab._copy_template_btn.isEnabled() is True


def test_copy_template_creates_clone_and_refreshes_tasks(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    templates_tab, tasks_tab, _ = _make_tab(qtbot, cfg, root)

    # Select the calibre template so the source is pre-resolved.
    for i in range(templates_tab._list.count()):
        if "[calibre]" in templates_tab._list.item(i).text():
            templates_tab._list.setCurrentRow(i)
            break

    # Mock the suffix prompt: user types "noconnect", clicks OK.
    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QInputDialog.getText",
        lambda *a, **kw: ("noconnect", True),
    )
    # Skip the dialog's exec_() so the test doesn't block on the
    # modal editor — clone_template has already written both files
    # before exec_() is called, which is what we're checking.
    monkeypatch.setattr(
        "auto_ext.ui.widgets.diff_editor.CloneTemplateDialog.exec_",
        lambda self: None,
    )

    templates_tab._on_copy_template()

    new_j2 = root / "templates" / "calibre" / "calibre_lvs_noconnect.qci.j2"
    new_manifest = (
        root / "templates" / "calibre"
        / "calibre_lvs_noconnect.qci.j2.manifest.yaml"
    )
    assert new_j2.is_file()
    assert new_manifest.is_file()
    # Manifest is byte-for-byte (knob declarations preserved).
    src_manifest = root / "templates" / "calibre" / "calibre_lvs.qci.j2.manifest.yaml"
    assert new_manifest.read_bytes() == src_manifest.read_bytes()

    # Tasks tab combo for calibre now lists the new file.
    calibre_combo = tasks_tab._template_combos["calibre"]
    items = [calibre_combo.itemText(i) for i in range(calibre_combo.count())]
    assert "calibre_lvs_noconnect.qci.j2" in items


def test_copy_template_rejects_existing_destination(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    templates_tab, _, _ = _make_tab(qtbot, cfg, root)

    # Pre-create the would-be destination so the second prompt fires.
    blocker = root / "templates" / "calibre" / "calibre_lvs_dup.qci.j2"
    blocker.write_text("existing\n", encoding="utf-8")

    for i in range(templates_tab._list.count()):
        if "[calibre]" in templates_tab._list.item(i).text():
            templates_tab._list.setCurrentRow(i)
            break

    # First prompt: "dup" (collides). Second prompt: cancel.
    calls = {"n": 0}

    def fake_get_text(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("dup", True)
        return ("", False)  # cancel

    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QInputDialog.getText", fake_get_text,
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QMessageBox.warning",
        lambda *a, **kw: warnings.append(a[2] if len(a) >= 3 else ""),
    )
    monkeypatch.setattr(
        "auto_ext.ui.widgets.diff_editor.CloneTemplateDialog.exec_",
        lambda self: None,
    )

    templates_tab._on_copy_template()

    # User cancelled after the collision warning fired.
    assert any("already exists" in w for w in warnings)
    # Existing file untouched.
    assert blocker.read_text(encoding="utf-8") == "existing\n"


def test_copy_template_falls_back_to_file_dialog_when_no_selection(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    templates_tab, _, _ = _make_tab(qtbot, cfg, root)

    # Force "no selection" path by clearing current row.
    templates_tab._list.setCurrentRow(-1)
    templates_tab._selected_path = None

    # Mock QFileDialog.getOpenFileName to return our quantus template.
    quantus_src = str(root / "templates" / "quantus" / "ext.cmd.j2")
    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QFileDialog.getOpenFileName",
        lambda *a, **kw: (quantus_src, "Jinja templates (*.j2)"),
    )
    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QInputDialog.getText",
        lambda *a, **kw: ("fast", True),
    )
    monkeypatch.setattr(
        "auto_ext.ui.widgets.diff_editor.CloneTemplateDialog.exec_",
        lambda self: None,
    )

    templates_tab._on_copy_template()
    assert (root / "templates" / "quantus" / "ext_fast.cmd.j2").is_file()


def test_copy_template_handles_missing_manifest_gracefully(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloning a preset (no .manifest.yaml sidecar) should still succeed."""
    auto_ext_root = tmp_path / "Auto_ext"
    config_dir = auto_ext_root / "config"
    config_dir.mkdir(parents=True)
    presets_dir = auto_ext_root / "templates" / "presets"
    presets_dir.mkdir(parents=True)
    (auto_ext_root / "templates" / "calibre").mkdir(parents=True)

    preset = presets_dir / "noseed.j2"
    preset.write_text("body\n", encoding="utf-8")
    # No manifest sidecar.

    (config_dir / "project.yaml").write_text("tech_name: X\n", encoding="utf-8")
    (config_dir / "tasks.yaml").write_text(
        "- library: L\n  cell: C\n  lvs_layout_view: layout\n",
        encoding="utf-8",
    )

    templates_tab, _, _ = _make_tab(qtbot, config_dir, auto_ext_root)
    # Find and select the preset row.
    for i in range(templates_tab._list.count()):
        if "noseed.j2" in templates_tab._list.item(i).text():
            templates_tab._list.setCurrentRow(i)
            break

    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QInputDialog.getText",
        lambda *a, **kw: ("v2", True),
    )
    monkeypatch.setattr(
        "auto_ext.ui.widgets.diff_editor.CloneTemplateDialog.exec_",
        lambda self: None,
    )

    templates_tab._on_copy_template()
    # New .j2 created; no manifest expected (none existed for source).
    assert (presets_dir / "noseed_v2.j2").is_file()
    assert not (presets_dir / "noseed_v2.j2.manifest.yaml").exists()
