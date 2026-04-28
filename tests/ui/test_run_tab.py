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

from auto_ext.core.runner import STAGE_ORDER  # noqa: E402
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


# ---- Phase 5.9 B+C: stage-row context menu --------------------------------


def _phase59_bc_seed_run_tree(
    tab: RunTab, controller: ConfigController, project_tools_config: Path
) -> None:
    """Helper: load config + manually seed the status tree so we can
    target a stage row without spinning up a worker. The dry-run helper
    works too but takes ~5s; the context-menu tests don't need that."""
    controller.load(project_tools_config)
    tab._reset_status_tree(controller.tasks, list(STAGE_ORDER))


def _phase59_bc_make_rendered_calibre(
    ae_root: Path, task_id: str, content: str = "*lvsRunDir: /tmp/run\n*lvsReportFile: r.report\n"
) -> Path:
    """Materialize a fake rendered calibre runset on disk so the menu's
    "Open rendered template" action enables for the calibre row."""
    rendered_dir = ae_root / "runs" / f"task_{task_id}" / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)
    qci = rendered_dir / "calibre_lvs.qci"
    qci.write_text(content, encoding="utf-8")
    return qci


def test_phase59_bc_context_menu_on_stage_row(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """Right-click on the calibre stage row builds a menu with both the
    "Open rendered template" and "Open LVS report" actions."""
    ae_root = tmp_path / "ae_root"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    _phase59_bc_seed_run_tree(tab, controller, project_tools_config)

    # Materialize fake rendered .qci + report so both actions enable.
    task_id = controller.tasks[0].task_id
    qci = _phase59_bc_make_rendered_calibre(
        ae_root,
        task_id,
        f"*lvsRunDir: {(ae_root / 'lvs_run').as_posix()}\n*lvsReportFile: out.report\n",
    )
    (ae_root / "lvs_run").mkdir()
    (ae_root / "lvs_run" / "out.report").write_text("CORRECT\n", encoding="utf-8")

    # Capture the QMenu instance built by the slot. Patch QMenu.exec_ to
    # avoid blocking; we read .actions() afterwards.
    captured: dict[str, object] = {}

    from auto_ext.ui.tabs import run_tab as run_tab_mod

    real_qmenu_exec = run_tab_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["menu"] = self
        captured["actions"] = [a.text() for a in self.actions()]
        captured["enabled"] = [a.isEnabled() for a in self.actions()]
        return None

    run_tab_mod.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        # Find the calibre stage row and trigger the context-menu signal.
        calibre_item = tab._stage_items[(task_id, "calibre")]
        rect = tab._status_tree.visualItemRect(calibre_item)
        # Pos must lie inside the stage-row rect so itemAt(pos) returns it.
        pos = rect.center()
        tab._on_tree_context_menu(pos)
    finally:
        run_tab_mod.QMenu.exec_ = real_qmenu_exec  # type: ignore[method-assign]

    assert captured["actions"] == ["Open rendered template", "Open LVS report"]
    # Both should be enabled given the on-disk fixtures we set up.
    assert captured["enabled"] == [True, True]
    # Sanity: the rendered helper agrees with what the menu pointed at.
    assert qci.exists()


def test_phase59_bc_context_menu_disabled_when_file_missing(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """No rendered file on disk → "Open rendered template" disabled with
    a tooltip; on the si row there's no LVS-report action at all."""
    ae_root = tmp_path / "ae_root"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    _phase59_bc_seed_run_tree(tab, controller, project_tools_config)
    task_id = controller.tasks[0].task_id

    captured: dict[str, object] = {}
    from auto_ext.ui.tabs import run_tab as run_tab_mod

    real_exec = run_tab_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = list(self.actions())
        return None

    run_tab_mod.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        si_item = tab._stage_items[(task_id, "si")]
        pos = tab._status_tree.visualItemRect(si_item).center()
        tab._on_tree_context_menu(pos)
    finally:
        run_tab_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    actions = captured["actions"]
    assert [a.text() for a in actions] == ["Open rendered template"]
    assert actions[0].isEnabled() is False
    assert actions[0].toolTip()  # non-empty hint for the user


def test_phase59_bc_context_menu_only_on_stage_rows(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """Right-clicking on the top-level task row (no parent) yields no
    menu at all — exec_ never runs."""
    ae_root = tmp_path / "ae_root"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    _phase59_bc_seed_run_tree(tab, controller, project_tools_config)
    task_id = controller.tasks[0].task_id

    called: list[bool] = []
    from auto_ext.ui.tabs import run_tab as run_tab_mod

    real_exec = run_tab_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        called.append(True)
        return None

    run_tab_mod.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        # Task row has no parent — the slot should bail out early.
        task_item = tab._task_items[task_id]
        pos = tab._status_tree.visualItemRect(task_item).center()
        tab._on_tree_context_menu(pos)

        # Empty space (well outside the tree) — itemAt returns None.
        tab._on_tree_context_menu(QPoint_off_screen())
    finally:
        run_tab_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert called == []


def QPoint_off_screen():  # noqa: N802 — helper used inline above
    """Build a QPoint clearly outside the tree's visible area."""
    from PyQt5.QtCore import QPoint as _QPoint

    return _QPoint(-100, -100)


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


# ---- TaskSpec.label display fallback -------------------------------------


def _label_seed_run_tree(
    tab: RunTab, controller: ConfigController, project_tools_config: Path
) -> None:
    """Helper mirroring ``_phase59_bc_seed_run_tree`` — load the config
    + manually populate the status tree without spinning up a worker so
    we can inspect the rendered column-0 text + UserRole."""
    controller.load(project_tools_config)
    tab._reset_status_tree(controller.tasks, list(STAGE_ORDER))


def test_label_status_tree_uses_task_id_when_label_unset(
    qtbot, project_tools_config: Path
) -> None:
    """No ``label:`` in the spec → the Run-tab status-tree top-level
    row's column 0 shows the canonical ``task_id`` and ``data(0,
    UserRole)`` carries the same value (so internal lookups are stable
    whether or not the user later assigns a label)."""
    controller = ConfigController()
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    _label_seed_run_tree(tab, controller, project_tools_config)

    assert tab._status_tree.topLevelItemCount() == 1
    parent = tab._status_tree.topLevelItem(0)
    expected_id = "WB_PLL_DCO__inv__layout__schematic"
    assert parent.text(0) == expected_id
    assert parent.data(0, Qt.UserRole) == expected_id


def test_label_status_tree_uses_label_when_set(
    qtbot, project_tools_config: Path
) -> None:
    """A spec carrying ``label: pretty name`` → column 0 shows the
    label, but ``data(0, UserRole)`` keeps the canonical task_id so
    the click handlers and on-disk paths still work."""
    # Rewrite the fixture's tasks.yaml to add a label.
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "  label: PLL DCO inverter\n",
        encoding="utf-8",
    )
    controller = ConfigController()
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    _label_seed_run_tree(tab, controller, project_tools_config)

    parent = tab._status_tree.topLevelItem(0)
    assert parent.text(0) == "PLL DCO inverter"
    # The canonical task_id is preserved on UserRole so _on_tree_click
    # and _on_tree_context_menu can still derive the right paths.
    assert parent.data(0, Qt.UserRole) == "WB_PLL_DCO__inv__layout__schematic"


def test_label_left_task_list_uses_label_when_set(
    qtbot, project_tools_config: Path
) -> None:
    """The left task picker (QListWidget) follows the same display
    fallback rule: visible text = label-or-id, UserRole = task_id."""
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "  label: my favourite inv\n",
        encoding="utf-8",
    )
    controller = ConfigController()
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)

    assert tab._task_list.count() == 1
    item = tab._task_list.item(0)
    assert item.text() == "my favourite inv"
    assert item.data(Qt.UserRole) == "WB_PLL_DCO__inv__layout__schematic"


