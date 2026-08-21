"""Handing a finished run back to Calibre Interactive.

The feature under test is "open *this* run", not "configure Calibre again",
so most of these tests are about provenance: every value in the launch must
come out of the :class:`~auto_ext.model.run.RunRecord` that was written when
the batch run happened, and nothing may be re-rendered or re-derived.

The three pre-flight checks (runset gone, workarea gone, calibre not on PATH)
get one test each plus one for all three at once, because each of them is a
thing that quietly stops being true between a run finishing and the user
deciding to re-check it.

No Calibre binary exists on any machine that runs this suite, so every launch
is a patched :class:`subprocess.Popen`; what is asserted is the argv, the cwd,
the env and the platform detach flags -- see the module docstring of
:mod:`auto_ext.core.handoff` for what remains unverified against a real tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auto_ext.core import handoff
from auto_ext.core.errors import AutoExtError
from auto_ext.core.handoff import (
    BATCH_FLAG,
    RUNSET_FLAG,
    HandoffError,
    build_calibre_interactive_argv,
    calibre_interactive_argv,
    detached_popen_kwargs,
    find_calibre_stage,
    handoff_env,
    launch_calibre_interactive,
    launch_detached,
    plan_calibre_handoff,
)
from auto_ext.model.run import EnvBinding, StageRecord, StageStatus, TaskStatus

CALIBRE_EXE = "/opt/mentor/calibre/bin/calibre"


def _which_ok(cmd: str, path: str | None = None) -> str | None:
    """Stand-in for ``shutil.which`` that always finds the tool."""

    return CALIBRE_EXE


def _which_missing(cmd: str, path: str | None = None) -> str | None:
    """Stand-in for ``shutil.which`` on a shell that never sourced Calibre."""

    return None


def _calibre_stage(
    runset: Path,
    cwd: Path,
    *,
    status: StageStatus = StageStatus.FAILED,
    rendered_path: str | None = "rendered/lvs.qci",
    argv: list[str] | None = None,
) -> StageRecord:
    """A calibre stage shaped exactly like the one the runner records."""

    if argv is None:
        argv = ["calibre", "-gui", "-lvs", RUNSET_FLAG, str(runset), BATCH_FLAG]
    return StageRecord(
        key="calibre",
        stage="calibre",
        status=status,
        argv=argv,
        cwd=str(cwd),
        rendered_path=rendered_path,
        log_path="logs/calibre.log",
        exit_code=0,
    )


@pytest.fixture
def runset(run_dir: Path) -> Path:
    """The archived runset inside the run directory, as the runner leaves it."""

    path = run_dir / "rendered" / "lvs.qci"
    path.write_text("*lvsRunDir: .\n*lvsReportFile: amp2.lvs.report\n", encoding="utf-8")
    return path


@pytest.fixture
def lvs_record(run_dir: Path, runset: Path, workarea: Path, make_run_record: Any) -> Any:
    """A finished, LVS-failing run -- the case this feature exists for."""

    return make_run_record(
        run_dir=run_dir,
        overall=TaskStatus.FAILED,
        stages=[_calibre_stage(runset, workarea)],
        workarea=str(workarea),
        env=[EnvBinding(name="WORK_ROOT", value="/work/alice", source="override")],
    )


# ---- picking the stage ---------------------------------------------------------


def test_find_calibre_stage_picks_the_calibre_row(lvs_record: Any) -> None:
    stage = find_calibre_stage(lvs_record)
    assert stage is not None
    assert stage.stage == "calibre"


def test_find_calibre_stage_returns_none_when_lvs_never_ran(make_run_record: Any) -> None:
    record = make_run_record(
        stages=[StageRecord(key="si", stage="si", status=StageStatus.PASSED)]
    )
    assert find_calibre_stage(record) is None


def test_find_calibre_stage_prefers_the_row_that_archived_a_runset(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """A record may hold several calibre rows; only one carries the runset."""

    bare = StageRecord(
        key="calibre.pre", stage="calibre", status=StageStatus.SKIPPED, rendered_path=None
    )
    record = make_run_record(
        run_dir=run_dir, stages=[_calibre_stage(runset, workarea), bare]
    )
    stage = find_calibre_stage(record)
    assert stage is not None
    assert stage.key == "calibre"


# ---- argv: the handoff is "the batch command line minus -batch" ----------------


def test_argv_drops_only_the_batch_flag(lvs_record: Any, runset: Path) -> None:
    argv, _cwd, _env = build_calibre_interactive_argv(lvs_record)
    assert argv == ["calibre", "-gui", "-lvs", RUNSET_FLAG, str(runset)]


def test_argv_never_contains_batch(lvs_record: Any) -> None:
    """The single defining property of the handoff, asserted on its own."""

    argv, _cwd, _env = build_calibre_interactive_argv(lvs_record)
    assert BATCH_FLAG not in argv


def test_argv_keeps_extra_flags_the_batch_run_used(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """Opening *this* run means every other flag survives verbatim.

    Rebuilding the command line from a local template would silently drop
    whatever the recorded invocation carried; that is the failure mode this
    nails down.
    """

    recorded = [
        "calibre",
        "-gui",
        "-lvs",
        "-hier",
        RUNSET_FLAG,
        str(runset),
        "-turbo",
        "4",
        BATCH_FLAG,
    ]
    record = make_run_record(
        run_dir=run_dir, stages=[_calibre_stage(runset, workarea, argv=recorded)]
    )
    argv, _cwd, _env = build_calibre_interactive_argv(record)
    assert argv == [
        "calibre",
        "-gui",
        "-lvs",
        "-hier",
        RUNSET_FLAG,
        str(runset),
        "-turbo",
        "4",
    ]


def test_argv_points_at_the_archived_runset_when_the_run_dir_moved(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """The recorded ``-runset`` value is retargeted at the archived copy.

    The rendered file lives inside the run directory, so the two normally
    agree; they diverge once the run directory has been moved or shipped, and
    then only the archived copy still exists.
    """

    stale = workarea / "gone" / "lvs.qci"
    record = make_run_record(
        run_dir=run_dir,
        stages=[
            _calibre_stage(
                runset,
                workarea,
                argv=["calibre", "-gui", "-lvs", RUNSET_FLAG, str(stale), BATCH_FLAG],
            )
        ],
    )
    argv, _cwd, _env = build_calibre_interactive_argv(record)
    assert argv[argv.index(RUNSET_FLAG) + 1] == str(runset)


def test_argv_falls_back_to_the_recorded_path_when_the_record_has_no_run_dir(
    runset: Path, workarea: Path, make_run_record: Any
) -> None:
    record = make_run_record(stages=[_calibre_stage(runset, workarea)])
    assert record.run_dir is None
    argv, _cwd, _env = build_calibre_interactive_argv(record)
    assert argv[argv.index(RUNSET_FLAG) + 1] == str(runset)


def test_argv_is_rebuilt_from_the_template_when_the_record_has_no_runset_flag(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """An argv shape we don't recognise degrades to the built-in template.

    Recorded but unrecognisable is worth a warning: the user is being handed a
    command line we constructed, not the one that ran.
    """

    record = make_run_record(
        run_dir=run_dir,
        stages=[_calibre_stage(runset, workarea, argv=["calibre", "-verify"])],
    )
    argv, _cwd, _env = build_calibre_interactive_argv(record)
    assert argv == calibre_interactive_argv("calibre", runset)

    plan = plan_calibre_handoff(record, environ={"PATH": "/usr/bin"}, which=_which_ok)
    assert any(RUNSET_FLAG in w for w in plan.warnings)


def test_argv_is_built_from_scratch_for_a_dry_run(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """Dry runs render the runset but never spawn calibre, so argv is empty.

    Handing that runset to Calibre Interactive is a legitimate use: render,
    then drive the tool by hand.
    """

    stage = _calibre_stage(runset, workarea, status=StageStatus.DRY_RUN, argv=[])
    record = make_run_record(run_dir=run_dir, stages=[stage], dry_run=True)
    argv, _cwd, _env = build_calibre_interactive_argv(record)
    assert argv == calibre_interactive_argv("calibre", runset)

    plan = plan_calibre_handoff(record, environ={"PATH": "/usr/bin"}, which=_which_ok)
    assert any("dry run" in w for w in plan.warnings)
    # A dry run is not a refusal: the runset is on disk, so the launch is legal.
    assert plan.ok


def test_explicit_executable_replaces_argv_zero(lvs_record: Any) -> None:
    argv, _cwd, _env = build_calibre_interactive_argv(
        lvs_record, executable="/tools/calibre_2019.2"
    )
    assert argv[0] == "/tools/calibre_2019.2"
    assert argv[1:3] == ["-gui", "-lvs"]


def test_a_path_with_shell_metacharacters_stays_one_argv_element(
    run_dir: Path, workarea: Path, make_run_record: Any
) -> None:
    """Same guarantee the runner has: argv is a list, so nothing is a shell."""

    nasty = run_dir / "rendered" / "amp2;rm -rf $(pwd).qci"
    nasty.write_text("*lvsRunDir: .\n", encoding="utf-8")
    record = make_run_record(
        run_dir=run_dir,
        stages=[
            _calibre_stage(
                nasty, workarea, rendered_path="rendered/amp2;rm -rf $(pwd).qci"
            )
        ],
    )
    argv, _cwd, _env = build_calibre_interactive_argv(record)
    assert argv.count(str(nasty)) == 1
    assert argv[-1] == str(nasty)


# ---- cwd ------------------------------------------------------------------------


def test_cwd_is_the_directory_the_stage_actually_ran_in(
    lvs_record: Any, workarea: Path
) -> None:
    _argv, cwd, _env = build_calibre_interactive_argv(lvs_record)
    assert cwd == workarea


def test_cwd_falls_back_through_work_dir_then_workarea_then_workspace(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """A stage that never started recorded no cwd; the run still knows where.

    Order matters: the parallel-isolation ``work_dir`` is the cwd a parallel
    run would have used, so it outranks the shared workarea.
    """

    stage = _calibre_stage(runset, workarea, argv=[])
    stage = stage.model_copy(update={"cwd": None})

    parallel = make_run_record(
        run_dir=run_dir,
        stages=[stage],
        work_dir=str(run_dir / "work"),
        workarea=str(workarea),
    )
    assert build_calibre_interactive_argv(parallel)[1] == run_dir / "work"

    serial = make_run_record(run_dir=run_dir, stages=[stage], workarea=str(workarea))
    assert build_calibre_interactive_argv(serial)[1] == workarea

    bare = make_run_record(
        run_dir=run_dir, stages=[stage], workspace_dir="/work/cds/verify/QCI_PATH_amp2"
    )
    assert build_calibre_interactive_argv(bare)[1] == Path("/work/cds/verify/QCI_PATH_amp2")


# ---- env -------------------------------------------------------------------------


def test_env_is_the_current_environment_with_the_recorded_bindings_on_top(
    lvs_record: Any,
) -> None:
    """Calibre needs the whole environment, not just what Auto_ext resolved."""

    env = handoff_env(lvs_record, {"PATH": "/usr/bin", "MGC_HOME": "/opt/mentor"})
    assert env["MGC_HOME"] == "/opt/mentor"
    assert env["WORK_ROOT"] == "/work/alice"


def test_env_recorded_value_wins_over_the_current_shell(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """Replaying the run means replaying the values it resolved.

    A ``shell`` binding is recorded precisely because its value mattered; if
    the user's shell has since changed it, the interactive session must still
    look at the data the batch run looked at.
    """

    record = make_run_record(
        run_dir=run_dir,
        stages=[_calibre_stage(runset, workarea)],
        env=[EnvBinding(name="WORK_ROOT", value="/work/old", source="shell")],
    )
    env = handoff_env(record, {"WORK_ROOT": "/work/new"})
    assert env["WORK_ROOT"] == "/work/old"


def test_env_skips_bindings_that_were_missing_during_the_run(
    run_dir: Path, runset: Path, workarea: Path, make_run_record: Any
) -> None:
    """An unset variable and a variable set to "" are different to Calibre."""

    record = make_run_record(
        run_dir=run_dir,
        stages=[_calibre_stage(runset, workarea)],
        env=[EnvBinding(name="QRC_HOME", value="", source="missing")],
    )
    assert "QRC_HOME" not in handoff_env(record, {"PATH": "/usr/bin"})


def test_env_defaults_to_os_environ(
    lvs_record: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_EXT_HANDOFF_PROBE", "yes")
    _argv, _cwd, env = build_calibre_interactive_argv(lvs_record)
    assert env["AUTO_EXT_HANDOFF_PROBE"] == "yes"


# ---- purity ----------------------------------------------------------------------


def test_builder_touches_no_files(
    run_dir: Path, workarea: Path, make_run_record: Any
) -> None:
    """It reports what the run did; it does not re-render or verify anything.

    Deliberate: the pure builder stays usable for a preview even when the
    artefacts are long gone. Existence is :func:`plan_calibre_handoff`'s job.
    """

    ghost = run_dir / "rendered" / "never_written.qci"
    record = make_run_record(
        run_dir=run_dir,
        stages=[_calibre_stage(ghost, workarea, rendered_path="rendered/never_written.qci")],
    )
    before = sorted(p.name for p in (run_dir / "rendered").iterdir())
    argv, _cwd, _env = build_calibre_interactive_argv(record)
    assert argv[argv.index(RUNSET_FLAG) + 1] == str(ghost)
    assert sorted(p.name for p in (run_dir / "rendered").iterdir()) == before


def test_builder_raises_a_domain_error_when_there_is_no_calibre_stage(
    make_run_record: Any,
) -> None:
    record = make_run_record(
        stages=[StageRecord(key="si", stage="si", status=StageStatus.PASSED)]
    )
    with pytest.raises(HandoffError) as exc_info:
        build_calibre_interactive_argv(record)
    assert isinstance(exc_info.value, AutoExtError)
    assert "calibre" in str(exc_info.value)


def test_builder_raises_when_the_stage_recorded_no_runset_at_all(
    workarea: Path, make_run_record: Any
) -> None:
    stage = StageRecord(
        key="calibre",
        stage="calibre",
        status=StageStatus.FAILED,
        cwd=str(workarea),
        rendered_path=None,
        argv=[],
    )
    record = make_run_record(stages=[stage])
    with pytest.raises(HandoffError):
        build_calibre_interactive_argv(record)


# ---- pre-flight ------------------------------------------------------------------


def test_plan_is_ok_when_everything_is_still_there(
    lvs_record: Any, runset: Path, workarea: Path
) -> None:
    plan = plan_calibre_handoff(
        lvs_record, environ={"PATH": "/usr/bin", "DISPLAY": ":0"}, which=_which_ok
    )
    assert plan.ok
    assert plan.reason is None
    assert plan.reasons == ()
    assert plan.runset == runset
    assert plan.cwd == workarea
    assert plan.stage_key == "calibre"


def test_plan_resolves_argv_zero_to_an_absolute_executable(lvs_record: Any) -> None:
    """So the launch does not depend on the child repeating the PATH lookup.

    Same reason :func:`auto_ext.tools.base.run_subprocess` resolves it: on
    Windows ``CreateProcess`` alone will not find a ``.bat`` shim.
    """

    plan = plan_calibre_handoff(lvs_record, environ={"PATH": "/x"}, which=_which_ok)
    assert plan.argv[0] == CALIBRE_EXE
    assert plan.executable_path == CALIBRE_EXE
    assert plan.executable == "calibre"


def test_plan_passes_the_effective_path_to_the_which_lookup(lvs_record: Any) -> None:
    """The PATH Calibre will be started with is the PATH it is looked up on."""

    seen: list[str | None] = []

    def spy(cmd: str, path: str | None = None) -> str | None:
        seen.append(path)
        return CALIBRE_EXE

    plan_calibre_handoff(lvs_record, environ={"PATH": "/opt/mentor/bin"}, which=spy)
    assert seen == ["/opt/mentor/bin"]


def test_plan_refuses_when_the_runset_is_gone(
    lvs_record: Any, runset: Path
) -> None:
    runset.unlink()
    plan = plan_calibre_handoff(lvs_record, environ={"PATH": "/x"}, which=_which_ok)
    assert not plan.ok
    assert plan.reason is not None
    assert str(runset) in plan.reason
    assert "runset" in plan.reason


def test_plan_refuses_when_the_working_directory_is_gone(
    run_dir: Path, runset: Path, tmp_path: Path, make_run_record: Any
) -> None:
    gone = tmp_path / "deleted_workarea"
    record = make_run_record(run_dir=run_dir, stages=[_calibre_stage(runset, gone)])
    plan = plan_calibre_handoff(record, environ={"PATH": "/x"}, which=_which_ok)
    assert not plan.ok
    assert plan.reason is not None
    assert str(gone) in plan.reason


def test_plan_refuses_when_calibre_is_not_on_path(lvs_record: Any) -> None:
    plan = plan_calibre_handoff(
        lvs_record, environ={"PATH": "/usr/bin"}, which=_which_missing
    )
    assert not plan.ok
    assert plan.reason is not None
    assert "PATH" in plan.reason
    assert "calibre" in plan.reason


def test_plan_reports_every_problem_at_once(
    run_dir: Path, runset: Path, tmp_path: Path, make_run_record: Any
) -> None:
    """One round trip, not three: the user fixes everything in one go."""

    runset.unlink()
    gone = tmp_path / "deleted_workarea"
    record = make_run_record(run_dir=run_dir, stages=[_calibre_stage(runset, gone)])
    plan = plan_calibre_handoff(record, environ={"PATH": "/x"}, which=_which_missing)
    assert len(plan.reasons) == 3
    assert plan.reason is not None
    assert plan.reason.count("\n") == 2


def test_plan_turns_a_missing_calibre_stage_into_a_reason_not_an_exception(
    make_run_record: Any,
) -> None:
    """GUI call sites must never need a try/except around this."""

    record = make_run_record(
        stages=[StageRecord(key="si", stage="si", status=StageStatus.PASSED)]
    )
    plan = plan_calibre_handoff(record, environ={"PATH": "/x"}, which=_which_ok)
    assert not plan.ok
    assert plan.reason is not None
    assert "calibre" in plan.reason
    assert plan.argv == ()
    assert plan.cwd is None


def test_plan_reasons_are_complete_sentences_naming_the_path(
    lvs_record: Any, runset: Path
) -> None:
    """They go straight into a message box, so they must read as English."""

    runset.unlink()
    plan = plan_calibre_handoff(
        lvs_record, environ={"PATH": "/usr/bin"}, which=_which_missing
    )
    for reason in plan.reasons:
        assert reason[0].isupper()
        assert reason.rstrip().endswith(".")


def test_plan_warns_when_no_display_is_set_on_posix(
    lvs_record: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A warning, not a refusal: we cannot prove X11 forwarding either way."""

    monkeypatch.setattr(handoff.sys, "platform", "linux")
    plan = plan_calibre_handoff(
        lvs_record, environ={"PATH": "/usr/bin"}, which=_which_ok
    )
    assert plan.ok
    assert any("DISPLAY" in w for w in plan.warnings)


