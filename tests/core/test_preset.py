"""Tests for :mod:`auto_ext.core.preset`."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.diff_template import compute_toggle
from auto_ext.core.errors import ConfigError
from auto_ext.core.preset import (
    apply_preset,
    list_presets,
    load_preset,
    save_preset,
)


def _make_toggle(name: str = "k"):
    return compute_toggle("a\nb\nC\nd\ne\n", "a\nb\nX\nd\ne\n", name)


# ---- save_preset -----------------------------------------------------------


def test_save_preset_writes_4_files(tmp_path: Path) -> None:
    toggle = _make_toggle("k")
    out = save_preset(toggle, "k_preset", presets_dir=tmp_path)
    assert out == tmp_path / "k_preset"
    for fname in ("meta.yaml", "on.txt", "off.txt", "snippet.j2"):
        assert (out / fname).is_file()


def test_save_preset_atomic_rename_cleans_stale_tmp(tmp_path: Path) -> None:
    # Simulate a stale .tmp left behind by a previous crashed save.
    stale = tmp_path / "k_preset.tmp"
    stale.mkdir()
    (stale / "junk").write_text("oops", encoding="utf-8")

    toggle = _make_toggle()
    out = save_preset(toggle, "k_preset", presets_dir=tmp_path)
    assert out.is_dir()
    assert not stale.exists()  # tmp dir consumed by rename
    assert not (out / "junk").exists()  # stale junk did not leak in


def test_save_preset_overwrite_false_refuses(tmp_path: Path) -> None:
    toggle = _make_toggle()
    save_preset(toggle, "k", presets_dir=tmp_path)
    with pytest.raises(FileExistsError):
        save_preset(toggle, "k", presets_dir=tmp_path)


@pytest.mark.parametrize("bad", ["UPPER", "with space", "with.dot", "", "with/slash"])
def test_save_preset_invalid_slug_raises(tmp_path: Path, bad: str) -> None:
    toggle = _make_toggle()
    with pytest.raises(ValueError):
        save_preset(toggle, bad, presets_dir=tmp_path)


# ---- load_preset -----------------------------------------------------------


def test_load_preset_round_trip(tmp_path: Path) -> None:
    toggle = _make_toggle("k")
    save_preset(
        toggle, "k", presets_dir=tmp_path,
        description="explanatory", applicable_tool="calibre",
    )
    loaded = load_preset("k", presets_dir=tmp_path)
    assert loaded.slug == "k"
    assert loaded.name == "k"
    assert loaded.description == "explanatory"
    assert loaded.applicable_tool == "calibre"
    assert loaded.on_text == toggle.on_text
    assert loaded.off_text == toggle.off_text
    assert len(loaded.hunks) == len(toggle.hunks)


# ---- list_presets ----------------------------------------------------------


def test_list_presets_skips_malformed(tmp_path: Path, caplog) -> None:
    save_preset(_make_toggle("a"), "a", presets_dir=tmp_path)
    save_preset(_make_toggle("b"), "b", presets_dir=tmp_path)
    # Break b's meta.yaml.
    (tmp_path / "b" / "meta.yaml").write_text(
        "this is: not valid: yaml: at all: [unbalanced\n",
        encoding="utf-8",
    )
    with caplog.at_level("WARNING"):
        result = list_presets(tmp_path)
    assert [p.slug for p in result] == ["a"]
    assert any("b" in rec.message for rec in caplog.records)


# ---- apply_preset ----------------------------------------------------------


def test_apply_preset_to_identical_template_succeeds(tmp_path: Path) -> None:
    toggle = _make_toggle("k")
    save_preset(toggle, "k", presets_dir=tmp_path)
    preset = load_preset("k", presets_dir=tmp_path)
    out, warnings = apply_preset(preset, toggle.on_text)
    assert "[% if k %]" in out
    assert "[% else %]" in out
    assert warnings == []


def test_apply_preset_to_template_with_extra_lines_succeeds(tmp_path: Path) -> None:
    toggle = _make_toggle("k")
    save_preset(toggle, "k", presets_dir=tmp_path)
    preset = load_preset("k", presets_dir=tmp_path)

    # Target template has an extra header line — anchors still match.
    target = "header\n" + toggle.on_text
    out, _ = apply_preset(preset, target)
    assert out.startswith("header\n")
    assert "[% if k %]" in out


def test_apply_preset_anchor_lost_raises(tmp_path: Path) -> None:
    toggle = _make_toggle("k")
    save_preset(toggle, "k", presets_dir=tmp_path)
    preset = load_preset("k", presets_dir=tmp_path)
    # Strip the on-side hunk content from the target — anchor lost.
    bad_target = "a\nb\nQQ\nd\ne\n"
    with pytest.raises(ValueError, match="anchor lost"):
        apply_preset(preset, bad_target)


def test_apply_preset_anchor_ambiguous_raises(tmp_path: Path) -> None:
    toggle = _make_toggle("k")
    save_preset(toggle, "k", presets_dir=tmp_path)
    preset = load_preset("k", presets_dir=tmp_path)
    # Duplicate the anchored on-side block + surrounding context so the
    # anchor matches in two distinct places.
    duped_target = toggle.on_text + toggle.on_text
    with pytest.raises(ValueError, match="ambiguous"):
        apply_preset(preset, duped_target)
