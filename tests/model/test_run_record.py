"""Run identity and the RunRecord contract.

Covers the "Run record" block of ``docs/refactor/04-tests-disposition.md``
section 3.A that belongs to the model layer: directory identity (A.1, A.2,
A.7), snapshot semantics (A.3), stage-level structure (A.5, A.6), and slug
path safety (A.8). The store-side items (A.9, archiving) live in
``tests/core/test_run_store.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from auto_ext.core.errors import AutoExtError
from auto_ext.model.run import (
    MAX_SAME_SECOND_RUNS,
    RUN_SCHEMA_VERSION,
    RUN_TIMESTAMP_FORMAT,
    DutSnapshot,
    JivaroSnapshot,
    LvsResult,
    RecipeSnapshot,
    RunAnnotations,
    RunBatch,
    RunIdError,
    RunRecord,
    RunResults,
    StageRecord,
    StageStatus,
    TaskStatus,
    allocate_run_dir,
    make_run_slug,
    parse_run_id,
    run_paths,
    slugify,
    validate_run_slug,
)

STAMP = "20260821T143205Z"


def _dut(**kw: object) -> DutSnapshot:
    base: dict[str, object] = {
        "library": "WB_PLL_DCO",
        "cell": "amp2",
        "layout_view": "layout",
    }
    base.update(kw)
    return DutSnapshot(**base)  # type: ignore[arg-type]


# ---- A.1 / A.2 / A.7: directory identity -------------------------------------


def test_two_runs_one_second_apart_get_two_directories(
    runs_root, frozen_clock
) -> None:
    """Rerunning the same DUT + recipe never overwrites the previous run.

    This is the direct counter-test to the old behaviour: stage logs were
    opened with ``"w"`` under ``logs/task_<id>/``, so a rerun destroyed the
    previous log.
    """

    first = allocate_run_dir(runs_root, "amp2-ext")
    (first / "logs" / "si.log").write_text("first run\n", encoding="utf-8")

    frozen_clock.tick(1)
    second = allocate_run_dir(runs_root, "amp2-ext")
    (second / "logs" / "si.log").write_text("second run\n", encoding="utf-8")

    assert first != second
    assert first.name == f"{STAMP}_amp2-ext"
    assert second.name == "20260821T143206Z_amp2-ext"
    assert (first / "logs" / "si.log").read_text(encoding="utf-8") == "first run\n"
    assert (second / "logs" / "si.log").read_text(encoding="utf-8") == "second run\n"


def test_same_second_runs_get_deterministic_suffix(runs_root, frozen_clock) -> None:
    """A.2: same wall-clock second, same slug -> ``-2`` / ``-3``, never a clash."""

    dirs = [allocate_run_dir(runs_root, "amp2-ext") for _ in range(3)]

    assert [d.name for d in dirs] == [
        f"{STAMP}_amp2-ext",
        f"{STAMP}_amp2-ext-2",
        f"{STAMP}_amp2-ext-3",
    ]
    for i, d in enumerate(dirs):
        (d / "logs" / "si.log").write_text(f"run {i}\n", encoding="utf-8")
    for i, d in enumerate(dirs):
        assert (d / "logs" / "si.log").read_text(encoding="utf-8") == f"run {i}\n"


def test_same_second_suffix_never_touches_the_timestamp(runs_root) -> None:
    """The disambiguator goes on the slug so ``parse_run_id`` keeps working."""

    now = datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc)
    allocate_run_dir(runs_root, "amp2-ext", now=now)
    second = allocate_run_dir(runs_root, "amp2-ext", now=now)

    stamp, slug = parse_run_id(second.name)
    assert stamp == now  # timestamp still parses, disambiguator went on the slug
    assert slug == "amp2-ext-2"


def test_allocate_creates_the_standard_subdirectories(runs_root, frozen_clock) -> None:
    d = allocate_run_dir(runs_root, "amp2-ext")

    paths = run_paths(d)
    assert paths.rendered.is_dir()
    assert paths.logs.is_dir()
    assert paths.results.is_dir()
    # work/ belongs to the parallel workdir machinery, not to allocation.
    assert not paths.work.exists()


def test_allocate_never_reuses_an_existing_directory(runs_root, frozen_clock) -> None:
    """A.7: the opposite of the old ``prepare_parallel_workdir`` stale-reuse."""

    stale = runs_root / f"{STAMP}_amp2-ext"
    stale.mkdir()
    (stale / "marker").write_text("previous run\n", encoding="utf-8")

    fresh = allocate_run_dir(runs_root, "amp2-ext")

    assert fresh != stale
    assert (stale / "marker").read_text(encoding="utf-8") == "previous run\n"


def test_allocate_gives_up_after_the_collision_limit(
    runs_root, frozen_clock, monkeypatch
) -> None:
    monkeypatch.setattr("auto_ext.model.run.MAX_SAME_SECOND_RUNS", 3)
    for _ in range(3):
        allocate_run_dir(runs_root, "amp2-ext")

    with pytest.raises(RunIdError, match="same-second siblings"):
        allocate_run_dir(runs_root, "amp2-ext")


def test_collision_limit_is_999() -> None:
    assert MAX_SAME_SECOND_RUNS == 999


def test_allocate_converts_a_non_utc_now_to_utc(runs_root) -> None:
    """A run named in local time would break the "sorts by time" guarantee."""

    tokyo = timezone(timedelta(hours=9))
    local = datetime(2026, 8, 21, 23, 32, 5, tzinfo=tokyo)

    d = allocate_run_dir(runs_root, "amp2-ext", now=local)

    assert d.name == f"{STAMP}_amp2-ext"


def test_allocate_uses_the_module_clock_when_now_is_omitted(
    runs_root, frozen_clock
) -> None:
    frozen_clock.set(datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc))

    d = allocate_run_dir(runs_root, "amp2-ext")

    assert d.name == "20300102T030405Z_amp2-ext"


def test_parse_run_id_rejects_a_non_run_directory_name() -> None:
    with pytest.raises(RunIdError):
        parse_run_id("task_WB_PLL_DCO__amp2__layout__schematic")


# ---- A.8: slug path safety ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "etc-passwd"),
        ("..", "x"),
        ("/", "x"),
        ("C:\\Windows", "c-windows"),
        ("amp2; rm -rf /", "amp2-rm-rf"),
        ("$(whoami)", "whoami"),
        ("`id`", "id"),
        ("AMP2_Top", "amp2-top"),
        ("  spaced  name  ", "spaced-name"),
        ("trailing...", "trailing"),
        ("", "x"),
        ("...", "x"),
    ],
)
def test_slugify_neutralises_hostile_input(raw: str, expected: str) -> None:
    """Slug halves come from user-controlled cell / recipe names."""

    out = slugify(raw)

    assert out == expected
    # Whatever went in, what comes out is a safe single path component.
    assert validate_run_slug(out) == out


def test_slugify_truncates_and_never_ends_in_a_dash() -> None:
    assert slugify("a" * 40) == "a" * 24
    assert slugify("abcdefghijklmnopqrstuvw_x", max_len=24) == "abcdefghijklmnopqrstuvw"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "..",
        "../evil",
        "a/b",
        "a\\b",
        "C:evil",
        "-leading-dash",
        ".hidden",
        "trailing.",
        "trailing ",
        " leading",
        "UPPER",
        "amp2;rm",
        "amp2\x00",
        "con",
        "CON",
        "nul.txt",
        "com1",
        "lpt9",
        "aux",
        "prn",
    ],
)
def test_validate_run_slug_rejects_unsafe_slugs(bad: str) -> None:
    with pytest.raises(RunIdError):
        validate_run_slug(bad)


def test_allocate_run_dir_refuses_a_traversing_slug(runs_root, frozen_clock) -> None:
    """The gate is inside allocation, so no caller can bypass it."""

    with pytest.raises(RunIdError):
        allocate_run_dir(runs_root, "../escape")

    assert list(runs_root.iterdir()) == []


def test_run_directory_name_has_no_colon(runs_root, frozen_clock) -> None:
    """Windows dev boxes: a colon in the name makes the directory uncreatable.

    The basic-format timestamp exists for exactly this reason; a plain
    ``datetime.isoformat()`` would put ``14:32:05`` into the path.
    """

    d = allocate_run_dir(runs_root, "amp2-ext")

    assert ":" not in d.name
    assert d.is_dir()


def test_make_run_slug_joins_cell_and_recipe() -> None:
    slug = make_run_slug(_dut(cell="AMP2_Top"), RecipeSnapshot(recipe_id="rc_coupled"))

    assert slug == "amp2-top-rc-coupled"


def test_make_run_slug_survives_a_hostile_cell_name() -> None:
    slug = make_run_slug(_dut(cell="../../etc"), RecipeSnapshot(recipe_id="ext"))

    assert slug == "etc-ext"
    assert validate_run_slug(slug) == slug


# ---- run_paths ----------------------------------------------------------------


def test_run_paths_is_pure_and_complete(tmp_path) -> None:
    d = tmp_path / "20260821T143205Z_amp2-ext"

    p = run_paths(d)

    assert p.root == d
    assert p.record == d / "run.json"
    assert p.events == d / "events.jsonl"
    assert p.annots == d / "annotations.json"
    assert p.rendered == d / "rendered"
    assert p.logs == d / "logs"
    assert p.results == d / "results"
    assert p.work == d / "work"
    assert not d.exists()  # pure: it created nothing


# ---- StageRecord --------------------------------------------------------------


def test_stage_record_carries_timing_paths_and_artifacts() -> None:
    """A.5: everything the GUI and a later post-mortem need, in one object."""

    started = datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc)
    stage = StageRecord(
        key="calibre",
        stage="calibre",
        status=StageStatus.PASSED,
        started_at=started,
        ended_at=started + timedelta(seconds=93.5),
        duration_s=93.5,
        argv=["calibre", "-lvs", "-hier", "lvs.qci"],
        cwd="/work/area",
        exit_code=0,
        log_path="logs/calibre.log",
        rendered_path="rendered/lvs.qci",
        artifacts=["/work/cds/verify/QCI_PATH_amp2/svdb"],
        details={"exit_code": 0, "lvs_report": {"banner": "CORRECT", "discrepancies": 0}},
    )

    assert stage.status == "passed"  # StrEnum: string comparisons still hold
    assert stage.duration_s == 93.5
    assert stage.finished_at == stage.ended_at
    assert stage.details["lvs_report"] == {"banner": "CORRECT", "discrepancies": 0}


def test_stage_record_skip_reason_survives_round_trip() -> None:
    """A.6: a skipped stage keeps the runner's wording."""

    stage = StageRecord(
        key="jivaro",
        stage="jivaro",
        status=StageStatus.SKIPPED,
        skip_reason="jivaro disabled for task",
    )

    back = StageRecord.model_validate_json(stage.model_dump_json())
    assert back.skip_reason == "jivaro disabled for task"


