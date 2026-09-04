"""The extract-type combo offers six members, and never the other nine.

Layer 1 of ``UX_VALIDATION.md`` asks whether a field is bound to a control.
This asks the question one notch finer: *which values does that control let a
user pick*, and does every one of them produce a deck this tool can render.

Nine of the fifteen do not. Picking one used to be two clicks, and the run
that followed passed -- with no inductor in the netlist, or no substrate
network in it. The owner ruled on 2026-09-04 that a knob they do not
understand is a knob they will never use; the ruler for this form became the
Quantus GUI they drive rather than the manual's full option table.

Everything here goes through the widget the way a user does: read the combo,
click through its entries, take the value back off the editor. The one test
that reaches past the combo is the readback case, which is deliberately the
opposite claim -- a value we do not *offer* must still be *shown* when a
recipe on disk carries it, or opening the file would silently rewrite it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.catalog import builtin_catalog  # noqa: E402
from auto_ext.model.recipe import (  # noqa: E402
    NOT_OFFERED_EXTRACT_TYPES,
    ExtractRule,
    offered_extract_types,
)
from auto_ext.ui.widgets.extract_rules import ExtractRulesEditor  # noqa: E402


def _editor(qtbot) -> ExtractRulesEditor:
    cat = builtin_catalog()
    widget = ExtractRulesEditor(
        selection_spec=cat.option("extract_selection"),
        type_spec=cat.option("extract_type"),
        field_path="extraction.extract",
    )
    qtbot.addWidget(widget)
    return widget


def _combo_items(combo) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def test_the_type_combo_never_offers_a_member_the_model_refuses(qtbot) -> None:
    """The defect, stated as the user meets it: what is in the drop-down.

    Before the ruling the list was the vendor's fifteen, so nine of the
    entries a user could pick led to a deck that runs and quietly omits the
    thing they picked it for.
    """

    widget = _editor(qtbot)
    items = _combo_items(widget.rows()[0].type_combo())

    assert items == [t.value for t in offered_extract_types()]
    for refused in NOT_OFFERED_EXTRACT_TYPES:
        assert refused.value not in items, (
            f"{refused.value} is offered on the form and refused by the model, "
            "so the only thing picking it can produce is an error"
        )


def test_every_member_the_combo_offers_makes_a_rule_the_model_accepts(qtbot) -> None:
    """Click through the whole list and build a rule out of each entry.

    The pairing is the point. A test that only checked the nine were absent
    would still pass if the combo went empty, and an empty combo is a worse
    control than an over-full one.
    """

    widget = _editor(qtbot)
    row = widget.rows()[0]
    combo = row.type_combo()

    assert combo.count() == 6
    for index in range(combo.count()):
        combo.setCurrentIndex(index)
        rule = ExtractRule(**row.value())
        assert rule.type.value == combo.itemText(index)


def test_a_new_rule_starts_on_the_catalog_default_not_the_first_entry(qtbot) -> None:
    """``none`` is still first in the list and still extracts nothing.

    Shortening the list moved every index; a widget that had been picking
    ``currentIndex(0)`` would now be picking a different wrong answer instead
    of the same one, which is exactly the kind of regression a shrunken value
    set invites.
    """

    widget = _editor(qtbot)
    widget.add_button().click()

    assert len(widget.rows()) == 2
    for row in widget.rows():
        assert row.type_combo().currentText() == "rc_coupled"


def test_a_recipe_on_disk_carrying_a_refused_type_is_still_shown(qtbot) -> None:
    """Not offering a value is not the same as pretending it is not there.

    A hand-edited YAML or an old recipe can name ``rlck_coupled``. Snapping
    the combo to its first entry would rewrite the user's file the moment
    they opened it, and they would have no way of knowing what it used to
    say -- which is a worse outcome than the one the ruling removed.
    """

    widget = _editor(qtbot)
    widget.set_value([{"selection": "all", "type": "rlck_coupled"}])

    combo = widget.rows()[0].type_combo()
    assert combo.currentText() == "rlck_coupled"
    assert widget.value()[0]["type"] == "rlck_coupled"
