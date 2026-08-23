"""Run bar tests: the two states, the fold, and the colour rules.

The load-bearing cases are the ones that would let a regression through
silently: that the folded stage menu and the stage checkboxes cannot
disagree about what will run, that no status is ever painted in the
accent, and that the bar never becomes the reason the window cannot
shrink.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QLabel, QWidget  # noqa: E402

from auto_ext.core.runner import STAGE_ORDER  # noqa: E402
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.widgets import run_bar as run_bar_mod  # noqa: E402
from auto_ext.ui.widgets.run_bar import (  # noqa: E402
    RUN_BAR_COMPACT_BELOW,
    ElidedLabel,
    RunBar,
    StageChipStrip,
    chip_palette,
)

_HEX = re.compile(r"#[0-9a-fA-F]{6}")


def _bar(qtbot) -> RunBar:
    bar = RunBar()
    qtbot.addWidget(bar)
    return bar


def _theme_colors() -> set[str]:
    """Every colour theme.py declares, in one flat lowercase set."""

    colors: set[str] = set()
    for value in vars(theme).values():
        if isinstance(value, str) and _HEX.fullmatch(value):
            colors.add(value.lower())
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, str) and _HEX.fullmatch(item):
                    colors.add(item.lower())
    return colors


# ---- idle state ----------------------------------------------------------


def test_idle_is_the_default_and_run_is_dead_without_a_selection(qtbot) -> None:
    bar = _bar(qtbot)

    assert bar.is_running() is False
    assert bar.selection_count() == 0
    assert bar.run_button_text() == "Run"
    assert bar.run_button().isEnabled() is False


def test_run_button_counts_what_it_will_run(qtbot) -> None:
    bar = _bar(qtbot)

    bar.set_selection_count(1)
    assert bar.run_button_text() == "Run 1 cell"
    bar.set_selection_count(3)
    assert bar.run_button_text() == "Run 3 cells"
    assert bar.run_button().isEnabled() is True


def test_run_requested_needs_both_a_selection_and_a_stage(qtbot) -> None:
    bar = _bar(qtbot)
    fired: list[int] = []
    bar.run_requested.connect(lambda: fired.append(1))

    bar._on_run_clicked()
    assert fired == [], "no selection must not dispatch"

    bar.set_selection_count(2)
    bar.set_selected_stages([])
    assert bar.run_button().isEnabled() is False
    bar._on_run_clicked()
    assert fired == [], "no stages must not dispatch"

    bar.set_selected_stages(["si"])
    bar._on_run_clicked()
    assert fired == [1]


def test_default_stages_leave_jivaro_off(qtbot) -> None:
    """Artboard 1a: the reduction stage is opt-in, the other four are not."""

    bar = _bar(qtbot)

    assert bar.selected_stages() == ("si", "strmout", "calibre", "quantus")


def test_jobs_and_flags_round_trip(qtbot) -> None:
    bar = _bar(qtbot)
    jobs: list[int] = []
    bar.jobs_changed.connect(jobs.append)

    bar.set_jobs(4)
    bar.set_dry_run(True)
    bar.set_continue_on_lvs_fail(True)

    assert bar.jobs() == 4
    assert jobs == [4]
    assert bar.is_dry_run() is True
    assert bar.continue_on_lvs_fail() is True


# ---- the fold (artboard 1j, concession 4) --------------------------------


def test_compact_swaps_the_stage_row_for_a_menu_button(qtbot) -> None:
    bar = _bar(qtbot)
    bar.show()

    bar.set_compact(True)

    assert bar.is_compact() is True
    assert bar.stages_button().isVisible() is True
    assert bar.stage_check("si").isVisible() is False
    assert bar.stages_button().text() == "stages 4/5 ▾"


def test_compact_shortens_the_selection_wording(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_selection_count(2)

    assert "2 cells selected" in bar._selection_label.text()
    bar.set_compact(True)
    assert bar._selection_label.text() == "2 selected"


def _hosted_bar(qtbot) -> tuple[QWidget, RunBar]:
    """A bar inside a host, which is how it is really used.

    A top-level widget cannot be resized below its own layout minimum, so
    driving the fold means driving the *host* -- exactly what the splitter
    in the Cells screen does.
    """

    from PyQt5.QtWidgets import QVBoxLayout

    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    bar = RunBar()
    layout.addWidget(bar)
    host.show()
    return host, bar


def test_resize_folds_the_bar_by_itself(qtbot) -> None:
    host, bar = _hosted_bar(qtbot)

    host.resize(RUN_BAR_COMPACT_BELOW + 200, 90)
    qtbot.wait(10)
    assert bar.is_compact() is False

    host.resize(RUN_BAR_COMPACT_BELOW - 300, 90)
    qtbot.wait(10)
    assert bar.is_compact() is True


def test_auto_compact_can_be_taken_over(qtbot) -> None:
    host, bar = _hosted_bar(qtbot)
    bar.auto_compact = False

    host.resize(RUN_BAR_COMPACT_BELOW - 300, 90)
    qtbot.wait(10)

    assert bar.is_compact() is False


def test_folded_menu_is_rebuilt_from_the_checkboxes_every_time(qtbot) -> None:
    """The two forms of the bar share one answer, so they cannot drift."""

    bar = _bar(qtbot)
    bar.set_compact(True)
    bar.set_selected_stages(["si", "calibre"])

    bar._rebuild_stages_menu()
    checked = {a.text() for a in bar.stages_menu().actions() if a.isChecked()}

    assert checked == {"si", "calibre"}


def test_toggling_a_menu_action_writes_back_to_the_stage_set(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_selected_stages(["si"])
    bar._rebuild_stages_menu()

    action = next(a for a in bar.stages_menu().actions() if a.text() == "jivaro")
    action.setChecked(True)

    assert "jivaro" in bar.selected_stages()
    assert bar.stage_check("jivaro").isChecked() is True


def test_menu_carries_dry_run_and_continue_on_lvs_fail(qtbot) -> None:
    """Concession 4: the two flags ride along inside the folded menu."""

    bar = _bar(qtbot)
    bar._rebuild_stages_menu()

    texts = [a.text() for a in bar.stages_menu().actions() if a.text()]

    assert texts[len(STAGE_ORDER) :] == ["dry run", "continue on LVS fail"]


# ---- recipe override -----------------------------------------------------


def test_per_row_is_the_first_choice_and_means_no_override(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_recipe_choices([("rc-typ", "RC typical 55C"), ("rc-worst", "RCworst 85C")])
    bar.set_per_row_summary(2)

    assert bar.recipe_combo().itemText(0) == "per row (2 recipes)"
    assert bar.recipe_override() is None


def test_choosing_a_recipe_overrides_only_this_run(qtbot) -> None:
    bar = _bar(qtbot)
    seen: list[object] = []
    bar.recipe_override_changed.connect(seen.append)
    bar.set_recipe_choices([("rc-typ", "RC typical 55C")])

    bar.set_recipe_override("rc-typ")

    assert bar.recipe_override() == "rc-typ"
    assert seen == ["rc-typ"]
    assert "row recipes are untouched" in bar._hint.full_text()


def test_recipe_choices_keep_the_current_override(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_recipe_choices([("rc-typ", "RC typical 55C")])
    bar.set_recipe_override("rc-typ")

    bar.set_recipe_choices([("rc-typ", "RC typical 55C"), ("rc-worst", "RCworst 85C")])

    assert bar.recipe_override() == "rc-typ"


def test_per_row_summary_reads_singular_for_one_recipe(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_recipe_choices([("rc-typ", "RC typical 55C")])
    bar.set_per_row_summary(1)

    assert bar.recipe_combo().itemText(0) == "per row (1 recipe)"


# ---- running panel -------------------------------------------------------


def test_running_swaps_the_bar_for_the_panel(qtbot) -> None:
    bar = _bar(qtbot)
    bar.show()

    bar.set_running(True)

    assert bar.is_running() is True
    assert bar._idle.isVisible() is False
    assert bar._panel.isVisible() is True


def test_counts_replace_the_progress_bar(qtbot) -> None:
    """No progress bar, no ETA: what finished is the whole report."""

    bar = _bar(qtbot)
    bar.set_counts(passed=3, failed=1, running=1, queued=2)

    assert bar.counts_text() == "3 passed · 1 failed · 1 running · 2 queued"


def test_no_progress_bar_anywhere_in_the_widget_tree(qtbot) -> None:
    from PyQt5.QtWidgets import QProgressBar

    bar = _bar(qtbot)
    bar.set_running(True)

    assert bar.findChildren(QProgressBar) == []


def test_cancel_reports_once_and_then_says_so(qtbot) -> None:
    bar = _bar(qtbot)
    fired: list[int] = []
    bar.cancel_requested.connect(lambda: fired.append(1))
    bar.set_running(True)

    bar.cancel_button().click()
    bar.mark_cancelling()

    assert fired == [1]
    assert bar.cancel_button().isEnabled() is False
    assert bar.cancel_button().text() == "cancelling…"


def test_collapse_hides_the_log_half_and_says_which_way_it_goes(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_log_widget(QLabel("log"))
    bar.set_running(True)
    bar.show()
    assert bar.log_container.isVisible() is True
    states: list[bool] = []
    bar.collapse_toggled.connect(states.append)

    bar.collapse_button().click()

    assert states == [True]
    assert bar.is_collapsed() is True
    assert bar.log_container.isVisible() is False
    assert bar.collapse_button().text() == "Expand"

    bar.collapse_button().click()
    assert states == [True, False]
    assert bar.collapse_button().text() == "Collapse"


def test_run_label_is_printed_not_derived(qtbot) -> None:
    bar = _bar(qtbot)

    bar.set_run_label("Run 138")

    assert bar.run_label() == "Run 138"


# ---- log slot ------------------------------------------------------------


def test_open_in_editor_is_dead_until_there_is_a_path(qtbot) -> None:
    bar = _bar(qtbot)
    seen: list[object] = []
    bar.open_log_requested.connect(seen.append)

    assert bar._open_log_button.isEnabled() is False

    path = Path("/runs/20260820T174200Z_dco/logs/quantus.log")
    bar.set_log_path(path)
    assert bar._open_log_button.isEnabled() is True
    bar._open_log_button.click()

    assert seen == [path]
    assert bar.log_path() == path


def test_follow_current_stage_is_on_by_default_and_announces_changes(qtbot) -> None:
    bar = _bar(qtbot)
    seen: list[bool] = []
    bar.follow_changed.connect(seen.append)

    assert bar.follows_current_stage() is True
    bar.set_follows_current_stage(False)

    assert seen == [False]


def test_the_log_slot_stays_out_of_the_way_until_something_is_mounted(qtbot) -> None:
    """An empty dark box is not a log pane; the panel is its two strips."""

    bar = _bar(qtbot)
    bar.set_running(True)
    bar.show()

    assert bar.log_container.isVisible() is False

    bar.set_log_widget(QLabel("log"))
    assert bar.log_container.isVisible() is True


def test_log_widget_is_mounted_and_replaced(qtbot) -> None:
    bar = _bar(qtbot)
    first = QLabel("first")
    second = QWidget()

    assert bar.set_log_widget(first) is None
    assert bar.log_widget() is first
    assert first.parent() is bar.log_container

    assert bar.set_log_widget(second) is first
    assert bar.log_widget() is second


# ---- stage chips ---------------------------------------------------------


def test_chip_strip_spells_out_every_stage(qtbot) -> None:
    strip = StageChipStrip()
    qtbot.addWidget(strip)

    strip.set_statuses({"si": "passed", "strmout": "passed", "calibre": "failed"})

    assert strip.chip_texts() == [
        "si ✓",
        "strmout ✓",
        "calibre ✗",
        "quantus –",
        "jivaro –",
    ]


def test_chip_strip_collapses_to_a_placeholder_before_a_task_starts(qtbot) -> None:
    strip = StageChipStrip()
    qtbot.addWidget(strip)

    strip.set_placeholder("queued")
    assert strip.chip_texts() == ["queued"]

    strip.set_status("si", "running")
    assert strip.placeholder() is None
    assert strip.chip_texts()[0] == "si ▶"


def test_chip_strip_follows_the_stage_set_it_is_given(qtbot) -> None:
    strip = StageChipStrip(["si", "calibre"])
    qtbot.addWidget(strip)

    assert strip.chip_texts() == ["si –", "calibre –"]

    strip.set_stages(["si"])
    assert strip.chip_texts() == ["si –"]


def test_chip_strip_never_demands_width(qtbot) -> None:
    strip = StageChipStrip()
    qtbot.addWidget(strip)

    assert strip.minimumSizeHint().width() == 0


def test_chip_colours_never_borrow_the_accent(qtbot) -> None:
    """The accent means selection. An outcome has its own scale."""

    accents = theme.accent_colors()
    for status in ("passed", "failed", "running", "cancelled", "skipped", "", "dry_run"):
        assert not (set(chip_palette(status)) & accents), status


def test_running_chip_uses_the_darkened_running_hue(qtbot) -> None:
    fill, border, text = chip_palette("running")

    assert fill == theme.STATUS_RUNNING == "#0f6fd1"
    assert border == theme.STATUS_RUNNING
    assert text == theme.ACCENT_ON


def test_unknown_chip_status_is_neutral_not_red(qtbot) -> None:
    fill, _border, text = chip_palette("something_new")

    assert fill is None
    assert text == theme.TEXT_DISABLED


# ---- elided label --------------------------------------------------------


def test_elided_label_costs_no_width(qtbot) -> None:
    label = ElidedLabel("x" * 400)
    qtbot.addWidget(label)

    assert label.minimumSizeHint().width() == 0


def test_elided_label_keeps_the_whole_string_for_the_tooltip(qtbot) -> None:
    label = ElidedLabel()
    qtbot.addWidget(label)
    label.resize(40, 20)

    label.set_full_text("/work/wa/Auto_ext_pro/logs/task/quantus.log")

    assert label.full_text().endswith("quantus.log")
    assert label.toolTip() == label.full_text()
    assert len(label.text()) < len(label.full_text())


# ---- sizing and style rules ---------------------------------------------


def test_bar_never_blocks_the_window_floor(qtbot) -> None:
    bar = _bar(qtbot)
    bar.set_selection_count(12)
    bar.set_recipe_choices([("rc", "A recipe with a fairly long display name")])

    assert bar.minimumSizeHint().width() <= 320


def test_stylesheet_uses_only_declared_theme_colours() -> None:
    declared = _theme_colors()

    used = {match.lower() for match in _HEX.findall(run_bar_mod._RUN_BAR_QSS)}

    assert used <= declared, sorted(used - declared)


def test_stylesheet_has_no_gradient_shadow_or_animation() -> None:
    qss = run_bar_mod._RUN_BAR_QSS.lower()

    for banned in ("gradient", "box-shadow", "animation", "transition"):
        assert banned not in qss


def test_visible_glyphs_stay_inside_the_dejavu_vocabulary(qtbot) -> None:
    """Every non-ASCII character the bar can show must be a blessed glyph."""

    allowed = set(theme.STATUS_GLYPH.values()) | set("▾▴▼⇆·–—…")
    bar = _bar(qtbot)
    bar.set_running(True)
    bar.set_counts(passed=1)
    bar.mark_cancelling()

    from PyQt5.QtWidgets import QAbstractButton

    texts = [w.text() for w in bar.findChildren(QLabel)]
    texts += [w.text() for w in bar.findChildren(QAbstractButton)]
    texts.append(bar.stages_button().text())

    for text in texts:
        for char in text:
            assert char.isascii() or char in allowed, (char, text)
