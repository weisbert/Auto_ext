"""Unit tests for :mod:`auto_ext.ui.os_open`.

No Qt fixtures needed. Per-platform dispatch is exercised by patching
``sys.platform`` plus the launcher entrypoint (``os.startfile`` on Windows,
``subprocess.Popen`` everywhere else).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from auto_ext.ui import os_open
from auto_ext.ui.os_open import open_containing_folder, open_in_os


def _phase59_bc_make_file(tmp_path: Path, name: str = "rendered.qci") -> Path:
    p = tmp_path / name
    p.write_text("stub\n", encoding="utf-8")
    return p


class _FakeProcess:
    """Enough of ``Popen`` for :func:`auto_ext.ui.os_open._settle`.

    ``status=None`` means the launcher is still running when the settle window
    expires, which is what a launcher that *is* the viewer's parent does and
    which the module reads as success.
    """

    def __init__(self, status: int | None = 0) -> None:
        self.status = status
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout if timeout is not None else -1.0)
        if self.status is None:
            raise subprocess.TimeoutExpired(cmd="launcher", timeout=timeout or 0)
        return self.status


def test_phase59_bc_open_in_os_dispatches_startfile_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _phase59_bc_make_file(tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(os_open.sys, "platform", "win32")

    def fake_startfile(arg: str) -> None:
        calls.append(arg)

    # os.startfile is Windows-only; on a real Linux test host the attr
    # may be missing entirely. Using setattr with raising=False adds it
    # for the duration of the test.
    monkeypatch.setattr(os_open.os, "startfile", fake_startfile, raising=False)

    open_in_os(target)
    assert calls == [str(target)]


def test_phase59_bc_open_in_os_dispatches_open_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _phase59_bc_make_file(tmp_path)
    calls: list[list[str]] = []

    monkeypatch.setattr(os_open.sys, "platform", "darwin")

    def fake_popen(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        return _FakeProcess(0)

    monkeypatch.setattr(os_open.subprocess, "Popen", fake_popen)

    open_in_os(target)
    assert calls == [["open", str(target)]]


def test_phase59_bc_open_in_os_dispatches_xdg_open_on_linux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _phase59_bc_make_file(tmp_path)
    calls: list[list[str]] = []

    monkeypatch.setattr(os_open.sys, "platform", "linux")

    def fake_popen(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        return _FakeProcess(0)

    monkeypatch.setattr(os_open.subprocess, "Popen", fake_popen)

    open_in_os(target)
    assert calls == [["xdg-open", str(target)]]


def test_phase59_bc_open_in_os_missing_path_raises_file_not_found(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "no_such_file"
    with pytest.raises(FileNotFoundError):
        open_in_os(missing)


def test_phase59_bc_open_in_os_missing_launcher_raises_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """xdg-open absent on the host → useful OSError (not bare FileNotFoundError).

    This separates "the file is gone" from "your distro lacks xdg-utils"
    so the GUI can show distinct messages.
    """
    target = _phase59_bc_make_file(tmp_path)
    monkeypatch.setattr(os_open.sys, "platform", "linux")

    def fake_popen(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise FileNotFoundError(2, "No such file or directory: 'xdg-open'")

    monkeypatch.setattr(os_open.subprocess, "Popen", fake_popen)

    with pytest.raises(OSError) as exc_info:
        open_in_os(target)
    # Path mentioned in message so users can copy-paste.
    assert str(target) in str(exc_info.value)
    # Shouldn't be the raw FileNotFoundError — caller-facing OSError.
    assert not isinstance(exc_info.value, FileNotFoundError)


# ---- S1: detached launch, launcher fallback, containing folder ----------
#
# The results panel opens three kinds of artefact with the OS default handler
# -- the extracted DSPF, the Calibre LVS report, and a stage log -- plus the
# folder that holds them. The tests above pin the per-platform dispatch; the
# ones below pin three properties that only misbehave on the office Linux
# server, never on the Windows development machine: the launcher is detached
# (a viewer cannot block the GUI and does not die with the ssh session),
# xdg-open is not the only launcher (a bare RHEL box may lack xdg-utils), and
# a relative path is made absolute first (the GUI cwd is not the user cwd).


class _Recorder:
    """Stand-in for ``subprocess.Popen`` that records argv and kwargs.

    ``missing`` names launchers whose binary is not installed (``Popen``
    itself raises); ``refuses`` names launchers that start and then exit with
    the given status, which is what ``xdg-open`` does when no application is
    registered for the file type.
    """

    def __init__(
        self,
        missing: tuple[str, ...] = (),
        refuses: dict[str, int] | None = None,
        status: int | None = 0,
    ) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self._missing = missing
        self._refuses = refuses or {}
        self._status = status

    def __call__(self, argv: list[str], **kwargs: Any) -> object:
        self.calls.append((list(argv), dict(kwargs)))
        if argv[0] in self._missing:
            raise FileNotFoundError(2, f"No such file or directory: {argv[0]!r}")
        if argv[0] in self._refuses:
            return _FakeProcess(self._refuses[argv[0]])
        return _FakeProcess(self._status)

    @property
    def argvs(self) -> list[list[str]]:
        return [argv for argv, _ in self.calls]


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "amp2.dspf"
    path.write_text("* extracted netlist\n", encoding="utf-8")
    return path


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os_open.sys, "platform", "linux")


# ---- detaching -------------------------------------------------------------


def test_viewer_is_launched_detached(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """setsid, so closing the ssh session that started Auto_ext does not SIGHUP
    the PDF viewer the user is still reading."""

    popen = _Recorder()
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(target)
    _argv, kwargs = popen.calls[0]
    assert kwargs["start_new_session"] is True


def test_viewer_streams_go_to_the_null_device(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody ever reads a launcher's output; an inherited pipe would fill up
    and block the GUI thread that wrote it."""

    popen = _Recorder()
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(target)
    _argv, kwargs = popen.calls[0]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL


