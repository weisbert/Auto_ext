"""Environment-variable discovery, resolution, and substitution.

Scans template source text for ``$X``, ``${X}``, and ``$env(X)`` references;
resolves them against ``overrides`` then ``os.environ``; and substitutes
resolved values back into arbitrary text (used by :mod:`auto_ext.core.template`
for template rendering and by :mod:`auto_ext.tools` for argv construction).

Resolution order: ``overrides[X]`` -> ``os.environ[X]`` -> missing.

The ``$$FOO`` escape is honoured: literal ``$FOO`` survives substitution
and is not treated as an env reference during discovery.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal

from auto_ext.core.errors import EnvResolutionError

logger = logging.getLogger(__name__)


EnvSource = Literal["override", "shell", "missing"]


# Regexes compiled once at module load.
#
# The negative lookbehind ``(?<!\$)`` rejects the second ``$`` of a ``$$FOO``
# escape, matching shell semantics. The bare form additionally requires no
# trailing word character so ``$FOO_BAR`` is one token (``FOO_BAR``), not
# two (``FOO`` + ``_BAR``).
_RE_ENV_TCL = re.compile(r"(?<!\$)\$env\(([A-Za-z_][A-Za-z0-9_]*)\)")
_RE_ENV_BRACE = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Bare ``$X``: reject ``$env(`` so the Tcl form isn't double-counted as a bare
# reference to an identifier named ``env``. Any other ``$name(...)`` still
# matches (shell has no array syntax; caller gets ``name`` expanded and the
# following ``(...)`` stays literal).
_RE_ENV_BARE = re.compile(r"(?<!\$)\$(?!env\()([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_])")

_ESCAPE_SENTINEL = "\x00"


@dataclass(frozen=True)
class EnvResolution:
    """Outcome of :func:`resolve_env`.

    ``resolved[var]`` is the empty string for ``missing`` vars; callers
    decide whether to treat missing as fatal (via :meth:`require`) or to
    proceed (GUI env panel flags them with a warning icon).
    """

    resolved: dict[str, str]
    sources: dict[str, EnvSource]

    @property
    def missing(self) -> list[str]:
        return sorted(name for name, src in self.sources.items() if src == "missing")

    def require(self) -> dict[str, str]:
        """Return ``self.resolved`` or raise :class:`EnvResolutionError`."""
        missing = self.missing
        if missing:
            raise EnvResolutionError(f"unresolved env vars: {', '.join(missing)}")
        return self.resolved


def discover_required_vars(template_sources: Iterable[str]) -> set[str]:
    """Return the union of env-var names referenced by the given source texts.

    Matches ``$X``, ``${X}``, and ``$env(X)``. ``$$X`` is treated as an
    escape and does NOT produce a match. ``$1`` and other non-identifier
    forms are ignored.
    """

    found: set[str] = set()
    for src in template_sources:
        # Mask the escape sequence so the bare regex can't see through it.
        masked = src.replace("$$", _ESCAPE_SENTINEL + _ESCAPE_SENTINEL)
        found.update(m.group(1) for m in _RE_ENV_TCL.finditer(masked))
        found.update(m.group(1) for m in _RE_ENV_BRACE.finditer(masked))
        found.update(m.group(1) for m in _RE_ENV_BARE.finditer(masked))
    return found


def resolve_env(required: set[str], overrides: dict[str, str]) -> EnvResolution:
    """Resolve each required var: ``overrides`` -> ``os.environ`` -> missing.

    Never raises. Missing vars have empty-string values and ``source="missing"``.
    """

    resolved: dict[str, str] = {}
    sources: dict[str, EnvSource] = {}

    for var in sorted(required):
        if var in overrides:
            resolved[var] = overrides[var]
            sources[var] = "override"
            logger.debug("env %s resolved from override", var)
        elif var in os.environ:
            resolved[var] = os.environ[var]
            sources[var] = "shell"
            logger.debug("env %s resolved from shell", var)
        else:
            resolved[var] = ""
            sources[var] = "missing"
            logger.warning("env %s is unresolved (no override, not in os.environ)", var)

    return EnvResolution(resolved=resolved, sources=sources)


def substitute_env(text: str, resolved: dict[str, str]) -> str:
    """Replace ``$X`` / ``${X}`` / ``$env(X)`` in ``text`` with values from ``resolved``.

    Unknown names (not present in ``resolved``) pass through unchanged. The
    ``$$`` escape collapses to a single ``$`` in the output. Processing
    order is Tcl -> brace -> bare; the forms are disjoint so order only
    matters for the escape handling.
    """

    if not text:
        return text

    # Protect $$ so the substitution passes don't see the escaped $.
    text = text.replace("$$", _ESCAPE_SENTINEL)

    def _replace(m: re.Match[str]) -> str:
        name = m.group(1)
        return resolved.get(name, m.group(0))

    text = _RE_ENV_TCL.sub(_replace, text)
    text = _RE_ENV_BRACE.sub(_replace, text)
    text = _RE_ENV_BARE.sub(_replace, text)

    # Restore the escape as a literal $.
    return text.replace(_ESCAPE_SENTINEL, "$")
