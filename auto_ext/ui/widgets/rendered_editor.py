"""Edit one generated tool input by hand; the diff becomes a recipe patch.

Replaces the ``EditTemplateDialog`` half of the old ``diff_editor.py``. That
dialog wrote a ``.j2`` template back to disk in place, which the redesign
removed: templates are catalog state now, and a user edit belongs to the
Recipe as a masked patch so it survives a template upgrade and travels with
the recipe to another cell.

So this dialog never writes a file. It shows the text the catalog would
generate, lets the user change it, and hands the result back to
:func:`auto_ext.ui.patch_capture.capture`, which turns the difference into a
:class:`~auto_ext.core.patch.TemplatePatch`. That is the only edit path that
:class:`~auto_ext.ui.widgets.patch_strip.PatchStrip` can later show, revert
and delete hunk by hunk.

No syntax highlighting: the text is a *rendered* runset, not a template --
there is no Jinja left in it, and colouring EDA syntax would be inventing a
lexer per tool.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_ext.ui import theme
from auto_ext.ui.patch_capture import RenderPreview

__all__ = ["RenderedFileEditor"]

_FOOTER = (
    "Nothing is written to disk. The difference from the generated text is "
    "stored on the recipe as a masked edit and re-applied on every run, for "
    "every cell."
)


def _mono_font() -> QFont:
    font = QFont(theme.FONT_MONO_FAMILIES[0])
    font.setStyleHint(QFont.TypeWriter)
    font.setPointSize(max(theme.FONT_SIZE_MIN - 2, 8))
    return font


class RenderedFileEditor(QDialog):
    """Modal editor over one :class:`~auto_ext.ui.patch_capture.RenderPreview`.

    :attr:`accepted_text` is the text to capture; it is only meaningful once
    :attr:`saved` is true. ``Store as a manual edit`` is disabled while the
    text is identical to the generated one, so the dialog cannot produce an
    empty patch.
    """

    def __init__(
        self,
        preview: RenderPreview,
        *,
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._preview = preview
        self._saved = False

        self.setWindowTitle(f"Edit generated file - {preview.filename}")
        self.setModal(True)
        self.resize(900, 700)

        root = QVBoxLayout(self)

        heading = QLabel(
            f"{preview.stage_key} / {preview.filename}"
            + (f"  -  {subtitle}" if subtitle else ""),
            self,
        )
        heading.setTextFormat(Qt.PlainText)
        heading.setWordWrap(True)
        heading.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-weight: {theme.FONT_WEIGHT_SEMIBOLD};"
        )
        root.addWidget(heading)

        provenance = QLabel(
            f"rendered from {preview.template_id} for cell {subtitle or '(none)'}"
            if subtitle
            else f"rendered from {preview.template_id}",
            self,
        )
        provenance.setTextFormat(Qt.PlainText)
        provenance.setWordWrap(True)
        provenance.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-family: {theme.FONT_MONO}; "
            f"font-size: {theme.FONT_SIZE_META}px;"
        )
        root.addWidget(provenance)

        self._editor = QPlainTextEdit(self)
        self._editor.setFont(_mono_font())
        self._editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._editor.setPlainText(preview.base_text)
        self._editor.textChanged.connect(self._refresh_buttons)
        root.addWidget(self._editor, 1)

        footer = QLabel(_FOOTER, self)
        footer.setWordWrap(True)
        footer.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        root.addWidget(footer)

        row = QHBoxLayout()
        self._revert_button = QPushButton("Revert to generated", self)
        self._revert_button.clicked.connect(self.revert_to_generated)
        self._store_button = QPushButton("Store as a manual edit", self)
        self._store_button.setProperty("primary", "true")
        self._store_button.setDefault(True)
        self._store_button.clicked.connect(self._on_store)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        row.addWidget(self._revert_button)
        row.addStretch(1)
        row.addWidget(self._store_button)
        row.addWidget(cancel)
        root.addLayout(row)

        self._refresh_buttons()

    # ---- public API ---------------------------------------------------

    @property
    def preview(self) -> RenderPreview:
        return self._preview

    @property
    def saved(self) -> bool:
        """``True`` once the user pressed Store on a text that differs."""

        return self._saved

    def edited_text(self) -> str:
        """Whatever is in the editor right now."""

        return self._editor.toPlainText()

    def accepted_text(self) -> str:
        """The text to capture. Only meaningful when :attr:`saved`."""

        return self._editor.toPlainText()

    def set_text(self, text: str) -> None:
        """Replace the editor contents. Used by the host and by tests."""

        self._editor.setPlainText(text)

    def has_changes(self) -> bool:
        return self.edited_text() != self._preview.base_text

    def revert_to_generated(self) -> None:
        """Throw the edit away and show the generated text again."""

        self._editor.setPlainText(self._preview.base_text)

    # ---- slots --------------------------------------------------------

    def _refresh_buttons(self) -> None:
        changed = self.has_changes()
        self._store_button.setEnabled(changed)
        self._revert_button.setEnabled(changed)

    def _on_store(self) -> None:
        if not self.has_changes():
            return
        self._saved = True
        self.accept()

    def reject(self) -> None:
        """Confirm before discarding an edit that would otherwise be lost."""

        if self.has_changes() and not self._saved:
            choice = QMessageBox.question(
                self,
                "Discard this edit?",
                "The generated file has unsaved changes. Discard them?",
                QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if choice != QMessageBox.Discard:
                return
        super().reject()
