"""Turn EDA files the user already owns into one Recipe, losing nothing.

``auto_ext.core.importer`` turns a raw export into a ``.j2``; ``auto_ext.migrate``
turns a v1 ``project.yaml`` into the v2 objects. Neither helps the user who has
a ``.cmd`` they saved out of the Quantus GUI, a ``.qci`` a colleague sent them
and an ``si.env`` they have been carrying between projects for three years, and
who wants *that* to become a recipe this tool can run.

:func:`import_recipe` takes one or more such files -- any subset of the five
render targets -- and returns a :class:`RecipeImportResult`. Nothing is written
unless the caller asks (:func:`write_imported_recipe`); the report is the
product, and a caller that does not like what it says can throw it away.

Two readers, because a generated file has two kinds of holes
------------------------------------------------------------
**Literals** -- ``-min_res 0.001``, ``*lvsReportOptions: S`` -- are recovered by
:func:`auto_ext.core.readback.read_back_from_templates`, which knows the
catalog's landing sites, quoting rules and types. That is the same function the
migration uses, on purpose: one parser, one set of answers.

**Variables** -- ``[[cell]]``, ``[[output_dir]]``, ``$env(SETUP_ROOT)`` -- are
recovered by matching the *template's own text at a landing site* against the
user's text at the same site (:func:`solve_template_vars`). ``inputView
value="[[library]]/[[cell]]/[[out_file]]"`` against ``inputView
value="INV_LIB/INV1/av_ext"`` yields three values that no per-option rule could
separate, because the catalog models three rows landing on one line. The
template is the pattern, the export is the string, the difference is the
answer.

The literal reader deliberately refuses those shared lines (see
:func:`auto_ext.core.readback.composite_sites`) -- handing ``lvs.deck_variant``
the string ``/pdk/.../CFXXX.wodio.qcilvs`` is worse than not reading it at all.
Shared lines are exactly what the variable solver is for, so between the two
nothing recoverable is dropped.

Everything else becomes a patch
-------------------------------
Whatever the catalog does not model is still in the user's file and must not
evaporate. So the importer renders the baseline that the recovered values
describe and hands the *user's file* to
:func:`auto_ext.core.patch.capture_patch` as the edited side. Every remaining
difference is kept as a masked, anchored hunk on the Recipe.

Masked matters here. The baseline is rendered with the user's *own* cell,
library and output directory -- recovered in step three precisely so that it
can be -- which means a captured hunk stores ``${cell}`` where the user's file
said ``INV1``, and the imported recipe still renders correctly for the next
DUT. A patch that froze ``INV1`` into every future run would be worse than a
failed import, because it would not look like a failure.

:attr:`RecipeImportResult.unmodelled_ratio` is how much of the input ended up
in that escape hatch. A high ratio is not an error; it is the signal that these
files come from a different tool version than the shipped templates describe,
and that a patch big enough to be a fork is a fork.

Unverified assumptions
----------------------
The same standing caveat as the patch layer: developed against the templates in
this repository and files rendered from them, never yet against a ``.cmd``
written by a real Quantus GUI on the office server. The numbers that would move
first are :data:`MIN_SITE_HITS` and :data:`MIN_SITE_COVERAGE` -- how much of a
file must be recognisable before it is accepted as a target at all -- and they
are gathered at the top for that reason.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import field as dc_field
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from auto_ext.catalog import (
    Catalog,
    Confidence,
    Currently,
    OptionSpec,
    OptionType,
    Owner,
    RenderTargetSpec,
    builtin_catalog,
)
from auto_ext.core.env import discover_required_vars, substitute_env
from auto_ext.core.errors import AutoExtError
from auto_ext.core.patch import capture_patch, mask_values, render_masked, sha256_text
from auto_ext.core.patch_models import TemplatePatch
from auto_ext.core.readback import (
    SPECIAL_READBACK,
    ReadBackError,
    ReadSite,
    SiteKey,
    TemplateReadBack,
    composite_sites,
    parse_by_syntax,
    read_back_from_templates,
    site_key,
)
from auto_ext.core.render import (
    RenderError,
    RunFacts,
    SiteFacts,
    TargetPlan,
    build_context,
    plan_targets,
    render_one,
    template_path_for,
)
from auto_ext.core.template import referenced_jinja_vars
from auto_ext.model.common import STAGE_ORDER, RenderTarget, Stage, WrittenFloat, slugify
from auto_ext.model.pdk import (
    DEFAULT_TECH_LIBRARY_FILE,
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    ParasiticDeviceContract,
    PdkProfile,
    QrcDeck,
)
from auto_ext.model.recipe import (
    OutputKind,
    Recipe,
    ResourceProfile,
    recipe_filename,
    recipe_from_catalog,
    save_recipe,
)
from auto_ext.model.run import DutSnapshot

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_WARN_RATIO",
    "MIN_SITE_COVERAGE",
    "MIN_SITE_HITS",
    "QUANTUS_MARGIN",
    "EnvSolution",
    "ImportSource",
    "ImportedFile",
    "MappedValue",
    "PatchedHunk",
    "RecipeImportError",
    "RecipeImportResult",
    "RoundTrip",
    "SolvedVars",
    "TargetScore",
    "detect_target",
    "env_vars_solvable_from_files",
    "import_recipe",
    "score_targets",
    "solve_env_from_file",
    "solve_template_vars",
    "write_imported_recipe",
]


# --- tunables ---------------------------------------------------------------

#: A file must match at least this many of a target's landing sites before it
#: is accepted as that target. Below it the answer is "this is not one of our
#: five files", which is a better outcome than a recipe built out of noise.
MIN_SITE_HITS = 5

#: ...and at least this fraction of them. A Quantus GUI of another vintage will
#: not write every option the shipped template does, so the bar is low; it is
#: here to reject a file that happens to contain a handful of familiar lines.
MIN_SITE_COVERAGE = 0.25

#: How many more sites the winner must match than the runner-up before a
#: Quantus command file is called ext or dspf on site counts alone. The
#: ``output_db -type`` rule decides first; this is only its fallback.
QUANTUS_MARGIN = 2

#: Above this share of imported lines living in patches, the import reports
#: itself as suspect. Two thirds of a ``.qci`` in one hunk is not an escape
#: hatch, it is a fork with extra steps.
DEFAULT_WARN_RATIO = 0.25

#: Bound to an env var that could neither be inferred nor supplied. Deliberately
#: not a plausible path: it has to be obvious in a diff.
_ENV_PLACEHOLDER = "/auto-ext-import/unresolved"

#: Recipe rows whose landing site says "the line is there" rather than carrying
#: a readable value. ``*cmnVConnectNamesState: ALL`` is a boolean spelled as a
#: word, and it is written only inside an ``[% if %]``, so neither the literal
#: reader (which cannot coerce ``ALL`` to a bool) nor the variable solver
#: (which never sees the line in the template, the ``[% if %]`` prefix breaks
#: the ``*key:`` shape) can recover it.
_PRESENCE_ROWS: dict[str, tuple[RenderTarget, SiteKey]] = {
    "lvs_connect_by_name": (RenderTarget.LVS_QCI, ("", "*cmnVConnectNamesState")),
}

#: ``[[name]]`` / ``[[name | filter]]`` -- a *variable reference*, and nothing
#: else. Only the leading identifier is captured: the filters in the shipped
#: templates are ``default(...)`` and ``join(...)``, neither of which changes
#: which value the slot carries.
#:
#: A slot holding a Jinja *expression* deliberately does not match.
#: ``[[ "true" if exclude_self_cap else "false" ]]`` has no single value that
#: could be read back out of it, and the Quantus tokeniser strips the quotes
#: before the solver sees the slot, so a looser pattern reads the expression's
#: first word -- ``true`` -- as the variable name and then reports every such
#: line as conflicting with every other. A site whose slot is an expression is
#: left to the literal reader, which knows the row's type and can coerce
#: ``true`` back into the boolean the recipe holds.
_JINJA_VAR_RE = re.compile(r"\[\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|[^\]]*?)?\]\]")

#: ``$env(X)`` / ``${X}`` / ``$X``, the grammar of :mod:`auto_ext.core.env`.
#: The Tcl form is first so ``$env(SETUP_ROOT)`` is not read as ``$env``.
_ENV_REF_RE = re.compile(
    r"\$env\(([A-Za-z_][A-Za-z0-9_]*)\)"
    r"|\$\{([A-Za-z_][A-Za-z0-9_]*)\}"
    r"|\$([A-Za-z_][A-Za-z0-9_]*)"
)

#: Any hole in a landing site's template text, Jinja or env, matched in one
#: pass so the two alternatives cannot overlap.
_HOLE_RE = re.compile(f"{_JINJA_VAR_RE.pattern}|{_ENV_REF_RE.pattern}")

#: Prefixes of the keys :class:`SolvedVars` returns.
_VAR = "var:"
_ENV = "env:"


class RecipeImportError(AutoExtError):
    """These files cannot become a recipe, and guessing would be worse."""


# --- inputs -----------------------------------------------------------------


@dataclass(frozen=True)
class ImportSource:
    """One file offered to the importer.

    Carries ``text`` rather than a path so a caller can import a buffer -- the
    GUI's paste box, a file already read for preview -- without writing a
    temporary file. ``target`` forces the answer for a file
    :func:`detect_target` refuses to classify.
    """

    label: str
    text: str
    target: RenderTarget | None = None

    @classmethod
    def from_path(cls, path: Path | str, *, target: RenderTarget | None = None) -> ImportSource:
        file = Path(path)
        try:
            text = file.read_text(encoding="utf-8")
        except OSError as exc:
            raise RecipeImportError(f"cannot read {file}: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise RecipeImportError(f"{file} is not UTF-8 text: {exc}") from exc
        return cls(label=str(file), text=text, target=target)


@dataclass(frozen=True)
class ImportedFile:
    """One accepted input and the target it was recognised as."""

    label: str
    target: RenderTarget
    line_count: int
    #: True when the caller named the target instead of the content deciding.
    forced: bool = False
    #: The file used CRLF; the import works in LF and says so rather than
    #: reporting every line as changed.
    crlf: bool = False


# --- report parts -----------------------------------------------------------


@dataclass(frozen=True)
class MappedValue:
    """One catalog-known value recovered from the user's files."""

    key: str
    value: Any
    owner: Owner
    #: Label of the file it came from.
    source: str
    site: ReadSite
    #: ``literal`` -- read at a landing site by the catalog's own rules;
    #: ``variable`` -- solved by matching the template's text at that site.
    origin: str
    #: Which object holds it now: ``recipe`` or ``profile``. A value can land
    #: in either, and a value that landed in one is never *also* kept as a
    #: patch hunk -- a hunk holding a modelled value pins the literal and
    #: silently kills the field.
    destination: str = "recipe"
    #: Field path within :attr:`destination`, or ``None`` when it did not land.
    applied_to: str | None = None
    #: Why it did not land, whenever ``applied_to`` is ``None``.
    note: str = ""

    @property
    def landed_in(self) -> str:
        """``recipe.extraction.corner`` / ``profile.tech_name`` / ``-``."""

        return f"{self.destination}.{self.applied_to}" if self.applied_to else "-"

    def describe(self) -> str:
        landed = f" -> {self.landed_in}" if self.applied_to else f" ({self.note})"
        return f"{self.key} = {self.value!r} [{self.source} {self.site.describe()}]{landed}"


@dataclass(frozen=True)
class PatchedHunk:
    """One difference the catalog does not model, kept as a manual edit."""

    target: RenderTarget
    template_id: str
    hunk_id: str
    removed: int
    added: int
    #: Line of the baseline the hunk is anchored at, 1-based.
    at_line: int
    #: The stored (masked) text, first line, trimmed for display.
    summary: str

    def describe(self) -> str:
        return (
            f"{self.target.value} line {self.at_line}: "
            f"-{self.removed}/+{self.added}  {self.summary}"
        )


