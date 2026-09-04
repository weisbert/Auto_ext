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

Rows the template freezes
-------------------------
A row whose ``currently`` is ``hardcoded_literal`` has its value typed into
the ``.j2``. Binding the Recipe field changes nothing, and
:func:`auto_ext.core.render.check_representable` refuses the render rather
than write the old value and report success. That refusal is the last line of
defence and stays where it is; it is a bad *first* one, because it arrives
after the user has filled the form, saved the recipe and started a run.

So :func:`template_freezes` marks the row here instead: the control is
disabled at the catalog default, the label carries a grey ``=``, the hint line
says :data:`NOT_SETTABLE`, and the tooltip names the file the literal lives
in. Grey and not amber on purpose -- nothing is wrong with the value or with
the row, the tool simply cannot write anything else yet, and borrowing the
warning colour would put a caution mark on ninety correct fields the day
someone adds a catalog row before its template hole.

The set is empty in the shipped catalog and the point is to keep it that way:
this is the display for a row that is added before its template is
parameterised, and ``tests/catalog/test_catalog.py`` fails the moment one is.

Assumptions
-----------
Collected here rather than scattered through the module:

* The label column is ``minmax(LABEL_MIN_WIDTH, LABEL_COLUMN_WIDTH)`` and the
  grid holds at most two pairs to a line, from artboard ``M`` section 4. The
  fold to the 940px window floor is the *column count's* job, not the label's:
  below :data:`PAIR_MIN_WIDTH` per pair the grid drops to one column, and a
  label is never squeezed under its floor. The previous rule had a cap and no
  floor, which folded by deleting whole label columns.
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
    QLayout,
    QMenu,
    QPushButton,
    QWidgetAction,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from auto_ext.catalog import Confidence, Currently, OptionSpec, OptionType
from auto_ext.catalog.spec import Screen, Tier
from auto_ext.ui import theme

__all__ = [
    "FROZEN_GLYPH",
    "LABEL_COLUMN_WIDTH",
    "LABEL_MIN_WIDTH",
    "PAIR_MIN_WIDTH",
    "VALUE_WIDTH_FLOORS",
    "VALUE_WIDTH_MAX",
    "VALUE_WIDTH_MIN",
    "NEEDS_CONFIRMATION",
    "NOT_SETTABLE",
    "OBJ_FROZEN_MARKER",
    "OBJ_GROUP_HEADER",
    "OBJ_OPTION_HINT",
    "OBJ_OPTION_LABEL",
    "OBJ_OPTION_UNIT",
    "OBJ_POINTER_LINK",
    "OBJ_POINTER_VALUE",
    "OBJ_QUESTION_MARKER",
    "QUESTION_GLYPH",
    "BoolOptionEditor",
    "ChoiceOptionEditor",
    "EditorKind",
    "ElidedLabel",
    "FormComboBox",
    "FreeChoiceOptionEditor",
    "ListOptionEditor",
    "MultiChoiceOptionEditor",
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
    "frozen_reason",
    "option_tooltip",
    "template_freezes",
    "value_width",
]

#: Maximum width of a label column. Artboard ``1f`` drew 196px, on the
#: reasoning that a cap is what lets the grid fold into a 940px window. The
#: fold is now the column count's job (:data:`PAIR_MIN_WIDTH`), so the cap is
#: free to be what artboard ``C`` measures instead: 292px, the width of the
#: label column in a 436px pair at the window floor.
#:
#: 196 was not enough for the catalog's own longest names --
#: ``include parasitic cap model`` and ``coupling cap threshold absolute``
#: both elide at it, and those two elide in the middle, which is exactly
#: where the word that tells them apart lives.
LABEL_COLUMN_WIDTH = 292

#: Floor of the label column, in pixels. Artboard ``M`` section 4:
#: ``minmax(120px, 1fr)``, and the operative half is the 120.
#:
#: Without it the column has no minimum at all -- :class:`ElidedLabel` pins
#: its ``minimumSizeHint`` to zero width so the screen can honour the 940px
#: window floor -- and a squeezed grid does not elide the labels, it removes
#: them: at 1280px the whole ``Output`` section rendered as a column of
#: anonymous check boxes reading only "default off". A user who cannot see
#: which value they are changing is worse off than one who has to scroll.
#: Below this width the *column count* drops (see :class:`OptionGrid`); the
#: label is never what gives way.
LABEL_MIN_WIDTH = 120

#: Marker on a row whose value nobody has confirmed against a real tool.
QUESTION_GLYPH = "?"

#: Tooltip prefix for such a row. Tests and screens match on this string.
NEEDS_CONFIRMATION = "NEEDS CONFIRMATION"

#: Marker on a row the shipped template writes as a literal. ``=`` reads as
#: "held at one value", is ASCII like :data:`QUESTION_GLYPH` and present in
#: every fallback font, and is a different glyph in a different colour from
#: the ``?`` it may sit beside -- the two say different things and must not
#: look like one mark.
FROZEN_GLYPH = "="

#: Hint and tooltip prefix for such a row. Tests and screens match on it.
NOT_SETTABLE = "NOT SETTABLE YET"

OBJ_OPTION_LABEL = "optionLabel"
OBJ_OPTION_HINT = "optionHint"
OBJ_OPTION_UNIT = "optionUnit"
OBJ_QUESTION_MARKER = "optionQuestionMarker"
OBJ_POINTER_VALUE = "optionPointerValue"
OBJ_POINTER_LINK = "optionPointerLink"
OBJ_WAS_VALUE = theme.OBJ_WAS_VALUE
OBJ_WHY_DISABLED = theme.OBJ_WHY_DISABLED
OBJ_OPTION_ROW = theme.OBJ_OPTION_ROW
OBJ_STATE_TAG = theme.OBJ_STATE_TAG
OBJ_FROZEN_MARKER = "optionFrozenMarker"
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

#: Width below which a ``label | control | annotation`` pair stops being one
#: of two on a line. Artboard ``M`` section 4:
#: ``columns = clamp(floor(available / 370), 1, 2)``. It is the number that
#: makes :data:`LABEL_MIN_WIDTH` affordable -- the grid gives up a column
#: before it gives up a label.
PAIR_MIN_WIDTH = 370

#: Bounds of :func:`value_width`, in characters. Artboard ``M`` section 4.
VALUE_WIDTH_MIN = 4
VALUE_WIDTH_MAX = 24

#: Per-type floor for :func:`value_width`, in characters. A value set is
#: evidence of how wide a control has to be; the *absence* of one is not
#: evidence that a narrow control is enough. ``netlist.global_power_sig``
#: defaults to the empty string and holds a supply-net name, so the formula
#: sees zero characters for a field the user types ``vdd!`` into.
#:
#: ``enum`` and ``bool`` are deliberately absent: their value set is closed
#: and known, so the computed width is already the right answer.
VALUE_WIDTH_FLOORS: Mapping[OptionType, int] = {
    OptionType.STR: 12,
    OptionType.PATH: 24,
    OptionType.LIST: 16,
}

