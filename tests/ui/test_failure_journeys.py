"""Fifteen journeys through a pipeline that went wrong.

``tests/ui/test_journeys.py`` holds seventeen journeys and **not one of them
runs a failing pipeline**: its only two run-shaped journeys monkeypatch the
run away, and there is no Runs-screen or result-card journey at all. That is
why every row of the error-path review is invisible today -- together with
two absences in ``tests/ui/conftest.py``: every ``QMessageBox`` static raises
(so no test can be reaching a dialog), and ``open_in_os`` is patched nowhere
(so no test can be clicking anything that launches a real handler on the
developer's machine).

So each journey here **builds a run directory on disk in the failure state it
is about**, opens it through a real ``MainWindow`` with the Runs screen wired
into it, clicks the affordance the card offers, and asserts what the user
would see or which file was opened. ``open_in_os`` is patched to record; the
dialogs each journey expects are patched in its own body, which is what the
conftest's own docstring asks for.

Master rows, in the order the ledger lists the journeys: M-22, M-23, M-24,
M-19/M-20/M-21, M-26, M-27, M-29, M-28, M-34/M-35, M-41/M-47, M-30, M-33,
M-36, M-31, M-43.

**These tests are rulers, not fixes.** Every one that fails at HEAD carries a
strict ``xfail`` naming its master row, so the suite stays green and the fix
has to flip the marker -- an ``XPASS`` under ``strict=True`` is a failure,
which is what makes the marker a ledger entry rather than a suppression.

``auto_ext/ui/main_window.py``, ``auto_ext/ui/screens/cells_screen.py`` and
``auto_ext/ui/widgets/run_bar.py`` are being rewritten in another session as
this is written; six of these journeys drive them. The assertions are about
what the user sees, so they should survive it, but the accessors may need
re-aiming once it merges.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QCheckBox,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTreeWidget,
)
from pytestqt.exceptions import capture_exceptions  # noqa: E402

from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.core.run_store import write_record  # noqa: E402
from auto_ext.model.run import (  # noqa: E402
    LvsResult,
    RunResults,
    StageRecord,
    allocate_run_dir,
)
from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.screens import cells_screen as cells_mod  # noqa: E402
from auto_ext.ui.screens import runs_screen as runs_mod  # noqa: E402
from auto_ext.ui.screens.cells_screen import COL_CELL, COL_LAST_RUN  # noqa: E402
from auto_ext.ui.widgets.failure_chip import PathLabel  # noqa: E402

_REPORT_WITH_MISMATCH = """\
LAYOUT NAME: amp2
SOURCE NAME: amp2

                    INCORRECT

     CELL SUMMARY

 CELL COMPARISON RESULTS ( TOP LEVEL )

 #     Layout        Source        Result
 1     amp2          amp2          MISMATCH

 DISCREPANCY INFORMATION

 Total: 3
 DISCREPANCIES:                 3