@dataclass(frozen=True)
class RoundTrip:
    """One target re-rendered and compared against the file it came from."""

    target: RenderTarget
    identical: bool
    #: Unified diff, empty when identical. Re-render first, imported file second.
    diff: str = ""

    def describe(self) -> str:
        return f"{self.target.value}: {'identical' if self.identical else 'DIFFERS'}"


@dataclass(frozen=True)
class TargetScore:
    """How many of one target's landing sites a file contains."""

    target: RenderTarget
    hits: int
    sites: int

    @property
    def coverage(self) -> float:
        return self.hits / self.sites if self.sites else 0.0


@dataclass(frozen=True)
class SolvedVars:
    """What the variable solver recovered from one file.

    ``values`` is keyed ``var:<template_var>`` / ``env:<VARNAME>`` so a Jinja
    variable and an environment variable of the same name cannot collide.
    """

    values: dict[str, str]
    #: Where each value was read, as a parsed-file key.
    sites: dict[str, SiteKey]
    #: One entry per hole two sites of the same file answer differently.
    conflicts: list[str]


@dataclass(frozen=True)
class RecipeImportResult:
    """Everything the import found, decided, and could not decide.

    Nothing here has been written to disk; :func:`write_imported_recipe` is the
    separate step, so a caller can show the report first.
    """

    recipe: Recipe
    #: The profile the baseline was rendered against: the caller's, or one
    #: derived from the files themselves (see :attr:`derived_profile`).
    profile: PdkProfile
    dut: DutSnapshot
    run: RunFacts
    resolved_env: dict[str, str]
    sources: tuple[ImportedFile, ...]
    mapped: tuple[MappedValue, ...]
    as_patch: tuple[PatchedHunk, ...]
    #: Catalog keys that land in one of the imported files but could not be
    #: read out of it, with the reason. These keep their catalog default, and
    #: any resulting difference is in :attr:`as_patch`.
    unread: dict[str, str]
    roundtrip: dict[RenderTarget, RoundTrip]
    warnings: tuple[str, ...]
    #: Lines living in :attr:`as_patch` over lines of file, taking the longer
    #: of the imported file and the baseline as the denominator. Zero means the
    #: catalog explains the whole input; see :attr:`high_unmodelled`.
    unmodelled_ratio: float
    unmodelled_by_target: dict[RenderTarget, float]
    derived_profile: bool
    catalog_version: str
    warn_ratio: float = DEFAULT_WARN_RATIO
    #: The caller's :class:`~auto_ext.model.recipe.ResourceProfile` with every
    #: resource-owned value these files state written into it -- eight turbo
    #: cores, a licence wait, a jivaro cpu count. Separate from :attr:`recipe`
    #: because it is deliberately not part of one: those are facts about a
    #: machine, not about an extraction, and they must not travel with a
    #: shared recipe (DECISIONS #21).
    #:
    #: **A report, not a render input.** Every one of these rows is a
    #: ``hardcoded_literal`` in the shipped templates, so
    #: :func:`auto_ext.core.render.check_representable` refuses a profile that
    #: disagrees with the literal -- the same refusal that keeps a dead field
    #: off the form. The rendered file gets the user's number from the patch
    #: instead, which is why these rows still report that they did not land,
    #: and why :meth:`rerender` does not default to this profile. What this
    #: field changes is narrower and was worse: the value used to be read,
    #: printed once in the report, and then have nowhere at all to go.
    resources: ResourceProfile = dc_field(default_factory=ResourceProfile)

    @property
    def targets(self) -> tuple[RenderTarget, ...]:
        return tuple(f.target for f in self.sources)

    @property
    def hunk_count(self) -> int:
        return len(self.as_patch)

    @property
    def applied_count(self) -> int:
        return sum(1 for value in self.mapped if value.applied_to)

    @property
    def high_unmodelled(self) -> bool:
        """So much of the file is a patch that a patch is the wrong tool."""

        return self.unmodelled_ratio > self.warn_ratio

    @property
    def clean_roundtrip(self) -> bool:
        return all(trip.identical for trip in self.roundtrip.values())

    @property
    def left_at_default(self) -> dict[str, str]:
        """:attr:`unread` minus every key that reached an object anyway.

        "The literal reader could not read this" and "this stayed at the
        catalog default" are not the same statement, and the difference is a
        whole class of key: ``*lvsRulesFile`` carries four modelled values on
        one line, so the reader refuses it whole and the variable solver then
        recovers each share and lands it. Reporting such a key as left at the
        default contradicts, three lines further down the same report, the
        section that says where it went.

        One property because there is one answer. The CLI grew this filter
        first and the import dialog never got it, so the same import told a
        terminal and a window two different stories about the same file.
        """

        landed = {row.key for row in self.mapped if row.applied_to}
        return {key: why for key, why in self.unread.items() if key not in landed}

    def summary(self) -> str:
        files = ", ".join(f"{f.label} -> {f.target.value}" for f in self.sources)
        return (
            f"imported {files}; {self.applied_count} value(s) into the recipe, "
            f"{self.hunk_count} hunk(s) as manual edits "
            f"({self.unmodelled_ratio:.0%} of lines), round trip "
            f"{'clean' if self.clean_roundtrip else 'NOT clean'}"
        )

    def rerender(
        self,
        *,
        dut: DutSnapshot | None = None,
        profile: PdkProfile | None = None,
        catalog: Catalog | None = None,
        templates_root: Path | None = None,
        resources: ResourceProfile | None = None,
    ) -> dict[RenderTarget, str]:
        """Render the imported recipe again, optionally for a different DUT.

        This is what the masked patch format is for: swapping ``dut`` has to
        move the cell name inside the manual edits too, not freeze the one that
        happened to be in the file the recipe was imported from.
        """

        cat = catalog if catalog is not None else builtin_catalog()
        use_dut = dut if dut is not None else self.dut
        use_profile = profile if profile is not None else self.profile
        # NOT :attr:`resources`: see its own docstring. A profile carrying a
        # value the template hardcodes is refused by
        # :func:`auto_ext.core.render.check_representable`, and rightly.
        res = resources if resources is not None else ResourceProfile()
        context = build_context(
            dut=use_dut,
            recipe=self.recipe,
            profile=use_profile,
            run=self.run,
            resolved_env=self.resolved_env,
            resources=res,
            site=SiteFacts(),
            catalog=cat,
        )
        wanted = set(self.targets)
        out: dict[RenderTarget, str] = {}
        for plan in plan_targets(self.recipe, catalog=cat):
            if plan.target not in wanted:
                continue
            out[plan.target] = render_one(
                plan,
                context=context,
                recipe=self.recipe,
                profile=use_profile,
                resolved_env=self.resolved_env,
                out_dir=Path("."),
                resources=res,
                catalog=cat,
                templates_root=templates_root,
                write=False,
            ).text
        return out


# --- target detection -------------------------------------------------------


def _target_site_keys(target: RenderTarget, catalog: Catalog) -> set[SiteKey]:
    spec = catalog.target(target)
    return {
        site_key(site, spec)
        for opt in catalog.options
        for site in opt.lands_in
        if site.target is target
    }


def score_targets(text: str, *, catalog: Catalog | None = None) -> list[TargetScore]:
    """How many of each target's landing sites this text contains, best first.

    The catalog is the judge, so a row added to ``options.yaml`` sharpens
    detection without anybody editing a list of magic strings here.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    scores: list[TargetScore] = []
    for spec in cat.targets:
        keys = _target_site_keys(spec.id, cat)
        try:
            parsed = parse_by_syntax(spec.syntax, text)
        except ReadBackError:
            parsed = {}
        scores.append(
            TargetScore(
                target=spec.id,
                hits=sum(1 for key in keys if key in parsed),
                sites=len(keys),
            )
        )
    return sorted(scores, key=lambda score: (-score.hits, score.target.value))


#: A Quantus option written where a statement name belongs.
_COLUMN_ZERO_OPTION = re.compile(r"^-[A-Za-z_][A-Za-z0-9_]*", re.MULTILINE)

#: Below this many, a stray line is a stray line and not a layout.
_COLUMN_ZERO_MIN = 3


def _column_zero_options(text: str) -> list[str]:
    """Options written at column 0, which is where a section header goes.

    A pre-2026 export -- and anything a script wrote by stripping
    continuations -- puts every ``-option value`` flush left.
    :func:`auto_ext.core.readback.parse_quantus` decides section boundaries by
    indentation, so each of those becomes a section carrying no option and its
    value is discarded; the file then matches two landing sites and is refused
    as "not a file this tool generates". True, and useless: the user's next
    question is which of the two hundred lines was wrong, and the answer is
    all of them, for one reason.
    """

    found = _COLUMN_ZERO_OPTION.findall(text)
    return found if len(found) >= _COLUMN_ZERO_MIN else []


def _quantus_kind(text: str) -> RenderTarget | None:
    """``output_db -type`` decides, because it is what the stage actually emits.

    Not the file name. A user who saved ``dspf.cmd`` out of the GUI while the
    dialog still said ``extracted_view`` would otherwise get an ext file called
    dspf, and believing the name means running the wrong extraction for an
    afternoon before anything looks wrong.
    """

    raw = parse_by_syntax("quantus_cmd", text).get(("output_db", "-type"))
    if raw is None or not raw.values:
        return None
    token = raw.values[0].strip().lower()
    if token == OutputKind.DSPF.value:
        return RenderTarget.QUANTUS_DSPF
    if token == OutputKind.EXTRACTED_VIEW.value:
        return RenderTarget.QUANTUS_EXT
    return None


def detect_target(
    text: str, *, label: str = "<text>", catalog: Catalog | None = None
) -> RenderTarget:
    """Which of the five generated files this is, decided by content.

    Raises :class:`RecipeImportError` -- naming what was tried and how well each
    target matched -- when nothing matches well enough, and when the two Quantus
    forms cannot be told apart. Both messages end in "say which one": a recipe
    built on a wrong guess fails at extraction time, hours later, in Quantus's
    words rather than ours.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    scores = score_targets(text, catalog=cat)
    best = scores[0]
    if best.hits < MIN_SITE_HITS or best.coverage < MIN_SITE_COVERAGE:
        flat = _column_zero_options(text)
        if flat:
            raise RecipeImportError(
                f"{label} is a Quantus command file whose option lines are all "
                f"at column 0 ({len(flat)} of them: {', '.join(flat[:4])}...). "
                "Indentation is what separates a statement from its options "
                "here, so at column 0 every option becomes a section of its "
                f"own and its value is dropped -- only {best.hits} landing "
                f"site(s) survived, and {MIN_SITE_HITS} are needed. Indent the "
                "option lines under their statement (this tool writes them "
                "indented, each continued with a trailing backslash) and "
                "import again."
            )
        detail = ", ".join(f"{s.target.value}={s.hits}/{s.sites}" for s in scores)
        raise RecipeImportError(
            f"{label} does not look like any file this tool generates "
            f"(matched landing sites: {detail}). Name the target explicitly if "
            "it really is one of si.env / lvs.qci / a quantus ext or dspf .cmd "
            "/ jivaro.xml."
        )

    if best.target not in (RenderTarget.QUANTUS_EXT, RenderTarget.QUANTUS_DSPF):
        return best.target

    decided = _quantus_kind(text)
    if decided is not None:
        return decided
    ext = next(s for s in scores if s.target is RenderTarget.QUANTUS_EXT)
    dspf = next(s for s in scores if s.target is RenderTarget.QUANTUS_DSPF)
    if abs(ext.hits - dspf.hits) >= QUANTUS_MARGIN:
        return ext.target if ext.hits > dspf.hits else dspf.target
    raise RecipeImportError(
        f"{label} is a Quantus command file but carries no readable "
        f"'output_db -type', and it matches the extracted_view form "
        f"({ext.hits} sites) about as well as the dspf form ({dspf.hits} "
        "sites). Name the target explicitly."
    )


