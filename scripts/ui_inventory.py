"""Dump every control the GUI puts in front of a user, as plain text.

Why
---
The first real office session found eight defects in one sitting, and the two
worst classes of them are invisible to the test suite by construction:

* **unreachable** -- a setting with no control at all. ``extraction.corner``
  had none, and the screen's own docstring documented the omission as
  deliberate, so nobody was going to notice by reading the code.
* **unusable** -- a control that exists and is the wrong kind. ``stages`` was
  asked for as a comma-separated string; fourteen options with a closed set of
  legal spellings were asked for as blank text boxes. Both are reachable, both
  pass every assertion, and both are unanswerable by a user who does not
  already know the answer.

``tests/ui/test_reachability.py`` catches the first class mechanically. The
second is a judgement about what a user can be expected to know, and no
assertion catches it -- it needs someone who does not already know the system
to try to use it and report where they got stuck.

This script produces the input for that: the full control inventory, with no
source code attached. Hand the output to a reviewer -- or to an agent briefed
as a first-time user with a concrete task and no repository access -- and ask
what they cannot figure out. Everything they name is a real defect, because
the inventory is literally all a user has.

Why the dump says more than "what is on the screen"
---------------------------------------------------
The 2026-09-04 walkthrough ran the previous version of this script and got
stuck twelve times on things the *instrument* could not see. A dump that names
the widgets but not their state cannot answer the one question the whole
exercise is about -- "I clicked it and nothing happened" -- so the reviewer
kept having to guess:

* rows whose editor is ``None`` were skipped **silently**, and the extract
  rules are exactly such a row (a span, not an editor), so the single most
  consequential setting in the tool dumped as an empty heading;
* only ``QPushButton`` was collected, so the entire run bar -- a combo, five
  checkboxes, a spin box and a tool button -- was absent;
* there was no Runs dumper at all, although runs are first-class objects with
  a lifecycle, a report, a log and a star;
* the Cells dump printed the column *schema* and no rows, so it was
  byte-identical between an empty project and a real one;
* every row was printed regardless of the density the screen is actually in,
  so the reader saw 87 rows where the app shows 21;
* ``DISABLED`` was printed with no reason, although the code already computes
  one and writes it into the tooltip;
* nothing said whether a control is *visible*, how many receivers its primary
  signal has, how far its scroll area can actually scroll, or which two
  controls on a screen carry the same words.

So every control now reports ``enabled``, ``visible`` and ``receivers``; every
disabled control reports the reason it carries; every scroll area reports its
range (a range of 0 is what turns a labelled "scroll to it" button into
decoration); every list reports rows/selected/checked; and each screen ends
with a duplicate-label index. ``--click-probe`` adds the cheapest possible
mechanisation of the original complaint: click each control in a throwaway
copy of the project and list the ones whose click leaves the dump byte for
byte the same.

Usage
-----
::

    python scripts/ui_inventory.py                       # empty project
    python scripts/ui_inventory.py --config-dir <dir>    # a real one
    python scripts/ui_inventory.py --screen recipes
    python scripts/ui_inventory.py --screen recipes --density all
    python scripts/ui_inventory.py --screen cells --mode running
    python scripts/ui_inventory.py --screen runs --runs-root <dir>
    python scripts/ui_inventory.py --config-dir <dir> --click-probe

Runs off-screen (``QT_QPA_PLATFORM=offscreen``), so it needs no display and is
safe over a slow X11 link or in CI. ``--click-probe`` never touches the
project it was pointed at: it copies ``config/`` and ``recipes/`` into a
temporary directory first, because a probe that clicks *Save* on the real
project would rewrite the files the reviewer is reading about.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The hints carry "·" and "—". On a Windows console the default codepage
# mangles both, and this output exists to be read by someone judging whether
# the wording is clear -- so it must arrive as written.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: A window has to be *shown* before Qt will answer ``isVisible()`` with
#: anything but ``False``, and "is this control on the screen or merely in the
#: tree" is half of what this dump exists to say. The offscreen platform makes
#: that free.
_WINDOW_SIZE = (1440, 900)

#: Screens whose dump is a page of the shell, in the order the nav rail lists
#: them. ``setup`` is the drawer and ``menus`` is the menu bar; neither is a
#: page, and both were missing from the inventory a reviewer was handed.
_PAGE_SCREENS = ("cells", "recipes", "runs", "project")


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------


@dataclass
class Options:
    """Everything the dump depends on, so a dump can be reproduced from it."""

    config_dir: Path | None = None
    auto_ext_root: Path | None = None
    runs_root: Path | None = None
    #: ``common`` or ``all`` -- which half of the Recipes form is drawn.
    density: str = "common"
    #: ``wide``, ``compact`` or ``running`` -- which Cells columns are drawn.
    mode: str = "wide"


# ---------------------------------------------------------------------------
# Controls: what a user can press, and whether pressing it can do anything
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Control:
    """One thing a user can press, with the state that decides what it does."""

    kind: str
    label: str
    enabled: bool
    visible: bool
    #: Receivers on the control's primary signal. ``0`` on a control that is
    #: visible and enabled is the exact shape of "styled like a link, does
    #: nothing when clicked".
    receivers: int
    #: What the control says about itself when it is off. The codebase writes
    #: real sentences into these tooltips, so a blank field here is a control
    #: that is greyed out and refuses to say why.
    reason: str = ""
    #: Extra facts worth one line: a combo's items, a checkbox's state.
    detail: str = ""

    def line(self) -> str:
        state = "enabled" if self.enabled else "DISABLED"
        seen = "visible" if self.visible else "hidden"
        head = f"    [{self.kind}] {self.label}"
        tail = f"{state} · {seen} · receivers {self.receivers}"
        out = f"{head:<52} {tail}"
        if self.detail:
            out += f"\n        {self.detail}"
        if not self.enabled:
            out += f"\n        disabled because: {self.reason}"
        return out


def _widget_classes() -> dict[str, Any]:
    """Qt classes, imported late so ``--help`` does not need a QApplication."""

    from PyQt5.QtWidgets import (
        QAbstractButton,
        QAbstractItemView,
        QAbstractSpinBox,
        QCheckBox,
        QComboBox,
        QLineEdit,
        QPlainTextEdit,
        QPushButton,
        QRadioButton,
        QTextEdit,
        QToolButton,
        QWidget,
    )

    return {
        "QAbstractButton": QAbstractButton,
        "QAbstractItemView": QAbstractItemView,
        "QAbstractSpinBox": QAbstractSpinBox,
        "QCheckBox": QCheckBox,
        "QComboBox": QComboBox,
        "QLineEdit": QLineEdit,
        "QPlainTextEdit": QPlainTextEdit,
        "QPushButton": QPushButton,
        "QRadioButton": QRadioButton,
        "QTextEdit": QTextEdit,
        "QToolButton": QToolButton,
        "QWidget": QWidget,
    }


def _control_kind(widget: object) -> str | None:
    """The words a user would use for this widget, or ``None`` to skip it."""

    q = _widget_classes()
    from auto_ext.ui.widgets.failure_chip import PathLabel

    if isinstance(widget, q["QAbstractItemView"]):
        # A list or a table is not a control a user presses; it gets its own
        # rows/selected/checked line further down.
        return None
    if isinstance(widget, PathLabel):
        return "path link"
    if isinstance(widget, q["QCheckBox"]):
        return "checkbox"
    if isinstance(widget, q["QRadioButton"]):
        return "radio button"
    if isinstance(widget, q["QToolButton"]):
        return "tool button (menu)" if widget.menu() else "tool button"
    if isinstance(widget, q["QPushButton"]):
        return "toggle button" if widget.isCheckable() else "button"
    if isinstance(widget, q["QAbstractButton"]):
        return "button"
    if isinstance(widget, q["QComboBox"]):
        return "dropdown (editable)" if widget.isEditable() else "dropdown (closed)"
    if isinstance(widget, q["QAbstractSpinBox"]):
        return "number spinner"
    if isinstance(widget, q["QLineEdit"]):
        return "text box (read-only)" if widget.isReadOnly() else "text box"
    if isinstance(widget, (q["QPlainTextEdit"], q["QTextEdit"])):
        return "multi-line text box"
    # Anything else that carries a ``clicked`` signal of its own is a control
    # a user can press even though Qt does not call it a button -- the
    # navigation rail, the health badge and the recipe cards are all this.
    if _bound_signal(widget, "clicked") is not None:
        return f"clickable {type(widget).__name__}"
    return None


def _bound_signal(obj: object, name: str) -> Any | None:
    """``obj.<name>`` when it is a bound Qt signal, else ``None``."""

    signal = getattr(obj, name, None)
    if signal is None:
        return None
    # A bound pyqtSignal answers ``connect``; a plain attribute does not.
    return signal if callable(getattr(signal, "connect", None)) else None


#: The signals one gesture on each kind of control can emit. More than one,
#: because a line edit is wired through ``textEdited`` in one place and
#: ``editingFinished`` in another, and counting only the first would report a
#: perfectly live filter box as dead.
_GESTURE_SIGNALS: dict[str, tuple[str, ...]] = {
    "QAbstractButton": ("clicked", "toggled"),
    "QComboBox": ("currentIndexChanged", "activated", "currentTextChanged"),
    "QAbstractSpinBox": ("valueChanged", "editingFinished"),
    "QLineEdit": ("textEdited", "textChanged", "editingFinished", "returnPressed"),
    "QPlainTextEdit": ("textChanged",),
    "QTextEdit": ("textChanged",),
}


def _gesture_signals(widget: object) -> list[Any]:
    """Every signal the user's own gesture on ``widget`` can emit."""

    q = _widget_classes()
    for name, signals in _GESTURE_SIGNALS.items():
        if isinstance(widget, q[name]):
            return [s for s in (_bound_signal(widget, n) for n in signals) if s]
    clicked = _bound_signal(widget, "clicked")
    return [clicked] if clicked else []


