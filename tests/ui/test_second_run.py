"""The second Run -- R-1..R-6 of the 2026-09-04 user report.

"点过一次 Run 之后，新增/编辑再点 Run 就做不到." Every defect the report
names lives on the *second* press, and the suite had no second press
anywhere: ``test_cells_screen.py`` starts a run, drives the reporter and
stops; ``test_run_bar.py`` clicks Run once; ``test_journeys.py``'s two
run-shaped journeys monkeypatch the run away. A run that is never retired
and re-started cannot show what the screen kept from the first one.

So this file is the rule ``tests/support/v2.py`` grew for it -- "anything the
production code holds state across must be driven twice, and the second time
must differ from the first" -- applied through :func:`tests.support.v2.twice`
and :func:`tests.support.v2.run_twice`. Nothing here calls ``start_run()``:
the click is a real ``QPushButton.click()``, so a screen that refuses the
second run by hiding or disabling the control fails these tests the way the
user meets it rather than passing because the test bypassed the widget.

Master rows: M-129 (R-1), M-130 (R-2), M-131 (R-3), M-132 (R-4), M-133
(R-5), M-134 (R-6).

**What the fix was.** The rulers landed first as strict ``xfail``\\ s and the
2026-09-05 change flipped them. The answer to R-1 was not "disable the Run
button with a reason on it" but the owner's own: "跑了一个仿真之后，用户还
能改什么东西，然后再继续扔仿真." There is no running state to be locked out
of any more. The table, the checkboxes, the toolbar and the context menu stay
live for the whole run, and Run means *enqueue a job* -- press it again and a
second job waits behind the first, resolved against the table as it looked
when the button went down. The two R-1 tests below were rewritten to that
semantics; the rest kept their assertions and lost their markers.

The run set is the check column, not the highlight (see
``cells_screen.py``'s module docstring), so the gestures here tick boxes:
:func:`_tick_row` clicks the box the delegate hit-tests, and
:func:`_select_row` clicks the cell name when a test needs the highlight
instead.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5 import sip  # noqa: E402
from PyQt5.QtCore import QObject, Qt, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QMessageBox,
    QStyle,
    QStyleOptionViewItem,
)

from auto_ext.model.cells import CellBook, CellEntry  # noqa: E402
from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.screens import cells_screen as cells_mod  # noqa: E402
from auto_ext.ui.screens.cells_screen import (  # noqa: E402
    COL_CELL,
    COL_CHECK,
    COL_OUT_VIEW,
    CellsScreen,
)
from tests.support.v2 import run_twice, twice  # noqa: E402

#: The two-recipe library the stub controller holds. Two, not one: with a
#: single recipe ``run_recipe(None)`` answers with it and R-3's refusal --
#: which is the whole subject of one test here -- is unreachable.
RECIPES = [("rc-default", "RC default"), ("rc-fast", "RC fast")]


# ---- the stand-ins ---------------------------------------------------------


class FakeWorker(QObject):
    """The surface ``CellsScreen`` touches on a ``RunWorker``, and no thread.

    A local copy rather than an import from ``test_cells_screen.py``: that
    file is in flux in another session, and a ruler that breaks when the
    thing it measures is edited is not a ruler.
    """

    error = pyqtSignal(str)
    finished = pyqtSignal()
    instances: list["FakeWorker"] = []

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.started = False
        self.cancelled = False
        self.summary = SimpleNamespace(runs=[])
        FakeWorker.instances.append(self)

    def start(self) -> None:
        self.started = True

    def request_cancel(self) -> None:
        self.cancelled = True

    def wait(self, _timeout_ms: int = 0) -> bool:
        return True


@pytest.fixture
def workers(monkeypatch) -> list[FakeWorker]:
    """Every worker the screen built, oldest first."""

    FakeWorker.instances = []
    monkeypatch.setattr(cells_mod, "RunWorker", FakeWorker)
    # The press-time pre-flight is the runner's own check, and the recipes in
    # this file's fake controller are stand-ins it cannot read. What it
    # refuses is asserted where the controller is real (test_failure_journeys).
    monkeypatch.setattr(cells_mod, "preflight", lambda *a, **k: None)
    return FakeWorker.instances


def _book() -> CellBook:
    return CellBook(
        cells=[
            CellEntry(library="EXAMPLE_LIB", cell="BLOCK_A_v3", layout_view="layout"),
            CellEntry(library="EXAMPLE_LIB", cell="CORE_TOP_v7", layout_view="layout"),
        ]
    )


class _StubController:
    """A dispatch-capable controller over a two-recipe library.

    Two things it does that a ``SimpleNamespace`` cannot, and both are
    load-bearing for the second press:

    * **``tasks`` follows the book.** In the real shell every committed cell
      edit reaches the controller (``cells_changed`` -> ``stage_cells``) and
      ``tasks`` is derived from the staged book, so a row added through the
      GUI *is* runnable. A stub with a frozen task list refuses the new row
      at ``_resolve_batches``'s "rows have no loaded task" gate, which would
      make R-3 fail for the fixture's reason instead of the code's.
    * **``run_recipe`` reproduces the refusal.** No id and more than one
      candidate means ``None``. A stub that answered every unknown id with a
      recipe would certify away the branch R-3 is about -- the failure mode
      ``tests/ui/test_cells_screen.py``'s own stub docstring records.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.book = _book()
        self.project = SimpleNamespace(name="demo")
        self.auto_ext_root = tmp_path / "auto_ext"
        self.workarea = tmp_path / "wa"
        self.is_dirty = False
        self.profile = SimpleNamespace(profile_id="hn001")
        self._library = {
            "rc-default": SimpleNamespace(recipe_id="rc-default", name="RC default"),
            "rc-fast": SimpleNamespace(recipe_id="rc-fast", name="RC fast"),
        }

    @property
    def tasks(self) -> list:
        return [SimpleNamespace(task_id=key) for key in self.book.keys]

    def run_recipe(self, recipe_id: str | None = None):
        if recipe_id:
            return self._library.get(recipe_id)
        return next(iter(self._library.values())) if len(self._library) == 1 else None

    def recipe_ids(self) -> list[str]:
        return sorted(self._library)