@pytest.mark.parametrize(
    "bad",
    [
        "/abs/logs/si.log",
        "C:/logs/si.log",
        "logs\\si.log",
        "../outside/si.log",
        "",
    ],
)
def test_stage_record_paths_must_stay_inside_the_run_dir(bad: str) -> None:
    with pytest.raises(ValidationError):
        StageRecord(key="si", stage="si", status=StageStatus.PASSED, log_path=bad)


def test_stage_record_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StageRecord(
            key="si", stage="si", status=StageStatus.PASSED, tool_result="whatever"
        )


def test_stage_record_details_rejects_non_json_values() -> None:
    with pytest.raises(ValidationError):
        StageRecord(
            key="si", stage="si", status=StageStatus.PASSED, details={"x": object()}
        )


# ---- LvsResult ----------------------------------------------------------------


def test_lvs_result_from_checks_report() -> None:
    """The parsed LVS report stops being a diagnostics-dict passenger."""

    from pathlib import Path as PathType

    from auto_ext.core.checks import LvsReport

    report = LvsReport(
        passed=False,
        banner="INCORRECT",
        discrepancies=7,
        source=PathType("/work/cds/verify/QCI_PATH_amp2/amp2.lvs.report"),
    )

    result = LvsResult.from_lvs_report(report, archived_path="results/lvs.report")

    assert result.passed is False
    assert result.banner == "INCORRECT"
    assert result.discrepancies == 7
    assert result.source_path.endswith("amp2.lvs.report")
    assert result.archived_path == "results/lvs.report"


