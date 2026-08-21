"""Typer CLI entry point.

Live subcommands:

- ``version`` — prints the package version (Phase 1).
- ``run`` — loads ``project.yaml`` + ``tasks.yaml`` and drives
  :func:`auto_ext.core.runner.run_tasks`.
- ``runs list / show / prune`` — the run history under
  ``<auto-ext-root>/runs/``: what ran, how long each stage took, what LVS
  said, how that compares with the previous run of the same cell, and what
  to try next when something failed.
- ``check-env`` — prints a Rich table of env-var resolution for every
  template referenced by the tasks. Exits 1 if anything is missing.
- ``import`` — turn a raw EDA export into a parameterised ``.j2`` +
  sidecar manifest with identity substitutions pre-applied.
- ``knob suggest / promote`` — inspect and promote candidate literals
  on an already-imported template.

``migrate`` stays a Phase 4c stub.

Rendering lives in :mod:`auto_ext.cli_reporter` (Rich tables, the failure
classifier, the LVS view); this module is the argument surface and the data
plumbing between the core API and those views.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import typer

if TYPE_CHECKING:
    from auto_ext.cli_reporter import SummaryRow
    from auto_ext.core.run_store import RunIndexEntry

app = typer.Typer(
    name="auto-ext",
    help="Automate the Cadence post-layout extraction flow (si/strmout/calibre/qrc/jivaro).",
    no_args_is_help=True,
    add_completion=False,
)

knob_app = typer.Typer(
    name="knob",
    help="Inspect or promote candidate literals on an imported template.",
    no_args_is_help=True,
)
app.add_typer(knob_app, name="knob")

runs_app = typer.Typer(
    name="runs",
    help="Inspect the run history under <auto-ext-root>/runs/.",
    no_args_is_help=True,
)
app.add_typer(runs_app, name="runs")


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
        help="Filter to specific task_id(s). Repeat to include multiple tasks. "
        "A task_id is the cell row's key — "
        "<library>__<cell>__<lvs_layout_view>__<lvs_source_view> — which is "
        "what `auto-ext runs list` shows a run's DUT as.",
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
        help="Root holding runs/. Each task lands in its own runs/<run_id>/ "
        "with its rendered files, logs and results. Defaults to --config-dir parent.",
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
    jobs: int = typer.Option(
        1,
        "--jobs",
        "-j",
        min=1,
        max=64,
        help="Run up to N tasks concurrently. Default 1 (serial). "
        "N>=2 isolates each task under runs/<run_id>/work/ with symlinked "
        "cds.lib/.cdsinit. License budget is yours to manage.",
    ),
    no_progress: bool = typer.Option(
        False,
        "--no-progress",
        help="Suppress the live progress table. Use in CI / non-TTY "
        "contexts; the final summary table is still printed.",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Run extraction tasks through the configured EDA tools.

    Press Ctrl-C once to request a graceful cancel: the in-flight
    subprocess is sent SIGTERM (10s grace) then SIGKILL; remaining
    stages / tasks are skipped; the summary table still prints.
    """
    import signal

    from auto_ext.core.config import load_project, load_tasks
    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.progress import CancelToken, NullReporter, ProgressReporter
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

    cancel_token = CancelToken()
    reporter: ProgressReporter
    if no_progress:
        reporter = NullReporter()
    else:
        from auto_ext.cli_reporter import RichCLIReporter

        reporter = RichCLIReporter()

    # Install a SIGINT handler that flips the cancel flag without
    # aborting the Python interpreter; the runner / run_subprocess see
    # the token within the next drain tick and terminate the in-flight
    # subprocess. Second Ctrl-C falls through to the default handler
    # (KeyboardInterrupt) so the user can still force-exit if a tool
    # ignores SIGTERM + SIGKILL.
    _sigint_fired = {"count": 0}

    def _on_sigint(signum: int, frame: object) -> None:
        _sigint_fired["count"] += 1
        if _sigint_fired["count"] == 1:
            cancel_token.cancel()
            typer.secho(
                "\ncancel requested; waiting for current stage to stop...",
                fg=typer.colors.YELLOW,
                err=True,
            )
        else:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGINT, _on_sigint)

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
            max_workers=jobs if jobs >= 2 else None,
            reporter=reporter,
            cancel_token=cancel_token,
        )
    except AutoExtError as exc:
        typer.secho(f"run aborted: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    finally:
        signal.signal(signal.SIGINT, previous_handler)

    _print_summary(summary, runs_root=summary.runs_root or root / "runs")
    exit_code = 0 if (summary.failed == 0 and summary.cancelled == 0) else 1
    raise typer.Exit(code=exit_code)


@app.command()
def gui(
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        help="Directory containing project.yaml + tasks.yaml to preload.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None,
        "--auto-ext-root",
        help="Root for runs/ and logs/. Defaults to --config-dir parent.",
    ),
    workarea: Optional[Path] = typer.Option(
        None,
        "--workarea",
        help="EDA cwd. Defaults to --auto-ext-root parent.",
    ),
    remember_config: bool = typer.Option(
        True,
        "--remember-config/--no-remember-config",
        help=(
            "When --config-dir is omitted, auto-load the last config_dir "
            "from QSettings and persist on every successful load. "
            "Pass --no-remember-config for one-shot launches."
        ),
    ),
) -> None:
    """Launch the PyQt5 GUI.

    The GUI reuses the same :func:`run_tasks` as the CLI; progress is
    streamed via Qt signals. Linux: ``run.sh gui ...`` prepends the
    bundled PyQt5 Qt5 lib path to ``LD_LIBRARY_PATH`` before spawning
    Python; see commit ``bc0d735`` for the ABI-blocker context.
    """
    try:
        from auto_ext.ui.app import run_gui
    except ImportError as exc:
        typer.secho(
            f"GUI dependencies not available: {exc}. "
            f"On Linux, use ./run.sh gui so LD_LIBRARY_PATH is set "
            f"for the bundled PyQt5 Qt5 libs.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    run_gui(
        config_dir=config_dir,
        auto_ext_root=auto_ext_root,
        workarea=workarea,
        remember_config=remember_config,
    )