def _primary_signal(widget: object) -> Any | None:
    """Kept for callers that want one signal: the first gesture signal."""

    signals = _gesture_signals(widget)
    return signals[0] if signals else None


def _receivers(widget: object, signal: Any | None) -> int:
    """``QObject.receivers`` for one signal; ``-1`` when Qt will not say."""

    if signal is None:
        return -1
    try:
        return int(widget.receivers(signal))  # type: ignore[attr-defined]
    except (TypeError, RuntimeError):  # pragma: no cover - overload trouble
        return -1


def _gesture_receivers(widget: object) -> int:
    """The most receivers any one gesture signal of ``widget`` has.

    ``0`` on a control that is visible and enabled is the shape of "styled
    like a link, cursor turns into a hand, clicking does nothing".
    """

    counts = [_receivers(widget, signal) for signal in _gesture_signals(widget)]
    return max(counts) if counts else -1


def _control_label(widget: object) -> str:
    """The words on the control, or the best identifier a user could quote."""

    q = _widget_classes()
    text = ""
    getter = getattr(widget, "text", None)
    if callable(getter):
        try:
            text = str(getter() or "")
        except TypeError:  # pragma: no cover - text(int) overloads
            text = ""
    if isinstance(widget, q["QComboBox"]):
        text = ""
    if text.strip():
        return repr(text)
    name = widget.objectName()  # type: ignore[attr-defined]
    if name:
        return f"(objectName {name})"
    accessible = widget.accessibleName()  # type: ignore[attr-defined]
    if accessible:
        return f"({accessible})"
    placeholder = getattr(widget, "placeholderText", None)
    if callable(placeholder) and placeholder():
        return f"(placeholder {placeholder()!r})"
    nearby = _sibling_label(widget)
    if nearby:
        return f"(the control beside {nearby!r})"
    tip = widget.toolTip()  # type: ignore[attr-defined]
    if tip:
        return f"(tooltip {tip.splitlines()[0]!r})"
    return "(unlabelled)"


def _sibling_label(widget: object) -> str:
    """Text of the nearest label in front of ``widget`` under the same parent.

    A combo box carries no words of its own, so a dump that gave up on it
    left the reviewer with an anonymous dropdown -- and the run bar is four
    such controls in a row. The caption beside it is what a user reads.
    """

    from PyQt5.QtWidgets import QLabel

    parent = widget.parent()  # type: ignore[attr-defined]
    if parent is None:
        return ""
    siblings = parent.children()
    if widget not in siblings:  # pragma: no cover - reparented mid-walk
        return ""
    for sibling in reversed(siblings[: siblings.index(widget)]):
        if isinstance(sibling, QLabel) and sibling.text().strip():
            return sibling.text().strip()
        full = getattr(sibling, "full_text", None)
        if callable(full) and str(full()).strip():
            return str(full()).strip()
    return ""


def _control_detail(widget: object) -> str:
    """One line of extra fact: what a dropdown offers, what a box is set to."""

    q = _widget_classes()
    if isinstance(widget, q["QComboBox"]):
        items = [widget.itemText(i) for i in range(widget.count())]
        return f"offers {items} · showing {widget.currentText()!r}"
    if isinstance(widget, q["QAbstractButton"]) and widget.isCheckable():
        return f"checked: {'yes' if widget.isChecked() else 'no'}"
    if isinstance(widget, q["QAbstractSpinBox"]):
        value = getattr(widget, "value", None)
        return f"value {value()!r}" if callable(value) else ""
    if isinstance(widget, q["QLineEdit"]):
        return f"holds {widget.text()!r}"
    return ""


