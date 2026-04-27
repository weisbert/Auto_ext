"""Modal preset picker (Phase 5.6).

Lists every valid preset under ``<auto_ext_root>/templates/presets/``,
shows a meta + snippet preview, and applies the selected preset to the
current template — refusing if the on-side anchors don't match (no
fuzzy fallback in v1, per locked decision).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QBrush, QColor, QFont
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.errors import ConfigError
from auto_ext.core.manifest import (
    KnobSpec,
    append_knob_to_manifest_yaml,
    manifest_path_for,
)
from auto_ext.core.preset import Preset, apply_preset, list_presets
from auto_ext.ui.widgets.jinja_highlighter import JinjaHighlighter


def _mono_font() -> QFont:
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f


@dataclass
class _ApplyOutcome:
    target_path: Path
    manifest_path: Path | None
    manifest_error: str | None


class PresetPickerDialog(QDialog):
    """Modal dialog: pick a preset and apply it to the current template."""

    def __init__(
        self,
        current_template_path: Path,
        bound_tool: str | None,
        presets_dir: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            f"Apply toggle preset - {current_template_path.name}"
        )
        self.setModal(True)
        self.resize(900, 640)

        self._current_template_path = current_template_path
        self._bound_tool = bound_tool
        self._presets_dir = presets_dir

        try:
            self._template_text = current_template_path.read_text(encoding="utf-8")
        except OSError:
            self._template_text = ""

        self._presets: list[Preset] = list_presets(presets_dir)
        self._current_preset: Preset | None = None
        self._merged_text: str | None = None
        self._last_outcome: _ApplyOutcome | None = None

        self._build_ui()
        self._populate_list()

    # ---- UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal, self)

        self._list = QListWidget(splitter)
        self._list.currentRowChanged.connect(self._on_row_changed)
        splitter.addWidget(self._list)

        right = QWidget(splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._meta_label = QLabel("(no preset selected)", right)
        self._meta_label.setStyleSheet("font-family: monospace; color: #444;")
        self._meta_label.setWordWrap(True)
        right_layout.addWidget(self._meta_label)
        self._snippet_view = QPlainTextEdit(right)
        self._snippet_view.setReadOnly(True)
        self._snippet_view.setFont(_mono_font())
        self._snippet_highlighter = JinjaHighlighter(self._snippet_view.document())
        right_layout.addWidget(self._snippet_view, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self._result_view = QPlainTextEdit(self)
        self._result_view.setReadOnly(True)
        self._result_view.setFont(_mono_font())
        self._result_view.setMaximumHeight(220)
        self._result_highlighter = JinjaHighlighter(self._result_view.document())
        root.addWidget(QLabel("Preview after applying:", self))
        root.addWidget(self._result_view)

        self._status_label = QLabel("", self)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color: #555; font-size: 12px;")
        root.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        self._save_overwrite_btn = QPushButton("Overwrite template", self)
        self._save_overwrite_btn.clicked.connect(self._on_save_overwrite)
        self._save_as_btn = QPushButton("Save as...", self)
        self._save_as_btn.clicked.connect(self._on_save_as)
        self._cancel_btn = QPushButton("Cancel", self)
        self._cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._save_overwrite_btn)
        btn_row.addWidget(self._save_as_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self._cancel_btn)
        root.addLayout(btn_row)

        self._save_overwrite_btn.setEnabled(False)
        self._save_as_btn.setEnabled(False)

    def _populate_list(self) -> None:
        self._list.clear()
        for preset in self._presets:
            item = QListWidgetItem(preset.slug)
            applicable = preset.applicable_tool
            if applicable is not None and self._bound_tool is not None:
                if applicable != self._bound_tool:
                    item.setForeground(QBrush(QColor("#888888")))
                    item.setToolTip(
                        f"this preset targets {applicable}; current "
                        f"template is bound to {self._bound_tool}"
                    )
                    item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            self._list.addItem(item)

    # ---- slots ------------------------------------------------------------

    def _on_row_changed(self, row: int) -> None:
        self._current_preset = None
        self._merged_text = None
        self._save_overwrite_btn.setEnabled(False)
        self._save_as_btn.setEnabled(False)
        if not (0 <= row < len(self._presets)):
            self._meta_label.setText("(no preset selected)")
            self._snippet_view.setPlainText("")
            self._result_view.setPlainText("")
            self._status_label.setText("")
            return
        preset = self._presets[row]
        self._current_preset = preset
        self._meta_label.setText(
            f"name: {preset.name}\n"
            f"description: {preset.description}\n"
            f"applicable_tool: {preset.applicable_tool or 'any'}\n"
            f"default: {preset.default}"
        )
        self._snippet_view.setPlainText(preset.snippet)

        # Try to apply.
        try:
            merged, _ = apply_preset(preset, self._template_text)
        except ValueError as exc:
            self._merged_text = None
            self._result_view.setPlainText("")
            self._status_label.setText(f"✗ {exc}")
            self._status_label.setStyleSheet("color: #c83232; font-weight: bold;")
            return
        self._merged_text = merged
        self._result_view.setPlainText(merged)
        self._status_label.setText("✓ Anchors matched, ready to write")
        self._status_label.setStyleSheet("color: #208020;")
        self._save_overwrite_btn.setEnabled(True)
        self._save_as_btn.setEnabled(True)

    # ---- save flows -------------------------------------------------------

    def _on_save_overwrite(self) -> None:
        if self._merged_text is None or self._current_preset is None:
            return
        choice = QMessageBox.question(
            self,
            "Overwrite template",
            f"This will overwrite {self._current_template_path.name} "
            f"(a .bak backup will be saved). Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if choice != QMessageBox.Yes:
            return
        bak = self._current_template_path.with_name(
            self._current_template_path.name + ".bak"
        )
        try:
            if self._current_template_path.is_file():
                bak.write_text(self._template_text, encoding="utf-8")
            self._current_template_path.write_text(
                self._merged_text, encoding="utf-8"
            )
        except OSError as exc:
            QMessageBox.critical(
                self, "Save failed",
                f"Writing {self._current_template_path} failed: {exc}",
            )
            return
        manifest_path, manifest_error = self._sync_manifest(
            self._current_template_path
        )
        self._last_outcome = _ApplyOutcome(
            target_path=self._current_template_path,
            manifest_path=manifest_path,
            manifest_error=manifest_error,
        )
        if manifest_error:
            QMessageBox.warning(
                self, "Manifest sync failed",
                f"Template was written\nbut manifest update failed: {manifest_error}",
            )
        self.accept()

    def _on_save_as(self) -> None:
        if self._merged_text is None or self._current_preset is None:
            return
        default_name = self._current_template_path.with_name(
            f"{self._current_template_path.stem}_with_"
            f"{self._current_preset.name}.j2"
        )
        path_str, _ = QFileDialog.getSaveFileName(
            self, "Save as", str(default_name),
            "Jinja templates (*.j2);;All files (*.*)",
        )
        if not path_str:
            return
        target = Path(path_str)
        try:
            target.write_text(self._merged_text, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Writing {target} failed: {exc}")
            return
        manifest_path, manifest_error = self._sync_manifest(target)
        self._last_outcome = _ApplyOutcome(
            target_path=target,
            manifest_path=manifest_path,
            manifest_error=manifest_error,
        )
        if manifest_error:
            QMessageBox.warning(
                self, "Manifest sync failed",
                f"Template written to {target}\nbut manifest update failed: {manifest_error}",
            )
        self.accept()

    def _sync_manifest(self, target: Path) -> tuple[Path | None, str | None]:
        if self._current_preset is None:
            return None, "preset missing"
        try:
            spec = KnobSpec(
                type="bool",
                default=self._current_preset.default,
                description=self._current_preset.description or None,
            )
            path = append_knob_to_manifest_yaml(
                target, self._current_preset.name, spec,
            )
            return path, None
        except (ConfigError, OSError, ValueError) as exc:
            return manifest_path_for(target), str(exc)

    # ---- public accessor for tests ---------------------------------------

    def last_outcome(self) -> _ApplyOutcome | None:
        return self._last_outcome


__all__ = ["PresetPickerDialog"]
