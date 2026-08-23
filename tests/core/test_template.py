"""Tests for :mod:`auto_ext.core.template`."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from auto_ext.core.errors import TemplateError
from auto_ext.core.template import (
    PlaceholderInventory,
    VarReference,
    _make_jinja_env,
    _referenced_jinja_vars,
    collect_var_references,
    enumerate_stage_templates,
    make_jinja_env,
    referenced_jinja_vars,
    render_template,
    resolve_template_path,
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


def test_render_none_value_for_referenced_var_raises(tmp_path: Path) -> None:
    """A None value for a [[X]] referenced var would silently render
    as the literal string 'None' (StrictUndefined only catches missing
    keys, not present-but-None). Catch + raise with a hint pointing at
    the project.yaml fields users typically forget to set."""
    tpl = tmp_path / "lvs.qci.j2"
    tpl.write_text(
        "*lvsRulesFile: $VERIFY_ROOT/runset/[[lvs_runset_version]]/[[pdk_subdir]]/x.qcilvs\n",
        encoding="utf-8",
    )
    with pytest.raises(TemplateError, match=r"references \['pdk_subdir'\].*is None"):
        render_template(
            tpl,
            context={"lvs_runset_version": "Ver_LVS_A", "pdk_subdir": None},
            env={"VERIFY_ROOT": "/v"},
        )


def test_render_none_value_for_unreferenced_var_does_not_raise(
    tmp_path: Path,
) -> None:
    """None values for vars NOT referenced in the template must not
    trip the guard — projects can leave optional fields unset without
    every template needing to reference them."""
    tpl = tmp_path / "lvs.qci.j2"
    tpl.write_text("*lvsRulesFile: $VERIFY_ROOT/[[pdk_subdir]]/x\n", encoding="utf-8")
    out = render_template(
        tpl,
        context={"pdk_subdir": "CFXXX", "project_subdir": None, "tech_name": None},
        env={"VERIFY_ROOT": "/v"},
    )
    assert "CFXXX" in out
    assert "None" not in out


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
    tpl.write_text("[% if %]unterminated", encoding="utf-8")

    with pytest.raises(TemplateError, match="Jinja syntax error"):
        render_template(tpl, context={}, env={})


def test_render_env_runs_before_jinja(tmp_path: Path) -> None:
    # Env substitution happens before Jinja. If env value contains a Jinja
    # expression, it IS re-parsed as Jinja on the second pass.
    tpl = tmp_path / "t.j2"
    tpl.write_text("value = $FOO\n", encoding="utf-8")

    result = render_template(tpl, context={"ignored": "X"}, env={"FOO": "[[ ignored ]]"})
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
    tpl.write_text("$FOO [% if %]broken", encoding="utf-8")

    caplog.set_level(logging.WARNING, logger="auto_ext.core.template")
    inv = scan_placeholders(tpl)

    # env var scan still works; Jinja var scan returns empty set.
    assert "FOO" in inv.env_vars
    assert inv.jinja_variables == set()
    assert any("jinja parse failed" in m.lower() for m in caplog.messages)


# ---- resolve_template_path -------------------------------------------------


def test_resolve_template_absolute_passthrough(tmp_path: Path) -> None:
    p = tmp_path / "x.j2"
    p.write_text("[[x]]", encoding="utf-8")
    # Absolute path returns as-is regardless of bases.
    assert resolve_template_path(p) == p
    assert resolve_template_path(p, auto_ext_root=tmp_path) == p


def test_resolve_template_cwd_relative_legacy(tmp_path: Path, monkeypatch) -> None:
    # Legacy `Auto_ext_pro/templates/foo.j2` form: caller has cwd =
    # workarea (tmp_path) and the bare relative path resolves via cwd.
    deploy = tmp_path / "Auto_ext_pro"
    (deploy / "templates" / "calibre").mkdir(parents=True)
    target = deploy / "templates" / "calibre" / "foo.j2"
    target.write_text("[[x]]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    rel = Path("Auto_ext_pro/templates/calibre/foo.j2")
    assert resolve_template_path(rel).is_file()


def test_resolve_template_auto_ext_root_fallback(tmp_path: Path) -> None:
    # New auto_ext-root-relative form: no cwd hit, but auto_ext_root / path exists.
    root = tmp_path / "Auto_ext"
    (root / "templates" / "calibre").mkdir(parents=True)
    target = root / "templates" / "calibre" / "foo.j2"
    target.write_text("[[x]]", encoding="utf-8")

    rel = Path("templates/calibre/foo.j2")
    resolved = resolve_template_path(rel, auto_ext_root=root)
    assert resolved == root / rel
    assert resolved.is_file()


def test_resolve_template_workarea_fallback_for_gui(tmp_path: Path) -> None:
    # GUI may run with cwd != workarea. Explicit workarea hint must be
    # tried before auto_ext_root so legacy paths still resolve.
    workarea = tmp_path / "wa"
    deploy = workarea / "Auto_ext_pro"
    (deploy / "templates" / "si").mkdir(parents=True)
    target = deploy / "templates" / "si" / "x.j2"
    target.write_text("[[x]]", encoding="utf-8")

    rel = Path("Auto_ext_pro/templates/si/x.j2")
    resolved = resolve_template_path(rel, workarea=workarea)
    assert resolved == workarea / rel


def test_resolve_template_miss_returns_original(tmp_path: Path) -> None:
    # Nothing matches → original path so callers surface the user's
    # input in the error message rather than a fabricated candidate.
    rel = Path("templates/nope/missing.j2")
    assert resolve_template_path(rel, auto_ext_root=tmp_path) == rel


def test_resolve_template_no_bases_falls_through(tmp_path: Path, monkeypatch) -> None:
    # Without any base hints the helper degrades to cwd-relative behavior.
    (tmp_path / "x.j2").write_text("[[x]]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve_template_path(Path("x.j2")).is_file()


# ---- collect_var_references ------------------------------------------------


def test_collect_var_references_single_var_per_line(tmp_path: Path) -> None:
    tpl = tmp_path / "x.j2"
    tpl.write_text(
        "*lvsRulesFile: [[calibre_lvs_dir]]/foo\n"
        "*lvsRunDir: [[output_dir]]\n",
        encoding="utf-8",
    )
    refs = collect_var_references([tpl])
    assert len(refs) == 2
    assert refs[0] == VarReference(
        var_name="calibre_lvs_dir",
        template_path=tpl,
        line_no=1,
        line_excerpt="*lvsRulesFile: [[calibre_lvs_dir]]/foo",
    )
    assert refs[1].var_name == "output_dir"
    assert refs[1].line_no == 2


def test_collect_var_references_multiple_vars_one_line(tmp_path: Path) -> None:
    tpl = tmp_path / "x.j2"
    tpl.write_text(
        "rules: [[a]]/[[b]]/[[c]]\n",
        encoding="utf-8",
    )
    refs = collect_var_references([tpl])
    assert [r.var_name for r in refs] == ["a", "b", "c"]
    assert all(r.line_no == 1 for r in refs)


def test_collect_var_references_dedupes_same_var_on_one_line(tmp_path: Path) -> None:
    tpl = tmp_path / "x.j2"
    tpl.write_text("[[output_dir]]/foo and [[output_dir]]/bar\n", encoding="utf-8")
    refs = collect_var_references([tpl])
    assert len(refs) == 1
    assert refs[0].var_name == "output_dir"


def test_collect_var_references_truncates_long_excerpt(tmp_path: Path) -> None:
    tpl = tmp_path / "x.j2"
    line = "x" * 200 + " [[var]]\n"
    tpl.write_text(line, encoding="utf-8")
    refs = collect_var_references([tpl], excerpt_max=20)
    assert len(refs) == 1
    assert len(refs[0].line_excerpt) == 20
    assert refs[0].line_excerpt.endswith("…")


def test_collect_var_references_skips_unreadable_paths(tmp_path: Path) -> None:
    real = tmp_path / "ok.j2"
    real.write_text("hello [[a]]\n", encoding="utf-8")
    missing = tmp_path / "missing.j2"
    refs = collect_var_references([real, missing])
    assert len(refs) == 1
    assert refs[0].template_path == real


def test_collect_var_references_no_match_returns_empty(tmp_path: Path) -> None:
    tpl = tmp_path / "x.j2"
    tpl.write_text("plain text, no placeholders\n", encoding="utf-8")
    assert collect_var_references([tpl]) == []


def test_collect_var_references_handles_whitespace_inside_brackets(
    tmp_path: Path,
) -> None:
    tpl = tmp_path / "x.j2"
    tpl.write_text("v=[[ name ]]\n", encoding="utf-8")
    refs = collect_var_references([tpl])
    assert len(refs) == 1
    assert refs[0].var_name == "name"


# ---- enumerate_stage_templates --------------------------------------------


def test_enumerate_stage_templates_returns_j2_files(tmp_path: Path) -> None:
    stage_dir = tmp_path / "templates" / "quantus"
    stage_dir.mkdir(parents=True)
    (stage_dir / "ext.cmd.j2").write_text("a", encoding="utf-8")
    (stage_dir / "dspf.cmd.j2").write_text("b", encoding="utf-8")
    # Anything that is not a ``.j2`` must NOT be returned -- a rendered
    # artefact left next to its template is the common case.
    (stage_dir / "ext.cmd").write_text("rendered\n", encoding="utf-8")
    out = enumerate_stage_templates(tmp_path, "quantus")
    names = [p.name for p in out]
    assert names == ["dspf.cmd.j2", "ext.cmd.j2"]  # sorted


def test_enumerate_stage_templates_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert enumerate_stage_templates(tmp_path, "stage_that_does_not_exist") == []


def test_enumerate_stage_templates_none_root_returns_empty() -> None:
    assert enumerate_stage_templates(None, "quantus") == []


def test_enumerate_stage_templates_skips_subdirs(tmp_path: Path) -> None:
    """Sub-folders under templates/<stage>/ aren't recursed; only direct
    .j2 files at the top level."""
    stage_dir = tmp_path / "templates" / "calibre"
    stage_dir.mkdir(parents=True)
    (stage_dir / "top.j2").write_text("x", encoding="utf-8")
    nested = stage_dir / "subdir"
    nested.mkdir()
    (nested / "nested.j2").write_text("y", encoding="utf-8")
    out = enumerate_stage_templates(tmp_path, "calibre")
    names = [p.name for p in out]
    assert names == ["top.j2"]


# ---- the shared Jinja environment ------------------------------------------
#
# ``make_jinja_env`` and ``referenced_jinja_vars`` became public so the
# catalog-driven pipeline in :mod:`auto_ext.core.render`, which renders from a
# source string rather than a path, uses the same environment and the same
# reference scanner as this module rather than building a second one.


def test_the_private_spellings_are_the_same_objects() -> None:
    """``core/patch.py`` and ``core/diff_template.py`` import the underscore
    names. One environment, two names -- never two environments."""

    assert _make_jinja_env is make_jinja_env
    assert _referenced_jinja_vars is referenced_jinja_vars


def test_jinja_env_uses_this_project_s_delimiters() -> None:
    env = make_jinja_env()
    assert (env.variable_start_string, env.variable_end_string) == ("[[", "]]")
    assert (env.block_start_string, env.block_end_string) == ("[%", "%]")
    assert (env.comment_start_string, env.comment_end_string) == ("[#", "#]")


def test_jinja_env_keeps_trim_blocks_off() -> None:
    """The templates' optional lines are written in the hugging
    ``[% if x %]LINE`` / ``[% endif %]NEXT`` form precisely because
    ``trim_blocks`` is off. Turning it on would silently change every
    conditional line's output."""

    env = make_jinja_env()
    assert env.trim_blocks is False
    assert env.keep_trailing_newline is True
    rendered = env.from_string("[% if on %]A\n[% endif %]B\n").render(on=True)
    assert rendered == "A\nB\n"


def test_jinja_env_is_strict_about_undefined_names() -> None:
    from jinja2 import UndefinedError

    with pytest.raises(UndefinedError):
        make_jinja_env().from_string("[[ nope ]]").render()


def test_referenced_jinja_vars_finds_plain_references() -> None:
    assert referenced_jinja_vars("a [[ cell ]] b [[library]]\n") == {
        "cell",
        "library",
    }


def test_referenced_jinja_vars_ignores_filter_expressions() -> None:
    """Deliberately conservative: an expression with a filter handles its own
    None, so the None guard that consumes this must not flag it."""

    assert referenced_jinja_vars("[[ freq | default(14) ]]") == set()
