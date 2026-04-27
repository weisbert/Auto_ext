"""Tests for :mod:`auto_ext.core.env`."""

from __future__ import annotations

import pytest

from auto_ext.core.env import (
    EnvResolution,
    derive_ancestor_dir_from_env_candidates,
    derive_parent_dir_from_env_candidates,
    discover_required_vars,
    resolve_env,
    substitute_env,
)
from auto_ext.core.errors import EnvResolutionError


# ---- discover_required_vars -------------------------------------------------


def test_discover_bare() -> None:
    assert discover_required_vars(["path=$FOO"]) == {"FOO"}


def test_discover_brace() -> None:
    assert discover_required_vars(["path=${BAR}/bin"]) == {"BAR"}


def test_discover_tcl() -> None:
    assert discover_required_vars(["set x $env(BAZ)"]) == {"BAZ"}


def test_discover_all_forms_mixed() -> None:
    src = "A=$FOO B=${BAR} C=$env(BAZ)"
    assert discover_required_vars([src]) == {"FOO", "BAR", "BAZ"}


def test_discover_escape_double_dollar() -> None:
    assert discover_required_vars(["literal $$FOO"]) == set()


def test_discover_adjacent_identifier_is_single_token() -> None:
    # `$FOO_BAR` must match as one identifier, not `$FOO` + `_BAR`.
    assert discover_required_vars(["$FOO_BAR"]) == {"FOO_BAR"}


def test_discover_brace_delimits_identifier() -> None:
    # `${FOO}_BAR` explicitly cuts FOO off; `_BAR` is literal suffix.
    assert discover_required_vars(["${FOO}_BAR"]) == {"FOO"}


def test_discover_rejects_numeric_positional() -> None:
    assert discover_required_vars(["echo $1 $2"]) == set()


def test_discover_rejects_env_with_space() -> None:
    # Tcl $env(...) requires a bare identifier; spaces inside fail to match.
    assert discover_required_vars(["$env(A B)"]) == set()


def test_discover_multiple_sources_unioned() -> None:
    a = "x=$A y=${B}"
    b = "z=$env(C) w=$A"
    assert discover_required_vars([a, b]) == {"A", "B", "C"}


def test_discover_handles_accepts_iterable() -> None:
    # Iterable, not just list.
    def gen() -> object:
        yield "$X"
        yield "${Y}"

    assert discover_required_vars(gen()) == {"X", "Y"}


# ---- resolve_env ------------------------------------------------------------


