"""Tests for the template-management right-click menu.

Two layers:

* :class:`CloneTemplateDialog` smoke (constructs, save writes the
  right pane to disk).
* :class:`TemplatesTab` integration: the list's right-click menu
  exposes ``Copy...`` and ``Delete...``. Copy clones the .j2 +
  manifest under a user-supplied suffix and refreshes Tasks-tab
  combos. Delete removes both files after confirmation, blocked
  when the template is bound or lives outside ``<auto_ext_root>/templates/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QPoint  # noqa: E402

from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs.run_tab import RunTab  # noqa: E402
from auto_ext.ui.tabs.tasks_tab import TasksTab  # noqa: E402
from auto_ext.ui.tabs.templates_tab import TemplatesTab  # noqa: E402
from auto_ext.ui.widgets.diff_editor import (  # noqa: E402
    CloneTemplateDialog,
    open_for_save_as_new,
)


def _capture_menu_actions(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch ``QMenu.exec_`` so building the context menu records its
    actions instead of blocking on user interaction. Returns the dict
    that the patch will populate with key ``"actions"``.
    """
    captured: dict[str, object] = {}
    from auto_ext.ui.tabs import templates_tab as tt_mod

    real_exec = tt_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = list(self.actions())
        return None

    monkeypatch.setattr(tt_mod.QMenu, "exec_", fake_exec)
    captured["_real_exec"] = real_exec
    return captured


def _select_row_with(tab: TemplatesTab, needle: str) -> int:
    """Return the row index whose label contains ``needle`` (and select
    it). Raises if no row matches — keeps tests honest."""
    for i in range(tab._list.count()):
        if needle in tab._list.item(i).text():
            tab._list.setCurrentRow(i)
            return i
    raise AssertionError(f"no template row matched {needle!r}")


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


# ---- Templates tab right-click menu (Copy + Delete) ----------------------


def test_no_copy_template_toolbar_button(qtbot, tmp_path: Path) -> None:
    """The toolbar button is gone — Copy lives on the list's right-click
    menu instead."""
    cfg, root = _scaffold_project(tmp_path)
    tab, _, _ = _make_tab(qtbot, cfg, root)
    assert not hasattr(tab, "_copy_template_btn")


