"""Parallel-mode tests for :mod:`auto_ext.core.runner`.

Exercises the ``max_workers >= 2`` path end-to-end against mock EDA
binaries. Real Cadence validation lives in the Phase 3.5 office
checklist (``docs/OFFICE_QUICKSTART.md §5``).

Skipped on Windows without Developer Mode — ``prepare_parallel_workdir``
uses ``os.symlink`` and there is no silent copy fallback.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.errors import ConfigError
from auto_ext.core.runner import run_tasks


def _host_can_symlink() -> bool:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "src"
        src.write_text("x", encoding="utf-8")
        try:
            os.symlink(src, Path(d) / "dst")
        except (OSError, NotImplementedError):
            return False
        return True


symlink_required = pytest.mark.skipif(
    not _host_can_symlink(),
    reason="symlink creation requires Admin / Developer Mode on Windows",
)


_TWO_TASKS_YAML = """\
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
  jivaro:
    enabled: true
    frequency_limit: 14
    error_max: 2
- library: WB_PLL_DCO
  cell: buf
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
  jivaro:
    enabled: true
    frequency_limit: 14
    error_max: 2
"""


def _load(config_dir: Path):
    project = load_project(config_dir / "project.yaml")
    tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    return project, tasks


@symlink_required
def test_parallel_two_jobs_both_pass(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """Two independent tasks under --jobs 2 both complete, each in its own
    task_dir with its own rendered si.env.
    """
    (project_tools_config / "tasks.yaml").write_text(_TWO_TASKS_YAML, encoding="utf-8")
    project, tasks = _load(project_tools_config)

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        max_workers=2,
    )

    assert summary.total == 2
    assert summary.passed == 2
    assert summary.failed == 0

    for task in tasks:
        task_dir = tmp_path / "project_root" / "runs" / f"task_{task.task_id}"
        assert task_dir.is_dir(), f"parallel task_dir missing: {task_dir}"
        assert (task_dir / "si.env").is_file(), (
            f"si.env not placed inside parallel task_dir for {task.task_id}"
        )
        assert (task_dir / "cds.lib").exists(), "cds.lib symlink missing"
        assert (task_dir / ".cdsinit").exists(), ".cdsinit symlink missing"

    # Serial path's side effect (writing to workarea/si.env) must NOT
    # happen in parallel mode — the shared workarea stays clean.
    assert not (workarea / "si.env").exists()


@symlink_required
def test_parallel_preserves_task_order_in_summary(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """Summary.tasks must follow submission order, not completion order.

    Completion order can jitter even with deterministic mocks (thread
    scheduling); the test just asserts the two task_ids come back in the
    same order they went in.
    """
    (project_tools_config / "tasks.yaml").write_text(_TWO_TASKS_YAML, encoding="utf-8")
    project, tasks = _load(project_tools_config)
    expected_order = [t.task_id for t in tasks]

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        max_workers=2,
    )

    assert [t.task_id for t in summary.tasks] == expected_order


@symlink_required
def test_parallel_one_failure_other_continues(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AUTO_EXT_MOCK_FORCE_FAIL applies to every mock invocation, so both
    tasks fail at the forced stage — the assertion is that each fails
    independently (abort inside the task, no cross-task cascade).
    """
    monkeypatch.setenv("AUTO_EXT_MOCK_FORCE_FAIL", "calibre")
    (project_tools_config / "tasks.yaml").write_text(_TWO_TASKS_YAML, encoding="utf-8")
    project, tasks = _load(project_tools_config)

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        max_workers=2,
    )

    assert summary.total == 2
    assert summary.failed == 2
    for task_result in summary.tasks:
        stages = {s.stage: s.status for s in task_result.stages}
        assert stages["si"] == "passed"
        assert stages["strmout"] == "passed"
        assert stages["calibre"] == "failed"
        # Per-task abort still applies: quantus/jivaro skipped when
        # continue_on_lvs_fail is False.
        assert stages["quantus"] == "skipped"
        assert stages["jivaro"] == "skipped"