# --- the variable solver ----------------------------------------------------


#: An ``[% if x %]...[% endif %]`` pair *inside* one landing site's value, as
#: opposed to the whole-line form that wraps ``*cmnVConnectNamesState``. Both
#: shipped instances (the third ``lvsPostTriggers`` trigger, the body of
#: ``simViewList``) guard a fragment of a line the rest of which is always
#: written. Non-greedy and single-level on purpose: anything cleverer -- an
#: ``[% else %]``, a nested pair -- leaves a tag standing in the pattern and
#: :func:`_site_pattern` gives up rather than guess.
_INLINE_IF_RE = re.compile(
    r"\[%\s*if\s+[^%]*?%\](?P<body>(?:(?!\[%).)*?)\[%\s*endif\s*%\]", re.DOTALL
)


def _holes(fragment: str, names: list[str]) -> str:
    """Regex for one fragment, appending each hole it opens to ``names``."""

    out: list[str] = []
    cursor = 0
    for match in _HOLE_RE.finditer(fragment):
        jinja = match.group(1)
        env = match.group(2) or match.group(3) or match.group(4)
        out.append(re.escape(fragment[cursor : match.start()]))
        out.append(f"(?P<g{len(names)}>.+)")
        names.append(f"{_VAR}{jinja}" if jinja else f"{_ENV}{env}")
        cursor = match.end()
    out.append(re.escape(fragment[cursor:]))
    return "".join(out)


def _site_pattern(template_value: str) -> tuple[re.Pattern[str], list[str]] | None:
    """A regex that reads a landing site's holes out of a rendered value.

    ``[[library]]/[[cell]]/[[out_file]]`` becomes ``^(?P<g0>.+)/(?P<g1>.+)/
    (?P<g2>.+)$``, positionally named because ``$env(X)`` and ``[[x]]`` can
    both occur in one line and a named group may not repeat.

    Every hole is greedy, so where a value is genuinely ambiguous the leftmost
    hole takes the longest share. That is the right bias: in every shipped site
    with more than one hole the leftmost is a directory
    (``[[calibre_lvs_dir]]/[[calibre_lvs_basename]].[[lvs_variant]].qcilvs``),
    and a directory is the one part that contains the separator. Returns
    ``None`` for a site with no holes -- a literal, which the literal reader
    owns.

    A fragment the template writes conditionally becomes an optional group, so
    the holes *inside* it still read out of a file that has the fragment and
    simply do not match against one that does not. Escaping the ``[% if %]``
    along with the rest would instead ask the user's file to contain the tag
    itself, and the whole site would go unsolved -- taking the values that
    share the line down with it.
    """

    names: list[str] = []
    pattern: list[str] = ["^"]
    literal: list[str] = []
    cursor = 0
    for match in _INLINE_IF_RE.finditer(template_value):
        plain = template_value[cursor : match.start()]
        body = match.group("body")
        pattern.append(_holes(plain, names))
        pattern.append(f"(?:{_holes(body, names)})?")
        literal += [plain, body]
        cursor = match.end()
    tail = template_value[cursor:]
    pattern.append(_holes(tail, names))
    literal.append(tail)
    if not names:
        return None
    if any("[%" in part for part in literal):
        # A tag this function does not model would be matched literally, which
        # can only ever fail. Better to hand the site back unsolved.
        return None
    pattern.append("$")
    return re.compile("".join(pattern), re.DOTALL), names


