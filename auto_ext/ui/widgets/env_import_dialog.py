"""Read this project's environment values out of files this project produced.

The Project screen's ``Read environment from a file...`` opens this. It is the
GUI over :func:`auto_ext.core.env_import.import_env`, and it exists for the
half of Setup that ``check-env`` can never answer: a variable the shell does
not export has no second place to be looked up -- except in the files the
project has already written, where the value is sitting in rendered form.

One decision per row
--------------------
The table is the dialog. Each row is one variable, and each row answers four
questions before the user is asked to accept anything:

``value``      what the file says.
``in effect``  what is in force today -- an existing pin, else the shell, else
               nothing. This is the column that makes the decision: a row
               where the two agree is a row with nothing to do.
``read from``  which file, and how (the expression that was inverted). An
               environment value decides where every stage reads and writes;
               accepting one without seeing where it came from is how this
               machine's paths get frozen into a profile that then travels.
``take it``    the checkbox. Pre-ticked only for rows that would change
               something.

A variable two files answer differently is shown with both answers and is
**not** pre-ticked. Averaging or voting would invent a rule this project does
not have, and the wrong answer looks exactly as plausible as the right one.

What it does not do
-------------------
It never writes. Accepting emits :attr:`EnvImportDialog.values_accepted` with
``{name: value}``; the host applies it to ``PdkProfile.env_overrides`` through
:meth:`~auto_ext.ui.screens.project_screen.ProjectScreen.apply_edit`, so the
edit is staged like any other and the user still has to press Save. The same
rule the Setup drawer's pin follows, for the same reason: a value read out of
a file must not be able to rewrite the PDK profile behind the user's back.

Assumptions
-----------
* The scan runs on the GUI thread, like the recipe import's -- it parses a
  handful of files and inverts a handful of expressions.
* ``env_overrides`` is the landing field because it already means "used
  INSTEAD of what the shell exports", and the health checks already draw a
  value that resolved that way with the exchange glyph rather than a tick.
  That "you deliberately deviated" mark is exactly what this feature wants.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.env_import import (
    EnvImportError,
    EnvImportResult,
    SolvedEnvVar,
    import_env,
)
from auto_ext.model.pdk import PdkProfile
from auto_ext.ui import theme

__all__ = [
    "COLUMNS",
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "EnvImportDialog",
    "in_effect",
]

DEFAULT_WIDTH = 900
DEFAULT_HEIGHT = 520

#: Table columns, in order. ``take it`` is the checkbox and carries no title:
#: a header over a column of checkboxes says nothing the checkboxes do not.
COLUMNS = ("", "variable", "value", "in effect now", "read from")

_NOTHING = "-- nothing --"


def in_effect(var: SolvedEnvVar) -> str:
    """What is in force for ``var`` today, and where it comes from.

    Precedence is the profile's, not this dialog's: a pin wins over the shell
    at render time, so a pin is what "in effect" means when there is one.
    """

    if var.pinned_value is not None:
        return f"{var.pinned_value}  (pinned)"
    if var.shell_value is not None:
        return f"{var.shell_value}  (shell)"
    return _NOTHING


class EnvImportDialog(QDialog):
    """Pick files, see what they say, tick what to adopt. Writes nothing."""

    #: The scan succeeded. Carries the
    #: :class:`~auto_ext.core.env_import.EnvImportResult`.
    scanned = pyqtSignal(object)
    #: The scan refused these files. Carries the message shown.
    scan_failed = pyqtSignal(str)
    #: The user accepted rows. Carries ``{name: value}`` for the ticked ones.
    values_accepted = pyqtSignal(object)

    def __init__(
        self,
        *,
        profile: PdkProfile | None,
        start_dir: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Read the environment from a file this project produced")
        self._profile = profile
        self._start_dir = Path(start_dir) if start_dir is not None else None
        self._result: EnvImportResult | None = None
        self._files: list[Path] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD
        )
        column.setSpacing(theme.SPACE_SM)

        column.addWidget(self._build_intro())
        column.addWidget(self._build_file_row())
        column.addWidget(self._build_table(), 1)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        column.addWidget(self._status)
        column.addWidget(self._build_buttons())

        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._refresh_enabled()

    # -- construction ----------------------------------------------------

    def _build_intro(self) -> QWidget:
        label = QLabel(
            "Point this at a runset, a .cmd or an si.env that was generated "
            "under THIS project's environment. The values it holds are already "
            "resolved, so they can be read back even where the shell has "
            "nothing to say -- which is the half `check-env` cannot answer.",
            self,
        )
        label.setWordWrap(True)
        label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        return label

    def _build_file_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        self._files_label = QLabel("No files chosen", row)
        self._files_label.setStyleSheet(f"font-family: {theme.FONT_MONO};")
        layout.addWidget(self._files_label, 1)

        add = QPushButton("Choose files...", row)
        add.clicked.connect(self._on_choose)
        layout.addWidget(add)

        self._scan_btn = QPushButton("Read them", row)
        self._scan_btn.clicked.connect(self.scan)
        layout.addWidget(self._scan_btn)
        return row

    def _build_table(self) -> QWidget:
        table = QTableWidget(0, len(COLUMNS), self)
        table.setHorizontalHeaderLabels(list(COLUMNS))
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        for index in range(2, len(COLUMNS)):
            header.setSectionResizeMode(index, QHeaderView.Stretch)
        table.itemChanged.connect(lambda _item: self._refresh_enabled())
        self._table = table
        return table

    def _build_buttons(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)
        layout.addStretch(1)

        cancel = QPushButton("Cancel", row)
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel)

        self._accept_btn = QPushButton("Pin the ticked values", row)
        self._accept_btn.setDefault(True)
        self._accept_btn.setToolTip(
            "Writes them into the profile's env_overrides as a staged edit. "
            "Nothing reaches disk until you Save."
        )
        self._accept_btn.clicked.connect(self._on_accept)
        layout.addWidget(self._accept_btn)
        return row

    # -- public API ------------------------------------------------------

    def set_files(self, paths: list[Path] | list[str]) -> None:
        """Offer these files, without scanning them yet."""

        self._files = [Path(p) for p in paths]
        self._files_label.setText(
            ", ".join(p.name for p in self._files) or "No files chosen"
        )
        self._files_label.setToolTip("\n".join(str(p) for p in self._files))
        self._refresh_enabled()

    def files(self) -> list[Path]:
        return list(self._files)

    def result(self) -> EnvImportResult | None:
        """The last successful scan, or ``None``."""

        return self._result

    def scan(self) -> bool:
        """Read the offered files. False (with a message shown) on refusal."""

        if not self._files:
            return False
        try:
            result = import_env(self._files, profile=self._profile)
        except EnvImportError as exc:
            self._result = None
            self._table.setRowCount(0)
            self._show(str(exc), failed=True)
            self.scan_failed.emit(str(exc))
            self._refresh_enabled()
            return False
        self._result = result
        self._fill(result)
        self._show(self._summary_text(result), failed=False)
        self.scanned.emit(result)
        self._refresh_enabled()
        return True

    def ticked(self) -> dict[str, str]:
        """``{name: value}`` for every row the user has ticked."""

        chosen: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                name = self._table.item(row, 1).text()
                chosen[name] = self._table.item(row, 2).text()
        return chosen

    def set_ticked(self, names: set[str]) -> None:
        """Tick exactly ``names``. For tests and for a select-all control."""

        for row in range(self._table.rowCount()):
            name = self._table.item(row, 1).text()
            self._table.item(row, 0).setCheckState(
                Qt.Checked if name in names else Qt.Unchecked
            )

    def status(self) -> str:
        return self._status.text()

    # -- rendering -------------------------------------------------------

    def _fill(self, result: EnvImportResult) -> None:
        table = self._table
        table.blockSignals(True)
        try:
            table.setRowCount(len(result.solved))
            for row, var in enumerate(result.solved):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                # Pre-ticked only where it would change something AND the files
                # agree. A disagreement is a question for the user, and a
                # pre-ticked answer to a question is not a question.
                ticked = var.would_change_anything and not var.disagreements
                check.setCheckState(Qt.Checked if ticked else Qt.Unchecked)
                if var.disagreements:
                    check.setToolTip(
                        "Two of these files disagree about this variable. "
                        "Decide which is right before pinning it."
                    )
                elif not var.would_change_anything:
                    check.setToolTip(
                        "Already in effect -- pinning it would only freeze "
                        "today's answer into a file that travels."
                    )
                table.setItem(row, 0, check)

                table.setItem(row, 1, QTableWidgetItem(var.name))
                table.setItem(row, 2, self._value_item(var))
                table.setItem(row, 3, QTableWidgetItem(in_effect(var)))
                source = QTableWidgetItem(f"{var.source}: {var.via}")
                source.setToolTip(var.via)
                table.setItem(row, 4, source)
        finally:
            table.blockSignals(False)

    def _value_item(self, var: SolvedEnvVar) -> QTableWidgetItem:
        if not var.disagreements:
            return QTableWidgetItem(var.value)
        others = ", ".join(f"{value} ({source})" for value, source in var.disagreements)
        item = QTableWidgetItem(f"{var.value}   [also: {others}]")
        item.setToolTip(f"Other files said: {others}")
        return item

    def _summary_text(self, result: EnvImportResult) -> str:
        parts = [result.summary()]
        if result.unreadable:
            names = ", ".join(item.label for item in result.unreadable)
            parts.append(f"could not read: {names}")
        if result.unanswered:
            parts.append(
                "not in these files: "
                + ", ".join(result.unanswered)
                + " -- a different generated file may carry them"
            )
        return ". ".join(parts)

    def _show(self, message: str, *, failed: bool) -> None:
        colour = theme.STATUS_FAILED if failed else theme.TEXT_SECONDARY
        self._status.setStyleSheet(
            f"color: {colour}; font-size: {theme.FONT_SIZE_META}px;"
        )
        self._status.setText(message)

    def _refresh_enabled(self) -> None:
        self._scan_btn.setEnabled(bool(self._files))
        self._accept_btn.setEnabled(bool(self.ticked()))

    # -- actions ---------------------------------------------------------

    def _on_choose(self) -> None:
        start = str(self._start_dir or Path.home())
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, "Files this project generated", start
        )
        if chosen:
            self.set_files([Path(p) for p in chosen])

    def _on_accept(self) -> None:
        chosen = self.ticked()
        if not chosen:
            return
        self.values_accepted.emit(chosen)
        self.accept()
