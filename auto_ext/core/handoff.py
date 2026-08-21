"""Hand a finished run back to Calibre Interactive, and launch it detached.

Why this exists
---------------
LVS fails often, and the batch verdict is not what the user acts on: they
re-check every questionable result in the real Calibre Interactive GUI before
believing it. Without this module that re-check means configuring Calibre by
hand a second time -- pointing it at the same rules file, the same layout, the
same netlist, the same run directory -- which is both tedious and a chance to
check something *different* from what the batch run actually did.

Everything Calibre Interactive needs is already on disk when a run finishes:
the rendered runset (``rendered/lvs.qci``, archived inside the run directory),
the workarea it ran in, and the environment it resolved. So the handoff is
deliberately *not* a second configuration pass. It re-uses the recorded
artefacts of one specific run::

    batch (what the runner did):  calibre -gui -lvs -runset <rendered.qci> -batch
    handoff (what this does):     calibre -gui -lvs -runset <rendered.qci>

Same runset, same cwd, same env; only ``-batch`` is dropped. That is the whole
idea: "open *this* run", never "configure it again". Nothing here re-renders a
template or re-resolves a path -- every value is read back out of the
:class:`~auto_ext.model.run.RunRecord`.

UNVERIFIED ASSUMPTIONS
----------------------
Neither of the following has been confirmed against a real Calibre install,
because no EDA binary exists on the development machine:

1. **The interactive argv.** That ``calibre -gui -lvs -runset <file>`` (the
   batch command line minus ``-batch``) is the correct way to open an existing
   runset in the interactive GUI is an assumption. Calibre may want a different
   flag, or may need the runset loaded from the GUI's own File menu instead.
   Every argv decision is therefore concentrated in
   :func:`calibre_interactive_argv` / :data:`BATCH_FLAG` / :data:`RUNSET_FLAG`
   so correcting it is a one-place edit, and :func:`build_calibre_interactive_argv`
   prefers the argv the run *actually* used over any locally built one.
2. **X11 forwarding.** Whether a GUI Calibre started this way survives on the
   office server over ``ssh -X`` has not been tested. :func:`plan_calibre_handoff`
   raises a non-blocking warning when the effective environment carries no
   display, but it cannot prove the display works.

Relationship to :mod:`auto_ext.tools.base`
------------------------------------------
:func:`auto_ext.tools.base.run_subprocess` is the opposite of what is wanted
here: it blocks, tees output into a stage log, and stays cancellable, because
the runner owns that process. Calibre Interactive is the *user's* process --
it must not block the GUI thread, must not be killed when Auto_ext exits, and
its output is its own business. Hence :func:`launch_detached`, which shares no
code with ``run_subprocess`` on purpose.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from auto_ext.core.errors import AutoExtError
from auto_ext.model.run import RunRecord, StageRecord, StageStatus

__all__ = [
    "BATCH_FLAG",
    "CALIBRE_EXECUTABLE",
    "CALIBRE_STAGE",
    "RUNSET_FLAG",
    "HandoffError",
    "HandoffPlan",
    "build_calibre_interactive_argv",
    "calibre_interactive_argv",
    "detached_popen_kwargs",
    "find_calibre_stage",
    "handoff_env",
    "launch_calibre_interactive",
    "launch_detached",
    "plan_calibre_handoff",
]

#: Stage name (``auto_ext.core.runner.STAGE_ORDER``) that produces a runset.
CALIBRE_STAGE = "calibre"

#: Default executable name. Resolved through the effective ``PATH`` before use.
CALIBRE_EXECUTABLE = "calibre"

#: The flag that makes Calibre run headless. Dropping it *is* the handoff.
BATCH_FLAG = "-batch"

#: The flag whose value is the runset (``.qci``) file.
RUNSET_FLAG = "-runset"

#: Environment variables that indicate a usable display on POSIX. Missing all
#: of them is a warning, not a refusal -- the user may be about to set one, and
#: refusing to launch on this basis would be guessing.
_DISPLAY_VARS = ("DISPLAY", "WAYLAND_DISPLAY")

# Windows process-creation flags, looked up defensively so this module imports
# (and its platform dispatch stays unit-testable) on Linux, where
# ``subprocess`` does not define them. The literals are the Win32 API values.
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


class HandoffError(AutoExtError):
    """A run record does not carry enough information to hand off.

    Raised only by the pure builder :func:`build_calibre_interactive_argv`.
    :func:`plan_calibre_handoff` catches it and turns the message into a
    user-facing reason, so GUI call sites never have to handle an exception.
    """


# ---- argv construction (the one place the command line is spelled out) ------


def calibre_interactive_argv(executable: str, runset: Path | str) -> list[str]:
    """Build the interactive command line from scratch.

    This is the *fallback*: it is used when the recorded stage argv is
    unusable. The preferred path is to take the argv the batch run really used
    and drop :data:`BATCH_FLAG`, which cannot drift from reality the way a
    locally rebuilt command line can.

    UNVERIFIED: see the module docstring -- this exact argv has never been run
    against a real Calibre.
    """

    return [executable, "-gui", "-lvs", RUNSET_FLAG, str(runset)]


def _strip_batch_flag(argv: Sequence[str]) -> list[str]:
    """Drop every :data:`BATCH_FLAG` occurrence. This is the whole handoff."""

    return [arg for arg in argv if arg != BATCH_FLAG]


def _runset_from_argv(argv: Sequence[str]) -> str | None:
    """Return the value that followed :data:`RUNSET_FLAG`, or ``None``."""

    for index, arg in enumerate(argv):
        if arg == RUNSET_FLAG and index + 1 < len(argv):
            return argv[index + 1]
    return None


def _retarget_runset(argv: Sequence[str], runset: Path) -> list[str]:
    """Point the ``-runset`` value at ``runset``, keeping everything else.

    The recorded argv names the runset the batch run consumed. That file lives
    inside the run directory, so it normally *is* ``runset`` already; the two
    differ only when the run directory has been moved or shipped elsewhere, in
    which case the archived copy is the one that still exists.
    """

    out = list(argv)
    for index, arg in enumerate(out):
        if arg == RUNSET_FLAG and index + 1 < len(out):
            out[index + 1] = str(runset)
            return out
    return out


# ---- reading the record ------------------------------------------------------


def find_calibre_stage(record: RunRecord) -> StageRecord | None:
    """Return the calibre stage of ``record``, or ``None`` if it has none.

    When a record carries more than one calibre stage (not produced today, but
    the ``"quantus.ext"`` / ``"quantus.dspf"`` key convention allows it), the
    last one that archived a rendered runset wins, falling back to the last
    calibre stage of any shape.
    """

    candidates = [
        stage
        for stage in record.stages
        if stage.stage == CALIBRE_STAGE or stage.key.split(".")[0] == CALIBRE_STAGE
    ]
    if not candidates:
        return None
    with_runset = [stage for stage in candidates if stage.rendered_path]
    return (with_runset or candidates)[-1]


def _resolve_runset(record: RunRecord, stage: StageRecord) -> Path:
    """Locate the runset this stage actually used.

    Preference order, both entries being recorded facts rather than recomputed
    paths:

    1. ``stage.rendered_path`` under ``record.run_dir`` -- the archived copy,
       which is immutable and survives the workarea being overwritten.
    2. the ``-runset`` value inside ``stage.argv`` -- the absolute path handed
       to the batch subprocess. Used when the record has no ``run_dir``.
    """

    if stage.rendered_path and record.run_dir:
        return Path(record.run_dir).joinpath(*stage.rendered_path.split("/"))

    from_argv = _runset_from_argv(stage.argv)
    if from_argv:
        return Path(from_argv)

    raise HandoffError(
        f"Run {record.run_id} recorded no LVS runset for its {stage.key} stage "
        "(neither an archived rendered file nor a -runset argument), so there is "
        "nothing for Calibre Interactive to open."
    )


def _resolve_cwd(record: RunRecord, stage: StageRecord) -> Path:
    """Locate the working directory this stage ran in.

    ``stage.cwd`` is the exact cwd of the batch subprocess and is therefore
    always preferred; the record-level directories are fallbacks for records
    whose stage never got that far (a dry run, for instance).
    """

    for candidate in (stage.cwd, record.work_dir, record.workarea, record.workspace_dir):
        if candidate:
            return Path(candidate)
    raise HandoffError(
        f"Run {record.run_id} recorded no working directory for its {stage.key} "
        "stage, so Calibre Interactive has nowhere to start."
    )


def handoff_env(
    record: RunRecord, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Rebuild the environment the recorded run resolved.

    Starts from the *current* environment (Calibre needs far more than the
    handful of variables Auto_ext discovers: ``PATH``, ``MGC_HOME``,
    ``LM_LICENSE_FILE``, ``DISPLAY``, ...) and lays the run's recorded bindings
    on top. Bindings with source ``missing`` are skipped: they had no value
    during the run either, and injecting an empty string is not the same thing
    as leaving a variable unset.

    Recorded ``shell`` bindings deliberately win over the current shell's
    value. The point of the handoff is to reproduce *that* run; if ``$WORK_ROOT``
    has changed meaning since, replaying the recorded value is what keeps the
    interactive session pointed at the same data.
    """

    env = dict(os.environ if environ is None else environ)
    for binding in record.env:
        if binding.source == "missing":
            continue
        env[binding.name] = binding.value
    return env


