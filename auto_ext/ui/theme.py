"""Design tokens and the application stylesheet.

This module is the single place the redesign's token table (design canvas
artboard ``1k``) exists in code. Every other UI module imports names from
here instead of writing a hex literal, a pixel count or a font family
inline. It is a leaf: it imports nothing from :mod:`auto_ext.ui` except
:data:`auto_ext.ui.models.STATUS_COLOR`, and nothing from ``core`` or
``model`` at all.

Three rules the canvas states outright, all enforced by
``tests/ui/test_theme.py``:

* **The accent never means pass, fail or warning.** ``#1f5fa8`` and its
  companions are selection and primary action only. Status has its own
  scale and nothing may borrow from the accent group.
* **The status scale is inherited verbatim** from
  :data:`auto_ext.ui.models.STATUS_COLOR` rather than copied, so the two
  cannot drift. :data:`STATUS_TEXT` re-exports it with exactly one
  substitution -- see :data:`RUNNING_TEXT`.
* **The four failure classes reuse the same two hues.** ``LIC`` and
  ``CFG`` are amber (fix the environment), ``LVS`` and ``CRS`` are red
  (fix the design or the tool). The three-letter code, not the colour, is
  what tells them apart, so the distinction survives greyscale printing
  and colour-blind readers.

Assumptions
-----------
Collected here rather than scattered through the file. Each is a value the
implementation needs but the ``1k`` swatch table does not itself list.

* :data:`SURFACE_BUTTON`, :data:`LINE_SEPARATOR` and :data:`LINE_WINDOW`
  are read off artboards ``1a`` / ``1e`` / ``1j`` (raised control on the
  title bar, toolbar separator rule, outer window frame). They are
  consistent across every artboard that draws them, but they are not in
  the ``1k`` list, so treat them as observed rather than specified.
* :data:`NAV_RAIL_COLLAPSE_BELOW` comes from artboard ``1j``'s prose
  ("Nav rail drops labels for 3-letter codes at <1200px wide"), which
  gives the threshold but not whether it is measured against the window
  or the rail's own container. It is applied to the shell's own width.
* :data:`ACCENT_ON` (text drawn on top of an accent fill) is not a listed
  token; the artboards always draw it as pure white.
* No artboard draws the health badge in a warnings-but-runnable state, so
  :mod:`auto_ext.ui.shell` renders it as the passed glyph in the warning
  hue. That combination is inferred, not specified.
"""

from __future__ import annotations

from auto_ext.ui.models import STATUS_COLOR

# ---------------------------------------------------------------------------
# Surfaces and lines -- neutral, slightly warm
# ---------------------------------------------------------------------------

#: Title bar fill. Shared with the status bar; both are window chrome.
SURFACE_TITLEBAR = "#dededa"
#: Status bar fill. Same value as :data:`SURFACE_TITLEBAR`, named apart so a
#: later divergence needs no call-site edits.
SURFACE_STATUSBAR = "#dededa"
#: Left navigation rail fill.
SURFACE_NAV_RAIL = "#e6e6e3"
#: Toolbar, run bar and group-box body fill.
SURFACE_TOOLBAR = "#eeeeeb"
#: Table header and group-box title fill.
SURFACE_TABLE_HEADER = "#e4e4e1"
#: The page behind everything a screen draws.
SURFACE_PAGE = "#f7f7f5"
#: Table body, card and text-input fill.
SURFACE_CARD = "#ffffff"
#: Raised control sitting on the title bar (the health badge). Observed on
#: artboards 1a / 1h / 1j; see the module Assumptions section.
SURFACE_BUTTON = "#f2f2f0"

#: Structural border: between chrome regions, around a panel.
LINE_STRUCTURAL = "#b9b9b4"
#: Border used inside a panel, one step lighter than structural.
LINE_PANEL = "#d3d3ce"
#: Rule between two table rows.
LINE_ROW = "#e8e8e4"
#: Short vertical rule that groups toolbar buttons. Observed, not listed.
LINE_SEPARATOR = "#c9c9c3"
#: Outer window frame. Observed, not listed.
LINE_WINDOW = "#9c9c96"