@pytest.fixture
def controller(tmp_path: Path) -> _StubController:
    return _StubController(tmp_path)


@pytest.fixture
def screen(qtbot, controller) -> CellsScreen:
    """A Cells screen that can dispatch, shown, with both rows in the run set.

    Shown because the run set is built with real mouse clicks on the check
    boxes and a hidden table has no item rectangles to hit-test.
    """

    widget = CellsScreen(controller, book=_book())
    qtbot.addWidget(widget)
    # What ``MainWindow._on_cells_changed`` does: every committed edit is
    # staged, so the controller's task list follows the table.
    widget.cells_changed.connect(lambda book: setattr(controller, "book", book))
    widget.set_recipe_choices(RECIPES)
    # The library holds two recipes and no row names one, so the dispatch
    # would refuse to guess. Picking one in the run bar is the step a user
    # takes; it is not what any test in this file is about.
    widget.run_bar.set_recipe_override("rc-default")
    # A pinned size, at artboard 1a's 1280px. Left to Qt the default came out
    # under RUN_BAR_COMPACT_BELOW on the Linux box, the bar folded its stage
    # boxes into the "stages" menu -- as designed -- and the assertions about
    # the wide strip failed there and nowhere else.
    widget.resize(1280, 720)
    widget.show()
    qtbot.waitExposed(widget)
    _tick_row(qtbot, widget, 0)
    _tick_row(qtbot, widget, 1)
    return widget


def _check_box_point(screen: CellsScreen, row: int):
    """Where the check indicator of ``row`` actually is.

    The same rect the delegate hit-tests, rather than the middle of the
    column: a click that lands beside the box is an ordinary row click and
    would build the run set for the wrong reason -- or not at all.
    """

    table = screen.table
    option = QStyleOptionViewItem()
    option.initFrom(table)
    option.rect = table.visualRect(table.model().index(row, COL_CHECK))
    option.features = QStyleOptionViewItem.HasCheckIndicator
    return (
        table.style()
        .subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, table)
        .center()
    )


def _tick_row(qtbot, screen: CellsScreen, row: int) -> None:
    """Put row ``row`` in the run set by clicking its box (toggles)."""

    qtbot.mouseClick(
        screen.table.viewport(),
        Qt.LeftButton,
        Qt.NoModifier,
        _check_box_point(screen, row),
    )


def _select_row(qtbot, screen: CellsScreen, row: int) -> None:
    """Highlight row ``row`` by clicking its cell name. Ticks nothing."""

    table = screen.table
    item = table.item(row, COL_CELL)
    assert item is not None, f"row {row} has no cell to click"
    qtbot.mouseClick(
        table.viewport(),
        Qt.LeftButton,
        Qt.NoModifier,
        table.visualItemRect(item).center(),
    )


def _task_ids(worker: FakeWorker | None) -> list[str]:
    """The row keys a dispatched worker was actually given, in batch order."""

    if worker is None:
        return []
    return [task.task_id for batch in worker.kwargs["batches"] for task in batch.tasks]


