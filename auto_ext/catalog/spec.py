"""The built-in parameter catalog: schema, loader, self-validation, queries.

``options.yaml`` next to this module is the data; everything here is the
contract that keeps the data honest. It replaces the per-template
``*.manifest.yaml`` knob system with one table that covers **every** value a
generated EDA input file contains -- not only the seven that happened to be
promoted to knobs -- and records, for each of them, who owns it, where it
lands, and exactly how it is spelled on the page.

Three ideas do most of the work:

**Owner is not section.** ``owner`` answers "which object holds this value"
(recipe / profile / cells / run / resources / fixed) and nothing else. Which
part of which file it is written into is a property of the *landing site*
(``target`` + ``section``). The two draft catalogs used one ``group`` column
for both, with the result that ``netlist`` meant "DSPF output options" in one
file and "si.env netlisting" in the other, and the two tables could not be
merged at all.

**Observation is not confidence.** ``observed`` says the row was transcribed
out of a file in this repo, and ``source_ref`` says which line -- that is a
fact, always certain, because copying is not judging. ``choices_confidence``
says how sure we are of the legal *value set*, which for most Quantus options
is pure guesswork. A row can be certainly hardcoded and completely unknown at
the same time; ``extract_type`` is exactly that, and one column cannot say so.

**Rendering is data.** The patch escape hatch (``docs/refactor/02-patch.md``)
diffs a user's manual edit against the *generated* text. A catalog that cannot
reproduce a file's quoting, line breaking and indentation byte for byte would
repaint the whole file on every catalog upgrade and drown the user's real
edits in noise. So each landing site carries its own rendering rule, and each
target carries the defaults those rules fall back to. This is why
``-array_vias_spacing auto`` (bare) and ``-max_via_array_size "auto"``
(quoted) are recorded as different rather than tidied into agreement.

What this module does NOT do: render anything. It answers questions about
options; the renderer is a separate concern and lands with the C2 work.

Verification claims. Nothing in ``options.yaml`` has been *run* against a real
Cadence installation -- there is none on the development machine. Every row
that still depends on one is marked in place with a ``question:`` and mirrored
in ``docs/refactor/OFFICE_TODO.md``. Rows carrying no question have been read
out of the vendor manual by the red-zone probe (``scripts/extdoc_probe.py``),
which is evidence about the tool but not about this PDK.

``range_verified`` is false on every row but one: the 0-100 fF bound on
``coupling_cap_threshold_absolute`` is transcribed from the manual, and it is
the only bound in this file that is not a guard rail somebody invented. The
same row's ``unit`` used to say farads, on the strength of a hand-written
manifest, which made its own default look physically impossible; the unit was
the wrong half.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.errors import AutoExtError
from auto_ext.model.common import Frozen, RenderTarget, Stage

__all__ = [
    "BUILTIN_CATALOG_PATH",
    "Catalog",
    "CatalogError",
    "ChoicesSource",
    "Confidence",
    "Currently",
    "LandingSite",
    "Layout",
    "OptionSpec",
    "OptionType",
    "Owner",
    "Quoting",
    "RenderRule",
    "RenderTargetSpec",
    "TemplateVarAudit",
    "audit_template_vars",
    "builtin_catalog",
    "choices_for",
    "default_templates_root",
    "load_catalog",
]

#: The catalog data file. Resolved from ``__file__`` and never from the
#: current directory: the test suite runs with cwd inside the repository and a
#: cwd-relative lookup would silently pass by finding the repo's own copy.
BUILTIN_CATALOG_PATH = Path(__file__).resolve().parent / "options.yaml"

#: ``<repo>/templates``. Same reasoning: absolute, derived from ``__file__``.
_REPO_ROOT = Path(__file__).resolve().parents[2]


class CatalogError(AutoExtError):
    """``options.yaml`` is missing, unparsable, or internally inconsistent."""


# ---- enums -----------------------------------------------------------------


class Owner(StrEnum):
    """Which object holds the value. Object identity only, never file layout."""

    #: A shared, portable extraction recipe. Must contain no PDK literal, no
    #: cell name and no machine property, or it stops being portable.
    RECIPE = "recipe"
    #: A PdkProfile: process facts (deck paths, corner literals, supply name
    #: tables, parasitic device names).
    PROFILE = "profile"
    #: A row of the cell table: the DUT identity.
    CELLS = "cells"
    #: Derived per run: workspace paths, result artifacts, invocation choices.
    RUN = "run"
    #: Machine- and site-local: core counts, license waits, employee id.
    #: Separate from RECIPE on purpose -- carrying a core count inside a
    #: Recipe means moving from an 8-core box to a 64-core box edits the
    #: recipe, which defeats portability (DECISIONS.md #21).
    RESOURCES = "resources"
    #: Not a setting: structure, plumbing, provenance, dead panel remnants.
    #: Recorded so nobody has to rediscover why the line is there.
    FIXED = "fixed"


class OptionType(StrEnum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    #: One value out of a closed set. See ``choices_confidence`` before
    #: building a combo box out of it.
    ENUM = "enum"
    #: Several values; ``choices``, when present, constrains the members.
    LIST = "list"
    PATH = "path"
    #: Structure rather than a value: a comment block, an empty Tcl table, a
    #: composite line, an extension point.
    STRUCTURAL = "structural"


class Confidence(StrEnum):
    """How sure we are of the legal value SET (never of the current value)."""

    CERTAIN = "certain"
    LIKELY = "likely"
    #: Invented. Never render this as a closed dropdown (DECISIONS.md #19):
    #: free text plus the default, with the choices shown as hints.
    GUESS = "guess"


class ChoicesSource(StrEnum):
    """A PDK-profile table that supplies this option's value set at run time.

    ``choices`` in the catalog is one PDK's answer, frozen at authoring time.
    For an option whose legal set is a *process fact* -- which corners this
    technology defines, which LVS deck variants were released -- that frozen
    list is right for exactly one PDK and quietly wrong for the next. A row
    that names a source here keeps its static ``choices`` as the fallback for
    when no profile is loaded, and the form fills the control from the loaded
    profile instead. :func:`choices_for` is the one place that resolution
    happens.

    The member value is the dotted path from a ``PdkProfile``; every table it
    names is a list of objects carrying a semantic ``name``, which is what the
    Recipe stores.
    """

    #: ``PdkProfile.corners`` -> the semantic names ``recipe.extraction.corner``
    #: chooses between. The tool literal each maps to is the separate
    #: profile-owned ``technology_corner`` row.
    PROFILE_CORNERS = "profile.corners"
    #: ``PdkProfile.lvs_decks.variants`` -> the names
    #: ``recipe.lvs.deck_variant`` chooses between.
    PROFILE_LVS_VARIANTS = "profile.lvs_decks.variants"


class Currently(StrEnum):
    """How the value reaches the generated file today."""

    #: ``[[var]]`` plus a knob declaration in a ``*.manifest.yaml``. Exactly
    #: seven of these exist: five in each quantus manifest (the same five,
    #: declared twice) plus lvs_variant and connect_by_name in the calibre one.
    MANIFEST_KNOB = "manifest_knob"
    #: ``[[var]]`` fed straight from ``runner._build_context``.
    JINJA_VAR = "jinja_var"
    #: Typed into the ``.j2``. Unreachable without editing the template.
    HARDCODED_LITERAL = "hardcoded_literal"
    #: Assembled in ``auto_ext/tools/*.py`` (the strmout stage has no template).
    PYTHON_ARGV = "python_argv"
    #: A ProjectConfig / TaskSpec field that never reaches a template as a
    #: ``[[var]]``.
    CONFIG_FIELD = "config_field"
    #: The value never reaches a file; it *selects* a PdkProfile entry, and
    #: what gets written is that entry's literal through a different catalog
    #: row. ``extraction_corner`` picks a ``PdkProfile.corners`` name and
    #: ``technology_corner`` writes the literal it maps to. Distinct from
    #: ``absent``, which means nothing is emitted on this value's account at
    #: all, and from ``config_field``, which is plumbing rather than a choice.
    PROFILE_SELECTOR = "profile_selector"
    #: Not emitted at all. Either a proposal (``observed: false``) or a line a
    #: real export carries that we drop (``observed: true``).
    ABSENT = "absent"


class Quoting(StrEnum):
    """How the value is spelled at one landing site."""

    BARE = "bare"
    DOUBLE = "double"
    #: SKILL boolean: ``'t`` / ``'nil``.
    SKILL_BOOL = "skill_bool"
    #: SKILL list: ``'("auCdl" "schematic")``.
    SKILL_LIST = "skill_list"
    XML_ATTR = "xml_attr"
    #: Tcl braces, passed through verbatim.
    TCL_BRACE = "tcl_brace"
    #: The row has no value (comment blocks, section markers).
    NONE = "none"


class Layout(StrEnum):
    """How the line is broken at one landing site."""

    #: ``key: value`` / ``key = value`` on a line of its own.
    OWN_LINE = "own_line"
    #: ``-option value`` on one indented continuation line.
    INLINE = "inline"
    #: ``-option`` on one line, its value on the next (Quantus does this for
    #: -cdl_out_map_directory, -technology_corner and -temperature).
    VALUE_ON_NEXT_LINE = "value_on_next_line"
    #: ``-option`` then one value per line (Quantus -output_xy).
    VALUE_PER_LINE = "value_per_line"
    #: Several tags share one physical line (the Jivaro XML).
    PACKED = "packed"


# ---- rendering --------------------------------------------------------------


class RenderRule(Frozen):
    """A fully resolved rendering rule for one landing site.

    Produced by :meth:`LandingSite.render`; every field is concrete, with the
    target's defaults already filled in.
    """

    quoting: Quoting
    layout: Layout
    indent: int = Field(ge=0)
    #: Emits a trailing ``" \\"`` (Quantus command files).
    continuation: bool
    #: The line is conditional. Such a line MUST be written in the hugging
    #: form -- ``[% if x %]LINE`` on one line and ``[% endif %]NEXT`` opening
    #: the next -- because ``trim_blocks`` is off in this project's Jinja
    #: environment and an ordinary block leaves a blank line behind.
    #: ``templates/calibre/calibre_lvs.qci.j2:31-32`` is the reference.
    optional: bool


class RenderTargetSpec(Frozen):
    """One rendered file: which template makes it, and its rendering defaults."""

    id: RenderTarget
    stage: Stage
    #: Repository-relative POSIX path of the template.
    template: str
    #: ``<dir>/<file>`` form used by ``TemplatePatch.template_id``.
    template_id: str = Field(pattern=r"^[a-z0-9_]+/[A-Za-z0-9_.-]+$")
    syntax: str
    quoting: Quoting
    layout: Layout
    indent: int = Field(ge=0)
    continuation: bool
    #: Emission order is fixed (by the ``line`` numbers of the landing sites)
    #: rather than logical. Reordering diffs against files users already hold.
    preserve_order: bool = True
    #: Key of the option holding this file's header block, when it has one.
    header_option: str | None = None
    notes: str | None = None

    @property
    def template_path(self) -> Path:
        """Absolute path of the template inside this checkout."""

        return _REPO_ROOT / self.template


class LandingSite(Frozen):
    """One place a value is written.

    Rendering fields are ``None`` when the site simply follows its target's
    default; :meth:`render` resolves them. ``target`` is ``None`` for the
    strmout stage, which has no rendered file at all -- its argv is built in
    ``auto_ext/tools/strmout.py``, which is precisely why a catalog derived
    from ``.j2`` files alone missed the entire stage.
    """

    target: RenderTarget | None = None
    stage: Stage | None = None
    #: Section within the file: a Quantus command name, a ``*lvs`` / ``*cmn``
    #: group, an XML comment block, ``argv``.
    section: str
    #: The literal directive as it appears: ``-ground_net``, ``*lvsRunDir``,
    #: ``simRunDir``, ``inputView``, ``-topCell``.
    option: str
    #: 1-based line in the current template. ``None`` when there is no
    #: template line to point at.
    line: int | None = Field(default=None, ge=1)
    quoting: Quoting | None = None
    layout: Layout | None = None
    indent: int | None = Field(default=None, ge=0)
    continuation: bool | None = None
    optional: bool = False
    note: str | None = None

    def render(self, target_spec: RenderTargetSpec | None) -> RenderRule:
        """Resolve this site's rendering rule against its target's defaults."""

        if target_spec is None:
            # Target-less sites (strmout argv) have no file layout to inherit.
            return RenderRule(
                quoting=self.quoting or Quoting.NONE,
                layout=self.layout or Layout.OWN_LINE,
                indent=self.indent or 0,
                continuation=bool(self.continuation),
                optional=self.optional,
            )
        return RenderRule(
            quoting=self.quoting if self.quoting is not None else target_spec.quoting,
            layout=self.layout if self.layout is not None else target_spec.layout,
            indent=self.indent if self.indent is not None else target_spec.indent,
            continuation=(
                self.continuation
                if self.continuation is not None
                else target_spec.continuation
            ),
            optional=self.optional,
        )


# ---- presentation -----------------------------------------------------------
# These three exist for the Recipes form and for nothing else. They are here
# rather than in the UI because the alternative is a hand-written table of
# eighty-five exceptions inside a widget, which is precisely the system this
# catalog replaced. See docs/refactor/RECIPES_FORM.md.


class Tier(StrEnum):
    """How much of the form a row belongs to. Artboard ``M`` section 3.

    The form shows ~85 options and the shipped recipe leaves 83 of them at
    the catalog default, so a single flat list spends almost all of its
    height on values nobody touches. Two densities fix that -- but only
    because nothing is ever made *unreachable*: ``ALL`` rows are one always-
    visible toggle away, they are searchable from either mode, and a row
    whose value differs from its default is promoted into ``COMMON``
    whatever its tier says.
    """

    #: Shown in both modes. The settings a person changes from job to job.
    COMMON = "common"
    #: Shown in All view, and in Common view when its value is non-default.
    FULL = "full"
    #: Owned by another screen, but worth *naming* here: drawn as a
    #: read-only pointer row that shows the catalog default and navigates to
    #: the screen that owns it. Not editable, and it binds to no Recipe
    #: field. See :class:`Screen`.
    #:
    #: The office report behind this was "I cannot find where to set the
    #: extracted view name". The ownership was right -- it is per cell -- and
    #: the discoverability was zero, because a person editing a recipe has no
    #: reason to guess that one of the settings they are looking for lives on
    #: a different page. A row that says "not here, and here is where"
    #: costs two lines of form and answers the question on the spot.
    ELSEWHERE = "elsewhere"
    #: Not a setting at all: an identity axis of the DUT (which library,
    #: which cell, which views). Never drawn, on either screen -- search
    #: still finds it, so a person who types "cell" learns where identity
    #: lives without a row of the form being spent on something nobody
    #: "sets". Distinct from :attr:`ELSEWHERE`, which is a real setting that
    #: simply belongs to another page.
    IDENTITY = "identity"


class Screen(StrEnum):
    """Which screen owns the value. Artboard ``M`` section 1.

    ``CELLS`` rows are per-DUT: ``out_file`` names the extracted view one
    cell produces, so it is a column on the cell table and cannot be a recipe
    field. The office report that produced this column was "I cannot find
    where to rename the Quantus output view" -- the ownership was right and
    the discoverability was zero, so search answers for these rows too.
    """

    RECIPES = "recipes"
    CELLS = "cells"
    #: The workspace's own settings -- one per project rather than per cell
    #: or per recipe. ``dspf_out_pattern`` is the case that forced this
    #: member: a DSPF is a file on disk, so it needs a whole path, and that
    #: path is a project convention. Asking "where does the DSPF go" while
    #: editing a recipe is the same question ``out_file`` raised for the
    #: Cells page, and it gets the same answer -- a pointer row.
    PROJECT = "project"


class SectionDisplay(Frozen):
    """One raw ``lands_in.section`` and how the form draws it. Artboard ``L``.

    Authored once per section -- twenty-three rows for the whole catalog --
    and never per option. Adding an option touches this table never; adding a
    template *section* touches it once. A section with no entry here is not an
    error: it renders under its own raw name at :data:`UNMAPPED_ORDER`, so a
    new tool section shows up ugly, visible, and fixable in one line.

    Three operations, and the level-2 headings need all three:

    * **rename** is :attr:`label` -- ``device_check`` reads "device checks".
    * **merge** is a shared :attr:`group`: sections in one group render as a
      single heading, taking the label of the lowest-ordered member.
      ``device_preserve`` and ``netlist_control`` are one heading.
    * **split** is :attr:`split_by`, and ``output_db`` is its only user: one
      section becomes one heading per emitted output format, because the
      vendor documents four *different* option sets under that name.
    """

    #: The tool, not the file: ``si`` / ``quantus`` / ``calibre`` / ``jivaro``,
    #: which is the directory component of a target's ``template_id``.
    #: Keying on the tool is what makes this table twenty-three rows instead
    #: of thirty-one -- ``quantus.ext.cmd`` and ``quantus.dspf.cmd`` share
    #: eight section names, with the same meaning in both, and giving them
    #: separate entries would be two places to change one heading. ``None``
    #: marks the synthetic bucket for rows with no landing site at all.
    tool: str | None = None
    section: str = Field(min_length=1)
    label: str = Field(min_length=1)
    #: Sections sharing a group render as one heading.
    group: str = Field(min_length=1)
    order: int = Field(ge=0)
    #: Name of the OptionSpec field to split this section by. Only
    #: ``requires_emit`` is understood, and only ``output_db`` uses it.
    split_by: str | None = None


#: Order given to a section with no :class:`SectionDisplay` entry. High enough
#: to sort last, finite so it still renders.
UNMAPPED_ORDER = 999


# ---- the option -------------------------------------------------------------

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CONTEXT_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class OptionSpec(Frozen):
    """One parameter. See ``options.yaml``'s header for the column semantics."""

    key: str = Field(pattern=_KEY_RE.pattern, max_length=64)
    #: The Jinja variable this value is (or will be) bound to in the flat
    #: legacy namespace. Unique across the catalog. For ``jinja_var`` and
    #: ``manifest_knob`` rows it is verifiable against the template today and
    #: :func:`audit_template_vars` does verify it; for the rest it reserves
    #: one agreed name so the importer and the patch baseline agree. Catalog
    #: key and template var are not always equal (``min_res_ohm`` ->
    #: ``min_res``), and recording that mapping is why this column exists.
    template_var: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=64)
    #: Namespaced key in the new render context (01-schema.md section 4), e.g.
    #: ``recipe.extraction.min_res_ohm`` / ``pdk.lvs_dir`` / ``cell``.
    context_path: str | None = None
    owner: Owner
    type: OptionType
    default: Any = None
    choices: list[Any] | None = None
    #: Members of :attr:`choices` the tool accepts and this form does NOT
    #: draw. Not a correction of the value set -- ``choices`` stays the
    #: vendor's complete list, because readback, import and every error
    #: message still have to be able to name a member we do not offer.
    #:
    #: The distinction this column exists to make: a member missing from
    #: ``choices`` is a claim about the *tool* ("no such value"), and a member
    #: listed here is a claim about *us* ("the tool has it, we do not offer
    #: it, and here is why"). Before this column the two were the same edit.
    #:
    #: Nine of ``extract_type``'s fifteen members are here. The owner ruled on
    #: 2026-09-04 that a knob they do not understand is a knob they will never
    #: use, and that a form must not offer one whose deck the templates cannot
    #: complete -- the ``rlc*``/``rlck*`` members need an inductor component
    #: contract no template emits, and the ``*_to_substrate`` members need a
    #: ``substrate_nets_file`` that is unreachable, so choosing one today buys
    #: a run that succeeds and extracts nothing of what was asked for.
    choices_not_offered: list[Any] = Field(default_factory=list)
    #: Why the :attr:`choices_not_offered` members are not drawn. Required
    #: when that list is non-empty and refused when it is empty: an exemption
    #: with no reason is exactly the "we decided not to" / "we forgot" pair
    #: this catalog exists to keep apart.
    not_offered_reason: str | None = None
    #: A PdkProfile table that supersedes ``choices`` once a profile is
    #: loaded. ``choices`` stays as the no-profile fallback and as the record
    #: of what one real PDK answered. See :class:`ChoicesSource`.
    choices_from: ChoicesSource | None = None
    choices_confidence: Confidence
    #: The model field accepts ``None``, and ``None`` means "resolve it from
    #: somewhere else" -- the profile for a corner, the DUT for the view to
    #: reduce. A form renders it as an explicit "(from the profile)" entry
    #: rather than as an empty control, because the fallback is a choice and
    #: not a blank the user forgot to fill in.
    nullable: bool = False
    #: Grey text for an empty control, when ``default`` is ``None`` and so
    #: cannot supply one. Says what the tool will do with the field left
    #: alone; never a fake value.
    placeholder: str | None = None
    range: tuple[float, float] | None = None
    #: False on every row in the shipped catalog. A range is a guard rail
    #: somebody invented until a datasheet says otherwise.
    range_verified: bool = False
    unit: str | None = None
    currently: Currently
    #: True = transcribed from a file in this repo, with ``source_ref`` naming
    #: it. Always certain when true.
    observed: bool
    source_ref: str | None = None
    lands_in: list[LandingSite] = Field(default_factory=list)
    #: Why this row exists and why it has this owner. Required: a catalog row
    #: without a reason is how the last one drifted.
    why: str = Field(min_length=1)
    notes: str | None = None
    #: An unanswered question this row depends on (mirrored in OFFICE_TODO.md).
    question: str | None = None

    # -- presentation ---------------------------------------------------
    #: Which density this row belongs to. Artboard ``M`` names it required on
    #: every row; it defaults to :attr:`Tier.FULL` here instead, because
    #: ``full`` is the answer that hides nothing -- an unclassified row shows
    #: in All view, is searchable, and is promoted into Common the moment its
    #: value leaves the default. A required column would be safer only if the
    #: unsafe default were the silent one, and it is the other way round.
    tier: Tier = Tier.FULL
    #: Which screen owns the value. Rows that are not ``recipes`` are drawn
    #: disabled with a pointer, and found by search.
    screen: Screen = Screen.RECIPES
    #: Output formats this row applies to, by ``output.emit`` member. Empty
    #: means every format. A recipe that does not emit a listed format draws
    #: the row disabled, never hidden: the option exists, the tool accepts
    #: it, and this recipe simply does not reach it. Hiding it would make the
    #: form lie about what the tool can do, which is the misunderstanding the
    #: catalog exists to end.
    requires_emit: list[str] = Field(default_factory=list)
    #: "I have no landing site of my own -- draw me where THAT row lands."
    #:
    #: The form groups by tool, read off each row's landing site, and a row
    #: with none falls into the synthetic ``Flow`` bucket. Flow is the right
    #: home for decisions *about* the run -- which stages, reduction on or
    #: off, the two policy flags -- and the wrong home for a setting that
    #: does reach the tool but arrives through something else's literal.
    #:
    #: ``extraction_corner`` is the case that forced the column: what reaches
    #: Quantus is the profile-owned ``technology_corner``, so the corner row
    #: has no site of its own and landed under Flow, while a person looking
    #: for it looks under Quantus beside the temperature. Naming the sibling
    #: is better than a hand-written exception in the screen because it is
    #: data, so the next such row costs one line and no code.
    #:
    #: Resolution is ONE level deep and the catalog self-check enforces it:
    #: the named key must exist and must itself have a landing site, so this
    #: can neither chain nor cycle.
    groups_with: str | None = None
    #: ``member -> what operand it takes`` for an enum whose members are not
    #: all self-contained. Empty means every member stands alone.
    #:
    #: ``extract -selection`` is the case: ``all`` takes nothing, ``net``
    #: takes a pattern, and the two file forms take a path. A plain enum
    #: cannot say that, so the form drew a combo whose last three entries
    #: produced a command line the tool rejects -- three quarters of the
    #: option unusable with nothing on screen to explain why.
    #:
    #: The sub-form reads this to decide whether a rule needs a value field
    #: beside its combo, and what to label it.
    choice_args: dict[str, str] = Field(default_factory=dict)
    #: This row describes ONE FIELD OF ONE MEMBER of the collection at
    #: :attr:`context_path`, not a scalar the form can bind a control to.
    #:
    #: ``extract_selection`` and ``extract_type`` are the two: the value they
    #: describe lives in ``recipe.extraction.extract[i]``, and how many ``i``
    #: there are is the user's choice. So there is no single value to read or
    #: write, no flat template alias, and no place on the form for an
    #: ordinary row -- the repeating sub-form owns them and reads their
    #: ``choices`` / ``choice_args`` by key.
    #:
    #: They keep a real ``context_path`` because that is where the value
    #: genuinely lives, and the alternative (``null``) would have made them
    #: look like the structural rows that bind to nothing at all.
    describes_member: bool = False

    @model_validator(mode="after")
    def _check(self) -> OptionSpec:
        if self.choices is not None:
            if self.type not in (OptionType.ENUM, OptionType.LIST):
                raise ValueError(
                    f"{self.key}: choices is only meaningful for enum or list "
                    f"options (got type={self.type})"
                )
            if not self.choices:
                raise ValueError(f"{self.key}: choices must not be empty")
            if len(set(map(str, self.choices))) != len(self.choices):
                raise ValueError(f"{self.key}: choices contains duplicates")
        if self.type is OptionType.ENUM and self.choices is None:
            raise ValueError(f"{self.key}: an enum option must list its choices")
        self._check_not_offered()
        if self.choices_from is not None and self.type is not OptionType.ENUM:
            raise ValueError(
                f"{self.key}: choices_from is only meaningful for an enum "
                f"option (got type={self.type})"
            )

        if self.range is not None:
            if self.type not in (OptionType.INT, OptionType.FLOAT):
                raise ValueError(
                    f"{self.key}: range is only meaningful for int or float "
                    f"options (got type={self.type})"
                )
            low, high = self.range
            if low > high:
                raise ValueError(f"{self.key}: range low {low} > high {high}")
        elif self.range_verified:
            raise ValueError(
                f"{self.key}: range_verified is set but there is no range to verify"
            )

        self._check_default()

        if self.observed != (self.source_ref is not None):
            raise ValueError(
                f"{self.key}: observed={self.observed} but source_ref="
                f"{self.source_ref!r}; an observed row must say which file and "
                f"line it was read from, and only an observed row may."
            )
        if self.context_path is not None and not _CONTEXT_PATH_RE.match(self.context_path):
            raise ValueError(f"{self.key}: malformed context_path {self.context_path!r}")
        if self.describes_member and self.context_path is None:
            raise ValueError(
                f"{self.key}: describes_member needs a context_path naming the "
                f"collection the member belongs to"
            )
        if (
            self.owner is Owner.RECIPE
            and self.currently is not Currently.ABSENT
            and (self.context_path is None or not self.context_path.startswith("recipe."))
        ):
            raise ValueError(
                f"{self.key}: a live recipe-owned option must bind to a "
                f"recipe.* context path (got {self.context_path!r})"
            )
        return self

    def _check_not_offered(self) -> None:
        """The self-check behind :attr:`choices_not_offered`.

        Three properties, and each of them is a way the column could quietly
        become useless: a member nobody has to justify is a rubber stamp, a
        member that is not in ``choices`` is a value-set edit wearing this
        column's clothes, and a set that swallows every member (or the row's
        own default) leaves a control with nothing to offer.
        """

        if not self.choices_not_offered:
            if self.not_offered_reason is not None:
                raise ValueError(
                    f"{self.key}: not_offered_reason is set but no member is "
                    f"listed in choices_not_offered"
                )
            return
        if not (self.not_offered_reason or "").strip():
            raise ValueError(
                f"{self.key}: choices_not_offered needs a not_offered_reason. "
                f"A member the tool accepts and this form hides is a decision, "
                f"and a decision nobody wrote down is indistinguishable from an "
                f"omission."
            )
        if self.choices is None:
            raise ValueError(
                f"{self.key}: choices_not_offered is only meaningful for a row "
                f"that lists its choices"
            )
        known = {str(choice) for choice in self.choices}
        unknown = sorted({str(m) for m in self.choices_not_offered} - known)
        if unknown:
            raise ValueError(
                f"{self.key}: choices_not_offered names {unknown}, which are not "
                f"in choices. Not offering a value the tool does not have is a "
                f"correction to choices, not an exemption."
            )
        if len({str(m) for m in self.choices_not_offered}) != len(self.choices_not_offered):
            raise ValueError(f"{self.key}: choices_not_offered contains duplicates")
        if not self.offered_choices:
            raise ValueError(
                f"{self.key}: choices_not_offered hides every member, leaving "
                f"the control nothing to offer"
            )
        if self.default is not None and str(self.default) not in {
            str(c) for c in self.offered_choices
        }:
            raise ValueError(
                f"{self.key}: default {self.default!r} is not offered, so a new "
                f"recipe would start on a value the form refuses to draw"
            )

    def _check_default(self) -> None:
        if self.default is None:
            return
        if self.type is OptionType.ENUM:
            if self.choices is not None and self.default not in self.choices:
                raise ValueError(
                    f"{self.key}: default {self.default!r} is not in {self.choices!r}"
                )
        elif self.type is OptionType.LIST:
            if not isinstance(self.default, list):
                raise ValueError(f"{self.key}: a list option needs a list default")
            if self.choices is not None:
                extra = [v for v in self.default if v not in self.choices]
                if extra:
                    raise ValueError(
                        f"{self.key}: default members {extra!r} are not in choices"
                    )
        elif self.type is OptionType.BOOL:
            if not isinstance(self.default, bool):
                raise ValueError(f"{self.key}: a bool option needs a bool default")
        elif self.type in (OptionType.INT, OptionType.FLOAT):
            if isinstance(self.default, bool) or not isinstance(self.default, (int, float)):
                raise ValueError(f"{self.key}: {self.type} option needs a numeric default")
            if self.range is not None and not (self.range[0] <= self.default <= self.range[1]):
                raise ValueError(
                    f"{self.key}: default {self.default} is outside "
                    f"[{self.range[0]}, {self.range[1]}]"
                )

    @property
    def offered_choices(self) -> list[Any]:
        """The members a control may draw: ``choices`` minus the exempted ones.

        Every form that builds a combo out of a row reads this rather than
        :attr:`choices`. ``choices`` stays the vendor's whole set so that
        readback can still recognise a deck, the importer can still name what
        it read, and a model error can still quote the value it refused --
        three readers that must not lose a member just because we stopped
        offering it.
        """

        if self.choices is None:
            return []
        hidden = {str(member) for member in self.choices_not_offered}
        return [choice for choice in self.choices if str(choice) not in hidden]

    @property
    def free_input(self) -> bool:
        """True when the GUI must offer free text instead of a closed list.

        DECISIONS.md #19: a guessed value set becomes a dropdown half full of
        invalid entries, which is worse than a text box with a good default.
        """

        return self.choices is None or self.choices_confidence is Confidence.GUESS

    @property
    def is_knob_today(self) -> bool:
        """One of the seven values a user can actually change right now."""

        return self.currently is Currently.MANIFEST_KNOB

    @property
    def expected_in_templates(self) -> bool:
        """True when ``template_var`` must literally appear in the templates.

        A :attr:`describes_member` row is excluded. Its value does reach the
        file, but through a loop over the collection rather than through a
        flat alias, so there is no ``[[extract_type]]`` to find -- the
        template says ``[[rule.type]]`` inside
        ``[% for rule in recipe.extraction.extract %]``. Requiring the alias
        would force us to keep a scalar binding beside the list, which is the
        two-sources-of-truth this whole change removes.
        """

        if self.describes_member:
            return False
        return self.currently in (Currently.JINJA_VAR, Currently.MANIFEST_KNOB)

    @property
    def sections(self) -> tuple[str, ...]:
        """Template sections this option is written into, in landing order."""

        seen: list[str] = []
        for site in self.lands_in:
            if site.section not in seen:
                seen.append(site.section)
        return tuple(seen)

    @property
    def targets(self) -> tuple[RenderTarget, ...]:
        seen: list[RenderTarget] = []
        for site in self.lands_in:
            if site.target is not None and site.target not in seen:
                seen.append(site.target)
        return tuple(seen)

    @property
    def stages(self) -> tuple[Stage, ...]:
        seen: list[Stage] = []
        for site in self.lands_in:
            if site.stage is not None and site.stage not in seen:
                seen.append(site.stage)
        return tuple(seen)

    @property
    def recipe_field_path(self) -> str | None:
        """``extraction.min_res_ohm`` for a recipe-owned option, else ``None``.

        ``None`` for a :attr:`describes_member` row: the path names a
        collection, and binding a single control to it would write a scalar
        over a list. Those rows reach the user through the sub-form that owns
        the collection, not through the flat form.
        """

        if self.owner is not Owner.RECIPE or self.context_path is None:
            return None
        if self.describes_member:
            return None
        if not self.context_path.startswith("recipe."):
            return None
        return self.context_path[len("recipe.") :]