"""


# ---- the window ------------------------------------------------------------


@pytest.fixture
def runs_root(v2_config_dir: Path) -> Path:
    """``<auto_ext_root>/runs`` -- what the Runs screen of this window lists.

    Deliberately not the ``runs_root`` fixture in ``tests/conftest.py``: that
    one is ``tmp_path/runs``, and a window built over ``v2_config_dir`` looks
    for its history next to the config directory. A journey whose runs are in
    the wrong place is a journey against an empty Runs screen.
    """

    root = v2_config_dir / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def window(qtbot, v2_config_dir: Path, isolated_recipe_path: Path, runs_root: Path):
    """A shown ``MainWindow`` over the fixture project, Runs screen included."""

    win = MainWindow(config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir)
    qtbot.addWidget(win)
    win.show()
    qtbot.waitExposed(win)
    return win


@pytest.fixture
def launches(monkeypatch) -> list[Path]:
    """Every path the app asked the OS to open.

    Patched in *both* modules that imported the name and in the module that
    defines it: ``main_window`` and ``runs_screen`` each do
    ``from auto_ext.ui.os_open import open_in_os``, so a patch on the source
    module alone would leave two live references pointing at the real
    launcher -- and this suite has never patched it at all, which is why no
    test could click anything that opens a file.
    """

    seen: list[Path] = []

    def _record(path: Path) -> None:
        seen.append(Path(path))

    for module in (
        "auto_ext.ui.os_open",
        "auto_ext.ui.main_window",
        "auto_ext.ui.screens.runs_screen",
    ):
        monkeypatch.setattr(f"{module}.open_in_os", _record)
    return seen


# ---- writing a run history by hand -----------------------------------------


def _write_run(runs_root: Path, make_run_record, *, slug: str = "amp2-ext", **kw):
    """Allocate a run directory and write a valid ``run.json`` into it."""

    run_dir = allocate_run_dir(runs_root, slug)
    record = make_run_record(run_dir=run_dir, **kw)
    write_record(run_dir, record)
    return run_dir, record


def _failed_lvs_run(runs_root: Path, make_run_record, **kw):
    """The ``populated_run`` shape of ``test_result_card.py``, on disk.

    A run whose calibre stage failed with an archived report and a log: the
    state every "read the log / show me the discrepancies" affordance is
    drawn for.
    """

    run_dir = allocate_run_dir(runs_root, kw.pop("slug", "amp2-ext"))
    (run_dir / "logs" / "si.log").write_text("si output\n", encoding="utf-8")
    (run_dir / "logs" / "calibre.log").write_text("calibre output\n", encoding="utf-8")
    (run_dir / "rendered" / "lvs.qci").write_text("*lvsRunDir: /wa\n", encoding="utf-8")
    (run_dir / "results" / "lvs.report").write_text(
        _REPORT_WITH_MISMATCH, encoding="utf-8"
    )
    record = make_run_record(
        run_dir=run_dir,
        overall=TaskStatus.FAILED,
        stages=[
            StageRecord(
                key="si",
                stage="si",
                status=StageStatus.PASSED,
                duration_s=12.5,
                log_path="logs/si.log",
                exit_code=0,
            ),
            StageRecord(
                key="calibre",
                stage="calibre",
                status=StageStatus.FAILED,
                duration_s=200.0,
                log_path="logs/calibre.log",
                rendered_path="rendered/lvs.qci",
                exit_code=0,
            ),
            StageRecord(
                key="quantus",
                stage="quantus",
                status=StageStatus.SKIPPED,
                skip_reason="aborted after earlier stage failure",
            ),
        ],
        results=RunResults(
            lvs=LvsResult(
                passed=False,
                banner="INCORRECT",
                discrepancies=3,
                source_path="/wa/amp2.lvs.report",
                archived_path="results/lvs.report",
            )
        ),
        **kw,
    )
    write_record(run_dir, record)
    return run_dir, record


# ---- reaching what is on screen --------------------------------------------


def _runs(window: MainWindow):
    window.shell.set_current_page("runs")
    screen = window.shell.page("runs")
    screen.refresh()
    return screen


def _button(widget, label: str) -> QPushButton:
    """The one visible button whose text is ``label``."""

    found = [
        b
        for b in widget.findChildren(QPushButton)
        if b.text() == label and not b.isHidden()
    ]
    assert found, (
        f"no button labelled {label!r}; the card offers "
        + repr(sorted({b.text() for b in widget.findChildren(QPushButton)}))
    )
    return found[0]


def _buttons(widget, needle: str) -> list[QPushButton]:
    return [b for b in widget.findChildren(QPushButton) if needle in b.text()]


def _path_rows(card) -> dict[str, PathLabel]:
    """``full text -> label`` for every path the card is drawing."""

    return {label.full_text(): label for label in card.findChildren(PathLabel)}


def _stage_tree(card) -> QTreeWidget:
    trees = card.findChildren(QTreeWidget)
    assert trees, "the card has no stage tree"
    return trees[0]


def _stage_row(card, key: str):
    tree = _stage_tree(card)
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item.text(0) == key:
            return item
    return None


def _visible_text(widget) -> str:
    """Everything the user can read on ``widget``, joined.

    Labels only, and only the ones actually drawn: the point of every
    "the card must explain this" assertion is that the sentence reached the
    screen, not that some attribute holds it.
    """

    from PyQt5.QtWidgets import QLabel

    return "\n".join(
        label.text()
        for label in widget.findChildren(QLabel)
        if not label.isHidden() and label.text()
    )


def _open_row_menu(qtbot, screen, module, point: QPoint) -> list:
    """Right-click a list row and return the deferred menu's actions.

    The popup is deferred one event-loop tick everywhere in this codebase
    (X11 delivers the context-menu event on press, so a synchronous
    ``exec_()`` is dismissed by the following release), which is why the wait
    is here and not optional.
    """

    captured: list = []
    real_exec = module.QMenu.exec_
    module.QMenu.exec_ = lambda self, *a, **k: captured.extend(self.actions())
    try:
        screen.customContextMenuRequested.emit(point)
        qtbot.wait(20)
    finally:
        module.QMenu.exec_ = real_exec
    return captured


def _menu_action(actions: list, needle: str):
    for action in actions:
        if needle.lower() in action.text().lower():
            return action
    return None


# ---- driving a run ----------------------------------------------------------


class _FakeWorker(QObject):
    """A ``RunWorker`` stand-in whose reporter the journey drives by hand."""

    error = pyqtSignal(str)
    finished = pyqtSignal()
    instances: list["_FakeWorker"] = []

    #: What ``QThread.wait`` answers. ``False`` models a tool that is still
    #: inside the SIGTERM grace window -- the state journey 13 is about.
    wait_returns = True

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.cancelled = False
        self.summary = SimpleNamespace(runs=[])
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        pass

    def request_cancel(self) -> None:
        self.cancelled = True

    def wait(self, _timeout_ms: int = 0) -> bool:
        return _FakeWorker.wait_returns


@pytest.fixture
def workers(monkeypatch) -> list[_FakeWorker]:
    _FakeWorker.instances = []
    _FakeWorker.wait_returns = True
    monkeypatch.setattr(cells_mod, "RunWorker", _FakeWorker)
    return _FakeWorker.instances


def _tick_cells(qtbot, cells, rows: tuple[int, ...]) -> tuple[str, ...]:
    """Put ``rows`` in the run set by clicking them."""

    table = cells.table
    for position, row in enumerate(rows):
        item = table.item(row, COL_CELL)
        assert item is not None, f"row {row} is not in the table"
        qtbot.mouseClick(
            table.viewport(),
            Qt.LeftButton,
            Qt.ControlModifier if position else Qt.NoModifier,
            table.visualItemRect(item).center(),
        )
        if position:
            # QTest leaves the modifier latched application-wide; a later
            # programmatic setCurrentIndex would then read it as a toggle.
            qtbot.keyRelease(table.viewport(), Qt.Key_Control, Qt.NoModifier)
    return cells.selected_keys()


def _add_rows(cells, count: int) -> None:
    for _ in range(count):
        cells.add_cell()


def _checkbox(widget, label: str) -> QCheckBox:
    found = [b for b in widget.findChildren(QCheckBox) if b.text() == label]
    assert found, (
        f"no checkbox labelled {label!r}; the bar offers "
        + repr(sorted({b.text() for b in widget.findChildren(QCheckBox)}))
    )
    return found[0]


# ============================================================================
# 1 -- "quantus passed and there is no DSPF"  (M-22)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-22: Tool.with_artifacts diverts a declared-but-absent output into "
    "diagnostics and leaves success alone, and no widget reads "
    "details['missing_artifacts'] -- so the stage is a plain pass and the "
    "absent DSPF is blamed on the host",
)
def test_a_stage_that_wrote_nothing_is_not_presented_as_a_plain_pass(
    qtbot, window: MainWindow, runs_root: Path, make_run_record, launches
) -> None:
    """"卡片说 quantus 通过了，DSPF 根本不存在."""

    run_dir, record = _write_run(
        runs_root,
        make_run_record,
        overall=TaskStatus.PASSED,
        dspf_path=str(runs_root / "amp2.dspf"),  # declared, never written
        stages=[
            StageRecord(
                key="quantus",
                stage="quantus",
                status=StageStatus.PASSED,
                exit_code=0,
                duration_s=41.0,
                log_path=None,
                details={"missing_artifacts": [str(runs_root / "amp2.dspf")]},
            )
        ],
    )

    screen = _runs(window)
    assert screen.select_run(record.run_id), "the run did not list"
    card = screen.result_card

    row = _path_rows(card).get(str(runs_root / "amp2.dspf"))
    assert row is not None, "the card drew no dspf row at all"
    assert "Not on this host" not in row.toolTip(), (
        "the DSPF is missing because quantus never wrote it, and the card "
        f"blames the host: {row.toolTip()!r}"
    )

    quantus = _stage_row(card, "quantus")
    assert quantus is not None
    assert quantus.text(1).lower() != "passed", (
        "a stage that exited 0 without producing its declared output is "
        "still drawn as a plain pass"
    )


