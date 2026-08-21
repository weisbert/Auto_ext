"""One :class:`~auto_ext.catalog.spec.OptionSpec` in, one control out.

Every widget in this module is built from the catalog and from nothing else.
There is no hand-written field list anywhere: adding a row to
``auto_ext/catalog/options.yaml`` adds a row to the form, and deleting one
removes it. That is the whole point -- the four-layer knob system it replaces
covered seven values because seven fields had been typed out by hand, and the
other hundred-odd were only reachable by editing a ``.j2``.

Control choice
--------------

============================  ==========================================
``OptionType``                widget
============================  ==========================================
``bool``                      ``QCheckBox``
``enum`` + certain / likely   ``QComboBox`` (closed list)
``enum`` + guess              ``QLineEdit`` -- see below
``int`` / ``float``           ``QLineEdit`` + numeric validator
``list``                      ``QLineEdit``, comma separated
``str`` / ``path``            ``QLineEdit``
``structural``                ``QLineEdit``, disabled (not a value)
============================  ==========================================

A guessed enum is deliberately **not** a combo box (``DECISIONS.md`` #19 and
:attr:`OptionSpec.free_input`). ``choices_confidence: guess`` means the value
set was invented on a development machine with no Cadence installation on it;
rendering that as a closed list would hand the user a menu of which roughly
half the entries are rejected by the tool, and hide the one spelling that
works. The guessed members are still shown -- as a hint beside the field and
in the tooltip -- so they cost nothing and claim nothing.

Ranges are advisory. Every ``range`` in the shipped catalog carries
``range_verified: false``, so a numeric field validates the *type* (a letter
cannot be typed into a float) but never the *bounds*: an out-of-range value is
accepted, marked amber and explained, because a guard rail somebody invented
must not be able to stop a real extraction.

Assumptions
-----------
Collected here rather than scattered through the module:

* ``LABEL_COLUMN_WIDTH`` (196) and the two-pair grid come from artboard
  ``1f``; the artboard was drawn at 1280px and the column is a maximum, not a
  minimum, so the same grid folds down to the 940px window floor.
* :data:`QUESTION_GLYPH` is ``?``. Artboard ``1f`` marks unconfirmed rows but
  does not fix the glyph; ``?`` is ASCII, present in DejaVu and in every
  fallback, and survives greyscale, which a colour-only difference does not.
* ``theme.py`` publishes no colour for "advisory warning on a form field", so
  the amber used for the confirmation marker and for an out-of-range value is
  :data:`~auto_ext.ui.theme.WARNING_TEXT_ON_WHITE` over
  ``STATUS_FILL["warning"]``, the tokens minted for warning text on a light
  surface. No new colour is introduced by this module.
* Labels are derived from ``recipe_field_path``. A curated label table would
  read better for the twenty options somebody curated and worse for the
  seventy nobody did, and it goes stale silently.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any

from PyQt5.QtCore import QLocale, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QDoubleValidator, QFont, QIntValidator
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from auto_ext.catalog import OptionSpec, OptionType
from auto_ext.ui import theme

__all__ = [
    "LABEL_COLUMN_WIDTH",
    "NEEDS_CONFIRMATION",
    "OBJ_GROUP_HEADER",
    "OBJ_OPTION_HINT",
    "OBJ_OPTION_LABEL",
    "OBJ_OPTION_UNIT",
    "OBJ_QUESTION_MARKER",
    "QUESTION_GLYPH",
    "BoolOptionEditor",
    "ChoiceOptionEditor",
    "EditorKind",
    "ElidedLabel",
    "ListOptionEditor",
    "NumberOptionEditor",
    "OptionEditor",
    "OptionGrid",
    "OptionGroup",
    "OptionLabel",
    "TextOptionEditor",
    "build_option_editor",
    "editor_kind",
    "group_label",
    "hint_text",
    "in_advisory_range",
    "option_label",
    "option_tooltip",
]

#: Maximum width of a label column. Artboard ``1f`` draws 196px; it is a cap
#: rather than a floor so the grid can still fold into a 940px window.
LABEL_COLUMN_WIDTH = 196

#: Marker on a row whose value nobody has confirmed against a real tool.
QUESTION_GLYPH = "?"

#: Tooltip prefix for such a row. Tests and screens match on this string.
NEEDS_CONFIRMATION = "NEEDS CONFIRMATION"

OBJ_OPTION_LABEL = "optionLabel"
OBJ_OPTION_HINT = "optionHint"
OBJ_OPTION_UNIT = "optionUnit"
OBJ_QUESTION_MARKER = "optionQuestionMarker"
OBJ_GROUP_HEADER = "optionGroupHeader"

#: Separator between hint parts. U+00B7, in DejaVu and in the agreed glyph set.
_DOT = " · "

#: En dash for ranges. U+2013, also in the agreed glyph set.
_DASH = "–"

#: Em dash joining a group title to its landing files.
_EM_DASH = "—"

#: Sentinel: "no explicit value, start from the catalog default".
_UNSET: Any = object()

#: Width of a numeric field. Artboard ``1f`` draws a 74px content box; 96
#: includes the frame and the padding the shared QSS adds.
_NUMBER_FIELD_WIDTH = 96

#: Pixels of slack in an elided label's size hint, so the last glyph is not
#: clipped into an ellipsis by a one-pixel rounding difference.
_ELIDE_SLACK = 2

#: Group names that are acronyms and must not be sentence-cased. Small and
#: explicit: this is a spelling table for five words, not a field list, and
#: a name missing from it renders as ``Lvs`` rather than not at all.
_ACRONYM_GROUPS = frozenset({"lvs", "dspf", "si", "qrc", "xy"})

#: Decimals a float field accepts. ``coupling_cap_threshold_absolute`` is 0.01
#: with a unit the catalog itself flags as physically impossible as written,
#: so the field must not round away a value the user typed deliberately.
_FLOAT_DECIMALS = 12


# ---- pure helpers ----------------------------------------------------------
# Everything in this section is importable and testable without a QApplication.


class EditorKind(StrEnum):
    """Which control a spec resolves to. One value per editor class."""

    CHECKBOX = "checkbox"
    COMBO = "combo"
    NUMBER = "number"
    LIST = "list"
    TEXT = "text"


def editor_kind(spec: OptionSpec) -> EditorKind:
    """Pick the control for one spec.

    The guessed-enum rule lives in :attr:`OptionSpec.free_input`, so this
    function cannot disagree with the catalog's own idea of what a closed set
    is -- it asks rather than re-deriving.
    """

    if spec.type is OptionType.BOOL:
        return EditorKind.CHECKBOX
    if spec.type is OptionType.ENUM:
        return EditorKind.TEXT if spec.free_input else EditorKind.COMBO
    if spec.type in (OptionType.INT, OptionType.FLOAT):
        return EditorKind.NUMBER
    if spec.type is OptionType.LIST:
        return EditorKind.LIST
    return EditorKind.TEXT


def option_label(spec: OptionSpec) -> str:
    """Human label: the leaf of the recipe field path, underscores removed.

    ``extraction.coupling_cap_threshold_absolute`` becomes ``coupling cap
    threshold absolute``. The group header already carries the prefix, so
    repeating it in every row would only cost width.
    """

    path = spec.recipe_field_path
    leaf = path.split(".")[-1] if path else spec.key
    return leaf.replace("_", " ")


def group_label(name: str) -> str:
    """``extraction`` -> ``Extraction``; ``lvs`` -> ``LVS``.

    Acronyms come from :data:`_ACRONYM_GROUPS`; everything else is
    sentence-cased, so ``extracted_view`` becomes ``Extracted view``.
    """

    if name.lower() in _ACRONYM_GROUPS:
        return name.upper()
    return name.replace("_", " ").capitalize()


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (list, tuple)):
        return ", ".join(_format_value(item) for item in value)
    return str(value)


def _trim_number(value: Any) -> str:
    """``100.0`` -> ``100``. For range bounds and integers only.

    A float *field* keeps its own repr instead (see :func:`_number_text`):
    the artboard shows ``55.0 C`` and ``0.01 F``, and dropping the ``.0``
    from a temperature makes it read like a count.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return str(value)


