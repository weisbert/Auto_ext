"""Single-file template generator dialog (Phase A + B + D).

A non-modal dialog that turns one raw EDA export (Calibre ``.qci`` /
si.env / Quantus ``.cmd`` / Jivaro ``.xml``) into a parameterized
template body with ``[[name]]`` placeholders.

Phase A scope:
    - drop a single raw file into the :class:`DropZone`,
    - auto-detect the tool (or honour the toolbar dropdown),
    - call :func:`auto_ext.core.importer.import_template` to compute the
      parameterized body,
    - render raw text on the left pane and the parameterized body on
      the right pane,
    - tint the rows that genuinely differ between raw and parameterized
      body (computed via :class:`difflib.SequenceMatcher` opcodes) with
      a soft yellow background on each side, mirroring the diff-tinting
      helpers used by :class:`TemplateDiffViewerDialog`.

Phase B scope:
    - identity override panel: a third pane on the right side of the
      splitter holding 6 :class:`QLineEdit` rows (one per
      :class:`Identity` field), populated from the auto-extracted
      identity on each successful drop,
    - editing any field debounces 300 ms then re-runs
      :func:`import_template` with the user's overrides applied; the
      right pane refreshes in place,
    - inline status label reports ``"自动抽取"`` / ``"用户覆盖"`` /
      ``"导入失败：..."`` rather than popping a :class:`QMessageBox`.

Phase D scope (new):
    - the ``保存`` button writes a ``.j2`` file plus a sibling
      ``<name>.j2.manifest.yaml`` via :class:`QFileDialog`,
    - if the chosen target already exists with a manifest carrying
      knobs, run :func:`merge_reimport` so user-promoted knobs survive
      the re-import,
    - existing files are renamed via :func:`backup_if_exists` before
      writing.

Out of scope (later phases):
    - knob candidate promote (Phase C),
    - ``.review.md`` writing (CLI-only).
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import cast

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QCursor, QFont, QTextCursor
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.errors import ConfigError
from auto_ext.core.importer import (
    TOOL,
    Identity,
    ImportError as CoreImportError,
    import_template,
    merge_reimport,
)
from auto_ext.core.io_utils import backup_if_exists
from auto_ext.core.manifest import (
    TemplateManifest,
    dump_manifest_yaml,
    load_manifest,
    manifest_path_for,
)
from auto_ext.ui.widgets.drop_zone import DropZone

logger = logging.getLogger(__name__)

#: Filter strings used by the save dialog. Keep the literal text in sync
#: with the matching against the selected filter in :meth:`_on_save_clicked`.
_SAVE_FILTER_JINJA = "Jinja templates (*.j2)"
_SAVE_FILTER_ALL = "All files (*)"
_SAVE_FILTER = f"{_SAVE_FILTER_JINJA};;{_SAVE_FILTER_ALL}"

#: Tooltip strings on the save button before / after a successful drop.
_SAVE_TOOLTIP_DISABLED = "Drop a raw template file first"
_SAVE_TOOLTIP_ENABLED = "Save as .j2 + .manifest.yaml"


# Soft yellow used to highlight rows on either pane that line up with a
# parameterized line on the right pane. Matches the palette used by
# :mod:`auto_ext.ui.widgets.template_diff_viewer` so the two viewers
# look consistent side-by-side.
_BG_PARAMETERIZED = QColor("#fff7d0")
_BG_DEFAULT = QColor(Qt.transparent)

#: Items shown in the toolbar tool-selection dropdown. ``"auto"`` means
#: "infer from the dropped file"; the four real tools force a tool.
_TOOL_CHOICES: tuple[str, ...] = ("auto", "calibre", "si", "quantus", "jivaro")

#: Concrete tools (``"auto"`` filtered out) — used for the fallback
#: pick-a-tool menu and toolbar lookups.
_REAL_TOOLS: tuple[TOOL, ...] = ("calibre", "si", "quantus", "jivaro")

#: Pretty labels for the fallback QMenu entries.
_TOOL_LABELS: dict[TOOL, str] = {
    "calibre": "Calibre",
    "si": "SI",
    "quantus": "Quantus",
    "jivaro": "Jivaro",
}


def _mono_font() -> QFont:
    """Monospace font matching the diff viewer's pane font."""
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f


