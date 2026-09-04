"""``scripts/ui_inventory.py`` -- the instrument the third layer is read with.

Why this file exists
--------------------
``docs/refactor/UX_VALIDATION.md`` names three layers. The third one -- a
first-time user given a concrete task and nothing but this script's output --
is the only layer that catches "the control exists and is the wrong kind", and
its whole evidence base is one text file. So the text file is not a
convenience: it *is* the layer. A reviewer who cannot see a control in the
dump reports it as missing from the app, and a reviewer who sees a control
with no state beside it cannot tell a live button from a dead one.

The 2026-09-04 walkthrough proved that the hard way. Twelve of the places the
reviewer got stuck were defects in the **instrument**, not the GUI: the
extraction rules dumped as an empty heading, the run bar was absent, the Runs
screen did not exist, the Cells dump was byte-identical between an empty
project and a real one, and 87 rows were printed for a screen that draws 21.
Every one of those was invisible because nothing measured the measuring stick.

This file measures it. The assertions are deliberately about *shape* rather
than wording -- a dump that renames a heading should stay green, and a dump
that stops naming a screen, a row or a control's state should go red.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

#: A dump line that describes one control: ``    [button] 'Save'  enabled · ...``.
#: Four spaces at least, which is what separates a control from a group
#: heading (``  [Quantus/extract]``) drawn at two.
CONTROL_LINE = re.compile(r"^ {4,}\[[^\]]+\]\s")
#: A dump line that describes one option row: ``      control : checkbox``.
OPTION_LINE = re.compile(r"^\s+control : ")


@pytest.fixture(scope="session")
def inventory(qapp):
    """The script under test, imported only once Qt has picked a platform.

    ``scripts/ui_inventory.py`` does ``os.environ.setdefault(
    "QT_QPA_PLATFORM", "offscreen")`` at import time, which is right for the
    script and wrong for this suite: six size and eliding tests are
    font-metric sensitive and go red under the offscreen platform. Depending
    on ``qapp`` means the real platform plugin is already chosen and the
    variable can no longer change it; popping it afterwards keeps any
    subprocess a later test starts on the same platform this one used.
    """

    before = os.environ.get("QT_QPA_PLATFORM")
    from scripts import ui_inventory

    if before is None:
        os.environ.pop("QT_QPA_PLATFORM", None)
    return ui_inventory


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty ``$AUTO_EXT_RECIPES`` and ``~``.

    The same reason ``tests/ui/conftest.py`` has one: the recipe search path
    walks the developer's home before it reaches the project, so "this dump
    shows one recipe" would pass or fail depending on whose machine ran it.
    """

    from auto_ext.ui.config_controller import RECIPES_ENV_VAR

    home = tmp_path / "home"
    (home / ".auto_ext" / "recipes").mkdir(parents=True)
    monkeypatch.delenv(RECIPES_ENV_VAR, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture
def opts(inventory, v2_config_dir_multi: Path, isolated_home: Path):
    """Options over the two-recipe, two-corner fixture.

    ``_multi`` rather than the single-recipe tree: a dropdown offering the
    only possible answer and a dropdown offering nothing look the same in a
    text dump, and half of what this script is for is showing what a control
    offers.
    """

    return inventory.Options(
        config_dir=v2_config_dir_multi / "config",
        auto_ext_root=v2_config_dir_multi,
    )


@pytest.fixture
def unresolved_opts(inventory, v2_config_dir_multi: Path, isolated_home: Path):
    """The same project, with a profile whose paths are unresolved env vars.

    The Setup drawer only grows its pin rows -- ``Browse``, ``Set``, the value
    box -- when something is actually missing, and ``healthy_profile`` is
    healthy on purpose. ``make_profile`` spells its deck directories as
    ``$VERIFY_ROOT/...``, and the autouse ``clean_env`` fixture guarantees
    that variable is unset, so the drawer opens in exactly the state a first
    launch on a fresh machine produces.
    """

    from auto_ext.core.profile_discover import write_profile_yaml
    from auto_ext.model.workspace import WORKSPACE_FILENAME, load_workspace, save_workspace
    from tests.support.v2 import make_profile

    # ``required_env`` is what makes the health report carry env rows at all,
    # and an env row is what the drawer grows a Browse/value/Set trio for.
    profile = make_profile(
        required_env=["VERIFY_ROOT", "calibre_source_added_place"]
    )
    config = v2_config_dir_multi / "config"
    write_profile_yaml(config / "profiles" / f"{profile.profile_id}.yaml", profile)
    workspace = load_workspace(config / WORKSPACE_FILENAME)
    save_workspace(
        workspace.model_copy(update={"pdk_profile": profile.profile_id}),
        config / WORKSPACE_FILENAME,
    )
    return inventory.Options(config_dir=config, auto_ext_root=v2_config_dir_multi)


@pytest.fixture
def window(inventory, opts, qtbot):
    """A booted, shown window over the fixture project."""

    built = inventory.build_window(opts)
    qtbot.addWidget(built)
    yield built
    built.close()


def dump(inventory, window, name: str, opts) -> str:
    return inventory.dump_screen(window, name, opts)


def row_block(text: str, key: str) -> str:
    """The dump's paragraph for one option row.

    Rows start at four spaces and their facts at six, so the block runs to the
    next line that starts at four and is not blank.
    """

    match = re.search(rf"^    {re.escape(key)}$\n((?:^ {{6,}}.*$\n?)*)", text, re.M)
    assert match, f"the dump has no row for {key}"
    return match.group(1)


def _lvs_run(runs_root: Path, make_run_record, *, discrepancies: int, **kw):
    """A finished run whose calibre stage failed with a discrepancy count."""

    from auto_ext.core.progress import StageStatus, TaskStatus
    from auto_ext.core.run_store import write_record
    from auto_ext.model.run import LvsResult, RunResults, StageRecord, allocate_run_dir

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


# ---------------------------------------------------------------------------
# The instrument's own contract
# ---------------------------------------------------------------------------


def test_the_dump_names_every_screen_the_app_has(inventory, window, opts) -> None:
    """A screen missing from the dump is a screen the reviewer says is missing.

    The Runs screen was a registered page for months and had no dumper, so the
    walkthrough's answer to "an LVS failed -- where is the discrepancy count"
    was "there is no results view in the inventory at all". There was; the
    instrument could not see it.
    """

    text = "\n".join(
        dump(inventory, window, name, opts) for name in sorted(inventory._DUMPERS)
    )
    for heading in (
        "=== Cells screen ===",
        "=== Recipes screen ===",
        "=== Runs screen ===",
        "=== Project screen ===",
        "=== Setup drawer ===",
        "=== Menu bar ===",
    ):
        assert heading in text, f"the dump does not name {heading}"

    from auto_ext.ui.main_window import MainWindow

    for key in [page[0] for page in MainWindow._PAGES]:
        assert key in inventory._DUMPERS, f"page {key!r} has no dumper"


@pytest.mark.parametrize("density", ("common", "all"))
def test_the_recipes_dump_lists_every_visible_row_of_that_density(
    inventory, window, opts, density: str
) -> None:
    """Every row the screen draws, and no row it does not.

    The old dump walked ``grid.keys()`` regardless of visibility, so it
    printed the All view -- 87 rows -- while the screen sat in Common at 21.
    A reader could not tell which 21 they would meet on opening the app, so
    "where does this control live" was unanswerable for the other 66.
    """

    opts.density = density
    text = dump(inventory, window, "recipes", opts)
    screen = window.shell.page("recipes")

    drawn = set(screen.visible_option_keys())
    hidden = set(screen.option_keys()) - drawn
    for key in sorted(drawn):
        assert re.search(rf"^    {re.escape(key)}$", text, re.M), (
            f"{key} is drawn in {density} density and is not in the dump"
        )
    for key in sorted(hidden):
        assert not re.search(rf"^    {re.escape(key)}$", text, re.M), (
            f"{key} is hidden in {density} density and the dump shows it anyway"
        )
    assert f"density     : {density}" in text
    assert f"({len(drawn)} of {len(screen.option_keys())} option rows drawn)" in text


def test_the_two_densities_do_not_produce_the_same_dump(inventory, window, opts) -> None:
    """The screen's own toggle says ``Common 21`` / ``All 87``; so must the dump."""

    opts.density = "common"
    common = dump(inventory, window, "recipes", opts)
    opts.density = "all"
    every = dump(inventory, window, "recipes", opts)
    assert common != every
    assert common.count("      control : ") < every.count("      control : ")


def test_every_control_line_says_enabled_visible_and_receivers(
    inventory, window, opts
) -> None:
    """Three facts per control, because two of them decide what a press does.

    A lit button and a greyed one are different affordances, a hidden one is
    not an affordance at all, and a visible enabled control whose primary
    signal has no receiver is the exact shape of "styled like a link, clicking
    does nothing".
    """

    for name in sorted(inventory._DUMPERS):
        text = dump(inventory, window, name, opts)
        lines = [line for line in text.splitlines() if CONTROL_LINE.match(line)]
        assert lines, f"{name}: the dump names no controls at all"
        for line in lines:
            assert "enabled" in line or "DISABLED" in line, line
            assert "visible" in line or "hidden" in line, line
            assert "receivers " in line, line


def test_the_dump_names_at_least_as_many_controls_as_the_window_holds(
    inventory, window, opts
) -> None:
    """The count is the guard against a whole family of widgets being skipped.

    ``_buttons()`` collected ``QPushButton`` only, so the run bar -- a combo,
    seven checkboxes, a spin box and a tool button -- was absent from the
    Cells dump entirely, and the walkthrough concluded there was no way to
    choose what a run does.
    """

    from PyQt5.QtWidgets import QAbstractButton, QComboBox, QLineEdit

    for name, root in (
        ("cells", window.cells_screen),
        ("recipes", window.recipes_screen),
        ("project", window.project_screen),
        ("runs", window.runs_screen),
        ("setup", window.setup_drawer),
    ):
        text = dump(inventory, window, name, opts)
        live = [
            widget
            for widget in root.findChildren((QAbstractButton, QComboBox, QLineEdit))
            if widget.isVisible() and not inventory._is_internal(widget, root)
        ]
        named = sum(
            1
            for line in text.splitlines()
            if CONTROL_LINE.match(line) or OPTION_LINE.match(line)
        )
        assert named >= len(live), (
            f"{name}: the window shows {len(live)} controls and the dump names "
            f"{named} of them"
        )


def test_every_scroll_area_reports_its_range(inventory, window, opts) -> None:
    """A range of 0 is what turns "Show 3 discrepancies" into decoration."""

    text = dump(inventory, window, "recipes", opts)
    assert "  scroll areas:" in text
    assert re.search(r"range\s+\d+\s+position\s+\d+", text)


def test_every_list_reports_rows_selected_and_checked(inventory, window, opts) -> None:
    text = dump(inventory, window, "cells", opts)
    assert re.search(r"rows\s+\d+\s+selected\s+\d+\s+checked\s+\d+", text)


# ---------------------------------------------------------------------------
# The twelve blind spots, one test each
# ---------------------------------------------------------------------------


def test_the_extract_rules_section_is_not_an_empty_heading(
    inventory, window, opts
) -> None:
    """M-114. The most consequential setting in the tool, dumped as a blank.

    ``extract`` is a *span* -- a sub-form, not one control -- so
    ``grid.editor(key)`` answers ``None`` for it and the old dump skipped it
    without a word. ``[Quantus/extract]`` appeared with zero rows under it,
    and the walkthrough's verdict on two of its four tasks was "there is no
    control anywhere for ``extract -type``".
    """

    opts.density = "common"
    text = dump(inventory, window, "recipes", opts)
    section = text.split("[Quantus/extract]", 1)[1].split("\n  [", 1)[0]
    assert "sub-form" in section
    assert "rules" in section
    assert "rc_coupled" in section or "c_only" in section, section
    assert "'+ add rule'" in section, "the sub-form's own buttons must be named here"


def test_no_option_key_is_dropped_without_saying_so(inventory, window, opts) -> None:
    """M-114, the general form: silence is the defect, not the omission."""

    opts.density = "all"
    text = dump(inventory, window, "recipes", opts)
    screen = window.shell.page("recipes")
    for group in screen.groups().values():
        for key in group.grid.keys():
            assert re.search(rf"^    {re.escape(key)}$", text, re.M), (
                f"{key} is a row of the form and the dump does not mention it"
            )


def test_the_cells_dump_shows_the_rows_and_not_only_the_headers(
    inventory, window, opts, tmp_path: Path
) -> None:
    """M-115. The old file was byte-identical between an empty project and one
    with cells in it, so the reviewer could not name a single DUT."""

    text = dump(inventory, window, "cells", opts)
    book = window.cells_screen.cells()
    assert book.cells, "the fixture must have at least one cell"
    for entry in book.cells:
        assert entry.library in text
        assert entry.cell in text
    assert re.search(r"^    row 0: \[[ x]\] ", text, re.M)

    empty = inventory.Options(config_dir=tmp_path / "nothing")
    other = inventory.build_window(empty)
    try:
        assert dump(inventory, other, "cells", empty) != text
    finally:
        other.close()


def test_the_recipe_column_is_not_reported_read_only(inventory, window, opts) -> None:
    """M-115. It is edited through a delegate, which is still editing.

    Reported read-only, the answer to "how do I put this cell on a recipe"
    became *you cannot*, and both of the walkthrough's first two tasks stopped
    there.
    """

    text = dump(inventory, window, "cells", opts)
    line = next(line for line in text.splitlines() if "'recipe'" in line)
    assert "read-only" not in line, line
    for _recipe_id, display in window.cells_screen.recipe_choices():
        assert display in line, f"the dropdown offers {display!r} and the dump hides it"


def test_the_runs_dump_names_the_list_the_card_and_the_context_menu(
    inventory, opts, qtbot, runs_root: Path, make_run_record
) -> None:
    """M-116. There was no Runs dumper, so from the dump runs did not exist."""

    _lvs_run(runs_root, make_run_record, discrepancies=3, cell="amp2")
    opts.runs_root = runs_root
    window = inventory.build_window(opts)
    qtbot.addWidget(window)
    text = dump(inventory, window, "runs", opts)

    assert "run list:" in text
    assert "discrepancies=3" in text
    for label in ("'Refresh'", "'Re-run this cell'", "'Open LVS report'"):
        assert label in text, f"the card's {label} button is not in the dump"
    assert "right-click on a run offers:" in text
    for label in ("'Rename...'", "'Edit note...'", "'Star'", "'Open run directory'"):
        assert label in text, f"the context menu's {label} is not in the dump"


def test_a_pointer_row_says_it_is_read_only_and_where_the_control_is(
    inventory, window, opts
) -> None:
    """M-117. ``PointerOptionEditor`` dumped as a raw class name.

    Seven other editors were translated into user words and this one was not,
    so it read as an editable text box with a default -- and the reviewer
    typed the extracted view they wanted into a row that binds to nothing.
    """

    opts.density = "all"
    text = dump(inventory, window, "recipes", opts)
    assert "PointerOptionEditor" not in text
    assert "control : read-only pointer row" in text
    assert "READ-ONLY: this row is not editable on this screen" in text
    assert "points to: set per cell, not per recipe" in text


def test_the_run_bar_is_in_the_cells_dump(inventory, window, opts) -> None:
    """M-118. ``_buttons()`` collected ``QPushButton`` and nothing else."""

    text = dump(inventory, window, "cells", opts)
    for stage in ("'si'", "'strmout'", "'calibre'", "'quantus'", "'jivaro'"):
        assert stage in text, f"the run bar's {stage} checkbox is not in the dump"
    assert "'dry run'" in text
    assert "'continue on LVS fail'" in text
    assert "number spinner" in text, "the jobs control is not in the dump"
    assert "recipe for this run" in text, "the recipe override combo is not in the dump"


def test_the_running_mode_prints_the_stages_column(inventory, window, opts) -> None:
    """M-119. ``stages`` is the only column ``MODE_RUNNING`` adds, and the old
    dump could not be told to switch, so it was never printed at all."""

    from auto_ext.ui.screens.cells_screen import COL_STAGES, COLUMN_TITLES

    opts.mode = "wide"
    wide = dump(inventory, window, "cells", opts)
    opts.mode = "running"
    running = dump(inventory, window, "cells", opts)

    title = COLUMN_TITLES[COL_STAGES]
    assert f"column {COL_STAGES}: {title!r}" in running
    assert f"column {COL_STAGES}: {title!r}" not in wide
    assert "mode    : running" in running


def test_a_disabled_row_says_why(inventory, window, opts) -> None:
    """M-120. Eleven rows said DISABLED and not one said what would undo it.

    The reason already exists -- the code writes a real sentence into the
    tooltip -- so the whole defect was that the dump threw it away. A control
    that genuinely carries no reason now says *that*, which is a different and
    also reportable defect.
    """

    opts.density = "all"
    text = dump(inventory, window, "recipes", opts)
    disabled = [line for line in text.splitlines() if "      DISABLED:" in line]
    assert disabled, "the fixture recipe must have at least one gated row"
    for line in disabled:
        reason = line.split("DISABLED:", 1)[1].strip()
        assert len(reason) >= 10, f"a bare DISABLED with no reason: {line!r}"

    for line in text.splitlines():
        if "disabled because:" in line:
            assert line.split("disabled because:", 1)[1].strip(), line


def test_a_value_that_is_not_the_catalog_default_is_marked_overridden(
    inventory, window, opts
) -> None:
    """M-121. A row showing 200 under a hint reading ``default 5000``.

    Every other row taught the reader "value equals hint"; this one broke that
    silently, and both numbers were in the dump with nothing comparing them.
    """

    opts.density = "all"
    text = dump(inventory, window, "recipes", opts)
    screen = window.shell.page("recipes")

    from auto_ext.ui.widgets.option_editor import PointerOptionEditor

    overridden = []
    for key in screen.option_keys():
        editor = screen.editor(key)
        if isinstance(editor, PointerOptionEditor):
            # A pointer row holds no value at all since 2026-09-04, so it can
            # be neither at nor away from a default. The dump marks it
            # READ-ONLY and names the screen that does own the value.
            continue
        if not inventory._same_as_default(editor.value(), editor.spec.default):
            overridden.append(key)
    assert overridden, "the fixture recipe must differ from the catalog somewhere"

    for key in overridden:
        block = row_block(text, key)
        assert "OVERRIDDEN" in block, f"{key} is not at its default and is not marked"


def test_each_project_group_heading_appears_at_most_once(
    inventory, window, opts
) -> None:
    """M-122. Two ``[Process]`` headings forty rows apart read as a rendering
    bug, and a reader who thinks a section is a bug does not look inside it."""

    text = dump(inventory, window, "project", opts)
    headings = [
        line.strip()
        for line in text.splitlines()
        if line.startswith("  [") and line.rstrip().endswith("]")
    ]
    duplicates = {h for h in headings if headings.count(h) > 1}
    assert duplicates == set(), f"repeated group headings: {sorted(duplicates)}"


def test_the_corner_table_prints_its_columns_not_a_truncated_repr(
    inventory, window, opts
) -> None:
    """M-123. The one place the literal handed to Quantus is written down was
    inside the elision, and neither ``Add`` nor ``Remove`` said which table it
    acted on -- and there are two tables and two of each button."""

    text = dump(inventory, window, "project", opts)
    profile = window.project_screen.profile()
    assert profile is not None and profile.corners

    assert "CornerSpec(" not in text, "a Python repr is not a table"
    assert "table 'Corner table' columns:" in text
    for column in ("name", "technology corner", "default temperature c"):
        assert f"'{column}'" in text
    for corner in profile.corners:
        assert f"name={corner.name!r}" in text
        assert f"technology corner={corner.technology_corner!r}" in text
    assert "- acts on 'Corner table'" in text
    assert "- acts on 'Deck variants'" in text


def test_the_duplicate_label_index_names_the_pairs(inventory, window, opts) -> None:
    """M-124. "These two say the same words -- do they do the same thing?"

    Two ``Add cell`` buttons are on the Cells screen; one of them is the
    empty-state overlay and is drawn over the table's viewport, which is
    exactly where a naive tree walk stops looking.
    """

    text = dump(inventory, window, "cells", opts)
    assert "duplicate labels on this screen:" in text
    index = text.split("duplicate labels on this screen:", 1)[1]
    assert "'Add cell' x2" in index
    assert "'Import from tasks.yaml' x2" in index


# ---------------------------------------------------------------------------
# The click probe
# ---------------------------------------------------------------------------


def _pressable(inventory, name: str, opts) -> list[str]:
    """The labels of everything the probe would offer to press on ``name``.

    ``_probe_targets`` filters to controls that are visible *and enabled*, so
    a button the app disables on purpose is simply absent here -- which is the
    difference between "pressing it does nothing" and "it refuses out loud".
    """

    window = inventory.build_window(opts)
    try:
        return [label for label, _ in inventory._probe_targets(window, name)]
    finally:
        window.close()


def _press(inventory, name: str, opts, needle: str) -> str:
    """Press the first control on ``name`` whose label contains ``needle``."""

    labels = _pressable(inventory, name, opts)
    matches = [i for i, label in enumerate(labels) if needle in label]
    assert matches, f"{name}: no pressable control matching {needle!r}"
    return inventory._probe_one(name, opts, matches[0])


def test_the_probe_names_the_controls_whose_press_changes_nothing(
    inventory, opts, unresolved_opts, runs_root: Path, make_run_record
) -> None:
    """M-124. The cheapest possible mechanisation of "nothing happened".

    Four of the ledger's rows were reachable by pressing one control in a
    freshly booted window and looking at the dump again: ``Refresh`` on the
    Runs screen (M-51), ``Show N discrepancies`` on the result card (M-26),
    ``Set`` on an empty pin row in the Setup drawer (M-53) and ``Re-check the
    PDK`` in the View menu with the drawer closed (M-59). When a fix cluster
    lands, the matching line here has to be updated -- which is the point: the
    list is the ledger, and each line says what the instrument sees *now*.

    M-53 is fixed, and its line is the interesting one. The fix was not to
    make Set do something on an empty box: it was to disable Set until there
    is a value and say why in the tooltip. A refusal a user can see is not a
    dead control, and the probe agrees by construction -- ``_probe_targets``
    only offers what is enabled, so the button is not in the list at all. The
    line is asserted from both ends so it cannot pass by the pin row having
    quietly disappeared instead.

    M-26 is fixed too and its line has NOT moved, which is a limit of the
    instrument rather than a claim about the app: ``show_lvs_detail`` now
    washes the LVS band in the accent tint, and a stylesheet is not something
    a text dump of labels and states can see. ``tests/ui/test_affordances.py``
    presses that button for real and asserts the pixels changed; this line
    records that a dump-based probe will keep listing it as a candidate.
    """

    _lvs_run(runs_root, make_run_record, discrepancies=3, cell="amp2")
    opts.runs_root = runs_root

    dead = "NO OBSERVABLE CHANGE"
    assert dead in _press(inventory, "runs", opts, "'Refresh'"), "M-51 is fixed?"
    assert dead in _press(inventory, "runs", opts, "discrepancies"), (
        "the dump can see the LVS highlight now?"
    )
    setup = _pressable(inventory, "setup", unresolved_opts)
    assert [label for label in setup if "'Browse'" in label], (
        "the pin row itself is gone, so this line proves nothing about Set"
    )
    assert not [label for label in setup if "'Set'" in label], (
        "Set is pressable on an empty pin row again -- M-53 came back"
    )
    assert dead in _press(inventory, "menus", opts, "Re-check"), "M-59 is fixed?"


def test_the_probe_reports_a_dialog_as_a_real_effect(inventory, unresolved_opts) -> None:
    """A control that opens a dialog demonstrably did something.

    Without this the probe would be a list of every button that opens a file
    picker, because a modal answers nothing to a dump.
    """

    verdict = _press(inventory, "setup", unresolved_opts, "'Browse'")
    assert "QFileDialog" in verdict
    assert "NO OBSERVABLE CHANGE" not in verdict


def test_the_probe_never_writes_to_the_project_it_was_pointed_at(
    inventory, opts
) -> None:
    """A probe that presses ``Save`` on the real project rewrites the files the
    reviewer is reading about, so ``click_probe`` works on a copy."""

    config = Path(opts.config_dir)
    before = {
        path.relative_to(config): path.read_bytes()
        for path in config.rglob("*")
        if path.is_file()
    }
    lines = inventory.click_probe("project", opts)
    assert any("after pressing" in line for line in lines)
    after = {
        path.relative_to(config): path.read_bytes()
        for path in config.rglob("*")
        if path.is_file()
    }
    assert after == before, "the click probe edited the project it was measuring"
