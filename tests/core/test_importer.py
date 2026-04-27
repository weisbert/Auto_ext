"""Tests for :mod:`auto_ext.core.importer` — per-tool importers, candidate
detection, PdkToken detection, and smart re-import merge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.importer import (
    Identity,
    ImportError,
    import_template,
)


# ---- fixtures --------------------------------------------------------------


@pytest.fixture
def raw_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "raw"


@pytest.fixture
def calibre_raw(raw_dir: Path) -> str:
    return (raw_dir / "calibre_sample.qci").read_text(encoding="utf-8")


@pytest.fixture
def si_raw(raw_dir: Path) -> str:
    return (raw_dir / "si_sample.env").read_text(encoding="utf-8")


@pytest.fixture
def quantus_raw(raw_dir: Path) -> str:
    return (raw_dir / "quantus_sample.cmd").read_text(encoding="utf-8")


@pytest.fixture
def jivaro_raw(raw_dir: Path) -> str:
    return (raw_dir / "jivaro_sample.xml").read_text(encoding="utf-8")


# ---- dispatcher ------------------------------------------------------------


def test_import_unknown_tool_rejected() -> None:
    with pytest.raises(ImportError, match="unknown tool"):
        import_template("bogus", "")  # type: ignore[arg-type]


# ---- calibre ---------------------------------------------------------------


def test_calibre_identity_extraction(calibre_raw: str) -> None:
    result = import_template("calibre", calibre_raw)
    assert result.tool == "calibre"
    assert result.identity == Identity(
        cell="INV1",
        library="INV_LIB",
        lvs_layout_view="layout",
        lvs_source_view="schematic",
    )
    assert result.raw_source == calibre_raw


def test_calibre_template_body_substitutes_identity_at_every_position(
    calibre_raw: str,
) -> None:
    body = import_template("calibre", calibre_raw).template_body
    # Every place the raw had the literal cell/library/view should now be
    # the placeholder. (Other lines — Tcl {{…}} triggers, SVDBcci, etc. —
    # pass through unchanged.)
    assert "*lvsLayoutPrimary: [[cell]]" in body
    assert "*lvsLayoutLibrary: [[library]]" in body
    assert "*lvsLayoutView: [[lvs_layout_view]]" in body
    assert "*lvsSourceView: [[lvs_source_view]]" in body
    assert "*lvsLayoutPaths: [[cell]].calibre.db" in body
    assert "*lvsSourcePath: [[cell]].src.net" in body
    assert "*lvsSpiceFile: [[cell]].sp" in body
    assert "*lvsERCDatabase: [[cell]].erc.results" in body
    assert "*lvsERCSummaryFile: [[cell]].erc.summary" in body
    assert "*lvsReportFile: [[cell]].lvs.report" in body
    assert "*cmnFDIDEFLayoutPath: [[cell]].def" in body
    assert "*lvsRunDir: [[output_dir]]" in body
    assert "*cmnTemplate_RN: [[output_dir]]" in body
    # Cross-checked identity appears exactly once per key line — no
    # leftover bare INV1 / INV_LIB in the body.
    assert "INV1" not in body
    assert "INV_LIB" not in body


def test_calibre_pathological_cell_no_substring_overshoot() -> None:
    # A cell named "data" must not match inside an absolute path like
    # "/data/RFIC3/...". Substitution is keyed, not global.
    raw = (
        "*lvsLayoutPrimary: data\n"
        "*lvsLayoutLibrary: SOMELIB\n"
        "*lvsLayoutView: layout\n"
        "*lvsSourceView: schematic\n"
        '*lvsPostTriggers: {{cat /data/RFIC3/foo/bar} process 1}\n'
    )
    result = import_template("calibre", raw)
    assert result.identity.cell == "data"
    # /data/RFIC3 stays intact.
    assert "/data/RFIC3/foo/bar" in result.template_body
    # The identity position is replaced with the placeholder.
    assert "*lvsLayoutPrimary: [[cell]]" in result.template_body


def test_calibre_cross_validation_mismatch_raises() -> None:
    raw = (
        "*lvsLayoutPrimary: INV1\n"
        "*lvsSourcePrimary: INV_OTHER\n"
    )
    with pytest.raises(ImportError, match="inconsistent"):
        import_template("calibre", raw)


def test_calibre_override_wins_and_warns(
    calibre_raw: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        result = import_template(
            "calibre",
            calibre_raw,
            identity_overrides=Identity(cell="OVERRIDDEN"),
        )
    assert result.identity.cell == "OVERRIDDEN"
    # A warning was logged because inferred (INV1) disagrees with override.
    assert any("OVERRIDDEN" in r.message for r in caplog.records)


def test_calibre_override_without_conflict_is_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = "*lvsSpiceFile: INV1.sp\n"
    with caplog.at_level("WARNING"):
        result = import_template(
            "calibre",
            raw,
            identity_overrides=Identity(cell="INV1", library="MY_LIB"),
        )
    # No warning because override matches inferred, and library wasn't in raw.
    assert result.identity.cell == "INV1"
    assert result.identity.library == "MY_LIB"
    assert not caplog.records


def test_calibre_preserves_non_matching_lines(calibre_raw: str) -> None:
    # Tcl-brace lines like *lvsPreTriggers:{{rm -rf ...}} should pass through.
    body = import_template("calibre", calibre_raw).template_body
    assert "*lvsPreTriggers: {{rm -rf %d/svdb} process 1}" in body
    assert "*cmnSpecifyLicenseWaitTime: 1" in body


def test_calibre_preserves_trailing_newline(calibre_raw: str) -> None:
    body = import_template("calibre", calibre_raw).template_body
    assert body.endswith("\n")


# ---- si --------------------------------------------------------------------


def test_si_identity_extraction(si_raw: str) -> None:
    result = import_template("si", si_raw)
    assert result.tool == "si"
    assert result.identity.cell == "INV1"
    assert result.identity.library == "INV_LIB"
    assert result.identity.lvs_source_view == "schematic"


def test_si_template_body_substitutions(si_raw: str) -> None:
    body = import_template("si", si_raw).template_body
    assert 'simLibName = "[[library]]"' in body
    assert 'simCellName = "[[cell]]"' in body
    assert 'simViewName = "[[lvs_source_view]]"' in body
    assert 'hnlNetlistFileName = "[[cell]].src.net"' in body
    assert 'simRunDir = "[[output_dir]]"' in body
    # Quoted non-identity strings pass through unchanged.
    assert 'simSimulator = "auCdl"' in body
    # The employee segment of ``/data/RFIC3/<project>/<employee>/`` is
    # substituted via the shared employee_id pre-pass; the project segment
    # stays raw so the PdkToken detector can still surface it as
    # ``project_subdir`` for init-project to promote.
    assert "/data/RFIC3/projB/[[employee_id]]/setup/verification" in body
    assert "/data/RFIC3/projB/alice/" not in body


def test_si_cross_validation_mismatch() -> None:
    raw = 'simLibName = "A"\nhnlNetlistFileName = "B.src.net"\nsimCellName = "C"\n'
    with pytest.raises(ImportError, match="inconsistent"):
        import_template("si", raw)


def test_si_injects_simrundir_when_missing() -> None:
    """Some Cadence flows export si.env without simRunDir. Without that
    line si writes netlist into cwd, breaking quantus downstream
    (LBRCXM-756). The importer auto-appends the canonical line so the
    imported template stays usable."""
    raw = (
        'simLibName = "MY_LIB"\n'
        'simCellName = "MY_CELL"\n'
        'simViewName = "schematic"\n'
        'simSimulator = "auCdl"\n'
        'incFILE = "$calibre_source_added_place"\n'
    )
    body = import_template("si", raw).template_body
    assert 'simRunDir = "[[output_dir]]"' in body
    # Identity substitution still ran on the lines that were present.
    assert 'simLibName = "[[library]]"' in body
    assert 'simCellName = "[[cell]]"' in body


def test_si_does_not_duplicate_simrundir_when_present(si_raw: str) -> None:
    """If the raw already carries a simRunDir line, the importer
    substitutes it and doesn't append a second copy."""
    body = import_template("si", si_raw).template_body
    assert body.count('simRunDir =') == 1
    assert 'simRunDir = "[[output_dir]]"' in body