def choices_for(spec: OptionSpec, profile: Any | None = None) -> list[str]:
    """The value set to offer for ``spec``, given the loaded PDK profile.

    Without a ``choices_from`` row, or without a profile, this is the
    catalog's own :attr:`~OptionSpec.offered_choices`. With both, it is the
    semantic ``name`` of every entry in the profile table the row names --
    which corners *this* PDK defines, which LVS deck variants *this* release
    shipped.

    ``offered_choices`` rather than ``choices``: this function answers "what
    may a control draw", which is not the same question as "what does the
    tool accept". A member the owner has ruled out is still a legal value the
    importer must be able to name, and it must still never appear in a combo.

    Duck-typed on purpose: a ``PdkProfile`` import here would make the catalog
    depend on the model it is supposed to describe. A profile whose table is
    missing or empty falls back to ``choices`` rather than offering nothing,
    because an empty drop-down is indistinguishable from a broken one.
    """

    fallback = [str(choice) for choice in spec.offered_choices]
    if spec.choices_from is None or profile is None:
        return fallback
    node: Any = profile
    for part in spec.choices_from.value.split(".")[1:]:
        node = getattr(node, part, None)
        if node is None:
            return fallback
    try:
        names = [getattr(entry, "name", None) for entry in node]
    except TypeError:  # pragma: no cover - a table that is not iterable
        return fallback
    found = [str(name) for name in names if name]
    return found or fallback


