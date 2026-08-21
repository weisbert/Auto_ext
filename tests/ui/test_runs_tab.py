"""Tests for :mod:`auto_ext.ui.tabs.runs_tab`.

Covers the three things the tab exists for: enumerating history without ever
being taken down by one bad directory, rendering the selected run, and
renaming / annotating a run without touching its identity.

Layout is pinned here too. The main window's minimum height is already
dominated by the Project tab; ``test_runs_tab_min_height_*`` fail if this tab
starts contributing its own tall minimum, which is what makes a window
un-shrinkable on a 1080p screen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QInputDialog, QMessageBox  # noqa: E402

from auto_ext.core.handoff import HandoffPlan  # noqa: E402
from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.core.run_store import read_annotations, write_record  # noqa: E402
from auto_ext.model.run import (  # noqa: E402
    LvsResult,
    RunResults,
    StageRecord,
    allocate_run_dir,
    run_paths,
)
from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.tabs import runs_tab as runs_tab_mod  # noqa: E402
from auto_ext.ui.tabs.runs_tab import RunsTab, entry_matches  # noqa: E402

MIN_HEIGHT_BUDGET = 500


def _write_run(runs_root: Path, make_run_record, *, slug: str = "amp2-ext", **kw):
    """Allocate a run directory and write a valid ``run.json`` into it."""

    run_dir = allocate_run_dir(runs_root, slug)
    record = make_run_record(run_dir=run_dir, **kw)
    write_record(run_dir, record)
    return run_dir, record


def _lvs_run(runs_root: Path, make_run_record, *, discrepancies: int, **kw):
    """A finished run whose calibre stage failed with a discrepancy count."""

    run_dir = allocate_run_dir(runs_root, kw.pop("slug", "amp2-ext"))
    (run_dir / "logs" / "calibre.log").write_text("output\n", encoding="utf-8")
    record = make_run_record(
        run_dir=run_dir,
        overall=TaskStatus.FAILED,
        stages=[
            StageRecord(
                key="calibre",
                stage="calibre",
                status=StageStatus.FAILED,
                duration_s=61.0,
                log_path="logs/calibre.log",
                exit_code=0,
            )
        ],
        results=RunResults(
            lvs=LvsResult(
                passed=False, banner="INCORRECT", discrepancies=discrepancies
            )
        ),
        **kw,
    )
    write_record(run_dir, record)
    return run_dir, record


# ---- entry_matches (pure) ---------------------------------------------------


def test_entry_matches_searches_every_documented_axis(
    runs_root: Path, make_run_record
) -> None:
    from auto_ext.core.run_store import list_runs

    _write_run(runs_root, make_run_record, cell="amp2", recipe_id="rc-coupled")
    entry = list_runs(runs_root)[0]

    assert entry_matches(entry, "")
    assert entry_matches(entry, "  ")
    assert entry_matches(entry, "amp2")
    assert entry_matches(entry, "RC-COUPLED")  # case-insensitive
    assert entry_matches(entry, entry.run_id)
    assert entry_matches(entry, "WB_PLL")  # library
    assert not entry_matches(entry, "nothing-like-this")


# ---- history listing --------------------------------------------------------


def test_empty_history_shows_one_centered_line_naming_the_runs_dir(
    qtbot, runs_root: Path
) -> None:
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    assert tab._stack.currentWidget() is tab._empty
    assert tab._empty.alignment() & Qt.AlignCenter
    text = tab._empty.text()
    assert "No runs recorded yet" in text
    assert str(runs_root) in text
    assert tab.entries == []
    assert tab.result_card.record is None


def test_empty_state_without_a_project_points_at_the_run_tab(qtbot) -> None:
    tab = RunsTab()
    qtbot.addWidget(tab)
    assert tab.runs_root is None
    assert "No project loaded" in tab._empty.text()


def test_runs_tab_min_height_stays_within_budget_when_empty(
    qtbot, runs_root: Path
) -> None:
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    assert tab.minimumSizeHint().height() < MIN_HEIGHT_BUDGET


def test_runs_tab_min_height_stays_within_budget_when_populated(
    qtbot, runs_root: Path, make_run_record, frozen_clock
) -> None:
    """Five runs, each with five stages, must not grow the tab's minimum.

    The history list and the result card both live inside splitters and the
    card's details inside a scroll area precisely so this stays true.
    """

    for i in range(5):
        frozen_clock.tick(60)
        run_dir = allocate_run_dir(runs_root, f"cell{i}-ext")
        record = make_run_record(
            run_dir=run_dir,
            cell=f"cell{i}",
            stages=[
                StageRecord(key=s, stage=s, status=StageStatus.PASSED, duration_s=1.0)
                for s in ("si", "strmout", "calibre", "quantus", "jivaro")
            ],
        )
        write_record(run_dir, record)

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    assert len(tab.entries) == 5
    assert tab.minimumSizeHint().height() < MIN_HEIGHT_BUDGET


def test_history_is_listed_newest_first_and_the_newest_is_selected(
    qtbot, runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, cell="oldest", slug="oldest-ext")
    frozen_clock.tick(60)
    _write_run(runs_root, make_run_record, cell="middle", slug="middle-ext")
    frozen_clock.tick(60)
    newest_dir, newest = _write_run(
        runs_root, make_run_record, cell="newest", slug="newest-ext"
    )

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    assert [e.cell for e in tab.entries] == ["newest", "middle", "oldest"]
    assert tab._tree.topLevelItemCount() == 3
    assert tab._tree.topLevelItem(0).data(0, Qt.UserRole) == newest.run_id
    # Opening the tab never leaves a blank card.
    assert tab.selected_entry is not None
    assert tab.selected_entry.run_id == newest.run_id
    assert tab.result_card.record is not None
    assert tab.result_card.record.run_id == newest.run_id
    assert tab.result_card.run_dir == newest_dir


def test_status_column_is_tinted_with_the_semantic_color(
    qtbot, runs_root: Path, make_run_record
) -> None:
    from auto_ext.ui.models import STATUS_COLOR

    _write_run(runs_root, make_run_record, overall=TaskStatus.FAILED)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    item = tab._tree.topLevelItem(0)
    assert item.text(2) == "failed"
    assert item.foreground(2).color().name() == STATUS_COLOR["failed"]


def test_filter_narrows_the_list_and_updates_the_count(
    qtbot, runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, cell="amp2", slug="amp2-ext")
    frozen_clock.tick(60)
    _write_run(runs_root, make_run_record, cell="buffer", slug="buffer-ext")

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    assert tab._count_label.text() == "2 run(s)"

    tab._filter.setText("buffer")
    assert tab._tree.topLevelItemCount() == 1
    assert tab._count_label.text() == "1 of 2 run(s)"
    assert tab.selected_entry is not None
    assert tab.selected_entry.cell == "buffer"


def test_filter_with_no_match_shows_the_empty_page_and_clears_the_card(
    qtbot, runs_root: Path, make_run_record
) -> None:
    _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    assert tab.result_card.record is not None

    tab._filter.setText("no-such-run")
    assert tab._stack.currentWidget() is tab._empty
    assert "no-such-run" in tab._empty.text()
    assert tab.result_card.record is None


def test_broken_and_hand_made_directories_are_skipped_not_fatal(
    qtbot, runs_root: Path, make_run_record
) -> None:
    """One corrupt run must never empty the history.

    ``list_runs`` warns and skips; this pins that the tab inherits that
    behaviour rather than propagating the failure into the GUI.
    """

    good_dir, good = _write_run(runs_root, make_run_record)
    (runs_root / "20260821T143205Z_broken").mkdir()
    (runs_root / "20260821T143205Z_broken" / "run.json").write_text(
        "{not json", encoding="utf-8"
    )
    (runs_root / "scratch-notes").mkdir()

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    assert [e.run_id for e in tab.entries] == [good.run_id]
    assert tab._tree.topLevelItemCount() == 1
    assert tab.result_card.record is not None


def test_a_listed_run_whose_record_will_not_load_reports_it_in_the_card(
    qtbot, runs_root: Path, make_run_record
) -> None:
    """``list_runs`` indexes without validating; ``read_record`` may still fail.

    A future schema version is the honest case: the row is listed (so the run
    is not invisible) and the card explains why it cannot be rendered.
    """

    run_dir, record = _write_run(runs_root, make_run_record)
    record_path = run_paths(run_dir).record
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    record_path.write_text(json.dumps(data), encoding="utf-8")

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    assert len(tab.entries) == 1
    assert tab.result_card.record is None
    assert record.run_id in tab.result_card._placeholder.text()
    assert tab.result_card._body.isHidden()


def test_refresh_keeps_the_current_selection(
    qtbot, runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, cell="older", slug="older-ext")
    frozen_clock.tick(60)
    _write_run(runs_root, make_run_record, cell="newer", slug="newer-ext")

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    tab._tree.setCurrentItem(tab._tree.topLevelItem(1))
    older_id = tab.selected_entry.run_id  # type: ignore[union-attr]

    frozen_clock.tick(60)
    _write_run(runs_root, make_run_record, cell="newest", slug="newest-ext")
    tab.refresh()

    assert len(tab.entries) == 3
    assert tab.selected_entry is not None
    assert tab.selected_entry.run_id == older_id


def test_run_selected_signal_carries_the_index_entry(
    qtbot, runs_root: Path, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, cell="a", slug="a-ext")
    frozen_clock.tick(60)
    _write_run(runs_root, make_run_record, cell="b", slug="b-ext")

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    with qtbot.waitSignal(tab.run_selected, timeout=1000) as blocker:
        tab._tree.setCurrentItem(tab._tree.topLevelItem(1))
    assert blocker.args[0].cell == "a"


# ---- the previous-run comparison -------------------------------------------


def test_selecting_a_run_compares_it_against_the_previous_run_of_the_same_dut(
    qtbot, runs_root: Path, make_run_record, frozen_clock
) -> None:
    _lvs_run(runs_root, make_run_record, discrepancies=17)
    frozen_clock.tick(3600)
    _lvs_run(runs_root, make_run_record, discrepancies=3)

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    body = tab.result_card._lvs_body.text()
    assert "17 -> 3 (down 14)" in body


def test_the_oldest_run_of_a_dut_has_nothing_to_compare_against(
    qtbot, runs_root: Path, make_run_record
) -> None:
    _lvs_run(runs_root, make_run_record, discrepancies=17)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    assert "No earlier run of this cell" in tab.result_card._lvs_body.text()


# ---- annotations ------------------------------------------------------------


def test_rename_writes_annotations_and_never_touches_the_directory_or_record(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renaming is a label change, nothing more.

    The directory name is referenced by ``batches/*.json`` and
    ``parent_run_id`` and anchors every relative path inside the run, so it is
    immutable; ``run.json`` is the immutable record. Only
    ``annotations.json`` may move.
    """

    run_dir, record = _write_run(runs_root, make_run_record)
    record_bytes = run_paths(run_dir).record.read_bytes()

    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("golden run", True))
    )

    with qtbot.waitSignal(tab.annotations_saved, timeout=1000) as blocker:
        tab._rename_btn.click()

    assert blocker.args == [record.run_id]
    assert run_dir.is_dir(), "the run directory must keep its name"
    assert run_paths(run_dir).record.read_bytes() == record_bytes
    assert read_annotations(run_dir).display_name == "golden run"
    # The list picks the new label up on the refresh that follows the write.
    assert tab._tree.topLevelItem(0).text(0) == "golden run"
    assert tab.result_card._title.text() == "golden run"


