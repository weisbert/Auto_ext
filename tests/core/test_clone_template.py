"""Tests for :mod:`auto_ext.core.clone_template` (Feature #1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.clone_template import (
    CloneTemplateError,
    clone_template,
    delete_template,
    derive_clone_destination,
    validate_suffix,
)


# ---- validate_suffix -------------------------------------------------------


@pytest.mark.parametrize(
    "good",
    ["noconnect", "v2", "with_underscore", "with-dash", "ABC123", "a"],
)
def test_validate_suffix_accepts_legal(good: str) -> None:
    validate_suffix(good)  # no exception


@pytest.mark.parametrize(
    "bad",
    [
        "",  # empty
        " ",  # whitespace
        "foo bar",  # internal whitespace
        "foo.bar",  # dot would form a fake compound ext
        "../escape",  # path traversal
        "foo/bar",  # path separator
        "foo\\bar",  # windows separator
        "foo\n",  # newline
    ],
)
def test_validate_suffix_rejects_bad(bad: str) -> None:
    with pytest.raises(CloneTemplateError):
        validate_suffix(bad)


# ---- derive_clone_destination ---------------------------------------------


@pytest.mark.parametrize(
    "stage,name,suffix,expected",
    [
        (
            "calibre",
            "calibre_lvs.qci.j2",
            "noconnect",
            "calibre_lvs_noconnect.qci.j2",
        ),
        (
            "quantus",
            "ext.cmd.j2",
            "fast",
            "ext_fast.cmd.j2",
        ),
        (
            "quantus",
            "dspf.cmd.j2",
            "v2",
            "dspf_v2.cmd.j2",
        ),
        (
            "jivaro",
            "default.xml.j2",
            "audit",
            "default_audit.xml.j2",
        ),
        (
            "si",
            "default.env.j2",
            "noseed",
            "default_noseed.env.j2",
        ),
        (
            "presets",
            "foo.j2",
            "v2",
            "foo_v2.j2",
        ),
        # Unknown middle ext is treated as part of the stem.
        (
            "calibre",
            "weird.unknown.j2",
            "v2",
            "weird.unknown_v2.j2",
        ),
    ],
)
def test_derive_clone_destination_per_stage(
    tmp_path: Path, stage: str, name: str, suffix: str, expected: str
) -> None:
    src = tmp_path / "templates" / stage / name
    src.parent.mkdir(parents=True)
    src.write_text("body\n", encoding="utf-8")
    dest = derive_clone_destination(src, suffix)
    assert dest.parent == src.parent  # same stage dir
    assert dest.name == expected


def test_derive_clone_destination_rejects_non_j2(tmp_path: Path) -> None:
    src = tmp_path / "calibre_lvs.qci"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(CloneTemplateError):
        derive_clone_destination(src, "noconnect")


def test_derive_clone_destination_rejects_bad_suffix(tmp_path: Path) -> None:
    src = tmp_path / "calibre_lvs.qci.j2"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(CloneTemplateError):
        derive_clone_destination(src, "")
    with pytest.raises(CloneTemplateError):
        derive_clone_destination(src, "../bad")


# ---- clone_template -------------------------------------------------------


def _seed_pair(dir_: Path, name: str, body: str, manifest: str | None) -> Path:
    src = dir_ / name
    src.write_text(body, encoding="utf-8")
    if manifest is not None:
        sidecar = dir_ / (name + ".manifest.yaml")
        sidecar.write_text(manifest, encoding="utf-8")
    return src


def test_clone_copies_j2_and_manifest(tmp_path: Path) -> None:
    stage = tmp_path / "templates" / "calibre"
    stage.mkdir(parents=True)
    src = _seed_pair(
        stage,
        "calibre_lvs.qci.j2",
        "*lvsAbortOnSupplyError: 0\n",
        "template: calibre_lvs.qci.j2\nknobs: {}\n",
    )
    dest = derive_clone_destination(src, "noconnect")
    out_j2, out_manifest = clone_template(src, dest)
    assert out_j2 == dest
    assert out_j2.is_file()
    assert out_j2.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert out_manifest is not None
    assert out_manifest == stage / "calibre_lvs_noconnect.qci.j2.manifest.yaml"
    assert out_manifest.is_file()
    # Manifest is byte-for-byte identical so knob declarations survive.
    assert out_manifest.read_bytes() == (
        stage / "calibre_lvs.qci.j2.manifest.yaml"
    ).read_bytes()


def test_clone_without_manifest_only_copies_j2(tmp_path: Path) -> None:
    stage = tmp_path / "templates" / "presets"
    stage.mkdir(parents=True)
    src = _seed_pair(stage, "foo.j2", "body\n", manifest=None)
    dest = derive_clone_destination(src, "v2")
    out_j2, out_manifest = clone_template(src, dest)
    assert out_j2.is_file()
    assert out_manifest is None
    # No manifest was created spuriously.
    assert not (stage / "foo_v2.j2.manifest.yaml").exists()


def test_clone_refuses_existing_destination(tmp_path: Path) -> None:
    stage = tmp_path / "templates" / "calibre"
    stage.mkdir(parents=True)
    src = _seed_pair(stage, "a.qci.j2", "body\n", "template: a.qci.j2\nknobs: {}\n")
    dest = stage / "a_v2.qci.j2"
    dest.write_text("PRE-EXISTING — DO NOT OVERWRITE\n", encoding="utf-8")
    with pytest.raises(CloneTemplateError):
        clone_template(src, dest)
    # Pre-existing content untouched.
    assert "PRE-EXISTING" in dest.read_text(encoding="utf-8")


def test_clone_rolls_back_j2_if_manifest_dest_exists(tmp_path: Path) -> None:
    stage = tmp_path / "templates" / "calibre"
    stage.mkdir(parents=True)
    src = _seed_pair(
        stage, "a.qci.j2", "body\n", "template: a.qci.j2\nknobs: {}\n"
    )
    dest = stage / "a_v2.qci.j2"
    # Pre-create only the dest manifest. The .j2 dest doesn't exist
    # yet — clone_template should not leave a half-written .j2 behind.
    (stage / "a_v2.qci.j2.manifest.yaml").write_text("blocker\n", encoding="utf-8")
    with pytest.raises(CloneTemplateError):
        clone_template(src, dest)
    assert not dest.exists()


def test_clone_refuses_missing_source(tmp_path: Path) -> None:
    src = tmp_path / "missing.j2"
    dest = tmp_path / "missing_v2.j2"
    with pytest.raises(CloneTemplateError):
        clone_template(src, dest)


def test_clone_creates_parent_dir_if_needed(tmp_path: Path) -> None:
    stage = tmp_path / "templates" / "calibre"
    stage.mkdir(parents=True)
    src = _seed_pair(stage, "x.qci.j2", "body\n", manifest=None)
    # Same dir; just exercising the mkdir branch by removing the dir
    # under a nested workspace.
    dest_dir = tmp_path / "clones"
    dest = dest_dir / "x_v2.qci.j2"
    out_j2, _ = clone_template(src, dest)
    assert out_j2.is_file()
    assert dest_dir.is_dir()


# ---- delete_template ------------------------------------------------------


def test_delete_template_removes_j2_and_manifest(tmp_path: Path) -> None:
    stage = tmp_path / "templates" / "calibre"
    stage.mkdir(parents=True)
    src = _seed_pair(
        stage, "calibre_lvs_v2.qci.j2", "body\n",
        "template: calibre_lvs_v2.qci.j2\nknobs: {}\n",
    )
    sidecar = stage / "calibre_lvs_v2.qci.j2.manifest.yaml"
    assert sidecar.is_file()

    deleted_manifest = delete_template(src)

    assert not src.exists()
    assert not sidecar.exists()
    assert deleted_manifest == sidecar


def test_delete_template_returns_none_when_no_manifest(tmp_path: Path) -> None:
    stage = tmp_path / "templates" / "presets"
    stage.mkdir(parents=True)
    src = _seed_pair(stage, "foo_v2.j2", "body\n", manifest=None)

    deleted_manifest = delete_template(src)

    assert not src.exists()
    assert deleted_manifest is None


def test_delete_template_rejects_non_j2(tmp_path: Path) -> None:
    p = tmp_path / "calibre_lvs.qci"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(CloneTemplateError):
        delete_template(p)
    # File untouched.
    assert p.is_file()


def test_delete_template_rejects_missing_target(tmp_path: Path) -> None:
    with pytest.raises(CloneTemplateError):
        delete_template(tmp_path / "nope.j2")