def test_plan_is_quiet_when_a_display_is_set(
    lvs_record: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handoff.sys, "platform", "linux")
    plan = plan_calibre_handoff(
        lvs_record, environ={"PATH": "/usr/bin", "DISPLAY": "localhost:10.0"},
        which=_which_ok,
    )
    assert not any("DISPLAY" in w for w in plan.warnings)


def test_plan_command_line_is_copy_pasteable(
    lvs_record: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handoff.sys, "platform", "linux")
    plan = plan_calibre_handoff(lvs_record, environ={"PATH": "/x"}, which=_which_ok)
    assert plan.command_line.startswith(CALIBRE_EXE)
    assert RUNSET_FLAG in plan.command_line


# ---- detaching -------------------------------------------------------------------


def test_detach_kwargs_on_windows_use_a_detached_new_process_group() -> None:
    """DETACHED_PROCESS: no console window. NEW_PROCESS_GROUP: no inherited Ctrl-C."""

    kwargs = detached_popen_kwargs("win32")
    assert kwargs["creationflags"] == 0x00000008 | 0x00000200
    assert "start_new_session" not in kwargs


def test_detach_kwargs_on_posix_start_a_new_session() -> None:
    """setsid(), so the SIGHUP that follows a closed ssh session misses Calibre."""

    kwargs = detached_popen_kwargs("linux")
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_detach_kwargs_always_discard_the_child_streams(platform: str) -> None:
    """A child writing to an unread pipe would block the GUI forever."""

    import subprocess as sp

    kwargs = detached_popen_kwargs(platform)
    assert kwargs["stdin"] == sp.DEVNULL
    assert kwargs["stdout"] == sp.DEVNULL
    assert kwargs["stderr"] == sp.DEVNULL


