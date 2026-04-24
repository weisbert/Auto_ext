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

from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs.project_tab import ProjectTab  # noqa: E402
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
