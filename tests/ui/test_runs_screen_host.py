"""The Runs screen as the shipped application wires it: inside a MainWindow.

``tests/ui/test_runs_screen.py`` drives a bare :class:`RunsScreen`, which is
exactly why the double-open of M-20 was invisible for so long: the screen
opened the log *and* re-emitted, and only the host -- which no test built
alongside it -- opened it a second time. Everything here therefore builds the
real window and clicks the real control, and asserts what reaches the OS.

The window is created without a project and pointed at the test's own runs
directory afterwards, because the wiring under test is established in
``MainWindow.__init__`` and does not depend on a loaded configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402

from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.core.run_store import write_record  # noqa: E402
from auto_ext.model.run import (  # noqa: E402
    LvsResult,
    RunResults,
    StageRecord,
    allocate_run_dir,
)
from auto_ext.ui import main_window as mw  # noqa: E402
from auto_ext.ui.main_window import MainWindow  # noqa: E402
from auto_ext.ui.screens import runs_screen as rs  # noqa: E402


@pytest.fixture
def history(runs_root: Path, make_run_record) -> Path:
    """One finished run whose calibre stage failed and archived its log."""

    run_dir = allocate_run_dir(runs_root, "amp2-ext")
    (run_dir / "logs" / "calibre.log").write_text(
        "ERROR: 3 discrepancies\n", encoding="utf-8"
    )
    (run_dir / "results" / "lvs.report").write_text("INCORRECT\n", encoding="utf-8")
    record = make_run_record(
        run_dir=run_dir,
        overall=TaskStatus.FAILED,
        stages=[
            StageRecord(
                key="calibre",
                stage="calibre",
                status=StageStatus.FAILED,
                duration_s=61.0,
                log_path="logs/calibre.log",
                exit_code=1,
            )
        ],
        results=RunResults(
            lvs=LvsResult(
                passed=False,
                banner="INCORRECT",
                discrepancies=3,
                archived_path="results/lvs.report",
            )
        ),
    )
    write_record(run_dir, record)
    return runs_root


@pytest.fixture
def window(qtbot, history: Path, isolated_recipe_path: Path) -> MainWindow:
    win = MainWindow()
    qtbot.addWidget(win)
    win.setAttribute(Qt.WA_DontShowOnScreen, True)
    win.show()
    qtbot.waitExposed(win)
    win.runs_screen.set_runs_root(history)
    return win


@pytest.fixture
def launches(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """One counter behind both modules' ``open_in_os``."""

    seen: list[Path] = []
    monkeypatch.setattr(rs, "open_in_os", seen.append)
    monkeypatch.setattr(mw, "open_in_os", seen.append)
    return seen


# ---- M-20: one click, one viewer -------------------------------------------


def test_opening_a_stage_log_launches_exactly_one_viewer(
    qtbot, window: MainWindow, launches: list[Path]
) -> None:
    card = window.runs_screen.result_card
    assert card.record is not None
    assert card._calibre_log_btn.isEnabled()

    card._calibre_log_btn.click()

    assert len(launches) == 1, f"one click, {len(launches)} editors: {launches}"
    assert launches[0].name == "calibre.log"


def test_opening_an_artifact_launches_exactly_one_viewer(
    qtbot, window: MainWindow, launches: list[Path]
) -> None:
    card = window.runs_screen.result_card
    assert card._report_btn.isEnabled()

    card._report_btn.click()

    assert len(launches) == 1, f"one click, {len(launches)} viewers: {launches}"
    assert launches[0].name == "lvs.report"


def test_the_run_directory_is_opened_once_from_the_context_menu(
    qtbot, window: MainWindow, launches: list[Path]
) -> None:
    screen = window.runs_screen
    entry = screen.entries[0]
    screen._open_path(entry.run_dir)
    assert launches == [entry.run_dir]
