"""Tests for :mod:`auto_ext.ui.widgets.failure_chip`.

Two halves: the code/ordering tables, which are pure, and the three leaf
widgets. The ordering assertions are the important ones -- "who has to act"
is the whole point of the taxonomy, and getting it backwards would send the
user to re-check a layout when the real problem was a license queue.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QPoint, Qt  # noqa: E402
from PyQt5.QtGui import QMouseEvent  # noqa: E402

from auto_ext.core.failure_class import ASSIGNABLE_CLASSES, FailureClass  # noqa: E402
from auto_ext.ui import theme  # noqa: E402
from auto_ext.ui.widgets import failure_chip as fc  # noqa: E402
from auto_ext.ui.widgets.failure_chip import (  # noqa: E402
    Chip,
    FailureChip,
    PathLabel,
)


# ============================================================================
# codes and ordering
# ============================================================================


def test_every_failure_class_has_a_three_letter_code() -> None:
    for member in FailureClass:
        code = fc.code_for(member)
        assert len(code) == 3, member
        assert code.isupper()


def test_cancelled_is_a_code_too_and_is_not_a_failure_class() -> None:
    """A cancellation is not a diagnosis, but it still needs a chip."""

    assert fc.code_for(fc.CANCELLED_KEY) == fc.CODE_CANCELLED
    assert fc.CANCELLED_KEY not in {m.value for m in FailureClass}


def test_unknown_is_never_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    """``failure_signatures.yaml`` is empty today, so this is the common case."""

    assert fc.code_for(FailureClass.UNKNOWN) == fc.CODE_UNKNOWN
    assert fc.code_for("something_a_future_version_invented") == fc.CODE_UNKNOWN
    assert fc.code_legend(fc.CODE_UNKNOWN).strip()
    style = fc.code_style(fc.CODE_UNKNOWN)
    # Grey, not red: the classifier declined to blame the design.
    assert style.text == theme.TEXT_SECONDARY
    assert theme.STATUS_FAILED not in style


def test_who_has_to_act_ordering() -> None:
    """Environment first, design second, unclassified last (canvas 1e)."""

    codes = [
        fc.CODE_CRASH,
        fc.CODE_UNKNOWN,
        fc.CODE_LICENSE,
        fc.CODE_LVS,
        fc.CODE_CONFIG,
        fc.CODE_CANCELLED,
    ]
    assert sorted(codes, key=fc.sort_key) == [
        fc.CODE_LICENSE,
        fc.CODE_CONFIG,
        fc.CODE_CANCELLED,
        fc.CODE_LVS,
        fc.CODE_CRASH,
        fc.CODE_UNKNOWN,
    ]


def test_actor_groups_partition_every_code() -> None:
    for code in fc.CODE_ORDER:
        assert fc.actor_for(code) in fc.ACTOR_ORDER
        assert fc.ACTOR_TITLES[fc.actor_for(code)].strip()
        assert fc.ACTOR_SUBTITLES[fc.actor_for(code)].strip()


def test_amber_means_environment_and_red_means_design() -> None:
    amber = {theme.STATUS_FILL["warning"]}
    for code in (fc.CODE_LICENSE, fc.CODE_CONFIG, fc.CODE_CANCELLED):
        assert fc.code_style(code).fill in amber
        assert fc.actor_for(code) == fc.ACTOR_ENVIRONMENT
    for code in (fc.CODE_LVS, fc.CODE_CRASH):
        assert theme.STATUS_FAILED in fc.code_style(code)
        assert fc.actor_for(code) == fc.ACTOR_DESIGN


def test_lvs_and_crs_differ_by_weight_as_well_as_by_letters() -> None:
    """A second, redundant discriminator: solid vs outlined (canvas 1e)."""

    assert fc.code_style(fc.CODE_LVS).fill == theme.STATUS_FAILED
    assert fc.code_style(fc.CODE_CRASH).fill == theme.SURFACE_CARD


def test_no_chip_ever_borrows_the_accent() -> None:
    """The accent means selection and primary action, never a verdict."""

    accent = theme.accent_colors()
    for code, style in fc.CODE_STYLE.items():
        # ACCENT_ON (white) is a text colour on a solid fill, not the accent.
        assert style.fill not in accent, code
        assert style.line not in accent, code


def test_the_four_assignable_classes_are_the_four_canvas_codes() -> None:
    assigned = {fc.code_for(cls) for cls in ASSIGNABLE_CLASSES}
    assert assigned == {fc.CODE_LICENSE, fc.CODE_CONFIG, fc.CODE_LVS, fc.CODE_CRASH}


# ============================================================================
# FailureChip
# ============================================================================


def test_failure_chip_shows_the_code_and_explains_itself(qtbot) -> None:
    chip = FailureChip(fc.CODE_LVS)
    qtbot.addWidget(chip)
    assert chip.code == fc.CODE_LVS
    assert chip.text() == "LVS"
    assert "layout vs schematic" in chip.toolTip()


def test_failure_chip_restyles_when_the_code_changes(qtbot) -> None:
    chip = FailureChip(fc.CODE_LICENSE)
    qtbot.addWidget(chip)
    amber = chip.styleSheet()
    chip.set_code(fc.CODE_LVS)
    assert chip.text() == "LVS"
    assert chip.styleSheet() != amber
    assert theme.STATUS_FAILED in chip.styleSheet()


def test_failure_chip_falls_back_rather_than_raising(qtbot) -> None:
    chip = FailureChip("")
    qtbot.addWidget(chip)
    assert chip.code == fc.CODE_UNKNOWN


def test_failure_chip_rail_form_is_the_canvas_width(qtbot) -> None:
    rail = FailureChip(fc.CODE_CRASH, stretch=True)
    inline = FailureChip(fc.CODE_CRASH)
    qtbot.addWidget(rail)
    qtbot.addWidget(inline)
    assert rail.width() == fc.FAILURE_CHIP_WIDTH
    assert inline.minimumWidth() == fc.FAILURE_CHIP_INLINE_WIDTH
    assert rail.minimumSizeHint().width() <= fc.FAILURE_CHIP_WIDTH


# ============================================================================
# Chip
# ============================================================================


def test_chip_tone_changes_the_stylesheet_and_nothing_else(qtbot) -> None:
    chip = Chip("si", fc.CHIP_TONE_PASSED)
    qtbot.addWidget(chip)
    assert chip.tone == fc.CHIP_TONE_PASSED
    assert theme.STATUS_PASSED in chip.styleSheet()
    chip.set_tone(fc.CHIP_TONE_FAILED)
    assert chip.text() == "si"
    assert theme.STATUS_FAILED in chip.styleSheet()


def test_chip_unknown_tone_degrades_to_muted(qtbot) -> None:
    chip = Chip("x", "not-a-tone")
    qtbot.addWidget(chip)
    assert theme.TEXT_DISABLED in chip.styleSheet()


def test_chip_set_chip_text_sets_both(qtbot) -> None:
    chip = Chip()
    qtbot.addWidget(chip)
    chip.set_chip_text("4 / 4 passed", fc.CHIP_TONE_PASSED)
    assert chip.text() == "4 / 4 passed"
    assert chip.tone == fc.CHIP_TONE_PASSED


# ============================================================================
# PathLabel
# ============================================================================

_LONG = "/proj/pdk/CFXXX/verify/QRC/Ver_Plus_1.0a/CFXXX/QCI_deck/preserveCellList.txt"


def test_path_label_never_reports_a_large_minimum_width(qtbot) -> None:
    """The failure mode the 940px window floor exists to prevent."""

    label = PathLabel()
    qtbot.addWidget(label)
    label.set_path(_LONG * 4)
    assert label.minimumSizeHint().width() == fc.PATH_LABEL_MIN_WIDTH


def test_path_label_elides_but_keeps_the_full_text_and_the_tooltip(qtbot) -> None:
    label = PathLabel()
    qtbot.addWidget(label)
    # Resize first: a hidden widget posts its resize event rather than
    # delivering it, so the elide would otherwise not have run yet.
    label.resize(120, 16)
    label.set_path(_LONG)
    assert label.full_text() == _LONG
    assert label.text() != _LONG
    # The tooltip is the resolved Path, so its separators are the host's.
    assert label.toolTip() == str(Path(_LONG))
    # Elided from the left: the informative tail survives.
    assert label.text().endswith("txt")


def test_path_label_size_hint_measures_the_full_text_not_the_elided_one(
    qtbot,
) -> None:
    """Without this a label that once shrank could never grow back."""

    label = PathLabel()
    qtbot.addWidget(label)
    label.resize(400, 16)
    label.set_path(_LONG)
    wide = label.sizeHint().width()
    label.resize(80, 16)
    label._apply_elide()
    assert label.text() != _LONG
    assert label.sizeHint().width() == wide


def test_path_label_click_emits_the_path(qtbot, tmp_path: Path) -> None:
    target = tmp_path / "amp2.lvs.report"
    target.write_text("x", encoding="utf-8")
    label = PathLabel()
    qtbot.addWidget(label)
    label.resize(200, 16)
    label.set_path(target)
    assert label.is_live()
    assert label.path == target
    assert theme.ACCENT in label.styleSheet()

    with qtbot.waitSignal(label.clicked, timeout=1000) as blocker:
        label.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.MouseButtonRelease,
                QPoint(5, 5),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
    assert blocker.args == [target]


def test_path_label_placeholder_is_inert_and_says_why(qtbot) -> None:
    label = PathLabel()
    qtbot.addWidget(label)
    label.set_placeholder(
        "/wa/amp2.lvs.report", reason="Gone - a later run overwrote it."
    )
    assert not label.is_live()
    assert label.path is None
    assert theme.ACCENT not in label.styleSheet()
    assert label.toolTip() == "Gone - a later run overwrote it."

    emitted: list[object] = []
    label.clicked.connect(emitted.append)
    label.mouseReleaseEvent(
        QMouseEvent(
            QMouseEvent.MouseButtonRelease,
            QPoint(5, 5),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    assert emitted == []
