"""Catalog-driven render pipeline: Recipe + PdkProfile + DUT + run -> tool inputs.

One path from semantic settings to the bytes an EDA tool reads::

    Recipe + PdkProfile + DutSnapshot + RunFacts
      -> build_context()          semantic values, translated per PDK
      -> check_representable()    refuse what the template cannot express
      -> render one .j2 per RenderTarget the recipe asks for
      -> apply Recipe.patches (auto_ext.core.patch)
      -> final text on disk

This module is the *new* pipeline. The legacy ``project.templates`` plus
``*.manifest.yaml`` knob path in :mod:`auto_ext.core.runner` is untouched and
still runs when no Recipe is supplied; the two coexist until the knob
machinery is removed.

Four things it does that the legacy path structurally could not
---------------------------------------------------------------

1. **Templates are not user state.** Which ``.j2`` makes which file comes from
   :mod:`auto_ext.catalog` (``targets:``), not from ``project.templates``.
   :func:`auto_ext.core.template.resolve_template_path` stays for the legacy
   path and for the GUI; nothing here calls it.

2. **The corner is translated, not copied.** ``recipe.extraction.corner`` is a
   semantic name (``typical``); the string Quantus sees comes from
   ``PdkProfile.corners``. A name this PDK does not define is refused by
   :func:`resolve_corner` *before* any file is written, because a rendered
   ``-technology_corner "rcwosrt"`` fails hours later inside Quantus with a
   message that names neither the recipe nor the profile.

3. **A setting the template hardcodes is refused, not ignored.** Most of the
   catalog's rows are still ``hardcoded_literal``: the value is typed into the
   ``.j2``. Binding a Recipe field for one of those would silently drop the
   user's setting. :func:`check_representable` compares every such row against
   the catalog default -- which *is* the template's literal -- and raises
   :class:`RenderError` naming the option, both values and the escape hatch.
   As rows get parameterised the check goes quiet on its own.

4. **The output is checked for surviving env references.** ``si`` and
   ``jivaro`` do not expand ``$VAR`` inside string values, so a leftover
   reference is a wrong file rather than a late failure. :func:`render_one`
   rescans the *final* text -- after patching, which can reintroduce one.

Unverified assumptions
----------------------
None are introduced here. Everything rendered is either transcribed from the
templates in this repository or supplied by objects carrying their own
provenance (``PdkProfile.discovered_from``, ``OptionSpec.observed``). The one
judgement call is the "stated value" rule in :func:`check_representable`: a
``None`` or empty-list field reads as "not stated, the template's literal
stands" rather than as a divergence, so a freshly discovered profile with
empty ``power_names`` still renders.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from jinja2 import TemplateSyntaxError, UndefinedError
from pydantic import BaseModel

from auto_ext.catalog import (
    Catalog,
    Currently,
    OptionSpec,
    Owner,
    RenderTargetSpec,
    builtin_catalog,
)
from auto_ext.core.env import (
    derive_parent_dir_from_env_candidates,
    discover_required_vars,
    resolve_path_expr,
    substitute_env,
)
from auto_ext.core.errors import AutoExtError
from auto_ext.core.patch import (
    PatchConflictError,
    apply_patch,
    build_stage_report,
    mask_values,
    render_masked,
)
from auto_ext.core.patch_models import StagePatchReport
from auto_ext.core.template import (
    guarded_jinja_vars,
    make_jinja_env,
    referenced_jinja_vars,
)
from auto_ext.model.common import STAGE_ORDER, RenderTarget, Stage
from auto_ext.model.pdk import PdkProfile
from auto_ext.model.recipe import OutputKind, Recipe, ResourceProfile
from auto_ext.model.run import DutSnapshot, JsonScalar

logger = logging.getLogger(__name__)


__all__ = [
    "RENDERED_FILENAMES",
    "STAGE_KEYS",
    "RenderError",
    "RenderedFile",
    "ResolvedCorner",
    "RunFacts",
    "SiteFacts",
    "TargetPlan",
    "Unrepresentable",
    "build_context",
    "check_representable",
    "declared_value",
    "flatten_context",
    "lookup",
    "plan_targets",
    "render_one",
    "required_env_vars",
    "resolve_corner",
    "template_path_for",
]


class RenderError(AutoExtError):
    """The render pipeline refuses to produce a file.

    Raised *before* anything is written whenever the requested settings cannot
    be turned into a correct tool input: an unknown corner, a temperature no
    source supplies, a value the template hardcodes, a catalog row the
    renderer does not bind, or an env reference that survived substitution.
    """


#: Name each render target is written under inside ``runs/<run_id>/rendered/``.
#: Deliberately not derived from the template stem: the legacy path names the
#: file after its template (``default.env``), which makes a run directory
#: describe the template instead of the artifact. These are the names the EDA
#: tools and ``docs/refactor/01-schema.md`` section 0 use.
RENDERED_FILENAMES: dict[RenderTarget, str] = {
    RenderTarget.SI_ENV: "si.env",
    RenderTarget.LVS_QCI: "lvs.qci",
    RenderTarget.QUANTUS_EXT: "ext.cmd",
    RenderTarget.QUANTUS_DSPF: "dspf.cmd",
    RenderTarget.JIVARO_XML: "jivaro.xml",
}

#: Stage key per target. ``quantus`` is the one stage that can run twice in a
#: single run (``recipe.output.emit`` lists both forms) and
#: :class:`~auto_ext.model.run.StageRecord` requires unique keys, so the two
#: quantus invocations are keyed apart exactly as its docstring specifies.
STAGE_KEYS: dict[RenderTarget, str] = {
    RenderTarget.SI_ENV: "si",
    RenderTarget.LVS_QCI: "calibre",
    RenderTarget.QUANTUS_EXT: "quantus.ext",
    RenderTarget.QUANTUS_DSPF: "quantus.dspf",
    RenderTarget.JIVARO_XML: "jivaro",
}

#: Which target each ``recipe.output.emit`` entry selects.
EMIT_TARGETS: dict[OutputKind, RenderTarget] = {
    OutputKind.EXTRACTED_VIEW: RenderTarget.QUANTUS_EXT,
    OutputKind.DSPF: RenderTarget.QUANTUS_DSPF,
}

#: Flat legacy alias -> context path, for rows whose ``context_path`` names a
#: container while the template variable needs one member of it. Exactly one
#: row qualifies today, and it lives here rather than in a branch so the
#: exception stays countable.
_FLAT_ALIAS_OVERRIDES: dict[str, str] = {
    # ``pdk.cdl_include_files`` is a list (a PDK may need several CDL
    # preludes); ``si.env``'s ``incFILE`` has one slot, and
    # ``PdkProfile.cdl_include_file`` is the property that refuses to flatten
    # more than one rather than dropping the rest.
    "cdl_include_file": "pdk.cdl_include_file",
}

#: Namespace roots of the render context. A catalog ``template_var`` equal to
#: one of these would shadow a whole namespace, so :func:`build_context`
#: refuses it instead of producing a context where ``[[recipe.lvs.svdb_cci]]``
#: silently resolves against a string.
_NAMESPACE_ROOTS: frozenset[str] = frozenset(
    {"paths", "pdk", "recipe", "run", "site", "resources", "env"}
)


# ---- corner translation ------------------------------------------------------


@dataclass(frozen=True)
class ResolvedCorner:
    """A semantic corner name translated into this PDK's tool literals."""

    #: The semantic name as the recipe (or the profile default) spells it.
    name: str
    #: What Quantus receives as ``-technology_corner``.
    technology_corner: str
    #: What Quantus receives as ``-temperature``.
    temperature_c: float
    #: Where the temperature came from, for the record and for error messages.
    temperature_source: str