#: Log view background -- the one dark surface in the app.
SURFACE_LOG = "#1e1f1c"
#: Log view text, paired with :data:`SURFACE_LOG`.
TEXT_LOG = "#d6d6d0"


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

#: Primary text.
TEXT_PRIMARY = "#1c1c1a"
#: Secondary text: paths, column labels, meta.
TEXT_SECONDARY = "#5c5c58"
#: Disabled text, placeholders, and "never run".
TEXT_DISABLED = "#8a8a85"


# ---------------------------------------------------------------------------
# Accent -- selection and primary action ONLY
# ---------------------------------------------------------------------------

#: The accent. Run button fill, links, focus ring, active nav bar.
ACCENT = "#1f5fa8"
#: Accent border, and the accent's pressed state.
ACCENT_PRESSED = "#14487f"
#: Selected table row, active nav item fill.
ACCENT_SELECTION = "#cfe0f4"
#: Very light accent wash for the row belonging to the current run.
ACCENT_TINT = "#e8f1fb"
#: Text drawn on top of an accent fill. Inferred; see Assumptions.
ACCENT_ON = "#ffffff"


def accent_colors() -> frozenset[str]:
    """Every colour reserved for selection and primary action.

    Exposed so a test -- and any future lint -- can assert that no status
    or failure-class token borrows from this group.
    """

    return frozenset({ACCENT, ACCENT_PRESSED, ACCENT_SELECTION, ACCENT_TINT})


# ---------------------------------------------------------------------------
# Status scale -- inherited from ui.models.STATUS_COLOR
# ---------------------------------------------------------------------------

#: ``running`` as text. :data:`auto_ext.ui.models.STATUS_COLOR` carries
#: ``#0080ff``, which is a fine fill but fails contrast as 11-12px text on
#: the white table body (roughly 3:1 against ``#ffffff``). Darkening it to
#: ``#0f6fd1`` clears 4.5:1 while staying the same hue, so a row that reads
#: "running" in the table and in the status bar is recognisably one colour.
#: This is the *only* deviation from ``STATUS_COLOR``.
RUNNING_TEXT = "#0f6fd1"

#: Status string -> text colour. Inherited verbatim from
#: :data:`auto_ext.ui.models.STATUS_COLOR` with :data:`RUNNING_TEXT`
#: substituted for ``running``. Never edit a value here: edit
#: ``ui/models.py`` and both the old tabs and the new screens move together.
STATUS_TEXT: dict[str, str] = {**STATUS_COLOR, "running": RUNNING_TEXT}

#: Named handles onto :data:`STATUS_TEXT` for the screens that only need one
#: hue. ``STATUS_WARNING`` is the amber the canvas assigns to cancelled runs,
#: env overrides, manual template edits and stale hunks alike.
STATUS_PASSED = STATUS_TEXT["passed"]
STATUS_FAILED = STATUS_TEXT["failed"]
STATUS_WARNING = STATUS_TEXT["cancelled"]
STATUS_RUNNING = STATUS_TEXT["running"]
STATUS_SKIPPED = STATUS_TEXT["skipped"]
STATUS_DRY_RUN = STATUS_TEXT["dry_run"]

#: Amber as *text on white*. ``#d69016`` is a fill and a glyph colour; at
#: body size on ``#ffffff`` it does not clear 4.5:1, so prose uses this.
WARNING_TEXT_ON_WHITE = "#a06a0d"

#: Semantic key -> chip/badge fill. Keys are the three semantics that get a
#: filled chip anywhere in the design; everything else is text-only.
STATUS_FILL: dict[str, str] = {
    "passed": "#e2f0e2",
    "failed": "#f6e0e0",
    "warning": "#fdf4e3",
}

