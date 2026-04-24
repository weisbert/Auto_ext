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

from auto_ext.ui.tabs.run_tab import RunContext, RunTab  # noqa: E402


def test_load_config_populates_task_list(
    qtbot, project_tools_config: Path
) -> None:
    ctx = RunContext(config_dir=project_tools_config)
    tab = RunTab(ctx)
    qtbot.addWidget(tab)

    # project_tools_config declares exactly one task.
    assert tab._task_list.count() == 1
    assert tab._task_list.item(0).checkState() == Qt.Checked


def test_dry_run_populates_status_tree(
    qtbot, project_tools_config: Path, workarea: Path, tmp_path: Path
) -> None:
    ctx = RunContext(
        config_dir=project_tools_config,
        auto_ext_root=tmp_path / "pr",
        workarea=workarea,
    )
    tab = RunTab(ctx)
    qtbot.addWidget(tab)

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
