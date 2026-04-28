"""Editable QComboBox for the ``dspf_out_path`` project / task field.

Reused by both :class:`auto_ext.ui.tabs.project_tab.ProjectTab` and
:class:`auto_ext.ui.tabs.tasks_tab.TasksTab`. Shared so the two tabs
present the same preset list, the same fully-resolved preview rules,
and the same "Custom..." escape hatch.

Design constraints (from the rewrite spec):

- Combo dropdown items show **resolved real paths** (no ``${X}``,
  ``[[X]]``, or ``{cell}`` literals visible to the user).
- Each preset item carries the **template form** (``${WORK_ROOT2}/{cell}.dspf``)
  in ``Qt.UserRole`` so the YAML write-back stays templated.
- A trailing ``Custom...`` sentinel item (``userData=None``) lets the
  user type a custom expression verbatim into the editable line.
- The Tasks-tab variant prepends an additional ``(default: <X>)``
  sentinel at index 0 whose ``userData=None`` deletes the per-task
  override on selection.
- A small italic preview label below the combo always displays the
  fully-resolved path, refreshed on every text change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.errors import ConfigError
from auto_ext.core.env import substitute_env


_CUSTOM_LABEL = "Custom..."


@dataclass(frozen=True)
class DspfPreset:
    """One preset entry in the combo.

    ``template`` is the YAML-form value (e.g. ``${WORK_ROOT2}/{cell}.dspf``);
    it stays in ``itemData`` and never reaches user-visible text. The
    runtime preview is computed on demand by the resolver callback.
    """

    template: str
    description: str


_PROJECT_PRESETS: tuple[DspfPreset, ...] = (
    DspfPreset(
        template="${WORK_ROOT2}/{cell}.dspf",
        description="legacy default; lands at workarea root",
    ),
    DspfPreset(
        template="${output_dir}/{cell}.dspf",
        description="alongside other per-task EDA output",
    ),
)


def resolve_dspf_template(
    template: str,
    extended_env: dict[str, str],
    *,
    cell: str = "<cell>",
    library: str = "<library>",
    task_id: str = "<task_id>",
) -> tuple[str, str | None]:
    """Resolve a ``dspf_out_path`` template for preview.

    Mirrors the runner's two-phase substitution
    (env / path-token then ``str.format``) but tolerates missing
    references and reports them via the second tuple slot rather than
    raising — so the GUI preview never explodes on a half-typed value.

    Returns ``(preview_text, error_msg_or_None)``. On success
    ``preview_text`` is the fully-resolved path. On failure
    ``preview_text`` echoes the original template (so the user sees what
    they typed) and ``error_msg_or_None`` carries the ``unresolved: $X``
    or ``unknown format key`` reason for display.
    """
    if not template:
        return "", "empty"
    after_env = substitute_env(template, extended_env)
    # Detect unresolved env references (substitute_env passes them
    # through as ``${X}`` / ``$X`` / ``$env(X)``).
    unresolved: list[str] = []
    for marker in ("${", "$env("):
        if marker in after_env:
            unresolved.append(marker.rstrip("("))
    # Bare `$IDENT`: hard to reliably detect without re-running the
    # regex, but the brace and tcl forms cover the common GUI cases.
    try:
        resolved = after_env.format(cell=cell, library=library, task_id=task_id)
    except KeyError as exc:
        return after_env, f"unknown format key {{{exc.args[0]}}}"
    except (IndexError, ValueError) as exc:
        return after_env, f"format error: {exc}"
    if unresolved:
        return resolved, f"unresolved: {', '.join(unresolved)}"
    return resolved, None


class DspfOutPathCombo(QWidget):
    """Editable combo + preview label for a ``dspf_out_path`` value.

    Emits :attr:`value_changed` with the staged YAML-form value every
    time the user picks a different preset, types into the editable
    line, or — in tasks-tab mode — hits the ``(default: ...)``
    sentinel.

    The widget never decides what to write to the model on its own; it
    just surfaces the current intent (``str`` for an explicit value,
    ``None`` for the tasks-tab default sentinel) and lets the calling
    tab stage the edit through its controller.

    ``resolver`` is a callback ``(template) -> (preview, error)`` that
    the widget invokes to refresh the preview label. The owning tab
    constructs it with the live env-overrides and any task-specific
    placeholders so the preview tracks UI state without this widget
    needing to import ConfigController.
    """

    #: Emitted when the staged value changes. Argument is the YAML-form
    #: string (``${WORK_ROOT2}/{cell}.dspf`` etc.) or ``None`` for the
    #: tasks-tab default sentinel.
    value_changed = pyqtSignal(object)

    _ROLE_TEMPLATE = int(Qt.UserRole)

    def __init__(
        self,
        resolver: Callable[[str], tuple[str, str | None]],
        *,
        include_default_sentinel: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        """Build the widget.

        ``include_default_sentinel`` (False on Project tab, True on Tasks
        tab) prepends a ``(default: <project_value_preview>)`` item at
        index 0 whose ``userData=None`` triggers the per-task delete.
        ``project_default_template`` and ``project_default_preview`` are
        only meaningful when this flag is True.
        """
        super().__init__(parent)
        self._resolver = resolver
        self._include_default_sentinel = include_default_sentinel
        self._default_template: str | None = None
        self._default_preview: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox(self)
        self._combo.setEditable(True)
        self._combo.setInsertPolicy(QComboBox.NoInsert)
        self._combo.setMinimumContentsLength(40)
        layout.addWidget(self._combo)

        self._preview_label = QLabel(self)
        self._preview_label.setStyleSheet(
            "color: #666; font-style: italic; font-size: 11px;"
        )
        self._preview_label.setWordWrap(True)
        layout.addWidget(self._preview_label)

        self._populating = False
        self._combo.currentIndexChanged.connect(self._on_index_changed)
        self._combo.lineEdit().editingFinished.connect(self._on_text_committed)

        self._populate_items()
        self._refresh_preview()

    # ---- public API --------------------------------------------------

    def set_default_hint(
        self, template: str | None, preview: str
    ) -> None:
        """Update the ``(default: <X>)`` sentinel label (tasks tab only).

        Called whenever the project-level ``dspf_out_path`` resolves to
        a new value (env override change, project edit, etc.). The combo
        is repopulated so the sentinel reflects the new effective default.
        """
        if not self._include_default_sentinel:
            return
        self._default_template = template
        self._default_preview = preview
        # Preserve the current selection / text across rebuild.
        current_data = self._combo.currentData(self._ROLE_TEMPLATE)
        current_text = self._combo.lineEdit().text()
        self._populate_items()
        # Try to restore: if a known userData matches, jump there;
        # otherwise drop the typed text back into the line edit.
        if current_data is not None:
            idx = self._find_item_by_template(current_data)
            if idx >= 0:
                self._combo.blockSignals(True)
                try:
                    self._combo.setCurrentIndex(idx)
                finally:
                    self._combo.blockSignals(False)
        elif current_text:
            self._combo.lineEdit().setText(current_text)
        self._refresh_preview()

    def set_value(self, template: str | None) -> None:
        """Display ``template`` (YAML-form). ``None`` selects the tasks-tab
        default sentinel (only valid when ``include_default_sentinel``).
        """
        self._populating = True
        try:
            if template is None:
                if self._include_default_sentinel:
                    self._combo.setCurrentIndex(0)
                else:
                    self._combo.lineEdit().clear()
                self._refresh_preview()
                return
            idx = self._find_item_by_template(template)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
                # Sync the editable line with the resolved label so a
                # later index change (e.g. user picks the default
                # sentinel) clears the overlay cleanly.
                self._combo.lineEdit().setText(self._combo.itemText(idx))
            else:
                # Custom value — park the combo on the trailing
                # ``Custom...`` sentinel so a subsequent ``setCurrentIndex(0)``
                # fires currentIndexChanged (Qt would skip the signal if
                # the index were already 0). Then drop the custom string
                # into the editable line.
                custom_idx = self._combo.count() - 1
                if custom_idx >= 0:
                    self._combo.setCurrentIndex(custom_idx)
                self._combo.lineEdit().setText(template)
            self._refresh_preview()
        finally:
            self._populating = False

    def current_value(self) -> str | None:
        """Return the staged value: a template string, or ``None`` if the
        tasks-tab default sentinel is selected.
        """
        idx = self._combo.currentIndex()
        item_text = self._combo.itemText(idx) if idx >= 0 else ""
        line_text = self._combo.lineEdit().text()
        # If the user typed something different from any preset's display
        # text, they're in custom mode → take the line edit verbatim.
        if line_text and line_text != item_text:
            return line_text
        # Default sentinel? → None.
        data = self._combo.currentData(self._ROLE_TEMPLATE)
        if data is None and self._include_default_sentinel and idx == 0:
            return None
        # Custom... sentinel? → take whatever is in the line edit.
        if data is None and item_text == _CUSTOM_LABEL:
            return line_text or None
        # Preset → return its template form.
        return data if isinstance(data, str) else (line_text or None)

    def refresh(self) -> None:
        """Re-resolve every item's preview. Call after env-overrides change."""
        self._populate_items()
        self._refresh_preview()

    # ---- internals ---------------------------------------------------

    def _populate_items(self) -> None:
        self._populating = True
        try:
            self._combo.clear()
            if self._include_default_sentinel:
                preview = self._default_preview or "<unset>"
                self._combo.addItem(f"(default: {preview})", None)
            for preset in _PROJECT_PRESETS:
                resolved, err = self._resolver(preset.template)
                label = resolved if not err else f"{resolved}  ({err})"
                self._combo.addItem(label, preset.template)
                tooltip = (
                    f"YAML form: {preset.template}\n"
                    f"Resolves to: {resolved}\n"
                    f"({preset.description})"
                )
                self._combo.setItemData(
                    self._combo.count() - 1, tooltip, Qt.ToolTipRole
                )
            # Trailing custom sentinel.
            self._combo.addItem(_CUSTOM_LABEL, None)
            self._combo.setItemData(
                self._combo.count() - 1,
                "Type a custom expression below.\n"
                "Tokens: ${env}, ${output_dir}, ${intermediate_dir},\n"
                "${paths.*}, {cell}, {library}, {task_id}.",
                Qt.ToolTipRole,
            )
        finally:
            self._populating = False

    def _find_item_by_template(self, template: str) -> int:
        for i in range(self._combo.count()):
            data = self._combo.itemData(i, self._ROLE_TEMPLATE)
            if data == template:
                return i
        return -1

    def _on_index_changed(self, _idx: int) -> None:
        if self._populating:
            return
        idx = self._combo.currentIndex()
        item_text = self._combo.itemText(idx) if idx >= 0 else ""
        if item_text == _CUSTOM_LABEL:
            # Selecting the sentinel itself clears the line so the user
            # can type fresh. No emission yet — wait for editingFinished.
            self._populating = True
            try:
                self._combo.lineEdit().clear()
            finally:
                self._populating = False
            self._refresh_preview()
            return
        # Default sentinel (tasks tab) or a real preset: always sync the
        # line edit to the item text so leftover custom typing doesn't
        # mask the selection. Without this, a previously-typed override
        # would survive when the user picks the default sentinel.
        self._populating = True
        try:
            data = self._combo.currentData(self._ROLE_TEMPLATE)
            if data is None and self._include_default_sentinel and idx == 0:
                # Default sentinel: clear the editable line so
                # ``current_value()`` returns None.
                self._combo.lineEdit().clear()
            elif isinstance(data, str):
                # Preset: keep the line in sync with the resolved label
                # so the user can see what was picked.
                self._combo.lineEdit().setText(item_text)
        finally:
            self._populating = False
        self._refresh_preview()
        self.value_changed.emit(self.current_value())

    def _on_text_committed(self) -> None:
        if self._populating:
            return
        self._refresh_preview()
        self.value_changed.emit(self.current_value())

    def _refresh_preview(self) -> None:
        value = self.current_value()
        if value is None:
            text = self._default_preview or "(use project default)"
            self._preview_label.setText(f"→ {text}")
            self._preview_label.setStyleSheet(
                "color: #666; font-style: italic; font-size: 11px;"
            )
            return
        try:
            preview, error = self._resolver(value)
        except ConfigError as exc:
            preview, error = value, f"resolve error: {exc}"
        if error:
            self._preview_label.setText(f"→ {preview}  ({error})")
            self._preview_label.setStyleSheet(
                "color: #c83232; font-style: italic; font-size: 11px;"
            )
        else:
            self._preview_label.setText(f"→ {preview}")
            self._preview_label.setStyleSheet(
                "color: #2a7a2a; font-style: italic; font-size: 11px;"
            )
