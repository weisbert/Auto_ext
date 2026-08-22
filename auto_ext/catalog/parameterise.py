"""Development-time rewriter: turn a hardcoded template literal into a placeholder.

Not runtime code. Nothing in ``auto_ext`` imports this module at run time; it
exists so the one-off job of parameterising the shipped ``.j2`` files is done
*by the catalog* instead of by hand, and so every rewrite it declines to make
is reported rather than guessed.

The gap it closes
-----------------
84 of the catalog's 177 rows are owned by a Recipe or a PdkProfile and still
carry ``currently: hardcoded_literal``: the field exists, the GUI shows it, and
the value is typed into the template.
:func:`auto_ext.core.render.check_representable` refuses to render such a
setting rather than dropping it silently -- correct, but it leaves the user
looking at fields that cannot be changed. Closing the gap means two edits per
row, in this order:

1. the literal in the ``.j2`` becomes ``[[template_var]]`` (this module), and
2. the row's ``currently`` becomes ``jinja_var`` (a data edit in
   ``options.yaml``, deliberately *not* automated here -- flipping the column
   without the template edit re-introduces exactly the silent drop).

The one acceptance test
-----------------------
**Rendering the five targets with catalog defaults must be byte-identical
before and after.** ``tests/catalog/test_byte_fidelity.py`` is that test. It is
why this module verifies the literal it is about to replace instead of trusting
the catalog's line number: a placeholder inserted in the wrong place is far
worse than a row left hardcoded, and the only cheap proof that a placeholder
went in the right place is that the literal it replaced was sitting there.

How a rewrite is decided
------------------------
For one landing site, in order, and any step may refuse:

1. **Anchor.** ``LandingSite.line`` is a hint, never an instruction. The line
   must actually carry the site's ``option`` directive, matched by a pattern
   that knows the target's syntax (``simRunDir =`` / ``*lvsRunDir:`` /
   ``-ground_net`` / ``<inputView``). If it does not, the file is searched --
   inside the site's section for the Quantus command files, where ``-type``
   appears in four different commands -- and a hit is accepted only when it is
   unique. Zero hits or several hits is a refusal, never a best guess.

2. **Slot.** The span of the line holding the value, from the site's resolved
   ``layout``: the rest of the line after ``=`` or ``:``; the argument after a
   Quantus ``-option`` and before its ``\\`` continuation; the *next* line for
   ``value_on_next_line``; the ``value="..."`` attribute contents for the XML.

3. **Literal.** The slot must contain the value the catalog says is there,
   spelled the way the site's ``quoting`` spells it. Exact slot equality first;
   failing that, exactly one occurrence inside a longer slot (which is how
   ``"[[qrc_deck_dir]]/preserveCellList.txt"`` gets its filename replaced
   without touching the directory). Anything else is a refusal.

   Booleans are matched as a *pair* of spellings, because two shapes exist and
   they are not interchangeable: a value spelled out (``'t`` / ``'nil``, ``1``
   / ``0``, ``true`` / ``false``) versus a whole line whose *presence* is the
   truth value. The second is what ``optional: true`` on the landing site
   means, and it is emitted in the hugging form described below.

4. **Rewrite.** The literal becomes an expression that renders back to exactly
   the bytes it replaced when the value is the catalog default. Plain strings
   become ``[[var]]``; booleans become an inline conditional in the spelling
   observed at that site; lists become a ``join`` or, for Quantus
   ``-output_xy``, a loop.

``trim_blocks`` is off
----------------------
Every shape that needs a ``[% %]`` statement emits it in the hugging form --
the tag glued to the front of the line it governs, and the closing tag glued to
the front of the *following* line -- because Jinja keeps the newline after a
statement tag in this project's environment, so a tag on a line of its own
emits a blank line the real tool export does not have.
``templates/calibre/calibre_lvs.qci.j2:31-32`` is the reference shape, and this
module reproduces it rather than inventing a second one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from auto_ext.catalog.spec import (
    Catalog,
    Currently,
    Layout,
    OptionSpec,
    OptionType,
    Owner,
    Quoting,
    RenderRule,
    RenderTargetSpec,
    builtin_catalog,
)
from auto_ext.core.errors import AutoExtError
from auto_ext.model.common import RenderTarget

__all__ = [
    "LOOP_VAR",
    "PARAMETERISE_OWNERS",
    "Certainty",
    "ParameteriseError",
    "ParameteriseReport",
    "PendingAudit",
    "Reason",
    "Refusal",
    "Rewrite",
    "Shape",
    "audit_pending",
    "main",
    "parameterise",
    "pending_options_for",
]


class ParameteriseError(AutoExtError):
    """The rewriter was asked for something it cannot even attempt."""


#: Name bound by every ``[% for %]`` this module emits. Underscore-prefixed so
#: it cannot collide with a render-context key, and constant so a reviewer
#: reading two parameterised templates sees one spelling in both.
LOOP_VAR = "_item"


# ---- report vocabulary -------------------------------------------------------


class Shape(StrEnum):
    """What kind of rewrite was applied at one landing site."""

    #: The value sits alone in its slot and was swapped for ``[[var]]``.
    SCALAR = "scalar"
    #: Same, but the value lives on the line *after* the option
    #: (Quantus ``-technology_corner`` / ``-temperature``).
    SCALAR_NEXT_LINE = "scalar_next_line"
    #: The literal is one part of a longer slot -- a filename inside a path, a
    #: suffix after another placeholder -- and only that part was replaced.
    FRAGMENT = "fragment"
    #: A boolean spelled out as a token (``'t`` / ``1`` / ``true``), replaced
    #: by an inline conditional in the spelling this site uses.
    BOOL_LITERAL = "bool_literal"
    #: A boolean whose truth value is the *presence of the line*. Emitted in
    #: the hugging ``[% if %]LINE`` / ``[% endif %]NEXT`` form.
    BOOL_OPTIONAL_LINE = "bool_optional_line"
    #: A list flattened into one slot (``'("auCdl" "schematic")``, ``VDD VSS``).
    LIST_JOINED = "list_joined"
    #: A list written one value per line, replaced by a hugging ``[% for %]``.
    LIST_PER_LINE = "list_per_line"


class Certainty(StrEnum):
    """How much of a reviewer's attention one rewrite deserves.

    Deliberately not :class:`auto_ext.catalog.spec.Confidence`: that column is
    about how sure we are of a value *set*, this one is about how sure we are
    that this particular edit landed where it was meant to.
    """

    #: The catalog's line number was right and the whole slot was the literal.
    #: Nothing to look at beyond the byte-fidelity test.
    CERTAIN = "certain"
    #: Byte-preserving, but something about the site was irregular: the line
    #: had drifted, the literal was a fragment of a longer slot, the template
    #: spells the value non-canonically and the expression now reproduces that
    #: on purpose, or the rewrite emits a statement tag. Read the note.
    REVIEW = "review"


class Reason(StrEnum):
    """Why a landing site was left alone."""

    #: The site names a different file than the one being rewritten.
    NOT_THIS_TARGET = "not_this_target"
    #: No line in the file (or in the site's section) carries the directive.
    ANCHOR_NOT_FOUND = "anchor_not_found"
    #: Several lines carry it and the catalog's line number is not one of them.
    ANCHOR_AMBIGUOUS = "anchor_ambiguous"
    #: The slot does not hold the literal the catalog says it holds.
    LITERAL_NOT_FOUND = "literal_not_found"
    #: The literal occurs more than once inside the slot, so replacing "the"
    #: occurrence would be a coin toss.
    LITERAL_AMBIGUOUS = "literal_ambiguous"
    #: The slot already carries this row's placeholder. Idempotent no-op.
    ALREADY_PARAMETERISED = "already_parameterised"
    #: The row has no default, so there is no literal to look for.
    NO_DEFAULT = "no_default"
    #: A combination of quoting / layout / type this rewriter does not encode.
    UNSUPPORTED_SHAPE = "unsupported_shape"


@dataclass(frozen=True)
class Rewrite:
    """One literal that became a placeholder."""

    option_key: str
    template_var: str
    target: RenderTarget
    section: str
    option: str
    #: 1-based line in the *input* text that the rewrite anchored on.
    line: int
    #: The line the catalog claimed, when it differed from :attr:`line`.
    catalog_line: int | None
    shape: Shape
    certainty: Certainty
    #: The exact characters removed.
    literal: str
    #: The exact characters written in their place.
    placeholder: str
    #: Input lines touched, verbatim and newline-free.
    before: tuple[str, ...]
    #: The lines that replaced them.
    after: tuple[str, ...]
    note: str | None = None

    def describe(self) -> str:
        head = (
            f"  {self.option_key} -> [[{self.template_var}]]  "
            f"({self.target.value}:{self.line}, {self.shape.value}, "
            f"{self.certainty.value})"
        )
        body = [f"      - {line}" for line in self.before]
        body += [f"      + {line}" for line in self.after]
        if self.note:
            body.append(f"      note: {self.note}")
        return "\n".join([head, *body])


@dataclass(frozen=True)
class Refusal:
    """One landing site the rewriter deliberately did not touch."""

    option_key: str
    template_var: str
    target: RenderTarget
    section: str
    option: str
    catalog_line: int | None
    reason: Reason
    #: What was looked for and what was found instead. Written to be read by
    #: whoever has to finish the row by hand.
    detail: str

    def describe(self) -> str:
        where = (
            f"{self.target.value}:{self.catalog_line}"
            if self.catalog_line is not None
            else self.target.value
        )
        return f"  {self.option_key} ({where}) [{self.reason.value}] {self.detail}"


@dataclass(frozen=True)
class ParameteriseReport:
    """Everything one :func:`parameterise` call did and refused to do."""

    target: RenderTarget
    rewrites: tuple[Rewrite, ...] = ()
    refusals: tuple[Refusal, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.rewrites)

    @property
    def needs_review(self) -> tuple[Rewrite, ...]:
        return tuple(r for r in self.rewrites if r.certainty is Certainty.REVIEW)

    @property
    def blocking(self) -> tuple[Refusal, ...]:
        """Refusals that leave a row hardcoded and therefore need hand work.

        ``already_parameterised`` and ``not_this_target`` are excluded: neither
        leaves anything undone.
        """

        quiet = {Reason.ALREADY_PARAMETERISED, Reason.NOT_THIS_TARGET}
        return tuple(r for r in self.refusals if r.reason not in quiet)

    def describe(self) -> str:
        lines = [
            f"{self.target.value}: {len(self.rewrites)} rewritten "
            f"({len(self.needs_review)} to review), "
            f"{len(self.blocking)} left for hand work"
        ]
        lines.extend(rewrite.describe() for rewrite in self.rewrites)
        lines.extend(refusal.describe() for refusal in self.blocking)
        return "\n".join(lines)


# ---- the audit ---------------------------------------------------------------


#: Owners whose values a user can set and that therefore must reach the file
#: through a variable. ``cells`` / ``run`` are per-DUT and per-run and already
#: bind; ``fixed`` is not a setting; ``resources`` is the resource layer's own
#: job and out of scope for this round.
PARAMETERISE_OWNERS: frozenset[Owner] = frozenset({Owner.RECIPE, Owner.PROFILE})


@dataclass(frozen=True)
class PendingAudit:
    """The work list: every row that is owned but still frozen in a template.

    The round is over when :attr:`total` is zero. Grouping is by render target
    because that is how the work splits -- one template at a time -- and a row
    landing in both Quantus files appears under both.
    """

    by_target: dict[RenderTarget, tuple[OptionSpec, ...]] = field(default_factory=dict)
    #: Rows with no render target at all (the strmout stage). Not template work.
    targetless: tuple[OptionSpec, ...] = ()

    @property
    def options(self) -> tuple[OptionSpec, ...]:
        """Every pending row once, in catalog order."""

        seen: dict[str, OptionSpec] = {}
        for rows in self.by_target.values():
            for opt in rows:
                seen.setdefault(opt.key, opt)
        for opt in self.targetless:
            seen.setdefault(opt.key, opt)
        return tuple(seen.values())

    @property
    def total(self) -> int:
        """Distinct rows still to do. Zero is the finish line."""

        return len(self.options)

    @property
    def site_count(self) -> int:
        """Landing sites still to do. Larger than :attr:`total`, because a row
        can land in both Quantus files."""

        return sum(len(rows) for rows in self.by_target.values()) + len(self.targetless)

    def counts(self) -> dict[str, int]:
        return {target.value: len(rows) for target, rows in self.by_target.items()}

    def describe(self) -> str:
        lines = [
            f"{self.total} row(s) still hardcoded, {self.site_count} landing site(s)"
        ]
        for target, rows in self.by_target.items():
            lines.append(f"  {target.value}: {len(rows)}")
            for opt in rows:
                lines.append(f"    {opt.key} ({opt.owner.value}, {opt.type.value})")
        if self.targetless:
            lines.append(f"  (no render target): {len(self.targetless)}")
        return "\n".join(lines)


def audit_pending(catalog: Catalog | None = None) -> PendingAudit:
    """Rows a user owns that the shipped templates still hardcode, by target.

    This is both the work list for the parameterisation round and the test that
    says it is finished: when it returns nothing, no Recipe or PdkProfile field
    is refused by :func:`auto_ext.core.render.check_representable` any more.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    by_target: dict[RenderTarget, list[OptionSpec]] = {}
    targetless: list[OptionSpec] = []

    for opt in cat.options:
        if opt.owner not in PARAMETERISE_OWNERS:
            continue
        if opt.currently is not Currently.HARDCODED_LITERAL:
            continue
        hit = False
        for site in opt.lands_in:
            if site.target is None:
                continue
            by_target.setdefault(site.target, []).append(opt)
            hit = True
        if not hit:
            targetless.append(opt)

    ordered = {
        spec.id: tuple(_dedupe(by_target[spec.id]))
        for spec in cat.targets
        if by_target.get(spec.id)
    }
    return PendingAudit(by_target=ordered, targetless=tuple(targetless))


