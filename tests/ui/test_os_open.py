"""Unit tests for :mod:`auto_ext.ui.os_open`.

Pure stdlib helper — no Qt fixtures needed. Per-platform dispatch is
exercised by patching ``sys.platform`` plus the launcher entrypoint
(``os.startfile`` on Windows, ``subprocess.Popen`` everywhere else).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.ui import os_open
from auto_ext.ui.os_open import open_in_os


def _phase59_bc_make_file(tmp_path: Path, name: str = "rendered.qci") -> Path:
    p = tmp_path / name
    p.write_text("stub\n", encoding="utf-8")
    return p


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

        class _Dummy:
            pass

        return _Dummy()

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

        class _Dummy:
            pass

        return _Dummy()

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
