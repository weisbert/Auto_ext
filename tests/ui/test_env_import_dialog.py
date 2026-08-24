"""The GUI over "read this project's environment out of what it produced".

The engine is tested in ``tests/core/test_env_import.py``. What is left here is
the part a user actually faces, and one property matters more than the rest:
**a row must not be pre-ticked unless accepting it is both a change and
unambiguous.** Pinning is not free -- it freezes today's answer into a file
that travels to the next machine -- so a default-on checkbox is a
recommendation, and recommending a pin that changes nothing, or one the files
themselves disagree about, is worse than offering no default at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtCore import Qt  # noqa: E402

from auto_ext.model.common import RenderTarget  # noqa: E402
from auto_ext.ui.widgets.env_import_dialog import (  # noqa: E402
    COLUMNS,
    EnvImportDialog,
    in_effect,
)
from tests.core.test_recipe_import import _render_all  # noqa: E402
from tests.support.v2 import ENV, make_profile  # noqa: E402


@pytest.fixture
def produced(tmp_path: Path) -> list[Path]:
    """Files this project would really have produced, on disk."""

    written: list[Path] = []
    for target, text in _render_all(tmp_path).items():
        path = tmp_path / target.value
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


@pytest.fixture
def dialog(qtbot, produced: list[Path]) -> EnvImportDialog:
    widget = EnvImportDialog(profile=make_profile())
    qtbot.addWidget(widget)
    widget.set_files(produced)
    return widget


def _row_of(dialog: EnvImportDialog, name: str) -> int:
    for row in range(dialog._table.rowCount()):
        if dialog._table.item(row, 1).text() == name:
            return row
    raise AssertionError(f"{name} is not in the table")


# ---- reading ---------------------------------------------------------------


def test_the_scan_button_needs_files(qtbot) -> None:
    widget = EnvImportDialog(profile=make_profile())
    qtbot.addWidget(widget)
    assert widget._scan_btn.isEnabled() is False
    widget.set_files([Path("somewhere/si.env")])
    assert widget._scan_btn.isEnabled() is True


def test_reading_fills_a_row_per_variable(
    dialog: EnvImportDialog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SETUP_ROOT", raising=False)
    assert dialog.scan() is True
    result = dialog.result()
    assert result is not None and result.solved
    assert dialog._table.rowCount() == len(result.solved)
    assert _row_of(dialog, "SETUP_ROOT") >= 0


def test_every_row_says_where_the_value_came_from(dialog: EnvImportDialog) -> None:
    dialog.scan()
    source_column = COLUMNS.index("read from")
    for row in range(dialog._table.rowCount()):
        text = dialog._table.item(row, source_column).text()
        assert ":" in text, "the row must name the file and how it was read"


def test_unreadable_files_are_reported_not_fatal(
    dialog: EnvImportDialog, tmp_path: Path
) -> None:
    junk = tmp_path / "NOTES.md"
    junk.write_text("# notes\nalpha = 1\n" * 20, encoding="utf-8")
    dialog.set_files(dialog.files() + [junk])

    assert dialog.scan() is True
    assert dialog._table.rowCount() > 0
    assert "NOTES.md" in dialog.status()


def test_nothing_readable_shows_the_refusal_and_clears_the_table(
    qtbot, tmp_path: Path
) -> None:
    junk = tmp_path / "NOTES.md"
    junk.write_text("# notes\nalpha = 1\n" * 20, encoding="utf-8")
    widget = EnvImportDialog(profile=make_profile())
    qtbot.addWidget(widget)
    widget.set_files([junk])

    with qtbot.waitSignal(widget.scan_failed):
        assert widget.scan() is False
    assert widget._table.rowCount() == 0
    assert widget.result() is None
    assert "NOTES.md" in widget.status()


# ---- what is pre-ticked ----------------------------------------------------


def test_a_value_that_would_change_something_is_pre_ticked(
    qtbot, produced: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SETUP_ROOT", raising=False)
    widget = EnvImportDialog(profile=make_profile())
    qtbot.addWidget(widget)
    widget.set_files(produced)
    widget.scan()

    row = _row_of(widget, "SETUP_ROOT")
    assert widget._table.item(row, 0).checkState() == Qt.Checked
    assert widget.ticked()["SETUP_ROOT"] == ENV["SETUP_ROOT"]


def test_a_value_already_in_effect_is_not_recommended(
    qtbot, produced: list[Path]
) -> None:
    """Pinning what is already in force only freezes today's answer."""

    profile = make_profile(env_overrides=dict(ENV))
    widget = EnvImportDialog(profile=profile)
    qtbot.addWidget(widget)
    widget.set_files(produced)
    widget.scan()

    assert widget.ticked() == {}
    assert widget._accept_btn.isEnabled() is False
    row = _row_of(widget, "SETUP_ROOT")
    assert "already in effect" in widget._table.item(row, 0).toolTip().lower()