def _number_text(spec: OptionSpec, value: Any) -> str:
    """How a numeric value is spelled in its own field."""

    if value is None:
        return ""
    if spec.type is OptionType.INT:
        return _trim_number(value)
    return str(value)


def _range_text(spec: OptionSpec) -> str:
    if spec.range is None:
        return ""
    low, high = spec.range
    return f"{_trim_number(low)} {_DASH} {_trim_number(high)}"


def hint_text(spec: OptionSpec) -> str:
    """The short grey line beside a field: default, range, guessed members.

    Kept to one elided line. Everything longer lives in
    :func:`option_tooltip`, which the user reaches by hovering -- a static
    tooltip, never a hover preview, because on an X11-forwarded link a
    repaint on mouse-over is the most expensive thing a form can do.
    """

    parts: list[str] = []
    if spec.default is not None or spec.type is OptionType.BOOL:
        parts.append(f"default {_format_value(spec.default)}")
    if spec.range is not None:
        suffix = "" if spec.range_verified else " (unverified)"
        parts.append(_range_text(spec) + suffix)
    if spec.free_input and spec.choices:
        shown = [str(choice) for choice in spec.choices[:3]]
        more = "" if len(spec.choices) <= 3 else ", ..."
        parts.append("guessed: " + ", ".join(shown) + more)
    return _DOT.join(parts)


