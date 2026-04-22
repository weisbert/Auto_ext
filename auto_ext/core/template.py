"""Jinja2 rendering, template scanning, and diff-mode editor backend.

Key rules (see project memory):
- All env-var references (``$X``, ``${X}``, ``$env(X)``) are substituted at
  render time with resolved values -- rendered files contain zero env refs.
- Literal placeholders (``__CELL_NAME__``, ``user_defined_*``) are bound to
  Jinja vars by name; Calibre ``.qci`` baked values are bound by key semantics.
- Diff-mode editor wraps line-level differences between two real exports as
  ``{% if toggle_name %}...{% endif %}`` blocks.

Implementation lands in Phase 2 (render/scan) and Phase 6 (diff-mode editor).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_template(template_path: Path, context: dict[str, Any], env: dict[str, str]) -> str:
    """Render a ``.j2`` template with env-var substitution. Phase 2."""

    raise NotImplementedError


def scan_placeholders(template_path: Path) -> dict[str, Any]:
    """Return placeholder + env-var inventory for a template. Phase 2."""

    raise NotImplementedError
