"""The Project screen: can a user actually change these two objects, and safely.

``WorkspaceConfig`` and ``PdkProfile`` had no control anywhere in the GUI
until this screen -- ``main_window``'s own docstring recorded the workspace
half as a *known gap*, which is the politest version of the same defect the
first office session found eight of. ``tests/ui/test_reachability.py`` is the
mechanical backstop that every field is bound; this file is about whether the
binding behaves.

Three properties matter more than the rest, and each is here because getting
it wrong reproduces a defect this project has already paid for:

1. **A validation failure appears at the control**, not at save time. The
   models validate on assignment precisely so a bad value can be reported
   while the user is still looking at what they typed.
2. **Only what changed is handed out.** A save that rewrote both files every
   time would rewrite ``workspace.yaml`` on a profile-only edit, which is how
   comment-preserving round trips quietly lose comments.
3. **A host push is not a user edit.** ``CellsScreen`` needed a fence for
   exactly this; a screen that marks itself dirty on load makes the window
   open with a star in the title and Save enabled, offering to write back the
   file it just read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")

from PyQt5.QtWidgets import QComboBox, QLineEdit, QSpinBox  # noqa: E402

from auto_ext.model.pdk import CornerSpec  # noqa: E402
from auto_ext.ui.project_fields import field_for_check  # noqa: E402
from auto_ext.ui.screens.project_screen import (  # noqa: E402
    UNSET_TEXT,
    ProjectScreen,
    field_editors,
)
from tests.support.v2 import make_profile, make_workspace  # noqa: E402


@pytest.fixture
def screen(qtbot) -> ProjectScreen:
    widget = ProjectScreen()
    qtbot.addWidget(widget)
    widget.set_project(
        workspace=make_workspace(),
        profile=make_profile(),
        config_dir="/work/wa/Auto_ext_pro/config",
        profile_ids=["hn001", "cf028"],
    )
    return widget


def _type(row, text: str) -> None:
    """Put ``text`` in a text row and commit it, as focus-out would."""

    control = row.control()
    assert isinstance(control, QLineEdit), control
    control.setText(text)
    row.commit()


# ---- loading ---------------------------------------------------------------


def test_every_declared_field_has_a_row(screen: ProjectScreen) -> None:
    for spec in field_editors():
        assert screen.row(spec.path) is not None, spec.path


def test_loading_shows_the_values_that_were_loaded(screen: ProjectScreen) -> None:
    assert screen.row("display_name").value() == "HN001 22nm"
    assert screen.row("lvs_decks.dir_expr").value() == "$calibre_source_added_place|parent"
    assert screen.row("output_dir_pattern").value() == "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    assert screen.row("tech_name_env_vars").value() == [
        "PDK_TECH_FILE",
        "PDK_LAYER_MAP_FILE",
        "PDK_DISPLAY_FILE",
    ]


def test_loading_is_not_an_edit(screen: ProjectScreen) -> None:
    """The fence CellsScreen needed, for the same reason."""

    assert screen.is_dirty() is False


def test_an_empty_project_disables_every_row(qtbot) -> None:
    """Not hidden: a screen that renders nothing looks like a crash."""

    widget = ProjectScreen()
    qtbot.addWidget(widget)
    widget.set_project(workspace=None, profile=None)
    assert widget.is_dirty() is False
    assert all(not widget.row(spec.path).isEnabled() for spec in field_editors())


# ---- editing ---------------------------------------------------------------


def test_editing_a_profile_field_reaches_the_working_copy(screen: ProjectScreen) -> None:
    _type(screen.row("tech_name"), "HN002")
    assert screen.profile().tech_name == "HN002"
    assert screen.is_dirty() is True


def test_editing_a_workspace_field_reaches_the_working_copy(screen: ProjectScreen) -> None:
    _type(screen.row("intermediate_dir"), "${WORK_ROOT2}/{run_slug}")
    assert screen.workspace().intermediate_dir == "${WORK_ROOT2}/{run_slug}"
    assert screen.is_dirty() is True


def test_the_callers_object_is_never_touched(qtbot) -> None:
    """The screen owns a working copy; the host's object stays as it was."""

    original = make_profile()
    widget = ProjectScreen()
    qtbot.addWidget(widget)
    widget.set_project(workspace=make_workspace(), profile=original)
    _type(widget.row("tech_name"), "HN002")
    assert original.tech_name == "HN001"
    assert widget.profile().tech_name == "HN002"


def test_editing_back_to_the_loaded_value_is_not_dirty(screen: ProjectScreen) -> None:
    """Dirtiness is a comparison, not a counter of keystrokes."""

    _type(screen.row("tech_name"), "HN002")
    assert screen.is_dirty() is True
    _type(screen.row("tech_name"), "HN001")
    assert screen.is_dirty() is False


def test_a_list_field_is_one_entry_per_line(screen: ProjectScreen) -> None:
    row = screen.row("power_names")
    row.control().setPlainText("VDD\nVDDA\n\n  VDDIO  \n")
    row.commit()
    assert screen.profile().power_names == ["VDD", "VDDA", "VDDIO"]