# ---- R-1: the controls disappear while a run is in flight ------------------


def test_run_stays_on_screen_and_queues_a_second_job_mid_flight(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """"Run 按钮干脆不见了" -- and the answer is not a tooltip on a dead button.

    The owner's ask was Cadence's: throw one simulation in, keep working,
    throw another one in. So Run is on screen, live, and means *enqueue*.
    The second press must not start a second worker -- one licence draw at a
    time, see ``worker.py`` -- and must not be swallowed either.
    """

    screen.run_bar.run_button().click()
    assert workers, "the first click dispatched nothing"

    bar = screen.run_bar
    button = bar.run_button()
    assert button.isVisibleTo(bar), (
        "the Run button left the screen while the run was in flight; the user "
        "cannot tell a busy app from a broken one"
    )
    assert button.isEnabled() is True, "Run went dead while a run was in flight"
    # The rest of the idle strip is intent the user may still want to change.
    assert bar.recipe_combo().isVisibleTo(bar) is True
    assert bar.stage_check("calibre").isVisibleTo(bar) is True
    assert bar._jobs_spin.isVisibleTo(bar) is True

    button.click()

    assert len(workers) == 1, "a second worker was started over the first"
    assert screen.queued_jobs() == 1, "the second press was swallowed"
    assert "1 run waiting" in bar.run_label(), (
        f"the panel title is {bar.run_label()!r} and does not say a run is "
        "waiting"
    )

    workers[0].finished.emit()
    qtbot.wait(10)

    assert len(workers) == 2, "the queued job never started"
    assert screen.queued_jobs() == 0
    assert workers[1].started is True


def test_add_during_a_run_adds_a_row_and_puts_it_in_the_next_batch(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """"勾另一个 cell / Add 全无反应" -- six entry points refused in silence.

    None of them refuses now. Add is the one the user named, so it is the
    one pinned here, down to the tick: a row is added in order to be run.
    """

    screen.run_bar.run_button().click()
    assert workers, "the first click dispatched nothing"
    rows_before = screen.table.rowCount()
    running = set(_task_ids(workers[0]))

    screen.toolbar_button("add").click()

    assert screen.table.rowCount() == rows_before + 1, (
        "Add did nothing while a run was in flight"
    )
    added = set(screen.cells().keys) - running
    assert len(added) == 1
    assert added <= set(screen.checked_keys()), "the new row is not in the run set"
    assert set(_task_ids(workers[0])) == running, (
        "the batch already in flight grew a row it was never dispatched with"
    )

    # And the rest of the table is live too, not only the Add button. Add
    # leaves the new row highlighted, so Duplicate and Remove have something
    # to act on and every toolbar button should be live.
    assert screen.table.editTriggers() != QAbstractItemView.NoEditTriggers
    for name in ("add", "duplicate", "remove", "import"):
        assert screen.toolbar_button(name).isEnabled() is True, name


# ---- R-2: the row added after a run is not in the run set ------------------


def test_a_row_added_after_a_run_joins_the_rows_that_already_ran(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """"按 Add，新行出来了，Run 却又跑了旧的那两个."

    The report describes the in-flight revision, where the new row is drawn
    highlighted but unticked. At this revision the bug is the mirror image --
    the new row takes the whole run set and the two that just ran fall out of
    it -- and the invariant that catches both is the same one: after Add, the
    run set is what it was plus the new row.
    """

    before = set(screen.checked_keys())
    assert len(before) == 2, "the fixture must start with both rows in the run set"

    def _add() -> tuple[set[str], str]:
        """Press Add and report what the user can then see about the run set."""

        screen.toolbar_button("add").click()
        return set(screen.checked_keys()), screen.run_bar.run_button_text()

    result = run_twice(screen, workers, between=_add)
    assert result.first is not None, "the first click dispatched nothing"

    ticked, run_label = result.between
    added = set(screen.cells().keys) - before
    assert len(added) == 1, "Add did not append a row"

    assert ticked == before | added, (
        f"the run set after Add is not 'what ran, plus the new row' -- the Run "
        f"button read {run_label!r}"
    )
    assert set(_task_ids(result.second)) == before | added, (
        "the second Run did not dispatch the rows the user could see ticked"
    )


# ---- R-3: the blank row refuses the whole batch ----------------------------


def test_the_new_blank_row_does_not_veto_the_rows_that_already_ran(
    qtbot, screen: CellsScreen, workers: list[FakeWorker], monkeypatch
) -> None:
    """"报'有些行没有 recipe'，连刚才跑得好好的都没跑."

    A brand-new blank row and a row bound to a recipe the user deleted are
    the same shape to the dispatch, and the refusal that is right for the
    second is wrong for the first. The blank row now inherits a recipe -- the
    highlighted row's, else the last row's -- so it resolves; the refusal
    itself is untouched and still stops a run for a row that genuinely
    cannot be resolved.
    """

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _p, title="", text="", *a, **k: shown.append((title, text))),
    )
    # Per-row recipes, not the run bar's override: with an override every row
    # resolves through it, including a blank one, and the defect is
    # unreachable. ``set_recipe_binding`` is what the table's recipe column
    # writes -- the column is a combo delegate with no keyboard route a test
    # can drive, so this is as close to the user's gesture as the widget gets.
    for key in screen.cells().keys:
        screen.set_recipe_binding(key, "rc-default")
    screen.run_bar.set_recipe_override(None)

    ran_before = set(screen.checked_keys())

    def _add_and_tick_everything() -> set[str]:
        screen.toolbar_button("add").click()
        # Add ticks the row it adds now, so this loop is a belt-and-braces
        # re-statement of the user's "我把新行也勾上": every row on screen
        # ends up in the run set, whichever of them Add left out.
        for row in range(screen.table.rowCount()):
            if screen.table.item(row, COL_CHECK).checkState() != Qt.Checked:
                _tick_row(qtbot, screen, row)
        return set(screen.checked_keys())

    result = run_twice(screen, workers, between=_add_and_tick_everything)
    assert result.first is not None, "the first click dispatched nothing"

    ticked = result.between
    assert ticked > ran_before, "the fixture did not end up with a new row ticked"

    assert result.second is not None, (
        "one brand-new blank row vetoed the whole batch: nothing ran, not even "
        "the rows that ran a moment ago"
        + (f" -- the app said {shown[-1][1]!r}" if shown else "")
    )
    assert ticked <= set(_task_ids(result.second)), (
        "the second run did not cover every row the user could see ticked"
    )


def test_a_row_that_really_has_no_recipe_still_stops_the_run(
    qtbot, screen: CellsScreen, workers: list[FakeWorker], monkeypatch
) -> None:
    """The other half of R-3: the refusal was right, its scope was wrong.

    A row bound to nothing, in a library holding two recipes, is a run that
    would have to guess which settings to extract with. It still refuses,
    and it still names the row.
    """

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _p, title="", text="", *a, **k: shown.append((title, text))),
    )
    keys = screen.cells().keys
    screen.set_recipe_binding(keys[0], "rc-default")
    screen.set_recipe_binding(keys[1], None)
    screen.run_bar.set_recipe_override(None)

    screen.run_bar.run_button().click()

    assert workers == [], "a row with no recipe at all was dispatched anyway"
    assert shown and keys[1] in shown[-1][1]
    assert keys[0] not in shown[-1][1], "the refusal named a row that was fine"


