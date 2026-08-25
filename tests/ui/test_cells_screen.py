"""Cells screen tests.

Four things here are load-bearing and the rest is detail:

* the screen's derived minimum size, because artboard ``1j`` fixes the
  window floor at 940x560 and the old Project tab alone demanded 1001px of
  height;
* the round trip through :mod:`auto_ext.model.cells`, because an in-place
  edit that bypasses validation is how a table grows two rows with the same
  key;
* the deferred context menu, because a synchronous ``exec_()`` under X11 is
  the "right-click twice" bug this codebase has already paid for once;
* the dispatch, because the screen must drive the *existing* worker rather
  than grow a second threading story.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QObject, QPoint, Qt, pyqtSignal  # noqa: E402
from PyQt5.QtWidgets import QAbstractItemView, QLabel, QTableWidget  # noqa: E402

from auto_ext.model.cells import CellBook, CellEntry  # noqa: E402
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.screens import cells_screen as cells_mod  # noqa: E402
from auto_ext.ui.screens.cells_screen import (  # noqa: E402
    COL_CELL,
    COL_CHECK,
    COL_GROUND,
    COL_LAST_RUN,
    COL_LIBRARY,
    COL_RECIPE,
    COL_STAGES,
    COL_STATUS,
    COL_VIEWS,
    MODE_COMPACT,
    MODE_RUNNING,
    MODE_WIDE,
    CellsScreen,
    RowStatus,
    install_cells_page,
)
from auto_ext.ui.shell import Shell  # noqa: E402
from auto_ext.ui.widgets.run_bar import StageChipStrip  # noqa: E402

#: The library the ``controller`` stub below holds, spelled the way the run
#: bar and the recipe column receive it. The two used to disagree --
#: ``rc-typ`` / ``rc-worst`` here against ``rc-default`` / ``rc-fast`` in the
#: stub -- and nothing noticed, because the stub answered every unknown id
#: with a recipe anyway. One library, one spelling.
RECIPES = [("rc-default", "RC default"), ("rc-fast", "RC fast")]


def _book(*extra: CellEntry) -> CellBook:
    return CellBook(
        cells=[
            CellEntry(library="EXAMPLE_LIB", cell="BLOCK_A_v3", layout_view="layout"),
            CellEntry(
                library="EXAMPLE_LIB",
                cell="CORE_TOP_v7",
                layout_view="layout",
                ground_net="avss",
            ),
            CellEntry(library="EXAMPLE_LIB_TOP", cell="TOP_WRAP_v1", layout_view="layout"),
            *extra,
        ]
    )


def _screen(
    qtbot,
    book: CellBook | None = None,
    controller=None,
    recipe_override: str | None = "rc-default",
) -> CellsScreen:
    """The screen, and -- when it can dispatch -- a recipe picked for the run.

    ``recipe_override`` exists because the stub library holds two recipes and
    the dispatch refuses to guess between them. Every test below that hands
    in a controller is about *dispatch* (does the worker get built, does the
    panel open, do reporter events land) and not about which recipe a run
    uses, so they take the same step a user takes: pick one in the run bar.
    Pass ``recipe_override=None`` to test the refusal itself.
    """

    screen = CellsScreen(controller, book=book if book is not None else _book())
    qtbot.addWidget(screen)
    screen.set_recipe_choices(RECIPES)
    if controller is not None and recipe_override is not None:
        screen.run_bar.set_recipe_override(recipe_override)
    return screen


def _visible_titles(screen: CellsScreen) -> list[str]:
    table = screen.table
    return [
        table.horizontalHeaderItem(column).text()
        for column in range(table.columnCount())
        if not table.isColumnHidden(column)
    ]


class FakeWorker(QObject):
    """Stand-in for :class:`~auto_ext.ui.worker.RunWorker`.

    Same surface the screen touches: two signals, ``start``,
    ``request_cancel`` and ``summary``. Nothing here runs a thread, so the
    reporter can be driven by hand and the assertions stay deterministic.
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
def fake_worker(monkeypatch) -> type[FakeWorker]:
    FakeWorker.instances = []
    monkeypatch.setattr(cells_mod, "RunWorker", FakeWorker)
    return FakeWorker


@pytest.fixture
def controller(tmp_path: Path):
    """A controller stub carrying tasks whose ids are the row keys.

    ``run_recipe`` and ``profile`` are here because ``run_tasks`` requires
    both -- the recipe says what to extract, the profile supplies the process
    literals -- and the dispatch refuses rather than starting a run that would
    produce plausible-looking parasitics from the wrong settings.

    **``run_recipe`` reproduces the real rule, including the refusal.** It
    used to read ``library.get(recipe_id, recipe)`` -- fall back to a recipe
    when the id is unknown or ``None`` -- which is the opposite of
    :meth:`ConfigController.run_recipe`, where no override and more than one
    candidate means ``None``. ``test_config_controller`` pins the real
    behaviour; nothing pinned the stub to it, so every dispatch test here
    passed and the refusal the user actually hits was unreachable by
    construction. A stub that contradicts a tested contract is worse than no
    stub: it certifies the branch it removes.
    """

    book = _book()
    tasks = [SimpleNamespace(task_id=key) for key in book.keys]
    recipe = SimpleNamespace(recipe_id="rc-default", name="RC default")
    other = SimpleNamespace(recipe_id="rc-fast", name="RC fast")
    library = {r.recipe_id: r for r in (recipe, other)}

    def run_recipe(recipe_id: str | None = None):
        if recipe_id:
            return library.get(recipe_id)
        return recipe if len(library) == 1 else None

    return SimpleNamespace(
        project=SimpleNamespace(name="demo"),
        tasks=tasks,
        auto_ext_root=tmp_path / "auto_ext",
        workarea=tmp_path / "wa",
        is_dirty=False,
        profile=SimpleNamespace(profile_id="hn001"),
        run_recipe=run_recipe,
        recipe_ids=lambda: sorted(library),
    )