# ============================================================================
# 2 -- "I asked for a stage the recipe doesn't have"  (M-23)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-23: _recipe_steps may intersect down to [] and the run proceeds; "
    "_compute_overall finds no FAILED and returns PASSED, and stage_chips "
    "draws nothing at all for an empty requested_stages",
)
def test_a_run_with_no_stages_at_all_does_not_read_as_passed(
    qtbot, window: MainWindow, runs_root: Path, make_run_record
) -> None:
    """"显示 PASSED，可是什么都没跑."""

    _run_dir, record = _write_run(
        runs_root,
        make_run_record,
        overall=TaskStatus.PASSED,
        requested_stages=[],
        stages=[],
    )

    screen = _runs(window)
    assert screen.select_run(record.run_id), "the run did not list"

    listed = screen._list.item(0).text() + " " + screen.result_card._badge.text()
    assert "pass" not in listed.lower(), (
        "a run whose requested stages and recipe stages did not intersect ran "
        f"nothing and is reported as {listed!r}"
    )


# ============================================================================
# 3 -- "LVS failed and I asked it to carry on anyway"  (M-24)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-24: the checkbox is read into RunRequest and then dropped -- "
    "_dispatch builds RunWorker without it, RunWorker has no such parameter, "
    "and the runner takes the value from the recipe only",
)
def test_continue_on_lvs_fail_reaches_the_runner(
    qtbot, window: MainWindow, workers: list[_FakeWorker]
) -> None:
    """"勾了 continue on LVS fail ... 卡片还说这个开关是关的."

    The other half of this row -- that ``run.json`` then records the recipe's
    value and the card prints it back -- needs a real failing calibre and
    belongs with the runner's own tests. What is asserted here is the half
    the GUI owns: the box the user ticked has to reach the thing that runs.
    """

    cells = window.shell.page("cells")
    bar = cells.run_bar
    _checkbox(bar, "continue on LVS fail").click()
    assert bar.continue_on_lvs_fail() is True, "the box did not tick"

    _tick_cells(qtbot, cells, (0,))
    request = cells.run_request()
    assert request.continue_on_lvs_fail is True, "the request lost it first"

    bar.run_button().click()
    assert workers, "the Run click dispatched nothing"

    assert workers[0].kwargs.get("continue_on_lvs_fail") is True, (
        "the flag stops at the GUI: the worker was built with "
        + repr(sorted(workers[0].kwargs))
    )


