"""Enable ``python -m auto_ext`` to invoke the Typer CLI."""

from auto_ext.cli import app


def main() -> None:
    app()


if __name__ == "__main__":
    main()