# ---- the table ------------------------------------------------------------


def test_every_row_is_one_dut(qtbot) -> None:
    screen = _screen(qtbot)

    assert screen.table.rowCount() == 3
    assert screen.table.item(0, COL_LIBRARY).text() == "EXAMPLE_LIB"
    assert screen.table.item(0, COL_CELL).text() == "BLOCK_A_v3"


def test_there_is_exactly_one_table_and_no_expansion_preview(qtbot) -> None:
    """The Cartesian preview pane is gone: expansion happens at add time."""

    screen = _screen(qtbot)

    assert screen.findChildren(QTableWidget) == [screen.table]


def test_wide_columns_match_the_artboard(qtbot) -> None:
    screen = _screen(qtbot)

    assert screen.column_mode() == MODE_WIDE
    assert _visible_titles(screen) == [
        "",
        "library",
        "cell",
        "layout",
        "source",
        "ground",
        "out view",
        "recipe",
        "last run",
        "status",
    ]


def test_row_and_header_heights_are_the_design_values(qtbot) -> None:
    screen = _screen(qtbot)

    assert screen.table.verticalHeader().defaultSectionSize() == theme.ROW_HEIGHT == 24
    assert screen.table.horizontalHeader().height() == theme.TABLE_HEADER_HEIGHT == 22


def test_cell_name_is_the_one_elastic_column(qtbot) -> None:
    from PyQt5.QtWidgets import QHeaderView

    screen = _screen(qtbot)
    header = screen.table.horizontalHeader()

    assert header.sectionResizeMode(COL_CELL) == QHeaderView.Stretch


def test_compact_merges_views_and_pushes_ground_into_the_tooltip(qtbot) -> None:
    """Artboard 1j, concession 2."""

    screen = _screen(qtbot)
    screen.set_column_mode(MODE_COMPACT)

    assert _visible_titles(screen) == [
        "",
        "library",
        "cell",
        "views",
        "out view",
        "recipe",
        "last run",
        "status",
    ]
    assert screen.table.item(1, COL_VIEWS).text() == "layout/schematic"
    assert screen.table.isColumnHidden(COL_GROUND) is True
    assert "ground net: avss" in screen.table.item(1, COL_CELL).toolTip()