def test_si_inject_preserves_trailing_newline_shape() -> None:
    """Body without a trailing newline gets one before the inject so
    the resulting file is still well-formed (every line newline-ended)."""
    raw = 'simLibName = "L"'  # no trailing newline at all
    body = import_template("si", raw).template_body
    assert body.endswith('simRunDir = "[[output_dir]]"\n')
    assert '\n\nsimRunDir' not in body  # no double-blank-line at the join


# ---- quantus ---------------------------------------------------------------


def test_quantus_identity_extraction(quantus_raw: str) -> None:
    result = import_template("quantus", quantus_raw)
    assert result.tool == "quantus"
    assert result.identity.cell == "INV1"
    assert result.identity.library == "INV_LIB"
    assert result.identity.lvs_layout_view == "layout"
    assert result.identity.ground_net == "vss"
    assert result.identity.out_file == "av_ext"


def test_quantus_template_body_substitutions(quantus_raw: str) -> None:
    body = import_template("quantus", quantus_raw).template_body
    assert '-ground_net "[[ground_net]]"' in body
    assert '-design_cell_name "[[cell]] [[lvs_layout_view]] [[library]]"' in body
    assert '-directory_name "[[output_dir]]/query_output"' in body
    assert (
        '-layer_map_file "[[output_dir]]/query_output/Design.gds.map"'
        in body
    )
    assert '-view_name "[[out_file]]"' in body
    # Numeric options pass through unmodified (candidate detector picks them up).
    assert "-exclude_floating_nets_limit 5000" in body
    assert "55.0" in body
    # Technology name is a PdkToken, not identity — stays as raw literal.
    assert '-technology_name "HN001"' in body