#: Semantic key -> chip/badge border, paired with :data:`STATUS_FILL`.
STATUS_LINE: dict[str, str] = {
    "passed": "#a8cfa8",
    "failed": "#dda9a9",
    "warning": "#f0dcb4",
}

#: Status string -> glyph. Restricted to characters DejaVu ships, so an
#: X11-forwarded session never renders a tofu box. No emoji, ever.
STATUS_GLYPH: dict[str, str] = {
    "passed": "✓",  # check
    "failed": "✗",  # ballot X
    "running": "▶",  # right-pointing triangle
    "cancelled": "■",  # filled square
    "skipped": "–",  # en dash
    "pending": "·",  # middle dot
    "dry_run": "…",  # horizontal ellipsis
}

#: Three-letter failure code -> colour. Two hues on purpose: amber means
#: "fix the environment", red means "fix the design or the tool". The code
#: itself is the discriminator, which is why LIC and CFG share a colour.
FAILURE_CODE_COLOR: dict[str, str] = {
    "LIC": STATUS_WARNING,
    "CFG": STATUS_WARNING,
    "LVS": STATUS_FAILED,
    "CRS": STATUS_FAILED,
}


def status_color(status: str) -> str:
    """Text colour for ``status``, falling back to secondary text.

    An unknown status is a state this build does not model yet; painting it
    neutral is honest, whereas defaulting to red would invent a verdict.
    """

    return STATUS_TEXT.get(status, TEXT_SECONDARY)


# ---------------------------------------------------------------------------
# Type -- two families, both present on CentOS 7
# ---------------------------------------------------------------------------

#: Sans families in fallback order. DejaVu ships with CentOS 7; Liberation is
#: the fallback on hosts that trimmed it.
FONT_SANS_FAMILIES = ("DejaVu Sans", "Liberation Sans", "sans-serif")
#: Mono families in fallback order. Used for every path, cell name, numeric
#: value and log line.
FONT_MONO_FAMILIES = ("DejaVu Sans Mono", "Liberation Mono", "monospace")


def _family_list(families: tuple[str, ...]) -> str:
    """Render a family tuple as a QSS ``font-family`` value."""

    return ", ".join('"' + f + '"' if " " in f else f for f in families)


#: QSS-ready ``font-family`` value for the sans stack.
FONT_SANS = _family_list(FONT_SANS_FAMILIES)
#: QSS-ready ``font-family`` value for the mono stack.
FONT_MONO = _family_list(FONT_MONO_FAMILIES)

#: Run title ("Run 137") -- sans 15/700.
FONT_SIZE_TITLE = 15
#: Screen and card titles -- sans 13/600.
FONT_SIZE_SECTION = 13
#: Body text, form labels, buttons -- sans 12/400.
FONT_SIZE_BODY = 12
#: Column headers, hints, meta -- sans 11/400. This is the floor.
FONT_SIZE_META = 11
#: Cell names, paths, values, logs -- mono 12/400.
FONT_SIZE_MONO = 12
#: The one oversized string in the app: the LVS verdict on a result card.
FONT_SIZE_MONO_HERO = 20
#: Nothing renders below this. Asserted by the theme tests.
FONT_SIZE_MIN = 11

FONT_WEIGHT_NORMAL = 400
FONT_WEIGHT_SEMIBOLD = 600
FONT_WEIGHT_BOLD = 700


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

#: The whole spacing vocabulary. A gap not on this ramp is a bug.
SPACING_RAMP = (2, 4, 6, 8, 10, 12, 16)
SPACE_XXS, SPACE_XS, SPACE_SM, SPACE_MD, SPACE_LG, SPACE_XL, SPACE_XXL = SPACING_RAMP