def test_running_mode_shows_chips_in_a_taller_row(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_column_mode(MODE_RUNNING)

    assert _visible_titles(screen) == ["", "library", "cell", "recipe", "stages"]
    assert isinstance(screen.table.cellWidget(0, COL_STAGES), StageChipStrip)
    assert (
        screen.table.verticalHeader().defaultSectionSize()
        == theme.STAGE_CHIP_ROW_HEIGHT
        == 26
    )


def test_running_mode_swaps_the_checkbox_for_a_status_glyph(qtbot) -> None:
    screen = _screen(qtbot)
    key = screen.cells().keys[0]
    screen.set_row_status(key, "passed", text="passed")

    screen.set_column_mode(MODE_RUNNING)

    item = screen.table.item(0, COL_CHECK)
    assert item.text() == theme.STATUS_GLYPH["passed"]
    assert not (item.flags() & Qt.ItemIsUserCheckable)


def test_leaving_running_mode_restores_the_checkboxes(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_column_mode(MODE_RUNNING)

    screen.set_column_mode(MODE_WIDE)

    item = screen.table.item(0, COL_CHECK)
    assert item.text() == ""
    assert bool(item.flags() & Qt.ItemIsUserCheckable)


# ---- empty state (artboard 1i) -------------------------------------------


def test_empty_state_keeps_the_header_so_the_shape_is_visible(qtbot) -> None:
    screen = CellsScreen()
    qtbot.addWidget(screen)
    screen.show()

    assert screen.table.rowCount() == 0
    assert screen.empty_state.isVisible() is True
    assert screen.table.horizontalHeader().isVisible() is True
    assert _visible_titles(screen)[1:3] == ["library", "cell"]


def test_empty_state_offers_the_two_ways_in(qtbot) -> None:
    screen = CellsScreen()
    qtbot.addWidget(screen)
    asked: list[int] = []
    screen.import_requested.connect(lambda: asked.append(1))

    screen.empty_state.import_button().click()
    assert asked == [1]

    screen.empty_state.add_button().click()
    assert screen.table.rowCount() == 1


def test_add_is_the_primary_action_only_while_there_is_nothing(qtbot) -> None:
    screen = CellsScreen()
    qtbot.addWidget(screen)

    assert screen.toolbar_button("add").property("primary") is True

    screen.add_cell()

    assert screen.toolbar_button("add").property("primary") is False
    assert screen.empty_state.isVisible() is False


def test_empty_state_takes_an_extra_hint_line(qtbot) -> None:
    screen = CellsScreen()
    qtbot.addWidget(screen)
    screen.show()

    screen.set_empty_state_hint("Setup is clean — 11 checks pass.")

    assert screen.empty_state._hint.isVisible() is True


# ---- editing --------------------------------------------------------------


def test_editing_a_field_goes_through_the_model(qtbot) -> None:
    screen = _screen(qtbot)
    seen: list[CellBook] = []
    screen.cells_changed.connect(seen.append)

    screen.table.item(0, COL_CELL).setText("BLOCK_back_v4")

    assert screen.cells().cells[0].cell == "BLOCK_back_v4"
    assert len(seen) == 1


def test_a_duplicate_row_is_refused_and_the_text_goes_back(qtbot) -> None:
    """``CellBook`` refuses duplicates outright; the screen must not soften
    that into a warning the user can scroll past."""

    screen = _screen(qtbot)
    rejected: list[str] = []
    screen.edit_rejected.connect(rejected.append)

    screen.table.item(0, COL_CELL).setText("CORE_TOP_v7")

    assert len(rejected) == 1
    assert screen.cells().cells[0].cell == "BLOCK_A_v3"
    assert screen.table.item(0, COL_CELL).text() == "BLOCK_A_v3"


def test_emptying_a_required_field_is_refused(qtbot) -> None:
    screen = _screen(qtbot)
    rejected: list[str] = []
    screen.edit_rejected.connect(rejected.append)

    screen.table.item(0, COL_LIBRARY).setText("")

    assert len(rejected) == 1
    assert screen.table.item(0, COL_LIBRARY).text() == "EXAMPLE_LIB"


def test_renaming_a_row_carries_its_recipe_and_status_along(qtbot) -> None:
    screen = _screen(qtbot)
    old_key = screen.cells().keys[0]
    screen.set_recipe_binding(old_key, "rc-default")
    screen.set_row_status(old_key, "passed", text="passed", when="08-20 17:42")

    screen.table.item(0, COL_CELL).setText("BLOCK_back_v9")
    new_key = screen.cells().keys[0]

    assert new_key != old_key
    assert screen.recipe_bindings()[new_key] == "rc-default"
    assert screen.row_status(new_key).when == "08-20 17:42"


def test_the_recipe_column_is_not_a_free_text_field(qtbot) -> None:
    """It is a bound choice, so it gets a combo delegate."""

    screen = _screen(qtbot)
    delegate = screen.table.itemDelegateForColumn(COL_RECIPE)

    assert isinstance(delegate, cells_mod.RecipeDelegate)


def test_recipe_binding_shows_the_display_name(qtbot) -> None:
    screen = _screen(qtbot)
    key = screen.cells().keys[0]

    assert screen.table.item(0, COL_RECIPE).text() == "—"
    screen.set_recipe_binding(key, "rc-fast")

    assert screen.table.item(0, COL_RECIPE).text() == "RC fast"


# ---- row commands ---------------------------------------------------------


def test_add_cell_appends_a_free_key_and_selects_it(qtbot) -> None:
    screen = _screen(qtbot)

    key = screen.add_cell()

    assert key in screen.cells().keys
    assert screen.selected_keys() == (key,)
    assert screen.table.rowCount() == 4


def test_add_cell_keeps_looking_until_the_key_is_free(qtbot) -> None:
    screen = _screen(qtbot)

    first = screen.add_cell()
    second = screen.add_cell()

    assert first != second
    assert len(set(screen.cells().keys)) == len(screen.cells().keys)


def test_duplicate_copies_the_row_and_its_recipe(qtbot) -> None:
    screen = _screen(qtbot)
    key = screen.cells().keys[0]
    screen.set_recipe_binding(key, "rc-default")
    screen.set_selected_keys([key])

    copies = screen.duplicate_selected()

    assert len(copies) == 1
    assert screen.cells().entry(copies[0]).cell == "BLOCK_A_v3_copy"
    assert screen.recipe_bindings()[copies[0]] == "rc-default"


def test_remove_drops_the_rows_and_everything_hanging_off_them(qtbot) -> None:
    screen = _screen(qtbot)
    key = screen.cells().keys[0]
    screen.set_recipe_binding(key, "rc-default")
    screen.set_row_status(key, "failed", text="lvs 3", code="LVS")
    screen.set_selected_keys([key])

    screen.remove_selected()

    assert key not in screen.cells().keys
    assert key not in screen.recipe_bindings()
    assert screen.row_status(key) == RowStatus()


def test_disabling_a_row_greys_it_and_keeps_it_in_the_table(qtbot) -> None:
    """The "not this one, for now" half of the old ``exclude``."""

    screen = _screen(qtbot)
    key = screen.cells().keys[0]

    screen.set_enabled_for([key], False)

    assert screen.cells().entry(key).enabled is False
    assert screen.table.rowCount() == 3
    assert screen.table.item(0, COL_CELL).foreground().color().name() == theme.TEXT_DISABLED
    assert "disabled" in screen.table.item(0, COL_CELL).toolTip()


def test_a_disabled_row_never_joins_a_batch(qtbot) -> None:
    screen = _screen(qtbot)
    keys = screen.cells().keys
    screen.set_enabled_for([keys[0]], False)
    screen.set_selected_keys(keys[:2])

    assert screen.run_request().keys == (keys[1],)


# ---- selection ------------------------------------------------------------


def test_checking_the_box_selects_the_row(qtbot) -> None:
    screen = _screen(qtbot)
    seen: list[tuple[str, ...]] = []
    screen.selection_changed.connect(seen.append)

    screen.table.item(0, COL_CHECK).setCheckState(Qt.Checked)

    assert screen.selected_keys() == (screen.cells().keys[0],)
    assert seen[-1] == (screen.cells().keys[0],)


def test_checking_a_second_box_keeps_the_first(qtbot) -> None:
    screen = _screen(qtbot)

    screen.table.item(0, COL_CHECK).setCheckState(Qt.Checked)
    screen.table.item(2, COL_CHECK).setCheckState(Qt.Checked)

    assert len(screen.selected_keys()) == 2


def test_selecting_the_row_ticks_the_box(qtbot) -> None:
    screen = _screen(qtbot)

    screen.set_selected_keys(screen.cells().keys[:2])

    assert screen.table.item(0, COL_CHECK).checkState() == Qt.Checked
    assert screen.table.item(1, COL_CHECK).checkState() == Qt.Checked
    assert screen.table.item(2, COL_CHECK).checkState() == Qt.Unchecked


def test_selection_drives_the_run_button(qtbot) -> None:
    screen = _screen(qtbot)

    screen.set_selected_keys(screen.cells().keys[:3])

    assert screen.run_bar.run_button_text() == "Run 3 cells"


def test_duplicate_and_remove_are_dead_without_a_selection(qtbot) -> None:
    screen = _screen(qtbot)

    assert screen.toolbar_button("duplicate").isEnabled() is False
    screen.set_selected_keys(screen.cells().keys[:1])
    assert screen.toolbar_button("remove").isEnabled() is True


# ---- filter ---------------------------------------------------------------


def test_filter_hides_the_rows_that_do_not_match(qtbot) -> None:
    screen = _screen(qtbot)

    screen.set_filter_text("BLOCK")

    assert screen.visible_keys() == (screen.cells().keys[0],)

    screen.set_filter_text("")
    assert len(screen.visible_keys()) == 3


def test_filter_also_matches_the_recipe_name(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_recipe_binding(screen.cells().keys[1], "rc-fast")

    screen.set_filter_text("rc fast")

    assert screen.visible_keys() == (screen.cells().keys[1],)


# ---- context menu ---------------------------------------------------------


def test_context_menu_is_deferred_and_lists_its_actions(qtbot) -> None:
    """X11 fires the context-menu event on press; a synchronous ``exec_()``
    is dismissed by the following release, which is the "right-click twice"
    bug. Pumping the loop is what makes the deferral observable."""

    screen = _screen(qtbot)
    screen.show()
    captured: dict[str, object] = {}
    real_exec = cells_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = [a.text() for a in self.actions() if a.text()]
        return None

    cells_mod.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        centre = screen.table.visualItemRect(screen.table.item(0, COL_CELL)).center()
        screen.table.customContextMenuRequested.emit(centre)
        assert "actions" not in captured, "popup must not be synchronous"
        qtbot.wait(10)
    finally:
        cells_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert captured["actions"] == [
        "Add cell",
        "Duplicate",
        "Remove",
        "Disable rows",
        "Export GDS…",
        "Copy row key",
    ]


def test_right_clicking_an_unselected_row_selects_it_first(qtbot) -> None:
    screen = _screen(qtbot)
    screen.show()
    real_exec = cells_mod.QMenu.exec_
    cells_mod.QMenu.exec_ = lambda self, *a, **k: None  # type: ignore[method-assign]
    try:
        centre = screen.table.visualItemRect(screen.table.item(2, COL_CELL)).center()
        screen.table.customContextMenuRequested.emit(centre)
        qtbot.wait(10)
    finally:
        cells_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert screen.selected_keys() == (screen.cells().keys[2],)


def test_context_menu_on_empty_space_still_opens(qtbot) -> None:
    screen = _screen(qtbot)
    screen.show()
    captured: dict[str, object] = {}
    real_exec = cells_mod.QMenu.exec_

    def fake_exec(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        captured["actions"] = [a.text() for a in self.actions() if a.text()]
        return None

    cells_mod.QMenu.exec_ = fake_exec  # type: ignore[method-assign]
    try:
        screen.table.customContextMenuRequested.emit(QPoint(5, 5000))
        qtbot.wait(10)
    finally:
        cells_mod.QMenu.exec_ = real_exec  # type: ignore[method-assign]

    assert captured["actions"][0] == "Add cell"


# ---- status column --------------------------------------------------------


def test_status_prints_the_failure_code_rather_than_relying_on_hue(qtbot) -> None:
    """LIC/CFG and LVS/CRS share two hues on purpose: the code is what
    separates them in greyscale and for a colour-blind reader."""

    screen = _screen(qtbot)
    key = screen.cells().keys[0]

    screen.set_row_status(key, "failed", text="3 discrepancies", when="08-20 17:42", code="LVS")

    item = screen.table.item(0, COL_STATUS)
    assert item.text() == "✗ LVS 3 discrepancies"
    assert item.foreground().color().name() == theme.STATUS_FAILED
    assert screen.table.item(0, COL_LAST_RUN).text() == "08-20 17:42"


def test_environment_codes_are_amber_and_readable_on_white(qtbot) -> None:
    screen = _screen(qtbot)
    key = screen.cells().keys[0]

    screen.set_row_status(key, "failed", text="no license", code="LIC")

    item = screen.table.item(0, COL_STATUS)
    assert item.text().startswith("✗ LIC")
    assert item.foreground().color().name() == theme.WARNING_TEXT_ON_WHITE


def test_never_run_is_a_dot_not_a_verdict(qtbot) -> None:
    screen = _screen(qtbot)

    assert screen.table.item(0, COL_STATUS).text() == "· never run"
    assert screen.table.item(0, COL_LAST_RUN).text() == "—"


def test_status_colours_never_borrow_the_accent(qtbot) -> None:
    screen = _screen(qtbot)
    key = screen.cells().keys[0]
    accents = theme.accent_colors()

    for status in ("passed", "failed", "cancelled", "running", "skipped", "pending"):
        screen.set_row_status(key, status)
        colour = screen.table.item(0, COL_STATUS).foreground().color().name()
        assert colour not in accents, status


def test_bulk_status_update_paints_every_row(qtbot) -> None:
    screen = _screen(qtbot)
    keys = screen.cells().keys

    screen.set_row_statuses(
        {
            keys[0]: RowStatus("passed", "passed", "08-20 17:42"),
            keys[1]: RowStatus("cancelled", "cancelled", "08-18 14:02"),
        }
    )

    assert screen.table.item(0, COL_STATUS).text() == "✓ passed"
    assert screen.table.item(1, COL_STATUS).text() == "■ cancelled"


# ---- sizing (artboard 1j) -------------------------------------------------


def test_screen_never_blocks_the_940x560_window(qtbot) -> None:
    """The hard number from the canvas.

    The shell's own chrome is already pinned under 200x200, so a screen
    that demanded more than this would be the whole reason a 1366x768
    laptop cannot shrink the window. Nothing here has a hard minimum: the
    toolbar shortens, the table scrolls, strings elide, the run bar folds.
    """

    screen = _screen(qtbot)
    screen.set_selected_keys(screen.cells().keys)
    hint = screen.minimumSizeHint()

    assert hint.width() <= 700, f"cells screen demands {hint.width()}px of width"
    assert hint.height() <= 400, f"cells screen demands {hint.height()}px of height"


def test_an_empty_screen_is_no_bigger_than_a_full_one(qtbot) -> None:
    """The guidance panel is an overlay, so it costs nothing in the layout."""

    screen = CellsScreen()
    qtbot.addWidget(screen)
    hint = screen.minimumSizeHint()

    assert hint.width() <= 700
    assert hint.height() <= 400


def test_a_very_long_cell_name_does_not_widen_the_screen(qtbot) -> None:
    book = CellBook(
        cells=[
            CellEntry(
                library="EXAMPLE_LIB",
                cell="BLOCK_" + "very_long_block_name_" * 8,
                layout_view="layout",
            )
        ]
    )
    screen = _screen(qtbot, book)

    assert screen.minimumSizeHint().width() <= 700


def test_shrinking_folds_the_toolbar_and_the_columns(qtbot) -> None:
    screen = _screen(qtbot)
    screen.show()

    screen.resize(1200, 700)
    qtbot.wait(10)
    assert screen.column_mode() == MODE_WIDE
    assert screen.toolbar_button("add").text() == "Add cell"

    screen.resize(900, 600)
    qtbot.wait(10)
    assert screen.column_mode() == MODE_COMPACT
    assert screen.toolbar_button("add").text() == "Add"


def test_a_run_in_flight_outranks_the_width_class(qtbot) -> None:
    screen = _screen(qtbot)
    screen.show()
    screen.set_column_mode(MODE_RUNNING)

    screen.resize(700, 500)
    qtbot.wait(10)

    assert screen.column_mode() == MODE_RUNNING


# ---- nav rail registration ------------------------------------------------


def test_install_cells_page_registers_and_tracks_the_row_count(qtbot) -> None:
    shell = Shell()
    qtbot.addWidget(shell)
    screen = CellsScreen(book=_book())

    returned = install_cells_page(shell, screen)

    assert returned is screen
    assert shell.page("cells") is screen
    assert shell.current_page_key() == "cells"
    assert shell.nav_button("cells").count_text() == "3"
    assert shell.nav_button("cells").code == "CEL"

    screen.add_cell()
    assert shell.nav_button("cells").count_text() == "4"


def test_install_cells_page_builds_its_own_screen_when_given_none(qtbot) -> None:
    shell = Shell()
    qtbot.addWidget(shell)

    screen = install_cells_page(shell)

    assert isinstance(screen, CellsScreen)
    assert shell.page_keys() == ["cells"]


# ---- running --------------------------------------------------------------


def test_run_request_reads_the_bar(qtbot) -> None:
    screen = _screen(qtbot)
    screen.set_selected_keys(screen.cells().keys[:2])
    screen.run_bar.set_selected_stages(["si", "calibre"])
    screen.run_bar.set_jobs(3)
    screen.run_bar.set_dry_run(True)

    request = screen.run_request()

    assert request.keys == tuple(screen.cells().keys[:2])
    assert request.stages == ("si", "calibre")
    assert request.jobs == 3
    assert request.dry_run is True
    assert request.recipe_override is None


def test_without_a_controller_the_screen_only_announces(qtbot, fake_worker) -> None:
    screen = _screen(qtbot)
    screen.set_selected_keys(screen.cells().keys[:1])
    seen: list[object] = []
    screen.run_requested.connect(seen.append)

    screen.start_run()

    assert len(seen) == 1
    assert fake_worker.instances == []
    assert screen.is_running() is False


def test_rows_wanting_different_recipes_become_different_batches(
    qtbot, controller, fake_worker
) -> None:
    """The point of the column: one run, two recipes, in row order."""

    screen = _screen(qtbot, controller=controller, recipe_override=None)
    keys = screen.cells().keys[:2]
    screen.set_recipe_binding(keys[0], "rc-default")
    screen.set_recipe_binding(keys[1], "rc-fast")
    screen.set_selected_keys(keys)

    screen.start_run()

    batches = fake_worker.instances[0].kwargs["batches"]
    assert [b.recipe.recipe_id for b in batches] == ["rc-default", "rc-fast"]
    assert [[t.task_id for t in b.tasks] for b in batches] == [[keys[0]], [keys[1]]]


def test_rows_sharing_a_recipe_stay_in_one_batch(
    qtbot, controller, fake_worker
) -> None:
    """Grouping, not one call per row: two rows on one recipe run together."""

    screen = _screen(qtbot, controller=controller, recipe_override=None)
    keys = screen.cells().keys[:2]
    for key in keys:
        screen.set_recipe_binding(key, "rc-fast")
    screen.set_selected_keys(keys)

    screen.start_run()

    batches = fake_worker.instances[0].kwargs["batches"]
    assert len(batches) == 1
    assert [t.task_id for t in batches[0].tasks] == list(keys)


def test_the_run_bar_override_replaces_every_row_recipe(
    qtbot, controller, fake_worker
) -> None:
    """"override applies to this run only -- row recipes are untouched"."""

    screen = _screen(qtbot, controller=controller, recipe_override=None)
    keys = screen.cells().keys[:2]
    screen.set_recipe_binding(keys[0], "rc-default")
    screen.set_recipe_binding(keys[1], "rc-fast")
    screen.set_selected_keys(keys)
    screen.run_bar.set_recipe_override("rc-fast")

    screen.start_run()

    batches = fake_worker.instances[0].kwargs["batches"]
    assert [b.recipe.recipe_id for b in batches] == ["rc-fast"]
    assert screen.recipe_bindings()[keys[0]] == "rc-default", "the row was rewritten"


def test_a_row_with_no_recipe_names_itself_in_the_refusal(
    qtbot, controller, fake_worker, monkeypatch
) -> None:
    """Refuse the whole run, and say which rows are missing one.

    Dispatching the rows that *do* name a recipe and quietly dropping the
    rest is the shape that gets the whole batch re-run later anyway.
    """

    from PyQt5.QtWidgets import QMessageBox

    shown: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: shown.append(a) or QMessageBox.Ok),
    )

    screen = _screen(qtbot, controller=controller, recipe_override=None)
    keys = screen.cells().keys[:2]
    screen.set_recipe_binding(keys[0], "rc-default")
    screen.set_selected_keys(keys)

    screen.start_run()

    assert fake_worker.instances == [], "it refused and dispatched anyway"
    assert keys[1] in shown[0][2], "the refusal does not name the offending row"
    assert keys[0] not in shown[0][2], "it named a row that was fine"


def test_with_two_recipes_and_no_choice_the_dispatch_says_so_and_starts_nothing(
    qtbot, controller, fake_worker, monkeypatch
) -> None:
    """The branch the old stub made unreachable.

    ``ConfigController.run_recipe`` returns ``None`` when there is no
    override and more than one candidate, because ``run_tasks`` takes one
    recipe for the whole batch and picking the alphabetically first one would
    run the wrong settings silently. That refusal is what the user meets, so
    it is worth a test of its own rather than being the thing the stub
    papered over.
    """

    from PyQt5.QtWidgets import QMessageBox

    shown: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda *a, **k: shown.append(a) or QMessageBox.Ok),
    )

    screen = _screen(qtbot, controller=controller, recipe_override=None)
    screen.set_selected_keys(screen.cells().keys[:1])

    screen.start_run()

    assert shown, "the dispatch refused silently"
    assert fake_worker.instances == [], "it refused and started a run anyway"
    assert screen.is_running() is False

    title, text = shown[0][1], shown[0][2]
    assert "profile" not in title.lower(), (
        "the profile is present; the dialog must not name it as the problem"
    )
    assert "run bar" in text, "the dialog does not say where to fix it"


