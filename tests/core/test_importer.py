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


# ---- quantus ---------------------------------------------------------------


def test_quantus_identity_extraction(quantus_raw: str) -> None:
    result = import_template("quantus", quantus_raw)
    assert result.tool == "quantus"
    assert result.identity.cell == "INV1"
    assert result.identity.library == "INV_LIB"
    assert result.identity.lvs_layout_view == "layout"
    assert result.identity.ground_net == "vss"


def test_quantus_template_body_substitutions(quantus_raw: str) -> None:
    body = import_template("quantus", quantus_raw).template_body
    assert '-ground_net "[[ground_net]]"' in body
    assert '-design_cell_name "[[cell]] [[lvs_layout_view]] [[library]]"' in body
    assert '-directory_name "[[output_dir]]/query_output"' in body
    assert (
        '-layer_map_file "[[output_dir]]/query_output/Design.gds.map"'
        in body
    )
    # Numeric options pass through unmodified (candidate detector picks them up).
    assert "-exclude_floating_nets_limit 5000" in body
    assert "55.0" in body
    # Technology name is a PdkToken, not identity — stays as raw literal.
    assert '-technology_name "HN001"' in body


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
    assert '<outputView value="av_ext_red"/>' in body


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


def test_pdk_tokens_calibre(calibre_raw: str) -> None:
    toks = import_template("calibre", calibre_raw).pdk_tokens
    by_cat = _pdk_values_by_category(toks)
    assert "CFXXX" in by_cat.get("pdk_subdir", set())
    assert "Ver_Plus_1.0l_0.9" in by_cat.get("runset_version", set())
    # No tech_name in calibre sample.
    assert "tech_name" not in by_cat


def test_pdk_tokens_quantus(quantus_raw: str) -> None:
    toks = import_template("quantus", quantus_raw).pdk_tokens
    by_cat = _pdk_values_by_category(toks)
    assert "HN001" in by_cat.get("tech_name", set())
    assert "CFXXX" in by_cat.get("pdk_subdir", set())
    assert "Ver_Plus_1.0a" in by_cat.get("runset_version", set())
    # /tmpdata/RFIC/rfic_share/[[employee_id]]/ is substituted, so
    # abs_path should NOT be reported for that path.
    for t in toks:
        assert "[[employee_id]]" not in t.value


def test_pdk_tokens_si_surfaces_project_subdir(si_raw: str) -> None:
    # si incFILE path: /data/RFIC3/projB/alice/setup/...
    toks = import_template("si", si_raw).pdk_tokens
    by_cat = _pdk_values_by_category(toks)
    # project_subdir extracts just the segment name.
    assert "projB" in by_cat.get("project_subdir", set())


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


def test_aggregate_pdk_subdir_requires_multi_tool_agreement(raw_dir: Path) -> None:
    # CFXXX appears in calibre + si + quantus → promote.
    from auto_ext.core.importer import aggregate_pdk_tokens

    constants = aggregate_pdk_tokens(_all_four_results(raw_dir))
    assert constants.pdk_subdir == "CFXXX"


def test_aggregate_runset_version_split_by_tool_group(raw_dir: Path) -> None:
    from auto_ext.core.importer import aggregate_pdk_tokens

    constants = aggregate_pdk_tokens(_all_four_results(raw_dir))
    # calibre + si carry the LVS runset; they agree on Ver_Plus_1.0l_0.9.
    assert constants.lvs_runset_version == "Ver_Plus_1.0l_0.9"
    # quantus carries the QRC runset (single tool, single-source OK).
    assert constants.qrc_runset_version == "Ver_Plus_1.0a"


def test_aggregate_project_subdir_single_tool_promotes(raw_dir: Path) -> None:
    # projB only appears in si's /data/RFIC3/<project>/ path; the relaxed
    # project_subdir rule promotes any value that all tools carrying the
    # category agree on (single-source OK).
    from auto_ext.core.importer import aggregate_pdk_tokens

    constants = aggregate_pdk_tokens(_all_four_results(raw_dir))
    assert constants.project_subdir == "projB"
    # No unclassified entries for projB (it was promoted).
    assert not any(u.token.value == "projB" for u in constants.unclassified)


def test_aggregate_project_subdir_conflict_unclassifies() -> None:
    # si says projA, some other tool (calibre) says projB — cross-tool
    # conflict triggers the unclassify-all fallback.
    from auto_ext.core.importer import (
        Identity,
        ImportResult,
        PdkToken,
        aggregate_pdk_tokens,
    )

    si = ImportResult(
        tool="si",
        identity=Identity(),
        template_body="",
        pdk_tokens=[PdkToken(value="projA", category="project_subdir", line=1)],
    )
    calibre = ImportResult(
        tool="calibre",
        identity=Identity(),
        template_body="",
        pdk_tokens=[PdkToken(value="projB", category="project_subdir", line=1)],
    )
    constants = aggregate_pdk_tokens({"si": si, "calibre": calibre})
    assert constants.project_subdir is None
    conflicted = {u.token.value for u in constants.unclassified}
    assert conflicted == {"projA", "projB"}


