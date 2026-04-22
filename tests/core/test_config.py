"""Tests for :mod:`auto_ext.core.config`."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_ext.core.config import (
    JivaroConfig,
    ProjectConfig,
    TaskConfig,
    TemplatePaths,
    dump_project_yaml,
    load_project,
    load_tasks,
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
    assert project.templates.calibre is None


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


def test_load_tasks_jivaro_defaults(fixtures_dir: Path) -> None:
    tasks = load_tasks(fixtures_dir / "tasks_scalar.yaml")
    assert tasks[0].jivaro.enabled is False
    assert tasks[0].jivaro.frequency_limit is None


def test_load_tasks_continue_on_lvs_fail_default(fixtures_dir: Path) -> None:
    tasks = load_tasks(fixtures_dir / "tasks_scalar.yaml")
    assert tasks[0].continue_on_lvs_fail is False


# ---- project template merging ---------------------------------------------


def test_load_tasks_merges_project_templates(
    tmp_path: Path, project_config: ProjectConfig
) -> None:
    # Project declares calibre template; task does not -> task inherits it.
    project_config.templates = TemplatePaths(calibre=Path("/proj/calibre.qci.j2"))
    p = tmp_path / "t.yaml"
    p.write_text(
        "- library: L\n  cell: c\n  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    tasks = load_tasks(p, project=project_config)
    assert tasks[0].templates.calibre == Path("/proj/calibre.qci.j2")


def test_load_tasks_per_task_template_overrides_project(
    tmp_path: Path, project_config: ProjectConfig
) -> None:
    project_config.templates = TemplatePaths(calibre=Path("/proj/default.qci.j2"))
    p = tmp_path / "t.yaml"
    p.write_text(
        "- library: L\n"
        "  cell: c\n"
        "  lvs_layout_view: layout\n"
        "  templates:\n"
        "    calibre: /task/override.qci.j2\n",
        encoding="utf-8",
    )
    tasks = load_tasks(p, project=project_config)
    assert tasks[0].templates.calibre == Path("/task/override.qci.j2")


# ---- TaskConfig invariants ------------------------------------------------


def test_task_config_is_frozen(fixtures_dir: Path) -> None:
    tasks = load_tasks(fixtures_dir / "tasks_scalar.yaml")
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        tasks[0].library = "changed"  # type: ignore[misc]


# ---- duplicate task_id warning --------------------------------------------


def test_duplicate_task_id_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Two specs expanding to the same task_id trigger a WARNING but not an error.
    import logging

    p = tmp_path / "dup.yaml"
    p.write_text(
        "- library: L\n  cell: c\n  lvs_layout_view: layout\n"
        "- library: L\n  cell: c\n  lvs_layout_view: layout\n",
        encoding="utf-8",
    )
    caplog.set_level(logging.WARNING, logger="auto_ext.core.config")
    tasks = load_tasks(p)

    assert len(tasks) == 2
    assert any("duplicate task_id" in m.lower() for m in caplog.messages)


# ---- support model checks ------------------------------------------------


def test_template_paths_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        TemplatePaths(calibre=Path("/x"), bogus="y")  # type: ignore[call-arg]


def test_jivaro_config_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        JivaroConfig(enabled=True, bogus=1)  # type: ignore[call-arg]