def option_tooltip(spec: OptionSpec) -> str:
    """Full explanation of one row: why it exists, where it lands, what is unknown."""

    lines = [f"{spec.key}  ({spec.type.value})", "", spec.why]
    if spec.choices:
        confidence = spec.choices_confidence.value
        lines.append(f"choices ({confidence}): " + ", ".join(str(c) for c in spec.choices))
    if spec.range is not None:
        verified = "verified" if spec.range_verified else "not verified against any datasheet"
        lines.append(f"range {_range_text(spec)} -- {verified}")
    sites = [f"{_site_file(site)} {site.option}" for site in spec.lands_in]
    if sites:
        lines.append("lands in: " + "; ".join(sites))
    if spec.notes:
        lines.append(spec.notes)
    if spec.question:
        lines.append("")
        lines.append(f"{NEEDS_CONFIRMATION}: {spec.question}")
    return "\n".join(lines)


def _site_file(site: Any) -> str:
    """Name of the file (or the stage, for the template-less strmout argv)."""

    if site.target is not None:
        return str(site.target.value)
    if site.stage is not None:
        return str(site.stage.value)
    return "?"


def in_advisory_range(spec: OptionSpec, value: Any) -> bool:
    """False only when a numeric value falls outside an advisory ``range``.

    Advisory is the operative word: the caller marks the field, it never
    refuses the value. Non-numeric specs and unset values are always in range.
    """

    if spec.range is None or value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    low, high = spec.range
    return low <= value <= high


# ---- small shared widgets --------------------------------------------------