def pending_options_for(
    target: RenderTarget, catalog: Catalog | None = None
) -> tuple[OptionSpec, ...]:
    """The rows :func:`parameterise` should be handed for one template."""

    return audit_pending(catalog).by_target.get(target, ())


def _dedupe(rows: Iterable[OptionSpec]) -> list[OptionSpec]:
    seen: dict[str, OptionSpec] = {}
    for opt in rows:
        seen.setdefault(opt.key, opt)
    return list(seen.values())


# ---- literal spellings -------------------------------------------------------


def _scalar_text(value: Any) -> str:
    """The characters a plain value occupies, matching what Jinja would emit."""

    if isinstance(value, bool):  # bools never come here, but keep str() honest
        return "true" if value else "false"
    return str(value)


#: Ordered candidate ``(true, false)`` spellings per ``(quoting, syntax)``,
#: **without** the quotes a ``double`` site puts around them. Those quotes
#: belong to the line, exactly as they do for a plain string, so a quoted
#: boolean is rewritten to ``"[[ 'true' if x else 'false' ]]"`` rather than to
#: an expression that carries its own quote characters.
#:
#: The first pair is canonical; later pairs are spellings the shipped templates
#: actually use that are *not* canonical and must still be reproduced byte for
#: byte -- ``templates/si/default.env.j2:6`` writes a bare ``nil`` where every
#: sibling line writes ``'nil``. A non-canonical hit is reported as
#: :attr:`Certainty.REVIEW` with the mismatch spelled out.
_BOOL_PAIRS: dict[tuple[Quoting, str], tuple[tuple[str, str], ...]] = {
    (Quoting.SKILL_BOOL, "skill"): (("'t", "'nil"), ("t", "nil")),
    (Quoting.BARE, "calibre_runset"): (("1", "0"), ("true", "false")),
    (Quoting.BARE, "quantus_cmd"): (("true", "false"), ("1", "0")),
    (Quoting.DOUBLE, "quantus_cmd"): (("true", "false"), ("1", "0")),
    (Quoting.DOUBLE, "skill"): (("t", "nil"),),
    (Quoting.XML_ATTR, "xml"): (("true", "false"), ("1", "0")),
}


