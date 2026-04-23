"""Typer CLI entry point.

Live subcommands as of Phase 4b1:

- ``version`` — prints the package version (Phase 1).
- ``run`` — loads ``project.yaml`` + ``tasks.yaml`` and drives
  :func:`auto_ext.core.runner.run_tasks`.
- ``check-env`` — prints a Rich table of env-var resolution for every
  template referenced by the tasks. Exits 1 if anything is missing.
- ``import`` — turn a raw EDA export into a parameterised ``.j2`` +
  sidecar manifest with identity substitutions pre-applied.
- ``knob suggest / promote`` — inspect and promote candidate literals
  on an already-imported template.

``migrate`` stays a Phase 4c stub.
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

knob_app = typer.Typer(
    name="knob",
    help="Inspect or promote candidate literals on an imported template.",
    no_args_is_help=True,
)
app.add_typer(knob_app, name="knob")


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
    """Convert legacy Run_ext.txt to tasks.yaml. Implementation lands in Phase 4c."""
    typer.secho(
        "auto-ext migrate: not implemented yet (Phase 4c).",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(code=2)


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
    from auto_ext.core.manifest import (
        TemplateManifest,
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
    if existing_manifest is not None and existing_manifest.knobs:
        outcome = merge_reimport(result, existing_manifest)
        body = outcome.body
        final_manifest = outcome.manifest
        # ``template`` was validated to match output.name by load_manifest.
        merge_messages = outcome.messages
    else:
        body = result.template_body
        final_manifest = TemplateManifest(template=output.name, knobs={})

    _backup_if_exists(output)
    _backup_if_exists(manifest_path)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    manifest_path.write_text(_dump_manifest(final_manifest), encoding="utf-8")

    review_path = output.with_name(output.name + ".review.md")
    _backup_if_exists(review_path)
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
    from auto_ext.core.manifest import (
        KnobSpec,
        SourceRef,
        TemplateManifest,
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

    _backup_if_exists(template)
    _backup_if_exists(manifest_path)

    template.write_text(body, encoding="utf-8")
    manifest_path.write_text(_dump_manifest(new_manifest), encoding="utf-8")

    typer.echo(f"promoted {len(keys)} knob(s); updated:")
    typer.echo(f"  {template}")
    typer.echo(f"  {manifest_path}")


# ---- import/knob helpers ---------------------------------------------------


def _backup_if_exists(path: Path) -> None:
    if path.exists():
        import shutil

        bak = path.with_name(path.name + ".bak")
        shutil.copy2(path, bak)


def _dump_manifest(manifest) -> str:
    from io import StringIO

    from ruamel.yaml import YAML

    data = {"template": manifest.template}
    if manifest.description is not None:
        data["description"] = manifest.description
    data["knobs"] = {
        name: spec.model_dump(exclude_none=True)
        for name, spec in manifest.knobs.items()
    }

    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


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
