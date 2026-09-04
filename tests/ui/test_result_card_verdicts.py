"""What the card says about a run whose record does not say "failed".

Three ways a card could look right and be wrong, all of them about a verdict
rather than a layout:

* a stage that **exited 0 and wrote nothing** was drawn as a plain green pass,
  and the output it never wrote was blamed on the host ("Not on this host"),
* a run with **no stages at all** was drawn as PASSED over an empty stage
  strip -- the card's one job, "do I have to re-check this by hand?", answered
  no by a run that never looked,
* a run started from **another project** offered that project's directories
  with nothing saying whose they were.

Everything here drives ``ResultCard.set_run`` and reads what is on screen:
the chip texts and tones of the stage strip, the rows of the Outputs grid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5")
pytest.importorskip("pytestqt")

from PyQt5.QtWidgets import QLabel  # noqa: E402

from auto_ext.core.progress import StageStatus, TaskStatus  # noqa: E402
from auto_ext.model.run import StageRecord  # noqa: E402
from auto_ext.ui.widgets import failure_chip as fc  # noqa: E402
from auto_ext.ui.widgets import result_card as rc  # noqa: E402
from auto_ext.ui.widgets.failure_chip import Chip, PathLabel  # noqa: E402
from auto_ext.ui.widgets.result_card import ResultCard  # noqa: E402


def _artifact_rows(card: ResultCard) -> dict[str, PathLabel]:
    """``{label: PathLabel}`` for the Outputs grid, in row order."""

    grid = card._artifacts_layout
    out: dict[str, PathLabel] = {}
    for row in range(grid.rowCount()):
        name_item = grid.itemAtPosition(row, 0)
        value_item = grid.itemAtPosition(row, 1)
        if name_item is None or value_item is None:
            continue
        name = name_item.widget()
        value = value_item.widget()
        if isinstance(name, QLabel) and isinstance(value, PathLabel):
            out[name.text()] = value
    return out


def _strip_chips(card: ResultCard) -> list[Chip]:
    """The pills the user sees in the stage strip, left to right."""

    layout = card._stage_chip_layout
    return [
        item.widget()
        for index in range(layout.count())
        if isinstance((item := layout.itemAt(index)).widget(), Chip)
    ]


def _stage_tree_rows(card: ResultCard) -> dict[str, list[str]]:
    """``{stage key: [child column-0 texts]}`` for the per-stage table."""

    tree = card._stage_tree
    out: dict[str, list[str]] = {}
    for index in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(index)
        out[item.text(0)] = [item.child(i).text(0) for i in range(item.childCount())]
    return out


# ---- a stage that exited 0 and wrote nothing --------------------------------


@pytest.fixture
def run_with_a_missing_dspf(make_run_record, tmp_path: Path):
    """Quantus exited 0; the DSPF it declared is not on disk.

    ``Tool.with_artifacts`` files a declared-but-absent output under
    ``diagnostics["missing_artifacts"]`` and leaves ``success`` alone, so the
    runner records the stage as passed. That is the honest record of what
    happened -- the tool really did exit 0 -- and it is the card's job to stop
    it reading as a clean run.
    """

    dspf = tmp_path / "work" / "amp2.dspf"  # declared, never written
    return make_run_record(
        overall=TaskStatus.PASSED,
        dspf_path=str(dspf),
        stages=[
            StageRecord(
                key="si", stage="si", status=StageStatus.PASSED, exit_code=0
            ),
            StageRecord(
                key="quantus.dspf",
                stage="quantus",
                status=StageStatus.PASSED,
                exit_code=0,
                details={"exit_code": 0, "missing_artifacts": [str(dspf)]},
            ),
        ],
    ), dspf


def test_a_stage_that_wrote_nothing_is_not_a_plain_pass(
    qtbot, run_with_a_missing_dspf
) -> None:
    record, _dspf = run_with_a_missing_dspf
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)

    chips = {chip.text().split()[0]: chip for chip in _strip_chips(card)}
    assert "quantus" in chips
    quantus = chips["quantus"]
    assert "missing" in quantus.text().lower(), quantus.text()
    assert "output" in quantus.text().lower()
    # si really did pass and must still read as a pass.
    assert "missing" not in chips["si"].text().lower()


def test_the_missing_output_chip_is_not_coloured_like_a_pass(
    run_with_a_missing_dspf,
) -> None:
    record, _dspf = run_with_a_missing_dspf
    by_stage = {chip.stage: chip for chip in rc.stage_chips(record)}
    assert by_stage["quantus"].tone == fc.CHIP_TONE_WARNING
    assert by_stage["si"].tone == fc.CHIP_TONE_PASSED
    assert "never wrote" in by_stage["quantus"].tooltip.lower()


def test_the_output_row_blames_the_tool_not_the_host(
    qtbot, run_with_a_missing_dspf
) -> None:
    """"Not on this host" is the wrong story: the tool never wrote it here."""

    record, dspf = run_with_a_missing_dspf
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)

    row = _artifact_rows(card)["dspf"]
    assert not row.is_live()
    tooltip = row.toolTip()
    assert "Not on this host" not in tooltip
    assert "never wrote" in tooltip.lower() or "never written" in tooltip.lower()
    assert "quantus" in tooltip.lower()


def test_the_stage_table_lists_the_output_that_never_appeared(
    qtbot, run_with_a_missing_dspf
) -> None:
    record, dspf = run_with_a_missing_dspf
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)

    children = _stage_tree_rows(card)["quantus.dspf"]
    assert any(dspf.name in text for text in children), children


def test_an_ordinary_missing_workarea_file_still_says_not_on_this_host(
    qtbot, make_run_record
) -> None:
    """The old wording is right when the tool never declared the output.

    A workarea copy that a later run overwrote, or a run record opened on
    another machine, is exactly "not on this host" -- the regression must not
    swallow that case too.
    """

    record = make_run_record(
        overall=TaskStatus.PASSED,
        dspf_path="/wa/amp2.dspf",
        stages=[StageRecord(key="quantus", stage="quantus", status=StageStatus.PASSED)],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)
    assert _artifact_rows(card)["dspf"].toolTip().startswith("Not on this host")


# ---- a run with no stages at all -------------------------------------------


def test_a_run_that_ran_nothing_does_not_draw_an_empty_strip(
    qtbot, make_run_record
) -> None:
    """A green PASSED over no chips at all is the worst thing the card can say.

    The runner refuses this dispatch now, but records written before that fix
    are still in the history and still open in this card.
    """

    record = make_run_record(overall=TaskStatus.PASSED, stages=[], requested_stages=[])
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)

    chips = _strip_chips(card)
    assert chips, "an empty stage strip says nothing at all"
    assert "no stage" in chips[0].text().lower()
    assert chips[0].tone == fc.CHIP_TONE_WARNING


def test_a_run_with_stages_is_unaffected(make_run_record) -> None:
    record = make_run_record(
        overall=TaskStatus.PASSED,
        requested_stages=["si"],
        stages=[StageRecord(key="si", stage="si", status=StageStatus.PASSED)],
    )
    chips = rc.stage_chips(record)
    assert chips[0].stage == "si"
    assert not any("no stage" in c.text for c in chips)


# ---- a run from another project --------------------------------------------


@pytest.fixture
def foreign_run(make_run_record, tmp_path: Path):
    """A run recorded by project B, opened while project A is loaded."""

    other = tmp_path / "projectB"
    record = make_run_record(
        overall=TaskStatus.PASSED,
        workspace_dir=str(other / "cds" / "verify" / "QCI_PATH_amp2"),
        workarea=str(other / "workarea"),
        config_dir=str(other / "config"),
        stages=[StageRecord(key="si", stage="si", status=StageStatus.PASSED)],
    )
    return record, other


def test_a_card_names_the_workarea_and_project_its_paths_belong_to(
    qtbot, foreign_run
) -> None:
    """Identity is on the card, not only in run.json."""

    record, other = foreign_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_run(record)

    rows = _artifact_rows(card)
    assert rows["workarea"].full_text() == str(other / "workarea")
    assert rows["project"].full_text() == str(other / "config")


def test_a_run_from_another_project_is_marked_as_such(qtbot, foreign_run) -> None:
    """With the open project known, the row says these are someone else's."""

    record, other = foreign_run
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_session_project(
        config_dir=other.parent / "projectA" / "config",
        workarea=other.parent / "projectA" / "workarea",
    )
    card.set_run(record)

    rows = _artifact_rows(card)
    marked = [label for label in rows if "another project" in label]
    assert marked, sorted(rows)
    named = "\n".join(rows[label].full_text() + rows[label].toolTip() for label in marked)
    assert str(other / "workarea") in named


def test_the_open_project_is_not_marked_as_foreign(qtbot, make_run_record, tmp_path) -> None:
    here = tmp_path / "projectA"
    record = make_run_record(
        overall=TaskStatus.PASSED,
        workarea=str(here / "workarea"),
        config_dir=str(here / "config"),
        stages=[StageRecord(key="si", stage="si", status=StageStatus.PASSED)],
    )
    card = ResultCard()
    qtbot.addWidget(card)
    card.set_session_project(
        config_dir=here / "config", workarea=here / "workarea"
    )
    card.set_run(record)

    assert not [label for label in _artifact_rows(card) if "another project" in label]