#: Table body row.
ROW_HEIGHT = 24
#: The taller row that carries stage chips.
STAGE_CHIP_ROW_HEIGHT = 26
#: Table header row.
TABLE_HEADER_HEIGHT = 22
#: Screen toolbar.
TOOLBAR_HEIGHT = 32
#: Shell title bar.
TITLEBAR_HEIGHT = 34
#: Shell status bar.
STATUSBAR_HEIGHT = 22
#: One navigation item.
NAV_ITEM_HEIGHT = 30
#: Navigation rail, labels shown.
NAV_RAIL_WIDTH = 132
#: Navigation rail, collapsed to three-letter codes.
NAV_RAIL_COLLAPSED_WIDTH = 44
#: Shell width below which the rail collapses. See Assumptions.
NAV_RAIL_COLLAPSE_BELOW = 1200
#: Setup drawer width when open.
SETUP_DRAWER_WIDTH = 520

#: Table cell padding, vertical / horizontal.
CELL_PADDING_V = 0
CELL_PADDING_H = 8
#: Ordinary push button padding.
BUTTON_PADDING_V = 3
BUTTON_PADDING_H = 10
#: The primary action ("Run 3 cells") is the one bigger target.
PRIMARY_BUTTON_PADDING_V = 6
PRIMARY_BUTTON_PADDING_H = 16

#: Corner radius everywhere -- square, no exceptions but the next one.
RADIUS = 0
#: Push buttons get the single softened corner in the design.
RADIUS_BUTTON = 2

#: Focus is a 1px accent border and nothing else. No glow, no shadow: both
#: force a large repaint, and this app is used over X11 forwarding.
FOCUS_BORDER_WIDTH = 1
FOCUS_BORDER_COLOR = ACCENT
#: Left bar marking a selected nav item / selected card.
SELECTED_BAR_WIDTH = 3

#: The hard floor for the main window.
WINDOW_MIN_WIDTH = 940
WINDOW_MIN_HEIGHT = 560


# ---------------------------------------------------------------------------
# Object names
# ---------------------------------------------------------------------------

#: Object names the shell assigns, re-exported so screens can target the same
#: chrome from their own stylesheets without re-typing string literals.
OBJ_TITLEBAR = "shellTitleBar"
OBJ_STATUSBAR = "shellStatusBar"
OBJ_NAV_RAIL = "shellNavRail"
OBJ_NAV_ITEM = "shellNavItem"
OBJ_NAV_LABEL = "shellNavLabel"
OBJ_NAV_COUNT = "shellNavCount"

#: The Recipes form's row container, so a state can be styled without
#: the label and the editor each carrying their own copy of the rule.
OBJ_OPTION_ROW = "optionRow"
#: The bordered "promoted" / "changed" tag beside a row's label.
OBJ_STATE_TAG = "optionStateTag"
#: ``was 55.0`` in mono, so the value a row left is still readable.
OBJ_WAS_VALUE = "optionWasValue"
#: Why a row is disabled, said ON the row rather than only on hover.
OBJ_WHY_DISABLED = "optionWhyDisabled"
#: The focused-row detail strip and its two labels. Spec ``M`` section 4.
OBJ_DETAIL_BAR = "focusDetailBar"
OBJ_DETAIL_PATH = "focusDetailPath"
OBJ_DETAIL_PROSE = "focusDetailProse"
#: The band label on a group header while a search is live. Artboard ``J``.
OBJ_SEARCH_BAND = "searchBand"
#: The ``NOT ON THIS SCREEN`` strip and its navigate button.
OBJ_ELSEWHERE_BAND = "elsewhereBand"
OBJ_STACK = "shellStack"
OBJ_HEALTH_BADGE = "shellHealthBadge"
OBJ_HEALTH_GLYPH = "shellHealthGlyph"
OBJ_HEALTH_TEXT = "shellHealthText"
OBJ_HEALTH_COUNT = "shellHealthCount"
OBJ_SETUP_DRAWER = "shellSetupDrawer"
OBJ_APP_NAME = "shellAppName"
OBJ_CONFIG_PATH = "shellConfigPath"
OBJ_STATUS_LEFT = "shellStatusLeft"
OBJ_STATUS_RIGHT = "shellStatusRight"


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------


