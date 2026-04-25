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
