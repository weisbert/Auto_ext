"""Tests for :mod:`auto_ext.ui.screens.runs_screen`.

The screen is a reader over an immutable run store, so the assertions fall
into four groups: it must never be taken down by one bad directory, it must
render the run the user picked, it must ask the host for anything it cannot
do itself, and it must not contribute a minimum size the 940x560 window floor
cannot pay.

The context-menu test is the load-bearing one for X11: the popup has to be
deferred by one event-loop tick or the user has to right-click twice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QPoint  # noqa: E402
from PyQt5.QtWidgets import QInputDialog  # noqa: E402

from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.core.run_store import (  # noqa: E402
    list_runs,
    read_annotations,
    write_record,
)
from auto_ext.model.run import (  # noqa: E402
    LvsResult,
    RunResults,
    StageRecord,
    allocate_run_dir,
    run_paths,
)
from auto_ext.ui.screens import runs_screen as rs  # noqa: E402
from auto_ext.ui.screens.runs_screen import RunsScreen  # noqa: E402

#: The whole window floor is 940x560; one screen must fit well inside it.
MIN_WIDTH_BUDGET = 700
MIN_HEIGHT_BUDGET = 400


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
            lvs=LvsResult(passed=False, banner="INCORRECT", discrepancies=discrepancies)
        ),
        **kw,
    )
    write_record(run_dir, record)
    return run_dir, record


@pytest.fixture
def history(runs_root: Path, make_run_record, frozen_clock):
    """Three runs of two cells: amp2 failed twice, bias passed once."""

    _lvs_run(runs_root, make_run_record, discrepancies=17, cell="amp2", slug="amp2-ext")
    frozen_clock.tick(3600)
    _write_run(
        runs_root,
        make_run_record,
        cell="bias",
        slug="bias-ext",
        overall=TaskStatus.PASSED,
        stages=[StageRecord(key="si", stage="si", status=StageStatus.PASSED)],
    )
    frozen_clock.tick(3600)
    _lvs_run(runs_root, make_run_record, discrepancies=3, cell="amp2", slug="amp2-ext")
    return runs_root


# ============================================================================
# pure helpers
# ============================================================================


def test_result_text_states_the_verdict_and_the_number_that_matters(
    runs_root: Path, make_run_record
) -> None:
    _lvs_run(runs_root, make_run_record, discrepancies=3)
    entry = list_runs(runs_root)[0]
    text, color = rs.result_text(entry)
    assert "failed" in text
    assert "D=3" in text
    from auto_ext.ui import theme

    assert color == theme.STATUS_FAILED
    assert color not in theme.accent_colors()


def test_result_text_never_invents_a_zero(runs_root: Path, make_run_record) -> None:
    _write_run(runs_root, make_run_record, overall=TaskStatus.PASSED)
    entry = list_runs(runs_root)[0]
    text, _ = rs.result_text(entry)
    assert "D=" not in text
    assert "passed" in text


# ============================================================================
# layout budget
# ============================================================================


def test_runs_screen_min_size_stays_within_budget_when_empty(
    qtbot, runs_root: Path
) -> None:
    screen = RunsScreen(runs_root=runs_root)
    qtbot.addWidget(screen)
    hint = screen.minimumSizeHint()
    assert hint.width() <= MIN_WIDTH_BUDGET
    assert hint.height() <= MIN_HEIGHT_BUDGET


def test_runs_screen_min_size_stays_within_budget_when_full(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    hint = screen.minimumSizeHint()
    assert hint.width() <= MIN_WIDTH_BUDGET
    assert hint.height() <= MIN_HEIGHT_BUDGET


def test_runs_screen_layout_minimum_matches_the_hint(qtbot, history) -> None:
    """A capped hint is worthless if the layout's own minimum is bigger."""

    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    minimum = screen.layout().minimumSize()
    assert minimum.width() <= MIN_WIDTH_BUDGET
    assert minimum.height() <= MIN_HEIGHT_BUDGET


# ============================================================================
# listing
# ============================================================================


def test_empty_history_shows_one_line_naming_the_runs_dir(
    qtbot, runs_root: Path
) -> None:
    screen = RunsScreen(runs_root=runs_root)
    qtbot.addWidget(screen)
    assert screen._stack.currentWidget() is screen._empty
    assert str(runs_root) in screen._empty.text()
    assert screen.result_card.record is None
    assert screen.status_text() == "no runs recorded yet"


