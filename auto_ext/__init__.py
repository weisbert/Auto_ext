"""Auto_ext: GUI + automation for the Cadence post-layout extraction flow.

Public surface is intentionally small. External callers should prefer
``auto_ext.cli`` (Typer app) or the high-level ``auto_ext.core.runner``
API once it lands. The package is versioned via :mod:`pyproject.toml`.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