# ============================================================================
# 4 -- "the log I need is gone / this host cannot open files"  (M-19/20/21)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-20: RunsScreen opens the path and re-emits, and MainWindow opens "
    "it again -- two launches per click; M-19: MainWindow._open_path has no "
    "try/except, so the second one takes the app down on a host with no "
    "launcher (M-21)",
)
def test_opening_a_log_launches_one_viewer_and_survives_a_host_with_none(
    qtbot, window: MainWindow, runs_root: Path, make_run_record, monkeypatch
) -> None:
    """"点一次 log，编辑器开两个" + "服务器上根本没有能打开它的东西."""

    _run_dir, record = _failed_lvs_run(runs_root, make_run_record)

    tried: list[Path] = []

    def _no_launcher(path: Path) -> None:
        # What ``open_in_os`` does on the deployment target: neither
        # xdg-open nor gio is on PATH, so it raises rather than returning.
        tried.append(Path(path))
        raise OSError(f"none of xdg-open, gio was found on PATH; cannot open {path}")

    for module in (
        "auto_ext.ui.os_open",
        "auto_ext.ui.main_window",
        "auto_ext.ui.screens.runs_screen",
    ):
        monkeypatch.setattr(f"{module}.open_in_os", _no_launcher)

    warned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(
            lambda _p=None, title="", text="", *a, **k: warned.append((title, text))
        ),
    )

    screen = _runs(window)
    assert screen.select_run(record.run_id)

    # The click is wrapped because the defect *is* an exception escaping a
    # slot: PyQt5 answers that with qFatal() in production -- the "the whole
    # program vanished" half of this row -- and pytest-qt's own hook would
    # otherwise turn it into a teardown error on a test that has already
    # failed for the right reason. Capturing it locally keeps the escape
    # itself assertable.
    with capture_exceptions() as escaped:
        _button(screen.result_card, "Open calibre.log").click()

    assert len(tried) == 1, (
        f"one click asked the OS to open the log {len(tried)} times"
    )
    assert not escaped, (
        "an exception left a slot; PyQt answers that with qFatal(), which is "
        "the user's 'the whole program vanished': "
        + repr([str(value) for _t, value, _tb in escaped])
    )
    assert warned, "the host cannot open files and nothing said so"
    assert any("calibre.log" in text for _title, text in warned), (
        f"the dialog does not name the path: {warned!r}"
    )
    assert window.isVisible(), "the window went away"