def test_rename_to_blank_clears_the_override(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, record = _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("golden", True))
    )
    tab._rename_btn.click()
    assert read_annotations(run_dir).display_name == "golden"

    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("   ", True))
    )
    tab._rename_btn.click()
    assert read_annotations(run_dir).display_name is None
    assert tab._tree.topLevelItem(0).text(0) == record.default_display_name


def test_rename_cancelled_writes_nothing(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _ = _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("ignored", False))
    )
    tab._rename_btn.click()
    assert not run_paths(run_dir).annots.exists()


def test_note_is_written_and_shown_on_the_card(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, _ = _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *a, **k: ("re-run after the M3 fix", True)),
    )
    tab._note_btn.click()

    assert read_annotations(run_dir).note == "re-run after the M3 fix"
    assert tab.result_card._note.text() == "re-run after the M3 fix"


def test_star_toggles_and_relabels_the_button(
    qtbot, runs_root: Path, make_run_record
) -> None:
    run_dir, _ = _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    assert tab._star_btn.text() == "Star"
    tab._star_btn.click()
    assert read_annotations(run_dir).starred is True
    assert tab._star_btn.text() == "Unstar"
    assert tab._tree.topLevelItem(0).text(0).startswith("* ")

    tab._star_btn.click()
    assert read_annotations(run_dir).starred is False
    assert tab._star_btn.text() == "Star"


