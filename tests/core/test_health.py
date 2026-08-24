"""PDK health checks: the single answer to "can I run right now?".

Three properties are load-bearing and each has its own block below:

1. **Every failure carries a fix.** A red row that does not say what to do is
   the state this module exists to replace.
2. **Undetermined is not failed.** An unset ``$SETUP_ROOT`` must not be
   reported as "assura_tech.lib is missing" -- the fix is a setup script, not
   a file.
3. **A stale cache is never trusted.** The environment moves under a profile;
   a confident but wrong "you can run" costs an hour of EDA time.

Paths in the env fixtures are POSIX (``Path.as_posix``) because
``resolve_path_expr`` applies its ``|parent`` filter with ``PurePosixPath``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from auto_ext.core import health
from auto_ext.core.health import (
    DEFAULT_TOOL_EXECUTABLES,
    OPTIONAL_TOOLS,
    cached_or_check,
    check_profile,
    default_checks,
    format_report,
    health_cache_path,
    iter_env_rows,
    profile_env_refs,
    read_report,
    resolve_profile_env,
    write_report,
)
from auto_ext.model.common import utcnow
from auto_ext.model.pdk import (
    CheckStatus,
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    PdkCheck,
    PdkCheckKind,
    PdkProfile,
    QrcDeck,
)

ALL_TOOLS_PRESENT = {name: f"/opt/eda/bin/{name}" for name in DEFAULT_TOOL_EXECUTABLES.values()}


def _which(table: dict[str, str] | None = None):
    """A ``shutil.which`` stand-in, so results do not depend on this host."""

    lookup = ALL_TOOLS_PRESENT if table is None else table
    return lambda name: lookup.get(name)


@pytest.fixture
def deck_tree(tmp_path: Path) -> dict[str, Path]:
    lvs = tmp_path / "verify" / "LVS" / "Ver_1" / "CFXXX"
    qrc = tmp_path / "verify" / "QRC" / "Ver_2" / "CFXXX" / "QCI_deck"
    setup = tmp_path / "setup"
    for d in (lvs, qrc, setup):
        d.mkdir(parents=True)
    (lvs / "CFXXX.wodio.qcilvs").write_text("; deck\n", encoding="utf-8")
    (lvs / "empty.cdl").write_text("", encoding="utf-8")
    (qrc / "query_cmd").write_text("# query\n", encoding="utf-8")
    (qrc / "preserveCellList.txt").write_text("", encoding="utf-8")
    (setup / "assura_tech.lib").write_text("techCorner( \"TYPICAL\" )\n", encoding="utf-8")
    (setup / "layers.map").write_text("; layers\n", encoding="utf-8")
    return {"root": tmp_path, "lvs": lvs, "qrc": qrc, "setup": setup}


@pytest.fixture
def healthy(deck_tree: dict[str, Path]) -> PdkProfile:
    """A profile whose every check should come back green."""

    setup = deck_tree["setup"]
    return PdkProfile(
        profile_id="hn001",
        display_name="HN001",
        tech_name="HN001",
        layer_map=(setup / "layers.map").as_posix(),
        tech_library_file=(setup / "assura_tech.lib").as_posix(),
        cdl_include_files=[(deck_tree["lvs"] / "empty.cdl").as_posix()],
        required_env=[],
        lvs_decks=LvsDeckSet(
            dir_expr=deck_tree["lvs"].as_posix(),
            basename="CFXXX",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="wodio",
            runset_version="Ver_1",
        ),
        qrc=QrcDeck(dir_expr=deck_tree["qrc"].as_posix(), runset_version="Ver_2"),
        corners=[CornerSpec(name="typical", technology_corner="TYPICAL")],
        default_corner="typical",
        power_names=["VDD"],
        ground_names=["VSS"],
    )


def _report(profile: PdkProfile, **kw):
    kw.setdefault("which", _which())
    return check_profile(profile, **kw)


# ---- the green path ----------------------------------------------------------


def test_a_complete_profile_is_ready_to_run(healthy):
    report = _report(healthy)
    assert report.ok, format_report(report)
    assert report.can_run
    assert report.exit_code == 0
    assert report.counts()["fail"] == 0 and report.counts()["unknown"] == 0


def test_the_report_names_the_profile_and_fingerprints_it(healthy):
    report = _report(healthy)
    assert report.profile_id == "hn001"
    assert report.profile_sha256 == healthy.fingerprint()


def test_an_ok_result_carries_no_fix_hint(healthy):
    for r in _report(healthy).results:
        assert r.fix_hint is None
        assert r.observed


def test_deck_versions_may_differ_without_upsetting_the_checks(healthy):
    # critique B4 again, from the health side: nothing here compares them.
    assert healthy.deck_versions == ("Ver_1", "Ver_2")
    assert _report(healthy).can_run


# ---- what the default check list covers --------------------------------------


def test_default_checks_cover_every_area_the_flow_depends_on(healthy):
    ids = {c.check_id for c in default_checks(healthy)}
    assert {
        "pdk.tech_name",
        "pdk.layer_map",
        "pdk.tech_library_file",
        "pdk.cdl_include",
        "lvs.deck_dir",
        "lvs.rules_files",
        "lvs.variants",
        "qrc.deck_dir",
        "qrc.query_cmd",
        "qrc.preserve_cell_list",
        "pdk.corners",
        "pdk.power_names",
        "pdk.ground_names",
    } <= ids
    assert {f"tool.{stage}" for stage in DEFAULT_TOOL_EXECUTABLES} <= ids


def test_required_env_vars_each_get_their_own_check(healthy):
    healthy.required_env = ["SETUP_ROOT", "VERIFY_ROOT"]
    ids = [c.check_id for c in default_checks(healthy)]
    assert ids[:2] == ["env.setup_root", "env.verify_root"]


def test_every_fix_hint_names_a_command_or_a_field(healthy):
    healthy.required_env = ["SETUP_ROOT"]
    for check in default_checks(healthy):
        # A hint has to be actionable: it quotes a command / field with
        # backticks or points at the profile file.
        assert "`" in check.fix_hint or "config/profiles/" in check.fix_hint
        assert len(check.fix_hint) > 40


def test_check_ids_are_unique(healthy):
    healthy.required_env = ["SETUP_ROOT", "VERIFY_ROOT"]
    ids = [c.check_id for c in default_checks(healthy)]
    assert len(ids) == len(set(ids))
    # And the profile itself accepts them, i.e. they are valid slugs.
    healthy.checks = default_checks(healthy)


def test_env_vars_differing_only_in_case_still_get_distinct_ids(healthy):
    # Slugs are lowercase but Linux env names are case-sensitive; folding the
    # two onto one id would make the report fail validation.
    healthy.required_env = ["SETUP_ROOT", "setup_root"]
    ids = [c.check_id for c in default_checks(healthy)][:2]
    assert ids == ["env.setup_root", "env.setup_root.2"]
    report = _report(healthy)
    assert len(report.results) == len(default_checks(healthy))


def test_an_undiscovered_deck_asks_for_the_field_not_the_directory(healthy):
    healthy.lvs_decks = LvsDeckSet()
    healthy.qrc = QrcDeck()
    by_id = {c.check_id: c for c in default_checks(healthy)}
    assert by_id["lvs.deck_dir"].kind is PdkCheckKind.FIELD_SET
    assert by_id["qrc.deck_dir"].kind is PdkCheckKind.FIELD_SET
    # ... and the QRC hint explains why it cannot be derived from the LVS one.
    assert "QCI_deck" in by_id["qrc.deck_dir"].fix_hint
    # No rules-file glob is declared when there is no directory to glob.
    assert "lvs.rules_files" not in by_id


def test_a_profile_may_override_the_check_list(healthy):
    healthy.checks = [
        PdkCheck(
            check_id="only.one",
            title="Only check",
            kind=PdkCheckKind.FIELD_SET,
            target="tech_name",
            fix_hint="set tech_name",
        )
    ]
    report = _report(healthy)
    assert [r.check_id for r in report.results] == ["only.one"]


# ---- env resolution ----------------------------------------------------------


def test_env_refs_include_path_expressions_not_just_required_env():
    p = PdkProfile(profile_id="p", display_name="p", required_env=["FOO"])
    refs = profile_env_refs(p)
    assert "FOO" in refs
    assert "SETUP_ROOT" in refs           # from tech_library_file
    assert "PDK_LAYER_MAP_FILE" in refs   # from layer_map
    assert "calibre_source_added_place" in refs  # from cdl_include_files
    assert "PDK_TECH_FILE" in refs        # tech_name is unset -> candidates


def test_env_refs_drop_the_tech_name_candidates_once_it_is_pinned():
    p = PdkProfile(profile_id="p", display_name="p", tech_name="HN001")
    assert "PDK_TECH_FILE" not in profile_env_refs(p)


def test_env_check_passes_from_an_override(healthy, monkeypatch):
    monkeypatch.delenv("VERIFY_ROOT", raising=False)
    healthy.required_env = ["VERIFY_ROOT"]
    healthy.env_overrides = {"VERIFY_ROOT": "/v"}
    result = _report(healthy).result("env.verify_root")
    assert result.status is CheckStatus.OK
    assert result.observed == "/v" and "override" in result.message


def test_env_check_fails_when_unset(healthy, monkeypatch):
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    healthy.required_env = ["NOT_SET_ANYWHERE"]
    result = _report(healthy).result("env.not_set_anywhere")
    assert result.status is CheckStatus.FAIL
    assert "env_overrides" in result.fix_hint


def test_an_empty_env_var_counts_as_unset(healthy, monkeypatch):
    monkeypatch.setenv("BLANK_VAR", "")
    healthy.required_env = ["BLANK_VAR"]
    assert _report(healthy).result("env.blank_var").status is CheckStatus.FAIL


def test_resolve_profile_env_feeds_the_check_env_table(healthy, monkeypatch):
    monkeypatch.setenv("VERIFY_ROOT", "/from/shell")
    healthy.required_env = ["VERIFY_ROOT"]
    rows = dict((name, (src, val)) for name, src, val in iter_env_rows(resolve_profile_env(healthy)))
    assert rows["VERIFY_ROOT"] == ("shell", "/from/shell")


# ---- undetermined is not failed ----------------------------------------------


def test_an_unresolved_env_var_makes_the_path_check_unknown(monkeypatch):
    for var in ("SETUP_ROOT", "PDK_LAYER_MAP_FILE", "calibre_source_added_place"):
        monkeypatch.delenv(var, raising=False)
    p = PdkProfile(profile_id="p", display_name="p", tech_name="HN001")
    report = check_profile(p, which=_which())

    lib = report.result("pdk.tech_library_file")
    assert lib.status is CheckStatus.UNKNOWN
    assert "$SETUP_ROOT" in lib.message
    # The blame lands on the variable, not on a file nobody could name.
    assert "assura_tech.lib" not in lib.message
    # Undetermined still blocks a run.
    assert not report.can_run


def test_a_resolved_but_absent_file_is_a_real_failure(healthy, tmp_path):
    healthy.layer_map = (tmp_path / "nope.map").as_posix()
    result = _report(healthy).result("pdk.layer_map")
    assert result.status is CheckStatus.FAIL
    assert result.message == "not a regular file"


def test_a_directory_where_a_file_is_expected_fails(healthy, deck_tree):
    healthy.layer_map = deck_tree["setup"].as_posix()
    assert _report(healthy).result("pdk.layer_map").status is CheckStatus.FAIL


def test_a_missing_deck_directory_fails(healthy, tmp_path):
    healthy.lvs_decks.dir_expr = (tmp_path / "gone").as_posix()
    report = _report(healthy)
    assert report.result("lvs.deck_dir").status is CheckStatus.FAIL
    # ... and the glob inside it says so rather than blaming the pattern.
    assert "directory does not exist" in report.result("lvs.rules_files").message


def test_a_deck_directory_without_rules_files_fails(healthy, deck_tree):
    (deck_tree["lvs"] / "CFXXX.wodio.qcilvs").unlink()
    report = _report(healthy)
    assert report.result("lvs.deck_dir").status is CheckStatus.OK
    assert report.result("lvs.rules_files").status is CheckStatus.FAIL


def test_the_rules_glob_reports_what_it_found(healthy):
    result = _report(healthy).result("lvs.rules_files")
    assert result.status is CheckStatus.OK
    assert "CFXXX.wodio.qcilvs" in result.message
    assert result.observed.endswith("CFXXX.*.qcilvs")


def test_an_empty_table_fails_with_a_fix(healthy):
    healthy.default_corner = None  # order matters: validate_assignment refuses
    healthy.corners = []            # to leave default_corner dangling
    result = _report(healthy).result("pdk.corners")
    assert result.status is CheckStatus.FAIL
    assert "assura_tech.lib" in result.fix_hint


def test_tech_name_may_be_auto_derived(healthy, monkeypatch):
    healthy.tech_name = None
    monkeypatch.setenv("PDK_TECH_FILE", "/pdk/HN001/tech.db")
    result = _report(healthy).result("pdk.tech_name")
    assert result.status is CheckStatus.OK
    assert result.observed == "HN001"


def test_tech_name_fails_when_nothing_can_derive_it(healthy, monkeypatch):
    healthy.tech_name = None
    for var in healthy.tech_name_env_vars:
        monkeypatch.delenv(var, raising=False)
    assert _report(healthy).result("pdk.tech_name").status is CheckStatus.FAIL


def test_an_unsupported_field_target_is_unknown_not_green(healthy):
    healthy.checks = [
        PdkCheck(
            check_id="odd.one",
            title="Odd",
            kind=PdkCheckKind.FIELD_SET,
            target="no_such_field",
            fix_hint="this target is not supported",
        )
    ]
    result = _report(healthy).result("odd.one")
    assert result.status is CheckStatus.UNKNOWN
    assert "unsupported field target" in result.message


def test_a_malformed_glob_target_is_unknown(healthy):
    healthy.checks = [
        PdkCheck(
            check_id="odd.glob",
            title="Odd glob",
            kind=PdkCheckKind.GLOB_NONEMPTY,
            target="nodirectorypart",
            fix_hint="use '<directory expression>/<pattern>'",
        )
    ]
    assert _report(healthy).result("odd.glob").status is CheckStatus.UNKNOWN


# ---- tools -------------------------------------------------------------------


def test_tool_lookup_never_executes_anything(healthy):
    seen: list[str] = []

    def spy(name: str) -> str | None:
        seen.append(name)
        return f"/opt/eda/{name}"

    _report(healthy, which=spy)
    assert sorted(seen) == sorted(DEFAULT_TOOL_EXECUTABLES.values())


def test_a_missing_required_tool_blocks_the_run(healthy):
    report = _report(healthy, which=_which({"si": "/opt/si"}))
    assert report.result("tool.calibre").status is CheckStatus.FAIL
    assert not report.can_run


def test_a_missing_jivaro_is_only_a_warning(healthy):
    present = {k: v for k, v in ALL_TOOLS_PRESENT.items() if k != "jivaro"}
    report = _report(healthy, which=_which(present))
    result = report.result("tool.jivaro")
    assert result.status is CheckStatus.WARN
    assert not result.blocking
    assert report.can_run and report.exit_code == 0
    assert "reduction disabled" in result.fix_hint


def test_tool_executables_stay_in_sync_with_the_tool_plugins():
    # The health module deliberately does not import auto_ext.tools (it stays a
    # leaf); this test is the seam that keeps the copy honest.
    from auto_ext.core.runner import _TOOL_REGISTRY

    assert DEFAULT_TOOL_EXECUTABLES == {
        stage: cls.executable for stage, cls in _TOOL_REGISTRY.items()
    }
    assert OPTIONAL_TOOLS <= set(_TOOL_REGISTRY)


# ---- required vs optional ----------------------------------------------------


def test_an_optional_check_degrades_a_failure_to_a_warning(healthy):
    healthy.power_names = []
    result = _report(healthy).result("pdk.power_names")
    assert result.status is CheckStatus.WARN
    assert not result.blocking
    assert result.fix_hint


def test_a_missing_preserve_cell_list_does_not_block(healthy, deck_tree):
    (deck_tree["qrc"] / "preserveCellList.txt").unlink()
    report = _report(healthy)
    assert report.result("qrc.preserve_cell_list").status is CheckStatus.WARN
    assert report.can_run


def test_a_missing_query_cmd_does_block(healthy, deck_tree):
    (deck_tree["qrc"] / "query_cmd").unlink()
    report = _report(healthy)
    assert report.result("qrc.query_cmd").status is CheckStatus.FAIL
    assert not report.can_run


# ---- rendering ---------------------------------------------------------------


def test_format_report_states_the_verdict_and_lists_fixes(healthy):
    healthy.default_corner = None  # order matters: validate_assignment refuses
    healthy.corners = []            # to leave default_corner dangling
    text = format_report(_report(healthy))
    assert "cannot run yet" in text
    assert "Process corner table" in text
    # The hint must send the user to the Quantus RuleSet, NOT to
    # `grep -i corner $SETUP_ROOT/assura_tech.lib`, which is what it used to say
    # until 2026-08-24, when that grep was run on a real PDK and came back empty.
    assert "RuleSet" in text
    assert "grep -i corner" not in text


def test_format_report_says_ready_when_it_is(healthy):
    assert "ready to run" in format_report(_report(healthy))


# ---- cache -------------------------------------------------------------------


def test_cache_lands_next_to_the_profile(tmp_path):
    assert health_cache_path(tmp_path / "profiles" / "hn001.yaml") == (
        tmp_path / "profiles" / "hn001.health.json"
    )


def test_report_round_trips_through_the_cache(healthy, tmp_path):
    profile_path = tmp_path / "profiles" / "hn001.yaml"
    report = _report(healthy)
    written = write_report(profile_path, report)
    assert written.is_file()
    assert read_report(profile_path) == report


def test_reading_a_missing_cache_is_a_miss_not_an_error(tmp_path):
    assert read_report(tmp_path / "hn001.yaml") is None


def test_reading_a_corrupt_cache_is_a_miss_not_an_error(tmp_path):
    profile_path = tmp_path / "hn001.yaml"
    health_cache_path(profile_path).write_text("{not json", encoding="utf-8")
    assert read_report(profile_path) is None


def test_a_cache_that_does_not_match_the_schema_is_a_miss(tmp_path):
    profile_path = tmp_path / "hn001.yaml"
    health_cache_path(profile_path).write_text(json.dumps({"nope": 1}), encoding="utf-8")
    assert read_report(profile_path) is None


def test_second_call_comes_from_the_cache(healthy, tmp_path):
    profile_path = tmp_path / "hn001.yaml"
    first, from_cache = cached_or_check(profile_path, healthy, which=_which())
    assert not from_cache
    second, from_cache = cached_or_check(profile_path, healthy, which=_which())
    assert from_cache and second == first


def test_editing_the_profile_invalidates_the_cache(healthy, tmp_path):
    profile_path = tmp_path / "hn001.yaml"
    cached_or_check(profile_path, healthy, which=_which())
    healthy.default_corner = None  # order matters: validate_assignment refuses
    healthy.corners = []            # to leave default_corner dangling
    report, from_cache = cached_or_check(profile_path, healthy, which=_which())
    assert not from_cache
    assert report.result("pdk.corners").status is CheckStatus.FAIL


def test_an_aged_out_cache_is_re_run(healthy, tmp_path, monkeypatch):
    profile_path = tmp_path / "hn001.yaml"
    stale = utcnow() - timedelta(hours=2)
    cached_or_check(profile_path, healthy, which=_which(), now=stale)
    _, from_cache = cached_or_check(
        profile_path, healthy, which=_which(), max_age_s=60
    )
    assert not from_cache
    _, from_cache = cached_or_check(
        profile_path, healthy, which=_which(), max_age_s=24 * 3600
    )
    assert from_cache


def test_force_bypasses_a_valid_cache(healthy, tmp_path):
    profile_path = tmp_path / "hn001.yaml"
    cached_or_check(profile_path, healthy, which=_which())
    _, from_cache = cached_or_check(profile_path, healthy, which=_which(), force=True)
    assert not from_cache


def test_write_false_leaves_no_cache_behind(healthy, tmp_path):
    profile_path = tmp_path / "hn001.yaml"
    cached_or_check(profile_path, healthy, which=_which(), write=False)
    assert not health_cache_path(profile_path).exists()


def test_writing_the_cache_leaves_no_temp_file(healthy, tmp_path):
    profile_path = tmp_path / "profiles" / "hn001.yaml"
    write_report(profile_path, _report(healthy))
    assert [p.name for p in profile_path.parent.iterdir()] == ["hn001.health.json"]


def test_write_failure_is_a_health_error(healthy, tmp_path, monkeypatch):
    def boom(*_args: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    with pytest.raises(health.HealthError, match="cannot write"):
        write_report(tmp_path / "hn001.yaml", _report(healthy))
