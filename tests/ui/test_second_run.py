"""The second Run -- R-1..R-4 of the 2026-09-04 user report.

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

Master rows: M-129 (R-1), M-130 (R-2), M-131 (R-3), M-132 (R-4).

**These tests are rulers, not fixes.** Each one that fails at HEAD carries a
strict ``xfail`` naming its master row, so the suite stays green and the fix
that lands has to flip the marker -- an ``XPASS`` under ``strict=True`` is a
failure, which is what makes the marker a ledger entry rather than a
suppression.

``auto_ext/ui/main_window.py``, ``auto_ext/ui/screens/cells_screen.py`` and
``auto_ext/ui/widgets/run_bar.py`` are all being rewritten in another session
as this is written. The assertions below are about what the *user* sees, so
they should survive that; the accessors they reach through (``run_bar``,
``save_button()``, ``run_button()``, ``selected_keys()``) may need re-aiming
once it merges.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QObject, Qt, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import QMessageBox  # noqa: E402

from auto_ext.model.cells import CellBook, CellEntry  # noqa: E402
from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.screens import cells_screen as cells_mod  # noqa: E402
from auto_ext.ui.screens.cells_screen import (  # noqa: E402
    COL_CELL,
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


@pytest.fixture
def workers(monkeypatch) -> list[FakeWorker]:
    """Every worker the screen built, oldest first."""

    FakeWorker.instances = []
    monkeypatch.setattr(cells_mod, "RunWorker", FakeWorker)
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
      at ``_dispatch``'s "rows have no loaded task" gate, which would make
      R-3 fail for the fixture's reason instead of the code's.
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

    Shown because the run set *is* the table selection at this revision and
    the selection is made with real mouse clicks: a hidden table has no item
    rectangles to click in.
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
    widget.show()
    qtbot.waitExposed(widget)
    _click_row(qtbot, widget, 0)
    _click_row(qtbot, widget, 1, add=True)
    return widget


def _click_row(qtbot, screen: CellsScreen, row: int, *, add: bool = False) -> None:
    """Put row ``row`` in the run set by clicking it; Ctrl-click to add.

    The trailing key release is not decoration. ``QTest`` sets the
    application-wide modifier state from the event it synthesises and never
    clears it, and ``QAbstractItemView::setCurrentIndex`` (which
    ``add_cell`` reaches through ``setCurrentCell``) reads
    ``QGuiApplication::keyboardModifiers()`` when it has no event to consult.
    A Ctrl-click left standing therefore turns the *next* programmatic
    selection into a toggle, which empties the run set for a reason that
    exists only inside the test. Releasing the key puts the ambient state
    back where a real user's fingers would leave it.
    """

    table = screen.table
    item = table.item(row, COL_CELL)
    assert item is not None, f"row {row} has no cell to click"
    qtbot.mouseClick(
        table.viewport(),
        Qt.LeftButton,
        Qt.ControlModifier if add else Qt.NoModifier,
        table.visualItemRect(item).center(),
    )
    if add:
        qtbot.keyRelease(table.viewport(), Qt.Key_Control, Qt.NoModifier)


def _task_ids(worker: FakeWorker | None) -> list[str]:
    """The row keys a dispatched worker was actually given, in batch order."""

    if worker is None:
        return []
    return [task.task_id for batch in worker.kwargs["batches"] for task in batch.tasks]


# ---- R-1: the controls disappear while a run is in flight ------------------


@pytest.mark.xfail(
    strict=True,
    reason="M-129: RunBar.set_running(True) hides the whole idle widget, so the "
    "Run button is not disabled-with-a-reason, it is gone",
)
def test_run_stays_on_screen_and_says_why_while_a_run_is_in_flight(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """"Run 按钮干脆不见了" -- a control that vanishes teaches nothing."""

    screen.run_bar.run_button().click()
    assert workers, "the first click dispatched nothing"

    button = screen.run_bar.run_button()
    assert button.isVisibleTo(screen.run_bar), (
        "the Run button left the screen while the run was in flight; the user "
        "cannot tell a busy app from a broken one"
    )
    assert button.isEnabled() is False
    assert button.toolTip().strip(), (
        "a disabled Run must say why it is disabled -- 'a run is already in "
        "flight' is the sentence the user is missing"
    )


@pytest.mark.xfail(
    strict=True,
    reason="M-129: add_cell returns on `self._worker is not None` with a bare "
    "return -- no status line, no tooltip, nothing on screen",
)
def test_add_during_a_run_tells_the_user_it_was_refused(
    qtbot, screen: CellsScreen, workers: list[FakeWorker]
) -> None:
    """"勾另一个 cell / Add 全无反应" -- six entry points refuse in silence."""

    messages: list[str] = []
    screen.status_message.connect(messages.append)
    screen.edit_rejected.connect(messages.append)

    screen.run_bar.run_button().click()
    assert workers, "the first click dispatched nothing"
    messages.clear()

    add = screen.toolbar_button("add")
    rows_before = screen.table.rowCount()
    add.click()

    assert screen.table.rowCount() == rows_before, (
        "a row was added mid-run; this test is about the refusal, not the add"
    )
    assert messages or add.toolTip().strip(), (
        "Add did nothing and said nothing: no status message and no tooltip "
        "on the disabled button"
    )


# ---- R-2: the row added after a run is not in the run set ------------------


@pytest.mark.xfail(
    strict=True,
    reason="M-130: add_cell ends in set_selected_keys([new]) -- a ClearAndSelect "
    "that drops every row that just ran out of the run set",
)
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

    before = set(screen.selected_keys())
    assert len(before) == 2, "the fixture must start with both rows in the run set"

    def _add() -> tuple[set[str], str]:
        """Press Add and report what the user can then see about the run set."""

        screen.toolbar_button("add").click()
        return set(screen.selected_keys()), screen.run_bar.run_button_text()

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


@pytest.mark.xfail(
    strict=True,
    reason="M-131: _blank_entry builds a CellEntry with recipe=None, run_recipe "
    "returns None with two recipes in the library, and _dispatch refuses the "
    "whole batch instead of the one unresolved row",
)
def test_the_new_blank_row_does_not_veto_the_rows_that_already_ran(
    qtbot, screen: CellsScreen, workers: list[FakeWorker], monkeypatch
) -> None:
    """"报'有些行没有 recipe'，连刚才跑得好好的都没跑."

    A brand-new blank row and a row bound to a recipe the user deleted are
    the same shape to the dispatch, and the refusal that is right for the
    second is wrong for the first. Either the resolved rows run, or the
    message names only the row that cannot.
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

    ran_before = set(screen.selected_keys())

    def _add_and_tick_everything() -> set[str]:
        screen.toolbar_button("add").click()
        # Tick every row, the new one included: ``add_cell`` leaves the run
        # set pointing at the new row alone (that is M-130), so the user's
        # "我把新行也勾上" is re-made here from scratch.
        for row in range(screen.table.rowCount()):
            _click_row(qtbot, screen, row, add=row > 0)
        return set(screen.selected_keys())

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


# ---- R-4: the Cells Save button never comes back ---------------------------


@pytest.mark.xfail(
    strict=True,
    reason="M-132: _enter_running_state disables all five toolbar buttons and "
    "_refresh_run_bar restores only four; set_unsaved fires on the controller's "
    "clean->dirty edge alone, which a second edit does not produce",
)
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
        _click_row(qtbot, cells, 0)
        cells.run_bar.run_button().click()
        assert workers, "the Run click dispatched nothing"
        workers[-1].finished.emit()

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
