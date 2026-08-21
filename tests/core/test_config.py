"""Tests for :mod:`auto_ext.core.config`."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_ext.core.config import (
    ProjectConfig,
    TaskConfig,
    apply_project_edits,
    apply_tasks_edits,
    dump_project_yaml,
    dump_tasks_yaml,
    load_project,
    load_tasks,
    load_tasks_with_raw,
)
from auto_ext.core.errors import ConfigError


# ---- load_project ----------------------------------------------------------


def test_load_project_minimal(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    assert isinstance(project, ProjectConfig)
    assert project.work_root == Path("/data/work")
    assert project.verify_root == Path("/data/verify")
    assert project.employee_id == "alice"


def test_load_project_sets_source_path(fixtures_dir: Path) -> None:
    p = fixtures_dir / "project_minimal.yaml"
    project = load_project(p)
    assert project.source_path == p.resolve()


def test_load_project_sets_raw(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    assert project.raw is not None
    assert project.raw["employee_id"] == "alice"


def test_load_project_defaults(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    assert project.env_overrides == {}
    assert project.extraction_output_dir == "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    assert project.intermediate_dir == "${WORK_ROOT2}"


def test_load_project_rejects_unknown_field(fixtures_dir: Path) -> None:
    with pytest.raises(ConfigError, match="bogus_field"):
        load_project(fixtures_dir / "project_bad_extra.yaml")


def test_load_project_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_project(tmp_path / "does_not_exist.yaml")


def test_load_project_empty_yaml_uses_all_defaults(tmp_path: Path) -> None:
    # After making path-roots + employee_id optional, a project.yaml with
    # only the section header is a valid minimal config — env vars from
    # the sourced PDK setup carry the real values.
    p = tmp_path / "minimal.yaml"
    p.write_text("{}\n", encoding="utf-8")
    project = load_project(p)
    assert project.work_root is None
    assert project.verify_root is None
    assert project.setup_root is None
    assert project.employee_id is None
    assert str(project.layer_map) == "${PDK_LAYER_MAP_FILE}"


def test_load_project_wrong_top_level_type(tmp_path: Path) -> None:
    p = tmp_path / "list_at_top.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_project(p)


def test_dump_project_yaml_roundtrips(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    dumped = dump_project_yaml(project)
    # Original comment must survive; ruamel preserves it.
    assert "Minimal valid project.yaml" in dumped
    assert "employee_id: alice" in dumped


# ---- apply_project_edits ---------------------------------------------------


def test_apply_project_edits_scalar_overwrite_preserves_comments(
    fixtures_dir: Path,
) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    apply_project_edits(project.raw, {"employee_id": "bob"})
    dumped = dump_project_yaml(project)
    assert "employee_id: bob" in dumped
    # Original comment must still be intact.
    assert "Minimal valid project.yaml" in dumped


def test_apply_project_edits_new_scalar_key(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    apply_project_edits(project.raw, {"tech_name": "HN042"})
    dumped = dump_project_yaml(project)
    assert "tech_name: HN042" in dumped


def test_apply_project_edits_none_deletes_key(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    assert "employee_id" in project.raw
    apply_project_edits(project.raw, {"employee_id": None})
    assert "employee_id" not in project.raw
    dumped = dump_project_yaml(project)
    assert "employee_id" not in dumped


def test_apply_project_edits_nested_env_override_round_trip(
    fixtures_dir: Path,
) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    apply_project_edits(
        project.raw,
        {"env_overrides.WORK_ROOT": "/tmp/override_root"},
    )
    dumped = dump_project_yaml(project)
    assert "env_overrides:" in dumped
    assert "WORK_ROOT: /tmp/override_root" in dumped
    # Removing the only child should prune the parent mapping.
    apply_project_edits(project.raw, {"env_overrides.WORK_ROOT": None})
    assert "env_overrides" not in project.raw
    dumped2 = dump_project_yaml(project)
    assert "env_overrides" not in dumped2


def test_apply_project_edits_unknown_key_raises(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    with pytest.raises(ConfigError, match="unknown key 'bogus'"):
        apply_project_edits(project.raw, {"bogus": "value"})


def test_apply_project_edits_too_many_segments_raises(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    with pytest.raises(ConfigError, match="too many dotted segments"):
        apply_project_edits(project.raw, {"a.b.c.d": 1})


# ---- dspf_out_path (project + per-task) ------------------------------------


def test_load_project_dspf_out_path_default(fixtures_dir: Path) -> None:
    """Loading a project.yaml without ``dspf_out_path`` falls back to the
    legacy intermediate_dir-based default."""
    project = load_project(fixtures_dir / "project_minimal.yaml")
    assert project.dspf_out_path == "${WORK_ROOT2}/{cell}.dspf"


def test_load_project_dspf_out_path_custom(tmp_path: Path) -> None:
    p = tmp_path / "project.yaml"
    p.write_text(
        "dspf_out_path: \"${output_dir}/{cell}.dspf\"\n",
        encoding="utf-8",
    )
    project = load_project(p)
    assert project.dspf_out_path == "${output_dir}/{cell}.dspf"


def test_load_project_dspf_out_path_rejects_non_string(tmp_path: Path) -> None:
    p = tmp_path / "project.yaml"
    p.write_text("dspf_out_path: 42\n", encoding="utf-8")
    # pydantic v2 coerces ints to str by default in str fields, so the
    # cleaner "type rejection" is via a clearly-wrong shape — a list.
    p.write_text("dspf_out_path:\n  - oops\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_project(p)


def test_apply_project_edits_dspf_out_path_round_trip(fixtures_dir: Path) -> None:
    project = load_project(fixtures_dir / "project_minimal.yaml")
    apply_project_edits(
        project.raw, {"dspf_out_path": "${output_dir}/{cell}.dspf"}
    )
    dumped = dump_project_yaml(project)
    assert "dspf_out_path: ${output_dir}/{cell}.dspf" in dumped
    # Round-trip None to confirm delete works through _EDIT_SCALAR_KEYS.
    apply_project_edits(project.raw, {"dspf_out_path": None})
    dumped2 = dump_project_yaml(project)
    assert "dspf_out_path" not in dumped2


# ---- load_tasks ------------------------------------------------------------


def test_load_tasks_scalar(fixtures_dir: Path) -> None:
    tasks = load_tasks(fixtures_dir / "tasks_scalar.yaml")
    assert len(tasks) == 1
    t = tasks[0]
    assert isinstance(t, TaskConfig)
    assert t.library == "TOP_LIB"
    assert t.cell == "inv"
    assert t.lvs_layout_view == "layout"
    assert t.lvs_source_view == "schematic"
    assert t.spec_index == 0
    assert t.expansion_index == 0


def test_load_tasks_cartesian_expand(fixtures_dir: Path) -> None:
    # 2 libs x 2 cells x 2 layouts x 1 source = 8 tasks.
    tasks = load_tasks(fixtures_dir / "tasks_expand.yaml")
    assert len(tasks) == 8


def test_load_tasks_expansion_order(fixtures_dir: Path) -> None:
    # Order must be: library -> cell -> layout -> source (outer-to-inner).
    # With libs=[a,b], cells=[c1,c2], layouts=[layout,layout_test], source=schematic,
    # the first 4 tasks must all have library=lib_a.
    tasks = load_tasks(fixtures_dir / "tasks_expand.yaml")
    assert [t.library for t in tasks[:4]] == ["lib_a"] * 4
    assert [t.library for t in tasks[4:]] == ["lib_b"] * 4
    # First task_id specifically (deterministic).
    assert tasks[0].task_id == "lib_a__c1__layout__schematic"
    assert tasks[-1].task_id == "lib_b__c2__layout_test__schematic"


def test_load_tasks_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_tasks(tmp_path / "nope.yaml")


def test_load_tasks_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        load_tasks(p)


def test_load_tasks_empty_list_field(fixtures_dir: Path) -> None:
    with pytest.raises(ConfigError, match="empty list"):
        load_tasks(fixtures_dir / "tasks_empty_list.yaml")


def test_load_tasks_accepts_dict_wrapper(tmp_path: Path) -> None:
    p = tmp_path / "wrapped.yaml"
    p.write_text(
        "tasks:\n"
        "  - library: L\n"
        "    cell: c\n"
        "    lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    tasks = load_tasks(p)
    assert len(tasks) == 1
    assert tasks[0].library == "L"


def test_load_tasks_rejects_dict_without_tasks_key(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("not_tasks:\n  - foo\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="'tasks'"):
        load_tasks(p)


def test_load_tasks_rejects_unknown_field(tmp_path: Path) -> None:
    p = tmp_path / "badspec.yaml"
    p.write_text(
        "- library: L\n"
        "  cell: c\n"
        "  lvs_layout_view: layout\n"
        "  bogus: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_tasks(p)


def test_load_tasks_continue_on_lvs_fail_default(fixtures_dir: Path) -> None:
    tasks = load_tasks(fixtures_dir / "tasks_scalar.yaml")
    assert tasks[0].continue_on_lvs_fail is False


# ---- TaskConfig invariants ------------------------------------------------


def test_task_config_is_frozen(fixtures_dir: Path) -> None:
    tasks = load_tasks(fixtures_dir / "tasks_scalar.yaml")
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        tasks[0].library = "changed"  # type: ignore[misc]


def test_project_config_rejects_removed_pdk_subdir_field(tmp_path: Path) -> None:
    """Phase 5.6.5 deletes pdk_subdir/runset_versions/etc; old YAMLs must
    fail loud (no compat shim) so users notice and migrate."""
    p = tmp_path / "project.yaml"
    p.write_text("pdk_subdir: CFXXX\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="pdk_subdir"):
        load_project(p)


def test_project_config_rejects_removed_runset_versions_field(tmp_path: Path) -> None:
    p = tmp_path / "project.yaml"
    p.write_text("runset_versions:\n  lvs: x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="runset_versions"):
        load_project(p)


# ---- load_tasks_with_raw / apply_tasks_edits ------------------------------


def _write_tasks(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "t.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_tasks_with_raw_returns_commented_tree(tmp_path: Path) -> None:
    p = _write_tasks(
        tmp_path,
        "# preamble\n- library: L\n  cell: c\n  lvs_layout_view: lay\n",
    )
    tasks, raw = load_tasks_with_raw(p)
    assert len(tasks) == 1
    # Either a ruamel CommentedSeq (list subclass) or a plain list — both accepted
    assert isinstance(raw, list)


def test_apply_tasks_edits_overwrites_preserves_preamble(tmp_path: Path) -> None:
    p = _write_tasks(
        tmp_path,
        "# top preamble comment\n"
        "# keep this line\n"
        "- library: OLD\n  cell: c\n  lvs_layout_view: lay\n",
    )
    _, raw = load_tasks_with_raw(p)
    apply_tasks_edits(
        raw,
        [{"library": "NEW", "cell": "c2", "lvs_layout_view": "lay"}],
    )
    text = dump_tasks_yaml(raw)
    assert "# top preamble comment" in text
    assert "# keep this line" in text
    assert "library: NEW" in text
    assert "library: OLD" not in text


def test_apply_tasks_edits_appends_new_spec(tmp_path: Path) -> None:
    p = _write_tasks(
        tmp_path,
        "- library: A\n  cell: c\n  lvs_layout_view: lay\n",
    )
    _, raw = load_tasks_with_raw(p)
    apply_tasks_edits(
        raw,
        [
            {"library": "A", "cell": "c", "lvs_layout_view": "lay"},
            {"library": "B", "cell": "c2", "lvs_layout_view": "lay"},
        ],
    )
    text = dump_tasks_yaml(raw)
    assert "library: A" in text
    assert "library: B" in text


def test_apply_tasks_edits_pops_trailing(tmp_path: Path) -> None:
    p = _write_tasks(
        tmp_path,
        "- library: A\n  cell: c\n  lvs_layout_view: lay\n"
        "- library: B\n  cell: c2\n  lvs_layout_view: lay\n",
    )
    _, raw = load_tasks_with_raw(p)
    apply_tasks_edits(
        raw,
        [{"library": "A", "cell": "c", "lvs_layout_view": "lay"}],
    )
    text = dump_tasks_yaml(raw)
    assert "library: A" in text
    assert "library: B" not in text


def test_apply_tasks_edits_empty_specs_rejected(tmp_path: Path) -> None:
    p = _write_tasks(
        tmp_path,
        "- library: A\n  cell: c\n  lvs_layout_view: lay\n",
    )
    _, raw = load_tasks_with_raw(p)
    with pytest.raises(ConfigError, match="empty"):
        apply_tasks_edits(raw, [])


def test_apply_tasks_edits_supports_wrapped_form(tmp_path: Path) -> None:
    """tasks.yaml may wrap the list in a ``tasks:`` mapping; roundtrip must survive."""
    p = _write_tasks(
        tmp_path,
        "tasks:\n  - library: L\n    cell: c\n    lvs_layout_view: lay\n",
    )
    _, raw = load_tasks_with_raw(p)
    apply_tasks_edits(
        raw,
        [{"library": "L2", "cell": "c", "lvs_layout_view": "lay"}],
    )
    text = dump_tasks_yaml(raw)
    assert "tasks:" in text
    assert "library: L2" in text