def _disabled_reason(widget: object) -> str:
    """Why this control is off, in the words the app itself would show."""

    tip = str(widget.toolTip() or "").strip()  # type: ignore[attr-defined]
    if tip:
        return tip.splitlines()[0]
    # Deliberately loud rather than blank. Eleven rows of the Recipes form
    # dumped a bare "DISABLED" and not one of them said what would re-enable
    # it; a greyed control with no reason is indistinguishable from a broken
    # one, and the fix is for the *dump* to say that out loud.
    return "(nothing on the control says why)"


def _is_internal(widget: object, root: object) -> bool:
    """True for the parts Qt builds *inside* a compound control.

    An editable ``QComboBox`` owns a ``QLineEdit`` and a drop-down button; a
    ``QSpinBox`` owns two arrows. Neither is a control a user meets on its
    own, and listing them would drown the ones that are.

    An item view is a host for its own scaffolding -- the corner button, the
    header sections -- but not for what is drawn *over* it: the Cells
    empty-state overlay is parented to the table's **viewport**, and it is one
    half of the "two buttons both saying Add cell" the walkthrough asked
    about. Qt's own furniture is a direct child of the view; an overlay
    reaches the view through the viewport, and that is the test used here.
    """

    q = _widget_classes()
    hosts = (q["QComboBox"], q["QAbstractSpinBox"])
    child = widget
    parent = widget.parent()  # type: ignore[attr-defined]
    while parent is not None and parent is not root:
        if isinstance(parent, hosts):
            return True
        if isinstance(parent, q["QAbstractItemView"]) and child is not parent.viewport():
            return True
        child, parent = parent, parent.parent()
    return False


def controls(root: object, *, skip_inside: tuple[type, ...] = ()) -> list[Control]:
    """Every control under ``root``, in the order Qt built them.

    ``skip_inside`` leaves out the parts of a bigger thing that is dumped
    properly elsewhere -- the Recipes form's option rows each get a paragraph
    of their own further down, and repeating their bare widgets here would
    bury the eight buttons a user actually has to choose between.
    """

    q = _widget_classes()
    found: list[Control] = []
    for widget in root.findChildren(q["QWidget"]):  # type: ignore[attr-defined]
        kind = _control_kind(widget)
        if kind is None or _is_internal(widget, root):
            continue
        if skip_inside and _hosted_by(widget, root, skip_inside):
            continue
        found.append(
            Control(
                kind=kind,
                label=_control_label(widget),
                enabled=widget.isEnabled(),
                visible=widget.isVisible(),
                receivers=_gesture_receivers(widget),
                reason=_disabled_reason(widget) if not widget.isEnabled() else "",
                detail=_control_detail(widget),
            )
        )
    return found


def _hosted_by(widget: object, root: object, hosts: tuple[type, ...]) -> bool:
    parent = widget.parent()  # type: ignore[attr-defined]
    while parent is not None and parent is not root:
        if isinstance(parent, hosts):
            return True
        parent = parent.parent()
    return False


def render_controls(
    root: object,
    *,
    title: str = "controls",
    skip_inside: tuple[type, ...] = (),
    duplicates: bool = True,
) -> list[str]:
    """The control block for one screen, plus its duplicate-label index."""

    found = controls(root, skip_inside=skip_inside)
    live = sum(1 for c in found if c.visible)
    out = [f"  {title} ({len(found)} in the tree, {live} on screen):"]
    out += [c.line() for c in found]
    out.append("")
    if duplicates:
        out += _duplicate_labels(found)
    return out


#: Kinds whose "label" is words the designer put on the control. A text box's
#: label is whatever the user's data happens to be, so two boxes that both
#: hold ``0.001`` are not a duplicate-label problem and listing them as one
#: buries the pair that is (two ``Add cell`` buttons on one screen).
_LABELLED_KINDS = (
    "button",
    "toggle button",
    "tool button",
    "tool button (menu)",
    "checkbox",
    "radio button",
    "path link",
    "menu action",
)


def _duplicate_labels(found: Iterable[Control]) -> list[str]:
    """Every wording that is on more than one control of the same screen.

    The reviewer's question is always the same and is always worth asking out
    loud: these two say the same words -- do they do the same thing?
    """

    by_label: dict[str, list[Control]] = {}
    for control in found:
        if control.label.startswith("("):  # an identifier, not words on screen
            continue
        if not control.kind.startswith(_LABELLED_KINDS):
            continue
        by_label.setdefault(control.label, []).append(control)
    dupes = {label: items for label, items in by_label.items() if len(items) > 1}
    if not dupes:
        return ["  duplicate labels on this screen: none", ""]
    out = ["  duplicate labels on this screen:"]
    for label, items in sorted(dupes.items()):
        out.append(f"    {label} x{len(items)}")
        for item in items:
            state = "enabled" if item.enabled else "DISABLED"
            seen = "visible" if item.visible else "hidden"
            out.append(
                f"      [{item.kind}] {state} · {seen} · receivers {item.receivers}"
            )
    out.append("")
    return out


# ---------------------------------------------------------------------------
# Scroll ranges, lists and tables
# ---------------------------------------------------------------------------


def render_scroll_areas(root: object) -> list[str]:
    """Range and position of every scroll area.

    A range of ``0`` is why a button labelled "Show 3 discrepancies" can do
    nothing at all: the thing it scrolls to is already in view and the scroll
    it asks for has nowhere to go.
    """

    from PyQt5.QtWidgets import QScrollArea

    areas = root.findChildren(QScrollArea)  # type: ignore[attr-defined]
    if not areas:
        return ["  scroll areas: none", ""]
    out = ["  scroll areas:"]
    for index, area in enumerate(areas):
        bar = area.verticalScrollBar()
        name = area.objectName() or f"#{index}"
        note = "  <- range 0: nothing here can scroll" if bar.maximum() == 0 else ""
        out.append(
            f"    {name:<24} range {bar.maximum():>5}  position {bar.value():>5}"
            f"  visible {'yes' if area.isVisible() else 'no'}{note}"
        )
    out.append("")
    return out