def test_detach_kwargs_default_to_the_running_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(handoff.sys, "platform", "win32")
    assert "creationflags" in detached_popen_kwargs()
    monkeypatch.setattr(handoff.sys, "platform", "linux")
    assert "start_new_session" in detached_popen_kwargs()


class _FakePopen:
    """Records what it was asked to spawn; never spawns anything."""

    calls: list[tuple[list[str], dict[str, Any]]] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        type(self).calls.append((list(argv), dict(kwargs)))
        self.pid = 4242


@pytest.fixture
def fake_popen(monkeypatch: pytest.MonkeyPatch) -> type[_FakePopen]:
    _FakePopen.calls = []
    monkeypatch.setattr(handoff.subprocess, "Popen", _FakePopen)
    return _FakePopen


def test_launch_detached_does_not_wait_and_returns_the_pid(
    fake_popen: type[_FakePopen], workarea: Path
) -> None:
    """Fire and forget: no communicate(), no wait(), no returncode inspection."""

    pid = launch_detached(["calibre", "-gui"], workarea, {"PATH": "/usr/bin"})
    assert pid == 4242
    argv, kwargs = fake_popen.calls[0]
    assert argv == ["calibre", "-gui"]
    assert kwargs["cwd"] == str(workarea)
    assert kwargs["env"] == {"PATH": "/usr/bin"}


