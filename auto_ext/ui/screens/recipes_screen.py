"""The Recipes screen: pick a recipe on the left, edit it on the right.

Artboards ``1f`` (form, manual-edit strip collapsed) and ``1g`` (strip
expanded). Two things about it are load-bearing.

**The form is generated, not written.** Every row comes from
``auto_ext/catalog/options.yaml`` via
:meth:`~auto_ext.catalog.spec.Catalog.by_owner`; there is no field list in
this module, and there must never be one. Adding a row to the catalog adds a
row here, with the right control (:func:`~auto_ext.ui.widgets.option_editor.
editor_kind`), the right unit, the right default and the right tooltip, and
binds it to the Recipe field its ``context_path`` names. That is the whole
difference from the system this replaces, where the GUI showed seven values
because seven had been typed into a manifest by hand and the remaining
hundred-odd were reachable only by editing a ``.j2``.

**The manual-edit strip is the only place templates are visible.** Everything
the catalog cannot express is a hunk in
:class:`~auto_ext.ui.widgets.patch_strip.PatchStrip`, and the strip's job is
to keep that list small: a hunk the catalog has since absorbed says so and
offers to delete itself.

Ownership
---------
The screen owns a *working copy* (``recipe.model_copy(deep=True)``) and never
the caller's object. It reads and writes no files: ``save_requested`` hands
the working copy out and the host persists it. Reverting a hunk is applied to
the working copy *and* announced, so the widget is usable on its own while
the host stays the only thing that touches disk.
:class:`~auto_ext.ui.widgets.recipe_import_dialog.RecipeImportDialog` follows
the same rule -- it reads the user's files and produces a
:class:`~auto_ext.core.recipe_import.RecipeImportResult`, and
``recipe_imported`` hands that out for the host to write.

**The form has two densities.** ``Common`` draws the nineteen rows a person
changes from job to job; ``All`` draws every row the tools accept. Hiding is
allowable only because nothing becomes unreachable, and three rules enforce
that: the toggle is always on screen and never disabled, :meth:`search
<RecipesScreen.search_matches>` always covers the whole catalog (including
the six settings another screen owns), and a row whose value differs from its
default is *promoted* into Common whatever its tier says. A Common view that
omits a non-default value is a bug, not a preference. The full rule is
artboard ``M`` section 3.

Assumptions
-----------
* Grouping is two levels and both come from the catalog: level 1 is the tool
  the row's landing site belongs to, in pipeline order
  (:data:`TOOL_ORDER`), and level 2 is
  :class:`~auto_ext.catalog.spec.SectionDisplay` -- the generated file's own
  section names, renamed and merged by a twenty-three row table rather than
  by anything in this module. Rows with no landing site collect under
  :data:`FLOW_TOOL`, last. See :func:`form_layout`.

  The grouping this replaced was the first component of ``recipe_field_path``
  (``extraction`` / ``output`` / ``netlist``), which is the shape of our data
  model and of nothing the user has ever seen. They think in tools, and the
  manual in their hand when a run fails is that tool's.
* A row is drawn **once** and never changes parent between the two densities.
  Twenty-three Quantus rows write both command files; drawing them twice
  would ask the user which copy is the real one, and moving a row between
  modes would break the toggle's promise to keep the focused row.
* ``extraction.corner`` has no landing site, so it lives under ``Flow`` with
  the other four decisions that are about the run rather than about a line in
  a file. The catalog owns the *literal* as ``technology_corner`` with
  ``owner: profile`` -- a process fact -- and the seam between "the recipe
  names a semantic corner" and "the profile maps it to ``TYPICAL``" is what
  makes a recipe portable.
* The six recipe-owned rows whose ``currently`` is ``absent`` have no
  ``context_path`` and therefore no field to bind to. They are proposals, not
  settings, and are skipped.
* A row whose ``currently`` is ``hardcoded_literal`` *is* shown, disabled and
  marked -- see :func:`~auto_ext.ui.widgets.option_editor.template_freezes`.
  Hiding it would say "this tool has no such setting", which is false and is
  the misunderstanding the catalog exists to end; leaving it editable would
  let the user fill in a value that ``check_representable`` refuses only once
  a run has started. There are none in the shipped catalog and
  :func:`frozen_option_keys` is how a build that grows one says so.
* The list toolbar is two rows. Artboard ``1f`` draws one row of three
  buttons inside a 214px panel, and at the design's own metrics those three
  already fill it (New 43px, Duplicate 78px, Delete 56px, plus gaps and
  padding: 197 of 214). ``Import...`` does not fit beside them at any label
  short enough to still say what it does, and a clipped button is worse than
  a second row. The artboard's row is kept exactly as drawn and the new
  action sits under it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.catalog import (
    Catalog,
    OptionSpec,
    Owner,
    Screen,
    Tier,
    builtin_catalog,
    choices_for,
)
from auto_ext.model.recipe import Recipe
from auto_ext.ui import theme
from auto_ext.ui.widgets.option_editor import (
    ElidedLabel,
    OptionEditor,
    OptionGroup,
    option_label,
    option_tooltip,
    template_freezes,
)
from auto_ext.ui.widgets.patch_strip import PatchStrip
from auto_ext.ui.widgets.recipe_import_dialog import RecipeImportDialog

__all__ = [
    "FLOW_TOOL",
    "FormSection",
    "FormTool",
    "TOOL_LABELS",
    "TOOL_ORDER",
    "OBJ_FORM_HEADER",
    "OBJ_LIST_HEADER",
    "OBJ_LIST_TOOLBAR",
    "OBJ_OPTIONS_SUMMARY",
    "OBJ_TOOL_HEADER",
    "OBJ_DENSITY_BAR",
    "DENSITY_ALL",
    "DENSITY_COMMON",
    "OBJ_RECIPE_LIST",
    "RECIPE_LIST_WIDTH",
    "RECIPE_LIST_MIN_WIDTH",
    "RecipesScreen",
    "frozen_specs",
    "form_layout",
    "import_status_text",
    "recipe_specs",
]

#: Level 1 of the form, in PIPELINE order -- the order a run executes them,
#: which is also the order the user narrates the flow in. ``strmout`` has no
#: recipe-owned options and so never appears. A tool the catalog grows later
#: sorts in after these rather than being dropped.
TOOL_ORDER: tuple[str, ...] = ("si", "calibre", "quantus", "jivaro")

#: Synthetic tool for the rows with no landing site at all: they are
#: decisions about the run rather than lines in a file. Always last.
FLOW_TOOL = "flow"

#: Level-1 headings. Only the ones that differ from the raw tool name are
#: here; the table is four entries, not a field list.
TOOL_LABELS: dict[str, str] = {
    "si": "si",
    "calibre": "Calibre LVS",
    "quantus": "Quantus",
    "jivaro": "Jivaro",
    FLOW_TOOL: "Flow",
}

#: Sub-heading for the rows of a split section that apply to every format.
_EVERY_FORMAT = "every format"

#: Default width of the recipe list, and its floor. Artboard ``G``: 252px
#: default, 180px floor, resizable. The old 214 was a fixed width copied from
#: an artboard drawn with one recipe in the library; it is now the splitter's
#: starting position, and the user moves it.
RECIPE_LIST_WIDTH = 252
RECIPE_LIST_MIN_WIDTH = 180

#: Height of the recipe rows, and of the list header strip. Artboard ``1f``.
RECIPE_ROW_HEIGHT = theme.STAGE_CHIP_ROW_HEIGHT
LIST_HEADER_HEIGHT = theme.ROW_HEIGHT
LIST_TOOLBAR_HEIGHT = theme.NAV_ITEM_HEIGHT
#: The toolbar is two of those rows. See the module Assumptions.
LIST_TOOLBAR_ROWS = 2

#: Minimum width of the editable title field. A frameless QLineEdit sizes to
#: its content, so without a floor a recipe called "rc" would be a two-
#: character click target.
_NAME_FIELD_MIN_WIDTH = 220

OBJ_RECIPE_LIST = "recipeList"
OBJ_LIST_HEADER = "recipeListHeader"
OBJ_LIST_TOOLBAR = "recipeListToolbar"
OBJ_FORM_HEADER = "recipeFormHeader"
OBJ_OPTIONS_SUMMARY = "recipeOptionsSummary"
OBJ_TOOL_HEADER = "recipeToolHeader"
OBJ_DENSITY_BAR = "recipeDensityBar"

#: Width of the search field. Wide enough for a model path fragment
#: (``output.dspf.busbit``), narrow enough to leave the counts on screen.
_SEARCH_WIDTH = 190

#: The two densities. Artboard ``M`` section 3.
DENSITY_COMMON = "common"
DENSITY_ALL = "all"

_DOT = " · "
_EM_DASH = "—"
_GLYPH_EXPANDED = "▾"

# ---- catalog -> form -------------------------------------------------------
# Pure functions: importable and testable without a QApplication.


def recipe_specs(catalog: Catalog | None = None) -> list[OptionSpec]:
    """Every catalog row that binds to a Recipe field, in field-path order.

    Rows without a ``recipe_field_path`` are dropped: they are the ``absent``
    proposals, which name no field to edit. The order is the field path rather
    than the catalog's own order, because the catalog is sorted by emission
    line -- correct for the renderer, arbitrary for a form.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    rows = [opt for opt in cat.by_owner(Owner.RECIPE) if opt.recipe_field_path]
    return sorted(rows, key=lambda opt: opt.recipe_field_path or "")