def test_preflight_accepts_same_cell_when_pattern_discriminates(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """Two tasks sharing (library, cell) used to be hard-rejected. The
    new preflight checks the *resolved* output_dir, so adding
    {lvs_layout_view} (or any axis key) to extraction_output_dir lets
    them coexist — covers the "same cell, two knob configs" use case.
    """
    proj_path = project_tools_config / "project.yaml"
    proj_text = proj_path.read_text(encoding="utf-8").replace(
        '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"',
        '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}_{lvs_layout_view}"',
    )
    proj_path.write_text(proj_text, encoding="utf-8")

    (project_tools_config / "tasks.yaml").write_text(
        """\
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  jivaro:
    enabled: false
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout_test
  lvs_source_view: schematic
  jivaro:
    enabled: false
""",
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)

    # Should NOT raise — the pattern discriminates the two tasks.
    summary = run_tasks(
        project,
        tasks,
        stages=["si"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        dry_run=True,
    )
    assert len(summary.tasks) == 2


def test_preflight_rejects_unknown_format_key(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """A typo in the format pattern (``{bogus}``) must surface a clean
    ConfigError naming the offending key plus the supported set, not a
    bare KeyError leaking from str.format.
    """
    proj_path = project_tools_config / "project.yaml"
    proj_text = proj_path.read_text(encoding="utf-8").replace(
        '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"',
        '"${WORK_ROOT}/QCI_PATH_{bogus}"',
    )
    proj_path.write_text(proj_text, encoding="utf-8")
    project, tasks = _load(project_tools_config)

    with pytest.raises(ConfigError, match="unknown format key.*'bogus'"):
        run_tasks(
            project,
            tasks,
            stages=["si"],
            auto_ext_root=tmp_path / "project_root",
            workarea=workarea,
            dry_run=True,
        )


def test_preflight_rejects_duplicate_library_cell(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """Two tasks with identical (library, cell) would collide on
    extraction_output_dir. The preflight refuses before any subprocess
    or thread starts — enforced in serial and parallel alike.
    """
    # Same library + cell, different out_file → still a collision on
    # the extraction output dir.
    (project_tools_config / "tasks.yaml").write_text(
        """\
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext_a
  jivaro:
    enabled: false
- library: WB_PLL_DCO
  cell: inv
  lvs_layout_view: layout_test
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext_b
  jivaro:
    enabled: false
""",
        encoding="utf-8",
    )
    project, tasks = _load(project_tools_config)

    with pytest.raises(ConfigError, match="duplicate"):
        run_tasks(
            project,
            tasks,
            stages=["si"],
            auto_ext_root=tmp_path / "project_root",
            workarea=workarea,
            max_workers=2,
            dry_run=True,
        )

    # Preflight runs before any rendering, so no rendered dir should
    # exist.
    assert not (tmp_path / "project_root" / "runs").exists()


def test_jobs_one_takes_serial_path(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """max_workers=None and max_workers=1 must behave identically: no
    parallel workdir created, si.env placed via serial_workdir (swapped
    in/out of workarea), summary green.
    """
    project, tasks = _load(project_tools_config)

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "strmout", "calibre", "quantus", "jivaro"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        max_workers=1,
    )

    assert summary.passed == 1
    task_id = tasks[0].task_id
    task_dir = tmp_path / "project_root" / "runs" / f"task_{task_id}"
    # Serial path still uses runs/task_<id>/rendered/ for rendered
    # templates, but there must be no symlinks (cds.lib / .cdsinit)
    # placed at task_dir's top level — those are the parallel marker.
    assert not (task_dir / "cds.lib").exists()
    assert not (task_dir / ".cdsinit").exists()
    # And after the run, workarea/si.env must have been cleaned up by
    # the serial context manager.
    assert not (workarea / "si.env").exists()