def solve_template_vars(
    template_text: str,
    user_text: str,
    *,
    target: RenderTarget,
    catalog: Catalog | None = None,
) -> SolvedVars:
    """Recover ``[[var]]`` and ``$env(VAR)`` values by matching site for site.

    A hole two sites of the same file answer differently is reported in
    :attr:`SolvedVars.conflicts` and the first answer is kept: the user's file
    disagreeing with itself is their business, but silently taking one of the
    two answers would put the wrong cell into the baseline and thereby into
    every hunk captured against it.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    spec = cat.target(target)
    template_sites = parse_by_syntax(spec.syntax, template_text)
    user_sites = parse_by_syntax(spec.syntax, user_text)

    values: dict[str, str] = {}
    sites: dict[str, SiteKey] = {}
    conflicts: list[str] = []
    for key, template_raw in template_sites.items():
        user_raw = user_sites.get(key)
        if user_raw is None:
            continue
        built = _site_pattern(template_raw.text)
        if built is None:
            continue
        pattern, names = built
        match = pattern.fullmatch(user_raw.text)
        if match is None:
            continue
        for index, name in enumerate(names):
            value = match.group(f"g{index}")
            if value is None:
                # A hole inside a fragment the user's file does not carry. The
                # value is not wrong, it is absent, and the profile default
                # stands.
                continue
            previous = values.get(name)
            if previous is None:
                values[name] = value
                sites[name] = key
            elif previous != value:
                conflicts.append(
                    f"{target.value} {key[1]}: {name} reads {value!r} here but "
                    f"{previous!r} at {sites[name][1]}; kept {previous!r}"
                )
    return SolvedVars(values=values, sites=sites, conflicts=conflicts)


# --- value acceptance -------------------------------------------------------


def _coerce_solved(option: OptionSpec, text: str) -> Any:
    """Type a solved string the way the catalog says the option is typed."""

    if option.type is OptionType.INT:
        return int(float(text))
    if option.type is OptionType.FLOAT:
        return WrittenFloat(text)
    if option.type is OptionType.LIST:
        return text.split()
    if option.type is OptionType.BOOL:
        raise ValueError("a boolean cannot be solved out of a rendered string")
    return text


def _assignable(option: OptionSpec) -> bool:
    """Whether writing this row into the Recipe would change the output.

    A ``hardcoded_literal`` row is typed into the ``.j2``, so binding its Recipe
    field is refused by :func:`auto_ext.core.render.check_representable` and
    would change nothing even if it were not. Those rows reach the generated
    file through the patch instead, which is what the escape hatch is for.
    """

    return (
        option.owner is Owner.RECIPE
        and option.recipe_field_path is not None
        and option.currently in (Currently.JINJA_VAR, Currently.MANIFEST_KNOB)
    )


def _unassignable_reason(option: OptionSpec) -> str:
    """Why :func:`_assignable` said no, in the words the report shows a user.

    Two different noes, and they used to look like one. Before the templates
    were parameterised almost every profile-owned row was also a
    ``hardcoded_literal``, so "the template writes this as a literal" happened
    to be true of both; it is now false of the whole profile side --
    ``-res_component`` is a ``[[var]]``, it simply is not the recipe's value to
    hold. Reporting the wrong reason sends the user to edit the wrong thing.
    """

    if option.currently not in (Currently.JINJA_VAR, Currently.MANIFEST_KNOB):
        return (
            "the template writes this as a literal "
            f"(currently: {option.currently.value}); a difference is kept "
            "as a manual edit instead"
        )
    if option.owner is not Owner.RECIPE:
        return (
            f"this value belongs to the {option.owner.value}, not the recipe; "
            "the recipe has no field for it, so a difference is kept as a "
            "manual edit instead"
        )
    # The only recipe row with no ``recipe.*`` path is a ``describes_member``
    # row, and those never get here: the reader that owns their collection
    # takes them first, and reports the whole ordered list as one row. The
    # comment this replaces claimed the branch could not be reached at all,
    # which stopped being true the day ``extract`` became a list -- it was
    # then reached on every quantus import, and printed a sentence saying the
    # catalog was missing something when it was not.
    return (  # pragma: no cover - describes_member rows are taken earlier
        f"{option.key} describes one member of {option.context_path}, which "
        "the recipe holds as an ordered list; the whole list is reported as "
        "one row instead of this one"
    )


@dataclass(frozen=True)
class _Verdict:
    """Whether a recovered value may be written into the Recipe, and why.

    Three outcomes, not two. ``refused`` means the value stays out of the
    Recipe and the difference becomes a patch hunk. An empty ``refused`` with
    a ``note`` means the value is used *and* something about it is worth
    saying -- which is the whole of DECISIONS #19 as revised: a value set the
    catalog admits it invented cannot be the authority that rejects a legal
    value, but the user is still entitled to know their spelling is not one
    this tool has ever seen.
    """

    refused: str = ""
    note: str = ""


def _implausible(option: OptionSpec, value: Any) -> _Verdict:
    """Whether ``value`` may be written into the Recipe under ``option``.

    A closed value set refuses; an admitted guess only advises. Eight shipped
    rows carry ``choices_confidence: guess`` or ``likely`` and say so in their
    own notes -- ``extraction_net_name_space`` records that even the *case* of
    its two members is unverified. Rejecting a value against a set like that
    demotes a legal setting to a patch hunk, which pins the literal and kills
    the field the form offers. The form already treats such a set as a hint
    (:attr:`~auto_ext.catalog.OptionSpec.free_input`); the importer answering
    differently is the two-answers-to-one-question this catalog exists to end.

    A value the *model* then refuses is not lost either: see
    :func:`_validate_degrading`.
    """

    if option.type is OptionType.ENUM and option.choices and value not in option.choices:
        mismatch = f"{value!r} is not one of the catalog's choices {option.choices}"
        if option.choices_confidence is Confidence.CERTAIN:
            return _Verdict(refused=mismatch)
        return _Verdict(
            note=(
                f"{mismatch}, but that set is a "
                f"{option.choices_confidence.value} -- the value is kept as "
                "written; check it against the tool's manual"
            )
        )
    if option.type in (OptionType.STR, OptionType.ENUM, OptionType.PATH) and not isinstance(
        value, str
    ):
        return _Verdict(refused=f"{value!r} is not a string")
    return _Verdict()


#: The head of one ``extract`` statement. Two layouts, one rule: a Quantus
#: statement head may carry its first option on the same line -- ``input_db
#: -type calibre`` is that shape, and
#: :func:`auto_ext.core.readback.parse_quantus` has always read it -- so
#: ``extract`` alone on a line and ``extract -selection all -type rc_coupled``
#: are both statement heads, and the manual's own examples use the second.
#: The word boundary is what keeps ``extraction_setup`` out.
_EXTRACT_HEAD = re.compile(r"^\s*extract\b")
_SELECTION_RE = re.compile(
    r"-selection\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?(?:\s+\"([^\"]*)\")?"
)
_TYPE_RE = re.compile(r"-type\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?")


def extract_rules_from_text(text: str) -> list[dict[str, Any]]:
    """Every ``extract`` statement in one command file, in file order.

    Order is the whole point: specifications accumulate first-to-last and the
    last one wins for any net it covers, so a reader that returned a set --
    or that kept only the first -- would silently discard the downgrade the
    author wrote the second statement for.

    Returns plain dicts rather than :class:`ExtractRule` objects so the caller
    can hand them to the same validation everything else goes through, and so
    a malformed statement fails with the recipe rather than here.
    """

    rules: list[dict[str, Any]] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if not _EXTRACT_HEAD.match(lines[index]):
            index += 1
            continue
        body: list[str] = []
        cursor = index
        # The head line itself may carry a continuation; walk while the
        # PREVIOUS line ended in a backslash.
        while cursor < len(lines):
            body.append(lines[cursor])
            if not lines[cursor].rstrip().endswith("\\"):
                break
            cursor += 1
        statement = " ".join(body)
        rule: dict[str, Any] = {}
        selection = _SELECTION_RE.search(statement)
        if selection:
            rule["selection"] = selection.group(1)
            if selection.group(2):
                rule["selection_arg"] = selection.group(2)
        kind = _TYPE_RE.search(statement)
        if kind:
            rule["type"] = kind.group(1)
        if rule:
            rules.append(rule)
        index = cursor + 1
    return rules


#: Which catalog row describes which key of one dict :func:`extract_rules_from_text`
#: returns. The two ``describes_member`` rows, and the reason the collection is
#: held to the same plausibility gate every scalar row goes through.
_EXTRACT_MEMBER_ROWS: tuple[tuple[str, str], ...] = (
    ("selection", "extract_selection"),
    ("type", "extract_type"),
)


def _extract_verdicts(
    rules: Sequence[Mapping[str, Any]], *, catalog: Catalog
) -> tuple[list[str], list[str]]:
    """``(refusals, advisories)`` for one file's ordered extract statements.

    The collection used to be assigned without passing :func:`_implausible` at
    all, so an unknown ``-type`` went straight to the model and took the whole
    import down with it -- while ``metal_fill -type actual``, one section
    further down the same file, degraded to a note and a hunk. Same gate,
    same outcome, and the statement number is named because a deck with four
    of them needs to say which one.
    """

    refusals: list[str] = []
    advisories: list[str] = []
    for index, rule in enumerate(rules, start=1):
        for member, key in _EXTRACT_MEMBER_ROWS:
            if member not in rule:
                continue
            verdict = _implausible(catalog.option(key), rule[member])
            if verdict.refused:
                refusals.append(f"extract statement {index}: {verdict.refused}")
            elif verdict.note:
                advisories.append(f"extract statement {index}: {verdict.note}")
    return refusals, advisories


def _assign(tree: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _validate(tree: Mapping[str, Any]) -> Recipe:
    """Build the Recipe, naming the recovered value the model refuses."""

    try:
        return Recipe.model_validate(dict(tree))
    except ValidationError as exc:
        where = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        raise RecipeImportError(
            f"the values read out of these files do not fit the Recipe model ({where})"
        ) from exc


def _validate_degrading(
    tree: dict[str, Any], defaults: Mapping[str, Any]
) -> tuple[Recipe, dict[str, str]]:
    """Build the Recipe, putting back the default for any value it refuses.

    One unknown spelling used to end the import. ``extract -type rcc`` is a
    real deck that ran last week -- the spelling this project itself once
    shipped -- and ``-exclude_floating_nets_limit 50`` sits outside a bound
    the catalog marks ``range_verified: false``, i.e. one we invented. Neither
    is a reason to refuse the whole file when every other unusable value in
    this importer degrades to a note plus a patch hunk that keeps the user's
    own text.

    So a value the model refuses is put back to the catalog default, its
    field path is reported, and the difference between the default and the
    user's file survives where every other unmodelled difference does: in the
    patch. Returns the Recipe and ``{field path: why it was put back}``.

    A refusal that names no field, or one whose field carries the default
    already, is a real structural error and is raised: quietly returning a
    Recipe nobody's file describes would be worse than failing.
    """

    degraded: dict[str, str] = {}
    for _ in range(len(tree) + 1):
        try:
            return _validate(tree), degraded
        except RecipeImportError:
            pass
        try:
            Recipe.model_validate(dict(tree))
        except ValidationError as exc:
            put_back = False
            for err in exc.errors():
                path = _field_path(err["loc"])
                if path is None or not _restore_default(tree, defaults, path):
                    continue
                degraded[path] = str(err["msg"])
                put_back = True
            if not put_back:
                raise
    return _validate(tree), degraded  # pragma: no cover - the loop terminates


def _field_path(loc: Sequence[Any]) -> str | None:
    """``('extraction','extract',0,'type')`` -> ``extraction.extract``.

    A collection is restored whole. Half a list is not a value the user's file
    describes: the statements accumulate and the last one wins, so keeping the
    readable ones would render a deck that extracts something nobody asked for.
    """

    parts: list[str] = []
    for part in loc:
        if isinstance(part, int):
            break
        parts.append(str(part))
    return ".".join(parts) if parts else None


def _restore_default(
    tree: dict[str, Any], defaults: Mapping[str, Any], path: str
) -> bool:
    """Put the catalog default back at ``path``. False when it is there already."""

    parts = path.split(".")
    node: Any = tree
    source: Any = defaults
    for part in parts[:-1]:
        if not isinstance(node, dict) or not isinstance(source, dict):
            return False
        node = node.get(part)
        source = source.get(part)
    if not isinstance(node, dict) or not isinstance(source, dict):
        return False
    leaf = parts[-1]
    if leaf not in source or node.get(leaf) == source[leaf]:
        return False
    node[leaf] = deepcopy(source[leaf])
    return True


# --- baseline objects -------------------------------------------------------


def _solved(values: Mapping[str, str], name: str) -> str | None:
    return values.get(f"{_VAR}{name}") or None


def _stand_in(
    values: Mapping[str, str], var: str, fallback: str, referenced: frozenset[str]
) -> tuple[str, str | None]:
    """The solved value, or a stand-in plus the note that has to go with it.

    The note is raised only when one of the imported templates actually
    references the variable. A ``dspf.cmd`` carries no cell name at all, and
    warning that the cell could not be read from it would be true and useless.
    """

    found = _solved(values, var)
    if found is not None:
        return found, None
    if var not in referenced:
        return fallback, None
    return fallback, (
        f"no {var} could be read from these files, although one of them needs "
        f"it; the baseline used {fallback!r}, so every line built from it "
        "became a manual edit"
    )


def _derive_dut(
    values: Mapping[str, str], referenced: frozenset[str]
) -> tuple[DutSnapshot, list[str]]:
    """The DUT the user's files are about, read out of the files themselves.

    Not cosmetic. If the baseline is rendered for the wrong cell then every
    line carrying a cell name differs, lands in a patch, and the patch carries
    *that* cell name into every future run. Recovering the real one is what
    keeps the imported recipe portable.
    """

    notes: list[str] = []
    fields: dict[str, Any] = {}
    for field_name, var, fallback in (
        ("library", "library", "IMPORTED_LIB"),
        ("cell", "cell", "imported_cell"),
        ("layout_view", "lvs_layout_view", "layout"),
        ("source_view", "lvs_source_view", "schematic"),
        ("ground_net", "ground_net", "vss"),
        ("out_file", "out_file", "av_ext"),
    ):
        value, note = _stand_in(values, var, fallback, referenced)
        fields[field_name] = value
        if note:
            notes.append(note)
    return DutSnapshot(**fields), notes


def _derive_run(
    values: Mapping[str, str], referenced: frozenset[str]
) -> tuple[RunFacts, list[str]]:
    """Run paths read out of the files. Only the two that reach a template."""

    output_dir, note = _stand_in(
        values, "output_dir", f"{_ENV_PLACEHOLDER}/output_dir", referenced
    )
    notes = [note] if note else []
    return (
        RunFacts(
            run_id="imported",
            run_slug="imported",
            run_dir=Path("imported"),
            workarea=Path("imported"),
            output_dir=output_dir,
            intermediate_dir=output_dir,
            dspf_out_path=_solved(values, "dspf_out_path") or f"{output_dir}/imported.dspf",
        ),
        notes,
    )


#: Template variables that carry a whole path assembled out of the QRC deck
#: directory plus one file name. Since the templates stopped concatenating the
#: two halves themselves, this is where the directory is still visible.
_QRC_COMPOSED_VARS: tuple[str, ...] = ("qrc_query_cmd_name", "preserve_cell_list_name")


def _qrc_dir(values: Mapping[str, str], fallback: Callable[[str, str], str]) -> str:
    """The QRC deck directory, recovered from whatever the file still shows.

    No template spells ``[[qrc_deck_dir]]`` any more -- each site writes one
    assembled path -- so solving the directory on its own returns nothing and
    the stand-in would put ``/auto-ext-import/unresolved`` into a line the
    imported file gives in full. Taking the parent of an assembled path keeps
    the round trip exact; the stand-in stays for a file that carries neither.
    """

    solved = _solved(values, "qrc_deck_dir")
    if solved:
        return solved
    for var in _QRC_COMPOSED_VARS:
        composed = _solved(values, var)
        if composed and "/" in composed:
            return str(PurePosixPath(composed).parent)
    return fallback("qrc_deck_dir", f"{_ENV_PLACEHOLDER}/qrc_deck_dir")


#: Semantic corner name used when the files name no ``-technology_corner`` at
#: all. Only reached by a file that does not carry the line; a file that does
#: gets its own literal, slugified.
_FALLBACK_CORNER = "typical"


@dataclass(frozen=True)
class ProfileLanding:
    """One value the importer put into the *derived* baseline profile.

    Recorded so the report can say where a profile-owned value went. Without
    it every such row is reported as "the recipe has no field for it, so a
    difference is kept as a manual edit" -- which is false whenever the value
    did land, and is exactly the sentence that hid the corner bug.
    """

    #: ``PdkProfile`` field path, e.g. ``corners.rcworst.technology_corner``.
    #: Dotted the whole way down, brackets included nowhere: the path is shown
    #: in a Rich table, and ``[rcworst]`` there is console markup, not text.
    field: str
    #: The value as it went in, so the report shows what landed rather than
    #: whatever the literal reader made of a line several rows share.
    value: Any


def _corner_literal(values: Mapping[str, str], readback: TemplateReadBack) -> str | None:
    """The ``-technology_corner`` string the user's files carry, if any."""

    solved = _solved(values, "technology_corner")
    if solved:
        return solved
    literal = readback.get("technology_corner")
    return literal if isinstance(literal, str) and literal else None


def _corner_for_literal(profile: PdkProfile, literal: str) -> CornerSpec | None:
    """The profile's corner whose tool literal is exactly ``literal``.

    Matched on the literal, not on the semantic name: the file says
    ``RCWORST`` and only the profile knows what a recipe should call that.
    """

    for corner in profile.corners:
        if corner.technology_corner == literal:
            return corner
    return None


def _corner_default(catalog: Catalog) -> str:
    """The ``-technology_corner`` literal the shipped catalog ships with."""

    default = catalog.option("technology_corner").default
    return str(default) if default is not None else "TYPICAL"


def _tech_library_expr(solved: str | None) -> str:
    """The profile expression that renders the technology library path.

    The shipped default is an env reference (``$env(SETUP_ROOT)/...``), and
    keeping it is what lets :func:`_infer_env` read the user's ``SETUP_ROOT``
    back out of their absolute path -- a site fact belongs in the environment,
    not baked into a recipe that gets shared. A path the expression cannot
    explain is a different library entirely, and then the literal goes into
    the derived profile: a profile field the user can edit beats a hunk that
    pins the path.
    """

    if not solved:
        return DEFAULT_TECH_LIBRARY_FILE
    ref = _sole_env_ref(DEFAULT_TECH_LIBRARY_FILE)
    if ref is not None and _invert_env(DEFAULT_TECH_LIBRARY_FILE, ref, solved):
        return DEFAULT_TECH_LIBRARY_FILE
    return solved


#: ``(template var, ParasiticDeviceContract field)`` for every parasitic-device
#: row a rendered file carries. Quantus names the device, Jivaro binds a model
#: of the same cell, and the contract refuses any other combination.
_PARASITIC_VARS: tuple[tuple[str, str], ...] = (
    ("res_component", "res_component"),
    ("cap_component", "cap_component"),
    ("jivaro_r_model", "res_model"),
    ("jivaro_c_model", "cap_model"),
    ("jivaro_l_model", "ind_model"),
    ("jivaro_k_model", "mutual_model"),
)

#: The two halves the contract validator checks against each other.
_PARASITIC_PAIRS: tuple[tuple[str, str], ...] = (
    ("res_component", "res_model"),
    ("cap_component", "cap_model"),
)