def test_dispatch_reuses_the_existing_worker(qtbot, controller, fake_worker) -> None:
    """One threading story in this app, not two: the screen builds the same
    RunWorker + QtProgressReporter + CancelToken the Run tab always has."""

    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:2])
    screen.run_bar.set_selected_stages(["si", "calibre"])
    screen.run_bar.set_jobs(2)

    screen.start_run()

    worker = fake_worker.instances[0]
    assert worker.started is True
    batches = worker.kwargs["batches"]
    assert len(batches) == 1, "one recipe for these rows means one batch"
    assert [t.task_id for t in batches[0].tasks] == list(screen.cells().keys[:2])
    assert batches[0].recipe.recipe_id == "rc-default"
    assert worker.kwargs["stages"] == ["si", "calibre"]
    assert worker.kwargs["max_workers"] == 2
    assert worker.kwargs["dry_run"] is False
    assert worker.kwargs["auto_ext_root"] == controller.auto_ext_root
    assert isinstance(worker.kwargs["reporter"], cells_mod.QtProgressReporter)
    assert isinstance(worker.kwargs["cancel_token"], cells_mod.CancelToken)


# ---- Export GDS ----------------------------------------------------------


def _pin_save_dialog(monkeypatch, chosen: str) -> None:
    monkeypatch.setattr(
        cells_mod.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (chosen, ""))
    )