def test_quantus_device_properties_file_substituted() -> None:
    raw = (
        '              -device_properties_file '
        '"/work/cds/verify/QCI_PATH_INV1/query_output/Design.props"\n'
    )
    body = import_template("quantus", raw).template_body
    assert (
        '-device_properties_file "[[output_dir]]/query_output/Design.props"'
        in body
    )


def test_quantus_cross_validation_mismatch_on_output_dir() -> None:
    # Two -directory_name-style keys resolving to different output_dir
    # prefixes is an inconsistency that must raise.
    raw = (
        '              -directory_name "/a/query_output" \\\n'
        '              -layer_map_file "/b/query_output/Design.gds.map"\n'
    )
    with pytest.raises(ImportError, match="inconsistent"):
        import_template("quantus", raw)


# ---- jivaro ----------------------------------------------------------------


def test_jivaro_identity_extraction(jivaro_raw: str) -> None:
    result = import_template("jivaro", jivaro_raw)
    assert result.tool == "jivaro"
    assert result.identity.library == "INV_LIB"
    assert result.identity.cell == "INV1"
    assert result.identity.out_file == "av_ext"


def test_jivaro_template_body_substitutions(jivaro_raw: str) -> None:
    body = import_template("jivaro", jivaro_raw).template_body
    assert '<inputView value="[[library]]/[[cell]]/[[out_file]]"/>' in body
    # frequencyLimit / errorMax capture the raw value into a Jinja default.
    assert (
        '<frequencyLimit value="[[jivaro_frequency_limit | default(14)]]"/>'
        in body
    )
    assert (
        '<errorMax value="[[jivaro_error_max | default(2)]]"/>' in body
    )
    # Non-identity attrs like <cpu value="1"/> pass through.
    assert '<cpu value="1"/>' in body
    assert '<outputView value="[[out_file]]_red"/>' in body


def test_jivaro_preserves_non_matching_lines(jivaro_raw: str) -> None:
    body = import_template("jivaro", jivaro_raw).template_body
    assert body.startswith('<?xml version="1.0"')
    assert "</reductionParameters>" in body


# ---- override reconciliation -----------------------------------------------


def test_override_fills_missing_field() -> None:
    raw = 'simCellName = "INV1"\n'
    result = import_template(
        "si", raw, identity_overrides=Identity(library="EXTERNAL_LIB")
    )
    # Library didn't appear in raw but override still surfaces in Identity.
    assert result.identity.library == "EXTERNAL_LIB"


def test_quantus_employee_id_preprocessed(quantus_raw: str) -> None:
    # Every ``/tmpdata/RFIC/rfic_share/<id>/`` becomes
    # ``/tmpdata/RFIC/rfic_share/[[employee_id]]/`` — alice the employee
    # disappears from the template body entirely.
    body = import_template("quantus", quantus_raw).template_body
    assert "/tmpdata/RFIC/rfic_share/[[employee_id]]/" in body
    assert "/tmpdata/RFIC/rfic_share/alice/" not in body


def test_quantus_employee_id_does_not_touch_other_tmpdata_variants() -> None:
    # Only the specific /tmpdata/RFIC/rfic_share/<id>/ prefix matches.
    # Other tmpdata paths stay as-is.
    raw = (
        '              -some_option "/tmpdata/other/path/file" \\\n'
    )
    body = import_template("quantus", raw).template_body
    assert "/tmpdata/other/path/file" in body
    assert "[[employee_id]]" not in body


# ---- candidate detection ---------------------------------------------------


def _candidate(candidates, key):
    """Find the Candidate by key in a list, else return None."""
    for c in candidates:
        if c.key == key:
            return c
    return None


