"""Tests for :class:`auto_ext.ui.config_controller.ConfigController`.

Runs under pytest-qt so the QObject signal machinery is live. Uses the
``project_tools_config`` fixture (from :mod:`tests.conftest`) to get a
full ``project.yaml`` + ``tasks.yaml`` on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.ui.config_controller import ConfigController  # noqa: E402


def test_load_emits_config_loaded(qtbot, project_tools_config: Path) -> None:
    controller = ConfigController()
    with qtbot.waitSignal(controller.config_loaded, timeout=2000) as blocker:
        controller.load(project_tools_config)
    assert blocker.args[0] == project_tools_config
    assert controller.project is not None
    assert controller.tasks  # at least one task
    assert controller.is_dirty is False


def test_load_missing_emits_config_error(qtbot, tmp_path: Path) -> None:
    controller = ConfigController()
    with qtbot.waitSignal(controller.config_error, timeout=2000) as blocker:
        controller.load(tmp_path / "nonexistent")
    assert "not found" in blocker.args[0]
    assert controller.project is None


def test_stage_edits_flips_dirty(qtbot, project_tools_config: Path) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    assert controller.is_dirty is False

    with qtbot.waitSignal(controller.dirty_changed, timeout=2000) as blocker:
        controller.stage_edits({"tech_name": "HN999"})
    assert blocker.args[0] is True
    assert controller.is_dirty is True
    assert controller.pending_edits == {"tech_name": "HN999"}


def test_revert_clears_pending(qtbot, project_tools_config: Path) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_edits({"tech_name": "HN999"})
    assert controller.is_dirty is True

    with qtbot.waitSignal(controller.dirty_changed, timeout=2000) as blocker:
        controller.revert()
    assert blocker.args[0] is False
    assert controller.pending_edits == {}


def test_save_writes_edits_to_disk(qtbot, project_tools_config: Path) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_edits({"tech_name": "HN999"})

    with qtbot.waitSignal(controller.config_saved, timeout=2000):
        ok = controller.save()
    assert ok is True
    assert controller.is_dirty is False
    # Reload raw from disk to verify the edit stuck.
    on_disk = (project_tools_config / "project.yaml").read_text(encoding="utf-8")
    assert "tech_name: HN999" in on_disk
    # Other original fields untouched.
    assert "calibre_lvs_dir: $calibre_source_added_place|parent" in on_disk
    assert controller.project is not None
    assert controller.project.tech_name == "HN999"


def test_save_noop_when_no_pending(project_tools_config: Path) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    # No stage_edits call — save() should return False silently.
    assert controller.save() is False


def test_save_detects_external_change(
    qtbot, project_tools_config: Path
) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_edits({"tech_name": "HN999"})

    # Simulate a different process rewriting project.yaml after load().
    import os

    path = project_tools_config / "project.yaml"
    current = path.read_text(encoding="utf-8")
    path.write_text(current + "# external edit\n", encoding="utf-8")
    # Force mtime forward defensively in case the write landed in the
    # same ns bucket as the load.
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    with qtbot.waitSignal(controller.config_error, timeout=2000) as blocker:
        ok = controller.save()
    assert ok is False
    assert "changed on disk" in blocker.args[0]
    # Pending edits should still be intact so the user can retry.
    assert controller.is_dirty is True


def test_save_force_overrides_external_change(
    qtbot, project_tools_config: Path
) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_edits({"tech_name": "HN999"})

    import os

    path = project_tools_config / "project.yaml"
    current = path.read_text(encoding="utf-8")
    path.write_text(current + "# external edit\n", encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    assert controller.save(force=True) is True
    on_disk = path.read_text(encoding="utf-8")
    assert "tech_name: HN999" in on_disk


def test_effective_env_overrides_merges_staged(
    qtbot, project_tools_config: Path
) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    baseline = controller.effective_env_overrides()
    # Fixture seeds several overrides; baseline should match the project.
    assert controller.project is not None
    assert baseline == controller.project.env_overrides

    controller.stage_edits(
        {
            "env_overrides.NEW_VAR": "new_value",
            "env_overrides.WORK_ROOT": None,  # clear existing
        }
    )
    effective = controller.effective_env_overrides()
    assert effective["NEW_VAR"] == "new_value"
    assert "WORK_ROOT" not in effective


def test_pending_overwrite_is_single_signal(
    qtbot, project_tools_config: Path
) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)

    received: list[bool] = []
    controller.dirty_changed.connect(lambda state: received.append(state))

    controller.stage_edits({"tech_name": "HN999"})
    controller.stage_edits({"tech_name": "HN888"})  # already dirty, no flip
    assert received == [True]
    assert controller.pending_edits == {"tech_name": "HN888"}


# ---- Phase 5.4: tasks edits ------------------------------------------


def test_stage_tasks_edits_flips_dirty(qtbot, project_tools_config: Path) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    assert controller.is_dirty is False

    with qtbot.waitSignal(controller.dirty_changed, timeout=2000):
        controller.stage_tasks_edits(
            [{"library": "L2", "cell": "c2", "lvs_layout_view": "lay"}]
        )
    assert controller.is_dirty is True
    assert controller.pending_task_specs is not None


def test_save_writes_both_project_and_tasks(
    qtbot, project_tools_config: Path
) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_edits({"tech_name": "HN_DUAL"})
    controller.stage_tasks_edits(
        [
            {
                "library": "L_DUAL",
                "cell": "c_dual",
                "lvs_layout_view": "lay",
            }
        ]
    )
    assert controller.save() is True

    # Verify both files on disk.
    project_text = (project_tools_config / "project.yaml").read_text(encoding="utf-8")
    tasks_text = (project_tools_config / "tasks.yaml").read_text(encoding="utf-8")
    assert "HN_DUAL" in project_text
    assert "L_DUAL" in tasks_text
    assert controller.is_dirty is False


def test_revert_clears_tasks_pending(qtbot, project_tools_config: Path) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_tasks_edits(
        [{"library": "X", "cell": "y", "lvs_layout_view": "lay"}]
    )
    assert controller.is_dirty is True
    controller.revert()
    assert controller.is_dirty is False
    assert controller.pending_task_specs is None


def test_task_specs_raw_returns_pending_when_staged(
    qtbot, project_tools_config: Path
) -> None:
    controller = ConfigController()
    controller.load(project_tools_config)
    staged = [{"library": "X", "cell": "y", "lvs_layout_view": "lay"}]
    controller.stage_tasks_edits(staged)
    got = controller.task_specs_raw()
    assert got == staged


# ---- has_external_change direct unit tests -----------------------------


def test_has_external_change_false_after_load(
    qtbot, project_tools_config: Path
) -> None:
    """A fresh load with no further filesystem activity must report no
    external change. The autosave skip relies on this False return so
    a steady-state GUI does not block its own writes."""
    controller = ConfigController()
    controller.load(project_tools_config)
    assert controller.has_external_change() is False


def test_has_external_change_detects_project_yaml_touch(
    qtbot, project_tools_config: Path
) -> None:
    """Bumping project.yaml's mtime via os.utime must flip the flag —
    no need to actually rewrite the contents. This is the exact path
    a sister-process editor would take."""
    import os

    controller = ConfigController()
    controller.load(project_tools_config)
    project_path = project_tools_config / "project.yaml"
    st = project_path.stat()
    os.utime(
        project_path,
        ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000),
    )
    assert controller.has_external_change() is True


def test_has_external_change_detects_tasks_yaml_touch(
    qtbot, project_tools_config: Path
) -> None:
    """Same touch-only mtime bump but on tasks.yaml — the flag spans
    both files."""
    import os

    controller = ConfigController()
    controller.load(project_tools_config)
    tasks_path = project_tools_config / "tasks.yaml"
    st = tasks_path.stat()
    os.utime(
        tasks_path,
        ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000),
    )
    assert controller.has_external_change() is True


def test_has_external_change_resets_after_internal_save(
    qtbot, project_tools_config: Path
) -> None:
    """After a successful internal save, the controller must track the
    new mtime — has_external_change should be False again. Without
    this, autosave would skip every subsequent stage because the file
    we just wrote would always look "external" to us."""
    controller = ConfigController()
    controller.load(project_tools_config)
    controller.stage_edits({"tech_name": "HN_INTERNAL_SAVE"})
    assert controller.save() is True
    assert controller.has_external_change() is False


def test_has_external_change_resets_after_reload(
    qtbot, project_tools_config: Path
) -> None:
    """``reload()`` re-records the mtime even if external edits had
    already flipped the flag — the flag is False after reload."""
    import os

    controller = ConfigController()
    controller.load(project_tools_config)
    project_path = project_tools_config / "project.yaml"
    st = project_path.stat()
    os.utime(
        project_path,
        ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000),
    )
    assert controller.has_external_change() is True
    controller.reload()
    assert controller.has_external_change() is False