def frozen_specs(catalog: Catalog | None = None) -> list[OptionSpec]:
    """Form rows the shipped templates still write as a literal.

    Empty in the shipped catalog, and keeping it empty is the point: a row
    here is a field the user can see, cannot set, and would otherwise only
    discover was frozen when a run refused the stage.
    """

    return [spec for spec in recipe_specs(catalog) if template_freezes(spec)]


@dataclass(frozen=True)
class FormSection:
    """Level 2 of the form: one heading and the rows under it."""

    #: Stable id, unique across the form. ``quantus/capacitance``, or
    #: ``quantus/output_db#dspf`` for a split section.
    key: str
    label: str
    order: int
    specs: tuple[OptionSpec, ...]


@dataclass(frozen=True)
class FormTool:
    """Level 1 of the form: one tool, in pipeline order."""

    #: ``si`` / ``calibre`` / ``quantus`` / ``jivaro``, or :data:`FLOW_TOOL`.
    tool: str
    label: str
    #: Template ids this tool writes, for the group's subtitle.
    templates: tuple[str, ...]
    sections: tuple[FormSection, ...]

    @property
    def specs(self) -> list[OptionSpec]:
        return [spec for section in self.sections for spec in section.specs]


def _tool_of(catalog: Catalog, spec: OptionSpec) -> tuple[str, str | None]:
    """``(tool, section)`` for one row, or ``(FLOW_TOOL, None)``.

    A row landing in two files is drawn ONCE, under its tool -- twenty-three
    Quantus rows write both ``ext.cmd`` and ``dspf.cmd``, and drawing them
    twice would ask the user which copy is the real one. The section is taken
    from the first landing site because no row in the catalog carries a
    different section in different targets, and ``test_catalog`` holds that.
    """

    for site in spec.lands_in:
        if site.target is not None:
            return catalog.tool_of(site.target), site.section
    return FLOW_TOOL, None


def _split_key(spec: OptionSpec) -> str | None:
    """Which ``requires_emit`` bucket a row falls in, or ``None`` for all."""

    return spec.requires_emit[0] if spec.requires_emit else None


