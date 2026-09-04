"""Tests for :class:`auto_ext.ui.widgets.init_wizard.InitProjectWizard`.

The wizard chains two existing pieces -- ``core.init_project`` reads the raw
exports, ``migrate`` converts the result to the v2 schema -- so these tests
are mostly about what the chain leaves on disk and about the two rules the
wizard adds on top of it: the destination is never the current working
directory, and nothing is written until the Commit page runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import QMimeData, QPointF, QUrl, Qt  # noqa: E402
from PyQt5.QtGui import QDropEvent  # noqa: E402

from auto_ext.model.cells import load_cells  # noqa: E402
from auto_ext.model.workspace import load_workspace  # noqa: E402
from auto_ext.ui.config_controller import ConfigController  # noqa: E402
from auto_ext.ui.widgets.init_wizard import (  # noqa: E402
    InitProjectWizard,
    default_project_root,
)


# ---- helpers ----------------------------------------------------------------


@pytest.fixture
def raw_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "raw"


@pytest.fixture
def elsewhere(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Stand outside the repository, and outside the wizard's destination.

    ``resolve_template_path``-style lookups start cwd-relative, so a test whose
    cwd is still the checkout can hit the repository's own templates instead
    of the ones it just wrote. It is also what makes the "destination is not
    cwd" rule testable in both directions.
    """

    stand = tmp_path / "cwd"
    stand.mkdir()
    monkeypatch.chdir(stand)
    return stand