@app.command()
def migrate() -> None:
    """Convert legacy Run_ext.txt to tasks.yaml. Implementation lands in Phase 4c."""
    typer.secho(
        "auto-ext migrate: not implemented yet (Phase 4c).",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


# ---- init-project (Phase 4b2) ---------------------------------------------


@app.command("init-project")
def init_project(
    raw_calibre: Path = typer.Option(
        ...,
        "--raw-calibre",
        help="Raw Calibre .qci export.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    raw_quantus: Path = typer.Option(
        ...,
        "--raw-quantus",
        help="Raw Quantus .cmd export.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    raw_si: Path = typer.Option(
        ...,
        "--raw-si",
        help="Raw si.env export.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    raw_jivaro: Optional[Path] = typer.Option(
        None,
        "--raw-jivaro",
        help=(
            "Raw Jivaro XML export. Optional: if omitted, no jivaro template "
            "is written and tasks.yaml defaults jivaro.enabled=false."
        ),
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    output_config_dir: Path = typer.Option(
        Path("./Auto_ext_pro/config"),
        "--output-config-dir",
        help="Destination for project.yaml + tasks.yaml.",
        resolve_path=True,
    ),
    output_templates_dir: Path = typer.Option(
        Path("./Auto_ext_pro/templates"),
        "--output-templates-dir",
        help=(
            "Destination root for imported templates. Per-tool subdirs are "
            "created: calibre/, si/, quantus/, jivaro/."
        ),
        resolve_path=True,
    ),
    cell: Optional[str] = typer.Option(
        None,
        "--cell",
        help="Identity override applied to every per-tool import.",
    ),
    library: Optional[str] = typer.Option(
        None,
        "--library",
        help="Identity override applied to every per-tool import.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Overwrite any existing file. Each overwritten file is first "
            "backed up to .bak."
        ),
    ),
) -> None:
    """Orchestrate 4 per-tool imports into a complete project skeleton.

    Thin Typer wrapper around :mod:`auto_ext.core.init_project`. Runs the
    Phase 4b1 importer on each raw file, cross-validates identities,
    aggregates PDK constants, and writes the four templates + sidecar
    manifests + a populated ``project.yaml`` + a one-task ``tasks.yaml``.
    """
    from auto_ext.core.importer import ImportError as CoreImportError
    from auto_ext.core.init_project import InitInputs, commit, dry_run

    inputs = InitInputs(
        raw_calibre=raw_calibre,
        raw_si=raw_si,
        raw_quantus=raw_quantus,
        raw_jivaro=raw_jivaro,
        output_config_dir=output_config_dir,
        output_templates_dir=output_templates_dir,
        cell_override=cell,
        library_override=library,
        force=force,
    )

    try:
        preview = dry_run(inputs)
    except OSError as exc:
        typer.secho(
            f"cannot read raw input: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    except CoreImportError as exc:
        typer.secho(
            f"import failed: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if preview.conflicts:
        typer.secho(
            "identity mismatch across raw files — reconcile or pass --cell/"
            "--library overrides:",
            fg=typer.colors.RED,
            err=True,
        )
        for line in preview.conflicts:
            typer.secho(f"  {line}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if not force:
        existing = [f.path for f in preview.files if f.will_overwrite]
        if existing:
            typer.secho(
                "refusing to overwrite existing file(s); pass --force to back up "
                "and replace:",
                fg=typer.colors.RED,
                err=True,
            )
            for p in existing:
                typer.secho(f"  {p}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)

    written = commit(preview)

    _print_init_project_summary(
        constants=preview.constants,
        results=preview.results,
        written=written,
        output_config_dir=output_config_dir,
    )


def _print_init_project_summary(
    *,
    constants,
    results: dict,
    written: list[Path],
    output_config_dir: Path,
) -> None:
    """Print a human-readable summary of what init-project detected/wrote."""

    typer.echo("")
    typer.echo("[init-project] Detected project constants:")
    _print_kv("  tech_name          ", constants.tech_name)
    if constants.paths:
        for key in sorted(constants.paths):
            _print_kv(f"  paths.{key:<13}", constants.paths[key])
    else:
        _print_kv("  paths              ", None)

    if constants.unclassified:
        typer.echo("")
        typer.secho(
            "[init-project] Unclassified hardcoded values (review manually):",
            fg=typer.colors.YELLOW,
        )
        for u in constants.unclassified:
            typer.echo(
                f"  {u.tool:<8} line {u.token.line:>3}: "
                f"{u.token.value!r} (category: {u.token.category})"
            )

    typer.echo("")
    typer.echo("[init-project] Wrote:")
    for p in written:
        typer.echo(f"  {p}")

    typer.echo("")
    typer.echo(
        f"Next: review {output_config_dir}/tasks.yaml (edit cells/libs),\n"
        f"then run: auto-ext check-env --config-dir {output_config_dir}\n"
        f"          auto-ext run --dry-run --config-dir {output_config_dir}"
    )


def _print_kv(label: str, value: Optional[str]) -> None:
    if value is None:
        typer.echo(f"{label}= (not detected)")
    else:
        typer.echo(f"{label}= {value}")


_VALID_IMPORT_TOOLS = ("calibre", "si", "quantus", "jivaro")


@app.command("import")
def import_cmd(
    tool: str = typer.Option(
        ...,
        "--tool",
        help=f"EDA format of the raw input. One of {list(_VALID_IMPORT_TOOLS)}.",
    ),
    input_path: Path = typer.Option(
        ...,
        "--input",
        help="Raw EDA export to parameterise.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        help="Target .j2 path. The sidecar manifest is written next to it.",
        resolve_path=True,
    ),
    cell: Optional[str] = typer.Option(None, "--cell"),
    library: Optional[str] = typer.Option(None, "--library"),
    lvs_layout_view: Optional[str] = typer.Option(None, "--lvs-layout-view"),
    lvs_source_view: Optional[str] = typer.Option(None, "--lvs-source-view"),
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help="Wipe any existing output + manifest instead of smart-merging.",
    ),
) -> None:
    """Parameterise a raw EDA export into ``.j2`` + ``.manifest.yaml``.

    Identity values (cell / library / views / ground_net / out_file) are
    auto-inferred from recognised per-format keys and substituted with
    ``[[...]]`` placeholders. All other literals are left as-is; use
    ``knob suggest`` + ``knob promote`` to turn them into knobs.

    If ``--output`` already has a manifest (and ``--fresh`` is not set),
    user-promoted knobs from the existing manifest are re-applied to the
    new body, their defaults refreshed from the raw, and manifest-level
    edits (description, range, unit) preserved.
    """
    from auto_ext.core.importer import (
        Identity,
        ImportError as CoreImportError,
        import_template,
        merge_reimport,
    )
    from auto_ext.core.io_utils import backup_if_exists
    from auto_ext.core.manifest import (
        TemplateManifest,
        dump_manifest_yaml,
        load_manifest,
        manifest_path_for,
    )

    if tool not in _VALID_IMPORT_TOOLS:
        typer.secho(
            f"unknown --tool {tool!r}; valid: {list(_VALID_IMPORT_TOOLS)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        raw = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        typer.secho(
            f"cannot read --input {input_path}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    overrides = Identity(
        cell=cell,
        library=library,
        lvs_layout_view=lvs_layout_view,
        lvs_source_view=lvs_source_view,
    )
    if all(
        getattr(overrides, f) is None
        for f in ("cell", "library", "lvs_layout_view", "lvs_source_view")
    ):
        overrides = None

    try:
        result = import_template(tool, raw, identity_overrides=overrides)
    except CoreImportError as exc:
        typer.secho(f"import failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    manifest_path = manifest_path_for(output)
    existing_manifest: Optional[TemplateManifest] = None
    if not fresh and output.exists() and manifest_path.exists():
        from auto_ext.core.errors import ConfigError

        try:
            existing_manifest = load_manifest(output)
        except ConfigError as exc:
            typer.secho(
                f"warning: existing manifest is unloadable, treating as --fresh: {exc}",
                fg=typer.colors.YELLOW,
                err=True,
            )
            existing_manifest = None

    merge_messages: list[str] = []
    auto_knobs = dict(result.auto_knobs)
    if existing_manifest is not None and existing_manifest.knobs:
        outcome = merge_reimport(result, existing_manifest)
        body = outcome.body
        # auto_knobs are the base; existing user-promoted knobs win on
        # any key conflict (their description / range / source survive).
        merged_knobs = {**auto_knobs, **outcome.manifest.knobs}
        final_manifest = TemplateManifest(
            template=output.name, knobs=merged_knobs
        )
        # ``template`` was validated to match output.name by load_manifest.
        merge_messages = outcome.messages
    else:
        body = result.template_body
        final_manifest = TemplateManifest(
            template=output.name, knobs=auto_knobs
        )

    backup_if_exists(output)
    backup_if_exists(manifest_path)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    manifest_path.write_text(dump_manifest_yaml(final_manifest), encoding="utf-8")

    review_path = output.with_name(output.name + ".review.md")
    backup_if_exists(review_path)
    review_path.write_text(
        _build_review_report(result, merge_messages), encoding="utf-8"
    )

    typer.echo(f"wrote template    : {output}")
    typer.echo(f"wrote manifest    : {manifest_path}")
    typer.echo(f"wrote review      : {review_path}")
    if merge_messages:
        typer.echo("")
        typer.echo("Smart-merge log:")
        for m in merge_messages:
            typer.echo(f"  {m}")
    if result.candidates:
        typer.echo(
            f"\n{len(result.candidates)} knob candidate(s) detected. "
            f"Inspect with: auto-ext knob suggest {output}"
        )


@knob_app.command("suggest")
def knob_suggest(
    template: Path = typer.Argument(
        ...,
        help="Path to the imported .j2 template.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Include low-confidence rows (default: high + medium only).",
    ),
) -> None:
    """List literals that could be promoted to knobs on ``template``."""
    from rich.console import Console
    from rich.table import Table

    from auto_ext.core.importer import (
        ImportError as CoreImportError,
        _detect_candidates,
    )

    tool = _infer_tool_from_path(template)
    if tool is None:
        typer.secho(
            f"cannot infer tool from path {template}; "
            "template must live under templates/{calibre,si,quantus,jivaro}/",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    body = template.read_text(encoding="utf-8")
    try:
        candidates = _detect_candidates(tool, body)
    except CoreImportError as exc:
        typer.secho(f"suggest failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    filtered = [c for c in candidates if show_all or c.confidence != "low"]
    if not filtered:
        typer.echo("no knob candidates detected.")
        raise typer.Exit(code=0)

    console = Console()
    table = Table(title=f"Knob candidates — {template.name}")
    table.add_column("#", justify="right")
    table.add_column("key", style="cyan")
    table.add_column("value")
    table.add_column("type")
    table.add_column("suggested_name")
    table.add_column("line", justify="right")
    for idx, c in enumerate(filtered, start=1):
        type_cell = f"{c.type}*" if c.confidence == "medium" else c.type
        if c.confidence == "low":
            type_cell = f"[dim]{type_cell}[/]"
        table.add_row(
            str(idx),
            c.key,
            repr(c.default),
            type_cell,
            c.suggested_name,
            str(c.line),
        )
    console.print(table)
    console.print(
        "[dim]rows marked * use the bool heuristic on 0/1 with a toggle-style key; "
        "override with --type on `knob promote`.[/]"
    )


@knob_app.command("promote")
def knob_promote(
    template: Path = typer.Argument(
        ...,
        help="Path to the imported .j2 template.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    keys: list[str] = typer.Argument(
        ...,
        metavar="KEY [KEY ...]",
        help="One or more raw-file keys (from `knob suggest`) to promote.",
    ),
    type_override: Optional[str] = typer.Option(
        None,
        "--type",
        help="Force a type for all promoted keys. One of: int, float, str, bool.",
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        help=(
            "Rename the knob. Only valid when promoting exactly one key; "
            "otherwise the suggested snake_case name is used."
        ),
    ),
) -> None:
    """Rewrite ``template`` so ``KEY``'s literal becomes ``[[name]]``, and
    add a matching entry to the sidecar manifest.
    """
    from ruamel.yaml import YAML

    from auto_ext.core.errors import ConfigError
    from auto_ext.core.importer import (
        _CAND_PATTERNS,
        _classify_value,
        _snake_case,
        _substitute_at_key,
    )
    from auto_ext.core.io_utils import backup_if_exists
    from auto_ext.core.manifest import (
        KnobSpec,
        SourceRef,
        TemplateManifest,
        dump_manifest_yaml,
        load_manifest,
        manifest_path_for,
    )

    if type_override is not None and type_override not in (
        "int",
        "float",
        "str",
        "bool",
    ):
        typer.secho(
            f"--type must be one of int/float/str/bool, got {type_override!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    if name is not None and len(keys) != 1:
        typer.secho(
            "--name is only valid when promoting exactly one key",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    tool = _infer_tool_from_path(template)
    if tool is None:
        typer.secho(
            f"cannot infer tool from path {template}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    body = template.read_text(encoding="utf-8")

    manifest_path = manifest_path_for(template)
    try:
        manifest = load_manifest(template)
    except ConfigError as exc:
        typer.secho(
            f"cannot load manifest {manifest_path}: {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if manifest is None:
        manifest = TemplateManifest(template=template.name, knobs={})

    new_knobs = dict(manifest.knobs)
    pattern = _CAND_PATTERNS[tool]

    for key in keys:
        # Locate the raw literal on its line.
        literal: Optional[str] = None
        for line in body.splitlines():
            for m in pattern.finditer(line):
                if m.group("key") == key:
                    literal = m.group("value")
                    break
            if literal is not None:
                break
        if literal is None:
            typer.secho(
                f"key {key!r} not found in {template} (or already promoted)",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

        cls = _classify_value(key, literal)
        if cls is None:
            typer.secho(
                f"key {key!r} value {literal!r} is not a promotable literal",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        inferred_type, inferred_default, _ = cls
        chosen_type = type_override or inferred_type

        # Recoerce literal to chosen_type (user may override int vs bool etc).
        try:
            from auto_ext.core.importer import _coerce_literal

            chosen_default = _coerce_literal(literal, chosen_type)
        except ValueError as exc:
            typer.secho(
                f"cannot coerce {literal!r} to --type {chosen_type}: {exc}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

        knob_name = name if name is not None else _snake_case(key)
        if knob_name in new_knobs:
            typer.secho(
                f"knob {knob_name!r} already present in manifest; refusing to overwrite",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

        body, _ = _substitute_at_key(tool, body, key, f"[[{knob_name}]]")
        new_knobs[knob_name] = KnobSpec(
            type=chosen_type,
            default=chosen_default,
            source=SourceRef(tool=tool, key=key),
        )

    new_manifest = manifest.model_copy(update={"knobs": new_knobs})

    backup_if_exists(template)
    backup_if_exists(manifest_path)

    template.write_text(body, encoding="utf-8")
    manifest_path.write_text(dump_manifest_yaml(new_manifest), encoding="utf-8")

    typer.echo(f"promoted {len(keys)} knob(s); updated:")
    typer.echo(f"  {template}")
    typer.echo(f"  {manifest_path}")


# ---- import/knob helpers ---------------------------------------------------


def _build_review_report(result, merge_messages: list[str]) -> str:
    from datetime import datetime

    lines: list[str] = []
    lines.append("# Import review")
    lines.append("")
    lines.append(f"- tool: **{result.tool}**")
    lines.append(f"- generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("## Identity")
    identity_rows = []
    for field_name in (
        "cell",
        "library",
        "lvs_layout_view",
        "lvs_source_view",
        "ground_net",
        "out_file",
    ):
        val = getattr(result.identity, field_name)
        if val is not None:
            identity_rows.append(f"- {field_name}: `{val}`")
    if identity_rows:
        lines.extend(identity_rows)
    else:
        lines.append("- (nothing extracted)")
    lines.append("")
    lines.append("## Knob candidates")
    if result.candidates:
        lines.append(
            f"{len(result.candidates)} detected. Run "
            f"`auto-ext knob suggest <template>` to inspect them."
        )
    else:
        lines.append("None detected.")
    lines.append("")
    lines.append("## Hardcoded values left as-is")
    if result.pdk_tokens:
        for tok in result.pdk_tokens:
            lines.append(
                f"- line {tok.line}: `{tok.value}` (category: {tok.category})"
            )
        lines.append("")
        lines.append(
            "These are project-level constants. For single-template imports, "
            "review and substitute by hand if your current project differs."
        )
    else:
        lines.append("None detected.")
    lines.append("")
    if merge_messages:
        lines.append("## Smart-merge log")
        for m in merge_messages:
            lines.append(f"- {m}")
        lines.append("")
    lines.append("## Next steps")
    lines.append("- `auto-ext knob suggest <template>`")
    lines.append("- `auto-ext knob promote <template> <key>...`")
    lines.append("")
    return "\n".join(lines)


def _infer_tool_from_path(template: Path):
    """Return the tool name by walking ``template``'s parent directories."""
    for part in reversed(template.parts):
        if part in ("calibre", "si", "quantus", "jivaro"):
            return part
    return None


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
    from auto_ext.core.env import derive_parent_dir_from_env_candidates, resolve_env
    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.runner import _discover_env_vars

    try:
        project = load_project(config_dir / "project.yaml")
        tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    except AutoExtError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    required = _discover_env_vars(project, tasks, auto_ext_root=config_dir.parent)
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

    if project.tech_name is None:
        derived = derive_parent_dir_from_env_candidates(
            project.tech_name_env_vars, resolution.resolved
        )
        if derived is None:
            typer.secho(
                f"warning: tech_name not set in project.yaml and could not "
                f"auto-derive from {project.tech_name_env_vars}. Templates "
                f"referencing [[tech_name]] will fail to render.",
                fg=typer.colors.YELLOW,
            )

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


# ---- run records: end-of-run summary ---------------------------------------


def _live_lvs(task_result):
    """The LVS outcome still sitting in this run's ``ToolResult.diagnostics``."""
    from auto_ext.cli_reporter import LvsView

    for stage_result in task_result.stages:
        tool_result = getattr(stage_result, "tool_result", None)
        if tool_result is None:
            continue
        report = tool_result.diagnostics.get("lvs_report")
        if report is not None:
            return LvsView.from_any(report)
    return None


def _live_failures(task_result, lvs, run_id: Optional[str]) -> list:
    """Classify the bad stages of an in-memory ``TaskResult``."""
    from auto_ext.cli_reporter import classify_stage_failure

    out = []
    for stage_result in task_result.stages:
        tool_result = getattr(stage_result, "tool_result", None)
        details = tool_result.diagnostics if tool_result is not None else {}
        log_path = None
        if tool_result is not None and tool_result.stdout_path is not None:
            log_path = str(tool_result.stdout_path)
        diagnosis = classify_stage_failure(
            stage=stage_result.stage,
            status=stage_result.status,
            error=stage_result.error,
            details=details,
            log_path=log_path,
            lvs=lvs if stage_result.stage == "calibre" else None,
            run_id=run_id,
        )
        if diagnosis is not None:
            out.append(diagnosis)
    return out


def _summary_rows(summary) -> list["SummaryRow"]:
    """Fuse each ``TaskResult`` with the run record that was written for it.

    The record is authoritative when it exists — it holds the per-stage
    timings, the archived LVS report path and the ``quantus.ext`` /
    ``quantus.dspf`` stage keys the in-memory result cannot express. A task
    that died before its record was finalized still gets a row, built from the
    ``ToolResult`` diagnostics that are in memory either way.
    """
    from auto_ext.cli_reporter import LvsView, SummaryRow, run_failures

    rows: list[SummaryRow] = []
    for task_result in summary.tasks:
        record = task_result.record
        live_lvs = _live_lvs(task_result)

        if record is not None and record.stages:
            run_dir = task_result.run_dir or (
                Path(record.run_dir) if record.run_dir else None
            )
            rows.append(
                SummaryRow(
                    task_id=task_result.task_id,
                    overall=str(task_result.overall),
                    stages=tuple((s.key, str(s.status)) for s in record.stages),
                    run_id=record.run_id,
                    display_name=record.default_display_name,
                    lvs=LvsView.from_any(record.results.lvs) or live_lvs,
                    failures=tuple(run_failures(record, run_dir=run_dir)),
                )
            )
            continue

        run_id = task_result.run_dir.name if task_result.run_dir else None
        rows.append(
            SummaryRow(
                task_id=task_result.task_id,
                overall=str(task_result.overall),
                stages=tuple((s.stage, str(s.status)) for s in task_result.stages),
                run_id=run_id,
                display_name=record.default_display_name if record else None,
                lvs=live_lvs,
                failures=tuple(_live_failures(task_result, live_lvs, run_id)),
            )
        )
    return rows


def _print_summary(summary, *, runs_root: Optional[Path] = None) -> None:
    """Closing block of ``auto-ext run``: table, failure notes, where it went."""
    from rich.console import Console

    from auto_ext.cli_reporter import build_summary_table, print_failure_notes

    console = Console()
    rows = _summary_rows(summary)
    # Rich's Live leaves no trailing newline on a non-TTY, so without this the
    # summary title lands on the same line as the progress table's bottom rule.
    console.print()
    console.print(build_summary_table(rows))
    print_failure_notes(console, rows)

    extras = []
    if summary.failed:
        extras.append(f"[red]{summary.failed} failed[/]")
    if summary.cancelled:
        extras.append(f"[yellow]{summary.cancelled} cancelled[/]")
    tail = f" ({', '.join(extras)})" if extras else ""
    console.print(f"[bold]{summary.passed}/{summary.total} tasks passed[/]" + tail)

    recorded = [row.run_id for row in rows if row.run_id]
    if recorded and runs_root is not None:
        console.print(f"run records written to {runs_root}")
        console.print(f"  [dim]inspect with: auto-ext runs show {recorded[0]}[/]")


# ---- runs (history) --------------------------------------------------------


def _resolve_runs_root(
    auto_ext_root: Optional[Path], config_dir: Optional[Path]
) -> Path:
    """``<auto-ext-root>/runs``, resolved the same way ``run`` resolves it."""
    if auto_ext_root is not None:
        root = auto_ext_root
    elif config_dir is not None:
        root = config_dir.parent
    else:
        root = Path.cwd()
    return root.resolve() / "runs"


def _require_run(entries: list["RunIndexEntry"], token: str) -> "RunIndexEntry":
    """Resolve ``token`` to exactly one run, or exit 2 explaining why not.

    Accepts a full run id, any unique prefix of one (the timestamp alone is
    usually enough), or ``latest``.
    """
    if not entries:
        typer.secho("no runs recorded yet", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if token == "latest":
        return entries[0]
    for entry in entries:
        if entry.run_id == token:
            return entry

    matches = [e for e in entries if e.run_id.startswith(token)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        typer.secho(
            f"{token!r} matches {len(matches)} runs; be more specific:",
            fg=typer.colors.RED,
            err=True,
        )
        for entry in matches[:10]:
            typer.secho(f"  {entry.run_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    typer.secho(
        f"no run matching {token!r}; `auto-ext runs list` shows what is on record",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


_RUNS_ROOT_HELP = "Root holding runs/. Defaults to --config-dir's parent, else cwd."
_RUNS_CONFIG_HELP = "Config dir; its parent is used as the root when --auto-ext-root is omitted."


@runs_app.command("list")
def runs_list(
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_RUNS_ROOT_HELP
    ),
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        help=_RUNS_CONFIG_HELP,
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    limit: int = typer.Option(
        20, "--limit", "-n", min=0, help="Show at most N runs. 0 shows all."
    ),
    cell: Optional[str] = typer.Option(
        None, "--cell", help="Only runs of this cell (exact match)."
    ),
    task: Optional[str] = typer.Option(
        None,
        "--task",
        help="Only runs of this cell row key "
        "(<library>__<cell>__<lvs_layout_view>__<lvs_source_view>).",
    ),
    failed: bool = typer.Option(
        False, "--failed", help="Only runs that did not pass."
    ),
) -> None:
    """List the run history, newest first.

    One row per ``runs/<run_id>/``: when it started, its name, the cell, how
    it ended, and what LVS said. Directories that are not runs (and runs with
    an unreadable ``run.json``) are skipped with a warning on stderr rather
    than taking the listing down.
    """
    from rich.console import Console

    from auto_ext.cli_reporter import build_runs_table
    from auto_ext.core.run_store import list_runs

    runs_root = _resolve_runs_root(auto_ext_root, config_dir)
    entries = list_runs(runs_root)
    total = len(entries)

    if cell is not None:
        entries = [e for e in entries if e.cell == cell]
    if task is not None:
        entries = [e for e in entries if e.dut_key == task]
    if failed:
        entries = [e for e in entries if e.overall != "passed"]
    matched = len(entries)
    if limit:
        entries = entries[:limit]

    if not entries:
        typer.echo(
            f"no runs on record under {runs_root}"
            if total == 0
            else f"no run matches the filter ({total} on record under {runs_root})"
        )
        raise typer.Exit(code=0)

    console = Console()
    console.print(build_runs_table(entries))
    console.print(
        f"[dim]{len(entries)} of {matched} matching run(s), {total} on record "
        f"under {runs_root}[/]"
    )


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(
        ...,
        metavar="RUN_ID",
        help="A run id, any unique prefix of one, or 'latest'.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_RUNS_ROOT_HELP
    ),
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        help=_RUNS_CONFIG_HELP,
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
) -> None:
    """Show one run in full.

    Per-stage status with durations and exit codes, the structured LVS
    outcome, how its discrepancy count compares with the previous run of the
    same DUT, the artifacts it left in the workarea, and — for anything that
    failed — a failure class plus the next thing to try.
    """
    from rich.console import Console

    from auto_ext.cli_reporter import (
        LvsView,
        build_stages_table,
        print_artifacts,
        print_comparison,
        print_diagnoses,
        print_lvs_block,
        print_run_header,
        print_skips,
        run_failures,
    )
    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.run_store import (
        find_previous_run,
        list_runs,
        read_annotations,
        read_record,
    )

    runs_root = _resolve_runs_root(auto_ext_root, config_dir)
    entries = list_runs(runs_root)
    entry = _require_run(entries, run_id)

    try:
        record = read_record(entry.run_dir)
    except AutoExtError as exc:
        typer.secho(f"cannot read {entry.run_id}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    annotations = read_annotations(entry.run_dir)
    console = Console()
    print_run_header(console, record, run_dir=entry.run_dir, annotations=annotations)

    if record.stages:
        console.print()
        console.print(build_stages_table(record))
    else:
        console.print()
        console.print(
            "[dim]no stage recorded — the run was still in flight when it was "
            "written[/]"
        )

    lvs = LvsView.from_any(record.results.lvs)
    if lvs is not None and not lvs.empty:
        print_lvs_block(console, lvs)

    print_comparison(
        console,
        current=record,
        current_lvs=lvs,
        previous=find_previous_run(runs_root, entry, entries=entries),
    )
    print_skips(console, record)
    print_artifacts(console, record)
    print_diagnoses(
        console,
        # The discrepancy delta is already a few lines above; do not tell the
        # user to run the command they are currently reading the output of.
        run_failures(record, run_dir=entry.run_dir, suggest_compare=False),
    )


@runs_app.command("prune")
def runs_prune(
    keep: int = typer.Option(
        ...,
        "--keep",
        min=0,
        help="Keep the N newest runs. 0 means unlimited (nothing is removed).",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Actually delete. Without it, prune only reports what it would remove.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_RUNS_ROOT_HELP
    ),
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        help=_RUNS_CONFIG_HELP,
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
) -> None:
    """Delete all but the N newest runs.

    Three classes of run are never deleted, and none of them consumes one of
    the N slots: runs the user pinned (starred, or carrying any tag), runs
    that never finished, and directories that could not be parsed as runs at
    all. Nothing prunes on a timer — this is the only entry point.
    """
    from auto_ext.core.run_store import list_runs, prune_runs

    runs_root = _resolve_runs_root(auto_ext_root, config_dir)
    if keep == 0:
        typer.echo("--keep 0 means unlimited; nothing removed.")
        raise typer.Exit(code=0)

    entries = list_runs(runs_root)
    protected = [e for e in entries if e.pinned or e.overall == "pending"]
    removed = prune_runs(runs_root, keep, dry_run=not yes)

    if not removed:
        typer.echo(
            f"nothing to prune: {len(entries)} run(s) on record under {runs_root}, "
            f"keeping {keep}."
        )
    else:
        verb = "removed" if yes else "would remove"
        typer.echo(f"{verb} {len(removed)} run(s):")
        for run in removed:
            typer.echo(f"  {run}")
    if protected:
        typer.echo(
            f"kept {len(protected)} pinned / unfinished run(s) outside the "
            f"--keep {keep} window."
        )
    if removed and not yes:
        typer.secho("nothing was deleted; re-run with --yes.", fg=typer.colors.YELLOW)
