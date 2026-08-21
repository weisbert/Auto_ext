"""Init-project GUI wizard: 4 raw EDA exports in, a v2 project out.

Six pages: Intro / Destination / RawFiles / Preview / Commit / Result.

What it writes
--------------

The v2 file set, under one project root the user picks::

    <root>/config/workspace.yaml       the run's location settings
    <root>/config/cells.yaml           one row per DUT
    <root>/config/resources.yaml       machine resources, kept out of the recipe
    <root>/config/profiles/<id>.yaml   the PDK, as discovered from the raws
    <root>/recipes/<id>.yaml           one recipe carrying the imported settings
    <root>/config/project.yaml         import provenance, see below
    <root>/config/tasks.yaml           import provenance, see below
    <root>/templates/<tool>/imported.* the imported templates

Two steps, and why both
-----------------------

:mod:`auto_ext.core.init_project` reads the raw exports and knows how to turn
them into the v1 pair plus templates. :mod:`auto_ext.migrate` knows how to
turn a v1 pair plus templates into the v2 file set -- it is the code that
reads settings back out of template text, groups them into recipes and builds
the PDK profile. Chaining the two is the whole implementation: there is no
second raw-import path to maintain, and the v2 files a new project gets are
byte-for-byte the ones a migrated project gets.

The v1 pair is what the second step *consumes*, so it exists on disk either
way. It is left there rather than cleaned up because it is the record of what
was imported and from which raw exports -- the generated headers name it --
and because a user who wants the conversion re-run needs its input. Nothing
loads it afterwards: :class:`~auto_ext.ui.config_controller.ConfigController`
reads only the v2 documents, and ``core.config.load_project`` now refuses a
file carrying the retired keys these two still hold.

The preview is real
-------------------

:class:`PreviewPage` runs the whole chain into a throwaway temporary
directory, with the migration in ``write=False`` mode, so the profile id,
the cell rows and the recipe names it shows are the ones that will actually
be written -- not a description of them. Nothing under the destination is
touched until :class:`CommitPage`.

Destination rule
----------------

The default destination is never the current working directory, and a root
that resolves to it is refused. A wizard that defaults to cwd writes a config
tree into whatever directory the shell happened to be in -- usually the
Auto_ext checkout itself.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from auto_ext.core.importer import ImportError as CoreImportError
from auto_ext.core.init_project import InitInputs, InitPreview, commit, dry_run
from auto_ext.migrate import (
    RESOURCES_FILENAME,
    MigrationError,
    MigrationReport,
    migrate_v1_to_v2,
)
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.widgets.drop_zone import DropZone

__all__ = [
    "CONFIG_SUBDIR",
    "RECIPES_SUBDIR",
    "TEMPLATES_SUBDIR",
    "InitProjectWizard",
    "default_project_root",
    "v2_relative_paths",
]

#: Layout of a project root. Matches what ``auto-ext migrate`` writes and what
#: :class:`~auto_ext.ui.config_controller.ConfigController` reads.
CONFIG_SUBDIR = "config"
RECIPES_SUBDIR = "recipes"
TEMPLATES_SUBDIR = "templates"


def default_project_root(controller: ConfigController | None) -> Path:
    """Where a new project lands unless the user says otherwise.

    Never the current working directory: see the module docstring. Uses the
    controller's workarea when there is one, else the user's home.
    """

    base = None
    if controller is not None and controller.workarea is not None:
        base = controller.workarea
    if base is None:
        base = Path.home()
    return Path(base) / "Auto_ext_pro"


def v2_relative_paths(report: MigrationReport) -> list[str]:
    """The files a migration of this report writes, relative to the root.

    Derived from the report rather than hard-coded, so a recipe split into
    two shows as two files. Mirrors ``migrate._write_all``.
    """

    paths = [
        f"{CONFIG_SUBDIR}/profiles/{report.profile.profile_id}.yaml",
        f"{CONFIG_SUBDIR}/workspace.yaml",
        f"{CONFIG_SUBDIR}/cells.yaml",
        f"{CONFIG_SUBDIR}/{RESOURCES_FILENAME}",
    ]
    paths.extend(f"{RECIPES_SUBDIR}/{r.recipe_id}.yaml" for r in report.recipes)
    return paths


# ---- pages ----------------------------------------------------------------


class IntroPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("New Auto_ext project")
        self.setSubTitle(
            "Takes 4 raw EDA exports and writes a project: cell table, PDK "
            "profile, one recipe, and the workspace paths."
        )

        layout = QVBoxLayout(self)
        body = QLabel(
            "<p>Before you start, have the following raw exports ready:</p>"
            "<ul>"
            "<li><b>Calibre</b> - <code>.qci</code> file</li>"
            "<li><b>si</b> - <code>si.env</code> file</li>"
            "<li><b>Quantus</b> - <code>.cmd</code> file</li>"
            "<li><b>Jivaro</b> - <code>.xml</code> file (optional)</li>"
            "</ul>"
            "<p>The wizard reads them, shows you exactly what it found, and "
            "writes nothing until you confirm. Every setting it recovers is "
            "listed on the preview page, including the ones nobody has "
            "checked against a real tool yet.</p>",
            self,
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch(1)


class DestinationPage(QWizardPage):
    """One field: the project root. Everything else hangs off it."""

    def __init__(
        self,
        controller: ConfigController | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setTitle("Choose the project root")
        self.setSubTitle(
            "config/, recipes/ and templates/ are created under this directory."
        )

        self._controller = controller

        root = QVBoxLayout(self)
        form = QFormLayout()

        self._root_edit = QLineEdit(self)
        self._root_edit.setText(str(default_project_root(controller)))
        pick = QPushButton("...", self)
        pick.setMaximumWidth(40)
        pick.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self._root_edit, 1)
        row.addWidget(pick)
        wrap = QWidget(self)
        wrap.setLayout(row)
        form.addRow("project root:", wrap)

        self._force_check = QCheckBox(
            "Overwrite existing files (.bak backups)", self
        )
        form.addRow("", self._force_check)
        root.addLayout(form)

        self._layout_label = QLabel("", self)
        self._layout_label.setTextFormat(Qt.PlainText)
        self._layout_label.setStyleSheet("font-family: monospace; color: #444;")
        root.addWidget(self._layout_label)

        self._banner = QLabel("", self)
        self._banner.setStyleSheet("color: #c00; font-weight: bold;")
        self._banner.setWordWrap(True)
        self._banner.setVisible(False)
        root.addWidget(self._banner)
        root.addStretch(1)

        self.registerField("project_root*", self._root_edit)
        self.registerField("force", self._force_check)

        self._root_edit.textChanged.connect(self._refresh)
        self._refresh()

    # -- helpers ------------------------------------------------------

    def project_root(self) -> Path | None:
        text = self._root_edit.text().strip()
        if not text:
            return None
        try:
            return Path(text).expanduser()
        except (OSError, ValueError):
            return None

    def _pick_dir(self) -> None:
        start = self._root_edit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "Select the project root", start, QFileDialog.ShowDirsOnly
        )
        if path:
            self._root_edit.setText(path)

    def _is_cwd(self, root: Path) -> bool:
        try:
            return root.resolve() == Path.cwd().resolve()
        except OSError:
            return False

    def _refresh(self) -> None:
        root = self.project_root()
        if root is None:
            self._layout_label.setText("")
            self._banner.setVisible(False)
            self.completeChanged.emit()
            return
        self._layout_label.setText(
            "\n".join(
                [
                    f"{root / CONFIG_SUBDIR}",
                    f"{root / RECIPES_SUBDIR}",
                    f"{root / TEMPLATES_SUBDIR}",
                ]
            )
        )
        if self._is_cwd(root):
            self._banner.setText(
                "That is the current working directory. Pick a project "
                "directory of its own - writing a config tree into whatever "
                "directory the shell was in is how the Auto_ext checkout "
                "itself gets one."
            )
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        root = self.project_root()
        return root is not None and not self._is_cwd(root)


class RawFilesPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Select raw EDA exports")
        self.setSubTitle("Drag and drop or click [...] to select; one file per tool.")

        root = QVBoxLayout(self)

        self._calibre_edit = self._build_row(root, "Calibre (*.qci)")
        self._si_edit = self._build_row(root, "si (*.env)")
        self._quantus_edit = self._build_row(root, "Quantus (*.cmd)")
        self._jivaro_edit = self._build_row(root, "Jivaro (*.xml, optional)")

        adv = QGroupBox("Advanced - identity overrides (optional)", self)
        adv.setCheckable(True)
        adv.setChecked(False)
        adv_form = QFormLayout(adv)
        self._cell_edit = QLineEdit(adv)
        self._library_edit = QLineEdit(adv)
        self._layout_view_edit = QLineEdit(adv)
        self._source_view_edit = QLineEdit(adv)
        self._out_file_edit = QLineEdit(adv)
        self._ground_net_edit = QLineEdit(adv)
        adv_form.addRow("cell:", self._cell_edit)
        adv_form.addRow("library:", self._library_edit)
        adv_form.addRow("layout view:", self._layout_view_edit)
        adv_form.addRow("source view:", self._source_view_edit)
        adv_form.addRow("out_file:", self._out_file_edit)
        adv_form.addRow("ground_net:", self._ground_net_edit)
        root.addWidget(adv)

        self._banner = QLabel("", self)
        self._banner.setStyleSheet("color: #c00; font-weight: bold;")
        self._banner.setVisible(False)
        root.addWidget(self._banner)

        self.registerField("raw_calibre*", self._calibre_edit)
        self.registerField("raw_si*", self._si_edit)
        self.registerField("raw_quantus*", self._quantus_edit)
        self.registerField("raw_jivaro", self._jivaro_edit)
        self.registerField("override_cell", self._cell_edit)
        self.registerField("override_library", self._library_edit)
        self.registerField("override_layout_view", self._layout_view_edit)
        self.registerField("override_source_view", self._source_view_edit)
        self.registerField("override_out_file", self._out_file_edit)
        self.registerField("override_ground_net", self._ground_net_edit)

        for edit in (
            self._calibre_edit,
            self._si_edit,
            self._quantus_edit,
            self._jivaro_edit,
        ):
            edit.textChanged.connect(self.completeChanged)
            edit.textChanged.connect(self._refresh_banner)

    def _build_row(self, parent_layout: QVBoxLayout, label: str) -> QLineEdit:
        row = QHBoxLayout()
        cap = QLabel(label, self)
        cap.setMinimumWidth(140)
        edit = QLineEdit(self)
        zone = DropZone("Drop file", self)
        zone.setMaximumHeight(48)
        zone.setMaximumWidth(160)
        zone.path_dropped.connect(lambda p, e=edit: e.setText(str(p)))
        pick = QPushButton("...", self)
        pick.setMaximumWidth(40)
        pick.clicked.connect(lambda _, e=edit, name=label: self._pick_file(e, name))
        clear = QPushButton("x", self)
        clear.setMaximumWidth(40)
        clear.clicked.connect(lambda _, e=edit: e.clear())

        row.addWidget(cap)
        row.addWidget(edit, 1)
        row.addWidget(zone)
        row.addWidget(pick)
        row.addWidget(clear)
        wrap = QWidget(self)
        wrap.setLayout(row)
        parent_layout.addWidget(wrap)
        return edit

    def _pick_file(self, edit: QLineEdit, label: str) -> None:
        start = edit.text() or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, f"Select {label}", start)
        if path:
            edit.setText(path)

    def _refresh_banner(self) -> None:
        # Encoding probe only - the heavy parse happens on PreviewPage.
        for edit in (
            self._calibre_edit,
            self._si_edit,
            self._quantus_edit,
            self._jivaro_edit,
        ):
            text = edit.text().strip()
            if not text:
                continue
            try:
                Path(text).read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self._banner.setText(f"{Path(text).name}: not a UTF-8 file - {exc}")
                self._banner.setVisible(True)
                return
            except OSError as exc:
                self._banner.setText(f"{Path(text).name}: cannot read - {exc}")
                self._banner.setVisible(True)
                return
        self._banner.clear()
        self._banner.setVisible(False)

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        for edit in (self._calibre_edit, self._si_edit, self._quantus_edit):
            text = edit.text().strip()
            if not text or not Path(text).is_file():
                return False
        jivaro = self._jivaro_edit.text().strip()
        return not jivaro or Path(jivaro).is_file()


class PreviewPage(QWizardPage):
    """The real chain, run into a temp directory. Nothing is written here."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Preview")
        self.setSubTitle(
            "Everything below was produced by actually running the import and "
            "the conversion; click Next to write the same thing for real."
        )

        self._preview: InitPreview | None = None
        self._report: MigrationReport | None = None
        self._error: str | None = None

        layout = QVBoxLayout(self)

        self._banner = QLabel("", self)
        self._banner.setStyleSheet("color: #c00; font-weight: bold;")
        self._banner.setVisible(False)
        self._banner.setWordWrap(True)
        layout.addWidget(self._banner)

        self._tabs = QTabWidget(self)

        summary = QWidget(self._tabs)
        summary_layout = QVBoxLayout(summary)

        summary_layout.addWidget(QLabel("Identity (consistent across tools):", summary))
        self._identity_view = self._read_only(summary, 120)
        summary_layout.addWidget(self._identity_view)

        summary_layout.addWidget(QLabel("PDK profile and recipe:", summary))
        self._v2_view = self._read_only(summary, 160)
        summary_layout.addWidget(self._v2_view)

        summary_layout.addWidget(QLabel("Files to be written:", summary))
        self._files_tree = QTreeWidget(summary)
        self._files_tree.setHeaderLabels(["path", "note"])
        summary_layout.addWidget(self._files_tree, 1)

        self._tabs.addTab(summary, "Summary")

        self._checks_view = self._read_only(self._tabs, None)
        self._tabs.addTab(self._checks_view, "Needs confirmation")

        self._yaml_view = self._read_only(self._tabs, None)
        self._tabs.addTab(self._yaml_view, "Generated YAML")

        layout.addWidget(self._tabs, 1)

    @staticmethod
    def _read_only(parent: QWidget, max_height: int | None) -> QPlainTextEdit:
        view = QPlainTextEdit(parent)
        view.setReadOnly(True)
        if max_height is not None:
            view.setMaximumHeight(max_height)
        return view

    # -- lifecycle ----------------------------------------------------

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        self._preview = None
        self._report = None
        self._error = None
        self._banner.setVisible(False)
        self._banner.clear()

        wizard = self.wizard()
        inputs = wizard.build_inputs()  # type: ignore[attr-defined]

        try:
            preview = dry_run(inputs)
        except UnicodeDecodeError as exc:
            self._fail(f"Raw file is not UTF-8: {exc}")
            return
        except OSError as exc:
            self._fail(f"Could not read raw file: {exc}")
            return
        except CoreImportError as exc:
            self._fail(f"Import failed: {exc}")
            return

        self._preview = preview
        wizard._preview = preview  # type: ignore[attr-defined]

        if preview.conflicts:
            lines = [
                "Identity conflicts - go back and resolve via the Advanced overrides:"
            ]
            lines.extend(f"  {c}" for c in preview.conflicts)
            self._banner.setText("\n".join(lines))
            self._banner.setVisible(True)
        else:
            try:
                self._report = _rehearse(inputs)
                wizard._report = self._report  # type: ignore[attr-defined]
            except (MigrationError, OSError) as exc:
                self._fail(f"Conversion to the new schema failed: {exc}")
                return

        self._render(preview, self._report)
        self.completeChanged.emit()

    def _fail(self, message: str) -> None:
        self._error = message
        self._banner.setText(message)
        self._banner.setVisible(True)
        self.completeChanged.emit()

    # -- rendering ----------------------------------------------------

    def _render(self, preview: InitPreview, report: MigrationReport | None) -> None:
        ident = preview.merged_identity
        self._identity_view.setPlainText(
            "\n".join(
                f"{name:>16}: {getattr(ident, name) or '(not detected)'}"
                for name in (
                    "cell",
                    "library",
                    "lvs_layout_view",
                    "lvs_source_view",
                    "out_file",
                    "ground_net",
                )
            )
        )

        if report is None:
            self._v2_view.setPlainText("(resolve the identity conflicts first)")
            self._checks_view.setPlainText("")
        else:
            lines = [
                f"{'pdk profile':>16}: {report.profile.profile_id}"
                f"  ({report.profile.tech_name or 'tech name not detected'})",
                f"{'corners':>16}: "
                + (", ".join(c.name for c in report.profile.corners) or "(none)"),
                f"{'cells':>16}: {len(report.cells)}",
            ]
            for entry in report.cells:
                lines.append(f"{'':>18}{entry.key}")
            for recipe in report.recipes:
                bound = len(report.bindings.get(recipe.recipe_id, []))
                lines.append(
                    f"{'recipe':>16}: {recipe.recipe_id}  ({recipe.name}, "
                    f"{bound} cell(s), stages: "
                    f"{', '.join(s.value for s in recipe.stages)})"
                )
            self._v2_view.setPlainText("\n".join(lines))

            checks = list(report.needs_confirmation())
            checks.extend(report.warnings)
            self._checks_view.setPlainText(
                "\n".join(checks)
                if checks
                else "(nothing flagged - unusual; read the generated YAML anyway)"
            )

        self._files_tree.clear()
        for item in preview.files:
            note = "overwrite -> .bak" if item.will_overwrite else ""
            self._files_tree.addTopLevelItem(QTreeWidgetItem([str(item.path), note]))
        if report is not None:
            root = preview.inputs.output_config_dir.parent
            for rel in v2_relative_paths(report):
                target = root / rel
                note = "already exists - left alone" if target.exists() else ""
                self._files_tree.addTopLevelItem(QTreeWidgetItem([str(target), note]))

        self._yaml_view.setPlainText(
            "### project.yaml (v1, kept for the runner) ###\n"
            + preview.project_yaml_text
            + "\n### tasks.yaml (v1, kept for the runner) ###\n"
            + preview.tasks_yaml_text
        )

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        if self._error is not None or self._preview is None:
            return False
        if self._preview.conflicts:
            return False
        if self._report is None:
            return False
        force = bool(self.wizard().field("force"))  # type: ignore[union-attr]
        if not force and any(f.will_overwrite for f in self._preview.files):
            return False
        return True


class CommitPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Write the project")
        self.setSubTitle("Click Next to start writing to disk.")
        self.setFinalPage(False)

        self._succeeded = False
        self._committing = False

        layout = QVBoxLayout(self)
        self._info_label = QLabel("", self)
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        self._banner = QLabel("", self)
        self._banner.setStyleSheet("color: #c00; font-weight: bold;")
        self._banner.setVisible(False)
        self._banner.setWordWrap(True)
        layout.addWidget(self._banner)

        self._log = QPlainTextEdit(self)
        self._log.setReadOnly(True)
        layout.addWidget(self._log, 1)

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        self._succeeded = False
        self._committing = False
        self._banner.setVisible(False)
        self._banner.clear()
        self._log.clear()

        preview: InitPreview | None = getattr(self.wizard(), "_preview", None)
        if preview is None:
            self._info_label.setText("(no preview - return to the previous page)")
            return
        root = preview.inputs.output_config_dir.parent
        self._info_label.setText(
            f"About to write the project under:\n  {root}\n\nClick Next to begin."
        )
        self.completeChanged.emit()

    def validatePage(self) -> bool:  # noqa: N802 - Qt API
        if self._succeeded:
            return True
        # Re-entrancy guard: _on_progress pumps the Qt event loop, which lets
        # a queued double-click on Next dispatch a second validatePage while
        # the first write is still in flight. That would write every file
        # twice and .bak the fresh copy as if it were stale.
        if self._committing:
            return False
        self._committing = True
        wizard = self.wizard()
        next_btn = wizard.button(QWizard.NextButton) if wizard is not None else None
        if next_btn is not None:
            next_btn.setEnabled(False)
        try:
            preview: InitPreview | None = getattr(wizard, "_preview", None)
            if preview is None:
                self._banner.setText("No preview state to write.")
                self._banner.setVisible(True)
                return False
            root = preview.inputs.output_config_dir.parent
            try:
                commit(preview, progress=self._on_progress)
                self._on_progress("converting to the new schema...")
                report = migrate_v1_to_v2(
                    preview.inputs.output_config_dir / "project.yaml",
                    preview.inputs.output_config_dir / "tasks.yaml",
                    template_root=preview.inputs.output_templates_dir,
                    out_root=root,
                    write=True,
                )
            except (OSError, MigrationError) as exc:
                self._banner.setText(f"Write failed: {exc}")
                self._banner.setVisible(True)
                return False
            wizard._report = report  # type: ignore[attr-defined]
            for path in report.written:
                self._on_progress(f"wrote {path}")
            for path in report.skipped:
                self._on_progress(f"kept existing {path}")
            self._succeeded = True
            self._on_progress(
                f"done - {len(preview.files) + len(report.written)} files written"
            )
            return True
        finally:
            self._committing = False
            if next_btn is not None:
                next_btn.setEnabled(True)

    def isComplete(self) -> bool:  # noqa: N802 - Qt API
        return True

    def _on_progress(self, line: str) -> None:
        self._log.appendPlainText(line)
        QApplication.processEvents()


class ResultPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("Project ready")
        self.setSubTitle("Generation complete.")
        self.setFinalPage(True)

        layout = QVBoxLayout(self)

        self._summary_label = QLabel("", self)
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        self._auto_load_check = QCheckBox(
            "Load this new project in the main window", self
        )
        self._auto_load_check.setChecked(True)
        layout.addWidget(self._auto_load_check)
        self.registerField("auto_load", self._auto_load_check)

        layout.addWidget(QLabel("Files written:", self))
        self._written_view = QPlainTextEdit(self)
        self._written_view.setReadOnly(True)
        layout.addWidget(self._written_view, 1)

        layout.addWidget(QLabel("Check these by hand before the first run:", self))
        self._checks_view = QPlainTextEdit(self)
        self._checks_view.setReadOnly(True)
        self._checks_view.setMaximumHeight(140)
        layout.addWidget(self._checks_view)

    def initializePage(self) -> None:  # noqa: N802 - Qt API
        preview: InitPreview | None = getattr(self.wizard(), "_preview", None)
        report: MigrationReport | None = getattr(self.wizard(), "_report", None)
        if preview is None:
            self._summary_label.setText("(no preview)")
            return
        root = preview.inputs.output_config_dir.parent
        written = [str(f.path) for f in preview.files]
        if report is not None:
            written.extend(str(p) for p in report.written)
        self._summary_label.setText(
            f"Project generated under {root} - {len(written)} files written."
        )
        self._written_view.setPlainText("\n".join(written))

        lines: list[str] = []
        if report is not None:
            lines.extend(report.needs_confirmation())
            lines.extend(report.warnings)
        if preview.constants.unclassified:
            lines.append("")
            lines.append("Unclassified tokens from the raw exports:")
            lines.extend(
                f"  {u.tool:<8} line {u.token.line:>3}: {u.token.value!r} "
                f"(category: {u.token.category})"
                for u in preview.constants.unclassified
            )
        self._checks_view.setPlainText("\n".join(lines) if lines else "(none)")