#: Floor for a numeric field with no ``range`` to measure.
_NUMBER_WIDTH_FLOOR = 6

#: Pixels a framed control spends on border and padding, over and above its
#: text. Measured against the shared QSS, which sets 3px of padding a side.
_CONTROL_CHROME = 12

#: Extra pixels a combo box spends on its drop-down arrow.
_COMBO_ARROW = 20

#: Width of the "other" field trailing a guessed member list. Narrow on
#: purpose: it is the exception, and the check boxes are the answer.
_OTHER_FIELD_WIDTH = 150

#: Pixels of slack in an elided label's size hint, so the last glyph is not
#: clipped into an ellipsis by a one-pixel rounding difference.
_ELIDE_SLACK = 2

#: Group names that are acronyms and must not be sentence-cased. Small and
#: explicit: this is a spelling table for five words, not a field list, and
#: a name missing from it renders as ``Lvs`` rather than not at all.
_ACRONYM_GROUPS = frozenset({"lvs", "dspf", "si", "qrc", "xy"})

#: What the grey hint says about a drop-down's value set, per
#: :class:`~auto_ext.catalog.spec.Confidence`. Three sentences rather than one
#: sentence and two silences: marking only the guessed lists meant that the
#: twenty rows carrying no phrase could be read either as "verified" or as
#: "nobody has labelled this one", and the form gave no way to tell which.
#: ``guess`` keeps the promise that the field still takes anything typed into
#: it, because that is the half of it a user acts on.
_LIST_CONFIDENCE: Mapping[Any, str] = {
    Confidence.CERTAIN: "confirmed list",
    Confidence.LIKELY: "list not confirmed",
    Confidence.GUESS: "guessed list - other values accepted",
}

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
    #: An ``enum`` whose value set is guessed: the members are offered, and
    #: anything else can still be typed. See :class:`FreeChoiceOptionEditor`.
    COMBO_FREE = "combo_free"
    NUMBER = "number"
    #: A ``list`` over a closed, trusted value set -> one check box per
    #: member. See :class:`MultiChoiceOptionEditor`.
    CHECKS = "checks"
    LIST = "list"
    TEXT = "text"
    #: Not a control at all: a row that names a setting this screen does not
    #: own and points at the screen that does. See
    #: :class:`PointerOptionEditor` and :attr:`Tier.ELSEWHERE`.
    POINTER = "pointer"


def editor_kind(spec: OptionSpec) -> EditorKind:
    """Pick the control for one spec.

    Whether the value set is closed lives in :attr:`OptionSpec.free_input`, so
    this function cannot disagree with the catalog's own idea of it -- it asks
    rather than re-deriving. What changes with the answer is now whether the
    combo box is *editable*, not whether there is one at all: an enum always
    gets a list to pick from.
    """

    # Ownership beats type. An ``elsewhere`` row is a real setting with a
    # real type, but not one this form may write, so it never resolves to a
    # control -- checking this first is what keeps a bool owned by the Cells
    # page from drawing an editable check box.
    if spec.tier is Tier.ELSEWHERE:
        return EditorKind.POINTER
    if spec.type is OptionType.BOOL:
        return EditorKind.CHECKBOX
    if spec.type is OptionType.ENUM:
        if not _has_a_choice_to_make(spec):
            # A drop-down that opens onto a single line is a text box wearing
            # a costume: it promises a menu, delivers one entry, and hides
            # the catalog's own open question about what else the tool takes.
            # The project already ruled on the identical shape for lists --
            # "with no members to draw, a text box is the honest control"
            # (docs/refactor/UX_VALIDATION.md) -- and an enum is that claim
            # with an arrow drawn on it. The one spelling we do know survives
            # as the field's value, so this is honest rather than empty.
            return EditorKind.TEXT
        return EditorKind.COMBO_FREE if spec.free_input else EditorKind.COMBO
    if spec.type in (OptionType.INT, OptionType.FLOAT):
        return EditorKind.NUMBER
    if spec.type is OptionType.LIST:
        # A member list to offer -> check boxes. With no ``choices`` at all
        # (netlist_view_list, the two extra supply-name lists) there is
        # nothing to draw boxes for and it stays a text field. A GUESSED
        # member list still gets boxes, plus the "other" field that keeps a
        # spelling nobody predicted reachable -- the same answer the editable
        # combo gives an enum, for the same reason.
        return EditorKind.CHECKS if spec.choices else EditorKind.LIST
    return EditorKind.TEXT


def _has_a_choice_to_make(spec: OptionSpec) -> bool:
    """True when an ``enum``'s drop-down would hold two entries or more.

    Counted the way the control counts them, not the way the catalog does:

    * a ``nullable`` row gains the explicit ``(from the profile)`` entry, and
      "resolve it for me" against one named value is a real decision;
    * a ``choices_from`` row's list arrives with the loaded ``PdkProfile``, so
      the catalog's frozen copy is one PDK's answer and says nothing about
      how long the real list is. The demo profile has one corner and the
      shipped PDK has nine -- counting the frozen list would turn the corner
      picker into a text box on exactly the machines that need it.
    """

    if spec.choices_from is not None or spec.nullable:
        return True
    return len(spec.choices or []) >= 2


def template_freezes(spec: OptionSpec) -> bool:
    """True when the shipped template types this value in as a literal.

    Asks the catalog rather than re-deriving, so the form and
    :func:`auto_ext.core.render.check_representable` can never disagree about
    which rows those are -- the two used to be the same list only by
    coincidence, and the coincidence is what let a field be editable in the
    GUI and refused at render time.
    """

    return spec.currently is Currently.HARDCODED_LITERAL