def _pin_dir_dialog(monkeypatch, chosen: str) -> None:
    monkeypatch.setattr(
        cells_mod.QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: chosen)
    )


def test_export_gds_runs_strmout_alone_and_never_moves_the_lvs_file(
    qtbot, controller, fake_worker, monkeypatch
) -> None:
    """The whole point of the feature, pinned.

    ``-strmFile`` and the runset's ``*lvsLayoutPaths`` are the same value, so
    an export that also ran calibre would point LVS at a file nobody wrote.
    The stage set is therefore pinned here, not merely defaulted -- whatever
    stages the run bar happens to have ticked are ignored.
    """
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:1])
    screen.run_bar.set_selected_stages(["si", "strmout", "calibre"])
    _pin_save_dialog(monkeypatch, "/elsewhere/inv.gds")

    screen.export_gds()

    kwargs = fake_worker.instances[0].kwargs
    assert kwargs["stages"] == ["strmout"]
    assert kwargs["layout_export_path"] == "/elsewhere/inv.gds"
    assert kwargs["dry_run"] is False


def test_export_gds_of_many_cells_keeps_them_apart(
    qtbot, controller, fake_worker, monkeypatch
) -> None:
    """A directory plus {cell}, not one filename: else each cell eats the last."""

    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:2])
    _pin_dir_dialog(monkeypatch, "/exports")

    screen.export_gds()

    assert fake_worker.instances[0].kwargs["layout_export_path"] == "/exports/{cell}.gds"