def test_detach_flags_come_from_the_shared_helper(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One definition of "detached" for the whole codebase.

    ``os_open`` and :func:`auto_ext.core.handoff.launch_detached` must not
    drift apart; both read :func:`~auto_ext.core.handoff.detached_popen_kwargs`.
    """

    from auto_ext.core.handoff import detached_popen_kwargs

    popen = _Recorder()
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(target)
    assert popen.calls[0][1] == detached_popen_kwargs()


# ---- launcher fallback -----------------------------------------------------


def test_falls_back_to_gio_when_xdg_open_is_not_installed(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The office server is a bare CentOS 7 box; ``xdg-utils`` may never have
    been installed, while the GLib tools usually have been."""

    popen = _Recorder(missing=("xdg-open",))
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(target)
    assert popen.argvs == [
        ["xdg-open", str(target)],
        ["gio", "open", str(target)],
    ]


def test_error_names_every_launcher_that_was_tried(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = _Recorder(missing=("xdg-open", "gio"))
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    with pytest.raises(OSError) as exc_info:
        open_in_os(target)
    message = str(exc_info.value)
    assert "xdg-open" in message
    assert "gio" in message
    assert str(target) in message


def test_a_launcher_that_cannot_be_executed_is_not_retried(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launcher that cannot be *started* is a real problem the user must see.

    Permission denied, exec format error: a second launcher would not fix any
    of those, so they are raised rather than swallowed. (A launcher that does
    start and then exits non-zero is a different case -- see below.)
    """

    attempts: list[list[str]] = []

    def denied(argv: list[str], **kwargs: Any) -> object:
        attempts.append(list(argv))
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os_open.subprocess, "Popen", denied)
    with pytest.raises(OSError) as exc_info:
        open_in_os(target)
    assert attempts == [["xdg-open", str(target)]]
    assert str(target) in str(exc_info.value)


def test_macos_uses_open_and_has_no_fallback(
    target: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = _Recorder(missing=("open",))
    monkeypatch.setattr(os_open.sys, "platform", "darwin")
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    with pytest.raises(OSError):
        open_in_os(target)
    assert popen.argvs == [["open", str(target)]]


# ---- paths ------------------------------------------------------------------


def test_a_relative_path_is_made_absolute_before_launching(
    tmp_path: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The GUI's cwd is not the user's cwd, and it is not guaranteed to stay
    put; a launcher handed a relative path is a latent bug."""

    (tmp_path / "amp2.lvs.report").write_text("CORRECT\n", encoding="utf-8")
    popen = _Recorder()
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)
    monkeypatch.chdir(tmp_path)

    open_in_os(Path("amp2.lvs.report"))
    launched = Path(popen.argvs[0][1])
    assert launched.is_absolute()
    assert launched.name == "amp2.lvs.report"


def test_a_directory_can_be_opened_too(
    tmp_path: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``svdb/`` and the run directory are directories, and the file manager is
    the right viewer for them."""

    svdb = tmp_path / "svdb"
    svdb.mkdir()
    popen = _Recorder()
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(svdb)
    assert popen.argvs == [["xdg-open", str(svdb)]]


def test_windows_startfile_receives_an_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "calibre.log").write_text("done\n", encoding="utf-8")
    calls: list[str] = []
    monkeypatch.setattr(os_open.sys, "platform", "win32")
    monkeypatch.setattr(
        os_open.os, "startfile", lambda arg: calls.append(arg), raising=False
    )
    monkeypatch.chdir(tmp_path)

    open_in_os(Path("calibre.log"))
    assert Path(calls[0]).is_absolute()


# ---- open_containing_folder -------------------------------------------------


def test_containing_folder_of_a_file_is_its_parent(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = _Recorder()
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_containing_folder(target)
    assert popen.argvs == [["xdg-open", str(target.parent)]]


def test_containing_folder_of_a_directory_is_itself(
    tmp_path: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """So "show me the run directory" and "show me where this log lives" are
    the same call."""

    run_dir = tmp_path / "20260821T143205Z_amp2-ext"
    run_dir.mkdir()
    popen = _Recorder()
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_containing_folder(run_dir)
    assert popen.argvs == [["xdg-open", str(run_dir)]]


def test_containing_folder_of_a_missing_path_raises_file_not_found(
    tmp_path: Path, linux: None
) -> None:
    with pytest.raises(FileNotFoundError):
        open_containing_folder(tmp_path / "never_written.dspf")


# ---- M-21: a launcher that starts and then does nothing --------------------
#
# The symptom this section exists for: "I click Open LVS report and nothing
# happens. No window, no error, nothing." xdg-open is installed, it starts, it
# finds no application registered for the file type, and it exits 3 -- long
# after the old code had stopped watching.


def test_a_launcher_that_refuses_the_file_is_reported(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    popen = _Recorder(refuses={"xdg-open": 3, "gio": 3})
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    with pytest.raises(OSError) as exc_info:
        open_in_os(target)
    message = str(exc_info.value)
    assert str(target) in message
    assert "xdg-open exited 3" in message
    assert "gio exited 3" in message


def test_the_next_launcher_is_tried_when_the_first_one_refuses(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """xdg-utils installed but useless, GLib's opener right behind it."""

    popen = _Recorder(refuses={"xdg-open": 4})
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(target)
    assert popen.argvs == [
        ["xdg-open", str(target)],
        ["gio", "open", str(target)],
    ]


def test_a_launcher_still_running_after_the_settle_window_counts_as_success(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """xdg-open's generic fallback *is* the viewer's parent and never exits.

    Waiting for it would be waiting for the user to close the document, so the
    wait is bounded and "still running" is the answer we want.
    """

    popen = _Recorder(status=None)
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(target)
    assert popen.argvs == [["xdg-open", str(target)]]


def test_the_gui_thread_is_never_blocked_for_longer_than_the_settle_window(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    processes: list[_FakeProcess] = []

    def popen(argv: list[str], **kwargs: Any) -> object:
        process = _FakeProcess(None)
        processes.append(process)
        return process

    monkeypatch.setattr(os_open.subprocess, "Popen", popen)
    open_in_os(target)
    assert processes[0].waits == [os_open.LAUNCH_SETTLE_S]
    assert os_open.LAUNCH_SETTLE_S <= 1.0, "this blocks the GUI thread"


def test_settle_zero_is_the_old_fire_and_forget(
    target: Path, linux: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller that must not block can still have the old behaviour."""

    popen = _Recorder(refuses={"xdg-open": 3})
    monkeypatch.setattr(os_open.subprocess, "Popen", popen)

    open_in_os(target, settle_s=0)  # does not raise: nothing was watched
    assert popen.argvs == [["xdg-open", str(target)]]


# ---- can this host open a file at all? -------------------------------------


def test_can_open_is_false_when_no_launcher_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The office server: no xdg-utils, no GLib tools, X11 forwarding only."""

    monkeypatch.setattr(os_open.shutil, "which", lambda _name: None)
    assert os_open.can_open("linux") is False
    assert os_open.file_opener("linux") is None


def test_can_open_names_the_launcher_it_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        os_open.shutil, "which", lambda name: "/usr/bin/gio" if name == "gio" else None
    )
    assert os_open.can_open("linux") is True
    assert os_open.file_opener("linux") == "/usr/bin/gio"


def test_windows_can_always_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """ShellExecuteW is part of the OS; there is nothing to look for on PATH."""

    monkeypatch.setattr(os_open.shutil, "which", lambda _name: None)
    assert os_open.can_open("win32") is True
    assert os_open.launcher_names("win32") == ()


def test_the_launcher_list_is_the_one_the_probes_report() -> None:
    """core/health.py cannot import this module (core never imports ui) and
    deploy/doctor.sh is not Python at all, so the three lists are asserted to
    agree here rather than trusted to."""

    from pathlib import Path as _Path

    from auto_ext.core import health

    assert os_open.launcher_names("linux") == health.FILE_OPENERS

    doctor = _Path(__file__).resolve().parents[2] / "deploy" / "doctor.sh"
    text = doctor.read_text(encoding="utf-8")
    for name in health.FILE_OPENERS:
        assert name in text, f"deploy/doctor.sh does not probe {name}"
