"""Environment-variable discovery, resolution, and override for EDA subprocesses.

- Discovery: scan all templates for ``$X``, ``${X}``, ``$env(X)`` refs.
- Resolution order: ``project.yaml.env_overrides[X]`` -> ``os.environ[X]`` -> missing.
- Override mechanism: merged values are passed via ``subprocess.run(env=...)``;
  the user's shell is never mutated.

Implementation lands in Phase 2.
"""

from __future__ import annotations


def discover_required_vars(template_sources: list[str]) -> set[str]:
    """Return the union of env vars referenced by the given template texts. Phase 2."""

    raise NotImplementedError


def resolve_env(required: set[str], overrides: dict[str, str]) -> dict[str, str]:
    """Resolve required vars against overrides and ``os.environ``. Phase 2."""

    raise NotImplementedError