def frozen_reason(spec: OptionSpec) -> str:
    """One sentence naming where the literal lives, for hint and tooltip."""

    files = [_site_file(site) for site in spec.lands_in]
    seen: list[str] = []
    for name in files:
        if name not in seen:
            seen.append(name)
    where = ", ".join(seen) if seen else "the shipped templates"
    return (
        f"{NOT_SETTABLE}: {where} writes this value as a literal, so changing "
        "it here would be ignored. Use a manual edit on the generated file "
        "until the template is parameterised."
    )


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
        # A list default with no members reads "default (none)" rather than
        # "default " -- three ``lvs_extra_*_names`` rows default to ``[]``
        # and rendered the second, which claims a default and then says
        # nothing about it.
        return ", ".join(_format_value(item) for item in value) or "(none)"
    if value == "":
        # Two catalog rows default to the empty string. Rendering that as
        # nothing produced the hint "default " with a trailing space and no
        # information -- a blank box beside a blank hint, which is the worst
        # thing a form can show.
        return "(empty)"
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
    """The short grey line beside a field, in one vocabulary.

    Four clauses, each answering a question a first-time user asked out loud
    on the walkthrough, and each phrased so that its *absence* also means
    something:

    ``default X``
        what is in the box now, and -- unless an empty-clause follows -- what
        the generated file will carry.
    ``empty = X`` / ``unset -- X``
        what happens when the box is cleared. Only a nullable row has one, so
        a row without it always sends what it shows. That is the distinction
        the old wording lost: ``default auto`` could equally mean "``auto`` is
        the literal we send" and "nobody has chosen yet", which are different
        command files.
    ``advisory range A - B (unverified)``
        the RANGE is the guess, never the value. ``range_verified`` is false
        on every shipped row, and "(unverified)" on its own was read as a
        doubt about the number the user had typed -- the opposite of what the
        field does, which is to accept it as written.
    ``confirmed list`` / ``list not confirmed`` / ``guessed list ...``
        how far the drop-down can be trusted. Every row with a value set says
        one of the three, because when only the guessed ones were marked the
        silence on the other twenty could equally have meant "verified" or
        "nobody labelled this one".

    Kept to one elided line. Everything longer lives in
    :func:`option_tooltip`, which the user reaches by hovering -- a static
    tooltip, never a hover preview, because on an X11-forwarded link a
    repaint on mouse-over is the most expensive thing a form can do.
    """

    parts: list[str] = []
    if spec.default is not None:
        parts.append(f"default {_format_value(spec.default)}")
    if spec.nullable and spec.placeholder:
        # A row that has BOTH a default and a meaning for empty has to say
        # the second one out loud: temperature_c shows 55.0, and nothing told
        # the user that clearing the box hands the decision to the corner.
        # The fallback existed in the model and was unreachable in the only
        # place it could have been used.
        parts.append(
            f"empty = {spec.placeholder}"
            if spec.default is not None
            else f"unset {_EM_DASH} {spec.placeholder}"
        )
    if spec.range is not None:
        if spec.range_verified:
            parts.append(f"range {_range_text(spec)}")
        else:
            parts.append(f"advisory range {_range_text(spec)} (unverified)")
    if spec.choices:
        parts.append(_LIST_CONFIDENCE[spec.choices_confidence])
    if template_freezes(spec):
        # First, not last: this line elides, and the one part of it the user
        # has to read is the part that says the field does nothing.
        parts.insert(0, NOT_SETTABLE.lower())
    return _DOT.join(parts)


def option_tooltip(spec: OptionSpec) -> str:
    """Full explanation of one row: why it exists, where it lands, what is unknown."""

    lines = [f"{spec.key}  ({spec.type.value})", ""]
    if template_freezes(spec):
        lines += [frozen_reason(spec), ""]
    lines.append(spec.why)
    if spec.choices:
        confidence = spec.choices_confidence.value
        lines.append(f"choices ({confidence}): " + ", ".join(str(c) for c in spec.choices))
    if spec.range is not None:
        verified = "verified" if spec.range_verified else "not verified against any datasheet"
        lines.append(f"range {_range_text(spec)} -- {verified}")
    sites = [f"{_site_file(site)} {site.option}" for site in spec.lands_in]
    if sites:
        lines.append("lands in: " + "; ".join(sites))
    # ``notes`` is deliberately NOT here. Artboard ``M`` section 1 makes it
    # ``internal: true`` -- never rendered in any widget, tooltip or status
    # bar. It is developer archaeology addressed to whoever next edits the
    # catalog, and it was reaching the user on mouse-over: the note on
    # ``coupling_cap_threshold_absolute`` alone is 400 characters that open
    # "DEFAULT AND UNIT DISAGREE WITH PHYSICS", and several rows carry more
    # than a screen of it. ``why`` is the field written for the reader, and
    # it is above.
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


def _type_floor(spec: OptionSpec) -> int:
    """Per-type minimum for :func:`value_width`, in characters."""

    if spec.type in (OptionType.INT, OptionType.FLOAT):
        if spec.range is None:
            return _NUMBER_WIDTH_FLOOR
        return max(len(_trim_number(bound)) for bound in spec.range)
    return VALUE_WIDTH_FLOORS.get(spec.type, 0)


def value_width(spec: OptionSpec, current: Any = None) -> int:
    """How wide one control has to be, in characters. Artboard ``M`` §4.

    ``max`` over the choice members and the default (and the current value,
    when the caller has one), clamped to
    :data:`VALUE_WIDTH_MIN`--:data:`VALUE_WIDTH_MAX`, then raised to the
    row's :data:`per-type floor <VALUE_WIDTH_FLOORS>`.

    Two defects come out of the same missing idea, that a control's width is
    a property of its *type*:

    * six fields hold one character -- ``@``, ``[]``, ``/``, ``#``, ``c``,
      ``r`` -- and each got the same ~340px box as ``AG RC RE RG``;
    * an ``enum`` too narrow for its own value scrolls its inner line edit to
      the tail, so ``SCHEMATIC`` renders as ``EMATIC`` and ``rc_coupled`` as
      ``oupled``. The extraction corner, the most consequential setting on
      the screen, could not be read at the window floor.

    Computed, never authored. An override in the catalog would be the
    hand-curated exception this module exists to avoid: the value set already
    knows how wide it is, and a row whose choices grow gets a wider control
    without anybody remembering to widen it.

    Width is settled once, at build. It deliberately does not follow what the
    user types -- a field that resizes under the cursor reflows every row
    beside it, which on a forwarded X11 link is the most expensive thing a
    form can do.
    """

    samples: list[str] = [str(choice) for choice in (spec.choices or [])]
    for value in (spec.default, current):
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (list, tuple)):
            samples.append(", ".join(str(item) for item in value))
        else:
            samples.append(str(value))
    widest = max((len(sample) for sample in samples), default=0)
    if widest == 0:
        # No choices, and a default that is absent or the empty string --
        # nothing to measure. This is the ONLY case the per-type floor is
        # for. Artboard ``M`` section 4 states the floor unconditionally,
        # which contradicts artboard ``I3``: an unconditional ``str`` floor
        # of 12 gives ``device_finger_delimiter`` a twelve-character box to
        # hold ``@``, and I3 draws that row at 30px. Applying the floor only
        # where there is no evidence satisfies both -- ``@`` measures 1 and
        # clamps up to 4, while ``netlist.global_power_sig`` measures 0 and
        # gets the 12 it needs to hold a supply-net name.
        return max(VALUE_WIDTH_MIN, _type_floor(spec))
    return min(max(widest, VALUE_WIDTH_MIN), VALUE_WIDTH_MAX)


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

        self._frozen_marker: QLabel | None = None
        if template_freezes(spec):
            frozen = QLabel(FROZEN_GLYPH, self)
            frozen.setObjectName(OBJ_FROZEN_MARKER)
            frozen.setAlignment(Qt.AlignCenter)
            frozen.setToolTip(frozen_reason(spec))
            frozen.setStyleSheet(
                f"font-family: {theme.FONT_MONO};"
                f" font-size: {theme.FONT_SIZE_META}px;"
                f" font-weight: {theme.FONT_WEIGHT_BOLD};"
                f" color: {theme.TEXT_DISABLED};"
                f" border: 1px solid {theme.LINE_PANEL};"
                f" background: {theme.SURFACE_TABLE_HEADER};"
                f" padding: 0px {theme.SPACE_XXS}px;"
            )
            row.addWidget(frozen, 0)
            self._frozen_marker = frozen

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
        # The floor is the half that matters. See LABEL_MIN_WIDTH: the inner
        # label pins its own minimum to zero so the screen can reach the
        # 940px window floor, and without a minimum here the grid took that
        # literally and removed whole label columns rather than eliding them.
        self.setMinimumWidth(LABEL_MIN_WIDTH)

    @property
    def spec(self) -> OptionSpec:
        return self._spec

    @property
    def needs_confirmation(self) -> bool:
        """True when the catalog row carries an unanswered ``question``."""

        return bool(self._spec.question)

    @property
    def is_frozen(self) -> bool:
        """True when the shipped template writes this value as a literal."""

        return template_freezes(self._spec)

    def marker(self) -> QLabel | None:
        """The ``?`` marker widget, or ``None`` on a confirmed row."""

        return self._marker

    def frozen_marker(self) -> QLabel | None:
        """The ``=`` marker widget, or ``None`` on a settable row."""

        return self._frozen_marker

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


