"""PDK health checks -- the single answer to "can I run right now?".

Everything that used to blow up at the moment an EDA binary was launched is
evaluated here, up front, with a fix hint attached::

    report = check_profile(profile)
    if not report.can_run:
        for r in report.blocking:
            print(r.title, "->", r.fix_hint)

What this replaces: ``runner._discover_env_vars`` scanning template text for
``$X`` on every run, followed by ``EnvResolution.require()`` raising
``EnvResolutionError`` with a bare list of names and no advice. That check had
no UI, ran too late, and drifted whenever a template changed.

Design constraints:

* **No side effects.** A check reads the environment and the filesystem and
  returns a value. It never launches an EDA binary: that would check out a
  licence and can take minutes, and "is ``calibre`` on PATH" is answered by
  :func:`shutil.which` for free. The schema draft's ``command_ok`` kind is
  deliberately absent rather than stubbed.
* **Undetermined is not failed.** When a path expression still references an
  env var nobody set, the honest answer about the file behind it is
  :attr:`~auto_ext.model.pdk.CheckStatus.UNKNOWN`, not "missing" -- the fix is
  to set the variable, not to create the file.
* **Cacheable.** :func:`write_report` stores the result next to the profile as
  ``<profile>.health.json`` with a fingerprint of the profile it describes, so
  :func:`cached_or_check` can tell a stale cache from a current one.

Wiring the existing CLI onto this module (``cli.py`` is untouched here)::

    # auto_ext/cli.py, check-env
    profile = read_profile_yaml(config_dir / "profiles" / f"{name}.yaml")
    resolution = resolve_profile_env(profile)       # -> the same var/source/value table
    report = check_profile(profile)                 # -> rows + fix hints
    ...
    raise typer.Exit(code=report.exit_code)

:func:`resolve_profile_env` returns the very
:class:`~auto_ext.core.env.EnvResolution` the current command already renders,
so the Rich table body does not change; only the source of ``required``
changes, from "grep the templates" to "the profile says so". The exit code
moves from ``resolution.missing`` to :attr:`PdkHealthReport.exit_code`, which
additionally accounts for decks, layer map and tool binaries.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from auto_ext.core.env import (
    EnvResolution,
    derive_parent_dir_from_env_candidates,
    discover_required_vars,
    resolve_env,
    resolve_path_expr,
)
from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.model.common import utcnow
from auto_ext.model.pdk import (
    CheckStatus,
    PdkCheck,
    PdkCheckKind,
    PdkCheckResult,
    PdkHealthReport,
    PdkProfile,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TOOL_EXECUTABLES",
    "FILE_OPENERS",
    "HealthError",
    "OPTIONAL_TOOLS",
    "cached_or_check",
    "check_profile",
    "default_checks",
    "format_report",
    "health_cache_path",
    "iter_env_rows",
    "profile_env_refs",
    "read_report",
    "resolve_profile_env",
    "write_report",
]


class HealthError(AutoExtError):
    """A health report could not be read or written."""


#: Stage -> executable looked up on ``PATH``. Mirrors the ``executable``
#: attribute of every :class:`~auto_ext.tools.base.Tool` subclass; the tools
#: package is not imported here so this module stays a leaf, and
#: ``tests/core/test_health.py`` asserts the two stay in sync.
DEFAULT_TOOL_EXECUTABLES: dict[str, str] = {
    "si": "si",
    "strmout": "strmout",
    "calibre": "calibre",
    "quantus": "qrc",
    "jivaro": "jivaro",
}

#: Tools whose absence is a warning, not a blocker. Reduction is opt-in
#: (the run bar's ``jivaro`` box starts unticked), so a site without a Jivaro
#: licence is a perfectly runnable site.
OPTIONAL_TOOLS: frozenset[str] = frozenset({"jivaro"})

#: Desktop file openers, in the order the GUI tries them on Linux/BSD. Any one
#: of them is enough. Duplicated from
#: :func:`auto_ext.ui.os_open.launcher_names` rather than imported: ``core``
#: never imports ``ui``, and ``tests/ui/test_os_open.py`` asserts the two lists
#: stay equal.
#:
#: Why a *health* check cares: every "Open the log / the report / the runset"
#: control in the results card ends in one of these binaries, and on a box that
#: has neither, all of them do nothing at all -- no window, no error. That is a
#: property of the host, discoverable before the click, and the Setup drawer is
#: where the host's properties are already listed.
FILE_OPENERS: tuple[str, ...] = ("xdg-open", "gio")

#: Separator joining the alternatives of an :attr:`PdkCheckKind.ON_PATH`
#: target. Any one of them being on ``PATH`` satisfies the check.
ON_PATH_ALTERNATIVES = "|"

_TMP_SUFFIX = ".tmp"

#: Suffix of the cache file that sits next to ``config/profiles/<id>.yaml``.
HEALTH_SUFFIX = ".health.json"


# ---- env ---------------------------------------------------------------------


def profile_env_refs(profile: PdkProfile) -> set[str]:
    """Every env var this profile needs resolved before anything can be checked.

    The union of ``required_env``, every ``$X`` inside the profile's own path
    expressions, and -- only when ``tech_name`` is unset -- the auto-derive
    candidates. Unlike ``runner._discover_env_vars`` this never opens a
    template: the profile is the declaration.
    """

    sources: list[str] = [
        profile.tech_library_file,
        profile.layer_map,
        *profile.cdl_include_files,
        *profile.extra_paths.values(),
    ]
    if profile.lvs_decks.dir_expr:
        sources.append(profile.lvs_decks.dir_expr)
    if profile.qrc.dir_expr:
        sources.append(profile.qrc.dir_expr)
    refs = discover_required_vars(sources)
    refs |= set(profile.required_env)
    if profile.tech_name is None:
        refs |= set(profile.tech_name_env_vars)
    return refs


def resolve_profile_env(profile: PdkProfile) -> EnvResolution:
    """Resolve every var from :func:`profile_env_refs` against this shell.

    Ladder is unchanged: ``profile.env_overrides`` -> ``os.environ`` ->
    missing. Tests inject an environment with ``monkeypatch.setenv``; nothing
    here reads a shell variable that :func:`auto_ext.core.env.resolve_env` does
    not read.
    """

    return resolve_env(profile_env_refs(profile), profile.env_overrides)


# ---- check declarations ------------------------------------------------------


def _profile_file(profile: PdkProfile) -> str:
    return f"config/profiles/{profile.profile_id}.yaml"


def _env_check_id(var: str, taken: set[str]) -> str:
    """``VERIFY_ROOT`` -> ``env.verify_root``, made unique against ``taken``.

    ``check_id`` is a slug and slugs are lowercase; env var names are
    case-sensitive on Linux, so ``PATH`` and ``Path`` would fold onto one id
    and a report with two identical ids does not validate. The real name is
    always carried in ``target`` and ``title``; the numeric suffix only keeps
    the id unique.
    """

    base = "env." + "".join(c if c.isalnum() else "_" for c in var.lower())
    candidate = base
    n = 1
    while candidate in taken:
        n += 1
        candidate = f"{base}.{n}"
    taken.add(candidate)
    return candidate


def default_checks(profile: PdkProfile) -> list[PdkCheck]:
    """Build the standard check list for ``profile``.

    A function of the profile, not a constant: a profile whose
    ``lvs_decks.dir_expr`` was never discovered gets a "fill this field in"
    check instead of a "does this directory exist" check, because those two
    situations have different fixes.

    A profile may override the list by populating ``PdkProfile.checks``;
    :func:`check_profile` uses that when it is non-empty.
    """

    yaml_file = _profile_file(profile)
    checks: list[PdkCheck] = []
    env_ids: set[str] = set()

    for var in profile.required_env:
        checks.append(
            PdkCheck(
                check_id=_env_check_id(var, env_ids),
                title=f"Environment variable {var}",
                kind=PdkCheckKind.ENV_VAR,
                target=var,
                fix_hint=(
                    f"Source the Cadence/PDK setup script that exports {var}, then re-run "
                    f"`auto-ext check-env`. To pin it instead, add `env_overrides: {{{var}: "
                    f"/abs/path}}` to {yaml_file}."
                ),
            )
        )

    checks.append(
        PdkCheck(
            check_id="pdk.tech_name",
            title="Technology name",
            kind=PdkCheckKind.FIELD_SET,
            target="tech_name",
            fix_hint=(
                f"Set `tech_name:` in {yaml_file}. It is auto-derived from the parent "
                f"directory of the first variable in `tech_name_env_vars` "
                f"({', '.join(profile.tech_name_env_vars) or 'none listed'}) when left "
                "empty, and none of those resolved. Quantus needs it for -technology_name."
            ),
        )
    )
    checks.append(
        PdkCheck(
            check_id="pdk.layer_map",
            title="Layer map file",
            kind=PdkCheckKind.FILE_EXISTS,
            target=profile.layer_map,
            fix_hint=(
                f"strmout is run as `-layerMap <this file>`. Point `layer_map:` in "
                f"{yaml_file} at the PDK layer map, or export the env var it references."
            ),
        )
    )
    checks.append(
        PdkCheck(
            check_id="pdk.tech_library_file",
            title="Technology library (assura_tech.lib)",
            kind=PdkCheckKind.FILE_EXISTS,
            target=profile.tech_library_file,
            fix_hint=(
                f"Quantus is run as `-technology_library_file <this file>`. Fix "
                f"`tech_library_file:` in {yaml_file}; the default assumes "
                "$SETUP_ROOT/assura_tech.lib."
            ),
        )
    )
    for index, expr in enumerate(profile.cdl_include_files):
        suffix = "" if index == 0 else f".{index + 1}"
        checks.append(
            PdkCheck(
                check_id=f"pdk.cdl_include{suffix}",
                title="Device CDL prelude (si.env incFILE)",
                kind=PdkCheckKind.FILE_EXISTS,
                target=expr,
                fix_hint=(
                    f"si writes `incFILE = \"{expr}\"` into si.env, and Calibre reads the "
                    f"resulting netlist. Fix `cdl_include_files:` in {yaml_file} or export "
                    "the env var it references."
                ),
            )
        )

    checks.extend(_lvs_checks(profile, yaml_file))
    checks.extend(_qrc_checks(profile, yaml_file))

    checks.append(
        PdkCheck(
            check_id="pdk.corners",
            title="Process corner table",
            kind=PdkCheckKind.FIELD_SET,
            target="corners",
            fix_hint=(
                "No corner is defined, so no Recipe can name one. Read the corner names "
                "off the Quantus GUI RuleSet list -- NOT off $SETUP_ROOT/assura_tech.lib, "
                "which on a real PDK was found to contain no corner names at all -- and "
                f"add one `corners:` entry per RuleSet entry to {yaml_file} (`name:` is "
                "yours to choose, `technology_corner:` is the literal Quantus expects)."
            ),
        )
    )
    checks.append(
        PdkCheck(
            check_id="pdk.power_names",
            title="Global power net names",
            kind=PdkCheckKind.FIELD_SET,
            target="power_names",
            required=False,
            fix_hint=(
                f"`*lvsPowerNames` will be emitted empty. Copy the list out of a known-good "
                f"Calibre runset export into `power_names:` in {yaml_file}."
            ),
        )
    )
    checks.append(
        PdkCheck(
            check_id="pdk.ground_names",
            title="Global ground net names",
            kind=PdkCheckKind.FIELD_SET,
            target="ground_names",
            required=False,
            fix_hint=(
                f"`*lvsGroundNames` will be emitted empty. Copy the list out of a known-good "
                f"Calibre runset export into `ground_names:` in {yaml_file}."
            ),
        )
    )

    checks.extend(_file_opener_checks())

    for stage, executable in DEFAULT_TOOL_EXECUTABLES.items():
        optional = stage in OPTIONAL_TOOLS
        checks.append(
            PdkCheck(
                check_id=f"tool.{stage}",
                title=f"{stage} binary ({executable})",
                kind=PdkCheckKind.ON_PATH,
                target=executable,
                required=not optional,
                fix_hint=(
                    f"`{executable}` is not on PATH. Load the module / source the setup "
                    f"script that provides the {stage} stage"
                    + (
                        ", or leave reduction disabled in the Recipe."
                        if optional
                        else ", then re-run `auto-ext check-env`."
                    )
                ),
            )
        )

    return checks


def _file_opener_checks(platform: str | None = None) -> list[PdkCheck]:
    """The "can this host open a file at all?" row, on the platforms it means.

    A warning, never a blocker: a box with no ``xdg-open`` runs the whole flow
    perfectly well, it just cannot show you the report afterwards. Omitted on
    Windows and macOS, where the answer is always yes -- ``ShellExecuteW`` and
    ``open`` are part of the OS, so there is nothing to look up and a row that
    can only say "yes" is noise in a list meant to be read.
    """

    platform = sys.platform if platform is None else platform
    if platform in ("win32", "darwin"):
        return []
    return [
        PdkCheck(
            check_id="tool.file_opener",
            title="Desktop file opener (xdg-open / gio)",
            kind=PdkCheckKind.ON_PATH,
            target=ON_PATH_ALTERNATIVES.join(FILE_OPENERS),
            required=False,
            fix_hint=(
                "Every 'Open the log / the report / the runset' button in the "
                "results card hands the file to one of these, so on this host "
                "they can do nothing at all. Install `xdg-utils` (or the GLib "
                "tools, which provide `gio`) to get them back; until then, use "
                "the in-app log view, which reads the archived log directly."
            ),
        )
    ]


def _lvs_checks(profile: PdkProfile, yaml_file: str) -> list[PdkCheck]:
    decks = profile.lvs_decks
    if decks.dir_expr is None:
        return [
            PdkCheck(
                check_id="lvs.deck_dir",
                title="Calibre LVS deck directory",
                kind=PdkCheckKind.FIELD_SET,
                target="lvs_decks.dir_expr",
                fix_hint=(
                    f"Scanning could not locate the LVS deck. Set `lvs_decks.dir_expr:` in "
                    f"{yaml_file} to the directory holding "
                    "`<basename>.<variant>.qcilvs`; when the PDK sets "
                    "$calibre_source_added_place to a file inside it, "
                    "`$calibre_source_added_place|parent` is the expression to use."
                ),
            )
        ]
    return [
        PdkCheck(
            check_id="lvs.deck_dir",
            title="Calibre LVS deck directory",
            kind=PdkCheckKind.DIR_EXISTS,
            target=decks.dir_expr,
            fix_hint=(
                f"`lvs_decks.dir_expr:` in {yaml_file} resolves to a directory that is not "
                "there. Check the runset version segment of the path (the LVS deck version "
                "is independent of the QRC one) and that the filesystem is mounted."
            ),
        ),
        PdkCheck(
            check_id="lvs.rules_files",
            title="Calibre LVS rules files",
            kind=PdkCheckKind.GLOB_NONEMPTY,
            target=f"{decks.dir_expr}/{decks.glob_pattern()}",
            fix_hint=(
                f"The deck directory holds no `{decks.glob_pattern()}`. Either "
                f"`lvs_decks.basename:` in {yaml_file} is wrong (it defaults to the last "
                "segment of the deck directory) or the directory is not the deck."
            ),
        ),
        PdkCheck(
            check_id="lvs.variants",
            title="LVS deck variants",
            kind=PdkCheckKind.FIELD_SET,
            target="lvs_decks.variants",
            fix_hint=(
                f"No variant is defined, so `Recipe.lvs.deck_variant` cannot resolve. Run "
                f"`ls {decks.dir_expr}` on the server and add one `lvs_decks.variants:` entry "
                f"per `<basename>.<suffix>.qcilvs` file to {yaml_file}, or re-scan from a "
                "machine that can reach the deck."
            ),
        ),
    ]


def _qrc_checks(profile: PdkProfile, yaml_file: str) -> list[PdkCheck]:
    qrc = profile.qrc
    if qrc.dir_expr is None:
        return [
            PdkCheck(
                check_id="qrc.deck_dir",
                title="QRC deck directory",
                kind=PdkCheckKind.FIELD_SET,
                target="qrc.dir_expr",
                fix_hint=(
                    f"Set `qrc.dir_expr:` in {yaml_file}. There is no env-var convention for "
                    "it and it cannot be derived from the LVS deck path: the QRC deck carries "
                    "its own runset version and sits one level deeper "
                    "(.../QRC/<version>/<pdk_subdir>/QCI_deck). Run "
                    "`ls $VERIFY_ROOT/runset/Calibre_QRC/QRC/*/*/QCI_deck` to find it."
                ),
            )
        ]
    return [
        PdkCheck(
            check_id="qrc.deck_dir",
            title="QRC deck directory",
            kind=PdkCheckKind.DIR_EXISTS,
            target=qrc.dir_expr,
            fix_hint=(
                f"`qrc.dir_expr:` in {yaml_file} resolves to a directory that is not there. "
                "Its runset version is allowed to differ from the LVS deck's, so do not copy "
                "the LVS version across -- run "
                "`ls $VERIFY_ROOT/runset/Calibre_QRC/QRC/*/*/QCI_deck`."
            ),
        ),
        PdkCheck(
            check_id="qrc.query_cmd",
            title="QRC query_cmd",
            kind=PdkCheckKind.FILE_EXISTS,
            target=f"{qrc.dir_expr}/{qrc.query_cmd_name}",
            fix_hint=(
                "Calibre's lvsPostTriggers runs `calibre -query_input <this file> -query "
                f"svdb`; without it Quantus gets no query_output. Check `qrc.dir_expr:` and "
                f"`qrc.query_cmd_name:` in {yaml_file}."
            ),
        ),
        PdkCheck(
            check_id="qrc.preserve_cell_list",
            title="QRC preserveCellList.txt",
            kind=PdkCheckKind.FILE_EXISTS,
            target=f"{qrc.dir_expr}/{qrc.preserve_cell_list_name}",
            required=False,
            fix_hint=(
                "Quantus is run as `-parasitic_blocking_device_cells_file <this file>`. Not "
                "every deck ships one; if yours does not, remove that option from the "
                f"template, otherwise fix `qrc.preserve_cell_list_name:` in {yaml_file}."
            ),
        ),
    ]


# ---- evaluation --------------------------------------------------------------

#: Dotted ``FIELD_SET`` targets this module knows how to read. Anything else
#: is reported as ``UNKNOWN`` rather than traversed blindly.
_FIELD_TARGETS: dict[str, Callable[[PdkProfile], object]] = {
    "tech_name": lambda p: p.tech_name,
    "corners": lambda p: p.corners,
    "default_corner": lambda p: p.default_corner,
    "power_names": lambda p: p.power_names,
    "ground_names": lambda p: p.ground_names,
    "required_env": lambda p: p.required_env,
    "lvs_decks.dir_expr": lambda p: p.lvs_decks.dir_expr,
    "lvs_decks.basename": lambda p: p.lvs_decks.basename,
    "lvs_decks.variants": lambda p: p.lvs_decks.variants,
    "lvs_decks.default_variant": lambda p: p.lvs_decks.default_variant,
    "qrc.dir_expr": lambda p: p.qrc.dir_expr,
}


def _describe(value: object) -> str:
    if isinstance(value, (list, tuple, dict, set)):
        return f"{len(value)} entries"
    return str(value)


def _unresolved_vars(expr: str, resolution: EnvResolution) -> list[str]:
    """Names in ``expr`` that resolved to nothing.

    ``resolve_env`` maps a missing var to the empty string, so substituting
    without this guard turns ``$SETUP_ROOT/assura_tech.lib`` into
    ``/assura_tech.lib`` -- a path that exists nowhere and blames the wrong
    thing. Callers report ``UNKNOWN`` when this is non-empty.
    """

    return sorted(
        name
        for name in discover_required_vars([expr])
        if resolution.sources.get(name, "missing") == "missing"
    )


def _split_glob(target: str) -> tuple[str, str]:
    """``"<dir expr>/<glob>"`` -> ``(dir_expr, glob)``.

    Only the last segment may contain wildcards; the directory part goes
    through the normal path-expression resolution.
    """

    head, _, tail = target.rpartition("/")
    if not head or not tail:
        raise ValueError(f"glob target {target!r} must be '<directory expression>/<pattern>'")
    return head, tail


def _result(
    check: PdkCheck,
    status: CheckStatus,
    *,
    observed: str | None,
    message: str | None,
    now: datetime,
) -> PdkCheckResult:
    if status is CheckStatus.FAIL and not check.required:
        status = CheckStatus.WARN
    return PdkCheckResult(
        check_id=check.check_id,
        status=status,
        required=check.required,
        title=check.title,
        observed=observed,
        message=message,
        fix_hint=None if status is CheckStatus.OK else check.fix_hint,
        checked_at=now,
    )


def _evaluate(
    check: PdkCheck,
    profile: PdkProfile,
    resolution: EnvResolution,
    which: Callable[[str], str | None],
    now: datetime,
) -> PdkCheckResult:
    kind = check.kind

    if kind is PdkCheckKind.ENV_VAR:
        source = resolution.sources.get(check.target, "missing")
        value = resolution.resolved.get(check.target, "")
        if source == "missing" or not value:
            return _result(
                check,
                CheckStatus.FAIL,
                observed=f"source={source}",
                message=f"{check.target} is not set",
                now=now,
            )
        return _result(
            check, CheckStatus.OK, observed=value, message=f"from {source}", now=now
        )

    if kind is PdkCheckKind.FIELD_SET:
        reader = _FIELD_TARGETS.get(check.target)
        if reader is None:
            return _result(
                check,
                CheckStatus.UNKNOWN,
                observed=None,
                message=f"unsupported field target {check.target!r}",
                now=now,
            )
        value = reader(profile)
        if check.target == "tech_name" and not value:
            derived = derive_parent_dir_from_env_candidates(
                profile.tech_name_env_vars, resolution.resolved
            )
            if derived:
                return _result(
                    check,
                    CheckStatus.OK,
                    observed=derived,
                    message="auto-derived from tech_name_env_vars",
                    now=now,
                )
        if not value:
            return _result(
                check,
                CheckStatus.FAIL,
                observed="(empty)",
                message=f"{check.target} is empty",
                now=now,
            )
        return _result(check, CheckStatus.OK, observed=_describe(value), message=None, now=now)

    if kind is PdkCheckKind.ON_PATH:
        # ``a|b`` means "either will do". One binary is the normal case; the
        # alternatives exist because a desktop file opener may be xdg-open or
        # gio and the box needs only one of them.
        names = [n for n in check.target.split(ON_PATH_ALTERNATIVES) if n]
        for name in names:
            found = which(name)
            if found is not None:
                return _result(check, CheckStatus.OK, observed=found, message=None, now=now)
        return _result(
            check,
            CheckStatus.FAIL,
            observed="(not on PATH)",
            message=(
                f"{' and '.join(names)} were not found on PATH"
                if len(names) > 1
                else f"{check.target} was not found on PATH"
            ),
            now=now,
        )

    if kind in (PdkCheckKind.FILE_EXISTS, PdkCheckKind.DIR_EXISTS, PdkCheckKind.GLOB_NONEMPTY):
        return _evaluate_path(check, resolution, now)

    return _result(
        check,
        CheckStatus.UNKNOWN,
        observed=None,
        message=f"unsupported check kind {kind.value!r}",
        now=now,
    )


def _evaluate_path(check: PdkCheck, resolution: EnvResolution, now: datetime) -> PdkCheckResult:
    expr = check.target
    pattern = ""
    if check.kind is PdkCheckKind.GLOB_NONEMPTY:
        try:
            expr, pattern = _split_glob(check.target)
        except ValueError as exc:
            return _result(
                check, CheckStatus.UNKNOWN, observed=None, message=str(exc), now=now
            )

    missing = _unresolved_vars(expr, resolution)
    if missing:
        return _result(
            check,
            CheckStatus.UNKNOWN,
            observed=expr,
            message=f"cannot resolve: {', '.join('$' + m for m in missing)} is not set",
            now=now,
        )

    try:
        resolved = resolve_path_expr(expr, resolution.resolved)
    except ConfigError as exc:
        return _result(check, CheckStatus.UNKNOWN, observed=expr, message=str(exc), now=now)
    if not resolved:
        return _result(
            check,
            CheckStatus.UNKNOWN,
            observed=expr,
            message="path expression resolved to an empty string",
            now=now,
        )

    path = Path(resolved)
    try:
        if check.kind is PdkCheckKind.FILE_EXISTS:
            ok = path.is_file()
            message = None if ok else "not a regular file"
        elif check.kind is PdkCheckKind.DIR_EXISTS:
            ok = path.is_dir()
            message = None if ok else "not a directory"
        else:
            if not path.is_dir():
                return _result(
                    check,
                    CheckStatus.FAIL,
                    observed=resolved,
                    message="directory does not exist, so nothing can match",
                    now=now,
                )
            hits = sorted(p.name for p in path.glob(pattern))
            ok = bool(hits)
            resolved = f"{resolved}/{pattern}"
            message = (
                f"{len(hits)} match(es): {', '.join(hits[:5])}"
                if ok
                else "no file matches this pattern"
            )
    except OSError as exc:
        return _result(
            check, CheckStatus.UNKNOWN, observed=resolved, message=str(exc), now=now
        )

    return _result(
        check,
        CheckStatus.OK if ok else CheckStatus.FAIL,
        observed=resolved,
        message=message,
        now=now,
    )


def check_profile(
    profile: PdkProfile,
    *,
    checks: Sequence[PdkCheck] | None = None,
    which: Callable[[str], str | None] | None = None,
    resolution: EnvResolution | None = None,
    now: datetime | None = None,
) -> PdkHealthReport:
    """Evaluate every check of ``profile`` and return the report.

    ``checks`` defaults to ``profile.checks`` when that is non-empty, else to
    :func:`default_checks`; passing an explicit list overrides both, and an
    explicit empty list means "check nothing" (the resulting report trivially
    says the run may proceed). ``which`` defaults to :func:`shutil.which` and
    is injectable so a test does not depend on what the host happens to have
    installed. ``resolution`` defaults to :func:`resolve_profile_env`.

    Reads the environment and the filesystem; writes nothing. Persisting the
    result is :func:`write_report`'s job.
    """

    now = now or utcnow()
    which = which or shutil.which
    resolution = resolution if resolution is not None else resolve_profile_env(profile)
    declared: Sequence[PdkCheck] = checks if checks is not None else (
        profile.checks or default_checks(profile)
    )

    results = [_evaluate(check, profile, resolution, which, now) for check in declared]
    report = PdkHealthReport(
        profile_id=profile.profile_id,
        checked_at=now,
        results=results,
        profile_sha256=profile.fingerprint(),
    )
    logger.debug(
        "health %s: %s (can_run=%s)", profile.profile_id, report.counts(), report.can_run
    )
    return report


# ---- cache -------------------------------------------------------------------


def health_cache_path(profile_path: Path) -> Path:
    """``config/profiles/HN001.yaml`` -> ``config/profiles/HN001.health.json``."""

    profile_path = Path(profile_path)
    return profile_path.with_name(profile_path.stem + HEALTH_SUFFIX)


def write_report(profile_path: Path, report: PdkHealthReport) -> Path:
    """Write ``<profile>.health.json`` atomically and return its path.

    Same protocol as :mod:`auto_ext.core.run_store`: temp file next to the
    target, then :func:`os.replace`. The file is a cache, gitignored, and safe
    to delete at any time.
    """

    target = health_cache_path(profile_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + _TMP_SUFFIX)
    try:
        tmp.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise HealthError(f"cannot write {target}: {exc}") from exc
    return target


def read_report(profile_path: Path) -> PdkHealthReport | None:
    """Load ``<profile>.health.json``, or ``None`` when it is absent or unusable.

    A cache that cannot be parsed is not an error: it is a cache miss. The
    reason is logged and the caller re-runs the checks.
    """

    target = health_cache_path(profile_path)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return PdkHealthReport.model_validate(data)
    except (OSError, ValueError) as exc:
        logger.info("discarding unusable health cache %s: %s", target, exc)
        return None


def cached_or_check(
    profile_path: Path,
    profile: PdkProfile,
    *,
    max_age_s: float | None = None,
    force: bool = False,
    write: bool = True,
    checks: Sequence[PdkCheck] | None = None,
    which: Callable[[str], str | None] | None = None,
    resolution: EnvResolution | None = None,
    now: datetime | None = None,
) -> tuple[PdkHealthReport, bool]:
    """Return ``(report, from_cache)``.

    The cache is used only when it describes *this* profile
    (``profile_sha256`` matches :meth:`PdkProfile.fingerprint`) and, when
    ``max_age_s`` is given, is younger than that. Otherwise the checks run
    again and -- unless ``write=False`` -- the cache is refreshed.

    A stale cache is never returned: the environment moves underneath a
    profile (a module unloaded, a mount dropped), which is exactly the case
    where a confident but wrong "you can run" would waste an hour.
    """

    if not force:
        cached = read_report(profile_path)
        if cached is not None and cached.profile_sha256 == profile.fingerprint():
            fresh = True
            if max_age_s is not None:
                age = utcnow() - cached.checked_at
                fresh = age <= timedelta(seconds=max_age_s)
            if fresh:
                return cached, True

    report = check_profile(
        profile, checks=checks, which=which, resolution=resolution, now=now
    )
    if write:
        write_report(profile_path, report)
    return report, False


def format_report(report: PdkHealthReport) -> str:
    """Plain-text rendering, one line per check plus fix hints for the rest.

    Deliberately dependency-free (no Rich) so the CLI, a log file and a test
    assertion can all use the same text.
    """

    symbol = {
        CheckStatus.OK: "ok  ",
        CheckStatus.WARN: "warn",
        CheckStatus.FAIL: "FAIL",
        CheckStatus.UNKNOWN: "????",
    }
    lines: list[str] = []
    for r in report.results:
        detail = r.observed or ""
        if r.message:
            detail = f"{detail} ({r.message})" if detail else r.message
        lines.append(f"[{symbol[r.status]}] {r.title}: {detail}".rstrip())
    problems = [r for r in report.results if not r.ok]
    if problems:
        lines.append("")
        for r in problems:
            lines.append(f"* {r.title}: {r.fix_hint}")
    verdict = "ready to run" if report.can_run else "cannot run yet"
    lines.append("")
    lines.append(f"{report.profile_id}: {verdict} -- {report.counts()}")
    return "\n".join(lines)


def iter_env_rows(resolution: EnvResolution) -> Iterable[tuple[str, str, str]]:
    """``(name, source, value)`` rows, sorted -- the body of ``check-env``'s table.

    Exists so ``cli.py`` can drop ``runner._discover_env_vars`` without also
    rewriting its Rich table.
    """

    for name in sorted(resolution.resolved):
        yield name, resolution.sources[name], resolution.resolved[name]
