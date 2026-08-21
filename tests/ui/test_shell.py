"""Shell tests: nav rail, health badge, Setup drawer, sizing contract.

The sizing cases are the load-bearing ones. Artboard ``1j`` fixes the
window floor at 940x560, and the reason the old UI cannot reach it is that
chrome and pages quietly advertise minimum widths and heights of their own.
:func:`test_shell_chrome_costs_almost_nothing` pins the shell's own
contribution far below that floor, so when the last old tab is replaced
the window really can shrink.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QLabel, QWidget  # noqa: E402

from auto_ext.model.pdk import CheckStatus, PdkCheckResult, PdkHealthReport  # noqa: E402
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.shell import (  # noqa: E402
    HealthBadge,
    NavButton,
    Shell,
    health_badge_state,
)

NOW = datetime(2026, 8, 20, 17, 42, tzinfo=timezone.utc)


def _result(check_id: str, status: CheckStatus, *, required: bool = True) -> PdkCheckResult:
    return PdkCheckResult(
        check_id=check_id,
        status=status,
        required=required,
        title=check_id,
        fix_hint="run check-env" if status is not CheckStatus.OK else None,
        checked_at=NOW,
    )


def _report(*results: PdkCheckResult) -> PdkHealthReport:
    return PdkHealthReport(profile_id="hn001", checked_at=NOW, results=list(results))


def _shell(qtbot) -> Shell:
    shell = Shell()
    qtbot.addWidget(shell)
    return shell


def _shell_with_pages(qtbot) -> Shell:
    shell = _shell(qtbot)
    shell.add_page("cells", "Cells", QWidget(), code="CEL", count=8)
    shell.add_page("recipes", "Recipes", QWidget(), code="REC", count=4)
    shell.add_page("runs", "Runs", QWidget(), code="RUN", count=137)
    return shell


# ---- sizing contract -----------------------------------------------------


def test_shell_chrome_costs_almost_nothing(qtbot) -> None:
    """The frame itself must never be why the window cannot shrink.

    With no pages mounted the shell is title bar + rail + empty stack +
    status bar. That whole assembly has to stay far under the 940x560
    window floor, or every screen added later inherits a debt it cannot
    pay off.
    """

    shell = _shell(qtbot)
    hint = shell.minimumSizeHint()

    assert hint.width() <= 200, f"shell chrome demands {hint.width()}px of width"
    assert hint.height() <= 200, f"shell chrome demands {hint.height()}px of height"
    assert hint.width() < theme.WINDOW_MIN_WIDTH
    assert hint.height() < theme.WINDOW_MIN_HEIGHT


def test_a_long_config_path_does_not_widen_the_shell(qtbot) -> None:
    """The title-bar path elides. An ordinary QLabel would not.

    A plain label reports its full text width as a minimum, so one deep
    project path would silently raise the window's minimum width.
    """

    shell = _shell(qtbot)
    before = shell.minimumSizeHint().width()

    shell.set_config_path("/proj/pdk/CFXXX/verify/" + "very_long_directory_name/" * 12)

    assert shell.minimumSizeHint().width() == before
    assert shell.minimumSizeHint().width() <= 200


def test_chrome_bars_advertise_zero_width(qtbot) -> None:
    shell = _shell(qtbot)
    for bar in (shell._title_bar, shell._status_bar):
        assert bar.minimumSizeHint().width() == 0
    assert shell._title_bar.height() == theme.TITLEBAR_HEIGHT
    assert shell._status_bar.height() == theme.STATUSBAR_HEIGHT


# ---- registering pages ---------------------------------------------------


def test_add_page_wires_nav_item_and_stack(qtbot) -> None:
    shell = _shell_with_pages(qtbot)

    assert shell.page_keys() == ["cells", "recipes", "runs"]
    assert shell.stack.count() == 3
    for key in shell.page_keys():
        assert shell.stack.indexOf(shell.page(key)) >= 0
        assert isinstance(shell.nav_button(key), NavButton)


def test_the_first_page_registered_becomes_current(qtbot) -> None:
    """A freshly built shell is never blank."""

    shell = _shell_with_pages(qtbot)

    assert shell.current_page_key() == "cells"
    assert shell.stack.currentWidget() is shell.page("cells")
    assert shell.nav_button("cells").is_selected()
    assert not shell.nav_button("runs").is_selected()


def test_duplicate_page_key_is_rejected(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    with pytest.raises(ValueError, match="already exists"):
        shell.add_page("cells", "Cells again", QWidget())


def test_unknown_page_key_is_rejected(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    with pytest.raises(KeyError):
        shell.set_current_page("nope")
    with pytest.raises(KeyError):
        shell.set_page_count("nope", 1)


def test_switching_pages_emits_once_and_moves_the_selection(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    seen: list[str] = []
    shell.page_changed.connect(seen.append)

    shell.set_current_page("runs")
    assert seen == ["runs"]
    assert shell.stack.currentWidget() is shell.page("runs")
    assert shell.nav_button("runs").is_selected()
    assert not shell.nav_button("cells").is_selected()

    # Re-selecting the current page is a no-op, not a second emission: the
    # screens hang refresh work off this signal.
    shell.set_current_page("runs")
    assert seen == ["runs"]


def test_clicking_a_nav_item_switches_the_page(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    seen: list[str] = []
    shell.page_changed.connect(seen.append)

    qtbot.mouseClick(shell.nav_button("recipes"), Qt.LeftButton)

    assert seen == ["recipes"]
    assert shell.current_page_key() == "recipes"


def test_nav_items_carry_a_count_and_none_means_no_number(qtbot) -> None:
    """Zero is an answer ("no cells yet"); ``None`` means "nothing to count"."""

    shell = _shell_with_pages(qtbot)
    assert shell.nav_button("cells").count_text() == "8"

    shell.set_page_count("cells", 0)
    assert shell.nav_button("cells").count_text() == "0"

    shell.set_page_count("cells", None)
    assert shell.nav_button("cells").count_text() == ""


def test_remove_page_hands_the_widget_back_and_reselects(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    widget = shell.page("cells")

    returned = shell.remove_page("cells")

    assert returned is widget
    assert shell.page_keys() == ["recipes", "runs"]
    assert shell.nav_button("cells") is None
    assert shell.current_page_key() == "recipes"
    assert shell.remove_page("cells") is None


# ---- the nav rail --------------------------------------------------------


def test_rail_starts_expanded_at_the_design_width(qtbot) -> None:
    shell = _shell_with_pages(qtbot)

    assert not shell.is_rail_collapsed()
    assert shell.nav_rail.width() == theme.NAV_RAIL_WIDTH
    assert shell.nav_button("cells").displayed_text() == "Cells"
    assert shell.nav_button("cells").height() == theme.NAV_ITEM_HEIGHT


def test_collapsing_swaps_labels_for_three_letter_codes(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    shell.auto_collapse_rail = False

    shell.set_rail_collapsed(True)

    assert shell.nav_rail.width() == theme.NAV_RAIL_COLLAPSED_WIDTH
    assert [shell.nav_button(k).displayed_text() for k in shell.page_keys()] == [
        "CEL",
        "REC",
        "RUN",
    ]
    # The full label survives as a tooltip, and the count is dropped -- at
    # 44px there is nowhere to put it.
    assert shell.nav_button("cells").toolTip() == "Cells"
    assert shell.nav_button("cells").count_text() == "8"
    assert not shell.nav_button("cells")._count.isVisibleTo(shell)

    shell.set_rail_collapsed(False)
    assert shell.nav_rail.width() == theme.NAV_RAIL_WIDTH
    assert shell.nav_button("cells").displayed_text() == "Cells"


def test_collapsed_codes_sit_in_the_middle_of_the_rail(qtbot) -> None:
    """At 44px the code has to be centred, which means the label owns the row.

    Left as-is, the spacer that right-aligns the count when expanded takes
    half the width and the code drifts left of centre.
    """

    shell = _shell_with_pages(qtbot)
    shell.auto_collapse_rail = False
    shell.show()
    qtbot.wait(10)
    shell.set_rail_collapsed(True)
    qtbot.wait(10)

    label = shell.nav_button("cells")._label
    assert label.alignment() & Qt.AlignHCenter
    usable = theme.NAV_RAIL_COLLAPSED_WIDTH - theme.SELECTED_BAR_WIDTH
    assert label.width() >= usable - 1, (label.width(), usable)


def test_the_code_defaults_to_the_first_three_letters(qtbot) -> None:
    shell = _shell(qtbot)
    shell.add_page("project", "Project", QWidget())
    assert shell.nav_button("project").code == "PRO"


def test_the_rail_collapses_on_its_own_below_the_threshold(qtbot) -> None:
    """Artboard 1j: the labels give way first, at <1200px."""

    shell = _shell_with_pages(qtbot)
    shell.resize(1280, 800)
    shell.show()
    qtbot.wait(10)
    assert not shell.is_rail_collapsed()

    shell.resize(theme.NAV_RAIL_COLLAPSE_BELOW - 1, 700)
    qtbot.wait(10)
    assert shell.is_rail_collapsed()

    shell.resize(1280, 800)
    qtbot.wait(10)
    assert not shell.is_rail_collapsed()


def test_auto_collapse_can_be_switched_off(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    shell.auto_collapse_rail = False
    shell.show()
    qtbot.wait(10)

    shell.resize(800, 600)
    qtbot.wait(10)

    assert not shell.is_rail_collapsed()


def test_selection_is_a_stylesheet_property_not_an_inline_colour(qtbot) -> None:
    """The 3px accent bar comes from one QSS rule, so the rail restyles once."""

    shell = _shell_with_pages(qtbot)
    button = shell.nav_button("cells")

    assert button.property("selected") is True
    assert shell.nav_button("runs").property("selected") is False
    assert button.styleSheet() == ""

    rule = f'QFrame#{theme.OBJ_NAV_ITEM}[selected="true"]'
    qss = theme.build_qss()
    assert rule in qss
    assert f"border-left: {theme.SELECTED_BAR_WIDTH}px solid {theme.ACCENT}" in qss


# ---- the health badge ----------------------------------------------------


def test_badge_state_without_a_report_is_neither_pass_nor_fail() -> None:
    state = health_badge_state(None)

    assert state.glyph == theme.STATUS_GLYPH["pending"]
    assert state.color == theme.TEXT_DISABLED
    assert state.count == ""
    assert state.color not in (theme.STATUS_PASSED, theme.STATUS_FAILED)


def test_badge_state_counts_blocking_checks() -> None:
    report = _report(
        _result("a", CheckStatus.OK),
        _result("b", CheckStatus.OK),
        _result("c", CheckStatus.FAIL),
    )

    state = health_badge_state(report)

    assert state.glyph == theme.STATUS_GLYPH["failed"]
    assert state.color == theme.STATUS_FAILED
    assert state.count == "1"
    assert "3" in state.tooltip


def test_badge_state_for_warnings_still_reads_runnable() -> None:
    """Warnings do not stop a run, so the glyph stays the passed one."""

    report = _report(
        _result("a", CheckStatus.OK),
        _result("b", CheckStatus.FAIL, required=False),
    )
    assert report.can_run

    state = health_badge_state(report)

    assert state.glyph == theme.STATUS_GLYPH["passed"]
    assert state.color == theme.STATUS_WARNING
    assert state.count == "1"


def test_badge_state_all_clear_shows_no_number() -> None:
    report = _report(_result("a", CheckStatus.OK), _result("b", CheckStatus.OK))

    state = health_badge_state(report)

    assert state.glyph == theme.STATUS_GLYPH["passed"]
    assert state.color == theme.STATUS_PASSED
    assert state.count == ""


def test_badge_never_paints_itself_with_the_accent() -> None:
    """The badge is the one status indicator sitting on chrome; guard it."""

    reports = [
        None,
        _report(_result("a", CheckStatus.OK)),
        _report(_result("a", CheckStatus.FAIL)),
        _report(_result("a", CheckStatus.WARN, required=False)),
    ]
    for report in reports:
        assert health_badge_state(report).color not in theme.accent_colors()


def test_shell_renders_the_report_on_the_badge(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    assert shell.health_badge_text() == f"{theme.STATUS_GLYPH['pending']} Setup"

    report = _report(_result("a", CheckStatus.OK), _result("b", CheckStatus.FAIL))
    shell.set_health_report(report)

    assert shell.health_report() is report
    assert shell.health_badge_text() == f"{theme.STATUS_GLYPH['failed']} Setup 1"


def test_badge_is_keyboard_reachable(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    badge = shell.health_badge

    assert isinstance(badge, HealthBadge)
    assert badge.focusPolicy() == Qt.StrongFocus

    with qtbot.waitSignal(badge.clicked, timeout=500):
        qtbot.keyClick(badge, Qt.Key_Space)


# ---- the Setup drawer ----------------------------------------------------


def test_drawer_starts_closed(qtbot) -> None:
    shell = _shell_with_pages(qtbot)

    assert not shell.is_setup_open()
    assert shell.setup_drawer.isHidden()
    # Closed, it costs the layout nothing.
    assert shell.minimumSizeHint().width() < theme.SETUP_DRAWER_WIDTH


def test_clicking_the_badge_opens_the_drawer(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    toggles: list[bool] = []
    clicks: list[int] = []
    shell.setup_toggled.connect(toggles.append)
    shell.health_badge_clicked.connect(lambda: clicks.append(1))

    qtbot.mouseClick(shell.health_badge, Qt.LeftButton)

    assert clicks == [1]
    assert toggles == [True]
    assert shell.is_setup_open()
    assert shell.health_badge.property("open") is True

    qtbot.mouseClick(shell.health_badge, Qt.LeftButton)

    assert toggles == [True, False]
    assert not shell.is_setup_open()
    assert shell.health_badge.property("open") is False


def test_setting_the_same_drawer_state_twice_emits_once(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    toggles: list[bool] = []
    shell.setup_toggled.connect(toggles.append)

    shell.set_setup_open(True)
    shell.set_setup_open(True)
    assert toggles == [True]


def test_drawer_takes_a_widget_and_gives_the_previous_one_back(qtbot) -> None:
    """The Setup screen mounts here; the shell only holds the slot."""

    shell = _shell_with_pages(qtbot)
    container = shell.setup_container
    assert container.layout() is not None
    assert container.layout().count() == 0

    first = QLabel("setup v1")
    shell.set_setup_widget(first)
    assert shell.setup_widget() is first
    assert first.parent() is container
    assert container.layout().count() == 1

    second = QLabel("setup v2")
    shell.set_setup_widget(second)
    assert shell.setup_widget() is second
    assert first.parent() is None, "the previous occupant is handed back, not leaked"
    assert container.layout().count() == 1

    shell.set_setup_widget(None)
    assert shell.setup_widget() is None
    assert second.parent() is None


def test_drawer_opens_at_the_design_width(qtbot) -> None:
    shell = _shell_with_pages(qtbot)
    shell.set_setup_open(True)
    assert shell.setup_drawer.width() == theme.SETUP_DRAWER_WIDTH


# ---- title bar and status bar text --------------------------------------


def test_title_bar_and_status_bar_text_round_trips(qtbot) -> None:
    shell = _shell(qtbot)

    assert shell.app_name() == "Auto_ext"
    shell.set_config_path("/home/rfv/wa/Auto_ext_pro/config")
    assert shell.config_path() == "/home/rfv/wa/Auto_ext_pro/config"
    shell.set_config_path(None)
    assert shell.config_path() == ""

    shell.set_status(left="idle", right="tasks.yaml saved")
    assert (shell.status_left(), shell.status_right()) == ("idle", "tasks.yaml saved")

    # One half at a time -- the other keeps whatever it had.
    shell.set_status(left="running")
    assert (shell.status_left(), shell.status_right()) == ("running", "tasks.yaml saved")
