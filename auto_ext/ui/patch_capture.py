"""Render one generated file, let the user edit it, store the edit as a patch.

This is the code behind the Recipes screen's ``[Edit rendered file]`` button
(artboard ``1g``) -- the escape hatch for the case the catalog cannot express.
It renders a target the way a run would, hands the text to
:class:`~auto_ext.ui.widgets.rendered_editor.RenderedFileEditor`, and turns
whatever comes back into a masked :class:`~auto_ext.core.patch.TemplatePatch`
mounted on the Recipe.

Qt-free on purpose: everything here is a pure function over models, so the
capture can be tested without a QApplication and the dialog stays a dumb text
box.

Why the masked round trip matters
---------------------------------

A stored edit has to survive being applied to a *different* DUT. So the edit
is captured against the masked twin of the same render -- the text with every
maskable context value replaced by a ``${slot}`` token -- and re-masked on the
way in. That is why one stored hunk can add ``-extra_netlist "$cell_extra.sp"``
for every cell rather than freezing the cell that happened to be selected when
the user typed it. :func:`auto_ext.core.patch.capture_patch` does the work;
this module's job is to hand it a base/masked pair that is line-aligned, which
means both sides must come from the same post-``substitute_env`` source and
the same context.

Deliberate duplications, all flagged because they have a real owner elsewhere
----------------------------------------------------------------------------

* :func:`resolve_render_env` unions the profile's env references with the
  workspace patterns' own. ``health.resolve_profile_env`` covers only the
  first half, and no single function covers both until the runner is ported
  to the v2 models.
* :func:`resolve_workspace_paths` expands ``WorkspaceConfig``'s three path
  patterns (env refs, then ``str.format``). The v2 runner will own this the
  day it is ported off ``ProjectConfig``; until it exists this is the only
  implementation, and it is kept in one function so the port is a deletion.
* :func:`build_preview` recomputes the masked twin that
  :func:`auto_ext.core.render.render_one` builds internally and does not
  return -- :class:`~auto_ext.core.render.RenderedFile` carries ``base_text``
  but not ``masked``. Adding a field to ``RenderedFile`` would remove this
  duplication; it is three lines here and a core change there, so it waits.

Unverified assumption
---------------------

A patch captured against one cell is assumed to apply to every other cell in
the book. That is the entire premise of the masked format and
``docs/refactor/02-patch.md`` states it, but nothing checks it at capture
time: a hunk whose surrounding lines happen to be cell-specific will simply
report ``review`` on the next DUT rather than being refused here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_ext.catalog import Catalog, builtin_catalog
from auto_ext.core.env import (
    EnvResolution,
    discover_required_vars,
    resolve_env,
    substitute_env,
)
from auto_ext.core.errors import AutoExtError
from auto_ext.core.health import profile_env_refs
from auto_ext.core.patch import (
    capture_patch,
    mask_values,
    render_masked,
    sha256_text,
)
from auto_ext.core.patch_models import TemplatePatch
from auto_ext.core.render import (
    RenderError,
    RunFacts,
    SiteFacts,
    TargetPlan,
    build_context,
    plan_targets,
    template_path_for,
)
from auto_ext.core.template import make_jinja_env
from auto_ext.model.cells import CellEntry
from auto_ext.model.pdk import PdkProfile
from auto_ext.model.recipe import Recipe, ResourceProfile
from auto_ext.model.run import DutSnapshot, utcnow
from auto_ext.model.workspace import WorkspaceConfig

__all__ = [
    "PREVIEW_RUN_ID",
    "CaptureError",
    "RenderPreview",
    "WorkspacePaths",
    "build_preview",
    "capture",
    "editable_targets",
    "resolve_render_env",
    "resolve_workspace_paths",
    "with_patch",
]

#: ``run.id`` used while previewing. A preview is not a run and must never
#: look like one in a rendered file that a user might then compare against a
#: real run directory.
PREVIEW_RUN_ID = "preview"


class CaptureError(AutoExtError):
    """The edit could not be rendered or could not be turned into a patch."""


@dataclass(frozen=True)
class WorkspacePaths:
    """The three ``WorkspaceConfig`` patterns, expanded for one cell."""

    output_dir: str
    intermediate_dir: str
    dspf_out_path: str


@dataclass(frozen=True)
class RenderPreview:
    """One generated file, plus everything :func:`capture` needs to store an edit."""

    plan: TargetPlan
    #: File name the run would write, e.g. ``ext.cmd``.
    filename: str
    #: What the catalog says this file should contain, before any patch.
    base_text: str
    #: The line-aligned twin with maskable values replaced by ``${slot}``.
    masked_text: str
    #: ``slot name -> the real value it stands for``, for this render.
    values: dict[str, str]
    #: Template source after ``substitute_env``; what ``capture_patch`` anchors on.
    substituted_source: str
    #: Digest of the *unsubstituted* template file, so a template change is
    #: detectable later.
    template_sha256: str

    @property
    def stage_key(self) -> str:
        return self.plan.stage_key

    @property
    def template_id(self) -> str:
        return self.plan.spec.template_id


def editable_targets(
    recipe: Recipe, *, catalog: Catalog | None = None
) -> list[TargetPlan]:
    """Every generated file this recipe produces, in stage order.

    ``strmout`` never appears: it renders nothing, so there is no text to
    edit. That is :func:`auto_ext.core.render.plan_targets`' rule, not one
    invented here.
    """

    return plan_targets(recipe, catalog=catalog)


def resolve_render_env(
    profile: PdkProfile, workspace: WorkspaceConfig
) -> EnvResolution:
    """Resolve every env var a render of this pair needs.

    :func:`auto_ext.core.health.resolve_profile_env` covers the profile's own
    path expressions only. The workspace's three patterns reference variables
    of their own -- ``$WORK_ROOT`` is the obvious one, and it appears in no
    profile field -- so a preview built on the profile's set alone fails on
    ``output_dir_pattern`` while reporting a profile problem. The union is
    what a run resolves, so it is what a preview resolves.
    """

    names = profile_env_refs(profile) | discover_required_vars(
        [
            workspace.output_dir_pattern,
            workspace.intermediate_dir,
            workspace.dspf_out_pattern,
        ]
    )
    return resolve_env(names, dict(profile.env_overrides))


def resolve_workspace_paths(
    workspace: WorkspaceConfig,
    cell: CellEntry,
    resolved_env: Mapping[str, str],
    *,
    recipe_id: str,
    run_id: str = PREVIEW_RUN_ID,
    run_slug: str = PREVIEW_RUN_ID,
) -> WorkspacePaths:
    """Expand the workspace patterns: env references first, then format keys.

    Same two-phase grammar ``project.yaml`` used, and the same one
    ``WorkspaceConfig`` documents. A pattern that survives phase one with a
    ``$`` still in it is an unresolved env var, which is reported rather than
    passed on -- ``si`` and ``jivaro`` do not expand ``$VAR`` inside string
    values, so the file would be wrong rather than merely unresolved.
    """

    keys = {
        "cell": cell.cell,
        "library": cell.library,
        "layout_view": cell.layout_view,
        "source_view": cell.source_view,
        "recipe": recipe_id,
        "run_id": run_id,
        "run_slug": run_slug,
    }
    env = dict(resolved_env)

    def expand(pattern: str, field: str) -> str:
        text = substitute_env(pattern, env)
        # The env check comes first, and not only for the better message:
        # an unsubstituted ``${WORK_ROOT}`` still looks like a format field to
        # ``str.format``, which would report it as an unknown format key and
        # send the user looking in the wrong file.
        if "$" in text:
            raise CaptureError(
                f"{field}: {text!r} still holds an env reference. Set the "
                f"variable in the shell, or pin it in the profile's "
                f"env_overrides."
            )
        try:
            return text.format(**keys)
        except KeyError as exc:  # pragma: no cover - WorkspaceConfig validates
            raise CaptureError(f"{field}: unknown format key {exc}") from exc

    return WorkspacePaths(
        output_dir=expand(workspace.output_dir_pattern, "output_dir_pattern"),
        intermediate_dir=expand(workspace.intermediate_dir, "intermediate_dir"),
        dspf_out_path=expand(workspace.dspf_out_pattern, "dspf_out_pattern"),
    )


def build_preview(
    plan: TargetPlan,
    *,
    recipe: Recipe,
    profile: PdkProfile,
    workspace: WorkspaceConfig,
    cell: CellEntry,
    resolved_env: Mapping[str, str],
    workarea: Path,
    resources: ResourceProfile | None = None,
    site: SiteFacts | None = None,
    catalog: Catalog | None = None,
    templates_root: Path | None = None,
    now: datetime | None = None,
) -> RenderPreview:
    """Render ``plan`` for ``cell`` and return it with its masked twin.

    Raises :class:`CaptureError` for anything the user can act on -- an
    unresolved env reference, a template that cannot be read, a recipe setting
    the template hardcodes. Everything else propagates.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    paths = resolve_workspace_paths(
        workspace, cell, resolved_env, recipe_id=recipe.recipe_id
    )
    run = RunFacts(
        run_id=PREVIEW_RUN_ID,
        run_slug=PREVIEW_RUN_ID,
        run_dir=Path(workarea) / "runs" / PREVIEW_RUN_ID,
        workarea=Path(workarea),
        output_dir=paths.output_dir,
        intermediate_dir=paths.intermediate_dir,
        dspf_out_path=paths.dspf_out_path,
        started_at=now if now is not None else utcnow(),
        stages=tuple(stage.value for stage in recipe.stages),
    )
    dut = DutSnapshot(
        library=cell.library,
        cell=cell.cell,
        layout_view=cell.layout_view,
        source_view=cell.source_view,
        ground_net=cell.ground_net,
        out_file=cell.out_file,
    )

    try:
        context = build_context(
            dut=dut,
            recipe=recipe,
            profile=profile,
            run=run,
            resolved_env=resolved_env,
            resources=resources,
            site=site,
            catalog=cat,
        )
        template_path = template_path_for(plan.spec, templates_root=templates_root)
        source = template_path.read_text(encoding="utf-8")
        substituted = substitute_env(source, dict(resolved_env))
        base_text = make_jinja_env().from_string(substituted).render(**context)
        masked_text = render_masked(substituted, context)
        values = mask_values(substituted, context)
    except (RenderError, OSError, UnicodeDecodeError) as exc:
        raise CaptureError(str(exc)) from exc

    from auto_ext.core.render import RENDERED_FILENAMES

    return RenderPreview(
        plan=plan,
        filename=RENDERED_FILENAMES[plan.target],
        base_text=base_text,
        masked_text=masked_text,
        values=dict(values),
        substituted_source=substituted,
        template_sha256=sha256_text(source),
    )