class FormComboBox(QComboBox):
    """A combo box that only answers the wheel once the user is inside it.

    Qt gives every combo ``Qt.WheelFocus`` and steps its current index on a
    wheel notch, so scrolling a long form edits whichever combo the cursor
    passes over -- no click, no keystroke, and nothing on screen naming the
    rows that moved. On the eighty-seven row Recipes form that reached the
    ``extract -type`` row, where RC-coupled against C-only is the difference
    between a real extraction and a cheap one.

    Two halves, and both are needed. ``StrongFocus`` stops the wheel from
    handing the combo focus in the first place, which is what made the very
    first notch an edit. :meth:`wheelEvent` then IGNORES the event rather
    than swallowing it: Qt propagates a wheel to the parent only while the
    receiver leaves it un-accepted, so the scroll area underneath still
    scrolls. Eating it would trade a silent edit for a form that stops dead
    wherever a combo happens to be.

    The text editors on this form never had the problem -- a ``QLineEdit``
    does not act on a wheel -- which is why the fix is a combo of its own
    rather than something on :class:`OptionEditor`.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)


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
        self._was: QLabel | None = None
        self._why: ElidedLabel | None = None
        self._frozen_override: Any = None

        self.setObjectName(OBJ_OPTION_ROW)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS
        )
        self._layout.setSpacing(theme.SPACE_XS)

    # -- construction helpers ------------------------------------------

    def _sized_to_value(self, control: QWidget) -> None:
        """Fix ``control`` at :func:`value_width` characters wide.

        Applied to the text-bearing controls only. A check box has no text of
        its own, and the numeric field keeps the artboard's own metric.
        """

        chars = value_width(self._spec)
        arrow = _COMBO_ARROW if isinstance(control, QComboBox) else 0
        digit = control.fontMetrics().horizontalAdvance("0")
        control.setFixedWidth(chars * digit + _CONTROL_CHROME + arrow)

    def _add_control(self, control: QWidget, *, stretch: int = 0) -> None:
        control.setToolTip(option_tooltip(self._spec))
        if self.is_frozen:
            # Disabled rather than hidden. The value is still what the run will
            # write, and a row that vanishes from the form reads as a setting
            # this tool does not have -- which is the misunderstanding the
            # whole catalog exists to end.
            control.setEnabled(False)
            if isinstance(control, QLineEdit):
                control.setReadOnly(True)
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
        # No tooltip. This label carried a third copy of the row's tooltip,
        # so hovering anywhere along a row could raise the same text from the
        # name, the marker, the control or the hint. The hint elides, and
        # what it elides is already in the control's tooltip beside it.
        self._layout.addWidget(label, 1)
        self._hint = label

    # -- public API ----------------------------------------------------

    @property
    def spec(self) -> OptionSpec:
        return self._spec

    @property
    def key(self) -> str:
        return self._spec.key

    @property
    def is_frozen(self) -> bool:
        """True when the shipped template writes this value as a literal.

        The control is disabled in that case and shows the catalog default,
        which is what the run will actually write.
        """

        return template_freezes(self._spec)

    def kind(self) -> EditorKind:
        return editor_kind(self._spec)

    def value(self) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        """Push a value in from the model. Never emits :attr:`value_changed`.

        A frozen row is the exception and has to be: its control shows what
        the *run will write*, which is the template's literal, not what the
        recipe happens to hold. Showing the recipe's value there would be the
        original bug wearing a disabled control -- the field would read
        ``RCWORST`` while the generated file said ``TYPICAL``. The stored value
        is not swallowed either; :meth:`frozen_override` keeps it and the row
        says so.
        """

        if self.is_frozen:
            self._note_frozen_override(value)
            return
        self._apply_value(value)

    def _apply_value(self, value: Any) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def frozen_override(self) -> Any:
        """A stored value this frozen row will not write, or ``None``.

        ``None`` also when the row is not frozen, and when the stored value is
        the literal anyway -- in both cases there is nothing to warn about.
        """

        return self._frozen_override

    def _note_frozen_override(self, value: Any) -> None:
        default = self._spec.default
        same = value == default or (value is None and default is None)
        self._frozen_override = None if same else value
        reason = frozen_reason(self._spec)
        if self._frozen_override is not None:
            reason += (
                f"\n\nThis recipe holds {_format_value(self._frozen_override)!r}. "
                f"The run will write {_format_value(default)!r} and refuse the "
                "stage rather than write the wrong value silently."
            )
        if self._control is not None:
            self._control.setToolTip(reason + "\n\n" + option_tooltip(self._spec))
        if self._hint is not None:
            self._hint.set_full_text(
                hint_text(self._spec)
                if self._frozen_override is None
                else f"{NOT_SETTABLE.lower()}{_DOT}recipe holds "
                f"{_format_value(self._frozen_override)}"
            )

    def set_row_state(self, state: str, *, was: str = "", why: str = "") -> None:
        """Mark this row promoted / changed / inapplicable, or clear it.

        One dynamic property and a repolish rather than a stylesheet per
        widget: the QSS in ``theme`` owns what each state LOOKS like, and this
        owns only which state a row is in. That is what keeps a new state from
        needing a new colour, and what stops the two channels of artboard
        ``H`` -- accent for "a person set this", amber for "we are not sure" --
        from being decided in two places.

        ``was`` is the value the row left, drawn in mono so a number is
        comparable at a glance. ``why`` is the on-row reason a row is
        disabled: the tooltip said it already, and a reason you have to hover
        to find is a reason most people never read.
        """

        if self.property("state") != state:
            self.setProperty("state", state or None)
            style = self.style()
            if style is not None:
                style.unpolish(self)
                style.polish(self)

        if was:
            if self._was is None:
                self._was = QLabel(self)
                self._was.setObjectName(OBJ_WAS_VALUE)
                self._layout.addWidget(self._was, 0)
            self._was.setText(f"was {was}")
            self._was.setVisible(True)
        elif self._was is not None:
            self._was.setVisible(False)

        if why:
            if self._why is None:
                self._why = ElidedLabel("", parent=self)
                self._why.setObjectName(OBJ_WHY_DISABLED)
                self._layout.addWidget(self._why, 1)
            self._why.set_full_text(why)
            self._why.setVisible(True)
        elif self._why is not None:
            self._why.setVisible(False)

    def row_state(self) -> str:
        return str(self.property("state") or "")

    def was_label(self) -> QLabel | None:
        return self._was

    def why_label(self) -> ElidedLabel | None:
        return self._why

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
        if self.is_frozen:  # pragma: no cover - the model is never asked
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

    def _apply_value(self, value: Any) -> None:
        self._quietly(lambda: self._box.setChecked(bool(value)))


class ChoiceOptionEditor(OptionEditor):
    """``enum`` with a certain or likely value set -> a closed ``QComboBox``.

    An unknown incoming value is added as an extra entry rather than dropped:
    a recipe written by hand, or by an older catalog, must survive being
    opened in the editor without quietly changing meaning.
    """

    #: First entry of a nullable combo. Shown instead of an empty row so the
    #: fallback reads as a choice ("resolve it for me") rather than as a value
    #: the user forgot to fill in. :meth:`value` maps it back to ``None``.
    UNSET_LABEL = "(from the profile)"

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._unset_label = self.UNSET_LABEL if spec.nullable else None
        self._combo = FormComboBox(self)
        self._combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLength)
        self._combo.setMinimumContentsLength(6)
        self._combo.setFont(_mono_font(self._combo))
        if self._unset_label is not None:
            self._combo.addItem(self._unset_label)
        for choice in spec.choices or []:
            self._combo.addItem(str(choice))
        if spec.default is not None:
            index = self._combo.findText(str(spec.default))
            if index >= 0:
                self._combo.setCurrentIndex(index)
        self._combo.currentTextChanged.connect(lambda _text: self._emit())
        self._sized_to_value(self._combo)
        self._add_control(self._combo)
        self._add_trailing()

    def combo(self) -> QComboBox:
        return self._combo

    def choices(self) -> list[str]:
        return [self._combo.itemText(i) for i in range(self._combo.count())]

    def set_source_note(self, note: str) -> None:
        """Append a clause to the grey hint saying where the list came from.

        Only ``choices_from`` rows use it, and only once a profile is loaded.
        A combo offering one item looks identical whether the source has one
        answer or the control is stuck, and the two readings lead somewhere
        very different -- the office report behind this was a corner list
        that "looked broken" under the demo profile, which has exactly one
        corner while the real PDK has nine.
        """

        if self._hint is None:
            return
        base = hint_text(self._spec)
        self._hint.set_full_text(f"{base} {_DOT} {note}" if base else note)
        self._hint.setToolTip(self._hint.full_text())

    def set_choices(self, choices: Sequence[str]) -> None:
        """Replace the offered value set, keeping the value on screen.

        This is what a ``choices_from`` row needs: the catalog's static list
        is one PDK's answer, and the form swaps in the loaded profile's own
        table (:func:`auto_ext.catalog.spec.choices_for`) once there is a
        profile. The current value survives even when the new list does not
        contain it -- a recipe naming a corner this PDK does not define must
        show that fact, not be silently retargeted at the first entry.
        """

        current = self._combo.currentText()

        def apply() -> None:
            self._combo.clear()
            if self._unset_label is not None:
                self._combo.addItem(self._unset_label)
            for choice in choices:
                self._combo.addItem(str(choice))
            self._select(current)

        self._quietly(apply)

    def value(self) -> str | None:
        text = self._combo.currentText()
        if self._unset_label is not None and text == self._unset_label:
            return None
        return text

    def _select(self, text: str) -> None:
        """Put ``text`` on screen, appending it when the list lacks it."""

        index = self._combo.findText(text)
        if index < 0:
            self._combo.addItem(text)
            index = self._combo.count() - 1
        self._combo.setCurrentIndex(index)
        # An editable combo puts the value in a QLineEdit, and setting text
        # there leaves the cursor at the end. :func:`value_width` now sizes
        # the control to its own choice set, so this only matters for a value
        # longer than any member -- but when it does, the field must clip at
        # the tail it is scrolled to, never at the head that identifies it.
        line = self._combo.lineEdit()
        if line is not None:
            line.setCursorPosition(0)

    def _apply_value(self, value: Any) -> None:
        if value is None and self._unset_label is not None:
            self._quietly(lambda: self._combo.setCurrentIndex(0))
            return
        text = "" if value is None else str(value)
        self._quietly(lambda: self._select(text))


class FreeChoiceOptionEditor(ChoiceOptionEditor):
    """``enum`` with a GUESSED value set -> an *editable* combo box.

    The catalog's ``choices_confidence: guess`` means the value set was
    invented on a machine with no Cadence on it. DECISIONS.md #19 answered
    that with a bare text box, on the reasoning that a closed list half full
    of invalid entries hides the one spelling that works. In use that traded
    one failure for a worse one: a blank box gives no idea what a legal value
    even looks like, so the user has to guess a spelling from nothing and gets
    it wrong in a way the form cannot see.

    An editable combo box is both halves at once. The members are on the
    drop-down, so there is always something correct-looking to pick; the field
    still accepts anything typed into it, so a wrong catalog guess can never
    lock the user out of the spelling their tool wants. ``NoInsert`` keeps a
    typed value out of the list itself -- it is this recipe's value, not a new
    catalog member -- and the hint line says the list is not authoritative.
    """

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        line = self._combo.lineEdit()
        if line is not None:
            line.setFont(_mono_font(line))
            if spec.default is not None:
                line.setPlaceholderText(str(spec.default))
            if self.is_frozen:
                line.setReadOnly(True)
        # An editable combo emits currentTextChanged for typing too, which the
        # base class already connects; nothing further to wire.


class TextOptionEditor(OptionEditor):
    """``str`` / ``path`` / ``structural`` -> a free text box.

    Enums no longer land here: they get a combo box, closed or editable
    (:class:`FreeChoiceOptionEditor`).
    """

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._edit = QLineEdit(self)
        self._edit.setFont(_mono_font(self._edit))
        if spec.default is not None:
            self._edit.setPlaceholderText(str(spec.default))
            _set_text_from_start(self._edit, str(spec.default))
        elif spec.placeholder:
            # No default to echo, so the grey text says what the tool does
            # with the field left alone rather than leaving it blank.
            self._edit.setPlaceholderText(spec.placeholder)
        if spec.type is OptionType.STRUCTURAL:
            self._edit.setReadOnly(True)
            self._edit.setEnabled(False)
        self._edit.textEdited.connect(lambda _text: self._emit())
        self._sized_to_value(self._edit)
        self._add_control(self._edit)
        self._add_trailing()

    def line_edit(self) -> QLineEdit:
        return self._edit

    def value(self) -> str:
        return self._edit.text()

    def _apply_value(self, value: Any) -> None:
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
        self._sized_to_value(self._edit)
        self._add_control(self._edit)
        self._add_trailing()

    def line_edit(self) -> QLineEdit:
        return self._edit

    def value(self) -> list[str]:
        return [part.strip() for part in self._edit.text().split(",") if part.strip()]

    def _apply_value(self, value: Any) -> None:
        if value is None:
            items: list[str] = []
        elif isinstance(value, (list, tuple)):
            items = [str(item) for item in value]
        else:
            items = [str(value)]
        self._quietly(
            lambda: _set_text_from_start(self._edit, self.SEPARATOR.join(items))
        )


class MultiChoiceOptionEditor(OptionEditor):
    """``list`` over a CLOSED value set -> one check box per member.

    ``stages`` is the case that forced this. It is the five stages of the
    flow, it can only ever be a subset of those five, and it was rendered as a
    comma-separated text box -- so turning Jivaro off meant knowing that the
    separator is a comma, that the spelling is ``jivaro`` and not ``Jivaro``,
    and retyping the other four without a typo. Nothing about a closed set of
    five is served by making the user spell it.

    Order is the catalog's, not the click order: for ``stages`` that is flow
    order, and the runner reads the list as a sequence. A value arriving from
    a recipe that names something outside the set gets its own check box
    appended rather than being dropped -- the same rule
    :class:`ChoiceOptionEditor` follows, and for the same reason.
    """

    #: Placeholder of the trailing free-text field on a guessed member list.
    OTHER_PLACEHOLDER = "other, comma separated"

    #: Text of the closed control. Artboard ``I1``: the row reads how many of
    #: how many are on, and nothing else.
    SUMMARY = "{on} of {total}"

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._row = QWidget(self)
        # Vertical, and off the form. Artboard ``I1``: eight members laid out
        # along the row is 81 characters of width for one value, it still
        # overflowed into a "..." button, and the row's width tracked the
        # value -- so the widest option on the screen was also the one nobody
        # reads. The members move into a popup, which OVERLAYS: opening it
        # reflows nothing, which is what the overflow button was compensating
        # for. What stays on the row is the count.
        layout = QVBoxLayout(self._row)
        layout.setContentsMargins(theme.SPACE_XS, theme.SPACE_XS, theme.SPACE_XS, theme.SPACE_XS)
        layout.setSpacing(theme.SPACE_XXS)
        # ``all`` / ``none`` first, because with eight members the common
        # edit is "everything except one" and doing that by hand is eight
        # clicks to get back to where you started. Artboard ``I1``.
        shortcuts = QWidget(self._row)
        shortcut_row = QHBoxLayout(shortcuts)
        shortcut_row.setContentsMargins(0, 0, 0, 0)
        shortcut_row.setSpacing(theme.SPACE_XS)
        self._all = QPushButton("all", shortcuts)
        self._none = QPushButton("none", shortcuts)
        for button in (self._all, self._none):
            button.setFlat(True)
            button.setEnabled(not self.is_frozen)
            shortcut_row.addWidget(button, 0)
        shortcut_row.addStretch(1)
        self._all.clicked.connect(lambda: self._set_all(True))
        self._none.clicked.connect(lambda: self._set_all(False))
        layout.addWidget(shortcuts)

        self._boxes: dict[str, QCheckBox] = {}
        default = spec.default if isinstance(spec.default, list) else []
        for choice in spec.choices or []:
            self._add_box(str(choice), str(choice) in {str(d) for d in default})
        layout.addStretch(1)

        self._other: QLineEdit | None = None
        if spec.free_input:
            other = QLineEdit(self._row)
            other.setFont(_mono_font(other))
            other.setPlaceholderText(self.OTHER_PLACEHOLDER)
            other.setMaximumWidth(_OTHER_FIELD_WIDTH)
            other.textEdited.connect(lambda _text: self._emit())
            if self.is_frozen:
                other.setReadOnly(True)
                other.setEnabled(False)
            layout.addWidget(other)
            self._other = other

        # The control on the form is the summary; the members hang off it.
        self._menu = QMenu(self)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._row)
        self._menu.addAction(action)

        self._summary = QPushButton(self)
        self._summary.setMenu(self._menu)
        self._summary.setFont(_mono_font(self._summary))
        digit = self._summary.fontMetrics().horizontalAdvance("0")
        total = len(spec.choices or [])
        self._summary.setFixedWidth(
            len(self.SUMMARY.format(on=total, total=total)) * digit
            + _CONTROL_CHROME
            + _COMBO_ARROW
        )
        self._add_control(self._summary)
        self._add_trailing()
        self._refresh_summary()

    def summary_button(self) -> QPushButton:
        """The closed control -- the thing that reads ``8 of 8``."""

        return self._summary

    def all_button(self) -> QPushButton:
        return self._all

    def none_button(self) -> QPushButton:
        return self._none

    def _set_all(self, on: bool) -> None:
        """Tick or clear every member in one go, and emit ONCE.

        Emitting per box would put eight validations and eight repaints
        through the form for one user action, and on a forwarded X11 link
        that is the difference between instant and visibly slow.
        """

        if self.is_frozen:
            return
        for box in self._boxes.values():
            box.blockSignals(True)
            try:
                box.setChecked(on)
            finally:
                box.blockSignals(False)
        self._emit()

    def _refresh_summary(self) -> None:
        on = len(self.value())
        total = len(self._boxes) + len(self._other_values())
        self._summary.setText(self.SUMMARY.format(on=on, total=max(total, on)))

    def _emit(self) -> None:
        self._refresh_summary()
        super()._emit()

    def other_edit(self) -> QLineEdit | None:
        """The trailing free-text field, or ``None`` on a closed member list."""

        return self._other

    def _other_values(self) -> list[str]:
        if self._other is None:
            return []
        return [part.strip() for part in self._other.text().split(",") if part.strip()]

    def _add_box(self, name: str, checked: bool) -> QCheckBox:
        box = QCheckBox(name, self._row)
        box.setChecked(checked)
        box.toggled.connect(lambda _on: self._emit())
        if self.is_frozen:
            box.setEnabled(False)
        layout = self._row.layout()
        # Before the trailing stretch, so a late member joins the row rather
        # than being pushed off the end of it.
        layout.insertWidget(layout.count() - 1 if layout.count() else 0, box)
        self._boxes[name] = box
        return box

    def check_boxes(self) -> dict[str, QCheckBox]:
        return dict(self._boxes)

    def value(self) -> list[str]:
        checked = [name for name, box in self._boxes.items() if box.isChecked()]
        # Typed members come after the catalog's, and never twice.
        return checked + [v for v in self._other_values() if v not in self._boxes]

    def _apply_value(self, value: Any) -> None:
        if value is None:
            wanted: list[str] = []
        elif isinstance(value, (list, tuple)):
            wanted = [str(item) for item in value]
        else:
            wanted = [str(value)]
        known = set(self._spec.choices or [])
        extra = [v for v in wanted if v not in {str(k) for k in known}]

        def apply() -> None:
            if self._other is None:
                # No free field to hold them: an unknown member gets its own
                # box rather than being dropped. A recipe written by hand must
                # not quietly change meaning by being opened here.
                for name in extra:
                    if name not in self._boxes:
                        self._add_box(name, True)
                extras_here: set[str] = set()
            else:
                _set_text_from_start(self._other, ", ".join(extra))
                extras_here = set(extra)
            for name, box in self._boxes.items():
                box.setChecked(name in set(wanted) - extras_here)

        self._quietly(apply)
        # ``_quietly`` mutes ``_emit``, and ``_emit`` is what keeps the count
        # honest -- so a value pushed in from the model would leave the closed
        # control reading the previous recipe's count.
        self._refresh_summary()


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
        elif spec.placeholder:
            self._edit.setPlaceholderText(spec.placeholder)
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

    def _apply_value(self, value: Any) -> None:
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


class PointerOptionEditor(OptionEditor):
    """A read-only row naming a setting another screen owns.

    Draws the catalog default and a link saying where the real control is,
    e.g. ``av_extracted   set per cell, not per recipe -- open the Cells
    column``. Artboard ``A`` draws exactly this.

    It binds to nothing. ``elsewhere`` rows carry no ``recipe_field_path``
    (they are not Recipe fields), so there is no value to read or write here
    and :meth:`value` answers with the catalog default, unchanging. It never
    emits ``value_changed`` -- a form that reported an edit for a row it does
    not own would put a star in the title bar that no Save could clear.

    The whole reason it exists is discoverability. The office report was "I
    cannot find where to rename the Quantus output view": the setting was
    per-cell and correctly lived on the Cells page, and a person editing a
    recipe had no way to learn that. Ownership was never the bug.
    """

    #: ``(screen, option key)`` -- the screen to open and the row to select.
    navigate_requested = pyqtSignal(str, str)

    def __init__(self, spec: OptionSpec, parent: QWidget | None = None) -> None:
        super().__init__(spec, parent)
        self._value = spec.default

        self._shown = ElidedLabel(_display_default(spec), parent=self)
        self._shown.setObjectName(OBJ_POINTER_VALUE)
        self._shown.setEnabled(False)
        self._add_control(self._shown, stretch=0)

        self._link = ElidedLabel(_pointer_text(spec), parent=self)
        self._link.setObjectName(OBJ_POINTER_LINK)
        self._link.setCursor(Qt.PointingHandCursor)
        self._link.setToolTip(
            f"{_pointer_text(spec)} — click to open that screen"
        )
        self._link.mouseReleaseEvent = self._on_link_clicked  # type: ignore[method-assign]
        self._add_control(self._link, stretch=1)
        self._add_trailing()

    def _on_link_clicked(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.navigate_requested.emit(str(self.spec.screen), self.spec.key)
            event.accept()

    @property
    def kind(self) -> EditorKind:
        return EditorKind.POINTER

    def value(self) -> Any:
        return self._value

    def _apply_value(self, value: Any) -> None:
        # Kept so the base class contract holds; the row is never written to
        # by the form, and a host pushing a value in only changes what the
        # row *reports* the other screen holds.
        self._value = value
        self._shown.set_full_text("" if value is None else str(value))

    def link_label(self) -> ElidedLabel:
        """The clickable half, for tests and for the focus-detail pane."""

        return self._link


def _display_default(spec: OptionSpec) -> str:
    value = spec.default
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "—"
    return str(value)


def _pointer_text(spec: OptionSpec) -> str:
    """Where the real control is. One sentence, per owning screen."""

    return _POINTER_TEXT.get(
        spec.screen, f"owned by another screen — {spec.screen}"
    )


#: Keyed by :class:`Screen` so a new owning screen is one entry, not a
#: condition in the widget. ``RECIPES`` has no entry: a recipes-owned row is
#: never a pointer row, it is a control.
_POINTER_TEXT: dict[Screen, str] = {
    Screen.CELLS: "set per cell, not per recipe — open the Cells column",
    Screen.PROJECT: "set once per project — open the Project page",
}


_EDITOR_BY_KIND: dict[EditorKind, type[OptionEditor]] = {
    EditorKind.CHECKBOX: BoolOptionEditor,
    EditorKind.COMBO: ChoiceOptionEditor,
    EditorKind.COMBO_FREE: FreeChoiceOptionEditor,
    EditorKind.NUMBER: NumberOptionEditor,
    EditorKind.CHECKS: MultiChoiceOptionEditor,
    EditorKind.LIST: ListOptionEditor,
    EditorKind.TEXT: TextOptionEditor,
    EditorKind.POINTER: PointerOptionEditor,
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
        #: Full-width rows that are not a single control. See :meth:`add_span`.
        self._spans: dict[str, QWidget] = {}
        self._span_labels: dict[str, QLabel] = {}
        self._order: list[str] = []
        #: Rows hidden by the density mode. They keep their widgets and their
        #: values -- hiding is a view, never an edit.
        self._hidden: set[str] = set()

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(0)
        self._grid.setVerticalSpacing(0)
        # Without this the grid pins the widget's own minimumSize to the
        # minimum of the shape it currently holds -- two columns' worth -- and
        # a widget that cannot be made narrower than two columns can never be
        # told to fold to one. :meth:`minimumSizeHint` is what should answer
        # that question, and it only gets asked once the layout stops
        # answering it first.
        self._grid.setSizeConstraint(QLayout.SetNoConstraint)
        #: Columns the last relayout used. Starts at the requested maximum so
        #: a grid that is never shown still reports the shape it was asked
        #: for -- ``tests/ui`` build grids without ever resizing them.
        self._columns = self._pairs_per_row
        self._apply_column_stretch()

    def _apply_column_stretch(self) -> None:
        for pair in range(self._pairs_per_row):
            self._grid.setColumnStretch(pair * 2, 0)
            self._grid.setColumnStretch(pair * 2 + 1, 1)

    # -- building ------------------------------------------------------

    def add_option(self, spec: OptionSpec, value: Any = _UNSET) -> OptionEditor:
        """Append one row. ``value`` left unset keeps the catalog default."""

        index = len(self.visible_keys())
        row, pair = divmod(index, self._columns)

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

    def add_span(self, key: str, label_text: str, widget: QWidget) -> QWidget:
        """Append one full-width row: a label, then ``widget`` across the rest.

        For a value that is not a single control -- the repeating ``extract``
        sub-form is the only one today. It goes through the grid rather than
        beside it so density, section membership and the focus machinery all
        treat it like any other row; a widget bolted on outside the grid would
        be the one thing on the form the mode toggle could not hide.
        """

        label = QLabel(label_text, self)
        label.setObjectName(OBJ_OPTION_LABEL)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._spans[key] = widget
        self._span_labels[key] = label
        self._order.append(key)
        self._relayout(self._columns, force=True)
        return widget

    def span(self, key: str) -> QWidget | None:
        return self._spans.get(key)

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
        # Spans have no OptionLabel and carry no per-row confirmation mark:
        # the sub-form's own rows do that. Walking ``_order`` blindly here
        # was a KeyError the moment a span joined it.
        return [
            key
            for key in self._order
            if key in self._labels and self._labels[key].needs_confirmation
        ]

    def frozen_keys(self) -> list[str]:
        """Rows the shipped templates write as a literal, in form order.

        Empty in the shipped catalog. A screen shows the count so a build that
        grows one says so on the page rather than only in a tooltip.
        """

        return [
            key
            for key in self._order
            if key in self._editors and self._editors[key].is_frozen
        ]

    def frozen_overrides(self) -> dict[str, Any]:
        """``{key: stored value}`` for frozen rows the recipe disagrees with.

        These are exactly what ``check_representable`` will refuse, named
        before the user starts a run instead of after.
        """

        return {
            key: self._editors[key].frozen_override()
            for key in self._order
            if key in self._editors
            and self._editors[key].frozen_override() is not None
        }

    def option_count(self) -> int:
        return len(self._order)

    # -- responsive shape ----------------------------------------------

    def columns(self) -> int:
        """Pairs per row the last relayout used."""

        return self._columns

    def columns_for_width(self, width: int) -> int:
        """``clamp(floor(width / PAIR_MIN_WIDTH), 1, pairs_per_row)``.

        Artboard ``M`` section 4. This is the mechanism that lets
        :data:`LABEL_MIN_WIDTH` be a promise rather than a wish: when the
        grid runs out of width it drops to one pair per row and gives that
        pair the whole line, instead of squeezing two label columns until
        both disappear. Widening past ``2`` is deliberately not offered --
        artboard ``D`` spends surplus width on the detail pane, because a
        third column of 24px rows is a spreadsheet, not a form.
        """

        return max(1, min(self._pairs_per_row, width // PAIR_MIN_WIDTH))

    def visible_keys(self) -> list[str]:
        """Rows currently drawn, in form order."""

        return [key for key in self._order if key not in self._hidden]

    def set_row_visible(self, key: str, visible: bool) -> None:
        """Show or hide one row, closing the gap it leaves behind.

        Hiding the two widgets is not enough on its own: a ``QGridLayout``
        keeps the cell, so a hidden row in the left column leaves its
        right-hand neighbour stranded a line below where it belongs. The
        surviving rows are re-flowed instead.
        """

        if key not in self._editors and key not in self._spans:
            return
        if visible:
            self._hidden.discard(key)
        else:
            self._hidden.add(key)
        if key in self._spans:
            self._span_labels[key].setVisible(visible)
            self._spans[key].setVisible(visible)
        else:
            self._labels[key].setVisible(visible)
            self._editors[key].setVisible(visible)
        self._relayout(self._columns, force=True)

    def _relayout(self, columns: int, *, force: bool = False) -> None:
        if columns == self._columns and not force:
            return
        self._columns = columns
        # ``pair`` is the column slot the next ordinary row takes; a span
        # takes a whole line, so it flushes the current line first and starts
        # a fresh one after itself. Letting a span share a line with a control
        # would put a three-row sub-form beside a one-line combo and leave the
        # combo floating in the middle of it.
        row = 0
        pair = 0
        for key in self.visible_keys():
            if key in self._spans:
                if pair:
                    row += 1
                    pair = 0
                self._grid.addWidget(self._span_labels[key], row, 0)
                self._grid.addWidget(
                    self._spans[key], row, 1, 1, max(columns * 2 - 1, 1)
                )
                row += 1
                continue
            self._grid.addWidget(self._labels[key], row, pair * 2)
            self._grid.addWidget(self._editors[key], row, pair * 2 + 1)
            pair += 1
            if pair >= columns:
                row += 1
                pair = 0
        for pair in range(self._pairs_per_row):
            # Columns past the current count hold nothing and must not be
            # handed any of the width.
            live = pair < columns
            self._grid.setColumnStretch(pair * 2, 0)
            self._grid.setColumnStretch(pair * 2 + 1, 1 if live else 0)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """The width of ONE pair, never of however many are on a line now.

        Without this the fold can never happen. ``QGridLayout`` reports the
        minimum of the shape it currently holds, so a two-column grid asks
        for two columns' worth; the parent layout honours that and never
        hands the widget a narrow geometry, so :meth:`resizeEvent` never sees
        a width small enough to drop a column. The grid has to advertise what
        it can shrink *to*, not what it happens to be.
        """

        base = super().minimumSizeHint()
        widest = 0
        for key in self._order:
            editor = self._editors.get(key) or self._spans.get(key)
            if editor is None:  # pragma: no cover - every key is one or other
                continue
            widest = max(widest, editor.minimumSizeHint().width())
        return QSize(min(base.width(), LABEL_MIN_WIDTH + widest), base.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._relayout(self.columns_for_width(self.width()))


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
        self._band = QLabel("", header)
        self._band.setObjectName(theme.OBJ_SEARCH_BAND)
        self._band.setVisible(False)
        header_row.addWidget(self._band, 0)
        column.addWidget(header)

        self._grid = OptionGrid(pairs_per_row=pairs_per_row, parent=self)
        self._grid.value_changed.connect(self.value_changed)
        column.addWidget(self._grid)

    def set_band(self, band: str) -> None:
        """Label this group with the search band its visible rows fall in.

        On the group header rather than on a container of its own: artboard
        ``J`` wants results banded, and re-parenting rows into three new
        boxes would make a row change parent depending on how the user got to
        it -- which the grouping rules forbid, because the mode toggle's
        "keep the focused row" behaviour depends on parents being stable.
        """

        self._band.setText(band)
        self._band.setVisible(bool(band))

    def band_text(self) -> str:
        return self._band.text() if self._band.isVisible() else ""

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
