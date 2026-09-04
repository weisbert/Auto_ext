"""The import dialog: the user's own EDA files, and an honest report of them.

Four claims, and each has a test that fails if the claim stops being true:

* **recognised by content.** Every sample file here is handed in under a name
  that lies about what it is, so a detector that peeked at the extension would
  fail the very first test.
* **the third section is not optional.** An import that says "5 values" and
  nothing else has hidden the ninety options it did not model. The report is
  asserted to carry that number, and the status line the screen shows carries
  it too.
* **too large a patch warns, it does not fail.** The amber scale, never the
  red one, and never the accent -- the one rule the design fixes absolutely.
* **it fits.** Artboard ``1j`` put the window floor at 940x560 and the
  generation of dialogs this replaces opened at a hardcoded 900x640 with four
  fields in them. The minimum is asserted on both pages, empty and with a
  hundred-row report open.

The sample files are real: rendered from the shipped templates through
``core.render``, exactly as ``tests/core/test_recipe_import.py`` does it, so a
test file is byte for byte what a run of this tool would put in front of a
user. ``resolve_template_path`` is never reached (the catalog resolves
templates from ``__file__``), so the "test cwd" trap does not apply here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QMimeData, QPoint, Qt, QUrl  # noqa: E402
from PyQt5.QtGui import QDropEvent  # noqa: E402
from PyQt5.QtWidgets import QDialog, QFileDialog  # noqa: E402

from auto_ext.core import render  # noqa: E402
from auto_ext.model.common import RenderTarget  # noqa: E402
from auto_ext.model.recipe import OutputKind, recipe_from_catalog  # noqa: E402
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.widgets import recipe_import_dialog as rid  # noqa: E402
from auto_ext.ui.widgets.recipe_import_dialog import (  # noqa: E402
    MAX_HINT_HEIGHT,
    MAX_HINT_WIDTH,
    PAGE_FILES,
    PAGE_REPORT,
    UNKNOWN_TARGET_TEXT,
    RecipeImportDialog,
)
from tests.support.v2 import ENV, make_dut, make_profile, make_run  # noqa: E402

CELL = "INV1"
LIBRARY = "INV_LIB"

#: A file whose name says nothing true about its content. Every sample is
#: offered under one of these.
LYING_NAMES = {
    RenderTarget.SI_ENV: "mystery-one.txt",
    RenderTarget.LVS_QCI: "mystery-two.dat",
    RenderTarget.QUANTUS_EXT: "mystery-three.log",
    RenderTarget.QUANTUS_DSPF: "mystery-four.bak",
    RenderTarget.JIVARO_XML: "mystery-five.tmp",
}


# ---- samples -------------------------------------------------------------


@pytest.fixture
def shipped(tmp_path: Path) -> dict[RenderTarget, str]:
    """What this tool writes today, for every target, as the user would see it."""

    recipe = recipe_from_catalog(
        recipe_id="shipped",
        name="shipped",
        output={"emit": [OutputKind.EXTRACTED_VIEW, OutputKind.DSPF]},
    )
    profile = make_profile()
    context = render.build_context(
        dut=make_dut(cell=CELL, library=LIBRARY),
        recipe=recipe,
        profile=profile,
        run=make_run(tmp_path),
        resolved_env=ENV,
    )
    return {
        plan.target: render.render_one(
            plan,
            context=context,
            recipe=recipe,
            profile=profile,
            resolved_env=ENV,
            out_dir=tmp_path / "rendered",
            write=False,
        ).text
        for plan in render.plan_targets(recipe)
    }


@pytest.fixture
def gui_export(fixtures_dir: Path) -> Path:
    """A Quantus ext.cmd this tool did not render: other version banner, other
    PDK paths, other cell, several values changed."""

    return fixtures_dir / "raw" / "gui_export.ext.cmd"


def _dialog(qtbot, **kwargs) -> RecipeImportDialog:
    dialog = RecipeImportDialog(**kwargs)
    qtbot.addWidget(dialog)
    return dialog


def _loaded(qtbot, shipped: dict[RenderTarget, str]) -> RecipeImportDialog:
    """A dialog holding all five files, each under a name that lies."""

    dialog = _dialog(qtbot)
    for target, text in shipped.items():
        dialog.add_source(LYING_NAMES[target], text)
    return dialog


def _analysed(qtbot, shipped: dict[RenderTarget, str]) -> RecipeImportDialog:
    dialog = _loaded(qtbot, shipped)
    assert dialog.analyse() is True, dialog.message_text()
    return dialog


# ---- pure helpers --------------------------------------------------------


def test_the_recipe_id_avoids_the_ids_the_host_already_has() -> None:
    assert rid.unique_recipe_id("RC typical 55C") == "rc-typical-55c"
    assert rid.unique_recipe_id("RC typical 55C", ["rc-typical-55c"]) == "rc-typical-55c-2"
    assert (
        rid.unique_recipe_id("RC typical 55C", ["rc-typical-55c", "rc-typical-55c-2"])
        == "rc-typical-55c-3"
    )
    assert rid.unique_recipe_id("   ") == "imported"


def test_the_first_section_lists_only_what_reached_a_recipe_field(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _analysed(qtbot, shipped)
    result = dialog.result_object()
    rows = rid.value_rows(result)

    assert rows, "nothing landed in the recipe at all"
    assert len(rows) == result.applied_count
    assert all(row.field for row in rows)
    assert [row.key for row in rows] == sorted(row.key for row in rows)
    assert any("-> recipe." in row.as_line() for row in rows)
    # The derived PdkProfile takes the PDK facts the same files carry, and the
    # section names the object each value went to.
    assert any("-> profile." in row.as_line() for row in rows)


def test_the_third_section_merges_the_unread_and_the_unassignable(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """Both populations end up at the catalog default, so both are reported.

    A value read but with no Recipe field, and a value no reader could recover,
    are different accidents with one consequence. Splitting them would make the
    user add two numbers together to answer "how much of my file does this
    thing not understand".
    """

    dialog = _analysed(qtbot, shipped)
    result = dialog.result_object()
    rows = rid.default_rows(result)

    unassignable = [value for value in result.mapped if not value.applied_to]
    assert len(rows) == len(unassignable) + len(result.left_at_default)
    keys = {row.key for row in rows}
    assert set(result.left_at_default) <= keys
    # An unread key has no value to show; an unassignable one does.
    assert all(row.value == "" for row in rows if row.key in result.left_at_default)
    assert any(row.value for row in rows)


def test_the_third_section_never_lists_a_value_the_recipe_holds(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """"Left at the catalog default" has to mean it.

    ``lvs_connect_by_name`` is recovered from whether its line is written at
    all, so the literal reader files it under ``unread`` while the recipe
    holds the value. The CLI's report has always dropped such a key from this
    section; the dialog extended from ``unread`` unconditionally, so the two
    surfaces gave different answers about one import -- and the GUI's answer
    was the wrong one, listing a value the user's recipe already carries.
    """

    # A runset from a site that does connect by name: the line is a boolean
    # spelled as a word, which is what the literal reader cannot take.
    connecting = dict(shipped)
    qci = connecting[RenderTarget.LVS_QCI]
    assert "*cmnVConnectNamesState" not in qci
    connecting[RenderTarget.LVS_QCI] = qci.replace(
        "*lvsReportOptions:", "*cmnVConnectNamesState: ALL\n*lvsReportOptions:"
    )

    dialog = _analysed(qtbot, connecting)
    result = dialog.result_object()
    landed = {row.key for row in result.mapped if row.applied_to}
    assert "lvs_connect_by_name" in landed, "the sample no longer exercises the case"
    assert "lvs_connect_by_name" in result.unread

    listed = {row.key for row in rid.default_rows(result)}
    assert "lvs_connect_by_name" not in listed
    assert not set(result.left_at_default) & landed


def test_the_third_section_groups_by_reason_biggest_first(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _analysed(qtbot, shipped)
    groups = rid.default_groups(rid.default_rows(dialog.result_object()))

    assert len(groups) > 1, "every row gave the same reason -- nothing to group"
    sizes = [len(rows) for _reason, rows in groups]
    assert sizes == sorted(sizes, reverse=True)
    assert all(reason for reason, _rows in groups), "a group with no reason"


# ---- the sizing contract -------------------------------------------------


def test_an_empty_dialog_demands_almost_nothing(qtbot) -> None:
    dialog = _dialog(qtbot)
    hint = dialog.minimumSizeHint()
    assert hint.width() <= MAX_HINT_WIDTH, f"empty, it demands {hint.width()}px"
    assert hint.height() <= MAX_HINT_HEIGHT, f"empty, it demands {hint.height()}px"


def test_it_still_fits_with_the_whole_report_open(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """A hundred rows of "not modelled" must not push the frame open.

    This is the shape of the bug the redesign found everywhere: a dialog that
    reserved 900x640 while showing four fields. Here the content is real and
    long, and the frame still has to fold into a 940x560 window.
    """

    dialog = _analysed(qtbot, shipped)
    dialog.show()
    qtbot.waitExposed(dialog)
    for section in dialog.sections():
        section.set_expanded(True)
    qtbot.wait(10)

    # Every section open at once is the worst case, and which of the three is
    # the long one is not the point: before the templates were parameterised
    # it was "not modelled", now it is "read into the recipe". The guard is on
    # the total, so it keeps stress-testing the layout either way.
    rows = sum(section.count() for section in dialog.sections())
    assert rows > 50, f"the report is not big enough to test ({rows} rows)"
    hint = dialog.minimumSizeHint()
    assert hint.width() <= MAX_HINT_WIDTH, f"the report demands {hint.width()}px"
    assert hint.height() <= MAX_HINT_HEIGHT, f"the report demands {hint.height()}px"


def test_the_size_it_opens_at_fits_the_window_floor() -> None:
    assert rid.DEFAULT_WIDTH <= theme.WINDOW_MIN_WIDTH
    assert rid.DEFAULT_HEIGHT <= theme.WINDOW_MIN_HEIGHT
    assert MAX_HINT_WIDTH <= rid.DEFAULT_WIDTH
    assert MAX_HINT_HEIGHT <= rid.DEFAULT_HEIGHT


# ---- picking the files ---------------------------------------------------


def test_every_file_is_recognised_by_its_content_not_its_name(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _loaded(qtbot, shipped)

    assert len(dialog.file_rows()) == len(shipped)
    for row in dialog.file_rows():
        assert not row.label.endswith((".env", ".qci", ".cmd", ".xml"))
    assert {row.detected for row in dialog.file_rows()} == set(shipped)
    assert all(not row.is_forced() for row in dialog.file_rows())
    assert dialog.analyse_button().isEnabled()


def test_an_unrecognisable_file_asks_instead_of_failing(qtbot) -> None:
    dialog = _dialog(qtbot)
    dialog.add_source("notes.txt", "just some notes\nnothing to do with EDA\n")

    row = dialog.file_rows()[0]
    assert row.detected is None
    assert row.combo().currentText() == UNKNOWN_TARGET_TEXT
    assert row.chosen() is None
    assert dialog.analyse_button().isEnabled() is False
    assert "could not be recognised" in dialog.message_text()

    row.combo().setCurrentIndex(row.combo().findData(RenderTarget.JIVARO_XML.value))
    assert row.chosen() is RenderTarget.JIVARO_XML
    assert row.is_forced() is True
    assert dialog.analyse_button().isEnabled() is True
    assert dialog.message_text() == ""


def test_the_target_combo_survives_a_long_file_name(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """The row's only control must not be squeezed away by the path beside it.

    The name label elides and the ``lines`` count is fixed, so the combo is
    what a greedy layout takes the width from -- and a one-pixel combo means
    the user cannot see, let alone change, what the file was recognised as.
    """

    dialog = _dialog(qtbot)
    dialog.add_source(
        "/proj/very/deep/tree/of/directories/that/keeps/going/mystery.log",
        shipped[RenderTarget.QUANTUS_DSPF],
    )
    dialog.show()
    qtbot.waitExposed(dialog)

    combo = dialog.file_rows()[0].combo()
    assert combo.currentText() == RenderTarget.QUANTUS_DSPF.value
    assert combo.width() >= combo.fontMetrics().horizontalAdvance(combo.currentText())


def test_a_named_target_is_reported_as_named_by_hand(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """Overriding the detector is allowed, and never silent."""

    dialog = _dialog(qtbot)
    dialog.add_source("mystery.log", shipped[RenderTarget.QUANTUS_EXT])
    row = dialog.file_rows()[0]
    assert row.detected is RenderTarget.QUANTUS_EXT
    row.combo().setCurrentIndex(row.combo().findData(RenderTarget.QUANTUS_DSPF.value))

    assert dialog.analyse() is True
    result = dialog.result_object()
    assert result.sources[0].target is RenderTarget.QUANTUS_DSPF
    assert result.sources[0].forced is True
    assert "named by hand" in dialog.report_meta_text()


def test_the_same_file_is_not_offered_twice(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _dialog(qtbot)
    assert dialog.add_source("one.txt", shipped[RenderTarget.SI_ENV]) is True
    assert dialog.add_source("one.txt", shipped[RenderTarget.SI_ENV]) is False
    assert len(dialog.file_rows()) == 1


def test_a_file_can_be_taken_back_out(qtbot, shipped: dict[RenderTarget, str]) -> None:
    dialog = _loaded(qtbot, shipped)
    with qtbot.waitSignal(dialog.files_changed, timeout=1000) as blocker:
        dialog.file_rows()[0].remove_requested.emit(dialog.file_rows()[0])
    assert blocker.args == [len(shipped) - 1]
    assert len(dialog.file_rows()) == len(shipped) - 1


def test_files_arrive_from_disk_and_from_a_drop(
    qtbot, tmp_path: Path, shipped: dict[RenderTarget, str]
) -> None:
    """One drop carries every file of a flow -- the base DropZone takes one."""

    first = tmp_path / "a.txt"
    first.write_text(shipped[RenderTarget.SI_ENV], encoding="utf-8")
    second = tmp_path / "b.txt"
    second.write_text(shipped[RenderTarget.LVS_QCI], encoding="utf-8")

    dialog = _dialog(qtbot)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(first)), QUrl.fromLocalFile(str(second))])
    event = QDropEvent(
        QPoint(1, 1), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier
    )
    dialog.drop_zone().dropEvent(event)

    assert len(dialog.file_rows()) == 2
    assert {row.detected for row in dialog.file_rows()} == {
        RenderTarget.SI_ENV,
        RenderTarget.LVS_QCI,
    }


def test_the_add_button_goes_through_the_file_chooser(
    qtbot, tmp_path: Path, monkeypatch, shipped: dict[RenderTarget, str]
) -> None:
    path = tmp_path / "whatever.txt"
    path.write_text(shipped[RenderTarget.JIVARO_XML], encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileNames",
        staticmethod(lambda *args, **kwargs: ([str(path)], "")),
    )

    dialog = _dialog(qtbot)
    dialog.add_button().click()
    assert [row.detected for row in dialog.file_rows()] == [RenderTarget.JIVARO_XML]


def test_a_file_that_cannot_be_read_says_so_in_the_failure_colour(
    qtbot, tmp_path: Path
) -> None:
    dialog = _dialog(qtbot)
    assert dialog.add_paths([tmp_path / "does-not-exist.cmd"]) == 0
    assert dialog.file_rows() == []
    assert "does-not-exist.cmd" in dialog.message_text()
    assert theme.STATUS_FAILED in dialog.message_label().styleSheet()


# ---- the report ----------------------------------------------------------


def test_the_report_reads_as_three_numbers(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """Landed / patched / not modelled, each with its own count in the header."""

    dialog = _analysed(qtbot, shipped)
    assert dialog.page() == PAGE_REPORT

    values, hunks, defaults = dialog.sections()
    assert values.title_text() == "Read into the recipe or the profile"
    assert hunks.title_text() == "Kept as manual edits"
    assert defaults.title_text().startswith("Not modelled")

    result = dialog.result_object()
    assert values.count() == result.applied_count > 0
    assert hunks.count() == result.hunk_count
    assert defaults.count() == len(rid.default_rows(result))
    # This comparison used to run the other way, and the inversion is the whole
    # point of parameterising the templates: the shipped files no longer freeze
    # the values, so most of what a user's file says now reaches a Recipe field
    # instead of being reported as "came out right by coincidence".
    assert values.count() > defaults.count(), (
        "most of a shipped file should now read into the recipe"
    )
    assert values.count_text() == f"{values.count()} values"
    assert defaults.count_text().endswith("options")


def test_the_number_of_unmodelled_options_is_never_hidden(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """The regression this section exists to prevent.

    "77 values imported" on its own is a half-truth: the rest of the options
    are in the user's file too and come out right for a reason the number does
    not give. The section shrank a lot when the templates were parameterised
    and the dominant reason changed with it -- most of what is left belongs to
    the PdkProfile, not to the recipe -- but a shrinking number is exactly when
    a section like this gets quietly dropped, so it is still counted here.
    """

    dialog = _analysed(qtbot, shipped)
    defaults = dialog.defaults_section()
    assert defaults.count() == len(rid.default_rows(dialog.result_object()))
    assert defaults.count() > 0
    assert defaults.toggle_button().isEnabled()

    defaults.set_expanded(True)
    assert defaults.is_expanded()
    body = defaults.body_layout()
    assert body.count() >= 2, "expanded and empty"
    text = "\n".join(
        widget.plain_text() if hasattr(widget, "plain_text") else widget.text()
        for widget in (body.itemAt(i).widget() for i in range(body.count()))
    )
    # A profile-owned row the templates still hardcode: no field on either
    # object can move it, so it is reported rather than silently applied. The
    # rows that ARE fields of the derived profile -- res_component, the corner,
    # the deck directory -- moved to the first section when the importer
    # started filling that profile from the files.
    assert "lvs_rules_filename_pattern" in text
    assert "option" in text  # the group captions say how many


def test_a_hunk_expands_into_the_diff_it_will_become(
    qtbot, gui_export: Path
) -> None:
    dialog = _dialog(qtbot)
    assert dialog.add_paths([gui_export]) == 1
    assert dialog.analyse() is True

    hunks = dialog.hunks_section()
    assert hunks.count() == 1
    assert "% of the imported lines" in hunks.hint_text()
    hunks.set_expanded(True)

    block = hunks.body_layout().itemAt(0).widget()
    head = block.layout().itemAt(0).widget()
    diff = block.layout().itemAt(1).widget()
    assert "quantus.ext.cmd line" in head.full_text()
    tags = {tag for tag, _text in diff.rows()}
    assert "add" in tags and "remove" in tags
    assert "19.14-s012" in diff.plain_text()


def test_an_import_with_no_hunks_says_so_rather_than_showing_an_empty_list(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _analysed(qtbot, shipped)
    hunks = dialog.hunks_section()
    assert hunks.count() == 0
    assert hunks.hint_text() == "the catalog explains every line of these files"
    assert hunks.toggle_button().isEnabled() is False
    hunks.set_expanded(True)
    assert hunks.is_expanded() is False


def test_the_round_trip_is_reported(qtbot, shipped: dict[RenderTarget, str]) -> None:
    dialog = _analysed(qtbot, shipped)
    assert dialog.result_object().clean_roundtrip is True
    assert theme.STATUS_GLYPH["passed"] in dialog.roundtrip_text()
    assert "byte for byte" in dialog.roundtrip_text()


# ---- the warning ---------------------------------------------------------


def _hexes(style: str) -> list[str]:
    return re.findall(r"#[0-9a-fA-F]{6}", style)


def _forked_qci(shipped: dict[RenderTarget, str]) -> str:
    """A ``.qci`` half of whose lines this build has never heard of.

    Inserted in the middle rather than appended: a pure insertion at the end
    of a file has no anchor after it, which ``PatchHunk`` refuses -- see
    :func:`test_a_file_the_patch_format_cannot_hold_is_refused_not_crashed`.
    """

    lines = shipped[RenderTarget.LVS_QCI].splitlines(keepends=True)
    extra = [f"*lvsLegacyOption{index}: yes\n" for index in range(40)]
    return "".join(lines[:5] + extra + lines[5:])


def test_too_large_a_patch_warns_in_amber_and_never_in_red(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """A patch that big is a fork, and the user has to be told -- but told.

    It is a judgement about whether the import is worth keeping, not a failed
    operation, so it uses the warning scale. The accent is neither, which is
    the rule the whole palette is built on.
    """

    dialog = _dialog(qtbot)
    dialog.add_source("legacy.qci", _forked_qci(shipped))
    assert dialog.analyse() is True

    result = dialog.result_object()
    assert result.high_unmodelled is True
    assert "manual edits" in dialog.warning_text()

    style = dialog.warning_label().styleSheet()
    assert theme.WARNING_TEXT_ON_WHITE in style
    assert theme.STATUS_FILL["warning"] in style
    assert theme.STATUS_FAILED not in style
    assert not (theme.accent_colors() & set(_hexes(style)))


def test_no_warning_means_no_banner(qtbot, shipped: dict[RenderTarget, str]) -> None:
    dialog = _analysed(qtbot, shipped)
    assert dialog.result_object().warnings == ()
    assert dialog.warning_text() == ""


# ---- refusals ------------------------------------------------------------


def test_two_files_of_one_target_are_refused_on_the_files_page(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _dialog(qtbot)
    dialog.add_source("one.log", shipped[RenderTarget.QUANTUS_EXT])
    dialog.add_source("two.log", shipped[RenderTarget.QUANTUS_EXT])

    with qtbot.waitSignal(dialog.analysis_failed, timeout=1000):
        assert dialog.analyse() is False
    assert dialog.page() == PAGE_FILES
    assert "one import produces at most one file per target" in dialog.message_text()
    assert dialog.result_object() is None


def test_a_file_the_patch_format_cannot_hold_is_refused_not_crashed(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """Forty lines appended at EOF is ordinary input and an invalid hunk.

    ``capture_patch`` produces a pure insertion with no anchor after it and
    ``PatchHunk`` rejects it -- with a ``pydantic.ValidationError``, which is
    not an ``AutoExtError``. Letting that reach the event loop would take the
    window down over a file the user just picked.
    """

    dialog = _dialog(qtbot)
    extra = "".join(f"*lvsLegacyOption{index}: yes\n" for index in range(40))
    dialog.add_source("appended.qci", shipped[RenderTarget.LVS_QCI] + extra)

    assert dialog.analyse() is False
    assert dialog.page() == PAGE_FILES
    assert "cannot be turned into a recipe" in dialog.message_text()


def test_analysing_nothing_does_nothing(qtbot) -> None:
    dialog = _dialog(qtbot)
    assert dialog.analyse_button().isEnabled() is False
    assert dialog.analyse() is False
    assert dialog.page() == PAGE_FILES


# ---- confirming ----------------------------------------------------------


def test_the_pages_swap_their_buttons(qtbot, shipped: dict[RenderTarget, str]) -> None:
    dialog = _dialog(qtbot)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.page() == PAGE_FILES
    assert dialog.analyse_button().isVisible() is True
    assert dialog.import_button().isVisible() is False

    for target, text in shipped.items():
        dialog.add_source(LYING_NAMES[target], text)
    assert dialog.analyse() is True
    qtbot.wait(10)
    assert dialog.import_button().isVisible() is True
    assert dialog.analyse_button().isVisible() is False

    dialog.back_button().click()
    qtbot.wait(10)
    assert dialog.page() == PAGE_FILES
    assert dialog.analyse_button().isVisible() is True


def test_enter_does_the_action_of_the_page_it_is_pressed_on(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    """Left to Qt, Return goes to the first auto-default button in the chain.

    Here that is ``Add files...``, so typing a name and pressing Enter would
    open a file chooser instead of doing the obvious thing.
    """

    dialog = _loaded(qtbot, shipped)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.analyse_button().isDefault() is True
    assert dialog.add_button().autoDefault() is False

    qtbot.keyClick(dialog.name_edit(), Qt.Key_Return)
    assert dialog.page() == PAGE_REPORT
    assert dialog.import_button().isDefault() is True


def test_a_second_analysis_that_fails_drops_the_first_report(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _analysed(qtbot, shipped)
    assert dialog.result_object() is not None

    dialog.back_button().click()
    dialog.add_source("second.log", shipped[RenderTarget.QUANTUS_EXT])
    assert dialog.analyse() is False
    assert dialog.result_object() is None, "the report outlived the files it described"


def test_confirming_hands_the_result_out_and_writes_nothing(
    qtbot, tmp_path: Path, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _analysed(qtbot, shipped)
    before = sorted(tmp_path.rglob("*.yaml"))

    with qtbot.waitSignal(dialog.import_accepted, timeout=1000) as blocker:
        dialog.import_button().click()

    result = blocker.args[0]
    assert result is dialog.result_object()
    assert result.recipe.recipe_id == dialog.recipe_id()
    assert dialog.result() == QDialog.Accepted
    assert sorted(tmp_path.rglob("*.yaml")) == before, "the dialog wrote a recipe"


def test_cancelling_hands_nothing_out(qtbot, shipped: dict[RenderTarget, str]) -> None:
    dialog = _analysed(qtbot, shipped)
    handed: list[object] = []
    dialog.import_accepted.connect(handed.append)

    dialog.cancel_button().click()
    assert handed == []
    assert dialog.result() == QDialog.Rejected


def test_the_typed_name_is_what_the_recipe_gets(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _dialog(qtbot, existing_ids=["rc-typical-55c"])
    dialog.add_source("mystery.log", shipped[RenderTarget.QUANTUS_EXT])
    dialog.name_edit().setText("RC typical 55C")

    assert dialog.recipe_id() == "rc-typical-55c-2"
    assert dialog.analyse() is True
    assert dialog.result_object().recipe.name == "RC typical 55C"
    assert dialog.result_object().recipe.recipe_id == "rc-typical-55c-2"


def test_the_name_defaults_to_the_first_file(
    qtbot, shipped: dict[RenderTarget, str]
) -> None:
    dialog = _dialog(qtbot)
    assert dialog.recipe_name() == "Imported recipe"
    dialog.add_source("dco-tank.log", shipped[RenderTarget.QUANTUS_EXT])
    assert dialog.recipe_name() == "dco-tank"
    assert dialog.recipe_id() == "dco-tank"