def resolve_corner(recipe: Recipe, profile: PdkProfile) -> ResolvedCorner:
    """Translate ``recipe.extraction.corner`` through ``profile.corners``.

    Name resolution: the recipe's choice, else ``profile.default_corner``.
    Temperature: ``recipe.extraction.temperature_c``, else the corner's
    ``default_temperature_c``.

    Raises :class:`RenderError` -- never a guess, never a half-filled result --
    when the name is unset everywhere, names a corner this profile does not
    define, or when no source supplies a temperature. All three are refused
    here rather than at render time so the message can name the recipe field
    *and* the profile that would have to change.
    """

    known = [c.name for c in profile.corners]
    wanted = recipe.extraction.corner or profile.default_corner
    if not wanted:
        raise RenderError(
            f"recipe {recipe.recipe_id!r} names no extraction corner and profile "
            f"{profile.profile_id!r} has no default_corner. Set "
            f"recipe.extraction.corner to one of {known or '(this profile defines no corners)'}, "
            "or give the profile a default_corner."
        )

    spec = profile.corner(wanted)
    if spec is None:
        raise RenderError(
            f"corner {wanted!r} is not defined by pdk profile {profile.profile_id!r}; "
            f"known corners: {known or '(none)'}. Rendering it anyway would hand "
            "Quantus a -technology_corner string this PDK does not know."
        )

    if recipe.extraction.temperature_c is not None:
        temperature = recipe.extraction.temperature_c
        source = "recipe.extraction.temperature_c"
    elif spec.default_temperature_c is not None:
        temperature = spec.default_temperature_c
        source = f"pdk.corners[{spec.name}].default_temperature_c"
    else:
        raise RenderError(
            f"no temperature for corner {spec.name!r}: recipe {recipe.recipe_id!r} "
            "leaves extraction.temperature_c unset and the corner in profile "
            f"{profile.profile_id!r} has no default_temperature_c. Quantus "
            "-temperature has no default worth guessing."
        )

    return ResolvedCorner(
        name=spec.name,
        technology_corner=spec.technology_corner,
        # Passed on, never rebuilt: both sources are already model-validated
        # floats, and ``float()`` on a
        # :class:`~auto_ext.model.common.WrittenFloat` would throw away the
        # spelling the file it came from used.
        temperature_c=temperature,
        temperature_source=source,
    )


# ---- run / site facts --------------------------------------------------------


