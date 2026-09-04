"""End-to-end journeys, driven the way a user drives them.

The rule that makes this file worth having
------------------------------------------
**A journey test may only touch what a user can touch, and may only assert on
what a user can see.** No private attribute, no controller method, no
``screen._working``, no "assert the signal fired". Click the button; type in
the box; read the file on disk; **then read the screen back.**

That rule is not stylistic. The Recipes screen's ``Save`` button shipped
staging its recipe into a queue and writing nothing -- so an edit made in the
GUI came back missing on the next launch -- and it shipped with tests around
it that passed, because they asserted ``save_requested`` was emitted and that
the controller had been staged. Both were true. Both are what the bug looks
like. The only assertion that could tell the difference is the one that reads
the bytes afterwards, and nothing in the suite made it.

Why the rule now has a fourth clause
------------------------------------
The first three clauses stopped at the disk, and the disk is the one place a
user never looks. Every invariant this project enforces ran the same
direction -- catalog to widget, widget to disk, recipe to rendered file -- and
nothing at all ran back. Two bugs lived in the gap and neither was reachable
by the suite:

* renaming a recipe wrote ``name:`` to the file correctly (asserted below,
  green throughout) while the list on the left threw the new name away on the
  next repaint, because a refactor changed what column 0 *means* and left a
  second writer behind;
* the Cells table's ``recipe`` column accepted a choice that no dispatch ever
  read.

Both are read-back defects. A write-only assertion cannot see either, and no
amount of them adds up to one. So: after the bytes, **reload and assert what
is on screen** -- the value, and the label the user actually reads.

Fixtures follow the same reasoning. Anything that draws a *choice* uses
``v2_config_dir_multi``: with one recipe and one corner in the tree, a control
offering nothing and a control offering the only answer look identical, and
the dispatch's "refuse to guess between candidates" branch is unreachable.

So the tests here are few, slow and blunt on purpose. They cover the seam
between "the widget did its part" and "the user got what they asked for",
which is exactly the seam a widget test cannot see across.

See ``tests/ui/test_reachability.py`` for the other half of the answer -- can
the user find the control at all -- and ``docs/refactor/UX_VALIDATION.md`` for
the part neither file can automate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")

from auto_ext.ui.main_window import MainWindow  # noqa: E402


@pytest.fixture
def window(qtbot, v2_config_dir: Path, isolated_recipe_path: Path) -> MainWindow:
    win = MainWindow(config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir)
    qtbot.addWidget(win)
    return win


@pytest.fixture
def window_multi(
    qtbot, v2_config_dir_multi: Path, isolated_recipe_path: Path
) -> MainWindow:
    """A window over a project with two recipes and two corners.

    For every journey whose subject is a *choice*. See the fixture note in
    this module's docstring.
    """

    win = MainWindow(
        config_dir=v2_config_dir_multi / "config", auto_ext_root=v2_config_dir_multi
    )
    qtbot.addWidget(win)
    return win


def _sole_recipe_file(v2_config_dir: Path) -> Path:
    files = sorted((v2_config_dir / "recipes").glob("*.yaml"))
    assert files, "the fixture project has no recipe to edit"
    return files[0]


# ---- the journey the office session actually took ---------------------------


def test_change_a_value_press_save_and_the_file_on_disk_has_it(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """"我在GUI里面更改了温度，然后点击保存，结果并没有被写入."

    The whole point is the last two lines: the assertion is against the file,
    not against a signal, a staged document or a dirty flag.
    """

    path = _sole_recipe_file(v2_config_dir)
    screen = window.shell.page("recipes")

    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")

    screen.save_button().click()

    assert "temperature_c: 125.0" in path.read_text(encoding="utf-8")


def test_rename_a_recipe_press_save_and_the_file_on_disk_has_it(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """"recipes的名字也没有改" -- there was no control at all; now there is."""

    path = _sole_recipe_file(v2_config_dir)
    screen = window.shell.page("recipes")

    screen.name_edit().setText("RC coupled 125C")
    screen.name_edit().textEdited.emit("RC coupled 125C")

    screen.save_button().click()

    assert "name: RC coupled 125C" in path.read_text(encoding="utf-8")


def test_renaming_a_recipe_does_not_leave_the_list_saying_two_things(
    window_multi: MainWindow
) -> None:
    """"改名之后左边并没有跟着改" -- the two writers to column 0 disagree.

    ``set_recipes`` builds column 0 from ``recipe_id``; ``_on_name_edited``
    writes ``name`` into it. So the row says one thing while you type and
    another after the next repaint, and a save repaints. Whatever the design
    answer is about *whether* the list should carry the name at all, the two
    writers have to agree -- that part needs no decision, and this asserts
    only that part.

    Run over the recipe that has a ``description``: with the description-less
    fixture recipe the second line falls back to ``name``, so the name was on
    screen by accident and the disagreement was invisible.
    """

    screen = window_multi.shell.page("recipes")
    described = next(r for r in screen.recipes() if r.description)
    screen.select_recipe(described.recipe_id)

    screen.name_edit().setText("RC coupled 125C")
    screen.name_edit().textEdited.emit("RC coupled 125C")
    while_typing = screen.list_row_lines(described.recipe_id)

    screen.save_button().click()
    after_repaint = screen.list_row_lines(described.recipe_id)

    assert while_typing == after_repaint, (
        "the list row changed under the user without the user touching it: "
        f"{while_typing!r} while typing, {after_repaint!r} after the save"
    )


def test_a_renamed_recipe_reads_back_under_its_new_name(
    window_multi: MainWindow
) -> None:
    """"改名之后左边并没有跟着改" -- the user's ruling, 2026-08-25.

    Artboard ``G`` had dropped ``name`` from the list. Whatever the column is
    technically showing, a rename that leaves the list unmoved reads as a
    rename that did not work, so ``name`` is the first line now and
    ``recipe_id`` the second. Run over the recipe that has a ``description``:
    with a description-less one the old layout showed ``name`` by accident.
    """

    screen = window_multi.shell.page("recipes")
    described = next(r for r in screen.recipes() if r.description)
    screen.select_recipe(described.recipe_id)

    screen.name_edit().setText("RC coupled 125C")
    screen.name_edit().textEdited.emit("RC coupled 125C")
    screen.save_button().click()

    first, second = screen.list_row_lines(described.recipe_id)
    assert first == "RC coupled 125C", "the list did not follow the rename"
    assert second == described.recipe_id, "the id lost its line"


def test_a_rename_reaches_the_cells_table_without_waiting_for_a_save(
    window_multi: MainWindow
) -> None:
    """The Cells column and the run bar spell recipes by name too.

    They were refreshed on new / duplicate / delete / save but not on an
    edit, so both went on showing the old name while the Recipes screen
    showed the new one.
    """

    recipes = window_multi.shell.page("recipes")
    target = recipes.recipes()[0].recipe_id
    recipes.select_recipe(target)

    recipes.name_edit().setText("RC coupled 125C")
    recipes.name_edit().textEdited.emit("RC coupled 125C")

    cells = window_multi.shell.page("cells")
    assert dict(cells.recipe_choices())[target] == "RC coupled 125C"


def test_a_recipe_picked_for_a_row_is_still_there_next_launch(
    window_multi: MainWindow, v2_config_dir_multi: Path, qtbot
) -> None:
    """The other half of "填写了recipe": it has to still be there tomorrow.

    The binding was screen state, so it reached neither ``cells.yaml`` nor
    the next launch. Reloading in a second window is the closest a test gets
    to relaunching the app.
    """

    cells = window_multi.shell.page("cells")
    key = cells.cells().keys[0]
    chosen = window_multi.shell.page("recipes").recipes()[1].recipe_id

    cells.set_recipe_binding(key, chosen)
    assert cells.save_button().isEnabled(), "binding a recipe must offer Save"
    cells.save_button().click()

    assert chosen in (v2_config_dir_multi / "config" / "cells.yaml").read_text(
        encoding="utf-8"
    )

    second = MainWindow(
        config_dir=v2_config_dir_multi / "config", auto_ext_root=v2_config_dir_multi
    )
    qtbot.addWidget(second)
    assert second.shell.page("cells").recipe_bindings()[key] == chosen


def test_the_recipe_chosen_for_a_row_is_the_recipe_that_row_runs(
    window_multi: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Cells table offers a ``recipe`` per row. Ask it to run one.

    Two recipes in the library on purpose: with one, the dispatch falls
    through to "there is only one candidate, use it" and the column's
    contribution is invisible either way.

    What the user sees is either a run starting or a dialog saying it cannot.
    Both are asserted, because "it refused" is the failure being hunted.
    """

    from PyQt5.QtWidgets import QMessageBox

    from auto_ext.ui.screens import cells_screen as cells_mod

    started: list[dict] = []

    class FakeWorker:
        def __init__(self, **kwargs: object) -> None:
            started.append(dict(kwargs))

        def __getattr__(self, name: str):
            return _Signal()

    class _Signal:
        def connect(self, *_a, **_k) -> None:
            return None

        def __call__(self, *_a, **_k) -> None:
            return None

    monkeypatch.setattr(cells_mod, "RunWorker", FakeWorker)

    refused: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: refused.append(a) or QMessageBox.Ok),
    )

    recipes = window_multi.shell.page("recipes")
    chosen = recipes.recipes()[1].recipe_id

    cells = window_multi.shell.page("cells")
    keys = tuple(cells.cells().keys)
    cells.set_checked_keys(keys)
    for key in keys:
        cells.set_recipe_binding(key, chosen)

    cells.start_run()

    assert not refused, f"the run was refused: {refused[0][2] if refused else ''}"
    assert started, "nothing was dispatched"
    batches = started[0]["batches"]
    assert [b.recipe.recipe_id for b in batches] == [chosen], (
        "the rows ran a recipe other than the one chosen for them"
    )
    assert sorted(t.task_id for t in batches[0].tasks) == sorted(keys)


