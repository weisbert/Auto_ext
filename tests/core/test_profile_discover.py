"""Scanning an environment into a draft PdkProfile.

The contract under test is "discover or say so": every field the scan cannot
establish stays empty and produces a :class:`DiscoveryNote` naming the field,
the rule that failed and the fix. A test that asserts a *guessed* value would
be asserting the bug.

The fake PDK tree mirrors the only real sample we have, ``docs/calibre_raw.txt``
lines 2 and 27, including its two awkward properties: the LVS and QRC decks
carry different runset versions, and the QRC deck sits one level deeper.

Note on paths: ``resolve_path_expr`` applies its ``|parent`` filter with
``PurePosixPath`` (rendered artefacts always target Linux), so every env value
these tests export is written with :meth:`Path.as_posix`. A Windows-style
``C:\\dir\\file`` would have no parent as far as that filter is concerned.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.env import resolve_env
from auto_ext.core.errors import ConfigError
from auto_ext.core.profile_discover import (
    BASE_ENV_CANDIDATES,
    CALIBRE_LVS_DIR_EXPR,
    SCAN_RULES,
    DiscoveryResult,
    discover_profile,
    profile_to_yaml,
    read_profile_yaml,
    write_profile_yaml,
)
from auto_ext.model.pdk import DEFAULT_TECH_NAME_ENV_VARS, PdkProfile

#: Repo root, resolved from this file rather than the cwd: pytest may be
#: invoked from anywhere, and docs/calibre_raw.txt is read below.
REPO_ROOT = Path(__file__).resolve().parents[2]

LVS_VERSION = "Ver_Plus_1.0l_0.9"
QRC_VERSION = "Ver_Plus_1.0a"
PDK_SUBDIR = "CFXXX"

TECH_LIB_SAMPLE = """; synthetic assura_tech.lib -- see SCAN_RULES["R6"], nobody has a real one
techCorner( "TYPICAL" )
techCorner( "RCWORST_CCWORST" )
corner = CBEST
; corner extraction for this technology is described in the PDK manual
"""


@pytest.fixture
def pdk_tree(tmp_path: Path) -> dict[str, Path]:
    """A fake PDK on disk, shaped like the one real sample."""

    verify = tmp_path / "verify"
    lvs_dir = verify / "runset" / "Calibre_QRC" / "LVS" / LVS_VERSION / PDK_SUBDIR
    qrc_dir = verify / "runset" / "Calibre_QRC" / "QRC" / QRC_VERSION / PDK_SUBDIR / "QCI_deck"
    setup = tmp_path / "setup"
    pdk = tmp_path / "pdk" / "HN001"
    for d in (lvs_dir, qrc_dir, setup, pdk):
        d.mkdir(parents=True)

    (lvs_dir / f"{PDK_SUBDIR}.wodio.qcilvs").write_text("; lvs deck\n", encoding="utf-8")
    (lvs_dir / f"{PDK_SUBDIR}.widio.qcilvs").write_text("; lvs deck\n", encoding="utf-8")
    (lvs_dir / "empty.cdl").write_text("", encoding="utf-8")
    (lvs_dir / "README").write_text("not a deck\n", encoding="utf-8")
    (qrc_dir / "query_cmd").write_text("# query\n", encoding="utf-8")
    (qrc_dir / "preserveCellList.txt").write_text("", encoding="utf-8")
    (setup / "assura_tech.lib").write_text(TECH_LIB_SAMPLE, encoding="utf-8")
    (pdk / "layers.map").write_text("; layer map\n", encoding="utf-8")
    (pdk / "tech.db").write_text("", encoding="utf-8")

    return {
        "root": tmp_path,
        "verify": verify,
        "lvs_dir": lvs_dir,
        "qrc_dir": qrc_dir,
        "setup": setup,
        "pdk": pdk,
    }


@pytest.fixture
def pdk_env(pdk_tree: dict[str, Path]) -> dict[str, str]:
    """Env overrides describing ``pdk_tree``, POSIX-style."""

    return {
        "calibre_source_added_place": (pdk_tree["lvs_dir"] / "empty.cdl").as_posix(),
        "VERIFY_ROOT": pdk_tree["verify"].as_posix(),
        "SETUP_ROOT": pdk_tree["setup"].as_posix(),
        "PDK_LAYER_MAP_FILE": (pdk_tree["pdk"] / "layers.map").as_posix(),
        "PDK_TECH_FILE": (pdk_tree["pdk"] / "tech.db").as_posix(),
    }


@pytest.fixture
def empty_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarantee none of the PDK variables leak in from the developer's shell."""

    for var in (*BASE_ENV_CANDIDATES, *DEFAULT_TECH_NAME_ENV_VARS):
        monkeypatch.delenv(var, raising=False)


