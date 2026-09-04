"""Tests for :mod:`auto_ext.ui.screens.setup_drawer`.

The drawer's contract is narrow: render a
:class:`~auto_ext.model.pdk.PdkHealthReport` faithfully, put the fix next to
the check it fixes, and ask the host for anything that would change the
world. The grouping and summary helpers are pure and tested without Qt; the
widget tests check the two things that would silently mislead the user -- a
verdict the drawer invented, and a button that looks live but writes nowhere.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.health import check_profile, default_checks  # noqa: E402
from auto_ext.model.pdk import (  # noqa: E402
    CheckStatus,
    PdkCheckResult,
    PdkHealthReport,
    PdkProfile,
)
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.screens import setup_drawer as sd  # noqa: E402
from auto_ext.ui.screens.setup_drawer import SetupDrawer  # noqa: E402

#: A drawer is a 520px panel; it must never dictate the window's minimum.
MIN_WIDTH_BUDGET = 700
MIN_HEIGHT_BUDGET = 400

NOW = datetime(2026, 8, 20, 17, 42, tzinfo=timezone.utc)


def _result(
    check_id: str,
    status: CheckStatus = CheckStatus.OK,
    *,
    title: str = "",
    observed: str | None = None,
    message: str | None = None,
    fix_hint: str | None = None,
    required: bool = True,
) -> PdkCheckResult:
    return PdkCheckResult(
        check_id=check_id,
        status=status,
        required=required,
        title=title or check_id,
        observed=observed,
        message=message,
        fix_hint=fix_hint if status is not CheckStatus.OK else None,
        checked_at=NOW,
    )


def _report(*results: PdkCheckResult) -> PdkHealthReport:
    return PdkHealthReport(profile_id="cfxxx", checked_at=NOW, results=list(results))


@pytest.fixture
def mixed_report() -> PdkHealthReport:
    """Canvas 1h: mostly green, one blocking env var, one optional warning."""

    return _report(
        _result(
            "env.work_root",
            title="Environment variable WORK_ROOT",
            observed="/work/wa",
            message="from shell",
        ),
        _result(
            "env.pdk_layer_map_file",
            CheckStatus.FAIL,
            title="Environment variable PDK_LAYER_MAP_FILE",
            observed="source=missing",
            message="PDK_LAYER_MAP_FILE is not set",
            fix_hint="Source /proj/pdk/CFXXX/setup/cshrc.CFXXX, then re-check.",
        ),
        _result(
            "lvs.deck_dir",
            title="Calibre LVS deck directory",
            observed="/proj/pdk/CFXXX/verify/LVS/CFXXX",
        ),
        _result("tool.si", title="si binary (si)", observed="/tools/bin/si"),
        _result(
            "tool.jivaro",
            CheckStatus.WARN,
            required=False,
            title="jivaro binary (jivaro)",
            observed="(not on PATH)",
            message="jivaro was not found on PATH",
            fix_hint="Load the module that provides jivaro, or leave reduction off.",
        ),
    )


# ============================================================================
# pure helpers
# ============================================================================


@pytest.mark.parametrize(
    ("check_id", "group"),
    [
        ("env.work_root", "env"),
        ("pdk.layer_map", "paths"),
        ("lvs.deck_dir", "paths"),
        ("qrc.deck_dir", "paths"),
        ("tool.calibre", "tools"),
        ("something.else", sd.GROUP_OTHER),
        ("nodots", sd.GROUP_OTHER),
    ],
)
def test_group_for_reads_the_check_id_prefix(check_id: str, group: str) -> None:
    assert sd.group_for(check_id) == group


def test_every_default_check_lands_in_a_named_group() -> None:
    """No real check may fall through to "Other" unnoticed."""

    profile = PdkProfile(profile_id="cfxxx", display_name="CFXXX")
    groups = {sd.group_for(c.check_id) for c in default_checks(profile)}
    assert groups <= {"env", "paths", "tools"}


def test_group_results_keeps_canvas_order_and_drops_empty_sections(
    mixed_report: PdkHealthReport,
) -> None:
    groups = sd.group_results(mixed_report)
    assert [g.key for g in groups] == ["env", "paths", "tools"]
    assert [g.title for g in groups] == [
        "Shell environment",
        "Paths and PDK files",
        "Tools on PATH",
    ]
    assert [r.check_id for r in groups[0].results] == [
        "env.work_root",
        "env.pdk_layer_map_file",
    ]
    assert sd.group_results(None) == []


def test_summary_counts_blocking_and_warning_apart(
    mixed_report: PdkHealthReport,
) -> None:
    assert sd.summary_text(mixed_report) == "5 checks - 3 ok - 1 warning - 1 failing"
    assert sd.summary_text(None) == "not checked yet"
    assert "no checks" in sd.summary_text(_report())


def test_summary_of_an_all_clear_report_says_nothing_about_failures() -> None:
    text = sd.summary_text(_report(_result("tool.si"), _result("tool.calibre")))
    assert text == "2 checks - 2 ok"


@pytest.mark.parametrize(
    ("status", "color"),
    [
        (CheckStatus.OK, theme.STATUS_PASSED),
        (CheckStatus.FAIL, theme.STATUS_FAILED),
        (CheckStatus.WARN, theme.STATUS_WARNING),
        (CheckStatus.UNKNOWN, theme.TEXT_DISABLED),
    ],
)
def test_status_glyph_uses_whitelisted_glyphs_and_no_accent(
    status: CheckStatus, color: str
) -> None:
    glyph, got = sd.status_glyph(status)
    assert got == color
    assert got not in theme.accent_colors()
    assert glyph in {"✓", "✗", "⇆", "·"}


def test_row_label_shortens_the_declared_title_to_the_thing_checked() -> None:
    assert (
        sd.row_label(_result("env.pdk_layer_map_file", title="Environment variable PDK_LAYER_MAP_FILE"))
        == "PDK_LAYER_MAP_FILE"
    )
    assert sd.row_label(_result("tool.calibre", title="calibre binary (calibre)")) == "calibre"
    assert (
        sd.row_label(_result("lvs.deck_dir", title="Calibre LVS deck directory"))
        == "Calibre LVS deck directory"
    )


# ============================================================================
# the widget
# ============================================================================


def test_setup_drawer_min_size_stays_within_budget(qtbot, mixed_report) -> None:
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    empty = drawer.minimumSizeHint()
    assert empty.width() <= MIN_WIDTH_BUDGET
    assert empty.height() <= MIN_HEIGHT_BUDGET

    drawer.set_report(mixed_report)
    full = drawer.minimumSizeHint()
    assert full.width() <= MIN_WIDTH_BUDGET
    assert full.height() <= MIN_HEIGHT_BUDGET


def test_setup_drawer_min_width_survives_an_absurd_observed_value(
    qtbot,
) -> None:
    """An observed path is unbounded; it may not widen the window."""

    long_path = "/" + "/".join(f"segment_{i}" for i in range(60))
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(
        _report(_result("lvs.deck_dir", title="deck", observed=long_path))
    )
    assert drawer.minimumSizeHint().width() <= MIN_WIDTH_BUDGET


def test_setup_drawer_starts_unchecked_and_says_so(qtbot) -> None:
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    assert drawer.report() is None
    assert drawer.summary() == "not checked yet"
    assert drawer.row_ids() == []


def test_setup_drawer_renders_one_row_per_check_in_report_order(
    qtbot, mixed_report
) -> None:
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    assert drawer.row_ids() == [
        "env.work_root",
        "env.pdk_layer_map_file",
        "lvs.deck_dir",
        "tool.si",
        "tool.jivaro",
    ]
    assert drawer.summary() == "5 checks - 3 ok - 1 warning - 1 failing"


def test_setup_drawer_writes_the_fix_next_to_the_failing_check(
    qtbot, mixed_report
) -> None:
    """Canvas 1h's whole point: no going and looking the answer up."""

    from PyQt5.QtWidgets import QLabel

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)

    row = drawer.row_widget("env.pdk_layer_map_file")
    assert row is not None
    texts = [child.text() for child in row.findChildren(QLabel)]
    assert any("PDK_LAYER_MAP_FILE" in t for t in texts)
    assert any("PDK_LAYER_MAP_FILE is not set" in t for t in texts)
    assert any("cshrc.CFXXX" in t for t in texts), "the fix hint must be on the row"


