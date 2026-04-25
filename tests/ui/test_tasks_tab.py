"""Tests for :class:`auto_ext.ui.tabs.tasks_tab.TasksTab`."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QCheckBox  # noqa: E402

from auto_ext.core.config import load_tasks  # noqa: E402
from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs.run_tab import RunTab  # noqa: E402
from auto_ext.ui.tabs.tasks_tab import TasksTab  # noqa: E402


def _make_tab(qtbot, config_dir: Path) -> tuple[TasksTab, ConfigController]:
    controller = ConfigController()
    run_tab = RunTab(controller)
    tab = TasksTab(controller, run_tab)
    qtbot.addWidget(run_tab)
    qtbot.addWidget(tab)
    controller.load(config_dir)
    return tab, controller


def _multi_spec_config(tmp_path: Path) -> Path:
    """Make a minimal config_dir with a multi-axis tasks.yaml."""
    d = tmp_path / "config"
    d.mkdir()
    (d / "project.yaml").write_text("employee_id: alice\n", encoding="utf-8")
    (d / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: [A, B]\n"
        "  lvs_layout_view: [lay, lay_t]\n"
        "  lvs_source_view: schematic\n"
        "  jivaro: {enabled: true, frequency_limit: 14, error_max: 2}\n",
        encoding="utf-8",
    )
    return d


def test_populate_loads_axis_tag_lists(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    assert tab._axis_widgets["cell"].values() == ["A", "B"]
    assert tab._axis_widgets["lvs_layout_view"].values() == ["lay", "lay_t"]
    assert tab._axis_widgets["library"].values() == ["L"]


def test_preview_shows_full_cartesian(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    tab._refresh_preview()  # flush coalescing timer
    assert tab._preview_table.rowCount() == 4
    # All checkboxes start checked (no exclude yet).
    for row in range(4):
        cb = tab._preview_table.cellWidget(row, 0)
        assert isinstance(cb, QCheckBox)
        assert cb.isChecked()


def test_uncheck_row_writes_exclude(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    tab._refresh_preview()
    # Find row with task_id containing 'A__lay_t' and untick it.
    target_row = None
    for row in range(tab._preview_table.rowCount()):
        tid_item = tab._preview_table.item(row, 1)
        if tid_item and "A__lay_t" in tid_item.text():
            target_row = row
            break
    assert target_row is not None
    cb = tab._preview_table.cellWidget(target_row, 0)
    cb.setChecked(False)

    assert controller.is_dirty is True
    specs = controller.pending_task_specs
    assert specs is not None
    assert len(specs) == 1
    excludes = specs[0].get("exclude") or []
    assert len(excludes) == 1
    # Selector should pin at least cell + lvs_layout_view (both multi-valued).
    e = excludes[0]
    assert e.get("cell") == "A"
    assert e.get("lvs_layout_view") == "lay_t"


def test_save_writes_exclude_to_disk(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    tab._refresh_preview()

    # Uncheck A__lay_t.
    for row in range(tab._preview_table.rowCount()):
        tid_item = tab._preview_table.item(row, 1)
        if tid_item and "A__lay_t" in tid_item.text():
            tab._preview_table.cellWidget(row, 0).setChecked(False)
            break

    assert controller.save() is True
    # Re-load from disk and confirm exclude took effect.
    tasks_after = load_tasks(cfg / "tasks.yaml")
    ids = {t.task_id for t in tasks_after}
    assert "L__A__lay_t__schematic" not in ids
    assert len(ids) == 3


def test_add_spec_creates_new_list_entry(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    assert len(tab._specs) == 1
    tab._on_add_spec()
    assert len(tab._specs) == 2
    assert controller.is_dirty is True


def test_remove_spec_forbidden_when_one_left(qtbot, tmp_path: Path, monkeypatch) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    # Stub QMessageBox.warning to avoid modal.
    from PyQt5.QtWidgets import QMessageBox

    calls: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: calls.append("warned")
    )
    tab._on_remove_spec()
    assert len(tab._specs) == 1
    assert calls == ["warned"]
    assert controller.is_dirty is False


def test_jivaro_override_writes_disk(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    # Simulate setting B's jivaro.enabled override to False.
    tab._on_override_enabled_changed("B", 2)  # index 2 = "false"
    assert controller.save() is True

    tasks_after = {t.cell: t for t in load_tasks(cfg / "tasks.yaml")}
    assert tasks_after["A"].jivaro.enabled is True
    assert tasks_after["B"].jivaro.enabled is False
    # frequency_limit should still inherit from spec default.
    assert tasks_after["B"].jivaro.frequency_limit == 14


def test_axis_edit_refreshes_preview(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    # Add a third cell via the axis widget's public API.
    tab._axis_widgets["cell"].set_values(["A", "B", "C"])
    tab._on_axis_changed("cell", ["A", "B", "C"])
    tab._refresh_preview()
    assert tab._preview_table.rowCount() == 6  # 3 cells * 2 layouts


def test_dirty_flag_clears_after_save(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    tab._on_add_spec()
    assert controller.is_dirty is True
    assert controller.save() is True
    assert controller.is_dirty is False


def test_populate_spec_list_summary(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    # List should have one entry that mentions "L" and "A".
    assert tab._spec_list.count() == 1
    text = tab._spec_list.item(0).text()
    assert "L" in text
    assert "A" in text


# ---- jivaro_overrides fold ------------------------------------------------


def test_jivaro_overrides_starts_folded_when_no_overrides(
    qtbot, tmp_path: Path
) -> None:
    # _multi_spec_config writes a spec with no jivaro_overrides; box
    # should be unchecked + table hidden so the editor stays compact.
    cfg = _multi_spec_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    assert tab._override_box.isCheckable() is True
    assert tab._override_box.isChecked() is False
    assert tab._override_table.isHidden() is True


def test_jivaro_overrides_auto_expands_when_spec_has_overrides(
    qtbot, tmp_path: Path
) -> None:
    # A spec with an actual override should load with the box expanded
    # so the user can see + edit the entry without hunting for the toggle.
    d = tmp_path / "config"
    d.mkdir()
    (d / "project.yaml").write_text("employee_id: alice\n", encoding="utf-8")
    (d / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: [A, B]\n"
        "  lvs_layout_view: layout\n"
        "  jivaro: {enabled: true}\n"
        "  jivaro_overrides:\n"
        "    A: {enabled: false}\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, d)
    assert tab._override_box.isChecked() is True
    assert tab._override_table.isHidden() is False


def test_jivaro_overrides_tooltip_explains_use_case(qtbot, tmp_path: Path) -> None:
    cfg = _multi_spec_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    tip = tab._override_box.toolTip()
    # Cheap content sanity: tooltip should mention the example shape and
    # call out that most projects don't need it.
    assert "jivaro_overrides" in tip
    assert "Example" in tip
    assert "folded" in tip


def test_jivaro_overrides_refolds_when_switching_to_no_override_spec(
    qtbot, tmp_path: Path
) -> None:
    d = tmp_path / "config"
    d.mkdir()
    (d / "project.yaml").write_text("employee_id: alice\n", encoding="utf-8")
    (d / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: [A, B]\n"
        "  lvs_layout_view: layout\n"
        "  jivaro_overrides:\n"
        "    A: {enabled: false}\n"
        "- library: M\n"
        "  cell: C\n"
        "  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, d)
    # Spec 0 has overrides → expanded.
    assert tab._override_box.isChecked() is True
    # Switch to spec 1 (no overrides) → should fold.
    tab._spec_list.setCurrentRow(1)
    assert tab._override_box.isChecked() is False
    assert tab._override_table.isHidden() is True
