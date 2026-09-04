"""The manual-edit strip: the escape hatch, and the only place templates show.

Artboards ``1f`` (collapsed) and ``1g`` (expanded). Everything the user can
learn about a ``.j2`` from this application, they learn here -- one strip at
the foot of the recipe form saying "this recipe has N manual edits", which
opens into one row per hunk with its intent, its state and a per-hunk Drop.

Its undo controls say **Drop**, never Revert. Revert is the form header's
word and means the whole recipe; see :data:`DROP_ONE`.

Three things this widget is responsible for
-------------------------------------------

**Showing the stored edit as a unified diff.** Storage is masked, anchored,
context-carrying hunks (``core/patch_models.PatchHunk``) because that is what
survives a per-cell re-render and a PDK swap; none of that is readable. The
display comes from :func:`~auto_ext.core.patch.render_hunk_as_udiff`, which is
the module's own display format, and the surrounding grey context lines come
from the hunk's stored anchors. By default the masked form is shown -- so the
row reads ``-topCell "${cell}"`` -- because that *is* the explanation for why
one stored edit works for eight DUTs. Pass ``mask_values`` to see a concrete
render instead.

**Naming the state without relying on colour.** Nine ``PatchStatus`` values
fold into four words the user can act on: ``applied`` / ``conflict`` /
``no-op`` / ``disabled``. The word is the signal; the colour agrees with it
but never carries it alone.

**Making the escape hatch converge.** A hunk whose text the catalog now emits
by itself comes back ``ABSORBED`` or ``NOOP``. That is a success -- the option
was promoted out of the escape hatch and into the catalog -- and the row says
so and offers to delete the hunk. Without that prompt the patch list only ever
grows, and a growing patch list is how the previous ``clone_template`` fork
ended up being the real configuration.

Assumptions
-----------
* The four diff tints are read off artboard ``1g``. ``theme.py`` publishes no
  diff palette, and the status fills are the wrong tool: ``STATUS_FILL`` maps
  "this stage passed", not "this line was added". They are checked against
  :func:`~auto_ext.ui.theme.accent_colors` in the tests so they can never
  drift into the accent, which is the one rule the design fixes absolutely.
* ``PatchHunk`` records no author and no per-hunk timestamp, so the artboard's
  ``edited 08-14 by rfv`` is rendered as the *patch's* capture date in the
  file header. Per-hunk provenance needs a model field that does not exist.
* A hunk header shows ``@@ line N`` only when a
  :class:`~auto_ext.core.patch_models.StagePatchReport` for that file has been
  handed in; the artboard's section name (``@@ line 8 -- capacitance``) needs
  a landing-site lookup that only the renderer can do.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.patch import render_hunk_as_udiff, unmask
from auto_ext.core.patch_models import (
    PatchHunk,
    PatchStatus,
    StagePatchReport,
    TemplatePatch,
)
from auto_ext.ui import theme
from auto_ext.ui.widgets.option_editor import ElidedLabel

__all__ = [
    "DEFAULT_DISPLAY_CONTEXT",
    "DIFF_ADD_FILL",
    "DIFF_ADD_TEXT",
    "DIFF_DEL_FILL",
    "DIFF_DEL_TEXT",
    "DROP_ALL",
    "DROP_ALL_IDLE",
    "DROP_ONE",
    "GLYPH_COLLAPSED",
    "GLYPH_EXPANDED",
    "NOOP_ADVICE",
    "OBJ_HUNK_ROW",
    "OBJ_PATCH_BAR",
    "OBJ_PATCH_FILE_HEADER",
    "OBJ_PATCH_FOOTER",
    "OBJ_PATCH_STRIP",
    "OBJ_STATE_CHIP",
    "STATE_COLOR",
    "STATE_LABEL",
    "DiffBlock",
    "HunkRow",
    "HunkState",
    "PatchStrip",
    "diff_rows",
    "file_summary",
    "generated_name",
    "hunk_state",
    "state_color",
    "summary_line",
    "template_name",
]

#: Diff tints, transcribed from artboard ``1g``. Deliberately not the status
#: scale: "added" is not "passed".
DIFF_ADD_FILL = "#f2f7f2"
DIFF_ADD_TEXT = "#256b25"
DIFF_DEL_FILL = "#fbf2f2"
DIFF_DEL_TEXT = "#8f2626"

#: Disclosure glyphs. Both are in the agreed DejaVu subset.
GLYPH_COLLAPSED = "▶"
GLYPH_EXPANDED = "▾"

#: What the strip's two undo controls are called. **Revert belongs to the
#: recipe and Drop belongs to a manual edit**, and the split is load-bearing
#: rather than a preference.
#:
#: The form header's plain ``Revert`` unstages the WHOLE recipe -- every form
#: edit, and the patches with them. These two undo only manual edits. Both
#: used to be called Revert as well, and the strip's read ``Revert all N``,
#: so of the two controls sitting five pixels apart the one saying "all" was
#: the *narrower* of the two: a user choosing between them by the plain
#: English of the labels chose wrong every time. ``Drop`` is not a new word
#: either -- the per-hunk tooltip has said "Drop this hunk from the recipe"
#: since the strip was written.
DROP_ONE = "Drop"
DROP_ALL = "Drop all {count} manual edits"
DROP_ALL_IDLE = "Drop all manual edits"

#: The sentence that makes the escape hatch shrink again.
NOOP_ADVICE = (
    "the generated file already contains this -- the catalog absorbed it. "
    "Delete the hunk to keep the escape hatch small."
)

OBJ_PATCH_STRIP = "patchStrip"
OBJ_PATCH_BAR = "patchStripBar"
OBJ_PATCH_FILE_HEADER = "patchFileHeader"
OBJ_PATCH_FOOTER = "patchStripFooter"
OBJ_STATE_CHIP = "patchStateChip"
OBJ_HUNK_ROW = "patchHunkRow"

#: Grey context lines kept on each side of a hunk body.
DEFAULT_DISPLAY_CONTEXT = 2

_UDIFF_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")

_DOT = " · "
_EM_DASH = "—"


# ---- pure helpers ----------------------------------------------------------
# No Qt below this line until the widget section; all of it is unit-testable
# without a QApplication.


class HunkState(StrEnum):
    """The four words a user can act on, plus "nobody has checked yet".

    ``PatchStatus`` has nine members because the matcher has nine outcomes.
    A person deciding what to do next has four choices, and collapsing the
    nine onto the four here -- once, in one function -- is what keeps the row
    readable and the vocabulary consistent between the strip, the run report
    and the status bar.
    """

    #: Placed into the generated file. CLEAN or SHIFTED.
    APPLIED = "applied"
    #: Could not be placed, or placed only by similarity. Blocks the stage.
    CONFLICT = "conflict"
    #: Changes nothing any more: the catalog now emits it, or the two sides
    #: are equal. The hunk can be deleted.
    NOOP = "noop"
    #: Recorded but switched off by the user.
    DISABLED = "disabled"
    #: No report has been handed in for this file yet.
    UNKNOWN = "unknown"


#: What the state chip reads. The word carries the meaning; the colour agrees.
STATE_LABEL: dict[HunkState, str] = {
    HunkState.APPLIED: "applied",
    HunkState.CONFLICT: "conflict",
    HunkState.NOOP: "no-op",
    HunkState.DISABLED: "disabled",
    HunkState.UNKNOWN: "not checked",
}

#: Chip colours, all from the status scale in ``theme``. Never the accent.
STATE_COLOR: dict[HunkState, str] = {
    HunkState.APPLIED: theme.STATUS_PASSED,
    HunkState.CONFLICT: theme.STATUS_FAILED,
    HunkState.NOOP: theme.WARNING_TEXT_ON_WHITE,
    HunkState.DISABLED: theme.TEXT_DISABLED,
    HunkState.UNKNOWN: theme.TEXT_SECONDARY,
}

_STATE_BY_STATUS: dict[PatchStatus, HunkState] = {
    PatchStatus.CLEAN: HunkState.APPLIED,
    PatchStatus.SHIFTED: HunkState.APPLIED,
    PatchStatus.REVIEW: HunkState.CONFLICT,
    PatchStatus.AMBIGUOUS: HunkState.CONFLICT,
    PatchStatus.LOST: HunkState.CONFLICT,
    PatchStatus.OVERLAP: HunkState.CONFLICT,
    PatchStatus.ABSORBED: HunkState.NOOP,
    PatchStatus.NOOP: HunkState.NOOP,
    PatchStatus.DISABLED: HunkState.DISABLED,
}


def hunk_state(status: PatchStatus | None, *, enabled: bool = True) -> HunkState:
    """Fold one :class:`PatchStatus` into a state the user can act on.

    ``enabled=False`` wins over everything: a hunk the user switched off is
    disabled whatever the last report said about it, because the report may
    predate the switch.
    """

    if not enabled:
        return HunkState.DISABLED
    if status is None:
        return HunkState.UNKNOWN
    return _STATE_BY_STATUS.get(status, HunkState.UNKNOWN)


def state_color(state: HunkState) -> str:
    return STATE_COLOR.get(state, theme.TEXT_SECONDARY)


def generated_name(template_id: str) -> str:
    """``quantus/ext.cmd.j2`` -> ``quantus/ext.cmd``: the file the user edits."""

    return template_id[:-3] if template_id.endswith(".j2") else template_id


def template_name(template_id: str) -> str:
    """``quantus/ext.cmd.j2`` -> ``ext.cmd.j2``: the file the hunk is captured against."""

    return template_id.rsplit("/", 1)[-1]


def diff_rows(
    hunk: PatchHunk,
    values: Mapping[str, str] | None = None,
    *,
    context: int = DEFAULT_DISPLAY_CONTEXT,
) -> list[tuple[str, str]]:
    """``(tag, text)`` rows for one hunk. Tag is ``context`` / ``add`` / ``remove``.

    The changed lines come from
    :func:`~auto_ext.core.patch.render_hunk_as_udiff` -- the display format
    the patch module already owns -- and the grey lines around them come from
    the hunk's own stored anchors, which is the only place surrounding
    context exists once the generated file is gone.

    ``values`` defaults to ``{}``, which renders the masked form: the row
    shows ``${cell}`` rather than one DUT's name, and that is the visible
    answer to "why does one stored edit work for every cell in the table".
    """

    subs = dict(values or {})
    rows: list[tuple[str, str]] = []

    if context > 0:
        for line in hunk.context_before_lines[-context:]:
            rows.append(("context", unmask(line, subs).rstrip("\n")))

    udiff = render_hunk_as_udiff(_terminated(hunk), subs, context=0)
    body = udiff.split("\n")
    if body and body[-1] == "":
        body.pop()
    # The first two lines are always the ``--- generated`` / ``+++ patched``
    # file headers. Dropped by position, never by prefix: a removed line whose
    # own text starts with ``--`` produces a body line that looks like one.
    for line in body[2:]:
        if _UDIFF_HUNK_HEADER.match(line):
            continue
        marker = line[:1]
        if marker == "+":
            rows.append(("add", line[1:]))
        elif marker == "-":
            rows.append(("remove", line[1:]))
        else:
            rows.append(("context", line[1:] if marker == " " else line))

    if context > 0:
        for line in hunk.context_after_lines[:context]:
            rows.append(("context", unmask(line, subs).rstrip("\n")))
    return rows


def _terminated(hunk: PatchHunk) -> PatchHunk:
    r"""A display copy whose ``before`` / ``after`` end in a newline.

    ``difflib.unified_diff`` writes its output by concatenation, so a final
    line with no ``\n`` runs straight into the next diff marker and the body
    comes back as ``-old+new`` on one line. A hunk captured at the end of a
    file that has no trailing newline is exactly that case. Only the display
    copy is padded; the stored hunk is what the matcher anchors against and is
    never touched.
    """

    fixes: dict[str, str] = {}
    for field in ("before", "after"):
        text = getattr(hunk, field)
        if text and not text.endswith("\n"):
            fixes[field] = text + "\n"
    return hunk.model_copy(update=fixes) if fixes else hunk


def file_summary(patches: Iterable[TemplatePatch]) -> list[tuple[str, int]]:
    """``[("ext.cmd", 2), ("calibre_lvs.qci", 1)]`` -- enabled hunks per file."""

    out: list[tuple[str, int]] = []
    for patch in patches:
        count = patch.enabled_count
        if count:
            out.append((generated_name(patch.template_id).rsplit("/", 1)[-1], count))
    return out


def summary_line(patches: Iterable[TemplatePatch]) -> str:
    """``ext.cmd 2 · calibre_lvs.qci 1`` -- the right-hand end of the collapsed bar."""

    return _DOT.join(f"{name} {count}" for name, count in file_summary(patches))


def _let_shrink(widget: QWidget) -> None:
    """Let a layout squeeze this widget past its text width.

    ``qSmartMinSize`` honours an explicitly set ``minimumWidth`` over the
    widget's own ``minimumSizeHint``, so one pixel here is the difference
    between a strip that can fold into a 940px window and one whose three
    buttons quietly set the floor for the whole application. The preferred
    size is untouched: at any usable width the widget still asks for, and
    gets, its full text.
    """

    widget.setMinimumWidth(1)


# ---- widgets ---------------------------------------------------------------


class DiffBlock(QLabel):
    """The monospaced body of one hunk: ``-`` red, ``+`` green, context grey.

    One widget per hunk rather than one per line. A patch of a dozen lines
    would otherwise be a dozen labels to lay out and repaint, and the strip is
    the one part of the recipe screen that grows without bound.
    """

    def __init__(
        self, rows: Sequence[tuple[str, str]] | None = None, parent: QWidget | None = None
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
        """The diff as text, with the ``-`` / ``+`` / space markers restored."""

        marker = {"add": "+", "remove": "-", "context": " "}
        return "\n".join(f"{marker.get(tag, ' ')}{text}" for tag, text in self._rows)

    def _build_html(self) -> str:
        base = (
            f"font-family:{theme.FONT_MONO};font-size:{theme.FONT_SIZE_MONO}px;"
            f"line-height:140%;"
        )
        parts = [f'<div style="{base}">']
        for tag, text in self._rows:
            if tag == "add":
                style = f"background:{DIFF_ADD_FILL};color:{DIFF_ADD_TEXT};"
                marker = "+"
            elif tag == "remove":
                style = f"background:{DIFF_DEL_FILL};color:{DIFF_DEL_TEXT};"
                marker = "-"
            else:
                style = f"color:{theme.TEXT_SECONDARY};"
                marker = "&nbsp;"
            body = html.escape(text).replace(" ", "&nbsp;").replace("\t", "&nbsp;" * 4)
            parts.append(f'<div style="{style}">{marker}&nbsp;{body}</div>')
        parts.append("</div>")
        return "".join(parts)


class HunkRow(QFrame):
    """One manual edit: header line, unified diff, and a state-specific note.

    The Revert button is per hunk on purpose. A recipe accumulates unrelated
    edits to unrelated files, and "revert everything" is not a usable answer
    when one of the three is wrong and the other two are the reason the run
    passes.
    """

    revert_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        hunk: PatchHunk,
        *,
        state: HunkState = HunkState.UNKNOWN,
        template_id: str = "",
        start_line: int | None = None,
        message: str = "",
        mask_values: Mapping[str, str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._hunk = hunk
        self._state = state
        self._template_id = template_id
        self.setObjectName(OBJ_HUNK_ROW)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"QFrame#{OBJ_HUNK_ROW} {{ background: {theme.SURFACE_CARD};"
            f" border: none; border-bottom: 1px solid {theme.LINE_ROW}; }}"
        )

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        header = QFrame(self)
        header.setStyleSheet(f"background: {theme.SURFACE_PAGE}; border: none;")
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(
            theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS
        )
        header_row.setSpacing(theme.SPACE_MD)

        anchor = f"@@ line {start_line}" if start_line is not None else f"@@ hunk {hunk.id}"
        self._anchor_label = QLabel(anchor, header)
        self._anchor_label.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        _let_shrink(self._anchor_label)
        header_row.addWidget(self._anchor_label, 0)

        intent = hunk.intent or "(no intent recorded)"
        self._intent_label = ElidedLabel(intent, parent=header)
        self._intent_label.setToolTip(intent)
        if not hunk.intent:
            self._intent_label.setStyleSheet(f"color: {theme.TEXT_DISABLED};")
        header_row.addWidget(self._intent_label, 1)

        self._chip = QLabel(STATE_LABEL[state], header)
        self._chip.setObjectName(OBJ_STATE_CHIP)
        self._chip.setToolTip(message or STATE_LABEL[state])
        self._chip.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" font-weight: {theme.FONT_WEIGHT_SEMIBOLD}; color: {state_color(state)};"
        )
        _let_shrink(self._chip)
        header_row.addWidget(self._chip, 0)

        self._revert_button = QPushButton(DROP_ONE, header)
        self._revert_button.setToolTip(
            "Drop this hunk from the recipe. The other manual edits stay, and "
            "so does everything you have changed on the form."
        )
        self._revert_button.clicked.connect(lambda: self.revert_requested.emit(hunk.id))
        _let_shrink(self._revert_button)
        header_row.addWidget(self._revert_button, 0)

        self._delete_button: QPushButton | None = None
        if state is HunkState.NOOP:
            delete = QPushButton("Delete", header)
            delete.setToolTip(NOOP_ADVICE)
            delete.clicked.connect(lambda: self.delete_requested.emit(hunk.id))
            _let_shrink(delete)
            header_row.addWidget(delete, 0)
            self._delete_button = delete

        column.addWidget(header)

        self._diff = DiffBlock(diff_rows(hunk, mask_values), self)
        diff_holder = QWidget(self)
        diff_layout = QHBoxLayout(diff_holder)
        diff_layout.setContentsMargins(theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XS)
        diff_layout.setSpacing(0)
        diff_layout.addWidget(self._diff, 1)
        column.addWidget(diff_holder)

        note = self._note_text(state, message)
        self._note_label: ElidedLabel | None = None
        if note:
            label = ElidedLabel(note, parent=self)
            label.setToolTip(note)
            label.setStyleSheet(
                f"color: {state_color(state)}; font-size: {theme.FONT_SIZE_META}px;"
                f" padding: 0px {theme.SPACE_SM}px {theme.SPACE_XS}px {theme.SPACE_SM}px;"
            )
            column.addWidget(label)
            self._note_label = label

    @staticmethod
    def _note_text(state: HunkState, message: str) -> str:
        if state is HunkState.NOOP:
            return NOOP_ADVICE
        if state is HunkState.CONFLICT:
            return message or (
                "this edit can no longer be placed; the stage will refuse to start "
                "rather than run with the wrong parasitics."
            )
        if state is HunkState.DISABLED:
            return "switched off -- recorded, but not applied to the generated file."
        return message

    # -- accessors -----------------------------------------------------

    @property
    def hunk(self) -> PatchHunk:
        return self._hunk

    @property
    def state(self) -> HunkState:
        return self._state

    @property
    def template_id(self) -> str:
        """The file this hunk belongs to. Half of a hunk's identity here."""

        return self._template_id

    def diff_block(self) -> DiffBlock:
        return self._diff

    def anchor_text(self) -> str:
        return self._anchor_label.text()

    def intent_text(self) -> str:
        """Why the user made this edit, or a placeholder when they said nothing."""

        return self._intent_label.full_text()

    def chip_text(self) -> str:
        return self._chip.text()

    def note_text(self) -> str:
        return self._note_label.full_text() if self._note_label is not None else ""

    def revert_button(self) -> QPushButton:
        return self._revert_button

    def delete_button(self) -> QPushButton | None:
        """Present only on a ``no-op`` row -- the one state worth deleting."""

        return self._delete_button


