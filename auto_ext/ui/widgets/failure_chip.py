"""Three-letter failure codes and the leaf widgets the Runs surface is built from.

Artboards ``1d`` / ``1e`` of the design canvas discriminate the four failure
classes by a **three-letter code**, not by colour: ``LIC`` and ``CFG`` share
one amber tone, ``LVS`` and ``CRS`` share one red tone. Two hues carry the
only thing a hue can carry honestly -- *who has to act* -- and the letters
carry the diagnosis, so a card stays readable in greyscale and to a
colour-blind reader.

Three widgets live here because all three are one-line leaves of the same
surface and sharing a module keeps their metrics in one place:

* :class:`FailureChip` -- the code badge, in the two forms the canvas draws
  (an inline badge in a card header, a full-height rail down the left edge of
  a failure row),
* :class:`Chip` -- the generic status pill used for stage state (``si + tick``)
  and for pass/fail tallies,
* :class:`PathLabel` -- an elided, clickable path. Paths are the one string
  in this app with no upper length bound, so they may never be allowed to
  report their rendered width as a minimum; see the layout note below.

The classification itself is never made here. It comes from
:mod:`auto_ext.core.failure_class`; this module only maps its
:class:`~auto_ext.core.failure_class.FailureClass` values onto codes, colours
and an ordering.

Layout contract
---------------
Nothing in this module may push a large minimum size onto its host. The
window floor is 940x560 px, and a path label that reports its full text width
as a minimum is the classic way to break it -- :class:`PathLabel` therefore
overrides ``minimumSizeHint`` to a fixed small width and elides in
``resizeEvent``, exactly as ``auto_ext.ui.shell._ElidedLabel`` does for the
title-bar config path.

Assumptions
-----------
Collected here rather than scattered, so a reviewer can check them in one
place. None is verified against a real Calibre or a real X11 session.

* **Codes for the two non-diagnoses.** The canvas legend names four codes.
  Two more states reach a card: a stage the user cancelled
  (:data:`CODE_CANCELLED`, ``CAN``) and a failure
  :mod:`~auto_ext.core.failure_class` refused to classify
  (:data:`CODE_UNKNOWN`, ``UNK``). Both get a three-letter code of the same
  shape rather than a blank chip, because a blank chip reads as "no data was
  loaded" instead of "no diagnosis was reached".
* **``UNK`` is grey, not red.** ``failure_signatures.yaml`` currently holds no
  signatures, so most non-LVS failures classify as ``unknown`` today.
  Painting that red would say "the design is at fault", which is precisely
  what the classifier declined to say. Grey is the neutral tone and the
  accompanying next-action text does the talking.
* **``CAN`` sits in the environment group.** A cancelled stage is re-runnable
  as-is, which is the property that defines that group.
* **``LVS`` is a solid chip and ``CRS`` an outlined one** (canvas 1e). The
  fill weight is a second, redundant discriminator on top of the letters; it
  is not load-bearing.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from auto_ext.core.failure_class import FailureClass
from auto_ext.ui import theme

__all__ = [
    "ACTOR_DESIGN",
    "ACTOR_ENVIRONMENT",
    "ACTOR_ORDER",
    "ACTOR_SUBTITLES",
    "ACTOR_TITLES",
    "ACTOR_UNCLASSIFIED",
    "CANCELLED_KEY",
    "CHIP_TONE_FAILED",
    "CHIP_TONE_MUTED",
    "CHIP_TONE_PASSED",
    "CHIP_TONE_PLAIN",
    "CHIP_TONE_WARNING",
    "CODE_ACTOR",
    "CODE_CANCELLED",
    "CODE_CONFIG",
    "CODE_CRASH",
    "CODE_LEGEND",
    "CODE_LICENSE",
    "CODE_LVS",
    "CODE_ORDER",
    "CODE_STYLE",
    "CODE_UNKNOWN",
    "Chip",
    "ChipStyle",
    "FAILURE_CHIP_INLINE_WIDTH",
    "FAILURE_CHIP_WIDTH",
    "FAILURE_CODES",
    "FailureChip",
    "PATH_LABEL_MIN_WIDTH",
    "PathLabel",
    "actor_for",
    "code_for",
    "code_legend",
    "code_style",
    "sort_key",
]


# ---------------------------------------------------------------------------
# Codes
# ---------------------------------------------------------------------------

#: No license seat was available.
CODE_LICENSE = "LIC"
#: A path or a configuration value is wrong, so the stage never really ran.
CODE_CONFIG = "CFG"
#: Layout and schematic really differ.
CODE_LVS = "LVS"
#: The tool started and then died.
CODE_CRASH = "CRS"
#: The user stopped the run. Not a diagnosis; see the module Assumptions.
CODE_CANCELLED = "CAN"
#: Nothing matched. Not a diagnosis either.
CODE_UNKNOWN = "UNK"

#: Bucket key a cancelled stage is grouped under. Must equal
#: ``auto_ext.ui.widgets.result_card.CANCELLED_KEY``; the two modules are
#: joined by this string and :func:`code_for` is the only place it is read.
CANCELLED_KEY = "cancelled"

#: :class:`~auto_ext.core.failure_class.FailureClass` value -> code. Keyed by
#: the enum's ``str`` value because that is what a record round-trips through
#: ``StageRecord.details["failure"]``.
FAILURE_CODES: dict[str, str] = {
    FailureClass.LICENSE_UNAVAILABLE.value: CODE_LICENSE,
    FailureClass.ENVIRONMENT.value: CODE_CONFIG,
    FailureClass.LVS_MISMATCH.value: CODE_LVS,
    FailureClass.TOOL_CRASH.value: CODE_CRASH,
    FailureClass.UNKNOWN.value: CODE_UNKNOWN,
    CANCELLED_KEY: CODE_CANCELLED,
}

#: One line per code, as drawn in the canvas 1e legend strip.
CODE_LEGEND: dict[str, str] = {
    CODE_LICENSE: "license / host",
    CODE_CONFIG: "config or path (AutoExtError)",
    CODE_LVS: "layout vs schematic mismatch",
    CODE_CRASH: "tool crashed / non-zero exit",
    CODE_CANCELLED: "you stopped the run",
    CODE_UNKNOWN: "no rule and no signature matched",
}


# ---------------------------------------------------------------------------
# Who has to act
# ---------------------------------------------------------------------------

#: Fix the shell, the paths or the license server, then re-run unchanged.
ACTOR_ENVIRONMENT = "environment"
#: Change the layout, the schematic or the tool. Re-running is pointless.
ACTOR_DESIGN = "design"
#: Nobody can be named yet: read the log first.
ACTOR_UNCLASSIFIED = "unclassified"

#: Groups in the order the canvas stacks them: cheapest actor first.
ACTOR_ORDER: tuple[str, ...] = (ACTOR_ENVIRONMENT, ACTOR_DESIGN, ACTOR_UNCLASSIFIED)

#: Group heading, straight from canvas 1e.
ACTOR_TITLES: dict[str, str] = {
    ACTOR_ENVIRONMENT: "Not your layout - environment",
    ACTOR_DESIGN: "Needs a decision from you",
    ACTOR_UNCLASSIFIED: "Not classified yet",
}

#: The line of meta text next to each heading.
ACTOR_SUBTITLES: dict[str, str] = {
    ACTOR_ENVIRONMENT: "re-runnable as-is once fixed",
    ACTOR_DESIGN: "re-running unchanged will fail the same way",
    ACTOR_UNCLASSIFIED: "read the log, then add a signature so the next run classifies itself",
}

#: Code -> group.
CODE_ACTOR: dict[str, str] = {
    CODE_LICENSE: ACTOR_ENVIRONMENT,
    CODE_CONFIG: ACTOR_ENVIRONMENT,
    CODE_CANCELLED: ACTOR_ENVIRONMENT,
    CODE_LVS: ACTOR_DESIGN,
    CODE_CRASH: ACTOR_DESIGN,
    CODE_UNKNOWN: ACTOR_UNCLASSIFIED,
}

#: Full ordering, cheapest action first. Inside the environment group a
#: license wait may clear itself, so it precedes an edit; inside the design
#: group the layout is the user's own call, so it precedes a tool crash that
#: may need a CAD administrator.
CODE_ORDER: tuple[str, ...] = (
    CODE_LICENSE,
    CODE_CONFIG,
    CODE_CANCELLED,
    CODE_LVS,
    CODE_CRASH,
    CODE_UNKNOWN,
)


def code_for(key: str | FailureClass) -> str:
    """Code for a :class:`FailureClass` (or the literal ``"cancelled"``).

    An unrecognised key yields :data:`CODE_UNKNOWN` rather than raising: a
    record written by a newer Auto_ext must still draw.
    """

    if isinstance(key, FailureClass):
        key = key.value
    return FAILURE_CODES.get(str(key), CODE_UNKNOWN)


def actor_for(code: str) -> str:
    """Which group ``code`` belongs to."""

    return CODE_ACTOR.get(code, ACTOR_UNCLASSIFIED)


def sort_key(code: str) -> tuple[int, int]:
    """Sort key implementing "who has to act": ``(group rank, code rank)``.

    Sorting a list of codes -- or of anything keyed by one -- with this puts
    the environment failures the user can clear without touching the design
    first, and the ones nobody has diagnosed last.
    """

    actor = actor_for(code)
    actor_rank = ACTOR_ORDER.index(actor) if actor in ACTOR_ORDER else len(ACTOR_ORDER)
    rank = CODE_ORDER.index(code) if code in CODE_ORDER else len(CODE_ORDER)
    return (actor_rank, rank)


def code_legend(code: str) -> str:
    """The one-line meaning of ``code``, for a legend strip or a tooltip."""

    return CODE_LEGEND.get(code, "unrecognised failure code")


# ---------------------------------------------------------------------------
# Chip styling
# ---------------------------------------------------------------------------


class ChipStyle(NamedTuple):
    """The three colours a chip needs."""

    fill: str
    line: str
    text: str


#: Code -> chip colours. Two hues only, and never the accent: the accent is
#: reserved for selection and for the primary action, and a status that
#: borrowed it would be indistinguishable from a selected row.
CODE_STYLE: dict[str, ChipStyle] = {
    CODE_LICENSE: ChipStyle(
        theme.STATUS_FILL["warning"],
        theme.STATUS_LINE["warning"],
        theme.WARNING_TEXT_ON_WHITE,
    ),
    CODE_CONFIG: ChipStyle(
        theme.STATUS_FILL["warning"],
        theme.STATUS_LINE["warning"],
        theme.WARNING_TEXT_ON_WHITE,
    ),
    CODE_CANCELLED: ChipStyle(
        theme.STATUS_FILL["warning"],
        theme.STATUS_LINE["warning"],
        theme.WARNING_TEXT_ON_WHITE,
    ),
    # Solid: the one failure that is about the design itself.
    CODE_LVS: ChipStyle(theme.STATUS_FAILED, theme.STATUS_FAILED, theme.ACCENT_ON),
    # Outlined: same hue, different weight, so LVS and CRS differ twice over.
    CODE_CRASH: ChipStyle(theme.SURFACE_CARD, theme.STATUS_FAILED, theme.STATUS_FAILED),
    CODE_UNKNOWN: ChipStyle(theme.SURFACE_CARD, theme.LINE_PANEL, theme.TEXT_SECONDARY),
}


def code_style(code: str) -> ChipStyle:
    """Colours for ``code``; an unknown code renders as :data:`CODE_UNKNOWN`."""

    return CODE_STYLE.get(code, CODE_STYLE[CODE_UNKNOWN])


#: Width of the code rail on a failure row (canvas 1e: ``flex: 0 0 62px``).
FAILURE_CHIP_WIDTH = 62
#: Width of the inline badge in a card header (canvas 1d).
FAILURE_CHIP_INLINE_WIDTH = 34


class FailureChip(QFrame):
    """The three-letter failure code, as a badge or as a full-height rail.

    ``stretch=True`` gives the 62px rail of canvas 1e, which spans the height
    of the failure row it labels; ``stretch=False`` gives the small inline
    badge canvas 1d puts in the card header.
    """

    def __init__(
        self,
        code: str = CODE_UNKNOWN,
        parent: QWidget | None = None,
        *,
        stretch: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self._stretch = bool(stretch)
        self._code = ""

        layout = QHBoxLayout(self)
        margin = theme.SPACE_XS if stretch else 2
        layout.setContentsMargins(margin, 2, margin, 2)
        layout.setSpacing(0)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._label)

        if stretch:
            self.setFixedWidth(FAILURE_CHIP_WIDTH)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        else:
            self.setMinimumWidth(FAILURE_CHIP_INLINE_WIDTH)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.set_code(code)

    @property
    def code(self) -> str:
        """The three-letter code currently displayed."""

        return self._code

    def text(self) -> str:
        """The rendered text -- the code itself."""

        return self._label.text()

    def set_code(self, code: str) -> None:
        """Display ``code`` and restyle to match its group."""

        code = str(code or CODE_UNKNOWN)
        self._code = code
        style = code_style(code)
        size = theme.FONT_SIZE_SECTION if self._stretch else theme.FONT_SIZE_META
        self._label.setText(code)
        self._label.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {size}px; "
            f"font-weight: {theme.FONT_WEIGHT_BOLD}; color: {style.text}; "
            "background: transparent; border: none;"
        )
        self.setStyleSheet(
            f"QFrame {{ background: {style.fill}; "
            f"border: 1px solid {style.line}; border-radius: {theme.RADIUS}px; }}"
        )
        self.setToolTip(f"{code} - {code_legend(code)}")


# ---------------------------------------------------------------------------
# Generic status chip
# ---------------------------------------------------------------------------

CHIP_TONE_PASSED = "passed"
CHIP_TONE_FAILED = "failed"
CHIP_TONE_WARNING = "warning"
#: A stage that never ran, or a fact with no verdict attached.
CHIP_TONE_MUTED = "muted"
#: A neutral outlined label, e.g. the recipe name in a card header.
CHIP_TONE_PLAIN = "plain"

_CHIP_TONE_STYLE: dict[str, ChipStyle] = {
    CHIP_TONE_PASSED: ChipStyle(
        theme.STATUS_FILL["passed"], theme.STATUS_LINE["passed"], theme.STATUS_PASSED
    ),
    CHIP_TONE_FAILED: ChipStyle(
        theme.STATUS_FILL["failed"], theme.STATUS_LINE["failed"], theme.STATUS_FAILED
    ),
    CHIP_TONE_WARNING: ChipStyle(
        theme.STATUS_FILL["warning"],
        theme.STATUS_LINE["warning"],
        theme.WARNING_TEXT_ON_WHITE,
    ),
    CHIP_TONE_MUTED: ChipStyle("transparent", theme.LINE_PANEL, theme.TEXT_DISABLED),
    CHIP_TONE_PLAIN: ChipStyle(
        "transparent", theme.LINE_STRUCTURAL, theme.TEXT_SECONDARY
    ),
}


class Chip(QLabel):
    """A small outlined pill: a stage state, a tally, a recipe label.

    Mono by default because most of what it carries is a stage name or a
    count; pass ``mono=False`` for prose such as a recipe label.
    """

    def __init__(
        self,
        text: str = "",
        tone: str = CHIP_TONE_MUTED,
        parent: QWidget | None = None,
        *,
        mono: bool = True,
        bold: bool = False,
    ) -> None:
        super().__init__(text, parent)
        self._tone = tone
        self._mono = mono
        self._bold = bold
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self._apply()

    @property
    def tone(self) -> str:
        """The tone key currently applied."""

        return self._tone

    def set_tone(self, tone: str) -> None:
        """Recolour the chip. An unknown tone falls back to muted."""

        self._tone = tone
        self._apply()

    def set_chip_text(self, text: str, tone: str | None = None) -> None:
        """Set text (and optionally tone) in one call."""

        self.setText(text)
        if tone is not None:
            self.set_tone(tone)

    def _apply(self) -> None:
        style = _CHIP_TONE_STYLE.get(self._tone, _CHIP_TONE_STYLE[CHIP_TONE_MUTED])
        family = theme.FONT_MONO if self._mono else theme.FONT_SANS
        weight = theme.FONT_WEIGHT_BOLD if self._bold else theme.FONT_WEIGHT_NORMAL
        self.setStyleSheet(
            f"QLabel {{ font-family: {family}; font-size: {theme.FONT_SIZE_META}px; "
            f"font-weight: {weight}; color: {style.text}; background: {style.fill}; "
            f"border: 1px solid {style.line}; border-radius: {theme.RADIUS}px; "
            f"padding: 1px {theme.SPACE_SM}px; }}"
        )


# ---------------------------------------------------------------------------
# Clickable, elided path
# ---------------------------------------------------------------------------

#: What :class:`PathLabel` reports as its minimum width, whatever it holds.
PATH_LABEL_MIN_WIDTH = 40


class PathLabel(QLabel):
    """A path rendered in mono, elided from the left, and clickable.

    Elides from the *left* because the informative end of a path is its tail,
    and reports a fixed small minimum width so that no path -- however long
    -- can widen the window.

    Clicking emits :attr:`clicked`; a label with no target is drawn in the
    secondary text colour, is not clickable and says why in its tooltip.
    """

    #: Emitted with the :class:`Path` when the user clicks a live label.
    clicked = pyqtSignal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        mode: Qt.TextElideMode = Qt.ElideLeft,
    ) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._path: Path | None = None
        self._mode = mode
        self._reason = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self._apply_style()

    # -- data -------------------------------------------------------------

    def set_path(
        self,
        path: Path | str | None,
        *,
        text: str | None = None,
        reason: str = "",
    ) -> None:
        """Point the label at ``path``.

        ``text`` overrides what is displayed (a run-relative form, say) while
        the click still opens ``path``. ``reason`` is shown in the tooltip of
        a label with no target, and is the only place the user learns *why*
        an output is not openable.
        """

        self._path = Path(path) if path is not None else None
        self._full_text = text if text is not None else (str(path) if path else "")
        self._reason = reason
        self._apply_style()
        self._apply_elide()
        self.setToolTip(self._tooltip())

    def set_placeholder(self, text: str, *, reason: str = "") -> None:
        """Show ``text`` as inert secondary content, e.g. ``"(none)"``."""

        self.set_path(None, text=text, reason=reason)

    @property
    def path(self) -> Path | None:
        """The click target, or ``None`` when the label is inert."""

        return self._path

    def full_text(self) -> str:
        """What the label would show if it had unlimited width."""

        return self._full_text

    def is_live(self) -> bool:
        """True when clicking does something."""

        return self._path is not None

    # -- rendering --------------------------------------------------------

    def _tooltip(self) -> str:
        if self._path is not None:
            return str(self._path)
        return self._reason or self._full_text

    def _apply_style(self) -> None:
        live = self._path is not None
        color = theme.ACCENT if live else theme.TEXT_SECONDARY
        self.setStyleSheet(
            f"QLabel {{ font-family: {theme.FONT_MONO}; "
            f"font-size: {theme.FONT_SIZE_META}px; color: {color}; }}"
        )
        self.setCursor(Qt.PointingHandCursor if live else Qt.ArrowCursor)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(PATH_LABEL_MIN_WIDTH, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """The width the *full* text wants, not the width of the elided text.

        ``QLabel.sizeHint`` measures ``text()``, which this class keeps
        elided; left alone, a label that once shrank could never grow back
        when its host widened again. Only a label given a non-Ignored size
        policy is affected, but the ratchet is invisible when it happens, so
        it is fixed here rather than at the call sites.
        """

        height = super().sizeHint().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full_text) + 2, height)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = self.width()
        if width <= 0:
            elided = self._full_text
        else:
            elided = self.fontMetrics().elidedText(self._full_text, self._mode, width)
        if elided != super().text():
            super().setText(elided)

    # -- interaction ------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if (
            self._path is not None
            and event.button() == Qt.LeftButton
            and self.rect().contains(event.pos())
        ):
            self.clicked.emit(self._path)
        super().mouseReleaseEvent(event)