def _scan(pdk_env: dict[str, str], **kw: object) -> DiscoveryResult:
    kw.setdefault("profile_id", "hn001")
    return discover_profile(env_overrides=pdk_env, **kw)  # type: ignore[arg-type]


# ---- the happy path ----------------------------------------------------------


def test_scan_finds_the_lvs_deck_and_its_variants(pdk_env, pdk_tree):
    profile = _scan(pdk_env).profile

    assert profile.lvs_decks.dir_expr == CALIBRE_LVS_DIR_EXPR
    assert profile.lvs_decks.basename == PDK_SUBDIR
    # Globbed, not assumed: README and empty.cdl are ignored, both decks found.
    assert [v.name for v in profile.lvs_decks.variants] == ["widio", "wodio"]
    assert [v.rules_suffix for v in profile.lvs_decks.variants] == ["widio", "wodio"]
    # Two variants means the scan refuses to pick one for the user.
    assert profile.lvs_decks.default_variant is None


def test_a_single_variant_becomes_the_default(pdk_env, pdk_tree):
    (pdk_tree["lvs_dir"] / f"{PDK_SUBDIR}.widio.qcilvs").unlink()
    result = _scan(pdk_env)
    assert result.profile.lvs_decks.default_variant == "wodio"
    assert result.note_for("lvs_decks.default_variant") is None


def test_scan_finds_the_qrc_deck_one_level_deeper(pdk_env, pdk_tree):
    profile = _scan(pdk_env).profile
    assert profile.qrc.dir_expr == pdk_tree["qrc_dir"].as_posix()
    assert profile.qrc.dir_expr.endswith("/QCI_deck")


def test_the_two_deck_versions_are_recorded_independently(pdk_env):
    # critique B4: they legitimately differ, and a scan that silently unified
    # them would send LVS at the wrong deck.
    profile = _scan(pdk_env).profile
    assert profile.deck_versions == (LVS_VERSION, QRC_VERSION)


def test_tech_name_comes_from_the_env_candidate_parent_dir(pdk_env):
    assert _scan(pdk_env).profile.tech_name == "HN001"


def test_corners_are_parsed_out_of_the_technology_library(pdk_env):
    profile = _scan(pdk_env).profile
    assert [c.name for c in profile.corners] == ["typical", "rcworst_ccworst", "cbest"]
    assert [c.technology_corner for c in profile.corners] == [
        "TYPICAL",
        "RCWORST_CCWORST",
        "CBEST",
    ]
    # The literal the templates hardcode today becomes the default, so a
    # migration keeps rendering the same -technology_corner value.
    assert profile.default_corner == "typical"


def test_prose_containing_the_word_corner_does_not_become_a_corner(pdk_env):
    assert "manual" not in [c.name for c in _scan(pdk_env).profile.corners]


def test_a_full_scan_only_asks_about_what_it_cannot_see(pdk_env):
    # Two things a mounted PDK still cannot answer: which of the two deck
    # variants this group runs, and the global supply lists (they only exist
    # in a Calibre export, not on the deck).
    result = _scan(pdk_env)
    assert [n.field for n in result.notes] == ["lvs_decks.default_variant", "power_names"]