def capture(
    preview: RenderPreview,
    edited_text: str,
    *,
    recipe: Recipe,
    profile: PdkProfile,
    catalog: Catalog | None = None,
    keep_literal: Sequence[str] = (),
    intents: Mapping[int, str] | None = None,
) -> TemplatePatch | None:
    """Turn ``edited_text`` into a patch, or ``None`` when nothing changed.

    ``keep_literal`` names slots whose value the user meant literally --
    the difference between "always use the recipe temperature" and
    "always use 85".
    """

    if edited_text.replace("\r\n", "\n") == preview.base_text.replace("\r\n", "\n"):
        return None
    cat = catalog if catalog is not None else builtin_catalog()
    existing = recipe.patch_for(preview.plan.stage, preview.template_id)
    try:
        return capture_patch(
            template_source=preview.substituted_source,
            template_sha256=preview.template_sha256,
            stage=preview.plan.stage,
            template_id=preview.template_id,
            profile_id=profile.profile_id,
            catalog_version=cat.catalog_version,
            base_real=preview.base_text,
            base_masked=preview.masked_text,
            edited_real=edited_text,
            values=preview.values,
            intents=intents,
            keep_literal=keep_literal,
            existing=existing,
        )
    except ValueError as exc:
        raise CaptureError(_explain(exc)) from exc