# ---- R-4: the Cells Save button never comes back ---------------------------


def test_the_cells_save_button_comes_back_after_a_run(
    qtbot, v2_config_dir: Path, isolated_recipe_path: Path, workers: list[FakeWorker]
) -> None:
    """"run 完之后 Cells 屏的 Save 永远是灰的（File → Save 还能用）."

    A full journey, because the Save button's enabled state is pushed by the
    host: the screen stages through ``cells_changed`` and the window owns
    both the queue and the file. The two Saves disagreeing about whether
    there is anything to write is the symptom, so both have to be in the
    test.
    """

    window = MainWindow(
        config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    cells = window.shell.page("cells")
    table = cells.table
    assert table.rowCount() >= 1, "the fixture project has no cell to edit"

    def _edit(view: str) -> None:
        """Type a new extracted-view name into row 0.

        ``out view`` and not the cell name: the row key is
        ``library__cell__layout__source``, so retyping the cell name rekeys
        the row and drops it out of the run set for reasons that have
        nothing to do with this test.
        """

        item = table.item(0, COL_OUT_VIEW)
        assert item is not None, "the extracted view has no cell to type into"
        item.setText(view)

    def _run_and_finish() -> None:
        _tick_row(qtbot, cells, 0)
        cells.run_bar.run_button().click()
        assert workers, "the Run click dispatched nothing"
        workers[-1].finished.emit()
        qtbot.wait(10)

    # Edit, run, edit: the state the run entered has to be handed back.
    twice(
        lambda: _edit("av_ext_125c"),
        between=_run_and_finish,
        second=lambda: _edit("av_ext_85c"),
    )

    assert window._save_action.isEnabled(), (
        "File -> Save went grey too; then this is not the Cells button's bug"
    )
    assert cells.save_button().isEnabled(), (
        "the Cells screen's own Save stayed disabled after the run, while "
        "File -> Save is live -- two controls over one queue disagreeing"
    )


def test_the_run_never_touches_the_save_button(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """M-132 at the screen: Save follows ``set_unsaved`` and nothing else.

    The run state used to disable all five toolbar buttons and hand four of
    them back, which is how a control with one legitimate owner acquired a
    second one that only ever said "off".
    """

    save = screen.save_button()
    screen.set_unsaved(True)
    assert save.isEnabled() is True

    screen.run_bar.run_button().click()
    assert workers, "the click dispatched nothing"
    assert save.isEnabled() is True, "the run took the Save button away"

    workers[0].finished.emit()
    qtbot.wait(10)
    assert save.isEnabled() is True, "the run gave back four buttons out of five"


# ---- the queue: what a press means once it has been made -------------------


def test_a_queued_job_runs_the_table_as_it_was_when_the_button_went_down(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """A press is a promise about rows the user could see at the time.

    Everything after it -- unticking, adding, renaming -- belongs to the
    *next* press. A queue that re-read the table when a job reached the
    front would run whatever happened to be ticked minutes later, which is
    the same class of surprise as the filter silently shrinking a batch.
    """

    keys = screen.cells().keys
    screen.run_bar.run_button().click()
    screen.run_bar.run_button().click()
    assert screen.queued_jobs() == 1

    # Everything the user does between the press and the dispatch.
    _tick_row(qtbot, screen, 1)  # untick CORE_TOP_v7
    screen.toolbar_button("add").click()
    assert set(screen.checked_keys()) != set(keys)

    workers[0].finished.emit()
    qtbot.wait(10)

    assert len(workers) == 2, "the queued job never started"
    assert set(_task_ids(workers[1])) == set(keys), (
        "the queued job ran the table as it looked when it reached the front "
        "of the queue, not as it looked when Run was pressed"
    )


def test_a_mid_flight_tick_changes_the_next_run_and_not_this_one(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """The checkboxes are live during a run, and they mean the next press."""

    screen.add_cell(
        CellEntry(library="EXAMPLE_LIB", cell="TOP_WRAP_v1", layout_view="layout")
    )
    extra = screen.cells().keys[-1]
    screen.set_checked_keys(screen.cells().keys[:2])
    first_two = set(screen.checked_keys())

    screen.run_bar.run_button().click()
    assert set(_task_ids(workers[0])) == first_two

    row = screen.row_of_key(extra)
    assert screen.table.item(row, COL_CHECK).flags() & Qt.ItemIsUserCheckable, (
        "the check column went read-only while a run was in flight"
    )
    _tick_row(qtbot, screen, row)

    assert set(_task_ids(workers[0])) == first_two, "the running batch changed"
    assert set(screen.checked_keys()) == first_two | {extra}

    workers[0].finished.emit()
    qtbot.wait(10)
    screen.run_bar.run_button().click()

    assert set(_task_ids(workers[1])) == first_two | {extra}


def test_rows_in_a_queued_job_say_queued_straight_away(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """A press the user made has to show on the rows it spoke for."""

    keys = screen.cells().keys
    screen.set_checked_keys([keys[0]])
    screen.run_bar.run_button().click()
    reporter = workers[0].kwargs["reporter"]
    reporter.on_task_start(keys[0], ["si"])
    assert screen.row_status(keys[0]).text == "running"

    screen.set_checked_keys([keys[1]])
    screen.run_bar.run_button().click()

    assert screen.queued_jobs() == 1
    assert screen.row_status(keys[1]).text == "queued", (
        "a row in a queued job says nothing about being spoken for"
    )
    assert screen.row_status(keys[0]).text == "running", (
        "queueing a second job overwrote the live status of the first"
    )


def test_dropping_the_queue_leaves_the_run_in_flight_alone(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """The visible way out of a queue the user has changed their mind about."""

    keys = screen.cells().keys
    screen.set_checked_keys([keys[0]])
    screen.run_bar.run_button().click()
    screen.set_checked_keys([keys[1]])
    screen.run_bar.run_button().click()
    assert screen.queued_jobs() == 1

    drop = screen.run_bar.drop_queued_button()
    assert drop.isVisibleTo(screen.run_bar) is True, "the queue cannot be dropped"
    assert drop.toolTip().strip(), "the drop control does not say what it does"

    drop.click()

    assert screen.queued_jobs() == 0
    assert workers[0].cancelled is False, "dropping the queue cancelled the run"
    assert screen.is_running() is True, "the run in flight was thrown away too"
    assert screen.row_status(keys[1]).text != "queued", (
        "a dropped job left its rows claiming to be queued"
    )

    workers[0].finished.emit()
    qtbot.wait(10)
    assert len(workers) == 1, "a dropped job ran anyway"
    assert screen.is_running() is False


def test_two_consecutive_runs_leave_no_thread_behind(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """M-134: the worker reference was dropped from inside its own slot.

    ``self._worker = None`` in the ``finished`` handler was frequently the
    last reference to a ``QThread`` still winding down, which sip answers by
    deleting the C++ object -- "QThread: Destroyed while thread is still
    running", and an app that gets stranger after every run. The fix is a
    ``deleteLater`` plus a zero-timer, so what this pins is that the worker
    is scheduled for deletion by Qt rather than collected by Python.
    """

    from PyQt5.QtCore import qInstallMessageHandler

    seen: list[str] = []
    previous = qInstallMessageHandler(
        lambda mode, context, message: seen.append(message)
    )
    try:
        screen.run_bar.run_button().click()
        first = workers[0]
        first.finished.emit()
        qtbot.wait(10)

        assert sip.isdeleted(first), (
            "the retired worker was never handed to deleteLater; its lifetime "
            "is Python's refcount again"
        )
        assert screen.is_running() is False

        screen.run_bar.run_button().click()
        assert len(workers) == 2, "the second run never started"
        workers[1].finished.emit()
        qtbot.wait(10)
    finally:
        qInstallMessageHandler(previous)

    assert not [m for m in seen if "Destroyed while thread is still running" in m], seen


# ---- R-5: cancel that never comes back -------------------------------------


def test_a_cancel_the_runner_ignores_gives_the_button_back_and_says_so(
    qtbot, screen: CellsScreen, workers: list[FakeWorker], monkeypatch
) -> None:
    """"按了 Cancel 就再也没回来，现在既没有 Run 也没有 Cancel."

    The worker here never emits ``finished`` -- a tool inside a call that
    has not returned. Nothing in the GUI can make it return; what the GUI
    owes the user is the sentence and the button, on a deadline.
    """

    monkeypatch.setattr(cells_mod, "CANCEL_DEADLINE_MS", 10)
    said: list[str] = []
    screen.status_message.connect(said.append)

    screen.run_bar.run_button().click()
    assert workers, "the click dispatched nothing"

    cancel = screen.run_bar.cancel_button()
    cancel.click()
    assert workers[0].cancelled is True
    assert cancel.isEnabled() is False, "the accepted cancel was not acknowledged"

    qtbot.waitUntil(lambda: cancel.isEnabled(), timeout=2000)

    assert cancel.text() == "Cancel run"
    assert cancel.toolTip().strip(), "the re-armed Cancel says nothing about why"
    assert any("not come back" in message for message in said), said
    # And the run bar has not pretended the run ended.
    assert screen.is_running() is True


def test_a_cancel_that_does_come_back_says_nothing_about_a_deadline(
    qtbot, screen: CellsScreen, workers: list[FakeWorker], monkeypatch
) -> None:
    """The deadline must not fire over a runner that stopped on time."""

    monkeypatch.setattr(cells_mod, "CANCEL_DEADLINE_MS", 10)
    said: list[str] = []
    screen.status_message.connect(said.append)

    screen.run_bar.run_button().click()
    screen.run_bar.cancel_button().click()
    workers[0].finished.emit()
    qtbot.wait(60)

    assert not any("not come back" in message for message in said), said


# ---- closing the window over a queue ---------------------------------------


def test_closing_over_a_queued_job_drops_it_and_says_so(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """``request_close`` reads ``is_running``, which now includes the queue.

    A press the user made and has not seen run is work they can still lose,
    so the close guard has to know about it and ``stop_run_and_wait`` has to
    throw it away rather than let it start on the way out.
    """

    screen.run_bar.run_button().click()
    screen.run_bar.run_button().click()
    assert screen.queued_jobs() == 1
    assert screen.is_running() is True

    assert screen.stop_run_and_wait() is True

    assert screen.queued_jobs() == 0
    assert workers[0].cancelled is True
    workers[0].finished.emit()
    qtbot.wait(10)
    assert len(workers) == 1, "a queued job started while the window was closing"
    assert screen.is_running() is False


# ---- the queue against a moving world --------------------------------------


def test_a_stale_cancel_deadline_does_not_stall_the_next_job(
    qtbot, screen: CellsScreen, workers: list[FakeWorker], monkeypatch
) -> None:
    """The deadline belongs to a *press*, not to whatever is in flight.

    ``singleShot`` cannot be recalled, so job 1's 15-second deadline is
    still on its way long after job 1 stopped. A guard that only asked "is
    the worker I cancelled still running" would let that timer land on job
    2's five-second-old Cancel and tell the user the runner had not come
    back.
    """

    said: list[str] = []
    screen.status_message.connect(said.append)

    # Job 1's deadline is short; job 2's is far away. That is the shape of
    # the real bug in miniature -- 15s armed at t+0, a cancel at t+10 -- and
    # the only way to hold the two apart inside a test's patience.
    monkeypatch.setattr(cells_mod, "CANCEL_DEADLINE_MS", 60)
    screen.run_bar.run_button().click()
    screen.run_bar.cancel_button().click()  # deadline A: 60 ms from now
    workers[0].finished.emit()  # job 1 stops at once
    qtbot.wait(10)

    monkeypatch.setattr(cells_mod, "CANCEL_DEADLINE_MS", 5000)
    screen.run_bar.run_button().click()  # job 2
    assert len(workers) == 2
    screen.run_bar.cancel_button().click()  # deadline B: 5 s from now
    said.clear()

    # Well past deadline A, nowhere near deadline B.
    qtbot.wait(250)

    assert not any("not come back" in m for m in said), said
    assert screen.run_bar.cancel_button().isEnabled() is False, (
        "a deadline armed for the previous job re-armed this job's Cancel"
    )

    # And the guard has not simply switched the deadline off: this job's own
    # Cancel, armed short, still comes back.
    monkeypatch.setattr(cells_mod, "CANCEL_DEADLINE_MS", 40)
    screen.cancel_run()
    qtbot.waitUntil(lambda: screen.run_bar.cancel_button().isEnabled(), timeout=2000)
    assert any("not come back" in m for m in said), (
        "the live job's own deadline never fired"
    )


def test_a_second_cancel_on_one_worker_moves_the_deadline_with_it(
    qtbot, screen: CellsScreen, workers: list[FakeWorker], monkeypatch
) -> None:
    """Two presses, one worker: only the newest press owns the deadline."""

    monkeypatch.setattr(cells_mod, "CANCEL_DEADLINE_MS", 150)
    said: list[str] = []
    screen.status_message.connect(said.append)

    screen.run_bar.run_button().click()
    cancel = screen.run_bar.cancel_button()
    cancel.click()
    qtbot.waitUntil(lambda: cancel.isEnabled(), timeout=2000)
    said.clear()
    cancel.click()  # re-armed, and a second deadline with it

    qtbot.wait(60)
    assert not any("not come back" in m for m in said), (
        "the first press's expired deadline fired again over the second"
    )
    qtbot.waitUntil(lambda: cancel.isEnabled(), timeout=2000)
    assert any("not come back" in m for m in said)


def test_a_waiting_job_keeps_the_config_it_was_pressed_against(
    qtbot, screen: CellsScreen, controller, workers: list[FakeWorker], tmp_path: Path
) -> None:
    """A reload between the press and the start must not move the run.

    The Recipes screen's Reload button and ``File -> Open`` are both one
    click away and neither is blocked while a run is going -- the screen
    stays free on purpose. So the five controller values a run needs are
    frozen when the button goes down, next to the batches.
    """

    screen.run_bar.run_button().click()
    screen.run_bar.run_button().click()
    assert screen.queued_jobs() == 1

    pressed_against = controller.workarea
    controller.workarea = tmp_path / "somewhere-else"
    controller.project = None  # what a failed reload leaves behind

    workers[0].finished.emit()
    qtbot.wait(10)

    assert len(workers) == 2, "the waiting job never started"
    assert workers[1].kwargs["workarea"] == pressed_against, (
        "the waiting job was dispatched into the workarea a later reload "
        "installed, not the one the user pressed Run against"
    )
    assert workers[1].kwargs["project"] is not None, (
        "a reload that cleared the project took the waiting job down with it"
    )


def test_rows_only_a_waiting_job_speaks_for_read_queued_in_both_columns(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """One row must not answer the same question two different ways."""

    keys = screen.cells().keys
    screen.set_checked_keys([keys[0]])
    screen.run_bar.run_button().click()
    assert screen.stage_strip(keys[1]).placeholder() == "—"

    screen.set_checked_keys([keys[1]])
    screen.run_bar.run_button().click()

    assert screen.queued_jobs() == 1
    assert screen.row_status(keys[1]).text == "queued"
    assert screen.stage_strip(keys[1]).placeholder() == "queued", (
        "the status column says the row is spoken for and the chip column "
        "still shows an em dash"
    )


def test_the_status_line_carries_the_waiting_count_through_the_whole_run(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """Depth on the line the user is actually looking at.

    The host writes "running - 1 run waiting" once, when the press lands,
    and the next stage event overwrites it. Over an hours-long extraction
    that is the queue depth being visible for a single frame.
    """

    said: list[str] = []
    screen.status_message.connect(said.append)

    screen.run_bar.run_button().click()
    screen.run_bar.run_button().click()
    reporter = workers[0].kwargs["reporter"]
    said.clear()

    reporter.on_stage_start(screen.cells().keys[0], "calibre")

    assert said, "a stage start said nothing"
    assert "1 run waiting" in said[-1], (
        f"the status line is {said[-1]!r} and has lost the queue depth"
    )

    screen.drop_queued_jobs()
    said.clear()
    reporter.on_stage_start(screen.cells().keys[0], "quantus")
    assert "waiting" not in said[-1], f"the suffix outlived the queue: {said[-1]!r}"


def test_the_table_is_typeable_through_a_run_including_out_view(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """The extracted-view name is one of the six columns the run used to hide.

    ``out view`` is the value Quantus writes with ``-view_name`` and Jivaro
    reads back as ``inputView``; a running layout that hid it made the whole
    back half of the flow untypeable for the length of the run.
    """

    screen.run_bar.run_button().click()
    assert workers, "the click dispatched nothing"
    assert screen.column_mode() == "running"

    assert screen.table.isColumnHidden(COL_OUT_VIEW) is False, (
        "the run took the extracted-view column off the table"
    )
    key = screen.cells().keys[0]
    row = screen.row_of_key(key)
    screen.table.item(row, COL_OUT_VIEW).setText("av_ext_125c")

    assert screen.cells().entry(key).out_file == "av_ext_125c"
    assert set(_task_ids(workers[0])), "the running batch lost its tasks"


def test_a_run_that_raises_does_not_park_the_queue_behind_a_dialog(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """``QMessageBox.critical`` spins an event loop; the queue moves in it.

    The static's nested loop delivers the worker's own ``finished`` and the
    zero-timer behind it, so the next job would start -- and print into a
    panel sitting behind an error nobody has read. The box is modeless.
    """

    screen.run_bar.run_button().click()
    screen.run_bar.run_button().click()
    assert screen.queued_jobs() == 1

    workers[0].error.emit("RuntimeError: the deck directory vanished")
    boxes = [w for w in screen.findChildren(QMessageBox) if w.isVisible()]
    assert boxes, "a run that raised said nothing"
    assert boxes[0].isModal() is False, (
        "the error dialog is modal, so the queue advances inside its own "
        "nested event loop"
    )
    assert "vanished" in boxes[0].text()

    workers[0].finished.emit()
    qtbot.wait(10)
    assert len(workers) == 2, "the waiting job never started"
    boxes[0].close()


def test_a_pending_cancel_deadline_does_not_outlive_the_screen(
    qtbot, controller, workers: list[FakeWorker], monkeypatch
) -> None:
    """15 seconds is long enough for the window to be gone first.

    A free-standing ``QTimer.singleShot`` keeps ticking after the widget it
    was armed for is destroyed, and its callback then reaches through a dead
    run bar -- ``RuntimeError: wrapped C/C++ object of type QPushButton has
    been deleted``, raised inside the Qt event loop where nobody can act on
    it. The deadline is a timer parented to the screen instead, so it dies
    with it.
    """

    from PyQt5.QtCore import QTimer
    from pytestqt.exceptions import capture_exceptions

    monkeypatch.setattr(cells_mod, "CANCEL_DEADLINE_MS", 40)
    doomed = CellsScreen(controller, book=_book())
    doomed.set_recipe_choices(RECIPES)
    doomed.run_bar.set_recipe_override("rc-default")
    doomed.set_checked_keys(doomed.cells().keys[:1])
    doomed.start_run()
    assert workers, "the run never started"
    doomed.cancel_run()

    armed = [t for t in doomed.findChildren(QTimer) if t.isActive()]
    assert armed, "the cancel deadline is not owned by the screen"

    with capture_exceptions() as raised:
        doomed.deleteLater()
        del doomed
        qtbot.wait(150)

    assert raised == [], raised
