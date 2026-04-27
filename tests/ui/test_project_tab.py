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


def _make_tab(qtbot, project_tools_config: Path) -> tuple[ProjectTab, ConfigController]:
    controller = ConfigController()
    run_tab = RunTab(controller)
    tab = ProjectTab(controller, run_tab)
    qtbot.addWidget(run_tab)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)
    return tab, controller


def test_populate_reflects_project_yaml(qtbot, project_tools_config: Path) -> None:
    tab, _ = _make_tab(qtbot, project_tools_config)
    assert tab._fields["tech_name"].text() == "HN001"
    assert tab._fields["pdk_subdir"].text() == "CFXXX"
    assert tab._fields["runset_versions.lvs"].text() == "Ver_Plus_1.0l_0.9"
    assert tab._fields["employee_id"].text() == "alice"


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


def test_hint_pdk_subdir_falls_back_to_no_candidate_message(monkeypatch) -> None:
    """pdk_subdir has a default env var chain (calibre_source_added_place);
    when the env is empty, hint surfaces the unresolved-candidates list
    instead of the legacy "no fallback" string.

    Scrub shell env explicitly: on developer machines that have already
    sourced the project setup script, ``$calibre_source_added_place``
    leaks through resolve_env -> os.environ and the fallback succeeds,
    masking the assertion.
    """
    monkeypatch.delenv("calibre_source_added_place", raising=False)
    hint = _hint_for_field("pdk_subdir", _bare_project(), {})
    assert "no candidate resolved" in hint
    assert "calibre_source_added_place" in hint


def test_hint_pdk_subdir_auto_derived_when_env_resolves() -> None:
    """When pdk_subdir_env_vars resolve, hint shows the derived value —
    same UX shape as tech_name's existing (auto-derived: HN001)."""
    hint = _hint_for_field(
        "pdk_subdir",
        _bare_project(),
        {"calibre_source_added_place": "/v/runset/x/Ver_1.0/CFXXX/empty.cdl"},
    )
    assert "auto-derived: CFXXX" in hint


def test_hint_runset_versions_lvs_auto_derived() -> None:
    """lvs_runset_version derives from grandparent of the env var path."""
    hint = _hint_for_field(
        "runset_versions.lvs",
        _bare_project(),
        {"calibre_source_added_place": "/v/runset/x/Ver_Plus_1.0l_0.9/CFXXX/empty.cdl"},
    )
    assert "auto-derived: Ver_Plus_1.0l_0.9" in hint


def test_hint_runset_versions_qrc_no_default_env_chain(monkeypatch) -> None:
    """qrc_runset_version_env_vars defaults to empty (no industry
    convention); hint preserves the legacy "no fallback" message so
    users know they must fill it manually or extend the chain.

    Defensive monkeypatch: even though the chain is empty by default,
    a future user-customised default would leak shell env without this.
    """
    monkeypatch.delenv("calibre_source_added_place", raising=False)
    hint = _hint_for_field("runset_versions.qrc", _bare_project(), {})
    assert "no fallback" in hint
    assert "[[qrc_runset_version]]" in hint


def test_hint_layer_map_default() -> None:
    hint = _hint_for_field("layer_map", _bare_project(), {})
    assert "${PDK_LAYER_MAP_FILE}" in hint


# ---- placeholder integration ----------------------------------------------


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
