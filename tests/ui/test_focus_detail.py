"""The focused-row strip on its own: what it says, and what its Reset acts on.

The strip follows **focus**, and focus is not a selection anybody made. A Tab
moves it; so does a stray click three rows away. Everything the strip shows is
therefore a claim about a row the user may not be looking at, and the one
control on it -- ``Reset`` -- writes ``spec.default`` into that row. The rule
this file holds is that the strip never describes one row while its button
would act on another, and that the button says out loud which row that is.

The screen-level half of the same rule lives in ``test_recipes_screen.py``
(``test_reset_names_the_row_it_is_about_to_reset``): this file drives the
widget with specs directly, so a failure here is the strip's and a failure
there is the wiring's.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.catalog import (  # noqa: E402
    Confidence,
    Currently,
    OptionSpec,
    OptionType,
    Owner,
    builtin_catalog,
)
from auto_ext.ui.widgets.focus_detail import FocusDetailBar  # noqa: E402


def spec(**over) -> OptionSpec:
    """A minimal valid recipe-owned catalog row, overridable field by field."""

    fields: dict[str, object] = {
        "key": "demo_key",
        "template_var": "demo_var",
        "context_path": "recipe.extraction.demo",
        "owner": Owner.RECIPE,
        "type": OptionType.STR,
        "choices_confidence": Confidence.CERTAIN,
        "currently": Currently.JINJA_VAR,
        "observed": False,
        "why": "a demo row",
    }
    fields.update(over)
    return OptionSpec(**fields)  # type: ignore[arg-type]


def _bar(qtbot) -> FocusDetailBar:
    bar = FocusDetailBar()
    qtbot.addWidget(bar)
    return bar


def test_the_reset_button_names_the_row_it_would_write_to(qtbot) -> None:
    """"Reset to default" named nothing, and sat 42px from what it destroys.

    Two rows described in turn have to leave two different words on the
    button, or the button is a coin flip the user cannot see the faces of.
    """

    bar = _bar(qtbot)
    warm = spec(key="temperature_c", type=OptionType.FLOAT, default=55.0)
    depth = spec(key="min_res_ohm", type=OptionType.FLOAT, default=0.001)

    bar.show_spec(warm, value=125.0, at_default=False)
    first = bar.reset_button().text()
    assert "temperature_c" in first

    bar.show_spec(depth, value=7.0, at_default=False)
    second = bar.reset_button().text()
    assert "min_res_ohm" in second
    assert "temperature_c" not in second
    assert first != second


def test_the_button_still_names_the_row_while_it_is_disabled(qtbot) -> None:
    """Disabled, not hidden -- and a nameless disabled button is worse.

    The strip deliberately keeps Reset on screen at the catalog default so it
    does not come and go as focus moves. A button that stays put has to keep
    saying what it is for while it waits.
    """

    bar = _bar(qtbot)
    bar.show()
    qtbot.waitExposed(bar)
    bar.show_spec(spec(key="busbit", default="<>"), value="<>", at_default=True)

    assert bar.reset_button().isVisible() is True
    assert bar.reset_button().isEnabled() is False
    assert "busbit" in bar.reset_button().text()
    assert "already at the catalog default" in bar.reset_button().toolTip()


def test_an_empty_strip_offers_no_row_and_no_button(qtbot) -> None:
    """With nothing focused there is nothing to name, so nothing is offered."""

    bar = _bar(qtbot)
    bar.show_spec(spec(key="busbit", default="<>"), value="x", at_default=False)
    bar.clear()

    assert bar.key() == ""
    assert bar.reset_button().isVisible() is False
    assert "focus a setting" in bar.prose_text()


def test_reset_emits_the_key_it_advertised(qtbot) -> None:
    """The name on the button and the key on the signal are one fact.

    Two sources for "which row" is how the button came to advertise one row
    and act on another in the first place.
    """

    bar = _bar(qtbot)
    one = spec(key="reduction_criterion", default="standard")
    bar.show_spec(one, value="other", at_default=False)
    assert one.key in bar.reset_button().text()

    with qtbot.waitSignal(bar.reset_requested, timeout=1000) as blocker:
        bar.reset_button().click()
    assert blocker.args == [one.key]


def test_every_recipe_owned_row_fits_a_readable_button(qtbot) -> None:
    """The label is a catalog key, and the longest of those is not short.

    The strip is 42px of a window whose floor is 940px wide, and the prose
    beside the button elides -- but a button that grew past a quarter of the
    strip would push the sentence it belongs to off the screen.
    """

    bar = _bar(qtbot)
    bar.resize(940, 42)
    widest = 0
    for one in builtin_catalog().by_owner(Owner.RECIPE):
        bar.show_spec(one, value=None, at_default=False)
        widest = max(widest, bar.reset_button().sizeHint().width())
    assert widest <= 940 // 4, f"the widest Reset label needs {widest}px"


def test_a_long_key_is_cut_in_the_middle_and_kept_whole_in_the_tooltip(
    qtbot,
) -> None:
    """Eight catalog keys share a prefix, so a tail cut hides what differs.

    ``global_nets_import_from_lvs`` and ``global_nets_force`` cut at the tail
    are the same twenty-two characters twice.
    """

    bar = _bar(qtbot)
    one = spec(key="parasitic_blocking_device_cells_type", default="white")
    bar.show_spec(one, value="black", at_default=False)

    label = bar.reset_button().text()
    assert "..." in label
    assert label.startswith("Reset parasitic")
    assert label.endswith("ells_type")
    assert one.key in bar.reset_button().toolTip()

    left = spec(key="global_nets_import_from_lvs", default=None)
    right = spec(key="global_nets_force", default=None)
    bar.show_spec(left, value="x", at_default=False)
    first = bar.reset_button().text()
    bar.show_spec(right, value="x", at_default=False)
    assert first != bar.reset_button().text(), (
        "two rows sharing a prefix left the same words on the button"
    )
