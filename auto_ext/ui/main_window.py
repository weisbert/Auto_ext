"""Top-level :class:`QMainWindow`: the shell plus the redesign screens.

The window used to be a :class:`QTabWidget` with five tabs (Run, Runs,
Project, Tasks, Templates). The redesign replaced the top tab bar with a left
navigation rail, and this round replaced the tabs themselves. What is left is
four screens and one drawer:

======== ===== ===================================================
key      code  screen
======== ===== ===================================================
cells    CEL   :class:`~auto_ext.ui.screens.cells_screen.CellsScreen`
recipes  RCP   :class:`~auto_ext.ui.screens.recipes_screen.RecipesScreen`
runs     RNS   :class:`~auto_ext.ui.screens.runs_screen.RunsScreen`
project  PRJ   :class:`~auto_ext.ui.screens.project_screen.ProjectScreen`
--       --    :class:`~auto_ext.ui.screens.setup_drawer.SetupDrawer`
======== ===== ===================================================

Where the old tabs went
-----------------------

* **Run** -- the task picker, the stage row, the jobs spinbox and Run/Cancel
  are the Cells screen's :class:`~auto_ext.ui.widgets.run_bar.RunBar`; the
  live status tree is the per-row stage strip; the embedded log viewer is
  :class:`~auto_ext.ui.widgets.log_view.LogView` mounted in the run bar's log
  slot by this window. The worker trio (``QtProgressReporter`` +
  ``CancelToken`` + ``RunWorker``) was never in ``run_tab``: it lives in
  ``ui/worker.py`` and ``ui/qt_reporter.py``, and the Cells screen builds it
  the same way the tab did.
* **Runs** -- the Runs screen, with the result card rewritten around it.
* **Project** -- split. The render knobs became the Recipes screen; the PDK
  fields became the profile and the workspace paths became
  ``config/workspace.yaml``, and both are edited on the Project screen, whose
  health verdict the Setup drawer shows.
* **Tasks** -- the Cells screen.
* **Templates** -- gone as a concept: templates are catalog state. Editing a
  generated file is ``Recipes -> Edit rendered file``, which stores the diff
  on the recipe (:mod:`auto_ext.ui.patch_capture`).

Where the Project screen fits
-----------------------------

``WorkspaceConfig`` and ``PdkProfile`` were both wholly unreachable until it
existed -- the gap this docstring used to record as deliberate. The screen
holds working copies and this window is what stages them, through the same
``ConfigController.save`` every other screen uses. The Setup drawer keeps the
health verdict and gains a way into the field a failing check is about
(``edit_field_requested``). See ``docs/refactor/PROJECTS_AND_SETUP.md``.

Errors from the controller land in the status bar rather than a modal box:
under X11 forwarding a dialog costs a round trip, and a load failure is
already visible as an empty screen. An error that follows an explicit user
action (Open, Reload, Save) *is* raised as a dialog, because there the user
is waiting for an answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt5.QtWidgets import (
    QFileDialog,
    QInputDialog,
    QMainWindow,
    QMessageBox,
)

from auto_ext.core.errors import AutoExtError
from auto_ext.model.recipe import Recipe, recipe_from_catalog
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.os_open import open_in_os
from auto_ext.ui.screens.cells_screen import CellsScreen
from auto_ext.ui.screens.project_screen import ProjectScreen
from auto_ext.ui.screens.recipes_screen import RecipesScreen
from auto_ext.ui.screens.runs_screen import RunsScreen
from auto_ext.ui.screens.setup_drawer import SetupDrawer
from auto_ext.ui.shell import Shell
from auto_ext.ui.theme import WINDOW_MIN_HEIGHT, WINDOW_MIN_WIDTH, build_qss
from auto_ext.ui.widgets.log_view import LogView

__all__ = ["MainWindow"]


class MainWindow(QMainWindow):
    _TITLE_BASE = "Auto_ext"

    #: ``(page key, nav label, collapsed 3-letter code)`` in rail order.
    #: The codes are given explicitly rather than derived: "Recipes" and
    #: "Runs" share no prefix today, but the shell's default derivation is
    #: first-three-letters and a fourth screen could collide silently.
    _PAGES = (
        ("cells", "Cells", "CEL"),
        ("recipes", "Recipes", "RCP"),
        ("runs", "Runs", "RNS"),
        ("project", "Project", "PRJ"),
    )

    def __init__(
        self,
        *,
        config_dir: Path | None = None,
        auto_ext_root: Path | None = None,
        workarea: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(self._TITLE_BASE)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.resize(1280, 800)
        # One stylesheet for the whole window: the menu bar, the shell, every
        # screen in its stack, and any dialog parented to this window.
        self.setStyleSheet(build_qss())

        #: Every ``config_error`` message this session, oldest first. The
        #: status bar shows the newest; explicit actions check the length to
        #: tell "my Open failed" from "an error was already there".
        self.errors: list[str] = []

        #: True while :meth:`_on_config_loaded` is pushing loaded documents
        #: into the screens, so their change signals do not read as edits.
        self._pushing = False

        self._controller = ConfigController(
            auto_ext_root=auto_ext_root,
            workarea=workarea,
            parent=self,
        )

        shell = Shell(self)
        self._shell = shell

        self._cells = CellsScreen(self._controller, parent=shell)
        self._recipes = RecipesScreen(parent=shell)
        self._runs = RunsScreen(shell)
        self._project = ProjectScreen(shell)
        self._setup = SetupDrawer(shell)
        self._log_view = LogView(shell)
        self._cells.run_bar.set_log_widget(self._log_view)
        # The run bar owns the checkbox and the viewer owns the behaviour, so
        # sync once at construction -- otherwise the two disagree until the
        # user happens to toggle it.
        self._log_view.set_follow(self._cells.run_bar.follows_current_stage())

        widgets: dict[str, Any] = {
            "cells": self._cells,
            "recipes": self._recipes,
            "runs": self._runs,
            "project": self._project,
        }
        for key, label, code in self._PAGES:
            shell.add_page(key, label, widgets[key], code=code)
        shell.set_setup_widget(self._setup)
        shell.set_status(left="idle", right="")
        self.setCentralWidget(shell)

        self._build_menus()
        self._connect()

        if config_dir is not None:
            self._controller.load(config_dir)

    # ---- accessors -------------------------------------------------------

    @property
    def shell(self) -> Shell:
        return self._shell

    @property
    def controller(self) -> ConfigController:
        return self._controller

    @property
    def cells_screen(self) -> CellsScreen:
        return self._cells

    @property
    def recipes_screen(self) -> RecipesScreen:
        return self._recipes

    @property
    def runs_screen(self) -> RunsScreen:
        return self._runs

    @property
    def project_screen(self) -> ProjectScreen:
        return self._project

    @property
    def setup_drawer(self) -> SetupDrawer:
        return self._setup

    @property
    def log_view(self) -> LogView:
        return self._log_view

    # ---- wiring ----------------------------------------------------------

    def _connect(self) -> None:
        controller = self._controller
        controller.config_loaded.connect(self._on_config_loaded)
        controller.config_saved.connect(self._on_config_saved)
        controller.config_error.connect(self._on_config_error)
        controller.dirty_changed.connect(self._on_dirty_changed)
        controller.health_changed.connect(self._on_health_changed)

        cells = self._cells
        cells.cells_changed.connect(self._on_cells_changed)
        cells.status_message.connect(self._set_status)
        cells.edit_rejected.connect(self._set_status)
        cells.import_requested.connect(self._open_init_wizard)
        cells.run_requested.connect(self._on_run_requested)
        cells.save_requested.connect(self.save)
        cells.run_finished.connect(self._on_run_finished)
        cells.log_path_changed.connect(self._log_view.set_active_log)
        cells.open_log_requested.connect(self._open_path)
        cells.run_bar.follow_changed.connect(self._log_view.set_follow)
        cells.selection_changed.connect(self._on_cells_selection_changed)

        recipes = self._recipes
        # Every edit reaches the controller, not only the ones Save is pressed
        # on. Without this the Recipes screen said "unsaved" while the
        # controller was clean, so the window title had no star, File -> Save
        # was greyed out, and closing the window threw the edit away with
        # nothing to warn about -- the screen was the only thing that knew.
        recipes.dirty_changed.connect(self._on_recipe_dirty_changed)
        recipes.save_requested.connect(self._on_recipe_save_requested)
        recipes.revert_requested.connect(self._on_recipe_revert_requested)
        recipes.new_requested.connect(self._on_recipe_new_requested)
        recipes.duplicate_requested.connect(self._on_recipe_duplicate_requested)
        recipes.delete_requested.connect(self._on_recipe_delete_requested)
        recipes.status_changed.connect(self._set_status)
        recipes.edit_rendered_requested.connect(self._on_edit_rendered_requested)
        for signal in (
            recipes.patch_revert_requested,
            recipes.patch_delete_requested,
        ):
            signal.connect(self._stage_current_recipe)
        recipes.patch_revert_all_requested.connect(self._stage_current_recipe)

        runs = self._runs
        runs.status_message.connect(self._set_status)
        runs.log_requested.connect(self._open_path)
        runs.artifact_requested.connect(self._open_path)
        runs.setup_requested.connect(self._open_setup_at)
        runs.rerun_requested.connect(self._on_rerun_requested)

        project = self._project
        project.edited.connect(self._on_project_edited)
        project.save_requested.connect(self._on_project_save_requested)
        project.revert_requested.connect(self._on_project_revert_requested)
        project.open_project_requested.connect(self._open_config_dir)
        project.project_chosen.connect(self._on_project_chosen)
        project.env_import_requested.connect(self._open_env_import)
        project.status_changed.connect(self._set_status)

        setup = self._setup
        setup.recheck_requested.connect(self._on_recheck_requested)
        setup.close_requested.connect(lambda: self._shell.set_setup_open(False))
        setup.override_requested.connect(self._on_override_requested)
        setup.edit_field_requested.connect(self._on_edit_field_requested)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_action = file_menu.addAction("&New project from raws...")
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._open_init_wizard)

        open_action = file_menu.addAction("&Open config directory...")
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_config_dir)

        reload_action = file_menu.addAction("&Reload from disk")
        reload_action.setShortcut("Ctrl+R")
        reload_action.triggered.connect(self._reload_config)

        file_menu.addSeparator()

        self._save_action = file_menu.addAction("&Save")
        self._save_action.setShortcut("Ctrl+S")
        self._save_action.setEnabled(False)
        self._save_action.triggered.connect(self.save)

        self._revert_action = file_menu.addAction("Re&vert pending edits")
        self._revert_action.setEnabled(False)
        self._revert_action.triggered.connect(self._controller.revert)

        file_menu.addSeparator()
        quit_action = file_menu.addAction("&Quit")
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self._on_quit)

        view_menu = self.menuBar().addMenu("&View")
        setup_action = view_menu.addAction("&Setup drawer")
        setup_action.setShortcut("Ctrl+E")
        setup_action.triggered.connect(self._shell.toggle_setup)

        recheck_action = view_menu.addAction("Re-check the &PDK")
        recheck_action.triggered.connect(self._on_recheck_requested)

    # ---- controller slots ------------------------------------------------

    def _on_config_loaded(self, config_dir: object) -> None:
        """Push every loaded document into the screen that owns it.

        ``CellsScreen.set_cells`` emits ``cells_changed`` -- it cannot tell a
        host push from a user edit -- so the push is fenced by
        :attr:`_pushing`. Without the fence, loading a project would stage the
        book it just loaded and the window would open already dirty.
        """

        controller = self._controller
        self._shell.set_config_path(None if config_dir is None else str(config_dir))

        self._pushing = True
        try:
            book = controller.cells
            if book is not None:
                self._cells.set_cells(book)
            self._cells.set_recipe_choices(
                [(r.recipe_id, r.name) for r in controller.recipes]
            )
            self._cells.set_empty_state_hint(
                "" if controller.can_run else self._cannot_run_hint()
            )
            # Before set_recipes: the corner control's item list comes from
            # the profile, and a recipe pushed into an empty list would show
            # its corner as an unknown extra entry.
            self._recipes.set_profile(controller.profile)
            self._recipes.set_recipes(controller.recipes)
            self._project.set_project(
                workspace=controller.workspace,
                profile=controller.profile,
                config_dir="" if config_dir is None else str(config_dir),
                profile_ids=self._profile_ids(),
            )
        finally:
            self._pushing = False

        self._push_recipe_usage()
        self._runs.set_runs_root(controller.runs_root)
        self._controller.refresh_health()

    def _profile_ids(self) -> list[str]:
        """Every profile the loaded config directory offers, for the picker.

        The screen does no I/O, so the set of profiles -- a property of the
        directory around the loaded documents, not of either document -- is
        the window's to supply. The currently-named one is included even when
        its file is missing: a workspace pointing at a profile that is not
        there is exactly the situation the picker has to be able to show.
        """

        controller = self._controller
        directory = controller.profiles_dir
        ids = sorted(p.stem for p in directory.glob("*.yaml")) if directory else []
        workspace = controller.workspace
        if workspace is not None and workspace.pdk_profile not in ids:
            ids.append(workspace.pdk_profile)
        return ids

    def _cannot_run_hint(self) -> str:
        """Why the Run button would not start anything, in one line."""

        controller = self._controller
        if controller.workspace is None:
            return "No project loaded. File -> Open config directory, or New project."
        book = controller.cells
        if book is None or not len(book):
            return "No cells yet. Add a row, or import one from a raw export."
        if controller.profile is None:
            return "config/profiles/ holds no profile that workspace.yaml names."
        return "No recipe on the search path. Create one on the Recipes screen."

    def _on_cells_changed(self, book: object) -> None:
        if self._pushing:
            return
        self._controller.stage_cells(book)  # type: ignore[arg-type]

    def _on_config_saved(self, _config_dir: object) -> None:
        self._set_status("saved")

    def _on_config_error(self, message: str) -> None:
        self.errors.append(message)
        self._set_status(f"error - {message.splitlines()[0]}")

    def _on_dirty_changed(self, dirty: bool) -> None:
        self._cells.set_unsaved(dirty)
        self.setWindowTitle(f"{self._TITLE_BASE}{' *' if dirty else ''}")
        self._shell.set_status(right="unsaved changes" if dirty else "")
        self._save_action.setEnabled(dirty)
        self._revert_action.setEnabled(dirty)

    def _on_health_changed(self, report: object) -> None:
        self._shell.set_health_report(report)  # type: ignore[arg-type]
        self._setup.set_report(report)  # type: ignore[arg-type]

    # ---- run slots -------------------------------------------------------

    def _on_run_requested(self, _request: object) -> None:
        self._shell.set_status(left="running")

    def _on_run_finished(self, _summary: object) -> None:
        """A run that just ended is the one the user wants to look at."""

        self._runs.refresh()
        self._shell.set_status(left="idle")

    def _on_cells_selection_changed(self, keys: object) -> None:
        count = len(tuple(keys or ()))
        self._shell.set_status(
            left="idle" if count == 0 else f"{count} cell(s) selected"
        )

    def _on_rerun_requested(self, entry: object) -> None:
        """Send the user back to Cells with that run's cell selected.

        The Runs screen owns no queue, so this is a navigation, not a
        dispatch: the Run button stays the one place a run starts.
        """

        key = getattr(entry, "task_id", None) or getattr(entry, "cell", None)
        if isinstance(key, str) and key:
            self._cells.set_selected_keys([key])
        self._shell.set_current_page("cells")

    # ---- recipe slots ----------------------------------------------------

    def _stage_current_recipe(self, *_args: object) -> None:
        """Stage whatever the Recipes screen is showing right now.

        The patch signals fire *after* the screen has applied the change to
        its working copy, so re-reading the working copy is the whole
        handler.
        """

        recipe = self._recipes.current_recipe()
        if recipe is not None:
            self._controller.stage_recipe(recipe)

    def _on_recipe_dirty_changed(self, dirty: bool) -> None:
        """Stage the working copy the moment the screen reports an edit.

        Only on the rising edge: the screen also reports *clean*, and it does
        so from ``_load`` while the host is pushing documents in, which would
        otherwise stage the recipe that was just loaded and open the window
        already dirty. Going clean is either a save (the queue is already
        flushed) or a revert (handled by its own slot).
        """

        if dirty and not self._pushing:
            self._stage_current_recipe()

    def _on_recipe_save_requested(self, recipe: object) -> None:
        """The Recipes screen's Save button means Save: stage it AND write it.

        This used to stage only. Two controls were called Save, they did
        different things, and the visible one -- the primary button on the
        screen the user is looking at -- was the one that never reached the
        disk: it queued the recipe and left the screen still showing
        ``unsaved``, so the edit came back missing on the next launch. Only
        ``File -> Save`` wrote anything.

        A ``save()`` here flushes the controller's whole queue, not this
        recipe alone, because the queue is the unit the controller commits
        and there is no per-document write. That matches the window title's
        single dirty star and ``File -> Save``; it does mean a staged Cells
        edit rides along, which is why the status line names what was written.
        """

        if not isinstance(recipe, Recipe):
            return
        self._controller.stage_recipe(recipe)
        self._push_recipe_choices()
        if not self.save():
            return
        # load() inside save() re-pushed every screen and dropped the
        # selection back to the first row; put the user back where they were.
        self._recipes.set_recipes(self._controller.recipes, select=recipe.recipe_id)
        self._set_status(f"saved {recipe.recipe_id}")

    def _on_recipe_revert_requested(self, recipe_id: str) -> None:
        """Throw the working copy away and reload from disk.

        The staged edit goes too. Revert on the screen means "undo what I
        typed", and every keystroke stages now, so the queue entry IS what
        was typed -- keeping it would redraw the screen from the very edit
        being undone, i.e. a Revert button that does nothing.
        ``File -> Revert pending edits`` is still the way to drop the whole
        queue, this one recipe included.
        """

        self._controller.unstage_recipe(recipe_id)
        self._recipes.set_recipes(self._controller.recipes, select=recipe_id)

    def _on_recipe_new_requested(self) -> None:
        recipe = recipe_from_catalog(
            recipe_id=self._unique_recipe_id("new-recipe"), name="New recipe"
        )
        self._controller.stage_recipe(recipe)
        self._recipes.set_recipes(self._controller.recipes, select=recipe.recipe_id)
        self._push_recipe_choices()

    def _on_recipe_duplicate_requested(self, recipe_id: str) -> None:
        source = self._controller.recipe(recipe_id)
        if source is None:
            return
        new_id = self._unique_recipe_id(f"{recipe_id}-copy")
        clone = source.model_copy(
            update={
                "recipe_id": new_id,
                "name": f"{source.name} (copy)",
                "derived_from": source.recipe_id,
            }
        )
        self._controller.stage_recipe(clone)
        self._recipes.set_recipes(self._controller.recipes, select=new_id)
        self._push_recipe_choices()

    def _on_recipe_delete_requested(self, recipe_id: str) -> None:
        self._controller.stage_recipe_deletion(recipe_id)
        self._recipes.set_recipes(self._controller.recipes)
        self._push_recipe_choices()

    def _unique_recipe_id(self, stem: str) -> str:
        taken = set(self._controller.recipe_ids())
        if stem not in taken:
            return stem
        index = 2
        while f"{stem}-{index}" in taken:
            index += 1
        return f"{stem}-{index}"

    def _push_recipe_choices(self) -> None:
        self._cells.set_recipe_choices(
            [(r.recipe_id, r.name) for r in self._controller.recipes]
        )
        self._push_recipe_usage()

    def _push_recipe_usage(self) -> None:
        """``recipe_id -> cells bound to it``, from the Cells screen.

        The binding has no home in ``cells.yaml`` yet (the Cells screen holds
        it), so this is the only place the two screens agree on the count.
        """

        counts: dict[str, int] = {}
        for recipe_id in self._cells.recipe_bindings().values():
            counts[recipe_id] = counts.get(recipe_id, 0) + 1
        self._recipes.set_usage_counts(counts)

    # ---- the escape hatch ------------------------------------------------

    def _on_edit_rendered_requested(self, recipe_id: str) -> None:
        """Render this recipe's first file, let the user edit it, store a patch.

        Everything this needs -- a profile, a cell to render for, and a
        resolvable environment -- is checked up front and reported as one
        sentence naming what is missing, because on a machine without the PDK
        loaded every one of them is a real and expected outcome.
        """

        from auto_ext.ui import patch_capture

        controller = self._controller
        recipe = controller.recipe(recipe_id)
        profile = controller.profile
        workspace = controller.workspace
        book = controller.cells
        workarea = controller.workarea
        missing = []
        if recipe is None:
            missing.append("the recipe")
        if profile is None:
            missing.append("a PDK profile")
        if workspace is None:
            missing.append("workspace.yaml")
        if book is None or not len(book):
            missing.append("at least one cell")
        if workarea is None:
            missing.append("a workarea")
        if missing:
            self._warn(
                "Cannot render this recipe",
                "Editing the generated file needs " + ", ".join(missing) + ".",
            )
            return
        assert recipe is not None and profile is not None  # narrowed above
        assert workspace is not None and book is not None and workarea is not None

        cell = self._preferred_cell(book)
        plans = patch_capture.editable_targets(recipe)
        if not plans:
            self._warn(
                "Nothing to edit",
                f"Recipe {recipe_id!r} declares no stage that generates a file.",
            )
            return
        # Env first, picker second. Asking the user which of four files to
        # edit and only then telling them the PDK is not sourced wastes the
        # one decision they made; the refusal does not depend on the answer.
        resolution = patch_capture.resolve_render_env(profile, workspace)
        if resolution.missing:
            # Refused rather than rendered: an unset variable substitutes as
            # the empty string, so the preview would succeed and show a file
            # full of paths like "/cds/verify/QCI_PATH_inv" -- and the edit
            # captured against it would be anchored to nonsense.
            self._warn(
                "The PDK environment is not loaded",
                "Rendering this recipe needs these variables, and this shell "
                "has none of them:\n\n"
                + "\n".join(f"  {name}" for name in resolution.missing)
                + "\n\nSource the PDK setup and re-check in Setup, or pin the "
                "values in the profile's env_overrides.",
            )
            return

        plan = self._choose_plan(plans, recipe)
        if plan is None:
            return
        try:
            preview = patch_capture.build_preview(
                plan,
                recipe=recipe,
                profile=profile,
                workspace=workspace,
                cell=cell,
                resolved_env=resolution.resolved,
                workarea=workarea,
            )
        except AutoExtError as exc:
            hint = (
                f"\n\nUnresolved: {', '.join(resolution.missing)}"
                if resolution.missing
                else ""
            )
            self._warn("Cannot render this recipe", f"{exc}{hint}")
            return

        from auto_ext.ui.widgets.rendered_editor import RenderedFileEditor

        dialog = RenderedFileEditor(preview, subtitle=cell.cell, parent=self)
        dialog.exec_()
        if not dialog.saved:
            return
        try:
            patch = patch_capture.capture(
                preview,
                dialog.accepted_text(),
                recipe=recipe,
                profile=profile,
            )
        except AutoExtError as exc:
            self._warn("Could not store the edit", str(exc))
            return
        if patch is None:
            self._set_status("no change - nothing stored")
            return
        updated = patch_capture.with_patch(recipe, patch)
        self._controller.stage_recipe(updated)
        # Write it. Staging alone left the controller dirty while the Recipes
        # screen -- reloaded from the controller a line below -- went back to
        # showing "saved" with its Save button DISABLED, so the screen said
        # the edit was safe and offered no way to make it so. The user pressed
        # "Store this edit" in a modal; that is the commit gesture, and the
        # screen's own Save button means write since this round.
        written = self.save()
        self._recipes.set_recipes(self._controller.recipes, select=recipe_id)
        count = len(patch.hunks)
        self._set_status(
            f"stored {count} manual edit(s) on {recipe_id}"
            + ("" if written else " - File -> Save writes them to the recipe file")
        )

    @staticmethod
    def _preferred_cell(book: Any) -> Any:
        """The cell a render preview uses: the first enabled row, else row 1.

        A patch is masked against this cell's values, so which row is picked
        changes nothing about what gets stored -- only what the user reads
        while editing.
        """

        enabled = book.enabled_cells()
        return enabled[0] if enabled else book.cells[0]

    def _choose_plan(self, plans: list[Any], recipe: Recipe) -> Any | None:
        """Ask which generated file to edit. ``None`` means the user cancelled.

        This used to be :meth:`_preferred_plan`, which took ``plans[0]``
        without asking. A recipe running the whole flow generates four files
        -- ``si.env``, ``lvs.qci``, ``quantus.ext.cmd``, ``jivaro.xml`` --
        and ``plans[0]`` is always ``si.env`` because the list is in stage
        order, so the button opened the si netlister runset no matter which
        file the user meant and offered no way to reach the other three.

        One file, no dialog: with a single target there is nothing to choose
        and a modal would be pure friction.
        """

        if len(plans) == 1:
            return plans[0]
        labels = [self._plan_label(plan, recipe) for plan in plans]
        # Preselect the file that already carries manual edits -- with one
        # patch mounted, that is nearly always the one being edited again.
        current = next(
            (
                index
                for index, plan in enumerate(plans)
                if recipe.patch_for(plan.stage, plan.spec.template_id) is not None
            ),
            0,
        )
        index = self._ask_which_plan(recipe, labels, current)
        return None if index is None else plans[index]

    def _ask_which_plan(
        self, recipe: Recipe, labels: list[str], current: int
    ) -> int | None:
        """The modal itself, alone, so a test can answer it in one line.

        Separated from :meth:`_choose_plan` for the same reason
        ``RenderedFileEditor.exec_`` is stubbed rather than driven: a modal
        that nothing answers does not fail a test, it hangs the run.
        """

        chosen, ok = QInputDialog.getItem(
            self,
            "Edit rendered file",
            f"Recipe {recipe.recipe_id!r} generates {len(labels)} files. "
            "Which one do you want to edit?",
            labels,
            current,
            False,
        )
        return labels.index(chosen) if ok else None

    @staticmethod
    def _plan_label(plan: Any, recipe: Recipe) -> str:
        """``quantus.ext.cmd - quantus stage - 2 manual edits``."""

        patch = recipe.patch_for(plan.stage, plan.spec.template_id)
        parts = [str(plan.spec.id.value), f"{plan.stage.value} stage"]
        if patch is not None:
            count = len(patch.hunks)
            parts.append(f"{count} manual edit" + ("" if count == 1 else "s"))
        return "  -  ".join(parts)

    # ---- setup drawer ----------------------------------------------------

    def _open_setup_at(self, hint: str) -> None:
        self._shell.set_setup_open(True)
        if hint:
            self._setup.scroll_to(hint)

    def _on_recheck_requested(self) -> None:
        self._controller.refresh_health(force=True)

    def _on_override_requested(self, name: str, value: str) -> None:
        """Pin one env var into the profile's ``env_overrides``.

        Staged like any other document edit, so the user still has to press
        Save -- a health-check row must not be able to rewrite the PDK
        profile behind their back.
        """

        profile = self._project.profile()
        if profile is None:
            return
        overrides = dict(profile.env_overrides)
        overrides[name] = value
        # Through the Project screen rather than straight to the controller:
        # the screen holds the working copy, and a second copy staged behind
        # its back would disagree with it the moment either was reverted -- and
        # the user would not see the pin they just made in the field that shows
        # pins.
        self._project.apply_edit("env_overrides", overrides)
        self._set_status(f"{name} pinned in the profile - Save to write it")

    # ---- project slots ---------------------------------------------------

    def _on_project_edited(self, workspace: object, profile: object) -> None:
        """Stage whichever of the two objects now differs from what was loaded.

        Every edit, not only the ones Save is pressed on -- see
        :attr:`ProjectScreen.edited`. Staging rather than writing keeps one
        save path for the whole window: the controller renders every pending
        document, refuses on an mtime conflict, and reloads afterwards.
        """

        if self._pushing:
            return
        if workspace is not None:
            self._controller.stage_workspace(workspace)  # type: ignore[arg-type]
        if profile is not None:
            self._controller.stage_profile(profile)  # type: ignore[arg-type]

    def _on_project_save_requested(self, workspace: object, profile: object) -> None:
        """Save what the screen has staged."""

        self._on_project_edited(workspace, profile)
        self.save()

    def _on_project_revert_requested(self) -> None:
        """Throw the screen's working copies away and re-push what is loaded.

        The screen holds working copies the controller has never seen (only
        Save stages them), so reverting is a re-push rather than a controller
        revert -- and the controller is reverted too, because a pinned env
        override staged from the Setup drawer is an edit to the same object.
        """

        self._controller.revert()
        self._push_project()
        self._set_status("project reverted to what is on disk")

    def _push_project(self) -> None:
        controller = self._controller
        self._pushing = True
        try:
            self._project.set_project(
                workspace=controller.workspace,
                profile=controller.profile,
                config_dir=str(controller.config_dir or ""),
                profile_ids=self._profile_ids(),
            )
        finally:
            self._pushing = False

    def set_known_projects(self, projects: list[tuple[str, str]]) -> None:
        """Fill the Project screen's switcher with ``(display name, config dir)``.

        Pushed in rather than read here: the list lives in ``QSettings``, and
        :mod:`auto_ext.ui.app` is the one module that owns that store. A window
        that read it directly would also read the developer's real settings
        file in every GUI test that does not think to isolate it.
        """

        self._project.set_known_projects(projects)

    def _on_project_chosen(self, config_dir: str) -> None:
        """Switch projects from the picker. Same guard as File -> Open."""

        if not self._confirm_discard("Switching project"):
            # The picker is showing the project the user declined to leave, so
            # put the selection back where it was.
            self._project.set_known_projects(self._project.known_projects())
            return
        self._load_and_report(Path(config_dir))

    def _open_env_import(self) -> None:
        """Read this project's environment out of files this project produced.

        The dialog writes nothing. What it hands back goes into the profile's
        ``env_overrides`` through the Project screen, so it is a staged edit
        the user still has to Save -- the same rule the Setup drawer's pin
        follows, and for the same reason: a value read out of a file must not
        rewrite the PDK profile behind the user's back.
        """

        from auto_ext.ui.widgets.env_import_dialog import EnvImportDialog

        profile = self._project.profile()
        if profile is None:
            self._set_status("no profile loaded, so there is nothing to read into")
            return
        dialog = EnvImportDialog(
            profile=profile,
            start_dir=self._controller.workarea or self._controller.config_dir,
            parent=self,
        )
        dialog.values_accepted.connect(self._on_env_values_accepted)
        dialog.exec_()

    def _on_env_values_accepted(self, values: object) -> None:
        accepted = dict(values)  # type: ignore[arg-type]
        if not accepted:
            return
        profile = self._project.profile()
        if profile is None:  # pragma: no cover - the dialog cannot open without one
            return
        overrides = dict(profile.env_overrides)
        overrides.update(accepted)
        self._project.apply_edit("env_overrides", overrides)
        self._shell.set_current_page("project")
        self._set_status(
            f"{len(accepted)} value(s) pinned in the profile - Save to write them"
        )

    def _on_edit_field_requested(self, path: str) -> None:
        """A Setup row asked for the field its fix hint names.

        The drawer says *what* to change; this is the only thing that can show
        the user *where*. A path the screen does not render is reported rather
        than silently ignored -- it means a fix hint and the field inventory
        have drifted apart, which is a bug in one of them.
        """

        self._shell.set_setup_open(False)
        self._shell.set_current_page("project")
        if not self._project.scroll_to(path):
            self._set_status(f"no editor for {path} on the Project screen")

    # ---- file actions ----------------------------------------------------

    def _open_config_dir(self) -> None:
        if not self._confirm_discard("Opening another project"):
            return
        start = str(self._controller.config_dir or Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Open config directory", start, QFileDialog.ShowDirsOnly
        )
        if not chosen:
            return
        self._load_and_report(Path(chosen))

    def _reload_config(self) -> None:
        if self._controller.config_dir is None:
            return
        if not self._confirm_discard("Reloading from disk"):
            return
        self._load_and_report(self._controller.config_dir)

    def _load_and_report(self, config_dir: Path) -> None:
        before = len(self.errors)
        self._controller.load(config_dir)
        if len(self.errors) > before:
            self._warn("Could not load that project", self.errors[-1])

    def save(self) -> bool:
        """Write every staged document. Returns whether anything was written."""

        controller = self._controller
        if not controller.is_dirty:
            return False
        if controller.save():
            return True
        if not controller.has_external_change():
            self._warn("Save failed", self.errors[-1] if self.errors else "unknown")
            return False
        names = "\n".join(str(p) for p in controller.externally_changed_paths())
        choice = QMessageBox.question(
            self,
            "Files changed on disk",
            "These files changed since they were loaded:\n\n"
            f"{names}\n\n"
            "Overwrite them with the pending edits?",
            QMessageBox.Save | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice != QMessageBox.Save:
            return False
        return controller.save(force=True)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Guard a *spontaneous* close; accept a programmatic one.

        Spontaneous means the window manager sent it -- the title-bar X, or
        the desktop asking everything to quit. That is the path where a user
        can lose work without meaning to, so it is the path that asks. A
        programmatic ``close()`` is accepted as given: the only one this app
        makes is ``File -> Quit``, which runs :meth:`request_close` first, and
        a caller that closes the window without asking has said what it wants.

        The distinction is not a testing convenience, though it does keep a
        harness that closes widgets for cleanup from raising a modal nothing
        can answer. It is what ``spontaneous`` is for: the difference between
        "the user reached for the X" and "some code called close()".
        """

        if event.spontaneous() and not self.request_close():
            event.ignore()
            return
        event.accept()

    def _on_quit(self) -> None:
        """File -> Quit: ask first, then close for real."""

        if self.request_close():
            self.close()

    def request_close(self) -> bool:
        """Everything that has to happen before the window may go away.

        Returns whether closing may proceed. Cancels a running batch (after
        asking) and offers to save pending edits. ``File -> Quit`` calls this
        and then closes; the window manager's X reaches it through
        :meth:`closeEvent`.

        Order matters. The run question comes first because a run is the
        expensive thing and cancelling it is the decision the user has to make
        with a clear head; only then is it worth asking about files.
        """

        if self._cells.is_running():
            choice = QMessageBox.question(
                self,
                "A run is still going",
                "Closing cancels it. The stages already finished keep their "
                "output; the one in flight does not.\n\nClose anyway?",
                QMessageBox.Close | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if choice != QMessageBox.Close:
                return False
            if not self._cells.stop_run_and_wait():
                # The runner is inside a subprocess that has not come back.
                # Refuse rather than exit over a live thread.
                self._warn(
                    "The run has not stopped yet",
                    "Cancellation was requested but the runner is still "
                    "inside a tool call. Wait for it to come back, then "
                    "close again.",
                )
                return False
        return self._confirm_discard("Closing the window")

    def _confirm_discard(self, what: str) -> bool:
        """Save / discard / cancel when pending edits would be lost."""

        if not self._controller.is_dirty:
            return True
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            f"{what} discards the pending edits. Save them first?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if choice == QMessageBox.Cancel:
            return False
        if choice == QMessageBox.Save:
            return self.save()
        self._controller.revert()
        return True

    def _open_init_wizard(self) -> None:
        from auto_ext.ui.widgets.init_wizard import InitProjectWizard

        if not self._confirm_discard("Creating a new project"):
            return
        dialog = InitProjectWizard(controller=self._controller, parent=self)
        dialog.accepted_with_load.connect(self._load_and_report)
        dialog.exec_()

    # ---- small helpers ---------------------------------------------------

    def _open_path(self, path: object) -> None:
        if path is None:
            return
        open_in_os(Path(str(path)))

    def _set_status(self, message: str) -> None:
        self._shell.set_status(left=message)

    def _warn(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