# ============================================================================
# 5 -- "show me the discrepancies"  (M-26)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-26: show_lvs_detail is ensureWidgetVisible on the first child of "
    "the scroll area, so on an unscrolled card nothing moves and nothing is "
    "highlighted -- and the destination cannot answer the question anyway",
)
def test_show_the_discrepancies_shows_the_user_something(
    qtbot, window: MainWindow, runs_root: Path, make_run_record, launches
) -> None:
    """""Show 3 discrepancies" 点了屏幕一动不动."""

    run_dir, record = _failed_lvs_run(runs_root, make_run_record)

    screen = _runs(window)
    assert screen.select_run(record.run_id)
    card = screen.result_card

    buttons = _buttons(card, "discrepanc")
    assert buttons, "the LVS failure row offers no way to see the discrepancies"
    button = buttons[0]
    assert "3" in button.text(), f"the button does not carry the count: {button.text()!r}"

    before = card._scroll.verticalScrollBar().value()
    qtbot.mouseClick(button, Qt.LeftButton)

    moved = card._scroll.verticalScrollBar().value() != before
    opened = [p for p in launches if p == run_dir / "results" / "lvs.report"]
    assert moved or opened, (
        "the button promised three discrepancies and the screen did not "
        "change: nothing scrolled, nothing was highlighted, and the archived "
        "report -- the only thing that holds the answer -- was not opened"
    )


# ============================================================================
# 6 -- "ten of my runs are missing"  (M-27)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-27: _index_entry has five silent skip paths, list_runs drops "
    "them, nothing counts them, and status_text reports the survivors with "
    "'nothing is ever overwritten' attached",
)
def test_the_runs_screen_says_how_many_directories_it_could_not_read(
    qtbot, window: MainWindow, runs_root: Path, make_run_record, frozen_clock
) -> None:
    """"十个 run 就是不在列表里，也没有任何东西说它们被丢掉了."""

    for index in range(3):
        _write_run(runs_root, make_run_record, slug=f"amp2-{index}")
        frozen_clock.tick(60)

    empty = runs_root / "20260101T000000Z_no-record"
    empty.mkdir()
    broken = runs_root / "20260102T000000Z_broken"
    broken.mkdir()
    (broken / "run.json").write_text("{ not json", encoding="utf-8")

    screen = _runs(window)
    assert len(screen.entries) == 3, "the three good runs did not list"

    status = screen.status_text()
    assert "2" in status and (
        "unread" in status.lower()
        or "could not" in status.lower()
        or "skipped" in status.lower()
    ), (
        "two directories under runs/ were dropped in silence while the status "
        f"line promised nothing is ever overwritten: {status!r}"
    )


# ============================================================================
# 7 -- "three of eight cells failed -- now what?"  (M-29)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-29: _on_task_finished passes neither when= nor code=, so "
    "COL_LAST_RUN is redrawn as an em dash for a run that just ended, and the "
    "row menu offers no route to the result although _live.run_dirs has it",
)
def test_a_cell_that_just_failed_can_be_followed_to_its_result(
    qtbot, window: MainWindow, runs_root: Path, workers: list[_FakeWorker]
) -> None:
    """"八个 cell 挂了三个，表里只剩一个 failed 和一个横杠."""

    cells = window.shell.page("cells")
    _add_rows(cells, 2)
    keys = _tick_cells(qtbot, cells, (0, 1, 2))
    assert len(keys) == 3, "the journey needs three rows in the run set"

    cells.run_bar.run_button().click()
    assert workers, "the Run click dispatched nothing"
    reporter = workers[0].kwargs["reporter"]

    failed_key = keys[1]
    run_dir = runs_root / "20260821T143205Z_amp2-ext"
    run_dir.mkdir(parents=True, exist_ok=True)
    reporter.run_started.emit(3, list(cells.run_bar.selected_stages()))
    for key in keys:
        reporter.task_started.emit(key, list(cells.run_bar.selected_stages()))
    reporter.run_dir_ready.emit(failed_key, run_dir)
    reporter.stage_finished.emit(failed_key, "calibre", "failed", "LVS INCORRECT")
    reporter.task_finished.emit(failed_key, "failed")
    for key in keys:
        if key != failed_key:
            reporter.task_finished.emit(key, "passed")
    workers[0].finished.emit()

    row = cells.row_of_key(failed_key)
    assert row is not None
    last_run = cells.table.item(row, COL_LAST_RUN)
    assert last_run is not None and last_run.text().strip() not in ("", "—", "-"), (
        "the run finished a second ago and the table's 'last run' column is "
        f"{None if last_run is None else last_run.text()!r}"
    )

    cells.set_selected_keys([failed_key])
    point = cells.table.visualItemRect(cells.table.item(row, COL_CELL)).center()
    actions = _open_row_menu(qtbot, cells.table, cells_mod, point)
    assert _menu_action(actions, "result") is not None, (
        "the row that just failed offers no route to its result: "
        + repr([a.text() for a in actions if a.text()])
    )


