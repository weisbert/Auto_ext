"""Parallel-mode tests for :mod:`auto_ext.core.runner`.

Exercises the ``max_workers >= 2`` path end-to-end against mock EDA
binaries. Real Cadence validation lives in the Phase 3.5 office
checklist (``docs/OFFICE_QUICKSTART.md §5``).

Skipped on Windows without Developer Mode — ``prepare_parallel_workdir``
uses ``os.symlink`` and there is no silent copy fallback.

Each task now runs in ``runs/<run_id>/work/`` rather than in a shared
``runs/task_<id>/``, so the isolation is a property of the run directory and
not of a task id two runs could both claim.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from auto_ext.core.config import load_project, load_tasks
from auto_ext.core.errors import ConfigError
from auto_ext.core.run_store import read_record
from auto_ext.core.runner import run_tasks


def _run_dirs(auto_ext_root: Path) -> list[Path]:
    """Run directories under ``auto_ext_root``, oldest name first."""

    root = auto_ext_root / "runs"
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir() if p.is_dir() and p.name not in ("batches", "latest")
    )


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
    run directory with its own work dir and rendered si.env.
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

    run_dirs = _run_dirs(tmp_path / "project_root")
    assert len(run_dirs) == 2, [d.name for d in run_dirs]
    cells = set()
    for run_dir in run_dirs:
        record = read_record(run_dir)
        cells.add(record.dut.cell)
        work_dir = run_dir / "work"
        assert record.work_dir == str(work_dir)
        assert work_dir.is_dir(), f"parallel work dir missing: {work_dir}"
        assert (work_dir / "si.env").is_file(), (
            f"si.env not placed inside the work dir for {record.dut.cell}"
        )
        assert (work_dir / "cds.lib").exists(), "cds.lib symlink missing"
        assert (work_dir / ".cdsinit").exists(), ".cdsinit symlink missing"
        # Stages ran with the work dir as cwd, and said so in the record.
        assert all(st.cwd == str(work_dir) for st in record.stages if st.cwd)
    assert cells == {"inv", "buf"}

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


def test_preflight_rejects_concurrent_workspace_sharing(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """Two tasks resolving to one workspace cannot run *at the same time*.

    Serial reuse of a workspace is legal now (see
    ``test_serial_tasks_may_share_one_workspace``); concurrent use is not,
    because two Calibre runs writing one svdb corrupt each other. The
    preflight refuses before any thread starts rather than silently
    serialising, which would look like a hang.
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

    with pytest.raises(ConfigError) as excinfo:
        run_tasks(
            project,
            tasks,
            stages=["si"],
            auto_ext_root=tmp_path / "project_root",
            workarea=workarea,
            max_workers=2,
            dry_run=True,
        )
    message = str(excinfo.value)
    assert "duplicate" in message
    # The message must name both escape hatches, or the user's only move is
    # to give up on --jobs.
    assert "--jobs 1" in message
    assert "run_slug" in message

    # Preflight runs before any run directory is claimed.
    assert not (tmp_path / "project_root" / "runs").exists()


@symlink_required
def test_parallel_same_cell_allowed_when_run_slug_isolates(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
) -> None:
    """The documented fix for the refusal above, exercised end to end."""

    proj_path = project_tools_config / "project.yaml"
    proj_path.write_text(
        proj_path.read_text(encoding="utf-8").replace(
            '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"',
            '"${WORK_ROOT}/cds/verify/QCI_PATH_{cell}_{run_id}"',
        ),
        encoding="utf-8",
    )
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

    summary = run_tasks(
        project,
        tasks,
        stages=["si"],
        auto_ext_root=tmp_path / "project_root",
        workarea=workarea,
        max_workers=2,
        dry_run=True,
    )

    assert summary.total == 2
    workspaces = {r.workspace_dir for r in summary.runs}
    assert len(workspaces) == 2, "{run_id} must give each run its own workspace"


def test_jobs_one_takes_serial_path(
    project_tools_config: Path,
    workarea: Path,
    mocks_on_path: Path,
    tmp_path: Path,
) -> None:
    """max_workers=None and max_workers=1 must behave identically: no
    work dir created, si.env placed via serial_workdir (swapped in/out of
    the workarea), summary green.
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
    run_dirs = _run_dirs(tmp_path / "project_root")
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    record = read_record(run_dir)
    # Serial still writes rendered/ and logs/ into the run directory, but
    # there is no work/ dir and no symlink farm — that is the parallel marker.
    assert (run_dir / "rendered").is_dir()
    assert not (run_dir / "work").exists()
    assert record.work_dir is None
    assert record.max_workers == 1
    assert all(st.cwd == str(workarea) for st in record.stages if st.cwd)
    # And after the run, workarea/si.env must have been cleaned up by
    # the serial context manager.
    assert not (workarea / "si.env").exists()


def test_work_dir_setup_failure_is_recorded_not_raised(
    project_tools_config: Path,
    workarea: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run directory is claimed before the work dir is built, so a symlink
    failure has to end up *in* that directory as a failed record.

    Letting it propagate would tear down the whole dispatch and leave an
    empty run directory behind that ``list_runs`` can only warn about.
    """

    def _denied(*args: object, **kwargs: object) -> None:
        raise OSError("symlink not permitted here")

    monkeypatch.setattr(os, "symlink", _denied)

    project, tasks = _load(project_tools_config)
    ae_root = tmp_path / "project_root"

    summary = run_tasks(
        project,
        tasks,
        stages=["si", "calibre"],
        auto_ext_root=ae_root,
        workarea=workarea,
        max_workers=2,
        dry_run=True,
    )

    assert summary.failed == 1
    run_dirs = _run_dirs(ae_root)
    assert len(run_dirs) == 1
    record = read_record(run_dirs[0])
    assert record.overall == "failed"
    assert record.work_dir is None
    assert "work dir" in (record.stages[0].error or "")