def test_a_disagreement_is_never_pre_ticked(
    qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-ticked answer to a question is not a question."""

    monkeypatch.delenv("SETUP_ROOT", raising=False)
    texts = _render_all(tmp_path)
    written: list[Path] = []
    for target, text in texts.items():
        if target is RenderTarget.QUANTUS_EXT:
            text = text.replace(ENV["SETUP_ROOT"], "/other/setup")
        path = tmp_path / target.value
        path.write_text(text, encoding="utf-8")
        written.append(path)

    widget = EnvImportDialog(profile=make_profile())
    qtbot.addWidget(widget)
    widget.set_files(written)
    widget.scan()

    row = _row_of(widget, "SETUP_ROOT")
    assert widget._table.item(row, 0).checkState() == Qt.Unchecked
    assert "disagree" in widget._table.item(row, 0).toolTip().lower()
    # and both answers are visible, not just the winner
    assert "also:" in widget._table.item(row, 2).text()


def test_the_user_can_still_tick_a_row_that_was_not_recommended(
    qtbot, produced: list[Path]
) -> None:
    """A recommendation is not a rule; the decision stays the user's."""

    widget = EnvImportDialog(profile=make_profile(env_overrides=dict(ENV)))
    qtbot.addWidget(widget)
    widget.set_files(produced)
    widget.scan()
    assert widget.ticked() == {}

    widget.set_ticked({"SETUP_ROOT"})
    assert widget.ticked() == {"SETUP_ROOT": ENV["SETUP_ROOT"]}
    assert widget._accept_btn.isEnabled() is True


# ---- the "in effect" column ------------------------------------------------


def test_in_effect_prefers_a_pin_over_the_shell() -> None:
    """The precedence is the profile's, not this dialog's."""

    from auto_ext.core.env_import import SolvedEnvVar

    var = SolvedEnvVar(
        name="SETUP_ROOT",
        value="/from/file",
        via="",
        source="",
        shell_value="/from/shell",
        pinned_value="/from/pin",
    )
    assert "/from/pin" in in_effect(var)
    assert "pinned" in in_effect(var)


def test_in_effect_falls_back_to_the_shell_then_to_nothing() -> None:
    from auto_ext.core.env_import import SolvedEnvVar

    shell_only = SolvedEnvVar("X", "/v", "", "", shell_value="/from/shell")
    assert "shell" in in_effect(shell_only)
    assert "nothing" in in_effect(SolvedEnvVar("X", "/v", "", ""))


# ---- accepting -------------------------------------------------------------


def test_accepting_hands_out_only_the_ticked_rows(
    dialog: EnvImportDialog, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SETUP_ROOT", raising=False)
    dialog.scan()
    dialog.set_ticked({"SETUP_ROOT"})

    with qtbot.waitSignal(dialog.values_accepted) as caught:
        dialog._accept_btn.click()
    assert caught.args == [{"SETUP_ROOT": ENV["SETUP_ROOT"]}]


def test_the_dialog_writes_nothing(dialog: EnvImportDialog, tmp_path: Path) -> None:
    """Adopting a site fact is a decision, and the host owns the disk."""

    before = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*") if path.is_file()}
    dialog.scan()
    dialog.set_ticked(set(dialog.ticked()) | {"SETUP_ROOT"})
    dialog._accept_btn.click()
    after = {path: path.stat().st_mtime_ns for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before