def test_candidates_calibre_classification(calibre_raw: str) -> None:
    cands = import_template("calibre", calibre_raw).candidates

    # cmnNumTurbo: int=2, high confidence, no bool-heuristic match.
    c = _candidate(cands, "cmnNumTurbo")
    assert c is not None and c.type == "int" and c.default == 2
    assert c.confidence == "high"
    assert c.suggested_name == "cmn_num_turbo"

    # cmnLicenseWaitTime: int=10, high — ``Wait`` is not a bool token.
    c = _candidate(cands, "cmnLicenseWaitTime")
    assert c is not None and c.type == "int" and c.default == 10
    assert c.confidence == "high"

    # lvsAbortOnSupplyError: 0 with ``Abort`` → bool=False, medium.
    c = _candidate(cands, "lvsAbortOnSupplyError")
    assert c is not None and c.type == "bool" and c.default is False
    assert c.confidence == "medium"

    # cmnRunHyper: 1 with ``Run``/``Hyper`` → bool=True, medium.
    c = _candidate(cands, "cmnRunHyper")
    assert c is not None and c.type == "bool" and c.default is True

    # cmnReleaseLicense: 1 with ``Release`` → bool=True, medium.
    c = _candidate(cands, "cmnReleaseLicense")
    assert c is not None and c.type == "bool"

    # lvsSVDBcci: 1 — no bool token, stays int high.
    c = _candidate(cands, "lvsSVDBcci")
    assert c is not None and c.type == "int" and c.default == 1
    assert c.confidence == "high"


def test_candidates_skip_substituted_identity(calibre_raw: str) -> None:
    cands = import_template("calibre", calibre_raw).candidates
    keys = {c.key for c in cands}
    # Identity lines were already substituted to ``[[cell]]`` etc.
    # — they must not appear as candidates.
    for ident_key in (
        "lvsLayoutPrimary",
        "lvsLayoutLibrary",
        "lvsLayoutView",
        "lvsSourcePrimary",
    ):
        assert ident_key not in keys


def test_candidates_skip_complex_structures(calibre_raw: str) -> None:
    # *lvsPreTriggers: {{rm -rf %d/svdb} process 1} — Tcl brace list,
    # not a knob candidate.
    cands = import_template("calibre", calibre_raw).candidates
    keys = {c.key for c in cands}
    assert "lvsPreTriggers" not in keys
    assert "cmnSlaveHosts" not in keys


def test_candidates_quantus_numeric(quantus_raw: str) -> None:
    cands = import_template("quantus", quantus_raw).candidates
    # -exclude_floating_nets_limit 5000 (int, high)
    c = _candidate(cands, "exclude_floating_nets_limit")
    assert c is not None and c.type == "int" and c.default == 5000
    # -coupling_cap_threshold_absolute 0.01 (float, high)
    c = _candidate(cands, "coupling_cap_threshold_absolute")
    assert c is not None and c.type == "float" and c.default == 0.01


def test_candidates_jivaro_skips_identity_and_filters() -> None:
    # frequencyLimit / errorMax got rewritten to [[...]] default() forms;
    # inputView too. No candidates.
    raw = (
        '<inputView value="LIB/CELL/av"/>\n'
        '<frequencyLimit value="14"/>\n'
        '<errorMax value="2"/>\n'
        '<cpu value="1"/>\n'
    )
    cands = import_template("jivaro", raw).candidates
    keys = {c.key for c in cands}
    assert keys == {"cpu"}
    c = _candidate(cands, "cpu")
    assert c.type == "int" and c.default == 1


def test_snake_case_preserves_already_snake() -> None:
    from auto_ext.core.importer import _snake_case

    assert _snake_case("exclude_floating_nets_limit") == "exclude_floating_nets_limit"
    assert _snake_case("cmnNumTurbo") == "cmn_num_turbo"
    assert _snake_case("lvsAbortOnSupplyError") == "lvs_abort_on_supply_error"


# ---- PdkToken detection ----------------------------------------------------


def _pdk_values_by_category(tokens):
    out: dict[str, set[str]] = {}
    for t in tokens:
        out.setdefault(t.category, set()).add(t.value)
    return out


def test_pdk_tokens_calibre_only_carries_segment_free_signals(
    calibre_raw: str,
) -> None:
    """Phase 5.6.5: pdk_subdir / runset_version / project_subdir
    categories were removed. Calibre raws have no HN... so PdkTokens
    are essentially empty (or just the rare abs_path)."""
    toks = import_template("calibre", calibre_raw).pdk_tokens
    by_cat = _pdk_values_by_category(toks)
    assert "tech_name" not in by_cat
    assert "pdk_subdir" not in by_cat
    assert "runset_version" not in by_cat


