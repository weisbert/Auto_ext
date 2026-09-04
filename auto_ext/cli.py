"""Typer CLI entry point.

Live subcommands:

- ``version`` — prints the package version (Phase 1).
- ``run`` — loads ``workspace.yaml`` + ``cells.yaml`` + the named recipe and
  drives :func:`auto_ext.core.runner.run_tasks`.
- ``runs list / show / prune`` — the run history under
  ``<auto-ext-root>/runs/``: what ran, how long each stage took, what LVS
  said, how that compares with the previous run of the same cell, and what
  to try next when something failed.
- ``check-env`` — env-var resolution plus, when a PdkProfile is in play,
  the full health report. A thin wrapper over ``profile health``.
- ``import`` — turn a raw EDA export into a parameterised ``.j2`` with
  identity substitutions pre-applied. A catalog-building tool, not a daily
  one.
- ``recipe list / show / new / set`` — the Recipe library
  (:mod:`auto_ext.model.recipe`): one portable extraction configuration
  per YAML file, found on the search path documented in
  :func:`recipe_search_path`.
- ``recipe import`` — the user's own EDA files (a ``.cmd`` saved out of the
  Quantus GUI, a colleague's ``.qci``, a hand-kept ``si.env``) become one
  recipe, via :func:`auto_ext.core.recipe_import.import_recipe`. A dry run
  until ``--write``; the four-section report is the product.
- ``recipe export`` — one recipe plus its manual edits as a single file to
  send to a colleague, with the catalog version its hunks were captured
  against recorded in the header.
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

One render path. ``run`` needs a ``--recipe`` and the ``--profile`` it
depends on; ``auto_ext.core.runner`` then renders every stage through the
catalog, with values from the Recipe and the PdkProfile and manual edits from
``Recipe.patches``. This module resolves the two objects from disk, checks the
profile's health before anything starts, and hands them over; it re-implements
none of that logic.

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
        help="Directory containing workspace.yaml + cells.yaml.",
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
    layout_out: Optional[str] = typer.Option(
        None,
        "--layout-out",
        help="Export a SECOND, standalone GDS to this path, for software "
        "outside this flow. Requires --stage strmout: the LVS layout file is "
        "never moved. Accepts ${ENV} and {cell}/{library}; {cell} is required "
        "when more than one cell is selected.",
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
    recipe: Optional[str] = typer.Option(
        None,
        "--recipe",
        help="Which extraction configuration to render. Required. "
        "`auto-ext recipe list` shows what is on the search path.",
    ),
    profile: Optional[str] = typer.Option(
        None,
        "--profile",
        help="The PdkProfile supplying the corner literals, deck paths and "
        "supply-name tables the recipe deliberately does not carry. Required; "
        "omit only when exactly one profile exists under "
        "<root>/config/profiles/, which is then selected automatically. Its "
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

    Every stage renders through :mod:`auto_ext.core.render`: templates from the
    catalog, values from the ``--recipe`` and the ``--profile``, manual edits
    from ``Recipe.patches``. The recipe also owns the stage set (intersected
    with ``--stage``), whether jivaro runs, and ``continue_on_lvs_fail``;
    ``--continue-on-lvs-fail`` overrides the last of those for one invocation.

    ``--config-dir`` holds ``workspace.yaml`` (where the Cadence work lands)
    and ``cells.yaml`` (the DUT table). A directory still holding the v1
    ``project.yaml`` + ``tasks.yaml`` pair is reported with the migration
    command rather than half-read.

    Press Ctrl-C once to request a graceful cancel: the in-flight
    subprocess is sent SIGTERM (10s grace) then SIGKILL; remaining
    stages / tasks are skipped; the summary table still prints.
    """
    import signal

    from rich.console import Console

    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.progress import CancelToken, NullReporter, ProgressReporter
    from auto_ext.core.runner import STAGE_ORDER, run_tasks

    try:
        project, tasks = _load_run_config(config_dir)
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

    root = (auto_ext_root or config_dir.parent).resolve()
    wa = (workarea or root.parent).resolve()

    if recipe is None:
        typer.secho(
            "--recipe is required: a run renders from the catalog, and the "
            "recipe is what says which targets and which values. "
            "`auto-ext recipe list` shows what is on the search path.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    directory = _profiles_dir(auto_ext_root, config_dir, profiles_dir)
    selected_profile = profile or (
        _sole_profile_id(directory) if directory.is_dir() else None
    )
    if selected_profile is None:
        known = _profile_ids(directory) if directory.is_dir() else []
        detail = (
            f"name one with --profile: {known} live under {directory}"
            if known
            else f"no profile under {directory}. Run "
            f"`auto-ext profile discover --write` to draft one."
        )
        typer.secho(
            "--profile is required: the corner literals, the LVS deck "
            "directory and the supply-name tables are process facts the "
            f"recipe deliberately does not carry. {detail}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    resource_obj: Optional["ResourceProfile"] = None

    # Resolve both objects before checking anything: a mistyped recipe name is
    # cheap to report and the health report is not, and being told to pass
    # --no-health-check in order to discover a typo would be absurd.
    loaded_recipe = _load_recipe(
        recipe_search_path(auto_ext_root, config_dir, recipes_dir), recipe
    )
    recipe_obj: "Recipe" = loaded_recipe.recipe
    if continue_on_lvs_fail:
        # The runner reads recipe.policy, so the flag has to land there or it
        # would do nothing.
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

    loaded_profile = _load_profile(directory, selected_profile)
    profile_obj: "PdkProfile" = loaded_profile.profile
    typer.echo(
        f"profile {profile_obj.profile_id} ({profile_obj.display_name}) "
        f"from {loaded_profile.path}"
    )
    report = _run_health(profile_obj)
    if not report.can_run:
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
            max_workers=jobs if jobs >= 2 else None,
            layout_export_path=layout_out,
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


@app.command("export-gds")
def export_gds(
    config_dir: Path = typer.Option(
        ...,
        "--config-dir",
        help="Directory containing workspace.yaml + cells.yaml.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    to: str = typer.Option(
        ...,
        "--to",
        help="Destination for the exported GDS. Accepts ${ENV} and "
        "{cell}/{library}; {cell} is required when exporting more than one "
        "cell, or each would overwrite the last.",
    ),
    task: Optional[list[str]] = typer.Option(
        None,
        "--task",
        help="Filter to specific task_id(s). Repeat for several. Default: "
        "every enabled cell.",
    ),
    recipe: Optional[str] = typer.Option(None, "--recipe"),
    profile: Optional[str] = typer.Option(None, "--profile"),
    auto_ext_root: Optional[Path] = typer.Option(None, "--auto-ext-root"),
    workarea: Optional[Path] = typer.Option(None, "--workarea"),
    recipes_dir: Optional[Path] = typer.Option(None, "--recipes-dir", help=_RECIPES_DIR_HELP),
    profiles_dir: Optional[Path] = typer.Option(None, "--profiles-dir", help=_PROFILES_DIR_HELP),
    resources: Optional[Path] = typer.Option(None, "--resources"),
    health_check: bool = typer.Option(True, "--health-check/--no-health-check"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the argv without spawning strmout."
    ),
    no_progress: bool = typer.Option(False, "--no-progress"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Export a standalone GDS for software outside this flow.

    Runs the ``strmout`` stage, and only that stage, writing to ``--to``.

    This does NOT relocate the layout file Calibre reads. That file is a
    producer/consumer contract -- strmout writes it, the LVS runset names the
    same value as ``*lvsLayoutPaths`` -- so it stays exactly where it is and
    this writes a second, separate file beside it. That is also why the stage
    set is pinned here rather than offered: there is no combination of flags
    that can point LVS at a file nobody wrote.

    Equivalent to ``auto-ext run --stage strmout --layout-out <path>``; this
    is the discoverable spelling of it.
    """
    run(
        config_dir=config_dir,
        task=task,
        stage="strmout",
        continue_on_lvs_fail=False,
        layout_out=to,
        dry_run=dry_run,
        auto_ext_root=auto_ext_root,
        workarea=workarea,
        recipe=recipe,
        profile=profile,
        resources=resources,
        recipes_dir=recipes_dir,
        profiles_dir=profiles_dir,
        health_check=health_check,
        jobs=1,
        no_progress=no_progress,
        verbose=verbose,
    )


@app.command()
def gui(
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        help="Config directory to preload: workspace.yaml + cells.yaml.",
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
        help="Target .j2 path. A review report is written next to it.",
        resolve_path=True,
    ),
    cell: Optional[str] = typer.Option(None, "--cell"),
    library: Optional[str] = typer.Option(None, "--library"),
    lvs_layout_view: Optional[str] = typer.Option(None, "--lvs-layout-view"),
    lvs_source_view: Optional[str] = typer.Option(None, "--lvs-source-view"),
) -> None:
    """Parameterise a raw EDA export into a ``.j2`` catalog template.

    Identity values (cell / library / views / ground_net / out_file) are
    auto-inferred from recognised per-format keys and substituted with
    ``[[...]]`` placeholders, and the Calibre importer additionally turns the
    ``connect_by_name`` line and the LVS deck variant into their catalog
    references. Every other literal is left as-is: which of them a user may
    change is decided by adding a row to ``auto_ext/catalog/options.yaml``,
    not by a sidecar next to the template.

    A catalog-building tool. The review report next to the output lists what
    was substituted and what was left hardcoded, which is the working list for
    that catalog work.
    """
    from auto_ext.core.importer import (
        Identity,
        ImportError as CoreImportError,
        import_template,
    )
    from auto_ext.core.io_utils import backup_if_exists

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

    backup_if_exists(output)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result.template_body, encoding="utf-8")

    review_path = output.with_name(output.name + ".review.md")
    backup_if_exists(review_path)
    review_path.write_text(_build_review_report(result), encoding="utf-8")

    typer.echo(f"wrote template    : {output}")
    typer.echo(f"wrote review      : {review_path}")
    for name, found in sorted(result.detected.items()):
        typer.echo(
            f"parameterised     : {name} = {found.value!r} "
            f"(catalog row `{found.catalog_key}`)"
        )
    if result.candidates:
        typer.echo(
            f"\n{len(result.candidates)} further literal(s) look tunable; "
            f"see {review_path.name}."
        )


# ---- import helpers --------------------------------------------------------


def _build_review_report(result) -> str:
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
    lines.append("## Parameterised")
    if result.detected:
        for name, found in sorted(result.detected.items()):
            lines.append(
                f"- `{name}` = `{found.value!r}` "
                f"(catalog row `{found.catalog_key}`)"
            )
    else:
        lines.append("Identity substitutions only.")
    lines.append("")
    lines.append("## Literals that look tunable")
    if result.candidates:
        for cand in result.candidates:
            lines.append(
                f"- line {cand.line}: `{cand.key}` = `{cand.default}` "
                f"(suggested name: `{cand.suggested_name}`)"
            )
        lines.append("")
        lines.append(
            "None of these is settable yet. To make one settable, add a row to "
            "`auto_ext/catalog/options.yaml` naming the field that owns it and "
            "where it lands, then reference it from the template."
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
    lines.append("## Next steps")
    lines.append("- `auto-ext catalog list` — what the generated file already models")
    lines.append("- add a catalog row for anything above that should be settable")
    lines.append("")
    return "\n".join(lines)


@app.command("check-env")
def check_env(
    config_dir: Optional[Path] = typer.Option(
        None,
        "--config-dir",
        help="Config directory; its `profiles/` subdirectory is where the "
        "profile is looked up when --profile is omitted.",
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

    A thin wrapper over ``auto-ext profile health``. The PdkProfile is named
    with ``--profile`` or is the only one under ``<root>/config/profiles/``;
    the full health report prints and the exit code is
    :attr:`~auto_ext.model.pdk.PdkHealthReport.exit_code`.

    There is no profile-less mode. The env vars a run needs are the profile's
    path expressions plus the catalog's templates, so without a profile there
    is no question to answer — which is why an unmigrated tree gets pointed at
    ``profile discover`` / ``migrate`` instead of a partial answer.
    """
    from rich.console import Console

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

    known = _profile_ids(directory) if directory.is_dir() else []
    detail = (
        f"name one with --profile: {known} live under {directory}"
        if known
        else f"no profile under {directory}. Run "
        f"`auto-ext profile discover --write` to draft one from a machine "
        f"scan, or `auto-ext migrate --config-dir <dir> --write` if this "
        f"tree still has a v1 project.yaml."
    )
    typer.secho(f"nothing to check: {detail}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2)


def _load_run_config(config_dir: Path):
    """Load the run's workspace + DUT table, or say how to migrate.

    ``workspace.yaml`` + ``cells.yaml`` is the live pair. A directory holding
    only the v1 ``project.yaml`` + ``tasks.yaml`` is routed through
    :func:`auto_ext.core.config.load_project`, whose refusal names the retired
    keys and the migration command — which is a better answer than "file not
    found: workspace.yaml".
    """
    from auto_ext.core.config import (
        load_project,
        load_tasks,
        load_v2_config,
        tasks_from_cells,
    )
    from auto_ext.core.errors import ConfigError
    from auto_ext.model.workspace import WORKSPACE_FILENAME

    if (config_dir / WORKSPACE_FILENAME).is_file():
        project, book = load_v2_config(config_dir)
        return project, tasks_from_cells(book)

    if (config_dir / "project.yaml").is_file():
        project = load_project(config_dir / "project.yaml")
        return project, load_tasks(config_dir / "tasks.yaml", project=project)

    raise ConfigError(
        f"{config_dir} holds neither {WORKSPACE_FILENAME} nor project.yaml. "
        f"`auto-ext migrate --config-dir <dir> --write` converts a v1 tree; "
        f"`auto-ext init-project` starts a new one."
    )


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
    import logging

    from pydantic import ValidationError
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    from auto_ext.model.recipe import ResourceProfile, upgrade_retired_resource_fields

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
    retired = upgrade_retired_resource_fields(data)
    if retired:
        # Every deployed copy of this file carries ``max_workers``: migrate
        # and the new-project wizard both wrote it. Refusing the file over a
        # number nothing ever read would be the 2026-08-28 failure mode again.
        logging.getLogger(__name__).info(
            "%s: dropped %s -- the run bar's jobs box and --jobs own "
            "parallelism (2026-09-04 ownership ruling)",
            path,
            ", ".join(f"{key}: {value}" for key, value in retired.items()),
        )
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

    if report.shipped_fallbacks:
        # Louder than "needs confirmation": those are the user's own values
        # nobody has checked. These are not the user's values at all.
        console.print()
        console.print("[bold red]NOT from your config -- seeded from the shipped profile[/]")
        console.print(
            "[dim]  A legacy config has no table for these, so there was nothing to "
            "migrate. Check them before the first real run.[/]"
        )
        for field_name in report.shipped_fallbacks:
            console.print(f"  [red]profile.{field_name}[/]")

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
            table.add_row(recipe_id, f"[red]unreadable: {exc}[/]", "", "", "", origin)
            continue
        # No stages column since 2026-09-04: which stages run is a property
        # of the dispatch, not of the recipe, so a per-recipe answer here
        # would be a second copy of the run bar's / --stage's decision.
        table.add_row(
            recipe_id,
            recipe.name,
            recipe.version,
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
            ("emits", ", ".join(k.value for k in recipe.output.emit)),
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


# ---- recipe import / export -------------------------------------------------

_TARGET_FLAG_HELP = (
    "Name the render target of one input file, for a file whose content "
    "cannot decide. `--target quantus.dspf.cmd` when exactly one file is "
    "being imported, `--target <file>=<target>` otherwise. Repeatable."
)

_IMPORT_FILE_HELP = (
    "An EDA file to import. Repeatable, and interchangeable with the "
    "positional form."
)


def _match_inputs(where: str, files: list[Path]) -> list[Path]:
    """Every input file ``where`` could mean: full path, or bare file name."""
    candidate = Path(where).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError:  # pragma: no cover - only a malformed path reaches this
        resolved = candidate
    hits: list[Path] = []
    for path in files:
        if path in hits:
            continue
        if path == resolved or str(path) == where or path.name == candidate.name:
            hits.append(path)
    return hits


def _parse_target_flags(values: list[str], files: list[Path], valid) -> dict[Path, str]:
    """Bind every ``--target`` to one input file, or exit 2 explaining why not.

    Split on the *last* ``=``: the left side is a path, and on Windows it
    starts with a drive letter, so splitting on the first separator would tear
    ``C:\\pdk\\ext.cmd=quantus.ext.cmd`` in the wrong place.
    """
    forced: dict[Path, str] = {}
    for raw in values:
        if "=" in raw:
            where, _, name = raw.rpartition("=")
        elif len(files) == 1:
            where, name = str(files[0]), raw
        else:
            typer.secho(
                f"--target {raw!r}: with more than one file, say which file it "
                f"is about: --target <file>={raw}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        name = name.strip()
        if name not in valid:
            typer.secho(
                f"unknown --target {name!r}; valid: {sorted(valid)}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        hits = _match_inputs(where.strip(), files)
        if not hits:
            typer.secho(
                f"--target {raw!r}: no input file matches {where!r}. Files:",
                fg=typer.colors.RED,
                err=True,
            )
            for path in files:
                typer.secho(f"  {path}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        if len(hits) > 1:
            typer.secho(
                f"--target {raw!r}: {where!r} matches {len(hits)} input files; "
                f"give the full path. Matches: {[str(p) for p in hits]}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        chosen = hits[0]
        if chosen in forced and forced[chosen] != name:
            typer.secho(
                f"--target names {chosen} twice, as {forced[chosen]} and {name}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        forced[chosen] = name
    return forced


def _read_import_source(path: Path, target):
    """One input file, newlines untouched.

    ``ImportSource.from_path`` reads through Python's universal-newline
    translation, which turns a CRLF file into an LF string before anything can
    notice -- and then the report's newline column says LF about a file that
    came off a Windows share. ``import_recipe`` normalises CRLF itself, so
    handing it the bytes as they are costs nothing and keeps that column true.
    """
    from auto_ext.core.recipe_import import ImportSource, RecipeImportError

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read()
    except UnicodeDecodeError as exc:
        raise RecipeImportError(f"{path} is not UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise RecipeImportError(f"cannot read {path}: {exc}") from exc
    return ImportSource(label=str(path), text=text, target=target)


def _parse_env_flags(values: list[str]) -> dict[str, str]:
    """``NAME=VALUE`` pairs for ``--env``, or exit 2."""
    overrides: dict[str, str] = {}
    for item in values:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            typer.secho(
                f"--env {item!r}: expected NAME=VALUE",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        overrides[name.strip()] = value
    return overrides


def _import_env(profile, catalog, overrides: dict[str, str]) -> dict[str, str]:
    """Env values the *profile* needs that the imported files cannot supply.

    A var the importer can solve out of the user's own file is never taken from
    this shell: doing so would put this machine's paths into the baseline, and
    from there into a stored hunk that travels with the recipe. Two shapes
    count as solvable and :func:`env_vars_solvable_from_files` knows both -- a
    reference the template still spells out, and a reference in a profile path
    expression whose rendered value a template now writes as a ``[[var]]``. A
    var only the profile mentions and no file shows has no such source, so the
    shell is the only place left to read it. ``--env`` overrides both.
    """
    import os

    from auto_ext.core.recipe_import import env_vars_solvable_from_files
    from auto_ext.core.render import required_env_vars

    env: dict[str, str] = {}
    if profile is not None:
        solvable = env_vars_solvable_from_files(profile, catalog=catalog)
        for var in sorted(required_env_vars(profile, catalog=catalog) - solvable):
            if var in os.environ:
                env[var] = os.environ[var]
    env.update(overrides)
    return env


def _derived_recipe_id(sources, catalog) -> str:
    """A recipe_id read out of the files, for an import without ``--as``.

    The cell name is what a user calls these files in their head, so that is
    what the recipe is named after. It comes from the same variable solver the
    import itself uses: the shipped template's text at a landing site, matched
    against the user's. When no file carries a cell -- a lone ``dspf.cmd`` does
    not -- the targets name the recipe instead, which is at least true.
    """
    from auto_ext.core.recipe_import import detect_target, solve_template_vars
    from auto_ext.core.render import template_path_for
    from auto_ext.model.common import slugify

    cell: Optional[str] = None
    seen: list[str] = []
    for source in sources:
        # ``import_recipe`` normalises CRLF before it looks at anything; this
        # has to see the same text or it would recognise a Windows file less
        # well than the import that follows it does.
        text = source.text.replace("\r\n", "\n")
        target = source.target or detect_target(text, label=source.label, catalog=catalog)
        if target.value not in seen:
            seen.append(target.value)
        if cell is not None:
            continue
        spec = catalog.target(target)
        try:
            pattern = template_path_for(spec).read_text(encoding="utf-8")
        except OSError:  # pragma: no cover - import_recipe reports this properly
            continue
        solved = solve_template_vars(pattern, text, target=target, catalog=catalog)
        cell = solved.values.get("var:cell")
    if cell:
        return slugify(f"imported-{cell}", max_len=64)
    return slugify("imported-" + "-".join(seen), max_len=64)


def _same_value(mine, theirs) -> bool:
    """Is a value read from a file the catalog default, spelling aside?"""
    if isinstance(mine, bool) != isinstance(theirs, bool):
        return False
    if isinstance(mine, (list, tuple)) and isinstance(theirs, (list, tuple)):
        return list(mine) == list(theirs)
    if isinstance(mine, float) or isinstance(theirs, float):
        try:
            return float(mine) == float(theirs)
        except (TypeError, ValueError):
            return False
    return mine == theirs


def _print_import_report(
    console,
    result,
    *,
    catalog,
    write: bool,
    target_dir: Path,
    written: Optional[Path],
    show_defaults: bool,
) -> None:
    """Render a :class:`auto_ext.core.recipe_import.RecipeImportResult`.

    Four sections, and the order is the argument: what the recipe now holds,
    what had to become a manual edit, what stayed at the catalog default, and
    what a human should look at. A value that appears in none of the four has
    been lost, which is the same contract ``migrate``'s disposition table
    carries.
    """
    from rich import box
    from rich.table import Table

    dut = result.dut
    if result.clean_roundtrip:
        trip = "every target re-renders byte for byte"
    else:
        differ = [
            target.value for target, hop in result.roundtrip.items() if not hop.identical
        ]
        trip = f"NOT clean: {', '.join(differ)}"
    _print_kv_block(
        console,
        "Recipe import",
        [
            ("recipe id", result.recipe.recipe_id),
            ("name", result.recipe.name),
            ("files", len(result.sources)),
            ("targets", ", ".join(t.value for t in result.targets)),
            ("emits", ", ".join(k.value for k in result.recipe.output.emit)),
            ("catalog", result.catalog_version),
            (
                "profile",
                f"derived from the files ({result.profile.profile_id})"
                if result.derived_profile
                else f"{result.profile.profile_id} ({result.profile.display_name})",
            ),
            (
                "dut",
                f"{dut.library}/{dut.cell} "
                f"({dut.layout_view} vs {dut.source_view}, ground {dut.ground_net})",
            ),
            # Names only. An env value is a machine path, and printing it in a
            # report somebody pastes into a ticket is how one leaks.
            ("env bound", ", ".join(sorted(result.resolved_env)) or "-"),
            ("into recipe", f"{result.applied_count} value(s)"),
            (
                "manual edits",
                f"{result.hunk_count} hunk(s), "
                f"{result.unmodelled_ratio:.0%} of the imported lines",
            ),
            ("round trip", trip),
            ("mode", "write" if write else "report only"),
        ],
    )

    table = Table(title="Files", box=box.SIMPLE_HEAD, pad_edge=False, expand=True)
    table.add_column("file", style="cyan", overflow="fold", ratio=3)
    table.add_column("target", no_wrap=True)
    table.add_column("lines", justify="right", no_wrap=True)
    table.add_column("target from", no_wrap=True)
    table.add_column("newline", no_wrap=True)
    for imported in result.sources:
        table.add_row(
            imported.label,
            imported.target.value,
            str(imported.line_count),
            "--target" if imported.forced else "content",
            "CRLF, read as LF" if imported.crlf else "LF",
        )
    console.print()
    console.print(table)

    # 1. what the recipe and the profile now hold -----------------------------
    applied = [row for row in result.mapped if row.applied_to]
    table = Table(
        title=f"Read into the recipe or the profile ({len(applied)})",
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        expand=True,
    )
    table.add_column("key", style="cyan", overflow="fold", ratio=2)
    table.add_column("value", overflow="fold", ratio=3)
    table.add_column("owner", no_wrap=True)
    table.add_column("landed in", overflow="fold", ratio=3)
    table.add_column("read from", overflow="fold", ratio=3)
    for row in sorted(applied, key=lambda item: item.key):
        table.add_row(
            row.key,
            _fmt_value(row.value),
            _fmt_value(row.owner),
            row.landed_in,
            f"{row.site.describe()} [{row.origin}]",
        )
    console.print()
    if applied:
        console.print(table)
    else:
        console.print("[bold]Read into the recipe or the profile (0)[/]")
        console.print(
            "[yellow]nothing the catalog models could be read; the whole input "
            "is below as manual edits.[/]"
        )

    # 2. what the catalog does not model -------------------------------------
    table = Table(
        title=f"Kept as manual edits ({result.hunk_count} hunk(s))",
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        expand=True,
    )
    table.add_column("target", style="cyan", no_wrap=True)
    table.add_column("at line", justify="right", no_wrap=True)
    table.add_column("-/+", justify="right", no_wrap=True)
    table.add_column("stored as (masked)", overflow="fold", ratio=4)
    for hunk in result.as_patch:
        table.add_row(
            hunk.target.value,
            str(hunk.at_line),
            f"-{hunk.removed}/+{hunk.added}",
            hunk.summary,
        )
    console.print()
    if not result.as_patch:
        console.print("[bold]Kept as manual edits (0 hunk(s))[/]")
        console.print("[dim]nothing: the catalog explains every line of every file.[/]")
    else:
        console.print(table)
        console.print(
            "[dim]masked at every landing site the catalog models, so a cell "
            "name inside a hunk follows the next DUT; the rest of the line is "
            "kept verbatim. `auto-ext patch show` prints one in full.[/]"
        )

    # 3. what stayed at the catalog default ----------------------------------
    rows: list[tuple[str, str, str, str]] = []
    agreed = 0
    for row in sorted(result.mapped, key=lambda item: item.key):
        if row.applied_to:
            continue
        default = catalog.option(row.key).default
        if _same_value(row.value, default) and not show_defaults:
            agreed += 1
            continue
        rows.append((row.key, _fmt_value(row.value), _fmt_value(default), row.note))
    # ``left_at_default`` and not ``unread``: a key the literal reader could
    # not read but something else did -- ``lvs_connect_by_name`` comes from
    # whether its line exists at all -- is in the recipe, and listing it here
    # as well would contradict the section above it. The filter lives on the
    # result so this table and the GUI's third section give one answer.
    for key, reason in sorted(result.left_at_default.items()):
        rows.append(
            (key, "(not readable)", _fmt_value(catalog.option(key).default), reason)
        )
    table = Table(
        title=f"Left at the catalog default ({len(rows)} shown)",
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        expand=True,
    )
    table.add_column("key", style="cyan", overflow="fold", ratio=2)
    table.add_column("in your file", overflow="fold", ratio=3)
    table.add_column("catalog default", overflow="fold", ratio=3)
    table.add_column("why not in the recipe", overflow="fold", ratio=4)
    for key, mine, theirs, why in rows:
        table.add_row(key, mine, theirs, why)
    console.print()
    if rows:
        console.print(table)
    else:
        console.print("[bold]Left at the catalog default (0 shown)[/]")
    if agreed:
        console.print(
            f"[dim]{agreed} further value(s) were read and already match the "
            f"catalog default; --show-defaults lists them.[/]"
        )
    if rows:
        console.print(
            "[dim]a value here that differs from the default is not lost: the "
            "difference is one of the hunks above.[/]"
        )

    # 4. what a human has to look at -----------------------------------------
    if result.warnings:
        console.print()
        console.print("[bold yellow]Warnings[/]")
        for warning in result.warnings:
            console.print(f"  [yellow]{warning}[/]")

    console.print()
    if written is not None:
        console.print(f"wrote {written}")
        console.print(
            f"[dim]inspect with: auto-ext recipe show {result.recipe.recipe_id}[/]"
        )
    else:
        from auto_ext.model.recipe import recipe_filename

        console.print(
            f"[yellow]nothing written; re-run with --write to save "
            f"{target_dir / recipe_filename(result.recipe)}.[/]"
        )
    if result.derived_profile:
        console.print(
            "[dim]the baseline profile was derived from these files, not "
            "discovered: it exists so the import had something to render "
            "against. Pass --profile <id> to import against a real one.[/]"
        )


@recipe_app.command("import")
def recipe_import(
    files: Optional[list[Path]] = typer.Argument(
        None,
        metavar="[FILE]...",
        help="EDA files to import: any subset of the five render targets.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    extra_files: Optional[list[Path]] = typer.Option(
        None,
        "--file",
        "-f",
        help=_IMPORT_FILE_HELP,
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    as_name: Optional[str] = typer.Option(
        None,
        "--as",
        metavar="NAME",
        help="recipe_id for the imported recipe. Without it, a name is derived "
        "from the cell the files are about.",
    ),
    display_name: Optional[str] = typer.Option(
        None, "--name", help="Display name. Defaults to `Imported <recipe_id>`."
    ),
    target: Optional[list[str]] = typer.Option(None, "--target", help=_TARGET_FLAG_HELP),
    profile_id: Optional[str] = typer.Option(
        None,
        "--profile",
        help="Import against this PdkProfile instead of one derived from the "
        "files. The profile decides the baseline the manual edits are a diff "
        "against, so a real one produces fewer of them.",
    ),
    profiles_dir: Optional[Path] = typer.Option(
        None, "--profiles-dir", help=_PROFILES_DIR_HELP
    ),
    env: Optional[list[str]] = typer.Option(
        None,
        "--env",
        metavar="NAME=VALUE",
        help="Bind an environment variable for the baseline render, "
        "overriding both the shell and anything read out of the files. "
        "Repeatable.",
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
    warn_ratio: float = typer.Option(
        0.25,
        "--warn-ratio",
        min=0.0,
        max=1.0,
        help="Warn when more than this fraction of the imported lines had to "
        "become manual edits. Default 0.25.",
    ),
    show_defaults: bool = typer.Option(
        False,
        "--show-defaults",
        help="List every value that matched the catalog default too, not just "
        "the count of them.",
    ),
    write: bool = typer.Option(
        False,
        "--write",
        help="Actually write the recipe. Without it, nothing is saved.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing recipe file."),
) -> None:
    """Turn EDA files you already have into one recipe.

    A ``.cmd`` saved out of the Quantus GUI, a ``.qci`` a colleague sent, an
    ``si.env`` carried between projects: name any subset of the five render
    targets and they become a Recipe this tool can run. Each file's target is
    decided by its content, never by its name; ``--target`` is for the file
    whose content cannot decide.

    Nothing is written without ``--write``, because the report is the point. It
    has four sections and every value the files contain appears in exactly one
    of them: read into the recipe, kept as a manual edit, left at the catalog
    default, or in the warnings. A value in none of them would be a lost value,
    which is the bug this listing exists to make impossible.

    Exits 1 when there are warnings (a human should look), 0 otherwise, and 2
    when the files cannot become a recipe at all.
    """
    from rich.console import Console

    from auto_ext.core.errors import AutoExtError
    from auto_ext.core.recipe_import import import_recipe, write_imported_recipe
    from auto_ext.model.common import RenderTarget

    inputs = [*(files or []), *(extra_files or [])]
    if not inputs:
        typer.secho(
            "name at least one file to import, positionally or with --file.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    catalog = _catalog_or_exit()
    forced = _parse_target_flags(list(target or []), inputs, {t.value for t in RenderTarget})
    overrides = _parse_env_flags(list(env or []))

    profile = None
    if profile_id is not None:
        profile = _load_profile(
            _profiles_dir(auto_ext_root, config_dir, profiles_dir), profile_id
        ).profile

    try:
        sources = [
            _read_import_source(
                path, RenderTarget(forced[path]) if path in forced else None
            )
            for path in inputs
        ]
        recipe_id = as_name or _derived_recipe_id(sources, catalog)
        result = import_recipe(
            sources,
            recipe_id=recipe_id,
            name=display_name,
            catalog=catalog,
            profile=profile,
            resolved_env=_import_env(profile, catalog, overrides),
            warn_ratio=warn_ratio,
        )
    except AutoExtError as exc:
        typer.secho(f"import failed: {exc}", fg=typer.colors.RED, err=True)
        if "env var" in str(exc):
            typer.secho(
                "bind it with --env NAME=VALUE, or import without --profile so "
                "the baseline comes from the files themselves.",
                fg=typer.colors.RED,
                err=True,
            )
        raise typer.Exit(code=2)

    dirs = recipe_search_path(auto_ext_root, config_dir, recipes_dir)
    target_dir = recipes_dir.expanduser().resolve() if recipes_dir else dirs[-1]
    written: Optional[Path] = None
    if write:
        try:
            written = write_imported_recipe(result, target_dir, overwrite=force)
        except AutoExtError as exc:
            typer.secho(
                f"{exc}" if force else f"{exc}, or pass --force",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

    _print_import_report(
        Console(),
        result,
        catalog=catalog,
        write=write,
        target_dir=target_dir,
        written=written,
        show_defaults=show_defaults,
    )
    raise typer.Exit(code=1 if result.warnings else 0)


#: Sentinels around the provenance block ``recipe export`` writes. They are
#: what lets a re-export replace the old header instead of stacking a second
#: one on top of it.
_EXPORT_BEGIN = "# --- auto-ext recipe export"
_EXPORT_END = "# --- end auto-ext recipe export"


def _strip_export_header(text: str) -> str:
    """Drop a leading export header, so re-exporting does not stack them."""
    lines = text.splitlines(keepends=True)
    if not lines or not lines[0].startswith(_EXPORT_BEGIN):
        return text
    for index, line in enumerate(lines):
        if line.startswith(_EXPORT_END):
            return "".join(lines[index + 1 :]).lstrip("\n")
    return text


def _export_header(recipe, *, source: Path, catalog_version: str) -> str:
    """The comment block that makes an exported recipe self-describing.

    A recipe is PDK-independent by construction, so the export really is the
    file itself -- there is nothing to inline. What the file does not carry is
    which parameter catalog its stored hunks were captured against, and that is
    the one thing the receiving side needs: a hunk anchored to a line the local
    catalog no longer renders will not apply, and the version is how somebody
    finds that out before a run instead of during one.
    """
    from datetime import datetime, timezone

    from auto_ext import __version__

    patch_versions = sorted({patch.base.catalog_version for patch in recipe.patches})
    rule = "-" * max(4, 79 - len(_EXPORT_BEGIN))
    end_rule = "-" * max(4, 79 - len(_EXPORT_END))
    lines = [
        f"{_EXPORT_BEGIN} {rule}",
        "# format: auto-ext/recipe-export/1",
        f"# recipe: {recipe.recipe_id}",
        f"# name: {recipe.name}",
        f"# version: {recipe.version}",
        f"# catalog-version: {catalog_version}",
        f"# auto-ext-version: {__version__}",
        f"# manual-edits: {recipe.manual_edit_count} hunk(s) in "
        f"{len(recipe.patches)} patch(es)",
        f"# patch-catalog-versions: {', '.join(patch_versions) or '(none)'}",
        f"# content-sha256: {recipe.content_sha256()}",
        f"# exported-at: {datetime.now(timezone.utc).isoformat()}",
        f"# exported-from: {source}",
        "#",
        "# A recipe is PDK-independent, so this file is the whole thing, manual",
        "# edits included: drop it into your recipes/ directory, or hand the path",
        "# straight to `auto-ext recipe show`.",
        "#",
        "# The stored hunks are masked at every landing site the catalog models,",
        "# so the cell and library names inside them follow whatever DUT you run",
        "# against. A hunk captured from a line the catalog does NOT model keeps",
        "# that line verbatim, absolute paths and all: run `auto-ext patch show`",
        "# and read them before sending this outside your project.",
        "#",
        "# If `auto-ext catalog list` on your machine reports a catalog version",
        "# other than the one above, these hunks were captured against a",
        "# different parameter catalog: review them with `auto-ext patch list`",
        "# before the first run.",
        f"{_EXPORT_END} {end_rule}",
    ]
    return "\n".join(lines) + "\n"


@recipe_app.command("export")
def recipe_export(
    name: str = typer.Argument(..., metavar="NAME", help="recipe_id, or a path to a .yaml."),
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        help="Write here instead of to stdout. An existing directory means "
        "<recipe_id>.yaml inside it.",
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
    force: bool = typer.Option(False, "--force", help="Overwrite an existing --out file."),
) -> None:
    """Export one recipe, manual edits and all, as a single file to send on.

    The export *is* a recipe file: the receiving side drops it into their
    ``recipes/`` directory and it loads. What the export adds is a provenance
    header naming the parameter catalog the stored hunks were captured against,
    so a mismatch surfaces on their machine before a run rather than during one.

    Without ``--out`` the document goes to stdout and every note goes to stderr,
    so ``auto-ext recipe export foo > foo.yaml`` produces a clean file.
    """
    from auto_ext.model.recipe import dump_recipe_yaml, load_recipe_with_raw, recipe_filename

    loaded = _load_recipe(recipe_search_path(auto_ext_root, config_dir, recipes_dir), name)
    recipe, raw = load_recipe_with_raw(loaded.path)
    catalog = _catalog_or_exit()

    body = _strip_export_header(dump_recipe_yaml(recipe, raw=raw))
    document = (
        _export_header(recipe, source=loaded.path, catalog_version=catalog.catalog_version)
        + body
    )
    stale = sorted(
        {
            patch.base.catalog_version
            for patch in recipe.patches
            if patch.base.catalog_version != catalog.catalog_version
        }
    )

    if out is None:
        typer.echo(document.rstrip("\n"))
        typer.secho(
            f"exported {recipe.recipe_id} from {loaded.path}: "
            f"{recipe.manual_edit_count} manual edit(s), "
            f"catalog {catalog.catalog_version}",
            err=True,
        )
        if stale:
            typer.secho(
                f"note: the stored hunks were captured against catalog "
                f"{', '.join(stale)}, not {catalog.catalog_version}; the header "
                f"records both.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        raise typer.Exit(code=0)

    destination = out.expanduser()
    if destination.is_dir():
        destination = destination / recipe_filename(recipe)
    destination = destination.resolve()
    if destination.exists() and not force:
        typer.secho(
            f"{destination} already exists; pass --force to overwrite it.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")

    typer.echo(f"wrote {destination}")
    typer.echo(f"  recipe_id:       {recipe.recipe_id}")
    typer.echo(f"  name:            {recipe.name}")
    typer.echo(
        f"  manual edits:    {recipe.manual_edit_count} hunk(s) in "
        f"{len(recipe.patches)} patch(es)"
    )
    typer.echo(f"  catalog version: {catalog.catalog_version}")
    typer.echo(f"  content sha256:  {recipe.content_sha256()[:16]}")
    if stale:
        typer.secho(
            f"note: the stored hunks were captured against catalog "
            f"{', '.join(stale)}, not {catalog.catalog_version}; the header "
            f"records both.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("  the receiving side drops this into their recipes/ directory.")


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


@profile_app.command("read-env")
def profile_read_env(
    files: Optional[list[Path]] = typer.Argument(
        None,
        metavar="[FILE]...",
        help="Files this project generated: a runset, a .cmd, an si.env.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    profile_id: Optional[str] = typer.Option(
        None, "--profile", help="Read against this profile instead of the only one."
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
        False,
        "--write",
        help="Pin every recovered value that would change something into the "
        "profile's env_overrides. Without it, this only reports.",
    ),
) -> None:
    """Recover this project's environment values from files it produced.

    The half ``check-env`` cannot do. It asks the shell; where the shell has
    nothing to say there is no second place to look -- except in the files
    this project has already written, which carry every path in resolved form.
    Matching them against the expressions the profile holds gives the value
    back: ``$env(SETUP_ROOT)/assura_tech.lib`` against
    ``/pdk/hn001/setup/assura_tech.lib`` yields ``SETUP_ROOT``.

    Exits 0 when nothing would change, 1 when something would, so a wrapper
    can branch on "a human should look". ``--write`` pins the changes into
    ``env_overrides`` -- the field that already means "used instead of the
    shell", which the health checks already mark as a deliberate deviation.
    """
    from rich.console import Console
    from rich.table import Table

    from auto_ext.core.env_import import EnvImportError, import_env
    from auto_ext.core.profile_discover import write_profile_yaml

    if not files:
        typer.secho(
            "name at least one file this project generated.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(code=2)

    loaded = _require_profile(auto_ext_root, config_dir, profiles_dir, profile_id)
    try:
        result = import_env(list(files), profile=loaded.profile)
    except EnvImportError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    console = Console()
    console.print(result.summary())
    for item in result.unreadable:
        console.print(f"[yellow]could not read {item.label}: {item.reason}[/]")

    if result.solved:
        table = Table(title="Recovered from your files", expand=True)
        table.add_column("variable", style="cyan", no_wrap=True)
        table.add_column("value", overflow="fold", ratio=3)
        table.add_column("in effect now", overflow="fold", ratio=3)
        table.add_column("read from", overflow="fold", ratio=3)
        for var in result.solved:
            current = (
                f"{var.pinned_value} (pinned)"
                if var.pinned_value is not None
                else (f"{var.shell_value} (shell)" if var.shell_value else "-- nothing --")
            )
            table.add_row(
                var.name,
                var.value + ("  [!]" if var.disagreements else ""),
                current,
                f"{var.source}: {var.via}",
            )
        console.print(table)

    for var in result.solved:
        for value, source in var.disagreements:
            console.print(
                f"[yellow]{var.name}: {source} says {value} instead. Decide which "
                f"is right before pinning it.[/]"
            )
    if result.unanswered:
        console.print(
            "not in these files: "
            + ", ".join(result.unanswered)
            + " -- a different generated file may carry them"
        )

    changes = result.changes
    if write and changes:
        # Disagreements are excluded on purpose: an unattended --write must
        # not silently pick a side in a question the files themselves raise.
        safe = [var for var in changes if not var.disagreements]
        skipped = [var.name for var in changes if var.disagreements]
        overrides = dict(loaded.profile.env_overrides)
        overrides.update({var.name: var.value for var in safe})
        write_profile_yaml(
            loaded.path, loaded.profile.model_copy(update={"env_overrides": overrides})
        )
        console.print(f"wrote {len(safe)} value(s) into {loaded.path}")
        if skipped:
            console.print(
                f"[yellow]left alone (the files disagree): {', '.join(skipped)}[/]"
            )
    elif write:
        console.print("nothing to write: every recovered value is already in effect")

    raise typer.Exit(code=1 if changes else 0)


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