def test_annotation_buttons_are_disabled_without_a_selection(
    qtbot, runs_root: Path
) -> None:
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    for button in (tab._rename_btn, tab._note_btn, tab._star_btn):
        assert not button.isEnabled()


# ---- context menu -----------------------------------------------------------


def test_history_context_menu_is_deferred_and_lists_its_actions(
    qtbot, runs_root: Path, make_run_record
) -> None:
    """X11 fires the context-menu event on press; the popup must be deferred.

    A synchronous ``exec_()`` is dismissed by the following button release,
    which is the "right-click twice" bug. Patching ``exec_`` and pumping the
    loop is what makes the deferral observable.
    """

    _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    tab.show()

    captured: dict[str, object] = {}
    real_exec = runs_tab_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = [a.text() for a in self.actions() if a.text()]
        return None

    runs_tab_mod.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        item = tab._tree.topLevelItem(0)
        tab._on_tree_context_menu(tab._tree.visualItemRect(item).center())
        assert "actions" not in captured, "popup must not be synchronous"
        qtbot.wait(10)
    finally:
        runs_tab_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert captured["actions"] == [
        "Rename...",
        "Edit note...",
        "Star",
        "Open run directory",
        "Copy run id",
    ]


def test_history_context_menu_ignores_empty_space(
    qtbot, runs_root: Path, make_run_record
) -> None:
    from PyQt5.QtCore import QPoint

    _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    opened: list[object] = []
    real_exec = runs_tab_mod.QMenu.exec_
    runs_tab_mod.QMenu.exec_ = lambda self, *a, **k: opened.append(self)  # type: ignore
    try:
        tab._on_tree_context_menu(QPoint(5, 5000))
        qtbot.wait(10)
    finally:
        runs_tab_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]
    assert opened == []