def _drop_file(zone, path: Path) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    event = QDropEvent(
        QPointF(zone.rect().center()),
        Qt.CopyAction,
        mime,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    zone.dropEvent(event)


def _make_wizard(qtbot, controller: ConfigController | None = None):
    wizard = InitProjectWizard(controller=controller)
    qtbot.addWidget(wizard)
    # QWizard.currentId() is -1 until show() / restart() / exec_() runs.
    wizard.restart()
    return wizard


def _drive_to_preview(
    wizard: InitProjectWizard,
    raw_dir: Path,
    root: Path,
    *,
    cell_override: str | None = None,
    force: bool = False,
) -> None:
    """Walk Intro -> Destination -> RawFiles -> Preview, filling the fields."""

    assert wizard.currentId() == 0
    wizard.next()
    wizard._destination._root_edit.setText(str(root))
    if force:
        wizard._destination._force_check.setChecked(True)
    wizard.next()
    page = wizard._raw_files
    page._calibre_edit.setText(str(raw_dir / "calibre_sample.qci"))
    page._si_edit.setText(str(raw_dir / "si_sample.env"))
    page._quantus_edit.setText(str(raw_dir / "quantus_sample.cmd"))
    page._jivaro_edit.setText(str(raw_dir / "jivaro_sample.xml"))
    if cell_override is not None:
        # The Advanced box is what turns the overrides on; typing into a
        # collapsed group is not a request to use it.
        page._advanced.setChecked(True)
        page._cell_edit.setText(cell_override)
    wizard.next()


# ---- the destination rule ---------------------------------------------------


def test_the_default_root_follows_the_controllers_workarea(
    qtbot, tmp_path: Path
) -> None:
    workarea = tmp_path / "wa"
    workarea.mkdir()
    controller = ConfigController(workarea=workarea)
    assert default_project_root(controller) == workarea / "Auto_ext_pro"

    wizard = _make_wizard(qtbot, controller=controller)
    wizard.next()
    assert wizard._destination._root_edit.text() == str(workarea / "Auto_ext_pro")


def test_the_default_root_falls_back_to_home_never_to_cwd(
    qtbot, elsewhere: Path
) -> None:
    """A wizard that defaulted to cwd would write a config tree into whatever
    directory the shell happened to be in -- usually the checkout itself."""

    assert default_project_root(None) == Path.home() / "Auto_ext_pro"
    wizard = _make_wizard(qtbot)
    wizard.next()
    text = wizard._destination._root_edit.text()
    assert text.startswith(str(Path.home()))
    assert Path(text).resolve() != elsewhere.resolve()


def test_the_current_directory_is_refused_with_a_reason(
    qtbot, elsewhere: Path
) -> None:
    wizard = _make_wizard(qtbot)
    wizard.next()
    page = wizard._destination
    page._root_edit.setText(str(elsewhere))

    assert page.isComplete() is False
    assert page._banner.isVisibleTo(page) or page._banner.text()
    assert "current working directory" in page._banner.text()

    page._root_edit.setText(str(elsewhere / "somewhere-else"))
    assert page.isComplete() is True


def test_the_destination_page_shows_the_three_directories_it_will_create(
    qtbot, tmp_path: Path
) -> None:
    wizard = _make_wizard(qtbot)
    wizard.next()
    page = wizard._destination
    page._root_edit.setText(str(tmp_path / "proj"))
    shown = page._layout_label.text()
    assert str(tmp_path / "proj" / "config") in shown
    assert str(tmp_path / "proj" / "recipes") in shown
    assert str(tmp_path / "proj" / "templates") in shown


# ---- raw files --------------------------------------------------------------


def test_dropping_a_file_fills_its_row(qtbot, raw_dir: Path) -> None:
    from auto_ext.ui.widgets.drop_zone import DropZone

    wizard = _make_wizard(qtbot)
    wizard.next()
    wizard.next()
    zones = wizard._raw_files.findChildren(DropZone)
    assert len(zones) == 4

    _drop_file(zones[0], raw_dir / "calibre_sample.qci")
    assert wizard._raw_files._calibre_edit.text() == str(
        raw_dir / "calibre_sample.qci"
    )


def test_jivaro_is_optional(qtbot, raw_dir: Path) -> None:
    wizard = _make_wizard(qtbot)
    wizard.next()
    wizard.next()
    page = wizard._raw_files
    page._calibre_edit.setText(str(raw_dir / "calibre_sample.qci"))
    page._si_edit.setText(str(raw_dir / "si_sample.env"))
    page._quantus_edit.setText(str(raw_dir / "quantus_sample.cmd"))
    assert page.isComplete() is True


def test_an_identity_override_reaches_the_preview(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    wizard = _make_wizard(qtbot)
    _drive_to_preview(
        wizard, raw_dir, tmp_path / "out", cell_override="OVERRIDE_CELL"
    )
    assert wizard._preview is not None
    assert wizard._preview.merged_identity.cell == "OVERRIDE_CELL"


def test_unticking_advanced_puts_the_overrides_back(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    """"在 Advanced 里填了 cell 名又把 Advanced 取消勾选，项目还是按我填的建的."

    A checkable ``QGroupBox`` whose checked state nothing reads is a switch
    painted on the wall: unticking greys the six boxes out and the wizard
    goes on using every value in them.
    """

    wizard = _make_wizard(qtbot)
    wizard.next()
    wizard.next()
    page = wizard._raw_files
    page._calibre_edit.setText(str(raw_dir / "calibre_sample.qci"))
    page._si_edit.setText(str(raw_dir / "si_sample.env"))
    page._quantus_edit.setText(str(raw_dir / "quantus_sample.cmd"))

    page._advanced.setChecked(True)
    page._cell_edit.setText("OVERRIDE_CELL")
    assert wizard.build_inputs().cell_override == "OVERRIDE_CELL"

    page._advanced.setChecked(False)
    assert wizard.build_inputs().cell_override is None

    wizard.next()
    assert wizard._preview is not None
    assert wizard._preview.merged_identity.cell == "INV1"
    # and re-ticking it brings back what was typed rather than losing it
    page._advanced.setChecked(True)
    assert wizard.build_inputs().cell_override == "OVERRIDE_CELL"


# ---- the preview ------------------------------------------------------------


def test_the_preview_reports_the_v2_objects_that_will_be_written(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    """Not a description of the conversion -- the conversion itself, run into
    a temporary directory with ``write=False``."""

    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, raw_dir, tmp_path / "out")
    page = wizard._preview_page

    assert page._preview is not None
    assert page._preview.merged_identity.cell == "INV1"
    report = wizard.report
    assert report is not None
    assert report.profile.profile_id
    assert len(report.recipes) >= 1
    assert len(report.cells) >= 1

    shown = page._v2_view.toPlainText()
    assert report.profile.profile_id in shown
    assert report.recipes[0].recipe_id in shown


def test_the_preview_lists_both_halves_of_the_file_set(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, raw_dir, tmp_path / "out")
    tree = wizard._preview_page._files_tree
    listed = {
        tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())
    }
    root = tmp_path / "out"
    assert str(root / "config" / "workspace.yaml") in listed
    assert str(root / "config" / "cells.yaml") in listed
    assert str(root / "config" / "project.yaml") in listed
    assert any("recipes" in path for path in listed)


def test_the_preview_writes_nothing_to_the_destination(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    root = tmp_path / "out"
    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, raw_dir, root)
    assert not root.exists(), sorted(p.name for p in root.rglob("*"))


def test_the_preview_surfaces_what_still_needs_checking(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    """The migration's own warnings are the honest part of the output."""

    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, raw_dir, tmp_path / "out")
    checks = wizard._preview_page._checks_view.toPlainText()
    assert "byte fidelity" in checks or "NEEDS CONFIRMATION" in checks.upper()


def test_an_identity_conflict_blocks_the_next_button(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    broken = tmp_path / "broken_raw"
    broken.mkdir()
    for name in ("calibre_sample.qci", "quantus_sample.cmd", "jivaro_sample.xml"):
        (broken / name).write_bytes((raw_dir / name).read_bytes())
    si_text = (raw_dir / "si_sample.env").read_text(encoding="utf-8")
    (broken / "si_sample.env").write_text(
        si_text.replace("INV1", "NOT_INV1"), encoding="utf-8"
    )

    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, broken, tmp_path / "out")
    page = wizard._preview_page

    assert page._preview is not None and page._preview.conflicts
    assert page.isComplete() is False
    assert "cell" in page._banner.text()


def test_existing_files_block_next_until_force_is_ticked(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path
) -> None:
    root = tmp_path / "out"
    (root / "config").mkdir(parents=True)
    (root / "config" / "project.yaml").write_text("# stale\n", encoding="utf-8")

    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, raw_dir, root, force=False)
    assert wizard._preview_page.isComplete() is False

    wizard._destination._force_check.setChecked(True)
    assert wizard._preview_page.isComplete() is True


# ---- the commit -------------------------------------------------------------


@pytest.fixture
def committed(qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path):
    """Drive the whole wizard through its commit; returns (wizard, root)."""

    root = tmp_path / "out"
    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, raw_dir, root)
    wizard.next()
    assert wizard._commit_page.validatePage() is True
    return wizard, root


