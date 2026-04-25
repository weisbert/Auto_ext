"""Tests for :class:`auto_ext.ui.widgets.tag_list_edit.TagListEdit`."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.ui.widgets.tag_list_edit import TagListEdit  # noqa: E402


def test_initial_values(qtbot) -> None:
    w = TagListEdit(["a", "b", "c"])
    qtbot.addWidget(w)
    assert w.values() == ["a", "b", "c"]


def test_set_values_deduplicates_preserving_order(qtbot) -> None:
    w = TagListEdit()
    qtbot.addWidget(w)
    w.set_values(["a", "b", "a", "c"])
    assert w.values() == ["a", "b", "c"]


def test_set_values_drops_empty_and_non_str(qtbot) -> None:
    w = TagListEdit()
    qtbot.addWidget(w)
    w.set_values(["a", "", None, "b"])  # type: ignore[list-item]
    assert w.values() == ["a", "b"]


def test_add_emits_values_changed(qtbot, monkeypatch) -> None:
    w = TagListEdit(["a"])
    qtbot.addWidget(w)

    # Stub QInputDialog.getText to simulate user typing "new".
    from PyQt5.QtWidgets import QInputDialog

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *args, **kwargs: ("new", True),
    )

    with qtbot.waitSignal(w.values_changed, timeout=1000) as sig:
        w._on_add()
    assert sig.args[0] == ["a", "new"]
    assert w.values() == ["a", "new"]


def test_add_duplicate_is_silently_ignored(qtbot, monkeypatch) -> None:
    w = TagListEdit(["a"])
    qtbot.addWidget(w)
    from PyQt5.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("a", True))
    # Should NOT emit (duplicate). Use assertNotEmitted pattern.
    with qtbot.assertNotEmitted(w.values_changed, wait=100):
        w._on_add()
    assert w.values() == ["a"]


def test_add_empty_string_is_silently_ignored(qtbot, monkeypatch) -> None:
    w = TagListEdit(["a"])
    qtbot.addWidget(w)
    from PyQt5.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("   ", True))
    with qtbot.assertNotEmitted(w.values_changed, wait=100):
        w._on_add()
    assert w.values() == ["a"]


def test_remove_emits_values_changed(qtbot) -> None:
    w = TagListEdit(["a", "b", "c"])
    qtbot.addWidget(w)
    with qtbot.waitSignal(w.values_changed, timeout=1000) as sig:
        w._on_remove("b")
    assert sig.args[0] == ["a", "c"]
    assert w.values() == ["a", "c"]


def test_remove_unknown_value_is_noop(qtbot) -> None:
    w = TagListEdit(["a"])
    qtbot.addWidget(w)
    with qtbot.assertNotEmitted(w.values_changed, wait=100):
        w._on_remove("ghost")
    assert w.values() == ["a"]