def _bool_pairs(rule: RenderRule, syntax: str) -> tuple[tuple[str, str], ...]:
    pairs = _BOOL_PAIRS.get((rule.quoting, syntax))
    if pairs is not None:
        return pairs
    # No table entry: fall back to the most widespread spelling, so an unlisted
    # (quoting, syntax) pair refuses on a literal the message can show rather
    # than on an internal lookup nobody can read.
    return (("true", "false"),)


def _literal_candidates(opt: OptionSpec, rule: RenderRule, syntax: str) -> list[str]:
    """Every spelling of ``opt.default`` this site could legitimately show."""

    value = opt.default
    if opt.type is OptionType.BOOL:
        tokens = [pair[0] if value else pair[1] for pair in _bool_pairs(rule, syntax)]
        if rule.quoting is Quoting.DOUBLE:
            return [f'"{token}"' for token in tokens]
        return tokens

    if rule.quoting is Quoting.SKILL_LIST:
        members = " ".join(f'"{_scalar_text(v)}"' for v in value)
        return [f"'({members})"]

    if opt.type is OptionType.LIST:
        if rule.quoting is Quoting.DOUBLE:
            return [" ".join(f'"{_scalar_text(v)}"' for v in value)]
        return [" ".join(_scalar_text(v) for v in value)]

    text = _scalar_text(value)
    if rule.quoting is Quoting.DOUBLE:
        return [f'"{text}"']
    # bare / xml_attr (the quotes belong to the attribute, not to the value) /
    # tcl_brace (passed through verbatim) all show the value as written.
    return [text]