def test_lvs_result_archived_path_must_be_run_relative() -> None:
    with pytest.raises(ValidationError):
        LvsResult(passed=True, archived_path="/tmp/lvs.report")


# ---- RunRecord ----------------------------------------------------------------


def test_run_record_identity_must_match_the_directory_name(make_run_record) -> None:
    record = make_run_record(cell="amp2", recipe_id="ext")

    assert record.run_id == f"{STAMP}_amp2-ext"
    assert record.slug == "amp2-ext"
    assert record.schema_version == RUN_SCHEMA_VERSION
    assert parse_run_id(record.run_id) == (
        datetime.strptime(STAMP, RUN_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc),
        "amp2-ext",
    )


def test_run_record_rejects_a_slug_that_contradicts_the_run_id() -> None:
    with pytest.raises(ValidationError, match="does not match run_id"):
        RunRecord(
            run_id=f"{STAMP}_amp2-ext",
            slug="something-else",
            created_at=datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc),
            dut=_dut(),
            recipe=RecipeSnapshot(),
            workspace_dir="/work",
        )


def test_run_record_rejects_a_free_form_run_id() -> None:
    with pytest.raises(ValidationError, match="timestamp"):
        RunRecord(
            run_id="task_WB_PLL_DCO__amp2__layout__schematic",
            slug="amp2",
            created_at=datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc),
            dut=_dut(),
            recipe=RecipeSnapshot(),
            workspace_dir="/work",
        )


