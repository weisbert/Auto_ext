"""Concurrency invariants between the controller, the run worker and Qt.

These are the ones no single-handler unit test reaches:

1. :meth:`ConfigController.has_external_change` mtime detection, both
   directions -- a save must not look like somebody else's edit.
2. :class:`QtProgressReporter` drops no signals under an emit storm. The
   runner emits one ``stage_finished`` per (task, stage) pair, so a parallel
   run over a few dozen cells pushes hundreds through in a burst.
3. Cancellation reaches an in-flight worker and the thread winds down. This
   is the hook a window-close handler would call.
4. Two screens staging edits in quick succession merge instead of stomping.
   The queue is keyed per document, and a recipe edit and a cell edit are two
   documents.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.core.progress import CancelToken  # noqa: E402
from auto_ext.model.cells import CELLS_FILENAME, CellEntry  # noqa: E402
from auto_ext.ui.qt_reporter import QtProgressReporter  # noqa: E402
from auto_ext.ui.worker import RunBatch, RunWorker  # noqa: E402


# ---- Invariant 1: has_external_change mtime detection -----------------------


def _bump_mtime(path: Path) -> None:
    """Push ``path``'s mtime a second forward.

    Nanosecond arithmetic rather than a rewrite: two writes inside one
    filesystem tick can land in the same mtime bucket on NTFS, which would
    make the positive case flaky rather than failing.
    """

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


@pytest.mark.parametrize("filename", [CELLS_FILENAME, "workspace.yaml"])
def test_an_external_rewrite_of_any_loaded_file_flips_the_flag(
    loaded_controller, v2_config_dir: Path, filename: str
) -> None:
    controller = loaded_controller
    assert controller.has_external_change() is False

    target = v2_config_dir / "config" / filename
    _bump_mtime(target)

    assert controller.has_external_change() is True
    assert controller.externally_changed_paths() == [target]


def test_an_external_rewrite_of_a_recipe_file_flips_the_flag(
    loaded_controller, v2_config_dir: Path
) -> None:
    """The flag spans the recipe files too, which live outside ``config/``."""

    controller = loaded_controller
    _bump_mtime(v2_config_dir / "recipes" / "rc-coupled-typical.yaml")
    assert controller.has_external_change() is True


def test_the_controllers_own_save_does_not_look_external(
    loaded_controller,
) -> None:
    """``save`` rewrites files and then reloads, which re-reads the mtimes."""

    controller = loaded_controller
    controller.stage_workspace(controller.workspace.model_copy(update={"keep_runs": 2}))
    assert controller.save() is True
    assert controller.has_external_change() is False


# ---- Invariant 2: emit storm does not lose signals --------------------------


def test_emit_storm_does_not_lose_signals(qtbot) -> None:
    reporter = QtProgressReporter()
    seen: list[tuple[str, str, str]] = []
    reporter.stage_finished.connect(
        lambda task, stage, status, error: seen.append((task, stage, status))
    )

    for index in range(100):
        reporter.on_stage_end(f"task{index}", "calibre", "passed")

    assert len(seen) == 100
    assert seen[0][0] == "task0" and seen[-1][0] == "task99"


def test_emit_storm_from_another_thread_delivers_all(qtbot) -> None:
    """Qt upgrades to a queued connection across threads; nothing is dropped."""

    reporter = QtProgressReporter()
    seen: list[str] = []
    reporter.stage_started.connect(lambda task, stage: seen.append(task))

    def storm() -> None:
        for index in range(100):
            reporter.on_stage_start(f"task{index}", "si")

    thread = threading.Thread(target=storm)
    thread.start()
    thread.join(timeout=10)

    qtbot.waitUntil(lambda: len(seen) == 100, timeout=10_000)
    assert len(seen) == 100


# ---- Invariant 3: cancellation reaches the worker ---------------------------


def test_cancel_flips_the_token_and_the_thread_winds_down(
    qtbot, loaded_controller, workarea: Path, tmp_path: Path, recipe, pdk_profile
) -> None:
    """The slice a window-close handler would use.

    ``MainWindow`` still installs no ``closeEvent``; when it does, this test
    should call ``window.close()`` instead of ``request_cancel()`` directly.
    """

    controller = loaded_controller
    token = CancelToken()
    worker = RunWorker(
        project=controller.project,
        batches=[RunBatch(recipe=recipe, tasks=controller.tasks)],
        stages=["si", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "pr",
        workarea=workarea,
        reporter=QtProgressReporter(),
        cancel_token=token,
        profile=pdk_profile,
        dry_run=True,
    )

    worker.start()
    worker.request_cancel()
    assert token.is_cancelled() is True

    qtbot.waitUntil(worker.isFinished, timeout=15_000)
    assert worker.isFinished() is True


def test_the_worker_forwards_the_recipe_and_profile_to_the_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recipe, pdk_profile
) -> None:
    """``run_tasks`` requires both; a worker that dropped them would start a
    run against the wrong settings and produce plausible parasitics."""

    captured: dict[str, object] = {}

    def fake_run_tasks(project, tasks, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("auto_ext.ui.worker.run_tasks", fake_run_tasks)
    worker = RunWorker(
        project=object(),
        batches=[RunBatch(recipe=recipe, tasks=[])],
        stages=["si"],
        auto_ext_root=tmp_path,
        workarea=tmp_path,
        reporter=QtProgressReporter(),
        cancel_token=CancelToken(),
        profile=pdk_profile,
    )
    worker.run()

    assert captured["recipe"] is recipe
    assert captured["profile"] is pdk_profile


# ---- Invariant 4: staged edits merge without stomping -----------------------


def test_two_screens_staging_different_documents_merge(
    loaded_controller, recipe
) -> None:
    controller = loaded_controller

    controller.stage_cells(
        controller.cells.model_copy(
            update={
                "cells": [
                    *controller.cells.cells,
                    CellEntry(library="LIB", cell="amp", layout_view="layout"),
                ]
            }
        )
    )
    controller.stage_recipe(recipe.model_copy(update={"description": "from Recipes"}))

    assert controller.pending_keys() == ["cells", f"recipe:{recipe.recipe_id}"]
    assert len(controller.cells) == 2
    assert controller.recipe(recipe.recipe_id).description == "from Recipes"

    # Reverse interleave: same outcome, neither document wins.
    controller.revert()
    controller.stage_recipe(recipe.model_copy(update={"description": "first"}))
    controller.stage_cells(controller.cells.model_copy(update={"cells": []}))
    assert controller.recipe(recipe.recipe_id).description == "first"
    assert len(controller.cells) == 0


def test_restaging_one_document_replaces_rather_than_appends(
    loaded_controller,
) -> None:
    controller = loaded_controller
    controller.stage_workspace(controller.workspace.model_copy(update={"keep_runs": 1}))
    controller.stage_workspace(controller.workspace.model_copy(update={"keep_runs": 2}))

    assert controller.pending_keys() == ["workspace"]
    assert controller.workspace.keep_runs == 2
