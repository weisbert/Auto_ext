"""The state-transition x unsaved-edits matrix, as one parametrised test.

The matrix is "every place a user can edit state" x "every transition that
can rebuild a view, replace a document, or write to disk". Of its thirteen
columns exactly one was covered before this file existed: a recipe renamed
while a cell is bound to it. `File -> Revert pending edits` was triggered by
no test at all, no test ever had two screens dirty at the same time, and
every cross-screen path in `tests/ui/test_main_window.py` is driven by
``.emit()``-ing the screen's own signal -- which makes this whole class of
defect invisible by construction, because each one lives either *between the
widget and the signal* (the signal is never emitted) or *between the signal
and the next repaint*.

So every cell below stages a real edit through a widget, drives the
transition the way a user drives it (a list click, a menu action, a button),
and asserts what is on screen or what is in the file. Nothing asserts
``controller.is_dirty`` or ``pending_keys()``: ``docs/refactor/UX_VALIDATION.md``
section 2 already explains why those two assertions are exactly the shape of
the bug.

**These tests are rulers, not fixes.** A cell whose current outcome is worse
than "preserved or prompted" carries a strict ``xfail`` naming its master
row, so the suite stays green and the fix that lands has to flip the marker
-- under ``strict=True`` an ``XPASS`` fails, which is what makes the marker a
ledger entry rather than a suppression. The four cells at the bottom of the
table are the ones the design got right; they are here unmarked so that a
fixture that stops working shows up as a real failure instead of quietly
letting the broken cells keep xfailing.

Master rows: M-01, M-02, M-03, M-04, M-05, M-06, M-07, M-13, M-14, M-15.

``auto_ext/ui/main_window.py``, ``auto_ext/ui/screens/cells_screen.py`` and
``auto_ext/ui/widgets/run_bar.py`` are being rewritten in another session as
this is written. The assertions are about what the user sees, so they should
survive it; the accessors they reach through may need re-aiming once it
merges.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QObject, Qt, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import QAction, QDialog, QMessageBox  # noqa: E402

from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.screens import cells_screen as cells_mod  # noqa: E402
from auto_ext.ui.screens.cells_screen import COL_CELL, COL_OUT_VIEW  # noqa: E402
from auto_ext.ui.widgets.recipe_import_dialog import RecipeImportDialog  # noqa: E402

#: The workspace field every path cell below types into. A pattern, not a
#: literal path: the Project screen validates it, and a literal from the
#: developer's home has no business in a public repo's fixtures.
DSPF_FIELD = "dspf_out_pattern"
OTHER_FIELD = "intermediate_dir"


# ---- the context every cell is handed ---------------------------------------


@dataclass
class Ctx:
    """One loaded window plus the handles a matrix cell needs to drive it."""

    window: MainWindow
    root: Path
    qtbot: Any
    monkeypatch: Any
    fixtures: Path

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def recipes(self):
        return self.window.shell.page("recipes")

    @property
    def cells(self):
        return self.window.shell.page("cells")

    @property
    def project(self):
        return self.window.shell.page("project")

    def read(self, name: str) -> str:
        return (self.config / name).read_text(encoding="utf-8")


@pytest.fixture
def ctx(
    qtbot,
    monkeypatch,
    v2_config_dir_multi: Path,
    isolated_recipe_path: Path,
    fixtures_dir: Path,
) -> Ctx:
    """A shown window over a project with two recipes and two corners.

    ``v2_config_dir_multi`` because most of this matrix is about a *choice* --
    which recipe the list is showing, which recipe the run bar overrides to.
    Under the single-recipe fixture a control that offers nothing and a
    control that offers the only answer draw identically.

    Shown, because several cells depend on focus moving between two controls
    and a hidden widget has no focus to move.
    """

    window = MainWindow(
        config_dir=v2_config_dir_multi / "config", auto_ext_root=v2_config_dir_multi
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return Ctx(
        window=window,
        root=v2_config_dir_multi,
        qtbot=qtbot,
        monkeypatch=monkeypatch,
        fixtures=fixtures_dir,
    )


# ---- the gestures ------------------------------------------------------------


def _menu_action(window: MainWindow, text: str) -> QAction:
    """The File-menu entry whose label is ``text``, ampersands included.

    The window's ``_save_action`` / ``_revert_action`` attributes would be
    shorter, but the menu is what the user has: an action reachable only from
    an attribute is an action the user cannot press, and this file has to be
    able to tell the difference.
    """

    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is None:
            continue
        for action in menu.actions():
            if action.text() == text:
                return action
    raise AssertionError(
        f"no menu entry named {text!r}; the menu has "
        + repr(
            [
                a.text()
                for m in window.menuBar().actions()
                if m.menu() is not None
                for a in m.menu().actions()
            ]
        )
    )


def _type_option(screen, key: str, value: str) -> None:
    """Type ``value`` into the Recipes form's editor for ``key``.

    ``textEdited`` and not ``textChanged``: the option editors commit on
    ``textEdited``, which is the signal a keystroke produces and a
    programmatic ``setText`` does not.
    """

    editor = screen.editor(key)
    assert editor is not None, f"the Recipes form has no editor for {key!r}"
    line = editor.line_edit()
    line.setText(value)
    line.textEdited.emit(value)


def _shown_option(screen, key: str) -> str:
    editor = screen.editor(key)
    assert editor is not None, f"the Recipes form has no editor for {key!r}"
    return editor.line_edit().text()


def _click_recipe(ctx: Ctx, recipe_id: str) -> None:
    """Select a recipe by clicking its row, the way the list is used."""

    tree = ctx.recipes.recipe_list
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        if item.data(0, Qt.UserRole) == recipe_id:
            tree.setCurrentItem(item)
            return
    raise AssertionError(f"{recipe_id!r} is not a row in the recipe list")


def _selected_recipe_id(ctx: Ctx) -> str | None:
    item = ctx.recipes.recipe_list.currentItem()
    return None if item is None else item.data(0, Qt.UserRole)


def _recipe_ids(ctx: Ctx) -> list[str]:
    return [recipe.recipe_id for recipe in ctx.recipes.recipes()]


def _retype(ctx: Ctx, path: str, value: str) -> None:
    """Select-all and type into a Project field, leaving focus where it is."""

    row = ctx.project.row(path)
    assert row is not None, f"the Project screen has no row for {path!r}"
    edit = row.control()
    edit.setFocus()
    edit.selectAll()
    ctx.qtbot.keyClicks(edit, value)


def _focus_elsewhere(ctx: Ctx, path: str = OTHER_FIELD) -> None:
    """Move focus to another field -- the only commit gesture these rows have."""

    row = ctx.project.row(path)
    assert row is not None
    row.control().setFocus()


def _answer_question(ctx: Ctx, answer, log: list) -> None:
    """Make every confirm dialog answer ``answer`` and record what it asked."""

    def _question(_parent=None, title: str = "", text: str = "", *a, **k):
        log.append((title, text))
        return answer

    ctx.monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))


def _record_warnings(ctx: Ctx, log: list) -> None:
    for kind in ("warning", "critical", "information"):
        ctx.monkeypatch.setattr(
            QMessageBox,
            kind,
            staticmethod(
                lambda _p=None, title="", text="", *a, **k: log.append((title, text))
            ),
        )


class _FakeWorker(QObject):
    """A ``RunWorker`` stand-in for the two cells that press Run."""

    error = pyqtSignal(str)
    finished = pyqtSignal()
    instances: list["_FakeWorker"] = []

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.summary = SimpleNamespace(runs=[])
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        pass

    def request_cancel(self) -> None:
        pass


def _stub_the_runner(ctx: Ctx) -> list[_FakeWorker]:
    _FakeWorker.instances = []
    ctx.monkeypatch.setattr(cells_mod, "RunWorker", _FakeWorker)
    return _FakeWorker.instances


def _select_first_cell(ctx: Ctx) -> None:
    table = ctx.cells.table
    assert table.rowCount() >= 1, "the fixture project has no cell to run"
    item = table.item(0, COL_CELL)
    ctx.qtbot.mouseClick(
        table.viewport(),
        Qt.LeftButton,
        Qt.NoModifier,
        table.visualItemRect(item).center(),
    )


# ---- the cells ---------------------------------------------------------------
#
# One function per matrix cell. Each stages an edit, drives the transition and
# asserts; the table below carries the metadata and the marker.


def cell_r1_c1(ctx: Ctx) -> None:
    """R1 x C1 -- an edit survives clicking away to another recipe and back."""

    ids = _recipe_ids(ctx)
    assert len(ids) >= 2, "this cell needs two recipes to click between"
    here, there = ids[0], ids[1]

    _click_recipe(ctx, here)
    on_disk = _shown_option(ctx.recipes, "temperature_c")
    _type_option(ctx.recipes, "temperature_c", "125.0")

    _click_recipe(ctx, there)
    _click_recipe(ctx, here)

    assert _shown_option(ctx.recipes, "temperature_c") == "125.0", (
        f"the form came back showing the on-disk value {on_disk!r}; the edit "
        "is still in the controller's queue, so Save will write it anyway"
    )


def cell_r1_c8_edit_after_revert(ctx: Ctx) -> None:
    """R1 x C8 -- after Revert, the Recipes screen can still stage an edit."""

    _type_option(ctx.recipes, "temperature_c", "125.0")
    assert ctx.window.windowTitle().endswith("*"), (
        "the first edit did not even reach the window title"
    )

    _menu_action(ctx.window, "Re&vert pending edits").trigger()

    _type_option(ctx.recipes, "temperature_c", "85.0")

    assert ctx.window.windowTitle().endswith("*"), (
        "a second edit after Revert staged nothing: the screen's dirty flag "
        "never fell, so its rising edge -- the only thing that stages -- "
        "cannot happen again"
    )
    assert _menu_action(ctx.window, "&Save").isEnabled(), (
        "File -> Save is grey with an edit on screen"
    )


def cell_r1r3_c8_revert_repaints(ctx: Ctx) -> None:
    """R1+R3 x C8 -- Revert puts the on-disk values back *on the screens*."""

    table = ctx.cells.table
    assert table.rowCount() >= 1
    disk_cell = table.item(0, COL_OUT_VIEW).text()
    disk_temp = _shown_option(ctx.recipes, "temperature_c")

    table.item(0, COL_OUT_VIEW).setText("av_ext_reverted")
    _type_option(ctx.recipes, "temperature_c", "125.0")

    _menu_action(ctx.window, "Re&vert pending edits").trigger()

    assert table.item(0, COL_OUT_VIEW).text() == disk_cell, (
        "the Cells table still shows the reverted edit, and its next repaint "
        "re-stages the whole edited book"
    )
    assert _shown_option(ctx.recipes, "temperature_c") == disk_temp, (
        "the Recipes form still shows the reverted edit"
    )


def cell_r5_ctrl_s(ctx: Ctx) -> None:
    """R5 x Save -- Ctrl+S writes what is in the box, not what was in it."""

    typed = "${WORK_ROOT}/typed_without_leaving_the_box_{cell}.dspf"
    _retype(ctx, DSPF_FIELD, typed)

    save = _menu_action(ctx.window, "&Save")
    save.trigger()

    assert typed in ctx.read("workspace.yaml"), (
        "the value in the box never reached the file"
        + ("" if save.isEnabled() else "; File -> Save was not even enabled")
        + " -- every Project control commits on focus-out only, and a "
        "shortcut does not move focus"
    )


def cell_r5_retyped_original(ctx: Ctx) -> None:
    """R5 x Save -- typing the original back un-stages the mistake."""

    before = ctx.read("workspace.yaml")
    row = ctx.project.row(DSPF_FIELD)
    assert row is not None
    original = str(row.value())

    _retype(ctx, DSPF_FIELD, "${WORK_ROOT}/mistyped_{cell}.dspf")
    _focus_elsewhere(ctx)
    _retype(ctx, DSPF_FIELD, original)
    _focus_elsewhere(ctx)

    _menu_action(ctx.window, "&Save").trigger()

    assert ctx.read("workspace.yaml") == before, (
        "the abandoned intermediate value was written: the screen goes clean "
        "when the value comes back, but nothing un-stages it"
    )


def cell_r10_c1_import(ctx: Ctx) -> None:
    """R10 x accept -- the imported recipe arrives in the library."""

    ctx.monkeypatch.setattr(
        RecipeImportDialog, "exec_", lambda self: QDialog.Rejected
    )
    before = set(_recipe_ids(ctx))

    ctx.recipes.import_button().click()
    dialog = ctx.recipes.import_dialog()
    assert dialog is not None, "Import... opened no dialog"
    ctx.qtbot.addWidget(dialog)

    assert dialog.add_paths([ctx.fixtures / "raw" / "gui_export.ext.cmd"]) == 1
    assert dialog.analyse() is True, "the sample file did not analyse"
    dialog.import_button().click()

    after = set(_recipe_ids(ctx))
    assert after - before, (
        "the import reported success and the library is unchanged: nothing "
        "listens to recipe_imported, so the produced Recipe is dropped"
    )


def cell_r1_c6_run_unsaved(ctx: Ctx) -> None:
    """R1 x C6 -- pressing Run on unsaved edits says so before it runs.

    The other half of this row -- that ``run.json`` cannot afterwards tell a
    run made from unsaved edits from one made after a Save -- belongs to the
    record writer in ``auto_ext/core/runner.py`` and is asserted there, not
    here: this file only sees what the user sees at the moment of the click.
    """

    workers = _stub_the_runner(ctx)
    said: list[tuple[str, str]] = []
    _answer_question(ctx, QMessageBox.Save, said)
    _record_warnings(ctx, said)
    status: list[str] = []
    ctx.cells.status_message.connect(status.append)

    _type_option(ctx.recipes, "temperature_c", "125.0")
    assert _menu_action(ctx.window, "&Save").isEnabled(), "the edit did not stage"

    _select_first_cell(ctx)
    ctx.cells.run_bar.set_recipe_override(_recipe_ids(ctx)[0])
    ctx.cells.run_bar.run_button().click()
    assert workers, "the Run click dispatched nothing; this cell is about the notice"

    spoken = [text for _title, text in said] + status
    assert any(
        word in text.lower()
        for text in spoken
        for word in ("unsaved", "not saved", "save them", "pending edit")
    ), (
        "the run was built from the unsaved recipe on screen and nothing said "
        f"so; all the user was told was {spoken!r}"
    )


def cell_r1_c11_scoped_save(ctx: Ctx) -> None:
    """R1 x C11 -- the Recipes screen's Save writes the recipe, and says what else.

    Four Save buttons over one queue is the design; the defect is that three
    of them present themselves as a scoped commit. Either the write is scoped
    or the status line names the other file it touched.
    """

    table = ctx.cells.table
    assert table.rowCount() >= 1
    table.item(0, COL_OUT_VIEW).setText("av_ext_rode_along")

    status: list[str] = []
    ctx.recipes.status_changed.connect(status.append)
    _type_option(ctx.recipes, "temperature_c", "125.0")
    ctx.recipes.save_button().click()

    wrote_cells = "av_ext_rode_along" in ctx.read("cells.yaml")
    named_cells = any("cell" in line.lower() for line in status)
    assert (not wrote_cells) or named_cells, (
        "the Recipes screen's Save flushed the whole queue -- cells.yaml was "
        f"rewritten too -- and the only thing it said was {status!r}"
    )


def cell_r1_c4_save_keeps_the_selection(ctx: Ctx) -> None:
    """R1 x Save -- saving does not throw the selection back to row one."""

    ids = _recipe_ids(ctx)
    assert len(ids) >= 2
    wanted = ids[1]
    _click_recipe(ctx, wanted)
    _type_option(ctx.recipes, "temperature_c", "125.0")

    _menu_action(ctx.window, "&Save").trigger()

    assert _selected_recipe_id(ctx) == wanted, (
        "Save reloaded the library with no selection to restore, so the list "
        "fell back to its first row and the user is editing another recipe"
    )


def cell_r13_c1_override_dropped(ctx: Ctx) -> None:
    """R13 x recipe deleted -- a set override does not quietly become "per row"."""

    ids = _recipe_ids(ctx)
    assert len(ids) >= 2
    doomed = ids[1]

    bar = ctx.cells.run_bar
    bar.set_recipe_override(doomed)
    assert bar.recipe_override() == doomed, "the override did not take"

    status: list[str] = []
    ctx.cells.status_message.connect(status.append)
    ctx.recipes.status_changed.connect(status.append)

    _click_recipe(ctx, doomed)
    ctx.recipes.delete_button().click()

    told = any("override" in line.lower() for line in status)
    assert bar.recipe_override() is not None or told, (
        "the override the user set is gone and the combo now reads 'per row' "
        "-- different run semantics, with nothing said: " + repr(status)
    )


# ---- the cells the design got right -----------------------------------------


def cell_c12_rename_reaches_the_table(ctx: Ctx) -> None:
    """R2 x C12 -- a rename reaches the Cells table without a save.

    The one cell of the matrix that was already covered, kept here as the
    control: if this fails, the fixture is broken rather than the app.
    """

    ids = _recipe_ids(ctx)
    _click_recipe(ctx, ids[0])
    name_edit = ctx.recipes.name_edit()
    name_edit.setText("Renamed while bound")
    name_edit.textEdited.emit("Renamed while bound")

    assert ("Renamed while bound") in [
        name for _rid, name in ctx.cells.recipe_choices()
    ], "the Cells screen's recipe column did not hear the rename"


def cell_c4_reload_asks_first(ctx: Ctx) -> None:
    """R1 x C4 -- File -> Reload with edits pending asks before discarding."""

    asked: list[tuple[str, str]] = []
    _answer_question(ctx, QMessageBox.Cancel, asked)
    _type_option(ctx.recipes, "temperature_c", "125.0")

    _menu_action(ctx.window, "&Reload from disk").trigger()

    assert asked, "Reload discarded the pending edits without asking"
    assert _shown_option(ctx.recipes, "temperature_c") == "125.0", (
        "the user cancelled and the edit went anyway"
    )


def cell_c10_quit_asks_first(ctx: Ctx) -> None:
    """R1 x C10 -- File -> Quit with edits pending asks before discarding."""

    asked: list[tuple[str, str]] = []
    _answer_question(ctx, QMessageBox.Cancel, asked)
    _type_option(ctx.recipes, "temperature_c", "125.0")

    _menu_action(ctx.window, "&Quit").trigger()

    assert asked, "Quit closed over the pending edits without asking"
    assert ctx.window.isVisible(), "the user cancelled and the window closed anyway"


def cell_c1_clean_switch_is_lossless(ctx: Ctx) -> None:
    """R1 x C1 with nothing pending -- the control for cell_r1_c1.

    Same two clicks, no edit staged. This is what makes the xfail on
    ``R1xC1`` mean "the *edit* was lost" rather than "the list is broken".
    """

    ids = _recipe_ids(ctx)
    assert len(ids) >= 2
    here, there = ids[0], ids[1]
    _click_recipe(ctx, here)
    shown = _shown_option(ctx.recipes, "temperature_c")
    _click_recipe(ctx, there)
    _click_recipe(ctx, here)
    assert _shown_option(ctx.recipes, "temperature_c") == shown
    assert _selected_recipe_id(ctx) == here


# ---- the matrix -------------------------------------------------------------


@dataclass(frozen=True)
class MatrixCell:
    """One (edit site, transition) pair, with what should happen and what does.

    ``expected`` is the outcome the design owes the user -- ``preserved`` or
    ``prompted``. ``today`` is what the app does now, seeded from the read-only
    review: ``discarded`` silently, ``desynchronised`` (screen != controller !=
    disk), or ``used`` (the unsaved state reaches disk or the EDA tool
    unannounced). A cell whose ``today`` differs from its ``expected`` is
    marked ``xfail(strict=True)``; when a row is fixed, ``today`` moves to
    match ``expected`` and the cell becomes a plain regression test. It keeps
    its ``master`` id either way, so the coverage assertion below still holds
    the row and nobody can drop it quietly.
    """

    cell_id: str
    site: str
    transition: str
    expected: str
    today: str
    master: str
    symptom: str
    check: Callable[[Ctx], None]


MATRIX: tuple[MatrixCell, ...] = (
    MatrixCell(
        cell_id="R1xC1",
        site="Recipes form",
        transition="select another recipe and come back",
        expected="preserved",
        today="desynchronised",
        master="M-01",
        symptom="_on_current_item_changed reads the stale library set_recipes "
        "was handed, never the controller's staged copy",
        check=cell_r1_c1,
    ),
    MatrixCell(
        cell_id="R1xC8-again",
        site="Recipes form",
        transition="File -> Revert pending edits, then edit again",
        expected="preserved",
        today="discarded",
        master="M-02",
        symptom="MainWindow stages the recipe on the rising edge of the "
        "screen's dirty flag, and Revert leaves that flag stuck true",
        check=cell_r1_c8_edit_after_revert,
    ),
    MatrixCell(
        cell_id="R1+R3xC8",
        site="Recipes form and Cells table, both dirty",
        transition="File -> Revert pending edits",
        expected="preserved",
        today="desynchronised",
        master="M-03",
        symptom="the Revert action is wired straight to the controller and "
        "re-pushes no screen, so both keep showing the reverted edits",
        check=cell_r1r3_c8_revert_repaints,
    ),
    MatrixCell(
        # M-04 is FIXED: a Project QLineEdit stages on ``textEdited`` now, so
        # the value in the box no longer waits for focus to leave before it
        # counts as an edit. ``today`` follows the app, so the strict xfail
        # this row used to carry is gone and the cell is a regression guard.
        cell_id="R5xSave",
        site="Project field",
        transition="File -> Save without leaving the box",
        expected="preserved",
        today="preserved",
        master="M-04",
        symptom="",
        check=cell_r5_ctrl_s,
    ),
    MatrixCell(
        cell_id="R5xSave-undo",
        site="Project field",
        transition="retype the original, then File -> Save",
        expected="preserved",
        today="wrong value written",
        master="M-05",
        symptom="_on_project_edited stages non-None payloads and has no "
        "un-stage branch, so the queue keeps the abandoned value",
        check=cell_r5_retyped_original,
    ),
    MatrixCell(
        cell_id="R10xC1",
        site="Recipe import dialog",
        transition="confirm the import",
        expected="preserved",
        today="discarded",
        master="M-06",
        symptom="recipe_imported has no receiver; the status line reports the "
        "import as done anyway",
        check=cell_r10_c1_import,
    ),
    MatrixCell(
        cell_id="R1xC6",
        site="Recipes form",
        transition="press Run",
        expected="prompted",
        today="silently used",
        master="M-07",
        symptom="every controller accessor is staged-first and the run path "
        "has no dirty check anywhere",
        check=cell_r1_c6_run_unsaved,
    ),
    MatrixCell(
        cell_id="R1xC11",
        site="Cells table, staged",
        transition="the Recipes screen's own Save",
        expected="prompted",
        today="silently used",
        master="M-13",
        symptom="there is no per-document write: all three scoped-looking "
        "Saves flush the whole queue and name only their own file",
        check=cell_r1_c11_scoped_save,
    ),
    MatrixCell(
        cell_id="R1xC4-selection",
        site="Recipes list selection",
        transition="File -> Save",
        expected="preserved",
        today="discarded",
        master="M-14",
        symptom="save() ends in load(), whose set_recipes call passes no "
        "select= and falls back to the first row",
        check=cell_r1_c4_save_keeps_the_selection,
    ),
    MatrixCell(
        cell_id="R13xC12",
        site="Run bar recipe override",
        transition="delete the recipe it names",
        expected="prompted",
        today="discarded",
        master="M-15",
        symptom="set_recipe_override falls back to index 0 -- 'per row', a "
        "different run semantics -- when findData misses",
        check=cell_r13_c1_override_dropped,
    ),
    # -- the cells that are already right, as controls --
    MatrixCell(
        cell_id="R2xC12",
        site="Recipe title",
        transition="rename while a cell is bound to it",
        expected="preserved",
        today="preserved",
        master="-",
        symptom="",
        check=cell_c12_rename_reaches_the_table,
    ),
    MatrixCell(
        cell_id="R1xC4",
        site="Recipes form",
        transition="File -> Reload from disk",
        expected="prompted",
        today="prompted",
        master="-",
        symptom="",
        check=cell_c4_reload_asks_first,
    ),
    MatrixCell(
        cell_id="R1xC10",
        site="Recipes form",
        transition="File -> Quit",
        expected="prompted",
        today="prompted",
        master="-",
        symptom="",
        check=cell_c10_quit_asks_first,
    ),
    MatrixCell(
        cell_id="R1xC1-clean",
        site="Recipes form, nothing pending",
        transition="select another recipe and come back",
        expected="preserved",
        today="preserved",
        master="-",
        symptom="",
        check=cell_c1_clean_switch_is_lossless,
    ),
)


def _params():
    for cell in MATRIX:
        broken = cell.today != cell.expected
        marks = (
            pytest.mark.xfail(
                strict=True,
                reason=(
                    f"{cell.master}: {cell.site} x {cell.transition} is "
                    f"{cell.today}, not {cell.expected} -- {cell.symptom}"
                ),
            ),
        )
        yield pytest.param(cell, id=cell.cell_id, marks=marks if broken else ())


@pytest.mark.parametrize("cell", list(_params()))
def test_an_edit_survives_the_transition(cell: MatrixCell, ctx: Ctx) -> None:
    """Stage the edit through the widget, drive the transition, read the screen."""

    cell.check(ctx)


def test_the_matrix_covers_every_high_severity_transition_row() -> None:
    """The table is the coverage claim, so the table itself is asserted.

    A cell quietly dropped from :data:`MATRIX` would take its master row's
    only regression test with it and nothing else would notice; naming the
    ids here means removing one is a decision somebody has to make in the
    open.
    """

    covered = {cell.master for cell in MATRIX if cell.master != "-"}
    assert covered == {
        "M-01",
        "M-02",
        "M-03",
        "M-04",
        "M-05",
        "M-06",
        "M-07",
        "M-13",
        "M-14",
        "M-15",
    }
    assert len({cell.cell_id for cell in MATRIX}) == len(MATRIX), "duplicate cell id"
    assert [c for c in MATRIX if c.today == c.expected], (
        "a matrix of nothing but xfails cannot tell a broken app from a "
        "broken fixture"
    )