class PatchStrip(QFrame):
    """Collapsed: one amber line. Expanded: the escape hatch, hunk by hunk.

    The strip owns no data. It is handed the recipe's patches (and, when a
    run has been planned, the per-file reports that say how each hunk landed)
    and it emits a request whenever the user wants one changed; deciding what
    "revert" does to a stored recipe is the screen's job, not the widget's.
    """

    #: New expanded state.
    toggled = pyqtSignal(bool)
    #: ``(stage, template_id, hunk_id)`` -- drop one hunk from the recipe.
    hunk_revert_requested = pyqtSignal(str, str, str)
    #: ``(stage, template_id, hunk_id)`` -- delete an absorbed / no-op hunk.
    hunk_delete_requested = pyqtSignal(str, str, str)
    #: Drop every hunk in every file.
    revert_all_requested = pyqtSignal()
    #: Open the generated file in an editor to capture a new edit.
    edit_rendered_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(OBJ_PATCH_STRIP)
        self.setFrameShape(QFrame.NoFrame)

        self._patches: list[TemplatePatch] = []
        self._reports: dict[tuple[str, str], StagePatchReport] = {}
        self._mask_values: dict[str, str] = {}
        self._expanded = False
        self._rows: list[HunkRow] = []

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        self._bar = self._build_bar()
        column.addWidget(self._bar)

        self._body = QScrollArea(self)
        self._body.setWidgetResizable(True)
        self._body.setFrameShape(QFrame.NoFrame)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._body_host = QWidget()
        self._body_layout = QVBoxLayout(self._body_host)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(0)
        self._body_layout.addStretch(1)
        self._body.setWidget(self._body_host)
        self._body.setVisible(False)
        column.addWidget(self._body, 1)

        self._footer = QLabel(self)
        self._footer.setObjectName(OBJ_PATCH_FOOTER)
        self._footer.setWordWrap(True)
        self._footer.setText(
            "A hunk that no longer applies after a template or catalog change is "
            "flagged conflict here and the stage refuses to start -- it is never "
            "silently dropped."
        )
        self._footer.setStyleSheet(
            f"QLabel#{OBJ_PATCH_FOOTER} {{ background: {theme.SURFACE_PAGE};"
            f" border-top: 1px solid {theme.LINE_PANEL};"
            f" color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
            f" padding: {theme.SPACE_XS}px {theme.SPACE_SM}px; }}"
        )
        self._footer.setVisible(False)
        column.addWidget(self._footer)

        self._refresh()

    # -- construction --------------------------------------------------

    def _build_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName(OBJ_PATCH_BAR)
        bar.setFrameShape(QFrame.NoFrame)
        row = QHBoxLayout(bar)
        row.setContentsMargins(theme.SPACE_MD, theme.SPACE_XS, theme.SPACE_MD, theme.SPACE_XS)
        row.setSpacing(theme.SPACE_MD)

        self._glyph = QLabel(GLYPH_COLLAPSED, bar)
        self._glyph.setStyleSheet(
            f"font-family: {theme.FONT_MONO};"
            f" font-weight: {theme.FONT_WEIGHT_BOLD};"
            f" color: {theme.WARNING_TEXT_ON_WHITE};"
        )
        row.addWidget(self._glyph, 0)

        self._title = QLabel("", bar)
        self._title.setStyleSheet(f"font-weight: {theme.FONT_WEIGHT_SEMIBOLD};")
        _let_shrink(self._title)
        row.addWidget(self._title, 0)

        self._subtitle = ElidedLabel("", parent=bar)
        self._subtitle.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: {theme.FONT_SIZE_META}px;"
        )
        row.addWidget(self._subtitle, 1)

        self._files = QLabel("", bar)
        self._files.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        _let_shrink(self._files)
        row.addWidget(self._files, 0)

        self._edit_button = QPushButton("Edit rendered file", bar)
        self._edit_button.setToolTip(
            "Render this recipe for one cell, open the result in an editor, and "
            "capture whatever you change as a new hunk."
        )
        self._edit_button.clicked.connect(self.edit_rendered_requested)
        _let_shrink(self._edit_button)
        row.addWidget(self._edit_button, 0)

        self._revert_all_button = QPushButton(DROP_ALL_IDLE, bar)
        self._revert_all_button.setToolTip(
            "Drop every manual edit on this recipe. The form's own values are "
            "untouched -- the header's Revert is what undoes those."
        )
        self._revert_all_button.clicked.connect(self.revert_all_requested)
        _let_shrink(self._revert_all_button)
        row.addWidget(self._revert_all_button, 0)

        self._toggle_button = QPushButton("Show diff", bar)
        self._toggle_button.clicked.connect(self.toggle)
        _let_shrink(self._toggle_button)
        row.addWidget(self._toggle_button, 0)
        return bar

    # -- data ----------------------------------------------------------

    def set_patches(
        self,
        patches: Sequence[TemplatePatch] | None,
        *,
        reports: Sequence[StagePatchReport] | None = None,
        mask_values: Mapping[str, str] | None = None,
    ) -> None:
        """Show one recipe's manual edits, optionally with their last outcome.

        ``reports`` is what a planned run learned about these hunks. Without
        it every row reads ``not checked``, which is honest: until the file
        has actually been rendered, nothing knows whether the anchor is still
        there.
        """

        self._patches = list(patches or [])
        self._reports = {
            (report.stage.value, report.template_id): report for report in (reports or [])
        }
        self._mask_values = dict(mask_values or {})
        self._rebuild_body()
        self._refresh()
        if self._expanded and not self.hunk_count():
            # The last hunk just went away under an open strip. Fold it, and
            # say so, so the host can put the form back where the diff was.
            self.set_expanded(False)

    def patches(self) -> list[TemplatePatch]:
        return list(self._patches)

    def hunk_count(self) -> int:
        """Enabled hunks across every file -- the number on the bar."""

        return sum(patch.enabled_count for patch in self._patches)

    def total_hunk_count(self) -> int:
        """Every recorded hunk, including the switched-off ones."""

        return sum(len(patch.hunks) for patch in self._patches)

    def states(self) -> dict[tuple[str, str], HunkState]:
        """``(template_id, hunk id) -> state`` for every hunk displayed.

        Keyed by the pair rather than the id alone: a hunk id is unique inside
        one :class:`TemplatePatch`, not across a recipe, so two files can
        legitimately both hold a hunk called ``00000000``.
        """

        return {(row.template_id, row.hunk.id): row.state for row in self._rows}

    def state_of(self, hunk_id: str, *, template_id: str | None = None) -> HunkState:
        """State of one hunk. Pass ``template_id`` when two files share an id."""

        for row in self._rows:
            if row.hunk.id != hunk_id:
                continue
            if template_id is None or row.template_id == template_id:
                return row.state
        return HunkState.UNKNOWN

    def hunk_ids_in_state(self, state: HunkState) -> list[str]:
        return [row.hunk.id for row in self._rows if row.state is state]

    def hunk_rows(self) -> list[HunkRow]:
        return list(self._rows)

    # -- expansion -----------------------------------------------------

    def set_expanded(self, expanded: bool) -> None:
        """Open or close the strip. Opening an empty strip is a no-op.

        Not merely disabled on the button: the host also drives this, and a
        strip that reports itself open while showing nothing would have the
        recipe form hidden behind an empty panel.
        """

        expanded = bool(expanded) and self.hunk_count() > 0
        if expanded == self._expanded:
            return
        self._expanded = expanded
        self._refresh()
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    # -- text ----------------------------------------------------------

    def summary_text(self) -> str:
        count = self.hunk_count()
        if not count:
            return "No manual edits"
        if self._expanded:
            return f"{count} manual edit" + ("" if count == 1 else "s")
        return f"This recipe has {count} manual edit" + ("" if count == 1 else "s")

    def subtitle_text(self) -> str:
        if not self.hunk_count():
            return (
                "edits you make to a generated file are captured here as a diff, "
                "never as a copy of the template"
            )
        if self._expanded:
            return "applied after render, before the tool runs"
        return (
            "stored as a diff against the rendered files "
            f"{_EM_DASH} re-applied on every run, survives template changes"
        )

    def file_summary_text(self) -> str:
        return summary_line(self._patches)

    # -- accessors for the host and the tests --------------------------

    def bar(self) -> QFrame:
        return self._bar

    def toggle_button(self) -> QPushButton:
        return self._toggle_button

    def revert_all_button(self) -> QPushButton:
        return self._revert_all_button

    def edit_button(self) -> QPushButton:
        return self._edit_button

    def footer(self) -> QLabel:
        return self._footer

    def glyph(self) -> str:
        return self._glyph.text()

    # -- internals -----------------------------------------------------

    def _rebuild_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []

        for patch in self._patches:
            if not patch.hunks:
                continue
            self._body_layout.addWidget(self._build_file_header(patch))
            report = self._reports.get((patch.stage.value, patch.template_id))
            outcomes = {o.hunk_id: o for o in report.outcomes} if report is not None else {}
            for hunk in patch.hunks:
                outcome = outcomes.get(hunk.id)
                row = HunkRow(
                    hunk,
                    state=hunk_state(
                        outcome.status if outcome is not None else None,
                        enabled=hunk.enabled,
                    ),
                    template_id=patch.template_id,
                    start_line=outcome.start_line if outcome is not None else None,
                    message=self._message_for(outcome),
                    mask_values=self._mask_values,
                    parent=self._body_host,
                )
                row.revert_requested.connect(
                    lambda hunk_id, p=patch: self.hunk_revert_requested.emit(
                        p.stage.value, p.template_id, hunk_id
                    )
                )
                row.delete_requested.connect(
                    lambda hunk_id, p=patch: self.hunk_delete_requested.emit(
                        p.stage.value, p.template_id, hunk_id
                    )
                )
                self._body_layout.addWidget(row)
                self._rows.append(row)
        self._body_layout.addStretch(1)

    @staticmethod
    def _message_for(outcome) -> str:
        if outcome is None:
            return ""
        parts = [part for part in (outcome.message, outcome.fuzz) if part]
        return " ".join(parts)

    def _build_file_header(self, patch: TemplatePatch) -> QFrame:
        header = QFrame(self._body_host)
        header.setObjectName(OBJ_PATCH_FILE_HEADER)
        header.setFrameShape(QFrame.NoFrame)
        header.setStyleSheet(
            f"QFrame#{OBJ_PATCH_FILE_HEADER} {{ background: {theme.SURFACE_TOOLBAR};"
            f" border: none; border-top: 1px solid {theme.LINE_PANEL};"
            f" border-bottom: 1px solid {theme.LINE_PANEL}; }}"
        )
        row = QHBoxLayout(header)
        row.setContentsMargins(theme.SPACE_SM, theme.SPACE_XXS, theme.SPACE_SM, theme.SPACE_XXS)
        row.setSpacing(theme.SPACE_SM)

        name = QLabel(generated_name(patch.template_id), header)
        name.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" font-weight: {theme.FONT_WEIGHT_BOLD};"
        )
        _let_shrink(name)
        row.addWidget(name, 0)

        captured = patch.base.captured_at.strftime("%m-%d")
        detail = ElidedLabel(
            f"rendered from {template_name(patch.template_id)}"
            f"{_DOT}captured {captured}"
            + (f"{_DOT}shown masked" if not self._mask_values else ""),
            parent=header,
        )
        detail.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        detail.setToolTip(
            "The stored hunk is masked: per-cell and per-PDK values live in "
            "${slots}, which is why one edit survives every DUT in the table."
            if not self._mask_values
            else "Shown with the current render's values substituted."
        )
        row.addWidget(detail, 1)

        count = len(patch.hunks)
        hunks = QLabel(f"{count} hunk" + ("" if count == 1 else "s"), header)
        hunks.setStyleSheet(
            f"font-family: {theme.FONT_MONO}; font-size: {theme.FONT_SIZE_META}px;"
            f" color: {theme.TEXT_SECONDARY};"
        )
        _let_shrink(hunks)
        row.addWidget(hunks, 0)
        return header

    def _refresh(self) -> None:
        count = self.hunk_count()
        has_edits = count > 0

        self._glyph.setText(GLYPH_EXPANDED if self._expanded else GLYPH_COLLAPSED)
        self._glyph.setVisible(has_edits)
        self._title.setText(self.summary_text())
        self._subtitle.set_full_text(self.subtitle_text())
        self._files.setText(self.file_summary_text())
        self._files.setVisible(has_edits and not self._expanded)

        self._toggle_button.setEnabled(has_edits)
        self._toggle_button.setText("Hide diff" if self._expanded else "Show diff")
        self._revert_all_button.setVisible(self._expanded and has_edits)
        self._revert_all_button.setText(
            DROP_ALL.format(count=count) if count else DROP_ALL_IDLE
        )
        self._edit_button.setVisible(self._expanded or not has_edits)

        self._body.setVisible(self._expanded and has_edits)
        self._footer.setVisible(self._expanded and has_edits)

        if has_edits:
            self._bar.setStyleSheet(
                f"QFrame#{OBJ_PATCH_BAR} {{ background: {theme.STATUS_FILL['warning']};"
                f" border: 1px solid {theme.STATUS_WARNING}; }}"
            )
        else:
            self._bar.setStyleSheet(
                f"QFrame#{OBJ_PATCH_BAR} {{ background: {theme.SURFACE_TOOLBAR};"
                f" border: 1px solid {theme.LINE_PANEL}; }}"
            )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Clicking the bar itself toggles, the way the artboard's chevron implies."""

        if event.button() == Qt.LeftButton and self.hunk_count():
            bar_area = self._bar.geometry()
            if bar_area.contains(event.pos()):
                self.toggle()
                event.accept()
                return
        super().mouseReleaseEvent(event)
