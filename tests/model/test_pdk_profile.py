"""The PdkProfile contract.

Covers section 1.1 of ``docs/refactor/01-schema.md`` plus the two corrections
``docs/refactor/05-catalog-critique.md`` makes to it: the LVS/QRC deck
asymmetry (B4) and the parasitic-device contract (D4).

The through-line of every test here: a profile may be *incomplete*, and that
must be representable, but it may never be *wrong*. An empty corner table is a
legal profile; a corner table with two entries called ``typical`` is not.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from auto_ext.model.pdk import (
    PDK_PROFILE_SCHEMA_VERSION,
    CheckStatus,
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    ParasiticDeviceContract,
    PdkCheck,
    PdkCheckKind,
    PdkCheckResult,
    PdkHealthReport,
    PdkProfile,
    QrcDeck,
)

NOW = datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc)


def _profile(**kw: object) -> PdkProfile:
    base: dict[str, object] = {"profile_id": "hn001", "display_name": "HN001 22nm"}
    base.update(kw)
    return PdkProfile(**base)  # type: ignore[arg-type]


def _result(check_id: str, status: CheckStatus, *, required: bool = True) -> PdkCheckResult:
    return PdkCheckResult(
        check_id=check_id, status=status, required=required, title=check_id, checked_at=NOW
    )


# ---- shape and defaults ------------------------------------------------------


def test_minimal_profile_carries_the_template_literals_as_defaults():
    p = _profile()
    assert p.schema_version == PDK_PROFILE_SCHEMA_VERSION
    # These four are the literals lifted out of the templates; a migration is
    # byte-neutral only while they keep exactly these values.
    assert p.tech_library_file == "$env(SETUP_ROOT)/assura_tech.lib"
    assert p.layer_map == "${PDK_LAYER_MAP_FILE}"
    assert p.cdl_include_files == ["$calibre_source_added_place"]
    assert p.tech_name_env_vars == ["PDK_TECH_FILE", "PDK_LAYER_MAP_FILE", "PDK_DISPLAY_FILE"]


def test_an_undiscovered_profile_is_empty_never_guessed():
    p = _profile()
    assert p.lvs_decks.dir_expr is None
    assert p.lvs_decks.variants == []
    assert p.qrc.dir_expr is None
    assert p.corners == []
    assert p.default_corner is None
    assert p.power_names == [] and p.ground_names == []


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        _profile(techname="HN001")


def test_profile_id_must_be_a_slug():
    with pytest.raises(ValidationError):
        _profile(profile_id="HN001")
    with pytest.raises(ValidationError):
        _profile(profile_id="../escape")


def test_display_name_must_not_be_empty():
    with pytest.raises(ValidationError):
        _profile(display_name="")


def test_json_round_trip_is_lossless():
    p = _profile(
        corners=[CornerSpec(name="typical", technology_corner="TYPICAL")],
        default_corner="typical",
        lvs_decks=LvsDeckSet(
            dir_expr="$calibre_source_added_place|parent",
            basename="CFXXX",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="wodio",
        ),
        power_names=["VDD", "AVDD"],
    )
    again = PdkProfile.model_validate(json.loads(p.model_dump_json()))
    assert again == p


# ---- env-name and path-key hygiene -------------------------------------------


@pytest.mark.parametrize("bad", ["not a var", "2START", "with-dash", ""])
def test_required_env_rejects_non_identifier_names(bad: str):
    with pytest.raises(ValidationError):
        _profile(required_env=[bad])


def test_required_env_rejects_duplicates():
    with pytest.raises(ValidationError):
        _profile(required_env=["SETUP_ROOT", "SETUP_ROOT"])


def test_extra_paths_keys_must_survive_as_jinja_attributes():
    # They are exposed as ``pdk.paths.<key>``; a key with a dash or a capital
    # would render as an attribute nobody can reference.
    _profile(extra_paths={"calibre_lvs_dir": "$X"})
    with pytest.raises(ValidationError):
        _profile(extra_paths={"calibre-lvs-dir": "$X"})
    with pytest.raises(ValidationError):
        _profile(extra_paths={"CalibreDir": "$X"})


# ---- corners -----------------------------------------------------------------


def test_duplicate_corner_names_are_rejected():
    with pytest.raises(ValidationError, match="duplicate corner names"):
        _profile(
            corners=[
                CornerSpec(name="typical", technology_corner="TYPICAL"),
                CornerSpec(name="typical", technology_corner="TYP"),
            ]
        )


def test_alias_may_not_shadow_another_corner():
    with pytest.raises(ValidationError, match="collides"):
        _profile(
            corners=[
                CornerSpec(name="typical", technology_corner="TYPICAL"),
                CornerSpec(name="cworst", technology_corner="CWORST", aliases=["typical"]),
            ]
        )


def test_default_corner_must_exist():
    with pytest.raises(ValidationError, match="default_corner"):
        _profile(
            corners=[CornerSpec(name="typical", technology_corner="TYPICAL")],
            default_corner="rcworst",
        )


def test_corner_lookup_accepts_name_and_alias():
    p = _profile(
        corners=[
            CornerSpec(
                name="rcworst",
                technology_corner="RCWORST_CCWORST",
                aliases=["rcworst_ccworst"],
                default_temperature_c=125.0,
            )
        ]
    )
    assert p.corner("rcworst") is p.corners[0]
    assert p.corner("rcworst_ccworst") is p.corners[0]
    assert p.corner("cbest") is None
    # The Recipe writes the semantic name; only the profile knows the literal.
    assert p.corner("rcworst").technology_corner == "RCWORST_CCWORST"


def test_corner_literal_may_not_be_empty():
    with pytest.raises(ValidationError):
        CornerSpec(name="typical", technology_corner="")


# ---- LVS deck set ------------------------------------------------------------


def test_duplicate_variant_names_are_rejected():
    with pytest.raises(ValidationError, match="duplicate lvs deck variant"):
        LvsDeckSet(
            variants=[
                LvsDeckVariant(name="wodio", rules_suffix="wodio"),
                LvsDeckVariant(name="wodio", rules_suffix="WODIO"),
            ]
        )


def test_default_variant_must_be_in_the_table():
    with pytest.raises(ValidationError, match="default_variant"):
        LvsDeckSet(
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="widio",
        )


def test_filename_pattern_needs_both_slots():
    with pytest.raises(ValidationError, match="missing"):
        LvsDeckSet(filename_pattern="{basename}.qcilvs")


def test_basename_falls_back_to_the_last_directory_segment():
    # This is the rule runner._build_context already implements; keeping it
    # means a migrated profile renders the same *lvsRulesFile line.
    decks = LvsDeckSet(dir_expr="$X|parent")
    assert decks.basename_for_dir("/v/runset/Calibre_QRC/LVS/Ver_1/CFXXX") == "CFXXX"
    assert LvsDeckSet(basename="OTHER").basename_for_dir("/a/CFXXX") == "OTHER"


def test_filename_for_assembles_the_three_segments():
    decks = LvsDeckSet(
        basename="CFXXX", variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")]
    )
    assert decks.filename_for("wodio") == "CFXXX.wodio.qcilvs"


def test_filename_for_refuses_a_variant_this_pdk_does_not_have():
    decks = LvsDeckSet(basename="CFXXX")
    with pytest.raises(KeyError, match="widio"):
        decks.filename_for("widio")


def test_glob_pattern_widens_when_the_basename_is_unknown():
    assert LvsDeckSet(basename="CFXXX").glob_pattern() == "CFXXX.*.qcilvs"
    assert LvsDeckSet().glob_pattern() == "*.*.qcilvs"


# ---- B4: the two decks are independent ---------------------------------------


def test_lvs_and_qrc_runset_versions_may_differ():
    # The one real sample has exactly this mismatch. Nothing may reject it.
    p = _profile(
        lvs_decks=LvsDeckSet(
            dir_expr="$VERIFY_ROOT/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX",
            runset_version="Ver_Plus_1.0l_0.9",
        ),
        qrc=QrcDeck(
            dir_expr="$VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CFXXX/QCI_deck",
            runset_version="Ver_Plus_1.0a",
        ),
    )
    assert p.deck_versions == ("Ver_Plus_1.0l_0.9", "Ver_Plus_1.0a")
    # ... and the QRC path is one level deeper, which is why it is its own field.
    assert p.qrc.dir_expr.endswith("/QCI_deck")
    assert not p.lvs_decks.dir_expr.endswith("/QCI_deck")


def test_qrc_deck_file_names_are_data_not_literals():
    qrc = QrcDeck(dir_expr="/decks/QCI_deck")
    assert qrc.query_cmd_name == "query_cmd"
    assert qrc.preserve_cell_list_name == "preserveCellList.txt"


# ---- D4: parasitic device contract -------------------------------------------


def test_default_parasitic_contract_matches_the_shipped_templates():
    c = ParasiticDeviceContract()
    assert c.res_component == "presistor" and c.cap_component == "pcapacitor"
    assert c.model_cells == {
        "res": "presistor",
        "cap": "pcapacitor",
        "ind": "pinductor",
        "mutual": "pmind",
    }


def test_changing_only_the_quantus_side_is_rejected():
    # This is the silent failure D4 describes: Quantus writes one device name,
    # Jivaro looks for another, and the reduction reads nothing at all.
    with pytest.raises(ValidationError, match="contract is broken"):
        ParasiticDeviceContract(res_component="myres")


def test_changing_both_sides_together_is_accepted():
    c = ParasiticDeviceContract(res_component="myres", res_model="myLib/myres/symbol")
    assert c.model_cells["res"] == "myres"


def test_model_path_must_be_library_cell_view():
    with pytest.raises(ValidationError, match="library/cell/view"):
        ParasiticDeviceContract(cap_model="analogLib/pcapacitor")


def test_inductor_side_is_unchecked_until_quantus_grows_one():
    # No RLCK extraction today, so there is no Quantus counterpart to compare
    # against; the asymmetry is recorded, not smoothed over.
    assert ParasiticDeviceContract().ind_component is None
    with pytest.raises(ValidationError, match="contract is broken"):
        ParasiticDeviceContract(ind_component="myind")


# ---- C4: the si.env incFILE slot ---------------------------------------------


def test_single_cdl_include_flattens_to_the_one_si_env_slot():
    assert _profile().cdl_include_file == "$calibre_source_added_place"
    assert _profile(cdl_include_files=[]).cdl_include_file is None


def test_several_cdl_includes_refuse_to_flatten_instead_of_dropping_one():
    p = _profile(cdl_include_files=["$a", "$b"])
    with pytest.raises(NotImplementedError, match="single incFILE slot"):
        _ = p.cdl_include_file


# ---- checks and health -------------------------------------------------------


def _check(check_id: str, **kw: object) -> PdkCheck:
    base: dict[str, object] = {
        "check_id": check_id,
        "title": check_id,
        "kind": PdkCheckKind.ENV_VAR,
        "target": "SETUP_ROOT",
        "fix_hint": "source the setup script",
    }
    base.update(kw)
    return PdkCheck(**base)  # type: ignore[arg-type]


def test_duplicate_check_ids_are_rejected():
    with pytest.raises(ValidationError, match="duplicate check_id"):
        _profile(checks=[_check("env.setup_root"), _check("env.setup_root")])


def test_check_needs_a_fix_hint():
    with pytest.raises(ValidationError):
        _check("env.setup_root", fix_hint="")


def test_result_ok_and_blocking_semantics():
    assert _result("a", CheckStatus.OK).ok
    assert not _result("a", CheckStatus.OK).blocking
    assert _result("a", CheckStatus.FAIL).blocking
    # Undetermined blocks too: "we could not tell" is not permission to run.
    assert _result("a", CheckStatus.UNKNOWN).blocking
    # Optional checks never block, whatever they say.
    assert not _result("a", CheckStatus.FAIL, required=False).blocking
    assert not _result("a", CheckStatus.WARN).blocking


def test_report_verdicts():
    report = PdkHealthReport(
        profile_id="hn001",
        checked_at=NOW,
        results=[
            _result("a", CheckStatus.OK),
            _result("b", CheckStatus.WARN, required=False),
            _result("c", CheckStatus.FAIL),
        ],
    )
    assert not report.ok
    assert not report.can_run
    assert [r.check_id for r in report.blocking] == ["c"]
    assert [r.check_id for r in report.warnings] == ["b"]
    assert report.counts() == {"ok": 1, "warn": 1, "fail": 1, "unknown": 0}
    assert report.exit_code == 1
    assert report.result("b").status is CheckStatus.WARN
    assert report.result("zz") is None


def test_a_report_with_only_warnings_still_lets_the_run_start():
    report = PdkHealthReport(
        profile_id="hn001",
        checked_at=NOW,
        results=[_result("a", CheckStatus.OK), _result("b", CheckStatus.FAIL, required=False)],
    )
    assert not report.ok and report.can_run and report.exit_code == 0


def test_report_rejects_duplicate_result_ids():
    with pytest.raises(ValidationError, match="duplicate check_id"):
        PdkHealthReport(
            profile_id="hn001",
            checked_at=NOW,
            results=[_result("a", CheckStatus.OK), _result("a", CheckStatus.OK)],
        )


# ---- fingerprint -------------------------------------------------------------


def test_fingerprint_ignores_bookkeeping_but_tracks_content():
    p = _profile()
    later = _profile(scanned_at=p.scanned_at + timedelta(hours=3), hand_edited=True)
    assert p.fingerprint() == later.fingerprint()

    changed = _profile(corners=[CornerSpec(name="typical", technology_corner="TYPICAL")])
    assert changed.fingerprint() != p.fingerprint()