# ---- the catalog ------------------------------------------------------------


class Catalog(Frozen):
    """The whole table, validated for internal consistency at load time."""

    schema_version: int
    #: Bumped whenever the data changes. ``TemplatePatch.base.catalog_version``
    #: records it so a conflict report can name the catalog that moved.
    catalog_version: str
    targets: list[RenderTargetSpec]
    options: list[OptionSpec]
    #: How the form titles each template section. See :class:`SectionDisplay`.
    #: Optional: an empty table renders every section under its raw name.
    sections: list[SectionDisplay] = Field(default_factory=list)

    def tool_of(self, target: RenderTarget) -> str:
        """``quantus.ext.cmd`` -> ``quantus``. Level 1 of the form's grouping.

        Read off ``template_id``'s directory rather than off the target id,
        because the id spells the *file* (``lvs.qci``) while the directory
        spells the *tool* (``calibre``), and the form groups by tool.
        """

        spec = next((t for t in self.targets if t.id == target), None)
        if spec is None:  # pragma: no cover - the model validates this
            return str(target.value).split(".")[0]
        return spec.template_id.split("/")[0]

    def section_display(self, tool: str | None, section: str) -> SectionDisplay:
        """The display rule for one section, invented if the table lacks it.

        Never raises. A missing entry is a presentation gap, not a broken
        catalog, and a form that refuses to draw is a worse answer than one
        that draws an ugly heading somebody can fix in one line.
        """

        for entry in self.sections:
            if entry.tool == tool and entry.section == section:
                return entry
        return SectionDisplay(
            tool=tool,
            section=section,
            label=section.replace("_", " "),
            group=section,
            order=UNMAPPED_ORDER,
        )

    @model_validator(mode="after")
    def _check(self) -> Catalog:
        target_ids = [t.id for t in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError(f"duplicate render targets: {target_ids}")

        keys = [o.key for o in self.options]
        dup_keys = sorted({k for k in keys if keys.count(k) > 1})
        if dup_keys:
            raise ValueError(f"duplicate option keys: {dup_keys}")

        tvars = [o.template_var for o in self.options]
        dup_tvars = sorted({v for v in tvars if tvars.count(v) > 1})
        if dup_tvars:
            raise ValueError(
                f"duplicate template_var: {dup_tvars}. One value, one name -- two "
                f"rows sharing a variable would give the renderer two answers."
            )

        by_id = {t.id: t for t in self.targets}
        for opt in self.options:
            for site in opt.lands_in:
                if site.target is None:
                    if site.stage is None:
                        raise ValueError(
                            f"{opt.key}: a landing site with no render target must "
                            f"name its stage (the strmout stage has no template)"
                        )
                elif site.target not in by_id:
                    raise ValueError(f"{opt.key}: unknown render target {site.target}")

        by_key = {o.key: o for o in self.options}
        for opt in self.options:
            if not opt.groups_with:
                continue
            if opt.lands_in:
                raise ValueError(
                    f"{opt.key}: groups_with is for rows with NO landing site of "
                    f"their own; this row has {len(opt.lands_in)}. Remove one or "
                    f"the other -- a row cannot be drawn in two places."
                )
            target = by_key.get(opt.groups_with)
            if target is None:
                raise ValueError(
                    f"{opt.key}: groups_with names {opt.groups_with!r}, which is "
                    f"not an option key"
                )
            if not any(site.target is not None for site in target.lands_in):
                raise ValueError(
                    f"{opt.key}: groups_with names {opt.groups_with!r}, which has "
                    f"no landing site itself, so there is nowhere to be drawn. "
                    f"Point at a row that reaches a file."
                )

        for target in self.targets:
            if target.header_option and target.header_option not in set(keys):
                raise ValueError(
                    f"{target.id}: header_option {target.header_option!r} is not an option key"
                )
        return self

    # -- lookup ---------------------------------------------------------

    def target(self, target: RenderTarget) -> RenderTargetSpec:
        for spec in self.targets:
            if spec.id == target:
                return spec
        raise KeyError(target)

    def option(self, key: str) -> OptionSpec:
        for opt in self.options:
            if opt.key == key:
                return opt
        raise KeyError(key)

    def by_template_var(self, template_var: str) -> OptionSpec | None:
        for opt in self.options:
            if opt.template_var == template_var:
                return opt
        return None

    def by_owner(self, owner: Owner) -> list[OptionSpec]:
        return [o for o in self.options if o.owner is owner]

    def by_stage(self, stage: Stage) -> list[OptionSpec]:
        return [o for o in self.options if stage in o.stages]

    def by_target(self, target: RenderTarget) -> list[OptionSpec]:
        return [o for o in self.options if target in o.targets]

    def by_section(self, target: RenderTarget, section: str) -> list[OptionSpec]:
        return [
            o
            for o in self.options
            if any(s.target == target and s.section == section for s in o.lands_in)
        ]

    def sections_of(self, target: RenderTarget) -> tuple[str, ...]:
        """Sections of one file, in the order they first appear in it."""

        ordered: list[tuple[int, str]] = []
        seen: set[str] = set()
        for opt in self.options:
            for site in opt.lands_in:
                if site.target != target or site.section in seen:
                    continue
                seen.add(site.section)
                ordered.append((site.line if site.line is not None else 10**6, site.section))
        return tuple(section for _, section in sorted(ordered))

    def emission_order(self, target: RenderTarget) -> list[tuple[int, OptionSpec, LandingSite]]:
        """Every landing site in one file, sorted by line.

        The renderer must follow this rather than any logical grouping:
        si.env's ``checkCAPPERI`` belongs with the capacitor group and is
        exported after the diode group, and moving it diffs against every real
        si.env our users hold.
        """

        rows = [
            (site.line, opt, site)
            for opt in self.options
            for site in opt.lands_in
            if site.target == target and site.line is not None
        ]
        return sorted(rows, key=lambda r: (r[0], r[1].key))

    # -- audit helpers --------------------------------------------------

    def knobs_today(self) -> list[OptionSpec]:
        """The seven values a user can change without editing a template."""

        return [o for o in self.options if o.is_knob_today]

    def unverified_ranges(self) -> list[OptionSpec]:
        return [o for o in self.options if o.range is not None and not o.range_verified]

    def open_questions(self) -> list[OptionSpec]:
        return [o for o in self.options if o.question]

    def free_input_options(self) -> list[OptionSpec]:
        return [o for o in self.options if o.free_input]

    def owner_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {owner.value: 0 for owner in Owner}
        for opt in self.options:
            counts[opt.owner.value] += 1
        return counts


# ---- loading ----------------------------------------------------------------


def default_templates_root() -> Path:
    """``<repo>/templates``, derived from ``__file__``.

    Never cwd-relative. ``resolve_template_path`` starts with a cwd-relative
    ``is_file()`` probe, and a test whose cwd is still inside the repository
    can hit the repository's own templates and pass for the wrong reason.
    """

    return _REPO_ROOT / "templates"


def _read_yaml(path: Path) -> Any:
    if not path.is_file():
        raise CatalogError(f"catalog file not found: {path}")
    yaml = YAML(typ="rt")
    try:
        with path.open("r", encoding="utf-8") as fh:
            return yaml.load(fh)
    except YAMLError as exc:
        raise CatalogError(f"{path}: YAML parse error: {exc}") from exc


def _plain(obj: Any) -> Any:
    """ruamel CommentedMap/CommentedSeq tree -> plain dicts and lists."""

    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def load_catalog(path: Path | None = None) -> Catalog:
    """Load and fully validate a catalog file.

    Raises :class:`CatalogError` for anything wrong with the file: parse
    errors, schema violations, duplicate keys or template vars, a default
    outside its own range, an unknown render target.
    """

    src = Path(path) if path is not None else BUILTIN_CATALOG_PATH
    data = _read_yaml(src)
    if not isinstance(data, dict):
        raise CatalogError(f"{src}: expected a mapping at top level")

    payload = _plain(data)
    # A site's stage is implied by its target; filling it here keeps the data
    # file free of a column that could disagree with the target table.
    target_stage = {t["id"]: t["stage"] for t in payload.get("targets", [])}
    for opt in payload.get("options", []):
        for site in opt.get("lands_in", []) or []:
            if site.get("stage") is None and site.get("target") is not None:
                site["stage"] = target_stage.get(site["target"])

    try:
        return Catalog.model_validate(payload)
    except ValidationError as exc:
        raise CatalogError(f"{src}: {exc}") from exc


@lru_cache(maxsize=1)
def builtin_catalog() -> Catalog:
    """The catalog shipped next to this module. Parsed once per process."""

    return load_catalog(BUILTIN_CATALOG_PATH)


# ---- the self-check ---------------------------------------------------------


class TemplateVarAudit(Frozen):
    """Result of cross-checking the catalog against the actual templates.

    Two failure directions, and both matter:

    * ``missing_in_template`` -- the catalog claims a value arrives as
      ``[[var]]`` in some file, and it does not. Stale row, or a renamed
      variable.
    * ``missing_in_catalog`` -- a template references a ``[[var]]`` that no
      catalog row claims for that file. This is the one that catches "added a
      template variable, forgot the catalog entry", which is exactly how the
      manifest system rotted.
    * ``no_landing_site`` -- a row claims its value arrives as ``[[var]]`` and
      then names no file at all. Neither direction above can see it: both walk
      ``opt.targets``, which is derived from ``lands_in``, so an empty
      ``lands_in`` makes the forwards loop empty and contributes nothing to
      the backwards one. It is the strongest claim the ``currently`` column
      can make paired with no evidence for it.
    """

    #: ``(option key, template_var, target)``
    missing_in_template: list[tuple[str, str, str]] = Field(default_factory=list)
    #: ``(target, template_var)``
    missing_in_catalog: list[tuple[str, str]] = Field(default_factory=list)
    #: ``(option key, template_var)``
    no_landing_site: list[tuple[str, str]] = Field(default_factory=list)
    #: Templates named by the catalog that could not be read at all.
    unreadable_templates: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.missing_in_template
            or self.missing_in_catalog
            or self.no_landing_site
            or self.unreadable_templates
        )

    def describe(self) -> str:
        """One human-readable block, for a test failure message."""

        if self.ok:
            return "catalog and templates agree"
        lines: list[str] = []
        for key, var, target in self.missing_in_template:
            lines.append(
                f"  catalog row {key!r} binds [[{var}]] in {target}, but the "
                f"template does not reference it"
            )
        for target, var in self.missing_in_catalog:
            lines.append(
                f"  {target} references [[{var}]] but no catalog row claims that "
                f"variable for this file -- add a row (or fix template_var)"
            )
        for key, var in self.no_landing_site:
            lines.append(
                f"  catalog row {key!r} says its value reaches a template as "
                f"[[{var}]] but names no landing site, so it reaches nothing. "
                f"Add the lands_in entry, or set currently to config_field if "
                f"the value only ever travels through Python"
            )
        for tpl in self.unreadable_templates:
            lines.append(f"  template named by the catalog is unreadable: {tpl}")
        return "\n".join(lines)