def test_run_record_rejects_duplicate_stage_keys(make_run_record) -> None:
    stage = StageRecord(key="quantus", stage="quantus", status=StageStatus.PASSED)

    with pytest.raises(ValidationError, match="duplicate stage keys"):
        make_run_record(stages=[stage, stage])


def test_run_record_allows_one_stage_twice_under_distinct_keys(make_run_record) -> None:
    """Quantus emitting both an extracted view and a DSPF is two stage rows."""

    record = make_run_record(
        stages=[
            StageRecord(key="quantus.ext", stage="quantus", status=StageStatus.PASSED),
            StageRecord(key="quantus.dspf", stage="quantus", status=StageStatus.PASSED),
        ]
    )

    assert [s.key for s in record.stages] == ["quantus.ext", "quantus.dspf"]
    assert record.stage("quantus.dspf") is not None
    assert record.stage("nope") is None


def test_run_record_is_frozen(make_run_record) -> None:
    record = make_run_record()

    with pytest.raises(ValidationError):
        record.overall = TaskStatus.FAILED


def test_run_record_rejects_unknown_fields(make_run_record) -> None:
    with pytest.raises(ValidationError):
        make_run_record(task_id="WB_PLL_DCO__amp2__layout__schematic")


def test_run_record_defaults_to_pending(make_run_record) -> None:
    record = make_run_record(overall=TaskStatus.PENDING)

    assert record.overall == "pending"
    assert record.ended_at is None
    assert record.duration_s is None


def test_run_record_duration_and_labels(make_run_record, frozen_clock) -> None:
    record = make_run_record(
        cell="amp2",
        recipe_id="ext",
        recipe_name="RC coupled typical",
        ended_at=frozen_clock.now() + timedelta(seconds=42),
    )

    assert record.duration_s == 42.0
    assert record.finished_at == record.ended_at
    assert record.dut_label == "WB_PLL_DCO__amp2__layout__schematic"
    assert record.default_display_name == "amp2 \u00b7 RC coupled typical"


def test_run_record_display_name_falls_back_to_recipe_id(make_run_record) -> None:
    record = make_run_record(cell="amp2", recipe_id="ext", recipe_name=None)

    assert record.default_display_name == "amp2 \u00b7 ext"


def test_run_record_json_round_trip_is_lossless(make_run_record, frozen_clock) -> None:
    """``run.json`` must be plain JSON: no Path, no set, no tuple key."""

    record = make_run_record(
        ended_at=frozen_clock.now() + timedelta(seconds=5),
        stages=[
            StageRecord(
                key="si",
                stage="si",
                status=StageStatus.PASSED,
                log_path="logs/si.log",
                rendered_path="rendered/si.env",
                artifacts=["/work/cds/verify/QCI_PATH_amp2/si.env"],
                details={"exit_code": 0, "argv": ["si", "-batch"]},
            )
        ],
        results=RunResults(lvs=LvsResult(passed=True, banner="CORRECT", discrepancies=0)),
        context={"cell": "amp2", "temperature": 55.0, "hyper": True, "unset": None},
    )

    payload = record.model_dump_json(indent=2)
    parsed = json.loads(payload)  # plain JSON, no custom decoder needed
    back = RunRecord.model_validate(parsed)

    assert back == record
    assert parsed["created_at"].startswith("2026-08-21T14:32:05")
    assert parsed["results"]["lvs"]["discrepancies"] == 0
    assert parsed["stages"][0]["log_path"] == "logs/si.log"


# ---- A.3: the recipe field is a snapshot, not a reference ---------------------