# ---- the plan ----------------------------------------------------------------


@dataclass(frozen=True)
class HandoffPlan:
    """Everything needed to launch, or the reason we cannot.

    A plan always comes back complete; :attr:`reasons` being non-empty is what
    marks it unlaunchable. GUI call sites therefore need no exception
    handling::

        plan = plan_calibre_handoff(record)
        if not plan.ok:
            QMessageBox.warning(self, "Cannot open Calibre Interactive", plan.reason)
            return
        launch_detached(plan.argv, plan.cwd, plan.env)
    """

    argv: tuple[str, ...]
    cwd: Path | None
    env: Mapping[str, str]
    runset: Path | None
    stage_key: str | None
    executable: str
    #: Blocking problems, each a complete English sentence naming the path.
    reasons: tuple[str, ...] = ()
    #: Non-blocking notes worth showing next to the command line.
    warnings: tuple[str, ...] = ()
    #: Absolute path ``executable`` resolved to, when it was found on PATH.
    executable_path: str | None = None
    #: Set by :func:`launch_calibre_interactive` once the process is running.
    pid: int | None = None

    @property
    def ok(self) -> bool:
        """True when nothing blocks the launch."""

        return not self.reasons

    @property
    def reason(self) -> str | None:
        """All blocking problems as one displayable string, or ``None``."""

        return "\n".join(self.reasons) if self.reasons else None

    @property
    def launched(self) -> bool:
        """True once a process has actually been spawned for this plan."""

        return self.pid is not None

    @property
    def command_line(self) -> str:
        """The command as the user could paste it into their own shell."""

        if sys.platform == "win32":
            return subprocess.list2cmdline(list(self.argv))
        return shlex.join(self.argv)


