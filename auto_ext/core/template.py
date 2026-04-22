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


def _make_jinja_env() -> Environment:
    """Build the Jinja environment used by both render and scan paths.

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


def render_template(
    template_path: Path,
    context: dict[str, Any],
    env: dict[str, str],
    *,
    strict_env: bool = True,
) -> str:
    """Render a ``.j2`` template with env-vars pre-substituted.

    Order: read file -> :func:`substitute_env` -> optional strict scan for
    leftover env refs -> Jinja render with ``context``.

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

    jenv = _make_jinja_env()
    try:
        template = jenv.from_string(substituted)
        return template.render(**context)
    except UndefinedError as exc:
        raise TemplateError(f"undefined Jinja variable in {template_path}: {exc}") from exc
    except TemplateSyntaxError as exc:
        raise TemplateError(f"Jinja syntax error in {template_path}: {exc}") from exc


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

    jenv = _make_jinja_env()
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