def test_a_cancelled_export_dialog_starts_nothing(
    qtbot, controller, fake_worker, monkeypatch
) -> None:
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:1])
    _pin_save_dialog(monkeypatch, "")

    screen.export_gds()

    assert fake_worker.instances == []
    assert screen.is_running() is False


def test_an_ordinary_run_requests_no_export(qtbot, controller, fake_worker) -> None:
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:1])

    screen.start_run()

    assert fake_worker.instances[0].kwargs["layout_export_path"] is None


def test_one_job_means_serial_not_a_pool_of_one(qtbot, controller, fake_worker) -> None:
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:1])
    screen.run_bar.set_jobs(1)

    screen.start_run()

    assert fake_worker.instances[0].kwargs["max_workers"] is None


def test_a_second_run_cannot_start_while_one_is_in_flight(
    qtbot, controller, fake_worker
) -> None:
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:1])

    screen.start_run()
    screen.start_run()

    assert len(fake_worker.instances) == 1


def test_running_locks_edits_and_switches_the_table(qtbot, controller, fake_worker) -> None:
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:2])
    messages: list[str] = []
    screen.status_message.connect(messages.append)

    screen.start_run()

    assert screen.is_running() is True
    assert screen.column_mode() == MODE_RUNNING
    assert screen.table.editTriggers() == QAbstractItemView.NoEditTriggers
    assert screen.toolbar_button("add").isEnabled() is False
    assert screen.run_bar.is_running() is True
    assert screen.run_bar.counts_text() == "0 passed · 0 failed · 0 running · 2 queued"
    assert any("edits are locked" in m for m in messages)


