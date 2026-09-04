"""One-shot conversion of the v1 config pair into the v2 object model.

``config/project.yaml`` + ``config/tasks.yaml`` become:

* ``config/workspace.yaml``      -- the five keys left of ``project.yaml``
* ``config/cells.yaml``          -- the DUT table, Cartesian expansion resolved
* ``config/profiles/<id>.yaml``  -- everything that was really about the PDK
* ``config/resources.yaml``      -- CPU / turbo / licence knobs (never a Recipe)
* ``recipes/<name>.yaml``        -- one per distinct set of render parameters

Written against ``docs/refactor/01-schema.md`` chapter 3 (disposition table
plus migrate pseudocode).

Three properties this module is built around
--------------------------------------------
**Nothing is edited in place.** ``project.yaml`` and ``tasks.yaml`` are opened
read-only and left exactly as they are, and no ``logs/`` or ``runs/``
directory is touched. The migration writes a second, parallel set of files; if
the result is wrong, the user deletes it and is back where they started. (The
schema pseudocode archives the old files into ``config/_migrated_v1`` and
deletes the old run layout. That is a separate, reversible-by-nobody step and
is deliberately not done here.)

**Re-running is safe.** Every derived name is a pure function of the input, so
a second run computes the same file set; and a file that already exists is
never overwritten. A second run therefore writes nothing and reports every
target as skipped, whether the existing file is byte-identical to what would
have been generated or has since been hand-edited (the two cases are
distinguished in the report and in :attr:`MigrationReport.warnings`).

**No field disappears silently.** Every key of both input files produces at
least one :class:`FieldDisposition` -- ``moved``, ``folded``, ``dropped``,
``seeded_from_template`` or ``decision``. Anything the migration cannot decide
on its own becomes a :class:`MigrationDecision` (it changes the output, so it
has a default and can be answered by a callback) or an :class:`OpenQuestion`
(it does not change the output; it is a value carried over that nobody has
confirmed yet). Both land in the report *and* as a comment in the generated
YAML, next to the field they are about.

Values are read back from the template text, not taken from schema defaults
----------------------------------------------------------------------------
A migration that quietly changed one Quantus literal would change extraction
results, and the user would have no way to see it. So every literal the
catalog knows about is parsed out of the *user's actual* template files and
written into the Recipe / Profile / ResourceProfile. The catalog supplies the
map (which option in which section of which file backs which field); this
module supplies four small parsers, one per target syntax. A value the parser
cannot recover keeps the catalog default and is listed in
:attr:`MigrationReport.warnings`.

The one deliberate exception is a table the legacy config **structurally
cannot hold**: the old script had no concept of a corner table, a deck-variant
table or a supply-net list, so there is no user value to be neutral about --
only the choice between an empty table (a blocking ``check-env`` failure on an
otherwise successful migration) and the shipped profile's. It takes the
shipped one, and every such field is named in
:attr:`MigrationReport.shipped_fallbacks`, in a ``seeded_from_shipped_profile``
disposition, in a warning, and in a ``NOT FROM YOUR CONFIG`` comment on the
field itself in the written YAML. A table the templates *can* show is never
replaced, however incomplete it is.

What is deliberately not implemented yet
----------------------------------------
``seed_patches`` in the schema pseudocode renders the old templates and the
new catalog side by side and turns the residue into a seed
:class:`~auto_ext.core.patch_models.TemplatePatch`. The *old* side of that
comparison needs a full legacy render context -- resolved env vars, a real
``output_dir``, an employee id -- which would make the migration depend on
being run inside a live PDK environment, and a migration you can only run on
the machine you are migrating away from is not much of a migration. So
``seed_patches=True`` raises :class:`NotImplementedError` rather than
pretending, and every report says so in :attr:`MigrationReport.warnings`:
"byte fidelity was not verified" is a fact about the output the user has to
know. What *is* verified is stronger than nothing: every value the catalog
models is read out of the user's own template text, so a modelled literal
cannot change during the move.
"""

from __future__ import annotations

import logging
import os
import re
import textwrap
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from auto_ext.catalog import (
    Catalog,
    Currently,
    Owner,
    builtin_catalog,
    default_templates_root,
    load_catalog,
)
from auto_ext.core.env import discover_required_vars
from auto_ext.core.errors import AutoExtError, ConfigError
from auto_ext.core.readback import (
    DEFAULT_READBACK_OWNERS,
    ReadBackError,
    TemplateReadBack,
    parse_calibre,
    parse_quantus,
    parse_skill,
    parse_xml,
)
from auto_ext.core.readback import read_back_from_templates as _read_back
from auto_ext.legacy_v1 import (
    ProjectConfigV1,
    TaskConfigV1,
    TaskSpecV1,
    load_manifest_v1,
    load_project_v1,
    load_tasks_v1_with_raw,
    resolve_knob_values_v1,
)
from auto_ext.core.profile_discover import (
    BUILTIN_PROFILE_PATH,
    builtin_profile,
    read_profile_yaml,
)
from auto_ext.model.cells import CELLS_FILENAME, CellBook, CellEntry, load_cells
from auto_ext.model.common import RenderTarget, Stage, slugify, utcnow
from auto_ext.model.pdk import (
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    ParasiticDeviceContract,
    PdkProfile,
    QrcDeck,
)
from auto_ext.model.recipe import (
    ExtractType,
    OutputKind,
    Recipe,
    ReductionSettings,
    ResourceProfile,
    load_recipe,
    recipe_from_catalog,
)
from auto_ext.model.workspace import (
    RETIRED_FORMAT_KEYS,
    WORKSPACE_FILENAME,
    WorkspaceConfig,
    load_workspace,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DispositionAction",
    "FieldDisposition",
    "MigrationDecision",
    "MigrationError",
    "MigrationReport",
    "OpenQuestion",
    "RESOURCES_FILENAME",
    "TemplateReadBack",
    "format_report",
    "migrate_v1_to_v2",
    "read_back_from_templates",
]

#: Where the resource knobs land. ``docs/refactor/01-schema.md`` section 0
#: does not name a file for :class:`ResourceProfile` -- it only says the
#: object is separate from the Recipe (DECISIONS #21). The values are real and
#: read back from the runset, so they need a home; this is it.
RESOURCES_FILENAME = "resources.yaml"

#: Synthetic ``${...}`` tokens that a path pattern may reference and that are
#: NOT shell environment variables. Mirrors ``runner._PATH_TOKEN_NAMES``; the
#: duplication is deliberate, migrate must not import the runner.
_PATH_TOKENS: frozenset[str] = frozenset(
    {
        "output_dir",
        "intermediate_dir",
        "layer_map",
        "calibre_lvs_dir",
        "calibre_lvs_basename",
        "qrc_deck_dir",
    }
)

#: Stage slot in ``ProjectConfigV1.templates`` -> the render target it feeds.
#: ``quantus`` is absent because one slot feeds two possible targets and the
#: file's own ``output_db -type`` decides which; see :func:`_quantus_target`.
_SLOT_TARGETS: dict[str, RenderTarget] = {
    "si": RenderTarget.SI_ENV,
    "calibre": RenderTarget.LVS_QCI,
    "jivaro": RenderTarget.JIVARO_XML,
}

#: Render target -> the stage whose knobs and template slot it belongs to.
#: Not derivable from the target id: ``lvs.qci`` is the ``calibre`` stage, and
#: two targets share the ``quantus`` stage.
_TARGET_STAGE: dict[RenderTarget, str] = {
    RenderTarget.SI_ENV: Stage.SI.value,
    RenderTarget.LVS_QCI: Stage.CALIBRE.value,
    RenderTarget.QUANTUS_EXT: Stage.QUANTUS.value,
    RenderTarget.QUANTUS_DSPF: Stage.QUANTUS.value,
    RenderTarget.JIVARO_XML: Stage.JIVARO.value,
}


class MigrationError(AutoExtError):
    """The migration cannot produce a faithful result and refuses to guess."""


# ---- report objects ----------------------------------------------------------

DispositionAction = Literal[
    "moved",
    "dropped",
    "folded",
    "seeded_from_template",
    "seeded_from_shipped_profile",
    "decision",
]


@dataclass(frozen=True)
class FieldDisposition:
    """What happened to one input field. The report lists every one of them."""

    source: str
    action: DispositionAction
    target: str | None
    note: str = ""

    def describe(self) -> str:
        arrow = f" -> {self.target}" if self.target else ""
        note = f"  ({self.note})" if self.note else ""
        return f"{self.source} [{self.action}]{arrow}{note}"


@dataclass(frozen=True)
class MigrationDecision:
    """A choice that changes the output and that only the user can make.

    ``default`` is what an unattended migration uses, and it is always the
    option that preserves current behaviour. ``options`` empty means free
    text (a name, for instance).
    """

    key: str
    question: str
    options: list[str]
    default: Any
    context: str = ""

    def describe(self) -> str:
        choices = f"  options: {', '.join(self.options)}" if self.options else ""
        context = f"\n      context: {self.context}" if self.context else ""
        return f"{self.key}\n      {self.question}\n      default: {self.default!r}{choices}{context}"


@dataclass(frozen=True)
class OpenQuestion:
    """A carried-over value nobody has confirmed against a real tool yet.

    Distinct from :class:`MigrationDecision`: answering it does not change
    what the migration writes, it changes whether the written value is right.
    Sourced from the catalog's own ``question:`` column, so the list stays in
    sync with ``docs/refactor/OFFICE_TODO.md`` without a second copy of it.
    """

    key: str
    question: str
    field_path: str
    value: Any
    file: str

    def describe(self) -> str:
        return f"{self.file}: {self.field_path} = {self.value!r}\n      {self.question}"