def test_setup_drawer_never_invents_a_fix_hint(qtbot) -> None:
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(
        _report(_result("qrc.deck_dir", CheckStatus.FAIL, title="deck", message="gone"))
    )
    from PyQt5.QtWidgets import QLabel

    row = drawer.row_widget("qrc.deck_dir")
    assert row is not None
    texts = [child.text() for child in row.findChildren(QLabel)]
    assert any("No fix hint was recorded" in t for t in texts)


def test_setup_drawer_copy_the_fix_puts_the_hint_on_the_clipboard(
    qtbot, mixed_report
) -> None:
    from PyQt5.QtWidgets import QPushButton

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    row = drawer.row_widget("env.pdk_layer_map_file")
    assert row is not None
    button = next(
        b for b in row.findChildren(QPushButton) if b.text() == "Copy the fix"
    )
    with qtbot.waitSignal(drawer.copy_requested, timeout=1000) as blocker:
        button.click()
    assert "cshrc.CFXXX" in blocker.args[0]


def test_setup_drawer_pin_row_is_dead_until_a_host_connects(
    qtbot, mixed_report
) -> None:
    """A button that silently writes nowhere is worse than a disabled one."""

    from PyQt5.QtWidgets import QLineEdit, QPushButton

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    assert drawer.can_pin_overrides() is False

    row = drawer.row_widget("env.pdk_layer_map_file")
    assert row is not None
    pin = next(b for b in row.findChildren(QPushButton) if b.text() == "Set")
    assert not pin.isEnabled()
    assert "override_requested" in pin.toolTip()

    drawer.override_requested.connect(lambda *_a: None)
    drawer.set_report(mixed_report)
    row = drawer.row_widget("env.pdk_layer_map_file")
    assert row is not None
    pin = next(b for b in row.findChildren(QPushButton) if b.text() == "Set")
    edit = next(iter(row.findChildren(QLineEdit)))
    assert edit.isEnabled(), "with a host connected the row takes a value"
    edit.setText("  /abs/path/to/layers.map  ")
    assert pin.isEnabled()
    with qtbot.waitSignal(drawer.override_requested, timeout=1000) as blocker:
        pin.click()
    assert blocker.args == ["PDK_LAYER_MAP_FILE", "/abs/path/to/layers.map"]


