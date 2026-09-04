"""The focused-row detail strip. Spec ``M`` section 4, artboard ``Q3-d``.

A 42px strip under the form that describes **only the row that has focus**:
where the value lives in the model, why the setting exists, its default and
advisory range, any open question, and the exact line it writes into the
generated file. Plus a Reset that puts the catalog default back.

Two reasons it is a strip and not per-row prose.

**All view can then carry no prose at all.** Ninety rows each explaining
themselves is a page nobody reads; one row explaining itself, on demand, is a
sentence everybody reads. The hint line beside a control stays short because
this exists to carry the rest.

**It repaints two widgets, never the page.** On a forwarded X11 link a
full-page repaint on every focus change is the most expensive thing a form
can do, so the strip updates its own two labels and nothing else moves.
"""

from __future__ import annotations

from typing import Any

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy

from auto_ext.catalog import OptionSpec
from auto_ext.ui import theme
from auto_ext.ui.widgets.option_editor import ElidedLabel

__all__ = [
    "DETAIL_BAR_HEIGHT",
    "OBJ_DETAIL_BAR",
    "OBJ_DETAIL_PATH",
    "OBJ_DETAIL_PROSE",
    "RESET_FOR",
    "RESET_IDLE",
    "RESET_KEY_CHARS",
    "FocusDetailBar",
]

#: Spec ``M`` section 4 states 42px. Two lines of meta text plus the frame.
DETAIL_BAR_HEIGHT = 42

#: What the Reset reads with nothing focused. Never shown -- the button is
#: hidden until a row is described -- but a button whose label is only ever
#: assigned in one branch is a button that can be drawn nameless.
RESET_IDLE = "Reset to default"

#: What it reads once a row is described. The key, not "to default": this
#: button follows FOCUS, which a Tab or a stray click moves, and it sits 42px
#: from the row it would overwrite. Naming the row is the whole difference
#: between an action and a coin flip -- see the module docstring of
#: ``tests/ui/test_focus_detail.py``.
RESET_FOR = "Reset {key}"

#: Characters of key the button spells out before eliding. The longest
#: recipe-owned key is 36, which draws a 345px button into a strip that has
#: 940px to share with the model path and the sentence describing the row.
#: The full key stays in the tooltip.
RESET_KEY_CHARS = 22

OBJ_DETAIL_BAR = "focusDetailBar"
OBJ_DETAIL_PATH = "focusDetailPath"
OBJ_DETAIL_PROSE = "focusDetailProse"

_DOT = " · "
_ARROW = " → "


class FocusDetailBar(QFrame):
    """Describes one row. Empty and quiet when nothing is focused."""

    reset_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_DETAIL_BAR)
        self.setFixedHeight(DETAIL_BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_XS)

        self._path = QLabel("", self)
        self._path.setObjectName(OBJ_DETAIL_PATH)
        row.addWidget(self._path, 0)

        self._prose = ElidedLabel("", parent=self)
        self._prose.setObjectName(OBJ_DETAIL_PROSE)
        row.addWidget(self._prose, 1)

        self._reset = QPushButton(RESET_IDLE, self)
        self._reset.setFlat(True)
        self._reset.clicked.connect(self._on_reset)
        row.addWidget(self._reset, 0)

        self._key = ""
        self.clear()

    # -- content -------------------------------------------------------

    def clear(self) -> None:
        self._key = ""
        self._path.setText("")
        self._prose.set_full_text("focus a setting to see what it does")
        self._reset.setText(RESET_IDLE)
        self._reset.setEnabled(False)
        self._reset.setVisible(False)

    def show_spec(
        self,
        spec: OptionSpec,
        *,
        value: Any = None,
        at_default: bool = True,
    ) -> None:
        """Describe ``spec``. ``at_default`` decides whether Reset is offered."""

        self._key = spec.key
        self._path.setText(spec.recipe_field_path or spec.context_path or spec.key)
        self._path.setToolTip("where this value lives in the recipe")
        self._prose.set_full_text(_prose_for(spec, value))
        self._prose.setToolTip(self._prose.full_text())
        # The label names the row, and it is written from the same ``spec``
        # that ``_key`` is taken from, so the button cannot advertise one row
        # and act on another. Focus is not a selection the user made: a Tab
        # or a stray click three rows away moves what this button writes to,
        # and until it said so a person who had just changed the temperature
        # could reach over and reset something else without seeing it happen.
        self._reset.setText(RESET_FOR.format(key=_short_key(spec.key)))
        self._reset.setVisible(True)
        # Disabled rather than hidden when the row is already at its default:
        # a button that comes and goes as focus moves is a button people stop
        # trusting, and "nothing to reset" is worth saying.
        self._reset.setEnabled(not at_default)
        # The tooltip carries the key in full, which is what the elided label
        # gives up and what every error message quotes.
        self._reset.setToolTip(
            f"{spec.key} is already at the catalog default"
            if at_default
            else f"put {spec.key} back to {_format(spec.default)}"
        )

    def key(self) -> str:
        return self._key

    def path_text(self) -> str:
        return self._path.text()

    def prose_text(self) -> str:
        return self._prose.full_text()

    def reset_button(self) -> QPushButton:
        return self._reset

    def _on_reset(self) -> None:
        if self._key:
            self.reset_requested.emit(self._key)


def _short_key(key: str) -> str:
    """``parasitic_blocking_device_cells_type`` -> ``parasitic_b...cells_type``.

    Elided in the MIDDLE, for the same reason the recipe list elides
    ``recipe_id`` there: catalog keys share their prefixes -- eight rows start
    ``global_nets_`` or ``extract_`` -- so a tail cut removes the only part
    that tells two of them apart. The full key is in the tooltip.
    """

    if len(key) <= RESET_KEY_CHARS:
        return key
    keep = RESET_KEY_CHARS - 3
    head = (keep + 1) // 2
    return f"{key[:head]}...{key[len(key) - (keep - head):]}"


def _prose_for(spec: OptionSpec, value: Any) -> str:
    """Everything the strip says about one row, in one elided line.

    Order is deliberate: what it is for, then what it would be if you left it
    alone, then where it lands. The open question goes LAST because it is the
    part a person needs only once, and the generated-file line goes with it
    because "what does this actually write" is the question the whole catalog
    exists to answer.
    """

    parts: list[str] = []
    if spec.why:
        parts.append(spec.why.strip())
    parts.append(f"default {_format(spec.default)}")
    if spec.range is not None:
        low, high = spec.range
        suffix = "" if spec.range_verified else " (unverified)"
        parts.append(f"advisory {_format(low)}..{_format(high)}{suffix}")
    if spec.unit:
        parts.append(spec.unit)
    line = _lands(spec)
    if line:
        parts.append(line)
    if spec.question:
        parts.append(f"open: {spec.question.strip()}")
    return _DOT.join(parts)


def _lands(spec: OptionSpec) -> str:
    """``→ ext.cmd line 11``, or every file when it lands in several."""

    bits: list[str] = []
    for site in spec.lands_in:
        if site.target is None:
            continue
        name = site.target.value.split(".", 1)[-1]
        at = f" line {site.line}" if site.line is not None else ""
        bits.append(f"{name} {site.option}{at}")
    if not bits:
        return ""
    return _ARROW.strip() + " " + ", ".join(bits)


def _format(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "—"
    return str(value)