def test_rows_outside_the_batch_are_not_marked_queued(
    qtbot, controller, fake_worker
) -> None:
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:1])

    screen.start_run()

    assert screen.stage_strip(screen.cells().keys[0]).placeholder() == "queued"
    assert screen.stage_strip(screen.cells().keys[2]).placeholder() == "—"


def test_reporter_events_drive_the_row_and_the_counts(
    qtbot, controller, fake_worker
) -> None:
    screen = _screen(qtbot, controller=controller)
    keys = screen.cells().keys
    screen.set_selected_keys(keys[:2])
    screen.run_bar.set_selected_stages(["si", "calibre"])
    screen.start_run()
    reporter = fake_worker.instances[0].kwargs["reporter"]

    reporter.on_run_start(2, ["si", "calibre"])
    reporter.on_task_start(keys[0], ["si", "calibre"])
    reporter.on_stage_start(keys[0], "si")

    strip = screen.stage_strip(keys[0])
    assert strip.placeholder() is None
    assert strip.statuses()["si"] == "running"
    assert screen.run_bar.counts_text() == "0 passed · 0 failed · 1 running · 1 queued"

    reporter.on_stage_end(keys[0], "si", "passed")
    reporter.on_stage_end(keys[0], "calibre", "failed", "LVS INCORRECT")
    reporter.on_task_end(keys[0], "failed")

    assert strip.chip_texts()[:2] == ["si ✓", "calibre ✗"]
    assert screen.table.item(0, COL_STATUS).text() == "✗ failed"
    assert screen.run_bar.counts_text() == "0 passed · 1 failed · 0 running · 1 queued"


