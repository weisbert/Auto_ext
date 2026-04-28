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

    # Feature #3 added "View log file" at the top of the menu. Without
    # an on-disk log file it stays disabled; the rendered + report
    # entries enable as before given the .qci + .report fixtures.
    assert captured["actions"] == [
        "View log file",
        "Open rendered template",
        "Open LVS report",
    ]
    assert captured["enabled"] == [False, True, True]
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
    # si has no rendered template (dry-runner returns None) and no log
    # on disk yet — only the two stage-agnostic entries appear, both
    # disabled with a tooltip explaining why.
    assert [a.text() for a in actions] == [
        "View log file",
        "Open rendered template",
    ]
    assert all(a.isEnabled() is False for a in actions)
    assert all(a.toolTip() for a in actions)  # non-empty hints


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


# ---- Feature #3: click semantics + extended right-click menu ------------


def test_feature3_single_click_does_not_emit_stage_selected(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """Single-click on a stage row must NOT emit ``stage_selected``.

    Pre-Feature-#3, ``itemClicked`` jumped the Log tab on every click,
    which made right-clicking a row impossible without losing focus.
    The signal is now wired to ``itemDoubleClicked`` instead.
    """
    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)
    tab._reset_status_tree(controller.tasks, list(STAGE_ORDER))

    # Auto-follow is the OTHER channel — turn it off so we isolate the
    # click path. Without this, an in-flight stage_started could pollute
    # the captured list and mask a regression.
    tab._auto_follow_check.setChecked(False)

    captured = _phase59_a_capture_stage_selected(tab)

    # Emit the itemClicked signal manually — that's how Qt would dispatch
    # a real click on the row. We expect NO stage_selected propagation.
    task_id = controller.tasks[0].task_id
    calibre_item = tab._stage_items[(task_id, "calibre")]
    tab._status_tree.itemClicked.emit(calibre_item, 0)

    # waitSignal-with-timeout is the canonical way to assert "did NOT
    # fire" — any emission flips the captured list.
    qtbot.wait(50)
    assert captured == [], (
        "Single-click must not emit stage_selected; only double-click "
        "(or auto-follow) is allowed to switch the Log tab."
    )


def test_feature3_double_click_emits_stage_selected(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """Double-click on a stage row emits ``stage_selected(log_path)`` —
    the in-GUI quick-switch users had pre-Feature-#3, just bumped to a
    less aggressive trigger."""
    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    controller.load(project_tools_config)
    tab._reset_status_tree(controller.tasks, list(STAGE_ORDER))

    # Disable auto-follow so we isolate the double-click path.
    tab._auto_follow_check.setChecked(False)

    captured = _phase59_a_capture_stage_selected(tab)

    task_id = controller.tasks[0].task_id
    calibre_item = tab._stage_items[(task_id, "calibre")]
    tab._status_tree.itemDoubleClicked.emit(calibre_item, 0)

    assert len(captured) == 1
    assert captured[0] == ae_root / "logs" / f"task_{task_id}" / "calibre.log"


def test_feature3_view_log_file_disabled_when_log_missing(
    qtbot, project_tools_config: Path, tmp_path: Path
) -> None:
    """The 'View log file' entry exists on every stage row but stays
    disabled with a tooltip until the worker actually writes the log.
    """
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
        item = tab._stage_items[(task_id, "calibre")]
        pos = tab._status_tree.visualItemRect(item).center()
        tab._on_tree_context_menu(pos)
    finally:
        run_tab_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    actions = captured["actions"]
    log_action = next(a for a in actions if a.text() == "View log file")
    assert log_action.isEnabled() is False
    assert log_action.toolTip() == "Log not yet produced"


def test_feature3_view_log_file_opens_log_when_present(
    qtbot, project_tools_config: Path, tmp_path: Path, monkeypatch
) -> None:
    """When the log file exists, 'View log file' is enabled and triggers
    :func:`open_in_os` with the resolved log path on click."""
    ae_root = tmp_path / "ae_root"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunTab(controller)
    qtbot.addWidget(tab)
    _phase59_bc_seed_run_tree(tab, controller, project_tools_config)
    task_id = controller.tasks[0].task_id

    # Materialize the log file on disk so the action enables.
    log_path = ae_root / "logs" / f"task_{task_id}" / "calibre.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("calibre stdout\n", encoding="utf-8")

    # Mock open_in_os at the module level — that's where run_tab imported
    # it from, so monkeypatching auto_ext.ui.tabs.run_tab.open_in_os is
    # what the real menu invocation will route through.
    opened: list[Path] = []
    from auto_ext.ui.tabs import run_tab as run_tab_mod

    monkeypatch.setattr(
        run_tab_mod, "open_in_os", lambda p: opened.append(Path(p))
    )

    captured: dict[str, object] = {}
    real_exec = run_tab_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = list(self.actions())
        return None

    run_tab_mod.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        item = tab._stage_items[(task_id, "calibre")]
        pos = tab._status_tree.visualItemRect(item).center()
        tab._on_tree_context_menu(pos)
    finally:
        run_tab_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    actions = captured["actions"]
    log_action = next(a for a in actions if a.text() == "View log file")
    assert log_action.isEnabled() is True
    assert log_action.toolTip() == str(log_path)

    # Trigger the action and confirm open_in_os received the right path.
    log_action.trigger()
    assert opened == [log_path]
