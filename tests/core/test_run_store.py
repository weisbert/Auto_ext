"""Reading and writing the run history.

Covers the store half of ``docs/refactor/04-tests-disposition.md`` section
3.A: atomic and non-overwriting record writes (A.6, A.7), stage results that
survive a round trip (A.5), and an enumerable history that tolerates junk
(A.9). Directory identity itself is tested in ``tests/model/test_run_record.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

import pytest

from auto_ext.core.errors import ConfigError
from auto_ext.core.run_store import (
    BATCHES_DIRNAME,
    RunStoreError,
    append_event,
    find_previous_run,
    list_runs,
    prune_runs,
    read_annotations,
    read_batch,
    read_events,
    read_record,
    write_annotations,
    write_batch,
    write_record,
)
from auto_ext.model.run import (
    LvsResult,
    RunAnnotations,
    RunBatch,
    RunResults,
    StageRecord,
    StageStatus,
    TaskStatus,
    allocate_run_dir,
    run_paths,
)

STAMP = "20260821T143205Z"


# ---- run.json -----------------------------------------------------------------


def test_write_then_read_record_round_trips(run_dir, make_run_record) -> None:
    record = make_run_record(
        run_dir=run_dir,
        stages=[
            StageRecord(
                key="calibre",
                stage="calibre",
                status=StageStatus.PASSED,
                duration_s=93.5,
                exit_code=0,
                log_path="logs/calibre.log",
                rendered_path="rendered/lvs.qci",
                artifacts=["/work/QCI_PATH_amp2/svdb"],
            )
        ],
        results=RunResults(
            lvs=LvsResult(
                passed=True,
                banner="CORRECT",
                discrepancies=0,
                archived_path="results/lvs.report",
            )
        ),
    )

    path = write_record(run_dir, record)

    assert path == run_paths(run_dir).record
    assert read_record(run_dir) == record
    assert read_record(path) == record  # the file path works too


def test_written_record_is_utf8_json_indented_by_two(run_dir, make_run_record) -> None:
    record = make_run_record(run_dir=run_dir, recipe_name="RC coupled · typical")

    path = write_record(run_dir, record)

    text = path.read_text(encoding="utf-8")
    assert text.startswith('{\n  "schema_version": 2,')
    assert text.endswith("\n")
    assert json.loads(text)["run_id"] == record.run_id


def test_write_record_leaves_no_temp_file_behind(run_dir, make_run_record) -> None:
    """A.6: atomic write means .tmp -> rename, and nothing lingering."""

    write_record(run_dir, make_run_record(run_dir=run_dir))

    assert [p.name for p in run_dir.iterdir() if p.name.startswith("run.json")] == [
        "run.json"
    ]


def test_write_record_refuses_to_overwrite_by_default(run_dir, make_run_record) -> None:
    """A.7: an existing record is never silently replaced."""

    first = make_run_record(run_dir=run_dir, overall=TaskStatus.PASSED)
    write_record(run_dir, first)

    with pytest.raises(RunStoreError, match="immutable"):
        write_record(run_dir, make_run_record(run_dir=run_dir, overall=TaskStatus.FAILED))

    assert read_record(run_dir).overall == "passed"


def test_finalize_replaces_the_pending_skeleton(run_dir, make_run_record) -> None:
    """The one sanctioned rewrite: skeleton at start, full record at finalize."""

    write_record(run_dir, make_run_record(run_dir=run_dir, overall=TaskStatus.PENDING))

    final = make_run_record(
        run_dir=run_dir,
        overall=TaskStatus.FAILED,
        stages=[
            StageRecord(key="calibre", stage="calibre", status=StageStatus.FAILED),
            StageRecord(
                key="quantus",
                stage="quantus",
                status=StageStatus.SKIPPED,
                skip_reason="aborted after earlier stage failure",
            ),
        ],
    )
    write_record(run_dir, final, overwrite=True)

    back = read_record(run_dir)
    assert back.overall == "failed"
    assert back.stage("quantus").skip_reason == "aborted after earlier stage failure"


def test_read_record_rejects_truncated_json(run_dir, make_run_record) -> None:
    write_record(run_dir, make_run_record(run_dir=run_dir))
    record_path = run_paths(run_dir).record
    text = record_path.read_text(encoding="utf-8")
    record_path.write_text(text[: len(text) // 2], encoding="utf-8")

    with pytest.raises(RunStoreError, match="not valid JSON"):
        read_record(run_dir)


def test_read_record_rejects_a_future_schema_version(run_dir, make_run_record) -> None:
    write_record(run_dir, make_run_record(run_dir=run_dir))
    record_path = run_paths(run_dir).record
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["schema_version"] = 99
    record_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ConfigError, match="newer than this build"):
        read_record(run_dir)


def test_read_record_rejects_a_pre_run_layer_version(run_dir, make_run_record) -> None:
    write_record(run_dir, make_run_record(run_dir=run_dir))
    record_path = run_paths(run_dir).record
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["schema_version"] = 1
    record_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ConfigError, match="no upgrader"):
        read_record(run_dir)


# ---- events.jsonl --------------------------------------------------------------


def test_events_append_in_order(run_dir, frozen_clock) -> None:
    append_event(run_dir, {"event": "run_start", "stages": ["si", "calibre"]})
    frozen_clock.tick(2)
    append_event(run_dir, {"event": "stage_end", "stage": "si", "status": "passed"})

    events = read_events(run_dir)

    assert [e["event"] for e in events] == ["run_start", "stage_end"]
    assert events[0]["at"] == "2026-08-21T14:32:05+00:00"
    assert events[1]["at"] == "2026-08-21T14:32:07+00:00"


def test_events_keep_a_caller_supplied_timestamp(run_dir, frozen_clock) -> None:
    append_event(run_dir, {"event": "stage_start", "at": "2020-01-01T00:00:00+00:00"})

    assert read_events(run_dir)[0]["at"] == "2020-01-01T00:00:00+00:00"


def test_read_events_of_a_run_that_wrote_none(run_dir) -> None:
    assert read_events(run_dir) == []


def test_read_events_skips_a_truncated_last_line(run_dir, caplog) -> None:
    """A killed run leaves half a line; the events before it still count."""

    append_event(run_dir, {"event": "run_start"})
    with run_paths(run_dir).events.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "stage_st')

    with caplog.at_level(logging.WARNING):
        events = read_events(run_dir)

    assert [e["event"] for e in events] == ["run_start"]
    assert "unparsable event line" in caplog.text


def test_append_event_stringifies_an_exotic_value(run_dir) -> None:
    """An event log must never abort a running EDA job over a stray value."""

    append_event(run_dir, {"event": "x", "path": run_dir, "extra": {1, 2}})

    event = read_events(run_dir)[0]
    assert event["path"] == str(run_dir)
    assert isinstance(event["extra"], str)


def test_append_event_rejects_an_unserializable_payload(run_dir) -> None:
    circular: dict[str, object] = {"event": "x"}
    circular["self"] = circular

    with pytest.raises(RunStoreError, match="JSON-serializable"):
        append_event(run_dir, circular)


# ---- annotations.json -----------------------------------------------------------


def test_annotations_default_when_absent(run_dir) -> None:
    annotations = read_annotations(run_dir)

    assert annotations.display_name is None
    assert annotations.tags == []
    assert not run_paths(run_dir).annots.exists()


def test_annotations_round_trip_and_leave_the_record_alone(
    run_dir, make_run_record, frozen_clock
) -> None:
    record = make_run_record(run_dir=run_dir)
    write_record(run_dir, record)
    before = run_paths(run_dir).record.read_bytes()

    frozen_clock.tick(60)
    write_annotations(
        run_dir,
        RunAnnotations(display_name="amp2 after metal fix", note="rerun", tags=["golden"]),
    )

    back = read_annotations(run_dir)
    assert back.display_name == "amp2 after metal fix"
    assert back.tags == ["golden"]
    assert back.updated_at == frozen_clock.now()
    assert run_paths(run_dir).record.read_bytes() == before
    assert read_record(run_dir) == record


def test_corrupt_annotations_degrade_to_defaults(run_dir, caplog) -> None:
    run_paths(run_dir).annots.write_text("{not json", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        annotations = read_annotations(run_dir)

    assert annotations.display_name is None
    assert "unreadable annotations" in caplog.text


# ---- batches ---------------------------------------------------------------------


def test_batch_index_round_trips(runs_root, frozen_clock) -> None:
    batch = RunBatch(
        batch_id=f"{STAMP}_nightly",
        created_at=frozen_clock.now(),
        label="nightly",
        run_ids=[f"{STAMP}_amp2-ext"],
        max_workers=4,
    )

    path = write_batch(runs_root, batch)

    assert path.parent.name == BATCHES_DIRNAME
    assert read_batch(runs_root, batch.batch_id) == batch


# ---- list_runs -------------------------------------------------------------------


def _write_run(runs_root, make_run_record, frozen_clock, *, cell="amp2", **kw):
    """Allocate a run directory and finalize a record inside it."""

    d = allocate_run_dir(runs_root, f"{cell}-ext")
    record = make_run_record(run_dir=d, cell=cell, **kw)
    write_record(d, record)
    frozen_clock.tick(1)
    return d, record


def test_list_runs_is_newest_first(runs_root, make_run_record, frozen_clock) -> None:
    cells = ["amp2", "mixer", "vco", "pll", "buf"]
    for cell in cells:
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)

    entries = list_runs(runs_root)

    assert [e.cell for e in entries] == list(reversed(cells))
    assert entries[0].created_at > entries[-1].created_at


def test_list_runs_honours_limit(runs_root, make_run_record, frozen_clock) -> None:
    for cell in ("amp2", "mixer", "vco"):
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)

    assert [e.cell for e in list_runs(runs_root, limit=2)] == ["vco", "mixer"]


def test_list_runs_of_an_empty_or_missing_root(runs_root, tmp_path) -> None:
    assert list_runs(runs_root) == []
    assert list_runs(tmp_path / "does-not-exist") == []


def test_list_runs_carries_the_lvs_outcome(
    runs_root, make_run_record, frozen_clock
) -> None:
    """The discrepancy count reaches the history list without a full parse."""

    _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="amp2",
        results=RunResults(
            lvs=LvsResult(passed=False, banner="INCORRECT", discrepancies=7)
        ),
    )

    entry = list_runs(runs_root)[0]

    assert entry.lvs_passed is False
    assert entry.lvs_discrepancies == 7
    assert entry.dut_key == "WB_PLL_DCO__amp2__layout__schematic"


def test_list_runs_uses_the_annotation_display_name(
    runs_root, make_run_record, frozen_clock
) -> None:
    d, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    assert list_runs(runs_root)[0].display_name == "amp2 · ext"

    write_annotations(d, RunAnnotations(display_name="the good one", starred=True))

    entry = list_runs(runs_root)[0]
    assert entry.display_name == "the good one"
    assert entry.starred is True
    assert entry.pinned is True


def test_list_runs_survives_junk_and_corruption(
    runs_root, make_run_record, frozen_clock, caplog
) -> None:
    """A.9: one bad directory must not take the whole history down."""

    _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    # A directory the user made by hand.
    (runs_root / "scratch notes").mkdir()
    # A run whose record never finished being written.
    truncated = allocate_run_dir(runs_root, "mixer-ext")
    run_paths(truncated).record.write_text('{"schema_version": 2, "run_i', encoding="utf-8")
    # A run whose record is valid JSON but not a run record.
    wrong_shape = allocate_run_dir(runs_root, "vco-ext")
    run_paths(wrong_shape).record.write_text('{"hello": "world"}', encoding="utf-8")
    # A run directory with no record at all.
    allocate_run_dir(runs_root, "pll-ext")
    # A stray file, and the layout entries that are not runs.
    (runs_root / "README.txt").write_text("notes\n", encoding="utf-8")
    (runs_root / BATCHES_DIRNAME).mkdir()

    with caplog.at_level(logging.WARNING):
        entries = list_runs(runs_root)

    assert [e.cell for e in entries] == ["amp2"]
    assert "skipping unreadable run record" in caplog.text
    assert "no run.json" in caplog.text


def test_list_runs_skips_a_record_without_a_dut(
    runs_root, make_run_record, frozen_clock, caplog
) -> None:
    d, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")
    data = json.loads(run_paths(d).record.read_text(encoding="utf-8"))
    del data["dut"]
    run_paths(d).record.write_text(json.dumps(data), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert list_runs(runs_root) == []

    assert "no dut section" in caplog.text


# ---- find_previous_run -------------------------------------------------------------


def test_find_previous_run_matches_the_same_dut(
    runs_root, make_run_record, frozen_clock
) -> None:
    _, first = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="amp2",
        results=RunResults(lvs=LvsResult(passed=False, discrepancies=12)),
    )
    _write_run(runs_root, make_run_record, frozen_clock, cell="mixer")
    _, latest = _write_run(
        runs_root,
        make_run_record,
        frozen_clock,
        cell="amp2",
        results=RunResults(lvs=LvsResult(passed=True, discrepancies=0)),
    )

    previous = find_previous_run(runs_root, latest.dut, before=latest.created_at)

    assert previous is not None
    assert previous.run_id == first.run_id
    assert previous.lvs_discrepancies == 12  # "12 -> 0 since last time"


def test_find_previous_run_accepts_an_index_entry(
    runs_root, make_run_record, frozen_clock
) -> None:
    _, first = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")
    _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    latest_entry = list_runs(runs_root)[0]
    previous = find_previous_run(runs_root, latest_entry)

    assert previous is not None
    assert previous.run_id == first.run_id


def test_find_previous_run_returns_none_for_a_first_run(
    runs_root, make_run_record, frozen_clock
) -> None:
    _, only = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    assert find_previous_run(runs_root, only.dut, before=only.created_at) is None


def test_find_previous_run_ignores_other_duts(
    runs_root, make_run_record, frozen_clock
) -> None:
    _write_run(runs_root, make_run_record, frozen_clock, cell="mixer")
    _, amp = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    assert find_previous_run(runs_root, amp.dut, before=amp.created_at) is None


def test_find_previous_run_distinguishes_views(
    runs_root, make_run_record, frozen_clock
) -> None:
    d = allocate_run_dir(runs_root, "amp2-ext")
    write_record(d, make_run_record(run_dir=d, cell="amp2", layout_view="layout_test"))
    frozen_clock.tick(1)
    d2 = allocate_run_dir(runs_root, "amp2-ext")
    latest = make_run_record(run_dir=d2, cell="amp2", layout_view="layout")
    write_record(d2, latest)

    assert find_previous_run(runs_root, latest.dut, before=latest.created_at) is None


def test_find_previous_run_accepts_a_run_id_cutoff(
    runs_root, make_run_record, frozen_clock
) -> None:
    _, first = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")
    _, second = _write_run(runs_root, make_run_record, frozen_clock, cell="amp2")

    previous = find_previous_run(runs_root, second.dut, before=second.run_id)

    assert previous is not None
    assert previous.run_id == first.run_id


# ---- prune_runs ---------------------------------------------------------------------


def test_prune_keeps_the_newest_and_reports_what_went(
    runs_root, make_run_record, frozen_clock
) -> None:
    made = [
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)
        for cell in ("a1", "a2", "a3", "a4", "a5")
    ]

    removed = prune_runs(runs_root, keep=2)

    assert removed == [made[2][1].run_id, made[1][1].run_id, made[0][1].run_id]
    assert [e.cell for e in list_runs(runs_root)] == ["a5", "a4"]
    assert not made[0][0].exists()


def test_prune_keeps_pinned_runs_whatever_their_age(
    runs_root, make_run_record, frozen_clock
) -> None:
    oldest, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="a1")
    tagged, _ = _write_run(runs_root, make_run_record, frozen_clock, cell="a2")
    for cell in ("a3", "a4"):
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)
    write_annotations(oldest, RunAnnotations(starred=True))
    write_annotations(tagged, RunAnnotations(tags=["tapeout"]))

    prune_runs(runs_root, keep=1)

    assert sorted(e.cell for e in list_runs(runs_root)) == ["a1", "a2", "a4"]


def test_prune_never_touches_an_unfinished_run(
    runs_root, make_run_record, frozen_clock
) -> None:
    """A PENDING record may belong to a run that is executing right now."""

    running, _ = _write_run(
        runs_root, make_run_record, frozen_clock, cell="a1", overall=TaskStatus.PENDING
    )
    for cell in ("a2", "a3"):
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)

    prune_runs(runs_root, keep=1)

    assert running.exists()
    assert sorted(e.cell for e in list_runs(runs_root)) == ["a1", "a3"]


def test_prune_leaves_unparsable_directories_alone(
    runs_root, make_run_record, frozen_clock
) -> None:
    junk = runs_root / "scratch notes"
    junk.mkdir()
    for cell in ("a1", "a2"):
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)

    prune_runs(runs_root, keep=1)

    assert junk.exists()


def test_prune_with_keep_zero_is_a_no_op(
    runs_root, make_run_record, frozen_clock
) -> None:
    for cell in ("a1", "a2", "a3"):
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)

    assert prune_runs(runs_root, keep=0) == []
    assert len(list_runs(runs_root)) == 3


def test_prune_dry_run_reports_without_deleting(
    runs_root, make_run_record, frozen_clock
) -> None:
    for cell in ("a1", "a2", "a3"):
        _write_run(runs_root, make_run_record, frozen_clock, cell=cell)

    removed = prune_runs(runs_root, keep=1, dry_run=True)

    assert len(removed) == 2
    assert len(list_runs(runs_root)) == 3


# ---- the whole shape together ---------------------------------------------------------


def test_a_finished_run_directory_is_self_describing(
    runs_root, make_run_record, frozen_clock
) -> None:
    """A.5: every path in ``run.json`` resolves inside the run directory."""

    d = allocate_run_dir(runs_root, "amp2-ext")
    paths = run_paths(d)
    (paths.logs / "calibre.log").write_text("LVS output\n", encoding="utf-8")
    (paths.rendered / "lvs.qci").write_text("*lvsRulesFile: x\n", encoding="utf-8")
    (paths.results / "lvs.report").write_text("CORRECT\n", encoding="utf-8")

    record = make_run_record(
        run_dir=d,
        ended_at=frozen_clock.now() + timedelta(seconds=120),
        stages=[
            StageRecord(
                key="calibre",
                stage="calibre",
                status=StageStatus.PASSED,
                started_at=frozen_clock.now(),
                ended_at=frozen_clock.now() + timedelta(seconds=93.5),
                duration_s=93.5,
                exit_code=0,
                log_path="logs/calibre.log",
                rendered_path="rendered/lvs.qci",
            )
        ],
        results=RunResults(
            lvs=LvsResult(passed=True, banner="CORRECT", archived_path="results/lvs.report")
        ),
    )
    write_record(d, record)
    append_event(d, {"event": "run_end", "status": "passed"})

    back = read_record(d)
    stage = back.stage("calibre")
    assert (d / stage.log_path).read_text(encoding="utf-8") == "LVS output\n"
    assert (d / stage.rendered_path).exists()
    assert (d / back.results.lvs.archived_path).is_file()
    assert back.duration_s == 120.0
    assert read_events(d)[-1]["event"] == "run_end"
