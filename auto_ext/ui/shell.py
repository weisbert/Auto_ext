"""Application shell: title bar, nav rail, content stack, status bar.

The shell is the frame every screen is mounted into. It owns four regions,
laid out exactly as artboard ``1a`` draws them::

    +--------------------------------------------------------------+ 34px
    | Auto_ext   /home/rfv/wa/Auto_ext_pro/config      [x Setup 1]  |
    +----------+---------------------------------------+-----------+
    |  Cells 8 |                                       |   Setup   |
    |  Recipes |          QStackedWidget               |  drawer   | 132px
    |  Runs    |          (one screen per nav item)    |  (520px,  | rail
    |          |                                       |   hidden) |
    +----------+---------------------------------------+-----------+
    | idle   last run 08-20 17:42 -- 2/3 passed        tasks.yaml   | 22px
    +--------------------------------------------------------------+

Deliberate non-responsibilities:

* **It runs no health checks.** :meth:`Shell.set_health_report` takes a
  :class:`~auto_ext.model.pdk.PdkHealthReport` produced by
  :mod:`auto_ext.core.health` (``check_profile`` / ``cached_or_check``) and
  renders it. The badge derives glyph, colour and count from the report's
  own ``blocking`` / ``warnings`` properties; it never re-decides what
  passing means.
* **It does not build the Setup drawer's contents.** It exposes an empty
  container (:attr:`Shell.setup_container`) plus
  :meth:`Shell.set_setup_widget`, and the open/close signal
  :attr:`Shell.setup_toggled`. What goes inside is the Setup screen's job.
* **It does not know what a page is.** :meth:`Shell.add_page` takes any
  ``QWidget``. The shell only knows a key, a label, an optional three-letter
  code for the collapsed rail, and an optional count badge.

Sizing contract: the shell's *own chrome* must never be what stops the
window shrinking to the design's 940x560 floor. The title bar and the
status bar therefore override ``minimumSizeHint`` to advertise zero width,
and the config-path label uses an ``Ignored`` horizontal size policy so a
long path elides instead of pushing the window wider. With no pages
mounted the shell's ``minimumSizeHint`` is the nav rail's width and the
two chrome bars' heights -- ``tests/ui/test_shell.py`` pins both under
200px.

Assumptions
-----------
* The rail collapse threshold (:data:`~auto_ext.ui.theme.NAV_RAIL_COLLAPSE_BELOW`)
  is measured against the shell's own width. Artboard ``1j`` says "<1200px
  wide" without naming the reference; the shell is the widest thing under
  the window frame, so the two differ only by the frame.
* Artboard ``1j`` shows three-letter codes ``CEL`` / ``REC`` / ``RUN`` for
  the collapsed rail but does not give a rule for deriving them, so an
  unspecified code falls back to the label's first three letters upper-cased
  and callers pass ``code=`` when that collides.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from auto_ext.ui.theme import (
    NAV_ITEM_HEIGHT,
    NAV_RAIL_COLLAPSE_BELOW,
    NAV_RAIL_COLLAPSED_WIDTH,
    NAV_RAIL_WIDTH,
    OBJ_APP_NAME,
    OBJ_CONFIG_PATH,
    OBJ_HEALTH_BADGE,
    OBJ_HEALTH_COUNT,
    OBJ_HEALTH_GLYPH,
    OBJ_HEALTH_TEXT,
    OBJ_NAV_COUNT,
    OBJ_NAV_ITEM,
    OBJ_NAV_LABEL,
    OBJ_NAV_RAIL,
    OBJ_SETUP_DRAWER,
    OBJ_STACK,
    OBJ_STATUS_LEFT,
    OBJ_STATUS_RIGHT,
    OBJ_STATUSBAR,
    OBJ_TITLEBAR,
    SETUP_DRAWER_WIDTH,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    STATUS_FAILED,
    STATUS_GLYPH,
    STATUS_PASSED,
    STATUS_WARNING,
    STATUSBAR_HEIGHT,
    TEXT_DISABLED,
    TITLEBAR_HEIGHT,
    build_qss,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from auto_ext.model.pdk import PdkHealthReport

__all__ = [
    "HealthBadge",
    "HealthBadgeState",
    "NavButton",
    "NavRail",
    "Shell",
    "health_badge_state",
]


def _repolish(widget: QWidget) -> None:
    """Re-evaluate ``widget``'s stylesheet after a dynamic property changed.

    Qt caches the resolved style per widget; a ``[selected="true"]`` rule
    does not re-apply on its own when the property flips.
    """

    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


# ---------------------------------------------------------------------------
# Health badge
# ---------------------------------------------------------------------------


class HealthBadgeState(NamedTuple):
    """Everything the title-bar badge renders, derived from one report."""

    glyph: str
    color: str
    count: str
    tooltip: str


def health_badge_state(report: PdkHealthReport | None) -> HealthBadgeState:
    """Reduce a PDK health report to the badge's four visible properties.

    Pure, and separate from the widget so it can be asserted without Qt.
    The verdict itself is the report's -- ``blocking`` and ``warnings`` are
    :class:`~auto_ext.model.pdk.PdkHealthReport` properties computed by
    :mod:`auto_ext.core.health`.

    ``None`` means "nobody has run the checks yet", which is neither pass
    nor fail: it renders in the neutral disabled tone with the pending
    glyph, so an unchecked project never looks like a green one.
    """

    if report is None:
        return HealthBadgeState(
            glyph=STATUS_GLYPH["pending"],
            color=TEXT_DISABLED,
            count="",
            tooltip="PDK health has not been checked yet. Open Setup to run the checks.",
        )

    total = len(report.results)
    blocking = report.blocking
    if blocking:
        return HealthBadgeState(
            glyph=STATUS_GLYPH["failed"],
            color=STATUS_FAILED,
            count=str(len(blocking)),
            tooltip=f"{len(blocking)} of {total} checks block a run. Open Setup for the fix.",
        )

    warnings = report.warnings
    if warnings:
        # No artboard draws this state; see the module Assumptions section.
        # A run may proceed, so the glyph stays the passed one and only the
        # hue moves to amber -- amber means "look at this", never "stopped".
        return HealthBadgeState(
            glyph=STATUS_GLYPH["passed"],
            color=STATUS_WARNING,
            count=str(len(warnings)),
            tooltip=f"{len(warnings)} of {total} checks warn. A run may still proceed.",
        )

    return HealthBadgeState(
        glyph=STATUS_GLYPH["passed"],
        color=STATUS_PASSED,
        count="",
        tooltip=f"All {total} checks pass.",
    )


class HealthBadge(QFrame):
    """Clickable ``[x Setup 1]`` chip in the title bar.

    A :class:`QFrame` rather than a ``QPushButton`` because the three parts
    are individually coloured -- glyph and count take the status hue, the
    word "Setup" stays primary text -- which a button's single text string
    cannot express. Keyboard activation is wired by hand for the same
    reason.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_HEALTH_BADGE)
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("open", False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_MD, 2, SPACE_MD, 2)
        layout.setSpacing(SPACE_SM)

        self._glyph = QLabel(self)
        self._glyph.setObjectName(OBJ_HEALTH_GLYPH)
        self._text = QLabel("Setup", self)
        self._text.setObjectName(OBJ_HEALTH_TEXT)
        self._count = QLabel(self)
        self._count.setObjectName(OBJ_HEALTH_COUNT)

        layout.addWidget(self._glyph)
        layout.addWidget(self._text)
        layout.addWidget(self._count)

        self._state = health_badge_state(None)
        self._apply_state()

    # -- data -------------------------------------------------------------

    def set_report(self, report: PdkHealthReport | None) -> None:
        """Render ``report``. ``None`` renders the not-yet-checked state."""

        self._state = health_badge_state(report)
        self._apply_state()

    @property
    def state(self) -> HealthBadgeState:
        return self._state

    def text(self) -> str:
        """``"x Setup 1"`` -- the badge as one string, for tests and logs."""

        parts = [self._state.glyph, "Setup"]
        if self._state.count:
            parts.append(self._state.count)
        return " ".join(parts)

    def set_open(self, is_open: bool) -> None:
        """Mark the badge as the source of an open drawer."""

        if bool(self.property("open")) == bool(is_open):
            return
        self.setProperty("open", bool(is_open))
        _repolish(self)

    def _apply_state(self) -> None:
        state = self._state
        self._glyph.setText(state.glyph)
        self._glyph.setStyleSheet(f"color: {state.color}; font-weight: 700;")
        self._count.setText(state.count)
        self._count.setStyleSheet(f"color: {state.color};")
        self._count.setVisible(bool(state.count))
        self.setToolTip(state.tooltip)

    # -- interaction ------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