def test_supply_names_come_from_a_real_runset_export(pdk_env, pdk_tree):
    # One deck variant plus a raw export = every field answered, zero notes.
    (pdk_tree["lvs_dir"] / f"{PDK_SUBDIR}.widio.qcilvs").unlink()
    raw = (REPO_ROOT / "docs" / "calibre_raw.txt").read_text(encoding="utf-8")
    result = _scan(pdk_env, raw_calibre_text=raw)
    profile = result.profile
    assert "VDD" in profile.power_names and "AHVDD" in profile.power_names
    assert "VSS" in profile.ground_names and "AGND" in profile.ground_names
    assert len(profile.power_names) == 29 and len(profile.ground_names) == 27
    assert result.complete
    assert result.notes == []


def test_required_env_lists_the_vars_the_profile_actually_depends_on(pdk_env):
    profile = _scan(pdk_env).profile
    assert set(BASE_ENV_CANDIDATES) <= set(profile.required_env)
    assert profile.required_env == sorted(profile.required_env)


def test_scanned_paths_are_recorded_for_provenance(pdk_env, pdk_tree):
    result = _scan(pdk_env)
    assert pdk_tree["lvs_dir"].as_posix() in result.profile.discovered_from
    assert pdk_tree["qrc_dir"].as_posix() in result.profile.discovered_from


# ---- nothing is ever invented ------------------------------------------------


def test_an_empty_environment_yields_an_empty_profile_and_notes(empty_env):
    result = discover_profile(profile_id="hn001")
    p = result.profile

    assert p.lvs_decks.dir_expr is None
    assert p.lvs_decks.variants == []
    assert p.qrc.dir_expr is None
    assert p.corners == [] and p.default_corner is None
    assert p.power_names == [] and p.ground_names == []
    assert p.tech_name is None

    fields = {n.field for n in result.notes}
    assert fields == {
        "tech_name",
        "lvs_decks.dir_expr",
        "qrc.dir_expr",
        "corners",
        "power_names",
    }
    assert not result.complete


def test_every_note_cites_a_documented_rule_and_a_concrete_fix(empty_env):
    for note in discover_profile(profile_id="hn001").notes:
        assert note.rule in SCAN_RULES
        assert note.reason
        # "concrete" means it names a command to run or a field to edit.
        assert "`" in note.fix_hint or ":" in note.fix_hint


def test_a_missing_deck_directory_does_not_invent_variants(pdk_env, pdk_tree):
    for f in pdk_tree["lvs_dir"].glob("*.qcilvs"):
        f.unlink()
    result = _scan(pdk_env)
    assert result.profile.lvs_decks.variants == []
    note = result.note_for("lvs_decks.variants")
    assert note is not None and "ls " in note.fix_hint


def test_an_unparsable_technology_library_leaves_the_corner_table_empty(pdk_env, pdk_tree):
    (pdk_tree["setup"] / "assura_tech.lib").write_text("nothing useful here\n", encoding="utf-8")
    result = _scan(pdk_env)
    assert result.profile.corners == []
    assert result.note_for("corners") is not None


def test_a_missing_technology_library_is_a_note_not_a_crash(pdk_env, pdk_tree):
    (pdk_tree["setup"] / "assura_tech.lib").unlink()
    result = _scan(pdk_env)
    assert result.profile.corners == []
    assert "cannot read" in result.note_for("corners").reason


def test_no_typical_corner_means_no_default_is_chosen(pdk_env, pdk_tree):
    (pdk_tree["setup"] / "assura_tech.lib").write_text(
        'techCorner( "CWORST" )\n', encoding="utf-8"
    )
    result = _scan(pdk_env)
    assert [c.name for c in result.profile.corners] == ["cworst"]
    assert result.profile.default_corner is None
    assert result.note_for("default_corner") is not None


def test_an_ambiguous_qrc_version_is_reported_not_guessed(pdk_env, pdk_tree):
    other = (
        pdk_tree["verify"]
        / "runset"
        / "Calibre_QRC"
        / "QRC"
        / "Ver_Plus_2.0"
        / PDK_SUBDIR
        / "QCI_deck"
    )
    other.mkdir(parents=True)
    result = _scan(pdk_env)
    assert result.profile.qrc.dir_expr is None
    note = result.note_for("qrc.dir_expr")
    assert note is not None and "ambiguous" in note.reason
    assert QRC_VERSION in note.fix_hint and "Ver_Plus_2.0" in note.fix_hint