def _fragment_candidates(
    opt: OptionSpec, rule: RenderRule, syntax: str, exact: Sequence[str]
) -> list[str]:
    """Spellings to look for *inside* a longer slot.

    A double-quoted slot that already holds a placeholder owns its own quotes:
    ``-parasitic_blocking_device_cells_file "[[qrc_deck_dir]]/preserveCellList.txt"``
    is one quoted string carrying two values, so the filename appears bare even
    though the site's quoting is ``double``. The unquoted spelling is therefore
    a legitimate fragment while never being a legitimate whole slot.
    """

    candidates = list(exact)
    if (
        rule.quoting is Quoting.DOUBLE
        and opt.type is not OptionType.BOOL
        and opt.type is not OptionType.LIST
    ):
        bare = _scalar_text(opt.default)
        if bare and bare not in candidates:
            candidates.append(bare)
    return candidates


def _quote(text: str, *, inside_double_quotes: bool) -> str:
    """Spell ``text`` as a Jinja string literal that reads well where it lands.

    Two constraints. A quote character inside the text picks the other quote as
    the delimiter -- ``'t`` and ``'nil`` carry a single quote and therefore need
    double quotes around them. Otherwise an expression sitting *inside* a
    double-quoted slot (a Quantus argument, an XML attribute) prefers single
    quotes, so the line stays readable to a person and to any tool that scans
    for quote pairs. Jinja is happy either way: ``[[ ]]`` wins over quoting.
    """

    if '"' in text and "'" in text:
        raise ParameteriseError(f"cannot spell {text!r} as a Jinja string literal")
    if '"' in text:
        return f"'{text}'"
    if "'" in text:
        return f'"{text}"'
    return f"'{text}'" if inside_double_quotes else f'"{text}"'


def _placeholder_for(
    opt: OptionSpec, rule: RenderRule, syntax: str, matched: str
) -> tuple[str, Shape, str | None]:
    """The expression that replaces ``matched`` and renders back to it.

    Returns ``(text, shape, note)``. ``note`` is non-empty when the spelling
    found at the site is not the canonical one, in which case the emitted
    expression reproduces the observed spelling for the default value and uses
    the canonical spelling for the other branch -- byte-identical today, and
    correct for the value the user is about to be able to choose.
    """

    var = opt.template_var

    if opt.type is OptionType.BOOL:
        quoted = rule.quoting is Quoting.DOUBLE
        # A double-quoted site's quotes belong to the line, so they stay
        # outside the expression and only the bare token goes inside it.
        token = matched[1:-1] if quoted and len(matched) >= 2 else matched
        canonical = _bool_pairs(rule, syntax)[0]
        true_form, false_form = canonical
        note: str | None = None
        if opt.default:
            if token != canonical[0]:
                true_form = token
                note = (
                    f"template spells true as {token!r} where the canonical "
                    f"spelling here is {canonical[0]!r}; the expression "
                    "reproduces the template so the render stays byte-identical"
                )
        elif token != canonical[1]:
            false_form = token
            note = (
                f"template spells false as {token!r} where the canonical "
                f"spelling here is {canonical[1]!r}; the expression reproduces "
                "the template so the render stays byte-identical"
            )
        inside = quoted or rule.quoting is Quoting.XML_ATTR
        expr = (
            f"[[ {_quote(true_form, inside_double_quotes=inside)} if {var} else "
            f"{_quote(false_form, inside_double_quotes=inside)} ]]"
        )
        return (f'"{expr}"' if quoted else expr), Shape.BOOL_LITERAL, note

    if rule.quoting is Quoting.SKILL_LIST:
        # The [% if %] here is inline, not a line of its own, so trim_blocks
        # does not enter into it. It exists so an empty list renders as '()
        # rather than '("").
        expr = f"""'([% if {var} %]"[[ {var} | join('" "') ]]"[% endif %])"""
        return expr, Shape.LIST_JOINED, None

    if opt.type is OptionType.LIST:
        return f"[[ {var} | join(' ') ]]", Shape.LIST_JOINED, None

    if rule.quoting is Quoting.DOUBLE:
        return f'"[[{var}]]"', Shape.SCALAR, None
    return f"[[{var}]]", Shape.SCALAR, None


# ---- anchoring ---------------------------------------------------------------


def _anchor_pattern(syntax: str, option: str) -> re.Pattern[str]:
    """A pattern matching the line, and the value span, that carries ``option``."""

    esc = re.escape(option)
    if syntax == "skill":
        return re.compile(rf"^(?P<head>\s*{esc}\s*=\s*)(?P<slot>.*?)(?P<tail>\s*)$")
    if syntax == "calibre_runset":
        return re.compile(rf"^(?P<head>\s*{esc}\s*:\s*)(?P<slot>.*?)(?P<tail>\s*)$")
    if syntax == "quantus_cmd":
        # The directive has to start a token: a request for ``-name_space``
        # must not match inside ``-net_name_space``.
        return re.compile(
            rf"(?P<head>(?:^|(?<=\s)){esc}(?![\w-])[ \t]*)"
            rf"(?P<slot>.*?)(?P<tail>(?:[ \t]*\\)?[ \t]*)$"
        )
    if syntax == "xml":
        return re.compile(
            rf'(?P<head><{esc}(?![\w-])[^>]*?value=")(?P<slot>[^"]*)(?P<tail>")'
        )
    raise ParameteriseError(f"no anchor pattern for template syntax {syntax!r}")