def test_launch_detached_applies_the_platform_detach_flags(
    fake_popen: type[_FakePopen], workarea: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handoff.sys, "platform", "linux")
    launch_detached(["calibre"], workarea)
    assert fake_popen.calls[0][1]["start_new_session"] is True

    monkeypatch.setattr(handoff.sys, "platform", "win32")
    launch_detached(["calibre"], workarea)
    assert fake_popen.calls[1][1]["creationflags"] == 0x00000008 | 0x00000200


def test_launch_detached_passes_argv_as_a_list_not_a_command_string(
    fake_popen: type[_FakePopen], workarea: Path
) -> None:
    """No shell is involved, so a runset path with metacharacters is inert."""

    launch_detached(["calibre", RUNSET_FLAG, "/w/a b;rm -rf .qci"], workarea)
    argv, kwargs = fake_popen.calls[0]
    assert argv[-1] == "/w/a b;rm -rf .qci"
    assert "shell" not in kwargs


def test_launch_detached_inherits_the_environment_when_none_is_given(
    fake_popen: type[_FakePopen], workarea: Path
) -> None:
    launch_detached(["calibre"], workarea)
    assert fake_popen.calls[0][1]["env"] is None


def test_launch_detached_rejects_an_empty_argv(workarea: Path) -> None:
    with pytest.raises(ValueError):
        launch_detached([], workarea)