@dataclass(frozen=True)
class RunFacts:
    """Everything the context needs that is true of *this run* only.

    Assembled by the runner once the run directory is claimed. A single bundle
    rather than loose keyword arguments because ``run.*`` in the render context
    and the corresponding run-record fields are both built from it, so they
    cannot drift.
    """

    run_id: str
    run_slug: str
    run_dir: Path
    workarea: Path
    #: Resolved Cadence workspace for this DUT (legacy ``extraction_output_dir``).
    output_dir: str
    intermediate_dir: str
    #: Resolved DSPF output path; ``None`` when the recipe emits no DSPF.
    dspf_out_path: str | None = None
    #: Where this dispatch exports a SECOND, standalone GDS to, for tools
    #: outside this flow. ``None`` (the norm) means no export.
    #:
    #: Deliberately NOT a relocation of the LVS layout file. That file is a
    #: producer/consumer contract -- strmout writes it and Calibre reads it
    #: back as ``*lvsLayoutPaths`` -- so moving it means moving both sides or
    #: breaking LVS. The export is a separate strmout invocation writing a
    #: separate file, and the LVS path is untouched by it.
    layout_export_path: str | None = None
    #: Parallel isolation cwd (``<run_dir>/work``); ``None`` in serial mode.
    work_dir: Path | None = None
    started_at: datetime | None = None
    batch_id: str | None = None
    dry_run: bool = False
    max_workers: int = 1
    #: Stage keys actually scheduled for this run.
    stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class SiteFacts:
    """Machine- and person-local facts. ``site.*`` in the render context.

    ``employee_id`` keeps the existing fallback chain (explicit value, then
    ``$USER`` / ``$USERNAME``, then ``"unknown"``); the caller resolves it and
    this object only carries the answer.
    """

    employee_id: str = "unknown"
    user: str | None = None
    host: str | None = None


# ---- context -----------------------------------------------------------------


def _plain(value: Any) -> Any:
    """Coerce a model value into something Jinja and JSON both accept.

    ``StrEnum`` members already render as their value under ``str()``, but
    routing every enum through here keeps the flattened context free of
    ``ExtractType.RC_COUPLED``-style spellings.
    """

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def _model_tree(model: Any) -> dict[str, Any]:
    """Nested plain-Python view of a pydantic model, one dict per submodel."""

    tree: dict[str, Any] = {}
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            tree[name] = _model_tree(value)
        else:
            tree[name] = _plain(value)
    return tree