#: Render-context namespaces a template may walk into directly. Kept here
#: rather than imported from ``core.render`` because the catalog must stay
#: importable without Jinja -- ``tests/catalog/test_catalog.py`` asserts the
#: two lists agree, which is the same trick ``Stage`` uses against
#: ``patch_models``.
_CONTEXT_NAMESPACES: frozenset[str] = frozenset(
    {"env", "paths", "pdk", "recipe", "resources", "run", "site"}
)


def audit_template_vars(
    catalog: Catalog | None = None, *, templates_root: Path | None = None
) -> TemplateVarAudit:
    """Cross-check every catalog ``template_var`` against the real templates.

    Run as a test. Adding a ``[[var]]`` to a template without adding the
    matching catalog row fails immediately instead of surfacing months later
    as a mysteriously missing value in a rendered command file.

    Only rows whose ``currently`` says the value arrives through Jinja today
    (``jinja_var`` / ``manifest_knob``) are required to appear; a hardcoded
    literal has, by definition, no variable in the file yet.

    Three findings, not two. The third, ``no_landing_site``, exists because
    both of the others iterate ``opt.targets``: a row that names no file has
    an empty loop in one and is invisible to the other, so "this value reaches
    a template" went unchecked for precisely the rows with nothing to check it
    against.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    root = Path(templates_root) if templates_root is not None else default_templates_root()

    # Imported lazily: core.template pulls in Jinja and core.env, and the
    # catalog itself must stay importable without them.
    from auto_ext.core.template import scan_placeholders

    per_target: dict[RenderTarget, set[str]] = {}
    unreadable: list[str] = []
    for spec in cat.targets:
        path = root / Path(spec.template).relative_to("templates")
        try:
            per_target[spec.id] = set(scan_placeholders(path).jinja_variables)
        except Exception:  # noqa: BLE001 - any read/parse failure is one finding
            unreadable.append(spec.template)

    missing_in_template: list[tuple[str, str, str]] = []
    no_landing_site: list[tuple[str, str]] = []
    for opt in cat.options:
        if not opt.expected_in_templates:
            continue
        if not opt.targets:
            # Both loops below are over ``opt.targets``, so this row would be
            # skipped by the audit entirely -- the one shape that could claim
            # to reach a template and be believed without evidence.
            no_landing_site.append((opt.key, opt.template_var))
            continue
        for target in opt.targets:
            found = per_target.get(target)
            if found is None:
                continue
            if opt.template_var not in found:
                missing_in_template.append((opt.key, opt.template_var, target.value))

    claimed: dict[RenderTarget, set[str]] = {t.id: set() for t in cat.targets}
    for opt in cat.options:
        for target in opt.targets:
            claimed[target].add(opt.template_var)

    missing_in_catalog: list[tuple[str, str]] = []
    for target, found in per_target.items():
        for var in sorted(found - claimed.get(target, set())):
            if var in _CONTEXT_NAMESPACES:
                # A template reaching into a namespace (``recipe.extraction``)
                # rather than a flat alias. The namespace is the render
                # context's own shape, not a value any row could claim, so
                # demanding a row for it would demand a row for the model.
                continue
            missing_in_catalog.append((target.value, var))

    return TemplateVarAudit(
        missing_in_template=sorted(missing_in_template),
        missing_in_catalog=sorted(missing_in_catalog),
        no_landing_site=sorted(no_landing_site),
        unreadable_templates=sorted(unreadable),
    )