def test_a_mapping_field_takes_name_equals_value(screen: ProjectScreen) -> None:
    row = screen.row("env_overrides")
    row.control().setPlainText("SETUP_ROOT = /pdk/setup\nVERIFY_ROOT=/pdk/verify")
    row.commit()
    assert screen.profile().env_overrides == {
        "SETUP_ROOT": "/pdk/setup",
        "VERIFY_ROOT": "/pdk/verify",
    }


def test_a_mapping_value_may_contain_a_colon(screen: ProjectScreen) -> None:
    """`=` is tried first: a path expression is far likelier to hold a colon."""

    row = screen.row("extra_paths")
    row.control().setPlainText("scratch = $A:$B/x")
    row.commit()
    assert screen.profile().extra_paths == {"scratch": "$A:$B/x"}


def test_a_mapping_line_with_no_separator_is_reported_not_swallowed(
    screen: ProjectScreen,
) -> None:
    row = screen.row("env_overrides")
    row.control().setPlainText("SETUP_ROOT /pdk/setup")
    row.commit()
    assert "SETUP_ROOT /pdk/setup" in row.error()
    assert screen.profile().env_overrides == {}
    assert screen.is_dirty() is False


# ---- validation ------------------------------------------------------------


def test_an_invalid_pattern_key_is_reported_under_its_own_control(
    screen: ProjectScreen,
) -> None:
    """``{task_id}`` was retired; the model says so and the row must show it."""

    row = screen.row("output_dir_pattern")
    _type(row, "${WORK_ROOT}/{task_id}")
    assert "task_id" in row.error()
    # and the working copy still holds the value that validated
    assert screen.workspace().output_dir_pattern == "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    assert screen.is_dirty() is False


def test_an_unknown_format_key_is_reported_too(screen: ProjectScreen) -> None:
    row = screen.row("output_dir_pattern")
    _type(row, "${WORK_ROOT}/{nonsense}")
    assert "nonsense" in row.error()


def test_a_fixed_value_clears_the_error(screen: ProjectScreen) -> None:
    row = screen.row("output_dir_pattern")
    _type(row, "${WORK_ROOT}/{task_id}")
    assert row.error()
    _type(row, "${WORK_ROOT}/{cell}")
    assert row.error() == ""
    assert screen.is_dirty() is True


def test_save_is_refused_while_a_field_is_invalid(screen: ProjectScreen, qtbot) -> None:
    """A refused save is not a lost edit: the good rows stay staged."""

    _type(screen.row("tech_name"), "HN002")
    _type(screen.row("output_dir_pattern"), "${WORK_ROOT}/{task_id}")
    with qtbot.assertNotEmitted(screen.save_requested):
        screen._save_btn.click()
    assert screen.profile().tech_name == "HN002"


# ---- saving ----------------------------------------------------------------


def test_save_hands_out_only_what_changed(screen: ProjectScreen, qtbot) -> None:
    """A profile-only edit must not offer to rewrite workspace.yaml."""

    _type(screen.row("tech_name"), "HN002")
    with qtbot.waitSignal(screen.save_requested) as caught:
        screen._save_btn.click()
    workspace, profile = caught.args
    assert workspace is None
    assert profile.tech_name == "HN002"


def test_save_hands_out_both_when_both_changed(screen: ProjectScreen, qtbot) -> None:
    _type(screen.row("tech_name"), "HN002")
    _type(screen.row("intermediate_dir"), "${WORK_ROOT2}/{cell}")
    with qtbot.waitSignal(screen.save_requested) as caught:
        screen._save_btn.click()
    workspace, profile = caught.args
    assert workspace is not None and profile is not None


def test_save_and_revert_are_disabled_until_something_changes(
    screen: ProjectScreen,
) -> None:
    assert screen._save_btn.isEnabled() is False
    assert screen._revert_btn.isEnabled() is False
    _type(screen.row("tech_name"), "HN002")
    assert screen._save_btn.isEnabled() is True
    assert screen._revert_btn.isEnabled() is True


# ---- choices ---------------------------------------------------------------


def test_the_profile_picker_offers_what_the_host_supplied(screen: ProjectScreen) -> None:
    combo = screen.row("pdk_profile").control()
    assert isinstance(combo, QComboBox)
    assert [combo.itemText(i) for i in range(combo.count())] == ["hn001", "cf028"]
    assert combo.currentText() == "hn001"


def test_a_nullable_choice_offers_unset_and_says_what_it_means(
    screen: ProjectScreen,
) -> None:
    row = screen.row("default_corner")
    combo = row.control()
    assert combo.itemText(0) == UNSET_TEXT
    assert combo.itemData(0) is None
    # the help line has to carry the meaning; an empty combo row does not
    assert "unset" in row._help.text().lower()