#: A Quantus command file section opens with the command name at column zero.
_QUANTUS_SECTION_RE = re.compile(r"^(?P<name>[a-z_][a-z0-9_]*)(?:\s|$)")


def _section_bounds(lines: Sequence[str], syntax: str, section: str) -> tuple[int, int]:
    """Half-open ``[start, end)`` 0-based line range to search.

    Only the Quantus command files have a structure worth using: a command name
    at column zero opens a section that runs to the next one. ``-type`` appears
    in four of them, so a file-wide search for it is meaningless while a
    section-scoped one is exact. Every other syntax searches the whole file --
    their directives are unique within a file by construction.
    """

    if syntax != "quantus_cmd":
        return (0, len(lines))
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        match = _QUANTUS_SECTION_RE.match(line)
        if match:
            starts.append((match.group("name"), i))
    for pos, (name, start) in enumerate(starts):
        if name != section:
            continue
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(lines)
        return (start, end)
    return (0, len(lines))


# ---- the rewriter ------------------------------------------------------------


@dataclass(frozen=True)
class _Site:
    """One landing site, flattened with everything the rewriter needs."""

    opt: OptionSpec
    section: str
    option: str
    catalog_line: int | None
    rule: RenderRule


def parameterise(
    template_text: str,
    target: RenderTarget,
    options: Sequence[OptionSpec] | None = None,
    *,
    catalog: Catalog | None = None,
) -> tuple[str, ParameteriseReport]:
    """Replace hardcoded literals in one template with catalog placeholders.

    ``template_text`` is the raw ``.j2`` source, LF-terminated. ``target``
    selects both the template's syntax and which landing sites apply.
    ``options`` defaults to :func:`pending_options_for`, i.e. every row this
    round is meant to close for that file; pass a narrower list to work one row
    at a time.

    Returns ``(new_text, report)``. ``new_text`` is ``template_text`` unchanged
    when nothing was rewritten. The call never raises for a site it cannot
    handle -- that is a :class:`Refusal` carrying the literal it looked for and
    what it found instead -- so a caller can take the mechanical part and read
    off exactly what is left.

    Sites are applied from the bottom of the file upwards, so a rewrite that
    changes the line count (a list written one value per line collapses eight
    lines into two) cannot invalidate the line numbers of the sites still to
    come.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    spec = cat.target(target)
    rows = list(options) if options is not None else list(pending_options_for(target, cat))

    sites: list[_Site] = []
    refusals: list[Refusal] = []
    for opt in rows:
        matched_any = False
        for site in opt.lands_in:
            if site.target != target:
                continue
            matched_any = True
            sites.append(
                _Site(
                    opt=opt,
                    section=site.section,
                    option=site.option,
                    catalog_line=site.line,
                    rule=site.render(spec),
                )
            )
        if not matched_any:
            refusals.append(
                Refusal(
                    option_key=opt.key,
                    template_var=opt.template_var,
                    target=target,
                    section="",
                    option="",
                    catalog_line=None,
                    reason=Reason.NOT_THIS_TARGET,
                    detail=(
                        f"row lands in {[t.value for t in opt.targets]}, "
                        f"not {target.value}"
                    ),
                )
            )

    lines = template_text.split("\n")

    # Bottom-up: see the docstring. Sites with no line number go last, because
    # they can only be found by search and a search is safest on a file that
    # has already been rewritten as far as it can be. Ties (two rows on one
    # line, as the Calibre post-trigger line has) keep catalog order.
    ordered = sorted(
        enumerate(sites),
        key=lambda pair: (-(pair[1].catalog_line or 0), pair[0]),
    )
    rewrites: list[Rewrite] = []
    for _, site in ordered:
        outcome = _apply_site(lines, site, spec)
        if isinstance(outcome, Rewrite):
            rewrites.append(outcome)
        else:
            refusals.append(outcome)

    rewrites.sort(key=lambda r: (r.line, r.option_key))
    report = ParameteriseReport(
        target=target, rewrites=tuple(rewrites), refusals=tuple(refusals)
    )
    return ("\n".join(lines) if report.changed else template_text, report)


def _refuse(site: _Site, target: RenderTarget, reason: Reason, detail: str) -> Refusal:
    return Refusal(
        option_key=site.opt.key,
        template_var=site.opt.template_var,
        target=target,
        section=site.section,
        option=site.option,
        catalog_line=site.catalog_line,
        reason=reason,
        detail=detail,
    )


def _apply_site(
    lines: list[str], site: _Site, spec: RenderTargetSpec
) -> Rewrite | Refusal:
    """Rewrite one landing site in ``lines`` (mutated in place), or refuse."""

    target = spec.id
    opt = site.opt

    if opt.type is OptionType.STRUCTURAL or site.rule.quoting is Quoting.NONE:
        return _refuse(
            site,
            target,
            Reason.UNSUPPORTED_SHAPE,
            f"type={opt.type.value} quoting={site.rule.quoting.value}: the row is "
            "structure rather than a value, so there is no literal to replace",
        )
    if opt.default is None and opt.type is not OptionType.BOOL:
        return _refuse(
            site,
            target,
            Reason.NO_DEFAULT,
            "the row has no default, so there is no literal to look for; record "
            "the value the template carries in options.yaml first",
        )

    pattern = _anchor_pattern(spec.syntax, site.option)
    anchor = _find_anchor(lines, site, spec, pattern)
    if isinstance(anchor, Refusal):
        return anchor
    index, drifted = anchor

    if site.rule.optional:
        return _rewrite_optional_line(lines, site, target, index, drifted)
    if site.rule.layout is Layout.VALUE_PER_LINE:
        return _rewrite_value_per_line(lines, site, spec, index, drifted, pattern)
    if site.rule.layout is Layout.VALUE_ON_NEXT_LINE:
        return _rewrite_next_line(lines, site, spec, index, drifted)
    return _rewrite_in_slot(lines, site, spec, index, drifted, pattern)


def _find_anchor(
    lines: Sequence[str],
    site: _Site,
    spec: RenderTargetSpec,
    pattern: re.Pattern[str],
) -> tuple[int, bool] | Refusal:
    """``(0-based line, drifted)`` for the line carrying the site's directive."""

    target = spec.id
    hinted: int | None = None
    if site.catalog_line is not None and 1 <= site.catalog_line <= len(lines):
        hinted = site.catalog_line - 1
        if pattern.search(lines[hinted]):
            return (hinted, False)

    start, end = _section_bounds(lines, spec.syntax, site.section)
    hits = [i for i in range(start, end) if pattern.search(lines[i])]
    if site.catalog_line is None:
        seen = "the catalog row records no line number"
    elif hinted is None:
        seen = f"catalog line {site.catalog_line} is past the end of the file"
    else:
        seen = f"catalog line {site.catalog_line} reads {lines[hinted]!r}"

    if not hits:
        return _refuse(
            site,
            target,
            Reason.ANCHOR_NOT_FOUND,
            f"no line in section {site.section!r} carries {site.option!r}; {seen}",
        )
    if len(hits) > 1:
        return _refuse(
            site,
            target,
            Reason.ANCHOR_AMBIGUOUS,
            f"{site.option!r} appears on lines {[i + 1 for i in hits]} of section "
            f"{site.section!r}; {seen}, so which one this row means is a guess",
        )
    return (hits[0], True)