#: Failure text from :mod:`auto_ext.core.patch` -> what the user can do about
#: it. Anything not listed here is passed through verbatim, because an
#: invented explanation is worse than a raw one.
_EXPLANATIONS: dict[str, str] = {
    "a pure-insertion hunk needs BOTH context anchors": (
        "A line added after the last line of the file cannot be stored: an "
        "edit is placed by the lines around it, and there is nothing after "
        "the end. Put the new line above the final line instead."
    ),
    "real/masked renders are not line-aligned": (
        "This recipe cannot be edited by hand right now: one of its values "
        "spans several lines or drives an [% if %] branch, which breaks the "
        "line-by-line match a stored edit is placed with."
    ),
}


def _explain(exc: Exception) -> str:
    """Turn a patch-layer refusal into something a user can act on."""

    text = str(exc)
    for needle, advice in _EXPLANATIONS.items():
        if needle in text:
            return advice
    return text


def with_patch(recipe: Recipe, patch: TemplatePatch) -> Recipe:
    """A copy of ``recipe`` carrying ``patch``, replacing any it supersedes.

    ``Recipe`` allows at most one patch per ``(stage, template_id)``; a
    re-capture of the same file replaces rather than appends, which is what
    keeps ``existing=`` in :func:`capture` able to preserve hunk ids.
    """

    kept = [
        p
        for p in recipe.patches
        if not (p.stage == patch.stage and p.template_id == patch.template_id)
    ]
    payload: dict[str, Any] = {"patches": [*kept, patch], "updated_at": utcnow()}
    return recipe.model_copy(update=payload)
