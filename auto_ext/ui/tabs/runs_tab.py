"""Runs tab: the run history on the left, one run's result card on the right.

Before S1 a finished GUI run produced nothing to look at -- ``logs/`` was
opened with mode ``"w"`` and the next run of the same cell overwrote it, and
``RunTab._on_run_finished`` discarded the summary. The CLI's rich table was
strictly better than the GUI. This tab is the answer to "let me look at that
again": every run is a directory under ``<auto_ext_root>/runs/``, this lists
them newest first and renders the selected one with
:class:`~auto_ext.ui.widgets.result_card.ResultCard`.

Reading is delegated wholesale to :mod:`auto_ext.core.run_store`
(:func:`~auto_ext.core.run_store.list_runs` never raises on a corrupt or
hand-made directory, so a single bad run cannot empty the history). Writing is
limited to ``annotations.json``: renaming a run and attaching a note change the
user-facing label only -- never the directory name, never ``run.json``, because
``batches/*.json``, ``parent_run_id`` and every relative path inside a run
reference the run by its immutable id.

Layout contract
---------------
Nothing here may contribute a large minimum height: the main window is already
pinned at 1056 px by the Project tab, which does not fit on a 1080p screen. The
history list and the result card live inside splitters, and the card puts its
own detail sections inside a scroll area, so this tab's
``minimumSizeHint().height()`` stays a small fraction of the window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAction,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_ext.core.errors import AutoExtError
from auto_ext.core.handoff import launch_calibre_interactive
from auto_ext.core.run_store import (
    RunIndexEntry,
    find_previous_run,
    list_runs,
    read_annotations,
    read_record,
    write_annotations,
)
from auto_ext.model.run import RunRecord
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.models import STATUS_COLOR, TASK_DISPLAY
from auto_ext.ui.os_open import open_in_os
from auto_ext.ui.tabs.log_tab import LogTab
from auto_ext.ui.widgets.result_card import (
    COLOR_MUTED,
    ResultCard,
    format_duration,
    format_timestamp,
)

#: Directory under ``auto_ext_root`` that holds every run directory.
RUNS_DIRNAME = "runs"

#: Item role carrying the ``run_id`` of a history row.
_ROLE_RUN_ID = Qt.UserRole


def entry_matches(entry: RunIndexEntry, needle: str) -> bool:
    """True when ``entry`` matches the filter box.

    Covers the axes schema doc section 2.4 names as searchable -- display
    name, slug, cell, recipe id, tags -- plus three the user reaches for in
    practice: the library (one cell name often recurs across libraries), the
    overall status ("failed" narrows to what needs attention), and the run id
    itself, which is what gets pasted when a colleague quotes one.

    Substring, case-insensitive, on every axis; an empty or blank needle
    matches everything.
    """

    needle = needle.strip().lower()
    if not needle:
        return True
    haystack = [
        entry.display_name,
        entry.slug,
        entry.cell,
        entry.library,
        entry.recipe_id,
        entry.run_id,
        entry.overall,
        *entry.tags,
    ]
    return any(needle in str(field).lower() for field in haystack)


class RunsTab(QWidget):
    """Run history browser plus the result card for the selected run."""

    #: Emitted with the selected :class:`~auto_ext.core.run_store.RunIndexEntry`
    #: (or ``None`` when the selection is cleared).
    run_selected = pyqtSignal(object)
    #: Emitted with the absolute :class:`Path` of a stage log the user opened.
    #: The embedded viewer is already showing it; the signal exists so a host
    #: can mirror it elsewhere.
    log_requested = pyqtSignal(object)
    #: Emitted with the ``run_id`` whose ``annotations.json`` was just written.
    annotations_saved = pyqtSignal(str)

    def __init__(
        self,
        controller: ConfigController | None = None,
        parent: QWidget | None = None,
        *,
        runs_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._runs_root_override = Path(runs_root) if runs_root is not None else None
        self._entries: list[RunIndexEntry] = []
        self._selected_run_id: str | None = None
        self._loading = False

        self._build_ui()

        if controller is not None:
            controller.config_loaded.connect(self._on_config_changed)
            controller.config_saved.connect(self._on_config_changed)

        self.refresh()

    # ---- public API ---------------------------------------------------

    @property
    def runs_root(self) -> Path | None:
        """``<auto_ext_root>/runs``, or the directory passed to the ctor."""

        if self._runs_root_override is not None:
            return self._runs_root_override
        if self._controller is None:
            return None
        root = self._controller.auto_ext_root
        return None if root is None else root / RUNS_DIRNAME

    def set_runs_root(self, runs_root: Path | None) -> None:
        """Point the history at ``runs_root`` and reload. ``None`` restores the
        controller-derived location."""

        self._runs_root_override = Path(runs_root) if runs_root is not None else None
        self.refresh()

    @property
    def entries(self) -> list[RunIndexEntry]:
        """The full history as last read, newest first (before filtering)."""

        return list(self._entries)

    @property
    def selected_entry(self) -> RunIndexEntry | None:
        """The history row the user has selected, or ``None``."""

        items = self._tree.selectedItems()
        if not items:
            return None
        run_id = items[0].data(0, _ROLE_RUN_ID)
        return self._entry_by_id(str(run_id)) if run_id else None

    @property
    def result_card(self) -> ResultCard:
        """The card rendering the selected run."""

        return self._card

    def refresh(self) -> None:
        """Re-read the run history from disk and repopulate the list.

        Keeps the current selection when that run is still present; otherwise
        selects the newest run so the tab is never opened on a blank card.
        """

        root = self.runs_root
        self._entries = list_runs(root) if root is not None else []
        self._repopulate()

    def select_run(self, run_id: str) -> bool:
        """Select the row for ``run_id``. Returns False when it is not listed."""

        for index in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(index)
            if item.data(0, _ROLE_RUN_ID) == run_id:
                self._tree.setCurrentItem(item)
                return True
        return False

    # ---- construction -------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self._filter = QLineEdit(self)
        self._filter.setPlaceholderText(
            "Filter by name, cell, recipe, tag or run id"
        )
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._repopulate)
        self._count_label = QLabel("", self)
        self._count_label.setStyleSheet(f"color: {COLOR_MUTED};")
        refresh_btn = QPushButton("Refresh", self)
        refresh_btn.clicked.connect(self.refresh)
        top.addWidget(QLabel("Runs:", self))
        top.addWidget(self._filter, stretch=1)
        top.addWidget(self._count_label)
        top.addWidget(refresh_btn)
        root.addLayout(top)

        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, stretch=1)

        self._empty = QLabel("", self._stack)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(f"color: {COLOR_MUTED};")
        self._stack.addWidget(self._empty)

        splitter = QSplitter(Qt.Horizontal, self._stack)

        left = QWidget(splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._tree = QTreeWidget(left)
        self._tree.setHeaderLabels(["run", "started", "status"])
        self._tree.setRootIsDecorated(False)
        self._tree.setColumnWidth(0, 220)
        self._tree.setColumnWidth(1, 160)
        self._tree.setSelectionMode(QTreeWidget.SingleSelection)
        self._tree.itemSelectionChanged.connect(self._on_selection_changed)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        left_layout.addWidget(self._tree, stretch=1)

        btn_row = QHBoxLayout()
        self._rename_btn = QPushButton("Rename...", left)
        self._rename_btn.clicked.connect(self._rename_selected)
        self._note_btn = QPushButton("Note...", left)
        self._note_btn.clicked.connect(self._edit_note_selected)
        self._star_btn = QPushButton("Star", left)
        self._star_btn.clicked.connect(self._toggle_star_selected)
        for button in (self._rename_btn, self._note_btn, self._star_btn):
            button.setEnabled(False)
            btn_row.addWidget(button)
        btn_row.addStretch(1)
        left_layout.addLayout(btn_row)

        splitter.addWidget(left)

        right = QSplitter(Qt.Vertical, splitter)
        self._card = ResultCard(right)
        self._card.log_requested.connect(self._on_log_requested)
        self._card.artifact_requested.connect(self._open_path)
        self._card.handoff_requested.connect(self._on_handoff_requested)
        self._log_tab = LogTab(right)
        right.addWidget(self._card)
        right.addWidget(self._log_tab)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([340, 860])
        self._splitter = splitter

        self._stack.addWidget(splitter)
        self._stack.setCurrentWidget(self._empty)

    # ---- history list --------------------------------------------------

    def _entry_by_id(self, run_id: str) -> RunIndexEntry | None:
        for entry in self._entries:
            if entry.run_id == run_id:
                return entry
        return None

    def _empty_text(self) -> str:
        root = self.runs_root
        if root is None:
            return (
                "No project loaded. Open one on the Run tab - run history is "
                "kept in the project's runs/ directory."
            )
        if self._entries and self._filter.text().strip():
            return f"No run matches {self._filter.text().strip()!r}."
        return (
            f"No runs recorded yet. Start a run on the Run tab; each one is "
            f"archived under {root} with its logs, rendered inputs and results."
        )

    def _repopulate(self) -> None:
        needle = self._filter.text()
        visible = [e for e in self._entries if entry_matches(e, needle)]

        self._loading = True
        try:
            self._tree.clear()
            for entry in visible:
                item = QTreeWidgetItem(
                    [
                        ("* " if entry.starred else "") + entry.display_name,
                        format_timestamp(entry.created_at),
                        TASK_DISPLAY.get(entry.overall, entry.overall),
                    ]
                )
                item.setData(0, _ROLE_RUN_ID, entry.run_id)
                color = STATUS_COLOR.get(entry.overall)
                if color:
                    item.setForeground(2, QColor(color))
                tooltip = [
                    entry.run_id,
                    entry.dut_key,
                    f"recipe {entry.recipe_id}" if entry.recipe_id else "",
                    f"duration {format_duration(entry.duration_s)}",
                ]
                if entry.dry_run:
                    tooltip.append("dry run")
                if entry.tags:
                    tooltip.append("tags: " + ", ".join(entry.tags))
                item.setToolTip(0, "\n".join(t for t in tooltip if t))
                self._tree.addTopLevelItem(item)
        finally:
            self._loading = False

        total = len(self._entries)
        if len(visible) == total:
            self._count_label.setText(f"{total} run(s)")
        else:
            self._count_label.setText(f"{len(visible)} of {total} run(s)")

        if not visible:
            self._empty.setText(self._empty_text())
            self._stack.setCurrentWidget(self._empty)
            self._card.clear()
            self._update_buttons()
            self.run_selected.emit(None)
            return

        self._stack.setCurrentWidget(self._splitter)
        # The tree was cleared above, so whichever row becomes current here is
        # a genuine selection change and drives the card through the signal.
        if self._selected_run_id is None or not self.select_run(self._selected_run_id):
            self._tree.setCurrentItem(self._tree.topLevelItem(0))

    def _on_config_changed(self, _config_dir: object = None) -> None:
        self.refresh()

    # ---- selection -> card ---------------------------------------------

    def _on_selection_changed(self) -> None:
        if self._loading:
            return
        entry = self.selected_entry
        self._update_buttons()
        if entry is None:
            self._selected_run_id = None
            self._card.clear()
            self.run_selected.emit(None)
            return

        self._selected_run_id = entry.run_id
        record = self._read_record(entry)
        if record is None:
            self.run_selected.emit(entry)
            return

        previous = find_previous_run(
            self.runs_root or entry.run_dir.parent, entry, entries=self._entries
        )
        self._card.set_run(
            record,
            run_dir=entry.run_dir,
            annotations=read_annotations(entry.run_dir),
            previous=previous,
        )
        self.run_selected.emit(entry)

    def _read_record(self, entry: RunIndexEntry) -> RunRecord | None:
        try:
            return read_record(entry.run_dir)
        except AutoExtError as exc:
            self._card.show_message(
                f"{entry.run_id} could not be read: {exc}"
            )
            return None

    def _update_buttons(self) -> None:
        entry = self.selected_entry
        enabled = entry is not None
        for button in (self._rename_btn, self._note_btn, self._star_btn):
            button.setEnabled(enabled)
        self._star_btn.setText(
            "Unstar" if entry is not None and entry.starred else "Star"
        )

    # ---- annotations ----------------------------------------------------

    def _write_annotations(self, entry: RunIndexEntry, **changes: Any) -> None:
        """Apply ``changes`` to the run's annotations and persist them.

        Only ``annotations.json`` is touched: the directory name and
        ``run.json`` are immutable, so a rename can never break the relative
        paths inside the run or the ``run_id`` references from batches.
        """

        annotations = read_annotations(entry.run_dir)
        try:
            write_annotations(entry.run_dir, annotations.model_copy(update=changes))
        except (AutoExtError, OSError) as exc:
            QMessageBox.warning(
                self,
                "Could not save",
                f"Failed to write annotations for {entry.run_id}:\n{exc}",
            )
            return
        self.annotations_saved.emit(entry.run_id)
        self.refresh()

    def _rename_selected(self) -> None:
        entry = self.selected_entry
        if entry is None:
            return
        current = read_annotations(entry.run_dir).display_name or entry.display_name
        text, ok = QInputDialog.getText(
            self,
            "Rename run",
            f"Display name for {entry.run_id}\n"
            "(the directory name never changes):",
            QLineEdit.Normal,
            current,
        )
        if not ok:
            return
        text = text.strip()
        self._write_annotations(entry, display_name=text or None)

    def _edit_note_selected(self) -> None:
        entry = self.selected_entry
        if entry is None:
            return
        current = read_annotations(entry.run_dir).note or ""
        text, ok = QInputDialog.getMultiLineText(
            self, "Run note", f"Note for {entry.run_id}:", current
        )
        if not ok:
            return
        text = text.strip()
        self._write_annotations(entry, note=text or None)

    def _toggle_star_selected(self) -> None:
        entry = self.selected_entry
        if entry is None:
            return
        starred = read_annotations(entry.run_dir).starred
        self._write_annotations(entry, starred=not starred)

    # ---- context menu ---------------------------------------------------

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        item = self._tree.itemAt(pos)
        if item is None:
            return
        self._tree.setCurrentItem(item)
        entry = self.selected_entry
        if entry is None:
            return

        menu = QMenu(self._tree)
        act_rename = QAction("Rename...", menu)
        act_rename.triggered.connect(lambda _c=False: self._rename_selected())
        menu.addAction(act_rename)

        act_note = QAction("Edit note...", menu)
        act_note.triggered.connect(lambda _c=False: self._edit_note_selected())
        menu.addAction(act_note)

        act_star = QAction("Unstar" if entry.starred else "Star", menu)
        act_star.setToolTip("Starred runs are never removed by prune.")
        act_star.triggered.connect(lambda _c=False: self._toggle_star_selected())
        menu.addAction(act_star)

        menu.addSeparator()

        act_open = QAction("Open run directory", menu)
        run_dir = entry.run_dir
        if run_dir.is_dir():
            act_open.setToolTip(str(run_dir))
            act_open.triggered.connect(
                lambda _c=False, p=run_dir: self._open_path(p)
            )
        else:
            act_open.setEnabled(False)
            act_open.setToolTip(f"The run directory is gone: {run_dir}")
        menu.addAction(act_open)

        act_copy = QAction("Copy run id", menu)
        act_copy.triggered.connect(
            lambda _c=False, t=entry.run_id: self._card.copy_text(t)
        )
        menu.addAction(act_copy)

        # X11 delivers the context-menu event on button *press*; a synchronous
        # exec_() is dismissed by the following release, forcing a second
        # right-click. Defer the popup one event-loop tick.
        global_pos = self._tree.viewport().mapToGlobal(pos)
        QTimer.singleShot(0, lambda: menu.exec_(global_pos))

    # ---- card actions ----------------------------------------------------

    def _on_log_requested(self, path: object) -> None:
        log_path = Path(str(path)) if path else None
        entry = self.selected_entry
        self._log_tab.set_active_log(
            log_path, entry.display_name if entry is not None else None
        )
        self.log_requested.emit(log_path)

    def _on_handoff_requested(self, record: object) -> None:
        """Re-plan and launch Calibre Interactive for the displayed run.

        The plan is rebuilt here rather than reusing the one the card
        pre-flighted: the runset may have been deleted, or the Calibre setup
        script sourced, in the seconds since the card was drawn.
        ``launch_calibre_interactive`` never raises -- every outcome comes back
        on the plan.
        """

        if not isinstance(record, RunRecord):
            return
        plan = launch_calibre_interactive(record)
        if not plan.ok:
            QMessageBox.warning(
                self,
                "Cannot open Calibre Interactive",
                plan.reason or "Calibre Interactive could not be started.",
            )
            return
        lines = [f"Calibre Interactive started (pid {plan.pid}).", "", plan.command_line]
        if plan.warnings:
            lines.extend(["", *plan.warnings])
        QMessageBox.information(self, "Calibre Interactive", "\n".join(lines))

    def _open_path(self, path: object) -> None:
        """Open ``path`` with the OS handler, reporting failures in a dialog."""

        target = Path(str(path))
        try:
            open_in_os(target)
        except FileNotFoundError:
            QMessageBox.warning(
                self, "File not found", f"The path no longer exists:\n{target}"
            )
        except OSError as exc:
            QMessageBox.warning(
                self, "Could not open", f"Failed to open:\n{target}\n\n{exc}"
            )