class ElidedLabel(QLabel):
    """A label that elides instead of widening the window.

    ``minimumSizeHint`` is pinned to zero width. A grid full of these is what
    lets the Recipes screen honour the 940x560 floor from artboard ``1j``: an
    ordinary ``QLabel`` reports its full text width as a minimum and, with
    ninety of them on one page, the form alone would out-vote the window.

    The size *policy* stays ``Preferred`` rather than ``Ignored``, which is
    the difference between a label that shrinks when it has to and one that
    disappears whenever the layout gives it a stretch factor of zero. The
    inherited ``sizeHint`` still reports the full text, so a label in a
    fixed column gets the width its text asks for and elides only under
    pressure.
    """

    def __init__(
        self,
        text: str = "",
        *,
        mode: Qt.TextElideMode = Qt.ElideRight,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._apply()

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._apply()

    def full_text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """The width of the *full* text, not of whatever is on screen now.

        Without this the hint is computed from the already-elided string, the
        layout gives the label that much room, ``_apply`` elides again, and
        the label walks itself down to an ellipsis over a few resizes.
        """

        return QSize(
            self.fontMetrics().horizontalAdvance(self._full) + _ELIDE_SLACK,
            super().sizeHint().height(),
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply()

    def _apply(self) -> None:
        width = max(0, self.width())
        if width <= 0:
            super().setText(self._full)
            return
        super().setText(self.fontMetrics().elidedText(self._full, self._mode, width))


class OptionLabel(QWidget):
    """Left cell of a form row: the name, plus the confirmation marker.

    The marker is a bordered amber ``?``. It says "nobody has put this value
    through a real tool yet", which is a different claim from "this value is
    wrong" and must not borrow the failure colour -- and it is a *glyph*, so
    it survives greyscale and colour blindness, which a tinted row would not.
    """

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec = spec

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_XS)

        # Elided in the MIDDLE, not on the right. Catalog leaf names share
        # long prefixes and differ at the end -- ``coupling cap threshold
        # absolute`` against ``coupling cap threshold relative`` -- so
        # right-elision removes the one word that tells them apart.
        self._text = ElidedLabel(option_label(spec), mode=Qt.ElideMiddle, parent=self)
        self._text.setObjectName(OBJ_OPTION_LABEL)
        self._text.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        self._text.setToolTip(option_tooltip(spec))
        row.addWidget(self._text, 1)

        self._marker: QLabel | None = None
        if spec.question:
            marker = QLabel(QUESTION_GLYPH, self)
            marker.setObjectName(OBJ_QUESTION_MARKER)
            marker.setAlignment(Qt.AlignCenter)
            marker.setToolTip(f"{NEEDS_CONFIRMATION}: {spec.question}")
            marker.setStyleSheet(
                f"font-family: {theme.FONT_MONO};"
                f" font-size: {theme.FONT_SIZE_META}px;"
                f" font-weight: {theme.FONT_WEIGHT_BOLD};"
                f" color: {theme.WARNING_TEXT_ON_WHITE};"
                f" border: 1px solid {theme.STATUS_LINE['warning']};"
                f" background: {theme.STATUS_FILL['warning']};"
                f" padding: 0px {theme.SPACE_XXS}px;"
            )
            row.addWidget(marker, 0)
            self._marker = marker

        self.setMaximumWidth(LABEL_COLUMN_WIDTH)

    @property
    def spec(self) -> OptionSpec:
        return self._spec

    @property
    def needs_confirmation(self) -> bool:
        """True when the catalog row carries an unanswered ``question``."""

        return bool(self._spec.question)

    def marker(self) -> QLabel | None:
        """The ``?`` marker widget, or ``None`` on a confirmed row."""

        return self._marker

    def text_label(self) -> ElidedLabel:
        return self._text


def _mono_font(widget: QWidget) -> QFont:
    font = widget.font()
    font.setFamily(theme.FONT_MONO_FAMILIES[0])
    font.setStyleHint(QFont.TypeWriter)
    return font


def _set_text_from_start(edit: QLineEdit, text: str) -> None:
    """Set a field's text and show its *beginning*.

    ``setText`` leaves the cursor at the end, so a value wider than the field
    scrolls to its tail: at the 940px window floor ``SCHEMATIC`` renders as
    ``EMATIC`` and ``rc_coupled`` as ``oupled``, which look like different
    values rather than a truncated one.
    """

    edit.setText(text)
    edit.setCursorPosition(0)


# ---- editors ---------------------------------------------------------------


class OptionEditor(QWidget):
    """Base class: a spec, a control, a typed value and one signal.

    Subclasses implement :meth:`value` / :meth:`set_value` and call
    :meth:`_emit` when the user changes something. ``set_value`` never emits
    -- pushing state in from the model must not look like a user edit, or the
    screen marks itself dirty the moment it loads a recipe.
    """

    #: ``(catalog key, typed value)``. The value is a plain Python object:
    #: ``bool`` / ``int`` / ``float`` / ``str`` / ``list[str]`` / ``None``.
    value_changed = pyqtSignal(str, object)

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec = spec
        self._muted = False
        self._invalid = False
        self._control: QWidget | None = None
        self._unit: QLabel | None = None
        self._hint: ElidedLabel | None = None

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS
        )
        self._layout.setSpacing(theme.SPACE_XS)

    # -- construction helpers ------------------------------------------

    def _add_control(self, control: QWidget, *, stretch: int = 0) -> None:
        control.setToolTip(option_tooltip(self._spec))
        self._layout.addWidget(control, stretch)
        self._control = control

    def _add_trailing(self) -> None:
        """Unit chip and the grey hint line, in artboard order."""

        if self._spec.unit:
            unit = QLabel(self._spec.unit, self)
            unit.setObjectName(OBJ_OPTION_UNIT)
            unit.setStyleSheet(
                f"color: {theme.TEXT_DISABLED}; font-family: {theme.FONT_MONO};"
            )
            self._layout.addWidget(unit, 0)
            self._unit = unit

        hint = hint_text(self._spec)
        if not hint:
            self._layout.addStretch(1)
            return
        label = ElidedLabel(hint, parent=self)
        label.setObjectName(OBJ_OPTION_HINT)
        label.setStyleSheet(
            f"color: {theme.TEXT_DISABLED}; font-size: {theme.FONT_SIZE_META}px;"
        )
        label.setToolTip(option_tooltip(self._spec))
        self._layout.addWidget(label, 1)
        self._hint = label

    # -- public API ----------------------------------------------------

    @property
    def spec(self) -> OptionSpec:
        return self._spec

    @property
    def key(self) -> str:
        return self._spec.key

    def kind(self) -> EditorKind:
        return editor_kind(self._spec)

    def value(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def set_value(self, value: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def unit_label(self) -> QLabel | None:
        return self._unit

    def hint_label(self) -> ElidedLabel | None:
        return self._hint

    def is_advisory_ok(self) -> bool:
        """False when the current value is outside an unverified ``range``."""

        return in_advisory_range(self._spec, self.value())

    def control(self) -> QWidget | None:
        """The editable widget itself, without the unit and hint decoration."""

        return self._control

    def set_invalid(self, invalid: bool, message: str = "") -> None:
        """Mark the control rejected by the model, with the reason on hover.

        Distinct from :meth:`is_advisory_ok`, which is a soft catalog range.
        This one means the Recipe model refused the value outright, so the
        text on screen and the value in the recipe have genuinely diverged
        and the user has to be told which one is real.
        """

        self._invalid = bool(invalid)
        if self._control is None:
            return
        if not self._invalid:
            self._control.setStyleSheet("")
            self._control.setToolTip(option_tooltip(self._spec))
            return
        self._control.setStyleSheet(f"border: 1px solid {theme.STATUS_FAILED};")
        self._control.setToolTip(
            (message or "the recipe model rejected this value")
            + "\n\nThe recipe still holds the previous value.\n\n"
            + option_tooltip(self._spec)
        )

    def is_invalid(self) -> bool:
        return self._invalid

    # -- internals -----------------------------------------------------

    def _emit(self) -> None:
        if self._muted:
            return
        self.value_changed.emit(self._spec.key, self.value())

    def _quietly(self, apply) -> None:
        self._muted = True
        try:
            apply()
        finally:
            self._muted = False


class BoolOptionEditor(OptionEditor):
    """``bool`` -> a bare check box; the name lives in the left cell."""

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._box = QCheckBox(self)
        self._box.setChecked(bool(spec.default))
        self._box.toggled.connect(lambda _checked: self._emit())
        self._add_control(self._box)
        self._add_trailing()

    def check_box(self) -> QCheckBox:
        return self._box

    def value(self) -> bool:
        return self._box.isChecked()

    def set_value(self, value: Any) -> None:
        self._quietly(lambda: self._box.setChecked(bool(value)))


class ChoiceOptionEditor(OptionEditor):
    """``enum`` with a certain or likely value set -> a closed ``QComboBox``.

    An unknown incoming value is added as an extra entry rather than dropped:
    a recipe written by hand, or by an older catalog, must survive being
    opened in the editor without quietly changing meaning.
    """

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._combo = QComboBox(self)
        self._combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
        self._combo.setMinimumContentsLength(6)
        self._combo.setFont(_mono_font(self._combo))
        for choice in spec.choices or []:
            self._combo.addItem(str(choice))
        if spec.default is not None:
            index = self._combo.findText(str(spec.default))
            if index >= 0:
                self._combo.setCurrentIndex(index)
        self._combo.currentTextChanged.connect(lambda _text: self._emit())
        self._add_control(self._combo)
        self._add_trailing()

    def combo(self) -> QComboBox:
        return self._combo

    def choices(self) -> list[str]:
        return [self._combo.itemText(i) for i in range(self._combo.count())]

    def value(self) -> str:
        return self._combo.currentText()

    def set_value(self, value: Any) -> None:
        text = "" if value is None else str(value)

        def apply() -> None:
            index = self._combo.findText(text)
            if index < 0:
                self._combo.addItem(text)
                index = self._combo.count() - 1
            self._combo.setCurrentIndex(index)

        self._quietly(apply)


class TextOptionEditor(OptionEditor):
    """``str`` / ``path`` / guessed ``enum`` / ``structural`` -> a free text box.

    For a guessed enum this is DECISIONS.md #19 made visible: the members are
    offered as a hint, the default fills the placeholder, and the tool -- not
    the catalog -- gets the last word on what is legal.
    """

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._edit = QLineEdit(self)
        self._edit.setFont(_mono_font(self._edit))
        if spec.default is not None:
            self._edit.setPlaceholderText(str(spec.default))
            _set_text_from_start(self._edit, str(spec.default))
        if spec.type is OptionType.STRUCTURAL:
            self._edit.setReadOnly(True)
            self._edit.setEnabled(False)
        self._edit.textEdited.connect(lambda _text: self._emit())
        self._add_control(self._edit, stretch=1)
        self._add_trailing()

    def line_edit(self) -> QLineEdit:
        return self._edit

    def value(self) -> str:
        return self._edit.text()

    def set_value(self, value: Any) -> None:
        text = "" if value is None else str(value)
        self._quietly(lambda: _set_text_from_start(self._edit, text))


class ListOptionEditor(OptionEditor):
    """``list`` -> one comma separated text box.

    A chip editor was considered and rejected: the eight list options in the
    catalog hold view names and net names, which are typed far more often
    than they are picked, and a chip row costs one widget per member on a
    link where widget count is the thing that stutters.
    """

    SEPARATOR = ", "

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._edit = QLineEdit(self)
        self._edit.setFont(_mono_font(self._edit))
        default = spec.default if isinstance(spec.default, list) else []
        joined = self.SEPARATOR.join(str(item) for item in default)
        _set_text_from_start(self._edit, joined)
        self._edit.setPlaceholderText(joined)
        self._edit.textEdited.connect(lambda _text: self._emit())
        self._add_control(self._edit, stretch=1)
        self._add_trailing()

    def line_edit(self) -> QLineEdit:
        return self._edit

    def value(self) -> list[str]:
        return [part.strip() for part in self._edit.text().split(",") if part.strip()]

    def set_value(self, value: Any) -> None:
        if value is None:
            items: list[str] = []
        elif isinstance(value, (list, tuple)):
            items = [str(item) for item in value]
        else:
            items = [str(value)]
        self._quietly(
            lambda: _set_text_from_start(self._edit, self.SEPARATOR.join(items))
        )


class NumberOptionEditor(OptionEditor):
    """``int`` / ``float`` -> a validated text box with an advisory range.

    The validator constrains the *type* only. Bounds are painted, never
    enforced, while ``range_verified`` is false -- which it is on every row
    the catalog ships. An out-of-range value turns the field amber and says
    so in the tooltip, and is still accepted, still saved and still rendered.
    """

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._edit = QLineEdit(self)
        self._edit.setFont(_mono_font(self._edit))
        self._edit.setMaximumWidth(_NUMBER_FIELD_WIDTH)

        validator: QIntValidator | QDoubleValidator
        if spec.type is OptionType.INT:
            validator = QIntValidator(self._edit)
            if spec.range is not None and spec.range_verified:
                validator.setRange(int(spec.range[0]), int(spec.range[1]))
        else:
            validator = QDoubleValidator(self._edit)
            validator.setNotation(QDoubleValidator.StandardNotation)
            validator.setDecimals(_FLOAT_DECIMALS)
            if spec.range is not None and spec.range_verified:
                validator.setRange(
                    float(spec.range[0]), float(spec.range[1]), _FLOAT_DECIMALS
                )
        validator.setLocale(QLocale.c())
        self._edit.setLocale(QLocale.c())
        self._edit.setValidator(validator)

        if spec.default is not None:
            _set_text_from_start(self._edit, _number_text(spec, spec.default))
            self._edit.setPlaceholderText(_number_text(spec, spec.default))
        self._edit.textEdited.connect(self._on_edited)
        self._add_control(self._edit, stretch=0)
        self._add_trailing()
        self._refresh_advisory()

    def line_edit(self) -> QLineEdit:
        return self._edit

    def value(self) -> int | float | None:
        text = self._edit.text().strip()
        if not text:
            return None
        try:
            return int(text) if self._spec.type is OptionType.INT else float(text)
        except ValueError:
            return None

    def set_value(self, value: Any) -> None:
        text = _number_text(self._spec, value)
        self._quietly(lambda: _set_text_from_start(self._edit, text))
        self._refresh_advisory()

    def _on_edited(self, _text: str) -> None:
        self._refresh_advisory()
        self._emit()

    def _refresh_advisory(self) -> None:
        """Amber border when outside an advisory range; never a hard block."""

        if self.is_advisory_ok():
            self._edit.setStyleSheet("")
            self._edit.setToolTip(option_tooltip(self._spec))
            return
        self._edit.setStyleSheet(
            f"border: 1px solid {theme.STATUS_WARNING};"
            f" color: {theme.WARNING_TEXT_ON_WHITE};"
        )
        self._edit.setToolTip(
            f"outside the catalog's advisory range {_range_text(self._spec)}"
            f" -- that range is not verified, so the value is accepted as typed."
            f"\n\n" + option_tooltip(self._spec)
        )


_EDITOR_BY_KIND: dict[EditorKind, type[OptionEditor]] = {
    EditorKind.CHECKBOX: BoolOptionEditor,
    EditorKind.COMBO: ChoiceOptionEditor,
    EditorKind.NUMBER: NumberOptionEditor,
    EditorKind.LIST: ListOptionEditor,
    EditorKind.TEXT: TextOptionEditor,
}


def build_option_editor(spec: OptionSpec, parent: QWidget | None = None) -> OptionEditor:
    """The control for one spec, already carrying the catalog default."""

    return _EDITOR_BY_KIND[editor_kind(spec)](spec, parent)


# ---- grid and group --------------------------------------------------------


class OptionGrid(QWidget):
    """A grid of ``label | field`` pairs, two pairs to a row.

    Rows are appended in the order the specs arrive; the caller decides that
    order, because the catalog's own order is emission order (by line number
    in the generated file), which is the right order for the renderer and the
    wrong one for a form.
    """

    value_changed = pyqtSignal(str, object)

    def __init__(self, *, pairs_per_row: int = 2, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pairs_per_row = max(1, pairs_per_row)
        self._editors: dict[str, OptionEditor] = {}
        self._labels: dict[str, OptionLabel] = {}
        self._order: list[str] = []

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(0)
        self._grid.setVerticalSpacing(0)
        for pair in range(self._pairs_per_row):
            self._grid.setColumnStretch(pair * 2, 0)
            self._grid.setColumnStretch(pair * 2 + 1, 1)

    # -- building ------------------------------------------------------

    def add_option(self, spec: OptionSpec, value: Any = _UNSET) -> OptionEditor:
        """Append one row. ``value`` left unset keeps the catalog default."""

        index = len(self._order)
        row, pair = divmod(index, self._pairs_per_row)

        label = OptionLabel(spec, self)
        editor = build_option_editor(spec, self)
        if value is not _UNSET:
            editor.set_value(value)
        editor.value_changed.connect(self.value_changed)

        self._grid.addWidget(label, row, pair * 2)
        self._grid.addWidget(editor, row, pair * 2 + 1)
        self._editors[spec.key] = editor
        self._labels[spec.key] = label
        self._order.append(spec.key)
        return editor

    def add_options(
        self, specs: Iterable[OptionSpec], values: Mapping[str, Any] | None = None
    ) -> None:
        for spec in specs:
            if values is not None and spec.key in values:
                self.add_option(spec, values[spec.key])
            else:
                self.add_option(spec)

    # -- queries -------------------------------------------------------

    def keys(self) -> list[str]:
        return list(self._order)

    def editor(self, key: str) -> OptionEditor | None:
        return self._editors.get(key)

    def label(self, key: str) -> OptionLabel | None:
        return self._labels.get(key)

    def values(self) -> dict[str, Any]:
        return {key: editor.value() for key, editor in self._editors.items()}

    def set_value(self, key: str, value: Any) -> None:
        editor = self._editors.get(key)
        if editor is not None:
            editor.set_value(value)

    def needs_confirmation_keys(self) -> list[str]:
        return [key for key in self._order if self._labels[key].needs_confirmation]

    def option_count(self) -> int:
        return len(self._order)


class OptionGroup(QFrame):
    """One bordered card: a 22px header strip over an :class:`OptionGrid`.

    The header carries the group name and, after an em dash, the template
    files the group's options actually land in -- both read off the catalog,
    so a group whose options move to another file re-labels itself.
    """

    value_changed = pyqtSignal(str, object)

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        *,
        pairs_per_row: int = 2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"OptionGroup {{ background: {theme.SURFACE_CARD};"
            f" border: 1px solid {theme.LINE_PANEL}; }}"
        )

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        header = QFrame(self)
        header.setObjectName(OBJ_GROUP_HEADER)
        header.setFixedHeight(theme.TABLE_HEADER_HEIGHT)
        header.setStyleSheet(
            f"QFrame#{OBJ_GROUP_HEADER} {{ background: {theme.SURFACE_TABLE_HEADER};"
            f" border: none; border-bottom: 1px solid {theme.LINE_PANEL}; }}"
        )
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(theme.SPACE_SM, 0, theme.SPACE_SM, 0)
        header_row.setSpacing(theme.SPACE_XS)
        caption = title if not subtitle else f"{title} {_EM_DASH} {subtitle}"
        self._header_label = ElidedLabel(caption, parent=header)
        self._header_label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        header_row.addWidget(self._header_label, 1)
        column.addWidget(header)

        self._grid = OptionGrid(pairs_per_row=pairs_per_row, parent=self)
        self._grid.value_changed.connect(self.value_changed)
        column.addWidget(self._grid)

    @property
    def grid(self) -> OptionGrid:
        return self._grid

    def title(self) -> str:
        return self._title

    def subtitle(self) -> str:
        return self._subtitle

    def header_text(self) -> str:
        return self._header_label.full_text()

    def add_option(self, spec: OptionSpec, value: Any = _UNSET) -> OptionEditor:
        return self._grid.add_option(spec, value)

    def add_options(
        self, specs: Sequence[OptionSpec], values: Mapping[str, Any] | None = None
    ) -> None:
        self._grid.add_options(specs, values)