def _derive_parasitics(
    values: Mapping[str, str],
    *,
    targets: Sequence[RenderTarget],
    catalog: Catalog,
    land: Callable[[str, str, Any], None],
    notes: list[str],
) -> ParasiticDeviceContract:
    """The parasitic-device contract the user's files describe.

    Six rows, one object, and a validator that refuses a half-changed pair: if
    Quantus writes ``presistor`` while Jivaro reads ``analogLib/rppolywo/symbol``
    the flow silently extracts nothing, so
    :class:`~auto_ext.model.pdk.ParasiticDeviceContract` will not hold it.

    Which means the two sides have to be filled together. A quantus command
    file imported *without* its jivaro XML gives the component and no model;
    the model is moved to match, which changes no rendered byte (the file that
    would show it is not part of this import) and keeps the profile loadable.
    When both files are here, both sides come from them -- and if they
    disagree, that disagreement is the user's own. It is reported and every
    row keeps the catalog default, so the difference stays visible as a hunk
    instead of being resolved by a guess.
    """

    default = ParasiticDeviceContract()
    fields: dict[str, str] = {}
    for var, field_name in _PARASITIC_VARS:
        found = _solved(values, var)
        if found:
            fields[field_name] = found
    if not fields:
        return default

    if RenderTarget.JIVARO_XML not in targets:
        for component_field, model_field in _PARASITIC_PAIRS:
            if component_field in fields and model_field not in fields:
                library, _cell, view = getattr(default, model_field).split("/")
                fields[model_field] = f"{library}/{fields[component_field]}/{view}"

    try:
        contract = ParasiticDeviceContract(**fields)
    except ValidationError as exc:
        notes.append(
            "the parasitic device names in these files do not form a contract "
            f"the profile can hold ({exc.errors()[0]['msg']}); every one of "
            "them stays at the catalog default, so the differences are kept as "
            "manual edits"
        )
        return default

    for var, field_name in _PARASITIC_VARS:
        found = _solved(values, var)
        option = catalog.by_template_var(var)
        if found and option is not None:
            land(option.key, f"parasitics.{field_name}", found)
    return contract


def _derive_profile(
    values: Mapping[str, str],
    *,
    profile_id: str,
    variant_name: str,
    readback: TemplateReadBack,
    referenced: frozenset[str],
    corner_literal: str | None,
    targets: Sequence[RenderTarget],
    catalog: Catalog,
) -> tuple[PdkProfile, list[str], dict[str, ProfileLanding]]:
    """A PdkProfile that makes the baseline render like the user's files.

    Only rows the templates read through a ``[[var]]`` are set. A PDK fact the
    template *hardcodes* -- the rules-file naming pattern -- cannot be moved by
    a profile field, because
    :func:`auto_ext.core.render.check_representable` refuses exactly that; that
    difference goes through the patch, and the path expressions that are env
    references go through :func:`_infer_env` instead.

    Which rows those are shrinks as templates are parameterised, and every row
    that crosses over has to be picked up here or the file stops round
    tripping: the value now arrives through a profile field, so a profile that
    leaves it unset renders a *different* file and the importer records the
    difference as a patch hunk. The supply tables, the QRC file names, the
    parasitic device contract and the corner are read straight out of the file
    for that reason.

    The corner is the one that bites hardest. ``-technology_corner "RCWORST"``
    against a profile whose only corner is ``TYPICAL`` renders ``TYPICAL``, and
    the difference becomes a hunk holding the literal ``RCWORST`` -- which pins
    it, so a recipe that later selects another corner renders one string and
    gets the other. A *derived* profile is a scaffold this function owns, so
    the corner the file names is added to it and becomes an entry the recipe
    can select by name. A profile the caller supplied is not ours to edit; see
    :func:`import_recipe`.

    ``variant_name`` is the deck variant the *recipe* ended up with, not the
    token found in the file: ``[[lvs_variant]]`` renders the recipe's value, so
    the profile has to define a variant under that name or ``_pdk_context``
    raises. When the two differ, the rules-file line differs too and is kept as
    a hunk -- visible, rather than a render that cannot happen.

    Returns the profile, notes for the report, and every catalog row that
    landed in it (see :class:`ProfileLanding`).

    This is a scaffold for the baseline render, not a discovered PDK, and says
    so in its own fields.
    """

    notes: list[str] = []
    landed: dict[str, ProfileLanding] = {}

    def land(key: str, field: str, value: Any) -> None:
        landed[key] = ProfileLanding(field=field, value=value)

    def fallback(var: str, stand_in: str) -> str:
        value, note = _stand_in(values, var, stand_in, referenced)
        if note:
            notes.append(note)
        return value

    def from_file(var: str, stand_in: str, key: str, field: str) -> str:
        """``fallback``, plus the landing when the value really came from a file."""

        value = fallback(var, stand_in)
        if _solved(values, var):
            land(key, field, value)
        return value

    # Every one of these reaches a template as a [[var]], and a None there is a
    # RenderError, not a blank. A visible stand-in turns "the baseline cannot be
    # built" into "here is the line you will have to look at", which is the
    # whole point of importing a file we only partly understand.
    lvs_dir = from_file(
        "calibre_lvs_dir",
        f"{_ENV_PLACEHOLDER}/lvs_deck_dir",
        "lvs_deck_dir",
        "lvs_decks.dir_expr",
    )
    qrc_dir = _qrc_dir(values, fallback)
    if _solved(values, "qrc_deck_dir") or any(
        _solved(values, var) for var in _QRC_COMPOSED_VARS
    ):
        land("qrc_deck_dir", "qrc.dir_expr", qrc_dir)
    cdl_include = from_file(
        "inc_file",
        f"{_ENV_PLACEHOLDER}/cdl_include_file",
        "cdl_include_file",
        "cdl_include_files",
    )
    tech_name = from_file("tech_name", "IMPORTED", "tech_name", "tech_name")
    basename = _solved(values, "calibre_lvs_basename")
    if basename:
        land("lvs_deck_basename", "lvs_decks.basename", basename)
    variant_token = _solved(values, "lvs_variant")
    temperature = readback.get("temperature_c")
    # Read back as typed values rather than solved strings: the supply tables
    # are lists and the query-command row is a file name parsed out of a
    # longer Tcl trigger line, both of which SPECIAL_READBACK already handles.
    power_names = readback.get("power_names") or []
    ground_names = readback.get("ground_names") or []
    query_cmd_name = readback.get("qrc_query_cmd_name")
    preserve_cell_list = readback.get("qrc_preserve_cell_list_name")
    if power_names:
        land("power_names", "power_names", power_names)
    if ground_names:
        land("ground_names", "ground_names", ground_names)
    if query_cmd_name:
        land("qrc_query_cmd_name", "qrc.query_cmd_name", query_cmd_name)
    if preserve_cell_list:
        land(
            "qrc_preserve_cell_list_name",
            "qrc.preserve_cell_list_name",
            preserve_cell_list,
        )

    corner_name = (
        slugify(corner_literal, max_len=64) if corner_literal else _FALLBACK_CORNER
    )
    shipped_corner = _corner_default(catalog)
    if corner_literal:
        land(
            "technology_corner",
            f"corners.{corner_name}.technology_corner",
            corner_literal,
        )
        if corner_literal != shipped_corner:
            notes.append(
                f"these files extract at corner {corner_literal!r}, which the "
                "shipped catalog does not know; the derived baseline profile "
                f"gained a corner {corner_name!r} -> {corner_literal!r} and "
                "recipe.extraction.corner names it, so the literal is a PDK "
                "fact rather than a manual edit"
            )

    tech_library = _solved(values, "technology_library_file")
    tech_library_expr = _tech_library_expr(tech_library)
    if tech_library:
        # The landed value is the path the file carried, not the expression
        # that reproduces it: the report answers "what did your file say and
        # where did it go", and the env reference is how, not what.
        land("technology_library_file", "tech_library_file", tech_library)

    qrc = QrcDeck(dir_expr=qrc_dir)
    if query_cmd_name:
        qrc = qrc.model_copy(update={"query_cmd_name": query_cmd_name})
    if preserve_cell_list:
        qrc = qrc.model_copy(update={"preserve_cell_list_name": preserve_cell_list})

    return (
        PdkProfile(
            profile_id=profile_id,
            display_name="Baseline derived from the imported files",
            description=(
                "Not a discovered PDK. Built by the recipe importer so that the "
                "baseline render matches the files it read; replace it with a "
                "real profile before running anything."
            ),
            tech_name=tech_name,
            tech_library_file=tech_library_expr,
            lvs_decks=LvsDeckSet(
                dir_expr=lvs_dir,
                basename=basename,
                variants=[
                    LvsDeckVariant(
                        name=variant_name, rules_suffix=variant_token or variant_name
                    )
                ],
                default_variant=variant_name,
            ),
            qrc=qrc,
            cdl_include_files=[cdl_include],
            power_names=list(power_names),
            ground_names=list(ground_names),
            parasitics=_derive_parasitics(
                values, targets=targets, catalog=catalog, land=land, notes=notes
            ),
            corners=[
                CornerSpec(
                    name=corner_name,
                    technology_corner=corner_literal or shipped_corner,
                    default_temperature_c=(
                        temperature if temperature is not None else 25.0
                    ),
                )
            ],
            default_corner=corner_name,
            discovered_from=["auto_ext.core.recipe_import"],
            hand_edited=True,
        ),
        notes,
        landed,
    )


#: Profile path expressions that may carry an env reference, paired with the
#: template variable a rendered file now writes that expression into. Every
#: pair exists because the templates stopped spelling the reference out: the
#: ``.j2`` used to say ``$env(SETUP_ROOT)/assura_tech.lib`` and now says
#: ``[[technology_library_file]]``, so ``discover_required_vars`` over the
#: template text no longer sees ``SETUP_ROOT`` and the only remaining way to
#: recover it from the user's own file is to invert the expression.
#:
#: The pairing is checked against the catalog before it is used
#: (:func:`_invertible_sites`), so a row that stops being a ``[[var]]`` drops
#: out of here rather than promising a value nothing can supply.
_PROFILE_ENV_EXPRESSIONS: tuple[tuple[str, Callable[[PdkProfile], Iterable[str]]], ...] = (
    ("technology_library_file", lambda p: [p.tech_library_file]),
    ("inc_file", lambda p: list(p.cdl_include_files)),
    ("calibre_lvs_dir", lambda p: [p.lvs_decks.dir_expr]),
    ("qrc_deck_dir", lambda p: [p.qrc.dir_expr]),
)

#: Prefix for the capture group :func:`_invert_env` builds. A named group keeps
#: the mapping back to the variable explicit and cannot collide with anything
#: ``re.escape`` emits for the literal parts of the expression.
_ENV_GROUP = "envhole"


def _sole_env_ref(expression: str) -> re.Match[str] | None:
    """The one env reference in ``expression``, when inverting it is possible.

    Three ways an expression is not invertible, and all three have to agree
    with what :func:`_invert_env` will actually do -- a caller that predicts
    "solvable" and then solves nothing leaves the var bound to nothing at all:

    - no reference: there is nothing to recover;
    - more than one: ``$A/$B`` against ``/x/y/z`` has three readings, and
      picking one writes a wrong site fact into the baseline;
    - a path filter (:func:`auto_ext.core.env.resolve_path_expr`):
      ``$calibre_source_added_place|parent`` renders the *parent* of the
      value, and a parent does not identify its child.
    """

    if "|" in expression:
        return None
    refs = list(_ENV_REF_RE.finditer(expression))
    return refs[0] if len(refs) == 1 else None


def _invertible_sites(
    profile: PdkProfile, catalog: Catalog
) -> list[tuple[str, str, re.Match[str]]]:
    """``(template var, expression, its one env ref)`` for every invertible pair."""

    found: list[tuple[str, str, re.Match[str]]] = []
    for var, read in _PROFILE_ENV_EXPRESSIONS:
        option = catalog.by_template_var(var)
        # No hole in any template means no rendered value to match against, so
        # whatever the profile says here never reaches a file as its own token.
        if option is None or not option.expected_in_templates:
            continue
        for expression in read(profile):
            if not expression:
                continue
            ref = _sole_env_ref(str(expression))
            if ref is not None:
                found.append((var, str(expression), ref))
    return found


