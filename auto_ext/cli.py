"""Typer CLI entry point.

Phase 1 only exposes a placeholder ``app`` Typer instance so that
``python -m auto_ext`` and the ``auto-ext`` console script resolve.
Real subcommands (``run``, ``migrate``, ``check-env``, ...) are wired
up in Phase 3 once the core modules exist.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="auto-ext",
    help="Automate the Cadence post-layout extraction flow (si/strmout/calibre/qrc/jivaro).",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the installed Auto_ext version and exit."""

    from auto_ext import __version__

    typer.echo(__version__)
