"""Does every control a user can press actually *do* something?

The mirror image of ``tests/ui/test_reachability.py``
-----------------------------------------------------
Reachability asks whether every field a user owns has a control in front of
it. It is necessary and it is not sufficient, because the office round's next
complaint was the opposite shape:

    "I clicked the runset path -- it is drawn in the link colour and the
    cursor turns into a hand -- and nothing happened."

Nothing in the suite asked that question. 2594 tests passed while a styled,
cursor-hinted label had no receiver at all, while ``Refresh`` re-emitted the
string already on the status bar, and while a button labelled *Show 3
discrepancies* asked a scroll area with a range of zero to scroll.

So this module measures **effect**, in two families.

Family 1 is mechanical and dull, and deliberately so, in the same shape as
``RECIPE_UNREACHABLE``: every button, menu item and clickable label reachable
from a booted window either has at least one receiver on the signal a press
emits, or is named in :data:`AFFORDANCE_EXEMPT` **with a written reason**. The
reason is the load-bearing part. "Nobody connected this" and "this is read at
Run time rather than connected" look identical in the widget tree and are
completely different claims, and only one of them is a defect.

Family 2 is the part a receiver count cannot reach: a control can be perfectly
connected to a slot whose whole body is an early ``return`` in the state the
user is actually in. So for each degenerate state the ledger names -- no runs,
nothing selected, the report deleted, a scroll range of zero -- the real widget
is clicked and the test demands an observable change of one of exactly four
kinds:

1. an outward signal carrying a **real path** (not merely "a signal fired");
2. a dialog, which the autouse ``_no_unexpected_modal`` guard turns into a
   failure unless the test opts in and asserts its text;
3. a visible-widget change -- ``isVisible`` / ``isEnabled`` / ``text()`` /
   a ``QStackedWidget`` index, snapshotted before and after;
4. a scroll-position move, and **only** after asserting
   ``scrollbar.maximum() > 0``, so that a range-zero area -- the exact
   condition that makes ledger row M-26 a no-op -- can never pass a test by
   scrolling nowhere.

Tests that fail at HEAD because a ledger row is still open are
``xfail(strict=True)`` and name the row. When that row is fixed the xfail
turns into an unexpected pass and this file goes red, which is the point: the
ledger and the suite cannot drift apart silently.

``RunBar`` is deliberately **not** in family 2. ``auto_ext/ui/widgets/run_bar.py``
is being reworked in a parallel session, and a degenerate-state matrix pinned
to a widget that is mid-change would be pinning the wrong thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QAction,
    QMenu,
    QWidget,
)

from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.core.run_store import write_record  # noqa: E402
from auto_ext.model.run import (  # noqa: E402
    LvsResult,
    RunResults,
    StageRecord,
    allocate_run_dir,
)
from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.screens.recipes_screen import _ClickableFrame  # noqa: E402
from auto_ext.ui.shell import HealthBadge, NavButton  # noqa: E402
from auto_ext.ui.widgets.failure_chip import PathLabel  # noqa: E402
from auto_ext.ui.widgets.option_editor import PointerOptionEditor  # noqa: E402

#: ``(class, signal names)`` for everything a user can press. A class is here
#: because a *press* on it reaches code, not because Qt calls it a button: the
#: navigation rail, the health badge, the recipe cards and every path in the
#: app are labels and frames with a hand cursor.
#:
#: More than one signal per class, because one gesture on a checkbox is
#: ``clicked`` *and* ``toggled`` *and* ``stateChanged``, and the codebase
#: legitimately uses all three. Counting only the first would call every
#: checkbox on the form dead.
PRESSABLE: tuple[tuple[type, tuple[str, ...]], ...] = (
    (QAbstractButton, ("clicked", "toggled", "stateChanged")),
    (PathLabel, ("clicked",)),
    (NavButton, ("clicked",)),
    (HealthBadge, ("clicked",)),
    (_ClickableFrame, ("clicked",)),
    (PointerOptionEditor, ("navigate_requested",)),
)

#: Controls with no receiver on the signal a press emits, and why.
#:
#: Exactly like ``RECIPE_UNREACHABLE`` in ``test_reachability.py``: an entry
#: is a claim that a human looked and decided, and it is reviewable in a way
#: that "no test covers this" never was. Anything not listed here must have a
#: receiver. Reasons that start with "M-nn open" are **not** decisions -- they
#: are ledger rows whose fix will delete the entry.
AFFORDANCE_EXEMPT: dict[str, str] = {
    "result_card:_launch_line": (
        "by design: filled with set_placeholder, which leaves the path None, "
        "so the label stays in the secondary colour and keeps the arrow "
        "cursor. It shows the command line for reading, not for opening"
    ),
    "runs_screen:_detail_title": (
        "by design: set_placeholder only. The run's name is a heading that "
        "happens to be drawn by the eliding label, not a link"
    ),
    "runs_screen:_detail_meta": (
        "by design: set_placeholder only, and what it holds is the cell and "
        "the timestamp -- there is nothing for a press to open"
    ),
    "runs_screen:_log_title": (
        "by design: set_placeholder only, so it stays in the secondary colour "
        "and keeps the arrow cursor. It is the heading of the built-in log "
        "viewer and names the file whose text is in the pane directly below "
        "it -- there is nothing left for a press to reveal"
    ),
    "setup_drawer:SetupDrawer/PathLabel": (
        "by design: every check row uses set_placeholder to show what the "
        "check *saw*, which is frequently not a path at all ('(empty)', 'not "
        "on PATH'). The row's own Browse / Copy the fix buttons are the "
        "affordances"
    ),
    "run_bar:_dry_run": (
        "by design, and the noise-reduction rule this audit was written with: "
        "the box is read at Run time (cells_screen passes dry_run=... into the "
        "run request), so a receiver would be a second source of truth"
    ),
    "run_bar:_continue_on_lvs": (
        "by design: read at Run time exactly like dry run, and for the same "
        "reason -- the value belongs to the run request, not to a slot"
    ),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _lvs_run(runs_root: Path, make_run_record, *, discrepancies: int, **kw):
    """A finished run whose calibre stage failed with a discrepancy count."""

    run_dir = allocate_run_dir(runs_root, kw.pop("slug", "amp2-ext"))
    (run_dir / "logs" / "calibre.log").write_text("output\n", encoding="utf-8")
    (run_dir / "results").mkdir(exist_ok=True)
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
def window(v2_config_dir_multi: Path, isolated_recipe_path: Path, qtbot) -> MainWindow:
    """A real, fully wired window over the two-recipe / two-corner fixture.

    ``_multi`` rather than ``v2_config_dir``: two recipes make the recipe
    override combo, Duplicate/Delete and the per-row recipe column
    non-degenerate, and two corners give the Project screen's corner table
    more than one row to Remove. A control that offers the only possible
    answer and a control that offers nothing are the same widget.
    """

    built = MainWindow(
        config_dir=v2_config_dir_multi / "config", auto_ext_root=v2_config_dir_multi
    )
    qtbot.addWidget(built)
    built.show()
    qtbot.waitExposed(built)
    return built


@pytest.fixture
def history(runs_root: Path, make_run_record, frozen_clock) -> Path:
    """One failed run with three LVS discrepancies."""

    _lvs_run(runs_root, make_run_record, discrepancies=3, cell="amp2")
    return runs_root


@pytest.fixture
def runs(window: MainWindow, history: Path, qtbot):
    """The hosted Runs screen, showing one failed run."""

    screen = window.runs_screen
    window.shell.set_current_page("runs")
    screen.set_runs_root(history)
    qtbot.wait(1)
    assert screen.selected_entry is not None, "the screen must auto-select row 0"
    return screen


# ---------------------------------------------------------------------------
# Family 1 -- every control has a receiver, or a written reason
# ---------------------------------------------------------------------------


def _slot_name(obj: object) -> str:
    """The attribute name its owner keeps it under, e.g. ``_runset_line``.

    Stable across the line-number churn of four files that are in flux, and
    far more use to a reader than an ordinal would be: the exemption key
    ``result_card:_runset_line`` says which control it is, and a rename of
    that attribute is exactly the moment somebody should re-read the reason.
    """

    parent = obj.parent()  # type: ignore[attr-defined]
    while parent is not None:
        for name, value in vars(parent).items():
            if value is obj:
                return name
        parent = parent.parent()
    return ""


def _label(obj: object) -> str:
    getter = getattr(obj, "text", None)
    try:
        return str(getter() or "") if callable(getter) else ""
    except TypeError:  # pragma: no cover - text(int) overloads
        return ""


def _owner(obj: object) -> object | None:
    """The nearest ancestor this repo wrote -- ``obj`` itself excluded.

    A bare ``QCheckBox`` says only ``PyQt5.QtWidgets`` about itself, which
    names no file anyone can go and read, and a ``PathLabel`` says
    ``failure_chip`` -- the file it is *defined* in, which is never the file
    somebody has to open. What a reader needs is the widget that put it
    there, so the walk starts at the parent.
    """

    node = obj.parent()  # type: ignore[attr-defined]
    while node is not None:
        if type(node).__module__.startswith("auto_ext."):
            return node
        node = node.parent()
    return None


def affordance_key(obj: object) -> str:
    """``<module>:<name>`` -- stable across the churn of four in-flux files.

    Deliberately not a line number and not an ordinal. An attribute name
    survives a refactor that moves a widget two hundred lines; a rename of
    that attribute is exactly the moment someone should re-read the reason
    beside it.

    A button's or a menu item's own words are part of its identity, because a
    designer wrote them. A path label's are not -- they are whatever the
    project happens to hold -- so an unnamed one falls back to "which widget
    of which owner", which also collapses a repeated row into one entry.
    """

    from auto_ext.ui.widgets.option_editor import OptionEditor

    if isinstance(obj, OptionEditor):
        # A pointer row is itself the pressable thing; its catalog key is the
        # only name it has.
        return f"option_editor:{obj.key}"

    owner = _owner(obj)
    source = owner if owner is not None else obj
    module = type(source).__module__.rsplit(".", 1)[-1]

    if isinstance(owner, OptionEditor):
        # One catalog row can own several boxes ("stages" owns five), so the
        # option key alone is not enough; the box's own word completes it.
        text = _label(obj)
        return f"option_editor:{owner.key}" + (f"/{text}" if text else "")

    name = _slot_name(obj) or obj.objectName()  # type: ignore[attr-defined]
    if not name and isinstance(obj, (QAbstractButton, QAction)):
        name = _label(obj)
    if not name:
        name = f"{type(source).__name__}/{type(obj).__name__}"
    return f"{module}:{name}"


def _context_menus(window: MainWindow, qtbot) -> list[QMenu]:
    """Every context menu the screens build, without a mouse.

    All of them are popped up from a deferred ``QTimer.singleShot`` so that
    the first right-click works over X11 -- a synchronous ``exec_`` is
    dismissed by the release event, which is the "you have to right-click
    twice" bug. So the only way to read one is to call the slot with
    ``QMenu.exec_`` intercepted, and the wait has to happen *inside* the
    patch: restore first and the deferred popup opens a real modal menu with
    nobody to dismiss it.
    """

    built: list[QMenu] = []
    original = QMenu.exec_

    def _capture(self, *_args, **_kwargs):
        built.append(self)
        return None

    QMenu.exec_ = _capture  # type: ignore[assignment]
    try:
        for view, slot in (
            (window.cells_screen.table, window.cells_screen._on_context_menu),
            (window.runs_screen._list, window.runs_screen._on_list_menu),
            (
                window.runs_screen.result_card._stage_tree,
                window.runs_screen.result_card._on_stage_menu,
            ),
            (
                window.recipes_screen.recipe_list,
                window.recipes_screen._on_list_context_menu,
            ),
        ):
            model = view.model()
            if model is None or model.rowCount() == 0:
                continue
            slot(QPoint(view.visualRect(model.index(0, 0)).center()))
        qtbot.wait(10)
    finally:
        QMenu.exec_ = original  # type: ignore[assignment]
    return built


def affordances(
    window: MainWindow, qtbot
) -> dict[str, list[tuple[object, tuple[object, ...]]]]:
    """``key -> [(control, the signals a press can emit), ...]`` for the window.

    A list rather than one entry: a repeating row -- the setup drawer builds
    one per failed check -- collapses onto a single key, and a key is only
    healthy when *every* instance behind it is.
    """

    found: dict[str, list[tuple[object, tuple[object, ...]]]] = {}

    def add(obj: object, signal_names: tuple[str, ...]) -> None:
        if str(obj.objectName()).startswith("qt_"):  # type: ignore[attr-defined]
            return  # Qt's own furniture, e.g. the menu-bar overflow button
        getter = getattr(obj, "menu", None)
        if callable(getter) and getter() is not None:
            # A button that owns a menu cannot be dead: pressing it opens the
            # menu, and the menu's own items are audited separately. That is a
            # property Qt guarantees rather than a judgement about one
            # control, so it is a rule here and not an AFFORDANCE_EXEMPT row.
            return
        signals = tuple(
            signal
            for signal in (getattr(obj, name, None) for name in signal_names)
            if signal is not None
        )
        if not signals:  # pragma: no cover - every entry has at least one
            return
        found.setdefault(affordance_key(obj), []).append((obj, signals))

    for widget in window.findChildren(QWidget):
        for cls, signal_names in PRESSABLE:
            if isinstance(widget, cls):
                add(widget, signal_names)
                break

    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is None:  # pragma: no cover - both top items own a menu
            continue
        for action in menu.actions():
            if not action.isSeparator():
                add(action, ("triggered",))

    for menu in _context_menus(window, qtbot):
        for action in menu.actions():
            if not action.isSeparator():
                add(action, ("triggered",))

    return found


def _is_dead(pair: tuple[object, tuple[object, ...]]) -> bool:
    """True when not one of the signals a press emits has a receiver."""

    obj, signals = pair
    return all(obj.receivers(signal) == 0 for signal in signals)


def test_every_control_has_a_receiver_or_a_written_reason(
    window: MainWindow, qtbot
) -> None:
    """The audit. A press that reaches no code is a lie the pixels tell.

    ``PathLabel`` is the shape that started this: it paints itself in the
    accent colour and sets a pointing-hand cursor whenever it holds a path, so
    the *styling* is driven by whether a path exists and not by whether anyone
    is listening. Two of them in one band, one wired and one not, look
    identical to a user.
    """

    dead = sorted(
        key
        for key, instances in affordances(window, qtbot).items()
        if key not in AFFORDANCE_EXEMPT and any(_is_dead(pair) for pair in instances)
    )
    assert dead == [], (
        "these controls reach no code when pressed. Connect them, or add each "
        "to AFFORDANCE_EXEMPT with a reason -- a control that looks live and "
        "is not is the defect this file exists for:\n  " + "\n  ".join(dead)
    )


def test_every_affordance_exemption_carries_a_reason() -> None:
    """The exemption set is only worth anything if the reasons are real."""

    blank = [key for key, why in AFFORDANCE_EXEMPT.items() if len(why.strip()) < 20]
    assert blank == [], f"AFFORDANCE_EXEMPT: these have no real reason: {blank}"


def test_no_exemption_names_a_control_that_is_gone(window: MainWindow, qtbot) -> None:
    """The other direction: a stale exemption hides the next dead control.

    An entry whose control no longer exists is worse than no entry, because
    the next person to add a control with that key inherits somebody else's
    decision without knowing it.
    """

    stale = sorted(set(AFFORDANCE_EXEMPT) - set(affordances(window, qtbot)))
    assert stale == [], f"AFFORDANCE_EXEMPT names controls the window has not: {stale}"


# ---------------------------------------------------------------------------
# Family 2 -- pressing the real control in the state the ledger names
# ---------------------------------------------------------------------------
#
# ``RunBar`` is deliberately absent from this family. ``run_bar.py`` is being
# reworked in a parallel session; a degenerate-state matrix pinned to a widget
# that is mid-change pins the wrong thing, and the run bar's own controls are
# still covered by family 1 above.


def visual_state(root: QWidget) -> tuple:
    """Everything a user could see, snapshotted for a before/after compare.

    Text, visibility, enabled-ness and the style sheet, because the minimal
    fix for several of the rows below is a transient highlight -- and a test
    that only watched ``isVisible`` would call that fix "no change" too.
    """

    from PyQt5.QtWidgets import QAbstractScrollArea

    # Positional, never ``id()``: PyQt hands out a fresh Python wrapper for
    # the same C++ widget on a later call, so an id-keyed snapshot compares
    # unequal to itself and every press looks like it did something.
    widgets = tuple(
        (
            index,
            type(w).__name__,
            w.isVisible(),
            w.isEnabled(),
            _label(w),
            w.styleSheet(),
        )
        for index, w in enumerate(root.findChildren(QWidget))
    )
    scrolls = tuple(
        (index, a.verticalScrollBar().value(), a.verticalScrollBar().maximum())
        for index, a in enumerate(root.findChildren(QAbstractScrollArea))
    )
    return widgets + scrolls


def button(root: QWidget, needle: str) -> QAbstractButton:
    """The one visible button whose words contain ``needle``."""

    found = [
        b
        for b in root.findChildren(QAbstractButton)
        if needle in b.text() and b.isVisible()
    ]
    assert len(found) == 1, f"{needle!r} matched {[b.text() for b in found]}"
    return found[0]


@pytest.fixture
def card(runs):
    """The result card of the hosted Runs screen, showing the failed run."""

    return runs.result_card


# M-26 is FIXED: show_lvs_detail keeps the scroll for the case where it helps
# and additionally washes the LVS band in the accent tint for LVS_HIGHLIGHT_MS,
# so the destination is visible on a card with a scroll range of zero too.
def test_show_discrepancies_does_something_when_the_card_cannot_scroll(
    card, qtbot
) -> None:
    """The degenerate state is the common one: a card that fits its window.

    The precondition is asserted, not assumed. A test that pressed the button
    and looked at the scroll bar without first pinning ``maximum() == 0``
    would be measuring a different situation entirely.
    """

    bar = card._scroll.verticalScrollBar()
    assert bar.maximum() == 0, "this test is about the card that has nothing to scroll"

    press = button(card, "discrepancies")
    assert press.isEnabled(), "a lit button is the whole point of the complaint"
    before = visual_state(card)
    qtbot.mouseClick(press, Qt.LeftButton)
    qtbot.wait(10)
    assert visual_state(card) != before, (
        "the button says 'Show 3 discrepancies' and pressing it changed "
        "nothing a user can see"
    )


def test_show_discrepancies_brings_the_band_back_when_there_is_range(
    card, window, qtbot
) -> None:
    """The state it *does* work in, pinned so a fix cannot break it.

    Scrolled to the bottom, with a real range, the press has somewhere to go
    and the view must move.
    """

    window.resize(940, 560)
    qtbot.wait(20)
    bar = card._scroll.verticalScrollBar()
    if bar.maximum() == 0:
        pytest.skip("the card still fits; this test is about the scrolled case")

    bar.setValue(bar.maximum())
    qtbot.wait(1)
    was = bar.value()
    qtbot.mouseClick(button(card, "discrepancies"), Qt.LeftButton)
    qtbot.wait(10)
    assert bar.value() != was, "the view did not move towards the LVS band"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "M-51 open: refresh() re-lists and re-emits status_text(), which is "
        "the string already on the bar, so a re-read of an unchanged "
        "directory produces no pixel at all"
    ),
)
def test_refresh_says_something_new_when_the_directory_has_not_changed(
    runs, window, qtbot
) -> None:
    """Pressing Refresh twice must be distinguishable from pressing it once."""

    press = button(runs, "Refresh")
    qtbot.mouseClick(press, Qt.LeftButton)
    qtbot.wait(10)
    first = window.shell.status_left()
    qtbot.mouseClick(press, Qt.LeftButton)
    qtbot.wait(10)
    assert window.shell.status_left() != first, (
        "the status line says the same words after the second Refresh, so the "
        "user cannot tell whether the button did anything"
    )


# M-20 is FIXED: RunsScreen now asks _delegated whether the host is listening
# before it opens anything itself, so one press is one editor.
def test_one_press_on_a_log_launches_exactly_one_editor(
    card, monkeypatch: pytest.MonkeyPatch, qtbot
) -> None:
    """The counter has to be shared, because the two launches are in two files.

    Every existing test builds a bare ``RunsScreen`` and patches ``open_in_os``
    in that module alone, which is precisely the arrangement in which the
    second launch is invisible.
    """

    opened: list[Path] = []
    for module in ("auto_ext.ui.main_window", "auto_ext.ui.screens.runs_screen"):
        monkeypatch.setattr(f"{module}.open_in_os", opened.append)

    qtbot.mouseClick(button(card, "Open calibre.log"), Qt.LeftButton)
    qtbot.wait(10)

    assert opened, "the press reached no launcher at all"
    assert opened[0].exists(), "a signal fired, but not with a path that is there"
    assert len(opened) == 1, f"one press, {len(opened)} editors: {opened}"


def test_open_the_lvs_report_is_disabled_and_says_why_when_it_is_missing(
    card,
) -> None:
    """A refusal a user can see beats a press that quietly does nothing.

    The fixture run has no ``results/lvs.report``, which is the ordinary state
    of a run whose calibre stage died before writing one.
    """

    press = button(card, "Open LVS report")
    assert not press.isEnabled()
    assert press.toolTip().strip(), (
        "a greyed button with no tooltip is indistinguishable from a broken one"
    )


def test_re_run_is_disabled_when_there_is_no_run_to_re_run(runs, qtbot) -> None:
    """The empty-history state, which is what a fresh project opens in."""

    runs.set_entries([])
    qtbot.wait(1)
    assert runs.selected_entry is None
    assert not button(runs, "Re-run this cell").isEnabled()


def test_choosing_a_run_swaps_the_empty_pane_for_the_card(runs, qtbot) -> None:
    """The ``QStackedWidget`` class of evidence, on a control that has it.

    Included because family 2 is only honest if it also pins the cases that
    *do* announce themselves -- otherwise the fix for a broken one could
    quietly break a working one and nothing would say so.
    """

    runs.set_entries([])
    qtbot.wait(1)
    empty = runs._stack.currentIndex()
    assert runs._stack.currentWidget() is runs._empty

    runs.refresh()
    qtbot.wait(1)
    assert runs.selected_entry is not None
    assert runs._stack.currentIndex() != empty, "the pane never became the card"
    assert runs.result_card.isVisible()


# M-52 is FIXED: _runset_line.clicked is connected to artifact_requested, the
# same receiver the artifact grid's identical widget already had. Its
# AFFORDANCE_EXEMPT entry is gone with it -- deleting that entry was the fix
# the entry itself named.
def test_the_runset_path_opens_the_file_it_points_at(card, tmp_path, qtbot) -> None:
    """A press on a link-coloured path must carry a path that is really there.

    The path is planted through ``PathLabel``'s own public API rather than by
    building a hand-off plan: a plan needs a PDK tree, a deck and a runset on
    disk, and none of that is what this test is about. What is under test is
    the wiring behind a label the app has already decided to draw as live.
    """

    runset = tmp_path / "frozen.runset"
    runset.write_text("*lvsRunset\n", encoding="utf-8")
    label = card._runset_line
    label.set_path(runset, text=str(runset))
    card._runset_row.setVisible(True)
    qtbot.wait(1)
    assert label.is_live(), "the label is drawing itself as clickable"

    with qtbot.waitSignal(card.artifact_requested, timeout=500) as blocker:
        qtbot.mouseClick(label, Qt.LeftButton)
    assert Path(blocker.args[0]).exists()


# M-62 is FIXED: _on_list_menu makes the row under the cursor the selected row
# before it builds the menu, the way CellsScreen already did.
def test_the_context_menu_acts_on_the_row_that_was_right_clicked(
    window, runs_root: Path, make_run_record, frozen_clock, qtbot
) -> None:
    """Right-clicking row B while row A is selected must act on B.

    The Cells screen already does the right thing -- it selects the row under
    the cursor before it builds the menu -- so this is an inconsistency
    between two screens as much as it is a defect in one.
    """

    _lvs_run(runs_root, make_run_record, discrepancies=17, cell="amp2", slug="amp2-ext")
    frozen_clock.tick(3600)
    _lvs_run(runs_root, make_run_record, discrepancies=3, cell="bias", slug="bias-ext")
    screen = window.runs_screen
    window.shell.set_current_page("runs")
    screen.set_runs_root(runs_root)
    qtbot.wait(5)
    assert len(screen.visible_entries) == 2

    screen._list.setCurrentRow(0)
    qtbot.wait(1)
    selected = screen.selected_entry
    other = screen.visible_entries[1]
    assert selected is not None and selected.run_id != other.run_id

    fired: list[object] = []
    screen.rerun_requested.connect(fired.append)
    for menu in _context_menus_for(screen, screen._list, 1, qtbot):
        for action in menu.actions():
            if action.text() == "Re-run this cell":
                action.trigger()
    qtbot.wait(5)

    assert fired and fired[0].run_id == other.run_id
    assert screen.selected_entry is not None
    assert screen.selected_entry.run_id == other.run_id, (
        "the menu queued one run while the card kept showing another"
    )


def _context_menus_for(owner, view, row: int, qtbot) -> list[QMenu]:
    """Build the context menu of one row, intercepting the deferred popup."""

    built: list[QMenu] = []
    original = QMenu.exec_

    def _capture(self, *_args, **_kwargs):
        built.append(self)
        return None

    QMenu.exec_ = _capture  # type: ignore[assignment]
    try:
        index = view.model().index(row, 0)
        owner._on_list_menu(QPoint(view.visualRect(index).center()))
        qtbot.wait(10)
    finally:
        QMenu.exec_ = original  # type: ignore[assignment]
    return built