def build_calibre_interactive_argv(
    record: RunRecord,
    stage: StageRecord | None = None,
    *,
    executable: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[list[str], Path, dict[str, str]]:
    """Return ``(argv, cwd, env)`` for re-opening ``record`` in Calibre Interactive.

    Pure: it reads the record and touches nothing on disk, so it says nothing
    about whether those files still exist -- that is
    :func:`plan_calibre_handoff`'s job.

    ``stage`` defaults to :func:`find_calibre_stage`. ``executable`` overrides
    ``argv[0]`` (for a user-configured absolute path); ``environ`` overrides
    the base environment (``os.environ`` by default).

    Raises:
        HandoffError: the record has no calibre stage, no runset, or no
            working directory -- i.e. there is nothing to hand off.
    """

    argv, cwd, env, _runset, _stage, _warnings = _build(
        record, stage, executable=executable, environ=environ
    )
    return argv, cwd, env


def _build(
    record: RunRecord,
    stage: StageRecord | None,
    *,
    executable: str | None,
    environ: Mapping[str, str] | None,
) -> tuple[list[str], Path, dict[str, str], Path, StageRecord, list[str]]:
    """Shared body of the builder and the planner. Raises :class:`HandoffError`."""

    resolved_stage = stage if stage is not None else find_calibre_stage(record)
    if resolved_stage is None:
        raise HandoffError(
            f"Run {record.run_id} has no calibre stage, so it produced no LVS "
            "runset to open."
        )

    runset = _resolve_runset(record, resolved_stage)
    cwd = _resolve_cwd(record, resolved_stage)
    env = handoff_env(record, environ)
    warnings: list[str] = []

    recorded = list(resolved_stage.argv)
    if recorded and RUNSET_FLAG in recorded:
        argv = _retarget_runset(_strip_batch_flag(recorded), runset)
        if executable:
            argv[0] = executable
    else:
        argv = calibre_interactive_argv(executable or CALIBRE_EXECUTABLE, runset)
        if recorded:
            warnings.append(
                "The command line recorded for this run had no -runset argument, "
                "so the interactive command line was rebuilt from the built-in "
                "template."
            )

    if record.dry_run or resolved_stage.status == StageStatus.DRY_RUN:
        warnings.append(
            "This was a dry run: the runset was rendered but Calibre never ran, "
            "so there are no LVS results yet. Calibre Interactive will start from "
            "the runset."
        )

    if sys.platform != "win32" and not any(env.get(var) for var in _DISPLAY_VARS):
        warnings.append(
            "No DISPLAY is set in this environment. Calibre Interactive is a GUI "
            "and needs an X display -- reconnect with 'ssh -X' (or 'ssh -Y') and "
            "start Auto_ext from that session."
        )

    return argv, cwd, env, runset, resolved_stage, warnings


def _which(cmd: str, path: str | None = None) -> str | None:
    """Indirection point for :func:`shutil.which` (kept patchable in tests)."""

    return shutil.which(cmd, path=path)


def plan_calibre_handoff(
    record: RunRecord,
    stage: StageRecord | None = None,
    *,
    executable: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[..., str | None] | None = None,
) -> HandoffPlan:
    """Build the plan and pre-flight it. Never raises.

    Three things are checked, because all three quietly stop being true between
    a run finishing and the user asking to re-check it:

    1. the rendered runset still exists,
    2. the working directory still exists,
    3. the calibre executable is on the effective ``PATH``.

    Each failure becomes a complete English sentence in
    :attr:`HandoffPlan.reasons` naming the offending path, ready to put
    straight into a message box. All three are reported together rather than
    one at a time, so a user who has both moved the run directory and forgotten
    to source the Calibre setup script learns that in one round trip.

    On success ``argv[0]`` is replaced by the absolute path the executable
    resolved to, so the launch does not depend on the child repeating the
    ``PATH`` lookup (and so Windows ``.bat`` shims work, as in
    :func:`auto_ext.tools.base.run_subprocess`).
    """

    resolver = which if which is not None else _which
    try:
        argv, cwd, env, runset, resolved_stage, warnings = _build(
            record, stage, executable=executable, environ=environ
        )
    except HandoffError as exc:
        return HandoffPlan(
            argv=(),
            cwd=None,
            env=dict(os.environ if environ is None else environ),
            runset=None,
            stage_key=stage.key if stage is not None else None,
            executable=executable or CALIBRE_EXECUTABLE,
            reasons=(str(exc),),
        )

    reasons: list[str] = []
    if not runset.is_file():
        reasons.append(
            f"The Calibre runset for this run is missing: {runset}. It is archived "
            "inside the run directory, so either it was deleted or the run "
            "directory was moved."
        )
    if not cwd.is_dir():
        reasons.append(
            f"The working directory this run used no longer exists: {cwd}. Calibre "
            "Interactive must start there for the runset's relative paths to "
            "resolve."
        )

    resolved_exe = resolver(argv[0], path=env.get("PATH"))
    if resolved_exe is None:
        reasons.append(
            f"Calibre was not found on PATH as {argv[0]!r}. Source the Calibre "
            "setup script in the shell that starts Auto_ext, or give the full path "
            "to the calibre executable."
        )
    else:
        argv = [resolved_exe, *argv[1:]]

    return HandoffPlan(
        argv=tuple(argv),
        cwd=cwd,
        env=env,
        runset=runset,
        stage_key=resolved_stage.key,
        executable=executable or CALIBRE_EXECUTABLE,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        executable_path=resolved_exe,
    )


# ---- launching ---------------------------------------------------------------


def detached_popen_kwargs(platform: str | None = None) -> dict[str, Any]:
    """Platform keyword arguments that make a child outlive its parent.

    The single source of truth for "fire and forget" in this codebase; also
    used by :mod:`auto_ext.ui.os_open`, so the two cannot drift.

    - **Windows**: ``DETACHED_PROCESS`` gives the child no console at all (no
      black window flashing over the GUI, and closing Auto_ext's console does
      not take it down); ``CREATE_NEW_PROCESS_GROUP`` keeps a Ctrl-C or
      Ctrl-Break aimed at Auto_ext from being delivered to it as well.
    - **POSIX**: ``start_new_session=True`` calls ``setsid()``, which detaches
      the child from Auto_ext's controlling terminal, so the SIGHUP sent when
      that terminal (or the ssh session owning it) goes away is not delivered
      to Calibre. The child is re-parented to init when Auto_ext exits and
      keeps running.

    All three standard streams go to the null device: nothing to buffer,
    nothing to drain, so the caller can never be blocked by a child that writes
    more than a pipe buffer's worth with nobody reading it.

    Not tracking the returned process is intentional. :mod:`subprocess` reaps
    abandoned children on the next spawn, so a Calibre the user closes leaves
    at worst a transient zombie -- versus one waiter thread per launch, which
    is what a long-lived GUI does not need.
    """

    target = platform if platform is not None else sys.platform
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if target == "win32":
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def launch_detached(
    argv: Sequence[str],
    cwd: Path | str,
    env: Mapping[str, str] | None = None,
) -> int:
    """Spawn ``argv`` and return its pid without waiting for it.

    The semantics are the opposite of
    :func:`auto_ext.tools.base.run_subprocess` in every respect that matters:
    no log tee, no cancellation, no wait. The caller's thread returns as soon
    as the process exists, the child survives the caller exiting, and its
    output is discarded (see :func:`detached_popen_kwargs`).

    Because nothing is waited on, a non-zero *exit* is invisible here. Only a
    failure to *start* is reported.

    Raises:
        ValueError: ``argv`` is empty.
        OSError: the executable could not be started (missing, not executable,
            cwd gone). The message names the executable and the cwd so the user
            can copy-paste it. ``FileNotFoundError`` and ``NotADirectoryError``
            are re-raised as a plain :class:`OSError` so callers cannot confuse
            "Calibre would not start" with "the file you asked to open is gone"
            -- the distinction :mod:`auto_ext.ui.os_open` relies on.
    """

    if not argv:
        raise ValueError("argv must not be empty")

    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            **detached_popen_kwargs(),
        )
    except OSError as exc:
        raise OSError(f"could not start {argv[0]!r} in {cwd}: {exc}") from exc
    return proc.pid


def launch_calibre_interactive(
    record: RunRecord,
    stage: StageRecord | None = None,
    *,
    executable: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[..., str | None] | None = None,
    launch: Callable[..., int] = launch_detached,
) -> HandoffPlan:
    """Plan, pre-flight and launch in one call. Never raises.

    Returns the plan either way: :attr:`HandoffPlan.ok` false means it was
    refused before launching and :attr:`HandoffPlan.reason` says why;
    :attr:`HandoffPlan.launched` true means :attr:`HandoffPlan.pid` is running.
    A launch that fails at spawn time comes back as a plan carrying that
    failure in :attr:`HandoffPlan.reasons` too, so one message box handles
    every outcome.
    """

    plan = plan_calibre_handoff(
        record, stage, executable=executable, environ=environ, which=which
    )
    if not plan.ok:
        return plan
    if plan.cwd is None:  # pragma: no cover - _build raises instead of returning None
        return replace(plan, reasons=("This run recorded no working directory.",))
    try:
        pid = launch(plan.argv, plan.cwd, plan.env)
    except OSError as exc:
        return replace(plan, reasons=(*plan.reasons, str(exc)))
    return replace(plan, pid=pid)