def render_item_views(root: object) -> list[str]:
    """Rows, selection and check state of every list, table and tree."""

    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QAbstractItemView

    views = [
        view
        for view in root.findChildren(QAbstractItemView)  # type: ignore[attr-defined]
        if not _is_internal(view, root)
    ]
    if not views:
        return ["  lists and tables: none", ""]
    out = ["  lists and tables:"]
    for index, view in enumerate(views):
        model = view.model()
        rows = model.rowCount() if model is not None else 0
        selection = view.selectionModel()
        selected = len(selection.selectedRows()) if selection is not None else 0
        checked = 0
        if model is not None:
            for row in range(rows):
                state = model.index(row, 0).data(Qt.CheckStateRole)
                checked += int(state == Qt.Checked)
        name = view.objectName() or f"#{index} ({type(view).__name__})"
        out.append(
            f"    {name:<24} rows {rows:>4}  selected {selected:>3}  checked {checked:>3}"
            f"  visible {'yes' if view.isVisible() else 'no'}"
        )
    out.append("")
    return out


# ---------------------------------------------------------------------------
# One option row
# ---------------------------------------------------------------------------


def _editor_kind(editor: object) -> str:
    """What the user sees, in the words a user would use."""

    name = type(editor).__name__
    return {
        "BoolOptionEditor": "checkbox",
        "ChoiceOptionEditor": "dropdown (closed)",
        "FreeChoiceOptionEditor": "dropdown (editable)",
        "MultiChoiceOptionEditor": "checkbox row",
        "NumberOptionEditor": "number box",
        "ListOptionEditor": "text box, comma separated",
        "TextOptionEditor": "text box",
        "PointerOptionEditor": "read-only pointer row",
    }.get(name, name)


def _same_as_default(value: Any, default: Any) -> bool:
    """Compare a row's answer with the catalog's, the way the screen does.

    Mirrors ``recipes_screen._display_value``: the editors normalise
    differently (a ``bool`` default of ``None`` reads back as ``False``, a
    list default of ``None`` as ``[]``), so a raw ``==`` would call half the
    form overridden.
    """

    def shown(item: Any) -> str:
        if item is None:
            return ""
        if isinstance(item, bool):
            return "on" if item else "off"
        if isinstance(item, (list, tuple)):
            return ",".join(str(part) for part in item)
        return str(item)

    return shown(value) == shown(default)


def describe_editor(editor: object) -> list[str]:
    """One control, as a reader with no source access would meet it."""

    from auto_ext.ui.widgets.option_editor import (
        PointerOptionEditor,
        frozen_reason,
        hint_text,
    )

    spec = editor.spec  # type: ignore[attr-defined]
    value = editor.value()  # type: ignore[attr-defined]
    lines = [f"    {spec.key}"]
    lines.append(f"      control : {_editor_kind(editor)}")
    lines.append(f"      value   : {value!r}")
    choices = getattr(editor, "choices", None)
    if callable(choices):
        lines.append(f"      offers  : {choices()}")
    boxes = getattr(editor, "check_boxes", None)
    if callable(boxes):
        lines.append(f"      offers  : {list(boxes())}")
    hint = hint_text(spec)
    if hint:
        lines.append(f"      hint    : {hint}")

    if isinstance(editor, PointerOptionEditor):
        # The sentence that says the row is not settable here is the whole
        # reason the row exists. Dropped, the row reads as an editable text
        # box carrying a default -- which is what the walkthrough concluded,
        # and then typed into.
        lines.append("      READ-ONLY: this row is not editable on this screen")
        lines.append(f"      points to: {editor.link_label().full_text()}")
    elif not _same_as_default(value, spec.default):
        # A value that is not the default and does not say so is how a form
        # teaches a reader that "value == hint" everywhere, and then breaks
        # that rule silently on one row out of eighty-seven.
        lines.append(
            f"      OVERRIDDEN: this recipe holds {value!r}; the catalog "
            f"default is {spec.default!r}"
        )

    control = editor.control()  # type: ignore[attr-defined]
    row_off = not editor.isEnabled()  # type: ignore[attr-defined]
    control_off = control is not None and not control.isEnabled()
    if row_off or control_off:
        if editor.is_frozen:  # type: ignore[attr-defined]
            reason = frozen_reason(spec)
        elif row_off:
            # The whole row is off: the screen's emit gating writes a real
            # sentence into the row's tooltip, e.g. "dspf only -- this recipe
            # emits extracted_view", and the old dump threw it away.
            reason = _disabled_reason(editor)
        else:
            # Only the inner control is off. That is the shape a STRUCTURAL
            # row takes, and it carries no sentence of its own, so say what it
            # is rather than echoing the tooltip's first line (the option key).
            reason = (
                f"the field is read-only ({spec.type.value}); it shows what "
                "the tool is given, and is not a choice this form makes"
            )
        lines.append(f"      DISABLED: {reason}")

    if isinstance(editor, PointerOptionEditor):
        # The link is an ElidedLabel with an overridden mouseReleaseEvent, so
        # the signal a click reaches is the editor's, not the label's.
        receivers = _receivers(editor, editor.navigate_requested)
    else:
        receivers = _gesture_receivers(control) if control is not None else -1
    lines.append(
        f"      state   : {'enabled' if not (row_off or control_off) else 'disabled'}"
        f" · {'visible' if editor.isVisible() else 'hidden'}"  # type: ignore[attr-defined]
        f" · receivers {receivers}"
    )
    return lines


def describe_span(key: str, widget: object) -> list[str]:
    """A full-width sub-form: a row whose value is not one control.

    ``OptionGrid.editor(key)`` answers ``None`` for these, and skipping the
    ``None`` was how the extraction rules -- the single most consequential
    setting in the tool -- dumped as an empty heading.
    """

    from auto_ext.ui.widgets.extract_rules import ExtractRulesEditor

    lines = [f"    {key}"]
    lines.append(f"      control : sub-form ({type(widget).__name__})")
    if isinstance(widget, ExtractRulesEditor):
        lines.append(f"      note    : {widget.note_text()}")
        rules = widget.value()
        lines.append(f"      rules   : {len(rules)} in order, first wins last")
        for index, (rule, row) in enumerate(zip(rules, widget.rows()), start=1):
            lines.append(f"        rule {index}: {rule}")
            lines.append(
                f"          selection offers: "
                f"{[row.selection_combo().itemText(i) for i in range(row.selection_combo().count())]}"
            )
            lines.append(
                f"          type offers     : "
                f"{[row.type_combo().itemText(i) for i in range(row.type_combo().count())]}"
            )
    # The sub-form's own buttons, named under the section they belong to. The
    # previous dump listed '↑', '↓', '×' and '+ add rule' in the screen's flat
    # button list with nothing attached, and the reviewer's question was
    # exactly "add a rule to *what*?".
    lines += [f"    {control.line().strip()}" for control in controls(widget)]
    return lines


