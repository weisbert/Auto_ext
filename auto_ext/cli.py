"""Typer CLI entry point.

Live subcommands:

- ``version`` — prints the package version (Phase 1).
- ``run`` — loads ``project.yaml`` + ``tasks.yaml`` and drives
  :func:`auto_ext.core.runner.run_tasks`.
- ``runs list / show / prune`` — the run history under
  ``<auto-ext-root>/runs/``: what ran, how long each stage took, what LVS
  said, how that compares with the previous run of the same cell, and what
  to try next when something failed.
- ``check-env`` — env-var resolution plus, when a PdkProfile is in play,
  the full health report. A thin wrapper over ``profile health``.
- ``import`` — turn a raw EDA export into a parameterised ``.j2`` +
  sidecar manifest with identity substitutions pre-applied.
- ``knob suggest / promote`` — inspect and promote candidate literals
  on an already-imported template.
- ``recipe list / show / new / set`` — the Recipe library
  (:mod:`auto_ext.model.recipe`): one portable extraction configuration
  per YAML file, found on the search path documented in
  :func:`recipe_search_path`.
- ``profile list / show / discover / health`` — the PdkProfile
  (:mod:`auto_ext.model.pdk`) under ``<root>/config/profiles/``, the
  machine scan that drafts one (:mod:`auto_ext.core.profile_discover`)
  and the health report that says whether a run can start
  (:mod:`auto_ext.core.health`).
- ``catalog list / show`` — the built-in parameter catalog
  (:mod:`auto_ext.catalog`): every value the generated EDA input files
  contain, who owns it and where it lands.
- ``patch list / show / drop`` — the manual-edit escape hatch stored on a
  recipe (:mod:`auto_ext.core.patch`).
- ``migrate`` — legacy ``project.yaml`` + ``tasks.yaml`` to the
  profile / recipe / cells / workspace world, via
  :func:`auto_ext.migrate.migrate_v1_to_v2`.

Two render paths coexist in this build, and ``--recipe`` is the only thing
that chooses between them. Without it, ``run`` renders from
``project.templates`` plus the ``*.manifest.yaml`` knob merge, untouched.
With it — and the ``--profile`` it requires — ``auto_ext.core.runner``
assembles its recipe pipeline: templates from the catalog, values from the
Recipe and the PdkProfile, manual edits from ``Recipe.patches``. This module
resolves the two objects from disk, checks the profile's health before
anything starts, and hands them over; it re-implements none of that logic.

Rendering lives in :mod:`auto_ext.cli_reporter` (Rich tables, the failure
classifier, the LVS view); this module is the argument surface and the data
plumbing between the core API and those views.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Optional

import typer

if TYPE_CHECKING:
    from auto_ext.cli_reporter import SummaryRow
    from auto_ext.core.run_store import RunIndexEntry
    from auto_ext.model.pdk import PdkProfile
    from auto_ext.model.recipe import Recipe, ResourceProfile

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

recipe_app = typer.Typer(
    name="recipe",
    help="The recipe library: portable extraction configurations.",
    no_args_is_help=True,
)
app.add_typer(recipe_app, name="recipe")

profile_app = typer.Typer(
    name="profile",
    help="The PDK profile: deck paths, corners, supply names, health.",
    no_args_is_help=True,
)
app.add_typer(profile_app, name="profile")

catalog_app = typer.Typer(
    name="catalog",
    help="The built-in parameter catalog behind the generated EDA input files.",
    no_args_is_help=True,
)
app.add_typer(catalog_app, name="catalog")

patch_app = typer.Typer(
    name="patch",
    help="Manual edits stored on a recipe, as masked anchored hunks.",
    no_args_is_help=True,
)
app.add_typer(patch_app, name="patch")


# ---- shared option help ----------------------------------------------------

#: Environment variable that prepends a directory to the recipe search path.
#: Documented in ``docs/refactor/01-schema.md`` section 1.2.
RECIPES_ENV_VAR = "AUTO_EXT_RECIPES"

_RECIPES_DIR_HELP = (
    "Extra recipes directory, searched last so it shadows the rest. "
    "Also the directory `recipe new` writes into."
)
_PROFILES_DIR_HELP = (
    "Directory holding <profile_id>.yaml. Defaults to <root>/config/profiles."
)
_PROFILE_ROOT_HELP = (
    "Auto_ext root holding config/profiles/. Defaults to --config-dir's "
    "parent, else cwd."
)
_RUNS_ROOT_HELP = "Root holding runs/. Defaults to --config-dir's parent, else cwd."
_RUNS_CONFIG_HELP = "Config dir; its parent is used as the root when --auto-ext-root is omitted."


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
    recipe: Optional[str] = typer.Option(
        None,
        "--recipe",
        help="Render through the catalog instead of project.templates + knob "
        "manifests. Needs --profile as well. `auto-ext recipe list` shows "
        "what is on the search path.",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="The PdkProfile supplying the corner literals, deck paths and "
        "supply-name tables the recipe deliberately does not carry. Its "
        "health report is checked before anything starts.",
    ),
    resources: Optional[Path] = typer.Option(
        None,
        "--resources",
        help="ResourceProfile YAML (turbo count, cpu counts, licence wait). "
        "Defaults to <root>/config/resources.yaml when that exists.",
    ),
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
    profiles_dir: Optional[Path] = typer.Option(
        None, "--profiles-dir", help=_PROFILES_DIR_HELP
    ),
    health_check: bool = typer.Option(
        True,
        "--health-check/--no-health-check",
        help="With --profile, refuse to start when the profile's health "
        "report says a required check failed. --no-health-check reports "
        "the failures and starts anyway.",
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

    Two render paths, and ``--recipe`` is the only thing that chooses:

    * Without it, templates come from ``project.templates`` and values from
      the ``*.manifest.yaml`` knob merge, exactly as before.
    * With it (and the ``--profile`` it requires), every stage renders through
      :mod:`auto_ext.core.render`: templates from the catalog, values from the
      Recipe and the PdkProfile, manual edits from ``Recipe.patches``. The
      recipe also owns the stage set (intersected with ``--stage``), whether
      jivaro runs, and ``continue_on_lvs_fail``.

    ``--knob`` belongs to the legacy path only — the recipe path has no knob
    layer for it to override — so combining the two is refused rather than
    silently ignored. ``--continue-on-lvs-fail`` is honoured on both: on the
    recipe path it overrides ``recipe.policy.continue_on_lvs_fail`` for this
    invocation.

    Press Ctrl-C once to request a graceful cancel: the in-flight
    subprocess is sent SIGTERM (10s grace) then SIGKILL; remaining
    stages / tasks are skipped; the summary table still prints.
    """
    import signal

    from rich.console import Console

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

    if recipe is not None and cli_knobs:
        typer.secho(
            "--knob overrides a *.manifest.yaml knob, and the recipe render "
            "path has no knob layer: the value would be ignored. Put it in "
            "the recipe instead (`auto-ext recipe set "
            f"{recipe} <field>=<value>`), or drop --recipe.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    recipe_obj: Optional["Recipe"] = None
    profile_obj: Optional["PdkProfile"] = None
    resource_obj: Optional["ResourceProfile"] = None

    # Resolve both objects before checking anything: a mistyped recipe name is
    # cheap to report and the health report is not, and being told to pass
    # --no-health-check in order to discover a typo would be absurd.
    if recipe is not None:
        loaded_recipe = _load_recipe(
            recipe_search_path(auto_ext_root, config_dir, recipes_dir), recipe
        )
        recipe_obj = loaded_recipe.recipe
        if continue_on_lvs_fail:
            # The recipe path reads recipe.policy, not task.continue_on_lvs_fail,
            # so the flag has to land there or it would do nothing.
            recipe_obj = recipe_obj.model_copy(
                update={
                    "policy": recipe_obj.policy.model_copy(
                        update={"continue_on_lvs_fail": True}
                    )
                }
            )
        ref = recipe_obj.ref(source_path=loaded_recipe.path)
        typer.echo(
            f"recipe {ref.recipe_id} v{ref.version} "
            f"({ref.content_sha256[:12]}) from {loaded_recipe.path}"
        )

    if profile is not None:
        loaded_profile = _load_profile(
            _profiles_dir(auto_ext_root, config_dir, profiles_dir), profile
        )
        profile_obj = loaded_profile.profile
        typer.echo(
            f"profile {profile_obj.profile_id} ({profile_obj.display_name}) "
            f"from {loaded_profile.path}"
        )
        # Only when the pair is complete: a profile without a recipe is about
        # to be refused by run_tasks, and burying that message under a health
        # report the user did not ask for helps nobody.
        report = _run_health(profile_obj) if recipe is not None else None
        if report is not None and not report.can_run:
            blocking = ", ".join(result.check_id for result in report.blocking)
            if health_check:
                # The full report, because the user has to act on it.
                _print_health(
                    Console(), report, profile=profile_obj, source=loaded_profile.path
                )
                typer.secho(
                    "refusing to start: the profile's health report has "
                    "blocking checks. Fix them, or pass --no-health-check to "
                    "run anyway.",
                    fg=typer.colors.RED,
                    err=True,
                )
                raise typer.Exit(code=2)
            # One line, because the user already said they know: printing 20
            # rows of a report they chose to skip only buries the run summary.
            typer.secho(
                f"--no-health-check: starting with {len(report.blocking)} "
                f"blocking check(s) unresolved ({blocking}). "
                f"`auto-ext profile health {profile_obj.profile_id}` explains "
                f"each one.",
                fg=typer.colors.YELLOW,
                err=True,
            )

    if recipe_obj is not None:
        resource_path = resources or (root / "config" / _RESOURCES_FILENAME)
        if resources is not None or resource_path.is_file():
            resource_obj = _load_resources(resource_path)
            typer.echo(f"resources {resource_obj.resource_id} from {resource_path}")

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
            recipe=recipe_obj,
            profile=profile_obj,
            resources=resource_obj,
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
def migrate(
    config_dir: Path = typer.Option(
        ...,
        "--config-dir",
        help="Directory containing the legacy project.yaml + tasks.yaml.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    out_root: Optional[Path] = typer.Option(
        None,
        "--out-root",
        help="Where config/ and recipes/ are written. Defaults to --config-dir's parent.",
    ),
    template_root: Optional[Path] = typer.Option(
        None,
        "--template-root",
        help="The templates/ directory the legacy config points into; its "
        "hardcoded literals are read back so the migration is value-neutral. "
        "Defaults to the templates shipped with this build.",
    ),
    catalog: Optional[Path] = typer.Option(
        None,
        "--catalog",
        help="An options.yaml (or the directory holding one) to migrate "
        "against. Defaults to the built-in catalog.",
    ),
    profile_id: Optional[str] = typer.Option(
        None,
        "--profile-id",
        help="Id of the PdkProfile to write. Default: slugified tech_name.",
    ),
    recipe_name_hint: str = typer.Option(
        "migrated",
        "--recipe-prefix",
        help="Fallback stem for a recipe with too few distinguishing "
        "parameters to name itself.",
    ),
    seed_patches: bool = typer.Option(
        False,
        "--seed-patches/--no-seed-patches",
        help="Re-render every migrated object and store the residue against "
        "the legacy output as template patches.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Dependency-free text (migrate.format_report) instead of tables.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Actually write the files. Without it, migrate only reports.",
    ),
) -> None:
    """Migrate a legacy project.yaml + tasks.yaml to profile / recipes / cells.

    Writes ``config/profiles/<id>.yaml``, ``config/cells.yaml``,
    ``config/workspace.yaml``, ``config/resources.yaml`` and one
    ``recipes/<id>.yaml`` per distinct effective configuration the old task
    table held.

    The listing is the point of the command: every legacy field appears in the
    disposition table, so nothing can vanish silently; every choice the
    migration had to make appears under "decisions" with the answer it used;
    every value carried over that nobody has confirmed against a real tool
    appears under "needs confirmation". Exits 1 when there are warnings, 0
    otherwise, so a wrapper script can branch on "a human should look".
    """
    from rich.console import Console

    from auto_ext.core.errors import AutoExtError

    try:
        from auto_ext.migrate import format_report, migrate_v1_to_v2
    except ImportError as exc:  # pragma: no cover - depends on the build
        typer.secho(
            f"auto-ext migrate: auto_ext.migrate.migrate_v1_to_v2 is not "
            f"available in this build ({exc}).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    root = (out_root or config_dir.parent).resolve()

    try:
        report = migrate_v1_to_v2(
            config_dir / "project.yaml",
            config_dir / "tasks.yaml",
            template_root=template_root,
            catalog_root=catalog,
            out_root=root,
            profile_id=profile_id,
            recipe_name_hint=recipe_name_hint,
            seed_patches=seed_patches,
            write=write,
        )
    except NotImplementedError as exc:
        typer.secho(f"auto-ext migrate: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    except AutoExtError as exc:
        typer.secho(f"migration failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    if plain:
        typer.echo(format_report(report))
    else:
        _print_migration_report(Console(), report, write=write, out_root=root)
    raise typer.Exit(code=1 if report.warnings else 0)


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
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        help="Directory containing project.yaml + tasks.yaml. Without "
        "--profile this selects the legacy env scan, which walks every "
        "template the tasks reference.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="Check this PdkProfile instead of scanning templates. Omit when "
        "exactly one profile exists under <root>/config/profiles/ — it is "
        "then selected automatically.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
    ),
    profiles_dir: Optional[Path] = typer.Option(
        None, "--profiles-dir", help=_PROFILES_DIR_HELP
    ),
) -> None:
    """Report whether this shell can run: env vars, decks, corners, tools.

    A thin wrapper over ``auto-ext profile health``. When a PdkProfile can be
    resolved — named with ``--profile``, or the only one under
    ``<root>/config/profiles/`` — this prints the full health report and exits
    with :attr:`~auto_ext.model.pdk.PdkHealthReport.exit_code`.

    With no profile and a ``--config-dir`` it falls back to the legacy scan:
    the env vars are discovered by walking the templates the tasks reference,
    resolved through ``project.env_overrides`` -> shell, and the exit code is
    1 if any is missing. Same table, same verdict as before profiles existed;
    the only reason it survives is that a project that has not been migrated
    yet still has to be checkable.
    """
    from rich.console import Console

    from auto_ext.core.config import load_project, load_tasks
    from auto_ext.core.env import derive_parent_dir_from_env_candidates, resolve_env
    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.health import iter_env_rows
    from auto_ext.core.runner import _discover_env_vars

    console = Console()
    directory = _profiles_dir(auto_ext_root, config_dir, profiles_dir)
    selected = profile or (
        _sole_profile_id(directory) if directory.is_dir() else None
    )
    if selected is not None:
        loaded = _load_profile(directory, selected)
        report = _run_health(loaded.profile)
        _print_health(console, report, profile=loaded.profile, source=loaded.path)
        raise typer.Exit(code=report.exit_code)

    if config_dir is None:
        known = _profile_ids(directory)
        detail = (
            f"name one with --profile: {known} live under {directory}"
            if known
            else f"no profile under {directory} and no --config-dir. Run "
            f"`auto-ext profile discover --write` to draft a profile, or "
            f"point --config-dir at a legacy project.yaml."
        )
        typer.secho(f"nothing to check: {detail}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    try:
        project = load_project(config_dir / "project.yaml")
        tasks = load_tasks(config_dir / "tasks.yaml", project=project)
    except AutoExtError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    required = _discover_env_vars(project, tasks, auto_ext_root=config_dir.parent)
    resolution = resolve_env(required, project.env_overrides)
    console.print(_env_table(iter_env_rows(resolution), title="Env resolution"))

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
    console.print(
        "[dim]legacy env scan (no PdkProfile). `auto-ext profile discover` "
        "drafts one; `auto-ext profile health` then also checks decks, "
        "corners and tool availability.[/]"
    )
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


# ---- recipe / profile / catalog / patch: shared plumbing --------------------
#
# The four C1 contracts live in four modules that know nothing about each
# other. Everything below is the argument surface over their public API plus
# the two bridges the ``run`` command needs; no rendering rule, no schema and
# no matching logic is re-implemented here.

class LoadedRecipe(NamedTuple):
    """A recipe plus the file it actually came from."""

    recipe: "Recipe"
    path: Path


class LoadedProfile(NamedTuple):
    """A profile plus the file it actually came from."""

    profile: "PdkProfile"
    path: Path


def _root_of(auto_ext_root: Optional[Path], config_dir: Optional[Path]) -> Path:
    """The Auto_ext root, resolved the way every other command resolves it."""
    if auto_ext_root is not None:
        return auto_ext_root.resolve()
    if config_dir is not None:
        return config_dir.resolve().parent
    return Path.cwd().resolve()


def recipe_search_path(
    auto_ext_root: Optional[Path] = None,
    config_dir: Optional[Path] = None,
    recipes_dir: Optional[Path] = None,
) -> list[Path]:
    """The recipe search path, earliest first; later entries shadow earlier.

    ``$AUTO_EXT_RECIPES`` -> ``~/.auto_ext/recipes`` -> ``<root>/recipes`` ->
    ``<config_dir>/recipes`` -> ``--recipes-dir``, exactly as
    ``docs/refactor/01-schema.md`` section 1.2 specifies. Duplicates are
    collapsed keeping the last occurrence, so naming the same directory twice
    does not make a recipe look shadowed by itself.
    """
    import os

    candidates: list[Path] = []
    env_value = os.environ.get(RECIPES_ENV_VAR)
    if env_value:
        candidates.extend(Path(part) for part in env_value.split(os.pathsep) if part)
    candidates.append(Path.home() / ".auto_ext" / "recipes")
    candidates.append(_root_of(auto_ext_root, config_dir) / "recipes")
    if config_dir is not None:
        candidates.append(config_dir.resolve() / "recipes")
    if recipes_dir is not None:
        candidates.append(recipes_dir)

    ordered: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in ordered:
            ordered.remove(resolved)
        ordered.append(resolved)
    return ordered


def _profiles_dir(
    auto_ext_root: Optional[Path] = None,
    config_dir: Optional[Path] = None,
    profiles_dir: Optional[Path] = None,
) -> Path:
    """``<root>/config/profiles``, or the explicit ``--profiles-dir``."""
    if profiles_dir is not None:
        return profiles_dir.expanduser().resolve()
    return _root_of(auto_ext_root, config_dir) / "config" / "profiles"


def _recipe_files(dirs: list[Path]) -> dict[str, list[Path]]:
    """``{recipe_id: [path, ...]}`` over the search path, in search order.

    Every hit is kept so ``recipe list`` can say which copies are shadowed;
    the winner is the last element.
    """
    found: dict[str, list[Path]] = {}
    for directory in dirs:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            found.setdefault(path.stem, []).append(path)
    return found


def _load_recipe(dirs: list[Path], name: str) -> LoadedRecipe:
    """Resolve ``name`` on the search path and load it, or exit 2."""
    from auto_ext.core.errors import AutoExtError
    from auto_ext.model.recipe import load_recipe

    direct = Path(name)
    if direct.suffix in (".yaml", ".yml") and direct.is_file():
        path = direct.resolve()
    else:
        hits = _recipe_files(dirs).get(name)
        if not hits:
            known = sorted(_recipe_files(dirs))
            typer.secho(
                f"no recipe named {name!r} on the search path:",
                fg=typer.colors.RED,
                err=True,
            )
            for directory in dirs:
                mark = "" if directory.is_dir() else "  (missing)"
                typer.secho(f"  {directory}{mark}", fg=typer.colors.RED, err=True)
            if known:
                typer.secho(f"known: {known}", fg=typer.colors.RED, err=True)
            else:
                typer.secho(
                    "none found; `auto-ext recipe new <name>` creates one from "
                    "the catalog defaults.",
                    fg=typer.colors.RED,
                    err=True,
                )
            raise typer.Exit(code=2)
        path = hits[-1]

    try:
        return LoadedRecipe(load_recipe(path), path)
    except AutoExtError as exc:
        typer.secho(f"cannot load recipe: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


def _profile_ids(directory: Path) -> list[str]:
    """Every ``<id>.yaml`` stem under ``directory``, sorted."""
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))


def _sole_profile_id(directory: Path) -> Optional[str]:
    """The only profile in ``directory``, or ``None`` when there is a choice."""
    ids = _profile_ids(directory)
    return ids[0] if len(ids) == 1 else None


def _load_profile(directory: Path, profile_id: str) -> LoadedProfile:
    """Load ``<directory>/<profile_id>.yaml``, or exit 2 explaining why not."""
    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.profile_discover import read_profile_yaml

    direct = Path(profile_id)
    path = (
        direct.resolve()
        if direct.suffix in (".yaml", ".yml") and direct.is_file()
        else directory / f"{profile_id}.yaml"
    )
    if not path.is_file():
        known = _profile_ids(directory)
        typer.secho(f"no profile at {path}", fg=typer.colors.RED, err=True)
        typer.secho(
            f"profiles under {directory}: {known or '(none)'}. "
            f"`auto-ext profile discover --write` drafts one from this shell.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        return LoadedProfile(read_profile_yaml(path), path)
    except AutoExtError as exc:
        typer.secho(f"cannot load profile: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


def _require_profile(
    auto_ext_root: Optional[Path],
    config_dir: Optional[Path],
    profiles_dir: Optional[Path],
    profile_id: Optional[str],
) -> LoadedProfile:
    """Resolve the profile a command was pointed at, or exit 2."""
    directory = _profiles_dir(auto_ext_root, config_dir, profiles_dir)
    selected = profile_id or _sole_profile_id(directory)
    if selected is None:
        known = _profile_ids(directory)
        typer.secho(
            f"name a profile: {known} live under {directory}"
            if known
            else f"no profile under {directory}; run "
            f"`auto-ext profile discover --write` to draft one",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    return _load_profile(directory, selected)


def _run_health(profile: "PdkProfile", *, path: Optional[Path] = None):
    """Evaluate a profile's checks.

    The checks always run: a cached "you can run" that the environment has
    since invalidated is exactly the answer worth an hour of wasted EDA time.
    With ``path`` the fresh result also refreshes
    ``config/profiles/<id>.health.json`` for the GUI; without it nothing is
    written, which is what ``run`` wants — starting a run should not touch the
    config directory.
    """
    from auto_ext.core.health import HealthError, cached_or_check, check_profile

    if path is None:
        return check_profile(profile)
    try:
        report, _from_cache = cached_or_check(path, profile, force=True)
    except HealthError as exc:
        # The cache is a convenience; failing to refresh it must not hide the
        # answer the user asked for.
        typer.secho(f"warning: {exc}", fg=typer.colors.YELLOW, err=True)
        return check_profile(profile)
    return report


# ---- rendering helpers ------------------------------------------------------

#: Rich styles for the four check states. Words, never glyphs: a Windows
#: console is GBK and a tick mark comes out as a question mark there.
_STATUS_STYLE = {
    "ok": "green",
    "warn": "yellow",
    "fail": "red",
    "unknown": "magenta",
}


def _status_cell(status: str) -> str:
    return f"[{_STATUS_STYLE.get(status, 'white')}]{status}[/]"


def _env_table(rows, *, title: str):
    """The ``name / source / value`` table shared by check-env and profile health."""
    from rich import box
    from rich.table import Table

    table = Table(title=title, box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("var", style="cyan", no_wrap=True)
    table.add_column("source")
    table.add_column("value", overflow="fold")

    style = {"missing": "red", "override": "yellow", "shell": "green"}
    for name, source, value in rows:
        shown = value if len(value) <= 80 else value[:77] + "..."
        table.add_row(
            name,
            f"[{style.get(source, 'white')}]{source}[/]",
            shown or "[dim](empty)[/]",
        )
    return table


def _print_health(console, report, *, profile=None, source: Optional[Path] = None) -> None:
    """Print one health report: the table, then a fix hint per problem."""
    from rich import box
    from rich.table import Table

    from auto_ext.core.health import iter_env_rows, resolve_profile_env

    title = f"Profile health: {report.profile_id}"
    if profile is not None:
        title += f" ({profile.display_name})"
    # No "what" column: the check id already names the check, and giving the
    # observed value the width instead keeps a 90-character deck path to two
    # lines rather than five. Every non-ok row repeats its title in the fix
    # block below, which is where a human reads prose anyway.
    table = Table(title=title, box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("check", style="cyan", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("req", no_wrap=True)
    table.add_column("observed", overflow="fold")

    for result in report.results:
        table.add_row(
            result.check_id,
            _status_cell(result.status.value),
            "yes" if result.required else "no",
            result.observed or (result.message or ""),
        )
    console.print(table)

    if profile is not None:
        console.print(_env_table(iter_env_rows(resolve_profile_env(profile)), title="Env resolution"))

    counts = report.counts()
    console.print(
        f"[bold]{counts['ok']} ok[/], "
        f"[yellow]{counts['warn']} warn[/], "
        f"[red]{counts['fail']} fail[/], "
        f"[magenta]{counts['unknown']} unknown[/]"
    )

    problems = [r for r in report.results if not r.ok]
    if problems:
        console.print()
        console.print("[bold]How to fix[/]")
        for result in problems:
            console.print(
                f"  [{_STATUS_STYLE.get(result.status.value, 'white')}]"
                f"{result.status.value}[/] {result.check_id} ({result.title}): "
                f"{result.message or result.observed or ''}"
            )
            if result.fix_hint:
                console.print(f"      [dim]{result.fix_hint}[/]")

    if source is not None:
        console.print(f"[dim]profile: {source}[/]")
    verdict = (
        "[green]this shell can start a run[/]"
        if report.can_run
        else "[red]a run cannot start until the blocking checks above pass[/]"
    )
    console.print(verdict)


def _print_kv_block(console, title: str, rows) -> None:
    """A dim label / value grid, the same shape ``runs show`` uses."""
    from rich.table import Table

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in rows:
        grid.add_row(label, "" if value is None else str(value))
    console.print(f"[bold]{title}[/]")
    console.print(grid)


def _fmt_value(value) -> str:
    """Render a setting for a table cell without inventing a syntax for it."""
    from enum import Enum

    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "(unset)"
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt_value(v) for v in value) or "(empty)"
    return str(value)


def _get_path(obj, dotted: str):
    """Walk a dotted attribute path on a pydantic model."""
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _set_path(tree: dict, dotted: str, value) -> None:
    """Assign into a nested plain dict, creating intermediate mappings."""
    parts = dotted.split(".")
    node = tree
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


# ---- the two run bridges ----------------------------------------------------

#: Recipe fields that are envelope rather than settings: identity, lineage
#: and bookkeeping. ``recipe show`` groups them out of the settings table.
_RECIPE_ENVELOPE_FIELDS: frozenset = frozenset(
    {
        "schema_version",
        "recipe_id",
        "name",
        "description",
        "version",
        "tags",
        "derived_from",
        "updated_at",
        "patches",
    }
)

#: File name of the :class:`~auto_ext.model.recipe.ResourceProfile` under
#: ``<root>/config/``. Mirrors ``auto_ext.migrate.RESOURCES_FILENAME``, which
#: is what writes it; duplicated rather than imported so the CLI does not pull
#: the whole migration module in to resolve one path.
_RESOURCES_FILENAME = "resources.yaml"


def _catalog_backed_recipe_fields() -> set[str]:
    """Recipe field paths a catalog row binds to a place in a rendered file."""
    from auto_ext.catalog import Owner, builtin_catalog

    return {
        option.recipe_field_path
        for option in builtin_catalog().by_owner(Owner.RECIPE)
        if option.recipe_field_path is not None
    }


def _load_resources(path: Path) -> "ResourceProfile":
    """Load ``config/resources.yaml``, or exit 2 explaining why not."""
    from pydantic import ValidationError
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    from auto_ext.model.recipe import ResourceProfile

    try:
        data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        typer.secho(f"cannot read {path}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    except YAMLError as exc:
        typer.secho(f"{path} is not valid YAML: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if not isinstance(data, dict):
        typer.secho(
            f"{path}: expected a mapping of resource settings",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        return ResourceProfile.model_validate(data)
    except ValidationError as exc:
        typer.secho(f"{path}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


def _print_migration_report(console, report, *, write: bool, out_root: Path) -> None:
    """Render an :class:`auto_ext.migrate.MigrationReport` as Rich tables.

    The plain-text twin is ``auto_ext.migrate.format_report``; ``--plain``
    prints that instead. Both show the same four sections, and the
    disposition table is the one that must never be elided: a legacy field
    that appears in neither the output nor this table has been lost.
    """
    from rich import box
    from rich.table import Table

    _print_kv_block(
        console,
        "Migration",
        [
            ("out root", out_root),
            ("profile", f"{report.profile.profile_id} ({report.profile.display_name})"),
            ("cells", f"{len(report.cells)} row(s)"),
            ("recipes", len(report.recipes)),
            ("workspace", report.workspace.output_dir_pattern),
            ("resources", report.resources.resource_id),
            ("seeded patches", len(report.seeded_patches)),
            ("mode", "write" if write else "report only"),
        ],
    )

    if report.recipes:
        table = Table(title="Recipes", box=box.SIMPLE_HEAD, pad_edge=False)
        table.add_column("recipe_id", style="cyan", no_wrap=True)
        table.add_column("name", overflow="fold")
        table.add_column("cells", justify="right", no_wrap=True)
        table.add_column("covers", overflow="fold")
        for recipe in report.recipes:
            bound = report.bindings.get(recipe.recipe_id, [])
            table.add_row(
                recipe.recipe_id, recipe.name, str(len(bound)), ", ".join(bound) or "-"
            )
        console.print()
        console.print(table)

    if report.dispositions:
        counts = report.disposition_counts()
        table = Table(
            title="Field dispositions ("
            + ", ".join(f"{action} {n}" for action, n in sorted(counts.items()))
            + ")",
            box=box.SIMPLE_HEAD,
            pad_edge=False,
            expand=True,
        )
        # Nothing here is no_wrap: a disposition source can be
        # "project.yaml:knobs.quantus.coupling_cap_threshold_absolute", and
        # pinning that column crushes the other three into single characters.
        table.add_column("source", style="cyan", overflow="fold", ratio=3)
        table.add_column("action", no_wrap=True)
        table.add_column("target", overflow="fold", ratio=3)
        table.add_column("note", overflow="fold", ratio=2)
        for item in report.dispositions:
            table.add_row(item.source, item.action, item.target or "-", item.note or "")
        console.print()
        console.print(table)

    if report.decisions:
        console.print()
        console.print("[bold]Decisions taken (answer them to change the output)[/]")
        for decision in report.decisions:
            console.print(f"  [cyan]{decision.key}[/]: {decision.question}")
            console.print(
                f"      [dim]answered {report.answers.get(decision.key)!r}"
                + (f"; options: {', '.join(decision.options)}" if decision.options else "")
                + "[/]"
            )

    if report.open_questions:
        console.print()
        console.print("[bold yellow]Needs confirmation[/]")
        for question in report.open_questions:
            console.print(
                f"  [yellow]{question.file}: {question.field_path} = "
                f"{question.value!r}[/]"
            )
            console.print(f"      [dim]{question.question}[/]")

    if report.warnings:
        console.print()
        console.print("[bold yellow]Warnings[/]")
        for warning in report.warnings:
            console.print(f"  [yellow]{warning}[/]")

    console.print()
    for path in report.written:
        console.print(f"wrote {path}")
    for path in report.skipped:
        console.print(f"[dim]left alone (already exists): {path}[/]")
    if not report.written and not report.skipped:
        console.print("[yellow]nothing written; re-run with --write.[/]")


# ---- recipe -----------------------------------------------------------------


@recipe_app.command("list")
def recipe_list(
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
) -> None:
    """List every recipe on the search path.

    A recipe_id found in more than one directory is reported once, with the
    copy that wins and a count of the copies it shadows.
    """
    from rich import box
    from rich.console import Console
    from rich.table import Table

    from auto_ext.core.errors import AutoExtError
    from auto_ext.model.common import STAGE_ORDER
    from auto_ext.model.recipe import load_recipe

    dirs = recipe_search_path(auto_ext_root, config_dir, recipes_dir)
    found = _recipe_files(dirs)
    console = Console()

    if not found:
        typer.echo("no recipe on the search path:")
        for directory in dirs:
            typer.echo(f"  {directory}{'' if directory.is_dir() else '  (missing)'}")
        typer.echo("`auto-ext recipe new <name>` creates one from the catalog defaults.")
        raise typer.Exit(code=0)

    # A resolved recipe path is 60+ characters; printing it per row leaves no
    # width for anything else, so the directories go in a legend underneath
    # and each row carries the index of the one it came from.
    legend: list[Path] = []
    table = Table(title="Recipes", box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("recipe_id", style="cyan", no_wrap=True)
    table.add_column("name", max_width=24, overflow="fold")
    table.add_column("ver", no_wrap=True)
    table.add_column("stages", max_width=12, overflow="fold")
    table.add_column("emit", max_width=14, overflow="fold")
    table.add_column("edits", justify="right", no_wrap=True)
    table.add_column("from", no_wrap=True)

    broken = 0
    for recipe_id in sorted(found):
        hits = found[recipe_id]
        path = hits[-1]
        if path.parent not in legend:
            legend.append(path.parent)
        origin = str(legend.index(path.parent) + 1)
        if len(hits) > 1:
            origin += f" [dim](+{len(hits) - 1})[/]"
        try:
            recipe = load_recipe(path)
        except AutoExtError as exc:
            broken += 1
            table.add_row(recipe_id, f"[red]unreadable: {exc}[/]", "", "", "", "", origin)
            continue
        stages = [item.value for item in recipe.stages]
        table.add_row(
            recipe_id,
            recipe.name,
            recipe.version,
            "all" if stages == [item.value for item in STAGE_ORDER] else ",".join(stages),
            ",".join(k.value for k in recipe.output.emit),
            str(recipe.manual_edit_count) if recipe.manual_edit_count else "-",
            origin,
        )
    console.print(table)
    for index, directory in enumerate(legend, start=1):
        console.print(f"[dim]  [{index}] {directory}[/]")
    console.print(
        f"[dim]{len(found)} recipe(s) over {len(dirs)} search directories; "
        f"(+n) = n shadowed copy/copies earlier on the path[/]"
    )
    if broken:
        raise typer.Exit(code=1)


@recipe_app.command("show")
def recipe_show(
    name: str = typer.Argument(..., metavar="NAME", help="recipe_id, or a path to a .yaml."),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
    changed: bool = typer.Option(
        False,
        "--changed",
        help="Only the settings that differ from the catalog defaults.",
    ),
    as_yaml: bool = typer.Option(
        False, "--yaml", help="Print the recipe file verbatim instead of a table."
    ),
) -> None:
    """Show one recipe, marking every setting that differs from the catalog.

    The catalog default is what the shipped templates emit, so a recipe with
    no marked rows renders byte-identically to a knob-driven run.
    """
    from rich import box
    from rich.console import Console
    from rich.table import Table

    from auto_ext.model.recipe import (
        PROFILE_FALLBACK_FIELDS,
        recipe_field_paths,
        recipe_from_catalog,
    )

    loaded = _load_recipe(recipe_search_path(auto_ext_root, config_dir, recipes_dir), name)
    if as_yaml:
        typer.echo(loaded.path.read_text(encoding="utf-8").rstrip("\n"))
        raise typer.Exit(code=0)

    recipe = loaded.recipe
    console = Console()
    _print_kv_block(
        console,
        f"Recipe {recipe.recipe_id}",
        [
            ("name", recipe.name),
            ("version", recipe.version),
            ("description", recipe.description or "-"),
            ("tags", ", ".join(recipe.tags) or "-"),
            ("derived from", recipe.derived_from or "-"),
            ("stages", ", ".join(s.value for s in recipe.stages)),
            ("manual edits", recipe.manual_edit_count),
            ("content sha256", recipe.content_sha256()[:16]),
            ("updated at", recipe.updated_at.isoformat()),
            ("source", loaded.path),
        ],
    )

    baseline = recipe_from_catalog()
    backed = _catalog_backed_recipe_fields()
    table = Table(title="Settings", box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("field", style="cyan", no_wrap=True)
    table.add_column("value", overflow="fold")
    table.add_column("catalog default", overflow="fold")
    table.add_column("renders", no_wrap=True)

    shown = 0
    for dotted in recipe_field_paths():
        if dotted in _RECIPE_ENVELOPE_FIELDS:
            continue
        mine = _get_path(recipe, dotted)
        theirs = _get_path(baseline, dotted)
        differs = mine != theirs
        if changed and not differs:
            continue
        shown += 1
        value = _fmt_value(mine)
        if dotted in PROFILE_FALLBACK_FIELDS:
            renders = "profile"
        elif dotted in backed:
            renders = "yes"
        else:
            renders = "-"
        table.add_row(
            dotted,
            f"[yellow]{value}[/]" if differs else value,
            _fmt_value(theirs) if differs else "[dim]=[/]",
            renders,
        )
    console.print()
    console.print(table)
    if shown == 0:
        console.print("[dim]every setting is at its catalog default[/]")
    console.print(
        "[dim]renders: yes = a catalog row puts it in a generated file; "
        "profile = the PdkProfile supplies the literal when this is unset.[/]"
    )


@recipe_app.command("new")
def recipe_new(
    name: str = typer.Argument(..., metavar="NAME", help="recipe_id for the new file."),
    display_name: Optional[str] = typer.Option(
        None, "--name", help="Display name. Defaults to the recipe_id."
    ),
    from_recipe: Optional[str] = typer.Option(
        None,
        "--from",
        help="Clone this recipe instead of starting from the catalog defaults; "
        "derived_from records the lineage.",
    ),
    description: Optional[str] = typer.Option(None, "--description"),
    tag: Optional[list[str]] = typer.Option(None, "--tag", help="Repeatable."),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing file."),
) -> None:
    """Create a recipe from the catalog defaults, or clone an existing one.

    Straight from the catalog the new recipe reproduces exactly what the
    shipped templates emit today, which is what makes it a safe starting
    point: ``run --recipe`` on it renders the same bytes as a run without it.
    """
    from pydantic import ValidationError

    from auto_ext.core.errors import AutoExtError
    from auto_ext.model.common import utcnow
    from auto_ext.model.recipe import Recipe, recipe_filename, recipe_from_catalog, save_recipe

    dirs = recipe_search_path(auto_ext_root, config_dir, recipes_dir)
    target_dir = recipes_dir.expanduser().resolve() if recipes_dir else dirs[-1]

    try:
        if from_recipe is not None:
            source = _load_recipe(dirs, from_recipe)
            recipe = source.recipe.model_copy(
                update={
                    "recipe_id": name,
                    "name": display_name or name,
                    "derived_from": source.recipe.recipe_id,
                    "version": "1",
                    "updated_at": utcnow(),
                }
            )
            # model_copy skips validation, so re-validate before writing:
            # recipe_id is a Slug and "My Recipe" is not one.
            recipe = Recipe.model_validate(recipe.model_dump(mode="json"))
        else:
            recipe = recipe_from_catalog(recipe_id=name, name=display_name or name)
        if description is not None:
            recipe.description = description
        if tag:
            recipe.tags = list(tag)
    except (AutoExtError, ValidationError) as exc:
        typer.secho(f"cannot create recipe: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    path = target_dir / recipe_filename(recipe)
    if path.exists() and not force:
        typer.secho(
            f"{path} already exists; pass --force to overwrite it.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    save_recipe(recipe, path)
    typer.echo(f"wrote {path}")
    typer.echo(f"  recipe_id: {recipe.recipe_id}")
    typer.echo(f"  name:      {recipe.name}")
    if recipe.derived_from:
        typer.echo(f"  derived:   {recipe.derived_from}")
    typer.echo(f"  inspect with: auto-ext recipe show {recipe.recipe_id}")


@recipe_app.command("set")
def recipe_set(
    name: str = typer.Argument(..., metavar="NAME", help="recipe_id, or a path to a .yaml."),
    assignment: list[str] = typer.Argument(
        ...,
        metavar="KEY=VALUE...",
        help="Dotted recipe field and its new value, e.g. "
        "extraction.min_res_ohm=0.01. Values are parsed as YAML scalars, so "
        "true / 5000 / 0.01 / [a, b] all mean what they look like.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the change without writing the file."
    ),
) -> None:
    """Set one or more recipe fields in place, keeping the file's comments.

    Every assignment is applied first and the whole result is validated once,
    so a recipe never lands half-edited: either every field takes or the file
    is untouched.
    """
    from io import StringIO

    from pydantic import ValidationError
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    from auto_ext.model.common import utcnow
    from auto_ext.model.recipe import Recipe, load_recipe_with_raw, recipe_field_paths, save_recipe

    loaded = _load_recipe(recipe_search_path(auto_ext_root, config_dir, recipes_dir), name)
    recipe, raw = load_recipe_with_raw(loaded.path)

    known = set(recipe_field_paths())
    scalar_reader = YAML(typ="safe")
    payload = recipe.model_dump(mode="json")
    changes: list[tuple[str, str, str]] = []

    for item in assignment:
        if "=" not in item:
            typer.secho(
                f"{item!r}: missing '=' (expected field.path=value)",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        dotted, raw_value = item.split("=", 1)
        dotted = dotted.strip()
        if dotted not in known:
            near = [k for k in sorted(known) if dotted.split(".")[-1] in k]
            typer.secho(f"unknown recipe field {dotted!r}", fg=typer.colors.RED, err=True)
            if near:
                typer.secho(f"did you mean: {near[:8]}", fg=typer.colors.RED, err=True)
            typer.secho(
                "`auto-ext recipe show <name>` lists every field.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        try:
            value = scalar_reader.load(StringIO(raw_value))
        except YAMLError as exc:
            typer.secho(f"{item!r}: cannot parse the value: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        changes.append((dotted, _fmt_value(_get_path(recipe, dotted)), _fmt_value(value)))
        _set_path(payload, dotted, value)

    payload["updated_at"] = utcnow().isoformat()
    try:
        updated = Recipe.model_validate(payload)
    except ValidationError as exc:
        typer.secho(f"the result is not a valid recipe:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    for dotted, before, after in changes:
        typer.echo(f"{dotted}: {before} -> {after}")
    if dry_run:
        typer.echo("--dry-run: nothing written.")
        raise typer.Exit(code=0)

    save_recipe(updated, loaded.path, raw=raw)
    typer.echo(f"wrote {loaded.path}")


# ---- profile ----------------------------------------------------------------


@profile_app.command("list")
def profile_list(
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    profiles_dir: Optional[Path] = typer.Option(
        None, "--profiles-dir", help=_PROFILES_DIR_HELP
    ),
) -> None:
    """List the PdkProfiles under <root>/config/profiles/."""
    from rich import box
    from rich.console import Console
    from rich.table import Table

    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.profile_discover import read_profile_yaml

    directory = _profiles_dir(auto_ext_root, config_dir, profiles_dir)
    ids = _profile_ids(directory)
    if not ids:
        typer.echo(f"no profile under {directory}")
        typer.echo("`auto-ext profile discover --write` drafts one from this shell.")
        raise typer.Exit(code=0)

    table = Table(title=f"Profiles in {directory}", box=box.SIMPLE_HEAD, pad_edge=False)
    table.add_column("profile_id", style="cyan", no_wrap=True)
    table.add_column("display name", overflow="fold")
    table.add_column("tech", overflow="fold")
    table.add_column("corners", overflow="fold")
    table.add_column("lvs variants", overflow="fold")
    table.add_column("edited", no_wrap=True)

    broken = 0
    for profile_id in ids:
        try:
            profile = read_profile_yaml(directory / f"{profile_id}.yaml")
        except AutoExtError as exc:
            broken += 1
            table.add_row(profile_id, f"[red]unreadable: {exc}[/]", "", "", "", "")
            continue
        table.add_row(
            profile_id,
            profile.display_name,
            profile.tech_name or "(auto)",
            ", ".join(c.name for c in profile.corners) or "-",
            ", ".join(profile.lvs_decks.variant_names) or "-",
            "yes" if profile.hand_edited else "no",
        )
    Console().print(table)
    if broken:
        raise typer.Exit(code=1)


@profile_app.command("show")
def profile_show(
    profile_id: Optional[str] = typer.Argument(
        None,
        metavar="[PROFILE_ID]",
        help="Profile to show. Optional when only one exists.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    profiles_dir: Optional[Path] = typer.Option(
        None, "--profiles-dir", help=_PROFILES_DIR_HELP
    ),
    as_yaml: bool = typer.Option(
        False, "--yaml", help="Print the profile file verbatim instead of a summary."
    ),
) -> None:
    """Show one PdkProfile: identity, decks, corners, supplies, parasitics."""
    from rich import box
    from rich.console import Console
    from rich.table import Table

    loaded = _require_profile(auto_ext_root, config_dir, profiles_dir, profile_id)
    if as_yaml:
        typer.echo(loaded.path.read_text(encoding="utf-8").rstrip("\n"))
        raise typer.Exit(code=0)

    profile = loaded.profile
    console = Console()
    lvs_version, qrc_version = profile.deck_versions
    _print_kv_block(
        console,
        f"Profile {profile.profile_id}",
        [
            ("display name", profile.display_name),
            ("description", profile.description or "-"),
            ("tech name", profile.tech_name or "(auto-derived from env)"),
            ("tech name env", ", ".join(profile.tech_name_env_vars)),
            ("tech library", profile.tech_library_file),
            ("layer map", profile.layer_map),
            ("cdl includes", ", ".join(profile.cdl_include_files) or "-"),
            ("required env", ", ".join(profile.required_env) or "-"),
            ("hand edited", "yes" if profile.hand_edited else "no"),
            ("scanned at", profile.scanned_at.isoformat()),
            ("fingerprint", profile.fingerprint()[:16]),
            ("source", loaded.path),
        ],
    )

    console.print()
    _print_kv_block(
        console,
        "Decks",
        [
            ("lvs dir", profile.lvs_decks.dir_expr or "(not discovered)"),
            ("lvs basename", profile.lvs_decks.basename or "(last path segment)"),
            ("lvs filename", profile.lvs_decks.filename_pattern),
            ("lvs variants", ", ".join(profile.lvs_decks.variant_names) or "(none)"),
            ("lvs default", profile.lvs_decks.default_variant or "-"),
            ("lvs runset", lvs_version or "-"),
            ("qrc dir", profile.qrc.dir_expr or "(not discovered)"),
            ("qrc query cmd", profile.qrc.query_cmd_name),
            ("qrc preserve list", profile.qrc.preserve_cell_list_name),
            ("qrc runset", qrc_version or "-"),
        ],
    )

    if profile.corners:
        table = Table(title="Corners", box=box.SIMPLE_HEAD, pad_edge=False)
        table.add_column("name", style="cyan", no_wrap=True)
        table.add_column("technology_corner")
        table.add_column("default temp (C)")
        table.add_column("aliases", overflow="fold")
        for corner in profile.corners:
            marker = " (default)" if corner.name == profile.default_corner else ""
            table.add_row(
                f"{corner.name}{marker}",
                corner.technology_corner,
                _fmt_value(corner.default_temperature_c),
                ", ".join(corner.aliases) or "-",
            )
        console.print()
        console.print(table)
    else:
        console.print()
        console.print("[yellow]no corner table: nothing can select -technology_corner yet[/]")

    console.print()
    _print_kv_block(
        console,
        "Supplies and parasitics",
        [
            ("power names", ", ".join(profile.power_names) or "(none)"),
            ("ground names", ", ".join(profile.ground_names) or "(none)"),
            ("res", f"{profile.parasitics.res_component} / {profile.parasitics.res_model}"),
            ("cap", f"{profile.parasitics.cap_component} / {profile.parasitics.cap_model}"),
            ("ind", f"{profile.parasitics.ind_component or '-'} / {profile.parasitics.ind_model}"),
            (
                "mutual",
                f"{profile.parasitics.mutual_component or '-'} / {profile.parasitics.mutual_model}",
            ),
        ],
    )

    if profile.extra_paths:
        console.print()
        _print_kv_block(
            console,
            "Extra paths",
            [(key, value) for key, value in sorted(profile.extra_paths.items())],
        )


@profile_app.command("discover")
def profile_discover(
    profile_id: str = typer.Option(
        "default", "--profile-id", help="Id for the drafted profile."
    ),
    display_name: Optional[str] = typer.Option(
        None, "--display-name", help="Defaults to the discovered tech name."
    ),
    raw_calibre: Optional[Path] = typer.Option(
        None,
        "--raw-calibre",
        help="A real Calibre runset export. The only source for the global "
        "power / ground name lists.",
        exists=True,
        dir_okay=False,
        resolve_path=True,
    ),
    no_filesystem: bool = typer.Option(
        False,
        "--no-filesystem",
        help="Read the environment only; list no directory. Useful when the "
        "PDK mount is slow or absent.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    profiles_dir: Optional[Path] = typer.Option(
        None, "--profiles-dir", help=_PROFILES_DIR_HELP
    ),
    write: bool = typer.Option(
        False, "--write", help="Write <profiles-dir>/<profile_id>.yaml."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing profile file, including a hand-edited one.",
    ),
) -> None:
    """Scan this shell for the PDK facts and draft a PdkProfile.

    Every field the scan could not fill is left empty and reported with the
    rule that did not hold plus the command to run next; nothing is guessed.
    Exits 1 when the draft has gaps, so a setup script can branch on it.
    """
    from rich import box
    from rich.console import Console
    from rich.table import Table

    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.profile_discover import (
        SCAN_RULES,
        discover_profile,
        write_profile_yaml,
    )

    raw_text = raw_calibre.read_text(encoding="utf-8", errors="replace") if raw_calibre else None
    result = discover_profile(
        profile_id=profile_id,
        display_name=display_name,
        raw_calibre_text=raw_text,
        use_filesystem=not no_filesystem,
    )
    profile = result.profile
    console = Console()

    _print_kv_block(
        console,
        f"Discovered profile {profile.profile_id}",
        [
            ("display name", profile.display_name),
            ("tech name", profile.tech_name or "(not found)"),
            ("lvs deck dir", profile.lvs_decks.dir_expr or "(not found)"),
            ("lvs variants", ", ".join(profile.lvs_decks.variant_names) or "(none)"),
            ("qrc deck dir", profile.qrc.dir_expr or "(not found)"),
            ("corners", ", ".join(c.name for c in profile.corners) or "(none)"),
            ("power names", str(len(profile.power_names))),
            ("ground names", str(len(profile.ground_names))),
            ("required env", ", ".join(profile.required_env) or "-"),
        ],
    )

    if result.scanned:
        console.print()
        console.print("[bold]Scanned[/]")
        for item in result.scanned:
            console.print(f"  [dim]{item}[/]")

    if result.notes:
        table = Table(title="Gaps", box=box.SIMPLE_HEAD, pad_edge=False)
        table.add_column("field", style="cyan", no_wrap=True)
        table.add_column("rule", no_wrap=True)
        table.add_column("what happened", overflow="fold")
        table.add_column("next step", overflow="fold")
        for note in result.notes:
            table.add_row(note.field, note.rule, note.reason, note.fix_hint)
        console.print()
        console.print(table)
        console.print(
            "[dim]rule text: "
            + "; ".join(f"{r}={SCAN_RULES.get(r, '')[:60]}" for r in sorted({n.rule for n in result.notes}))
            + "[/]"
        )

    directory = _profiles_dir(auto_ext_root, config_dir, profiles_dir)
    path = directory / f"{profile.profile_id}.yaml"
    console.print()
    if not write:
        console.print(f"[yellow]nothing written; re-run with --write to save {path}.[/]")
        raise typer.Exit(code=1 if result.notes else 0)

    if path.exists() and not force:
        typer.secho(
            f"{path} already exists; pass --force to overwrite it "
            f"(a hand-edited profile is worth keeping).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    try:
        write_profile_yaml(path, profile)
    except AutoExtError as exc:
        typer.secho(f"cannot write the profile: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    console.print(f"wrote {path}")
    console.print(f"  [dim]check it with: auto-ext profile health {profile.profile_id}[/]")
    raise typer.Exit(code=1 if result.notes else 0)


@profile_app.command("health")
def profile_health(
    profile_id: Optional[str] = typer.Argument(
        None,
        metavar="[PROFILE_ID]",
        help="Profile to check. Optional when only one exists.",
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    profiles_dir: Optional[Path] = typer.Option(
        None, "--profiles-dir", help=_PROFILES_DIR_HELP
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Dependency-free text (core.health.format_report) instead of tables.",
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the PdkHealthReport as JSON."
    ),
) -> None:
    """Check whether this shell can run: env, decks, corners, tools.

    Exits 0 when a run may start, 1 when a required check failed or could not
    be evaluated. A ``fail`` on an optional check is reported as ``warn`` and
    does not affect the exit code. The result is cached next to the profile as
    ``<profile_id>.health.json``; the checks always re-run here, the cache is
    only refreshed.
    """
    from rich.console import Console

    from auto_ext.core.health import format_report

    loaded = _require_profile(auto_ext_root, config_dir, profiles_dir, profile_id)
    report = _run_health(loaded.profile, path=loaded.path)

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
    elif plain:
        typer.echo(format_report(report))
    else:
        _print_health(Console(), report, profile=loaded.profile, source=loaded.path)
    raise typer.Exit(code=report.exit_code)


# ---- catalog ----------------------------------------------------------------


def _catalog_or_exit():
    from auto_ext.catalog import CatalogError, builtin_catalog

    try:
        return builtin_catalog()
    except CatalogError as exc:
        typer.secho(f"catalog error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)


def _validated_choice(value: Optional[str], valid, label: str) -> Optional[str]:
    """Accept one of ``valid`` (case-insensitively) or exit 2 listing them."""
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered not in valid:
        typer.secho(
            f"unknown {label} {value!r}; valid: {sorted(valid)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    return lowered


#: ``Currently`` values shortened for the listing's ``now`` column. The full
#: value is a mouthful and the column has to survive an 80-column console.
_CURRENTLY_SHORT = {
    "hardcoded_literal": "literal",
    "manifest_knob": "knob",
    "jinja_var": "jinja",
    "config_field": "config",
    "python_argv": "argv",
    "absent": "absent",
}


def _short_targets(option) -> str:
    """The files an option lands in, short enough for a table cell.

    ``quantus.ext.cmd`` -> ``ext.cmd``: the stage is already a separate
    filter, and the full id makes the column wider than an 80-column console.
    A target-less landing site (the strmout argv) is shown as ``<stage> argv``.
    """
    seen: list[str] = []
    for site in option.lands_in:
        if site.target is not None:
            parts = site.target.value.split(".")
            label = ".".join(parts[-2:])
        elif site.stage is not None:
            label = f"{site.stage.value} argv"
        else:
            label = "?"
        if label not in seen:
            seen.append(label)
    return ", ".join(seen)


@catalog_app.command("list")
def catalog_list(
    owner: Optional[str] = typer.Option(
        None,
        "--owner",
        help="recipe | profile | cells | run | resources | fixed — which "
        "object holds the value.",
    ),
    stage: Optional[str] = typer.Option(
        None, "--stage", help="si | strmout | calibre | quantus | jivaro."
    ),
    target: Optional[str] = typer.Option(
        None,
        "--target",
        help="A rendered file: si.env | lvs.qci | quantus.ext.cmd | "
        "quantus.dspf.cmd | jivaro.xml.",
    ),
    section: Optional[str] = typer.Option(
        None, "--section", help="Section within a file; needs --target."
    ),
    search: Optional[str] = typer.Option(
        None,
        "--search",
        help="Substring match over key, template_var, option name and why.",
    ),
    knobs_today: bool = typer.Option(
        False, "--knobs-today", help="Only the values a user can change without editing a template."
    ),
    questions: bool = typer.Option(
        False, "--questions", help="Only rows with an unanswered question."
    ),
    free_input: bool = typer.Option(
        False,
        "--free-input",
        help="Only rows whose value set is a guess, so a UI must offer free text.",
    ),
    unverified_range: bool = typer.Option(
        False, "--unverified-range", help="Only rows carrying an unverified numeric range."
    ),
    limit: int = typer.Option(
        40, "--limit", "-n", min=0, help="Show at most N rows. 0 shows all."
    ),
) -> None:
    """List the built-in parameter catalog.

    One row per value the generated EDA input files contain — not just the
    seven that happen to be knobs today. ``auto-ext catalog show KEY`` prints
    one row in full, including every place it lands and how it is spelled
    there.
    """
    from rich import box
    from rich.console import Console
    from rich.table import Table

    catalog = _catalog_or_exit()
    valid_owners = {o.value for o in {opt.owner for opt in catalog.options}}
    valid_owners |= {"recipe", "profile", "cells", "run", "resources", "fixed"}
    valid_stages = {s.value for spec in catalog.targets for s in (spec.stage,)}
    valid_stages |= {"si", "strmout", "calibre", "quantus", "jivaro"}
    valid_targets = {spec.id.value for spec in catalog.targets}

    owner = _validated_choice(owner, valid_owners, "owner")
    stage = _validated_choice(stage, valid_stages, "stage")
    target = _validated_choice(target, valid_targets, "render target")
    if section is not None and target is None:
        typer.secho("--section needs --target", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    rows = list(catalog.options)
    if owner is not None:
        rows = [o for o in rows if o.owner.value == owner]
    if stage is not None:
        rows = [o for o in rows if stage in [s.value for s in o.stages]]
    if target is not None:
        rows = [o for o in rows if target in [t.value for t in o.targets]]
    if section is not None:
        rows = [
            o
            for o in rows
            if any(s.target is not None and s.target.value == target and s.section == section
                   for s in o.lands_in)
        ]
    if knobs_today:
        rows = [o for o in rows if o.is_knob_today]
    if questions:
        rows = [o for o in rows if o.question]
    if free_input:
        rows = [o for o in rows if o.free_input]
    if unverified_range:
        rows = [o for o in rows if o.range is not None and not o.range_verified]
    if search:
        needle = search.lower()
        rows = [
            o
            for o in rows
            if needle in o.key.lower()
            or needle in o.template_var.lower()
            or needle in (o.context_path or "").lower()
            or needle in o.why.lower()
            or any(needle in s.option.lower() for s in o.lands_in)
        ]

    matched = len(rows)
    shown = rows[:limit] if limit else rows
    console = Console()
    if not shown:
        typer.echo(f"no catalog option matches ({len(catalog.options)} in the catalog)")
        raise typer.Exit(code=0)

    # SIMPLE_HEAD drops the vertical rules: six columns plus a 35-character
    # key do not fit an 80-column console with box borders, and folding every
    # cell makes the listing unreadable.
    table = Table(title="Catalog options", box=box.SIMPLE_HEAD, pad_edge=False)
    # Never fold the key: it is the argument to `catalog show`, and a key
    # broken across two lines with the rest of the row wedged between the
    # halves cannot be copied out of the terminal.
    table.add_column("key", style="cyan", no_wrap=True)
    table.add_column("owner", no_wrap=True)
    table.add_column("type", no_wrap=True)
    table.add_column("default", max_width=14, overflow="ellipsis")
    table.add_column("now", no_wrap=True)
    table.add_column("files", max_width=14, overflow="ellipsis")

    for option in shown:
        table.add_row(
            option.key,
            option.owner.value,
            option.type.value,
            _fmt_value(option.default),
            _CURRENTLY_SHORT.get(option.currently.value, option.currently.value),
            _short_targets(option) or "-",
        )
    console.print(table)

    counts = catalog.owner_counts()
    console.print(
        f"[dim]{len(shown)} of {matched} matching, {len(catalog.options)} in "
        f"catalog {catalog.catalog_version} "
        f"(by owner: {', '.join(f'{k} {v}' for k, v in counts.items())})[/]"
    )
    console.print(
        "[dim]now = how the value reaches the file today: "
        + ", ".join(f"{v}={k}" for k, v in _CURRENTLY_SHORT.items())
        + "[/]"
    )
    console.print(f"[dim]one row in full: auto-ext catalog show {shown[0].key}[/]")


@catalog_app.command("show")
def catalog_show(
    key: str = typer.Argument(..., metavar="KEY", help="A catalog option key."),
) -> None:
    """Show one catalog option: provenance, value set, and every landing site."""
    from rich import box
    from rich.console import Console
    from rich.table import Table

    catalog = _catalog_or_exit()
    try:
        option = catalog.option(key)
    except KeyError:
        near = [o.key for o in catalog.options if key.lower() in o.key.lower()]
        typer.secho(f"no catalog option named {key!r}", fg=typer.colors.RED, err=True)
        if near:
            typer.secho(f"did you mean: {sorted(near)[:8]}", fg=typer.colors.RED, err=True)
        else:
            typer.secho(
                "`auto-ext catalog list --search <text>` finds one.",
                fg=typer.colors.RED,
                err=True,
            )
        raise typer.Exit(code=2)

    console = Console()
    range_text = "-"
    if option.range is not None:
        verified = "verified" if option.range_verified else "unverified"
        range_text = f"[{option.range[0]}, {option.range[1]}] ({verified})"
    _print_kv_block(
        console,
        f"Catalog option {option.key}",
        [
            ("template var", option.template_var),
            ("context path", option.context_path or "-"),
            ("owner", option.owner.value),
            ("type", option.type.value),
            ("default", _fmt_value(option.default)),
            ("choices", _fmt_value(option.choices) if option.choices else "-"),
            ("choice confidence", option.choices_confidence.value),
            ("free input", "yes" if option.free_input else "no"),
            ("range", range_text),
            ("unit", option.unit or "-"),
            ("currently", option.currently.value),
            ("observed", "yes" if option.observed else "no"),
            ("source", option.source_ref or "-"),
            ("recipe field", option.recipe_field_path or "-"),
        ],
    )

    console.print()
    console.print("[bold]Why[/]")
    console.print(f"  {option.why}")
    if option.notes:
        console.print()
        console.print("[bold]Notes[/]")
        console.print(f"  {option.notes}")
    if option.question:
        console.print()
        console.print("[bold yellow]Open question[/]")
        console.print(f"  [yellow]{option.question}[/]")

    if option.lands_in:
        table = Table(title="Lands in", box=box.SIMPLE_HEAD, pad_edge=False)
        table.add_column("target", style="cyan", no_wrap=True)
        table.add_column("section", overflow="fold")
        table.add_column("option", overflow="fold")
        table.add_column("line", justify="right")
        table.add_column("quoting", no_wrap=True)
        table.add_column("layout", no_wrap=True)
        table.add_column("opt", no_wrap=True)
        for site in option.lands_in:
            spec = catalog.target(site.target) if site.target is not None else None
            rule = site.render(spec)
            table.add_row(
                site.target.value if site.target is not None else f"({site.stage.value} argv)",
                site.section,
                site.option,
                str(site.line) if site.line is not None else "-",
                rule.quoting.value,
                rule.layout.value,
                "yes" if rule.optional else "no",
            )
        console.print()
        console.print(table)
    else:
        console.print()
        console.print("[dim]this option is not emitted anywhere today[/]")


# ---- patch ------------------------------------------------------------------


def _find_hunk(recipe: "Recipe", hunk_id: str):
    """Locate one hunk by id or unique id prefix, or exit 2."""
    matches = [
        (patch, hunk)
        for patch in recipe.patches
        for hunk in patch.hunks
        if hunk.id == hunk_id or hunk.id.startswith(hunk_id)
    ]
    exact = [pair for pair in matches if pair[1].id == hunk_id]
    if exact:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        typer.secho(
            f"{hunk_id!r} matches {len(matches)} hunks; be more specific:",
            fg=typer.colors.RED,
            err=True,
        )
        for patch, hunk in matches:
            typer.secho(f"  {hunk.id}  {patch.template_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.secho(
        f"no hunk {hunk_id!r} on recipe {recipe.recipe_id!r}; "
        f"`auto-ext patch list {recipe.recipe_id}` lists them",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


@patch_app.command("list")
def patch_list(
    name: str = typer.Argument(..., metavar="RECIPE", help="recipe_id, or a path to a .yaml."),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
) -> None:
    """List the manual edits stored on a recipe, one row per hunk.

    A patch is stored masked: the values the render substitutes are held as
    ``${slot}`` so the edit follows a cell or a profile change instead of
    breaking on it. Use ``auto-ext patch show`` to read one as a diff.
    """
    from rich import box
    from rich.console import Console
    from rich.table import Table

    loaded = _load_recipe(recipe_search_path(auto_ext_root, config_dir, recipes_dir), name)
    recipe = loaded.recipe
    console = Console()

    if not recipe.patches:
        typer.echo(f"recipe {recipe.recipe_id} has no patch.")
        typer.echo(f"  [source: {loaded.path}]")
        raise typer.Exit(code=0)

    table = Table(
        title=f"Patches on {recipe.recipe_id}", box=box.SIMPLE_HEAD, pad_edge=False
    )
    table.add_column("hunk", style="cyan", no_wrap=True)
    table.add_column("stage", no_wrap=True)
    table.add_column("template", no_wrap=True)
    table.add_column("on", no_wrap=True)
    table.add_column("-/+", justify="right", no_wrap=True)
    table.add_column("intent", overflow="fold")

    for patch in recipe.patches:
        for hunk in patch.hunks:
            table.add_row(
                hunk.id,
                patch.stage.value,
                patch.template_id,
                "yes" if hunk.enabled else "[dim]no[/]",
                f"{len(hunk.before_lines)}/{len(hunk.after_lines)}",
                hunk.intent or "[dim](no intent recorded)[/]",
            )
    console.print(table)

    for patch in recipe.patches:
        console.print(
            f"[dim]{patch.template_id}: captured "
            f"{patch.base.captured_at.isoformat()} against catalog "
            f"{patch.base.catalog_version or '?'} / profile "
            f"{patch.base.profile_id or '?'}, on_fuzzy={patch.on_fuzzy.value}[/]"
        )
    console.print(
        f"[dim]{recipe.manual_edit_count} enabled hunk(s) over "
        f"{len(recipe.patches)} file(s); source {loaded.path}[/]"
    )


@patch_app.command("show")
def patch_show(
    name: str = typer.Argument(..., metavar="RECIPE", help="recipe_id, or a path to a .yaml."),
    hunk_id: str = typer.Argument(
        ..., metavar="HUNK_ID", help="A hunk id, or any unique prefix of one."
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
    context: int = typer.Option(
        0, "--context", "-C", min=0, help="Diff context lines to show."
    ),
) -> None:
    """Show one stored hunk as a unified diff, in its masked form.

    ``${name}`` in the output is a slot: the value the renderer fills in.
    That is why the edit survives a cell swap — the diff is not anchored to
    the substituted text.
    """
    from rich.console import Console

    from auto_ext.core.patch import render_hunk_as_udiff

    loaded = _load_recipe(recipe_search_path(auto_ext_root, config_dir, recipes_dir), name)
    patch, hunk = _find_hunk(loaded.recipe, hunk_id)
    console = Console()

    _print_kv_block(
        console,
        f"Hunk {hunk.id}",
        [
            ("recipe", loaded.recipe.recipe_id),
            ("stage", patch.stage.value),
            ("template", patch.template_id),
            ("enabled", "yes" if hunk.enabled else "no"),
            ("intent", hunk.intent or "-"),
            ("anchored", f"head={hunk.anchored_at_head} tail={hunk.anchored_at_tail}"),
            (
                "occurrence",
                f"{hunk.occurrence_index}/{hunk.occurrence_count}"
                if hunk.occurrence_count is not None
                else "-",
            ),
            ("recorded start", hunk.recorded_start),
            ("insertion", "yes" if hunk.is_insertion else "no"),
            ("on fuzzy", patch.on_fuzzy.value),
            ("captured at", patch.base.captured_at.isoformat()),
            ("catalog", patch.base.catalog_version or "-"),
            ("profile", patch.base.profile_id or "-"),
        ],
    )

    console.print()
    console.print("[bold]Diff (masked)[/]")
    diff = render_hunk_as_udiff(hunk, {}, label=f" {patch.template_id}", context=context)
    for line in diff.splitlines():
        if line.startswith("+"):
            console.print(f"[green]{line}[/]", highlight=False)
        elif line.startswith("-"):
            console.print(f"[red]{line}[/]", highlight=False)
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/]", highlight=False)
        else:
            console.print(line, highlight=False)

    if hunk.captured_values:
        console.print()
        _print_kv_block(
            console,
            "Slot values when this was captured",
            sorted(hunk.captured_values.items()),
        )


@patch_app.command("drop")
def patch_drop(
    name: str = typer.Argument(..., metavar="RECIPE", help="recipe_id, or a path to a .yaml."),
    hunk_id: str = typer.Argument(
        ..., metavar="HUNK_ID", help="A hunk id, or any unique prefix of one."
    ),
    auto_ext_root: Optional[Path] = typer.Option(
        None, "--auto-ext-root", help=_PROFILE_ROOT_HELP
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
    recipes_dir: Optional[Path] = typer.Option(
        None, "--recipes-dir", help=_RECIPES_DIR_HELP
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Actually remove it. Without this, drop only reports what it would remove.",
    ),
) -> None:
    """Remove one stored hunk from a recipe.

    A patch left with no hunks is removed with it — an empty patch record
    still pins a base fingerprint, and keeping one around would make a
    later capture look like a re-capture of an edit nobody made.
    """
    from pydantic import ValidationError

    from auto_ext.core.patch import render_hunk_as_udiff
    from auto_ext.model.common import utcnow
    from auto_ext.model.recipe import Recipe, load_recipe_with_raw, save_recipe

    loaded = _load_recipe(recipe_search_path(auto_ext_root, config_dir, recipes_dir), name)
    recipe, raw = load_recipe_with_raw(loaded.path)
    patch, hunk = _find_hunk(recipe, hunk_id)

    typer.echo(f"{'removing' if yes else 'would remove'} hunk {hunk.id} from {patch.template_id}:")
    if hunk.intent:
        typer.echo(f"  intent: {hunk.intent}")
    for line in render_hunk_as_udiff(hunk, {}).splitlines():
        typer.echo(f"  {line}")

    if not yes:
        typer.secho("nothing was removed; re-run with --yes.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    payload = recipe.model_dump(mode="json")
    remaining = []
    for entry in payload.get("patches", []):
        if entry["template_id"] != patch.template_id or entry["stage"] != patch.stage.value:
            remaining.append(entry)
            continue
        entry["hunks"] = [h for h in entry["hunks"] if h["id"] != hunk.id]
        if entry["hunks"]:
            remaining.append(entry)
    payload["patches"] = remaining
    payload["updated_at"] = utcnow().isoformat()

    try:
        updated = Recipe.model_validate(payload)
    except ValidationError as exc:
        typer.secho(f"the result is not a valid recipe:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    save_recipe(updated, loaded.path, raw=raw)
    typer.echo(
        f"wrote {loaded.path} ({updated.manual_edit_count} enabled hunk(s) left "
        f"over {len(updated.patches)} file(s))"
    )
