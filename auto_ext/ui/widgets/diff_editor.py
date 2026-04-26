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

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import (
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QRegExpValidator,
)
from PyQt5.QtCore import QRegExp
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
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
from auto_ext.ui.widgets.jinja_highlighter import JinjaHighlighter


_TOGGLE_NAME_REGEX = QRegExp(r"[a-z][a-z0-9_]*")


def _mono_font() -> QFont:
    f = QFont()
    f.setFamily("Consolas")
    f.setStyleHint(QFont.TypeWriter)
    return f


# ---- drop zone --------------------------------------------------------------


class _DropZone(QFrame):
    """Bordered drop area that emits ``path_dropped(Path)`` on a single
    local-file drop. Doubles as a click target opening the file dialog
    via the ``[…]`` button next to it (handled by the parent dialog —
    this widget itself is just the drop affordance)."""

    path_dropped = pyqtSignal(object)  # Path

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self._normal_style = (
            "QFrame { border: 2px dashed #888; min-height: 40px; "
            "background: #f8f8f8; }"
        )
        self._active_style = (
            "QFrame { border: 2px dashed #2080d0; min-height: 40px; "
            "background: #e8f0fa; }"
        )
        self.setStyleSheet(self._normal_style)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        self._label = QLabel(label, self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setStyleSheet("color: #666;")
        layout.addWidget(self._label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        urls = event.mimeData().urls() if event.mimeData() else []
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.setStyleSheet(self._active_style)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 — Qt API
        self.setStyleSheet(self._normal_style)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self.setStyleSheet(self._normal_style)
        urls = event.mimeData().urls() if event.mimeData() else []
        if len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.path_dropped.emit(Path(urls[0].toLocalFile()))
        else:
            event.ignore()

    def set_caption(self, text: str) -> None:
        self._label.setText(text)


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
            f"Diff toggle 编辑器 — {current_template_path.name}"
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
        except OSError:
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
        self._on_zone = _DropZone("On (true) — drop file here", self)
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

        self._off_zone = _DropZone("Off (false) — drop file here", self)
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
            "允许在已有 [% if %] 块之外插入 (推荐勾选)", self
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
        self._save_overwrite_btn = QPushButton("覆盖原模板", self)
        self._save_overwrite_btn.clicked.connect(self._on_save_overwrite)
        self._save_as_btn = QPushButton("另存为…", self)
        self._save_as_btn.clicked.connect(self._on_save_as)
        self._save_preset_btn = QPushButton("+ 保存为 preset", self)
        self._save_preset_btn.clicked.connect(self._on_save_preset)
        self._cancel_btn = QPushButton("取消", self)
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
        try:
            self._on_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._on_text = ""
            QMessageBox.warning(self, "读取失败", f"无法读取 {path}: {exc}")
        self._on_path_label.setText(str(path))
        self._refresh_state()

    def _set_off_path(self, path: Path) -> None:
        self._off_path = path
        try:
            self._off_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            self._off_text = ""
            QMessageBox.warning(self, "读取失败", f"无法读取 {path}: {exc}")
        self._off_path_label.setText(str(path))
        self._refresh_state()

    def _refresh_state(self) -> None:
        self._default_label.setText("ON" if self._on_value else "OFF")
        self._toggle = None
        self._merged_for_target = None
        self._right_preview.setPlainText("")
        self._right_highlighter.set_hunk_ranges([], [])
        self._refresh_manifest_preview(None)
        self._update_button_state(error=None)

        if not self._on_text or not self._off_text:
            self._status_label.setText("⓵ 请拖入两个 raw 文件")
            self._status_label.setStyleSheet("color: #888;")
            return
        if not self._toggle_name_edit.text().strip():
            self._status_label.setText("⓶ 请输入 toggle 名称 ([a-z][a-z0-9_]*)")
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
        parts = [f"✓ 检测到 {len(toggle.hunks)} 个 hunk(s)"]
        if existing:
            parts.append(f"已有 [% if %] 块: {len(existing)} 个")
        for w in toggle.warnings:
            if isinstance(w, LargeDiffWarning):
                parts.append(
                    f"⚠ 差异过大 ({w.change_ratio:.0%}). {w.message}"
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
                f"manifest 解析失败: {exc}"
            )
            return
        existing_marker = (
            "(已存在)" if existing is not None else "(将新建)"
        )
        default_str = "true" if toggle.on_value else "false"
        desc = self._description_edit.text().strip()
        desc_part = f", description: {desc!r}" if desc else ""
        self._manifest_preview_label.setText(
            f"Manifest 同步: {sidecar.name} {existing_marker}\n"
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
            "覆盖原模板",
            f"将覆盖 {target.name}（备份保存为 {target.name}.bak）。继续?",
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
            QMessageBox.critical(self, "保存失败", f"写入 {target} 失败: {exc}")
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
                self, "Manifest 同步失败",
                f"模板已写入 {target}\n但 manifest 更新失败: {manifest_error}\n"
                f"请手动编辑 {manifest_path_for(target)}",
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
            self, "另存为", str(default_name),
            "Jinja templates (*.j2);;All files (*.*)",
        )
        if not path_str:
            return
        target = Path(path_str)
        try:
            target.write_text(self._merged_for_target, encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"写入 {target} 失败: {exc}")
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
                self, "Manifest 同步失败",
                f"模板已写入 {target}\n但 manifest 更新失败: {manifest_error}",
            )
        self.accept()

    def _on_save_preset(self) -> None:
        if self._toggle is None:
            return
        if self._auto_ext_root is None:
            QMessageBox.warning(
                self, "缺少 root", "auto_ext_root 未配置，无法定位 presets 目录"
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
            QMessageBox.warning(self, "Preset 保存失败", str(exc))
            return
        QMessageBox.information(
            self, "Preset 已保存",
            f"已写入 {presets_dir / slug}/",
        )

    def _prompt_for_preset_slug(self) -> tuple[str, bool]:
        default = (self._toggle.toggle_name if self._toggle else "")
        text, ok = QInputDialog.getText(
            self, "保存 Preset",
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
