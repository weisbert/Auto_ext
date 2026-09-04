"""Jinja2 template rendering with pre-Jinja env-var substitution, plus
placeholder scanning for the Template Editor (Phase 5 GUI).

Render-time contract (see project memory): all ``$X`` / ``${X}`` /
``$env(X)`` references are substituted to their resolved values BEFORE
the text is handed to Jinja. Rendered output therefore contains no env
references, regardless of the tool's native substitution rules. In
``strict_env=True`` mode (default), any env reference that remains after
substitution raises :class:`TemplateError` — caller's responsibility to
resolve everything ahead of time via :func:`auto_ext.core.env.resolve_env`.

The diff-mode toggle editor (Phase 6) is NOT in this module; only
:func:`render_template` and :func:`scan_placeholders` are.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import (
    BaseLoader,
    Environment,
    StrictUndefined,
    TemplateSyntaxError,
    UndefinedError,
    meta,
)

from auto_ext.core.env import discover_required_vars, substitute_env
from auto_ext.core.errors import TemplateError

logger = logging.getLogger(__name__)


# Literal placeholders found in existing EDA templates:
# - ``__CELL_NAME__`` (si.env, qrc) — uppercase-only inside double underscores
# - ``user_defined_<anything>`` (jivaro xml)
_RE_LITERAL_PLACEHOLDER = re.compile(r"__([A-Z][A-Z0-9_]*)__")
_RE_USER_DEFINED = re.compile(r"\buser_defined_[A-Za-z0-9_]+\b")


@dataclass(frozen=True)
class PlaceholderInventory:
    """Inventory of every placeholder class found in a template source."""

    env_vars: set[str]
    literal_placeholders: set[str]
    user_defined: set[str]
    jinja_variables: set[str]


def make_jinja_env() -> Environment:
    """Build the Jinja environment used by every render and scan path.

    Public because the catalog-driven pipeline in
    :mod:`auto_ext.core.render` renders from a template *source string* rather
    than a path and must use exactly this environment: same delimiters, same
    ``StrictUndefined``, same ``keep_trailing_newline``. A second environment
    built by hand elsewhere is how two renderers of one template start
    disagreeing about whitespace.

    Delimiters are ``[[ ]]`` / ``[% %]`` / ``[# #]`` instead of Jinja's
    defaults. Rationale: Calibre ``.qci`` files use Tcl brace literals such
    as ``*lvsPreTriggers: {{rm -rf %d/svdb} process 1}`` which collide with
    default ``{{ ... }}`` variable syntax and make the template unparseable.
    XML CDATA (``]]>``) would collide with ``]]`` but none of the supplied
    production templates use CDATA.
    """
    return Environment(
        loader=BaseLoader(),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
        variable_start_string="[[",
        variable_end_string="]]",
        block_start_string="[%",
        block_end_string="%]",
        comment_start_string="[#",
        comment_end_string="#]",
    )


#: Pre-existing private spelling, kept because :mod:`auto_ext.core.patch` and
#: :mod:`auto_ext.core.diff_template` import it by this name. One environment,
#: two names -- never two environments.
_make_jinja_env = make_jinja_env


def resolve_template_path(
    path: Path,
    *,
    auto_ext_root: Path | None = None,
    workarea: Path | None = None,
) -> Path:
    """Best-effort lookup that decouples paths from the deploy-dir name.

    Resolution order, first hit wins:

    1. ``path`` as-is — covers absolute paths and the legacy
       workarea-relative form (e.g. ``Auto_ext_pro/templates/foo.j2``)
       when cwd is the workarea, as the runner configures it.
    2. ``workarea / path`` — explicit workarea fallback for callers
       (such as the GUI) whose cwd may not be the workarea.
    3. ``auto_ext_root / path`` — covers auto_ext-root-relative paths
       (e.g. ``templates/calibre/foo.j2``) so a project.yaml is portable
       across deploy dirs without rewriting every template entry.

    On miss returns ``path`` unchanged so downstream errors surface the
    caller's original string rather than a derived candidate. ``None``
    bases fall through (each step is a no-op), matching pre-fallback
    semantics when neither hint is supplied.
    """

    if path.is_absolute():
        return path
    try:
        if path.is_file():
            return path
    except OSError:
        pass
    for base in (workarea, auto_ext_root):
        if base is None:
            continue
        candidate = base / path
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            pass
    return path


def render_template(
    template_path: Path,
    context: dict[str, Any],
    env: dict[str, str],
    *,
    strict_env: bool = True,
) -> str:
    """Render a ``.j2`` template file with env-vars pre-substituted.

    Order: read file -> :func:`substitute_env` -> optional strict scan for
    leftover env refs -> Jinja render with ``context``.

    ``context`` is the whole Jinja namespace; there is no second value layer
    merged on top of it. The ``knobs`` parameter that used to supply one is
    gone with the ``*.manifest.yaml`` mechanism -- the catalog pipeline in
    :mod:`auto_ext.core.render` builds one namespace and renders from a source
    string, and this function is what everything else (tests, one-off scripts)
    uses to render a file from disk.

    Raises :class:`TemplateError` for missing files, Jinja syntax errors,
    :class:`StrictUndefined` violations, and (under ``strict_env=True``)
    any env reference that survives substitution.
    """

    if not template_path.is_file():
        raise TemplateError(f"template not found: {template_path}")

    try:
        source = template_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise TemplateError(f"template is not valid UTF-8: {template_path}") from exc

    if strict_env:
        required = discover_required_vars([source])
        missing = sorted(required - set(env))
        if missing:
            raise TemplateError(
                f"unresolved env refs in {template_path}: {missing}"
            )

    substituted = substitute_env(source, env)

    merged_context = dict(context)

    # Catch the silent "None stringifies to 'None'" trap before Jinja
    # paints "None.None.qcilvs" into a path. StrictUndefined only catches
    # missing keys; a present-but-None value falls through.
    referenced = referenced_jinja_vars(substituted)
    guarded = guarded_jinja_vars(substituted)
    none_keys = sorted(
        name for name in referenced
        if name in merged_context
        and merged_context[name] is None
        and name not in guarded
    )
    if none_keys:
        raise TemplateError(
            f"template {template_path} references {none_keys} but the "
            f"resolved value is None; set the corresponding field(s) on the "
            f"PdkProfile (e.g. tech_name, the LVS deck), the Recipe or the "
            f"cell row before running"
        )

    jenv = make_jinja_env()
    try:
        template = jenv.from_string(substituted)
        return template.render(**merged_context)
    except UndefinedError as exc:
        raise TemplateError(f"undefined Jinja variable in {template_path}: {exc}") from exc
    except TemplateSyntaxError as exc:
        raise TemplateError(f"Jinja syntax error in {template_path}: {exc}") from exc


_JINJA_VAR_RE = re.compile(r"\[\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]\]")


@dataclass(frozen=True)
class VarReference:
    """One occurrence of a ``[[var_name]]`` reference in a template.

    Answers "which templates read this name": given a path key or any other
    Jinja variable, where in the template tree it is referenced. ``line_no``
    is 1-indexed; ``line_excerpt`` is the matched line truncated to
    something readable as inline traceability.
    """

    var_name: str
    template_path: Path
    line_no: int
    line_excerpt: str


_VAR_REFERENCE_LINE_RE = re.compile(r"\[\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*\]\]")


def enumerate_stage_templates(
    auto_ext_root: Path | None, stage: str
) -> list[Path]:
    """Return all ``*.j2`` files under ``<auto_ext_root>/templates/<stage>/``.

    Used by the Project / Tasks tab template pickers. Returns absolute
    paths, sorted lexicographically. Empty when ``auto_ext_root`` is
    ``None`` or the stage subdirectory doesn't exist — callers fall
    back to "no choices" gracefully.

    Only ``.j2`` files come back; anything else in the directory is ignored.
    """
    if auto_ext_root is None:
        return []
    stage_dir = auto_ext_root / "templates" / stage
    if not stage_dir.is_dir():
        return []
    return sorted(p for p in stage_dir.glob("*.j2") if p.is_file())


def collect_var_references(
    template_paths: list[Path], *, excerpt_max: int = 80
) -> list[VarReference]:
    """Scan the given templates for every ``[[name]]`` reference.

    Returns one :class:`VarReference` per (var_name, line_no) pair across
    all templates. A line carrying ``[[a]] foo [[b]]`` produces two
    entries (one for each var) sharing the line number and excerpt.

    Excerpts are right-trimmed and truncated to ``excerpt_max`` chars
    with an ellipsis marker so the GUI can show inline context without
    blowing out the row height.

    Templates that fail to read (missing file, non-UTF-8) are silently
    skipped — the caller (the Project tab) shouldn't crash on a stale
    template path; missing files surface elsewhere.
    """
    results: list[VarReference] = []
    for path in template_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.rstrip("\r")
            seen_on_line: set[str] = set()
            excerpt = stripped.strip()
            if len(excerpt) > excerpt_max:
                excerpt = excerpt[: excerpt_max - 1] + "…"
            for m in _VAR_REFERENCE_LINE_RE.finditer(stripped):
                name = m.group(1)
                if name in seen_on_line:
                    continue
                seen_on_line.add(name)
                results.append(
                    VarReference(
                        var_name=name,
                        template_path=path,
                        line_no=line_no,
                        line_excerpt=excerpt,
                    )
                )
    return results


#: A bare ``[% if name %]`` guard. See :func:`guarded_jinja_vars`.
_JINJA_GUARD_RE = re.compile(r'\[%-?\s*if\s+([A-Za-z_][A-Za-z0-9_]*)\s*-?%\]')


def referenced_jinja_vars(source: str) -> set[str]:
    """Names appearing as ``[[name]]`` in template source.

    Catches simple identifier references; ignores filter pipelines and
    expressions (``[[name|default(...)]]``) since those handle None
    themselves via the filter. The conservative scope is fine for the
    None-check guard — false positives would be expressions that
    deliberately handle None, and they're rare in this project's
    templates.
    """
    return set(_JINJA_VAR_RE.findall(source))


def guarded_jinja_vars(source: str) -> set[str]:
    """Names a ``[% if name %]`` in the source makes optional.

    A guarded var may legitimately be ``None``: that is how an optional line
    spells "omit me", and it is the ONLY way to spell it for an option whose
    default is to say nothing. Every other ``None`` reaching Jinja is painted
    into the generated file as the literal ``"None"``, which is the trap the
    caller below exists to catch.

    Deliberately narrow -- a bare identifier only, no expressions. A var
    inside ``[% if a and b %]`` is not exempted, because the line can then be
    written for a reason that has nothing to do with that var being set.
    """

    return set(_JINJA_GUARD_RE.findall(source))


def scan_placeholders(template_path: Path) -> PlaceholderInventory:
    """Return the placeholder inventory for a template (no rendering).

    ``jinja_variables`` is best-effort: if the template has a Jinja syntax
    error, an empty set is returned and a warning is logged (the Template
    Editor should not crash on a half-edited template).
    """

    if not template_path.is_file():
        raise TemplateError(f"template not found: {template_path}")

    text = template_path.read_text(encoding="utf-8")

    env_vars = discover_required_vars([text])
    literal = {m.group(1) for m in _RE_LITERAL_PLACEHOLDER.finditer(text)}
    user_defined = {m.group(0) for m in _RE_USER_DEFINED.finditer(text)}

    jenv = make_jinja_env()
    try:
        ast = jenv.parse(text)
        jinja_vars = set(meta.find_undeclared_variables(ast))
    except TemplateSyntaxError as exc:
        logger.warning("scan_placeholders: Jinja parse failed for %s: %s", template_path, exc)
        jinja_vars = set()

    return PlaceholderInventory(
        env_vars=env_vars,
        literal_placeholders=literal,
        user_defined=user_defined,
        jinja_variables=jinja_vars,
    )


#: Pre-existing private spelling of :func:`referenced_jinja_vars`, kept so any
#: caller written against it keeps working.
_referenced_jinja_vars = referenced_jinja_vars