def form_layout(catalog: Catalog | None = None) -> list[FormTool]:
    """The form's whole shape: tool, then section, then rows. Artboard ``M`` §2.

    Level 1 is the tool the row's landing site belongs to, in pipeline order,
    because that is the vocabulary the user already holds -- they think *si*,
    *Calibre LVS*, *Quantus*, *Jivaro*, and when something goes wrong the
    thing in their hand is that tool's manual. The old grouping was by the
    first component of the Recipe field path, which is the shape of our data
    model and of nothing the user has ever seen.

    Level 2 is :class:`~auto_ext.catalog.spec.SectionDisplay`, so the headings
    are the generated file's own section names and cannot drift from the
    catalog. Rows with no landing site collect under :data:`FLOW_TOOL`, last:
    they are decisions *about* the run rather than lines in a file.

    A row is never in two places, and never changes parent between the two
    density modes. That last property is what the mode toggle's
    "keep the focused row" behaviour depends on.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    #: ``tool -> section group key -> (display, specs)``
    tools: dict[str, dict[str, tuple[Any, list[OptionSpec]]]] = {}
    templates: dict[str, list[str]] = {}

    for spec in recipe_specs(cat):
        tool, section = _tool_of(cat, spec)
        display = cat.section_display(None if tool == FLOW_TOOL else tool, section or FLOW_TOOL)
        bucket = display.group
        if display.split_by == "requires_emit":
            # One section becomes one heading per emitted format. The vendor
            # documents four DIFFERENT option sets under the one name
            # ``output_db``, so a single heading would promise that the rows
            # under it are interchangeable, and they are not.
            bucket = f"{bucket}#{_split_key(spec) or _EVERY_FORMAT}"
        sections = tools.setdefault(tool, {})
        if bucket not in sections:
            sections[bucket] = (display, [])
        sections[bucket][1].append(spec)
        seen = templates.setdefault(tool, [])
        for site in spec.lands_in:
            if site.target is None:
                continue
            try:
                template_id = cat.target(site.target).template_id
            except KeyError:  # pragma: no cover - the catalog validates this
                continue
            if template_id not in seen:
                seen.append(template_id)

    def tool_rank(name: str) -> tuple[int, str]:
        try:
            return (TOOL_ORDER.index(name), "")
        except ValueError:
            return (len(TOOL_ORDER), name)

    out: list[FormTool] = []
    for tool in sorted(tools, key=tool_rank):
        built: list[FormSection] = []
        for bucket, (display, specs) in tools[tool].items():
            label = display.label
            if display.split_by == "requires_emit":
                fmt = bucket.split("#", 1)[1]
                label = f"{label} {_EM_DASH} {fmt.replace('_', ' ')}"
            built.append(
                FormSection(
                    key=f"{tool}/{bucket}",
                    label=label,
                    order=display.order,
                    specs=tuple(specs),
                )
            )
        built.sort(key=lambda s: (s.order, s.label))
        out.append(
            FormTool(
                tool=tool,
                label=TOOL_LABELS.get(tool, tool),
                templates=tuple(templates.get(tool, ())),
                sections=tuple(built),
            )
        )
    return out



def _get_path(root: Any, path: str) -> Any:
    node = root
    for part in path.split("."):
        node = getattr(node, part)
    return node


def _set_path(root: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    node = root
    for part in parts[:-1]:
        node = getattr(node, part)
    setattr(node, parts[-1], value)


def _display_value(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, (list, tuple)):
        return ",".join(_display_value(item) for item in value)
    return "" if value is None else str(value)


# ---- list decoration -------------------------------------------------------


def _let_shrink(widget: QWidget) -> None:
    """Let a layout squeeze this widget past its own text width.

    ``qSmartMinSize`` honours an explicitly set ``minimumWidth`` over the
    widget's ``minimumSizeHint``, so one pixel here is the difference between
    a screen that folds into the 940px window floor and one whose five
    buttons quietly set the floor for the whole application. The preferred
    size is untouched: at any usable width the widget still asks for, and
    gets, its full text.
    """

    widget.setMinimumWidth(1)


class _ClickableFrame(QFrame):
    """A frame that reports a plain left click. Used for the options summary bar.

    A ``QPushButton`` styled flat would work too, but the bar is a strip of
    labels the full width of the form, and a button's focus and hover
    repainting over that area is exactly the kind of full-width redraw the
    X11 link cannot afford.
    """

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _SelectedBarDelegate(QStyledItemDelegate):
    """Paints the 3px accent bar the design uses for "this row is selected".

    A stylesheet cannot draw it: ``QTreeView::item`` has no border-left that
    survives the selection background, and the bar has to sit inside the row
    rectangle rather than beside it.
    """

    def paint(self, painter, option, index) -> None:
        if index.column() == 0:
            self._paint_two_lines(painter, option, index)
        else:
            super().paint(painter, option, index)
        if index.column() != 0 or not (option.state & QStyle.State_Selected):
            return
        painter.save()
        painter.fillRect(
            option.rect.x(),
            option.rect.y(),
            theme.SELECTED_BAR_WIDTH,
            option.rect.height(),
            QColor(theme.ACCENT),
        )
        painter.restore()

    def _paint_two_lines(self, painter, option, index) -> None:
        """The recipe's ``name``, wrapped, then ``recipe_id`` in mono under it.

        The list originally drew ``name`` alone in a 214px column and the
        migrator writes names like "rc_coupled, corner typical, 55C,
        extracted_view, with reduction" -- sixty-three characters of which
        twelve fitted. Artboard ``G`` answered that by promoting ``recipe_id``
        to the first line and dropping ``name`` from the list entirely.

        The user overruled that on 2026-08-25: renaming a recipe and seeing
        the list not move reads as a rename that did not work, whatever the
        column is technically showing. So ``name`` is back on the first line
        -- but wrapped over two lines rather than truncated at twelve
        characters, which is what made the original arrangement untenable.
        ``recipe_id`` keeps a line of its own because it is the file name and
        the thing every error message quotes; ``description`` moves to the
        tooltip. See ``docs/refactor/RECIPES_FORM.md`` section 7.
        """

        painter.save()
        style = option.widget.style() if option.widget else None
        if style is not None:
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        rect = option.rect.adjusted(
            theme.SELECTED_BAR_WIDTH + theme.SPACE_XS, theme.SPACE_XXS, -theme.SPACE_XS, 0
        )
        selected = bool(option.state & QStyle.State_Selected)

        name = index.data(Qt.DisplayRole) or ""
        title = QFont(option.font)
        title.setPointSize(-1)
        title.setPixelSize(theme.FONT_SIZE_BODY)
        title.setWeight(QFont.DemiBold)
        painter.setFont(title)
        painter.setPen(QColor(theme.TEXT_PRIMARY))
        metrics = painter.fontMetrics()
        line = metrics.height()
        wanted = metrics.boundingRect(
            0, 0, rect.width(), 0, Qt.TextWordWrap, name
        ).height()
        used = max(min(wanted, 2 * line), line)
        if wanted > 2 * line:
            name = metrics.elidedText(name, Qt.ElideRight, 2 * rect.width())
        painter.drawText(
            rect.x(),
            rect.y(),
            rect.width(),
            used,
            Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
            name,
        )

        ident = index.data(Qt.UserRole + 1) or ""
        if ident:
            mono = QFont(option.font)
            mono.setFamily(theme.FONT_MONO_FAMILIES[0])
            mono.setPointSize(-1)
            mono.setPixelSize(theme.FONT_SIZE_META)
            painter.setFont(mono)
            painter.setPen(
                QColor(theme.TEXT_PRIMARY if selected else theme.TEXT_SECONDARY)
            )
            below = rect.adjusted(0, used, 0, 0)
            painter.drawText(
                below,
                Qt.AlignLeft | Qt.AlignTop,
                painter.fontMetrics().elidedText(ident, Qt.ElideMiddle, below.width()),
            )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt naming
        """Tall enough for the wrapped name plus the ``recipe_id`` under it.

        ``uniformItemSizes`` is off for exactly this: a short name takes one
        line and a migrator-written sentence takes two, and the row grows
        rather than eliding at twelve characters.
        """

        base = super().sizeHint(option, index)
        if index.column() != 0:
            return base
        name = index.data(Qt.DisplayRole) or ""
        ident = index.data(Qt.UserRole + 1) or ""
        metrics = option.fontMetrics
        line = metrics.height()
        wrapped = max(
            min(
                metrics.boundingRect(
                    0, 0, self._text_width(option), 0, Qt.TextWordWrap, name
                ).height(),
                # Two lines and no more. A name long enough to need a third is
                # a name that wanted to be the description, and a library
                # where one row is six lines tall stops being a list.
                2 * line,
            ),
            line,
        )
        below = line if ident else 0
        return QSize(base.width(), wrapped + below + 2 * theme.SPACE_XXS)

    @staticmethod
    def _text_width(option) -> int:
        """Usable text width, asked of the VIEW rather than of ``option``.

        ``option.rect`` is empty while Qt is collecting size hints, so
        measuring the wrap against it gives one word per line and a row six
        lines tall. The column knows its own width at that point; the item
        does not.
        """

        view = option.widget
        width = view.columnWidth(0) if view is not None else 0
        if width <= 0:
            width = RECIPE_LIST_WIDTH
        return max(1, width - theme.SELECTED_BAR_WIDTH - 2 * theme.SPACE_XS)


# ---- the screen ------------------------------------------------------------


class RecipesScreen(QWidget):
    """Recipe library on the left, catalog-generated form on the right."""

    #: A different recipe is now showing. Carries its ``recipe_id``.
    recipe_selected = pyqtSignal(str)
    #: The working copy started or stopped differing from the loaded one.
    dirty_changed = pyqtSignal(bool)
    #: One sentence for the shell's status bar.
    status_changed = pyqtSignal(str)
    #: Persist this :class:`~auto_ext.model.recipe.Recipe`. Carries the
    #: working copy, which the host is free to keep -- the screen reloads
    #: from whatever it is handed next.
    save_requested = pyqtSignal(object)
    #: Throw the working copy away and reload ``recipe_id``.
    revert_requested = pyqtSignal(str)
    new_requested = pyqtSignal()
    duplicate_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    #: The import dialog was confirmed. Carries the
    #: :class:`~auto_ext.core.recipe_import.RecipeImportResult`; the host
    #: persists it with ``recipe_import.write_imported_recipe`` and pushes the
    #: library back in, exactly as it does for ``save_requested``.
    recipe_imported = pyqtSignal(object)
    #: ``(stage, template_id, hunk_id)``, already applied to the working copy.
    patch_revert_requested = pyqtSignal(str, str, str)
    patch_delete_requested = pyqtSignal(str, str, str)
    #: ``recipe_id`` -- every hunk dropped from the working copy.
    patch_revert_all_requested = pyqtSignal(str)
    #: ``recipe_id`` -- render this recipe and open the result in an editor.
    edit_rendered_requested = pyqtSignal(str)

    def __init__(
        self, catalog: Catalog | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog if catalog is not None else builtin_catalog()
        self._specs = {spec.key: spec for spec in recipe_specs(self._catalog)}
        #: The loaded PdkProfile, or None. Only ``choices_from`` rows read it.
        self._profile: Any = None
        self._groups: dict[str, OptionGroup] = {}
        #: Which density is on screen. Per session, never stored on a recipe.
        self._density = DENSITY_COMMON
        #: ``tool -> FormTool`` and ``tool -> heading widget``, so a
        #: density change can restate a tool's count without a rebuild.
        self._tools: dict[str, FormTool] = {}
        self._tool_headers: dict[str, QLabel] = {}
        #: ``option key -> section key``, for showing and hiding by row.
        self._section_of: dict[str, str] = {}
        self._editors: dict[str, OptionEditor] = {}

        self._recipes: list[Recipe] = []
        self._original: Recipe | None = None
        self._working: Recipe | None = None
        self._usage: dict[str, int] = {}
        self._dirty = False
        self._loading = False
        self._import_dialog: RecipeImportDialog | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # A splitter, not a fixed 214px column. Artboard ``G``: the list holds
        # recipe ids and sentence-long descriptions, and how much room those
        # need is the user's judgement, not a number copied off an artboard
        # that was drawn before anybody had twelve recipes.
        self._splitter = QSplitter(Qt.Horizontal, self)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(1)
        self._splitter.addWidget(self._build_list_panel())
        self._splitter.addWidget(self._build_form_panel())
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([RECIPE_LIST_WIDTH, 900])
        root.addWidget(self._splitter)

        self._apply_density()
        self._refresh_header()
        self._refresh_status()

    # -- construction --------------------------------------------------

    def _build_list_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.NoFrame)
        panel.setMinimumWidth(RECIPE_LIST_MIN_WIDTH)
        panel.setStyleSheet(
            f"background: {theme.SURFACE_CARD};"
            f" border-right: 1px solid {theme.LINE_STRUCTURAL};"
        )
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        header = QLabel("recipe", panel)
        header.setObjectName(OBJ_LIST_HEADER)
        header.setFixedHeight(LIST_HEADER_HEIGHT)
        header.setStyleSheet(
            f"QLabel#{OBJ_LIST_HEADER} {{ background: {theme.SURFACE_TABLE_HEADER};"
            f" border-bottom: 1px solid {theme.LINE_STRUCTURAL};"
            f" color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
            f" padding: 0px {theme.SPACE_SM}px; }}"
        )
        column.addWidget(header)

        self._list = QTreeWidget(panel)
        self._list.setObjectName(OBJ_RECIPE_LIST)
        self._list.setColumnCount(2)
        self._list.setHeaderHidden(True)
        self._list.setRootIsDecorated(False)
        # Off on purpose: a row's height follows its wrapped description,
        # so a 65-character sentence takes two lines and grows the row
        # rather than being elided. Artboard G.
        self._list.setUniformRowHeights(False)
        self._list.setWordWrap(True)
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.setFrameShape(QFrame.NoFrame)
        self._list.setItemDelegate(_SelectedBarDelegate(self._list))
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_list_context_menu)
        self._list.currentItemChanged.connect(self._on_current_item_changed)
        self._list.header().setStretchLastSection(False)
        column.addWidget(self._list, 1)

        toolbar = QFrame(panel)
        toolbar.setObjectName(OBJ_LIST_TOOLBAR)
        toolbar.setFixedHeight(LIST_TOOLBAR_HEIGHT * LIST_TOOLBAR_ROWS)
        toolbar.setStyleSheet(
            f"QFrame#{OBJ_LIST_TOOLBAR} {{ background: {theme.SURFACE_TOOLBAR};"
            f" border-top: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        rows = QVBoxLayout(toolbar)
        rows.setContentsMargins(theme.SPACE_XS, 0, theme.SPACE_XS, 0)
        rows.setSpacing(theme.SPACE_XXS)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE_XXS)

        self._new_button = QPushButton("New", toolbar)
        self._new_button.clicked.connect(self.new_requested)
        _let_shrink(self._new_button)
        row.addWidget(self._new_button)

        self._duplicate_button = QPushButton("Duplicate", toolbar)
        self._duplicate_button.clicked.connect(self._emit_duplicate)
        _let_shrink(self._duplicate_button)
        row.addWidget(self._duplicate_button)

        self._delete_button = QPushButton("Delete", toolbar)
        self._delete_button.clicked.connect(self._emit_delete)
        _let_shrink(self._delete_button)
        row.addWidget(self._delete_button)
        row.addStretch(1)
        rows.addLayout(row)

        second = QHBoxLayout()
        second.setContentsMargins(0, 0, 0, 0)
        second.setSpacing(theme.SPACE_XXS)
        self._import_button = QPushButton("Import…", toolbar)
        self._import_button.setToolTip(
            "Turn EDA files you already have -- a Quantus command file, a "
            "Calibre deck setup, an si.env -- into a recipe"
        )
        self._import_button.clicked.connect(self._on_import_clicked)
        _let_shrink(self._import_button)
        second.addWidget(self._import_button)
        second.addStretch(1)
        rows.addLayout(second)

        column.addWidget(toolbar)
        return panel

    def _build_form_panel(self) -> QWidget:
        panel = QWidget(self)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        column.addWidget(self._build_form_header(panel))
        column.addWidget(self._build_density_bar(panel))
        column.addWidget(self._build_options_summary(panel))

        self._scroll = QScrollArea(panel)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._form_host = QWidget()
        self._form_layout = QVBoxLayout(self._form_host)
        self._form_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        self._form_layout.setSpacing(theme.SPACE_SM)
        self._build_groups()
        self._form_layout.addStretch(1)
        self._scroll.setWidget(self._form_host)
        column.addWidget(self._scroll, 1)

        self._patch_strip = PatchStrip(panel)
        self._patch_strip.toggled.connect(self._on_patch_strip_toggled)
        self._patch_strip.hunk_revert_requested.connect(self._on_hunk_revert)
        self._patch_strip.hunk_delete_requested.connect(self._on_hunk_delete)
        self._patch_strip.revert_all_requested.connect(self._on_revert_all_hunks)
        self._patch_strip.edit_rendered_requested.connect(self._on_edit_rendered)
        column.addWidget(self._patch_strip)
        return panel

    def _build_form_header(self, parent: QWidget) -> QWidget:
        header = QFrame(parent)
        header.setObjectName(OBJ_FORM_HEADER)
        header.setFrameShape(QFrame.NoFrame)
        header.setStyleSheet(
            f"QFrame#{OBJ_FORM_HEADER} {{ background: {theme.SURFACE_TOOLBAR};"
            f" border-bottom: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        row = QHBoxLayout(header)
        row.setContentsMargins(theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM)
        row.setSpacing(theme.SPACE_MD)

        # The title is an editable field, not a label. ``name`` is the only
        # thing about a recipe a user can rename -- ``recipe_id`` names the
        # file and the cell bindings, so it stays fixed and is shown in the
        # meta line beside this. Drawn frameless so it reads as the heading it
        # is until you click into it; the artboard's title block is unchanged.
        self._name_edit = QLineEdit("", header)
        self._name_edit.setFrame(False)
        self._name_edit.setPlaceholderText("Recipe name")
        self._name_edit.setToolTip(
            "Rename this recipe. The file name and the cell bindings follow "
            "recipe_id, which does not change."
        )
        self._name_edit.setStyleSheet(
            f"font-size: {theme.FONT_SIZE_TITLE}px;"
            f" font-weight: {theme.FONT_WEIGHT_BOLD};"
            f" background: transparent; padding: 0px;"
        )
        self._name_edit.setMinimumWidth(_NAME_FIELD_MIN_WIDTH)
        self._name_edit.textEdited.connect(self._on_name_edited)
        row.addWidget(self._name_edit, 0)

        self._meta_label = ElidedLabel("", parent=header)
        self._meta_label.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        row.addWidget(self._meta_label, 1)

        self._dirty_label = QLabel("", header)
        self._dirty_label.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.WARNING_TEXT_ON_WHITE};"
        )
        _let_shrink(self._dirty_label)
        row.addWidget(self._dirty_label, 0)

        self._revert_button = QPushButton("Revert", header)
        self._revert_button.clicked.connect(self._emit_revert)
        _let_shrink(self._revert_button)
        row.addWidget(self._revert_button, 0)

        self._save_button = QPushButton("Save", header)
        self._save_button.setProperty("primary", "true")
        self._save_button.clicked.connect(self._emit_save)
        _let_shrink(self._save_button)
        row.addWidget(self._save_button, 0)
        return header

    def _build_options_summary(self, parent: QWidget) -> QWidget:
        """The one-line stand-in for the form while the diff owns the height.

        Artboard ``1g``: when the escape hatch is open the form folds to a row
        of current values. Clicking it folds the hatch back and returns the
        form, so the two never fight for the same pixels.
        """

        bar = _ClickableFrame(parent)
        bar.setObjectName(OBJ_OPTIONS_SUMMARY)
        bar.setFrameShape(QFrame.NoFrame)
        bar.setToolTip("Show the options again and fold the manual edits away")
        bar.setStyleSheet(
            f"QFrame#{OBJ_OPTIONS_SUMMARY} {{ background: {theme.SURFACE_PAGE};"
            f" border-bottom: 1px solid {theme.LINE_PANEL}; }}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS)
        row.setSpacing(theme.SPACE_LG)

        caption = QLabel(f"{_GLYPH_EXPANDED} Options", bar)
        _let_shrink(caption)
        row.addWidget(caption, 0)

        self._summary_label = ElidedLabel("", parent=bar)
        self._summary_label.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        row.addWidget(self._summary_label, 1)

        bar.setVisible(False)
        bar.clicked.connect(lambda: self._patch_strip.set_expanded(False))
        self._summary_bar = bar
        return bar

    def _build_density_bar(self, parent: QWidget) -> QWidget:
        """``Common N | All M``, plus the counts that keep it honest.

        Artboard ``M`` section 3. The toggle is the whole reason hiding rows
        is allowable at all, so it is always on screen, never in a menu, and
        never disabled: a form that quietly holds back sixty-six settings and
        looks complete is worse than the crowded one it replaced. Beside it
        the bar states how many rows were promoted, how many carry an
        unanswered question and how many sit outside their advisory range --
        the three things a hidden row could otherwise hide.
        """

        bar = QFrame(parent)
        bar.setObjectName(OBJ_DENSITY_BAR)
        bar.setFrameShape(QFrame.NoFrame)
        bar.setFixedHeight(theme.TOOLBAR_HEIGHT)
        bar.setStyleSheet(
            f"QFrame#{OBJ_DENSITY_BAR} {{ background: {theme.SURFACE_TOOLBAR};"
            f" border-bottom: 1px solid {theme.LINE_PANEL}; }}"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_MD, 0)
        row.setSpacing(theme.SPACE_XS)

        self._density_buttons: dict[str, QPushButton] = {}
        for mode in (DENSITY_COMMON, DENSITY_ALL):
            button = QPushButton("", bar)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setChecked(mode == self._density)
            button.clicked.connect(lambda _c, m=mode: self.set_density(m))
            _let_shrink(button)
            row.addWidget(button, 0)
            self._density_buttons[mode] = button

        self._search = QLineEdit(bar)
        self._search.setPlaceholderText("Find  name, path or value")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(_SEARCH_WIDTH)
        self._search.textChanged.connect(self._on_search)
        row.addWidget(self._search, 0)

        self._density_note = ElidedLabel("", parent=bar)
        self._density_note.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        row.addWidget(self._density_note, 1)
        return bar

    # -- search --------------------------------------------------------

    def search_matches(self, needle: str) -> list[OptionSpec]:
        """Catalog rows matching ``needle``. Always the WHOLE catalog.

        Artboard ``M`` and rule 3 of the split: search is not a filter of the
        current density, it is a third view. Searching in Common view has to
        find the sixty-six rows Common is hiding, and it has to find the rows
        that are not on this screen at all -- the office report this answers
        was "I cannot find where to rename the Quantus output view", and the
        answer is a per-cell setting on another screen.

        Matched against everything a person might type: the label, the model
        path, the catalog key, the current value, the choice members, the
        generated option name and ``why``.
        """

        text = needle.strip().lower()
        if not text:
            return []
        out: list[OptionSpec] = []
        for spec in recipe_specs(self._catalog) + self._elsewhere_specs():
            haystack = [
                spec.key,
                spec.recipe_field_path or "",
                option_label(spec),
                spec.why,
                _display_value(self._value_of(spec)),
                " ".join(str(c) for c in (spec.choices or [])),
                " ".join(site.option for site in spec.lands_in),
            ]
            if any(text in part.lower() for part in haystack):
                out.append(spec)
        return out

    def _elsewhere_specs(self) -> list[OptionSpec]:
        """Rows another screen owns. Found by search, never editable here."""

        return [
            spec
            for spec in self._catalog.options
            if spec.screen is not Screen.RECIPES
        ]

    def _value_of(self, spec: OptionSpec) -> Any:
        if self._working is None or spec.recipe_field_path is None:
            return spec.default
        try:
            return _get_path(self._working, spec.recipe_field_path)
        except AttributeError:
            return spec.default

    def _on_search(self, text: str) -> None:
        matches = self.search_matches(text)
        if not text.strip():
            self._apply_density()
            self.status_changed.emit("")
            return
        keys = {spec.key for spec in matches}
        for tool in form_layout(self._catalog):
            live = 0
            for section in tool.sections:
                group = self._groups.get(section.key)
                if group is None:  # pragma: no cover - built together
                    continue
                here = 0
                for spec in section.specs:
                    on = spec.key in keys
                    group.grid.set_row_visible(spec.key, on)
                    here += int(on)
                live += here
                group.setVisible(here > 0)
            self._refresh_tool_header(tool, live)
        here_count = sum(1 for spec in matches if spec.screen is Screen.RECIPES)
        elsewhere = [spec for spec in matches if spec.screen is not Screen.RECIPES]
        parts = [f"{here_count} match{'' if here_count == 1 else 'es'}"]
        if elsewhere:
            # Named, not silently dropped. A search that returns nothing for
            # ``out_file`` teaches the user the setting does not exist.
            names = ", ".join(option_label(spec) for spec in elsewhere)
            parts.append(f"{len(elsewhere)} on the Cells screen: {names}")
        self._density_note.set_full_text(_DOT.join(parts))
        self.status_changed.emit(_DOT.join(parts))

    def search_field(self) -> QLineEdit:
        return self._search

    # -- density -------------------------------------------------------

    def density(self) -> str:
        return self._density

    def set_density(self, mode: str) -> None:
        """Switch between the two densities. A view change, never an edit."""

        if mode not in (DENSITY_COMMON, DENSITY_ALL):
            raise ValueError(f"unknown density {mode!r}")
        self._density = mode
        for name, button in self._density_buttons.items():
            button.setChecked(name == mode)
        self._apply_density()

    def _is_promoted(self, spec: OptionSpec) -> bool:
        """True when a row's value has left its catalog default.

        Rule 2 of the split, and the one that makes it safe: a Common view
        that omits a non-default value is a form lying about what the run
        will do. Tier does not get a vote here.
        """

        if self._working is None or spec.recipe_field_path is None:
            return False
        try:
            current = _get_path(self._working, spec.recipe_field_path)
        except AttributeError:  # pragma: no cover - the model validates this
            return False
        return _display_value(current) != _display_value(spec.default)

    def _shows_in_common(self, spec: OptionSpec) -> bool:
        return spec.tier is Tier.COMMON or self._is_promoted(spec)

    def visible_option_keys(self) -> list[str]:
        """Rows the current density draws, in form order."""

        return [
            key
            for tool in form_layout(self._catalog)
            for section in tool.sections
            for spec in section.specs
            if (key := spec.key) in self._editors
            and (self._density == DENSITY_ALL or self._shows_in_common(spec))
        ]

    def promoted_keys(self) -> list[str]:
        """All-tier rows Common shows anyway, because their value moved."""

        return [
            spec.key
            for spec in recipe_specs(self._catalog)
            if spec.tier is not Tier.COMMON and self._is_promoted(spec)
        ]

    def emitted_formats(self) -> set[str]:
        """Output formats the working copy actually asks for."""

        if self._working is None:
            return set()
        return {str(item) for item in getattr(self._working.output, "emit", []) or []}

    def _applies_to_this_recipe(self, spec: OptionSpec) -> bool:
        """False when the row belongs to an output format this recipe skips.

        Rendering such a row writes an option the tool does not accept under
        the chosen output type: the vendor documents four *different* option
        sets under ``output_db``, and mixing them produces an illegal command
        file that fails hours into a run.
        """

        if not spec.requires_emit:
            return True
        emitted = self.emitted_formats()
        return not emitted or bool(emitted & set(spec.requires_emit))

    def inapplicable_keys(self) -> list[str]:
        """Rows drawn disabled because this recipe does not emit their format."""

        return [
            spec.key
            for spec in recipe_specs(self._catalog)
            if spec.key in self._editors and not self._applies_to_this_recipe(spec)
        ]

    def _apply_emit_gating(self) -> None:
        """Grey the rows this recipe cannot reach. Disabled, never hidden.

        Hiding them would say "this tool has no such setting", which is false
        and is the exact misunderstanding the catalog exists to end. The row
        keeps its label, its real value and a reason, and search still finds
        it -- that is the point of drawing it at all.
        """

        for spec in recipe_specs(self._catalog):
            editor = self._editors.get(spec.key)
            if editor is None or not spec.requires_emit:
                continue
            ok = self._applies_to_this_recipe(spec)
            editor.setEnabled(ok)
            label = self._label_of(spec.key)
            if label is not None:
                label.setEnabled(ok)
            editor.setToolTip(
                option_tooltip(spec)
                if ok
                else f"{', '.join(spec.requires_emit)} only "
                f"{_EM_DASH} this recipe emits "
                f"{', '.join(sorted(self.emitted_formats())) or 'nothing'}"
            )

    def _label_of(self, key: str):
        section = self._section_of.get(key)
        group = self._groups.get(section) if section else None
        return group.grid.label(key) if group is not None else None

    def _apply_density(self) -> None:
        self._apply_emit_gating()
        shown = set(self.visible_option_keys())
        for tool in form_layout(self._catalog):
            live = 0
            for section in tool.sections:
                group = self._groups.get(section.key)
                if group is None:  # pragma: no cover - built from the same layout
                    continue
                for spec in section.specs:
                    group.grid.set_row_visible(spec.key, spec.key in shown)
                here = sum(1 for spec in section.specs if spec.key in shown)
                live += here
                group.setVisible(here > 0)
            self._refresh_tool_header(tool, live)
        self._refresh_density_bar(len(shown))

    def _refresh_tool_header(self, tool: FormTool, live: int) -> None:
        header = self._tool_headers.get(tool.tool)
        if header is None:  # pragma: no cover - built together
            return
        total = len(tool.specs)
        files = ", ".join(tool.templates)
        head = f"{tool.label}  {files}" if files else tool.label
        if self._density == DENSITY_ALL:
            header.setText(f"{head} {_EM_DASH} {total}")
        elif live:
            header.setText(f"{head} {_EM_DASH} {live} of {total} shown")
        else:
            # Artboard ``M`` section 5: a tool with nothing to say still says
            # it. One that vanished would read as a stage that is not being
            # run, which is a different and much more alarming claim.
            header.setText(
                f"{head} {_EM_DASH} {total} options, all at the catalog default"
            )

    def _refresh_density_bar(self, shown: int) -> None:
        total = len(self._editors)
        common = sum(
            1 for spec in recipe_specs(self._catalog) if spec.tier is Tier.COMMON
        )
        self._density_buttons[DENSITY_COMMON].setText(f"Common {common}")
        self._density_buttons[DENSITY_ALL].setText(f"All {total}")
        promoted = len(self.promoted_keys())
        unverified = sum(
            1
            for key in self.visible_option_keys()
            if (spec := self._specs.get(key)) is not None and spec.question
        )
        parts = [f"{shown} of {total} shown"]
        if promoted:
            parts.append(f"{promoted} promoted")
        if unverified:
            parts.append(f"{unverified} unverified")
        self._density_note.set_full_text(_DOT.join(parts))

    def _build_groups(self) -> None:
        """Tool heading, then one card per section. Artboard ``B``.

        Two levels, both from the catalog: the heading is the tool, the cards
        under it are the generated file's own sections. Nothing here decides
        what goes where -- see :func:`form_layout`.
        """

        for tool in form_layout(self._catalog):
            self._form_layout.addWidget(self._build_tool_header(tool))
            self._tools[tool.tool] = tool
            for section in tool.sections:
                group = OptionGroup(
                    section.label,
                    "",
                    parent=self._form_host,
                )
                group.add_options(section.specs)
                group.value_changed.connect(self._on_value_changed)
                self._form_layout.addWidget(group)
                self._groups[section.key] = group
                for spec in section.specs:
                    editor = group.grid.editor(spec.key)
                    if editor is not None:
                        self._editors[spec.key] = editor
                        self._section_of[spec.key] = section.key

    def _build_tool_header(self, tool: FormTool) -> QWidget:
        """Level-1 heading: the tool, the files it writes, and its row count.

        A tool with no rows in the current density still gets this line. One
        that disappeared entirely would read as a stage that is not being
        run, which is a different and much worse statement than "nothing here
        needs you".
        """

        header = QLabel(self._form_host)
        header.setObjectName(OBJ_TOOL_HEADER)
        files = ", ".join(tool.templates)
        count = len(tool.specs)
        caption = f"{tool.label}  {files} {_EM_DASH} {count}" if files else f"{tool.label} {_EM_DASH} {count}"
        header.setText(caption)
        header.setStyleSheet(
            f"QLabel#{OBJ_TOOL_HEADER} {{ color: {theme.TEXT_PRIMARY};"
            f" font-size: {theme.FONT_SIZE_SECTION}px;"
            f" font-weight: {theme.FONT_WEIGHT_SEMIBOLD};"
            f" padding: {theme.SPACE_XS}px 0px {theme.SPACE_XXS}px 0px; }}"
        )
        self._tool_headers[tool.tool] = header
        return header

    # -- data ----------------------------------------------------------

    def set_recipes(
        self, recipes: Sequence[Recipe], *, select: str | None = None
    ) -> None:
        """Replace the library. Selection follows ``select``, else the first row."""

        self._recipes = list(recipes)
        self._list.blockSignals(True)
        self._list.clear()
        for recipe in self._recipes:
            item = QTreeWidgetItem()
            item.setData(0, Qt.UserRole, recipe.recipe_id)
            self._fill_list_item(item, recipe)
            self._list.addTopLevelItem(item)
        self._list.blockSignals(False)
        self._list.resizeColumnToContents(1)

        wanted = select if select is not None else (
            self._recipes[0].recipe_id if self._recipes else None
        )
        if wanted is None:
            self._load(None)
        else:
            self.select_recipe(wanted)

    def _fill_list_item(self, item: QTreeWidgetItem, recipe: Recipe) -> None:
        """Write every visible field of one list row, from one Recipe.

        **The only writer.** Column 0 carries the ``name`` and the delegate
        draws it on the first line, wrapped; ``recipe_id`` rides in
        ``UserRole + 1`` and is drawn in mono under it; ``description``
        becomes the tooltip (``_SelectedBarDelegate._paint_two_lines``).

        This used to be open-coded in :meth:`set_recipes` while
        ``_on_name_edited`` wrote a *different* meaning into the same cells --
        the name into column 0, and nothing at all into the second line. So a
        row said one thing while the user typed and another after the next
        repaint, and every save repaints. The two agreeing is not a rule
        anyone can hold in their head across two call sites; it is a rule that
        survives by there being one.
        """

        item.setText(0, recipe.name)
        item.setText(1, self._edit_badge(recipe))
        item.setData(0, Qt.UserRole + 1, recipe.recipe_id)
        item.setToolTip(0, recipe.description or recipe.recipe_id)
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        if recipe.manual_edit_count:
            item.setForeground(1, QColor(theme.WARNING_TEXT_ON_WHITE))
            item.setToolTip(
                1, f"{recipe.manual_edit_count} manual edits to the generated files"
            )

    def set_profile(self, profile: Any | None) -> None:
        """Point every ``choices_from`` control at this PDK profile's tables.

        Which corners exist, and which LVS deck variants were released, are
        process facts. The catalog's static ``choices`` are one PDK's answer
        and would be quietly wrong on the next one, so the controls for those
        rows are refilled here from the profile that is actually loaded --
        :func:`auto_ext.catalog.spec.choices_for` decides what that means, and
        falls back to the catalog list when there is no profile.

        The working copy is re-pushed afterwards: rebuilding a combo's item
        list resets its index, and a recipe must not have its corner silently
        changed by a profile arriving.
        """

        self._profile = profile
        self._loading = True
        try:
            for key, editor in self._editors.items():
                spec = self._specs[key]
                if spec.choices_from is None:
                    continue
                setter = getattr(editor, "set_choices", None)
                if setter is None:  # pragma: no cover - enum rows are combos
                    continue
                setter(choices_for(spec, profile))
                path = spec.recipe_field_path
                if self._working is not None and path is not None:
                    editor.set_value(_get_path(self._working, path))
        finally:
            self._loading = False

    def recipes(self) -> list[Recipe]:
        return list(self._recipes)

    def select_recipe(self, recipe_id: str) -> bool:
        for index in range(self._list.topLevelItemCount()):
            item = self._list.topLevelItem(index)
            if item.data(0, Qt.UserRole) == recipe_id:
                self._list.setCurrentItem(item)
                return True
        return False

    def current_recipe_id(self) -> str | None:
        return self._working.recipe_id if self._working is not None else None

    def current_recipe(self) -> Recipe | None:
        """The working copy, with every edit made so far already applied."""

        return self._working

    def set_usage_counts(self, counts: Mapping[str, int]) -> None:
        """``recipe_id -> number of cells pointing at it``, for the header line."""

        self._usage = dict(counts)
        self._refresh_header()

    def set_patch_reports(self, reports: Sequence[Any] | None) -> None:
        """Hand the strip what a planned run learned about this recipe's hunks."""

        self._patch_strip.set_patches(
            self._working.patches if self._working is not None else [], reports=reports
        )

    def is_dirty(self) -> bool:
        return self._dirty

    def changed_field_paths(self) -> list[str]:
        """Recipe field paths where the working copy differs from the loaded one."""

        if self._working is None or self._original is None:
            return []
        changed: list[str] = []
        for spec in self._specs.values():
            path = spec.recipe_field_path
            if path is None:
                continue
            if _get_path(self._working, path) != _get_path(self._original, path):
                changed.append(path)
        return sorted(changed)

    # -- accessors -----------------------------------------------------

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def patch_strip(self) -> PatchStrip:
        return self._patch_strip

    @property
    def recipe_list(self) -> QTreeWidget:
        return self._list

    def list_row_lines(self, recipe_id: str) -> tuple[str, str] | None:
        """The two lines the list actually paints for ``recipe_id``.

        ``_SelectedBarDelegate._paint_two_lines`` draws ``DisplayRole`` on the
        first line and ``UserRole + 1`` on the second. This reads back exactly
        those, so a test can assert on **what is on screen** without knowing
        the role numbers or re-deriving the text from the Recipe -- which is
        the assertion that was missing when a refactor changed what column 0
        means and left the rename handler writing the old meaning into it.

        ``None`` when no row carries that id.
        """

        item = self._item_for(recipe_id)
        if item is None:
            return None
        return item.text(0), str(item.data(0, Qt.UserRole + 1) or "")

    def groups(self) -> dict[str, OptionGroup]:
        return dict(self._groups)

    def group(self, name: str) -> OptionGroup | None:
        return self._groups.get(name)

    def editor(self, key: str) -> OptionEditor | None:
        return self._editors.get(key)

    def option_keys(self) -> list[str]:
        return list(self._editors)

    def needs_confirmation_keys(self) -> list[str]:
        """Every displayed option whose catalog row still carries a question."""

        keys: list[str] = []
        for group in self._groups.values():
            keys.extend(group.grid.needs_confirmation_keys())
        return keys

    def save_button(self) -> QPushButton:
        return self._save_button

    def name_edit(self) -> QLineEdit:
        """The editable recipe title in the form header."""

        return self._name_edit

    def revert_button(self) -> QPushButton:
        return self._revert_button

    def new_button(self) -> QPushButton:
        return self._new_button

    def duplicate_button(self) -> QPushButton:
        return self._duplicate_button

    def delete_button(self) -> QPushButton:
        return self._delete_button

    def import_button(self) -> QPushButton:
        return self._import_button

    def import_dialog(self) -> RecipeImportDialog | None:
        """The dialog the Import button opened, or ``None`` before it has been.

        Kept as an attribute rather than a local so the screen stays drivable
        from outside -- the host may want to preload a path, and a test needs
        to reach the dialog the click created.
        """

        return self._import_dialog

    def frozen_option_keys(self) -> list[str]:
        """Catalog keys shown on this form that the templates freeze.

        Empty against the shipped catalog. Non-empty means somebody added a
        row before its template hole, and every one of these fields is
        disabled on the page.
        """

        return [key for key, editor in self._editors.items() if editor.is_frozen]

    def frozen_overrides(self) -> dict[str, Any]:
        """``{key: stored value}`` for frozen rows this recipe disagrees with.

        Exactly what ``check_representable`` will refuse. Surfaced here so the
        page can say it before a run does.
        """

        return {
            key: editor.frozen_override()
            for key, editor in self._editors.items()
            if editor.frozen_override() is not None
        }

    def options_summary_bar(self) -> QFrame:
        return self._summary_bar

    def options_summary_text(self) -> str:
        """What the form folds down to while the escape hatch is open.

        Values that differ from the catalog default, because those are the
        ones worth carrying in a single line; an all-defaults recipe says so
        instead of listing ninety unchanged rows.
        """

        if self._working is None:
            return ""
        parts: list[str] = []
        for spec in self._specs.values():
            path = spec.recipe_field_path
            if path is None:
                continue
            value = _get_path(self._working, path)
            if value == spec.default:
                continue
            parts.append(f"{path.split('.')[-1]} {_display_value(value)}")
        if not parts:
            return "all catalog defaults"
        return _DOT.join(parts)

    def header_meta_text(self) -> str:
        if self._working is None:
            return ""
        used = self._usage.get(self._working.recipe_id)
        parts = []
        if used is not None:
            parts.append(f"used by {used} cell" + ("" if used == 1 else "s"))
        parts.append(f"last edited {self._working.updated_at.strftime('%m-%d')}")
        return _DOT.join(parts)

    def status_text(self) -> str:
        if self._working is None:
            return "no recipe selected"
        blocked = self.frozen_overrides()
        if blocked:
            # Ahead of the dirty/saved line on purpose: a recipe that cannot
            # render is a bigger fact about it than whether it is saved.
            names = ", ".join(sorted(blocked))
            return (
                f"recipe {self._working.name} {_EM_DASH} "
                f"{len(blocked)} value" + ("" if len(blocked) == 1 else "s")
                + f" the templates still hardcode ({names}); the run will refuse "
                "these stages"
            )
        changed = len(self.changed_field_paths())
        if not self._dirty:
            return f"recipe {self._working.name} {_EM_DASH} saved"
        if changed:
            return (
                f"recipe {self._working.name} {_EM_DASH} "
                f"{changed} field" + ("" if changed == 1 else "s") + " changed, not saved"
            )
        return f"recipe {self._working.name} {_EM_DASH} manual edits changed, not saved"

    # -- loading -------------------------------------------------------

    def _load(self, recipe: Recipe | None) -> None:
        self._loading = True
        try:
            self._original = recipe
            self._working = recipe.model_copy(deep=True) if recipe is not None else None
            for key, editor in self._editors.items():
                spec = self._specs[key]
                path = spec.recipe_field_path
                editor.set_invalid(False)
                if self._working is None or path is None:
                    editor.setEnabled(self._working is not None)
                    continue
                editor.setEnabled(True)
                editor.set_value(_get_path(self._working, path))
            self._name_edit.setText(
                self._working.name if self._working is not None else ""
            )
            self._patch_strip.set_patches(
                self._working.patches if self._working is not None else []
            )
        finally:
            self._loading = False
        self._set_dirty(False)
        self._apply_density()
        self._refresh_header()
        if self._working is not None:
            self.recipe_selected.emit(self._working.recipe_id)

    def _recipe_by_id(self, recipe_id: str) -> Recipe | None:
        for recipe in self._recipes:
            if recipe.recipe_id == recipe_id:
                return recipe
        return None

    @staticmethod
    def _edit_badge(recipe: Recipe) -> str:
        count = recipe.manual_edit_count
        return str(count) if count else ""

    # -- slots ---------------------------------------------------------

    def _on_current_item_changed(self, current, _previous) -> None:
        if current is None:
            self._load(None)
            return
        recipe_id = current.data(0, Qt.UserRole)
        self._load(self._recipe_by_id(str(recipe_id)))

    def _on_value_changed(self, key: str, value: Any) -> None:
        if self._loading or self._working is None:
            return
        spec = self._specs.get(key)
        editor = self._editors.get(key)
        if spec is None or spec.recipe_field_path is None:
            return
        try:
            _set_path(self._working, spec.recipe_field_path, value)
        except ValidationError as exc:
            if editor is not None:
                editor.set_invalid(True, _first_error(exc))
            self._refresh_status()
            return
        if editor is not None:
            editor.set_invalid(False)
        self._recompute_dirty()
        if self._density == DENSITY_COMMON:
            # A row whose value just left the default has to appear. The
            # reverse is deliberately NOT done here: artboard M section 3
            # keeps a row that returned to its default on screen until the
            # next mode switch, save or recipe change, so a control never
            # disappears under the cursor that just reset it.
            for key in self.promoted_keys():
                section = self._section_of.get(key)
                group = self._groups.get(section) if section else None
                if group is not None:
                    group.grid.set_row_visible(key, True)
                    group.setVisible(True)
            self._refresh_density_bar(len(self.visible_option_keys()))

    def _on_name_edited(self, text: str) -> None:
        """Rename the working copy as the user types, list row included.

        The row is repainted through :meth:`_fill_list_item`, the same call
        :meth:`set_recipes` makes, so what the list says while typing is what
        it will say after the next rebuild. Whether the *name* belongs on that
        row at all is a separate question and lives in ``RECIPES_FORM.md``;
        this only guarantees the two writers cannot disagree about it.

        An empty box is a half-typed name, not a rename: ``Recipe.name`` is
        ``min_length=1``, so the old name is held until there is a new one
        rather than raising on every backspace to empty.
        """

        if self._loading or self._working is None:
            return
        name = text.strip()
        if not name:
            return
        self._working.name = name
        item = self._item_for(self._working.recipe_id)
        if item is not None:
            self._fill_list_item(item, self._working)
        self._recompute_dirty()

    def _item_for(self, recipe_id: str) -> QTreeWidgetItem | None:
        for index in range(self._list.topLevelItemCount()):
            item = self._list.topLevelItem(index)
            if item.data(0, Qt.UserRole) == recipe_id:
                return item
        return None

    def _on_patch_strip_toggled(self, expanded: bool) -> None:
        self._summary_label.set_full_text(self.options_summary_text())
        self._summary_bar.setVisible(expanded)
        self._scroll.setVisible(not expanded)

    def _on_hunk_revert(self, stage: str, template_id: str, hunk_id: str) -> None:
        self._drop_hunk(stage, template_id, hunk_id)
        self.patch_revert_requested.emit(stage, template_id, hunk_id)

    def _on_hunk_delete(self, stage: str, template_id: str, hunk_id: str) -> None:
        self._drop_hunk(stage, template_id, hunk_id)
        self.patch_delete_requested.emit(stage, template_id, hunk_id)

    def _drop_hunk(self, stage: str, template_id: str, hunk_id: str) -> None:
        """Remove one hunk from the working copy, and the file if it empties."""

        if self._working is None:
            return
        kept = []
        for patch in self._working.patches:
            if patch.stage.value != stage or patch.template_id != template_id:
                kept.append(patch)
                continue
            hunks = [hunk for hunk in patch.hunks if hunk.id != hunk_id]
            if hunks:
                kept.append(patch.model_copy(update={"hunks": hunks}))
        self._working.patches = kept
        self._patch_strip.set_patches(kept)
        self._recompute_dirty()

    def _on_revert_all_hunks(self) -> None:
        if self._working is None:
            return
        self._working.patches = []
        self._patch_strip.set_patches([])
        self._recompute_dirty()
        self.patch_revert_all_requested.emit(self._working.recipe_id)

    def _on_edit_rendered(self) -> None:
        if self._working is not None:
            self.edit_rendered_requested.emit(self._working.recipe_id)

    def _emit_save(self) -> None:
        if self._working is not None:
            self.save_requested.emit(self._working)

    def _emit_revert(self) -> None:
        if self._original is not None:
            recipe_id = self._original.recipe_id
            self._load(self._original)
            self.revert_requested.emit(recipe_id)

    def _emit_duplicate(self) -> None:
        if self._working is not None:
            self.duplicate_requested.emit(self._working.recipe_id)

    def _emit_delete(self) -> None:
        if self._working is not None:
            self.delete_requested.emit(self._working.recipe_id)

    def _on_import_clicked(self) -> None:
        """Open the import dialog, modally, over this screen.

        A fresh dialog each time: the previous one carries the files and the
        report of the previous import, and a dialog that reopens showing the
        last user's ``.qci`` is how a wrong file gets imported twice. The old
        one is released here rather than on close, so the accessor stays valid
        for as long as anything could still ask about it.
        """

        if self._import_dialog is not None:
            self._import_dialog.deleteLater()
        dialog = RecipeImportDialog(
            catalog=self._catalog,
            existing_ids=[recipe.recipe_id for recipe in self._recipes],
            parent=self,
        )
        dialog.import_accepted.connect(self._on_import_accepted)
        self._import_dialog = dialog
        dialog.exec_()

    def _on_import_accepted(self, result: object) -> None:
        """Hand the imported recipe out. The host writes it, this screen does not."""

        self.status_changed.emit(import_status_text(result))
        self.recipe_imported.emit(result)

    def _on_list_context_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        recipe_id = str(item.data(0, Qt.UserRole))
        menu = QMenu(self._list)
        duplicate = menu.addAction("Duplicate")
        duplicate.triggered.connect(
            lambda _checked=False, rid=recipe_id: self.duplicate_requested.emit(rid)
        )
        delete = menu.addAction("Delete")
        delete.triggered.connect(
            lambda _checked=False, rid=recipe_id: self.delete_requested.emit(rid)
        )

        # X11 delivers the context-menu event on button *press*; a synchronous
        # exec_() is dismissed by the following release, forcing a second
        # right-click. Defer the popup one event-loop tick.
        global_pos = self._list.viewport().mapToGlobal(pos)
        QTimer.singleShot(0, lambda: menu.exec_(global_pos))

    # -- state ---------------------------------------------------------

    def _recompute_dirty(self) -> None:
        if self._working is None or self._original is None:
            self._set_dirty(False)
            return
        self._set_dirty(
            self._working.content_sha256() != self._original.content_sha256()
        )

    def _set_dirty(self, dirty: bool) -> None:
        changed = dirty != self._dirty
        self._dirty = dirty
        self._dirty_label.setText("unsaved" if dirty else "")
        self._save_button.setEnabled(dirty and self._working is not None)
        self._revert_button.setEnabled(dirty and self._working is not None)
        if self._summary_bar.isVisible():
            self._summary_label.set_full_text(self.options_summary_text())
        self._refresh_status()
        if changed:
            self.dirty_changed.emit(dirty)

    def _refresh_header(self) -> None:
        has = self._working is not None
        self._name_edit.setEnabled(has)
        self._name_edit.setPlaceholderText(
            "Recipe name" if has else "No recipe selected"
        )
        self._meta_label.set_full_text(self.header_meta_text())
        self._duplicate_button.setEnabled(has)
        self._delete_button.setEnabled(has)
        self._save_button.setEnabled(has and self._dirty)
        self._revert_button.setEnabled(has and self._dirty)

    def _refresh_status(self) -> None:
        self.status_changed.emit(self.status_text())


def import_status_text(result: Any) -> str:
    """The status-bar line for one finished import.

    The three numbers the report page shows, in the order it shows them, so
    the bar and the dialog cannot tell different stories. The count of options
    left at the default is the one worth carrying out of the dialog: it is the
    part the user will not remember having been told.
    """

    kept = sum(1 for value in result.mapped if not value.applied_to) + len(result.unread)
    return (
        f"imported {result.recipe.name} {_EM_DASH} "
        f"{result.applied_count} value" + ("" if result.applied_count == 1 else "s") + ", "
        f"{result.hunk_count} manual edit" + ("" if result.hunk_count == 1 else "s") + ", "
        f"{kept} option" + ("" if kept == 1 else "s") + " left at the catalog default"
    )


def _first_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:  # pragma: no cover - pydantic always reports at least one
        return str(exc)
    return str(errors[0].get("msg", "")) or str(exc)