def _pin_widgets(drawer: SetupDrawer, check_id: str):
    """``(line edit, Set button)`` of one check's pin row, as rendered."""

    from PyQt5.QtWidgets import QLineEdit, QPushButton

    row = drawer.row_widget(check_id)
    assert row is not None, check_id
    edit = next(iter(row.findChildren(QLineEdit)))
    pin = next(b for b in row.findChildren(QPushButton) if b.text() == "Set")
    return edit, pin


def test_setup_drawer_keeps_what_was_typed_across_a_recheck(
    qtbot, mixed_report
) -> None:
    """"在 Setup 里填了路径，按 Re-check 想看看好没好，框又空了."

    Re-check is the natural next move after typing a path, and it arrives as
    a whole new report -- as does every load, project switch and save. The
    drawer rebuilds its body for each of them, so the box has to be refilled
    from what the user typed rather than from nothing.
    """

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.override_requested.connect(lambda *_a: None)
    drawer.set_report(mixed_report)

    edit, _pin = _pin_widgets(drawer, "env.pdk_layer_map_file")
    qtbot.keyClicks(edit, "/pdk/layers.map")

    # what the host does when Re-check comes back
    drawer.set_report(mixed_report)

    edit, _pin = _pin_widgets(drawer, "env.pdk_layer_map_file")
    assert edit.text() == "/pdk/layers.map"


def test_setup_drawer_forgets_the_typed_pins_when_the_profile_changes(
    qtbot, mixed_report
) -> None:
    """A path typed for one PDK is not an answer for the next one."""

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.override_requested.connect(lambda *_a: None)
    drawer.set_report(mixed_report)
    edit, _pin = _pin_widgets(drawer, "env.pdk_layer_map_file")
    qtbot.keyClicks(edit, "/pdk/layers.map")

    other = mixed_report.model_copy(update={"profile_id": "hn001"})
    drawer.set_report(other)

    edit, _pin = _pin_widgets(drawer, "env.pdk_layer_map_file")
    assert edit.text() == ""