class NavButton(QFrame):
    """One 30px row in the nav rail: label, optional count, selected bar.

    Selection is a 3px accent bar on the left edge plus a tinted fill, both
    from the stylesheet's ``[selected="true"]`` rule -- never a colour set
    inline, so the whole rail restyles from one place.
    """

    clicked = pyqtSignal(str)

    def __init__(
        self,
        key: str,
        label: str,
        *,
        code: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_NAV_ITEM)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(NAV_ITEM_HEIGHT)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("selected", False)
        self.setProperty("collapsed", False)

        self._key = key
        self._label_text = label
        self._code = (code or label[:3]).upper()
        self._collapsed = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_LG, 0, SPACE_LG, 0)
        layout.setSpacing(SPACE_XS)

        self._label = QLabel(label, self)
        self._label.setObjectName(OBJ_NAV_LABEL)
        self._count = QLabel(self)
        self._count.setObjectName(OBJ_NAV_COUNT)
        self._count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._count.setVisible(False)

        layout.addWidget(self._label)
        layout.addStretch(1)
        layout.addWidget(self._count)

    # -- data -------------------------------------------------------------

    @property
    def key(self) -> str:
        return self._key

    @property
    def label(self) -> str:
        return self._label_text

    @property
    def code(self) -> str:
        return self._code

    def set_count(self, count: int | None) -> None:
        """Right-hand number, or ``None`` to show nothing at all.

        Zero is a real answer ("no cells yet") and is shown; ``None`` means
        "this screen does not count anything".
        """

        self._count.setText("" if count is None else str(count))
        self._count.setVisible(count is not None and not self._collapsed)

    def count_text(self) -> str:
        return self._count.text()

    def set_selected(self, selected: bool) -> None:
        if bool(self.property("selected")) == bool(selected):
            return
        self.setProperty("selected", bool(selected))
        _repolish(self)

    def is_selected(self) -> bool:
        return bool(self.property("selected"))

    def set_collapsed(self, collapsed: bool) -> None:
        """Swap the label for the three-letter code and drop the count."""

        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.setProperty("collapsed", collapsed)

        layout = self.layout()
        if collapsed:
            layout.setContentsMargins(0, 0, 0, 0)
            # The label has to take the whole row for AlignCenter to mean
            # anything, so the spacer that pushes the count right when
            # expanded gives up its stretch here.
            layout.setStretch(0, 1)
            layout.setStretch(1, 0)
            self._label.setText(self._code)
            self._label.setAlignment(Qt.AlignCenter)
            self._count.setVisible(False)
            self.setToolTip(self._label_text)
        else:
            layout.setContentsMargins(SPACE_LG, 0, SPACE_LG, 0)
            layout.setStretch(0, 0)
            layout.setStretch(1, 1)
            self._label.setText(self._label_text)
            self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._count.setVisible(bool(self._count.text()))
            self.setToolTip("")
        _repolish(self)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def displayed_text(self) -> str:
        return self._label.text()

    # -- interaction ------------------------------------------------------

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self._key)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.clicked.emit(self._key)
            event.accept()
            return
        super().keyPressEvent(event)