def _env_name(ref: re.Match[str]) -> str:
    return ref.group(1) or ref.group(2) or ref.group(3)


def _invert_env(expression: str, ref: re.Match[str], value: str) -> dict[str, str]:
    """Read the env value back out of ``value`` by matching it against ``expression``.

    The inverse of :func:`auto_ext.core.env.substitute_env`:
    ``$env(SETUP_ROOT)/assura_tech.lib`` against
    ``/pdk/hn001/setup/assura_tech.lib`` gives ``SETUP_ROOT=/pdk/hn001/setup``.
    A value that does not fit the expression yields nothing, which leaves the
    var unbound and therefore reported -- reporting beats inventing.

    A recovered value that is *itself* an env reference is refused for the
    same reason. Matching the expression against a file that spells the same
    expression out gives ``SETUP_ROOT = "$env(SETUP_ROOT)"``: not an answer,
    the question again. :func:`auto_ext.core.env.substitute_env` does not
    re-scan its own replacement, so the reference survived into the rendered
    baseline and the render refused it -- one file's honest ``$env(...)``
    turning into "this cannot be rendered", several layers away from the
    binding that caused it.
    """

    pattern = (
        re.escape(expression[: ref.start()])
        + f"(?P<{_ENV_GROUP}>.+)"
        + re.escape(expression[ref.end() :])
    )
    match = re.fullmatch(pattern, value)
    if match is None:
        return {}
    bound = match.group(_ENV_GROUP)
    if discover_required_vars([bound]):
        return {}
    return {_env_name(ref): bound}


def _env_from_profile(
    profile: PdkProfile | None, values: Mapping[str, str], catalog: Catalog
) -> dict[str, str]:
    """Env vars recovered by matching the profile's expressions against the file.

    A var recovered here is a fact about the *user's* site read out of the
    *user's* file, which is the only source that keeps this machine's paths out
    of the baseline and out of the hunks captured against it.
    """

    if profile is None:
        return {}
    found: dict[str, str] = {}
    for var, expression, ref in _invertible_sites(profile, catalog):
        solved = _solved(values, var)
        if not solved:
            continue
        for name, bound in _invert_env(expression, ref, solved).items():
            found.setdefault(name, bound)
    return found


@dataclass(frozen=True)
class EnvSolution:
    """One environment variable recovered from a file the user's site produced."""

    name: str
    value: str
    #: How it was recovered, in the user's terms -- the expression that was
    #: inverted, or the template that spells the reference out. Shown next to
    #: the value: an env value is a site fact, and a user asked to accept one
    #: is entitled to know what it was read out of.
    via: str


def solve_env_from_file(
    text: str,
    *,
    target: RenderTarget,
    profile: PdkProfile | None,
    catalog: Catalog | None = None,
    templates_root: Path | None = None,
) -> tuple[list[EnvSolution], list[str]]:
    """Read environment values back out of one generated file.

    Returns ``(solutions, notes)``. This is :func:`import_recipe`'s env half
    on its own: same solver, same inversion, no Recipe, no baseline render and
    no patch capture -- so a file that is a poor recipe (a different tool
    vintage, a heavily hand-edited runset) still gives up whatever site facts
    it does carry, instead of failing as a whole.

    The two sources are the two :func:`_infer_env` uses, and the second is the
    interesting one: a value the templates no longer spell as ``$env(X)``
    because the parameterisation round moved it into a profile expression is
    recovered by matching that expression against what the file actually says.
    ``$env(SETUP_ROOT)/assura_tech.lib`` against
    ``/pdk/hn001/setup/assura_tech.lib`` yields ``SETUP_ROOT``.

    Nothing here reads ``os.environ``. What the shell says is a different
    question -- and the whole point of this function is the variables about
    which the shell has nothing to say.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    spec = cat.target(target)
    solved = solve_template_vars(
        _template_text(spec, templates_root), text, target=target, catalog=cat
    )

    found: dict[str, EnvSolution] = {}
    notes = list(solved.conflicts)

    # 1. references the template itself still spells out
    for key, value in solved.values.items():
        if not key.startswith(_ENV):
            continue
        name = key[len(_ENV) :]
        found.setdefault(
            name,
            EnvSolution(name, value, f"written as $env({name}) in {spec.id.value}"),
        )

    # 2. references that now reach the file through a profile expression
    if profile is not None:
        for var, expression, ref in _invertible_sites(profile, cat):
            rendered = _solved(solved.values, var)
            if not rendered:
                continue
            for name, bound in _invert_env(expression, ref, rendered).items():
                found.setdefault(
                    name,
                    EnvSolution(name, bound, f"{expression} matched against {rendered}"),
                )

    return [found[name] for name in sorted(found)], notes


def env_vars_solvable_from_files(
    profile: PdkProfile | None,
    *,
    catalog: Catalog | None = None,
    templates_root: Path | None = None,
) -> set[str]:
    """Env vars an import can read out of the user's files rather than the shell.

    Two sources, and the second one is why this function exists: a reference
    still written in a template, and a reference in a profile path expression
    whose rendered value a template now carries as a ``[[var]]``. Taking either
    from the shell would put this machine's paths into the baseline and, from
    there, into a stored hunk that travels with the recipe.

    Everything *not* in this set has no source but the shell, so a caller that
    over-reports here does not merely pick a worse value -- it leaves the var
    with no value at all. That is why the second source is computed by the same
    :func:`_invertible_sites` the solving itself uses.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    sources: list[str] = []
    for spec in cat.targets:
        try:
            sources.append(
                template_path_for(spec, templates_root=templates_root).read_text(
                    encoding="utf-8"
                )
            )
        except OSError:  # pragma: no cover - import_recipe reports this properly
            continue
    solvable = discover_required_vars(sources)
    if profile is None:
        return solvable
    solvable.update(_env_name(ref) for _var, _expr, ref in _invertible_sites(profile, cat))
    return solvable


def _infer_env(
    values: Mapping[str, str],
    supplied: Mapping[str, str],
    required: Iterable[str],
    *,
    profile: PdkProfile | None,
    catalog: Catalog,
) -> tuple[dict[str, str], list[str]]:
    """Bind every env var the templates reference, preferring what was solved.

    An env reference is substituted before Jinja runs, so an unbound one is not
    a missing value, it is a file that cannot be rendered at all. Solving
    ``$env(SETUP_ROOT)/assura_tech.lib`` against the user's absolute path is
    also what keeps a machine path *out* of the recipe's patches: env values
    are site facts, and a recipe carrying one stops being portable the moment
    it is shared.

    Two ways to solve one. A reference the template itself spells out arrives
    already split by the hole solver, under an ``env:`` key. A reference that
    now reaches the file through a profile expression -- everything the
    parameterisation round moved out of the ``.j2`` -- is recovered by
    :func:`_env_from_profile` instead. ``supplied`` wins over both, because it
    is the caller saying so out loud.
    """

    notes: list[str] = []
    env = {
        key[len(_ENV) :]: value for key, value in values.items() if key.startswith(_ENV)
    }
    for name, bound in _env_from_profile(profile, values, catalog).items():
        env.setdefault(name, bound)
    env.update(supplied)
    for var in sorted(set(required) - set(env)):
        env[var] = f"{_ENV_PLACEHOLDER}/{var}"
        notes.append(
            f"environment variable {var} was neither supplied nor readable from "
            "these files; the baseline used a placeholder, so the line built "
            "from it became a manual edit"
        )
    return env, notes


# --- the import -------------------------------------------------------------


def _template_text(spec: RenderTargetSpec, templates_root: Path | None) -> str:
    """The shipped template for one target, as the pattern both readers use."""

    path = template_path_for(spec, templates_root=templates_root)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecipeImportError(
            f"cannot read the template {path} that {spec.id.value} is imported "
            f"against: {exc}"
        ) from exc


def _load(files: Iterable[Path | str | ImportSource]) -> list[ImportSource]:
    loaded = [
        item if isinstance(item, ImportSource) else ImportSource.from_path(item)
        for item in files
    ]
    if not loaded:
        raise RecipeImportError("no files to import")
    return loaded


def _stages_for(targets: Sequence[RenderTarget], catalog: Catalog) -> list[Stage]:
    present = {catalog.target(target).stage for target in targets}
    return [stage for stage in STAGE_ORDER if stage in present]


def _emit_for(targets: Sequence[RenderTarget]) -> list[OutputKind] | None:
    kinds = [
        kind
        for kind, target in (
            (OutputKind.EXTRACTED_VIEW, RenderTarget.QUANTUS_EXT),
            (OutputKind.DSPF, RenderTarget.QUANTUS_DSPF),
        )
        if target in targets
    ]
    return kinds or None


@dataclass(frozen=True)
class _Baseline:
    """The three texts :func:`capture_patch` needs, plus what produced them."""

    plan: TargetPlan
    source: str
    substituted: str
    real: str
    masked: str
    values: dict[str, str]


def _baseline(
    plan: TargetPlan,
    *,
    context: Mapping[str, Any],
    recipe: Recipe,
    profile: PdkProfile,
    resources: ResourceProfile,
    resolved_env: Mapping[str, str],
    catalog: Catalog,
    templates_root: Path | None,
) -> _Baseline:
    """Render one target three ways: real, masked, and the slot values.

    The real render goes through :func:`auto_ext.core.render.render_one` rather
    than a local Jinja call so the import is held to the same representability
    and leftover-env checks a run is.
    """

    source = _template_text(plan.spec, templates_root)
    substituted = substitute_env(source, dict(resolved_env))
    rendered = render_one(
        plan,
        context=context,
        recipe=recipe,
        profile=profile,
        resolved_env=resolved_env,
        out_dir=Path("."),
        resources=resources,
        catalog=catalog,
        templates_root=templates_root,
        write=False,
    )
    return _Baseline(
        plan=plan,
        source=source,
        substituted=substituted,
        real=rendered.text,
        masked=render_masked(substituted, context),
        values=mask_values(substituted, context),
    )


def _hunk_summaries(patch: TemplatePatch, target: RenderTarget) -> list[PatchedHunk]:
    out: list[PatchedHunk] = []
    for hunk in patch.hunks:
        before = hunk.before.splitlines()
        after = hunk.after.splitlines()
        shown = next((line.strip() for line in (*after, *before) if line.strip()), "")
        out.append(
            PatchedHunk(
                target=target,
                template_id=patch.template_id,
                hunk_id=hunk.id,
                removed=len(before),
                added=len(after),
                at_line=(hunk.recorded_start or 0) + 1,
                summary=shown if len(shown) <= 90 else f"{shown[:87]}...",
            )
        )
    return out


def _roundtrip(target: RenderTarget, original: str, produced: str) -> RoundTrip:
    if original == produced:
        return RoundTrip(target=target, identical=True)
    return RoundTrip(
        target=target,
        identical=False,
        diff="\n".join(
            difflib.unified_diff(
                produced.splitlines(),
                original.splitlines(),
                fromfile=f"{target.value} (re-rendered)",
                tofile=f"{target.value} (imported file)",
                lineterm="",
                n=2,
            )
        ),
    )


def _landing_site(
    solved_site: Mapping[str, ReadSite],
    readback: TemplateReadBack,
    key: str,
    *,
    catalog: Catalog,
) -> ReadSite | None:
    """Where a value that landed in the profile was read from.

    The solver's site first: a row whose landing site several rows share is
    refused by the literal reader for the whole line, and the solver is the
    one that took only this row's part of it.
    """

    option = catalog.option(key)
    solved = solved_site.get(f"{_VAR}{option.template_var}")
    return solved if solved is not None else readback.sites.get(key)