# ---------------------------------------------------------------------------
# Screen dumpers
# ---------------------------------------------------------------------------


def _form_hosts() -> tuple[type, ...]:
    """Widgets whose innards are dumped row by row further down."""

    from auto_ext.ui.widgets.extract_rules import ExtractRulesEditor
    from auto_ext.ui.widgets.option_editor import OptionEditor

    return (OptionEditor, ExtractRulesEditor)


def dump_recipes(window: object, opts: Options) -> str:
    from auto_ext.ui.widgets.option_editor import group_label

    screen = window.shell.page("recipes")  # type: ignore[attr-defined]
    screen.set_density(opts.density)
    _pump()

    shown = set(screen.visible_option_keys())
    out = ["=== Recipes screen ===", ""]
    out.append(
        f"  density     : {screen.density()} "
        f"({len(shown)} of {len(screen.option_keys())} option rows drawn)"
    )
    out.append(f"  title field : {screen.name_edit().text()!r} (editable)")
    out.append(f"  recipe list : {_tree_lines(screen.recipe_list)}")
    out.append("")
    out += render_controls(screen, skip_inside=_form_hosts())
    out += render_scroll_areas(screen)
    out += render_item_views(screen)

    for name, group in screen.groups().items():
        grid = group.grid
        visible = grid.visible_keys()
        if not visible and not group.isVisible():
            # A group the density folded away is still worth one line: a
            # section that vanishes reads as a feature the tool does not have.
            out.append(f"  [{group_label(name)}]  (hidden in this density)")
            out.append("")
            continue
        out.append(f"  [{group_label(name)}]  header: {group.header_text()!r}")
        for key in grid.keys():
            editor = grid.editor(key)
            span = grid.span(key)
            hidden = key not in visible
            if editor is not None:
                if hidden:
                    continue
                out += describe_editor(editor)
            elif span is not None:
                if hidden:
                    continue
                out += describe_span(key, span)
            else:
                # Never drop a key without saying so. The previous version of
                # this script skipped these silently, which is how a whole
                # sub-form went missing from an artefact whose entire promise
                # is "this is everything a user has".
                out.append(f"    {key}")
                out.append("      control : NOT DUMPED - no editor and no sub-form")
        out.append("")
    return "\n".join(out)


def _tree_lines(tree: object) -> str:
    """Row texts of a QTreeWidget, which is how the recipe list is drawn."""

    from PyQt5.QtCore import Qt

    rows = []
    for index in range(tree.topLevelItemCount()):  # type: ignore[attr-defined]
        item = tree.topLevelItem(index)  # type: ignore[attr-defined]
        second = item.data(0, Qt.UserRole + 1)
        rows.append(f"{item.text(0)!r}" + (f" / {second!r}" if second else ""))
    return f"{len(rows)} row(s): " + ", ".join(rows) if rows else "0 rows"


def dump_cells(window: object, opts: Options) -> str:
    """The Cells screen: the columns AND the rows that are in them.

    The previous version printed the column schema only, which made the file
    byte-identical between an empty project and a real one -- so the reviewer
    could not name a single cell, and could not tell whether the project had
    one row or forty.
    """

    from PyQt5.QtCore import Qt
    from auto_ext.ui.screens.cells_screen import (
        COL_CHECK,
        COL_RECIPE,
        COLUMN_TITLES,
        EDITABLE_FIELDS,
    )

    screen = window.shell.page("cells")  # type: ignore[attr-defined]
    # ``resizeEvent`` picks the mode from the width; a mode asked for on the
    # command line has to survive the next layout pass.
    screen.auto_compact = False
    screen.set_column_mode(opts.mode)
    _pump()

    table = screen.table
    choices = screen.recipe_choices()
    out = ["=== Cells screen ===", "", f"  mode    : {screen.column_mode()}"]
    out.append(f"  rows    : {table.rowCount()} ({len(screen.visible_keys())} pass the filter)")
    out.append(f"  checked : {len(screen.selected_keys())}")
    out.append(f"  run     : {screen.run_bar.run_button_text()!r}")
    out.append("")
    out += render_controls(screen)
    out += render_scroll_areas(screen)
    out += render_item_views(screen)

    columns = [c for c in range(len(COLUMN_TITLES)) if not table.isColumnHidden(c)]
    out.append("  columns:")
    for column in columns:
        title = COLUMN_TITLES[column] or "(check)"
        if column in EDITABLE_FIELDS:
            how = "editable - type into the cell"
        elif column == COL_CHECK:
            how = "checkbox - tick it to put the row in the next run"
        elif column == COL_RECIPE:
            how = (
                "editable - a dropdown offering "
                + str(["—"] + [name for _id, name in choices])
            )
        else:
            how = "read-only"
        out.append(f"    column {column}: {title!r:22} {how}")
    out.append("")

    out.append("  rows:")
    if table.rowCount() == 0:
        out.append("    (this project has no cells)")
    for row in range(table.rowCount()):
        check = table.item(row, COL_CHECK)
        ticked = check is not None and check.checkState() == Qt.Checked
        cells = []
        for column in columns:
            if column == COL_CHECK:
                continue
            item = table.item(row, column)
            cells.append(f"{COLUMN_TITLES[column]}={item.text()!r}" if item else "")
        out.append(f"    row {row}: {'[x]' if ticked else '[ ]'} " + "  ".join(cells))
    out.append("")
    return "\n".join(out)


def dump_runs(window: object, opts: Options) -> str:
    """The Runs screen: the history, the card, and what the card can open.

    There was no dumper for this screen at all, although it is a registered
    page and it is where the LVS discrepancy count, the LVS report,
    ``calibre.log``, rename, note and star all live. From the old dump, the
    Runs screen did not exist -- which is why the walkthrough could not answer
    "an LVS failed, how many discrepancies" at all.
    """

    screen = window.shell.page("runs")  # type: ignore[attr-defined]
    out = ["=== Runs screen ===", ""]
    out.append(f"  runs root  : {screen.runs_root}")
    out.append(f"  status line: {screen.status_text()!r}")
    out.append(f"  runs       : {len(screen.entries)} kept, {len(screen.visible_entries)} shown")
    selected = screen.selected_entry
    out.append(f"  selected   : {selected.display_name if selected else None!r}")
    out.append("")
    out.append("  run list:")
    if not screen.visible_entries:
        out.append("    (no runs recorded in this project)")
    for entry in screen.visible_entries:
        out.append(
            f"    {'*' if entry.starred else ' '} {entry.display_name}"
            f"  cell={entry.cell}  recipe={entry.recipe_id}"
            f"  result={entry.overall}"
            f"  lvs_passed={entry.lvs_passed}"
            f"  discrepancies={entry.lvs_discrepancies}"
        )
    out.append("")
    out += render_controls(screen)
    out += render_scroll_areas(screen)
    out += render_item_views(screen)
    out.append("  right-click on a run offers:")
    for line in _menu_actions(screen, screen._list, screen._on_list_menu):
        out.append(f"    {line}")
    out.append("")
    out.append("  right-click on a stage row of the result card offers:")
    card = screen.result_card
    for line in _menu_actions(card, card._stage_tree, card._on_stage_menu):
        out.append(f"    {line}")
    out.append("")
    return "\n".join(out)