def test_the_corner_picker_lists_the_corners_in_the_table(screen: ProjectScreen) -> None:
    combo = screen.row("default_corner").control()
    assert [combo.itemText(i) for i in range(combo.count())] == [UNSET_TEXT, "typical"]


def test_adding_a_corner_makes_it_selectable_as_the_default(
    screen: ProjectScreen,
) -> None:
    """The whole reason a corner table and a corner default share a screen."""

    row = screen.row("corners")
    table = row.control()
    table.set_rows(
        [
            CornerSpec(name="typical", technology_corner="TYPICAL"),
            CornerSpec(name="rcworst", technology_corner="RCWORST"),
        ]
    )
    row.commit()
    assert [c.name for c in screen.profile().corners] == ["typical", "rcworst"]
    combo = screen.row("default_corner").control()
    assert [combo.itemText(i) for i in range(combo.count())] == [
        UNSET_TEXT,
        "typical",
        "rcworst",
    ]


def test_a_half_typed_table_row_is_not_an_error(screen: ProjectScreen) -> None:
    """A corner needs two columns and a user types them one at a time."""

    row = screen.row("corners")
    table = row.control()
    table._on_add()
    table.table().item(table.row_count() - 1, 0).setText("rcworst")
    assert row.error() == ""
    # the incomplete row is on screen but not in the working copy yet
    assert table.row_count() == 2
    assert [c.name for c in screen.profile().corners] == ["typical"]


def test_removing_the_corner_the_default_points_at_is_reported(
    screen: ProjectScreen,
) -> None:
    """The model refuses it, and the message has to reach the user.

    Silently dropping ``default_corner`` would be worse than the error: a
    recipe that left its corner unset would start rendering without one.
    """

    row = screen.row("corners")
    row.control().set_rows([CornerSpec(name="rcworst", technology_corner="RCWORST")])
    row.commit()
    assert "default_corner" in row.error()
    assert [c.name for c in screen.profile().corners] == ["typical"]


# ---- the Setup drawer's way in ---------------------------------------------


def test_every_check_field_mapping_points_at_a_real_row(screen: ProjectScreen) -> None:
    """``CHECK_FIELDS`` is a claim about this screen; hold it to it."""

    from auto_ext.ui.project_fields import CHECK_FIELDS

    for check_id, path in CHECK_FIELDS.items():
        assert screen.row(path) is not None, f"{check_id} -> {path} has no row"


def test_a_numbered_check_resolves_to_its_list_field() -> None:
    """``pdk.cdl_include.2`` is the second entry of one list field."""

    assert field_for_check("pdk.cdl_include") == "cdl_include_files"
    assert field_for_check("pdk.cdl_include.2") == "cdl_include_files"


def test_checks_with_no_field_to_open_map_to_nothing() -> None:
    """PATH and the shell environment are not fixed on this screen."""

    assert field_for_check("tool.calibre") is None
    assert field_for_check("env.setup_root") is None


def test_scroll_to_reports_a_path_it_cannot_show(screen: ProjectScreen) -> None:
    assert screen.scroll_to("lvs_decks.dir_expr") is True
    assert screen.scroll_to("no.such.field") is False


# ---- chrome ----------------------------------------------------------------


def test_the_header_names_the_project_and_keeps_the_full_path(
    screen: ProjectScreen,
) -> None:
    assert screen._title.text() == "config"
    assert screen._title.toolTip() == "/work/wa/Auto_ext_pro/config"


def test_keep_runs_is_a_number_control(screen: ProjectScreen) -> None:
    """It was a plain text box class of defect that the office round found."""

    assert isinstance(screen.row("keep_runs").control(), QSpinBox)


def test_reloading_clears_a_field_error(screen: ProjectScreen) -> None:
    row = screen.row("output_dir_pattern")
    _type(row, "${WORK_ROOT}/{task_id}")
    assert row.error()
    screen.set_project(workspace=make_workspace(), profile=make_profile())
    assert row.error() == ""
    assert screen.errors() == {}
    assert screen.is_dirty() is False


def test_the_status_line_says_which_of_the_three_states_it_is_in(
    screen: ProjectScreen, qtbot
) -> None:
    messages: list[str] = []
    screen.status_changed.connect(messages.append)
    _type(screen.row("tech_name"), "HN002")
    assert messages[-1] == "unsaved changes"
    _type(screen.row("output_dir_pattern"), "${WORK_ROOT}/{task_id}")
    assert "task_id" in messages[-1]


def test_a_project_directory_with_no_trailing_name_still_renders(qtbot) -> None:
    widget = ProjectScreen()
    qtbot.addWidget(widget)
    widget.set_project(
        workspace=make_workspace(), profile=make_profile(), config_dir=""
    )
    assert widget._title.text() == "project"


def test_the_screen_writes_nothing(screen: ProjectScreen, tmp_path: Path) -> None:
    """It is a widget, not a persistence layer -- the host owns the disk."""

    _type(screen.row("tech_name"), "HN002")
    screen._save_btn.click()
    assert list(tmp_path.iterdir()) == []