def build_qss() -> str:
    """Return the whole application stylesheet as one QSS string.

    Applied once, at :class:`~auto_ext.ui.main_window.MainWindow` level, so
    it reaches the menu bar, the shell, every screen in the stack, and any
    dialog parented to the window. :class:`~auto_ext.ui.shell.Shell` also
    applies it to itself, so a shell built standalone (a test, a future
    second window) is styled without a host.

    Deliberately *not* a universal ``QWidget { background: ... }`` rule:
    that repaints every widget in the tree on every style recalculation,
    which is exactly the kind of full-surface redraw that stutters over an
    X11-forwarded link. Backgrounds are set per class and per object name.
    """

    return _QSS


_QSS = f"""
/* ---- base ------------------------------------------------------------- */
QWidget {{
    font-family: {FONT_SANS};
    font-size: {FONT_SIZE_BODY}px;
    color: {TEXT_PRIMARY};
}}
QMainWindow, QDialog {{
    background: {SURFACE_PAGE};
}}
QWidget:disabled {{
    color: {TEXT_DISABLED};
}}
QToolTip {{
    background: {SURFACE_CARD};
    color: {TEXT_PRIMARY};
    border: 1px solid {LINE_STRUCTURAL};
    padding: {SPACE_XXS}px {SPACE_SM}px;
}}

/* ---- shell chrome ----------------------------------------------------- */
QFrame#{OBJ_TITLEBAR} {{
    background: {SURFACE_TITLEBAR};
    border: none;
    border-bottom: 1px solid {LINE_STRUCTURAL};
}}
QLabel#{OBJ_APP_NAME} {{
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}
QLabel#{OBJ_CONFIG_PATH} {{
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_SECONDARY};
}}
QFrame#{OBJ_STATUSBAR} {{
    background: {SURFACE_STATUSBAR};
    border: none;
    border-top: 1px solid {LINE_STRUCTURAL};
}}
QLabel#{OBJ_STATUS_LEFT}, QLabel#{OBJ_STATUS_RIGHT} {{
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_SECONDARY};
}}

QFrame#{OBJ_HEALTH_BADGE} {{
    background: {SURFACE_BUTTON};
    border: 1px solid {LINE_STRUCTURAL};
    border-radius: {RADIUS}px;
}}
QFrame#{OBJ_HEALTH_BADGE}[open="true"] {{
    background: {ACCENT_SELECTION};
    border: 1px solid {ACCENT};
}}
QFrame#{OBJ_HEALTH_BADGE}:focus {{
    border: {FOCUS_BORDER_WIDTH}px solid {FOCUS_BORDER_COLOR};
}}
QLabel#{OBJ_HEALTH_TEXT} {{
    font-size: {FONT_SIZE_META}px;
}}
QLabel#{OBJ_HEALTH_COUNT} {{
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_META}px;
}}

QFrame#{OBJ_NAV_RAIL} {{
    background: {SURFACE_NAV_RAIL};
    border: none;
    border-right: 1px solid {LINE_STRUCTURAL};
}}
QFrame#{OBJ_NAV_ITEM} {{
    background: transparent;
    border: none;
    border-left: {SELECTED_BAR_WIDTH}px solid transparent;
}}
QFrame#{OBJ_NAV_ITEM}[selected="true"] {{
    background: {ACCENT_SELECTION};
    border-left: {SELECTED_BAR_WIDTH}px solid {ACCENT};
}}
QFrame#{OBJ_NAV_ITEM}:focus {{
    border-left: {SELECTED_BAR_WIDTH}px solid {ACCENT_PRESSED};
}}
QLabel#{OBJ_NAV_LABEL} {{
    color: {TEXT_SECONDARY};
}}
QFrame#{OBJ_NAV_ITEM}[selected="true"] QLabel#{OBJ_NAV_LABEL} {{
    color: {TEXT_PRIMARY};
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}
QLabel#{OBJ_NAV_COUNT} {{
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_DISABLED};
}}
/* Row states -- artboard H. One channel, at x=0, and it means "a person set
   this". The amber channel lives at the far right and is drawn by the
   widgets themselves; the two never share a pixel. */
QWidget#{OBJ_OPTION_ROW} {{
    border-left: {SELECTED_BAR_WIDTH}px solid transparent;
    background: transparent;
}}
QWidget#{OBJ_OPTION_ROW}[state="changed"] {{
    border-left: {SELECTED_BAR_WIDTH}px solid {ACCENT};
}}
QWidget#{OBJ_OPTION_ROW}[state="promoted"] {{
    border-left: {SELECTED_BAR_WIDTH}px solid {ACCENT};
    background: {ACCENT_TINT};
}}
QWidget#{OBJ_OPTION_ROW}[state="inapplicable"] {{
    border-left: {SELECTED_BAR_WIDTH}px solid transparent;
    background: transparent;
}}
QLabel#{OBJ_STATE_TAG} {{
    font-size: {FONT_SIZE_META}px;
    color: {ACCENT};
    border: 1px solid {ACCENT};
    border-radius: 2px;
    padding: 0px 3px;
}}
QLabel#{OBJ_WAS_VALUE} {{
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_SECONDARY};
}}
QLabel#{OBJ_WHY_DISABLED} {{
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_DISABLED};
}}
QFrame#{OBJ_DETAIL_BAR} {{
    background: {SURFACE_CARD};
    border-top: 1px solid {LINE_STRUCTURAL};
}}
QLabel#{OBJ_DETAIL_PATH} {{
    font-family: {FONT_MONO};
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_PRIMARY};
}}
QLabel#{OBJ_DETAIL_PROSE} {{
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_SECONDARY};
}}
QLabel#{OBJ_SEARCH_BAND} {{
    font-size: {FONT_SIZE_META}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
    color: {ACCENT};
}}
QFrame#{OBJ_ELSEWHERE_BAND} {{
    background: {SURFACE_TABLE_HEADER};
    border: 1px solid {LINE_PANEL};
}}
QFrame#{OBJ_NAV_ITEM}[selected="true"] QLabel#{OBJ_NAV_COUNT} {{
    color: {TEXT_SECONDARY};
}}
QFrame#{OBJ_NAV_ITEM}[collapsed="true"] QLabel#{OBJ_NAV_LABEL} {{
    font-size: {FONT_SIZE_META}px;
}}
QFrame#{OBJ_NAV_ITEM}[collapsed="true"][selected="true"] QLabel#{OBJ_NAV_LABEL} {{
    font-weight: {FONT_WEIGHT_BOLD};
}}

QStackedWidget#{OBJ_STACK} {{
    background: {SURFACE_PAGE};
}}
QFrame#{OBJ_SETUP_DRAWER} {{
    background: {SURFACE_PAGE};
    border: none;
    border-left: 1px solid {LINE_WINDOW};
}}

/* ---- controls --------------------------------------------------------- */
QPushButton {{
    background: {SURFACE_PAGE};
    border: 1px solid {LINE_STRUCTURAL};
    border-radius: {RADIUS_BUTTON}px;
    padding: {BUTTON_PADDING_V}px {BUTTON_PADDING_H}px;
}}
QPushButton:hover {{
    background: {SURFACE_BUTTON};
}}
QPushButton:pressed {{
    background: {SURFACE_TABLE_HEADER};
}}
QPushButton:focus {{
    border: {FOCUS_BORDER_WIDTH}px solid {FOCUS_BORDER_COLOR};
}}
QPushButton:disabled {{
    color: {TEXT_DISABLED};
    border: 1px solid {LINE_PANEL};
}}
QPushButton[primary="true"] {{
    background: {ACCENT};
    border: 1px solid {ACCENT_PRESSED};
    color: {ACCENT_ON};
    font-weight: {FONT_WEIGHT_SEMIBOLD};
    padding: {PRIMARY_BUTTON_PADDING_V}px {PRIMARY_BUTTON_PADDING_H}px;
}}
QPushButton[primary="true"]:pressed {{
    background: {ACCENT_PRESSED};
}}
QPushButton[primary="true"]:disabled {{
    background: {LINE_PANEL};
    border: 1px solid {LINE_STRUCTURAL};
    color: {TEXT_DISABLED};
}}

QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox {{
    background: {SURFACE_CARD};
    border: 1px solid {LINE_STRUCTURAL};
    border-radius: {RADIUS}px;
    padding: {SPACE_XXS}px {SPACE_XS}px;
    selection-background-color: {ACCENT_SELECTION};
    selection-color: {TEXT_PRIMARY};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QAbstractSpinBox:focus, QComboBox:focus {{
    border: {FOCUS_BORDER_WIDTH}px solid {FOCUS_BORDER_COLOR};
}}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{
    background: {SURFACE_PAGE};
    color: {TEXT_DISABLED};
}}

QCheckBox, QRadioButton {{
    spacing: {SPACE_SM}px;
}}

/* ---- tables and trees ------------------------------------------------- */
QHeaderView::section {{
    background: {SURFACE_TABLE_HEADER};
    border: none;
    border-right: 1px solid {LINE_PANEL};
    border-bottom: 1px solid {LINE_STRUCTURAL};
    padding: 0px {CELL_PADDING_H}px;
    font-size: {FONT_SIZE_META}px;
    color: {TEXT_SECONDARY};
}}
QTableView, QTreeView, QListView {{
    background: {SURFACE_CARD};
    border: 1px solid {LINE_STRUCTURAL};
    alternate-background-color: {SURFACE_CARD};
    selection-background-color: {ACCENT_SELECTION};
    selection-color: {TEXT_PRIMARY};
    outline: 0;
}}
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{
    background: {ACCENT_SELECTION};
    color: {TEXT_PRIMARY};
}}

/* ---- containers ------------------------------------------------------- */
QGroupBox {{
    background: {SURFACE_TOOLBAR};
    border: 1px solid {LINE_STRUCTURAL};
    border-radius: {RADIUS}px;
    margin-top: {SPACE_XL}px;
    padding-top: {SPACE_MD}px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    background: {SURFACE_TABLE_HEADER};
    padding: {SPACE_XXS}px {SPACE_MD}px;
    font-size: {FONT_SIZE_SECTION}px;
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}
QSplitter::handle {{
    background: {LINE_STRUCTURAL};
}}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QTabWidget::pane {{
    background: {SURFACE_PAGE};
    border: 1px solid {LINE_STRUCTURAL};
}}
QTabBar::tab {{
    background: {SURFACE_NAV_RAIL};
    border: 1px solid {LINE_STRUCTURAL};
    border-bottom: none;
    padding: {BUTTON_PADDING_V}px {BUTTON_PADDING_H}px;
}}
QTabBar::tab:selected {{
    background: {SURFACE_PAGE};
    font-weight: {FONT_WEIGHT_SEMIBOLD};
}}

QMenuBar {{
    background: {SURFACE_TITLEBAR};
    border-bottom: 1px solid {LINE_STRUCTURAL};
}}
QMenuBar::item:selected, QMenu::item:selected {{
    background: {ACCENT_SELECTION};
    color: {TEXT_PRIMARY};
}}
QMenu {{
    background: {SURFACE_CARD};
    border: 1px solid {LINE_STRUCTURAL};
}}
QMenu::item {{
    padding: {SPACE_XS}px {SPACE_XL}px;
}}
"""