# ---- helpers --------------------------------------------------------------


def _rehearse(inputs: InitInputs) -> MigrationReport:
    """Run the whole chain into a temp directory and report the outcome.

    ``write=False`` on the migration, and every path under a directory that
    is deleted on the way out, so the preview cannot touch the destination
    even if the user backs out.
    """

    with tempfile.TemporaryDirectory(prefix="auto_ext_init_") as tmp:
        root = Path(tmp)
        staged = dataclasses.replace(
            inputs,
            output_config_dir=root / CONFIG_SUBDIR,
            output_templates_dir=root / TEMPLATES_SUBDIR,
            force=True,
        )
        commit(dry_run(staged))
        return migrate_v1_to_v2(
            staged.output_config_dir / "project.yaml",
            staged.output_config_dir / "tasks.yaml",
            template_root=staged.output_templates_dir,
            out_root=root / "out",
            write=False,
        )


# ---- wizard ---------------------------------------------------------------


class InitProjectWizard(QWizard):
    """Modal wizard: raw exports in, a loadable v2 project out.

    Emits :attr:`accepted_with_load` with the new ``config`` directory when
    the user finishes with the auto-load box ticked (the default). The owner
    (:class:`~auto_ext.ui.main_window.MainWindow`) feeds that path into
    :meth:`ConfigController.load`.
    """

    #: Emitted on accept iff the ResultPage's auto-load box is checked.
    accepted_with_load = pyqtSignal(object)  # Path

    def __init__(
        self,
        controller: ConfigController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Auto_ext project")
        self.setModal(True)
        self.resize(900, 720)
        self.setOption(QWizard.IndependentPages, False)
        self.setOption(QWizard.HaveFinishButtonOnEarlyPages, False)

        self._controller = controller
        self._preview: InitPreview | None = None
        self._report: MigrationReport | None = None

        self._intro = IntroPage(self)
        self._destination = DestinationPage(controller, self)
        self._raw_files = RawFilesPage(self)
        self._preview_page = PreviewPage(self)
        self._commit_page = CommitPage(self)
        self._result = ResultPage(self)

        self.addPage(self._intro)
        self.addPage(self._destination)
        self.addPage(self._raw_files)
        self.addPage(self._preview_page)
        self.addPage(self._commit_page)
        self.addPage(self._result)

        self.accepted.connect(self._on_accepted)

    # ---- public helpers ----------------------------------------------

    @property
    def report(self) -> MigrationReport | None:
        """The conversion report, once the preview or the commit produced one."""

        return self._report

    def project_root(self) -> Path | None:
        """The destination the user picked, or ``None`` while it is unset."""

        return self._destination.project_root()

    def build_inputs(self) -> InitInputs:
        """Translate the collected fields into :class:`InitInputs`.

        ``output_config_dir`` / ``output_templates_dir`` are derived from the
        one project root rather than asked for separately: the migration
        writes ``recipes/`` next to ``config/``, so the two directories were
        never independent.
        """

        def _text(name: str) -> str | None:
            value = str(self.field(name) or "").strip()
            return value or None

        def _path(name: str) -> Path | None:
            value = _text(name)
            return Path(value) if value else None

        root = self.project_root()
        calibre = _path("raw_calibre")
        si = _path("raw_si")
        quantus = _path("raw_quantus")
        if root is None or calibre is None or si is None or quantus is None:
            raise RuntimeError("required wizard fields are unset")
        return InitInputs(
            raw_calibre=calibre,
            raw_si=si,
            raw_quantus=quantus,
            raw_jivaro=_path("raw_jivaro"),
            output_config_dir=root / CONFIG_SUBDIR,
            output_templates_dir=root / TEMPLATES_SUBDIR,
            cell_override=_text("override_cell"),
            library_override=_text("override_library"),
            layout_view_override=_text("override_layout_view"),
            source_view_override=_text("override_source_view"),
            out_file_override=_text("override_out_file"),
            ground_net_override=_text("override_ground_net"),
            force=bool(self.field("force")),
        )

    def _on_accepted(self) -> None:
        if self._preview is None:
            return
        if not bool(self.field("auto_load")):
            return
        self.accepted_with_load.emit(self._preview.inputs.output_config_dir)
