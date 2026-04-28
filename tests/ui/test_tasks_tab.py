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


def _make_tab(
    qtbot, config_dir: Path, *, auto_ext_root: Path | None = None
) -> tuple[TasksTab, ConfigController]:
    controller = ConfigController(auto_ext_root=auto_ext_root)
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


def test_copy_spec_inserts_deep_clone_after_selection(
    qtbot, tmp_path: Path
) -> None:
    """Phase 5.5.2: 'copy' button duplicates the selected spec right
    after it, auto-selects the copy, and stages the change. Nested
    structures (jivaro etc.) must not share refs with the original —
    editing the copy must leave the source untouched.
    """
    d = tmp_path / "config"
    d.mkdir()
    (d / "project.yaml").write_text("employee_id: alice\n", encoding="utf-8")
    (d / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n"
        "  jivaro: {enabled: true, frequency_limit: 14}\n"
        "  jivaro_overrides:\n"
        "    A: {enabled: false}\n"
        "- library: M\n"
        "  cell: B\n"
        "  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    tab, controller = _make_tab(qtbot, d)
    assert len(tab._specs) == 2

    # Select spec 0 (the one with nested jivaro_overrides) and copy it.
    tab._spec_list.setCurrentRow(0)
    tab._on_copy_spec()

    # Inserted right after index 0.
    assert len(tab._specs) == 3
    assert tab._specs[0]["library"] == "L"
    assert tab._specs[1]["library"] == "L"  # the copy
    assert tab._specs[2]["library"] == "M"  # unchanged

    # Auto-selected the new copy.
    assert tab._spec_list.currentRow() == 1
    assert tab._current_index == 1

    # Dirty flag staged.
    assert controller.is_dirty is True
    pending = controller.pending_task_specs
    assert pending is not None
    assert len(pending) == 3

    # Deep copy: mutating the copy's nested dict must not touch source.
    tab._specs[1]["jivaro"]["frequency_limit"] = 99
    tab._specs[1]["jivaro_overrides"]["A"]["enabled"] = True
    assert tab._specs[0]["jivaro"]["frequency_limit"] == 14
    assert tab._specs[0]["jivaro_overrides"]["A"]["enabled"] is False


def test_copy_spec_no_op_when_nothing_selected(qtbot, tmp_path: Path) -> None:
    """When the list has no selection, the copy slot is a safe no-op
    (mirrors how _on_remove_spec / _move_spec early-return).
    """
    cfg = _multi_spec_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    # Force "no selection" state.
    tab._spec_list.setCurrentRow(-1)
    tab._current_index = -1
    before = len(tab._specs)
    tab._on_copy_spec()
    assert len(tab._specs) == before
    assert controller.is_dirty is False


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


# ---- per-task knob editor (Phase 5.5.1 A) -----------------------------


def _knobs_config(tmp_path: Path) -> Path:
    """Build a config_dir with a project.yaml + tasks.yaml + a quantus
    template + manifest so the per-task knob editor has real knobs to
    render. Mimics the real repo's quantus shape on a tiny scale.
    """
    root = tmp_path
    templates = root / "templates" / "quantus"
    templates.mkdir(parents=True)
    (templates / "ext.cmd.j2").write_text(
        "filter_cap -limit [[exclude_floating_nets_limit]]\n"
        "temperature [[temperature]]\n",
        encoding="utf-8",
    )
    (templates / "ext.cmd.j2.manifest.yaml").write_text(
        "template: ext.cmd.j2\n"
        "knobs:\n"
        "  exclude_floating_nets_limit:\n"
        "    type: int\n"
        "    default: 5000\n"
        "    range: [100, 100000]\n"
        "  temperature:\n"
        "    type: float\n"
        "    default: 55.0\n",
        encoding="utf-8",
    )

    cfg = root / "config"
    cfg.mkdir()
    (cfg / "project.yaml").write_text(
        "templates:\n"
        f"  quantus: {(templates / 'ext.cmd.j2').as_posix()}\n",
        encoding="utf-8",
    )
    (cfg / "tasks.yaml").write_text(
        "- library: L\n  cell: A\n  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    return cfg


def test_per_task_knobs_starts_folded_when_no_task_overrides(
    qtbot, tmp_path: Path
) -> None:
    cfg = _knobs_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    assert tab._knobs_box.isCheckable() is True
    assert tab._knobs_box.isChecked() is False
    assert tab._knobs_form_host.isHidden() is True


def test_per_task_knobs_auto_expands_when_spec_has_task_knobs(
    qtbot, tmp_path: Path
) -> None:
    root = tmp_path
    cfg = _knobs_config(tmp_path)
    (cfg / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n"
        "  knobs:\n"
        "    quantus:\n"
        "      temperature: 25.0\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, cfg)
    assert tab._knobs_box.isChecked() is True
    assert tab._knobs_form_host.isHidden() is False
    # The quantus group should hold 2 KnobEditor rows; temperature should
    # be in override mode (is_default=False), the other in default mode.
    editors = tab._task_knob_editors
    assert ("quantus", "temperature") in editors
    assert ("quantus", "exclude_floating_nets_limit") in editors
    temp = editors[("quantus", "temperature")]
    assert temp._reset_btn.isEnabled() is True


def test_per_task_knob_edit_stages_spec_change(qtbot, tmp_path: Path) -> None:
    cfg = _knobs_config(tmp_path)
    tab, controller = _make_tab(qtbot, cfg)
    # Pull the editor for temperature and fake an edit.
    # The fold starts collapsed; the editor was still rendered though
    # (rebuild runs regardless of the box state — only visibility differs).
    editor = tab._task_knob_editors[("quantus", "temperature")]
    editor._line.setText("25.0")
    editor._line.editingFinished.emit()
    assert controller.is_dirty is True
    specs = controller.pending_task_specs
    assert specs is not None
    assert specs[0]["knobs"]["quantus"]["temperature"] == 25.0


def test_per_task_knob_reset_removes_task_override(qtbot, tmp_path: Path) -> None:
    cfg = _knobs_config(tmp_path)
    (cfg / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n"
        "  knobs:\n"
        "    quantus:\n"
        "      temperature: 25.0\n",
        encoding="utf-8",
    )
    tab, controller = _make_tab(qtbot, cfg)
    editor = tab._task_knob_editors[("quantus", "temperature")]
    editor._reset_btn.click()
    specs = controller.pending_task_specs
    assert specs is not None
    # Cascading prune: the only task override was removed → knobs key
    # should be gone entirely from the spec dict.
    assert "knobs" not in specs[0]


def test_per_task_knob_default_hint_shows_project_layer_value(
    qtbot, tmp_path: Path
) -> None:
    cfg = _knobs_config(tmp_path)
    # Set a project-layer override, but no task-layer override yet.
    (cfg / "project.yaml").write_text(
        "templates:\n"
        f"  quantus: {(tmp_path / 'templates' / 'quantus' / 'ext.cmd.j2').as_posix()}\n"
        "knobs:\n"
        "  quantus:\n"
        "    temperature: 60.0\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, cfg)
    editor = tab._task_knob_editors[("quantus", "temperature")]
    # Task layer empty → is_default=True; the (default) hint should
    # show only unit/range, not the manifest's 55.0.
    assert editor._reset_btn.isEnabled() is False
    # Now stage a task-layer override and re-render.
    editor._line.setText("70.0")
    editor._line.editingFinished.emit()
    # After re-render the editor is a fresh instance; re-fetch.
    editor2 = tab._task_knob_editors[("quantus", "temperature")]
    # The hint should reference the project layer (60.0), not manifest
    # default (55.0), because the project layer is the effective fallback.
    assert "60" in editor2._hint.text()
    assert editor2._reset_btn.isEnabled() is True


def test_per_task_knobs_isolation_across_specs(qtbot, tmp_path: Path) -> None:
    cfg = _knobs_config(tmp_path)
    (cfg / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n"
        "  knobs:\n"
        "    quantus: {temperature: 25.0}\n"
        "- library: M\n"
        "  cell: B\n"
        "  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, cfg)
    # Spec 0 has knobs → expanded.
    assert tab._knobs_box.isChecked() is True
    # Switch to spec 1 (no task knobs) → fold.
    tab._spec_list.setCurrentRow(1)
    assert tab._knobs_box.isChecked() is False


# ---- Per-task Templates ComboBox (Phase 5.x — A half) --------------------


def _templates_config(tmp_path: Path) -> tuple[Path, Path]:
    """Build a config_dir + auto_ext_root with two quantus templates so
    the per-stage ComboBox has a real choice to switch between."""
    root = tmp_path
    templates = root / "templates" / "quantus"
    templates.mkdir(parents=True)
    (templates / "ext.cmd.j2").write_text("ext placeholder\n", encoding="utf-8")
    (templates / "ext.cmd.j2.manifest.yaml").write_text(
        "template: ext.cmd.j2\nknobs: {}\n", encoding="utf-8"
    )
    (templates / "dspf.cmd.j2").write_text("dspf placeholder\n", encoding="utf-8")
    (templates / "dspf.cmd.j2.manifest.yaml").write_text(
        "template: dspf.cmd.j2\nknobs: {}\n", encoding="utf-8"
    )

    cfg = root / "config"
    cfg.mkdir()
    (cfg / "project.yaml").write_text(
        "templates:\n  quantus: templates/quantus/ext.cmd.j2\n",
        encoding="utf-8",
    )
    (cfg / "tasks.yaml").write_text(
        "- library: L\n  cell: A\n  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    return cfg, root


def test_per_task_templates_combo_lists_both(qtbot, tmp_path: Path) -> None:
    cfg, root = _templates_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, auto_ext_root=root)
    quantus = tab._template_combos["quantus"]
    items = [quantus.itemText(i) for i in range(quantus.count())]
    # Index 0 is the "(default: <project value>)" sentinel.
    assert items[0].startswith("(default:")
    assert "templates/quantus/ext.cmd.j2" in items[0]
    assert "ext.cmd.j2" in items
    assert "dspf.cmd.j2" in items


def test_per_task_template_override_mutates_spec(qtbot, tmp_path: Path) -> None:
    cfg, root = _templates_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, auto_ext_root=root)
    quantus = tab._template_combos["quantus"]
    idx = quantus.findData("templates/quantus/dspf.cmd.j2")
    assert idx >= 0
    quantus.setCurrentIndex(idx)
    spec = tab._current_spec()
    assert spec is not None
    assert spec["templates"]["quantus"] == "templates/quantus/dspf.cmd.j2"


def test_per_task_template_clear_falls_back_to_project(
    qtbot, tmp_path: Path
) -> None:
    cfg, root = _templates_config(tmp_path)
    # Pre-seed a per-task override in tasks.yaml, then clear via the GUI.
    (cfg / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n"
        "  templates:\n"
        "    quantus: templates/quantus/dspf.cmd.j2\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, cfg, auto_ext_root=root)
    # Verify the override was loaded.
    assert tab._current_spec()["templates"]["quantus"] == (
        "templates/quantus/dspf.cmd.j2"
    )
    # Clear button → fall back to project default.
    tab._on_task_template_clear("quantus")
    spec = tab._current_spec()
    # 'templates' key gets pruned when no overrides remain.
    assert "templates" not in spec or spec["templates"].get("quantus") is None


def test_per_task_template_auto_expands_when_override_exists(
    qtbot, tmp_path: Path
) -> None:
    """A spec carrying a templates.<stage> override should land with
    the Templates collapsible group already expanded so the user sees
    the override without hunting for the toggle."""
    cfg, root = _templates_config(tmp_path)
    (cfg / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n"
        "  templates:\n"
        "    quantus: templates/quantus/dspf.cmd.j2\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, cfg, auto_ext_root=root)
    assert tab._templates_box.isChecked() is True


def test_per_task_template_starts_folded_when_no_overrides(
    qtbot, tmp_path: Path
) -> None:
    cfg, root = _templates_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg, auto_ext_root=root)
    assert tab._templates_box.isChecked() is False


# ---- per-task dspf_out_path combo (Tasks tab) ----------------------------


def _dspf_tasks_config(tmp_path: Path) -> Path:
    """Build a config_dir with a project + tasks for dspf combo tests.
    Includes env_overrides for the templates' substitution chain so the
    preview resolver has live values to work with.
    """
    d = tmp_path / "config"
    d.mkdir()
    (d / "project.yaml").write_text(
        "env_overrides:\n"
        "  WORK_ROOT: /w\n"
        "  WORK_ROOT2: /wkr2\n"
        "dspf_out_path: \"${WORK_ROOT2}/{cell}.dspf\"\n",
        encoding="utf-8",
    )
    (d / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    return d


def test_dspf_combo_has_default_sentinel_at_index_0(
    qtbot, tmp_path: Path
) -> None:
    """The tasks tab variant prepends a ``(default: <X>)`` sentinel at
    index 0 whose label shows the project layer's resolved preview."""
    cfg = _dspf_tasks_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    combo = tab._dspf_combo._combo
    label0 = combo.itemText(0)
    assert label0.startswith("(default:")
    # Must show the resolved real path, not the template form.
    assert "${" not in label0
    assert "{cell}" not in label0
    # Project layer resolves WORK_ROOT2 + cell A → /wkr2/A.dspf.
    assert "/wkr2/A.dspf" in label0


def test_dspf_combo_select_default_sentinel_clears_per_task_override(
    qtbot, tmp_path: Path
) -> None:
    """A spec carrying a per-task override drops back to inheritance
    when the user picks the ``(default: ...)`` sentinel."""
    d = tmp_path / "config"
    d.mkdir()
    (d / "project.yaml").write_text(
        "env_overrides:\n  WORK_ROOT2: /wkr2\n"
        "dspf_out_path: \"${WORK_ROOT2}/{cell}.dspf\"\n",
        encoding="utf-8",
    )
    (d / "tasks.yaml").write_text(
        "- library: L\n"
        "  cell: A\n"
        "  lvs_layout_view: layout\n"
        "  dspf_out_path: \"/per/task/{cell}.dspf\"\n",
        encoding="utf-8",
    )
    tab, _ = _make_tab(qtbot, d)
    spec = tab._current_spec()
    assert spec is not None
    assert spec.get("dspf_out_path") == "/per/task/{cell}.dspf"
    # Pick the default sentinel (index 0) → per-task override is removed.
    tab._dspf_combo._combo.setCurrentIndex(0)
    spec = tab._current_spec()
    assert spec is not None
    assert "dspf_out_path" not in spec


def test_dspf_combo_select_preset_stores_per_task_override(
    qtbot, tmp_path: Path
) -> None:
    """Selecting a preset in tasks tab stores the template form on the
    spec (not the resolved real path)."""
    cfg = _dspf_tasks_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    combo = tab._dspf_combo._combo
    # Find the ${output_dir} preset.
    idx = -1
    for i in range(combo.count()):
        if combo.itemData(i) == "${output_dir}/{cell}.dspf":
            idx = i
            break
    assert idx >= 0
    combo.setCurrentIndex(idx)
    spec = tab._current_spec()
    assert spec is not None
    assert spec["dspf_out_path"] == "${output_dir}/{cell}.dspf"


def test_dspf_combo_preview_reflects_per_task_value_when_set(
    qtbot, tmp_path: Path
) -> None:
    """When a per-task override is set, the combo's preview resolves
    that value; when unset, the (default: <X>) sentinel reflects the
    project layer's value."""
    cfg = _dspf_tasks_config(tmp_path)
    tab, _ = _make_tab(qtbot, cfg)
    # Initially: spec has no override → combo selects index 0 (default sentinel).
    combo = tab._dspf_combo._combo
    assert combo.currentIndex() == 0
    preview = tab._dspf_combo._preview_label.text()
    # Default sentinel preview shows the resolved project default.
    assert "/wkr2/A.dspf" in preview
    # Now type a custom override and verify the preview tracks it.
    line = combo.lineEdit()
    line.setText("/over/{cell}.dspf")
    line.editingFinished.emit()
    spec = tab._current_spec()
    assert spec is not None
    assert spec["dspf_out_path"] == "/over/{cell}.dspf"
    preview2 = tab._dspf_combo._preview_label.text()
    assert "/over/A.dspf" in preview2