__all__ = [
    "ACCENT",
    "ACCENT_ON",
    "ACCENT_PRESSED",
    "ACCENT_SELECTION",
    "ACCENT_TINT",
    "BUTTON_PADDING_H",
    "BUTTON_PADDING_V",
    "CELL_PADDING_H",
    "CELL_PADDING_V",
    "FAILURE_CODE_COLOR",
    "FOCUS_BORDER_COLOR",
    "FOCUS_BORDER_WIDTH",
    "FONT_MONO",
    "FONT_MONO_FAMILIES",
    "FONT_SANS",
    "FONT_SANS_FAMILIES",
    "FONT_SIZE_BODY",
    "FONT_SIZE_META",
    "FONT_SIZE_MIN",
    "FONT_SIZE_MONO",
    "FONT_SIZE_MONO_HERO",
    "FONT_SIZE_SECTION",
    "FONT_SIZE_TITLE",
    "FONT_WEIGHT_BOLD",
    "FONT_WEIGHT_NORMAL",
    "FONT_WEIGHT_SEMIBOLD",
    "LINE_PANEL",
    "LINE_ROW",
    "LINE_SEPARATOR",
    "LINE_STRUCTURAL",
    "LINE_WINDOW",
    "NAV_ITEM_HEIGHT",
    "NAV_RAIL_COLLAPSED_WIDTH",
    "NAV_RAIL_COLLAPSE_BELOW",
    "NAV_RAIL_WIDTH",
    "OBJ_APP_NAME",
    "OBJ_CONFIG_PATH",
    "OBJ_HEALTH_BADGE",
    "OBJ_HEALTH_COUNT",
    "OBJ_HEALTH_GLYPH",
    "OBJ_HEALTH_TEXT",
    "OBJ_NAV_COUNT",
    "OBJ_NAV_ITEM",
    "OBJ_NAV_LABEL",
    "OBJ_NAV_RAIL",
    "OBJ_SETUP_DRAWER",
    "OBJ_STACK",
    "OBJ_STATUSBAR",
    "OBJ_STATUS_LEFT",
    "OBJ_STATUS_RIGHT",
    "OBJ_TITLEBAR",
    "PRIMARY_BUTTON_PADDING_H",
    "PRIMARY_BUTTON_PADDING_V",
    "RADIUS",
    "RADIUS_BUTTON",
    "ROW_HEIGHT",
    "RUNNING_TEXT",
    "SELECTED_BAR_WIDTH",
    "SETUP_DRAWER_WIDTH",
    "SPACE_LG",
    "SPACE_MD",
    "SPACE_SM",
    "SPACE_XL",
    "SPACE_XS",
    "SPACE_XXL",
    "SPACE_XXS",
    "SPACING_RAMP",
    "STAGE_CHIP_ROW_HEIGHT",
    "STATUSBAR_HEIGHT",
    "STATUS_DRY_RUN",
    "STATUS_FAILED",
    "STATUS_FILL",
    "STATUS_GLYPH",
    "STATUS_LINE",
    "STATUS_PASSED",
    "STATUS_RUNNING",
    "STATUS_SKIPPED",
    "OBJ_OPTION_ROW",
    "OBJ_STATE_TAG",
    "OBJ_WAS_VALUE",
    "OBJ_WHY_DISABLED",
    "OBJ_DETAIL_BAR",
    "OBJ_DETAIL_PATH",
    "OBJ_DETAIL_PROSE",
    "OBJ_SEARCH_BAND",
    "OBJ_ELSEWHERE_BAND",
    "STATUS_TEXT",
    "STATUS_WARNING",
    "SURFACE_BUTTON",
    "SURFACE_CARD",
    "SURFACE_LOG",
    "SURFACE_NAV_RAIL",
    "SURFACE_PAGE",
    "SURFACE_STATUSBAR",
    "SURFACE_TABLE_HEADER",
    "SURFACE_TITLEBAR",
    "SURFACE_TOOLBAR",
    "TABLE_HEADER_HEIGHT",
    "TEXT_DISABLED",
    "TEXT_LOG",
    "TEXT_PRIMARY",
    "TEXT_SECONDARY",
    "TITLEBAR_HEIGHT",
    "TOOLBAR_HEIGHT",
    "WARNING_TEXT_ON_WHITE",
    "WINDOW_MIN_HEIGHT",
    "WINDOW_MIN_WIDTH",
    "accent_colors",
    "build_qss",
    "status_color",
]