def _menu_actions(owner: object, view: object, builder: Callable[[Any], None]) -> list[str]:
    """Open a context menu without a mouse, and say what it offers.

    Both context menus in the app are built in a slot and popped up from a
    deferred ``QTimer.singleShot``, so there is no way to read them without
    calling the slot and intercepting ``QMenu.exec_``. A menu is as much part
    of "what a user has" as a button is, and none of it was in the inventory.
    """

    from PyQt5.QtCore import QPoint
    from PyQt5.QtWidgets import QMenu

    model = view.model()  # type: ignore[attr-defined]
    if model is None or model.rowCount() == 0:
        return ["(nothing to right-click: this view is empty)"]
    index = model.index(0, 0)
    rect = view.visualRect(index)  # type: ignore[attr-defined]
    captured: list[Any] = []
    original = QMenu.exec_

    def _capture(self, *_args, **_kwargs):
        captured.append(self)
        return None

    QMenu.exec_ = _capture  # type: ignore[assignment]
    try:
        builder(QPoint(rect.center()))
        _pump()
    finally:
        QMenu.exec_ = original  # type: ignore[assignment]

    if not captured:
        return ["(no menu was built)"]
    lines = []
    for menu in captured:
        for action in menu.actions():
            if action.isSeparator():
                lines.append("---")
                continue
            state = "enabled" if action.isEnabled() else "DISABLED"
            receivers = _receivers(action, action.triggered)
            line = f"{action.text()!r:38} {state} · receivers {receivers}"
            if not action.isEnabled() and action.toolTip():
                line += f"\n      disabled because: {action.toolTip()}"
            lines.append(line)
    return lines


def dump_setup(window: object, opts: Options) -> str:
    """The drawer. Never opens it: whether it is open is part of the answer.

    ``Re-check the PDK`` is a menu item that exists precisely for the case
    where the drawer is *closed*, so an instrument that opened the drawer
    before measuring would report a visible effect the user never gets.
    """

    screen = window.setup_drawer  # type: ignore[attr-defined]
    out = [
        "=== Setup drawer ===",
        "",
        f"  open: {'yes' if window.shell.is_setup_open() else 'no'}",  # type: ignore[attr-defined]
        "",
    ]
    out += render_controls(screen)
    out += render_scroll_areas(screen)
    out += render_item_views(screen)
    return "\n".join(out)


def dump_menus(window: object, opts: Options) -> str:
    """The menu bar. Three of the app's doors are only here."""

    out = ["=== Menu bar ===", ""]
    for menu_action in window.menuBar().actions():  # type: ignore[attr-defined]
        menu = menu_action.menu()
        if menu is None:
            continue
        out.append(f"  {menu_action.text()}")
        for action in menu.actions():
            if action.isSeparator():
                out.append("    ---")
                continue
            shortcut = action.shortcut().toString()
            state = "enabled" if action.isEnabled() else "DISABLED"
            seen = "visible" if action.isVisible() else "hidden"
            out.append(
                f"    [menu action] {action.text()!r:34} {state} · {seen}"
                f" · receivers {_receivers(action, action.triggered)}"
                + (f" · {shortcut}" if shortcut else "")
            )
        out.append("")
    return "\n".join(out)


def dump_project(window: object, opts: Options) -> str:
    """The Project screen: workspace.yaml + the PDK profile, field by field.

    Prints the help line as well as the label. Both objects are full of terms
    a first-time reader has no way to guess ("dir_expr", "filename_pattern",
    "preserve cell list"), so the sentence under the control IS the control's
    usability -- which is exactly the class of defect this inventory is for.

    Groups are emitted once each, in the order the screen draws them. Walking
    declaration order and starting a heading on every change printed
    ``[Process]`` twice, forty rows apart, which reads as a rendering bug and
    hides whatever is under the second copy.
    """

    from auto_ext.ui.project_fields import (
        PROFILE_FIELDS,
        PROFILE_GROUP_ORDER,
        WORKSPACE_FIELDS,
        WORKSPACE_GROUP_ORDER,
    )

    screen = window.shell.page("project")  # type: ignore[attr-defined]
    _pump()
    out = ["=== Project screen ===", ""]
    out += render_controls(screen)
    out += render_scroll_areas(screen)
    out += render_item_views(screen)

    for title, order, fields in (
        ("Project", WORKSPACE_GROUP_ORDER, WORKSPACE_FIELDS),
        ("PDK profile", PROFILE_GROUP_ORDER, PROFILE_FIELDS),
    ):
        out.append(f"  == {title} ==")
        groups = list(order)
        for spec in fields:
            if spec.group not in groups:
                groups.append(spec.group)
        for group in groups:
            in_group = [spec for spec in fields if spec.group == group]
            if not in_group:
                continue
            out.append(f"  [{group}]")
            for spec in in_group:
                out += _describe_field(screen, spec)
        out.append("")
    return "\n".join(out)


def _describe_field(screen: object, spec: Any) -> list[str]:
    from auto_ext.ui.project_fields import FieldKind

    row = screen.row(spec.path)  # type: ignore[attr-defined]
    state = "enabled" if row is not None and row.isEnabled() else "disabled"
    if row is None:
        return [f"    {spec.label:22} {spec.kind.value:8} {state:8} (no control)"]

    control = row.control()
    lines = []
    if spec.kind is FieldKind.TABLE:
        lines.append(f"    {spec.label:22} {spec.kind.value:8} {state:8}")
        lines += _describe_table(control, spec)
    else:
        lines.append(
            f"    {spec.label:22} {spec.kind.value:8} {state:8} {_repr_value(row)}"
        )
    lines.append(f"      {spec.help}")
    if spec.unset_means:
        lines.append(f"      unset: {spec.unset_means}")
    if row.error():
        lines.append(f"      ERROR shown under this row: {row.error()}")
    return lines