def _drift_note(site: _Site, index: int, drifted: bool) -> str | None:
    if not drifted:
        return None
    return (
        f"catalog says line {site.catalog_line}, the directive is on line "
        f"{index + 1}; update lands_in"
    )


def _match_literal(
    slot: str, exact: Sequence[str], fragments: Sequence[str]
) -> tuple[str, bool] | None:
    """``(literal, whole_slot)`` for the first candidate the slot shows.

    Whole-slot equality wins over a fragment match across *all* candidates
    before any fragment is considered; otherwise a canonical spelling sitting
    inside a longer slot could beat an exact non-canonical one. A fragment is
    accepted only when it occurs exactly once, so "which occurrence did the
    catalog mean" is never a question this function answers by guessing.
    """

    for candidate in exact:
        if slot == candidate:
            return (candidate, True)
    for candidate in fragments:
        if slot.count(candidate) == 1:
            return (candidate, False)
    return None


def _already_done(slot: str, var: str) -> bool:
    return "[[" in slot and re.search(rf"\b{re.escape(var)}\b", slot) is not None


def _finish(
    site: _Site,
    target: RenderTarget,
    *,
    line: int,
    drifted: bool,
    shape: Shape,
    literal: str,
    placeholder: str,
    before: tuple[str, ...],
    after: tuple[str, ...],
    notes: Sequence[str],
) -> Rewrite:
    kept = [note for note in notes if note]
    return Rewrite(
        option_key=site.opt.key,
        template_var=site.opt.template_var,
        target=target,
        section=site.section,
        option=site.option,
        line=line,
        catalog_line=site.catalog_line if drifted else None,
        shape=shape,
        certainty=Certainty.REVIEW if kept else Certainty.CERTAIN,
        literal=literal,
        placeholder=placeholder,
        before=before,
        after=after,
        note="; ".join(kept) or None,
    )


def _fragment_note(opt: OptionSpec, slot: str, literal: str) -> str:
    """Why a fragment rewrite is never certain, stated so it can be acted on.

    A fragment shares its slot with something else -- almost always another
    row's placeholder. Whether the result is right depends on what
    ``context_path`` binds, and this module cannot know that: it rewrites text
    and never builds a render context. The one row that got this wrong in
    review is named here because it is the shape to look for, not because it is
    the only one that can have it.
    """

    return (
        f"the literal is one part of a longer slot ({slot!r}); only {literal!r} "
        f"was replaced. Check that [[{opt.template_var}]] binds *just* that "
        f"fragment: this row's context_path is {opt.context_path!r}, and a path "
        "that resolves to the composed value (a whole path where the fragment "
        "is only the file name) renders the prefix twice. "
        "tests/catalog/test_byte_fidelity.py is what proves which it is"
    )


