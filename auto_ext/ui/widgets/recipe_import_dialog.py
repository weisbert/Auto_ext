"""Import the user's own EDA files as a recipe.

The Recipes screen's ``Import...`` button opens this. It is the GUI over
:func:`auto_ext.core.recipe_import.import_recipe`, and it exists because the
files a user already has -- a ``.cmd`` saved out of the Quantus GUI, a ``.qci``
a colleague sent, an ``si.env`` grown over three tape-outs -- are the real
starting point, and until now the only way in was ``auto-ext migrate``, which
eats a v1 ``project.yaml`` nobody has any more.

Two pages, one decision each
----------------------------

**Files.** Drop or pick any number of files. Each row shows what the *content*
was recognised as -- never the file name, see
:func:`~auto_ext.core.recipe_import.detect_target` -- and a file the detector
refuses to classify gets the same combo box with nothing chosen, so the user
can name the target instead of the import failing. Nothing is read back or
rendered until ``Analyse``.

**Report.** Three sections, and the third is the point of the dialog:

``Read into the recipe or the profile``
    Values the catalog models and one of the two objects can hold. Recipe
    values are portable: they survive a PDK swap and a different cell. Profile
    values are the PDK facts the same files carry -- the corner literal, the
    deck paths -- and they land in the derived baseline profile rather than in
    a patch, which is what keeps the Recipe field that selects them alive.
``Kept as manual edits``
    Everything the catalog does not model but the file says anyway, captured
    as masked, anchored hunks. Expands to the diff, one hunk at a time.
``Not modelled -- left at the catalog default``
    The honest number. Options the user's file sets that neither object has a
    field for, grouped by *why*. A user who imports a 400-line ``.qci`` and is
    told "42 values imported" without this section has been told a half-truth:
    the other ninety options are still in the file, and they come out right
    only because the shipped template happens to write the same text.

Ratio warning
-------------
:attr:`~auto_ext.core.recipe_import.RecipeImportResult.warnings` carries the
"this is a fork, not a patch" message when too many of the imported lines had
to become hunks. It is shown in the amber warning scale, never the red failure
scale: a large patch is a judgement about whether the import is worth keeping,
not a failed operation.

What this dialog does not do
----------------------------
It never writes. :func:`~auto_ext.core.recipe_import.import_recipe` is a dry
run by design and ``write_imported_recipe`` is the separate step, so the report
is shown before anything lands on disk. Accepting emits
:attr:`RecipeImportDialog.import_accepted` with the whole
:class:`~auto_ext.core.recipe_import.RecipeImportResult`; the host persists it,
exactly as it already does for ``RecipesScreen.save_requested``.

Assumptions
-----------
* The analysis runs on the GUI thread. It measures at about 25 ms for all five
  targets at once, which is under one frame; a worker thread here would buy
  nothing and cost the cancellation plumbing.
* ``Recipe.recipe_id`` is derived from the name with
  :func:`~auto_ext.model.common.slugify` and made unique against the ids the
  host hands in, because two recipes with one id is a silent overwrite at save
  time and there is nowhere else to catch it.
* The diff rows come from :mod:`auto_ext.ui.widgets.patch_strip` rather than a
  second renderer, so a hunk looks the same here as it does in the escape hatch
  it is about to become.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from auto_ext.catalog import Catalog, builtin_catalog
from auto_ext.core.errors import AutoExtError
from auto_ext.core.patch_models import PatchHunk
from auto_ext.core.recipe_import import (
    ImportSource,
    PatchedHunk,
    RecipeImportError,
    RecipeImportResult,
    detect_target,
    import_recipe,
)
from auto_ext.model.common import RenderTarget, slugify
from auto_ext.ui import theme
from auto_ext.ui.widgets.drop_zone import DropZone
from auto_ext.ui.widgets.option_editor import ElidedLabel
from auto_ext.ui.widgets.patch_strip import DiffBlock, diff_rows

__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "FILE_FILTER",
    "MAX_HINT_HEIGHT",
    "MAX_HINT_WIDTH",
    "OBJ_DIALOG_HEADER",
    "OBJ_FILE_ROW",
    "OBJ_MESSAGE",
    "OBJ_SECTION_BODY",
    "OBJ_SECTION_HEADER",
    "OBJ_WARNING_BANNER",
    "PAGE_FILES",
    "PAGE_REPORT",
    "UNKNOWN_TARGET_TEXT",
    "DefaultRow",
    "FileRow",
    "MultiDropZone",
    "RecipeImportDialog",
    "ReportSection",
    "ValueRow",
    "default_groups",
    "default_rows",
    "hunk_entries",
    "unique_recipe_id",
    "value_rows",
]

#: The dialog has to be squeezable to this, so it still opens inside the
#: 940x560 window floor artboard ``1j`` fixes. Asserted by the tests on both
#: pages, empty and full: the widgets this generation replaces opened at a
#: hardcoded 900x640 with four fields in them.
MAX_HINT_WIDTH = 560
MAX_HINT_HEIGHT = 420

#: What it opens at when the user has not resized it. Comfortably inside the
#: window floor, and only a preference -- the minimum above is the contract.
DEFAULT_WIDTH = 620
DEFAULT_HEIGHT = 460

PAGE_FILES = "files"
PAGE_REPORT = "report"

#: Extensions the five render targets are written with in practice. Detection
#: reads content, so this only narrows the file chooser.
FILE_FILTER = "EDA inputs (*.env *.qci *.cmd *.xml);;All files (*)"

#: Shown in the target combo of a file the detector would not classify. The en
#: dash is in the design's glyph set.
UNKNOWN_TARGET_TEXT = "not recognised – choose"

OBJ_DIALOG_HEADER = "importDialogHeader"
OBJ_FILE_ROW = "importFileRow"
OBJ_MESSAGE = "importMessage"
OBJ_SECTION_HEADER = "importSectionHeader"
OBJ_SECTION_BODY = "importSectionBody"
OBJ_WARNING_BANNER = "importWarningBanner"

_GLYPH_COLLAPSED = "▶"
_GLYPH_EXPANDED = "▾"
_GLYPH_PASSED = "✓"
_GLYPH_NEUTRAL = "–"
_DOT = " · "
_EM_DASH = "—"

_UNRECOGNISED_HINT = " could not be recognised. Name the target, or remove the file."


# ---- pure helpers ----------------------------------------------------------
# Everything above the widget section is importable and assertable without a
# QApplication, which is where the report's meaning is actually pinned down.


@dataclass(frozen=True)
class ValueRow:
    """One catalog value that made it into the Recipe or the PdkProfile."""

    key: str
    value: str
    #: Where it landed, object included: ``recipe.extraction.min_res_ohm`` or
    #: ``profile.corners.rcworst.technology_corner``.
    field: str
    where: str

    def as_line(self) -> str:
        return f"{self.key} = {self.value} -> {self.field}"


@dataclass(frozen=True)
class DefaultRow:
    """One option the Recipe cannot carry, so the catalog default stands."""

    key: str
    #: The value read out of the user's file, or ``""`` when nothing could be
    #: read at all -- two different accidents, and the row says which.
    value: str
    reason: str
    where: str

    def as_line(self) -> str:
        return f"{self.key} = {self.value}" if self.value else self.key


def value_rows(result: RecipeImportResult) -> list[ValueRow]:
    """The first section: what the import actually put in the recipe or profile.

    Both objects, one section. A value that landed in the derived PdkProfile
    -- the corner literal, the deck directory, the parasitic device names --
    is as understood as one that landed in a Recipe field, and listing it
    among the things the import could *not* place would be the same half-truth
    the third section exists to prevent.
    """

    rows = [
        ValueRow(
            key=item.key,
            value=repr(item.value),
            field=item.landed_in,
            where=item.site.describe(),
        )
        for item in result.mapped
        if item.applied_to
    ]
    return sorted(rows, key=lambda row: row.key)


def default_rows(result: RecipeImportResult) -> list[DefaultRow]:
    """The third section: everything in the files the recipe cannot hold.

    Two populations, deliberately merged. A value that was *read* but has no
    Recipe field (a ``mapped`` row with no ``applied_to``) and a value the
    readers could not recover at all (an ``unread`` key) are different
    accidents with one consequence: the catalog default is what gets rendered,
    and any disagreement with the user's file is in the second section as a
    hunk. Reporting them apart would make the user add two numbers together to
    answer "how much of my file does this thing not understand".
    """

    rows = [
        DefaultRow(
            key=item.key,
            value=repr(item.value),
            reason=item.note or "no recipe field",
            where=item.site.describe(),
        )
        for item in result.mapped
        if not item.applied_to
    ]
    rows.extend(
        DefaultRow(key=key, value="", reason=reason, where="")
        for key, reason in result.unread.items()
    )
    return sorted(rows, key=lambda row: row.key)


def default_groups(rows: Sequence[DefaultRow]) -> list[tuple[str, list[DefaultRow]]]:
    """``[(reason, rows)]``, biggest group first.

    Ninety rows each carrying the same sentence is a wall of text; ninety keys
    under one sentence is a fact. Biggest first, so the dominant reason -- in
    practice "the template writes this as a literal" -- is what gets read.
    """

    buckets: dict[str, list[DefaultRow]] = {}
    for row in rows:
        buckets.setdefault(row.reason, []).append(row)
    return sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))


def hunk_entries(
    result: RecipeImportResult,
) -> list[tuple[PatchedHunk, PatchHunk | None]]:
    """Pair each reported hunk with the stored one, for the diff.

    :class:`~auto_ext.core.recipe_import.PatchedHunk` is a summary -- counts
    and a first line. The text lives on the :class:`PatchHunk` inside the
    recipe, and the pair is what one row needs.
    """

    stored: dict[tuple[str, str], PatchHunk] = {
        (patch.template_id, hunk.id): hunk
        for patch in result.recipe.patches
        for hunk in patch.hunks
    }
    return [
        (item, stored.get((item.template_id, item.hunk_id))) for item in result.as_patch
    ]


def unique_recipe_id(name: str, taken: Iterable[str] = ()) -> str:
    """A slug for ``name`` that is not already in ``taken``.

    A collision is resolved by suffix rather than refused: the user is
    importing a second Quantus command file, and "pick another name" when the
    tool can pick one is friction with no information in it.
    """

    used = set(taken)
    base = slugify(name, max_len=56) if name.strip() else "imported"
    if base not in used:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in used:
            return candidate
    return base  # pragma: no cover - a thousand recipes of one name


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _failure_text(exc: Exception) -> str:
    """One readable line for anything the import can refuse a file with.

    :class:`~auto_ext.core.recipe_import.RecipeImportError` is already written
    for a person. A :class:`~pydantic.ValidationError` is not, and it is
    reachable from ordinary input: forty extra lines at the end of a ``.qci``
    become a pure insertion with no anchor after it, which ``PatchHunk``
    refuses. Its ``str()`` is four lines of pydantic bookkeeping, so only the
    first message is shown.
    """

    if isinstance(exc, ValidationError):
        errors = exc.errors()
        detail = str(errors[0].get("msg", "")) if errors else str(exc)
        return f"these files cannot be turned into a recipe -- {detail}"
    return str(exc)


def _let_shrink(widget: QWidget) -> None:
    """Let a layout squeeze this widget past its own text width.

    ``qSmartMinSize`` honours an explicit ``minimumWidth`` over the widget's
    ``minimumSizeHint``, so this one pixel is the difference between a dialog
    that folds into the window floor and one whose button row sets a minimum
    nobody asked for.
    """

    widget.setMinimumWidth(1)


# ---- small widgets ---------------------------------------------------------


class MultiDropZone(DropZone):
    """:class:`DropZone`, for any number of files and in the design's colours.

    The base class accepts exactly one local file, which is right for the init
    wizard's one-slot-per-tool rows and wrong here: an import is usually the
    three or four files that describe one flow, dropped together. Only the
    arity changes; the hover convention and the caption are inherited.

    The two style strings are replaced rather than edited, because the base
    class predates ``theme`` and hardcodes its colours.
    """

    #: ``list[Path]`` -- every local file in one drop, in the order given.
    paths_dropped = pyqtSignal(object)

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self._normal_style = (
            f"QFrame {{ border: 1px dashed {theme.LINE_STRUCTURAL};"
            f" border-radius: {theme.RADIUS}px;"
            f" background: {theme.SURFACE_CARD};"
            f" min-height: {theme.TOOLBAR_HEIGHT}px; }}"
        )
        self._active_style = (
            f"QFrame {{ border: 1px dashed {theme.ACCENT};"
            f" border-radius: {theme.RADIUS}px;"
            f" background: {theme.ACCENT_TINT};"
            f" min-height: {theme.TOOLBAR_HEIGHT}px; }}"
        )
        self.setStyleSheet(self._normal_style)
        self._label.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        _let_shrink(self)

    @staticmethod
    def _local_paths(event: QDragEnterEvent | QDropEvent) -> list[Path]:
        data = event.mimeData()
        urls = data.urls() if data is not None else []
        return [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt naming
        if self._local_paths(event):
            event.acceptProposedAction()
            self.setStyleSheet(self._active_style)
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt naming
        self.setStyleSheet(self._normal_style)
        paths = self._local_paths(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.paths_dropped.emit(paths)


class FileRow(QFrame):
    """One offered file: its name, what it was recognised as, and a way out.

    The combo is there whether or not detection succeeded. A detected row is
    still overridable -- a user who saved a ``dspf.cmd`` out of a session
    configured for extracted views knows something the content does not say --
    and an override is recorded, so the report can mark the file as named by
    hand rather than recognised.
    """

    changed = pyqtSignal()
    remove_requested = pyqtSignal(object)

    def __init__(
        self,
        label: str,
        text: str,
        detected: RenderTarget | None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_FILE_ROW)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"QFrame#{OBJ_FILE_ROW} {{ background: {theme.SURFACE_CARD};"
            f" border: none; border-bottom: 1px solid {theme.LINE_ROW}; }}"
        )
        self._label = label
        self._text = text
        self._detected = detected

        row = QHBoxLayout(self)
        row.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS
        )
        row.setSpacing(theme.SPACE_SM)

        self._name = ElidedLabel(Path(label).name, mode=Qt.ElideMiddle, parent=self)
        self._name.setToolTip(label)
        self._name.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_MONO}px;"
        )
        row.addWidget(self._name, 1)

        self._lines = QLabel(_plural(len(text.splitlines()), "line"), self)
        self._lines.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        _let_shrink(self._lines)
        row.addWidget(self._lines, 0)

        self._combo = QComboBox(self)
        # Wide enough for the longest target name and no wider. Not shrunk to
        # one pixel like the labels around it: the name beside it can elide
        # away, this is the row's only control and has to stay readable.
        self._combo.setMinimumContentsLength(len(RenderTarget.QUANTUS_DSPF.value))
        self._combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self._combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._combo.setToolTip("What this file is. Detected from its content.")
        if detected is None:
            self._combo.addItem(UNKNOWN_TARGET_TEXT, None)
        for target in RenderTarget:
            self._combo.addItem(target.value, target.value)
        if detected is not None:
            self._combo.setCurrentIndex(self._combo.findData(detected.value))
        self._combo.currentIndexChanged.connect(lambda _index: self.changed.emit())
        row.addWidget(self._combo, 0)

        self._remove = QPushButton("Remove", self)
        self._remove.setToolTip("Take this file out of the import")
        self._remove.clicked.connect(lambda: self.remove_requested.emit(self))
        _let_shrink(self._remove)
        row.addWidget(self._remove, 0)

    # -- accessors -----------------------------------------------------

    @property
    def label(self) -> str:
        return self._label

    @property
    def text(self) -> str:
        return self._text

    @property
    def detected(self) -> RenderTarget | None:
        """What the content said, before any override."""

        return self._detected

    def combo(self) -> QComboBox:
        return self._combo

    def chosen(self) -> RenderTarget | None:
        data = self._combo.currentData()
        return RenderTarget(data) if data is not None else None

    def is_forced(self) -> bool:
        """The target on this row is the user's answer, not the content's."""

        chosen = self.chosen()
        return chosen is not None and chosen is not self._detected

    def source(self) -> ImportSource:
        """The row as an importer input.

        ``target`` is passed only when the user overrode the detector, so a
        recognised file is classified again inside the import and
        ``ImportedFile.forced`` stays honest.
        """

        return ImportSource(
            label=self._label,
            text=self._text,
            target=self.chosen() if self.is_forced() else None,
        )


class ReportSection(QFrame):
    """One collapsible section of the report: title, count, hint, body.

    The count sits in the header in mono, so the three sections read as three
    numbers without opening any of them -- which is the whole request: how much
    landed, how much became a patch, how much is not modelled at all.
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        unit: str,
        *,
        hint: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self._unit = unit
        self._count = 0
        self._expanded = False

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        header = QFrame(self)
        header.setObjectName(OBJ_SECTION_HEADER)
        header.setFrameShape(QFrame.NoFrame)
        header.setStyleSheet(
            f"QFrame#{OBJ_SECTION_HEADER} {{ background: {theme.SURFACE_TABLE_HEADER};"
            f" border: none; border-bottom: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        bar = QHBoxLayout(header)
        bar.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS
        )
        bar.setSpacing(theme.SPACE_SM)

        self._glyph = QLabel(_GLYPH_COLLAPSED, header)
        self._glyph.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; color: {theme.TEXT_SECONDARY};"
        )
        bar.addWidget(self._glyph, 0)

        self._title = QLabel(title, header)
        self._title.setStyleSheet(f"font-weight: {theme.FONT_WEIGHT_SEMIBOLD};")
        _let_shrink(self._title)
        bar.addWidget(self._title, 0)

        self._count_label = QLabel("", header)
        _let_shrink(self._count_label)
        bar.addWidget(self._count_label, 0)

        self._hint = ElidedLabel(hint, parent=header)
        self._hint.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        bar.addWidget(self._hint, 1)

        self._toggle = QPushButton("Show", header)
        self._toggle.clicked.connect(self.toggle)
        _let_shrink(self._toggle)
        bar.addWidget(self._toggle, 0)
        column.addWidget(header)

        self._body = QWidget(self)
        self._body.setObjectName(OBJ_SECTION_BODY)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_SM, theme.SPACE_SM
        )
        self._body_layout.setSpacing(theme.SPACE_XS)
        self._body.setVisible(False)
        column.addWidget(self._body)

        self.set_count(0)

    # -- content -------------------------------------------------------

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def clear(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def add_widget(self, widget: QWidget) -> None:
        widget.setParent(self._body)
        self._body_layout.addWidget(widget)

    def set_count(self, count: int, *, emphasis: str | None = None) -> None:
        """Put ``count`` in the header, optionally in the ``emphasis`` colour."""

        self._count = count
        self._count_label.setText(_plural(count, self._unit))
        colour = emphasis or (theme.TEXT_PRIMARY if count else theme.TEXT_DISABLED)
        self._count_label.setStyleSheet(
            f"font-family: {theme.FONT_MONO};"
            f" font-weight: {theme.FONT_WEIGHT_SEMIBOLD}; color: {colour};"
        )
        self._toggle.setEnabled(count > 0)
        if not count and self._expanded:
            self.set_expanded(False)

    def set_hint(self, text: str) -> None:
        self._hint.set_full_text(text)

    # -- state ---------------------------------------------------------

    def count(self) -> int:
        return self._count

    def count_text(self) -> str:
        return self._count_label.text()

    def hint_text(self) -> str:
        return self._hint.full_text()

    def title_text(self) -> str:
        return self._title.text()

    def toggle_button(self) -> QPushButton:
        return self._toggle

    def glyph_text(self) -> str:
        return self._glyph.text()

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        """Open or close the body. Opening an empty section is a no-op."""

        expanded = bool(expanded) and self._count > 0
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._body.setVisible(expanded)
        self._glyph.setText(_GLYPH_EXPANDED if expanded else _GLYPH_COLLAPSED)
        self._toggle.setText("Hide" if expanded else "Show")
        self.toggled.emit(expanded)


class _ListBlock(QLabel):
    """A list of ``(text, meta)`` pairs as one rich-text widget.

    One widget, not one per row: the third section can hold a hundred rows, and
    a hundred labels is a hundred layout items to place and repaint over a link
    that is usually X11 forwarding.
    """

    def __init__(
        self,
        rows: Sequence[tuple[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, str]] = []
        self.setTextFormat(Qt.RichText)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setWordWrap(False)
        self.set_rows(rows or [])

    def set_rows(self, rows: Sequence[tuple[str, str]]) -> None:
        self._rows = list(rows)
        self.setText(self._build_html())

    def rows(self) -> list[tuple[str, str]]:
        return list(self._rows)

    def plain_text(self) -> str:
        return "\n".join(
            f"{text}    {meta}" if meta else text for text, meta in self._rows
        )

    def _build_html(self) -> str:
        parts = [
            f'<div style="font-family:{theme.FONT_MONO};'
            f"font-size:{theme.FONT_SIZE_MONO}px;line-height:135%;\">"
        ]
        for text, meta in self._rows:
            body = html.escape(text).replace(" ", "&nbsp;")
            tail = (
                f'<span style="color:{theme.TEXT_SECONDARY};'
                f'font-size:{theme.FONT_SIZE_META}px;">'
                f"&nbsp;&nbsp;{html.escape(meta)}</span>"
                if meta
                else ""
            )
            parts.append(f"<div>{body}{tail}</div>")
        parts.append("</div>")
        return "".join(parts)


# ---- the dialog ------------------------------------------------------------


class RecipeImportDialog(QDialog):
    """Pick files, see what they became, confirm. Writes nothing itself."""

    #: How many files are currently offered.
    files_changed = pyqtSignal(int)
    #: The analysis succeeded. Carries the
    #: :class:`~auto_ext.core.recipe_import.RecipeImportResult`.
    analysed = pyqtSignal(object)
    #: The analysis refused these files. Carries the message shown.
    analysis_failed = pyqtSignal(str)
    #: The user confirmed the report. Carries the ``RecipeImportResult``; the
    #: host persists it with ``recipe_import.write_imported_recipe``.
    import_accepted = pyqtSignal(object)

    def __init__(
        self,
        *,
        catalog: Catalog | None = None,
        existing_ids: Sequence[str] = (),
        start_dir: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import a recipe from EDA files")
        self._catalog = catalog if catalog is not None else builtin_catalog()
        self._existing = list(existing_ids)
        self._start_dir = Path(start_dir) if start_dir is not None else None
        self._rows: list[FileRow] = []
        self._result: RecipeImportResult | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_files_page())
        self._stack.addWidget(self._build_report_page())
        column.addWidget(self._stack, 1)
        column.addWidget(self._build_buttons())

        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._refresh_files_page()

    # -- construction --------------------------------------------------

    def _build_files_page(self) -> QWidget:
        page = QWidget(self)
        column = QVBoxLayout(page)
        column.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        column.setSpacing(theme.SPACE_SM)

        intro = QLabel(
            "Files you already have -- a Quantus command file, a Calibre deck "
            "setup, an si.env -- become one recipe. Each file is recognised by "
            "what is in it, never by its name.",
            page,
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        _let_shrink(intro)
        column.addWidget(intro)

        self._drop = MultiDropZone("Drop EDA files here", page)
        self._drop.paths_dropped.connect(self._on_paths_dropped)
        column.addWidget(self._drop)

        self._file_scroll = QScrollArea(page)
        self._file_scroll.setWidgetResizable(True)
        self._file_scroll.setFrameShape(QFrame.NoFrame)
        self._file_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self._file_host = QWidget()
        self._file_layout = QVBoxLayout(self._file_host)
        self._file_layout.setContentsMargins(0, 0, 0, 0)
        self._file_layout.setSpacing(0)
        self._empty_label = QLabel("No files yet.", self._file_host)
        self._empty_label.setStyleSheet(f"color: {theme.TEXT_DISABLED};")
        _let_shrink(self._empty_label)
        self._file_layout.addWidget(self._empty_label)
        self._file_layout.addStretch(1)
        self._file_scroll.setWidget(self._file_host)
        column.addWidget(self._file_scroll, 1)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(theme.SPACE_SM)
        caption = QLabel("Recipe name", page)
        _let_shrink(caption)
        name_row.addWidget(caption, 0)
        self._name_edit = QLineEdit(page)
        self._name_edit.setPlaceholderText("named after the first file")
        self._name_edit.textChanged.connect(self._on_name_changed)
        _let_shrink(self._name_edit)
        name_row.addWidget(self._name_edit, 1)
        self._id_label = QLabel("", page)
        self._id_label.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        _let_shrink(self._id_label)
        name_row.addWidget(self._id_label, 0)
        column.addLayout(name_row)

        self._message = QLabel("", page)
        self._message.setObjectName(OBJ_MESSAGE)
        self._message.setWordWrap(True)
        self._message.setVisible(False)
        _let_shrink(self._message)
        column.addWidget(self._message)
        return page

    def _build_report_page(self) -> QWidget:
        page = QWidget(self)
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        header = QFrame(page)
        header.setObjectName(OBJ_DIALOG_HEADER)
        header.setFrameShape(QFrame.NoFrame)
        header.setStyleSheet(
            f"QFrame#{OBJ_DIALOG_HEADER} {{ background: {theme.SURFACE_TOOLBAR};"
            f" border: none; border-bottom: 1px solid {theme.LINE_STRUCTURAL}; }}"
        )
        bar = QHBoxLayout(header)
        bar.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS
        )
        bar.setSpacing(theme.SPACE_SM)
        self._report_title = ElidedLabel("", parent=header)
        self._report_title.setStyleSheet(
            f"font-size: {theme.FONT_SIZE_SECTION}px;"
            f" font-weight: {theme.FONT_WEIGHT_SEMIBOLD};"
        )
        bar.addWidget(self._report_title, 0)
        self._report_meta = ElidedLabel("", parent=header)
        self._report_meta.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        bar.addWidget(self._report_meta, 1)
        column.addWidget(header)

        self._warning = QLabel("", page)
        self._warning.setObjectName(OBJ_WARNING_BANNER)
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet(
            f"QLabel#{OBJ_WARNING_BANNER} {{"
            f" background: {theme.STATUS_FILL['warning']};"
            f" border: none; border-bottom: 1px solid {theme.STATUS_LINE['warning']};"
            f" color: {theme.WARNING_TEXT_ON_WHITE};"
            f" font-size: {theme.FONT_SIZE_META}px;"
            f" padding: {theme.SPACE_XS}px {theme.SPACE_MD}px; }}"
        )
        self._warning.setVisible(False)
        _let_shrink(self._warning)
        column.addWidget(self._warning)

        self._report_scroll = QScrollArea(page)
        self._report_scroll.setWidgetResizable(True)
        self._report_scroll.setFrameShape(QFrame.NoFrame)
        self._report_scroll.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        host = QWidget()
        body = QVBoxLayout(host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(theme.SPACE_SM)

        self._values_section = ReportSection(
            "Read into the recipe or the profile",
            "value",
            hint="portable: these survive another cell and another PDK",
            parent=host,
        )
        body.addWidget(self._values_section)

        self._hunks_section = ReportSection(
            "Kept as manual edits",
            "hunk",
            hint="what the catalog does not model, stored as an anchored diff",
            parent=host,
        )
        body.addWidget(self._hunks_section)

        self._defaults_section = ReportSection(
            f"Not modelled {_GLYPH_NEUTRAL} left at the catalog default",
            "option",
            hint="your files set these; the recipe has no field to carry them",
            parent=host,
        )
        body.addWidget(self._defaults_section)
        body.addStretch(1)
        self._report_scroll.setWidget(host)
        column.addWidget(self._report_scroll, 1)

        self._roundtrip = QLabel("", page)
        self._roundtrip.setWordWrap(True)
        self._roundtrip.setStyleSheet(
            f"background: {theme.SURFACE_PAGE};"
            f" border-top: 1px solid {theme.LINE_PANEL};"
            f" color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
            f" padding: {theme.SPACE_XS}px {theme.SPACE_MD}px;"
        )
        _let_shrink(self._roundtrip)
        column.addWidget(self._roundtrip)
        return page

    def _build_buttons(self) -> QWidget:
        bar = QFrame(self)
        bar.setFrameShape(QFrame.NoFrame)
        bar.setStyleSheet(
            f"background: {theme.SURFACE_TOOLBAR};"
            f" border-top: 1px solid {theme.LINE_STRUCTURAL};"
        )
        row = QHBoxLayout(bar)
        row.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS
        )
        row.setSpacing(theme.SPACE_XS)

        self._add_button = QPushButton("Add files...", bar)
        self._add_button.clicked.connect(self._on_add_clicked)
        _let_shrink(self._add_button)
        row.addWidget(self._add_button, 0)

        self._back_button = QPushButton("Back", bar)
        self._back_button.clicked.connect(self.show_files_page)
        _let_shrink(self._back_button)
        row.addWidget(self._back_button, 0)
        row.addStretch(1)

        self._cancel_button = QPushButton("Cancel", bar)
        self._cancel_button.clicked.connect(self.reject)
        _let_shrink(self._cancel_button)
        row.addWidget(self._cancel_button, 0)

        self._analyse_button = QPushButton("Analyse", bar)
        self._analyse_button.setProperty("primary", "true")
        self._analyse_button.clicked.connect(self.analyse)
        _let_shrink(self._analyse_button)
        row.addWidget(self._analyse_button, 0)

        self._import_button = QPushButton("Import", bar)
        self._import_button.setProperty("primary", "true")
        self._import_button.clicked.connect(self.accept)
        _let_shrink(self._import_button)
        row.addWidget(self._import_button, 0)

        # Enter must do the page's own action. Left alone, Qt hands Return to
        # the first auto-default button in the focus chain -- which here is
        # "Add files...", so typing a name and pressing Enter would open a
        # file chooser. The page's primary button is made the default instead,
        # in _refresh_files_page, and nothing else may claim it.
        for button in (
            self._add_button,
            self._back_button,
            self._cancel_button,
            self._analyse_button,
            self._import_button,
        ):
            button.setAutoDefault(False)
        return bar

    # -- accessors -----------------------------------------------------

    def page(self) -> str:
        return PAGE_FILES if self._stack.currentIndex() == 0 else PAGE_REPORT

    def file_rows(self) -> list[FileRow]:
        return list(self._rows)

    def drop_zone(self) -> MultiDropZone:
        return self._drop

    def name_edit(self) -> QLineEdit:
        return self._name_edit

    def add_button(self) -> QPushButton:
        return self._add_button

    def back_button(self) -> QPushButton:
        return self._back_button

    def analyse_button(self) -> QPushButton:
        return self._analyse_button

    def import_button(self) -> QPushButton:
        return self._import_button

    def cancel_button(self) -> QPushButton:
        return self._cancel_button

    def values_section(self) -> ReportSection:
        return self._values_section

    def hunks_section(self) -> ReportSection:
        return self._hunks_section

    def defaults_section(self) -> ReportSection:
        return self._defaults_section

    def sections(self) -> list[ReportSection]:
        """The three, in the order they are read."""

        return [self._values_section, self._hunks_section, self._defaults_section]

    def message_label(self) -> QLabel:
        """The line under the file list. Exposed so its colour is assertable."""

        return self._message

    def warning_label(self) -> QLabel:
        """The report's warning banner. Amber, never the failure scale."""

        return self._warning

    def message_text(self) -> str:
        """The line under the file list, empty when there is none.

        Read off the text rather than the widget's visibility: a child of a
        dialog that has not been shown yet reports ``isVisible() is False``
        whatever it was told, and the message is set while the dialog is still
        being filled.
        """

        return self._message.text()

    def warning_text(self) -> str:
        return self._warning.text()

    def roundtrip_text(self) -> str:
        return self._roundtrip.text()

    def report_meta_text(self) -> str:
        return self._report_meta.full_text()

    def result_object(self) -> RecipeImportResult | None:
        """The analysed import, or ``None`` before ``Analyse`` has succeeded.

        Not called ``result``: :meth:`QDialog.result` is Accepted/Rejected, and
        overriding it would break the dialog protocol every caller relies on.
        """

        return self._result

    def recipe_name(self) -> str:
        typed = self._name_edit.text().strip()
        if typed:
            return typed
        return Path(self._rows[0].label).stem if self._rows else "Imported recipe"

    def recipe_id(self) -> str:
        return unique_recipe_id(self.recipe_name(), self._existing)

    # -- files ---------------------------------------------------------

    def add_paths(self, paths: Iterable[Path | str]) -> int:
        """Read and classify ``paths``. Returns how many were accepted."""

        added = 0
        problems: list[str] = []
        for path in paths:
            try:
                source = ImportSource.from_path(path)
            except RecipeImportError as exc:
                problems.append(str(exc))
                continue
            if self.add_source(source.label, source.text):
                added += 1
        if problems:
            self._show_message("\n".join(problems), error=True)
        return added

    def add_source(self, label: str, text: str) -> bool:
        """Add one already-read file. ``False`` when it is already offered."""

        if any(row.label == label for row in self._rows):
            return False
        try:
            detected: RenderTarget | None = detect_target(
                text.replace("\r\n", "\n"), label=label, catalog=self._catalog
            )
        except RecipeImportError:
            detected = None
        row = FileRow(label, text, detected, parent=self._file_host)
        row.changed.connect(self._refresh_files_page)
        row.remove_requested.connect(self._remove_row)
        self._rows.append(row)
        self._file_layout.insertWidget(self._file_layout.count() - 1, row)
        self._refresh_files_page()
        self.files_changed.emit(len(self._rows))
        return True

    def _remove_row(self, row: object) -> None:
        if not isinstance(row, FileRow) or row not in self._rows:
            return
        self._rows.remove(row)
        self._file_layout.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        self._refresh_files_page()
        self.files_changed.emit(len(self._rows))

    def _on_paths_dropped(self, paths: object) -> None:
        self.add_paths(list(paths))  # type: ignore[arg-type]

    def _on_add_clicked(self) -> None:
        start = str(self._start_dir) if self._start_dir is not None else ""
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, "Add EDA files", start, FILE_FILTER
        )
        if chosen:
            self.add_paths(chosen)

    def _on_name_changed(self, _text: str) -> None:
        self._refresh_files_page()

    def _refresh_files_page(self) -> None:
        count = len(self._rows)
        self._empty_label.setVisible(count == 0)
        unclassified = sum(1 for row in self._rows if row.chosen() is None)
        on_files = self.page() == PAGE_FILES
        self._add_button.setVisible(on_files)
        self._analyse_button.setVisible(on_files)
        self._back_button.setVisible(not on_files)
        self._import_button.setVisible(not on_files)
        self._analyse_button.setEnabled(count > 0 and not unclassified)
        self._analyse_button.setDefault(on_files)
        self._import_button.setDefault(not on_files)
        self._id_label.setText(f"id {self.recipe_id()}" if count else "")
        if unclassified:
            self._show_message(_plural(unclassified, "file") + _UNRECOGNISED_HINT)
        elif self.message_text().endswith(_UNRECOGNISED_HINT):
            self._show_message("")

    def _show_message(self, text: str, *, error: bool = False) -> None:
        """One line under the file list. Amber for a caveat, red for a refusal.

        The accent is never either of those, and a file this dialog cannot read
        is a genuine failure rather than a warning, so the two scales stay
        apart here as everywhere else.
        """

        colour = theme.STATUS_FAILED if error else theme.WARNING_TEXT_ON_WHITE
        self._message.setStyleSheet(
            f"QLabel#{OBJ_MESSAGE} {{ color: {colour};"
            f" font-size: {theme.FONT_SIZE_META}px; }}"
        )
        self._message.setText(text)
        self._message.setVisible(bool(text))

    # -- analysis ------------------------------------------------------

    def analyse(self) -> bool:
        """Run the import as a dry run and show the report. ``False`` if refused."""

        if not self._rows:
            return False
        try:
            result = import_recipe(
                [row.source() for row in self._rows],
                recipe_id=self.recipe_id(),
                name=self.recipe_name(),
                catalog=self._catalog,
            )
        except (AutoExtError, ValidationError, OSError) as exc:
            # Any earlier report described a different set of files. Keeping it
            # would leave ``result_object`` answering for an import that no
            # longer exists.
            self._result = None
            message = _failure_text(exc)
            self._show_message(message, error=True)
            self.analysis_failed.emit(message)
            return False
        self._result = result
        self._fill_report(result)
        self._stack.setCurrentIndex(1)
        self._refresh_files_page()
        self.analysed.emit(result)
        return True

    def show_files_page(self) -> None:
        self._stack.setCurrentIndex(0)
        self._refresh_files_page()

    def accept(self) -> None:  # noqa: D102 - QDialog protocol
        if self._result is not None:
            self.import_accepted.emit(self._result)
        super().accept()

    # -- the report ----------------------------------------------------

    def _fill_report(self, result: RecipeImportResult) -> None:
        self._report_title.set_full_text(result.recipe.name)
        meta = [", ".join(source.target.value for source in result.sources)]
        forced = sum(1 for source in result.sources if source.forced)
        if forced:
            meta.append(_plural(forced, "target") + " named by hand")
        self._report_meta.set_full_text(_DOT.join(meta))

        self._fill_values(result)
        self._fill_hunks(result)
        self._fill_defaults(result)

        text = "\n".join(result.warnings)
        self._warning.setText(text)
        self._warning.setVisible(bool(text))

        if result.clean_roundtrip:
            self._roundtrip.setText(
                f"{_GLYPH_PASSED} re-rendering this recipe reproduces every "
                "imported file byte for byte"
            )
        else:
            differing = ", ".join(
                target.value
                for target, trip in result.roundtrip.items()
                if not trip.identical
            )
            self._roundtrip.setText(
                f"{_GLYPH_NEUTRAL} re-rendering does not reproduce {differing} "
                "byte for byte"
            )

    def _fill_values(self, result: RecipeImportResult) -> None:
        rows = value_rows(result)
        section = self._values_section
        section.clear()
        section.set_count(len(rows))
        if rows:
            section.add_widget(_ListBlock([(row.as_line(), row.where) for row in rows]))

    def _fill_hunks(self, result: RecipeImportResult) -> None:
        section = self._hunks_section
        section.clear()
        entries = hunk_entries(result)
        section.set_count(
            len(entries),
            emphasis=theme.WARNING_TEXT_ON_WHITE if entries else None,
        )
        section.set_hint(
            f"{result.unmodelled_ratio:.0%} of the imported lines"
            if entries
            else "the catalog explains every line of these files"
        )
        for summary, hunk in entries:
            section.add_widget(_hunk_widget(summary, hunk))

    def _fill_defaults(self, result: RecipeImportResult) -> None:
        section = self._defaults_section
        section.clear()
        rows = default_rows(result)
        section.set_count(len(rows))
        for reason, group in default_groups(rows):
            caption = QLabel(f"{_plural(len(group), 'option')} {_EM_DASH} {reason}")
            caption.setWordWrap(True)
            caption.setStyleSheet(
                f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
            )
            _let_shrink(caption)
            section.add_widget(caption)
            section.add_widget(
                _ListBlock([(row.as_line(), row.where) for row in group])
            )

    # -- sizing --------------------------------------------------------

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        """Never bigger than the budget the window floor leaves a dialog.

        Qt derives a dialog's minimum from its layout, and the report page can
        grow a hundred rows tall. Clamping here is what keeps a 940x560 window
        able to open this at all -- the class of bug the redesign found
        everywhere: dialogs reserving 900x640 while showing four fields. The
        content scrolls; the frame does not grow.
        """

        hint = super().minimumSizeHint()
        return QSize(
            min(hint.width(), MAX_HINT_WIDTH), min(hint.height(), MAX_HINT_HEIGHT)
        )


def _hunk_widget(summary: PatchedHunk, hunk: PatchHunk | None) -> QWidget:
    """One hunk in the report: its anchor line, then the masked diff."""

    block = QFrame()
    column = QVBoxLayout(block)
    column.setContentsMargins(0, 0, 0, theme.SPACE_XS)
    column.setSpacing(theme.SPACE_XXS)

    head = ElidedLabel(summary.describe(), parent=block)
    head.setStyleSheet(
        f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
        f" color: {theme.TEXT_SECONDARY};"
    )
    column.addWidget(head)
    if hunk is not None:
        column.addWidget(DiffBlock(diff_rows(hunk), parent=block))
    return block
