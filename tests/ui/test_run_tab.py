"""Smoke tests for the Run tab widget.

Exercises config loading, task/stage selection, starting a dry-run,
and verifying the live status tree populates. Heavy interaction tests
would require more fixtures; this file stays narrow.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402

from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs.run_tab import RunTab  # noqa: E402


def test_load_config_populates_task_list(
    qtbot, project_tools_config: Path
) -> None:
    controller = ConfigController()
    tab = RunTab(controller)
    qtbot.addWidget(tab)

    controller.load(project_tools_config)

    # project_tools_config declares exactly one task.
    assert tab._task_list.count() == 1
    assert tab._task_list.item(0).checkState() == Qt.Checked


def test_dry_run_populates_status_tree(
    qtbot, project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    controller = ConfigController(
        auto_ext_root=tmp_path / "pr",
        workarea=workarea,
    )
    tab = RunTab(controller)
    qtbot.addWidget(tab)

    controller.load(project_tools_config)

    # Enable dry-run so we don't spawn real subprocesses.
    tab._dry_run_check.setChecked(True)

    # Kick off and wait for the worker to finish.
    def is_done() -> bool:
        return tab._worker is None and tab._status_tree.topLevelItemCount() > 0

    tab._start_run()
    qtbot.waitUntil(is_done, timeout=15_000)

    # Tree should have one task row with all 5 stages under it.
    assert tab._status_tree.topLevelItemCount() == 1
    task_item = tab._status_tree.topLevelItem(0)
    assert task_item.childCount() == 5
    # All stages should have a terminal (non-empty) status after dry-run.
    for i in range(task_item.childCount()):
        assert task_item.child(i).text(1) != ""


def test_new_task_id_defaults_unchecked_on_reload(
    qtbot, project_tools_config: Path
) -> None:
    """Phase 5.4 UX: task_ids that weren't in the list before should
    default to Unchecked after a reload so users opt in explicitly to
    newly-added cells."""
    controller = ConfigController()
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)

    # Seed: initial load — one task, defaults to checked per first-load rule.
    assert tab._task_list.count() == 1
    assert tab._task_list.item(0).checkState() == Qt.Checked

    # Now rewrite tasks.yaml to introduce a second, brand-new task_id.
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "- library: NEW_LIB\n"
        "  cell: new_cell\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n",
        encoding="utf-8",
    )
    controller.reload()

    # Two items now: the original task_id preserves its checked state;
    # the new one defaults to Unchecked (user must opt in).
    assert tab._task_list.count() == 2
    labels = [tab._task_list.item(i).text() for i in range(2)]
    original_idx = labels.index("WB_PLL_DCO__inv__layout__schematic")
    new_idx = labels.index("NEW_LIB__new_cell__layout__schematic")
    assert tab._task_list.item(original_idx).checkState() == Qt.Checked
    assert tab._task_list.item(new_idx).checkState() == Qt.Unchecked


def test_save_disables_when_run_starts_with_pending_edits(
    qtbot, project_tools_config: Path
) -> None:
    """Symmetry partner of ``test_save_button_recovers_after_run_finishes``.

    The existing test covers run-end re-enabling Save. This one covers
    the run-START side: if the user has pending edits and a run kicks
    off, the Save button must flip disabled. The
    ``worker_state_changed(True)`` signal is the contract; ProjectTab
    listens on it and re-evaluates ``_save_btn.setEnabled``.
    """
    from auto_ext.ui.tabs.project_tab import ProjectTab

    controller = ConfigController()
    run_tab = RunTab(controller)
    project_tab = ProjectTab(controller, run_tab)
    project_tab._autosave_enabled = False
    qtbot.addWidget(run_tab)
    qtbot.addWidget(project_tab)
    controller.load(project_tools_config)

    # Stage an edit while no run is active → Save should be enabled.
    controller.stage_edits({"tech_name": "HN_PRE_RUN"})
    assert controller.is_dirty is True
    assert project_tab._save_btn.isEnabled() is True

    # Pretend the run just started: flip is_worker_active() then emit
    # worker_state_changed(True) like _start_run() does. ProjectTab's
    # _on_worker_state_changed should disable Save even though dirty
    # is still True.
    worker_active = {"value": True}
    run_tab.is_worker_active = lambda: worker_active["value"]  # type: ignore[method-assign]
    run_tab.worker_state_changed.emit(True)

    assert controller.is_dirty is True  # edits still pending
    assert project_tab._save_btn.isEnabled() is False  # Save grey during run

    # And when the run finishes (worker_state_changed(False)) Save
    # comes back IF still dirty — already covered by
    # test_save_button_recovers_after_run_finishes, replicate the
    # final assertion as a sanity check.
    worker_active["value"] = False
    run_tab.worker_state_changed.emit(False)
    assert project_tab._save_btn.isEnabled() is True


# ---- Phase 5.9 A: auto-follow live log streaming -------------------------


def _phase59_a_capture_stage_selected(tab: RunTab) -> list[object]:
    """Subscribe to ``tab.stage_selected`` and return the captured payloads.

    Avoids QSignalSpy so the assertion shape stays trivial (regular list).
    """

    captured: list[object] = []
    tab.stage_selected.connect(lambda payload: captured.append(payload))
    return captured


def test_phase59_a_auto_follow_default_is_on(qtbot) -> None:
    """Default-ON contract: the user shouldn't have to opt in to live
    log streaming — the EDA flows are long enough that auto-follow
    "just works" is the right baseline.
    """

    controller = ConfigController()
    tab = RunTab(controller)
    qtbot.addWidget(tab)

    assert tab._auto_follow_log is True
    assert tab._auto_follow_check.isChecked() is True


def test_phase59_a_auto_follow_emits_stage_selected_on_start(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """When auto-follow is ON, the worker's ``stage_started`` event must
    propagate as ``stage_selected(log_path)`` so the Log tab switches
    immediately rather than waiting for the user to click the row.
    """

    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)

    # Sanity: default-on and the project_tools_config task is loaded.
    assert tab._auto_follow_log is True
    task_id = "WB_PLL_DCO__inv__layout__schematic"

    captured = _phase59_a_capture_stage_selected(tab)

    # Simulate a worker fanning out a stage_started event. _on_stage_started
    # is a public-ish slot (named like a Qt slot) so calling it directly
    # mirrors how QtProgressReporter dispatches the signal on the GUI thread.
    tab._on_stage_started(task_id, "calibre")

    assert len(captured) == 1
    payload = captured[0]
    assert isinstance(payload, Path)
    # Path shape must match the existing _on_tree_click derivation so the
    # Log tab tails the same file the user would hit by clicking.
    assert payload == ae_root / "logs" / f"task_{task_id}" / "calibre.log"


def test_phase59_a_auto_follow_off_does_not_emit(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """Toggling auto-follow OFF must suppress the auto-emission so the
    user keeps manual control of which stage's log they're watching.
    """

    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)

    # Toggle the checkbox OFF and verify the bound flag flipped.
    tab._auto_follow_check.setChecked(False)
    assert tab._auto_follow_log is False

    captured = _phase59_a_capture_stage_selected(tab)
    tab._on_stage_started("WB_PLL_DCO__inv__layout__schematic", "calibre")

    assert captured == []
