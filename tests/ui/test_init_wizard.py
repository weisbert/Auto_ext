"""Tests for :class:`auto_ext.ui.widgets.init_wizard.InitProjectWizard`.

Phase 5.7 GUI tests. Mirror the shape of ``test_diff_editor.py``: each
test constructs a wizard with an ephemeral ``ConfigController``, drives
it via ``qtbot``, and asserts on the side effects.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QMimeData, QPointF, QUrl, Qt  # noqa: E402
from PyQt5.QtGui import QDropEvent  # noqa: E402

from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.widgets.init_wizard import InitProjectWizard  # noqa: E402


# ---- fixtures --------------------------------------------------------------


@pytest.fixture
def raw_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "raw"


def _drop_file(zone, path: Path) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    pos = QPointF(zone.rect().center())
    event = QDropEvent(pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    zone.dropEvent(event)


def _make_wizard(qtbot, controller: ConfigController | None = None) -> InitProjectWizard:
    wiz = InitProjectWizard(controller=controller)
    qtbot.addWidget(wiz)
    # Force the wizard to land on its first page; QWizard.currentId() is -1
    # until either show() / restart() / exec_() runs.
    wiz.restart()
    return wiz


def _drive_to_preview(
    wiz: InitProjectWizard,
    raw_dir: Path,
    out_root: Path,
    *,
    cell_override: str | None = None,
    force: bool = False,
) -> None:
    """Walk Intro → Destination → RawFiles → Preview, populating fields."""
    # Page 0 (Intro): no fields, just advance.
    assert wiz.currentId() == 0
    wiz.next()
    # Page 1 (Destination)
    dest = wiz._destination
    dest._cfg_edit.setText(str(out_root / "config"))
    dest._tpl_edit.setText(str(out_root / "templates"))
    if force:
        dest._force_check.setChecked(True)
    wiz.next()
    # Page 2 (RawFiles)
    raw_page = wiz._raw_files
    raw_page._calibre_edit.setText(str(raw_dir / "calibre_sample.qci"))
    raw_page._si_edit.setText(str(raw_dir / "si_sample.env"))
    raw_page._quantus_edit.setText(str(raw_dir / "quantus_sample.cmd"))
    raw_page._jivaro_edit.setText(str(raw_dir / "jivaro_sample.xml"))
    if cell_override is not None:
        raw_page._cell_edit.setText(cell_override)
    wiz.next()
    # Now on Page 3 (Preview); initializePage already ran.


# ---- 1. wizard constructs --------------------------------------------------


def test_wizard_constructs_with_intro_page(qtbot) -> None:
    wiz = _make_wizard(qtbot)
    assert wiz.currentId() == 0
    assert wiz._intro.title()


# ---- 2. destination defaults via controller workarea ----------------------


def test_destination_page_default_paths(qtbot, tmp_path: Path) -> None:
    workarea = tmp_path / "wa"
    workarea.mkdir()
    controller = ConfigController(workarea=workarea)
    wiz = _make_wizard(qtbot, controller=controller)
    wiz.next()  # advance to DestinationPage
    cfg_text = wiz._destination._cfg_edit.text()
    tpl_text = wiz._destination._tpl_edit.text()
    assert cfg_text.endswith(str(Path("Auto_ext_pro") / "config"))
    assert tpl_text.endswith(str(Path("Auto_ext_pro") / "templates"))


def test_destination_page_default_uses_home_when_no_controller(qtbot) -> None:
    wiz = _make_wizard(qtbot)
    wiz.next()
    cfg_text = wiz._destination._cfg_edit.text()
    assert cfg_text.startswith(str(Path.home()))
    assert "Auto_ext_pro" in cfg_text


# ---- 3. force required when targets exist ---------------------------------


def test_destination_page_force_required_when_targets_exist(
    qtbot, raw_dir: Path, tmp_path: Path
) -> None:
    out_root = tmp_path / "out"
    cfg = out_root / "config"
    cfg.mkdir(parents=True)
    (cfg / "project.yaml").write_text("# stale\n", encoding="utf-8")

    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, raw_dir, out_root, force=False)
    # Preview ran but isComplete() must be False because force is unchecked
    # and one will_overwrite is True.
    preview_page = wiz._preview_page
    assert preview_page._preview is not None
    assert preview_page.isComplete() is False


# ---- 4. raw files page drag-drop populates --------------------------------


def test_raw_files_page_drag_drop_populates(qtbot, raw_dir: Path) -> None:
    wiz = _make_wizard(qtbot)
    wiz.next()
    wiz.next()
    raw_page = wiz._raw_files
    # Find the calibre row's drop zone (first DropZone child).
    # (Each row has its own DropZone wired to its own QLineEdit.)
    from auto_ext.ui.widgets.drop_zone import DropZone

    zones = raw_page.findChildren(DropZone)
    assert len(zones) == 4
    # First zone connects to the calibre line edit.
    _drop_file(zones[0], raw_dir / "calibre_sample.qci")
    assert raw_page._calibre_edit.text() == str(raw_dir / "calibre_sample.qci")


# ---- 5. optional jivaro blank allowed -------------------------------------


def test_raw_files_page_optional_jivaro_blank_allowed(
    qtbot, raw_dir: Path
) -> None:
    wiz = _make_wizard(qtbot)
    wiz.next()
    wiz.next()
    raw_page = wiz._raw_files
    raw_page._calibre_edit.setText(str(raw_dir / "calibre_sample.qci"))
    raw_page._si_edit.setText(str(raw_dir / "si_sample.env"))
    raw_page._quantus_edit.setText(str(raw_dir / "quantus_sample.cmd"))
    # jivaro deliberately empty.
    assert raw_page.isComplete() is True


# ---- 6. advanced overrides pass through to preview ------------------------


def test_raw_files_page_advanced_overrides_pass_through(
    qtbot, raw_dir: Path, tmp_path: Path
) -> None:
    wiz = _make_wizard(qtbot)
    _drive_to_preview(
        wiz, raw_dir, tmp_path / "out", cell_override="OVERRIDE_CELL"
    )
    preview = wiz._preview
    assert preview is not None
    assert preview.merged_identity.cell == "OVERRIDE_CELL"


# ---- 7. preview page runs dry_run on enter --------------------------------


def test_preview_page_runs_dry_run_on_enter(
    qtbot, raw_dir: Path, tmp_path: Path
) -> None:
    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, raw_dir, tmp_path / "out")
    preview_page = wiz._preview_page
    assert preview_page._preview is not None
    assert preview_page._preview.merged_identity.cell == "INV1"
    yaml_text = preview_page._yaml_view.toPlainText()
    assert "### project.yaml ###" in yaml_text
    assert "### tasks.yaml ###" in yaml_text


# ---- 8. preview blocks on identity conflict -------------------------------


def test_preview_page_blocks_on_identity_conflict(
    qtbot, raw_dir: Path, tmp_path: Path
) -> None:
    # Build a broken raw set with a mismatched cell.
    broken = tmp_path / "broken_raw"
    broken.mkdir()
    for name in ("calibre_sample.qci", "quantus_sample.cmd", "jivaro_sample.xml"):
        (broken / name).write_bytes((raw_dir / name).read_bytes())
    si_raw = (raw_dir / "si_sample.env").read_text(encoding="utf-8")
    si_raw = si_raw.replace("INV1", "NOT_INV1")
    (broken / "si_sample.env").write_text(si_raw, encoding="utf-8")

    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, broken, tmp_path / "out")
    preview_page = wiz._preview_page
    assert preview_page._preview is not None
    assert preview_page._preview.conflicts
    assert preview_page.isComplete() is False
    assert "cell" in preview_page._banner.text()


# ---- 9. commit writes files (sync) ---------------------------------------


def test_commit_writes_files(qtbot, raw_dir: Path, tmp_path: Path) -> None:
    out_root = tmp_path / "out"
    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, raw_dir, out_root)
    # Advance from PreviewPage to CommitPage.
    wiz.next()
    commit_page = wiz._commit_page
    # Triggering validatePage() does the actual commit (sync).
    assert commit_page.validatePage() is True
    assert commit_page._succeeded is True
    # 10 files on disk.
    cfg = out_root / "config"
    tpl = out_root / "templates"
    assert (cfg / "project.yaml").is_file()
    assert (cfg / "tasks.yaml").is_file()
    for tool, ext in (("calibre", "qci"), ("si", "env"), ("quantus", "cmd"), ("jivaro", "xml")):
        assert (tpl / tool / f"imported.{ext}.j2").is_file()
        assert (tpl / tool / f"imported.{ext}.j2.manifest.yaml").is_file()


# ---- 10. progress callback updates log pane -------------------------------


def test_commit_progress_updates_log_pane(
    qtbot, raw_dir: Path, tmp_path: Path
) -> None:
    out_root = tmp_path / "out"
    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, raw_dir, out_root)
    wiz.next()
    commit_page = wiz._commit_page
    commit_page.validatePage()
    log_text = commit_page._log.toPlainText()
    # 10 written lines + 1 final summary line.
    lines = [l for l in log_text.splitlines() if l]
    assert len(lines) >= 10


# ---- 11. result page auto-load triggers signal ----------------------------


def test_result_page_auto_load_emits_signal(
    qtbot, raw_dir: Path, tmp_path: Path
) -> None:
    out_root = tmp_path / "out"
    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, raw_dir, out_root)
    wiz.next()  # to CommitPage
    wiz._commit_page.validatePage()  # commit
    # Manually advance past CommitPage to ResultPage so accept fires.
    wiz.next()  # to ResultPage
    received: list[Path] = []
    wiz.accepted_with_load.connect(lambda p: received.append(Path(p)))
    # Auto-load default-checked.
    assert wiz._result._auto_load_check.isChecked()
    wiz.accept()
    assert received == [out_root / "config"]


def test_result_page_auto_load_unchecked_skips_signal(
    qtbot, raw_dir: Path, tmp_path: Path
) -> None:
    out_root = tmp_path / "out"
    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, raw_dir, out_root)
    wiz.next()
    wiz._commit_page.validatePage()
    wiz.next()
    wiz._result._auto_load_check.setChecked(False)
    received: list[Path] = []
    wiz.accepted_with_load.connect(lambda p: received.append(Path(p)))
    wiz.accept()
    assert received == []


# ---- 12. CommitPage rejects re-entrant validatePage ----------------------


def test_commit_page_rejects_reentrant_validation(
    qtbot, raw_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate a queued double-click landing during a sync commit.

    ``commit()`` is monkey-patched to spin processEvents for ~100 ms
    and re-enter ``validatePage`` from within. The re-entrancy guard
    must reject the second call so ``commit`` runs exactly once.
    """
    out_root = tmp_path / "out"
    wiz = _make_wizard(qtbot)
    _drive_to_preview(wiz, raw_dir, out_root)
    wiz.next()  # advance to CommitPage
    commit_page = wiz._commit_page

    import time

    from PyQt5.QtWidgets import QApplication

    import auto_ext.ui.widgets.init_wizard as wizard_mod
    from auto_ext.core import init_project as ip

    real_commit = ip.commit
    call_count = {"n": 0}
    reentrant_results: list[bool] = []

    def spinning_commit(preview, *, progress=None):
        call_count["n"] += 1
        # First entry: spin processEvents and trigger a recursive
        # validatePage() call — the guard should make it return False.
        if call_count["n"] == 1:
            deadline = time.monotonic() + 0.1
            tried = False
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if not tried:
                    reentrant_results.append(commit_page.validatePage())
                    tried = True
        return real_commit(preview, progress=progress)

    # The wizard imports `commit` by name into its module namespace, so
    # patch the binding the page actually calls.
    monkeypatch.setattr(wizard_mod, "commit", spinning_commit)

    assert commit_page.validatePage() is True
    # Second (re-entrant) attempt rejected by the guard.
    assert reentrant_results == [False], reentrant_results
    # commit() ran exactly once.
    assert call_count["n"] == 1
    assert commit_page._succeeded is True