def test_context_menu_has_copy_and_delete(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _, _ = _make_tab(qtbot, cfg, root)
    row = _select_row_with(tab, "[calibre]")

    captured = _capture_menu_actions(monkeypatch)
    pos = tab._list.visualItemRect(tab._list.item(row)).center()
    tab._on_template_list_context_menu(pos)

    actions = captured["actions"]
    texts = [a.text() for a in actions]
    assert texts == ["Copy...", "Delete..."]


def test_context_menu_no_op_on_empty_space(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    tab, _, _ = _make_tab(qtbot, cfg, root)

    captured = _capture_menu_actions(monkeypatch)
    # Pick a position well below the last row.
    far_pos = QPoint(5, tab._list.height() + 200)
    tab._on_template_list_context_menu(far_pos)

    # No menu was opened — exec_ never called → captured stays empty.
    assert "actions" not in captured


def test_copy_action_creates_clone_and_refreshes_tasks(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    templates_tab, tasks_tab, _ = _make_tab(qtbot, cfg, root)
    src = root / "templates" / "calibre" / "calibre_lvs.qci.j2"

    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QInputDialog.getText",
        lambda *a, **kw: ("noconnect", True),
    )
    monkeypatch.setattr(
        "auto_ext.ui.widgets.diff_editor.CloneTemplateDialog.exec_",
        lambda self: None,
    )

    templates_tab._invoke_copy_template(src)

    new_j2 = root / "templates" / "calibre" / "calibre_lvs_noconnect.qci.j2"
    new_manifest = (
        root / "templates" / "calibre"
        / "calibre_lvs_noconnect.qci.j2.manifest.yaml"
    )
    assert new_j2.is_file()
    assert new_manifest.is_file()
    src_manifest = root / "templates" / "calibre" / "calibre_lvs.qci.j2.manifest.yaml"
    assert new_manifest.read_bytes() == src_manifest.read_bytes()

    calibre_combo = tasks_tab._template_combos["calibre"]
    items = [calibre_combo.itemText(i) for i in range(calibre_combo.count())]
    assert "calibre_lvs_noconnect.qci.j2" in items


def test_copy_action_rejects_existing_destination(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    templates_tab, _, _ = _make_tab(qtbot, cfg, root)
    src = root / "templates" / "calibre" / "calibre_lvs.qci.j2"

    blocker = root / "templates" / "calibre" / "calibre_lvs_dup.qci.j2"
    blocker.write_text("existing\n", encoding="utf-8")

    calls = {"n": 0}

    def fake_get_text(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return ("dup", True)
        return ("", False)

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

    templates_tab._invoke_copy_template(src)

    assert any("already exists" in w for w in warnings)
    assert blocker.read_text(encoding="utf-8") == "existing\n"


def test_copy_action_handles_missing_manifest_gracefully(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloning a preset (no .manifest.yaml sidecar) still succeeds."""
    auto_ext_root = tmp_path / "Auto_ext"
    config_dir = auto_ext_root / "config"
    config_dir.mkdir(parents=True)
    presets_dir = auto_ext_root / "templates" / "presets"
    presets_dir.mkdir(parents=True)
    (auto_ext_root / "templates" / "calibre").mkdir(parents=True)

    preset = presets_dir / "noseed.j2"
    preset.write_text("body\n", encoding="utf-8")

    (config_dir / "project.yaml").write_text("tech_name: X\n", encoding="utf-8")
    (config_dir / "tasks.yaml").write_text(
        "- library: L\n  cell: C\n  lvs_layout_view: layout\n",
        encoding="utf-8",
    )

    templates_tab, _, _ = _make_tab(qtbot, config_dir, auto_ext_root)

    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QInputDialog.getText",
        lambda *a, **kw: ("v2", True),
    )
    monkeypatch.setattr(
        "auto_ext.ui.widgets.diff_editor.CloneTemplateDialog.exec_",
        lambda self: None,
    )

    templates_tab._invoke_copy_template(preset)

    assert (presets_dir / "noseed_v2.j2").is_file()
    assert not (presets_dir / "noseed_v2.j2.manifest.yaml").exists()


# ---- Delete action -------------------------------------------------------


def test_delete_action_disabled_for_bound_template(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The calibre template is bound via project.templates.calibre, so
    Delete must stay disabled with an explanatory tooltip."""
    cfg, root = _scaffold_project(tmp_path)
    tab, _, _ = _make_tab(qtbot, cfg, root)
    row = _select_row_with(tab, "[calibre]")

    captured = _capture_menu_actions(monkeypatch)
    pos = tab._list.visualItemRect(tab._list.item(row)).center()
    tab._on_template_list_context_menu(pos)

    actions = captured["actions"]
    delete_action = next(a for a in actions if a.text() == "Delete...")
    assert delete_action.isEnabled() is False
    assert "Bound to project.templates.calibre" in delete_action.toolTip()


def test_delete_action_enabled_for_unbound_template(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A discovered-but-not-bound template can be deleted."""
    cfg, root = _scaffold_project(tmp_path)
    # Add a stray template not referenced anywhere.
    stray = root / "templates" / "calibre" / "stray.qci.j2"
    stray.write_text("body\n", encoding="utf-8")

    tab, _, _ = _make_tab(qtbot, cfg, root)
    row = _select_row_with(tab, "stray.qci.j2")

    captured = _capture_menu_actions(monkeypatch)
    pos = tab._list.visualItemRect(tab._list.item(row)).center()
    tab._on_template_list_context_menu(pos)

    actions = captured["actions"]
    delete_action = next(a for a in actions if a.text() == "Delete...")
    assert delete_action.isEnabled() is True
    assert delete_action.toolTip() == str(stray)


def test_delete_action_removes_files_after_confirmation(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    stray = root / "templates" / "calibre" / "stray.qci.j2"
    stray.write_text("body\n", encoding="utf-8")
    stray_manifest = root / "templates" / "calibre" / "stray.qci.j2.manifest.yaml"
    stray_manifest.write_text("knobs: {}\n", encoding="utf-8")

    tab, tasks_tab, _ = _make_tab(qtbot, cfg, root)
    _select_row_with(tab, "stray.qci.j2")

    from PyQt5.QtWidgets import QMessageBox

    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QMessageBox.question",
        lambda *a, **kw: QMessageBox.Yes,
    )

    tab._invoke_delete_template(stray)

    assert not stray.exists()
    assert not stray_manifest.exists()

    # Tasks tab combos refreshed — the stray no longer appears.
    calibre_combo = tasks_tab._template_combos["calibre"]
    items = [calibre_combo.itemText(i) for i in range(calibre_combo.count())]
    assert "stray.qci.j2" not in items


def test_delete_action_aborts_on_cancel(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, root = _scaffold_project(tmp_path)
    stray = root / "templates" / "calibre" / "stray.qci.j2"
    stray.write_text("body\n", encoding="utf-8")

    tab, _, _ = _make_tab(qtbot, cfg, root)

    from PyQt5.QtWidgets import QMessageBox

    monkeypatch.setattr(
        "auto_ext.ui.tabs.templates_tab.QMessageBox.question",
        lambda *a, **kw: QMessageBox.No,
    )

    tab._invoke_delete_template(stray)
    assert stray.is_file()


def test_delete_action_blocked_outside_templates_dir(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A template discovered via project.templates pointing OUTSIDE
    ``<auto_ext_root>/templates/`` must have Delete disabled even
    when unbound — we don't reach into shared filesystems from the GUI.
    """
    cfg, root = _scaffold_project(tmp_path)

    # Drop an out-of-tree template and bind it loosely (we'll check
    # the disabled-tooltip code path by simulating the entry's
    # off-tree resolved path).
    outside = tmp_path / "shared" / "foreign.qci.j2"
    outside.parent.mkdir(parents=True)
    outside.write_text("body\n", encoding="utf-8")

    tab, _, _ = _make_tab(qtbot, cfg, root)

    # Build a synthetic TemplateEntry with no tool binding pointing to
    # the foreign file. This is the same shape collect_template_entries
    # would produce for an unbound discovered template that happened to
    # live outside templates/ (rare but possible if a symlink farm).
    from auto_ext.ui.templates_view import TemplateEntry
    entry = TemplateEntry(path=outside, tool=None, in_project=False)
    reason = tab._delete_blocked_reason(entry, outside)
    assert reason is not None
    assert "Outside" in reason