def _describe_table(control: object, spec: Any) -> list[str]:
    """A table row by row, rather than a truncated Python repr.

    ``[CornerSpec(name='typical', technology_corner='TYPICAL', ...`` told the
    reviewer two of the four columns and then stopped, and the one place the
    literal handed to the tool is written down was inside the elision. A table
    is a table: print its headers and its rows.
    """

    table = control.table()  # type: ignore[attr-defined]
    headers = [
        table.horizontalHeaderItem(c).text() for c in range(table.columnCount())
    ]
    lines = [f"      table '{spec.label}' columns: {headers}"]
    if table.rowCount() == 0:
        lines.append("      (no rows)")
    for row in range(table.rowCount()):
        cells = []
        for column, header in enumerate(headers):
            item = table.item(row, column)
            cells.append(f"{header}={(item.text() if item else '')!r}")
        lines.append(f"      row {row}: " + "  ".join(cells))
    # The two Add/Remove pairs on this screen are identical words on identical
    # widgets; the only thing that tells them apart is which table owns them.
    for control_row in controls(control):
        lines.append(f"      {control_row.line().strip()} - acts on '{spec.label}'")
    return lines


def _repr_value(row: object) -> str:
    """One short rendering of what a row currently shows."""

    try:
        value = row.value()  # type: ignore[attr-defined]
    except Exception as exc:  # a half-typed mapping line, for instance
        return f"<unreadable: {exc}>"
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


_DUMPERS: dict[str, Callable[[object, Options], str]] = {
    "recipes": dump_recipes,
    "cells": dump_cells,
    "runs": dump_runs,
    "project": dump_project,
    "setup": dump_setup,
    "menus": dump_menus,
}


# ---------------------------------------------------------------------------
# Booting a window
# ---------------------------------------------------------------------------


def _pump() -> None:
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def build_window(opts: Options) -> object:
    """A shown MainWindow. Shown, because ``isVisible`` is half the answer."""

    from auto_ext.ui.main_window import MainWindow

    window = MainWindow(config_dir=opts.config_dir, auto_ext_root=opts.auto_ext_root)
    if opts.runs_root is not None:
        # Part of "which project is this", not part of dumping the Runs
        # screen: the click probe has to find the result card's buttons
        # before any dumper has run.
        window.runs_screen.set_runs_root(opts.runs_root)
    window.resize(*_WINDOW_SIZE)
    window.show()
    _pump()
    return window


def dump_screen(window: object, name: str, opts: Options) -> str:
    """One screen, with the shell put on it first so visibility is truthful.

    Navigating is the reader's own gesture -- they asked for this screen --
    and it is done here rather than inside a dumper so that a dumper can be
    used as a *measurement* without moving the thing being measured.
    """

    if name in _PAGE_SCREENS:
        window.shell.set_current_page(name)  # type: ignore[attr-defined]
        _pump()
    elif name == "setup":
        window.shell.set_setup_open(True)  # type: ignore[attr-defined]
        _pump()
    return _DUMPERS[name](window, opts)


# ---------------------------------------------------------------------------
# The click probe
# ---------------------------------------------------------------------------


@dataclass
class _Sink:
    """Everything a click asked the desktop or the user for."""

    opened: list[str] = field(default_factory=list)


def _neutralise_dialogs(sink: _Sink) -> Callable[[], None]:
    """Answer every modal with "cancel", and record that it was asked.

    A probe that clicks every button in a window will hit controls that open a
    file dialog, a warning or a text prompt. Left alone, the first one blocks
    forever. Recorded and cancelled, each one is *evidence*: a control that
    opens a dialog demonstrably did something.
    """

    from PyQt5.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

    saved: list[tuple[Any, str, Any]] = []

    def patch(owner: Any, name: str, replacement: Any) -> None:
        saved.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    for kind in ("warning", "critical", "information", "question", "about"):
        def _box(_p=None, title="", text="", *a, _kind=kind, **k):
            sink.opened.append(f"QMessageBox.{_kind}({title!r})")
            return QMessageBox.Cancel

        patch(QMessageBox, kind, staticmethod(_box))

    for name in ("getOpenFileName", "getSaveFileName", "getOpenFileNames"):
        def _file(*a, _name=name, **k):
            sink.opened.append(f"QFileDialog.{_name}")
            return ("", "")

        patch(QFileDialog, name, staticmethod(_file))

    def _dir(*a, **k):
        sink.opened.append("QFileDialog.getExistingDirectory")
        return ""

    patch(QFileDialog, "getExistingDirectory", staticmethod(_dir))

    for name in ("getText", "getMultiLineText"):
        def _input(*a, _name=name, **k):
            sink.opened.append(f"QInputDialog.{_name}")
            return ("", False)

        patch(QInputDialog, name, staticmethod(_input))

    def _exec(self, *a, **k):
        sink.opened.append(f"{type(self).__name__}.exec_")
        return 0

    patch(QDialog, "exec_", _exec)
    patch(QMenu, "exec_", _exec)

    def restore() -> None:
        for owner, name, original in reversed(saved):
            setattr(owner, name, original)

    return restore


def _sandbox(opts: Options) -> Iterator[Options]:
    """A throwaway copy of the project, because a probe clicks *Save* too."""

    if opts.config_dir is None:
        yield opts
        return
    with tempfile.TemporaryDirectory(prefix="ui_inventory_probe_") as tmp:
        root = Path(tmp)
        shutil.copytree(opts.config_dir, root / "config")
        source_root = (
            opts.auto_ext_root
            if opts.auto_ext_root is not None
            else opts.config_dir.resolve().parent
        )
        recipes = Path(source_root) / "recipes"
        if recipes.is_dir():
            shutil.copytree(recipes, root / "recipes")
        yield Options(
            config_dir=root / "config",
            auto_ext_root=root,
            runs_root=opts.runs_root,
            density=opts.density,
            mode=opts.mode,
        )


def click_probe(name: str, opts: Options) -> list[str]:
    """Press each control of one screen and say which changed nothing.

    The cheapest possible mechanisation of the complaint this whole review
    started from -- "I clicked it and nothing happened". A control whose press
    leaves the dump byte for byte identical, opens no dialog and moves no
    scroll bar is a *candidate* defect; it is not proof, because an effect can
    live entirely outside the app (a spawned editor, the clipboard) or on a
    screen this dump does not cover. Every such control is worth one question.

    Each press gets its own window over its own copy of the project, so one
    press cannot change what the next one is asked to do -- and so the probe
    can safely press ``Save`` and ``Delete``.
    """

    generator = _sandbox(opts)
    safe = next(generator)
    try:
        window = build_window(safe)
        labels = [label for label, _ in _probe_targets(window, name)]
        window.close()
        _pump()

        out = [
            "  after pressing (each press in a fresh copy of the project):",
            "    (only this screen's dump is compared, so an effect that lands "
            "on another screen reads as no change here)",
        ]
        for index, label in enumerate(labels):
            out.append(f"    {label:<40} {_probe_one(name, safe, index)}")
        out.append("")
        return out
    finally:
        for _ in generator:  # pragma: no cover - drains the TemporaryDirectory
            pass


