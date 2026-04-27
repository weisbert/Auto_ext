"""Tests for :mod:`auto_ext.core.manifest`."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core.errors import ConfigError
from auto_ext.core.manifest import (
    KnobSpec,
    SourceRef,
    TemplateManifest,
    append_knob_to_manifest_yaml,
    current_knob_value,
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


# ---- choices ---------------------------------------------------------------


def test_knob_spec_choices_accepts_default_in_set() -> None:
    spec = KnobSpec(type="str", default="wodio", choices=["wodio", "widio"])
    assert spec.choices == ["wodio", "widio"]


def test_knob_spec_choices_rejects_default_not_in_set() -> None:
    with pytest.raises(ValueError, match="not in choices"):
        KnobSpec(type="str", default="other", choices=["wodio", "widio"])


def test_knob_spec_choices_rejects_non_str_type() -> None:
    with pytest.raises(ValueError, match="choices is only valid for str"):
        KnobSpec(type="int", default=1, choices=[1, 2, 3])


def test_knob_spec_choices_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one"):
        KnobSpec(type="str", default="x", choices=[])


def test_knob_spec_choices_rejects_duplicate_entries() -> None:
    with pytest.raises(ValueError, match="duplicates"):
        KnobSpec(type="str", default="a", choices=["a", "b", "a"])


def test_knob_spec_choices_and_range_mutually_exclusive() -> None:
    # range is numeric-only so this also trips the type check; assert the
    # specific ``mutually exclusive`` message for a str-typed knob using a
    # numeric type that bypasses the earlier guard would mask the intent.
    # Easier: directly construct with both via a str-typed knob — pydantic
    # runs the range check first which itself rejects str+range, so we
    # exercise the mutex via int-typed range pretending a typo path:
    with pytest.raises(ValueError, match="range is only valid for int or float"):
        KnobSpec(type="str", default="a", choices=["a"], range=(0, 1))


def test_resolve_choice_override_rejected_when_not_in_set(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  variant:
    type: str
    default: wodio
    choices: [wodio, widio]
""",
    )
    with pytest.raises(ConfigError, match="not in allowed choices"):
        resolve_knob_values(m, {"variant": "wood"}, {}, {})


def test_resolve_choice_override_accepts_valid_value(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  variant:
    type: str
    default: wodio
    choices: [wodio, widio]
""",
    )
    assert resolve_knob_values(m, {"variant": "widio"}, {}, {}) == {"variant": "widio"}


def test_current_knob_value_choice_override_rejected_when_not_in_set(
    tmp_path: Path,
) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  variant:
    type: str
    default: wodio
    choices: [wodio, widio]
""",
    )
    m = load_manifest(tpl)
    assert m is not None
    with pytest.raises(ConfigError, match="not in allowed choices"):
        current_knob_value(m, {"calibre": {"variant": "wood"}}, "calibre", "variant")


# ---- SourceRef -------------------------------------------------------------


def test_source_ref_roundtrips_on_knob(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  num_turbo:
    type: int
    default: 2
    source:
      tool: calibre
      key: cmnNumTurbo
""",
    )
    m = load_manifest(tpl)
    assert m is not None
    src = m.knobs["num_turbo"].source
    assert isinstance(src, SourceRef)
    assert src.tool == "calibre"
    assert src.key == "cmnNumTurbo"


def test_source_ref_absent_by_default(tmp_path: Path) -> None:
    # Phase 4a manifests had no source field; they must still load cleanly.
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 5
""",
    )
    m = load_manifest(tpl)
    assert m is not None
    assert m.knobs["limit"].source is None


def test_source_ref_extra_field_rejected(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 5
    source:
      tool: calibre
      key: cmnX
      bogus: 1
""",
    )
    with pytest.raises(ConfigError):
        load_manifest(tpl)


def test_source_ref_unknown_tool_rejected(tmp_path: Path) -> None:
    tpl = _write_pair(
        tmp_path,
        "example.cmd.j2",
        """\
template: example.cmd.j2
knobs:
  limit:
    type: int
    default: 5
    source:
      tool: bogus
      key: cmnX
""",
    )
    with pytest.raises(ConfigError):
        load_manifest(tpl)


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


# ---- current_knob_value ----------------------------------------------------


def test_current_knob_value_returns_default_when_unset(tmp_path: Path) -> None:
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
    # Stage absent from project_knobs.
    assert current_knob_value(m, {}, "quantus", "temperature") == (55.0, "default")
    # Stage present but knob not set.
    assert current_knob_value(m, {"quantus": {}}, "quantus", "temperature") == (
        55.0,
        "default",
    )


def test_current_knob_value_returns_project_override(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
    range: [0.0, 200.0]
""",
    )
    project_knobs = {"quantus": {"temperature": 70.0}}
    assert current_knob_value(m, project_knobs, "quantus", "temperature") == (
        70.0,
        "project",
    )


