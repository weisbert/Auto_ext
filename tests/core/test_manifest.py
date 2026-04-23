"""Tests for :mod:`auto_ext.core.manifest`."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.errors import ConfigError
from auto_ext.core.manifest import (
    KnobSpec,
    TemplateManifest,
    load_manifest,
    manifest_path_for,
    resolve_knob_values,
)


# ---- helpers ---------------------------------------------------------------


def _write_pair(
    tmp_path: Path,
    template_name: str = "example.cmd.j2",
    manifest_body: str | None = None,
) -> Path:
    """Drop an empty ``template`` file + optional sidecar under tmp_path.

    Returns the template path. When ``manifest_body`` is ``None`` no sidecar
    is written (exercises the absent-manifest branch).
    """
    tpl = tmp_path / template_name
    tpl.write_text("[[ignored]]\n", encoding="utf-8")
    if manifest_body is not None:
        sidecar = manifest_path_for(tpl)
        sidecar.write_text(manifest_body, encoding="utf-8")
    return tpl


# ---- load_manifest ---------------------------------------------------------


def test_load_manifest_absent_returns_none(tmp_path: Path) -> None:
    tpl = _write_pair(tmp_path, manifest_body=None)
    assert load_manifest(tpl) is None


def test_load_manifest_valid(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
description: example
knobs:
  temperature:
    type: float
    default: 55.0
    unit: "C"
  limit:
    type: int
    default: 5000
    range: [100, 100000]
""",
    )
    m = load_manifest(tpl)
    assert isinstance(m, TemplateManifest)
    assert m.template == "example.cmd.j2"
    assert m.knobs["temperature"].type == "float"
    assert m.knobs["temperature"].default == 55.0
    assert m.knobs["limit"].default == 5000
    assert m.knobs["limit"].range == (100, 100000)


def test_load_manifest_empty_sidecar_errors(tmp_path: Path) -> None:
    tpl = _write_pair(tmp_path, "example.cmd.j2", "")
    with pytest.raises(ConfigError, match="empty"):
        load_manifest(tpl)


def test_load_manifest_malformed_yaml(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        "template: example.cmd.j2\nknobs:\n  bad: [unclosed\n",
    )
    sidecar = manifest_path_for(tpl)
    with pytest.raises(ConfigError) as exc_info:
        load_manifest(tpl)
    assert str(sidecar) in str(exc_info.value)


def test_load_manifest_template_name_mismatch(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        "template: other.j2\nknobs: {}\n",
    )
    with pytest.raises(ConfigError, match="does not match"):
        load_manifest(tpl)


def test_load_manifest_identity_collision(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  cell:
    type: str
    default: foo
""",
    )
    with pytest.raises(ConfigError, match="identity"):
        load_manifest(tpl)


def test_load_manifest_dotted_knob_rejected(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  foo.bar:
    type: int
    default: 1
""",
    )
    with pytest.raises(ConfigError, match="must not contain"):
        load_manifest(tpl)


def test_load_manifest_default_type_mismatch(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: "hello"
""",
    )
    with pytest.raises(ConfigError, match="expected int"):
        load_manifest(tpl)


def test_load_manifest_default_out_of_range(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 50
    range: [100, 200]
""",
    )
    with pytest.raises(ConfigError, match="outside range"):
        load_manifest(tpl)


def test_load_manifest_range_on_non_numeric_rejected(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  flag:
    type: bool
    default: true
    range: [0, 1]
""",
    )
    with pytest.raises(ConfigError, match="range is only valid"):
        load_manifest(tpl)


def test_load_manifest_unknown_field_rejected(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 5
    bogus: 1
""",
    )
    with pytest.raises(ConfigError):
        load_manifest(tpl)


def test_load_manifest_empty_knobs_ok(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        "template: example.cmd.j2\ndescription: none\nknobs: {}\n",
    )
    m = load_manifest(tpl)
    assert isinstance(m, TemplateManifest)
    assert m.knobs == {}


# ---- KnobSpec ranges -------------------------------------------------------