def test_launch_detached_reports_a_failure_to_start_as_a_plain_oserror(
    workarea: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not FileNotFoundError: "calibre won't start" is not "your file is gone"."""

    def boom(argv: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError(2, "No such file or directory: 'calibre'")

    monkeypatch.setattr(handoff.subprocess, "Popen", boom)
    with pytest.raises(OSError) as exc_info:
        launch_detached(["calibre"], workarea)
    assert not isinstance(exc_info.value, FileNotFoundError)
    assert "calibre" in str(exc_info.value)
    assert str(workarea) in str(exc_info.value)


# ---- the one-call path -----------------------------------------------------------


def test_launch_calibre_interactive_launches_the_planned_command(
    lvs_record: Any, runset: Path, workarea: Path
) -> None:
    seen: list[tuple[Any, Any, Any]] = []

    def fake_launch(argv: Any, cwd: Any, env: Any) -> int:
        seen.append((tuple(argv), cwd, dict(env)))
        return 99

    plan = launch_calibre_interactive(
        lvs_record,
        environ={"PATH": "/usr/bin", "DISPLAY": ":0"},
        which=_which_ok,
        launch=fake_launch,
    )
    assert plan.launched
    assert plan.pid == 99
    argv, cwd, env = seen[0]
    assert argv == (CALIBRE_EXE, "-gui", "-lvs", RUNSET_FLAG, str(runset))
    assert cwd == workarea
    assert env["WORK_ROOT"] == "/work/alice"


def test_launch_calibre_interactive_does_not_launch_a_refused_plan(
    lvs_record: Any, runset: Path
) -> None:
    runset.unlink()
    launched: list[Any] = []

    plan = launch_calibre_interactive(
        lvs_record,
        environ={"PATH": "/x"},
        which=_which_ok,
        launch=lambda *a: launched.append(a) or 1,
    )
    assert launched == []
    assert not plan.ok
    assert not plan.launched


def test_launch_calibre_interactive_turns_a_spawn_failure_into_a_reason(
    lvs_record: Any,
) -> None:
    """One message box handles refusals and spawn failures alike."""

    def boom(argv: Any, cwd: Any, env: Any) -> int:
        raise OSError("could not start 'calibre' in /w: Permission denied")

    plan = launch_calibre_interactive(
        lvs_record, environ={"PATH": "/x"}, which=_which_ok, launch=boom
    )
    assert not plan.ok
    assert not plan.launched
    assert plan.reason is not None
    assert "Permission denied" in plan.reason