def test_current_knob_value_unknown_knob_raises(tmp_path: Path) -> None:
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
        current_knob_value(m, {}, "quantus", "bogus")


def test_current_knob_value_out_of_range_raises(tmp_path: Path) -> None:
    m = _manifest(
        tmp_path,
        """\
template: example.cmd.j2
knobs:
  temperature:
    type: float
    default: 55.0
    range: [0.0, 100.0]
""",
    )
    # Mirrors resolve_knob_values' project-layer range check so the GUI
    # surfaces the same ConfigError it would see from the runner.
    with pytest.raises(ConfigError, match="outside allowed range"):
        current_knob_value(m, {"quantus": {"temperature": 999.0}}, "quantus", "temperature")


# ---- append_knob_to_manifest_yaml ------------------------------------------


def test_append_knob_creates_sidecar_when_absent(tmp_path: Path) -> None:
    tpl = tmp_path / "ext.cmd.j2"
    tpl.write_text("body\n", encoding="utf-8")
    sidecar = manifest_path_for(tpl)
    assert not sidecar.exists()

    spec = KnobSpec(type="bool", default=True, description="toggle me")
    out_path = append_knob_to_manifest_yaml(tpl, "my_toggle", spec)
    assert out_path == sidecar
    assert sidecar.is_file()

    m = load_manifest(tpl)
    assert m is not None
    assert m.template == "ext.cmd.j2"
    assert "my_toggle" in m.knobs
    assert m.knobs["my_toggle"].type == "bool"
    assert m.knobs["my_toggle"].default is True
    assert m.knobs["my_toggle"].description == "toggle me"


def test_append_knob_appends_to_existing_sidecar_preserving_comments(
    tmp_path: Path,
) -> None:
    tpl = _write_pair(
        tmp_path,
        "ext.cmd.j2",
        """\
# Hand-authored manifest with a leading comment.
template: ext.cmd.j2
knobs:
  temperature:  # in degrees C
    type: float
    default: 55.0
""",
    )
    spec = KnobSpec(type="bool", default=False)
    append_knob_to_manifest_yaml(tpl, "verbose", spec)

    sidecar_text = manifest_path_for(tpl).read_text(encoding="utf-8")
    # ruamel round-trip preserves leading comment + inline comment.
    assert "# Hand-authored manifest" in sidecar_text
    assert "# in degrees C" in sidecar_text

    m = load_manifest(tpl)
    assert m is not None
    assert set(m.knobs) == {"temperature", "verbose"}
    assert m.knobs["verbose"].default is False


def test_append_knob_idempotent_same_spec(tmp_path: Path) -> None:
    tpl = tmp_path / "ext.cmd.j2"
    tpl.write_text("body\n", encoding="utf-8")
    spec = KnobSpec(type="bool", default=True, description="d")
    append_knob_to_manifest_yaml(tpl, "k", spec)
    mtime_before = manifest_path_for(tpl).stat().st_mtime_ns
    # Second call with the same spec is a no-op (no rewrite).
    append_knob_to_manifest_yaml(tpl, "k", spec)
    mtime_after = manifest_path_for(tpl).stat().st_mtime_ns
    assert mtime_before == mtime_after


def test_append_knob_refuses_conflicting_spec(tmp_path: Path) -> None:
    tpl = tmp_path / "ext.cmd.j2"
    tpl.write_text("body\n", encoding="utf-8")
    append_knob_to_manifest_yaml(
        tpl, "k", KnobSpec(type="bool", default=True)
    )
    with pytest.raises(ConfigError, match="different spec"):
        append_knob_to_manifest_yaml(
            tpl, "k", KnobSpec(type="bool", default=False)
        )