def test_the_commit_writes_the_v2_file_set(committed) -> None:
    _wizard, root = committed
    config = root / "config"

    assert (config / "workspace.yaml").is_file()
    assert (config / "cells.yaml").is_file()
    assert (config / "resources.yaml").is_file()
    assert list((config / "profiles").glob("*.yaml"))
    assert list((root / "recipes").glob("*.yaml"))


def test_the_commit_keeps_the_imported_pair_as_provenance(committed) -> None:
    """The conversion's own input, left on disk as the record of the import.

    Nothing loads it afterwards -- the controller reads only the v2
    documents -- but the generated headers name it, and re-running the
    conversion needs it.
    """

    _wizard, root = committed
    assert (root / "config" / "project.yaml").is_file()
    assert (root / "config" / "tasks.yaml").is_file()
    for tool, ext in (
        ("calibre", "qci"),
        ("si", "env"),
        ("quantus", "cmd"),
        ("jivaro", "xml"),
    ):
        assert (root / "templates" / tool / f"imported.{ext}.j2").is_file()


def test_what_the_commit_wrote_is_what_the_controller_loads(
    committed, isolated_recipe_path: Path, qtbot
) -> None:
    """The end-to-end contract: a wizard-made project opens in the GUI."""

    _wizard, root = committed
    controller = ConfigController(auto_ext_root=root)
    errors: list[str] = []
    controller.config_error.connect(errors.append)
    controller.load(root / "config")

    assert errors == [], errors
    assert controller.workspace is not None
    assert controller.profile is not None
    assert len(controller.cells) >= 1
    assert controller.recipe_ids()
    assert controller.can_run is True


def test_the_written_documents_reload_as_themselves(committed) -> None:
    wizard, root = committed
    report = wizard.report
    assert report is not None
    assert (
        load_workspace(root / "config" / "workspace.yaml").model_dump()
        == report.workspace.model_dump()
    )
    assert (
        load_cells(root / "config" / "cells.yaml").model_dump()
        == report.cells.model_dump()
    )


def test_the_commit_log_names_every_file(committed) -> None:
    wizard, _root = committed
    lines = [line for line in wizard._commit_page._log.toPlainText().splitlines() if line]
    assert len(lines) >= 10
    assert any(line.startswith("wrote ") for line in lines)


def test_the_result_page_emits_the_config_dir_for_auto_load(committed) -> None:
    wizard, root = committed
    wizard.next()
    received: list[Path] = []
    wizard.accepted_with_load.connect(lambda p: received.append(Path(p)))
    assert wizard._result._auto_load_check.isChecked()
    wizard.accept()
    assert received == [root / "config"]


def test_unticking_auto_load_emits_nothing(committed) -> None:
    wizard, _root = committed
    wizard.next()
    wizard._result._auto_load_check.setChecked(False)
    received: list[Path] = []
    wizard.accepted_with_load.connect(lambda p: received.append(Path(p)))
    wizard.accept()
    assert received == []


def test_the_result_page_repeats_what_needs_checking(committed) -> None:
    wizard, _root = committed
    wizard.next()
    text = wizard._result._checks_view.toPlainText()
    assert text and text != "(none)"


def test_a_double_click_on_next_cannot_commit_twice(
    qtbot, raw_dir: Path, tmp_path: Path, elsewhere: Path, monkeypatch
) -> None:
    """``_on_progress`` pumps the event loop, which lets a queued click in.

    A second commit would write every file again and back the *fresh* copy up
    as if it were stale.
    """

    import time

    from PyQt5.QtWidgets import QApplication

    import auto_ext.ui.widgets.init_wizard as wizard_mod
    from auto_ext.core import init_project as core_init

    root = tmp_path / "out"
    wizard = _make_wizard(qtbot)
    _drive_to_preview(wizard, raw_dir, root)
    wizard.next()
    page = wizard._commit_page

    real_commit = core_init.commit
    calls = {"n": 0}
    reentrant: list[bool] = []

    def spinning_commit(preview, *, progress=None):
        calls["n"] += 1
        if calls["n"] == 1:
            deadline = time.monotonic() + 0.1
            tried = False
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if not tried:
                    reentrant.append(page.validatePage())
                    tried = True
        return real_commit(preview, progress=progress)

    monkeypatch.setattr(wizard_mod, "commit", spinning_commit)

    assert page.validatePage() is True
    assert reentrant == [False], reentrant
    assert calls["n"] == 1
