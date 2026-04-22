"""Tests for :mod:`auto_ext.core.template`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from auto_ext.core.errors import TemplateError
from auto_ext.core.template import (
    PlaceholderInventory,
    render_template,
    scan_placeholders,
)


# ---- render_template -------------------------------------------------------


def test_render_substitutes_env_and_jinja(
    fixtures_dir: Path, sample_overrides: dict[str, str]
) -> None:
    result = render_template(
        fixtures_dir / "templates" / "hello.j2",
        context={"cell": "inv"},
        env=sample_overrides,
    )
    assert "work = /w" in result
    assert "emp = alice" in result
    assert "lib = tsmc180" in result
    # Literal placeholder is NOT a Jinja var; stays as-is.
    assert "cell = __CELL_NAME__" in result
    assert "rendered = inv" in result


def test_render_pure_env_no_jinja(
    fixtures_dir: Path, sample_overrides: dict[str, str]
) -> None:
    result = render_template(
        fixtures_dir / "templates" / "pure_env.j2",
        context={},
        env=sample_overrides,
    )
    assert result.strip() == "/w/output/alice/tsmc180"


def test_render_strict_env_raises_on_unresolved(tmp_path: Path) -> None:
    tpl = tmp_path / "t.j2"
    tpl.write_text("path = $MISSING_VAR\n", encoding="utf-8")

    with pytest.raises(TemplateError, match="unresolved env refs"):
        render_template(tpl, context={}, env={})


def test_render_strict_env_false_leaves_literal(tmp_path: Path) -> None:
    tpl = tmp_path / "t.j2"
    tpl.write_text("path = $MISSING_VAR\n", encoding="utf-8")

    result = render_template(tpl, context={}, env={}, strict_env=False)
    assert "path = $MISSING_VAR" in result


def test_render_jinja_undefined_raises(fixtures_dir: Path) -> None:
    with pytest.raises(TemplateError, match="undefined Jinja variable"):
        render_template(
            fixtures_dir / "templates" / "strict_undef.j2",
            context={},
            env={},
        )


def test_render_missing_file(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="template not found"):
        render_template(tmp_path / "nope.j2", context={}, env={})


def test_render_double_dollar_escape_not_unresolved(tmp_path: Path) -> None:
    # $$FOO should render to literal $FOO with strict_env=True (no raise).
    tpl = tmp_path / "t.j2"
    tpl.write_text("literal $$FOO\n", encoding="utf-8")

    result = render_template(tpl, context={}, env={})
    assert "literal $FOO" in result


def test_render_jinja_syntax_error(tmp_path: Path) -> None:
    tpl = tmp_path / "bad.j2"
    tpl.write_text("{% if %}unterminated", encoding="utf-8")

    with pytest.raises(TemplateError, match="Jinja syntax error"):
        render_template(tpl, context={}, env={})


def test_render_env_runs_before_jinja(tmp_path: Path) -> None:
    # Env substitution happens before Jinja. If env value contains a Jinja
    # expression, it IS re-parsed as Jinja on the second pass.
    tpl = tmp_path / "t.j2"
    tpl.write_text("value = $FOO\n", encoding="utf-8")

    result = render_template(tpl, context={"ignored": "X"}, env={"FOO": "{{ ignored }}"})
    assert "value = X" in result


def test_render_zero_length_template(tmp_path: Path) -> None:
    tpl = tmp_path / "empty.j2"
    tpl.write_text("", encoding="utf-8")
    assert render_template(tpl, context={}, env={}) == ""


# ---- scan_placeholders -----------------------------------------------------


def test_scan_collects_env_vars(fixtures_dir: Path) -> None:
    inv = scan_placeholders(fixtures_dir / "templates" / "hello.j2")
    assert isinstance(inv, PlaceholderInventory)
    assert {"WORK_ROOT", "EMP", "LIB"} <= inv.env_vars


def test_scan_collects_literal_placeholders(fixtures_dir: Path) -> None:
    inv = scan_placeholders(fixtures_dir / "templates" / "hello.j2")
    assert "CELL_NAME" in inv.literal_placeholders


def test_scan_collects_user_defined(tmp_path: Path) -> None:
    tpl = tmp_path / "t.j2"
    tpl.write_text("<param name='user_defined_fmax'>5.0</param>\n", encoding="utf-8")

    inv = scan_placeholders(tpl)
    assert "user_defined_fmax" in inv.user_defined


def test_scan_collects_jinja_variables(fixtures_dir: Path) -> None:
    inv = scan_placeholders(fixtures_dir / "templates" / "hello.j2")
    assert "cell" in inv.jinja_variables


def test_scan_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(TemplateError, match="template not found"):
        scan_placeholders(tmp_path / "missing.j2")


def test_scan_broken_jinja_warns_but_returns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    tpl = tmp_path / "bad.j2"
    tpl.write_text("$FOO {% if %}broken", encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="auto_ext.core.template")
    inv = scan_placeholders(tpl)

    # env var scan still works; Jinja var scan returns empty set.
    assert "FOO" in inv.env_vars
    assert inv.jinja_variables == set()
    assert any("jinja parse failed" in m.lower() for m in caplog.messages)