def test_knob_spec_int_float_promotion() -> None:
    spec = KnobSpec(type="float", default=5)
    assert spec.default == 5.0 and isinstance(spec.default, float)


def test_knob_spec_bool_not_accepted_as_int() -> None:
    with pytest.raises(ValueError):
        KnobSpec(type="int", default=True)


def test_knob_spec_range_low_gt_high_rejected() -> None:
    with pytest.raises(ValueError):
        KnobSpec(type="int", default=5, range=(10, 1))


# ---- resolve_knob_values ---------------------------------------------------


def _manifest(tmp_path: Path, body: str) -> TemplateManifest:
    tpl = _write_pair(tmp_path, "example.cmd.j2", body)
    m = load_manifest(tpl)
    assert m is not None
    return m


def test_resolve_precedence_manifest_lowest(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    result = resolve_knob_values(m, {}, {}, {})
    assert result == {"temperature": 55.0}


def test_resolve_project_beats_manifest(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    result = resolve_knob_values(m, {"temperature": 60.0}, {}, {})
    assert result == {"temperature": 60.0}


def test_resolve_task_beats_project(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    result = resolve_knob_values(
        m, {"temperature": 60.0}, {"temperature": 70.0}, {}
    )
    assert result == {"temperature": 70.0}


def test_resolve_cli_beats_task(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    result = resolve_knob_values(
        m, {"temperature": 60.0}, {"temperature": 70.0}, {"temperature": "80"}
    )
    assert result == {"temperature": 80.0}


def test_resolve_unknown_knob_project_rejected(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    with pytest.raises(ConfigError, match="not declared"):
        resolve_knob_values(m, {"bogus": 1.0}, {}, {})


def test_resolve_unknown_knob_task_rejected(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    with pytest.raises(ConfigError, match="not declared"):
        resolve_knob_values(m, {}, {"bogus": 1.0}, {})


def test_resolve_unknown_knob_cli_rejected(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    with pytest.raises(ConfigError, match="not declared"):
        resolve_knob_values(m, {}, {}, {"bogus": "1"})


def test_resolve_cli_string_coerced_to_float(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
""",
    )
    result = resolve_knob_values(m, {}, {}, {"temperature": "3.14"})
    assert result["temperature"] == 3.14 and isinstance(result["temperature"], float)


def test_resolve_cli_float_string_on_int_knob_rejected(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 5
""",
    )
    with pytest.raises(ConfigError, match="cannot parse"):
        resolve_knob_values(m, {}, {}, {"limit": "3.14"})


def test_resolve_cli_bool_string_true(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  enabled:
    type: bool
    default: false
""",
    )
    assert resolve_knob_values(m, {}, {}, {"enabled": "true"}) == {"enabled": True}
    assert resolve_knob_values(m, {}, {}, {"enabled": "no"}) == {"enabled": False}
    assert resolve_knob_values(m, {}, {}, {"enabled": "1"}) == {"enabled": True}


def test_resolve_range_violation_rejected(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 5000
    range: [100, 100000]
""",
    )
    with pytest.raises(ConfigError, match="outside allowed range"):
        resolve_knob_values(m, {"limit": 50}, {}, {})


def test_resolve_no_manifest_any_override_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="no manifest"):
        resolve_knob_values(None, {"x": 1}, {}, {})
    with pytest.raises(ConfigError, match="no manifest"):
        resolve_knob_values(None, {}, {"x": 1}, {})
    with pytest.raises(ConfigError, match="no manifest"):
        resolve_knob_values(None, {}, {}, {"x": "1"})


def test_resolve_no_manifest_no_overrides_returns_empty() -> None:
    assert resolve_knob_values(None, {}, {}, {}) == {}


def test_resolve_project_type_mismatch_rejected(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 5
""",
    )
    # project layer sends a string where an int is expected — typed path
    # does not coerce strings.
    with pytest.raises(ConfigError, match="expected int"):
        resolve_knob_values(m, {"limit": "5"}, {}, {})