def test_history_lists_newest_first_and_selects_the_newest(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    assert len(screen.entries) == 3
    assert screen._list.count() == 3
    newest = screen.entries[0]
    assert screen.selected_entry is not None
    assert screen.selected_entry.run_id == newest.run_id
    assert screen.result_card.record is not None
    assert screen.result_card.record.run_id == newest.run_id
    assert "3 runs kept" in screen.status_text()
    assert "nothing is ever overwritten" in screen.status_text()


def test_one_unreadable_directory_does_not_empty_the_history(
    qtbot, history
) -> None:
    broken = history / "20260101T000000Z_broken"
    broken.mkdir()
    (broken / "run.json").write_text("{ not json", encoding="utf-8")
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    assert len(screen.entries) == 3


def test_a_record_that_will_not_validate_reports_itself_on_the_card(
    qtbot, runs_root: Path, make_run_record
) -> None:
    """``list_runs`` indexes without validating; ``read_record`` may still fail.

    A future schema version is the honest case: the row stays listed, so the
    run is not invisible, and the card explains why it cannot be rendered.
    """

    import json

    run_dir, record = _write_run(runs_root, make_run_record)
    record_path = run_paths(run_dir).record
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    record_path.write_text(json.dumps(data), encoding="utf-8")

    screen = RunsScreen(runs_root=runs_root)
    qtbot.addWidget(screen)
    assert len(screen.entries) == 1
    assert screen.result_card.record is None
    assert record.run_id in screen.result_card._placeholder.text()
    assert screen.result_card._body.isHidden()
    # The header still names the run and marks it unreadable.
    assert screen._tally_chip.text() == "unreadable"


def test_cell_filter_narrows_the_list(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    assert [c for c in ("amp2", "bias")] == sorted(
        {e.cell for e in screen.entries}
    )

    screen.set_cell_filter("bias")
    assert [e.cell for e in screen.visible_entries] == ["bias"]
    assert screen._list.count() == 1

    screen.set_cell_filter(rs.ALL_CELLS)
    assert screen._list.count() == 3


def test_failures_only_hides_the_runs_that_passed(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    screen.set_failures_only(True)
    assert screen._list.count() == 2
    assert all(e.overall == "failed" for e in screen.visible_entries)
    assert "bias" not in {e.cell for e in screen.visible_entries}


def test_a_filter_that_matches_nothing_shows_the_empty_pane(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    screen.set_cell_filter("bias")
    screen.set_failures_only(True)
    assert screen.visible_entries == []
    assert screen._stack.currentWidget() is screen._empty
    assert "filter" in screen._empty.text()
    assert screen.result_card.record is None


def test_set_entries_injects_a_listing_without_touching_disk(
    qtbot, history
) -> None:
    entries = list_runs(history)
    screen = RunsScreen()
    qtbot.addWidget(screen)
    assert screen.runs_root is None
    screen.set_entries(entries)
    assert screen._list.count() == 3
    assert screen.selected_entry is not None


# ============================================================================
# selection -> card
# ============================================================================


def test_selecting_a_run_compares_it_against_the_previous_run_of_the_dut(
    qtbot, history
) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    # Newest is the second amp2 run, whose predecessor reported 17.
    delta = screen.result_card.discrepancy_delta
    assert delta is not None
    assert (delta.previous, delta.current, delta.delta) == (17, 3, -14)
    assert "17 -> 3 (down 14)" in screen.result_card._lvs_body.text()


def test_run_selected_signal_carries_the_index_entry(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.run_selected, timeout=1000) as blocker:
        screen._list.setCurrentRow(1)
    assert blocker.args[0].cell == "bias"


def test_the_detail_header_states_the_run_and_its_tally(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    entry = screen.selected_entry
    assert entry is not None
    assert screen._detail_title.full_text() == entry.display_name
    assert entry.run_id in screen._detail_meta.full_text()
    assert screen._tally_chip.isVisibleTo(screen)
    assert "stages passed" in screen._tally_chip.text()


# ============================================================================
# what the screen asks the host to do
# ============================================================================


def test_rerun_is_a_request_and_carries_the_index_entry(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.rerun_requested, timeout=1000) as blocker:
        screen._rerun_btn.click()
    assert blocker.args[0].run_id == screen.entries[0].run_id


def test_the_card_rerun_button_reaches_the_screen_signal(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.rerun_requested, timeout=1000) as blocker:
        screen.result_card._rerun_btn.click()
    assert blocker.args[0].run_id == screen.entries[0].run_id


def test_a_config_failure_sends_the_user_to_setup(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    with qtbot.waitSignal(screen.setup_requested, timeout=1000) as blocker:
        screen.result_card.setup_requested.emit("Paths")
    assert blocker.args == ["Paths"]


def test_handoff_is_launched_only_when_no_host_took_the_signal(
    qtbot, history, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: list[object] = []
    monkeypatch.setattr(
        rs,
        "launch_calibre_interactive",
        lambda record, *a, **k: launched.append(record),
    )
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    record = screen.result_card.record
    assert record is not None

    # A host is listening -> the screen must not launch anything itself.
    seen: list[object] = []
    screen.handoff_requested.connect(seen.append)
    screen._on_handoff_requested(record)
    assert launched == []
    assert [r.run_id for r in seen] == [record.run_id]


def test_handoff_reports_a_refused_plan_instead_of_launching(
    qtbot, history, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_ext.core.handoff import HandoffPlan

    warned: list[tuple] = []
    monkeypatch.setattr(
        rs,
        "launch_calibre_interactive",
        lambda record, *a, **k: HandoffPlan(
            argv=(),
            cwd=None,
            env={},
            runset=None,
            stage_key=None,
            executable="calibre",
            reasons=("Calibre was not found on PATH.",),
        ),
    )
    monkeypatch.setattr(
        rs.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a))
    )
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    record = screen.result_card.record
    assert record is not None
    screen._on_handoff_requested(record)
    assert warned and "Calibre was not found on PATH." in warned[0][2]


def test_opening_a_log_goes_through_os_open_and_is_announced(
    qtbot, history, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[Path] = []
    monkeypatch.setattr(rs, "open_in_os", opened.append)
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    target = screen.entries[0].run_dir / "logs" / "calibre.log"
    with qtbot.waitSignal(screen.log_requested, timeout=1000) as blocker:
        screen.result_card.log_requested.emit(target)
    assert opened == [target]
    assert blocker.args == [target]


def test_clicking_an_artifact_path_opens_it_with_the_os_handler(
    qtbot, history, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The card only names the path; the screen is what opens it."""

    from auto_ext.ui.widgets.failure_chip import PathLabel

    opened: list[Path] = []
    monkeypatch.setattr(rs, "open_in_os", opened.append)
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)

    live = [
        label
        for label in screen.result_card._artifacts_group.findChildren(PathLabel)
        if label.is_live()
    ]
    assert live, "at least the run directory always exists"
    with qtbot.waitSignal(screen.artifact_requested, timeout=1000) as blocker:
        live[0].clicked.emit(live[0].path)
    assert opened == [live[0].path]
    assert blocker.args == [live[0].path]


def test_a_missing_path_is_reported_not_raised(
    qtbot, history, monkeypatch: pytest.MonkeyPatch
) -> None:
    warned: list[tuple] = []

    def boom(_path):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(rs, "open_in_os", boom)
    monkeypatch.setattr(
        rs.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a))
    )
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    screen._open_path(history / "no-such-file")
    assert warned and "no longer exists" in warned[0][2]


# ============================================================================
# annotations
# ============================================================================


def test_rename_writes_only_annotations_and_keeps_the_directory(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir, record = _write_run(runs_root, make_run_record)
    record_bytes = run_paths(run_dir).record.read_bytes()

    screen = RunsScreen(runs_root=runs_root)
    qtbot.addWidget(screen)
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("golden run", True))
    )
    with qtbot.waitSignal(screen.annotations_saved, timeout=1000) as blocker:
        screen._rename(screen.entries[0])

    assert blocker.args == [record.run_id]
    assert run_dir.is_dir(), "the run directory must keep its name"
    assert run_paths(run_dir).record.read_bytes() == record_bytes
    assert read_annotations(run_dir).display_name == "golden run"
    assert screen.result_card._title.text() == "golden run"


def test_star_toggles_and_survives_a_refresh(
    qtbot, runs_root: Path, make_run_record
) -> None:
    run_dir, _ = _write_run(runs_root, make_run_record)
    screen = RunsScreen(runs_root=runs_root)
    qtbot.addWidget(screen)
    screen._toggle_star(screen.entries[0])
    assert read_annotations(run_dir).starred is True
    assert screen.entries[0].starred is True
    assert screen._list.item(0).text().startswith("* ")


def test_note_shows_up_on_the_card(
    qtbot, runs_root: Path, make_run_record, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_run(runs_root, make_run_record)
    screen = RunsScreen(runs_root=runs_root)
    qtbot.addWidget(screen)
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *a, **k: ("re-run after the M3 fix", True)),
    )
    screen._edit_note(screen.entries[0])
    assert screen.result_card._note.text() == "re-run after the M3 fix"


# ============================================================================
# context menu
# ============================================================================


def test_run_list_context_menu_is_deferred_by_one_tick(qtbot, history) -> None:
    """X11 delivers the menu event on press; a synchronous exec_ is dismissed
    by the release, which is the "you have to right-click twice" bug."""

    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    screen.show()

    captured: dict[str, object] = {}
    real_exec = rs.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = [a.text() for a in self.actions() if a.text()]
        return None

    rs.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        pos = screen._list.visualItemRect(screen._list.item(0)).center()
        screen._on_list_menu(pos)
        assert "actions" not in captured, "the popup must not be synchronous"
        qtbot.wait(10)
    finally:
        rs.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert captured["actions"] == [
        "Rename...",
        "Edit note...",
        "Star",
        "Open run directory",
        "Copy run id",
        "Re-run this cell",
    ]


def test_run_list_context_menu_ignores_empty_space(qtbot, history) -> None:
    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    opened: list[object] = []
    real_exec = rs.QMenu.exec_
    rs.QMenu.exec_ = lambda self, *a, **k: opened.append(self)  # type: ignore
    try:
        screen._on_list_menu(QPoint(5, 5000))
        qtbot.wait(10)
    finally:
        rs.QMenu.exec_ = real_exec  # type: ignore[method-assign]
    assert opened == []


# ============================================================================
# design constraints
# ============================================================================

#: Every non-ASCII character the design allows. The ten from the project
#: glyph whitelist, plus the horizontal ellipsis, which is
#: ``theme.STATUS_GLYPH["dry_run"]`` and also the character Qt itself inserts
#: when a label elides.
ALLOWED_GLYPHS = set("\u2713\u2717\u25b6\u25a0\u2013\u00b7\u21c6\u25be\u25b4\u25bc\u2026")


def _widgets(root):
    from PyQt5.QtWidgets import QWidget

    yield root
    for child in root.findChildren(QWidget):
        yield child


def _font_sizes(sheet: str) -> list[int]:
    import re

    return [int(m) for m in re.findall(r"font-size:\s*(\d+)px", sheet)]


def test_no_rendered_text_uses_a_glyph_outside_the_whitelist(qtbot, history) -> None:
    """DejaVu on CentOS 7 has these; anything else risks a tofu box."""

    from PyQt5.QtWidgets import QAbstractButton, QLabel

    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    offenders: list[str] = []
    for widget in _widgets(screen):
        if not isinstance(widget, (QLabel, QAbstractButton)):
            continue
        for char in widget.text():
            if ord(char) > 127 and char not in ALLOWED_GLYPHS:
                offenders.append(f"{type(widget).__name__}: {widget.text()!r}")
                break
    assert offenders == []


def test_no_stylesheet_goes_below_the_11px_floor(qtbot, history) -> None:
    from auto_ext.ui import theme

    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    for widget in _widgets(screen):
        for size in _font_sizes(widget.styleSheet()):
            assert size >= theme.FONT_SIZE_MIN, (
                f"{type(widget).__name__} sets font-size: {size}px"
            )


def test_no_animation_gradient_or_shadow_anywhere(qtbot, history) -> None:
    """X11 forwarding: a large repaint is the thing that stutters."""

    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    for widget in _widgets(screen):
        sheet = widget.styleSheet().lower()
        for banned in ("gradient", "box-shadow", "animation", "transition"):
            assert banned not in sheet, f"{type(widget).__name__}: {banned}"


def test_corner_radii_are_zero_or_the_button_two(qtbot, history) -> None:
    import re

    from auto_ext.ui import theme

    screen = RunsScreen(runs_root=history)
    qtbot.addWidget(screen)
    allowed = {theme.RADIUS, theme.RADIUS_BUTTON}
    for widget in _widgets(screen):
        radii = re.findall(r"border-radius:\s*(\d+)px", widget.styleSheet())
        assert {int(r) for r in radii} <= allowed, type(widget).__name__