def _probe_root(window: object, name: str) -> object:
    if name in _PAGE_SCREENS:
        window.shell.set_current_page(name)  # type: ignore[attr-defined]
        _pump()
        return window.shell.page(name)  # type: ignore[attr-defined]
    if name == "setup":
        window.shell.set_setup_open(True)  # type: ignore[attr-defined]
        _pump()
        return window.setup_drawer  # type: ignore[attr-defined]
    return window


def _probe_targets(window: object, name: str) -> list[tuple[str, Callable[[], None]]]:
    """Everything the probe can press on this screen, with how to press it.

    Menu items are here as well as buttons. ``Re-check the PDK`` is a menu
    item whose whole job is the case where the drawer is *closed*, so a probe
    that only pressed buttons could never have reached the one control whose
    documented symptom is "nothing on screen changed, so I did it three more
    times".
    """

    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QAbstractButton

    if name == "menus":
        targets = []
        for menu_action in window.menuBar().actions():  # type: ignore[attr-defined]
            menu = menu_action.menu()
            if menu is None:
                continue
            for action in menu.actions():
                if action.isSeparator() or not action.isEnabled():
                    continue
                targets.append(
                    (f"{menu_action.text()} > {action.text()}", action.trigger)
                )
        return targets

    root = _probe_root(window, name)
    return [
        (
            _control_label(widget),
            lambda w=widget: QTest.mouseClick(w, Qt.LeftButton),
        )
        for widget in root.findChildren(QAbstractButton)  # type: ignore[attr-defined]
        if widget.isVisible()
        and widget.isEnabled()
        and not _is_internal(widget, root)
        # A button that owns a menu is skipped for two reasons, and both
        # matter. It cannot be a no-op -- pressing it opens the menu, which is
        # a visible effect Qt guarantees -- and pressing it *hangs the probe*:
        # ``QPushButton.showMenu`` spins Qt's own modal event loop, and with
        # no user to dismiss it offscreen the loop never returns. The two
        # checkbox-row summary buttons on the Recipes form are exactly this.
        and not _owns_menu(widget)
    ]


def _owns_menu(widget: object) -> bool:
    """True for a button whose press opens a menu Qt drives itself."""

    getter = getattr(widget, "menu", None)
    return callable(getter) and getter() is not None


def _probe_baseline(window: object, name: str, opts: Options) -> str:
    """What the probe compares before and after.

    A menu item's product almost never lands on the menu bar, so for the menu
    the baseline is the whole window: every screen, plus the title bar, which
    is where the unsaved-changes star lives.
    """

    if name != "menus":
        return dump_screen(window, name, opts)
    shell = window.shell  # type: ignore[attr-defined]
    # ``_DUMPERS`` directly, not ``dump_screen``: navigating to a screen in
    # order to measure it would itself be the change we are looking for.
    parts = [
        window.windowTitle(),  # type: ignore[attr-defined]
        f"page: {shell.current_page_key()}  setup open: {shell.is_setup_open()}",
    ]
    parts += [_DUMPERS[screen](window, opts) for screen in sorted(_DUMPERS)]
    return "\n".join(parts)


def _probe_one(name: str, opts: Options, index: int) -> str:
    """Boot, press control ``index``, and describe what moved."""

    from PyQt5.QtWidgets import QScrollArea

    sink = _Sink()
    restore = _neutralise_dialogs(sink)
    try:
        window = build_window(opts)
        targets = _probe_targets(window, name)
        if index >= len(targets):  # pragma: no cover - the tree shifted
            return "could not be found on the second boot"
        _label, press = targets[index]
        root = _probe_root(window, name)
        scrolls = {
            id(area): area.verticalScrollBar().value()
            for area in root.findChildren(QScrollArea)  # type: ignore[attr-defined]
        }
        before = _probe_baseline(window, name, opts)
        press()
        _pump()
        after = _probe_baseline(window, name, opts)
        moved = [
            area
            for area in root.findChildren(QScrollArea)  # type: ignore[attr-defined]
            if scrolls.get(id(area)) != area.verticalScrollBar().value()
        ]
        window.close()
        _pump()

        notes = []
        if sink.opened:
            notes.append("opened " + ", ".join(sorted(set(sink.opened))))
        if moved:
            notes.append(f"{len(moved)} scroll area(s) moved")
        if before != after:
            notes.append("the dump changed")
        if not notes:
            return "NO OBSERVABLE CHANGE  <- candidate defect"
        return "; ".join(notes)
    finally:
        restore()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from auto_ext.ui.screens.cells_screen import MODE_COMPACT, MODE_RUNNING, MODE_WIDE
    from auto_ext.ui.screens.recipes_screen import DENSITY_ALL, DENSITY_COMMON

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--auto-ext-root", type=Path, default=None)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="a run history to dump, for projects whose runs live elsewhere",
    )
    parser.add_argument("--screen", choices=sorted(_DUMPERS), action="append")
    parser.add_argument(
        "--density",
        choices=(DENSITY_COMMON, DENSITY_ALL),
        default=DENSITY_COMMON,
        help="which half of the Recipes form to draw; the app opens in common",
    )
    parser.add_argument(
        "--mode",
        choices=(MODE_WIDE, MODE_COMPACT, MODE_RUNNING),
        default=MODE_WIDE,
        help="which Cells columns to draw; running is the only one with stages",
    )
    parser.add_argument(
        "--click-probe",
        action="store_true",
        help=(
            "click every enabled control and list the ones whose click leaves "
            "the dump identical. Works on a throwaway copy of the project."
        ),
    )
    args = parser.parse_args(argv)

    from PyQt5.QtWidgets import QApplication

    opts = Options(
        config_dir=args.config_dir,
        auto_ext_root=args.auto_ext_root,
        runs_root=args.runs_root,
        density=args.density,
        mode=args.mode,
    )
    app = QApplication.instance() or QApplication([])
    window = build_window(opts)
    wanted = args.screen or sorted(_DUMPERS)

    blocks = []
    for name in wanted:
        block = dump_screen(window, name, opts)
        if args.click_probe:
            block += "\n" + "\n".join(click_probe(name, opts))
        blocks.append(block)
    print("\n".join(blocks))
    window.close()
    _pump()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