def test_setup_drawer_set_is_dead_while_the_box_is_empty(
    qtbot, mixed_report
) -> None:
    """"按了 Set 什么都没发生，也没说框是空的."

    The button was enabled from the state the row is *born* in -- a host is
    connected and the check names a variable -- and the one thing it actually
    needs, a value, was checked only inside the click handler, which returned
    without a word.
    """

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.override_requested.connect(lambda *_a: None)
    drawer.set_report(mixed_report)

    edit, pin = _pin_widgets(drawer, "env.pdk_layer_map_file")
    assert edit.text() == ""
    assert pin.isEnabled() is False
    assert "value" in pin.toolTip().lower()

    qtbot.keyClicks(edit, "/pdk/layers.map")
    assert pin.isEnabled() is True

    # whitespace is not a value either
    edit.clear()
    qtbot.keyClicks(edit, "   ")
    assert pin.isEnabled() is False


def test_setup_drawer_offers_the_pin_row_for_env_checks_only(
    qtbot, mixed_report
) -> None:
    from PyQt5.QtWidgets import QLineEdit

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    env_row = drawer.row_widget("env.pdk_layer_map_file")
    tool_row = drawer.row_widget("tool.jivaro")
    assert env_row is not None and tool_row is not None
    assert env_row.findChildren(QLineEdit)
    assert not tool_row.findChildren(QLineEdit)


def test_setup_drawer_header_buttons_are_requests_not_actions(
    qtbot, mixed_report
) -> None:
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    with qtbot.waitSignal(drawer.recheck_requested, timeout=1000):
        drawer._recheck_btn.click()
    with qtbot.waitSignal(drawer.close_requested, timeout=1000):
        drawer._close_btn.click()


def test_setup_drawer_scroll_to_finds_a_rendered_check(qtbot, mixed_report) -> None:
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    assert drawer.scroll_to("tool.jivaro") is True
    assert drawer.scroll_to("no.such.check") is False


def test_setup_drawer_renders_a_real_report_from_core_health(qtbot) -> None:
    """End to end over the module that actually owns the verdict."""

    profile = PdkProfile(profile_id="cfxxx", display_name="CFXXX")
    report = check_profile(profile, which=lambda _name: None)
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(report)

    assert drawer.row_ids() == [r.check_id for r in report.results]
    assert drawer.summary() == sd.summary_text(report)
    # Nothing on PATH, so at least one row must be blocking and carry a fix.
    assert report.blocking
    blocking = report.blocking[0]
    assert drawer.row_widget(blocking.check_id) is not None
    assert blocking.fix_hint


# ============================================================================
# design constraints
# ============================================================================

#: Every non-ASCII character the design allows. The ten from the project
#: glyph whitelist, plus the horizontal ellipsis, which is
#: ``theme.STATUS_GLYPH["dry_run"]`` and also the character Qt itself inserts
#: when a label elides.
ALLOWED_GLYPHS = set("\u2713\u2717\u25b6\u25a0\u2013\u00b7\u21c6\u25be\u25b4\u25bc\u2026")


def _widgets(root):
    from PyQt5.QtWidgets import QWidget

    yield root
    for child in root.findChildren(QWidget):
        yield child


def _font_sizes(sheet: str) -> list[int]:
    import re

    return [int(m) for m in re.findall(r"font-size:\s*(\d+)px", sheet)]


def test_no_rendered_text_uses_a_glyph_outside_the_whitelist(
    qtbot, mixed_report
) -> None:
    from PyQt5.QtWidgets import QAbstractButton, QLabel

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    offenders: list[str] = []
    for widget in _widgets(drawer):
        if not isinstance(widget, (QLabel, QAbstractButton)):
            continue
        for char in widget.text():
            if ord(char) > 127 and char not in ALLOWED_GLYPHS:
                offenders.append(f"{type(widget).__name__}: {widget.text()!r}")
                break
    assert offenders == []


def test_no_stylesheet_goes_below_the_11px_floor(qtbot, mixed_report) -> None:
    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    for widget in _widgets(drawer):
        for size in _font_sizes(widget.styleSheet()):
            assert size >= theme.FONT_SIZE_MIN, (
                f"{type(widget).__name__} sets font-size: {size}px"
            )


def test_the_drawer_never_paints_a_verdict_in_the_accent(
    qtbot, mixed_report
) -> None:
    """The accent is selection and primary action; a check status is neither."""

    drawer = SetupDrawer()
    qtbot.addWidget(drawer)
    drawer.set_report(mixed_report)
    accent = theme.accent_colors()
    for status in CheckStatus:
        _glyph, color = sd.status_glyph(status)
        assert color not in accent