def test_resolve_override_wins_over_shell(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("FOO", "from-shell")
    r = resolve_env({"FOO"}, {"FOO": "from-override"})
    assert r.resolved["FOO"] == "from-override"
    assert r.sources["FOO"] == "override"


def test_resolve_shell_fallback(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("FOO", "from-shell")
    r = resolve_env({"FOO"}, {})
    assert r.resolved["FOO"] == "from-shell"
    assert r.sources["FOO"] == "shell"


def test_resolve_missing(clean_env: pytest.MonkeyPatch) -> None:
    r = resolve_env({"UNDEFINED_X"}, {})
    assert r.resolved["UNDEFINED_X"] == ""
    assert r.sources["UNDEFINED_X"] == "missing"
    assert r.missing == ["UNDEFINED_X"]


def test_resolve_is_deterministic(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("FOO", "1")
    r1 = resolve_env({"FOO", "BAR"}, {"BAR": "2"})
    r2 = resolve_env({"FOO", "BAR"}, {"BAR": "2"})
    assert r1 == r2


def test_envresolution_missing_is_sorted(clean_env: pytest.MonkeyPatch) -> None:
    r = resolve_env({"Z_VAR", "A_VAR", "M_VAR"}, {})
    assert r.missing == ["A_VAR", "M_VAR", "Z_VAR"]


def test_envresolution_require_ok(clean_env: pytest.MonkeyPatch) -> None:
    r = resolve_env({"FOO"}, {"FOO": "x"})
    assert r.require() == {"FOO": "x"}


def test_envresolution_require_raises(clean_env: pytest.MonkeyPatch) -> None:
    r = resolve_env({"FOO", "BAR"}, {})
    with pytest.raises(EnvResolutionError, match="BAR.*FOO|FOO.*BAR"):
        r.require()


def test_resolve_override_with_empty_string(clean_env: pytest.MonkeyPatch) -> None:
    # Explicit empty override is valid, not missing.
    r = resolve_env({"FOO"}, {"FOO": ""})
    assert r.resolved["FOO"] == ""
    assert r.sources["FOO"] == "override"
    assert r.missing == []


# ---- substitute_env ---------------------------------------------------------


def test_substitute_bare() -> None:
    assert substitute_env("path=$FOO", {"FOO": "/data"}) == "path=/data"


def test_substitute_brace() -> None:
    assert substitute_env("path=${FOO}/bin", {"FOO": "/usr"}) == "path=/usr/bin"


def test_substitute_tcl() -> None:
    assert substitute_env("set x $env(FOO)", {"FOO": "bar"}) == "set x bar"


def test_substitute_leaves_unknown_as_literal() -> None:
    # UNKNOWN is not in resolved; passes through unchanged.
    assert substitute_env("a=$UNKNOWN b=$FOO", {"FOO": "1"}) == "a=$UNKNOWN b=1"


def test_substitute_escape_double_dollar_becomes_single() -> None:
    # $$FOO is literal $FOO in output, and FOO is NOT expanded even if resolved.
    assert substitute_env("literal $$FOO", {"FOO": "x"}) == "literal $FOO"


def test_substitute_adjacent_identifier_not_split() -> None:
    # FOO is resolved but FOO_BAR is not; $FOO_BAR must stay literal.
    assert substitute_env("$FOO_BAR", {"FOO": "x"}) == "$FOO_BAR"


def test_substitute_brace_splits_identifier() -> None:
    # ${FOO}_BAR inserts FOO value and leaves _BAR as literal suffix.
    assert substitute_env("${FOO}_BAR", {"FOO": "x"}) == "x_BAR"


def test_substitute_empty_text_returns_empty() -> None:
    assert substitute_env("", {"FOO": "x"}) == ""


def test_substitute_all_three_forms_in_one_string() -> None:
    text = "A=$A B=${B} C=$env(C)"
    resolved = {"A": "1", "B": "2", "C": "3"}
    assert substitute_env(text, resolved) == "A=1 B=2 C=3"


def test_substitute_empty_value() -> None:
    assert substitute_env("x=$FOO;", {"FOO": ""}) == "x=;"


def test_envresolution_is_frozen() -> None:
    r = EnvResolution(resolved={"X": "1"}, sources={"X": "shell"})
    with pytest.raises((AttributeError, TypeError)):
        r.resolved = {}  # type: ignore[misc]


# ---- derive_parent_dir_from_env_candidates ---------------------------------


def test_derive_parent_dir_returns_first_set_var() -> None:
    resolved = {
        "PDK_TECH_FILE": "/pdk/HN042/techfile.tf",
        "PDK_LAYER_MAP_FILE": "/pdk/HN001/layers.map",
    }
    assert (
        derive_parent_dir_from_env_candidates(
            ["PDK_TECH_FILE", "PDK_LAYER_MAP_FILE"], resolved
        )
        == "HN042"
    )


def test_derive_parent_dir_skips_empty_values() -> None:
    resolved = {"PDK_TECH_FILE": "", "PDK_LAYER_MAP_FILE": "/pdk/HN001/layers.map"}
    assert (
        derive_parent_dir_from_env_candidates(
            ["PDK_TECH_FILE", "PDK_LAYER_MAP_FILE"], resolved
        )
        == "HN001"
    )


def test_derive_parent_dir_returns_none_when_all_unset() -> None:
    assert (
        derive_parent_dir_from_env_candidates(
            ["PDK_TECH_FILE", "PDK_LAYER_MAP_FILE", "PDK_DISPLAY_FILE"], {}
        )
        is None
    )


def test_derive_parent_dir_returns_none_when_path_has_no_parent() -> None:
    # Bare filename (no parent dir) → no usable tech name.
    resolved = {"PDK_TECH_FILE": "techfile.tf"}
    assert (
        derive_parent_dir_from_env_candidates(["PDK_TECH_FILE"], resolved)
        is None
    )


# ---- derive_ancestor_dir_from_env_candidates -------------------------------


def test_derive_ancestor_dir_depth_1_matches_parent() -> None:
    """depth=1 is just the immediate parent dir name; same as
    derive_parent_dir_from_env_candidates."""
    resolved = {"V": "/a/b/c/file.txt"}
    assert (
        derive_ancestor_dir_from_env_candidates(["V"], resolved, depth=1) == "c"
    )


def test_derive_ancestor_dir_depth_2_extracts_grandparent() -> None:
    """The user's $calibre_source_added_place pattern:
    $VERIFY_ROOT/runset/Calibre_QRC/LVS/<runset>/<pdk_subdir>/empty.cdl
                                        depth=2  depth=1
    """
    resolved = {
        "calibre_source_added_place": (
            "/v/runset/Calibre_QRC/LVS/Ver_Plus_1.0l_0.9/"
            "CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9/empty.cdl"
        )
    }
    pdk = derive_ancestor_dir_from_env_candidates(
        ["calibre_source_added_place"], resolved, depth=1
    )
    runset = derive_ancestor_dir_from_env_candidates(
        ["calibre_source_added_place"], resolved, depth=2
    )
    assert pdk == "CF710_Plus_CalLVS_QCI_CCI_081825_V1d0l_0d9"
    assert runset == "Ver_Plus_1.0l_0.9"


def test_derive_ancestor_dir_falls_through_to_next_candidate() -> None:
    resolved = {"V1": "", "V2": "/a/b/c/file.txt"}
    assert (
        derive_ancestor_dir_from_env_candidates(["V1", "V2"], resolved, depth=2)
        == "b"
    )


def test_derive_ancestor_dir_returns_none_when_too_shallow() -> None:
    """Path doesn't have enough ancestors at the requested depth."""
    resolved = {"V": "file.txt"}  # no ancestors
    assert (
        derive_ancestor_dir_from_env_candidates(["V"], resolved, depth=2) is None
    )


def test_derive_ancestor_dir_rejects_invalid_depth() -> None:
    with pytest.raises(ValueError, match="depth must be >= 1"):
        derive_ancestor_dir_from_env_candidates(["V"], {}, depth=0)
