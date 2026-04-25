"""One-knob editor row for the Templates tab's Knobs page.

Picks a typed widget per :class:`auto_ext.core.manifest.KnobSpec`:
``QCheckBox`` for bool, ``QLineEdit`` with a numeric validator for
int/float, plain ``QLineEdit`` for str. A ``[reset]`` button next to
the widget clears the project-level override so the manifest default
takes back over; emitting ``None`` is the contract for "no override".

The tab owns the controller stage/save plumbing — this widget only
emits :pyattr:`value_changed(name, value)` and lets the tab decide
what to stage. ``set_value`` is silent (does not emit) so the tab
can rebuild rows without echoing edits back into the controller.
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QDoubleValidator, QIntValidator
from PyQt5.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from auto_ext.core.manifest import KnobSpec


class KnobEditor(QWidget):
    """Typed widget + reset button bound to one :class:`KnobSpec`."""

    #: Emitted when the user mutates the value. ``value`` is the typed
    #: Python object to stage as the new project override, or ``None``
    #: to mean "remove the project override and fall back to manifest
    #: default" (the [reset] button or an emptied numeric field).
    value_changed = pyqtSignal(str, object)

    def __init__(self, name: str, spec: KnobSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._spec = spec
        self._silent = False  # set during set_value to suppress emit

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._checkbox: QCheckBox | None = None
        self._line: QLineEdit | None = None

        if spec.type == "bool":
            self._checkbox = QCheckBox(self)
            self._checkbox.toggled.connect(self._on_bool_toggled)
            layout.addWidget(self._checkbox)
        else:
            self._line = QLineEdit(self)
            self._line.setPlaceholderText("(default)")
            if spec.type == "int":
                v = QIntValidator(self)
                if spec.range is not None:
                    v.setRange(int(spec.range[0]), int(spec.range[1]))
                self._line.setValidator(v)
            elif spec.type == "float":
                v = QDoubleValidator(self)
                if spec.range is not None:
                    v.setRange(float(spec.range[0]), float(spec.range[1]), 6)
                self._line.setValidator(v)
            self._line.editingFinished.connect(self._on_line_finished)
            layout.addWidget(self._line, stretch=1)

        self._reset_btn = QPushButton("reset", self)
        self._reset_btn.setToolTip("Remove the project override and use the manifest default")
        self._reset_btn.setMaximumWidth(60)
        self._reset_btn.clicked.connect(self._on_reset_clicked)
        layout.addWidget(self._reset_btn)

        self._hint = QLabel("", self)
        self._hint.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self._hint)

    @property
    def name(self) -> str:
        return self._name

    @property
    def spec(self) -> KnobSpec:
        return self._spec

    def set_value(self, value: Any, *, is_default: bool) -> None:
        """Render ``value`` without emitting a signal.

        ``is_default=True`` means the project layer has no override and
        ``value`` is the manifest default; the [reset] button is
        disabled and the trailing hint is cleared. ``is_default=False``
        means the project explicitly overrode the manifest default;
        the hint shows ``(default: <X>)`` so the user can compare and
        the [reset] button is enabled.
        """
        self._silent = True
        try:
            if self._checkbox is not None:
                self._checkbox.setChecked(bool(value))
            elif self._line is not None:
                self._line.setText(_fmt(value))
            self._reset_btn.setEnabled(not is_default)
            if is_default:
                hint_parts: list[str] = []
                if self._spec.unit:
                    hint_parts.append(self._spec.unit)
                if self._spec.range is not None:
                    hint_parts.append(
                        f"range: [{self._spec.range[0]}, {self._spec.range[1]}]"
                    )
                self._hint.setText(" · ".join(hint_parts))
            else:
                self._hint.setText(f"(default: {_fmt(self._spec.default)})")
        finally:
            self._silent = False

    # ---- private slots -----------------------------------------------

    def _on_bool_toggled(self, checked: bool) -> None:
        if self._silent:
            return
        self.value_changed.emit(self._name, bool(checked))

    def _on_line_finished(self) -> None:
        if self._silent or self._line is None:
            return
        text = self._line.text().strip()
        if text == "":
            self.value_changed.emit(self._name, None)
            return
        try:
            if self._spec.type == "int":
                value: Any = int(text)
            elif self._spec.type == "float":
                value = float(text)
            else:
                value = text
        except ValueError:
            # Validator should have caught this; if the user pasted
            # bad input, drop it silently rather than stage garbage.
            return
        self.value_changed.emit(self._name, value)

    def _on_reset_clicked(self) -> None:
        self.value_changed.emit(self._name, None)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