def test_saving_leaves_the_screen_clean_and_on_the_same_recipe(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """A Save that works has to *look* like it worked.

    The staging-only bug was doubly invisible: nothing was written, and the
    header went on saying ``unsaved`` afterwards, so the only feedback the
    button gave was none at all.
    """

    screen = window.shell.page("recipes")
    before = screen.current_recipe().recipe_id

    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    assert screen.save_button().isEnabled(), "an edited recipe must offer Save"

    screen.save_button().click()

    assert screen.save_button().isEnabled() is False, "still dirty after a save"
    assert screen.current_recipe().recipe_id == before, "the selection jumped"


def test_an_edit_survives_a_reload_of_the_project(
    window: MainWindow, v2_config_dir: Path, qtbot
) -> None:
    """The user's actual test: "我下次打开还是这样".

    Reloading from disk is the closest thing to relaunching the app that a
    test can do without a second process, and it fails for the same reason a
    relaunch did.
    """

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    screen.save_button().click()

    second = MainWindow(config_dir=v2_config_dir / "config", auto_ext_root=v2_config_dir)
    qtbot.addWidget(second)
    reopened = second.shell.page("recipes")

    assert reopened.editor("temperature_c").value() == 125.0


def test_an_unsaved_edit_shows_up_as_unsaved_everywhere(
    window: MainWindow,
) -> None:
    """One dirty state, not two.

    The screen used to be the only thing that knew about an option edit: it
    said "unsaved" while the window title had no star and File -> Save was
    greyed out. Three places claiming to report the same fact, two of them
    wrong, is how an edit gets closed away.
    """

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")

    assert window.controller.is_dirty is True
    assert window.windowTitle().endswith("*")
    assert "unsaved" in window.shell.status_right()


def test_revert_actually_puts_the_old_value_back(window: MainWindow) -> None:
    """Revert reads from disk, not from the queue it is undoing."""

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    before = editor.value()

    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    assert screen.editor("temperature_c").value() == 125.0

    screen.revert_button().click()

    assert screen.editor("temperature_c").value() == before
    assert window.controller.is_dirty is False


# ---- closing the window -----------------------------------------------------


def _quit_action(window: MainWindow):
    from PyQt5.QtWidgets import QMenu

    for menu in window.menuBar().findChildren(QMenu):
        for action in menu.actions():
            if "Quit" in action.text():
                return action
    raise AssertionError("File -> Quit is gone")


def test_quitting_with_unsaved_edits_asks_instead_of_dropping_them(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There was no close guard at all, so the answer was silently "drop".

    Same complaint as the Save button, one step later: the user makes an
    edit, closes the window, and next launch it is not there.
    """

    from PyQt5.QtWidgets import QMessageBox

    screen = window.shell.page("recipes")
    editor = screen.editor("temperature_c")
    editor.line_edit().setText("125.0")
    editor.line_edit().textEdited.emit("125.0")
    assert window.controller.is_dirty

    asked: list[str] = []

    def fake_question(_parent, title, *_args, **_kwargs):
        asked.append(title)
        return QMessageBox.Cancel

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))

    _quit_action(window).trigger()

    assert asked, "the window quit without asking"
    assert window.isVisible() is False or window.controller.is_dirty
    assert window.controller.is_dirty, "Cancel must not discard the edit"


def test_quitting_clean_asks_nothing(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PyQt5.QtWidgets import QMessageBox

    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.Cancel),
    )

    assert window.request_close() is True
    assert asked == []


def test_quitting_during_a_run_asks_and_stops_the_thread(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quitting mid-run used to drop the last reference to a live QThread."""

    from PyQt5.QtWidgets import QMessageBox

    from auto_ext.ui.screens.cells_screen import CellsScreen

    stopped: list[bool] = []
    monkeypatch.setattr(CellsScreen, "is_running", lambda self: True)
    monkeypatch.setattr(
        CellsScreen,
        "stop_run_and_wait",
        lambda self, timeout_ms=5000: stopped.append(True) or True,
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Close)
    )

    assert window.request_close() is True
    assert stopped == [True], "the run was never actually stopped"


def test_a_run_the_user_will_not_abandon_keeps_the_window_open(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PyQt5.QtWidgets import QMessageBox

    from auto_ext.ui.screens.cells_screen import CellsScreen

    monkeypatch.setattr(CellsScreen, "is_running", lambda self: True)
    monkeypatch.setattr(
        CellsScreen,
        "stop_run_and_wait",
        lambda self, timeout_ms=5000: pytest.fail("the run must not be touched"),
    )
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Cancel)
    )

    assert window.request_close() is False


# ---- the cell-side journey --------------------------------------------------


def test_type_an_extracted_view_into_the_cells_table_and_it_reaches_the_file(
    window: MainWindow, v2_config_dir: Path
) -> None:
    """"没有更改ext输出的文件名称的" -- out_file was tooltip-only."""

    from auto_ext.ui.screens.cells_screen import COL_OUT_VIEW

    cells = window.shell.page("cells")
    table = cells.table
    item = table.item(0, COL_OUT_VIEW)
    assert item is not None, "the extracted view has no cell to type into"

    item.setText("av_ext_125c")
    # The screen's own Save, not File -> Save: the cell table had no Save at
    # all, so the only way to write cells.yaml was a menu the user had no
    # reason to open.
    assert cells.save_button().isEnabled(), "an edited table must offer Save"
    cells.save_button().click()

    text = (v2_config_dir / "config" / "cells.yaml").read_text(encoding="utf-8")
    assert "av_ext_125c" in text
    assert cells.save_button().isEnabled() is False