def _rewrite_in_slot(
    lines: list[str],
    site: _Site,
    spec: RenderTargetSpec,
    index: int,
    drifted: bool,
    pattern: re.Pattern[str],
) -> Rewrite | Refusal:
    """The common case: the value sits in a slot on the directive's own line."""

    target = spec.id
    opt = site.opt
    line = lines[index]
    match = pattern.search(line)
    if match is None:  # pragma: no cover - _find_anchor only returns matches
        raise ParameteriseError(f"anchor for {opt.key} stopped matching line {index + 1}")
    slot = match.group("slot")

    if _already_done(slot, opt.template_var):
        return _refuse(
            site,
            target,
            Reason.ALREADY_PARAMETERISED,
            f"line {index + 1} already reads {slot!r}",
        )

    candidates = _literal_candidates(opt, site.rule, spec.syntax)
    fragments = _fragment_candidates(opt, site.rule, spec.syntax, candidates)
    found = _match_literal(slot, candidates, fragments)
    if found is None:
        extra = (
            " The slot already carries other placeholders, so the literal this "
            "row owns may have been folded into one of them."
            if "[[" in slot
            else ""
        )
        return _refuse(
            site,
            target,
            Reason.LITERAL_NOT_FOUND,
            f"line {index + 1} slot is {slot!r}; expected one of {fragments!r}.{extra}",
        )
    literal, whole = found
    if not whole and slot.count(literal) != 1:  # pragma: no cover - guarded above
        return _refuse(
            site,
            target,
            Reason.LITERAL_AMBIGUOUS,
            f"{literal!r} occurs {slot.count(literal)} times in {slot!r}",
        )

    placeholder, shape, spelling_note = _placeholder_for(
        opt, site.rule, spec.syntax, literal
    )
    notes = [_drift_note(site, index, drifted), spelling_note]
    if not whole:
        if shape is Shape.SCALAR:
            # A fragment of a longer slot: the quotes belong to the slot, not
            # to this value, so the placeholder must not bring its own.
            placeholder = f"[[{opt.template_var}]]"
            shape = Shape.FRAGMENT
        notes.append(_fragment_note(opt, slot, literal))

    new_slot = slot.replace(literal, placeholder, 1)
    new_line = line[: match.start("slot")] + new_slot + line[match.end("slot") :]
    lines[index] = new_line
    return _finish(
        site,
        target,
        line=index + 1,
        drifted=drifted,
        shape=shape,
        literal=literal,
        placeholder=placeholder,
        before=(line,),
        after=(new_line,),
        notes=notes,
    )


#: Any line, split into indent / value / optional Quantus continuation.
_BARE_VALUE_RE = re.compile(r"^(?P<head>[ \t]*)(?P<slot>.*?)(?P<tail>(?:[ \t]*\\)?[ \t]*)$")


def _rewrite_next_line(
    lines: list[str],
    site: _Site,
    spec: RenderTargetSpec,
    index: int,
    drifted: bool,
) -> Rewrite | Refusal:
    """``-technology_corner`` on one line, its value alone on the next."""

    target = spec.id
    opt = site.opt
    value_index = index + 1
    if value_index >= len(lines):
        return _refuse(
            site,
            target,
            Reason.UNSUPPORTED_SHAPE,
            f"layout is value_on_next_line but line {index + 1} is the last line",
        )
    value_line = lines[value_index]
    body = _BARE_VALUE_RE.match(value_line)
    if body is None:  # pragma: no cover - the pattern matches any line
        raise ParameteriseError(f"cannot split value line {value_index + 1}")
    slot = body.group("slot")

    if _already_done(slot, opt.template_var):
        return _refuse(
            site,
            target,
            Reason.ALREADY_PARAMETERISED,
            f"line {value_index + 1} already reads {slot!r}",
        )

    candidates = _literal_candidates(opt, site.rule, spec.syntax)
    fragments = _fragment_candidates(opt, site.rule, spec.syntax, candidates)
    found = _match_literal(slot, candidates, fragments)
    if found is None:
        return _refuse(
            site,
            target,
            Reason.LITERAL_NOT_FOUND,
            f"line {value_index + 1} (the value line under {site.option!r}) is "
            f"{slot!r}; expected one of {candidates!r}",
        )
    literal, whole = found
    placeholder, _shape, spelling_note = _placeholder_for(
        opt, site.rule, spec.syntax, literal
    )
    notes = [_drift_note(site, index, drifted), spelling_note]
    if not whole:
        placeholder = f"[[{opt.template_var}]]"
        notes.append(_fragment_note(opt, slot, literal))

    new_slot = slot.replace(literal, placeholder, 1)
    new_line = value_line[: body.start("slot")] + new_slot + value_line[body.end("slot") :]
    lines[value_index] = new_line
    return _finish(
        site,
        target,
        line=value_index + 1,
        drifted=drifted,
        shape=Shape.SCALAR_NEXT_LINE,
        literal=literal,
        placeholder=placeholder,
        before=(value_line,),
        after=(new_line,),
        notes=notes,
    )


