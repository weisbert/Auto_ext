"""Tests for :class:`auto_ext.ui.widgets.log_view.LogView`.

The tailer that used to be ``tabs/log_tab.py``. Its two non-obvious jobs are
appending only new bytes and surviving a file that is replaced or truncated
underneath it -- both of which happen on the office server, where a re-run
reuses the same log path and ``/data`` misses watcher events.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from auto_ext.ui.widgets.log_view import LogView  # noqa: E402


@pytest.fixture
def view(qtbot) -> LogView:
    widget = LogView()
    qtbot.addWidget(widget)
    return widget


def test_a_fresh_view_shows_nothing(view: LogView) -> None:
    assert view.path is None
    assert view.text() == ""


def test_pointing_at_a_file_shows_what_is_already_in_it(
    view: LogView, tmp_path: Path
) -> None:
    log = tmp_path / "calibre.log"
    log.write_text("line one\nline two\n", encoding="utf-8")

    view.set_active_log(log)

    assert view.path == log
    # Byte-for-byte, trailing newline included: the tailer inserts at the
    # document end rather than appending, so the file's own line endings are
    # what shows.
    assert view.text() == "line one\nline two\n"


def test_a_missing_log_is_created_so_the_watcher_has_a_target(
    view: LogView, tmp_path: Path
) -> None:
    """A stage log does not exist until the tool writes its first line, which
    is exactly the moment the user wants to be watching."""

    log = tmp_path / "runs" / "r1" / "logs" / "quantus.log"
    view.set_active_log(log)
    assert log.is_file()


def test_new_bytes_are_appended_not_re_read(view: LogView, tmp_path: Path) -> None:
    log = tmp_path / "si.log"
    log.write_text("first\n", encoding="utf-8")
    view.set_active_log(log)

    with log.open("a", encoding="utf-8") as handle:
        handle.write("second\n")
    view._append_new_content()

    assert view.text().splitlines() == ["first", "second"]


def test_a_truncated_file_restarts_the_view(view: LogView, tmp_path: Path) -> None:
    """A re-run reusing the same path must not show the old run's tail."""

    log = tmp_path / "si.log"
    log.write_text("old run output\n", encoding="utf-8")
    view.set_active_log(log)

    log.write_text("new\n", encoding="utf-8")
    view._append_new_content()

    assert view.text().splitlines() == ["new"]


def test_switching_files_clears_the_previous_one(
    view: LogView, tmp_path: Path
) -> None:
    first = tmp_path / "a.log"
    first.write_text("aaa\n", encoding="utf-8")
    second = tmp_path / "b.log"
    second.write_text("bbb\n", encoding="utf-8")

    view.set_active_log(first)
    view.set_active_log(second)

    assert view.path == second
    assert "aaa" not in view.text()
    assert "bbb" in view.text()


def test_clearing_stops_the_poll_timer(view: LogView, tmp_path: Path) -> None:
    log = tmp_path / "a.log"
    log.write_text("x\n", encoding="utf-8")
    view.set_active_log(log)
    assert view._poll.isActive() is True

    view.set_active_log(None)
    assert view.path is None
    assert view.text() == ""
    assert view._poll.isActive() is False


def test_follow_is_on_by_default_and_settable(view: LogView) -> None:
    """The run bar owns the checkbox; this widget only holds the answer."""

    assert view.follows() is True
    view.set_follow(False)
    assert view.follows() is False


def test_undecodable_bytes_do_not_break_the_tail(
    view: LogView, tmp_path: Path
) -> None:
    """EDA tools emit latin-1 in a UTF-8 log more often than anyone admits."""

    log = tmp_path / "calibre.log"
    log.write_bytes(b"before\n\xff\xfe binary junk\nafter\n")
    view.set_active_log(log)

    assert "before" in view.text()
    assert "after" in view.text()


def test_an_unwritable_directory_is_survived(
    view: LogView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runner reports a broken run directory; the viewer just shows
    nothing rather than raising into the Qt event loop."""

    def refuse(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", refuse)
    view.set_active_log(tmp_path / "nope" / "x.log")

    assert view.text() == ""
    assert view._poll.isActive() is False