def test_stage_start_publishes_the_log_path_from_the_run_directory(
    qtbot, controller, fake_worker, tmp_path: Path
) -> None:
    """The run id is a timestamp, so the log path can only come from the
    reporter's ``run_dir_ready`` event -- never from the row key."""

    screen = _screen(qtbot, controller=controller)
    keys = screen.cells().keys
    screen.set_selected_keys(keys[:1])
    screen.start_run()
    reporter = fake_worker.instances[0].kwargs["reporter"]
    seen: list[object] = []
    screen.log_path_changed.connect(seen.append)

    reporter.on_stage_start(keys[0], "quantus")
    assert seen == [], "no run directory yet, so no path to give"

    run_dir = tmp_path / "runs" / "20260820T174200Z_dco-rcworst"
    reporter.on_run_dir(keys[0], run_dir)
    reporter.on_stage_start(keys[0], "quantus")

    assert seen == [run_dir / "logs" / "quantus.log"]
    assert screen.run_bar.log_path() == run_dir / "logs" / "quantus.log"


def test_follow_off_leaves_the_log_where_it_was(qtbot, controller, fake_worker) -> None:
    screen = _screen(qtbot, controller=controller)
    keys = screen.cells().keys
    screen.set_selected_keys(keys[:1])
    screen.run_bar.set_follows_current_stage(False)
    screen.start_run()
    reporter = fake_worker.instances[0].kwargs["reporter"]
    reporter.on_run_dir(keys[0], Path("/runs/x"))
    seen: list[object] = []
    screen.log_path_changed.connect(seen.append)

    reporter.on_stage_start(keys[0], "si")

    assert seen == []


def test_open_in_editor_is_its_own_signal(qtbot, controller, fake_worker) -> None:
    """Following the current stage and asking for an editor are different
    intents; a host that opens a file on every stage start would thrash."""

    screen = _screen(qtbot, controller=controller)
    keys = screen.cells().keys
    screen.set_selected_keys(keys[:1])
    screen.start_run()
    reporter = fake_worker.instances[0].kwargs["reporter"]
    reporter.on_run_dir(keys[0], Path("/runs/x"))
    reporter.on_stage_start(keys[0], "si")

    followed: list[object] = []
    opened: list[object] = []
    screen.log_path_changed.connect(followed.append)
    screen.open_log_requested.connect(opened.append)

    screen.run_bar._open_log_button.click()

    assert opened == [Path("/runs/x/logs/si.log")]
    assert followed == []


def test_starting_a_run_opens_the_panel_and_finishing_gives_it_back(
    qtbot, controller, fake_worker
) -> None:
    """Concession 5: the panel opens over the table, then gets out of the way."""

    screen = _screen(qtbot, controller=controller)
    screen.show()
    screen.resize(1200, 700)
    qtbot.wait(10)
    screen.run_bar.set_log_widget(QLabel("log"))
    screen.set_selected_keys(screen.cells().keys[:1])
    idle_before = screen.splitter.sizes()[1]

    screen.start_run()
    running = screen.splitter.sizes()[1]

    fake_worker.instances[0].finished.emit()
    qtbot.wait(10)

    assert running > idle_before
    assert screen.splitter.sizes()[1] < running


def test_cancel_asks_the_worker_once_and_says_so(qtbot, controller, fake_worker) -> None:
    screen = _screen(qtbot, controller=controller)
    screen.set_selected_keys(screen.cells().keys[:1])
    screen.start_run()

    screen.cancel_run()

    assert fake_worker.instances[0].cancelled is True
    assert screen.run_bar.cancel_button().isEnabled() is False


def test_finishing_gives_the_screen_back(qtbot, controller, fake_worker) -> None:
    screen = _screen(qtbot, controller=controller)
    keys = screen.cells().keys
    screen.set_selected_keys(keys[:1])
    screen.start_run()
    worker = fake_worker.instances[0]
    reporter = worker.kwargs["reporter"]
    summaries: list[object] = []
    screen.run_finished.connect(summaries.append)

    reporter.on_task_start(keys[0], ["si"])
    reporter.on_task_end(keys[0], "passed")
    worker.finished.emit()

    assert screen.is_running() is False
    assert screen.column_mode() == MODE_WIDE
    assert screen.table.editTriggers() != QAbstractItemView.NoEditTriggers
    assert screen.toolbar_button("add").isEnabled() is True
    assert summaries == [worker.summary]


def test_rows_with_no_loaded_task_stop_the_dispatch(
    qtbot, controller, fake_worker, monkeypatch
) -> None:
    """A row the runner has never heard of must not silently run a subset."""

    warned: list[tuple] = []
    monkeypatch.setattr(
        cells_mod.QMessageBox, "warning", lambda *args, **kwargs: warned.append(args)
    )
    screen = _screen(qtbot, controller=controller)
    key = screen.add_cell()
    screen.set_selected_keys([key])

    screen.start_run()

    assert fake_worker.instances == []
    assert len(warned) == 1