def lookup(context: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """``(found, value)`` for a dotted context path.

    Returns ``found=False`` rather than raising, so a catalog row pointing at a
    key this context does not build is reported by the caller with the row's
    name attached instead of as a bare ``KeyError``.
    """

    node: Any = context
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return (False, None)
        node = node[part]
    return (True, node)


def _pdk_context(
    profile: PdkProfile,
    recipe: Recipe,
    corner: ResolvedCorner,
    env: Mapping[str, str],
) -> dict[str, Any]:
    """The ``pdk.*`` namespace: profile state with every path expression resolved.

    Three entries are assembled rather than stored: ``lvs_rules_file`` (the
    three segments line 1 of ``calibre_lvs.qci.j2`` used to concatenate by
    hand), ``qrc_query_cmd`` and ``qrc_preserve_cell_list`` (directory plus the
    filename the templates had frozen into them).
    """

    resolved_env = dict(env)

    lvs_dir = (
        resolve_path_expr(profile.lvs_decks.dir_expr, resolved_env)
        if profile.lvs_decks.dir_expr
        else None
    )
    lvs_basename = (
        profile.lvs_decks.basename_for_dir(lvs_dir)
        if lvs_dir is not None
        else profile.lvs_decks.basename
    )
    lvs_rules_file: str | None = None
    if lvs_dir is not None and profile.lvs_decks.variants:
        try:
            filename = profile.lvs_decks.filename_for(
                recipe.lvs.deck_variant, resolved_dir=lvs_dir
            )
        except KeyError as exc:
            raise RenderError(
                f"recipe {recipe.recipe_id!r} asks for lvs deck variant "
                f"{recipe.lvs.deck_variant!r}, which profile "
                f"{profile.profile_id!r} does not define: {exc.args[0]}"
            ) from exc
        lvs_rules_file = str(PurePosixPath(lvs_dir) / filename)

    qrc_dir = (
        resolve_path_expr(profile.qrc.dir_expr, resolved_env)
        if profile.qrc.dir_expr
        else None
    )

    try:
        cdl_include_file = profile.cdl_include_file
    except NotImplementedError as exc:
        # The profile deliberately refuses to flatten several CDL includes into
        # si.env's single incFILE slot. Converted into this module's own error
        # so one run fails with a readable reason instead of the whole dispatch
        # tearing down on an unexpected exception type.
        raise RenderError(str(exc)) from exc

    tech_name = profile.tech_name or derive_parent_dir_from_env_candidates(
        list(profile.tech_name_env_vars), resolved_env
    )

    lvs_version, qrc_version = profile.deck_versions

    return {
        "profile_id": profile.profile_id,
        "tech_name": tech_name,
        "layer_map": substitute_env(str(profile.layer_map), resolved_env),
        "tech_library_file": substitute_env(profile.tech_library_file, resolved_env),
        "cdl_include_file": (
            substitute_env(cdl_include_file, resolved_env)
            if cdl_include_file is not None
            else None
        ),
        "cdl_include_files": [
            substitute_env(p, resolved_env) for p in profile.cdl_include_files
        ],
        "lvs_dir": lvs_dir,
        "lvs_basename": lvs_basename,
        "lvs_rules_file": lvs_rules_file,
        "lvs_runset_version": lvs_version,
        "qrc_deck_dir": qrc_dir,
        "qrc_query_cmd": (
            str(PurePosixPath(qrc_dir) / profile.qrc.query_cmd_name)
            if qrc_dir is not None
            else None
        ),
        "qrc_preserve_cell_list": (
            str(PurePosixPath(qrc_dir) / profile.qrc.preserve_cell_list_name)
            if qrc_dir is not None
            else None
        ),
        "qrc_runset_version": qrc_version,
        "power_names": list(profile.power_names),
        "ground_names": list(profile.ground_names),
        "corner": corner.technology_corner,
        "corner_name": corner.name,
        "temperature_c": corner.temperature_c,
        "parasitics": _model_tree(profile.parasitics),
        "paths": {
            key: resolve_path_expr(expr, resolved_env)
            for key, expr in profile.extra_paths.items()
        },
    }


def _recipe_tree(
    recipe: Recipe, corner: ResolvedCorner, dut: DutSnapshot
) -> dict[str, Any]:
    """``recipe.*``: every field, with the corner and DUT fallbacks folded in.

    ``extraction.corner`` and ``extraction.temperature_c`` are the two fields
    allowed to be ``None`` in the model and resolved through the profile
    (``auto_ext.model.recipe.PROFILE_FALLBACK_FIELDS``). The tree carries the
    *resolved* values so no template needs to know about the fallback, while
    ``pdk.corner`` carries the tool literal.

    ``reduction.views_to_reduce`` is the same idea against the DUT rather than
    the profile (``auto_ext.model.recipe.DUT_FALLBACK_FIELDS``): unset means
    "the view Quantus just wrote", which is ``out_file`` -- the same name
    ``inputView`` and ``-view_name`` already carry. It resolves to ``None``
    when the cell has no ``out_file``, exactly as the flat ``out_file`` alias
    beside it does; a DUT with no extracted view is a reduction that should
    not have been scheduled, and that belongs to the runner's stage gate, not
    to a silent substitution here.
    """

    tree = _model_tree(recipe)
    # ``patches`` is the escape hatch's storage, not a value any template reads,
    # and it is large. Dropping it keeps RunRecord.context readable; the patches
    # themselves are in RunRecord.recipe and their outcome in
    # RunRecord.patch_reports.
    tree.pop("patches", None)
    tree["id"] = recipe.recipe_id
    tree["extraction"]["corner"] = corner.name
    tree["extraction"]["temperature_c"] = corner.temperature_c
    if tree["reduction"].get("views_to_reduce") is None:
        tree["reduction"]["views_to_reduce"] = dut.out_file
    return tree


def build_context(
    *,
    dut: DutSnapshot,
    recipe: Recipe,
    profile: PdkProfile,
    run: RunFacts,
    resolved_env: Mapping[str, str],
    resources: ResourceProfile | None = None,
    site: SiteFacts | None = None,
    corner: ResolvedCorner | None = None,
    catalog: Catalog | None = None,
) -> dict[str, Any]:
    """Build the Jinja render context for one DUT under one Recipe.

    Pure: no I/O, no clock, no environment reads. The returned dict is the
    single source both the render and :attr:`RunRecord.context` consume, so
    what the record shows is what the files were made from.

    Shape (``docs/refactor/01-schema.md`` section 4):

    - flat DUT identity: ``library`` ``cell`` ``lvs_layout_view``
      ``lvs_source_view`` ``ground_net`` ``out_file``
    - ``paths.*`` resolved workspace paths, ``pdk.*`` the profile, ``recipe.*``
      every recipe field, plus ``run.*`` ``site.*`` ``resources.*`` ``env.*``
    - **plus one flat alias per catalog row**, named by
      ``OptionSpec.template_var``. The shipped templates still use the flat
      names, so this is what lets the new pipeline render them at all; it is
      also the name the catalog reserves for each value, which is why the
      aliases come from the catalog rather than from a hand-written list that
      would drift out of step with it.

    Raises :class:`RenderError` when a catalog row's ``template_var`` would
    shadow a namespace root, or when its ``context_path`` names a key this
    function does not build. Both are catalog/renderer drift, and catching
    them here with the row's name attached beats a ``StrictUndefined`` five
    stages later.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    corner = corner if corner is not None else resolve_corner(recipe, profile)
    resources = resources if resources is not None else ResourceProfile()
    site = site if site is not None else SiteFacts()

    env = dict(resolved_env)
    output_dir = run.output_dir

    context: dict[str, Any] = {
        # DUT identity stays flat: the one group that appears in every template
        # and is not a setting at all.
        "library": dut.library,
        "cell": dut.cell,
        "lvs_layout_view": dut.layout_view,
        "lvs_source_view": dut.source_view,
        "ground_net": dut.ground_net,
        "out_file": dut.out_file,
        # Not a catalog row and deliberately so: no template renders it, and
        # a template that did would be relocating the LVS layout file.
        "layout_export_path": run.layout_export_path,
        "paths": {
            "output_dir": output_dir,
            "intermediate_dir": run.intermediate_dir,
            "dspf_out": run.dspf_out_path,
            "layout_export": run.layout_export_path,
            "run_dir": str(run.run_dir),
            "work_dir": str(run.work_dir) if run.work_dir is not None else None,
            # Calibre's query post-trigger writes these three under output_dir
            # and both quantus templates read them back: one convention that
            # was typed into the .j2 files three times.
            "query_output_dir": str(PurePosixPath(output_dir) / "query_output"),
            "query_layer_map_file": str(
                PurePosixPath(output_dir) / "query_output" / "Design.gds.map"
            ),
            "query_device_properties_file": str(
                PurePosixPath(output_dir) / "query_output" / "Design.props"
            ),
        },
        "pdk": _pdk_context(profile, recipe, corner, env),
        "recipe": _recipe_tree(recipe, corner, dut),
        "run": {
            "id": run.run_id,
            "slug": run.run_slug,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "dry_run": run.dry_run,
            "batch_id": run.batch_id,
            "stages": list(run.stages),
            "max_workers": run.max_workers,
        },
        "site": {
            "employee_id": site.employee_id,
            "user": site.user,
            "host": site.host,
            "workarea": str(run.workarea),
        },
        "resources": _model_tree(resources),
        "env": env,
    }

    _bind_flat_aliases(context, cat)
    return context


def _bind_flat_aliases(context: dict[str, Any], catalog: Catalog) -> None:
    """Add ``OptionSpec.template_var`` -> value for every row that has a value.

    Rows with ``context_path: null`` are structure, plumbing or provenance --
    they hold no value a template could bind, so they get no alias.
    """

    for opt in catalog.options:
        if opt.describes_member:
            # The row describes a member of a collection; there is no single
            # value to alias. Templates loop over the collection instead --
            # see the extract block in the two quantus files.
            continue
        path = _FLAT_ALIAS_OVERRIDES.get(opt.key, opt.context_path)
        if path is None:
            continue
        if opt.template_var in _NAMESPACE_ROOTS:
            raise RenderError(
                f"catalog row {opt.key!r} declares template_var "
                f"{opt.template_var!r}, which is a render-context namespace; "
                "rename the template_var in options.yaml"
            )
        found, value = lookup(context, path)
        if not found:
            raise RenderError(
                f"catalog row {opt.key!r} points at context path {path!r}, which "
                "the render pipeline does not build. Either the row's "
                "context_path or auto_ext/core/render.py is out of date."
            )
        context[opt.template_var] = _plain(value)


def flatten_context(context: Mapping[str, Any]) -> dict[str, JsonScalar]:
    """Flatten the render context to dotted keys, for ``RunRecord.context``.

    Lists join into one space-separated string and everything else becomes a
    JSON scalar: the record's field is ``dict[str, JsonScalar]`` and its
    purpose is a readable diff between two runs, not a re-loadable copy.
    """

    flat: dict[str, JsonScalar] = {}

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, sub in value.items():
                walk(f"{prefix}.{key}" if prefix else str(key), sub)
            return
        if isinstance(value, (list, tuple)):
            flat[prefix] = " ".join(str(_plain(v)) for v in value)
            return
        plain = _plain(value)
        flat[prefix] = (
            plain
            if plain is None or isinstance(plain, (str, int, float, bool))
            else str(plain)
        )

    for key, value in context.items():
        walk(str(key), value)
    return flat


# ---- what the templates can still not express --------------------------------


@dataclass(frozen=True)
class Unrepresentable:
    """One setting the shipped template cannot express.

    The value is typed into the ``.j2`` (``currently: hardcoded_literal``), so
    the renderer would bind the user's Recipe/Profile value into a variable no
    template reads and write the old literal anyway. Reported instead.
    """

    option_key: str
    #: Where the user set it, e.g. ``recipe.extraction.decoupling_factor``.
    context_path: str | None
    #: What they asked for.
    wanted: Any
    #: What the template will write regardless (``OptionSpec.default``).
    template_literal: Any
    #: Which generated files carry the literal.
    targets: tuple[RenderTarget, ...]

    def describe(self) -> str:
        where = ", ".join(t.value for t in self.targets) or "(no target)"
        field_name = self.context_path or self.option_key
        return (
            f"{field_name} = {self.wanted!r}, but {where} still hardcodes "
            f"{self.template_literal!r}"
        )


#: How to read the *declared* value of a profile-owned catalog row.
#:
#: Recipe- and resources-owned rows are resolved mechanically from their
#: ``context_path`` (the field path is the path), but profile rows are not: the
#: render context holds ``pdk.qrc_query_cmd`` as a full path while the value the
#: template hardcodes is only the file name, and ``pdk.tech_library_file`` is
#: env-substituted in the context while the profile declares an expression. So
#: the comparison reads the model, spelled out one row at a time.
#:
#: Keyed by ``OptionSpec.key``. A hardcoded_literal profile row missing from
#: here raises :class:`RenderError` rather than passing unchecked -- the same
#: "inventory and runner stay in sync" rule the rest of this codebase uses.
_PROFILE_DECLARED: dict[str, Callable[[PdkProfile], Any]] = {
    "cdl_include_file": lambda p: p.cdl_include_files[0] if p.cdl_include_files else None,
    "lvs_rules_filename_pattern": lambda p: p.lvs_decks.filename_pattern,
    "power_names": lambda p: list(p.power_names),
    "ground_names": lambda p: list(p.ground_names),
    "qrc_preserve_cell_list_name": lambda p: p.qrc.preserve_cell_list_name,
    "qrc_query_cmd_name": lambda p: p.qrc.query_cmd_name,
    "cap_component": lambda p: p.parasitics.cap_component,
    "res_component": lambda p: p.parasitics.res_component,
    "technology_library_file": lambda p: p.tech_library_file,
    "parasitic_res_model": lambda p: p.parasitics.res_model,
    "parasitic_cap_model": lambda p: p.parasitics.cap_model,
    "parasitic_ind_model": lambda p: p.parasitics.ind_model,
    "parasitic_mutual_model": lambda p: p.parasitics.mutual_model,
    # Not a stored field: the corner table maps a semantic name onto the
    # literal, and the literal is what the template froze.
    "technology_corner": lambda p: None,
}

#: Owners whose values a user can now set and whose rows are therefore worth
#: comparing against the template literal. ``cells`` and ``run`` are excluded
#: because their values are per-DUT / per-run by construction and every one of
#: their rows that reaches a file is already a ``[[var]]``; ``fixed`` is
#: excluded by definition.
_CHECKED_OWNERS: frozenset[Owner] = frozenset(
    {Owner.RECIPE, Owner.PROFILE, Owner.RESOURCES}
)


def declared_value(
    option: OptionSpec,
    *,
    recipe: Recipe,
    profile: PdkProfile,
    resources: ResourceProfile,
    corner: ResolvedCorner,
) -> Any:
    """The value the user declared for ``option``, before any path resolution.

    "Declared" and not "rendered" on purpose: the render context resolves
    ``$env(SETUP_ROOT)/assura_tech.lib`` into an absolute path, and comparing
    *that* against the catalog default would report a divergence on every
    machine.

    Raises :class:`RenderError` for a checkable row this function cannot read,
    so adding a row to ``options.yaml`` without wiring it here fails loudly
    instead of silently escaping :func:`check_representable`.
    """

    if option.owner is Owner.RECIPE:
        path = option.recipe_field_path
        if path is None:
            raise RenderError(
                f"catalog row {option.key!r} is recipe-owned but has no "
                f"recipe.* context_path, so its value cannot be read"
            )
        node: Any = recipe
        for part in path.split("."):
            node = getattr(node, part)
        return _plain(node)

    if option.owner is Owner.RESOURCES:
        if option.context_path is None or not option.context_path.startswith("resources."):
            raise RenderError(
                f"catalog row {option.key!r} is resources-owned but its "
                f"context_path {option.context_path!r} does not name a "
                "ResourceProfile field"
            )
        node = resources
        for part in option.context_path[len("resources.") :].split("."):
            node = getattr(node, part)
        return _plain(node)

    if option.owner is Owner.PROFILE:
        if option.key == "technology_corner":
            return corner.technology_corner
        reader = _PROFILE_DECLARED.get(option.key)
        if reader is None:
            raise RenderError(
                f"catalog row {option.key!r} is profile-owned and hardcoded in a "
                "template, but auto_ext/core/render.py does not know how to read "
                "it from a PdkProfile; add it to _PROFILE_DECLARED"
            )
        return _plain(reader(profile))

    raise RenderError(
        f"catalog row {option.key!r} has owner {option.owner.value!r}, which "
        "the representability check does not cover"
    )


def _is_stated(value: Any) -> bool:
    """Whether the user actually said something.

    ``None`` and an empty list read as "not stated, the template's literal
    stands". Without this rule a freshly discovered profile -- whose
    ``power_names`` table is empty until someone fills it in -- would be
    refused, even though the template still writes the full supply list and
    the render is byte-identical to today's.
    """

    if value is None:
        return False
    if isinstance(value, (list, tuple)) and not value:
        return False
    return True


def check_representable(
    targets: Iterable[RenderTarget],
    *,
    recipe: Recipe,
    profile: PdkProfile,
    resources: ResourceProfile,
    corner: ResolvedCorner,
    catalog: Catalog | None = None,
) -> list[Unrepresentable]:
    """Settings that will *not* reach ``targets`` because the template froze them.

    A row is reported when all of these hold:

    - ``currently`` is ``hardcoded_literal`` -- the value is typed into the
      ``.j2``, so no ``[[var]]`` binding can change it;
    - the owner is one a user can edit (recipe / profile / resources);
    - the row lands in one of the files about to be rendered;
    - the declared value is stated (see :func:`_is_stated`) and differs from
      ``OptionSpec.default``, which is by the catalog's own definition today's
      effective value, i.e. the literal in the template.

    Returns the list; :func:`render_one` is what turns a non-empty list into a
    :class:`RenderError`. Splitting the two lets the GUI show the same
    diagnosis as a preview without provoking a failure.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    wanted_targets = set(targets)
    found: list[Unrepresentable] = []

    for opt in cat.options:
        if opt.currently is not Currently.HARDCODED_LITERAL:
            continue
        if opt.owner not in _CHECKED_OWNERS:
            continue
        hit = tuple(t for t in opt.targets if t in wanted_targets)
        if not hit:
            continue
        value = declared_value(
            opt, recipe=recipe, profile=profile, resources=resources, corner=corner
        )
        if not _is_stated(value):
            continue
        if value == _plain(opt.default):
            continue
        found.append(
            Unrepresentable(
                option_key=opt.key,
                context_path=opt.context_path,
                wanted=value,
                template_literal=_plain(opt.default),
                targets=hit,
            )
        )
    return found


def _raise_unrepresentable(items: Sequence[Unrepresentable], recipe: Recipe) -> None:
    lines = "\n  ".join(item.describe() for item in items)
    raise RenderError(
        f"recipe {recipe.recipe_id!r} sets {len(items)} value(s) the shipped "
        f"templates still hardcode, so rendering would silently ignore them:\n"
        f"  {lines}\n\n"
        "Until the catalog parameterises these rows, express the change as a "
        "manual edit on the generated file (Recipe.patches, see "
        "docs/refactor/02-patch.md) or leave the field at its catalog default."
    )


# ---- planning ----------------------------------------------------------------


@dataclass(frozen=True)
class TargetPlan:
    """One generated file this run will produce."""

    #: Unique within the run; ``quantus.ext`` / ``quantus.dspf`` when the
    #: recipe emits both quantus forms.
    stage_key: str
    stage: Stage
    target: RenderTarget
    spec: RenderTargetSpec = field(repr=False)


def plan_targets(
    recipe: Recipe,
    *,
    stages: Iterable[str] | None = None,
    catalog: Catalog | None = None,
) -> list[TargetPlan]:
    """Which files this recipe renders, in stage order.

    ``recipe.output.emit`` is a list, so the quantus stage can appear twice --
    once for the extracted view, once for the DSPF. The legacy path could not
    express that at all: ``ProjectConfig.templates`` has a single quantus slot,
    so a run emitted one form and structurally could not emit the other.

    ``stages`` narrows the plan the way the CLI's ``--stage`` does; ``None``
    means "every stage this recipe declares". ``strmout`` never appears here:
    it renders nothing, its argv is built from the context by
    :class:`~auto_ext.tools.strmout.StrmoutTool`.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    wanted = None if stages is None else set(stages)

    per_stage: dict[Stage, list[RenderTarget]] = {}
    for spec in cat.targets:
        if spec.stage is Stage.QUANTUS:
            continue
        per_stage.setdefault(spec.stage, []).append(spec.id)
    per_stage[Stage.QUANTUS] = [EMIT_TARGETS[kind] for kind in recipe.output.emit]

    plans: list[TargetPlan] = []
    # Canonical stage order, not the order the recipe happens to list them in:
    # si must netlist before Calibre compares against it, whatever the YAML says.
    declared = set(recipe.stages)
    for stage in STAGE_ORDER:
        if stage not in declared:
            continue
        if wanted is not None and stage.value not in wanted:
            continue
        for target in per_stage.get(stage, []):
            spec = cat.target(target)
            key = STAGE_KEYS[target]
            if stage is Stage.QUANTUS and len(per_stage[Stage.QUANTUS]) == 1:
                # Only one quantus invocation: keep the plain stage name so the
                # run record, the log file and the progress reporter all read
                # "quantus" rather than the disambiguated form.
                key = Stage.QUANTUS.value
            plans.append(TargetPlan(stage_key=key, stage=stage, target=target, spec=spec))
    return plans


def template_path_for(
    spec: RenderTargetSpec, *, templates_root: Path | None = None
) -> Path:
    """Absolute path of a target's template.

    ``templates_root`` overrides the checkout's ``templates/`` directory; it
    exists so tests can render from a temporary tree, not so a user can
    re-point a target. Which template makes which file is catalog state now.
    """

    if templates_root is None:
        return spec.template_path
    return Path(templates_root) / PurePosixPath(spec.template).relative_to("templates")


def required_env_vars(
    profile: PdkProfile,
    *,
    extra_sources: Iterable[str] = (),
    catalog: Catalog | None = None,
    templates_root: Path | None = None,
) -> set[str]:
    """Env vars this pipeline needs resolved before it can render.

    Sources: every catalog template, every path expression on the profile, the
    profile's own ``required_env`` list, plus whatever the caller adds
    (``extra_sources``: the workspace's output/intermediate/dspf patterns).
    ``tech_name_env_vars`` join in only when ``tech_name`` is unset, matching
    the auto-derivation.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    sources: list[str] = [str(profile.layer_map), profile.tech_library_file]
    sources.extend(profile.cdl_include_files)
    sources.extend(profile.extra_paths.values())
    if profile.lvs_decks.dir_expr:
        sources.append(profile.lvs_decks.dir_expr)
    if profile.qrc.dir_expr:
        sources.append(profile.qrc.dir_expr)
    sources.extend(extra_sources)

    for spec in cat.targets:
        path = template_path_for(spec, templates_root=templates_root)
        try:
            sources.append(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RenderError(f"cannot read catalog template {path}: {exc}") from exc

    required = discover_required_vars(sources)
    required.update(profile.required_env)
    if profile.tech_name is None:
        required.update(profile.tech_name_env_vars)
    return required


# ---- rendering ---------------------------------------------------------------


@dataclass(frozen=True)
class RenderedFile:
    """One generated tool input, on disk."""

    plan: TargetPlan
    out_path: Path
    text: str
    #: The pre-patch render, kept so a caller can diff "what the catalog said"
    #: against "what actually shipped" without re-rendering.
    base_text: str
    #: ``None`` when the recipe carries no patch for this file.
    patch_report: StagePatchReport | None = None

    @property
    def stage_key(self) -> str:
        return self.plan.stage_key

    @property
    def target(self) -> RenderTarget:
        return self.plan.target


def render_one(
    plan: TargetPlan,
    *,
    context: Mapping[str, Any],
    recipe: Recipe,
    profile: PdkProfile,
    resolved_env: Mapping[str, str],
    out_dir: Path,
    resources: ResourceProfile | None = None,
    corner: ResolvedCorner | None = None,
    catalog: Catalog | None = None,
    templates_root: Path | None = None,
    write: bool = True,
) -> RenderedFile:
    """Render one target and apply its patch, or refuse with a reason.

    Order, and why:

    1. **Representability check.** Before reading anything, so a recipe that
       cannot be honoured never leaves a half-written file behind.
    2. **Env substitution**, with a strict pre-scan: ``$X`` / ``${X}`` /
       ``$env(X)`` are replaced before Jinja sees the source. Rendered output
       must contain no env reference at all, because ``si`` and ``jivaro`` do
       not expand them inside string values.
    3. **Jinja render** with ``StrictUndefined`` and this project's ``[[ ]]``
       delimiters. ``trim_blocks`` is off, which is why every optional line in
       the templates is written in the hugging
       ``[% if x %]LINE`` / ``[% endif %]NEXT`` form.
    4. **Patch application**, when the recipe carries one for this file. The
       masked twin of the same render enables the fast path; a blocking report
       raises :class:`~auto_ext.core.patch.PatchConflictError` rather than
       shipping a file the user did not approve.
    5. **Final env rescan.** A patch stores literal text and can reintroduce a
       ``$VAR`` the substitution pass never saw.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    resources = resources if resources is not None else ResourceProfile()
    corner = corner if corner is not None else resolve_corner(recipe, profile)

    blocked = check_representable(
        [plan.target],
        recipe=recipe,
        profile=profile,
        resources=resources,
        corner=corner,
        catalog=cat,
    )
    if blocked:
        _raise_unrepresentable(blocked, recipe)

    template_path = template_path_for(plan.spec, templates_root=templates_root)
    try:
        source = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RenderError(f"cannot read template {template_path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise RenderError(f"template is not valid UTF-8: {template_path}") from exc

    missing = sorted(discover_required_vars([source]) - set(resolved_env))
    if missing:
        raise RenderError(f"unresolved env refs in {template_path}: {missing}")
    substituted = substitute_env(source, dict(resolved_env))

    # StrictUndefined catches a *missing* key; a key that is present and None
    # renders as the literal "None", which is how "None/None.wodio.qcilvs" gets
    # written into a runset. Every None here is a field the profile or the
    # recipe has not filled in, so say which one.
    #
    # The exception is a var the template itself guards with ``[% if x %]``:
    # None means "omit the line" there, which is the only way to spell a
    # default of "say nothing". The catalog declares the same fact as
    # ``LandingSite.optional``; the source is what actually decides, so it is
    # what is read, and tests/catalog/test_catalog.py asserts the two agree.
    guarded = guarded_jinja_vars(substituted)
    none_keys = sorted(
        name
        for name in referenced_jinja_vars(substituted)
        if name in context and context[name] is None and name not in guarded
    )
    if none_keys:
        raise RenderError(
            f"{plan.target.value} references {none_keys} but the resolved value "
            f"is None; fill the matching field(s) in pdk profile "
            f"{profile.profile_id!r} or recipe {recipe.recipe_id!r} before running"
        )

    jenv = make_jinja_env()
    try:
        base_text = jenv.from_string(substituted).render(**dict(context))
    except UndefinedError as exc:
        raise RenderError(
            f"undefined Jinja variable while rendering {plan.target.value} from "
            f"{template_path}: {exc}"
        ) from exc
    except TemplateSyntaxError as exc:
        raise RenderError(f"Jinja syntax error in {template_path}: {exc}") from exc

    text = base_text
    stage_report: StagePatchReport | None = None
    patch = recipe.patch_for(plan.stage, plan.spec.template_id)
    if patch is not None:
        masked = render_masked(substituted, context)
        values = mask_values(substituted, context)
        report = apply_patch(base_text, patch, values, base_masked_text=masked)
        stage_report = build_stage_report(patch, report, values)
        if report.blocking_under(patch.on_fuzzy):
            raise PatchConflictError(report, template_id=patch.template_id)
        text = report.patched_text

    leftover = sorted(discover_required_vars([text]))
    if leftover:
        raise RenderError(
            f"rendered {plan.target.value} still references env var(s) {leftover}; "
            "si and jivaro do not expand $VAR inside string values, so the file "
            "would be wrong rather than merely unresolved"
        )

    out_path = Path(out_dir) / RENDERED_FILENAMES[plan.target]
    if write:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    logger.debug(
        "rendered %s from %s (%d bytes, %s)",
        plan.target.value,
        template_path,
        len(text),
        "patched" if stage_report is not None else "no patch",
    )

    return RenderedFile(
        plan=plan,
        out_path=out_path,
        text=text,
        base_text=base_text,
        patch_report=stage_report,
    )