def test_pdk_tokens_quantus(quantus_raw: str) -> None:
    toks = import_template("quantus", quantus_raw).pdk_tokens
    by_cat = _pdk_values_by_category(toks)
    assert "HN001" in by_cat.get("tech_name", set())
    # /tmpdata/RFIC/rfic_share/[[employee_id]]/ is substituted, so
    # abs_path should NOT be reported for that path.
    for t in toks:
        assert "[[employee_id]]" not in t.value


def test_pdk_tokens_jivaro_has_none(jivaro_raw: str) -> None:
    toks = import_template("jivaro", jivaro_raw).pdk_tokens
    assert toks == []


def test_pdk_tokens_include_line_numbers(quantus_raw: str) -> None:
    toks = import_template("quantus", quantus_raw).pdk_tokens
    for t in toks:
        assert t.line >= 1


# ---- smart re-import merge -------------------------------------------------


def _build_manifest(**kwargs_for_knobs):
    from auto_ext.core.manifest import KnobSpec, SourceRef, TemplateManifest

    knobs = {}
    for name, cfg in kwargs_for_knobs.items():
        src = cfg.pop("source", None)
        if src is not None and not isinstance(src, SourceRef):
            src = SourceRef(**src)
        knobs[name] = KnobSpec(source=src, **cfg)
    return TemplateManifest(template="calibre_sample.qci", knobs=knobs)


def test_merge_substitutes_user_promoted_knob(calibre_raw: str) -> None:
    from auto_ext.core.importer import merge_reimport

    existing = _build_manifest(
        num_turbo={
            "type": "int",
            "default": 2,
            "source": {"tool": "calibre", "key": "cmnNumTurbo"},
            "description": "kept through merge",
        },
    )
    new_result = import_template("calibre", calibre_raw)
    outcome = merge_reimport(new_result, existing)

    # Body now references [[num_turbo]] at the cmnNumTurbo line.
    assert "*cmnNumTurbo: [[num_turbo]]" in outcome.body
    # Default unchanged (raw literal 2 == existing default 2).
    assert outcome.manifest.knobs["num_turbo"].default == 2
    # Manifest edit preserved.
    assert outcome.manifest.knobs["num_turbo"].description == "kept through merge"


def test_merge_refreshes_default_when_raw_changed() -> None:
    from auto_ext.core.importer import merge_reimport

    existing = _build_manifest(
        num_turbo={
            "type": "int",
            "default": 2,
            "source": {"tool": "calibre", "key": "cmnNumTurbo"},
        },
    )
    # Raw with a bumped cmnNumTurbo.
    raw = (
        "*lvsLayoutPrimary: INV1\n"
        "*lvsLayoutLibrary: LIB\n"
        "*lvsLayoutView: layout\n"
        "*lvsSourceView: schematic\n"
        "*cmnNumTurbo: 8\n"
    )
    new_result = import_template("calibre", raw)
    outcome = merge_reimport(new_result, existing)

    assert outcome.manifest.knobs["num_turbo"].default == 8
    assert "*cmnNumTurbo: [[num_turbo]]" in outcome.body
    assert any("default updated" in m for m in outcome.messages)


def test_merge_preserves_user_defined_knob_without_source(calibre_raw: str) -> None:
    from auto_ext.core.importer import merge_reimport

    existing = _build_manifest(
        manual_x={"type": "int", "default": 99},  # no source
    )
    new_result = import_template("calibre", calibre_raw)
    outcome = merge_reimport(new_result, existing)

    # Body unchanged for manual_x — importer has no idea where to substitute.
    assert "[[manual_x]]" not in outcome.body
    assert "manual_x" in outcome.manifest.knobs
    assert outcome.manifest.knobs["manual_x"].default == 99
    assert any(
        "user-defined" in m and "manual_x" in m for m in outcome.messages
    )


def test_merge_warns_when_source_key_missing_in_new_raw() -> None:
    from auto_ext.core.importer import merge_reimport

    existing = _build_manifest(
        vanished={
            "type": "int",
            "default": 42,
            "source": {"tool": "calibre", "key": "cmnThatWasRemoved"},
        },
    )
    # Raw that does NOT contain cmnThatWasRemoved.
    raw = "*lvsLayoutPrimary: INV1\n*lvsLayoutLibrary: LIB\n*lvsLayoutView: layout\n*lvsSourceView: schematic\n"
    new_result = import_template("calibre", raw)
    outcome = merge_reimport(new_result, existing)

    # Knob kept but surfaced as stale.
    assert "vanished" in outcome.manifest.knobs
    assert any(
        "not found" in m and "vanished" in m for m in outcome.messages
    )