# ============================================================================
# 8 -- "one card must not carry another run's numbers"  (M-28)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-28: set_run resets five fields and not _delta, and _fill_lvs's "
    "lvs-is-None branch leaves it holding the previous selection's count",
)
def test_a_run_that_never_reached_lvs_shows_no_discrepancy_count(
    qtbot, window: MainWindow, runs_root: Path, make_run_record, frozen_clock
) -> None:
    """"一个连 LVS 都没跑到的 run，卡片却要给我看 17 个 discrepancy."

    The one assertion in this file that reads a widget accessor rather than
    a label, and the reason is worth writing down: the stale count's only
    route to something visible is the failure row's *Show N discrepancies*
    button, and that button appears only for a failure the card classifies
    as an LVS mismatch -- which needs ``results.lvs`` present, which is
    exactly what this run does not have. So today the carried-over number is
    one repaint away from the screen rather than on it. ``discrepancy_delta``
    is the public property the button label reads and the property the
    ledger names; asserting it here is asserting the number before it is
    drawn, not asserting a private flag.
    """

    _failed_lvs_run(runs_root, make_run_record, slug="amp2-lvs")
    frozen_clock.tick(3600)
    _crashed_dir, crashed = _write_run(
        runs_root,
        make_run_record,
        slug="amp2-crash",
        overall=TaskStatus.FAILED,
        stages=[
            StageRecord(
                key="si",
                stage="si",
                status=StageStatus.FAILED,
                exit_code=127,
                duration_s=0.4,
            )
        ],
    )

    screen = _runs(window)
    entries = screen.entries
    assert len(entries) == 2, "both runs must list for the carry-over to be possible"

    with_lvs = [e for e in entries if e.run_id != crashed.run_id][0]
    assert screen.select_run(with_lvs.run_id)
    assert screen.select_run(crashed.run_id)

    card = screen.result_card
    assert card._lvs_banner.text() == "not run", (
        f"the LVS band reads {card._lvs_banner.text()!r} for a run that "
        "crashed in si"
    )
    digits = [
        b.text()
        for b in card.findChildren(QPushButton)
        if any(ch.isdigit() for ch in b.text())
    ]
    assert not digits, f"a button on this card carries another run's number: {digits!r}"
    assert card.discrepancy_delta is None, (
        "the card is still holding the previous run's discrepancy count "
        f"({card.discrepancy_delta}); the LVS band says 'not run' and every "
        "label built from that number would say otherwise"
    )


# ============================================================================
# 9 -- "the stage produced a log -- the card says it did not"  (M-34/M-35)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-34: three messages pick 'no log' from path.is_file() alone and "
    "never distinguish 'none recorded' from 'recorded, now gone'; M-35: "
    "_on_stage_double_click returns in silence when the file is not there",
)
def test_a_recorded_log_that_is_gone_says_so_rather_than_saying_there_was_none(
    qtbot, window: MainWindow, runs_root: Path, make_run_record, launches
) -> None:
    """"说这个 stage 没产生 log，calibre.log 明明就在那儿" + 双击什么都不发生."""

    run_dir, record = _failed_lvs_run(runs_root, make_run_record)

    screen = _runs(window)
    assert screen.select_run(record.run_id)
    card = screen.result_card
    card.toggle_stage_logs(True)

    # Deleted *after* the card was drawn, which is the state the ledger
    # names: the record says where the log is, the file no longer is.
    (run_dir / "logs" / "calibre.log").unlink()

    item = _stage_row(card, "calibre")
    assert item is not None, "the calibre stage has no row in the tree"
    tree = _stage_tree(card)
    tree.setCurrentItem(item)

    open_log = _button(card, "Open log")
    assert "calibre.log" in open_log.toolTip(), (
        "the tooltip says the stage produced no log, when the run records "
        f"one and only the file is missing: {open_log.toolTip()!r}"
    )

    status: list[str] = []
    screen.status_message.connect(status.append)
    tree.itemDoubleClicked.emit(item, 0)
    assert status or launches, (
        "double-clicking the failed stage did nothing at all, on a row whose "
        "own hint advertises the gesture"
    )


