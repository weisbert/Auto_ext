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

Assumptions
-----------
* Groups are the first component of ``recipe_field_path``
  (``extraction`` / ``output`` / ``lvs`` / ``reduction`` / ``netlist`` /
  ``policy``), and a root-level field lands in ``General``.
  :data:`GROUP_ORDER` fixes the order of the ones that exist today; a group
  the catalog grows later renders at the end rather than not at all.
* ``extraction.corner`` has no row here. The catalog owns that literal as
  ``technology_corner`` with ``owner: profile`` -- it is a process fact, and
  the seam between "the recipe names a semantic corner" and "the profile maps
  it to ``TYPICAL``" is what makes a recipe portable. Artboard ``1f`` draws
  the corner in the extraction group; following ``owner`` rather than the
  artboard is deliberate, and the field re-appears the day the catalog says
  the recipe owns it.
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

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError
from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
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
    QStyle,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.catalog import Catalog, OptionSpec, Owner, builtin_catalog, choices_for
from auto_ext.model.recipe import Recipe
from auto_ext.ui import theme
from auto_ext.ui.widgets.option_editor import (
    ElidedLabel,
    OptionEditor,
    OptionGroup,
    group_label,
    template_freezes,
)
from auto_ext.ui.widgets.patch_strip import PatchStrip
from auto_ext.ui.widgets.recipe_import_dialog import RecipeImportDialog

__all__ = [
    "GROUP_ORDER",
    "OBJ_FORM_HEADER",
    "OBJ_LIST_HEADER",
    "OBJ_LIST_TOOLBAR",
    "OBJ_OPTIONS_SUMMARY",
    "OBJ_RECIPE_LIST",
    "RECIPE_LIST_WIDTH",
    "RecipesScreen",
    "frozen_specs",
    "grouped_specs",
    "import_status_text",
    "recipe_specs",
]

#: Groups in the order artboard ``1f`` reads top to bottom, with the two the
#: artboard does not draw appended. Anything the catalog grows later is sorted
#: in after these rather than dropped.
GROUP_ORDER: tuple[str, ...] = (
    "extraction",
    "output",
    "lvs",
    "reduction",
    "netlist",
    "policy",
    "general",
)

#: Width of the recipe list. Artboard ``1f``: 214px.
RECIPE_LIST_WIDTH = 214

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

_DOT = " · "
_EM_DASH = "—"
_GLYPH_EXPANDED = "▾"

#: Fallback group for a recipe field that sits at the root of the model.
_ROOT_GROUP = "general"


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


def grouped_specs(catalog: Catalog | None = None) -> list[tuple[str, list[OptionSpec]]]:
    """``[(group name, specs)]`` -- the form's shape, straight from the catalog."""

    buckets: dict[str, list[OptionSpec]] = {}
    for spec in recipe_specs(catalog):
        path = spec.recipe_field_path or ""
        group = path.split(".")[0] if "." in path else _ROOT_GROUP
        buckets.setdefault(group, []).append(spec)

    def order(name: str) -> tuple[int, str]:
        try:
            return (GROUP_ORDER.index(name), "")
        except ValueError:
            return (len(GROUP_ORDER), name)

    return [(name, buckets[name]) for name in sorted(buckets, key=order)]


def _group_subtitle(catalog: Catalog, specs: Iterable[OptionSpec]) -> str:
    """The template files a group's options land in, deduplicated, in order."""

    seen: list[str] = []
    for spec in specs:
        for target in spec.targets:
            try:
                template_id = catalog.target(target).template_id
            except KeyError:  # pragma: no cover - the catalog validates this
                continue
            if template_id not in seen:
                seen.append(template_id)
    return ", ".join(seen)


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
        root.addWidget(self._build_list_panel())
        root.addWidget(self._build_form_panel(), 1)

        self._refresh_header()
        self._refresh_status()

    # -- construction --------------------------------------------------

    def _build_list_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setFrameShape(QFrame.NoFrame)
        panel.setFixedWidth(RECIPE_LIST_WIDTH)
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
        self._list.setUniformRowHeights(True)
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

    def _build_groups(self) -> None:
        for name, specs in grouped_specs(self._catalog):
            group = OptionGroup(
                group_label(name),
                _group_subtitle(self._catalog, specs),
                parent=self._form_host,
            )
            group.add_options(specs)
            group.value_changed.connect(self._on_value_changed)
            self._form_layout.addWidget(group)
            self._groups[name] = group
            for spec in specs:
                editor = group.grid.editor(spec.key)
                if editor is not None:
                    self._editors[spec.key] = editor

    # -- data ----------------------------------------------------------

    def set_recipes(
        self, recipes: Sequence[Recipe], *, select: str | None = None
    ) -> None:
        """Replace the library. Selection follows ``select``, else the first row."""

        self._recipes = list(recipes)
        self._list.blockSignals(True)
        self._list.clear()
        for recipe in self._recipes:
            item = QTreeWidgetItem([recipe.name, self._edit_badge(recipe)])
            item.setData(0, Qt.UserRole, recipe.recipe_id)
            item.setSizeHint(0, QSize(0, RECIPE_ROW_HEIGHT))
            item.setToolTip(0, recipe.description or recipe.name)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            if recipe.manual_edit_count:
                item.setForeground(1, QColor(theme.WARNING_TEXT_ON_WHITE))
                item.setToolTip(
                    1, f"{recipe.manual_edit_count} manual edits to the generated files"
                )
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

    def _on_name_edited(self, text: str) -> None:
        """Rename the working copy as the user types, list row included.

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
            item.setText(0, name)
            item.setToolTip(0, self._working.description or name)
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