@dataclass
class MigrationReport:
    """Everything the migration produced, decided, and could not decide."""

    profile: PdkProfile
    recipes: list[Recipe]
    cells: CellBook
    workspace: WorkspaceConfig
    resources: ResourceProfile
    #: ``recipe_id`` -> the :attr:`CellEntry.key` values that recipe covers.
    #: This is how the user rebuilds "what used to run together".
    bindings: dict[str, list[str]] = field(default_factory=dict)
    dispositions: list[FieldDisposition] = field(default_factory=list)
    decisions: list[MigrationDecision] = field(default_factory=list)
    #: Decision key -> the answer actually used (default, or the resolver's).
    answers: dict[str, Any] = field(default_factory=dict)
    open_questions: list[OpenQuestion] = field(default_factory=list)
    #: Profile fields whose value came from the shipped profile because the
    #: legacy config structurally could not supply one. Names as they appear
    #: in the written YAML (``corners``, ``lvs_decks.variants``, ...). Kept
    #: apart from :attr:`warnings` because the two ask for different things:
    #: a warning says "look at this", these say "this is not yours".
    shipped_fallbacks: list[str] = field(default_factory=list)
    #: Always empty in this build: see the module docstring on ``seed_patches``.
    seeded_patches: list[tuple[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)
    #: Targets that already existed and were left alone (idempotent re-run).
    skipped: list[Path] = field(default_factory=list)

    def disposition_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.dispositions:
            counts[item.action] = counts.get(item.action, 0) + 1
        return counts

    def needs_confirmation(self) -> list[str]:
        """The "check this by hand" list: decisions taken plus open questions."""

        lines = [d.describe() for d in self.decisions]
        lines.extend(q.describe() for q in self.open_questions)
        return lines


# ---- template read-back ------------------------------------------------------
# The parsers and the read-back rules live in auto_ext.core.readback: the
# recipe importer needs the same answer against a *rendered* file, and a
# neutral module is what stops that import from pulling the whole v1 migration
# in behind it. The private spellings below are kept because they are this
# module's own history and its tests' entry points.

_parse_skill = parse_skill
_parse_calibre = parse_calibre
_parse_quantus = parse_quantus
_parse_xml = parse_xml


def read_back_from_templates(
    texts: Mapping[RenderTarget, str],
    *,
    catalog: Catalog | None = None,
    owners: Sequence[Owner] = DEFAULT_READBACK_OWNERS,
) -> TemplateReadBack:
    """Recover every catalog-known literal from a set of template texts.

    Thin wrapper over :func:`auto_ext.core.readback.read_back_from_templates`
    that restates the failure in this module's own error type, so a caller
    catching :class:`MigrationError` around a migration still catches
    everything the migration can fail with.
    """

    try:
        return _read_back(texts, catalog=catalog, owners=owners)
    except ReadBackError as exc:
        raise MigrationError(str(exc)) from exc


# ---- template resolution -----------------------------------------------------


def _resolve_template_file(
    raw: Path | None, *, template_root: Path, repo_root: Path
) -> Path | None:
    """Find the file a ``project.yaml`` template slot names.

    Explicit candidates only, in a fixed order, and no cwd-relative probe:
    :func:`auto_ext.core.template.resolve_template_path` starts with
    ``path.is_file()``, which resolves against whatever the cwd happens to be
    and lets a test standing in the repository silently hit the repository's
    own templates instead of the ones it deployed.
    """

    if raw is None:
        return None
    if raw.is_absolute():
        return raw if raw.is_file() else None
    parts = raw.parts
    candidates = [template_root / raw]
    if parts and parts[0] == template_root.name:
        candidates.append(template_root.parent / raw)
        candidates.append(template_root / Path(*parts[1:]) if len(parts) > 1 else template_root)
    candidates.append(repo_root / raw)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _quantus_target(path: Path | None, text: str | None) -> RenderTarget | None:
    """Which of the two Quantus targets a template file is.

    Decided by ``output_db -type`` in the file itself, because that is the
    line that actually determines what the stage emits. The file name is only
    the fallback for a file too broken to parse.
    """

    if path is None:
        return None
    if text is not None:
        raw = _parse_quantus(text).get(("output_db", "-type"))
        if raw is not None and raw.values:
            token = raw.values[0].strip().lower()
            if token == OutputKind.DSPF.value:
                return RenderTarget.QUANTUS_DSPF
            if token == OutputKind.EXTRACTED_VIEW.value:
                return RenderTarget.QUANTUS_EXT
    return RenderTarget.QUANTUS_DSPF if "dspf" in path.name.lower() else RenderTarget.QUANTUS_EXT


@dataclass(frozen=True)
class _TemplateSet:
    """The template files behind one task (or the project defaults)."""

    files: dict[RenderTarget, Path]
    texts: dict[RenderTarget, str]
    missing: tuple[str, ...]

    @property
    def fingerprint(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((target.value, str(path)) for target, path in self.files.items()))

    @property
    def emit(self) -> list[OutputKind]:
        kinds: list[OutputKind] = []
        if RenderTarget.QUANTUS_EXT in self.files:
            kinds.append(OutputKind.EXTRACTED_VIEW)
        if RenderTarget.QUANTUS_DSPF in self.files:
            kinds.append(OutputKind.DSPF)
        return kinds


def _build_template_set(
    slots: Mapping[str, Path | None], *, template_root: Path, repo_root: Path
) -> _TemplateSet:
    files: dict[RenderTarget, Path] = {}
    texts: dict[RenderTarget, str] = {}
    missing: list[str] = []
    for slot, raw in slots.items():
        resolved = _resolve_template_file(raw, template_root=template_root, repo_root=repo_root)
        if resolved is None:
            if raw is not None:
                missing.append(f"{slot}: {raw}")
            continue
        try:
            text = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise MigrationError(f"cannot read template {resolved}: {exc}") from exc
        if slot == "quantus":
            target = _quantus_target(resolved, text)
        else:
            target = _SLOT_TARGETS.get(slot)
        if target is None:
            continue
        files[target] = resolved
        texts[target] = text
    return _TemplateSet(files=files, texts=texts, missing=tuple(missing))


# ---- grouping ----------------------------------------------------------------


@dataclass(frozen=True)
class _GroupKey:
    """Everything that makes two tasks need two different Recipes.

    Identity axes are excluded on purpose: a Recipe that named a cell would
    not be portable, which is the one property a Recipe has to keep.
    """

    knobs: tuple[tuple[str, tuple[tuple[str, Any], ...]], ...]
    #: The reduction *parameters* as they will land, never the on/off switch:
    #: whether Jivaro runs is a per-dispatch decision since 2026-09-04, and
    #: splitting a library over it would hand the user two identical recipes.
    #: Resolved rather than raw, so a task that says nothing and a task that
    #: says the default do not become two indistinguishable recipes.
    jivaro: tuple[float, float]
    continue_on_lvs_fail: bool
    templates: tuple[tuple[str, str], ...]


@dataclass
class _Group:
    key: _GroupKey
    members: list[TaskConfigV1]
    template_set: _TemplateSet
    knobs: dict[str, dict[str, Any]]


def _freeze_knobs(knobs: Mapping[str, Mapping[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (stage, tuple(sorted((name, value) for name, value in values.items())))
        for stage, values in sorted(knobs.items())
    )


def _effective_knobs(
    task: TaskConfigV1, project: ProjectConfigV1, template_set: _TemplateSet
) -> dict[str, dict[str, Any]]:
    """manifest default < project.knobs < task.knobs, per stage.

    What the v1 runner resolved at render time, minus the ``--knob`` CLI layer,
    which was per-run and therefore has nothing to migrate into. A stage whose
    sidecar manifest is already gone keeps the user's own override values
    verbatim -- see :func:`auto_ext.legacy_v1.resolve_knob_values_v1`.
    """

    resolved: dict[str, dict[str, Any]] = {}
    stage_files: dict[str, Path] = {}
    for target, path in template_set.files.items():
        stage_files.setdefault(_TARGET_STAGE[target], path)
    for stage in sorted(set(project.knobs) | set(task.knobs) | set(stage_files)):
        path = stage_files.get(stage)
        manifest = load_manifest_v1(path) if path is not None else None
        try:
            values = resolve_knob_values_v1(
                manifest,
                dict(project.knobs.get(stage, {})),
                dict(task.knobs.get(stage, {})),
            )
        except ConfigError as exc:
            raise MigrationError(f"task {task.task_id}: cannot resolve {stage} knobs: {exc}") from exc
        if values:
            resolved[stage] = values
    return resolved


# ---- recipe naming -----------------------------------------------------------


#: Short id tokens for the extraction types a migrated recipe can carry.
#: Not exhaustive over :class:`ExtractType` on purpose -- the fallback below
#: is ``ext``, so a member with no token here still produces a usable id, and
#: adding fifteen abbreviations nobody has ever typed would make the ids
#: harder to read rather than easier. The two ``c_only`` spellings both get a
#: token because telling them apart is the point of having them.
_EXTRACT_TOKENS: dict[str, str] = {
    ExtractType.RC_COUPLED.value: "rc",
    ExtractType.RC_DECOUPLED.value: "rcd",
    ExtractType.R_ONLY.value: "r",
    ExtractType.C_ONLY_COUPLED.value: "c",
    ExtractType.C_ONLY_DECOUPLED.value: "cd",
    ExtractType.NONE.value: "noext",
}


def _number_token(value: float, suffix: str = "") -> str:
    if float(value).is_integer():
        return f"{int(value)}{suffix}"
    return f"{value}".replace(".", "p") + suffix


def _extract_token(recipe: Recipe) -> str:
    """The id token for a recipe's extraction, now that there may be several.

    One rule keeps the id it always had. More than one gets the FIRST rule's
    token plus a count -- ``rc+2`` -- because the first rule is the one that
    covers the whole chip in the vendor's own downgrade pattern, and a name
    that silently reported only that would hide the very thing the extra
    rules were added to do.
    """

    rules = recipe.extraction.extract
    head = _EXTRACT_TOKENS.get(rules[0].type.value, "ext") if rules else "ext"
    return head if len(rules) <= 1 else f"{head}+{len(rules) - 1}"


def _base_tokens(recipe: Recipe) -> list[str]:
    tokens = [_extract_token(recipe)]
    if recipe.extraction.corner:
        tokens.append(str(recipe.extraction.corner))
    if recipe.extraction.temperature_c is not None:
        tokens.append(_number_token(recipe.extraction.temperature_c, "c"))
    return tokens


def _human_name(recipe: Recipe) -> str:
    """A display name that says what the recipe does, derived like the id."""

    rules = recipe.extraction.extract
    bits = [
        rules[0].type.value
        if len(rules) == 1
        else f"{rules[0].type.value} + {len(rules) - 1} more rule"
        + ("" if len(rules) == 2 else "s")
    ]
    if recipe.extraction.corner:
        bits.append(f"corner {recipe.extraction.corner}")
    if recipe.extraction.temperature_c is not None:
        bits.append(f"{_number_token(recipe.extraction.temperature_c)}C")
    bits.append("+".join(kind.value for kind in recipe.output.emit) or "no output")
    # No "with reduction" bit any more: whether Jivaro runs is decided on the
    # run bar per dispatch (2026-09-04), so it cannot be part of what names a
    # recipe. The reduction *parameters* still are, via _discriminators.
    return ", ".join(bits)


def _jivaro_key(task: Any) -> tuple[float, float]:
    """The reduction parameters this task will actually put in a Recipe.

    Two things are deliberately absent. ``enabled`` is not here because since
    2026-09-04 nothing on a Recipe decides whether the reduction runs -- the
    run bar's jivaro box does -- so two cells that differed only in that used
    to become ``...-red`` and ``...-nored``, and would now become two byte
    identical recipes with a number on the end.

    And the values are *resolved* against the schema defaults rather than
    taken raw, because ``jivaro: {enabled: false}`` leaves both at ``None``
    while ``jivaro: {frequency_limit: 14, error_max: 2}`` states them, and
    those two produce the same Recipe. Keying on the raw pair split them
    anyway, which is the same defect one level down.
    """

    defaults = ReductionSettings()
    limit = task.jivaro.frequency_limit
    error = task.jivaro.error_max
    return (
        float(limit) if limit is not None else float(defaults.frequency_limit_ghz),
        float(error) if error is not None else float(defaults.error_max_pct),
    )


def _discriminators(recipe: Recipe) -> list[str]:
    """Extra name parts, in the order they get added to break a tie."""

    emit = "-".join(
        "view" if kind is OutputKind.EXTRACTED_VIEW else "dspf" for kind in recipe.output.emit
    )
    return [
        emit or "noout",
        str(recipe.lvs.deck_variant),
        "cbn" if recipe.lvs.connect_by_name else "nocbn",
        _number_token(recipe.reduction.frequency_limit_ghz, "ghz"),
        _number_token(recipe.reduction.error_max_pct, "pct"),
        "lenient" if recipe.policy.continue_on_lvs_fail else "strict",
        f"fl{recipe.extraction.exclude_floating_nets_limit}",
    ]


def _derive_recipe_ids(recipes: Sequence[Recipe]) -> list[str]:
    """Names derived from the parameters, unique, and stable across runs.

    A tie is broken by appending the first discriminator that actually
    differs, so two recipes that differ only in reduction settings end up
    ``...-red`` and ``...-nored`` rather than ``migrated-1`` and
    ``migrated-2``. A numeric suffix is the last resort.
    """

    parts = [_base_tokens(recipe) for recipe in recipes]
    extras = [_discriminators(recipe) for recipe in recipes]

    def names() -> list[str]:
        return [slugify("-".join(token for token in group if token), max_len=64) for group in parts]

    for level in range(len(extras[0]) if extras else 0):
        current = names()
        if len(set(current)) == len(current):
            break
        clusters: dict[str, list[int]] = {}
        for index, name in enumerate(current):
            clusters.setdefault(name, []).append(index)
        for indices in clusters.values():
            if len(indices) < 2:
                continue
            # Only append a discriminator that actually separates this
            # cluster; appending one that does not just makes every name
            # longer without making any of them distinguishable.
            if len({extras[index][level] for index in indices}) < 2:
                continue
            for index in indices:
                parts[index].append(extras[index][level])

    final = names()
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in final:
        if name in seen:
            seen[name] += 1
            result.append(slugify(f"{name}-{seen[name]}", max_len=64))
        else:
            seen[name] = 1
            result.append(name)
    return result


# ---- YAML writing ------------------------------------------------------------


def _commented(payload: Any) -> Any:
    if isinstance(payload, dict):
        out = CommentedMap()
        for key, value in payload.items():
            out[key] = _commented(value)
        return out
    if isinstance(payload, list):
        return [_commented(item) for item in payload]
    return payload


def _attach_comments(tree: CommentedMap, comments: Mapping[str, str]) -> None:
    """Attach a comment before each dotted key path that exists in ``tree``."""

    for path, text in comments.items():
        parts = path.split(".")
        node: Any = tree
        for part in parts[:-1]:
            if not isinstance(node, CommentedMap) or part not in node:
                node = None
                break
            node = node[part]
        if not isinstance(node, CommentedMap) or parts[-1] not in node:
            continue
        indent = 2 * (len(parts) - 1)
        node.yaml_set_comment_before_after_key(parts[-1], before=text, indent=indent)


def _render_yaml(payload: Mapping[str, Any], *, header: str, comments: Mapping[str, str]) -> str:
    tree = _commented(dict(payload))
    tree.yaml_set_start_comment(header)
    _attach_comments(tree, comments)
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 100
    buffer = StringIO()
    yaml.dump(tree, buffer)
    return buffer.getvalue()


def _write_once(
    path: Path,
    text: str,
    *,
    report: MigrationReport,
    loader: Callable[[Path], Any],
    expected: Any,
) -> None:
    """Write ``text`` unless ``path`` exists; then read it back and verify.

    Never overwriting is what makes a second run a no-op and what protects a
    file the user has since edited by hand. The read-back is the guarantee
    that what landed on disk still validates as the object the migration
    built -- a YAML dump that quietly loses a field would otherwise only
    surface at the next run.
    """

    if path.exists():
        report.skipped.append(path)
        try:
            identical = path.read_text(encoding="utf-8") == text
        except OSError as exc:
            raise MigrationError(f"cannot read the existing {path}: {exc}") from exc
        if identical:
            report.warnings.append(f"{path} already exists with identical content; left alone")
        else:
            report.warnings.append(
                f"{path} already exists and differs from what this migration would "
                "write; left alone. Delete it and re-run if you want it regenerated."
            )
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise MigrationError(f"cannot write {path}: {exc}") from exc

    reloaded = loader(path)
    if reloaded.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise MigrationError(
            f"{path} does not read back as the object that was written; "
            "the migration refuses to leave a file it cannot reload"
        )
    report.written.append(path)


# ---- the migration itself ----------------------------------------------------


def migrate_v1_to_v2(
    project_yaml: Path,
    tasks_yaml: Path,
    *,
    template_root: Path | None = None,
    catalog_root: Path | None = None,
    out_root: Path,
    profile_id: str | None = None,
    recipe_name_hint: str = "migrated",
    resolve: Callable[[MigrationDecision], Any] | None = None,
    seed_patches: bool = False,
    write: bool = True,
    now: datetime | None = None,
) -> MigrationReport:
    """Convert one v1 config pair into the v2 file set.

    ``template_root`` is the ``templates/`` directory the old config points
    into (defaults to the one shipped with this build). ``catalog_root`` is a
    directory or file holding an ``options.yaml``; ``None`` uses the built-in
    catalog. ``resolve`` is asked about every :class:`MigrationDecision` and
    may return the default by simply not being passed. ``now`` fixes the
    timestamps written into the generated objects, which is what makes two
    runs byte-comparable.

    ``write=False`` computes everything and touches nothing -- the ``--dry-run``
    path. ``seed_patches=True`` raises :class:`NotImplementedError`: it needs
    the catalog-driven renderer that lands in C2 (see the module docstring).

    Deviations from the ``01-schema.md`` pseudocode, all deliberate:

    * groups are ordered by first appearance rather than by sorting a
      ``frozenset`` (sorting sets is not a total order, so the pseudocode's
      recipe numbering would not be reproducible);
    * recipe ids are derived from the parameters (``rc-typical-55c``) instead
      of ``migrated-1``, so a re-run produces the same file names -- the
      ``recipe.<n>.id`` decision still lets the user override each one, and
      ``recipe_name_hint`` is the fallback stem when a recipe has too few
      distinguishing parameters to name itself;
    * ``PdkProfile.checks`` is left empty, because
      :func:`auto_ext.core.health.default_checks` derives the list from the
      profile and pinning a copy into the file only makes it go stale;
    * the old files are not archived and ``logs/`` / ``runs/`` are not
      touched.
    """

    if seed_patches:
        raise NotImplementedError(
            "seed_patches is not implemented. It needs a side-by-side render of "
            "the old template and the new catalog path, and the old side needs a "
            "full legacy render context (resolved env, output_dir, employee_id) "
            "that the migration deliberately does not build -- a migration that "
            "needed a live PDK environment could not be run before the move. "
            "Wire it to auto_ext.core.render once that context can be faked "
            "safely; until then run with seed_patches=False and treat "
            "MigrationReport.warnings as the statement of what was not verified."
        )

    project_yaml = Path(project_yaml)
    tasks_yaml = Path(tasks_yaml)
    out_root = Path(out_root)
    stamp = now or utcnow()
    ask = resolve or (lambda decision: decision.default)

    catalog = _load_catalog(catalog_root)
    templates_dir = Path(template_root) if template_root is not None else default_templates_root()
    repo_root = templates_dir.parent

    project = load_project_v1(project_yaml)
    tasks, raw_tasks = load_tasks_v1_with_raw(tasks_yaml, project)
    specs = _raw_specs(raw_tasks, tasks_yaml)

    dispositions: list[FieldDisposition] = []
    decisions: list[MigrationDecision] = []
    answers: dict[str, Any] = {}
    shipped_fallbacks: list[str] = []
    warnings: list[str] = [
        "byte fidelity against the old templates was NOT verified: the "
        "catalog-driven renderer (C2) does not exist yet, so seed_patches is "
        "unavailable. Every value the catalog knows about was read back from "
        "your template text, but a structural edit (an added or removed line) "
        "is not represented anywhere."
    ]

    def decide(
        key: str, question: str, options: Sequence[str], default: Any, context: str = ""
    ) -> Any:
        decision = MigrationDecision(
            key=key,
            question=question,
            options=list(options),
            default=default,
            context=context,
        )
        decisions.append(decision)
        answer = ask(decision)
        if decision.options and answer not in decision.options:
            raise MigrationError(
                f"decision {key!r}: {answer!r} is not one of {decision.options}"
            )
        answers[key] = answer
        return answer

    # ---- 1. the project-level template set, and what it says ----------------
    project_slots = {
        "si": project.templates.si,
        "calibre": project.templates.calibre,
        "quantus": project.templates.quantus,
        "jivaro": project.templates.jivaro,
    }
    project_templates = _build_template_set(
        project_slots, template_root=templates_dir, repo_root=repo_root
    )
    for slot, raw in project_slots.items():
        if raw is None:
            dispositions.append(
                FieldDisposition(
                    f"project.yaml:templates.{slot}",
                    "dropped",
                    None,
                    "slot was empty; the catalog supplies this target",
                )
            )
        else:
            dispositions.append(
                FieldDisposition(
                    f"project.yaml:templates.{slot}",
                    "dropped" if slot != "quantus" else "moved",
                    "recipe:output.emit" if slot == "quantus" else None,
                    "the catalog owns the template; values were read back from this file",
                )
            )
    for missing in project_templates.missing:
        warnings.append(
            f"project.yaml templates: {missing} could not be found under "
            f"{templates_dir}; nothing was read back from it"
        )
    _warn_on_custom_templates(project_templates, catalog, warnings)

    readback = read_back_from_templates(project_templates.texts, catalog=catalog)

    # ---- 2. PdkProfile -----------------------------------------------------
    profile = _build_profile(
        project=project,
        project_yaml=project_yaml,
        templates_dir=templates_dir,
        template_set=project_templates,
        readback=readback,
        catalog=catalog,
        profile_id=profile_id,
        decide=decide,
        dispositions=dispositions,
        warnings=warnings,
        shipped_fallbacks=shipped_fallbacks,
        stamp=stamp,
    )

    # ---- 3. groups -> recipes ----------------------------------------------
    groups = _group_tasks(
        tasks=tasks,
        project=project,
        templates_dir=templates_dir,
        repo_root=repo_root,
        warnings=warnings,
    )
    recipes = [
        _recipe_from_group(
            group=group,
            profile=profile,
            catalog=catalog,
            dispositions=dispositions,
            warnings=warnings,
            stamp=stamp,
        )
        for group in groups
    ]
    ids = _derive_recipe_ids(recipes) if recipes else []
    for index, (group, recipe, derived) in enumerate(zip(groups, recipes, ids), start=1):
        if not derived:
            derived = f"{recipe_name_hint}-{index}"
        chosen = decide(
            key=f"recipe.{index}.id",
            question=(
                f"Group {index} covers {len(group.members)} cell(s). "
                "What should this recipe be called? The default is derived from its "
                "parameters; rename it to whatever your team calls this run."
            ),
            options=[],
            default=derived,
            context=", ".join(member.task_id for member in group.members),
        )
        recipe.recipe_id = slugify(str(chosen), max_len=64)
        recipe.name = _human_name(recipe)

    chosen_ids = [recipe.recipe_id for recipe in recipes]
    duplicate_ids = sorted({rid for rid in chosen_ids if chosen_ids.count(rid) > 1})
    if duplicate_ids:
        raise MigrationError(
            f"two recipes ended up with the same id: {duplicate_ids}; "
            "answer the recipe.<n>.id decisions with distinct names"
        )

    # ---- 4. cells ----------------------------------------------------------
    cells = _build_cells(
        tasks=tasks,
        specs=specs,
        decide=decide,
        dispositions=dispositions,
        warnings=warnings,
    )

    # ---- 5. workspace ------------------------------------------------------
    workspace = _build_workspace(
        project=project,
        profile=profile,
        tasks=tasks,
        decide=decide,
        dispositions=dispositions,
        warnings=warnings,
    )

    # ---- 6. resources ------------------------------------------------------
    resources = _build_resources(readback, dispositions=dispositions)

    # ---- 7. bindings, and the cells that ended up in more than one recipe ---
    bindings: dict[str, list[str]] = {}
    for group, recipe in zip(groups, recipes):
        bindings[recipe.recipe_id] = [_task_key(member) for member in group.members]
    _check_multi_recipe_cells(bindings, decide=decide, warnings=warnings)

    report = MigrationReport(
        profile=profile,
        recipes=recipes,
        cells=cells,
        workspace=workspace,
        resources=resources,
        bindings=bindings,
        dispositions=dispositions,
        decisions=decisions,
        answers=answers,
        warnings=warnings,
        shipped_fallbacks=shipped_fallbacks,
    )
    report.open_questions = _collect_open_questions(catalog, report)
    for key, (was, now_value) in sorted(readback.diverged.items()):
        report.warnings.append(
            f"template read-back: {key} is {now_value!r} in your templates, not the "
            f"catalog default {was!r}; the migrated value is yours"
        )

    if write:
        _write_all(report, out_root=out_root, project_yaml=project_yaml, tasks_yaml=tasks_yaml)

    logger.info(
        "migrated %s + %s -> %d recipe(s), %d cell row(s), %d file(s) written",
        project_yaml,
        tasks_yaml,
        len(report.recipes),
        len(report.cells),
        len(report.written),
    )
    return report


def _load_catalog(catalog_root: Path | None) -> Catalog:
    if catalog_root is None:
        return builtin_catalog()
    root = Path(catalog_root)
    path = root if root.is_file() else root / "options.yaml"
    return load_catalog(path)


def _raw_specs(raw_tasks: Any, tasks_yaml: Path) -> list[TaskSpecV1]:
    """Re-validate the raw entries so ``exclude`` is visible again.

    ``load_tasks`` resolves ``exclude`` during expansion and the resolved
    ``TaskConfigV1`` list no longer mentions it. The migration has to report
    what was excluded, so the raw entries are validated a second time.
    """

    if isinstance(raw_tasks, dict):
        entries = raw_tasks.get("tasks", [])
    else:
        entries = raw_tasks
    specs: list[TaskSpecV1] = []
    for index, entry in enumerate(entries):
        try:
            specs.append(TaskSpecV1.model_validate(_plain(entry)))
        except ValidationError as exc:
            raise MigrationError(f"{tasks_yaml} [entry #{index}]: {exc}") from exc
    return specs


def _plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _plain(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_plain(value) for value in obj]
    return obj


def _task_key(task: TaskConfigV1) -> str:
    return f"{task.library}__{task.cell}__{task.lvs_layout_view}__{task.lvs_source_view}"


def _warn_on_custom_templates(
    template_set: _TemplateSet, catalog: Catalog, warnings: list[str]
) -> None:
    """Flag templates that are not the ones the catalog was built from.

    Modelled values still migrate (they are read back from the user's file),
    but an inserted or deleted line is invisible to the catalog and there is
    no patch machinery yet to carry it. Saying so is the whole point.
    """

    for target, path in template_set.files.items():
        try:
            builtin = catalog.target(target).template_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if template_set.texts[target] != builtin:
            warnings.append(
                f"{path} differs from the built-in {target} template. Values the "
                "catalog models were read back from your file, but any added or "
                "removed line is not represented in the migrated config; re-check "
                "it once the patch editor lands."
            )


# ---- profile -----------------------------------------------------------------


def _build_profile(
    *,
    project: ProjectConfigV1,
    project_yaml: Path,
    templates_dir: Path,
    template_set: _TemplateSet,
    readback: TemplateReadBack,
    catalog: Catalog,
    profile_id: str | None,
    decide: Callable[..., Any],
    dispositions: list[FieldDisposition],
    warnings: list[str],
    shipped_fallbacks: list[str],
    stamp: datetime,
) -> PdkProfile:
    paths = dict(project.paths)
    lvs_dir = paths.pop("calibre_lvs_dir", None)
    qrc_dir = paths.pop("qrc_deck_dir", None)

    pid = slugify(profile_id or project.tech_name or "default", max_len=64)

    # Tables a legacy config CANNOT supply, no matter how complete it is: the
    # old script had no concept of a corner table, a deck-variant table or a
    # supply-net list, so there is nothing in project.yaml or in the templates
    # to read them back from. Writing them empty is honest but useless -- it
    # is a blocking `check-env` failure on a migration that otherwise
    # succeeded, and the only way out was to hand-merge two YAML files (the
    # shipped profile has the PDK facts, the migrated one has the site's real
    # paths; neither is a superset). So: fall back to the shipped profile, and
    # say loudly that these rows are not the user's.
    #
    # Only ever when the table is EMPTY. A value read back from the user's own
    # templates is never replaced -- value-neutrality is the whole point of
    # the migration, and it still holds for everything the templates can show.
    shipped = builtin_profile()

    def _seeded_from_shipped(field_name: str, note: str) -> None:
        """Record that ``field_name`` did not come from the user's config."""

        shipped_fallbacks.append(field_name)
        dispositions.append(
            FieldDisposition(
                f"(nothing in {project_yaml.name} or the legacy templates)",
                "seeded_from_shipped_profile",
                f"profile:{pid}.{field_name}",
                note,
            )
        )

    variants, default_variant = _lvs_variants(templates_dir, template_set, warnings)
    if not variants and shipped is not None and shipped.lvs_decks.variants:
        variants = [v.model_copy(deep=True) for v in shipped.lvs_decks.variants]
        default_variant = shipped.lvs_decks.default_variant
        _seeded_from_shipped(
            "lvs_decks.variants",
            f"{len(variants)} variant(s) from {BUILTIN_PROFILE_PATH.name}: "
            f"{', '.join(v.name for v in variants)}",
        )
        warnings.append(
            "lvs_decks.variants came from the profile shipped with this build, not "
            "from your config -- a legacy config has no deck-variant table to read. "
            f"Check that {', '.join(v.rules_suffix for v in variants)} are the deck "
            "suffixes this PDK actually has."
        )

    lvs_decks = LvsDeckSet(
        dir_expr=lvs_dir,
        basename=None,
        filename_pattern=readback.get("lvs_rules_filename_pattern", "{basename}.{suffix}.qcilvs"),
        variants=variants,
        default_variant=default_variant,
    )
    qrc = QrcDeck(
        dir_expr=qrc_dir,
        query_cmd_name=readback.get("qrc_query_cmd_name", "query_cmd"),
        preserve_cell_list_name=readback.get(
            "qrc_preserve_cell_list_name", "preserveCellList.txt"
        ),
    )

    corner_literal = readback.get("technology_corner")
    corners: list[CornerSpec] = []
    default_corner: str | None = None
    if corner_literal:
        semantic = decide(
            key="corner.semantic_name",
            question=(
                f'The templates hardcode -technology_corner "{corner_literal}". '
                "What is this corner called in your team's vocabulary? The name "
                "becomes the portable handle a Recipe refers to."
            ),
            options=[],
            default=slugify(str(corner_literal), max_len=64),
            context=f"literal read back from {templates_dir}",
        )
        default_corner = slugify(str(semantic), max_len=64)
        corners = [
            CornerSpec(
                name=default_corner,
                technology_corner=str(corner_literal),
                description="Read back from the migrated templates.",
            )
        ]
        warnings.append(
            f"the corner table holds exactly one corner ({corner_literal}); the "
            f"templates cannot show any other. Add the rest by hand in "
            f"config/profiles/{pid}.yaml -- a Recipe is only portable across "
            "corners that exist in this table."
        )
        dispositions.append(
            FieldDisposition(
                "templates/quantus/*.cmd.j2:-technology_corner",
                "seeded_from_template",
                f"profile:{pid}.corners[0]",
                f"literal {corner_literal!r} -> corner {default_corner!r}",
            )
        )
    elif shipped is not None and shipped.corners:
        corners = [c.model_copy(deep=True) for c in shipped.corners]
        default_corner = shipped.default_corner
        _seeded_from_shipped(
            "corners",
            f"{len(corners)} corner(s) from {BUILTIN_PROFILE_PATH.name}: "
            f"{', '.join(c.name for c in corners)}",
        )
        warnings.append(
            "no -technology_corner literal was found in the templates, so the corner "
            f"table came from the profile shipped with this build ({len(corners)} "
            f"corners, default {default_corner!r}) -- NOT from your config. Check the "
            "names against this PDK before trusting a Recipe to be portable across them."
        )
    else:
        warnings.append(
            "no -technology_corner literal was found in the templates; the profile "
            "ships with an empty corner table and every Recipe leaves the corner unset"
        )

    parasitics = ParasiticDeviceContract(
        res_component=readback.get("res_component", "presistor"),
        cap_component=readback.get("cap_component", "pcapacitor"),
        res_model=readback.get("parasitic_res_model", "analogLib/presistor/symbol"),
        cap_model=readback.get("parasitic_cap_model", "analogLib/pcapacitor/symbol"),
        ind_model=readback.get("parasitic_ind_model", "analogLib/pinductor/symbol"),
        mutual_model=readback.get("parasitic_mutual_model", "analogLib/pmind/symbol"),
    )

    # The supply-net lists usually DO come back from the templates' *lvsPowerNames
    # / *lvsGroundNames lines. When they do not, the templates simply did not
    # carry those lines, which is again nothing to be neutral about.
    power_names = list(readback.get("power_names", []))
    ground_names = list(readback.get("ground_names", []))
    if shipped is not None:
        for field_name, current, from_shipped in (
            ("power_names", power_names, shipped.power_names),
            ("ground_names", ground_names, shipped.ground_names),
        ):
            if current or not from_shipped:
                continue
            current.extend(from_shipped)
            _seeded_from_shipped(
                field_name, f"{len(from_shipped)} name(s) from {BUILTIN_PROFILE_PATH.name}"
            )
            warnings.append(
                f"{field_name} came from the profile shipped with this build "
                f"({len(from_shipped)} names), not from your templates, which carry no "
                "such line. LVS treats these as the supply nets -- check them."
            )

    cdl_includes = readback.get("cdl_include_file")
    profile = PdkProfile(
        profile_id=pid,
        display_name=project.tech_name or "migrated PDK",
        description=f"Migrated from {project_yaml.name} and the templates it pointed at.",
        tech_name=project.tech_name,
        tech_name_env_vars=list(project.tech_name_env_vars),
        tech_library_file=readback.get(
            "technology_library_file", "$env(SETUP_ROOT)/assura_tech.lib"
        ),
        layer_map=str(project.layer_map),
        cdl_include_files=[cdl_includes] if cdl_includes else ["$calibre_source_added_place"],
        env_overrides=dict(project.env_overrides),
        required_env=[],
        lvs_decks=lvs_decks,
        qrc=qrc,
        extra_paths=paths,
        corners=corners,
        default_corner=default_corner,
        power_names=power_names,
        ground_names=ground_names,
        parasitics=parasitics,
        checks=[],
        discovered_from=[str(project_yaml), str(templates_dir)],
        scanned_at=stamp,
        hand_edited=False,
    )
    profile.required_env = _required_env(project, profile)

    for source, target in (
        ("tech_name", "tech_name"),
        ("tech_name_env_vars", "tech_name_env_vars"),
        ("layer_map", "layer_map"),
        ("env_overrides", "env_overrides"),
    ):
        dispositions.append(
            FieldDisposition(f"project.yaml:{source}", "moved", f"profile:{pid}.{target}")
        )
    if lvs_dir is not None:
        dispositions.append(
            FieldDisposition(
                "project.yaml:paths.calibre_lvs_dir", "moved", f"profile:{pid}.lvs_decks.dir_expr"
            )
        )
    if qrc_dir is not None:
        dispositions.append(
            FieldDisposition(
                "project.yaml:paths.qrc_deck_dir", "moved", f"profile:{pid}.qrc.dir_expr"
            )
        )
    for key in paths:
        dispositions.append(
            FieldDisposition(
                f"project.yaml:paths.{key}", "moved", f"profile:{pid}.extra_paths.{key}"
            )
        )
    for source in ("work_root", "verify_root", "setup_root"):
        if getattr(project, source) is not None:
            dispositions.append(
                FieldDisposition(
                    f"project.yaml:{source}",
                    "dropped",
                    None,
                    "never consumed by any code path; read the shell variable instead",
                )
            )
    if project.employee_id:
        dispositions.append(
            FieldDisposition(
                "project.yaml:employee_id",
                "decision",
                "~/.auto_ext/site.yaml:employee_id",
                "site-level, not project-level; this migration does not write to your home dir",
            )
        )
        warnings.append(
            f"project.yaml carries employee_id={project.employee_id!r}. It is a "
            "site setting now: put it in ~/.auto_ext/site.yaml by hand. Nothing "
            "was written outside the output directory."
        )

    if qrc_dir and re.search(r"<[^>]+>", qrc_dir):
        placeholder = decide(
            key="pdk.qrc_deck_dir",
            question=(
                f"qrc_deck_dir still contains unfilled placeholders: {qrc_dir!r}. "
                "Fill in the real runset version and PDK subdirectory -- the profile "
                "health check cannot pass until the directory exists."
            ),
            options=[],
            default=qrc_dir,
            context="project.yaml:paths.qrc_deck_dir",
        )
        profile.qrc = QrcDeck(
            dir_expr=str(placeholder),
            query_cmd_name=profile.qrc.query_cmd_name,
            preserve_cell_list_name=profile.qrc.preserve_cell_list_name,
        )
        if str(placeholder) == qrc_dir:
            warnings.append(
                f"qrc_deck_dir is still {qrc_dir!r}: the <...> parts are placeholders "
                "from the sample config, not a real path. Edit the profile before running."
            )

    for key, reason in sorted(readback.unread.items()):
        option = catalog.option(key)
        if option.owner is Owner.PROFILE and option.currently is Currently.HARDCODED_LITERAL:
            warnings.append(f"profile field {key} kept its catalog default: {reason}")

    return profile


def _lvs_variants(
    templates_dir: Path, template_set: _TemplateSet, warnings: list[str]
) -> tuple[list[LvsDeckVariant], str | None]:
    """LVS deck variants come from the calibre manifest's ``lvs_variant`` knob.

    It is the only place the alternatives are written down; the template text
    only ever shows the one that is currently selected.
    """

    path = template_set.files.get(RenderTarget.LVS_QCI)
    if path is None:
        return [], None
    manifest = load_manifest_v1(path)
    knob = manifest.knobs.get("lvs_variant") if manifest is not None else None
    if knob is None or not knob.choices:
        warnings.append(
            "no lvs_variant knob with choices was found next to the calibre template; "
            "the profile ships without a deck-variant table and Recipe.lvs.deck_variant "
            "keeps its default"
        )
        return [], None
    variants = [
        LvsDeckVariant(name=slugify(str(choice), max_len=64), rules_suffix=str(choice))
        for choice in knob.choices
    ]
    default = slugify(str(knob.default), max_len=64) if knob.default else None
    return variants, default


def _required_env(project: ProjectConfigV1, profile: PdkProfile) -> list[str]:
    """Env vars the profile's own path expressions depend on.

    Scanned from the path expressions rather than from the template text: a
    template mentions ``$env(SETUP_ROOT)`` inside a value that the profile
    already carries, and scanning the whole file would also pick up the
    synthetic ``${output_dir}`` tokens, which are not shell variables at all.
    """

    sources = [
        str(profile.layer_map),
        str(profile.tech_library_file),
        *[str(item) for item in profile.cdl_include_files],
        *[str(value) for value in profile.extra_paths.values()],
        project.extraction_output_dir,
        project.intermediate_dir,
        project.dspf_out_path,
    ]
    if profile.lvs_decks.dir_expr:
        sources.append(str(profile.lvs_decks.dir_expr))
    if profile.qrc.dir_expr:
        sources.append(str(profile.qrc.dir_expr))
    found = discover_required_vars(sources)
    ignored = _PATH_TOKENS | set(project.paths)
    return sorted(name for name in found if name not in ignored)


# ---- recipes -----------------------------------------------------------------


def _group_tasks(
    *,
    tasks: Sequence[TaskConfigV1],
    project: ProjectConfigV1,
    templates_dir: Path,
    repo_root: Path,
    warnings: list[str],
) -> list[_Group]:
    """One group per distinct set of render parameters, in first-seen order."""

    groups: dict[_GroupKey, _Group] = {}
    cache: dict[tuple[Any, ...], _TemplateSet] = {}
    for task in tasks:
        slots = {
            "si": task.templates.si,
            "calibre": task.templates.calibre,
            "quantus": task.templates.quantus,
            "jivaro": task.templates.jivaro,
        }
        cache_key = tuple(str(value) for value in slots.values())
        template_set = cache.get(cache_key)
        if template_set is None:
            template_set = _build_template_set(
                slots, template_root=templates_dir, repo_root=repo_root
            )
            cache[cache_key] = template_set
            for missing in template_set.missing:
                warnings.append(
                    f"task {task.task_id}: template {missing} could not be found under "
                    f"{templates_dir}; nothing was read back from it"
                )
        knobs = _effective_knobs(task, project, template_set)
        key = _GroupKey(
            knobs=_freeze_knobs(knobs),
            jivaro=_jivaro_key(task),
            continue_on_lvs_fail=task.continue_on_lvs_fail,
            templates=template_set.fingerprint,
        )
        group = groups.get(key)
        if group is None:
            groups[key] = _Group(
                key=key, members=[task], template_set=template_set, knobs=knobs
            )
        else:
            group.members.append(task)
    return list(groups.values())


def _assign(tree: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _recipe_from_group(
    *,
    group: _Group,
    profile: PdkProfile,
    catalog: Catalog,
    dispositions: list[FieldDisposition],
    warnings: list[str],
    stamp: datetime,
) -> Recipe:
    """Build one Recipe: catalog defaults, then the template, then the knobs.

    The layering is what makes the result value-preserving. The catalog holds
    what the shipped templates say; the read-back holds what *this project's*
    templates say and wins over it; the resolved knobs hold what the YAML said
    and win over both.
    """

    member_ids = ", ".join(member.task_id for member in group.members)
    readback = read_back_from_templates(group.template_set.texts, catalog=catalog)

    # Layer 1: the catalog's own defaults, i.e. what the templates shipped
    # with this build emit.
    base = recipe_from_catalog(
        recipe_id="migrated", name="Migrated recipe", catalog=catalog
    ).model_dump(mode="json")

    # Layer 2: what THIS project's templates say, which wins over the catalog.
    for option in catalog.by_owner(Owner.RECIPE):
        path = option.recipe_field_path
        if path is None or option.key not in readback.values:
            continue
        value = readback.values[option.key]
        _assign(base, path, value)
        if value != option.default:
            dispositions.append(
                FieldDisposition(
                    f"templates:{option.key}",
                    "seeded_from_template",
                    f"recipe:{path}",
                    f"your template says {value!r}, the catalog default is {option.default!r}",
                )
            )

    try:
        recipe = Recipe.model_validate(
            {
                **base,
                "recipe_id": "migrated",
                "name": "Migrated recipe",
                "description": f"Migrated from tasks: {member_ids}",
                "tags": ["migrated"],
                "updated_at": stamp,
            }
        )
    except ValidationError as exc:
        raise MigrationError(
            f"the values read back for [{member_ids}] do not form a valid recipe: {exc}"
        ) from exc

    # emit follows the quantus template the tasks in this group actually used
    emit = group.template_set.emit
    if emit:
        recipe.output.emit = emit
    else:
        warnings.append(
            f"group [{member_ids}] has no quantus template; output.emit keeps the "
            f"catalog default {[k.value for k in recipe.output.emit]}"
        )

    # knobs win over everything
    for stage, values in sorted(group.knobs.items()):
        for name, value in sorted(values.items()):
            option = catalog.by_template_var(name)
            if option is None or option.recipe_field_path is None:
                warnings.append(
                    f"knob {stage}.{name}={value!r} has no catalog row with a Recipe "
                    "field; the value is not represented in the migrated config"
                )
                dispositions.append(
                    FieldDisposition(
                        f"knobs:{stage}.{name}",
                        "dropped",
                        None,
                        "no catalog row maps this knob to a Recipe field",
                    )
                )
                continue
            _set_path(recipe, option.recipe_field_path, value)
            dispositions.append(
                FieldDisposition(
                    f"knobs:{stage}.{name}",
                    "folded",
                    f"recipe:{option.recipe_field_path}",
                    f"effective value {value!r}",
                )
            )

    # jivaro block -> reduction. Its parameters move; its on/off switch does
    # not, because since 2026-09-04 nothing on a Recipe decides whether the
    # reduction runs -- ticking jivaro on the run bar is the whole decision.
    jivaro = group.members[0].jivaro
    if jivaro.frequency_limit is not None:
        recipe.reduction.frequency_limit_ghz = float(jivaro.frequency_limit)
    if jivaro.error_max is not None:
        recipe.reduction.error_max_pct = float(jivaro.error_max)
    dispositions.append(
        FieldDisposition("tasks.yaml:jivaro", "moved", "recipe:reduction", f"cells [{member_ids}]")
    )
    dispositions.append(
        FieldDisposition(
            "tasks.yaml:jivaro.enabled",
            "dropped",
            "run bar / --stage jivaro",
            f"was {jivaro.enabled!r}; whether the reduction runs is a decision "
            f"about one dispatch, so it is ticked per run rather than stored "
            f"(cells [{member_ids}])",
        )
    )

    recipe.policy.continue_on_lvs_fail = group.members[0].continue_on_lvs_fail
    dispositions.append(
        FieldDisposition(
            "tasks.yaml:continue_on_lvs_fail", "moved", "recipe:policy.continue_on_lvs_fail"
        )
    )

    if profile.default_corner:
        recipe.extraction.corner = profile.default_corner
    return recipe


def _set_path(model: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    node = model
    for part in parts[:-1]:
        node = getattr(node, part)
    setattr(node, parts[-1], value)


# ---- cells -------------------------------------------------------------------


def _build_cells(
    *,
    tasks: Sequence[TaskConfigV1],
    specs: Sequence[TaskSpecV1],
    decide: Callable[..., Any],
    dispositions: list[FieldDisposition],
    warnings: list[str],
) -> CellBook:
    entries: list[CellEntry] = []
    seen: dict[str, TaskConfigV1] = {}
    for task in tasks:
        key = _task_key(task)
        previous = seen.get(key)
        if previous is not None:
            if (
                previous.ground_net != task.ground_net
                or previous.out_file != task.out_file
                or (previous.label or None) != (task.label or None)
            ):
                raise MigrationError(
                    f"tasks.yaml lists {key} twice with different settings "
                    f"(ground_net / out_file / label). The cell table allows one row "
                    "per combination, so pick one before migrating."
                )
            warnings.append(
                f"tasks.yaml lists {key} more than once; the cell table keeps one row"
            )
            continue
        seen[key] = task
        entries.append(
            CellEntry(
                library=task.library,
                cell=task.cell,
                layout_view=task.lvs_layout_view,
                source_view=task.lvs_source_view,
                ground_net=task.ground_net,
                out_file=task.out_file,
                display_name=task.label or None,
            )
        )

    excluded = _excluded_combinations(tasks, specs)
    if excluded:
        keep = decide(
            key="cells.excluded",
            question=(
                f"{len(excluded)} combination(s) were removed by an 'exclude' selector. "
                "Are they permanently out (no row at all), or parked for now "
                "(a row with enabled: false)?"
            ),
            options=["drop", "disable"],
            default="drop",
            context=", ".join(key for key, _ in excluded),
        )
        taken = set(seen)
        for key, entry in excluded:
            if key in taken:
                # Another spec produced this combination for real, or a second
                # spec excluded the same one. Either way the table already has
                # its row and a second one would fail the duplicate check.
                warnings.append(
                    f"combination {key} is excluded by one spec and already in the "
                    "table from another; the existing row is kept"
                )
                continue
            if keep == "disable":
                taken.add(key)
                entries.append(entry)
                dispositions.append(
                    FieldDisposition(
                        "tasks.yaml:exclude",
                        "moved",
                        f"cells:{key}.enabled=false",
                        "kept as a parked row",
                    )
                )
            else:
                dispositions.append(
                    FieldDisposition(
                        "tasks.yaml:exclude",
                        "dropped",
                        None,
                        f"combination {key} was excluded and no row was created",
                    )
                )

    for source, target in (
        ("library", "library"),
        ("cell", "cell"),
        ("lvs_layout_view", "layout_view"),
        ("lvs_source_view", "source_view"),
        ("ground_net", "ground_net"),
        ("out_file", "out_file"),
        ("label", "display_name"),
    ):
        dispositions.append(FieldDisposition(f"tasks.yaml:{source}", "moved", f"cells:{target}"))

    return CellBook(cells=entries)


def _excluded_combinations(
    tasks: Sequence[TaskConfigV1], specs: Sequence[TaskSpecV1]
) -> list[tuple[str, CellEntry]]:
    """Combinations a spec's ``exclude`` removed, recovered by subtraction.

    Computed as "full Cartesian product minus what the loader produced" so the
    selector semantics are not re-implemented here -- there is exactly one
    implementation of ``_is_excluded`` and this is not a second one.
    """

    produced: dict[int, set[str]] = {}
    for task in tasks:
        produced.setdefault(task.spec_index, set()).add(_task_key(task))

    out: list[tuple[str, CellEntry]] = []
    for index, spec in enumerate(specs):
        if not spec.exclude:
            continue
        kept = produced.get(index, set())
        for library in _as_list(spec.library):
            for cell in _as_list(spec.cell):
                for layout in _as_list(spec.lvs_layout_view):
                    for source in _as_list(spec.lvs_source_view):
                        key = f"{library}__{cell}__{layout}__{source}"
                        if key in kept:
                            continue
                        out.append(
                            (
                                key,
                                CellEntry(
                                    library=library,
                                    cell=cell,
                                    layout_view=layout,
                                    source_view=source,
                                    ground_net=spec.ground_net,
                                    out_file=spec.out_file,
                                    display_name=spec.label or None,
                                    enabled=False,
                                    note="excluded in tasks.yaml before the migration",
                                ),
                            )
                        )
    return out


def _as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


# ---- workspace ---------------------------------------------------------------


def _build_workspace(
    *,
    project: ProjectConfigV1,
    profile: PdkProfile,
    tasks: Sequence[TaskConfigV1],
    decide: Callable[..., Any],
    dispositions: list[FieldDisposition],
    warnings: list[str],
) -> WorkspaceConfig:
    out_pattern = project.extraction_output_dir
    dspf_pattern = project.dspf_out_path

    if "{task_id}" in out_pattern or "{task_id}" in dspf_pattern:
        replacement = decide(
            key="path.task_id",
            question=(
                "The path patterns still use {task_id}, which no longer exists. "
                "{run_slug} gives every run its own Cadence workspace; {cell} keeps "
                "the old behaviour of reusing one workspace per cell."
            ),
            options=["{run_slug}", "{cell}"],
            default="{run_slug}",
            context=f"output_dir_pattern={out_pattern!r}, dspf_out_pattern={dspf_pattern!r}",
        )
        out_pattern = out_pattern.replace("{task_id}", replacement)
        dspf_pattern = dspf_pattern.replace("{task_id}", replacement)
        dispositions.append(
            FieldDisposition(
                "project.yaml:extraction_output_dir",
                "decision",
                "workspace:output_dir_pattern",
                f"{{task_id}} -> {replacement}",
            )
        )

    for old, new in RETIRED_FORMAT_KEYS.items():
        if old == "task_id":
            continue
        token = "{" + old + "}"
        if token in out_pattern or token in dspf_pattern:
            out_pattern = out_pattern.replace(token, new)
            dspf_pattern = dspf_pattern.replace(token, new)
            dispositions.append(
                FieldDisposition(
                    "project.yaml:extraction_output_dir",
                    "moved",
                    "workspace:output_dir_pattern",
                    f"{token} -> {new}",
                )
            )

    overrides = {task.dspf_out_path for task in tasks if task.dspf_out_path is not None}
    if overrides:
        options = [project.dspf_out_path, *sorted(overrides)]
        chosen = decide(
            key="path.dspf_out",
            question=(
                "Some tasks overrode dspf_out_path. There is one pattern per workspace "
                "now, so pick the one that should apply to every cell."
            ),
            options=options,
            default=project.dspf_out_path,
            context=f"per-task values: {sorted(overrides)}",
        )
        dspf_pattern = str(chosen)
        dispositions.append(
            FieldDisposition(
                "tasks.yaml:dspf_out_path",
                "decision",
                "workspace:dspf_out_pattern",
                f"per-task overrides collapsed to {chosen!r}",
            )
        )
        warnings.append(
            f"tasks.yaml carried per-task dspf_out_path overrides ({sorted(overrides)}); "
            f"the workspace now uses one pattern: {dspf_pattern!r}"
        )

    try:
        workspace = WorkspaceConfig(
            pdk_profile=profile.profile_id,
            output_dir_pattern=out_pattern,
            intermediate_dir=project.intermediate_dir,
            dspf_out_pattern=dspf_pattern,
        )
    except ValidationError as exc:
        raise MigrationError(f"cannot build workspace.yaml: {exc}") from exc

    dispositions.extend(
        [
            FieldDisposition(
                "project.yaml:extraction_output_dir", "moved", "workspace:output_dir_pattern"
            ),
            FieldDisposition("project.yaml:intermediate_dir", "moved", "workspace:intermediate_dir"),
            FieldDisposition("project.yaml:dspf_out_path", "moved", "workspace:dspf_out_pattern"),
        ]
    )
    return workspace


# ---- resources ---------------------------------------------------------------


def _build_resources(
    readback: TemplateReadBack, *, dispositions: list[FieldDisposition]
) -> ResourceProfile:
    mapping = {
        "lvs_num_turbo": "lvs_num_turbo",
        "lvs_run_mt": "lvs_run_mt",
        "lvs_run_hyper": "lvs_run_hyper",
        "lvs_license_wait_time": "lvs_license_wait_time",
        "reduction_cpu": "reduction_cpu",
    }
    values: dict[str, Any] = {}
    for key, field_name in mapping.items():
        if key in readback.values:
            values[field_name] = readback.values[key]
            dispositions.append(
                FieldDisposition(
                    f"templates:{key}",
                    "seeded_from_template",
                    f"resources:{field_name}",
                    f"value {readback.values[key]!r}",
                )
            )
    return ResourceProfile(**values)


# ---- cross-checks and questions ----------------------------------------------


def _check_multi_recipe_cells(
    bindings: Mapping[str, Sequence[str]],
    *,
    decide: Callable[..., Any],
    warnings: list[str],
) -> None:
    owners: dict[str, list[str]] = {}
    for recipe_id, keys in bindings.items():
        for key in keys:
            owners.setdefault(key, []).append(recipe_id)
    shared = {key: ids for key, ids in owners.items() if len(ids) > 1}
    if not shared:
        return
    answer = decide(
        key="cells.multi_recipe",
        question=(
            "Some cells are covered by more than one recipe. That is now two "
            "separate runs rather than a collision to work around with a "
            "discriminator in the output path. Is that what you meant?"
        ),
        options=["yes", "no"],
        default="yes",
        context="; ".join(f"{key}: {', '.join(ids)}" for key, ids in sorted(shared.items())),
    )
    if answer == "no":
        raise MigrationError(
            "cells covered by more than one recipe were rejected: "
            + "; ".join(f"{key} -> {', '.join(ids)}" for key, ids in sorted(shared.items()))
        )
    warnings.append(
        "these cells run under more than one recipe (one run each): "
        + "; ".join(f"{key} -> {', '.join(ids)}" for key, ids in sorted(shared.items()))
    )


def _collect_open_questions(catalog: Catalog, report: MigrationReport) -> list[OpenQuestion]:
    """Catalog questions attached to values this migration actually wrote.

    The catalog carries 31 open questions; only the ones whose value ended up
    in a generated file are worth putting in front of the user, and each one
    is shown with the value that was written, not in the abstract.
    """

    questions: list[OpenQuestion] = []
    for option in catalog.options:
        if not option.question:
            continue
        if option.owner is Owner.RECIPE and option.recipe_field_path:
            for recipe in report.recipes:
                try:
                    value = _get_path(recipe, option.recipe_field_path)
                except AttributeError:
                    continue
                questions.append(
                    OpenQuestion(
                        key=option.key,
                        question=option.question,
                        field_path=option.recipe_field_path,
                        value=_jsonable(value),
                        file=f"recipes/{recipe.recipe_id}.yaml",
                    )
                )
        elif option.owner is Owner.PROFILE:
            path = _PROFILE_QUESTION_FIELDS.get(option.key)
            if path is None:
                continue
            try:
                value = _get_path(report.profile, path)
            except AttributeError:
                continue
            questions.append(
                OpenQuestion(
                    key=option.key,
                    question=option.question,
                    field_path=path,
                    value=_jsonable(value),
                    file=f"config/profiles/{report.profile.profile_id}.yaml",
                )
            )
        elif option.owner is Owner.RESOURCES:
            path = _RESOURCE_QUESTION_FIELDS.get(option.key)
            if path is None:
                continue
            questions.append(
                OpenQuestion(
                    key=option.key,
                    question=option.question,
                    field_path=path,
                    value=_jsonable(_get_path(report.resources, path)),
                    file=f"config/{RESOURCES_FILENAME}",
                )
            )
    return questions


#: Profile-owned catalog rows have no ``recipe_field_path``; this is the
#: equivalent map for the two objects that are not Recipes.
_PROFILE_QUESTION_FIELDS: dict[str, str] = {
    "cdl_include_file": "cdl_include_files",
    "technology_corner": "corners",
    "cap_component": "parasitics.cap_component",
    "parasitic_res_model": "parasitics.res_model",
}

_RESOURCE_QUESTION_FIELDS: dict[str, str] = {
    "lvs_license_wait_time": "lvs_license_wait_time",
    "lvs_num_turbo": "lvs_num_turbo",
    "lvs_run_hyper": "lvs_run_hyper",
    "quantus_cpu_count": "quantus_cpu_count",
}


def _get_path(model: Any, path: str) -> Any:
    node = model
    for part in path.split("."):
        node = getattr(node, part)
    return node


def _jsonable(value: Any) -> Any:
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return value.value
    return value


# ---- writing -----------------------------------------------------------------


def _header(project_yaml: Path, tasks_yaml: Path, what: str) -> str:
    return (
        f"{what}\n"
        f"Generated by auto-ext migrate from {project_yaml.name} + {tasks_yaml.name}.\n"
        "Both source files were left untouched. This file is never overwritten by a\n"
        "second migration: delete it if you want it regenerated.\n"
        "Lines marked NEEDS CONFIRMATION carry a value nobody has checked against a\n"
        "real tool yet -- see the migration report and docs/refactor/OFFICE_TODO.md.\n"
    )


def _write_all(
    report: MigrationReport, *, out_root: Path, project_yaml: Path, tasks_yaml: Path
) -> None:
    config_dir = out_root / "config"
    recipes_dir = out_root / "recipes"

    #: file (as named by OpenQuestion.file) -> field path -> question text.
    #: Every unconfirmed value gets its question written next to it, so the
    #: file itself tells the user what to check without the report in hand.
    question_text: dict[str, dict[str, str]] = {}
    for question in report.open_questions:
        question_text.setdefault(question.file, {})[question.field_path] = textwrap.fill(
            f"NEEDS CONFIRMATION: {question.question}", width=76
        )

    profile_rel = f"config/profiles/{report.profile.profile_id}.yaml"
    # A table seeded from the shipped profile must not carry the comment that
    # says where the migration read it back from -- that comment would be a
    # lie about provenance, on the one field where provenance is the whole
    # question.
    seeded = set(report.shipped_fallbacks)
    profile_comments = {
        **question_text.get(profile_rel, {}),
        "corners": (
            "NOT FROM YOUR CONFIG: a legacy config has no corner table, so this one\n"
            "came from the profile shipped with this build. Check the names against\n"
            "this PDK -- a Recipe is portable only across corners this table names."
            if "corners" in seeded
            else "NEEDS CONFIRMATION: only the corner the templates hardcode is here.\n"
            "Add the rest of this PDK's corners; a Recipe is portable only across\n"
            "corners this table names."
        ),
        "parasitics": "NEEDS CONFIRMATION: parasitic device names were read back from the\n"
        "templates and never checked against your PDK.",
        "cdl_include_files": "NEEDS CONFIRMATION: one CDL prelude is assumed. Multiple\n"
        "preludes are not supported yet.",
        "lvs_decks": (
            "NOT FROM YOUR CONFIG: the variant table came from the profile shipped\n"
            "with this build -- your templates carry no deck-variant list. Everything\n"
            "else in this block is yours."
            if "lvs_decks.variants" in seeded
            else "NEEDS CONFIRMATION: deck variants come from the calibre manifest,\n"
            "which is the only place they were ever written down."
        ),
    }
    for net_field in ("power_names", "ground_names"):
        if net_field in seeded:
            profile_comments[net_field] = (
                "NOT FROM YOUR CONFIG: your templates carry no supply-net line, so\n"
                "this list came from the profile shipped with this build. LVS treats\n"
                "these as the supply nets -- check them."
            )
    resource_comments = {
        **question_text.get(f"config/{RESOURCES_FILENAME}", {}),
        "lvs_run_mt": "NEEDS CONFIRMATION: read back from the runset; nobody has confirmed\n"
        "the licence-wait unit or that this site has Hyperscaling licences.",
    }
    _write_once(
        config_dir / "profiles" / f"{report.profile.profile_id}.yaml",
        _render_yaml(
            report.profile.model_dump(mode="json"),
            header=_header(project_yaml, tasks_yaml, "PDK profile."),
            comments=profile_comments,
        ),
        report=report,
        loader=read_profile_yaml,
        expected=report.profile,
    )

    _write_once(
        config_dir / WORKSPACE_FILENAME,
        _render_yaml(
            report.workspace.model_dump(mode="json"),
            header=_header(project_yaml, tasks_yaml, "Workspace: where the EDA work lands."),
            comments={
                "output_dir_pattern": "Location only -- no longer part of a run's identity.",
            },
        ),
        report=report,
        loader=load_workspace,
        expected=report.workspace,
    )

    _write_once(
        config_dir / CELLS_FILENAME,
        _render_yaml(
            report.cells.model_dump(mode="json", exclude_none=True),
            header=_header(project_yaml, tasks_yaml, "The cell table: one row per DUT."),
            comments={
                "cells": "Cartesian expansion is already resolved: every row is explicit.\n"
                "Set enabled: false to park a row instead of deleting it.",
            },
        ),
        report=report,
        loader=load_cells,
        expected=report.cells,
    )

    _write_once(
        config_dir / RESOURCES_FILENAME,
        _render_yaml(
            report.resources.model_dump(mode="json"),
            header=_header(
                project_yaml,
                tasks_yaml,
                "Machine resources. Deliberately NOT part of a Recipe: a Recipe has to\n"
                "stay portable between an 8-core box and a 64-core one.",
            ),
            comments=resource_comments,
        ),
        report=report,
        loader=lambda path: ResourceProfile.model_validate(_read_yaml_mapping(path)),
        expected=report.resources,
    )

    question_text: dict[str, dict[str, str]] = {}
    for question in report.open_questions:
        question_text.setdefault(question.file, {})[question.field_path] = question.question

    for recipe in report.recipes:
        rel = f"recipes/{recipe.recipe_id}.yaml"
        comments = {
            path: textwrap.fill(f"NEEDS CONFIRMATION: {text}", width=76)
            for path, text in sorted(question_text.get(rel, {}).items())
        }
        comments["extraction"] = (
            "Values came from your template text and your knobs, not from schema\n"
            "defaults, so this recipe reproduces what you were running."
        )
        _write_once(
            recipes_dir / f"{recipe.recipe_id}.yaml",
            _render_yaml(
                recipe.model_dump(mode="json"),
                header=_header(
                    project_yaml,
                    tasks_yaml,
                    f"Recipe {recipe.recipe_id}: "
                    f"{len(report.bindings.get(recipe.recipe_id, []))} cell(s) used these settings.",
                ),
                comments=comments,
            ),
            report=report,
            loader=load_recipe,
            expected=recipe,
        )


def _read_yaml_mapping(path: Path) -> Any:
    yaml = YAML(typ="rt")
    with Path(path).open("r", encoding="utf-8") as handle:
        data = yaml.load(handle)
    if not isinstance(data, dict):
        raise MigrationError(f"{path}: expected a mapping at top level")
    return _plain(data)


# ---- report rendering --------------------------------------------------------


def format_report(report: MigrationReport) -> str:
    """Render the report as plain text: what was produced, and what to check."""

    lines: list[str] = []
    lines.append("=== produced ===")
    lines.append(f"profile   : {report.profile.profile_id} ({report.profile.display_name})")
    lines.append(f"cells     : {len(report.cells)} row(s)")
    lines.append(f"recipes   : {len(report.recipes)}")
    for recipe in report.recipes:
        bound = report.bindings.get(recipe.recipe_id, [])
        lines.append(f"  - {recipe.recipe_id}: {len(bound)} cell(s)")
        for key in bound:
            lines.append(f"      {key}")
    lines.append(f"workspace : {report.workspace.output_dir_pattern}")
    lines.append(f"resources : {report.resources.resource_id}")

    lines.append("")
    lines.append("=== field dispositions ===")
    counts = report.disposition_counts()
    lines.append("  " + ", ".join(f"{action}: {count}" for action, count in sorted(counts.items())))
    for item in report.dispositions:
        lines.append(f"  {item.describe()}")

    lines.append("")
    lines.append("=== decisions taken (answer them to change the output) ===")
    if not report.decisions:
        lines.append("  none")
    for decision in report.decisions:
        lines.append(f"  {decision.describe()}")
        lines.append(f"      answered: {report.answers.get(decision.key)!r}")

    lines.append("")
    lines.append("=== NOT from your config (seeded from the shipped profile) ===")
    if not report.shipped_fallbacks:
        lines.append("  none")
    for field_name in report.shipped_fallbacks:
        lines.append(f"  profile.{field_name}")

    lines.append("")
    lines.append("=== needs confirmation (values carried over, nobody has checked them) ===")
    if not report.open_questions:
        lines.append("  none")
    for question in report.open_questions:
        lines.append(f"  {question.describe()}")

    lines.append("")
    lines.append("=== warnings ===")
    if not report.warnings:
        lines.append("  none")
    for warning in report.warnings:
        lines.append(f"  {warning}")

    lines.append("")
    lines.append("=== files ===")
    for path in report.written:
        lines.append(f"  written: {path}")
    for path in report.skipped:
        lines.append(f"  skipped (already exists): {path}")
    if not report.written and not report.skipped:
        lines.append("  nothing written (dry run)")
    return "\n".join(lines)