# ============================================================================
# 10 -- "I renamed a run and it vanished"  (M-41/M-47)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-41: write_annotations' atomic write mkdirs the parent, so "
    "renaming a run whose directory is gone recreates it holding only "
    "annotations.json and the row then vanishes; M-47: _update_detail_header "
    "leaves Re-run enabled on a run whose record could not be read",
)
def test_renaming_a_run_whose_directory_is_gone_says_so_and_creates_no_ghost(
    qtbot, window: MainWindow, runs_root: Path, make_run_record, monkeypatch
) -> None:
    """"给 run 改了个名字，它就从列表里消失了" + Re-run 还亮着."""

    run_dir, record = _write_run(runs_root, make_run_record)

    screen = _runs(window)
    assert screen.select_run(record.run_id)
    entry = screen.selected_entry
    assert entry is not None

    shutil.rmtree(run_dir)

    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("Renamed", True))
    )
    warned: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(
            lambda _p=None, title="", text="", *a, **k: warned.append((title, text))
        ),
    )

    point = screen._list.visualItemRect(screen._list.item(0)).center()
    actions = _open_row_menu(qtbot, screen._list, runs_mod, point)
    rename = _menu_action(actions, "rename")
    assert rename is not None, "the row menu offers no Rename"
    rename.trigger()

    assert not run_dir.exists(), (
        "renaming a run whose directory was deleted put the directory back, "
        "holding nothing but annotations.json -- a ghost prune-runs will "
        "never remove"
    )
    assert warned, "the run is gone and the rename reported success"

    screen.select_run(record.run_id)
    rerun = _button(screen, "Re-run this cell")
    assert rerun.isEnabled() is False, (
        "Re-run is lit on a run whose directory is gone"
    )
    assert rerun.toolTip().strip(), "and it does not say why"


# ============================================================================
# 11 -- "I unticked follow and lost the log"  (M-30)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-30: one checkbox drives two unrelated behaviours -- autoscroll "
    "and the log-path push -- and _enter_running_state has already set the "
    "path to None, so unticking leaves no log for the whole run",
)
def test_unticking_follow_does_not_take_the_log_away(
    qtbot, window: MainWindow, runs_root: Path, workers: list[_FakeWorker]
) -> None:
    """"取消 follow 之后整场 run 就再也没有 log 了."""

    cells = window.shell.page("cells")
    bar = cells.run_bar
    follow = _checkbox(bar, "follow current stage")
    follow.click()
    assert bar.follows_current_stage() is False, "the box did not untick"

    keys = _tick_cells(qtbot, cells, (0,))
    bar.run_button().click()
    assert workers, "the Run click dispatched nothing"
    reporter = workers[0].kwargs["reporter"]

    run_dir = runs_root / "20260821T143205Z_amp2-ext"
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "logs" / "calibre.log").write_text("output\n", encoding="utf-8")
    reporter.run_dir_ready.emit(keys[0], run_dir)
    reporter.stage_started.emit(keys[0], "calibre")

    assert bar.log_path() is not None, (
        "a stage is running and the run bar has no log to show; unticking "
        "'follow' was meant to stop the pane jumping, not to empty it"
    )
    assert _button(bar, "Open in editor").isEnabled(), (
        "and there is no other way to reach the log either"
    )


# ============================================================================
# 12 -- "nothing is ticked under stages"  (M-33)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-33: _refresh_idle_text sets the enabled state from the stage set "
    "and setToolTip is never called on the run button anywhere in the file; "
    "_cannot_run_hint lists four reasons and this is not one of them",
)
def test_a_dead_run_button_says_why_it_is_dead(
    qtbot, window: MainWindow
) -> None:
    """"按钮写着 Run 3 cells，是灰的，没有任何地方说为什么."""

    cells = window.shell.page("cells")
    bar = cells.run_bar
    for stage in ("si", "strmout", "calibre", "quantus", "jivaro"):
        box = bar.stage_check(stage)
        if box.isChecked():
            box.click()
    assert bar.selected_stages() == (), "the stages did not untick"

    _tick_cells(qtbot, cells, (0,))
    button = bar.run_button()
    assert button.isEnabled() is False, "with no stage ticked, Run must be dead"

    assert "stage" in button.toolTip().lower(), (
        f"the disabled Run button's tooltip is {button.toolTip()!r} -- the "
        "user is told a cell is selected and nothing about the stages"
    )


