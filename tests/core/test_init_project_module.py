"""Tests for :mod:`auto_ext.core.init_project` (Phase 5.7).

Pure-Python unit tests for the orchestrator the CLI + GUI wizard share.
The CLI's end-to-end coverage lives in ``tests/test_cli_init_project.py``;
these cases pin individual pieces (cross-validate, dry_run, commit) at
the module level so a wizard-side regression doesn't have to mock Typer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.init_project import (
    InitInputs,
    cross_validate_identities,
    commit,
    dry_run,
)


@pytest.fixture
def raw_projectA_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "raw"


def _inputs(
    raw_dir: Path,
    out_root: Path,
    *,
    include_jivaro: bool = True,
    cell_override: str | None = None,
    library_override: str | None = None,
    force: bool = False,
) -> InitInputs:
    return InitInputs(
        raw_calibre=raw_dir / "calibre_sample.qci",
        raw_si=raw_dir / "si_sample.env",
        raw_quantus=raw_dir / "quantus_sample.cmd",
        raw_jivaro=(raw_dir / "jivaro_sample.xml") if include_jivaro else None,
        output_config_dir=out_root / "config",
        output_templates_dir=out_root / "templates",
        cell_override=cell_override,
        library_override=library_override,
        force=force,
    )


# ---- dry_run --------------------------------------------------------------


def test_dry_run_projectA_produces_full_preview(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    preview = dry_run(_inputs(raw_projectA_dir, tmp_path))
    assert len(preview.files) == 10
    assert all(f.will_overwrite is False for f in preview.files)
    assert preview.merged_identity.cell == "INV1"
    assert preview.merged_identity.library == "INV_LIB"
    assert preview.constants.tech_name == "HN001"
    assert preview.constants.pdk_subdir == "CFXXX"
    assert not preview.conflicts
    # Ordering: 4 (template, manifest) pairs then project.yaml then tasks.yaml.
    roles = [f.role for f in preview.files]
    assert roles[-2] == "project_yaml"
    assert roles[-1] == "tasks_yaml"


def test_dry_run_jivaro_omitted_yields_8_files(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    preview = dry_run(_inputs(raw_projectA_dir, tmp_path, include_jivaro=False))
    assert len(preview.files) == 8
    assert "jivaro" not in preview.results
    paths = [f.path for f in preview.files]
    assert not any("jivaro" in p.parts for p in paths)
    # tasks.yaml carries jivaro.enabled: false.
    assert "enabled: false" in preview.tasks_yaml_text


def test_dry_run_identity_conflict_returns_conflicts_list_not_raises(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    # Mutate si raw so its cell disagrees with calibre/quantus.
    broken = tmp_path / "broken_raw"
    broken.mkdir()
    for name in ("calibre_sample.qci", "quantus_sample.cmd", "jivaro_sample.xml"):
        (broken / name).write_bytes((raw_projectA_dir / name).read_bytes())
    si_raw = (raw_projectA_dir / "si_sample.env").read_text(encoding="utf-8")
    si_raw = si_raw.replace("INV1", "NOT_INV1")
    (broken / "si_sample.env").write_text(si_raw, encoding="utf-8")

    preview = dry_run(_inputs(broken, tmp_path / "out"))
    assert preview.conflicts
    assert any("cell" in c for c in preview.conflicts)
    # Cell is None on conflict (caller decides resolution).
    assert preview.merged_identity.cell is None
    # Non-conflicting fields still merge.
    assert preview.merged_identity.library == "INV_LIB"


def test_dry_run_with_overrides_wins(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    preview = dry_run(
        _inputs(raw_projectA_dir, tmp_path, cell_override="OVERRIDE_CELL")
    )
    assert preview.merged_identity.cell == "OVERRIDE_CELL"


def test_dry_run_unclassified_tokens_surfaced(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    # Calibre normally has no /data/RFIC3/... path; inject a conflicting
    # projA reference so aggregate_pdk_tokens unclassifies projA vs projB.
    conflict = tmp_path / "conflict_raw"
    conflict.mkdir()
    for name in (
        "calibre_sample.qci",
        "quantus_sample.cmd",
        "jivaro_sample.xml",
        "si_sample.env",
    ):
        (conflict / name).write_bytes((raw_projectA_dir / name).read_bytes())
    calibre_raw = (conflict / "calibre_sample.qci").read_text(encoding="utf-8")
    calibre_raw += (
        "*lvsPostTriggers: {{cat /data/RFIC3/projA/bob/x/y} process 1}\n"
    )
    (conflict / "calibre_sample.qci").write_text(calibre_raw, encoding="utf-8")

    preview = dry_run(_inputs(conflict, tmp_path / "out"))
    assert preview.constants.unclassified
    values = {u.token.value for u in preview.constants.unclassified}
    # projA from calibre + projB from si both unclassified.
    assert any("projA" in v for v in values)


def test_dry_run_marks_overwrites_when_targets_exist(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    cfg = out / "config"
    cfg.mkdir(parents=True)
    (cfg / "project.yaml").write_text("# stale\n", encoding="utf-8")
    preview = dry_run(_inputs(raw_projectA_dir, out))
    project_entry = next(
        f for f in preview.files if f.role == "project_yaml"
    )
    assert project_entry.will_overwrite is True
    other = next(f for f in preview.files if f.role == "tasks_yaml")
    assert other.will_overwrite is False


# ---- cross_validate_identities --------------------------------------------


def test_cross_validate_no_results_yields_empty_identity() -> None:
    merged, conflicts = cross_validate_identities({})
    assert merged.cell is None
    assert merged.library is None
    assert conflicts == []


# ---- commit ---------------------------------------------------------------


def test_commit_writes_all_files_in_order(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    preview = dry_run(_inputs(raw_projectA_dir, tmp_path))
    progress: list[str] = []
    written = commit(preview, progress=progress.append)
    assert len(written) == 10
    for f in preview.files:
        assert f.path.is_file()
    # Progress callback fired in lockstep, in the same order.
    assert len(progress) == 10
    assert progress[-2].endswith("project.yaml")
    assert progress[-1].endswith("tasks.yaml")
    # Written list matches preview.files order.
    assert [str(p) for p in written] == [str(f.path) for f in preview.files]


def test_commit_overwrite_makes_bak(
    raw_projectA_dir: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    cfg = out / "config"
    cfg.mkdir(parents=True)
    stale = "# stale-content-marker\n"
    (cfg / "project.yaml").write_text(stale, encoding="utf-8")

    preview = dry_run(_inputs(raw_projectA_dir, out, force=True))
    commit(preview)
    bak = cfg / "project.yaml.bak"
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == stale
    # The new project.yaml is no longer the stale content.
    assert (cfg / "project.yaml").read_text(encoding="utf-8") != stale


def test_commit_oserror_surfaces_unmodified(
    raw_projectA_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No rollback in v1: OSError mid-write surfaces, partial state stays."""
    preview = dry_run(_inputs(raw_projectA_dir, tmp_path))
    # Fail the 6th write.
    real_write = Path.write_text
    calls = {"n": 0}

    def fake_write(self: Path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 6:
            raise OSError("simulated write failure")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fake_write)

    with pytest.raises(OSError, match="simulated write failure"):
        commit(preview)
    # Partial state: 5 files landed, 5 did not.
    landed = [f.path for f in preview.files if f.path.exists()]
    assert len(landed) == 5
