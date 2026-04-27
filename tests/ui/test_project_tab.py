"""Tests for :class:`auto_ext.ui.tabs.project_tab.ProjectTab`.

Uses the ``project_tools_config`` conftest fixture for a realistic
``project.yaml`` with env_overrides seeded, a live ConfigController,
and a real (but inert) RunTab so ``is_worker_active()`` returns False.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.config import ProjectConfig  # noqa: E402
from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs.project_tab import ProjectTab, _hint_for_field  # noqa: E402
from auto_ext.ui.tabs.run_tab import RunTab  # noqa: E402


def _make_tab(
    qtbot,
    project_tools_config: Path,
    *,
    autosave: bool = False,
    auto_ext_root: Path | None = None,
) -> tuple[ProjectTab, ConfigController]:
    """Build a ProjectTab + ConfigController for testing.

    ``autosave`` defaults to False so staging-only assertions
    (``pending_edits == {...}``) keep their pre-autosave semantics.
    Pass ``autosave=True`` for tests that exercise the auto-save flow.

    ``auto_ext_root`` (optional) lets tests aim the controller at the
    real repo root so the Templates ComboBox can enumerate the bundled
    ``templates/<stage>/*.j2``. When ``None`` the controller falls
    back to ``config_dir.parent`` (tmp dir, no templates).
    """
    controller = ConfigController(auto_ext_root=auto_ext_root)
    run_tab = RunTab(controller)
    tab = ProjectTab(controller, run_tab)
    tab._autosave_enabled = autosave
    qtbot.addWidget(run_tab)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)
    return tab, controller


def test_populate_reflects_project_yaml(qtbot, project_tools_config: Path) -> None:
    tab, _ = _make_tab(qtbot, project_tools_config)
    assert tab._fields["tech_name"].text() == "HN001"
    assert tab._fields["employee_id"].text() == "alice"
    # Phase 5.6.5: paths replaces pdk_subdir / runset_versions form fields.
    assert "calibre_lvs_dir" in tab._path_fields
    assert "qrc_deck_dir" in tab._path_fields
    assert (
        tab._path_fields["calibre_lvs_dir"].text()
        == "$calibre_source_added_place|parent"
    )


def test_field_edit_marks_dirty_and_enables_save(
    qtbot, project_tools_config: Path
) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)
    assert controller.is_dirty is False
    assert tab._save_btn.isEnabled() is False

    tab._fields["tech_name"].setText("HN999")
    with qtbot.waitSignal(controller.dirty_changed, timeout=2000):
        tab._on_field_edited("tech_name")

    assert controller.is_dirty is True
    assert controller.pending_edits == {"tech_name": "HN999"}
    assert tab._save_btn.isEnabled() is True
    assert tab._revert_btn.isEnabled() is True


def test_clear_field_stages_none(qtbot, project_tools_config: Path) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)
    tab._fields["employee_id"].setText("")
    tab._on_field_edited("employee_id")
    assert controller.pending_edits == {"employee_id": None}


def test_save_writes_to_disk_and_clears_dirty(
    qtbot, project_tools_config: Path
) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)
    tab._fields["tech_name"].setText("HN777")
    tab._on_field_edited("tech_name")
    assert controller.is_dirty is True

    with qtbot.waitSignal(controller.config_saved, timeout=2000):
        tab._on_save_clicked()

    on_disk = (project_tools_config / "project.yaml").read_text(encoding="utf-8")
    assert "tech_name: HN777" in on_disk
    assert controller.is_dirty is False
    assert tab._fields["tech_name"].text() == "HN777"


def test_env_override_round_trip(qtbot, project_tools_config: Path) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)

    # WORK_ROOT is referenced by extraction_output_dir so it's always in
    # the discovered required set. The fixture pre-seeds an override for
    # it; staging a new value should update the row's value column.
    controller.stage_edits({"env_overrides.WORK_ROOT": "/tmp/staged_work_root"})
    tab._refresh_env_table()

    row_idx = _find_env_row(tab, "WORK_ROOT")
    assert row_idx is not None
    source = tab._env_table.item(row_idx, 1).text()
    value = tab._env_table.item(row_idx, 2).text()
    assert "override" in source
    assert value == "/tmp/staged_work_root"


def test_env_clear_removes_override(qtbot, project_tools_config: Path) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)

    # Fixture seeds WORK_ROOT override; clear it and verify it flips back.
    assert "WORK_ROOT" in controller.project.env_overrides
    tab._on_clear_override("WORK_ROOT")
    assert controller.pending_edits == {"env_overrides.WORK_ROOT": None}
    effective = controller.effective_env_overrides()
    assert "WORK_ROOT" not in effective


def test_revert_restores_original_on_next_load(
    qtbot, project_tools_config: Path
) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)
    tab._fields["tech_name"].setText("HN_TYPO")
    tab._on_field_edited("tech_name")
    assert controller.is_dirty is True

    controller.revert()
    assert controller.is_dirty is False
    # The QLineEdit still shows the typo (revert doesn't repopulate the
    # form; user can reload if they want the form refreshed).
    # project on disk is untouched.
    on_disk = (project_tools_config / "project.yaml").read_text(encoding="utf-8")
    assert "tech_name: HN001" in on_disk
    assert "HN_TYPO" not in on_disk


def test_save_disabled_while_run_active(
    qtbot, project_tools_config: Path, monkeypatch
) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)

    # Pretend the RunTab has an in-flight worker.
    monkeypatch.setattr(tab._run_tab, "is_worker_active", lambda: True)

    tab._fields["tech_name"].setText("HN888")
    tab._on_field_edited("tech_name")
    # dirty fires but save button stays disabled because a run is active.
    assert controller.is_dirty is True
    assert tab._save_btn.isEnabled() is False


def _find_env_row(tab: ProjectTab, var_name: str) -> int | None:
    for r in range(tab._env_table.rowCount()):
        item = tab._env_table.item(r, 0)
        if item is not None and item.text() == var_name:
            return r
    return None


def test_env_panel_resolves_auto_ext_root_relative_templates(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    """Regression: env discover must resolve auto_ext-root-relative
    template paths even when cwd is neither workarea nor the deploy dir.
    Reproduces the 'discover error: cannot read template templates\\si\\
    default.env.j2' that surfaced after stripping the Auto_ext_pro/ prefix.
    """
    deploy = tmp_path / "auto_ext_pro_max"
    config_dir = deploy / "config"
    config_dir.mkdir(parents=True)
    templates = deploy / "templates" / "calibre"
    templates.mkdir(parents=True)
    # Template references one env var so _discover_env_vars actually has
    # work to do (and would fail to read the file if path resolution broke).
    (templates / "x.qci.j2").write_text(
        "*lvsRulesFile: $VERIFY_ROOT/foo\n", encoding="utf-8"
    )
    (config_dir / "project.yaml").write_text(
        "templates:\n"
        "  calibre: templates/calibre/x.qci.j2\n",
        encoding="utf-8",
    )
    (config_dir / "tasks.yaml").write_text(
        "- library: L\n  cell: C\n  lvs_layout_view: layout\n", encoding="utf-8"
    )
    # cwd outside both deploy and workarea — exercises step-3 fallback
    # in resolve_template_path. This is the realistic GUI launch case
    # when run.sh is not used.
    monkeypatch.chdir(tmp_path)

    controller = ConfigController(auto_ext_root=deploy, workarea=tmp_path)
    run_tab = RunTab(controller)
    tab = ProjectTab(controller, run_tab)
    qtbot.addWidget(run_tab)
    qtbot.addWidget(tab)
    controller.load(config_dir)

    # Env panel should have populated with VERIFY_ROOT (read from the
    # template), not collapsed into a "(discover error: ...)" row.
    assert tab._env_table.rowCount() > 0
    first_cell = tab._env_table.item(0, 0).text()
    assert not first_cell.startswith("(discover error"), (
        f"env panel showed discover error: {first_cell!r}"
    )
    assert _find_env_row(tab, "VERIFY_ROOT") is not None


# ---- _hint_for_field (pure rules) -----------------------------------------


def _bare_project(**kwargs) -> ProjectConfig:
    return ProjectConfig(**kwargs)


def test_hint_work_root_reads_shell(monkeypatch) -> None:
    monkeypatch.setenv("WORK_ROOT", "/data/work")
    hint = _hint_for_field("work_root", _bare_project(), {})
    assert "shell $WORK_ROOT" in hint
    assert "/data/work" in hint


def test_hint_work_root_unset_when_no_shell(monkeypatch) -> None:
    monkeypatch.delenv("WORK_ROOT", raising=False)
    hint = _hint_for_field("work_root", _bare_project(), {})
    assert "✗ unset" in hint


def test_hint_work_root_prefers_override_over_shell(monkeypatch) -> None:
    # Staged env override wins over shell — matches resolve_env precedence.
    monkeypatch.setenv("WORK_ROOT", "/data/from_shell")
    hint = _hint_for_field(
        "work_root", _bare_project(), {"WORK_ROOT": "/data/from_override"}
    )
    assert "/data/from_override" in hint
    assert "/data/from_shell" not in hint


def test_hint_employee_id_falls_back_to_user(monkeypatch) -> None:
    monkeypatch.setenv("USER", "alice")
    hint = _hint_for_field("employee_id", _bare_project(), {})
    assert "alice" in hint


def test_hint_tech_name_derives_from_pdk_tech_file(monkeypatch) -> None:
    # tech_name_env_vars defaults to PDK_TECH_FILE first; runner derives
    # parent dir name as the tech_name.
    monkeypatch.setenv("PDK_TECH_FILE", "/foo/HN001/tech.lib")
    monkeypatch.delenv("PDK_LAYER_MAP_FILE", raising=False)
    monkeypatch.delenv("PDK_DISPLAY_FILE", raising=False)
    hint = _hint_for_field("tech_name", _bare_project(), {})
    assert "auto-derived: HN001" in hint


def test_hint_tech_name_no_candidate(monkeypatch) -> None:
    for v in ("PDK_TECH_FILE", "PDK_LAYER_MAP_FILE", "PDK_DISPLAY_FILE"):
        monkeypatch.delenv(v, raising=False)
    hint = _hint_for_field("tech_name", _bare_project(), {})
    assert "no candidate" in hint


def test_hint_layer_map_default() -> None:
    hint = _hint_for_field("layer_map", _bare_project(), {})
    assert "${PDK_LAYER_MAP_FILE}" in hint


# ---- placeholder integration ----------------------------------------------


# ---- Paths group (Phase 5.6.5) -------------------------------------------


def test_paths_group_edit_stages_paths_dotted_key(
    qtbot, project_tools_config: Path
) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)
    line = tab._path_fields["calibre_lvs_dir"]
    line.setText("$calibre_source_added_place|parent|parent")
    tab._on_path_field_edited("calibre_lvs_dir")
    assert controller.is_dirty is True
    assert controller.pending_edits == {
        "paths.calibre_lvs_dir": "$calibre_source_added_place|parent|parent"
    }


def test_paths_group_resolved_preview_in_tooltip(
    qtbot, project_tools_config: Path
) -> None:
    tab, _ = _make_tab(qtbot, project_tools_config)
    tip = tab._path_fields["calibre_lvs_dir"].toolTip()
    # The tooltip should preview the resolved path using the staged env.
    # Fixture sets calibre_source_added_place to .../Ver_Plus_1.0l_0.9/CFXXX/empty.cdl
    assert "resolves to:" in tip
    assert "CFXXX" in tip


def test_paths_group_used_by_lists_calibre_template(
    qtbot, project_tools_config: Path
) -> None:
    tab, _ = _make_tab(qtbot, project_tools_config)
    label = tab._path_used_by_labels["calibre_lvs_dir"]
    text = label.text()
    # The bundled production calibre template references calibre_lvs_dir.
    assert "calibre_lvs.qci.j2" in text
    # qrc_deck_dir is referenced by both calibre + quantus templates.
    label2 = tab._path_used_by_labels["qrc_deck_dir"]
    text2 = label2.text()
    assert "calibre_lvs.qci.j2" in text2
    assert "ext.cmd.j2" in text2


def test_paths_group_clear_field_stages_none(
    qtbot, project_tools_config: Path
) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config)
    tab._path_fields["calibre_lvs_dir"].setText("")
    tab._on_path_field_edited("calibre_lvs_dir")
    assert controller.pending_edits == {"paths.calibre_lvs_dir": None}


# ---- Save button bug fix + auto-save (Phase 5.6.6 / 5.9) -----------------


def test_save_button_recovers_after_run_finishes(
    qtbot, project_tools_config: Path
) -> None:
    """Edits staged while a run is in flight latched Save in the disabled
    state because _on_dirty_changed read is_worker_active() at toggle
    time and nobody re-fired the signal afterwards. RunTab now emits
    worker_state_changed; ProjectTab re-evaluates its Save button on
    that signal."""
    tab, controller = _make_tab(qtbot, project_tools_config, autosave=False)

    # Pretend a run is in flight: monkey-patch is_worker_active to True
    # for the duration of the edit, then flip it False and emit the
    # signal as RunTab._on_worker_done would.
    run_tab = tab._run_tab

    worker_active = {"value": True}
    run_tab.is_worker_active = lambda: worker_active["value"]  # type: ignore[method-assign]

    tab._fields["tech_name"].setText("HN_DURING_RUN")
    tab._on_field_edited("tech_name")
    assert controller.is_dirty is True
    # During run: Save grey because of the gate.
    assert tab._save_btn.isEnabled() is False

    # Run finishes: worker active flips False, RunTab emits the signal.
    worker_active["value"] = False
    run_tab.worker_state_changed.emit(False)

    # Save button should now be enabled — this is the bug fix.
    assert tab._save_btn.isEnabled() is True


def test_field_edit_autosaves_when_no_run(
    qtbot, project_tools_config: Path
) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config, autosave=True)
    tab._fields["tech_name"].setText("HN_AUTOSAVED")
    tab._on_field_edited("tech_name")
    # Auto-save flushed the edit through controller.save() → load()
    # cycle, so dirty is back to False and pending_edits is empty.
    assert controller.is_dirty is False
    assert controller.pending_edits == {}
    # Project model + on-disk file both reflect the new value.
    assert controller.project.tech_name == "HN_AUTOSAVED"
    on_disk = (project_tools_config / "project.yaml").read_text(encoding="utf-8")
    assert "tech_name: HN_AUTOSAVED" in on_disk


def test_path_edit_autosaves(qtbot, project_tools_config: Path) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config, autosave=True)
    tab._path_fields["calibre_lvs_dir"].setText(
        "$calibre_source_added_place|parent|parent"
    )
    tab._on_path_field_edited("calibre_lvs_dir")
    assert controller.is_dirty is False
    assert (
        controller.project.paths["calibre_lvs_dir"]
        == "$calibre_source_added_place|parent|parent"
    )


def test_remove_path_autosaves(qtbot, project_tools_config: Path) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config, autosave=True)
    assert "qrc_deck_dir" in controller.project.paths
    tab._on_remove_path_clicked("qrc_deck_dir")
    assert controller.is_dirty is False
    assert "qrc_deck_dir" not in controller.project.paths


def test_env_override_autosaves(qtbot, project_tools_config: Path) -> None:
    tab, controller = _make_tab(qtbot, project_tools_config, autosave=True)
    tab._controller.stage_edits({})  # no-op, just to mirror real flow
    # Simulate the override path manually since it goes through QInputDialog;
    # call _on_clear_override which is the simpler entry point.
    assert "WORK_ROOT" in controller.project.env_overrides
    tab._on_clear_override("WORK_ROOT")
    assert controller.is_dirty is False
    assert "WORK_ROOT" not in controller.project.env_overrides


def test_field_edit_does_not_autosave_during_run(
    qtbot, project_tools_config: Path
) -> None:
    """Auto-save respects the worker_active gate — staged edits during
    a run accumulate in pending_edits instead of triggering save()."""
    tab, controller = _make_tab(qtbot, project_tools_config, autosave=True)
    tab._run_tab.is_worker_active = lambda: True  # type: ignore[method-assign]

    tab._fields["tech_name"].setText("HN_DURING_RUN")
    tab._on_field_edited("tech_name")
    # Edit was staged but not flushed.
    assert controller.is_dirty is True
    assert controller.pending_edits == {"tech_name": "HN_DURING_RUN"}


def test_field_edit_does_not_autosave_on_external_conflict(
    qtbot, project_tools_config: Path
) -> None:
    """If project.yaml has changed on disk since load, auto-save bails
    out so the user has to consciously force-save through the warning
    dialog (no silent overwrite)."""
    tab, controller = _make_tab(qtbot, project_tools_config, autosave=True)
    # Simulate external mtime drift.
    tab._controller.has_external_change = lambda: True  # type: ignore[method-assign]

    tab._fields["tech_name"].setText("HN_NEW")
    tab._on_field_edited("tech_name")
    # Edit was staged but not flushed.
    assert controller.is_dirty is True
    assert controller.pending_edits == {"tech_name": "HN_NEW"}


# ---- Templates ComboBox (Project tab — B half) ---------------------------


def test_templates_combo_lists_available_j2_files(
    qtbot, project_tools_config: Path, templates_root: Path
) -> None:
    """Each per-stage ComboBox enumerates *.j2 files under
    <auto_ext_root>/templates/<stage>/. Aim auto_ext_root at the real
    repo root so the bundled production templates show up."""
    tab, _ = _make_tab(
        qtbot, project_tools_config, auto_ext_root=templates_root.parent
    )
    quantus_combo = tab._template_combos["quantus"]
    items = [quantus_combo.itemText(i) for i in range(quantus_combo.count())]
    assert items[0] == "(unset)"
    assert "ext.cmd.j2" in items
    assert "dspf.cmd.j2" in items


def test_templates_combo_set_stages_dotted_key(
    qtbot, project_tools_config: Path, templates_root: Path
) -> None:
    tab, controller = _make_tab(
        qtbot,
        project_tools_config,
        autosave=False,
        auto_ext_root=templates_root.parent,
    )
    quantus_combo = tab._template_combos["quantus"]
    idx = quantus_combo.findData("templates/quantus/dspf.cmd.j2")
    assert idx >= 0
    quantus_combo.setCurrentIndex(idx)
    assert controller.pending_edits == {
        "templates.quantus": "templates/quantus/dspf.cmd.j2"
    }


def test_templates_combo_clear_stages_none(
    qtbot, project_tools_config: Path, templates_root: Path
) -> None:
    tab, controller = _make_tab(
        qtbot,
        project_tools_config,
        autosave=False,
        auto_ext_root=templates_root.parent,
    )
    tab._on_template_clear_clicked("quantus")
    assert controller.pending_edits == {"templates.quantus": None}


def test_templates_combo_autosaves_when_enabled(
    qtbot, project_tools_config: Path, templates_root: Path
) -> None:
    tab, controller = _make_tab(
        qtbot,
        project_tools_config,
        autosave=True,
        auto_ext_root=templates_root.parent,
    )
    quantus_combo = tab._template_combos["quantus"]
    idx = quantus_combo.findData("templates/quantus/dspf.cmd.j2")
    quantus_combo.setCurrentIndex(idx)
    assert controller.is_dirty is False
    assert (
        str(controller.project.templates.quantus).replace("\\", "/")
        == "templates/quantus/dspf.cmd.j2"
    )


def test_placeholder_updates_after_env_override(
    qtbot, project_tools_config: Path, monkeypatch
) -> None:
    # Drop shell + fixture's project-level override so the staged
    # override is the only source of truth for the hint.
    monkeypatch.delenv("WORK_ROOT", raising=False)
    tab, controller = _make_tab(qtbot, project_tools_config)
    controller.stage_edits({"env_overrides.WORK_ROOT": None})  # clear fixture's seed
    tab._refresh_hints()
    line = tab._fields["work_root"]
    assert "✗ unset" in line.placeholderText()

    # Stage a fresh override; placeholder should pick it up immediately.
    controller.stage_edits({"env_overrides.WORK_ROOT": "/staged/override"})
    tab._refresh_hints()
    assert "/staged/override" in line.placeholderText()
