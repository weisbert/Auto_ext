"""The repeating ``extract`` sub-form. Artboards ``F1``-``F3``.

One row per :class:`~auto_ext.model.recipe.ExtractRule`, in order, because
order is the semantics: Quantus accumulates ``extract`` specifications
first-to-last and **the last one wins for any net it covers**. That is what
makes the standard RF strategy expressible --

    extract -selection all             -type c_only_coupled
    extract -selection nets_file "clk" -type rc_coupled

whole chip at capacitance only, the nets that matter at RC. Two scalars could
not say it, so until this widget existed the one thing an RF engineer most
wants from this tool was unreachable from the GUI, the YAML and the CLI
alike.

Three things the design turns on:

**The operand field appears and disappears with the member that needs it.**
``all`` takes nothing; ``net`` takes a pattern; the two file forms take a
path. Which is which comes from the catalog row's ``choice_args`` -- this
widget hard-codes none of it, so a fifth member costs one line of YAML.

**Order is editable and visible.** The move buttons are not a convenience:
a rule list whose order the user cannot see or change is a list whose meaning
they cannot predict.

**The last row cannot be removed.** A recipe with no extract statement runs
Quantus and extracts nothing, which reports as a successful extraction of a
cell that happens to have no parasitics -- the worst kind of wrong answer.

**The type combo offers six of the vendor's fifteen members.** The other nine
are on the catalog row as ``choices_not_offered`` with a written reason, and
the model refuses them: each one needs a contract no template emits, so
picking it would produce the same silent success the paragraph above exists to
prevent. The owner ruled on 2026-09-04 that a knob they do not understand is a
knob they will never use -- the ruler for this form is the Quantus GUI they
actually drive, not the manual's full option table. A rule read back off disk
carrying one of the nine is still *shown* (see :meth:`ExtractRuleRow.set_value`);
what the form will not do is offer it fresh.
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from auto_ext.catalog import OptionSpec
from auto_ext.ui import theme
from auto_ext.ui.widgets.option_editor import FormComboBox

__all__ = [
    "OBJ_RULE_INDEX",
    "OBJ_RULE_ROW",
    "ExtractRuleRow",
    "ExtractRulesEditor",
]

OBJ_RULE_ROW = "extractRuleRow"
OBJ_RULE_INDEX = "extractRuleIndex"

#: Placeholder for the operand box, per ``choice_args`` kind. The kind string
#: comes from the catalog; anything unrecognised falls back to it verbatim,
#: so a new kind shows up as itself rather than as an empty box.
_ARG_PLACEHOLDER = {
    "pattern": "net name or pattern",
    "net-name pattern": "net name or pattern",
    "file": "path to a file",
    "file listing net names": "path to a file of net names",
    "file listing selected paths": "path to a file of selected paths",
}


class ExtractRuleRow(QWidget):
    """One rule: selection, its operand when it has one, and the type."""

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)
    move_requested = pyqtSignal(object, int)

    def __init__(
        self,
        *,
        selection_spec: OptionSpec,
        type_spec: OptionSpec,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_RULE_ROW)
        self._selection_spec = selection_spec
        self._type_spec = type_spec

        row = QHBoxLayout(self)
        row.setContentsMargins(0, theme.SPACE_XXS, 0, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_XS)

        self._index = QLabel("1", self)
        self._index.setObjectName(OBJ_RULE_INDEX)
        self._index.setFixedWidth(18)
        self._index.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self._index, 0)

        # :class:`FormComboBox`, not a bare one: these two sit in the same
        # scrolled column as the rest of the form, and a wheel notch over an
        # unfocused combo used to rewrite the rule under the cursor.
        self._selection = FormComboBox(self)
        for choice in selection_spec.offered_choices:
            self._selection.addItem(str(choice))
        self._selection.currentIndexChanged.connect(self._on_selection_changed)
        row.addWidget(self._selection, 0)

        self._arg = QLineEdit(self)
        self._arg.textEdited.connect(lambda _t: self.changed.emit())
        row.addWidget(self._arg, 1)

        # ``offered_choices``, not ``choices``: nine of the fifteen
        # ``extract -type`` members render a deck that runs, reports success
        # and omits the very thing the type was chosen for, because the
        # contract they need (inductor components, a substrate net file) is
        # emitted by no template. The owner ruled on 2026-09-04 that a knob
        # they do not understand is a knob they will never use, and the model
        # refuses those nine, so a combo that still listed them would offer
        # nine ways to get an error dialog.
        self._type = FormComboBox(self)
        for choice in type_spec.offered_choices:
            self._type.addItem(str(choice))
        self._type.currentIndexChanged.connect(lambda _i: self.changed.emit())
        row.addWidget(self._type, 0)

        self._up = QPushButton("↑", self)
        self._down = QPushButton("↓", self)
        self._remove = QPushButton("×", self)
        for button, tip in (
            (self._up, "move this rule earlier -- later rules override earlier ones"),
            (self._down, "move this rule later -- later rules override earlier ones"),
            (self._remove, "remove this rule"),
        ):
            button.setFlat(True)
            button.setFixedWidth(22)
            button.setToolTip(tip)
            row.addWidget(button, 0)
        self._up.clicked.connect(lambda: self.move_requested.emit(self, -1))
        self._down.clicked.connect(lambda: self.move_requested.emit(self, 1))
        self._remove.clicked.connect(lambda: self.remove_requested.emit(self))

        self._sync_arg_visibility()

    # -- value ---------------------------------------------------------

    def value(self) -> dict[str, Any]:
        """The rule as the model takes it. ``selection_arg`` omitted when empty."""

        rule: dict[str, Any] = {
            "selection": self._selection.currentText(),
            "type": self._type.currentText(),
        }
        text = self._arg.text().strip()
        if text and self._arg_kind():
            rule["selection_arg"] = text
        return rule

    def set_value(self, rule: Any) -> None:
        """Fill from a rule. Missing fields take the CATALOG default.

        Not the first combo entry: ``extract -type``'s first member is
        ``none``, which extracts nothing, so a new rule that defaulted to the
        top of the list would quietly add a statement that turns extraction
        off for every net it covers.
        """

        selection = str(
            _get(rule, "selection", None)
            or self._selection_spec.default
            or "all"
        )
        kind = str(
            _get(rule, "type", None) or self._type_spec.default or ""
        )
        arg = _get(rule, "selection_arg", None)

        for combo, wanted in ((self._selection, selection), (self._type, kind)):
            combo.blockSignals(True)
            try:
                at = combo.findText(wanted)
                if at < 0 and wanted:
                    # A value this build does not offer still has to be shown.
                    # Silently snapping to the first entry would rewrite the
                    # user's recipe on open.
                    combo.addItem(wanted)
                    at = combo.count() - 1
                combo.setCurrentIndex(max(at, 0))
            finally:
                combo.blockSignals(False)
        self._arg.blockSignals(True)
        try:
            self._arg.setText("" if arg is None else str(arg))
        finally:
            self._arg.blockSignals(False)
        self._sync_arg_visibility()

    # -- presentation --------------------------------------------------

    def set_position(self, index: int, total: int) -> None:
        self._index.setText(str(index + 1))
        self._up.setEnabled(index > 0)
        self._down.setEnabled(index < total - 1)
        self._remove.setEnabled(total > 1)
        self._remove.setToolTip(
            "remove this rule"
            if total > 1
            else "a recipe needs at least one extract rule"
        )

    def selection_combo(self) -> FormComboBox:
        return self._selection

    def type_combo(self) -> FormComboBox:
        return self._type

    def arg_edit(self) -> QLineEdit:
        return self._arg

    def _arg_kind(self) -> str | None:
        return self._selection_spec.choice_args.get(self._selection.currentText())

    def _sync_arg_visibility(self) -> None:
        kind = self._arg_kind()
        self._arg.setVisible(kind is not None)
        if kind is None:
            # Clearing on hide, not just hiding: an operand left behind by a
            # member that no longer takes one would be refused by the model
            # with an error about a field the user cannot see.
            if self._arg.text():
                self._arg.blockSignals(True)
                try:
                    self._arg.clear()
                finally:
                    self._arg.blockSignals(False)
            return
        self._arg.setPlaceholderText(_ARG_PLACEHOLDER.get(kind, kind))
        self._arg.setToolTip(f"{self._selection.currentText()} takes a {kind}")

    def _on_selection_changed(self, _index: int) -> None:
        self._sync_arg_visibility()
        self.changed.emit()


class ExtractRulesEditor(QWidget):
    """The whole ordered list, plus Add."""

    value_changed = pyqtSignal(str, object)

    def __init__(
        self,
        *,
        selection_spec: OptionSpec,
        type_spec: OptionSpec,
        field_path: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._selection_spec = selection_spec
        self._type_spec = type_spec
        self._field_path = field_path
        self._rows: list[ExtractRuleRow] = []
        self._quiet = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._rows_host = QWidget(self)
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        outer.addWidget(self._rows_host)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self._add = QPushButton("+ add rule", self)
        self._add.setFlat(True)
        self._add.setToolTip(
            "a later rule overrides an earlier one for any net it covers"
        )
        self._add.clicked.connect(self._on_add)
        footer.addWidget(self._add, 0)
        self._note = QLabel("", self)
        self._note.setObjectName(theme.OBJ_OPTION_HINT if hasattr(theme, "OBJ_OPTION_HINT") else "")
        footer.addWidget(self._note, 1)
        outer.addLayout(footer)

        self.set_value([{}])

    # -- value ---------------------------------------------------------

    def field_path(self) -> str:
        return self._field_path

    def value(self) -> list[dict[str, Any]]:
        return [row.value() for row in self._rows]

    def set_value(self, rules: Any) -> None:
        """Rebuild from a list of rules (models or plain dicts)."""

        items = list(rules or [])
        if not items:
            items = [{}]
        self._quiet = True
        try:
            while len(self._rows) > len(items):
                self._drop_row(self._rows[-1])
            while len(self._rows) < len(items):
                self._append_row()
            for row, rule in zip(self._rows, items):
                row.set_value(rule)
        finally:
            self._quiet = False
        self._renumber()

    def rows(self) -> list[ExtractRuleRow]:
        return list(self._rows)

    def add_button(self) -> QPushButton:
        return self._add

    def note_text(self) -> str:
        return self._note.text()

    # -- internals -----------------------------------------------------

    def _append_row(self) -> ExtractRuleRow:
        row = ExtractRuleRow(
            selection_spec=self._selection_spec,
            type_spec=self._type_spec,
            parent=self._rows_host,
        )
        row.changed.connect(self._emit)
        row.remove_requested.connect(self._on_remove)
        row.move_requested.connect(self._on_move)
        self._rows_layout.addWidget(row)
        self._rows.append(row)
        return row

    def _drop_row(self, row: ExtractRuleRow) -> None:
        self._rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()

    def _on_add(self) -> None:
        row = self._append_row()
        row.set_value({})
        self._renumber()
        self._emit()

    def _on_remove(self, row: ExtractRuleRow) -> None:
        if len(self._rows) <= 1:
            return
        self._drop_row(row)
        self._renumber()
        self._emit()

    def _on_move(self, row: ExtractRuleRow, delta: int) -> None:
        at = self._rows.index(row)
        to = at + delta
        if not 0 <= to < len(self._rows):
            return
        self._rows.insert(to, self._rows.pop(at))
        self._rows_layout.insertWidget(to, row)
        self._renumber()
        self._emit()

    def _renumber(self) -> None:
        total = len(self._rows)
        for index, row in enumerate(self._rows):
            row.set_position(index, total)
        self._note.setText(
            ""
            if total <= 1
            else f"{total} rules — a later rule overrides an earlier one "
            "for any net it covers"
        )

    def _emit(self) -> None:
        if self._quiet:
            return
        self.value_changed.emit(self._field_path, self.value())


def _get(obj: Any, name: str, fallback: Any) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, fallback)
    return getattr(obj, name, fallback)