def test_filesystem_scanning_can_be_switched_off_entirely(pdk_env):
    result = _scan(pdk_env, use_filesystem=False)
    # The env-derived facts survive; everything needing a directory listing
    # becomes a note.
    assert result.profile.lvs_decks.dir_expr == CALIBRE_LVS_DIR_EXPR
    assert result.profile.lvs_decks.variants == []
    assert result.profile.qrc.dir_expr is None
    assert result.profile.corners == []


def test_scan_accepts_an_explicit_resolution(pdk_env):
    resolution = resolve_env(set(pdk_env), pdk_env)
    profile = discover_profile(profile_id="hn001", resolution=resolution).profile
    assert profile.lvs_decks.basename == PDK_SUBDIR


def test_profile_id_is_slugified(pdk_env):
    assert _scan(pdk_env, profile_id="HN 001/x").profile.profile_id == "hn-001-x"


# ---- persistence -------------------------------------------------------------


def test_yaml_round_trip_preserves_the_profile(pdk_env, tmp_path):
    profile = _scan(pdk_env).profile
    path = write_profile_yaml(tmp_path / "profiles" / "hn001.yaml", profile)
    assert path.is_file()
    assert read_profile_yaml(path) == profile


def test_yaml_keeps_pydantic_field_order(pdk_env):
    text = profile_to_yaml(_scan(pdk_env).profile)
    # Alphabetical ordering (ruamel's default for plain dicts) would put
    # corners before display_name; field order is what a human reads.
    assert text.index("profile_id:") < text.index("display_name:") < text.index("corners:")


def test_reading_a_profile_with_an_unknown_key_fails_loudly(tmp_path):
    path = tmp_path / "hn001.yaml"
    path.write_text(
        "profile_id: hn001\ndisplay_name: HN001\ntechname: oops\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="not a valid PDK profile"):
        read_profile_yaml(path)


def test_reading_a_future_schema_version_fails_with_advice(tmp_path):
    path = tmp_path / "hn001.yaml"
    path.write_text(
        "schema_version: 99\nprofile_id: hn001\ndisplay_name: HN001\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="newer than this build"):
        read_profile_yaml(path)


def test_reading_a_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="cannot read profile"):
        read_profile_yaml(tmp_path / "nope.yaml")


def test_reading_a_non_mapping_is_a_config_error(tmp_path):
    path = tmp_path / "hn001.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a mapping"):
        read_profile_yaml(path)


def test_writing_leaves_no_temp_file_behind(pdk_env, tmp_path):
    profiles = tmp_path / "profiles"
    write_profile_yaml(profiles / "hn001.yaml", _scan(pdk_env).profile)
    assert [p.name for p in profiles.iterdir()] == ["hn001.yaml"]


def test_a_hand_written_profile_still_validates(tmp_path):
    path = tmp_path / "hn001.yaml"
    path.write_text(
        "profile_id: hn001\n"
        "display_name: HN001 22nm\n"
        "corners:\n"
        "  - name: typical\n"
        "    technology_corner: TYPICAL\n"
        "default_corner: typical\n",
        encoding="utf-8",
    )
    profile = read_profile_yaml(path)
    assert isinstance(profile, PdkProfile)
    assert profile.corner("typical").technology_corner == "TYPICAL"


# ---- the scan's contract with the PDK it is scanning ------------------------
#
# Section 3.D of the tests disposition. The office machine's PDK tree is
# read-only and shared, the profile is cached on the user's side, and a scan
# that is not repeatable is a scan whose output nobody can review.