# ---- Phase 4b2: aggregate_pdk_tokens ---------------------------------------


def _all_four_results(raw_dir: Path):
    """Import all 4 fixture raws and return the per-tool ImportResult dict."""
    from auto_ext.core.importer import import_template

    return {
        "calibre": import_template(
            "calibre", (raw_dir / "calibre_sample.qci").read_text(encoding="utf-8")
        ),
        "si": import_template(
            "si", (raw_dir / "si_sample.env").read_text(encoding="utf-8")
        ),
        "quantus": import_template(
            "quantus", (raw_dir / "quantus_sample.cmd").read_text(encoding="utf-8")
        ),
        "jivaro": import_template(
            "jivaro", (raw_dir / "jivaro_sample.xml").read_text(encoding="utf-8")
        ),
    }


def test_aggregate_tech_name_from_quantus(raw_dir: Path) -> None:
    from auto_ext.core.importer import aggregate_pdk_tokens

    constants = aggregate_pdk_tokens(_all_four_results(raw_dir))
    assert constants.tech_name == "HN001"


def test_aggregate_calibre_lvs_dir_extracted_from_rules_file(raw_dir: Path) -> None:
    """Phase 5.6.5: ``paths.calibre_lvs_dir`` = dirname of the calibre raw's
    ``*lvsRulesFile`` value. Whole-path capture replaces the per-segment
    pdk_subdir / runset_version extraction."""
    from auto_ext.core.importer import aggregate_pdk_tokens

    constants = aggregate_pdk_tokens(_all_four_results(raw_dir))
    assert (
        constants.paths["calibre_lvs_dir"]
        == "$VERIFY_ROOT/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/CFXXX"
    )


def test_aggregate_qrc_deck_dir_cross_checked(raw_dir: Path) -> None:
    """``paths.qrc_deck_dir`` is the dirname of calibre's
    ``-query_input <X>/query_cmd`` and quantus's
    ``-parasitic_blocking_device_cells_file "<X>/preserveCellList.txt"``;
    if both are present they must agree."""
    from auto_ext.core.importer import aggregate_pdk_tokens

    constants = aggregate_pdk_tokens(_all_four_results(raw_dir))
    assert (
        constants.paths["qrc_deck_dir"]
        == "$VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_Plus_1.0a/CFXXX/QCI_deck"
    )


def test_aggregate_qrc_deck_dir_conflict_unclassifies() -> None:
    """When calibre and quantus disagree on the QRC deck dir, neither
    promotes — both values land in unclassified for human review."""
    from auto_ext.core.importer import (
        Identity,
        ImportResult,
        aggregate_pdk_tokens,
    )

    calibre = ImportResult(
        tool="calibre",
        identity=Identity(),
        template_body=(
            "*lvsRulesFile: /r/LVS/x/CF/CF.wodio.qcilvs\n"
            "*lvsPostTriggers: {{calibre -query_input /qrc/A/QCI_deck/query_cmd } process 1}\n"
        ),
    )
    quantus = ImportResult(
        tool="quantus",
        identity=Identity(),
        template_body=(
            '-parasitic_blocking_device_cells_file "/qrc/B/QCI_deck/preserveCellList.txt"\n'
        ),
    )
    constants = aggregate_pdk_tokens({"calibre": calibre, "quantus": quantus})
    assert "qrc_deck_dir" not in constants.paths
    values = {u.token.value for u in constants.unclassified}
    assert "/qrc/A/QCI_deck" in values
    assert "/qrc/B/QCI_deck" in values


def test_aggregate_qrc_deck_dir_quantus_only() -> None:
    """If only quantus has a deck-dir-bearing line (no calibre query_cmd),
    the quantus value still promotes."""
    from auto_ext.core.importer import (
        Identity,
        ImportResult,
        aggregate_pdk_tokens,
    )

    quantus = ImportResult(
        tool="quantus",
        identity=Identity(),
        template_body=(
            '-parasitic_blocking_device_cells_file "/q/QCI_deck/preserveCellList.txt"\n'
        ),
    )
    constants = aggregate_pdk_tokens({"quantus": quantus})
    assert constants.paths.get("qrc_deck_dir") == "/q/QCI_deck"


def test_aggregate_abs_path_always_unclassified() -> None:
    # abs_path tokens are never auto-promoted — user must decide.
    from auto_ext.core.importer import (
        Identity,
        ImportResult,
        PdkToken,
        aggregate_pdk_tokens,
    )

    quantus = ImportResult(
        tool="quantus",
        identity=Identity(),
        template_body="",
        pdk_tokens=[
            PdkToken(
                value="/tmpdata/RFIC/rfic_share/bob/",
                category="abs_path",
                line=7,
            )
        ],
    )
    constants = aggregate_pdk_tokens({"quantus": quantus})
    values = {u.token.value for u in constants.unclassified}
    assert "/tmpdata/RFIC/rfic_share/bob/" in values