def test_label_selected_tasks_uses_user_role(
    qtbot, project_tools_config: Path
) -> None:
    """``_selected_tasks`` keys on UserRole, not the visible column —
    so a labelled spec round-trips the right TaskConfig back."""
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "  label: pretty\n",
        encoding="utf-8",
    )
    controller = ConfigController()
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)

    selected = tab._selected_tasks()
    assert len(selected) == 1
    assert selected[0].task_id == "WB_PLL_DCO__inv__layout__schematic"
    assert selected[0].label == "pretty"


def test_label_tree_click_round_trips_via_user_role(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """Clicking a stage row under a labelled task row must compute the
    log path from the canonical task_id (UserRole), not the visible
    label — otherwise the path would land in
    ``logs/task_<label>/...`` and miss the actual log file."""
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "  label: ignore me\n",
        encoding="utf-8",
    )
    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    _label_seed_run_tree(tab, controller, project_tools_config)

    captured = _phase59_a_capture_stage_selected(tab)

    # Drive the click handler on the calibre stage row (child of the
    # labelled task row).
    task_id = "WB_PLL_DCO__inv__layout__schematic"
    calibre_item = tab._stage_items[(task_id, "calibre")]
    tab._on_tree_click(calibre_item, 0)

    assert len(captured) == 1
    payload = captured[0]
    assert isinstance(payload, Path)
    # Path uses the canonical task_id, not the label.
    assert payload == ae_root / "logs" / f"task_{task_id}" / "calibre.log"


def test_label_display_for_log_path_returns_label(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """``RunTab.display_for_log_path`` reverse-maps a log path back to
    the user-facing label-or-id string, which the main window threads
    into ``LogTab.set_active_log`` so the log header can show the
    pretty name alongside the path."""
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n"
        "  label: friendly name\n",
        encoding="utf-8",
    )
    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)

    task_id = "WB_PLL_DCO__inv__layout__schematic"
    log_path = ae_root / "logs" / f"task_{task_id}" / "calibre.log"
    assert tab.display_for_log_path(log_path) == "friendly name"
    # When no label, falls back to the task_id.
    (project_tools_config / "tasks.yaml").write_text(
        "- library: WB_PLL_DCO\n"
        "  cell: inv\n"
        "  lvs_layout_view: layout\n"
        "  lvs_source_view: schematic\n",
        encoding="utf-8",
    )
    controller.reload()
    assert tab.display_for_log_path(log_path) == task_id
    # None payload short-circuits.
    assert tab.display_for_log_path(None) is None
