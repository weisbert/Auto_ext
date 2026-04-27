"""Modal diff-mode toggle editor (Phase 5.6).

Two raw EDA exports go in (drag-drop or browse), one Jinja-wrapped
``.j2`` template comes out. Owner is the Templates tab; the dialog
itself has no controller dependency — it operates on file paths
directly so smoke tests can construct it standalone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtGui import (
    QFont,
    QRegExpValidator,
)
from PyQt5.QtCore import QRegExp
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.diff_template import (
    LargeDiffWarning,
    OverlapError,
    ToggleResult,
    apply_toggle_to_template,
    compute_toggle,
    detect_existing_toggle_blocks,
)
from auto_ext.core.errors import ConfigError
from auto_ext.core.manifest import (
    KnobSpec,
    append_knob_to_manifest_yaml,
    load_manifest,
    manifest_path_for,
)
from auto_ext.core.preset import save_preset
from auto_ext.ui.widgets.drop_zone import DropZone
from auto_ext.ui.widgets.jinja_highlighter import JinjaHighlighter


_TOGGLE_NAME_REGEX = QRegExp(r"[a-z][a-z0-9_]*")


def _mono_font() -> QFont:
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f


# ---- main dialog ------------------------------------------------------------


@dataclass
class _SaveOutcome:
    target_path: Path
    bak_path: Path | None
    manifest_path: Path | None
    manifest_error: str | None


class DiffEditorDialog(QDialog):
    """Modal dialog: import two raws, name a toggle, save the wrapped .j2."""

    def __init__(
        self,
        current_template_path: Path,
        bound_tool: str | None,
        auto_ext_root: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            f"Diff toggle editor - {current_template_path.name}"
        )
        self.setModal(True)
        self.resize(1100, 720)

        self._current_template_path = current_template_path
        self._bound_tool = bound_tool
        self._auto_ext_root = auto_ext_root

        try:
            self._current_template_text = current_template_path.read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeDecodeError):
            self._current_template_text = ""

        self._on_path: Path | None = None
        self._off_path: Path | None = None
        self._on_text: str = ""
        self._off_text: str = ""
        self._on_value: bool = True  # which side is "true" by default

        self._toggle: ToggleResult | None = None
        self._merged_for_target: str | None = None
        self._last_outcome: _SaveOutcome | None = None

        self._build_ui()
        self._refresh_state()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # Toggle name + description row.
        meta_row = QHBoxLayout()
        meta_row.addWidget(QLabel("Toggle name:", self))
        self._toggle_name_edit = QLineEdit(self)
        self._toggle_name_edit.setPlaceholderText("e.g. connect_by_net_name")
        self._toggle_name_edit.setValidator(
            QRegExpValidator(_TOGGLE_NAME_REGEX, self)
        )
        self._toggle_name_edit.editingFinished.connect(self._recompute)
        self._toggle_name_edit.textChanged.connect(self._on_name_changed)
        meta_row.addWidget(self._toggle_name_edit, 1)

        meta_row.addWidget(QLabel("type: bool  default:", self))
        self._default_label = QLabel("ON", self)
        self._default_label.setStyleSheet("font-weight: bold; color: #2080d0;")
        meta_row.addWidget(self._default_label)
        root.addLayout(meta_row)

        desc_row = QHBoxLayout()
        desc_row.addWidget(QLabel("Description:", self))
        self._description_edit = QLineEdit(self)
        self._description_edit.setPlaceholderText("(optional, written into the manifest)")
        desc_row.addWidget(self._description_edit, 1)
        root.addLayout(desc_row)

        # Drop zones.
        zones = QHBoxLayout()
        self._on_zone = DropZone("On (true) — drop file here", self)
        self._on_zone.path_dropped.connect(self._on_zone_dropped)
        self._on_path_label = QLabel("(no file)", self)
        self._on_path_label.setStyleSheet("color: #888; font-family: monospace;")
        on_browse = QPushButton("…", self)
        on_browse.setMaximumWidth(28)
        on_browse.clicked.connect(self._on_browse_on)

        on_col = QVBoxLayout()
        on_col.addWidget(self._on_zone)
        on_row_btn = QHBoxLayout()
        on_row_btn.addWidget(self._on_path_label, 1)
        on_row_btn.addWidget(on_browse)
        on_col.addLayout(on_row_btn)

        self._swap_btn = QPushButton("⇄", self)
        self._swap_btn.setToolTip("Swap the on/off raws and flip the default")
        self._swap_btn.setMaximumWidth(40)
        self._swap_btn.clicked.connect(self._on_swap)

        self._off_zone = DropZone("Off (false) — drop file here", self)
        self._off_zone.path_dropped.connect(self._off_zone_dropped)
        self._off_path_label = QLabel("(no file)", self)
        self._off_path_label.setStyleSheet("color: #888; font-family: monospace;")
        off_browse = QPushButton("…", self)
        off_browse.setMaximumWidth(28)
        off_browse.clicked.connect(self._on_browse_off)

        off_col = QVBoxLayout()
        off_col.addWidget(self._off_zone)
        off_row_btn = QHBoxLayout()
        off_row_btn.addWidget(self._off_path_label, 1)
        off_row_btn.addWidget(off_browse)
        off_col.addLayout(off_row_btn)

        zones.addLayout(on_col, 1)
        zones.addWidget(self._swap_btn)
        zones.addLayout(off_col, 1)
        root.addLayout(zones)

        # Side-by-side preview panes.
        splitter = QSplitter(Qt.Horizontal, self)
        self._left_preview = QPlainTextEdit(self)
        self._left_preview.setReadOnly(True)
        self._left_preview.setFont(_mono_font())
        self._left_preview.setPlainText(self._current_template_text)
        self._left_highlighter = JinjaHighlighter(self._left_preview.document())

        self._right_preview = QPlainTextEdit(self)
        self._right_preview.setReadOnly(True)
        self._right_preview.setFont(_mono_font())
        self._right_preview.setPlaceholderText(
            "Preview of the template after applying the toggle. To populate:\n"
            "  1. Drop the on-side raw file in the [On (true)] zone above\n"
            "  2. Drop the off-side raw file in the [Off (false)] zone above\n"
            "  3. Type a toggle name in the [Toggle name] field\n"
            "     (must match [a-z][a-z0-9_]*, e.g. connect_by_net_name)\n\n"
            "The preview appears automatically once the inputs are valid. "
            "Any errors (anchor not found, encoding error, etc.) show up "
            "in the status line below."
        )
        self._right_highlighter = JinjaHighlighter(self._right_preview.document())

        splitter.addWidget(_with_caption("Current .j2", self._left_preview, self))
        splitter.addWidget(_with_caption("After applying toggle", self._right_preview, self))
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # Status banner.
        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #555; font-size: 12px;")
        root.addWidget(self._status_label)

        # Allow-existing-toggles checkbox (decision-2-vs-4 relaxed default).
        self._allow_existing_toggles_check = QCheckBox(
            "Allow inserts outside existing [% if %] blocks (recommended)", self
        )
        self._allow_existing_toggles_check.setChecked(True)
        self._allow_existing_toggles_check.toggled.connect(self._recompute)
        root.addWidget(self._allow_existing_toggles_check)

        # Manifest preview label.
        self._manifest_preview_label = QLabel("", self)
        self._manifest_preview_label.setStyleSheet(
            "font-family: monospace; color: #666; font-size: 11px;"
        )
        self._manifest_preview_label.setWordWrap(True)
        root.addWidget(self._manifest_preview_label)

        # Buttons.
        btn_row = QHBoxLayout()
        self._save_overwrite_btn = QPushButton("Overwrite template", self)
        self._save_overwrite_btn.clicked.connect(self._on_save_overwrite)
        self._save_as_btn = QPushButton("Save as...", self)
        self._save_as_btn.clicked.connect(self._on_save_as)
        self._save_preset_btn = QPushButton("+ Save as preset", self)
        self._save_preset_btn.clicked.connect(self._on_save_preset)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._save_overwrite_btn)
        btn_row.addWidget(self._save_as_btn)
        btn_row.addWidget(self._save_preset_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._cancel_btn)
        root.addLayout(btn_row)

    # ---- slots ------------------------------------------------------------

    def _on_name_changed(self, _text: str) -> None:
        # Light recompute trigger so the preview updates on every keystroke
        # once both raws are loaded.
        if self._on_text and self._off_text:
            self._recompute()

    def _on_zone_dropped(self, path: object) -> None:
        self._set_on_path(Path(str(path)))

    def _off_zone_dropped(self, path: object) -> None:
        self._set_off_path(Path(str(path)))

    def _on_browse_on(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select on-side raw", "",
        )
        if path:
            self._set_on_path(Path(path))

    def _on_browse_off(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select off-side raw", "",
        )
        if path:
            self._set_off_path(Path(path))

    def _on_swap(self) -> None:
        self._on_path, self._off_path = self._off_path, self._on_path
        self._on_text, self._off_text = self._off_text, self._on_text
        self._on_value = not self._on_value
        self._on_path_label.setText(
            str(self._on_path) if self._on_path else "(no file)"
        )
        self._off_path_label.setText(
            str(self._off_path) if self._off_path else "(no file)"
        )
        self._refresh_state()

    # ---- helpers ----------------------------------------------------------

    def _set_on_path(self, path: Path) -> None:
        self._on_path = path
        self._on_text = self._read_raw_or_warn(path)
        self._on_path_label.setText(str(path))
        self._refresh_state()

    def _set_off_path(self, path: Path) -> None:
        self._off_path = path
        self._off_text = self._read_raw_or_warn(path)
        self._off_path_label.setText(str(path))
        self._refresh_state()

    def _read_raw_or_warn(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Read failed", f"Could not read {path}:\n{exc}")
        except UnicodeDecodeError as exc:
            QMessageBox.warning(
                self, "Encoding error",
                f"{path} is not UTF-8 text (byte {exc.start}: {exc.reason}).\n"
                f"Template files should be plain text; make sure the "
                f"dropped file is a template, not a binary / output file.",
            )
        return ""

    def _refresh_state(self) -> None:
        self._default_label.setText("ON" if self._on_value else "OFF")
        self._toggle = None
        self._merged_for_target = None
        self._right_preview.setPlainText("")
        self._right_highlighter.set_hunk_ranges([], [])
        self._refresh_manifest_preview(None)
        self._update_button_state(error=None)

        if not self._on_text or not self._off_text:
            self._status_label.setText("⓵ Drop two raw files")
            self._status_label.setStyleSheet("color: #888;")
            return
        if not self._toggle_name_edit.text().strip():
            self._status_label.setText("⓶ Enter a toggle name ([a-z][a-z0-9_]*)")
            self._status_label.setStyleSheet("color: #888;")
            return
        self._recompute()

    def _recompute(self) -> None:
        if not self._on_text or not self._off_text:
            return
        name = self._toggle_name_edit.text().strip()
        if not name:
            return
        try:
            toggle = compute_toggle(
                self._on_text, self._off_text, name, on_value=self._on_value
            )
        except ValueError as exc:
            self._toggle = None
            self._merged_for_target = None
            self._status_label.setText(f"✗ {exc}")
            self._status_label.setStyleSheet("color: #c83232; font-weight: bold;")
            self._right_preview.setPlainText("")
            self._right_highlighter.set_hunk_ranges([], [])
            self._update_button_state(error=str(exc))
            return

        self._toggle = toggle
        # Apply the toggle into the current template.
        try:
            merged = apply_toggle_to_template(
                self._current_template_text,
                toggle,
                allow_existing_toggles=self._allow_existing_toggles_check.isChecked(),
            )
        except (OverlapError, ValueError) as exc:
            self._merged_for_target = None
            self._status_label.setText(f"✗ {exc}")
            self._status_label.setStyleSheet("color: #c83232; font-weight: bold;")
            self._right_preview.setPlainText("")
            self._right_highlighter.set_hunk_ranges([], [])
            self._update_button_state(error=str(exc))
            return

        self._merged_for_target = merged
        self._right_preview.setPlainText(merged)
        self._tint_right_preview(merged, toggle)

        existing = detect_existing_toggle_blocks(self._current_template_text)
        parts = [f"✓ Detected {len(toggle.hunks)} hunk(s)"]
        if existing:
            parts.append(f"Existing [% if %] blocks: {len(existing)}")
        for w in toggle.warnings:
            if isinstance(w, LargeDiffWarning):
                parts.append(
                    f"⚠ Large diff ({w.change_ratio:.0%}). {w.message}"
                )
            else:
                parts.append(f"⚠ {w.message}")
        has_warning = any(isinstance(w, LargeDiffWarning) for w in toggle.warnings)
        self._status_label.setText("  |  ".join(parts))
        self._status_label.setStyleSheet(
            "color: #b06000; font-weight: bold;" if has_warning
            else "color: #208020;"
        )
        self._refresh_manifest_preview(toggle)
        self._update_button_state(error=None)

    def _tint_right_preview(self, merged: str, toggle: ToggleResult) -> None:
        """Best-effort line-range tinting for the after-toggle preview.

        Walks ``merged`` for ``[% if %]`` blocks and tags lines inside
        the if-branch as on-side, lines inside the else-branch as
        off-side. Pure-deletion blocks (no else) tag only the if-branch.
        """
        lines = merged.splitlines()
        on_ranges: list[tuple[int, int]] = []
        off_ranges: list[tuple[int, int]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if "[% if " in line and "%]" in line and toggle.toggle_name in line:
                # Find the matching [% endif %].
                depth = 1
                j = i
                else_at: int | None = None
                while j < len(lines):
                    if j > i and "[% if " in lines[j]:
                        depth += 1
                    elif "[% endif %]" in lines[j]:
                        depth -= 1
                        if depth == 0:
                            break
                    elif depth == 1 and "[% else %]" in lines[j]:
                        else_at = j
                    j += 1
                if j >= len(lines):
                    break
                if else_at is not None:
                    on_ranges.append((i, else_at + 1))
                    off_ranges.append((else_at, j + 1))
                else:
                    if "[% if not " in line:
                        off_ranges.append((i, j + 1))
                    else:
                        on_ranges.append((i, j + 1))
                i = j + 1
            else:
                i += 1
        self._right_highlighter.set_hunk_ranges(on_ranges, off_ranges)

    def _refresh_manifest_preview(self, toggle: ToggleResult | None) -> None:
        if toggle is None:
            self._manifest_preview_label.setText("")
            return
        sidecar = manifest_path_for(self._current_template_path)
        try:
            existing = load_manifest(self._current_template_path)
        except ConfigError as exc:
            self._manifest_preview_label.setText(
                f"Manifest parse failed: {exc}"
            )
            return
        existing_marker = (
            "(exists)" if existing is not None else "(will create)"
        )
        default_str = "true" if toggle.on_value else "false"
        desc = self._description_edit.text().strip()
        desc_part = f", description: {desc!r}" if desc else ""
        self._manifest_preview_label.setText(
            f"Manifest sync: {sidecar.name} {existing_marker}\n"
            f"  + {toggle.toggle_name}: "
            f"{{type: bool, default: {default_str}{desc_part}}}"
        )

    def _update_button_state(self, *, error: str | None) -> None:
        ready = (
            self._toggle is not None
            and self._merged_for_target is not None
            and error is None
        )
        self._save_overwrite_btn.setEnabled(ready)
        self._save_as_btn.setEnabled(ready)
        self._save_preset_btn.setEnabled(ready)

    # ---- save flows -------------------------------------------------------

    def _on_save_overwrite(self) -> None:
        if self._merged_for_target is None or self._toggle is None:
            return
        target = self._current_template_path
        choice = QMessageBox.question(
            self,
            "Overwrite template",
            f"This will overwrite {target.name} (a backup will be saved "
            f"as {target.name}.bak). Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return
        bak = target.with_name(target.name + ".bak")
        try:
            if target.is_file():
                bak.write_text(self._current_template_text, encoding="utf-8")
            target.write_text(self._merged_for_target, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Writing {target} failed: {exc}")
            return
        manifest_path, manifest_error = self._sync_manifest(target)
        self._last_outcome = _SaveOutcome(
            target_path=target,
            bak_path=bak if bak.is_file() else None,
            manifest_path=manifest_path,
            manifest_error=manifest_error,
        )
        if manifest_error:
            QMessageBox.warning(
                self, "Manifest sync failed",
                f"Template written to {target}\nbut manifest update failed: {manifest_error}\n"
                f"Please edit {manifest_path_for(target)} manually.",
            )
        self.accept()

    def _on_save_as(self) -> None:
        if self._merged_for_target is None or self._toggle is None:
            return
        default_name = (
            self._current_template_path.with_name(
                f"{self._current_template_path.stem}_with_"
                f"{self._toggle.toggle_name}.j2"
            )
        )
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save as", str(default_name),
            "Jinja templates (*.j2);;All files (*.*)",
        )
        if not path_str:
            return
        target = Path(path_str)
        try:
            target.write_text(self._merged_for_target, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Writing {target} failed: {exc}")
            return
        manifest_path, manifest_error = self._sync_manifest(target)
        self._last_outcome = _SaveOutcome(
            target_path=target,
            bak_path=None,
            manifest_path=manifest_path,
            manifest_error=manifest_error,
        )
        if manifest_error:
            QMessageBox.warning(
                self, "Manifest sync failed",
                f"Template written to {target}\nbut manifest update failed: {manifest_error}",
            )
        self.accept()

    def _on_save_preset(self) -> None:
        if self._toggle is None:
            return
        if self._auto_ext_root is None:
            QMessageBox.warning(
                self, "Missing root",
                "auto_ext_root is not configured; cannot locate presets directory."
            )
            return
        slug, ok = self._prompt_for_preset_slug()
        if not ok or not slug:
            return
        presets_dir = self._auto_ext_root / "templates" / "presets"
        presets_dir.mkdir(parents=True, exist_ok=True)
        try:
            save_preset(
                self._toggle, slug, presets_dir=presets_dir,
                description=self._description_edit.text().strip(),
                applicable_tool=self._bound_tool,
            )
        except (FileExistsError, ValueError) as exc:
            QMessageBox.warning(self, "Preset save failed", str(exc))
            return
        QMessageBox.information(
            self, "Preset saved",
            f"Written to {presets_dir / slug}/",
        )

    def _prompt_for_preset_slug(self) -> tuple[str, bool]:
        default = (self._toggle.toggle_name if self._toggle else "")
        text, ok = QInputDialog.getText(
            self, "Save preset",
            "Slug ([a-z0-9_-]+):", QLineEdit.Normal, default,
        )
        return text.strip(), ok

    def _sync_manifest(self, target: Path) -> tuple[Path | None, str | None]:
        if self._toggle is None:
            return None, "toggle missing"
        try:
            spec = KnobSpec(
                type="bool",
                default=self._toggle.on_value,
                description=self._description_edit.text().strip() or None,
            )
            path = append_knob_to_manifest_yaml(
                target, self._toggle.toggle_name, spec,
            )
            return path, None
        except (ConfigError, OSError, ValueError) as exc:
            return manifest_path_for(target), str(exc)

    # ---- public accessors for tests --------------------------------------

    def last_outcome(self) -> _SaveOutcome | None:
        return self._last_outcome

    # ---- programmatic helpers (used by tests) ----------------------------

    def set_on_text_for_tests(self, path: Path) -> None:
        self._set_on_path(path)

    def set_off_text_for_tests(self, path: Path) -> None:
        self._set_off_path(path)


def _with_caption(caption: str, widget: QWidget, parent: QWidget) -> QWidget:
    """Wrap a widget under a small caption label."""
    container = QWidget(parent)
    container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    label = QLabel(caption, container)
    label.setStyleSheet("color: #555; font-size: 11px;")
    layout.addWidget(label)
    layout.addWidget(widget, 1)
    return container


__all__ = ["DiffEditorDialog"]