def test_aggregate_tech_name_non_quantus_source_unclassified() -> None:
    # HN... on calibre (unusual) is suspicious — do not promote, surface
    # in unclassified for user review.
    from auto_ext.core.importer import (
        Identity,
        ImportResult,
        PdkToken,
        aggregate_pdk_tokens,
    )

    calibre = ImportResult(
        tool="calibre",
        identity=Identity(),
        template_body="",
        pdk_tokens=[PdkToken(value="HN999", category="tech_name", line=1)],
    )
    constants = aggregate_pdk_tokens({"calibre": calibre})
    assert constants.tech_name is None
    assert any(u.token.value == "HN999" for u in constants.unclassified)


# ---- apply_project_constants (Phase 5.6.5 body rewrite) -------------------


def test_apply_constants_substitutes_calibre_paths_and_basename() -> None:
    """Calibre body gets calibre_lvs_dir + qrc_deck_dir substituted, plus
    the rules-file basename rewritten so the imported template matches
    the production calibre template's [[calibre_lvs_basename]] knob."""
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = (
        "*lvsRulesFile: /r/LVS/Ver_Plus_1.0l_0.9/CFXXX/CFXXX.wodio.qcilvs\n"
        "*lvsPostTriggers: {{calibre -query_input /q/QCI_deck/query_cmd -query svdb } process 1}\n"
    )
    constants = ProjectConstants(
        tech_name="HN001",
        paths={
            "calibre_lvs_dir": "/r/LVS/Ver_Plus_1.0l_0.9/CFXXX",
            "qrc_deck_dir": "/q/QCI_deck",
        },
    )
    out = apply_project_constants("calibre", body, constants)
    assert "[[calibre_lvs_dir]]" in out
    assert "[[calibre_lvs_basename]].wodio.qcilvs" in out
    assert "[[qrc_deck_dir]]" in out
    # Raw values gone.
    assert "/r/LVS/Ver_Plus_1.0l_0.9/CFXXX" not in out
    assert "/q/QCI_deck" not in out


def test_apply_constants_quantus_uses_qrc_deck_dir() -> None:
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = (
        '-technology_name "HN001"\n'
        '-parasitic_blocking_device_cells_file "/q/QCI_deck/preserveCellList.txt"\n'
    )
    constants = ProjectConstants(
        tech_name="HN001",
        paths={"qrc_deck_dir": "/q/QCI_deck"},
    )
    out = apply_project_constants("quantus", body, constants)
    assert '-technology_name "[[tech_name]]"' in out
    assert '"[[qrc_deck_dir]]/preserveCellList.txt"' in out


def test_apply_constants_no_substring_overshoot() -> None:
    """A path value that happens to be a prefix of another identifier
    must not match it — boundary anchors guard against that."""
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = "path /q/dir and /q/dir_extra both\n"
    constants = ProjectConstants(paths={"qrc_deck_dir": "/q/dir"})
    out = apply_project_constants("quantus", body, constants)
    assert "/q/dir_extra" in out
    assert "[[qrc_deck_dir]] and" in out


def test_apply_constants_none_fields_are_no_op() -> None:
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = "some random content\n"
    out = apply_project_constants("calibre", body, ProjectConstants())
    assert out == body


def test_apply_constants_si_body_untouched_by_paths() -> None:
    """paths.calibre_lvs_dir / qrc_deck_dir don't apply to si templates."""
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = "incFILE = '/r/LVS/Ver_Plus_1.0l_0.9/CFXXX/empty.cdl'\n"
    constants = ProjectConstants(
        paths={"calibre_lvs_dir": "/r/LVS/Ver_Plus_1.0l_0.9/CFXXX"}
    )
    out = apply_project_constants("si", body, constants)
    assert out == body


# ---- auto-knobs (calibre connect_by_name + lvs_variant) -------------------


def test_calibre_connect_by_name_off_variant_injects_block() -> None:
    """Raw without ``*cmnVConnectNamesState`` but with ``*cmnShowOptions:``
    gets the wrapped block injected after the ShowOptions anchor and a
    knob defaulting to False (matches the ON-less raw byte-for-byte
    when rendered with default=False)."""
    raw = (
        "*cmnShowOptions: 1\n"
        "*cmnSpecifyLicenseWaitTime: 1\n"
    )
    result = import_template("calibre", raw)
    body = result.template_body
    assert "[% if connect_by_name %]*cmnVConnectNamesState: ALL\n[% endif %]" in body
    assert "connect_by_name" in result.auto_knobs
    assert result.auto_knobs["connect_by_name"].type == "bool"
    assert result.auto_knobs["connect_by_name"].default is False