def _derive_resources(
    base: ResourceProfile, readback: TemplateReadBack, *, catalog: Catalog
) -> tuple[ResourceProfile, dict[str, str]]:
    """The caller's resource profile with what these files state written in.

    ``*cmnNumTurbo: 8`` is read by the literal reader like everything else and
    was then refused by :func:`_assignable` (a Recipe has no field for it) and
    by the result object (which had no slot for it either), so the eight the
    user's runset asks for was printed once and forgotten. It belongs to the
    machine, not the recipe, which is why it goes to a separate object rather
    than into :class:`~auto_ext.model.recipe.Recipe`.

    Returns the profile and ``{catalog key: field name}`` for what moved, so
    the report can say where each value went.
    """

    values: dict[str, Any] = {}
    landed: dict[str, str] = {}
    for key, value in readback.values.items():
        option = catalog.option(key)
        path = option.context_path
        if option.owner is not Owner.RESOURCES or path is None:
            continue
        if not path.startswith("resources."):
            continue
        field_name = path[len("resources.") :]
        if field_name not in type(base).model_fields:
            # ``employee_id`` carries the resources owner only because the
            # owner enum has no ``site`` member; it lives in site.yaml.
            continue
        values[field_name] = value
        landed[key] = field_name
    if not values:
        return base, {}
    return base.model_copy(update=values), landed


def _with_resources(
    mapped: Sequence[MappedValue], landed: Mapping[str, str]
) -> list[MappedValue]:
    """Say, on the row itself, that a resource-owned value was kept.

    ``applied_to`` stays ``None`` on purpose: every one of these rows is a
    hardcoded literal in the template today, so the rendered file still comes
    from the patch and claiming the field drives it would be the "you can
    change it but it does nothing" lie this importer's landing rule exists to
    prevent.
    """

    return [
        replace(
            row,
            note="; ".join(
                part
                for part in (
                    row.note,
                    f"the value is kept on RecipeImportResult.resources"
                    f".{landed[row.key]}, which is per machine and does not "
                    "travel with the recipe",
                )
                if part
            ),
        )
        if row.key in landed and not row.applied_to
        else row
        for row in mapped
    ]


def _with_degraded(
    mapped: Sequence[MappedValue], degraded: Mapping[str, str]
) -> list[MappedValue]:
    """Un-land every row the model refused, saying which value it refused.

    A row that reports ``applied_to`` for a field the Recipe does not actually
    hold is the worst answer available: the user reads "it went in", edits the
    field, and the patch that carries their real line renders over it.
    """

    return [
        replace(
            row,
            applied_to=None,
            note="; ".join(
                part
                for part in (
                    row.note,
                    f"the Recipe model refuses this value ({degraded[row.applied_to]}), "
                    "so the catalog default is rendered and your line is kept as "
                    "a manual edit",
                )
                if part
            ),
        )
        if row.applied_to in degraded
        else row
        for row in mapped
    ]


def _with_landing(
    mapped: Sequence[MappedValue],
    *,
    key: str,
    value: Any,
    destination: str,
    field: str,
    owner: Owner,
    site: ReadSite | None,
    labels: Mapping[RenderTarget, str],
) -> list[MappedValue]:
    """Record that ``key`` landed in ``destination.field``, and say so once.

    An existing row carrying the same value is *revised* rather than joined by
    a second row: the report must not answer "where did this go" twice. A row
    carrying a different value stays exactly as it is -- that is the composite
    line the literal reader had to refuse (``*lvsRulesFile`` read whole), and
    its refusal is still the truth about that reading. The value that did land
    joins as its own row, the way a solved variable already does.
    """

    rows = list(mapped)
    for index, row in enumerate(rows):
        if row.key == key and row.value == value:
            rows[index] = replace(row, destination=destination, applied_to=field, note="")
            return rows
    if site is None:  # pragma: no cover - every landing is read somewhere
        return rows
    rows.append(
        MappedValue(
            key=key,
            value=value,
            owner=owner,
            source=labels[site.target],
            site=site,
            origin="variable",
            destination=destination,
            applied_to=field,
        )
    )
    return rows


