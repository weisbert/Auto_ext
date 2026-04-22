"""Domain exceptions for the Auto_ext core layer.

Every module under :mod:`auto_ext.core` raises a subclass of
:class:`AutoExtError` so callers (runner, GUI, CLI) can catch a single
base type and still tell what went wrong.

Convention: ``parse_lvs_report`` returning ``False`` is NOT a
:class:`CheckError`. :class:`CheckError` is only for "cannot determine
pass/fail" (truncated report, banner missing, unreadable file).
"""

from __future__ import annotations


class AutoExtError(Exception):
    """Base class for all Auto_ext domain errors. Do not raise directly."""


class ConfigError(AutoExtError):
    """Malformed ``project.yaml`` or ``tasks.yaml`` (parse or schema failure)."""


class EnvResolutionError(AutoExtError):
    """Required env vars could not be resolved and caller asked to fail-fast."""


class TemplateError(AutoExtError):
    """Template I/O, Jinja syntax error, or unresolved env refs under strict mode."""


class WorkdirError(AutoExtError):
    """Workdir preparation failed (missing source, symlink denied, etc.)."""


class CheckError(AutoExtError):
    """LVS report is too malformed to classify. Distinct from a clean INCORRECT."""