def test_recipe_snapshot_keeps_the_values_it_was_built_with(make_run_record) -> None:
    """Editing the project config after the fact cannot rewrite history."""

    live_knobs = {"quantus": {"temperature": 55.0}}
    live_paths = {"calibre_lvs_dir": "/pdk/v1/LVS"}
    snapshot = RecipeSnapshot(
        recipe_id="ext",
        templates={"quantus": "/catalog/quantus/ext.cmd.j2"},
        knobs=live_knobs,
        jivaro=JivaroSnapshot(enabled=True, frequency_limit=14.0, error_max=2.0),
        dspf_out_path="${WORK_ROOT2}/{cell}.dspf",
        paths=live_paths,
    )
    record = make_run_record(recipe=snapshot)

    # Mutate the dicts the caller passed in, the way a config reload would.
    live_knobs["quantus"]["temperature"] = 125.0
    live_paths["calibre_lvs_dir"] = "/pdk/v2/LVS"

    assert record.recipe.knobs == {"quantus": {"temperature": 55.0}}
    assert record.recipe.paths == {"calibre_lvs_dir": "/pdk/v1/LVS"}
    assert record.recipe.jivaro.enabled is True
    assert record.recipe.dspf_out_path == "${WORK_ROOT2}/{cell}.dspf"

    back = RunRecord.model_validate_json(record.model_dump_json())
    assert back.recipe == record.recipe


def test_recipe_snapshot_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RecipeSnapshot(recipe_id="ext", corner="TYPICAL")


def test_dut_snapshot_from_task_config() -> None:
    """The S1 bridge from the live ``TaskConfig`` to the frozen DUT snapshot."""

    from auto_ext.core.config import JivaroConfig, TaskConfig, TemplatePaths

    task = TaskConfig(
        task_id="WB_PLL_DCO__amp2__layout__schematic",
        library="WB_PLL_DCO",
        cell="amp2",
        lvs_source_view="schematic",
        lvs_layout_view="layout",
        templates=TemplatePaths(),
        ground_net="vss",
        out_file="av_extracted",
        label="the fast one",
        jivaro=JivaroConfig(),
        continue_on_lvs_fail=False,
        spec_index=0,
        expansion_index=0,
    )

    dut = DutSnapshot.from_task_config(task)

    assert dut.library == "WB_PLL_DCO"
    assert dut.layout_view == "layout"
    assert dut.source_view == "schematic"
    assert dut.out_file == "av_extracted"
    assert dut.display_name == "the fast one"
    assert dut.key == task.task_id


def test_env_bindings_from_resolution(make_run_record) -> None:
    """The env that produced the rendered files, with its provenance."""

    from auto_ext.core.env import EnvResolution
    from auto_ext.model.run import EnvBinding

    resolution = EnvResolution(
        resolved={"WORK_ROOT": "/w", "SETUP_ROOT": "/s", "MISSING_X": ""},
        sources={"WORK_ROOT": "override", "SETUP_ROOT": "shell", "MISSING_X": "missing"},
    )

    bindings = EnvBinding.from_resolution(resolution)

    assert [(b.name, b.source) for b in bindings] == [
        ("MISSING_X", "missing"),
        ("SETUP_ROOT", "shell"),
        ("WORK_ROOT", "override"),
    ]
    record = make_run_record(env=bindings)
    assert RunRecord.model_validate_json(record.model_dump_json()).env == bindings


def test_env_binding_rejects_an_unknown_source() -> None:
    from auto_ext.model.run import EnvBinding

    with pytest.raises(ValidationError):
        EnvBinding(name="WORK_ROOT", value="/w", source="guessed")


# ---- annotations / batches ----------------------------------------------------


def test_annotations_default_to_empty_and_stamp_the_frozen_clock(frozen_clock) -> None:
    annotations = RunAnnotations()

    assert annotations.display_name is None
    assert annotations.tags == []
    assert annotations.starred is False
    assert annotations.pinned is False
    assert annotations.updated_at == frozen_clock.now()


@pytest.mark.parametrize(
    ("kwargs", "pinned"),
    [({}, False), ({"starred": True}, True), ({"tags": ["golden"]}, True)],
)
def test_annotations_pinned_reflects_star_or_tags(kwargs: dict, pinned: bool) -> None:
    assert RunAnnotations(**kwargs).pinned is pinned


def test_annotations_are_mutable_and_validated(frozen_clock) -> None:
    annotations = RunAnnotations()

    annotations.display_name = "amp2 after metal fix"

    assert annotations.display_name == "amp2 after metal fix"
    with pytest.raises(ValidationError):
        annotations.starred = "yes please"  # type: ignore[assignment]


def test_run_batch_indexes_its_members(frozen_clock) -> None:
    batch = RunBatch(
        batch_id=f"{STAMP}_nightly",
        created_at=frozen_clock.now(),
        label="nightly",
        run_ids=[f"{STAMP}_amp2-ext", f"{STAMP}_amp2-ext-2"],
        max_workers=4,
    )

    assert len(batch.run_ids) == 2
    assert RunBatch.model_validate_json(batch.model_dump_json()) == batch


def test_run_id_error_is_an_auto_ext_error() -> None:
    assert issubclass(RunIdError, AutoExtError)