def test_aggregate_single_tool_pdk_subdir_is_unclassified() -> None:
    # Only calibre has CFZZZ; without a second tool agreeing, it stays
    # unclassified per the strict ≥2-tool rule.
    from auto_ext.core.importer import (
        ImportResult,
        Identity,
        PdkToken,
        aggregate_pdk_tokens,
    )

    calibre = ImportResult(
        tool="calibre",
        identity=Identity(),
        template_body="",
        pdk_tokens=[PdkToken(value="CFZZZ", category="pdk_subdir", line=1)],
    )
    constants = aggregate_pdk_tokens({"calibre": calibre})
    assert constants.pdk_subdir is None
    assert any(u.token.value == "CFZZZ" for u in constants.unclassified)


def test_aggregate_runset_conflict_unclassifies_all() -> None:
    # calibre + si disagree on LVS version → both tokens unclassify, none
    # gets promoted. User must resolve.
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
        pdk_tokens=[PdkToken(value="Ver_Plus_1.0a", category="runset_version", line=1)],
    )
    si = ImportResult(
        tool="si",
        identity=Identity(),
        template_body="",
        pdk_tokens=[PdkToken(value="Ver_Plus_2.0b", category="runset_version", line=1)],
    )
    constants = aggregate_pdk_tokens({"calibre": calibre, "si": si})
    assert constants.lvs_runset_version is None
    conflicted = {u.token.value for u in constants.unclassified}
    assert conflicted == {"Ver_Plus_1.0a", "Ver_Plus_2.0b"}


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


# ---- Phase 4b2: apply_project_constants (body rewrite) --------------------


def test_apply_constants_substitutes_all_fields_in_calibre() -> None:
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = (
        "*lvsRulesFile: /r/LVS/Ver_Plus_1.0l_0.9/CFXXX/x.qcilvs\n"
        "*cmnTemplate_RN: [[output_dir]]\n"
    )
    constants = ProjectConstants(
        tech_name="HN001",
        pdk_subdir="CFXXX",
        lvs_runset_version="Ver_Plus_1.0l_0.9",
        qrc_runset_version="Ver_Plus_1.0a",
    )
    out = apply_project_constants("calibre", body, constants)
    assert "[[pdk_subdir]]" in out
    assert "[[lvs_runset_version]]" in out
    # Raw values no longer present (replaced).
    assert "CFXXX" not in out
    assert "Ver_Plus_1.0l_0.9" not in out
    # Quantus-only runset untouched in a calibre body.
    assert "Ver_Plus_1.0a" not in out
    # tech_name absent in calibre body → no change.
    # Identity placeholder untouched.
    assert "[[output_dir]]" in out


def test_apply_constants_quantus_uses_qrc_runset() -> None:
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = '-technology_name "HN001"\n-parasitic_blocking "/r/QRC/Ver_Plus_1.0a/CFXXX/x"\n'
    constants = ProjectConstants(
        tech_name="HN001",
        pdk_subdir="CFXXX",
        lvs_runset_version="Ver_Plus_1.0l_0.9",
        qrc_runset_version="Ver_Plus_1.0a",
    )
    out = apply_project_constants("quantus", body, constants)
    assert '-technology_name "[[tech_name]]"' in out
    assert "[[qrc_runset_version]]" in out
    assert "[[pdk_subdir]]" in out
    # lvs version never appears in quantus body; unchanged either way.
    assert "[[lvs_runset_version]]" not in out


def test_apply_constants_no_substring_overshoot() -> None:
    # pdk_subdir=projB must not match inside a hypothetical projBar identifier.
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = "path /data/RFIC3/projB/x\nother /data/RFIC3/projBar/y\n"
    constants = ProjectConstants(project_subdir="projB")
    out = apply_project_constants("si", body, constants)
    assert "/data/RFIC3/[[project_subdir]]/x" in out
    # projBar untouched because B is followed by [A-Za-z0-9].
    assert "/data/RFIC3/projBar/y" in out


def test_apply_constants_none_fields_are_no_op() -> None:
    from auto_ext.core.importer import ProjectConstants, apply_project_constants

    body = "some random content\n"
    out = apply_project_constants("calibre", body, ProjectConstants())
    assert out == body


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