def _detect_tool_from_file(path: Path, content: str) -> TOOL | None:
    """Auto-detect the tool from extension and content fingerprints.

    First match wins; rules are intentionally simple so they can be
    audited at a glance. Returns ``None`` if nothing matched — callers
    fall back to the pick-a-tool menu.
    """
    suffix = path.suffix.lower()
    if suffix in (".qci", ".qcilvs") or "*lvsLayoutPrimary:" in content:
        return "calibre"
    if suffix == ".env" or "simLibName =" in content:
        return "si"
    if suffix == ".cmd" or "-design_cell_name" in content:
        return "quantus"
    if suffix == ".xml" or "<reductionParameters>" in content:
        return "jivaro"
    return None


class TemplateGeneratorDialog(QDialog):
    """Drop a raw EDA export → see the parameterized template body.

    The dialog is non-modal: the parent tab keeps a reference so the
    user can compare against other windows.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Template Generator")
        self.setModal(False)
        self.resize(1200, 720)

        # Guard for synchronized scrolling so the slot doesn't recurse.
        self._syncing = False
        # Latest import outputs — kept on the instance so Phase B/C/D
        # follow-ups can reach into them without re-running the import.
        self._raw_text: str = ""
        self._template_body: str = ""
        # Resolved tool from the most recent successful drop. Phase B
        # re-imports use this when the user edits an identity field.
        self._current_tool: TOOL | None = None
        # Suppress the textChanged debounce while we programmatically
        # populate the identity line edits from a fresh drop.
        self._suppress_override_signal = False

        self._build_ui()

    # ---- UI construction --------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Toolbar row: tool selector + drop zone.
        toolbar = QHBoxLayout()
        self._tool_combo = QComboBox(self)
        for choice in _TOOL_CHOICES:
            self._tool_combo.addItem(choice)
        self._tool_combo.setCurrentText("auto")
        self._tool_combo.setToolTip(
            "Pick the target tool; auto = infer from filename / content"
        )
        toolbar.addWidget(self._tool_combo)

        self._drop_zone = DropZone("Drop a raw template file", self)
        self._drop_zone.path_dropped.connect(self._on_path_dropped)
        toolbar.addWidget(self._drop_zone, 1)

        self._open_btn = QPushButton("Open file...", self)
        self._open_btn.setToolTip("Select a raw template file via dialog")
        self._open_btn.clicked.connect(self._on_open_clicked)
        toolbar.addWidget(self._open_btn)
        root.addLayout(toolbar)

        # Side-by-side panes + identity override panel (Phase B).
        splitter = QSplitter(Qt.Horizontal, self)
        self._left_pane = self._make_pane()
        self._right_pane = self._make_pane()
        self._identity_panel = self._make_identity_panel()
        splitter.addWidget(self._left_pane)
        splitter.addWidget(self._right_pane)
        splitter.addWidget(self._identity_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)
        splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(splitter, 1)

        # Synchronized scrolling.
        self._left_pane.verticalScrollBar().valueChanged.connect(
            self._on_left_scroll
        )
        self._right_pane.verticalScrollBar().valueChanged.connect(
            self._on_right_scroll
        )

        # Debounce: every textChanged restarts the 300 ms countdown; on
        # timeout we re-run import_template with the user's overrides.
        self._override_timer = QTimer(self)
        self._override_timer.setSingleShot(True)
        self._override_timer.setInterval(300)
        self._override_timer.timeout.connect(self._reimport_with_overrides)

        # Bottom: cancel + (disabled) save.
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.clicked.connect(self.close)
        bottom.addWidget(self._cancel_btn)
        self._save_btn = QPushButton("Save", self)
        self._save_btn.setEnabled(False)
        self._save_btn.setToolTip(_SAVE_TOOLTIP_DISABLED)
        self._save_btn.clicked.connect(self._on_save_clicked)
        bottom.addWidget(self._save_btn)
        root.addLayout(bottom)

    def _make_pane(self) -> QPlainTextEdit:
        pane = QPlainTextEdit(self)
        pane.setReadOnly(True)
        pane.setFont(_mono_font())
        pane.setLineWrapMode(QPlainTextEdit.NoWrap)
        return pane

    def _make_identity_panel(self) -> QWidget:
        """Build the right-most pane: 6 :class:`QLineEdit` rows (one per
        :class:`Identity` field) plus a status :class:`QLabel`.

        Editing any field starts (or restarts) the debounce timer; the
        timer's slot then calls :meth:`_reimport_with_overrides`.
        """
        wrap = QWidget(self)
        outer = QVBoxLayout(wrap)
        outer.setContentsMargins(4, 4, 4, 4)

        group = QGroupBox("Identity overrides", wrap)
        form = QFormLayout(group)

        self._cell_edit = QLineEdit(group)
        self._library_edit = QLineEdit(group)
        self._lvs_layout_view_edit = QLineEdit(group)
        self._lvs_source_view_edit = QLineEdit(group)
        self._out_file_edit = QLineEdit(group)
        self._ground_net_edit = QLineEdit(group)

        form.addRow("cell:", self._cell_edit)
        form.addRow("library:", self._library_edit)
        form.addRow("lvs_layout_view:", self._lvs_layout_view_edit)
        form.addRow("lvs_source_view:", self._lvs_source_view_edit)
        form.addRow("out_file:", self._out_file_edit)
        form.addRow("ground_net:", self._ground_net_edit)

        for edit in self._identity_edits():
            edit.setPlaceholderText("(not extracted)")
            edit.textChanged.connect(self._on_identity_edit_changed)

        outer.addWidget(group)

        self._identity_status = QLabel("", wrap)
        self._identity_status.setWordWrap(True)
        outer.addWidget(self._identity_status)
        outer.addStretch(1)

        return wrap

    def _identity_edits(self) -> tuple[QLineEdit, ...]:
        """Return the 6 line edits in :class:`Identity` field order."""
        return (
            self._cell_edit,
            self._library_edit,
            self._lvs_layout_view_edit,
            self._lvs_source_view_edit,
            self._out_file_edit,
            self._ground_net_edit,
        )

    # ---- drop handling ----------------------------------------------------

    def _on_open_clicked(self) -> None:
        """Pop a file-open dialog and route the chosen path through the
        same handler the DropZone uses, so the two entry points share
        every downstream behaviour (auto-detect, identity panel, etc.).
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open raw template file",
            "",
            "raw EDA exports (*.qci *.qcilvs *.env *.cmd *.xml);;all files (*)",
        )
        if not path:
            return
        self._on_path_dropped(Path(path))

    def _on_path_dropped(self, path: object) -> None:
        file_path = Path(str(path))
        text = self._read_text_or_warn(file_path)
        if text is None:
            return

        tool = self._resolve_tool(file_path, text)
        if tool is None:
            # User dismissed the pick-a-tool menu — abort silently.
            return

        try:
            result = import_template(tool, text)
        except CoreImportError as exc:
            QMessageBox.warning(
                self,
                "Import failed",
                f"Could not parse this file as a {tool} template:\n{exc}",
            )
            return

        # Reflect the tool we ended up using on the toolbar so the user
        # sees what was picked.
        self._tool_combo.setCurrentText(tool)

        self._raw_text = text
        self._template_body = result.template_body
        self._current_tool = tool
        self._render_panes()
        self._populate_identity_panel(result.identity)
        # Phase D: a successful drop unlocks the Save button. We only
        # ever transition False -> True here; a subsequent failed
        # re-import keeps the previous valid body and must NOT lock the
        # button again.
        self._save_btn.setEnabled(True)
        self._save_btn.setToolTip(_SAVE_TOOLTIP_ENABLED)

    def _populate_identity_panel(self, identity: Identity) -> None:
        """Push auto-extracted identity values into the line edits.

        Cancels any pending debounce so the synthetic ``textChanged``
        bursts from :meth:`QLineEdit.setText` do not trigger an
        immediate re-import.
        """
        self._override_timer.stop()
        values = (
            identity.cell,
            identity.library,
            identity.lvs_layout_view,
            identity.lvs_source_view,
            identity.out_file,
            identity.ground_net,
        )
        self._suppress_override_signal = True
        try:
            for edit, value in zip(self._identity_edits(), values):
                edit.setText(value or "")
        finally:
            self._suppress_override_signal = False

        any_missing = any(v is None for v in values)
        text = (
            "auto-extracted (some fields empty)"
            if any_missing
            else "auto-extracted"
        )
        self._set_identity_status(text, "auto")

    def _resolve_tool(self, path: Path, content: str) -> TOOL | None:
        """Decide which tool importer to dispatch to.

        Honours the toolbar dropdown if it isn't ``auto``; otherwise
        runs the fingerprint detector and falls back to a popup menu.
        Returns ``None`` if the user dismisses the menu.
        """
        selection = self._tool_combo.currentText()
        if selection != "auto":
            return cast(TOOL, selection)

        detected = _detect_tool_from_file(path, content)
        if detected is not None:
            return detected

        return self._prompt_tool_menu()

    def _prompt_tool_menu(self) -> TOOL | None:
        """Pop a QMenu at the cursor letting the user pick a tool.

        Returns the picked tool, or ``None`` if the user dismissed the
        menu (e.g. clicked outside / pressed Escape).
        """
        menu = QMenu(self)
        actions: dict[object, TOOL] = {}
        for tool in _REAL_TOOLS:
            action = menu.addAction(_TOOL_LABELS[tool])
            actions[action] = tool
        chosen = menu.exec_(QCursor.pos())
        if chosen is None:
            return None
        return actions.get(chosen)

    def _read_text_or_warn(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Read failed", f"Could not read {path}:\n{exc}")
        except UnicodeDecodeError as exc:
            QMessageBox.warning(
                self,
                "Encoding error",
                f"{path} is not UTF-8 text (byte {exc.start}: {exc.reason}).",
            )
        return None

    # ---- rendering --------------------------------------------------------

    def _render_panes(self) -> None:
        """Push the latest raw + parameterized text into both panes and
        tint only the rows that genuinely differ between raw and body.

        True-diff semantic (replaces the earlier "any line containing a
        ``[[...]]`` placeholder" heuristic): the importer is allowed to
        insert lines that have no counterpart on the raw side (e.g.
        Calibre's ``[% if connect_by_name %]...[% endif %]`` toggle
        block, or si.env's auto-injected ``simRunDir = "[[output_dir]]"``
        line). Row indices on the two sides therefore diverge after such
        insertions, and naive same-index tinting tints unchanged raw
        lines while skipping the freshly substituted body lines. We use
        :class:`difflib.SequenceMatcher` to compute the actual diff
        opcodes and highlight only ``replace`` / ``delete`` ranges on
        the left and ``replace`` / ``insert`` ranges on the right.
        """
        self._left_pane.setPlainText(self._raw_text)
        self._right_pane.setPlainText(self._template_body)

        left_lines = self._raw_text.splitlines()
        right_lines = self._template_body.splitlines()
        matcher = difflib.SequenceMatcher(
            a=left_lines, b=right_lines, autojunk=False
        )
        left_rows: set[int] = set()
        right_rows: set[int] = set()
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                continue
            if op in ("replace", "delete"):
                left_rows.update(range(i1, i2))
            if op in ("replace", "insert"):
                right_rows.update(range(j1, j2))

        self._tint_pane(self._left_pane, left_rows)
        self._tint_pane(self._right_pane, right_rows)

    def _tint_pane(self, pane: QPlainTextEdit, rows: set[int]) -> None:
        doc = pane.document()
        cursor = QTextCursor(doc)
        block = doc.firstBlock()
        index = 0
        while block.isValid():
            block_fmt = block.blockFormat()
            if index in rows:
                block_fmt.setBackground(_BG_PARAMETERIZED)
            else:
                block_fmt.setBackground(_BG_DEFAULT)
            cursor.setPosition(block.position())
            cursor.setBlockFormat(block_fmt)
            block = block.next()
            index += 1

    # ---- synchronized scrolling ------------------------------------------

    def _on_left_scroll(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._right_pane.verticalScrollBar().setValue(value)
        finally:
            self._syncing = False

    def _on_right_scroll(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self._left_pane.verticalScrollBar().setValue(value)
        finally:
            self._syncing = False

    # ---- identity overrides (Phase B) ------------------------------------

    def _on_identity_edit_changed(self, _text: str) -> None:
        """Slot for every :class:`QLineEdit` ``textChanged`` signal.

        Restarts the debounce countdown. No-ops when:
            - we are programmatically populating the panel from a fresh
              drop (``self._suppress_override_signal``),
            - no raw file has been dropped yet (re-import would have
              nothing to operate on).
        """
        if self._suppress_override_signal:
            return
        if not self._raw_text or self._current_tool is None:
            return
        self._override_timer.start()

    def _collect_overrides(self) -> Identity | None:
        """Return an :class:`Identity` built from the 6 line edits, or
        ``None`` if every field is empty (no overrides at all)."""
        values = [edit.text().strip() for edit in self._identity_edits()]
        if not any(values):
            return None
        cell, library, layout_view, source_view, out_file, ground_net = values
        return Identity(
            cell=cell or None,
            library=library or None,
            lvs_layout_view=layout_view or None,
            lvs_source_view=source_view or None,
            out_file=out_file or None,
            ground_net=ground_net or None,
        )

    def _reimport_with_overrides(self) -> None:
        """Re-run :func:`import_template` with the user's edited fields.

        Keeps the previous body and shows an inline error in the status
        label on failure rather than popping a :class:`QMessageBox` —
        live editing must not be interrupted by a modal dialog.
        """
        if not self._raw_text or self._current_tool is None:
            return

        overrides = self._collect_overrides()
        try:
            result = import_template(
                self._current_tool,
                self._raw_text,
                identity_overrides=overrides,
            )
        except CoreImportError as exc:
            self._set_identity_status(f"import failed: {exc}", "error")
            return

        self._template_body = result.template_body
        self._render_panes()
        if overrides is None:
            # All edits empty — equivalent to a no-override import; keep
            # the panel in "auto" status so the user sees they're back
            # to the inferred values.
            any_missing = any(
                v is None
                for v in (
                    result.identity.cell,
                    result.identity.library,
                    result.identity.lvs_layout_view,
                    result.identity.lvs_source_view,
                    result.identity.out_file,
                    result.identity.ground_net,
                )
            )
            text = (
                "auto-extracted (some fields empty)"
                if any_missing
                else "auto-extracted"
            )
            self._set_identity_status(text, "auto")
        else:
            self._set_identity_status("user override", "user")

    def _set_identity_status(
        self, text: str, kind: str = "auto"
    ) -> None:
        """Update the status label text + color.

        ``kind`` is one of ``"auto"`` / ``"user"`` / ``"error"`` and
        drives the stylesheet color (matching the spec: gray / blue /
        red respectively).
        """
        colors = {
            "auto": "#888",
            "user": "#2080d0",
            "error": "#c04040",
        }
        color = colors.get(kind, "#888")
        self._identity_status.setText(text)
        self._identity_status.setStyleSheet(f"color: {color};")

    # ---- save (Phase D) ---------------------------------------------------

    def _on_save_clicked(self) -> None:
        """Write the current template body + manifest to a user-picked path.

        Mirrors the CLI's ``import`` command (``cli.py`` lines 600-625)
        minus the ``.review.md`` artifact, which is CLI-only. Smart-merge
        kicks in when the chosen target already has a manifest with at
        least one knob: ``merge_reimport`` re-applies user-promoted
        knobs to the freshly-computed body so they survive the save.
        """
        # Defense in depth: the button shouldn't even be clickable here.
        if not self._template_body or self._current_tool is None:
            return

        path_str, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save template",
            "",
            _SAVE_FILTER,
        )
        if not path_str:
            return

        output = Path(path_str)
        # Auto-append ".j2" only when the user chose the Jinja filter.
        # When they pick "All files", honour their literal input — the
        # user may genuinely want a non-.j2 extension (e.g. for
        # one-off comparisons).
        if (
            selected_filter == _SAVE_FILTER_JINJA
            and output.suffix.lower() != ".j2"
        ):
            output = output.with_name(output.name + ".j2")

        manifest_path = manifest_path_for(output)

        # Re-run the importer at save time so the body matches the
        # current identity overrides exactly. The right pane already
        # reflects this body, but recomputing here also gives us a
        # fresh ``ImportResult`` that ``merge_reimport`` can chew on.
        try:
            result = import_template(
                self._current_tool,
                self._raw_text,
                identity_overrides=self._collect_overrides(),
            )
        except CoreImportError as exc:
            QMessageBox.warning(self, "Save failed", str(exc))
            return

        # Smart-merge if the target already exists with a manifest.
        existing_manifest: TemplateManifest | None = None
        if output.exists() and manifest_path.exists():
            try:
                existing_manifest = load_manifest(output)
            except ConfigError as exc:
                logger.warning(
                    "template_generator: existing manifest at %s is "
                    "unloadable, treating as fresh save: %s",
                    manifest_path,
                    exc,
                )
                existing_manifest = None

        merge_messages: list[str] = []
        auto_knobs = dict(result.auto_knobs)
        if existing_manifest is not None and existing_manifest.knobs:
            outcome = merge_reimport(result, existing_manifest)
            body = outcome.body
            # auto_knobs are the base; existing user-promoted knobs win
            # on any key conflict so manual edits survive a re-save.
            merged_knobs = {**auto_knobs, **outcome.manifest.knobs}
            final_manifest = TemplateManifest(
                template=output.name, knobs=merged_knobs
            )
            merge_messages = outcome.messages
        else:
            body = result.template_body
            final_manifest = TemplateManifest(
                template=output.name, knobs=auto_knobs
            )

        try:
            backup_if_exists(output)
            backup_if_exists(manifest_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(body, encoding="utf-8")
            manifest_path.write_text(
                dump_manifest_yaml(final_manifest), encoding="utf-8"
            )
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return

        message = f"Template: {output}\nManifest: {manifest_path}"
        if merge_messages:
            shown = merge_messages[:6]
            if len(merge_messages) > 6:
                shown = shown + ["..."]
            message += "\n\n" + "\n".join(shown)
        QMessageBox.information(self, "Saved", message)


__all__ = ["TemplateGeneratorDialog"]
