"""Tests for :mod:`auto_ext.ui.templates_view` (Qt-free helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.config import ProjectConfig, TemplatePaths
from auto_ext.core.env import EnvResolution
from auto_ext.core.manifest import KnobSpec, TemplateManifest
from auto_ext.ui.templates_view import (
    TemplateEntry,
    collect_template_entries,
    env_var_status,
    jinja_variable_status,
    literal_placeholder_status,
    user_defined_status,
)


# ---- collect_template_entries ---------------------------------------------


def _project(templates: dict[str, str | None]) -> ProjectConfig:
    paths = {k: (Path(v) if v else None) for k, v in templates.items()}
    return ProjectConfig(templates=TemplatePaths(**paths))


def test_collect_entries_no_project_no_root_returns_empty() -> None:
    assert collect_template_entries(None, None) == []


def test_collect_entries_lists_bound_in_tool_order(tmp_path: Path) -> None:
    # Bound paths don't need to exist for the listing — they're shown as-is.
    project = _project(
        {
            "calibre": "templates/calibre/foo.qci.j2",
            "quantus": "templates/quantus/ext.cmd.j2",
            "si": "templates/si/default.env.j2",
            "jivaro": None,
        }
    )
    entries = collect_template_entries(project, None)
    tools = [e.tool for e in entries]
    # Order is the fixed (si, calibre, quantus, jivaro); jivaro is None so absent.
    assert tools == ["si", "calibre", "quantus"]
    for e in entries:
        assert e.in_project is True


def test_collect_entries_walks_unused_templates_under_root(tmp_path: Path) -> None:
    root = tmp_path / "Auto_ext"
    (root / "templates" / "calibre").mkdir(parents=True)
    (root / "templates" / "calibre" / "bound.j2").write_text("[[x]]", encoding="utf-8")
    (root / "templates" / "calibre" / "spare.j2").write_text("[[y]]", encoding="utf-8")
    (root / "templates" / "quantus").mkdir()
    (root / "templates" / "quantus" / "ext.cmd.j2").write_text("[[z]]", encoding="utf-8")

    project = _project(
        {"calibre": str(root / "templates" / "calibre" / "bound.j2")}
    )
    entries = collect_template_entries(project, root)
    bound = [e for e in entries if e.in_project]
    unused = [e for e in entries if not e.in_project]
    assert len(bound) == 1
    # spare.j2 + ext.cmd.j2 should both surface as unused.
    unused_names = sorted(e.path.name for e in unused)
    assert unused_names == ["ext.cmd.j2", "spare.j2"]


def test_collect_entries_dedup_bound_path_against_walk(tmp_path: Path) -> None:
    root = tmp_path / "Auto_ext"
    (root / "templates" / "si").mkdir(parents=True)
    real = root / "templates" / "si" / "default.env.j2"
    real.write_text("[[cell]]", encoding="utf-8")

    project = _project({"si": str(real)})
    entries = collect_template_entries(project, root)
    # The bound entry should appear once; the rglob walk shouldn't add a duplicate.
    assert sum(1 for e in entries if e.path.name == "default.env.j2") == 1


# ---- placeholder classifiers ----------------------------------------------


def test_env_var_status_maps_resolution_source() -> None:
    res = EnvResolution(
        resolved={"FOO": "/x", "BAR": "/y", "BAZ": ""},
        sources={"FOO": "shell", "BAR": "override", "BAZ": "missing"},
    )
    assert env_var_status("FOO", res) == "ok"
    assert env_var_status("BAR", res) == "override"
    assert env_var_status("BAZ", res) == "missing"
    # Var not in resolution at all → missing (defensive default).
    assert env_var_status("UNKNOWN", res) == "missing"


def test_literal_and_user_defined_are_info_only() -> None:
    assert literal_placeholder_status("CELL_NAME") == "info"
    assert user_defined_status("user_defined_freq") == "info"


def test_jinja_variable_identity_is_ok() -> None:
    # `cell` is in _IDENTITY_KEYS — runner injects it via _build_context.
    assert jinja_variable_status("cell", manifest=None) == "ok"
    assert jinja_variable_status("output_dir", manifest=None) == "ok"


def test_jinja_variable_declared_knob_is_ok() -> None:
    spec = KnobSpec(type="float", default=55.0)
    manifest = TemplateManifest(template="x.j2", knobs={"temperature": spec})
    assert jinja_variable_status("temperature", manifest=manifest) == "ok"


def test_jinja_variable_undeclared_is_missing() -> None:
    assert jinja_variable_status("totally_unknown", manifest=None) == "missing"
    spec = KnobSpec(type="int", default=1)
    manifest = TemplateManifest(template="x.j2", knobs={"limit": spec})
    assert jinja_variable_status("totally_unknown", manifest=manifest) == "missing"


def test_template_entry_is_frozen() -> None:
    entry = TemplateEntry(tool="si", path=Path("a.j2"), in_project=True)
    with pytest.raises(Exception):
        entry.tool = "calibre"  # type: ignore[misc]