def test_scanning_the_same_tree_twice_yields_the_same_profile(pdk_env, pdk_tree):
    """Idempotence, field order included.

    ``profile_to_yaml`` is what a user reviews and what git diffs. If two
    scans of an unchanged tree produced the same *values* in a different
    order -- a set iterated somewhere, a dict built from a directory listing
    -- every re-scan would show as a change and the review would stop
    happening.
    """

    first = _scan(pdk_env)
    second = _scan(pdk_env)

    # ``scanned_at`` is the one field that is *meant* to move: it records when
    # the scan ran, not what it found. Everything else -- and the fingerprint,
    # which is what the health cache keys on -- has to be identical.
    def content(profile):
        return {k: v for k, v in profile.model_dump().items() if k != "scanned_at"}

    assert content(first.profile) == content(second.profile)
    assert first.profile.fingerprint() == second.profile.fingerprint()
    assert [(note.field, note.rule) for note in first.notes] == [
        (note.field, note.rule) for note in second.notes
    ]

    # Field order too: the YAML is what a user reviews and what git diffs.
    def keys(profile):
        return [
            line.split(":", 1)[0]
            for line in profile_to_yaml(profile).splitlines()
            if line and not line.startswith((" ", "-", "#"))
        ]

    assert keys(first.profile) == keys(second.profile)


def test_the_scan_writes_nothing_into_the_pdk_tree(pdk_env, pdk_tree):
    """The PDK mount is shared and read-only; a scan that wrote would fail there.

    Compares the whole tree -- every path, its size and its content -- before
    and after. A cache file, a lock file or a touched mtime would all show up.
    """

    root = pdk_tree["root"]

    def snapshot() -> dict[str, bytes | None]:
        return {
            str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None)
            for path in sorted(root.rglob("*"))
        }

    before = snapshot()
    _scan(pdk_env)
    assert snapshot() == before


def test_the_scan_never_opens_anything_for_writing(pdk_env, monkeypatch):
    """Belt and braces for the read-only mount: no write mode, anywhere.

    The tree comparison above would miss a write that happened to reproduce
    the same bytes, and would miss a write *outside* the tree that a
    read-only-home user would still be refused. This one fails on the attempt
    rather than on the result.
    """

    import builtins

    real_open = builtins.open

    def guarded(file, mode="r", *args, **kwargs):
        if any(flag in str(mode) for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"the scan opened {file!r} for writing (mode {mode!r})")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    result = _scan(pdk_env)
    assert result.profile.profile_id == "hn001"


def test_two_technologies_produce_two_profiles_that_do_not_collide(
    pdk_env, pdk_tree, tmp_path
):
    """Two PDKs on one machine is the normal case, not an edge case.

    Each scan is asked for its own ``profile_id``; the two files sit side by
    side and each reads back as itself. The id is the only thing that keeps
    them apart, so it has to reach both the object and the file name.
    """

    first = _scan(pdk_env, profile_id="hn001", display_name="HN001")
    second = _scan(pdk_env, profile_id="cf028", display_name="CF028")

    profiles_dir = tmp_path / "profiles"
    written = [
        write_profile_yaml(profiles_dir / "hn001.yaml", first.profile),
        write_profile_yaml(profiles_dir / "cf028.yaml", second.profile),
    ]
    assert len({p.name for p in written}) == 2

    back_first = read_profile_yaml(profiles_dir / "hn001.yaml")
    back_second = read_profile_yaml(profiles_dir / "cf028.yaml")
    assert back_first.profile_id == "hn001"
    assert back_second.profile_id == "cf028"
    assert back_first.display_name == "HN001"
    assert back_second.display_name == "CF028"
    # The two files describe the same tree, so everything except identity
    # matches -- which is what makes the id the load-bearing part.
    assert back_first.lvs_decks.model_dump() == back_second.lvs_decks.model_dump()


def test_a_profile_is_not_referenced_by_any_recipe_or_cell_field():
    """"Invisible in normal use" is a schema property, not a UI decision.

    The user picks a PDK once, in ``workspace.yaml``. If ``profile_id`` could
    also appear on a Recipe or a Cells row, a recipe would stop being portable
    and a cell would carry a process fact -- and both would do so silently,
    because either would still validate.
    """

    from auto_ext.model.cells import CellEntry
    from auto_ext.model.recipe import Recipe, recipe_field_paths
    from auto_ext.model.workspace import WorkspaceConfig

    assert "pdk_profile" in WorkspaceConfig.model_fields
    assert "pdk_profile" not in Recipe.model_fields
    assert "pdk_profile" not in CellEntry.model_fields
    assert not [f for f in recipe_field_paths() if "profile" in f or "pdk" in f]
    assert not [f for f in CellEntry.model_fields if "profile" in f or "pdk" in f]
