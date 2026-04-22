"""Typer CLI entry point.

Phase 1 exposes ``version`` (real) and stubs for the Phase 3+ subcommands
(``run``, ``migrate``, ``check-env``) so that ``python -m auto_ext`` stays
in Typer's multi-command mode. With only one registered command Typer
silently folds it into the top-level entry and rejects the subcommand
name on the CLI ("Got unexpected extra argument (version)").
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


@app.command()
def run() -> None:
    """Run extraction tasks. Implementation lands in Phase 3."""

    typer.secho(
        "auto-ext run: not implemented yet (Phase 3). "
        "Core modules + tool plugins are required first.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


@app.command()
def migrate() -> None:
    """Convert legacy Run_ext.txt to tasks.yaml. Implementation lands in Phase 4."""

    typer.secho(
        "auto-ext migrate: not implemented yet (Phase 4).",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


@app.command("check-env")
def check_env() -> None:
    """Report env-var resolution status. Implementation lands in Phase 2."""

    typer.secho(
        "auto-ext check-env: not implemented yet (Phase 2).",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)