class NavRail(QFrame):
    """The 132px (44px collapsed) column of :class:`NavButton` rows."""

    item_clicked = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_NAV_RAIL)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedWidth(NAV_RAIL_WIDTH)

        self._collapsed = False
        self._buttons: dict[str, NavButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, SPACE_XS, 0, 0)
        layout.setSpacing(0)
        layout.addStretch(1)

    def add_item(self, key: str, label: str, *, code: str | None = None) -> NavButton:
        if key in self._buttons:
            raise ValueError(f"nav item {key!r} already exists")
        button = NavButton(key, label, code=code, parent=self)
        button.set_collapsed(self._collapsed)
        button.clicked.connect(self.item_clicked)
        layout = self.layout()
        # Insert before the trailing stretch so items stay top-aligned.
        layout.insertWidget(layout.count() - 1, button)
        self._buttons[key] = button
        return button

    def remove_item(self, key: str) -> None:
        button = self._buttons.pop(key, None)
        if button is None:
            return
        self.layout().removeWidget(button)
        button.setParent(None)
        button.deleteLater()

    def button(self, key: str) -> NavButton | None:
        return self._buttons.get(key)

    def keys(self) -> list[str]:
        return list(self._buttons)

    def set_selected(self, key: str | None) -> None:
        for item_key, button in self._buttons.items():
            button.set_selected(item_key == key)

    def set_collapsed(self, collapsed: bool) -> None:
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self.setFixedWidth(NAV_RAIL_COLLAPSED_WIDTH if collapsed else NAV_RAIL_WIDTH)
        for button in self._buttons.values():
            button.set_collapsed(collapsed)

    def is_collapsed(self) -> bool:
        return self._collapsed


