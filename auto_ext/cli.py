"""Typer CLI entry point.

Three live subcommands as of Phase 3:

- ``version`` — prints the package version (Phase 1).
- ``run`` — loads ``project.yaml`` + ``tasks.yaml`` and drives
  :func:`auto_ext.core.runner.run_tasks`.
- ``check-env`` — prints a Rich table of env-var resolution for every
  template referenced by the tasks. Exits 1 if anything is missing.

``migrate`` stays a Phase 4 stub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

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
def run(
    config_dir: Path = typer.Option(
        ...,
        "--config-dir",
        help="Directory containing project.yaml + tasks.yaml.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    task: Optional[list[str]] = typer.Option(
        None,
        "--task",
        help="Filter to specific task_id(s). Repeat to include multiple tasks.",
    ),
    stage: Optional[str] = typer.Option(
        None,
        "--stage",
        help="Comma-separated stages to run "
        "(si,strmout,calibre,quantus,jivaro). Default: all.",
    ),
    continue_on_lvs_fail: bool = typer.Option(
        False,
        "--continue-on-lvs-fail",
        help="Force continue_on_lvs_fail=True on every task (overrides per-task config).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Render templates but do not spawn subprocesses.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None,
        "--auto-ext-root",
        help="Root for runs/ and logs/ output. Defaults to --config-dir parent.",
    ),
    workarea: Optional[Path] = typer.Option(
        None,
        "--workarea",
        help="EDA cwd (where si.env lands). Defaults to --auto-ext-root parent.",
    ),
    knob: Optional[list[str]] = typer.Option(
        None,
        "--knob",
        help="Override a knob for this run. Format: <stage>.<name>=<value>. "
        "Repeatable. Quote values containing spaces, e.g. "
        '--knob "quantus.temperature=60".',
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Run extraction tasks through the configured EDA tools (serial)."""
    from auto_ext.core.config import load_project, load_tasks
    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.runner import STAGE_ORDER, run_tasks

    try:
        project = load_project(config_dir / "project.yaml")
        tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    except AutoExtError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if task:
        want = set(task)
        filtered = [t for t in tasks if t.task_id in want]
        missing = want - {t.task_id for t in filtered}
        if missing:
            typer.secho(
                f"task(s) not found: {sorted(missing)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        tasks = filtered

    stages_list = (
        [s.strip() for s in stage.split(",") if s.strip()] if stage else list(STAGE_ORDER)
    )

    if continue_on_lvs_fail:
        tasks = [t.model_copy(update={"continue_on_lvs_fail": True}) for t in tasks]

    try:
        cli_knobs = _parse_cli_knobs(knob or [], STAGE_ORDER)
    except AutoExtError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    root = (auto_ext_root or config_dir.parent).resolve()
    wa = (workarea or root.parent).resolve()

    try:
        summary = run_tasks(
            project,
            tasks,
            stages=stages_list,
            auto_ext_root=root,
            workarea=wa,
            verbose=verbose,
            dry_run=dry_run,
            cli_knobs=cli_knobs,
        )
    except AutoExtError as exc:
        typer.secho(f"run aborted: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    _print_summary(summary)
    raise typer.Exit(code=0 if summary.failed == 0 else 1)


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
def check_env(
    config_dir: Path = typer.Option(
        ...,
        "--config-dir",
        help="Directory containing project.yaml + tasks.yaml.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
) -> None:
    """Report env-var resolution status for every template in use."""
    from rich.console import Console
    from rich.table import Table

    from auto_ext.core.config import load_project, load_tasks
    from auto_ext.core.env import resolve_env
    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.runner import _discover_env_vars

    try:
        project = load_project(config_dir / "project.yaml")
        tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    except AutoExtError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    required = _discover_env_vars(project, tasks)
    resolution = resolve_env(required, project.env_overrides)

    console = Console()
    table = Table(title="Env resolution")
    table.add_column("var", style="cyan")
    table.add_column("source")
    table.add_column("value")

    for name in sorted(resolution.resolved):
        src = resolution.sources[name]
        val = resolution.resolved[name]
        if len(val) > 80:
            val = val[:77] + "..."
        style = {"missing": "red", "override": "yellow", "shell": "green"}[src]
        table.add_row(name, f"[{style}]{src}[/]", val or "[dim](empty)[/]")
    console.print(table)

    if resolution.missing:
        console.print(f"[red]missing vars: {resolution.missing}[/]")
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


def _parse_cli_knobs(
    entries: list[str], valid_stages: tuple[str, ...]
) -> dict[str, dict[str, str]]:
    """Parse repeated ``--knob stage.name=value`` into a nested string dict.

    Values stay strings here; :func:`auto_ext.core.manifest.resolve_knob_values`
    does the per-knob type coercion at render time.
    """
    from auto_ext.core.errors import ConfigError

    out: dict[str, dict[str, str]] = {}
    for entry in entries:
        if "=" not in entry:
            raise ConfigError(f"--knob {entry!r}: missing '=' (expected stage.name=value)")
        lhs, value = entry.split("=", 1)
        if "." not in lhs:
            raise ConfigError(
                f"--knob {entry!r}: missing '.' in {lhs!r} (expected stage.name=value)"
            )
        stage, name = lhs.split(".", 1)
        if stage not in valid_stages:
            raise ConfigError(
                f"--knob {entry!r}: unknown stage {stage!r}; valid: {list(valid_stages)}"
            )
        if not name:
            raise ConfigError(f"--knob {entry!r}: empty knob name")
        out.setdefault(stage, {})[name] = value
    return out


def _print_summary(summary) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Run summary")
    table.add_column("task_id", style="cyan")
    table.add_column("overall")
    table.add_column("stages")
    for t in summary.tasks:
        stages_str = " ".join(f"{s.stage}:{s.status[0]}" for s in t.stages)
        style = "green" if t.overall == "passed" else "red"
        table.add_row(t.task_id, f"[{style}]{t.overall}[/]", stages_str)
    console.print(table)
    console.print(
        f"[bold]{summary.passed}/{summary.total} tasks passed[/] ({summary.failed} failed)"
    )