def test_calibre_connect_by_name_on_variant_wraps_existing_line() -> None:
    """Raw with the ON line gets that line wrapped in place; the knob
    defaults to True so the rendered body matches the ON raw exactly."""
    raw = (
        "*cmnShowOptions: 1\n"
        "*cmnVConnectNamesState: ALL\n"
        "*cmnSpecifyLicenseWaitTime: 1\n"
    )
    result = import_template("calibre", raw)
    body = result.template_body
    assert "[% if connect_by_name %]*cmnVConnectNamesState: ALL\n[% endif %]" in body
    # Original unwrapped line shouldn't survive.
    assert "\n*cmnVConnectNamesState: ALL\n" not in body
    assert result.auto_knobs["connect_by_name"].default is True


def test_calibre_lvs_variant_wodio_substituted() -> None:
    raw = "*lvsRulesFile: /pdk/runset/foo/CFXXX.wodio.qcilvs\n"
    result = import_template("calibre", raw)
    assert "[[lvs_variant]]" in result.template_body
    assert ".wodio.qcilvs" not in result.template_body
    spec = result.auto_knobs["lvs_variant"]
    assert spec.type == "str"
    assert spec.default == "wodio"
    assert spec.choices == ["wodio", "widio"]


def test_calibre_lvs_variant_widio_substituted() -> None:
    raw = "*lvsRulesFile: /pdk/runset/foo/CFXXX.widio.qcilvs\n"
    result = import_template("calibre", raw)
    assert "[[lvs_variant]]" in result.template_body
    assert result.auto_knobs["lvs_variant"].default == "widio"


def test_calibre_lvs_variant_unrecognized_skipped() -> None:
    """A custom suffix (neither wodio nor widio) gets no knob and the
    body is left untouched — we never invent an enum value."""
    raw = "*lvsRulesFile: /pdk/runset/foo/CFXXX.custom.qcilvs\n"
    result = import_template("calibre", raw)
    assert "[[lvs_variant]]" not in result.template_body
    assert "lvs_variant" not in result.auto_knobs


def test_calibre_no_anchor_no_connect_by_name_knob() -> None:
    """A raw with no ``*cmnShowOptions:`` and no
    ``*cmnVConnectNamesState:`` gets neither an injected block nor a
    knob — there's nothing to parameterize."""
    raw = "*lvsLayoutPrimary: SOMECELL\n"
    result = import_template("calibre", raw)
    assert "connect_by_name" not in result.auto_knobs


def test_non_calibre_tools_have_empty_auto_knobs() -> None:
    """Only the calibre importer auto-parameterizes knobs at present."""
    cases = [
        ("si", 'simLibName = "L"\nsimCellName = "C"\n'),
        ("quantus", '              -ground_net "vss"\n'),
        ("jivaro", '<inputView value="L/C/av"/>\n'),
    ]
    for tool, raw in cases:
        result = import_template(tool, raw)
        assert result.auto_knobs == {}, f"{tool} produced auto_knobs"


def test_calibre_fixture_seeds_both_auto_knobs(calibre_raw: str) -> None:
    """The bundled fixture has both anchors (`*cmnShowOptions:` for the
    OFF variant and `*lvsRulesFile: ....wodio.qcilvs`), so a fresh
    import of the fixture seeds both auto-knobs."""
    result = import_template("calibre", calibre_raw)
    assert set(result.auto_knobs) == {"connect_by_name", "lvs_variant"}
    assert result.auto_knobs["connect_by_name"].default is False
    assert result.auto_knobs["lvs_variant"].default == "wodio"


def test_merge_cross_tool_source_is_skipped() -> None:
    from auto_ext.core.importer import merge_reimport

    # Knob promoted from quantus tool, but we're re-importing calibre.
    existing = _build_manifest(
        wrong_tool={
            "type": "int",
            "default": 1,
            "source": {"tool": "quantus", "key": "whatever"},
        },
    )
    raw = "*lvsLayoutPrimary: INV1\n*lvsLayoutLibrary: LIB\n*lvsLayoutView: layout\n*lvsSourceView: schematic\n"
    new_result = import_template("calibre", raw)
    outcome = merge_reimport(new_result, existing)

    assert "[[wrong_tool]]" not in outcome.body
    assert any("does not match" in m for m in outcome.messages)