# ---------------------------------------------------------------------------
# Chrome bars
# ---------------------------------------------------------------------------


class _ElidedLabel(QLabel):
    """A label that shrinks to nothing and elides instead of widening.

    The config path is the one string in the title bar that can be
    arbitrarily long. An ordinary ``QLabel`` would report its full text
    width as a minimum and drag the window's minimum width along with it,
    which is precisely the failure mode the 940px floor exists to prevent.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        *,
        mode: Qt.TextElideMode = Qt.ElideLeft,
    ) -> None:
        super().__init__(parent)
        self._full_text = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self._apply_elide()

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, super().minimumSizeHint().height())

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


class _ChromeBar(QFrame):
    """A fixed-height strip that never dictates the window's width."""

    def __init__(self, object_name: str, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(height)
        self._height = height

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(0, self._height)


# ---------------------------------------------------------------------------
# Shell
# ---------------------------------------------------------------------------


class Shell(QWidget):
    """Title bar + nav rail + content stack + status bar, with a Setup drawer.

    Register a screen with :meth:`add_page`; the shell creates its nav item
    and pushes the widget onto the stack::

        shell = Shell()
        shell.add_page("cells", "Cells", CellsScreen(), code="CEL", count=8)
        shell.add_page("recipes", "Recipes", RecipesScreen(), code="REC")
        shell.page_changed.connect(on_page_changed)

    The first page registered becomes current, so a freshly built shell is
    never blank.
    """

    #: Emitted with the new page key whenever the visible page changes.
    page_changed = pyqtSignal(str)
    #: Emitted with the drawer's new open state.
    setup_toggled = pyqtSignal(bool)
    #: Emitted before the drawer toggles, so a host can refresh the report.
    health_badge_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        #: When true the rail collapses on its own below
        #: :data:`~auto_ext.ui.theme.NAV_RAIL_COLLAPSE_BELOW` px. Set False to
        #: drive :meth:`set_rail_collapsed` by hand.
        self.auto_collapse_rail = True

        self._pages: dict[str, QWidget] = {}
        self._current_key: str | None = None
        self._setup_widget: QWidget | None = None
        self._setup_open = False
        self._health_report: PdkHealthReport | None = None
        self._initial_focus_placed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_title_bar())

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._nav = NavRail(body)
        self._nav.item_clicked.connect(self.set_current_page)
        body_layout.addWidget(self._nav)

        self._stack = QStackedWidget(body)
        self._stack.setObjectName(OBJ_STACK)
        body_layout.addWidget(self._stack, 1)

        body_layout.addWidget(self._build_setup_drawer(body))

        outer.addWidget(body, 1)
        outer.addWidget(self._build_status_bar())

        self.setStyleSheet(build_qss())

    # -- construction -----------------------------------------------------

    def _build_title_bar(self) -> QWidget:
        bar = _ChromeBar(OBJ_TITLEBAR, TITLEBAR_HEIGHT, self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(SPACE_LG, 0, SPACE_LG, 0)
        layout.setSpacing(SPACE_XL)

        self._app_name = QLabel("Auto_ext", bar)
        self._app_name.setObjectName(OBJ_APP_NAME)
        layout.addWidget(self._app_name)

        self._config_path = _ElidedLabel("", bar)
        self._config_path.setObjectName(OBJ_CONFIG_PATH)
        layout.addWidget(self._config_path, 1)

        self._badge = HealthBadge(bar)
        self._badge.clicked.connect(self._on_badge_clicked)
        layout.addWidget(self._badge)

        self._title_bar = bar
        return bar

    def _build_status_bar(self) -> QWidget:
        bar = _ChromeBar(OBJ_STATUSBAR, STATUSBAR_HEIGHT, self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(SPACE_MD, 0, SPACE_MD, 0)
        layout.setSpacing(SPACE_XL)

        self._status_left = _ElidedLabel("", bar, mode=Qt.ElideRight)
        self._status_left.setObjectName(OBJ_STATUS_LEFT)
        layout.addWidget(self._status_left, 1)

        self._status_right = _ElidedLabel("", bar, mode=Qt.ElideRight)
        self._status_right.setObjectName(OBJ_STATUS_RIGHT)
        self._status_right.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self._status_right, 1)

        self._status_bar = bar
        return bar

    def _build_setup_drawer(self, parent: QWidget) -> QWidget:
        drawer = QFrame(parent)
        drawer.setObjectName(OBJ_SETUP_DRAWER)
        drawer.setFrameShape(QFrame.NoFrame)
        drawer.setFixedWidth(SETUP_DRAWER_WIDTH)
        drawer.setVisible(False)

        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        container = QWidget(drawer)
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        layout.addWidget(container, 1)

        self._setup_drawer = drawer
        self._setup_container = container
        return drawer

    # -- pages ------------------------------------------------------------

    def add_page(
        self,
        key: str,
        label: str,
        widget: QWidget,
        *,
        code: str | None = None,
        count: int | None = None,
    ) -> QWidget:
        """Register ``widget`` as the screen behind a new nav item.

        ``code`` is the three-letter label used once the rail collapses;
        it defaults to the first three letters of ``label``, which is only
        good enough when no two labels share a prefix. ``count`` is the
        number shown on the right of the nav item, or ``None`` for none.

        Returns ``widget`` so a caller can chain. Raises ``ValueError`` on a
        duplicate key.
        """

        if key in self._pages:
            raise ValueError(f"page {key!r} already exists")
        self._pages[key] = widget
        self._stack.addWidget(widget)
        button = self._nav.add_item(key, label, code=code)
        button.set_count(count)
        if self._current_key is None:
            self.set_current_page(key)
        return widget

    def remove_page(self, key: str) -> QWidget | None:
        """Unregister a page, returning the widget (ownership goes back to
        the caller). Selection moves to the first remaining page."""

        widget = self._pages.pop(key, None)
        if widget is None:
            return None
        self._stack.removeWidget(widget)
        widget.setParent(None)
        self._nav.remove_item(key)
        if self._current_key == key:
            self._current_key = None
            remaining = self.page_keys()
            if remaining:
                self.set_current_page(remaining[0])
            else:
                self._nav.set_selected(None)
        return widget

    def set_current_page(self, key: str) -> None:
        """Show the page registered as ``key``. No-op if already current."""

        widget = self._pages.get(key)
        if widget is None:
            raise KeyError(f"no page registered as {key!r}")
        if self._current_key == key:
            return
        self._current_key = key
        self._stack.setCurrentWidget(widget)
        self._nav.set_selected(key)
        self.page_changed.emit(key)

    def current_page_key(self) -> str | None:
        return self._current_key

    def current_page(self) -> QWidget | None:
        if self._current_key is None:
            return None
        return self._pages[self._current_key]

    def page(self, key: str) -> QWidget | None:
        return self._pages.get(key)

    def page_keys(self) -> list[str]:
        """Page keys in registration order -- the same order the rail shows."""

        return list(self._pages)

    def set_page_count(self, key: str, count: int | None) -> None:
        """Update the number on a nav item (cell count, run count, ...)."""

        button = self._nav.button(key)
        if button is None:
            raise KeyError(f"no page registered as {key!r}")
        button.set_count(count)

    def nav_button(self, key: str) -> NavButton | None:
        return self._nav.button(key)

    @property
    def stack(self) -> QStackedWidget:
        return self._stack

    @property
    def nav_rail(self) -> NavRail:
        return self._nav

    # -- rail collapse ----------------------------------------------------

    def set_rail_collapsed(self, collapsed: bool) -> None:
        self._nav.set_collapsed(collapsed)

    def is_rail_collapsed(self) -> bool:
        return self._nav.is_collapsed()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self.auto_collapse_rail:
            self._nav.set_collapsed(event.size().width() < NAV_RAIL_COLLAPSE_BELOW)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Start focus on the screen, not on the chrome.

        The health badge is the first tab-focusable widget in the tree, so
        without this the window opens with a focus ring drawn around
        "Setup" -- pointing the eye at the one control the user is least
        likely to want. Only on the first show; after that, focus is the
        user's.
        """

        super().showEvent(event)
        if not self._initial_focus_placed and self._current_key is not None:
            self._initial_focus_placed = True
            self._pages[self._current_key].setFocus(Qt.OtherFocusReason)

    # -- title bar / status bar text --------------------------------------

    def set_app_name(self, text: str) -> None:
        self._app_name.setText(text)

    def app_name(self) -> str:
        return self._app_name.text()

    def set_config_path(self, path: str | Path | None) -> None:
        """Show the loaded config dir, elided from the left when too long."""

        self._config_path.set_full_text("" if path is None else str(path))

    def config_path(self) -> str:
        return self._config_path.full_text()

    def set_status(self, left: str | None = None, right: str | None = None) -> None:
        """Set either half of the status bar. ``None`` leaves that half alone."""

        if left is not None:
            self._status_left.set_full_text(left)
        if right is not None:
            self._status_right.set_full_text(right)

    def status_left(self) -> str:
        return self._status_left.full_text()

    def status_right(self) -> str:
        return self._status_right.full_text()

    # -- health badge -----------------------------------------------------

    def set_health_report(self, report: PdkHealthReport | None) -> None:
        """Render a report from :mod:`auto_ext.core.health` on the badge."""

        self._health_report = report
        self._badge.set_report(report)

    def health_report(self) -> PdkHealthReport | None:
        return self._health_report

    def health_badge_text(self) -> str:
        return self._badge.text()

    @property
    def health_badge(self) -> HealthBadge:
        return self._badge

    def _on_badge_clicked(self) -> None:
        self.health_badge_clicked.emit()
        self.toggle_setup()

    # -- setup drawer -----------------------------------------------------

    @property
    def setup_container(self) -> QWidget:
        """The empty widget the Setup screen mounts itself into.

        It already has a zero-margin :class:`QVBoxLayout`; add to that, or
        hand the whole widget over via :meth:`set_setup_widget`.
        """

        return self._setup_container

    @property
    def setup_drawer(self) -> QFrame:
        return self._setup_drawer

    def set_setup_widget(self, widget: QWidget | None) -> None:
        """Put ``widget`` in the drawer, detaching whatever was there.

        The container takes ownership of ``widget``; the previous occupant
        is reparented out and handed back to whoever created it.
        """

        previous = self._setup_widget
        if previous is not None and previous is not widget:
            self._setup_container.layout().removeWidget(previous)
            previous.setParent(None)
        self._setup_widget = widget
        if widget is not None:
            self._setup_container.layout().addWidget(widget)

    def setup_widget(self) -> QWidget | None:
        return self._setup_widget

    def set_setup_open(self, is_open: bool) -> None:
        """Open or close the drawer, emitting :attr:`setup_toggled` on change.

        Tracked in a flag rather than read back from ``isVisible()``: a
        child of a window that has not been shown yet reports invisible
        whatever it was told, which would make the state unreadable in
        tests and before the first ``show()``.
        """

        is_open = bool(is_open)
        if is_open == self._setup_open:
            return
        self._setup_open = is_open
        self._setup_drawer.setVisible(is_open)
        self._badge.set_open(is_open)
        self.setup_toggled.emit(is_open)

    def is_setup_open(self) -> bool:
        return self._setup_open

    def toggle_setup(self) -> None:
        self.set_setup_open(not self.is_setup_open())