# ---- card actions routed by the tab ----------------------------------------


def test_opening_a_stage_log_drives_the_embedded_viewer(
    qtbot, runs_root: Path, make_run_record
) -> None:
    run_dir, _ = _lvs_run(runs_root, make_run_record, discrepancies=3)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    log_path = run_dir / "logs" / "calibre.log"
    with qtbot.waitSignal(tab.log_requested, timeout=1000) as blocker:
        tab.result_card.log_requested.emit(log_path)

    assert blocker.args == [log_path]
    assert tab._log_tab._path == log_path
    assert "calibre.log" in tab._log_tab._header.text()


def test_handoff_request_goes_through_core_and_reports_the_outcome(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, record = _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    launched: list[object] = []

    def fake_launch(rec, *a, **k):
        launched.append(rec)
        return HandoffPlan(
            argv=("/opt/calibre", "-gui", "-lvs", "-runset", "x.qci"),
            cwd=Path("/wa"),
            env={},
            runset=Path("x.qci"),
            stage_key="calibre",
            executable="calibre",
            pid=4242,
        )

    shown: list[str] = []
    monkeypatch.setattr(runs_tab_mod, "launch_calibre_interactive", fake_launch)
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda parent, title, text, *a, **k: shown.append(text)),
    )

    tab._on_handoff_requested(record)

    assert launched and launched[0].run_id == record.run_id
    assert shown and "4242" in shown[0]


def test_handoff_refusal_is_surfaced_with_the_plan_reason(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, record = _write_run(runs_root, make_run_record)
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)

    monkeypatch.setattr(
        runs_tab_mod,
        "launch_calibre_interactive",
        lambda rec, *a, **k: HandoffPlan(
            argv=(),
            cwd=None,
            env={},
            runset=None,
            stage_key=None,
            executable="calibre",
            reasons=("Calibre was not found on PATH as 'calibre'.",),
        ),
    )
    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text, *a, **k: warned.append(text)),
    )

    tab._on_handoff_requested(record)
    assert warned == ["Calibre was not found on PATH as 'calibre'."]


def test_handoff_ignores_a_payload_that_is_not_a_record(
    qtbot, runs_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    called: list[object] = []
    monkeypatch.setattr(
        runs_tab_mod, "launch_calibre_interactive", lambda *a, **k: called.append(a)
    )
    tab._on_handoff_requested("not a record")
    assert called == []


def test_open_path_reports_a_missing_file_instead_of_raising(
    qtbot, runs_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tab = RunsTab(runs_root=runs_root)
    qtbot.addWidget(tab)
    warned: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda parent, title, text, *a, **k: warned.append(text)),
    )
    tab._open_path(tmp_path / "gone.dspf")
    assert warned and "no longer exists" in warned[0]


# ---- runs_root resolution ---------------------------------------------------


def test_runs_root_is_derived_from_the_controller(
    qtbot, tmp_path: Path, project_tools_config: Path
) -> None:
    ae_root = tmp_path / "pr"
    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunsTab(controller)
    qtbot.addWidget(tab)

    assert tab.runs_root == ae_root / "runs"
    # Nothing on disk yet -> the empty page, not an exception.
    assert tab._stack.currentWidget() is tab._empty


def test_loading_a_config_refreshes_the_history(
    qtbot, tmp_path: Path, project_tools_config: Path, make_run_record
) -> None:
    ae_root = tmp_path / "pr"
    runs = ae_root / "runs"
    runs.mkdir(parents=True)
    _write_run(runs, make_run_record)

    controller = ConfigController(auto_ext_root=ae_root, workarea=tmp_path / "wa")
    tab = RunsTab(controller)
    qtbot.addWidget(tab)
    assert len(tab.entries) == 1

    controller.load(project_tools_config)
    assert len(tab.entries) == 1
    assert tab._stack.currentWidget() is tab._splitter


def test_set_runs_root_reloads(
    qtbot, tmp_path: Path, make_run_record
) -> None:
    first = tmp_path / "runs_a"
    first.mkdir()
    second = tmp_path / "runs_b"
    second.mkdir()
    _write_run(second, make_run_record)

    tab = RunsTab(runs_root=first)
    qtbot.addWidget(tab)
    assert tab.entries == []

    tab.set_runs_root(second)
    assert len(tab.entries) == 1
    assert tab.runs_root == second