def import_recipe(
    files: Iterable[Path | str | ImportSource],
    *,
    recipe_id: str,
    name: str | None = None,
    catalog: Catalog | None = None,
    profile: PdkProfile | None = None,
    dut: DutSnapshot | None = None,
    resources: ResourceProfile | None = None,
    resolved_env: Mapping[str, str] | None = None,
    templates_root: Path | None = None,
    warn_ratio: float = DEFAULT_WARN_RATIO,
) -> RecipeImportResult:
    """Build one Recipe out of the user's own EDA files. Writes nothing.

    ``files`` is any subset of the five render targets, as paths or as
    :class:`ImportSource` buffers; each file's target is decided by its content
    unless the ``ImportSource`` names one. ``profile`` and ``dut`` are the
    baseline the recovered values are rendered against -- pass the real ones
    when they are known. Both are otherwise derived from the files, which is
    what keeps the resulting patches free of this cell's name and this
    machine's paths.

    The steps, in order, and what each one buys:

    1. **Recognise** every file (:func:`detect_target`). One that cannot be
       recognised stops the import instead of contributing noise to a recipe.
    2. **Read the literals** with the catalog's own rules
       (:func:`auto_ext.core.readback.read_back_from_templates`), refusing the
       lines that several catalog rows share.
    3. **Solve the variables** by matching the template's text at each landing
       site against the user's (:func:`solve_template_vars`). This is where the
       cell, the library, the output directory, the deck variant and the env
       values come from.
    4. **Build** the Recipe from the catalog defaults plus every recovered
       value that would actually change a rendered file.
    5. **Render the baseline** and hand the user's file to
       :func:`auto_ext.core.patch.capture_patch` as the edited side, so every
       remaining difference is kept as a masked hunk instead of being lost.
    6. **Render again, patches and all**, and diff against the input. That
       round trip is reported per target: an import that does not reproduce its
       own input says so rather than being believed.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    res = resources if resources is not None else ResourceProfile()
    warnings: list[str] = []

    # 1. recognise -----------------------------------------------------------
    texts: dict[RenderTarget, str] = {}
    labels: dict[RenderTarget, str] = {}
    imported: list[ImportedFile] = []
    supplied_env = dict(resolved_env or {})
    for source in _load(files):
        # A deck that says ``$env(SETUP_ROOT)/assura_tech.lib`` and one that
        # says the path it expands to are the same deck, and the caller is the
        # only one who knows which is which. Expanding here rather than
        # further in is what lets the rest of the import treat both alike --
        # the solver reads a path, the profile holds a path, and the baseline
        # renders the same bytes the user's file carries.
        text = substitute_env(source.text.replace("\r\n", "\n"), supplied_env)
        target = source.target or detect_target(text, label=source.label, catalog=cat)
        if target in texts:
            raise RecipeImportError(
                f"{source.label} and {labels[target]} are both {target.value}; "
                "one import produces at most one file per target"
            )
        texts[target] = text
        labels[target] = source.label
        imported.append(
            ImportedFile(
                label=source.label,
                target=target,
                line_count=len(text.splitlines()),
                forced=source.target is not None,
                crlf="\r\n" in source.text,
            )
        )
    order = [spec.id for spec in cat.targets if spec.id in texts]

    unbound = sorted(discover_required_vars(texts.values()))
    if unbound:
        # Refused here, by name, rather than four steps later in the
        # renderer's words. Everything downstream -- the profile expression
        # the value is read into, the baseline it renders, the hunk that
        # would carry the reference back into the output -- fails on this one
        # missing fact, and only the user has it.
        raise RecipeImportError(
            f"{', '.join(labels[target] for target in order)} spell(s) out "
            f"environment variable(s) {', '.join(unbound)}, and this tool "
            "writes decks with every path already resolved, so it cannot "
            "render the baseline your file is compared against without their "
            "values. Supply them (--env NAME=VALUE, or resolved_env=) and "
            "import again."
        )

    # 2. the literals, 3. the variables --------------------------------------
    readback = read_back_from_templates(texts, catalog=cat)
    composites = composite_sites(cat)
    template_source: dict[RenderTarget, str] = {}
    solved: dict[str, str] = {}
    solved_site: dict[str, ReadSite] = {}
    for target in order:
        spec = cat.target(target)
        raw_template = _template_text(spec, templates_root)
        template_source[target] = raw_template
        found = solve_template_vars(raw_template, texts[target], target=target, catalog=cat)
        warnings.extend(found.conflicts)
        user_sites = parse_by_syntax(spec.syntax, texts[target])
        for key, value in found.values.items():
            if key in solved:
                if solved[key] != value:
                    warnings.append(
                        f"{key} reads {value!r} in {labels[target]} but "
                        f"{solved[key]!r} in {labels[solved_site[key].target]}; "
                        f"kept {solved[key]!r}"
                    )
                continue
            where = found.sites[key]
            solved[key] = value
            solved_site[key] = ReadSite(
                target=target,
                section=where[0],
                option=where[1],
                line=user_sites[where].line if where in user_sites else None,
            )

    # 4. build the recipe -----------------------------------------------------
    mapped: list[MappedValue] = []
    tree: dict[str, Any] = recipe_from_catalog(
        recipe_id=recipe_id, name=name or f"Imported {recipe_id}", catalog=cat
    ).model_dump(mode="python")
    tree.pop("patches", None)
    tree.pop("updated_at", None)
    defaults = deepcopy(tree)

    for key in sorted(readback.values):
        option = cat.option(key)
        value = readback.values[key]
        site = readback.sites[key]
        shared = composites.get((site.target, site.section, site.option))
        if key in SPECIAL_READBACK:
            # The refusal below exists because the generic rule hands every
            # row on a shared line the WHOLE line. A row with a special
            # handler is the exception readback.py documents: it takes only
            # its own share. ``run_qrc_query`` reads whether the third
            # post-trigger is there at all, which is a fact about that line
            # and about nothing else -- refusing it threw away a correct
            # answer and left the recipe claiming a query run the file does
            # not ask for, with the box in the form powerless to change it.
            shared = None
        applied: str | None = None
        if shared is not None:
            note = (
                f"{site.option} also carries "
                f"{[other for other in shared if other != key]}, so the value "
                "read there belongs to the whole line, not to this row"
            )
        elif option.describes_member:
            # The collection's own reader owns these; it reports the whole
            # ordered list as one row below. A second row here would show the
            # LAST statement's value as if it were the answer -- and did.
            continue
        elif not _assignable(option):
            note = _unassignable_reason(option)
        else:
            verdict = _implausible(option, value)
            note = verdict.refused or verdict.note
            if not verdict.refused:
                applied = option.recipe_field_path
                assert applied is not None  # guaranteed by _assignable
                _assign(tree, applied, value)
        mapped.append(
            MappedValue(
                key=key,
                value=value,
                owner=option.owner,
                source=labels[site.target],
                site=site,
                origin="literal",
                applied_to=applied,
                note=note,
            )
        )

    applied_keys = {value.key for value in mapped if value.applied_to}
    for key in sorted(solved):
        if not key.startswith(_VAR):
            continue
        option = cat.by_template_var(key[len(_VAR) :])
        if option is None or option.key in applied_keys or not _assignable(option):
            continue
        site = solved_site[key]
        try:
            typed = _coerce_solved(option, solved[key])
        except ValueError as exc:
            mapped.append(
                MappedValue(
                    key=option.key,
                    value=solved[key],
                    owner=option.owner,
                    source=labels[site.target],
                    site=site,
                    origin="variable",
                    note=str(exc),
                )
            )
            continue
        verdict = _implausible(option, typed)
        path = option.recipe_field_path
        assert path is not None  # guaranteed by _assignable
        if not verdict.refused:
            _assign(tree, path, typed)
            applied_keys.add(option.key)
        mapped.append(
            MappedValue(
                key=option.key,
                value=typed,
                owner=option.owner,
                source=labels[site.target],
                site=site,
                origin="variable",
                applied_to=None if verdict.refused else path,
                note=verdict.refused or verdict.note,
            )
        )

    # The extract statements, read as an ordered list rather than row by
    # row. ``extract_selection`` / ``extract_type`` are describes_member rows
    # and so are deliberately not assignable one at a time -- there is no
    # single value to assign, and picking the first statement would drop the
    # rest without saying so. Exactly one row is reported here, whatever
    # happens, because "the reader found nothing" is the one outcome the
    # report used to be unable to show: the rules fell back to the catalog
    # default, the difference was pinned as a hunk, and the hunk then won over
    # anything the user later edited in the form.
    extract_targets = [
        target
        for target in (RenderTarget.QUANTUS_EXT, RenderTarget.QUANTUS_DSPF)
        if target in texts
    ]
    for target in extract_targets:
        rules = extract_rules_from_text(texts[target])
        if not rules:
            continue
        site = ReadSite(target=target, section="extract", option="-selection")
        shown = "; ".join(" ".join(str(v) for v in rule.values()) for rule in rules)
        refusals, advisories = _extract_verdicts(rules, catalog=cat)
        if refusals:
            warnings.append(
                f"{labels[target]}: the extract statements were left at the "
                f"catalog default and kept as a manual edit instead ({'; '.join(refusals)})"
            )
        else:
            _assign(tree, "extraction.extract", rules)
        applied_keys.update({"extract_selection", "extract_type"})
        order_note = (
            f"{len(rules)} extract statements, kept in file order -- the last "
            "one wins for any net it covers"
            if len(rules) > 1
            else ""
        )
        mapped.append(
            MappedValue(
                key="extract_selection",
                value=shown,
                owner=Owner.RECIPE,
                source=labels[target],
                # A real ReadSite, not None: the report and the dialog both
                # ask a mapped value where it came from, and one that cannot
                # answer is one the user cannot check. No line number -- the
                # rules come from every extract statement in the file, not
                # from one of them.
                site=site,
                origin="literal",
                applied_to=None if refusals else "extraction.extract",
                note="; ".join(
                    part for part in (*refusals, *advisories, order_note) if part
                ),
            )
        )
        break
    else:
        for target in extract_targets:
            mapped.append(
                MappedValue(
                    key="extract_selection",
                    value=None,
                    owner=Owner.RECIPE,
                    source=labels[target],
                    site=ReadSite(target=target, section="extract", option="-selection"),
                    origin="literal",
                    note=(
                        "no `extract` statement could be read out of this file, "
                        "so the recipe keeps the catalog's default extract list; "
                        "any difference is kept as a manual edit, which then "
                        "overrides the form"
                    ),
                )
            )
            break

    for key, (target, where) in _PRESENCE_ROWS.items():
        if target not in texts:
            continue
        option = cat.option(key)
        path = option.recipe_field_path
        if not _assignable(option) or path is None:
            continue
        present = where in parse_by_syntax(cat.target(target).syntax, texts[target])
        _assign(tree, path, present)
        applied_keys.add(key)
        mapped.append(
            MappedValue(
                key=key,
                value=present,
                owner=option.owner,
                source=labels[target],
                site=ReadSite(target=target, section=where[0], option=where[1]),
                origin="literal",
                applied_to=path,
                note="recovered from whether the line is written at all",
            )
        )

    tree["stages"] = _stages_for(order, cat)
    if Stage.JIVARO in tree["stages"]:
        # A stage in the list and a stage that runs are two different facts,
        # and the runner reads the second one: ``recipe.reduction.enabled`` is
        # what gates the jivaro stage. Importing the XML and leaving it False
        # declares a stage the same recipe has also disabled. The migration
        # path has always set it; this one had not.
        tree.setdefault("reduction", {})["enabled"] = True
    emit = _emit_for(order)
    if emit is not None:
        tree.setdefault("output", {})["emit"] = emit
    # Kept apart from ``res``, which is what the baseline renders with: see
    # RecipeImportResult.resources.
    stated_resources, resource_landings = _derive_resources(res, readback, catalog=cat)
    if resource_landings:
        mapped = _with_resources(mapped, resource_landings)
    base_recipe, degraded = _validate_degrading(tree, defaults)
    if degraded:
        mapped = _with_degraded(mapped, degraded)
        warnings.extend(
            f"{path} was read as a value the Recipe model refuses ({why}); the "
            "catalog default is used instead and your own line is kept as a "
            "manual edit"
            for path, why in sorted(degraded.items())
        )

    # 5. baseline objects, then the render ------------------------------------
    referenced = frozenset(
        name for source in template_source.values() for name in referenced_jinja_vars(source)
    )
    derived = profile is None
    corner_literal = _corner_literal(solved, readback)
    landed_in_profile: dict[str, ProfileLanding] = {}
    if profile is None:
        profile, notes, landed_in_profile = _derive_profile(
            solved,
            profile_id=slugify(f"{recipe_id}-baseline", max_len=64),
            variant_name=base_recipe.lvs.deck_variant,
            readback=readback,
            referenced=referenced,
            corner_literal=corner_literal,
            targets=order,
            catalog=cat,
        )
        warnings.extend(notes)

    # The corner is the seam between the two objects: the profile binds the
    # tool literal, the recipe names it. Both halves or neither -- a recipe
    # that leaves the corner unset renders the profile's default, and the
    # difference from the user's file would be stored as a hunk holding the
    # literal, which pins it and makes the field dead.
    if corner_literal is not None:
        spec = _corner_for_literal(profile, corner_literal)
        if spec is not None:
            _assign(tree, "extraction.corner", spec.name)
            base_recipe = _validate(tree)
            if not derived:
                mapped = _with_landing(
                    mapped,
                    key="technology_corner",
                    value=corner_literal,
                    destination="recipe",
                    field="extraction.corner",
                    owner=cat.option("technology_corner").owner,
                    site=_landing_site(
                        solved_site, readback, "technology_corner", catalog=cat
                    ),
                    labels=labels,
                )
        else:
            # A profile the caller supplied is theirs, not ours: adding a
            # corner to it behind their back would edit a PDK fact on the
            # strength of one file. Say what is missing and what it costs.
            refused = (
                f"these files extract at corner {corner_literal!r}, and pdk "
                f"profile {profile.profile_id!r} defines no corner with that "
                f"-technology_corner (it has "
                f"{[corner.technology_corner for corner in profile.corners] or '(none)'}). "
                "Add it to the profile and import again, or the difference is "
                "kept as a manual edit that pins the literal into every render."
            )
            warnings.append(refused)
            mapped = [
                replace(row, note=refused)
                if row.key == "technology_corner" and not row.applied_to
                else row
                for row in mapped
            ]

    for key, landing in landed_in_profile.items():
        mapped = _with_landing(
            mapped,
            key=key,
            value=landing.value,
            destination="profile",
            field=landing.field,
            owner=cat.option(key).owner,
            site=_landing_site(solved_site, readback, key, catalog=cat),
            labels=labels,
        )

    if dut is None:
        dut, notes = _derive_dut(solved, referenced)
        warnings.extend(notes)
    run, notes = _derive_run(solved, referenced)
    warnings.extend(notes)
    env, notes = _infer_env(
        solved,
        resolved_env or {},
        discover_required_vars(template_source.values()),
        profile=profile,
        catalog=cat,
    )
    warnings.extend(notes)

    try:
        context = build_context(
            dut=dut,
            recipe=base_recipe,
            profile=profile,
            run=run,
            resolved_env=env,
            resources=res,
            site=SiteFacts(),
            catalog=cat,
        )
    except RenderError as exc:
        raise RecipeImportError(
            f"the baseline for {recipe_id!r} cannot be built: {exc}"
        ) from exc

    plans = {plan.target: plan for plan in plan_targets(base_recipe, catalog=cat)}
    patches: list[TemplatePatch] = []
    hunks: list[PatchedHunk] = []
    unmodelled_by_target: dict[RenderTarget, float] = {}
    unmodelled_lines = 0
    total_lines = 0
    for target in order:
        try:
            base = _baseline(
                plans[target],
                context=context,
                recipe=base_recipe,
                profile=profile,
                resources=res,
                resolved_env=env,
                catalog=cat,
                templates_root=templates_root,
            )
        except RenderError as exc:
            raise RecipeImportError(
                f"the baseline for {target.value} cannot be rendered: {exc}"
            ) from exc
        patch = capture_patch(
            template_source=base.substituted,
            template_sha256=sha256_text(base.source),
            stage=plans[target].stage,
            template_id=plans[target].spec.template_id,
            profile_id=profile.profile_id,
            catalog_version=cat.catalog_version,
            base_real=base.real,
            base_masked=base.masked,
            edited_real=texts[target],
            values=base.values,
        )
        # Denominator is the longer of the two texts, so a hunk that replaces
        # 40 baseline lines with 60 of the user's cannot report more than 100%.
        lines = max(len(texts[target].splitlines()), len(base.real.splitlines()))
        touched = sum(
            max(len(hunk.before.splitlines()), len(hunk.after.splitlines()))
            for hunk in patch.hunks
        )
        unmodelled_by_target[target] = touched / lines if lines else 0.0
        unmodelled_lines += touched
        total_lines += lines
        if patch.hunks:
            patches.append(patch)
            hunks.extend(_hunk_summaries(patch, target))

    tree["patches"] = patches
    recipe = _validate(tree)

    ratio = unmodelled_lines / total_lines if total_lines else 0.0
    if ratio > warn_ratio:
        warnings.append(
            f"{ratio:.0%} of the imported lines had to be kept as manual edits "
            f"(the warning threshold is {warn_ratio:.0%}). These files most "
            "likely come from a different tool version than the shipped "
            "templates describe; a patch that large is a fork, and reviewing "
            "the hunks is worth more than the import."
        )

    result = RecipeImportResult(
        recipe=recipe,
        profile=profile,
        dut=dut,
        run=run,
        resolved_env=env,
        sources=tuple(imported),
        mapped=tuple(mapped),
        as_patch=tuple(hunks),
        unread={
            key: reason
            for key, reason in readback.unread.items()
            if any(site.target in texts for site in cat.option(key).lands_in)
        },
        roundtrip={},
        warnings=tuple(warnings),
        unmodelled_ratio=ratio,
        unmodelled_by_target=unmodelled_by_target,
        derived_profile=derived,
        catalog_version=cat.catalog_version,
        warn_ratio=warn_ratio,
        resources=stated_resources,
    )

    # 6. round trip -------------------------------------------------------------
    try:
        final = result.rerender(catalog=cat, templates_root=templates_root, resources=res)
    except AutoExtError as exc:
        raise RecipeImportError(
            f"the imported recipe {recipe_id!r} does not render back: {exc}"
        ) from exc
    roundtrip = {
        target: _roundtrip(target, texts[target], final.get(target, "")) for target in order
    }
    differing = [target.value for target, trip in roundtrip.items() if not trip.identical]
    if differing:
        warnings.append(
            f"re-rendering the imported recipe does not reproduce "
            f"{', '.join(differing)} byte for byte; RecipeImportResult.roundtrip "
            "carries the diff"
        )
    return replace(result, roundtrip=roundtrip, warnings=tuple(warnings))


def write_imported_recipe(
    result: RecipeImportResult, directory: Path | str, *, overwrite: bool = False
) -> Path:
    """Save the imported recipe. The step the dry run exists to precede."""

    path = Path(directory) / recipe_filename(result.recipe)
    if path.exists() and not overwrite:
        raise RecipeImportError(f"{path} already exists; pass overwrite=True to replace it")
    save_recipe(result.recipe, path)
    logger.info("wrote imported recipe %s", path)
    return path