def _rewrite_value_per_line(
    lines: list[str],
    site: _Site,
    spec: RenderTargetSpec,
    index: int,
    drifted: bool,
    pattern: re.Pattern[str],
) -> Rewrite | Refusal:
    """``-output_xy`` followed by one quoted value per line -> a hugging loop.

    The value lines collapse into one loop body, and ``[% endfor %]`` is glued
    to the front of the line that follows them. That gluing is the whole point:
    with ``trim_blocks`` off, an ``[% endfor %]`` on a line of its own emits the
    newline that follows it and the file grows a blank line the tool never
    wrote.
    """

    target = spec.id
    opt = site.opt
    values = list(opt.default or [])
    if not values:
        return _refuse(
            site,
            target,
            Reason.NO_DEFAULT,
            "layout is value_per_line but the row's default list is empty, so "
            "there is no block to recognise",
        )

    match = pattern.search(lines[index])
    if match is None:  # pragma: no cover - _find_anchor only returns matches
        raise ParameteriseError(f"anchor for {opt.key} stopped matching line {index + 1}")
    if match.group("slot").strip():
        return _refuse(
            site,
            target,
            Reason.UNSUPPORTED_SHAPE,
            f"layout is value_per_line but line {index + 1} also carries a value "
            f"({match.group('slot')!r})",
        )

    first = index + 1
    last = index + len(values)
    if last + 1 >= len(lines):
        return _refuse(
            site,
            target,
            Reason.UNSUPPORTED_SHAPE,
            "the value block runs to the end of the file, so there is no "
            "following line for [% endfor %] to hug",
        )

    quoted = site.rule.quoting is Quoting.DOUBLE
    shapes: list[tuple[str, str]] = []
    for offset, value in enumerate(values):
        line = lines[first + offset]
        want = f'"{_scalar_text(value)}"' if quoted else _scalar_text(value)
        body = _BARE_VALUE_RE.match(line)
        if body is None:  # pragma: no cover - the pattern matches any line
            raise ParameteriseError(f"cannot split value line {first + offset + 1}")
        if body.group("slot") != want:
            if line.lstrip().startswith("[%") or _already_done(line, opt.template_var):
                return _refuse(
                    site,
                    target,
                    Reason.ALREADY_PARAMETERISED,
                    f"line {first + offset + 1} already reads {line!r}",
                )
            return _refuse(
                site,
                target,
                Reason.LITERAL_NOT_FOUND,
                f"line {first + offset + 1} is {body.group('slot')!r}, expected "
                f"{want!r} (value {offset + 1} of {len(values)} under "
                f"{site.option!r})",
            )
        shapes.append((body.group("head"), body.group("tail")))

    heads = {head for head, _ in shapes}
    tails = {tail for _, tail in shapes}
    if len(heads) != 1 or len(tails) != 1:
        return _refuse(
            site,
            target,
            Reason.UNSUPPORTED_SHAPE,
            f"the {len(values)} value lines do not share one indent/continuation "
            f"shape (indents {sorted(heads)!r}, tails {sorted(tails)!r}), so one "
            "loop body cannot reproduce them",
        )

    head, tail = shapes[0]
    slot = f'"[[ {LOOP_VAR} ]]"' if quoted else f"[[ {LOOP_VAR} ]]"
    loop_line = f"[% for {LOOP_VAR} in {opt.template_var} %]{head}{slot}{tail}"
    before = tuple(lines[first : last + 2])
    endfor_line = "[% endfor %]" + lines[last + 1]

    # The following line is rewritten first: splicing the block away would move
    # it, and its index is only valid until then.
    lines[last + 1] = endfor_line
    lines[first : last + 1] = [loop_line]

    return _finish(
        site,
        target,
        line=first + 1,
        drifted=drifted,
        shape=Shape.LIST_PER_LINE,
        literal="\n".join(before[: len(values)]),
        placeholder=loop_line,
        before=before,
        after=(loop_line, endfor_line),
        notes=[
            _drift_note(site, index, drifted),
            "[% endfor %] is glued to the following line on purpose: trim_blocks "
            "is off, so a tag on a line of its own would emit a blank line",
        ],
    )


def _rewrite_optional_line(
    lines: list[str], site: _Site, target: RenderTarget, index: int, drifted: bool
) -> Rewrite | Refusal:
    """A boolean whose truth value is the presence of the whole line.

    Emitted in the hugging form and no other: ``[% if x %]LINE`` then
    ``[% endif %]NEXT``. That is what
    ``templates/calibre/calibre_lvs.qci.j2:31-32`` does, and with
    ``trim_blocks`` off it is the only form that does not leave a blank line
    behind when the flag is false.
    """

    opt = site.opt
    line = lines[index]
    if line.lstrip().startswith("[%"):
        return _refuse(
            site,
            target,
            Reason.ALREADY_PARAMETERISED,
            f"line {index + 1} already opens with a statement tag: {line!r}",
        )
    if index + 1 >= len(lines):
        return _refuse(
            site,
            target,
            Reason.UNSUPPORTED_SHAPE,
            "the optional line is the last line of the file, so there is no "
            "following line for [% endif %] to hug",
        )
    if not opt.default:
        return _refuse(
            site,
            target,
            Reason.UNSUPPORTED_SHAPE,
            "the row defaults to false while the line is present in the "
            "template; wrapping it would change what a default render emits",
        )

    var = opt.template_var
    before = (line, lines[index + 1])
    new_line = f"[% if {var} %]{line}"
    next_line = f"[% endif %]{lines[index + 1]}"
    lines[index] = new_line
    lines[index + 1] = next_line

    return _finish(
        site,
        target,
        line=index + 1,
        drifted=drifted,
        shape=Shape.BOOL_OPTIONAL_LINE,
        literal=line,
        placeholder=f"[% if {var} %]",
        before=before,
        after=(new_line, next_line),
        notes=[
            _drift_note(site, index, drifted),
            "hugging form: each tag shares its line with the text it governs, "
            "because trim_blocks is off",
        ],
    )


# ---- command line ------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Print the work list, or a dry-run rewrite of one template.

    ``python -m auto_ext.catalog.parameterise`` prints the audit;
    ``... --target si.env --diff`` prints what a rewrite of that template would
    do; ``... --target si.env --write`` performs it. Nothing is written to a
    template without ``--write``.
    """

    import argparse

    parser = argparse.ArgumentParser(
        description="Catalog-driven parameterisation of the shipped templates."
    )
    parser.add_argument("--target", choices=[t.value for t in RenderTarget])
    parser.add_argument("--write", action="store_true", help="apply the rewrite")
    parser.add_argument("--diff", action="store_true", help="print a unified diff")
    args = parser.parse_args(argv)

    cat = builtin_catalog()
    if args.target is None:
        print(audit_pending(cat).describe())
        return 0

    target = RenderTarget(args.target)
    spec = cat.target(target)
    path = spec.template_path
    source = path.read_text(encoding="utf-8")
    new_text, report = parameterise(source, target, catalog=cat)
    print(report.describe())
    if args.diff:
        import difflib

        print(
            "\n".join(
                difflib.unified_diff(
                    source.split("\n"),
                    new_text.split("\n"),
                    fromfile=f"a/{spec.template}",
                    tofile=f"b/{spec.template}",
                    lineterm="",
                )
            )
        )
    if args.write and report.changed:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(new_text)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual tool
    raise SystemExit(main())
