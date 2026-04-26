"""Init-project GUI wizard (Phase 5.7).

PyQt5 :class:`QWizard` around :mod:`auto_ext.core.init_project`. Six
pages: Intro / Destination / RawFiles / Preview / Commit / Result.

v1 simplifications (locked 2026-04-26):
- Synchronous commit, no QThread / CancelToken / rollback.
- No QSettings "don't show intro again" toggle.
- Preview page has 2 tabs: 概要 + 生成的 yaml.
- Defaults output_*_dir to ``Path.home()/Auto_ext_pro/...`` when the
  controller has no workarea.
"""

from __future__ import annotations

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
from auto_ext.ui.config_controller import ConfigController
from auto_ext.ui.widgets.drop_zone import DropZone


def _default_output_dir(controller: ConfigController | None, sub: str) -> Path:
    if controller is not None and controller.workarea is not None:
        return controller.workarea / "Auto_ext_pro" / sub
    return Path.home() / "Auto_ext_pro" / sub


# ---- pages ----------------------------------------------------------------


class IntroPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("新建 Auto_ext 项目")
        self.setSubTitle(
            "本向导基于 auto-ext init-project 命令，从 4 份 raw EDA 导出文件 "
            "生成 project.yaml + tasks.yaml + 4 个 imported 模板。"
        )

        layout = QVBoxLayout(self)
        body = QLabel(
            "<p>开始之前，请准备好以下原始导出：</p>"
            "<ul>"
            "<li><b>Calibre</b> — <code>.qci</code> 文件</li>"
            "<li><b>si</b> — <code>si.env</code> 文件</li>"
            "<li><b>Quantus</b> — <code>.cmd</code> 文件</li>"
            "<li><b>Jivaro</b> — <code>.xml</code> 文件 (可选)</li>"
            "</ul>"
            "<p>向导将依次让你选择输出目录、原始文件，预览要生成的内容，"
            "最后写盘。中途可随时返回上一步修改。</p>"
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        layout.addStretch(1)


class DestinationPage(QWizardPage):
    def __init__(
        self,
        controller: ConfigController | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setTitle("选择输出目录")
        self.setSubTitle("project.yaml + tasks.yaml 与 imported 模板的写盘位置。")

        self._controller = controller

        form = QFormLayout(self)

        self._cfg_edit = QLineEdit(self)
        self._cfg_edit.setText(str(_default_output_dir(controller, "config")))
        cfg_pick = QPushButton("…", self)
        cfg_pick.setMaximumWidth(40)
        cfg_pick.clicked.connect(lambda: self._pick_dir(self._cfg_edit))
        cfg_row = QHBoxLayout()
        cfg_row.addWidget(self._cfg_edit, 1)
        cfg_row.addWidget(cfg_pick)
        cfg_wrap = QWidget(self)
        cfg_wrap.setLayout(cfg_row)
        form.addRow("output_config_dir:", cfg_wrap)

        self._tpl_edit = QLineEdit(self)
        self._tpl_edit.setText(str(_default_output_dir(controller, "templates")))
        tpl_pick = QPushButton("…", self)
        tpl_pick.setMaximumWidth(40)
        tpl_pick.clicked.connect(lambda: self._pick_dir(self._tpl_edit))
        tpl_row = QHBoxLayout()
        tpl_row.addWidget(self._tpl_edit, 1)
        tpl_row.addWidget(tpl_pick)
        tpl_wrap = QWidget(self)
        tpl_wrap.setLayout(tpl_row)
        form.addRow("output_templates_dir:", tpl_wrap)

        self._force_check = QCheckBox(
            "覆盖已有文件 (.bak 备份)", self
        )
        form.addRow("", self._force_check)

        self.registerField("output_config_dir*", self._cfg_edit)
        self.registerField("output_templates_dir*", self._tpl_edit)
        self.registerField("force", self._force_check)

        self._cfg_edit.textChanged.connect(self.completeChanged)
        self._tpl_edit.textChanged.connect(self.completeChanged)

    def _pick_dir(self, edit: QLineEdit) -> None:
        start = edit.text() or str(Path.home())
        path = QFileDialog.getExistingDirectory(
            self, "选择目录", start, QFileDialog.ShowDirsOnly
        )
        if path:
            edit.setText(path)

    def isComplete(self) -> bool:  # noqa: N802 — Qt API
        return bool(self._cfg_edit.text().strip()) and bool(
            self._tpl_edit.text().strip()
        )


class RawFilesPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("选择原始 EDA 导出")
        self.setSubTitle("拖放或点击 […] 选择，每个工具一个文件。")

        root = QVBoxLayout(self)

        self._calibre_edit = self._build_row(root, "Calibre (*.qci)", required=True)
        self._si_edit = self._build_row(root, "si (*.env)", required=True)
        self._quantus_edit = self._build_row(
            root, "Quantus (*.cmd)", required=True
        )
        self._jivaro_edit = self._build_row(
            root, "Jivaro (*.xml, 可选)", required=False
        )

        adv = QGroupBox("Advanced — 身份覆盖 (可选)", self)
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
        adv_form.addRow("lvs_layout_view:", self._layout_view_edit)
        adv_form.addRow("lvs_source_view:", self._source_view_edit)
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

    def _build_row(
        self, parent_layout: QVBoxLayout, label: str, *, required: bool
    ) -> QLineEdit:
        row = QHBoxLayout()
        cap = QLabel(label, self)
        cap.setMinimumWidth(140)
        edit = QLineEdit(self)
        zone = DropZone("拖入文件", self)
        zone.setMaximumHeight(48)
        zone.setMaximumWidth(160)
        zone.path_dropped.connect(lambda p, e=edit: e.setText(str(p)))
        pick = QPushButton("…", self)
        pick.setMaximumWidth(40)
        pick.clicked.connect(lambda _, e=edit, l=label: self._pick_file(e, l))
        clear = QPushButton("✕", self)
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
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择 {label}", start
        )
        if path:
            edit.setText(path)

    def _refresh_banner(self) -> None:
        # Encoding probe only — heavy parse happens on PreviewPage.
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
                self._banner.setText(f"{Path(text).name}: 不是 UTF-8 文件 — {exc}")
                self._banner.setVisible(True)
                return
            except OSError as exc:
                self._banner.setText(f"{Path(text).name}: 无法读取 — {exc}")
                self._banner.setVisible(True)
                return
        self._banner.clear()
        self._banner.setVisible(False)

    def isComplete(self) -> bool:  # noqa: N802 — Qt API
        for edit in (self._calibre_edit, self._si_edit, self._quantus_edit):
            text = edit.text().strip()
            if not text:
                return False
            p = Path(text)
            if not p.is_file():
                return False
        # Jivaro optional, but if supplied must exist.
        jiv = self._jivaro_edit.text().strip()
        if jiv and not Path(jiv).is_file():
            return False
        return True


class PreviewPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("预览")
        self.setSubTitle("检查检测到的身份与即将生成的内容；点击下一步以写盘。")

        self._preview: InitPreview | None = None
        self._error: str | None = None

        layout = QVBoxLayout(self)

        self._banner = QLabel("", self)
        self._banner.setStyleSheet("color: #c00; font-weight: bold;")
        self._banner.setVisible(False)
        self._banner.setWordWrap(True)
        layout.addWidget(self._banner)

        self._tabs = QTabWidget(self)

        # --- Tab 1: 概要 ---
        summary_widget = QWidget(self._tabs)
        summary_layout = QVBoxLayout(summary_widget)

        summary_layout.addWidget(QLabel("身份 (跨工具一致):", summary_widget))
        self._identity_view = QPlainTextEdit(summary_widget)
        self._identity_view.setReadOnly(True)
        self._identity_view.setMaximumHeight(120)
        summary_layout.addWidget(self._identity_view)

        summary_layout.addWidget(QLabel("ProjectConstants:", summary_widget))
        self._constants_view = QPlainTextEdit(summary_widget)
        self._constants_view.setReadOnly(True)
        self._constants_view.setMaximumHeight(120)
        summary_layout.addWidget(self._constants_view)

        summary_layout.addWidget(QLabel("Unclassified tokens:", summary_widget))
        self._unclassified_view = QPlainTextEdit(summary_widget)
        self._unclassified_view.setReadOnly(True)
        self._unclassified_view.setMaximumHeight(100)
        summary_layout.addWidget(self._unclassified_view)

        summary_layout.addWidget(QLabel("将要写入的文件:", summary_widget))
        self._files_tree = QTreeWidget(summary_widget)
        self._files_tree.setHeaderLabels(["路径", "覆盖?"])
        summary_layout.addWidget(self._files_tree, 1)

        self._tabs.addTab(summary_widget, "概要")

        # --- Tab 2: 生成的 yaml ---
        self._yaml_view = QPlainTextEdit(self._tabs)
        self._yaml_view.setReadOnly(True)
        self._tabs.addTab(self._yaml_view, "生成的 yaml")

        layout.addWidget(self._tabs, 1)

    def initializePage(self) -> None:  # noqa: N802 — Qt API
        self._preview = None
        self._error = None
        self._banner.setVisible(False)
        self._banner.clear()

        wiz = self.wizard()
        inputs = wiz.build_inputs()  # type: ignore[attr-defined]

        try:
            preview = dry_run(inputs)
        except UnicodeDecodeError as exc:
            self._error = f"原始文件不是 UTF-8: {exc}"
            self._banner.setText(self._error)
            self._banner.setVisible(True)
            self.completeChanged.emit()
            return
        except OSError as exc:
            self._error = f"无法读取原始文件: {exc}"
            self._banner.setText(self._error)
            self._banner.setVisible(True)
            self.completeChanged.emit()
            return
        except CoreImportError as exc:
            self._error = f"导入失败: {exc}"
            self._banner.setText(self._error)
            self._banner.setVisible(True)
            self.completeChanged.emit()
            return

        self._preview = preview
        wiz._preview = preview  # type: ignore[attr-defined]

        if preview.conflicts:
            lines = ["身份冲突 — 请返回上一步用 Advanced overrides 解决:"]
            lines.extend(f"  {c}" for c in preview.conflicts)
            self._banner.setText("\n".join(lines))
            self._banner.setVisible(True)

        self._render_summary(preview)
        self._render_yaml(preview)
        self.completeChanged.emit()

    def _render_summary(self, preview: InitPreview) -> None:
        ident = preview.merged_identity
        ident_text = "\n".join(
            f"{name:>16}: {getattr(ident, name) or '(未检测)'}"
            for name in (
                "cell",
                "library",
                "lvs_layout_view",
                "lvs_source_view",
                "out_file",
                "ground_net",
            )
        )
        self._identity_view.setPlainText(ident_text)

        c = preview.constants
        const_text = "\n".join(
            f"{name:>20}: {value or '(未检测)'}"
            for name, value in (
                ("tech_name", c.tech_name),
                ("pdk_subdir", c.pdk_subdir),
                ("project_subdir", c.project_subdir),
                ("lvs_runset_version", c.lvs_runset_version),
                ("qrc_runset_version", c.qrc_runset_version),
            )
        )
        self._constants_view.setPlainText(const_text)

        if preview.constants.unclassified:
            uncl_lines = [
                f"{u.tool:<8} line {u.token.line:>3}: {u.token.value!r} "
                f"(category: {u.token.category})"
                for u in preview.constants.unclassified
            ]
            self._unclassified_view.setPlainText("\n".join(uncl_lines))
        else:
            self._unclassified_view.setPlainText("(none — clean import)")

        self._files_tree.clear()
        for f in preview.files:
            item = QTreeWidgetItem(
                [str(f.path), "覆盖 → .bak" if f.will_overwrite else ""]
            )
            self._files_tree.addTopLevelItem(item)

    def _render_yaml(self, preview: InitPreview) -> None:
        text = (
            "### project.yaml ###\n"
            + preview.project_yaml_text
            + "\n### tasks.yaml ###\n"
            + preview.tasks_yaml_text
        )
        self._yaml_view.setPlainText(text)

    def isComplete(self) -> bool:  # noqa: N802 — Qt API
        if self._error is not None:
            return False
        if self._preview is None:
            return False
        if self._preview.conflicts:
            return False
        # If force is unset and any will_overwrite is True, block here too —
        # mirrors the CLI's pre-commit refusal.
        wiz = self.wizard()
        force = bool(wiz.field("force"))  # type: ignore[union-attr]
        if not force and any(f.will_overwrite for f in self._preview.files):
            return False
        return True


class CommitPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("写入项目文件")
        self.setSubTitle("点击下一步开始写盘")

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

    def initializePage(self) -> None:  # noqa: N802 — Qt API
        self._succeeded = False
        self._committing = False
        self._banner.setVisible(False)
        self._banner.clear()
        self._log.clear()

        preview: InitPreview | None = getattr(self.wizard(), "_preview", None)
        if preview is None:
            self._info_label.setText("(no preview — return to previous page)")
            return
        cfg = preview.inputs.output_config_dir
        tpl = preview.inputs.output_templates_dir
        self._info_label.setText(
            f"即将写入 {len(preview.files)} 个文件到:\n"
            f"  config: {cfg}\n"
            f"  templates: {tpl}\n\n"
            f"点击下一步开始。"
        )
        self.completeChanged.emit()

    def validatePage(self) -> bool:  # noqa: N802 — Qt API
        if self._succeeded:
            return True
        # Re-entrancy guard: commit() pumps the Qt event loop via
        # QApplication.processEvents() in _on_progress, which lets a
        # queued double-click on Next dispatch a second validatePage()
        # call while the first commit is mid-flight. That second call
        # would re-enter commit() and corrupt freshly-written files
        # (write twice, .bak the new copy as "stale"). Reject reentry.
        if self._committing:
            return False
        self._committing = True
        wiz = self.wizard()
        next_btn = wiz.button(QWizard.NextButton) if wiz is not None else None
        if next_btn is not None:
            next_btn.setEnabled(False)
        try:
            preview: InitPreview | None = getattr(wiz, "_preview", None)
            if preview is None:
                self._banner.setText("无 preview 状态可写盘。")
                self._banner.setVisible(True)
                return False
            try:
                commit(preview, progress=self._on_progress)
            except OSError as exc:
                self._banner.setText(f"写盘失败: {exc}")
                self._banner.setVisible(True)
                return False
            self._succeeded = True
            self._on_progress(f"✓ 完成，共写入 {len(preview.files)} 个文件")
            return True
        finally:
            self._committing = False
            if next_btn is not None:
                next_btn.setEnabled(True)

    def isComplete(self) -> bool:  # noqa: N802 — Qt API
        return True

    def _on_progress(self, line: str) -> None:
        self._log.appendPlainText(line)
        QApplication.processEvents()


class ResultPage(QWizardPage):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTitle("项目已就绪")
        self.setSubTitle("生成完成。")
        self.setFinalPage(True)

        layout = QVBoxLayout(self)

        self._summary_label = QLabel("", self)
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        self._auto_load_check = QCheckBox(
            "在主窗口中加载这个新项目", self
        )
        self._auto_load_check.setChecked(True)
        layout.addWidget(self._auto_load_check)
        self.registerField("auto_load", self._auto_load_check)

        layout.addWidget(QLabel("已写入文件:", self))
        self._written_view = QPlainTextEdit(self)
        self._written_view.setReadOnly(True)
        layout.addWidget(self._written_view, 1)

        layout.addWidget(QLabel("Unclassified tokens (建议手动复核):", self))
        self._unclassified_view = QPlainTextEdit(self)
        self._unclassified_view.setReadOnly(True)
        self._unclassified_view.setMaximumHeight(120)
        layout.addWidget(self._unclassified_view)

    def initializePage(self) -> None:  # noqa: N802 — Qt API
        preview: InitPreview | None = getattr(self.wizard(), "_preview", None)
        if preview is None:
            self._summary_label.setText("(no preview)")
            return
        self._summary_label.setText(
            f"✓ 项目骨架生成成功 — "
            f"{len(preview.files)} 个文件已写入到 "
            f"{preview.inputs.output_config_dir} 与 "
            f"{preview.inputs.output_templates_dir}。"
        )
        self._written_view.setPlainText(
            "\n".join(str(f.path) for f in preview.files)
        )
        if preview.constants.unclassified:
            uncl_lines = [
                f"{u.tool:<8} line {u.token.line:>3}: {u.token.value!r} "
                f"(category: {u.token.category})"
                for u in preview.constants.unclassified
            ]
            self._unclassified_view.setPlainText("\n".join(uncl_lines))
        else:
            self._unclassified_view.setPlainText("(none)")


# ---- wizard ---------------------------------------------------------------


class InitProjectWizard(QWizard):
    """Modal wizard that wraps :func:`auto_ext.core.init_project.commit`.

    Emits :attr:`accepted_with_load` with the newly written
    ``output_config_dir`` when the user finishes the wizard with the
    "auto-load" checkbox enabled (default). The owner (MainWindow) is
    expected to feed that path into :meth:`ConfigController.load`.
    """

    #: Emitted on accept iff the ResultPage's auto-load box is checked.
    accepted_with_load = pyqtSignal(object)  # Path

    def __init__(
        self,
        controller: ConfigController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建 Auto_ext 项目")
        self.setModal(True)
        self.resize(900, 720)
        self.setOption(QWizard.IndependentPages, False)
        self.setOption(QWizard.HaveFinishButtonOnEarlyPages, False)

        self._controller = controller
        self._preview: InitPreview | None = None

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

    def build_inputs(self) -> InitInputs:
        """Translate the wizard's collected fields into :class:`InitInputs`.

        Called by :meth:`PreviewPage.initializePage` to drive ``dry_run``.
        """

        def _path(name: str) -> Path | None:
            text = str(self.field(name) or "").strip()
            return Path(text) if text else None

        def _str(name: str) -> str | None:
            text = str(self.field(name) or "").strip()
            return text or None

        cfg = _path("output_config_dir")
        tpl = _path("output_templates_dir")
        cal = _path("raw_calibre")
        si = _path("raw_si")
        qua = _path("raw_quantus")
        if cfg is None or tpl is None or cal is None or si is None or qua is None:
            raise RuntimeError("required wizard fields are unset")
        return InitInputs(
            raw_calibre=cal,
            raw_si=si,
            raw_quantus=qua,
            raw_jivaro=_path("raw_jivaro"),
            output_config_dir=cfg,
            output_templates_dir=tpl,
            cell_override=_str("override_cell"),
            library_override=_str("override_library"),
            layout_view_override=_str("override_layout_view"),
            source_view_override=_str("override_source_view"),
            out_file_override=_str("override_out_file"),
            ground_net_override=_str("override_ground_net"),
            force=bool(self.field("force")),
        )

    def _on_accepted(self) -> None:
        if self._preview is None:
            return
        if not bool(self.field("auto_load")):
            return
        self.accepted_with_load.emit(self._preview.inputs.output_config_dir)


__all__ = ["InitProjectWizard"]
