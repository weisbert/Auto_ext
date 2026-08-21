"""Tests for :class:`auto_ext.ui.widgets.rendered_editor.RenderedFileEditor`.

The dialog is deliberately dumb: it shows the generated text, reports whether
the user changed it, and never touches the filesystem. The last part is the
one worth pinning -- its predecessor (``EditTemplateDialog``) wrote a ``.j2``
back to disk in place, which is exactly what the patch mechanism replaced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QMessageBox  # noqa: E402

from auto_ext.ui import patch_capture  # noqa: E402
from auto_ext.ui.widgets.rendered_editor import RenderedFileEditor  # noqa: E402
from tests.support.v2 import ENV, make_cell, make_workspace  # noqa: E402


@pytest.fixture
def preview(recipe, pdk_profile, tmp_path: Path):
    plan = patch_capture.editable_targets(recipe)[0]
    return patch_capture.build_preview(
        plan,
        recipe=recipe,
        profile=pdk_profile,
        workspace=make_workspace(),
        cell=make_cell(),
        resolved_env=ENV,
        workarea=tmp_path,
    )


@pytest.fixture
def dialog(qtbot, preview) -> RenderedFileEditor:
    widget = RenderedFileEditor(preview, subtitle="inv")
    qtbot.addWidget(widget)
    return widget


def test_it_opens_on_the_generated_text(dialog, preview) -> None:
    assert dialog.edited_text() == preview.base_text
    assert dialog.has_changes() is False
    assert dialog.saved is False


def test_store_is_disabled_until_something_changes(dialog) -> None:
    """A dialog that could produce an empty patch would put a no-op hunk on
    the recipe and make the manual-edit count lie."""

    assert dialog._store_button.isEnabled() is False
    dialog.set_text(dialog.edited_text() + "; typed by hand\n")
    assert dialog._store_button.isEnabled() is True


def test_storing_accepts_and_hands_back_the_edited_text(dialog) -> None:
    edited = dialog.edited_text() + "; typed by hand\n"
    dialog.set_text(edited)
    dialog._store_button.click()

    assert dialog.saved is True
    assert dialog.accepted_text() == edited
    assert dialog.result() == RenderedFileEditor.Accepted


def test_revert_puts_the_generated_text_back(dialog, preview) -> None:
    dialog.set_text("everything replaced")
    dialog.revert_to_generated()

    assert dialog.edited_text() == preview.base_text
    assert dialog._store_button.isEnabled() is False


def test_cancelling_an_edit_asks_before_discarding_it(
    dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda parent, title, text, *a, **k: asked.append(title) or QMessageBox.Cancel,
    )
    dialog.set_text(dialog.edited_text() + "x")
    dialog.reject()

    assert asked == ["Discard this edit?"]
    assert dialog.isVisible() is False or dialog.result() != RenderedFileEditor.Rejected

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    dialog.reject()
    assert dialog.result() == RenderedFileEditor.Rejected


def test_cancelling_an_untouched_dialog_asks_nothing(
    dialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args, **kwargs):  # pragma: no cover - the point is it is not called
        raise AssertionError("nothing to discard, nothing to ask")

    monkeypatch.setattr(QMessageBox, "question", explode)
    dialog.reject()
    assert dialog.result() == RenderedFileEditor.Rejected


def test_the_dialog_writes_nothing_to_disk(
    dialog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its predecessor overwrote a ``.j2`` in place. This one must not."""

    def refuse(*args, **kwargs):
        raise AssertionError("the editor must not write files")

    monkeypatch.setattr(Path, "write_text", refuse)
    monkeypatch.setattr(Path, "write_bytes", refuse)

    dialog.set_text(dialog.edited_text() + "; typed by hand\n")
    dialog._store_button.click()
    assert dialog.saved is True


def test_the_header_names_the_file_and_the_cell_it_was_rendered_for(
    dialog, preview
) -> None:
    from PyQt5.QtWidgets import QLabel

    labels = [w.text() for w in dialog.findChildren(QLabel)]
    joined = "\n".join(labels)
    assert preview.filename in joined
    assert "inv" in joined
    assert preview.template_id in joined
