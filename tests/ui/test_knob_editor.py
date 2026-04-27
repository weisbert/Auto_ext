"""Direct tests for :class:`auto_ext.ui.widgets.knob_editor.KnobEditor`.

Existing coverage was indirect (through TemplatesTab); the choices
branch added in Phase 5.6.3 deserves focused tests since QComboBox is
a separate widget path with its own data-binding semantics.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QCheckBox, QComboBox, QLineEdit  # noqa: E402

from auto_ext.core.manifest import KnobSpec  # noqa: E402
from auto_ext.ui.widgets.knob_editor import KnobEditor  # noqa: E402


def _find(widget: KnobEditor, kind: type) -> object | None:
    return widget.findChild(kind)


def test_choices_renders_combobox(qtbot) -> None:
    spec = KnobSpec(type="str", default="wodio", choices=["wodio", "widio"])
    w = KnobEditor("lvs_variant", spec)
    qtbot.addWidget(w)
    combo = _find(w, QComboBox)
    assert combo is not None
    assert _find(w, QLineEdit) is None
    assert combo.count() == 2
    assert [combo.itemData(i) for i in range(combo.count())] == ["wodio", "widio"]


def test_choices_set_value_picks_correct_index(qtbot) -> None:
    spec = KnobSpec(type="str", default="wodio", choices=["wodio", "widio"])
    w = KnobEditor("lvs_variant", spec)
    qtbot.addWidget(w)
    w.set_value("widio", is_default=False)
    combo = _find(w, QComboBox)
    assert combo.currentData() == "widio"
    assert combo.currentIndex() == 1


def test_choices_set_value_silent_no_emit(qtbot) -> None:
    spec = KnobSpec(type="str", default="wodio", choices=["wodio", "widio"])
    w = KnobEditor("lvs_variant", spec)
    qtbot.addWidget(w)
    received: list[tuple[str, object]] = []
    w.value_changed.connect(lambda n, v: received.append((n, v)))
    w.set_value("widio", is_default=False)
    assert received == []


def test_choices_user_change_emits_value(qtbot) -> None:
    spec = KnobSpec(type="str", default="wodio", choices=["wodio", "widio"])
    w = KnobEditor("lvs_variant", spec)
    qtbot.addWidget(w)
    w.set_value("wodio", is_default=True)
    received: list[tuple[str, object]] = []
    w.value_changed.connect(lambda n, v: received.append((n, v)))
    combo = _find(w, QComboBox)
    combo.setCurrentIndex(1)
    assert received == [("lvs_variant", "widio")]


def test_choices_unknown_value_falls_back_to_index_zero(qtbot) -> None:
    """A stale project.yaml override that no longer matches any choice
    must not crash the editor; surface as the first choice instead."""
    spec = KnobSpec(type="str", default="wodio", choices=["wodio", "widio"])
    w = KnobEditor("lvs_variant", spec)
    qtbot.addWidget(w)
    w.set_value("legacy_value", is_default=False)
    combo = _find(w, QComboBox)
    assert combo.currentIndex() == 0


def test_bool_still_renders_checkbox_when_no_choices(qtbot) -> None:
    spec = KnobSpec(type="bool", default=False)
    w = KnobEditor("connect_by_name", spec)
    qtbot.addWidget(w)
    assert _find(w, QCheckBox) is not None
    assert _find(w, QComboBox) is None
    assert _find(w, QLineEdit) is None