# ============================================================================
# 13 -- "quit while a stubborn tool is running"  (M-36)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-36: stop_run_and_wait's default timeout is 5s while the kill "
    "sequence is SIGTERM -> 10s grace -> SIGKILL, so a tool that ignores the "
    "first signal cannot be gone in time and the first Quit always refuses",
)
def test_quitting_during_a_stubborn_tool_closes_on_the_first_attempt(
    qtbot, window: MainWindow, workers: list[_FakeWorker], monkeypatch
) -> None:
    """"跑着的时候退出，说 run 还没停，只能再退一次."""

    cells = window.shell.page("cells")
    _tick_cells(qtbot, cells, (0,))
    cells.run_bar.run_button().click()
    assert workers, "the Run click dispatched nothing"
    # The tool ignores SIGTERM and exits inside the 10s grace window, which
    # is longer than stop_run_and_wait is willing to wait for.
    _FakeWorker.wait_returns = False

    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda _p=None, title="", text="", *a, **k: (
                asked.append((title, text)) or QMessageBox.Close
            )
        ),
    )
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(
            lambda _p=None, title="", text="", *a, **k: told.append((title, text))
        ),
    )

    assert window.request_close() is True, (
        "the user said Close and the window refused: "
        + repr([t for t, _ in told])
    )


# ============================================================================
# 14 -- "two cells at once"  (M-31)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-31: _on_stage_started fires for every task and unconditionally "
    "repoints the single shared LogView, and set_active_log clears the "
    "document and restarts the offset on every call",
)
def test_the_log_pane_stays_with_the_cell_the_user_is_watching(
    qtbot, window: MainWindow, runs_root: Path, workers: list[_FakeWorker]
) -> None:
    """"jobs=4 的时候 log 面板一直在闪，一个 cell 的输出都读不完."""

    cells = window.shell.page("cells")
    _add_rows(cells, 1)
    cells.run_bar.set_jobs(2)
    keys = _tick_cells(qtbot, cells, (0, 1))
    assert len(keys) == 2

    cells.run_bar.run_button().click()
    assert workers, "the Run click dispatched nothing"
    reporter = workers[0].kwargs["reporter"]

    dirs = {}
    for index, key in enumerate(keys):
        run_dir = runs_root / f"20260821T14320{index}Z_cell-{index}"
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / "logs" / "si.log").write_text("x\n", encoding="utf-8")
        dirs[key] = run_dir
        reporter.run_dir_ready.emit(key, run_dir)

    # The user clicks the first row to watch it, then the second cell's
    # stage starts.
    cells.set_selected_keys([keys[0]])
    reporter.stage_started.emit(keys[0], "si")
    reporter.stage_started.emit(keys[1], "si")

    path = cells.run_bar.log_path()
    assert path is not None
    assert dirs[keys[0]] in path.parents, (
        "the log pane followed whichever worker thread reported last; the "
        f"user is watching {keys[0]} and the pane shows {path}"
    )


# ============================================================================
# 15 -- "a run that died leaves something I can act on"  (M-43)
# ============================================================================


@pytest.mark.xfail(
    strict=True,
    reason="M-43: the pending skeleton indexes and lists, prune-runs refuses "
    "it, the GUI offers no delete, and the card prints a pending chip over an "
    "empty stage table -- indistinguishable from a run in flight",
)
def test_an_abandoned_pending_run_explains_itself_and_can_be_removed(
    qtbot, window: MainWindow, runs_root: Path, make_run_record
) -> None:
    """"有一个 run 永远停在 pending，删也删不掉."""

    _run_dir, record = _write_run(
        runs_root,
        make_run_record,
        overall=TaskStatus.PENDING,
        stages=[],
    )

    screen = _runs(window)
    assert screen.select_run(record.run_id), "the skeleton did not list"
    card = screen.result_card

    text = _visible_text(card).lower()
    assert "no stage" in text or "in flight" in text or "abandoned" in text, (
        "the card draws a pending chip over an empty stage table and says "
        "nothing; the CLI's own words for this state are 'no stage recorded "
        "- the run was still in flight when it was written'"
    )

    point = screen._list.visualItemRect(screen._list.item(0)).center()
    actions = _open_row_menu(qtbot, screen._list, runs_mod, point)
    assert _menu_action(actions, "remove") or _menu_action(actions, "delete"), (
        "and there is no way to get rid of it: "
        + repr([a.text() for a in actions if a.text()])
    )
