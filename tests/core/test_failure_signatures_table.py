"""The *shipped* ``failure_signatures.yaml``, not the loader that reads it.

``tests/core/test_failure_class.py`` covers the schema, the matcher and the
rule order against tables built in the test. This file asserts things about
the table the product actually installs: that it parses, that every entry
says where its wording came from, and that each entry still fires on the text
it was written against.

Where the wording comes from matters more than usual here. The project has no
captured EDA log (``docs/refactor/OFFICE_TODO.md`` first priority, backlog
TASK-010), and an invented pattern is worse than no pattern: it fires on the
wrong run and sends the user down the wrong path. So the two entries that ship
are the two whose exact text this repository can produce or cite --
:func:`auto_ext.tools.base.run_subprocess`'s own "not found" line, and the
Quantus error the runner has a dedicated workaround for -- and this file pins
both to their source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.failure_class import (
    FailureClass,
    classify_failure,
    load_signatures,
)
from auto_ext.tools.base import run_subprocess


@pytest.fixture(scope="module")
def table():
    return load_signatures()


def test_the_shipped_table_parses_and_is_not_empty(table) -> None:
    """An empty table makes half the classifier unreachable in a stock install."""

    assert len(table) > 0
    ids = [s.id for s in table.signatures]
    assert len(ids) == len(set(ids))


def test_every_shipped_signature_says_where_its_wording_came_from(table) -> None:
    """Provenance is the load-bearing part: no note, no way to review it."""

    for signature in table.signatures:
        assert signature.note, f"signature {signature.id!r} carries no note"
        assert signature.next_action, f"signature {signature.id!r} says nothing to do"


def test_the_not_found_signature_matches_the_line_the_runner_writes(
    tmp_path: Path, table
) -> None:
    """The fixture is produced by the code the pattern was written against.

    ``run_subprocess`` returns 127 for an executable that is not on PATH and
    writes its own diagnosis into the log. The exit code is the primary
    evidence and rule 3 gets there first; the signature is what still answers
    when the code did not survive into the record -- a stage that died before
    it was stored, an older run.json.
    """

    log = tmp_path / "quantus.log"
    code = run_subprocess(
        ["auto-ext-no-such-tool-xyz", "-cmd", "x"],
        cwd=tmp_path,
        env={"PATH": str(tmp_path)},
        log_path=log,
    )
    assert code == 127

    verdict = classify_failure(
        stage="quantus", exit_code=None, log_text=log.read_text(encoding="utf-8")
    )
    assert verdict.failure_class is FailureClass.ENVIRONMENT
    assert verdict.signature_id is not None
    assert "auto-ext-no-such-tool-xyz" in (verdict.evidence or "")


def test_the_quantus_cdl_map_signature_beats_the_bare_exit_code() -> None:
    """LBRCXM-756 has one cause, and it is not "the tool crashed".

    The runner publishes ``si.env`` into the Cadence workspace *because* of
    this error (``_publish_si_env_to_output_dir``); a run where that publish
    did not happen -- si failed, or the workspace was cleaned between stages
    -- lands here. Without the signature it is a generic non-zero exit.
    """

    log = (
        "Quantus 18.21-s340\n"
        "*ERROR* (LBRCXM-756): Cannot open the cdl_out_map_directory file.\n"
    )
    verdict = classify_failure(stage="quantus", exit_code=1, log_text=log)
    assert verdict.failure_class is FailureClass.ENVIRONMENT
    assert "si.env" in verdict.next_action


def test_a_signature_does_not_fire_on_an_ordinary_log() -> None:
    verdict = classify_failure(
        stage="quantus",
        exit_code=1,
        log_text="# argv: ['qrc', '-cmd', 'ext.cmd']\nsolving nets...\n",
    )
    assert verdict.failure_class is FailureClass.TOOL_CRASH
    assert verdict.signature_id is None
