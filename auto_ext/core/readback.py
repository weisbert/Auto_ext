"""Read the literals a catalog knows about back out of a tool input file.

Split out of :mod:`auto_ext.migrate`, which was its only caller when it was
written and is no longer. Two callers now need the same answer for two
different reasons:

* the **migration** reads the user's *v1 templates* so that no value modelled
  by the catalog can change while the config moves to the v2 object model;
* the **recipe importer** (:mod:`auto_ext.core.recipe_import`) reads a
  *rendered file the user already owns* -- a ``.cmd`` saved out of the Quantus
  GUI, a colleague's ``.qci`` -- so that the same values become a Recipe.

The two inputs are the same syntax with one difference: a template has Jinja
at the interesting positions and a real export has a literal there. Every rule
in this module falls out of that: a site holding ``[[cell]]`` is skipped
(:class:`NotALiteral`), a site holding ``pll_top`` is read.

What this module does NOT decide
--------------------------------
Whether a recovered value may be *used*. A landing site shared by several
catalog rows -- ``*lvsRulesFile``, which is a directory plus a basename plus a
deck variant plus a filename pattern on one line -- parses as one string, and
the generic "the option's value is the value" rule would hand that whole
string to each of the four rows. In a template those sites hold Jinja and the
question never arises; against a real export it does.
:func:`composite_sites` reports which sites are shared so a caller can refuse
them, and :func:`read_back_from_templates` still returns whatever the generic
rule produced. Interpreting is the caller's job, not the parser's.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from auto_ext.catalog import (
    Catalog,
    LandingSite,
    OptionSpec,
    OptionType,
    Owner,
    Quoting,
    RenderTargetSpec,
    builtin_catalog,
)
from auto_ext.core.errors import AutoExtError
from auto_ext.model.common import RenderTarget, WrittenFloat

__all__ = [
    "DEFAULT_READBACK_OWNERS",
    "PARSERS",
    "SPECIAL_READBACK",
    "NotALiteral",
    "RawValue",
    "ReadBackError",
    "ReadSite",
    "SiteKey",
    "TemplateReadBack",
    "composite_sites",
    "has_jinja",
    "parse_by_syntax",
    "parse_calibre",
    "parse_quantus",
    "parse_skill",
    "parse_xml",
    "read_back_from_templates",
    "site_key",
]

#: Which owners :func:`read_back_from_templates` walks unless told otherwise.
#: The three whose values a user can set; ``cells`` and ``run`` are per-DUT and
#: per-run, and ``fixed`` is not a setting at all.
DEFAULT_READBACK_OWNERS: tuple[Owner, ...] = (Owner.RECIPE, Owner.PROFILE, Owner.RESOURCES)

#: A parsed file is keyed by ``(section, option)``. Section is ``""`` for every
#: syntax but the Quantus command file; see :func:`site_key`.
SiteKey = tuple[str, str]

_JINJA_MARKERS = ("[[", "[%", "[#")

_TRUE_TOKENS = frozenset({"t", "'t", "true", "1", "yes", "on"})
_FALSE_TOKENS = frozenset({"nil", "'nil", "false", "0", "no", "off"})


class ReadBackError(AutoExtError):
    """The catalog names a target whose syntax no parser here can read."""


@dataclass(frozen=True)
class RawValue:
    """One option's raw value as it appears in a file.

    ``line`` is the 1-based line the *directive* was found on, not its value's;
    the two differ for the Quantus layouts that put the value on the next line
    or one value per line. It is provenance only -- no read-back rule depends
    on it -- and it is what lets a report say "``-min_res`` on line 29 of
    ext.cmd" instead of naming the file alone.
    """

    values: tuple[str, ...]
    text: str
    line: int | None = None


class NotALiteral(Exception):
    """The site holds a Jinja expression or a value this parser cannot read."""


@dataclass(frozen=True)
class ReadSite:
    """Where one recovered value was actually read from."""

    target: RenderTarget
    section: str
    option: str
    line: int | None = None

    def describe(self) -> str:
        where = f"{self.section} {self.option}" if self.section else self.option
        at = f" line {self.line}" if self.line is not None else ""
        return f"{self.target.value}: {where}{at}"


@dataclass(frozen=True)
class TemplateReadBack:
    """Literals recovered from one set of template files.

    ``values`` is keyed by catalog key, so the caller writes them wherever the
    catalog says that key lives (``recipe_field_path`` / ``context_path``).
    ``unread`` records, per catalog key, why nothing was recovered -- that
    string is what the report shows the user.
    """

    values: dict[str, Any]
    unread: dict[str, str]
    #: Catalog keys whose recovered value differs from the catalog default,
    #: i.e. places where the user's template says something else.
    diverged: dict[str, tuple[Any, Any]]
    #: Which render targets were actually available.
    targets: tuple[RenderTarget, ...]
    #: Per recovered key, the landing site the value came from. Provenance for
    #: the report, and the hook a caller needs in order to ask whether that
    #: site was shared with other rows (see :func:`composite_sites`).
    sites: dict[str, ReadSite] = field(default_factory=dict)

    def get(self, key: str, fallback: Any = None) -> Any:
        return self.values.get(key, fallback)


def has_jinja(text: str) -> bool:
    return any(marker in text for marker in _JINJA_MARKERS)


def _tokenize(text: str) -> list[str]:
    """Split on whitespace, keeping double-quoted runs as one token.

    Quotes are removed; a quoted empty string survives as the empty value,
    which is what ``globalPowerSig = ""`` needs.
    """

    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == '"':
            end = text.find('"', index + 1)
            if end == -1:
                tokens.append(text[index + 1 :])
                break
            tokens.append(text[index + 1 : end])
            index = end + 1
            continue
        end = index
        while end < len(text) and not text[end].isspace():
            end += 1
        tokens.append(text[index:end])
        index = end
    return tokens


def _unquote(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        return stripped[1:-1]
    return stripped


_SI_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_CALIBRE_LINE = re.compile(r"^\s*(\*[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*?)\s*$")
_XML_ELEMENT = re.compile(r"<([A-Za-z_][A-Za-z0-9_]*)\s+value=\"([^\"]*)\"\s*/>")
_QUANTUS_OPTION = re.compile(r"^-[A-Za-z_]")


def parse_skill(text: str) -> dict[SiteKey, RawValue]:
    """``si.env``: ``key = value``, one per line. Section is not encoded."""

    out: dict[SiteKey, RawValue] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = _SI_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        out[("", key)] = RawValue(values=(_unquote(value),), text=value, line=number)
    return out


def parse_calibre(text: str) -> dict[SiteKey, RawValue]:
    """``lvs.qci``: ``*Key: value``, one per line."""

    out: dict[SiteKey, RawValue] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = _CALIBRE_LINE.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        out[("", key)] = RawValue(values=(_unquote(value),), text=value, line=number)
    return out


def parse_xml(text: str) -> dict[SiteKey, RawValue]:
    """``jivaro.xml``: ``<tag value="..."/>``, packed onto few lines."""

    out: dict[SiteKey, RawValue] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        for tag, value in _XML_ELEMENT.findall(line):
            out[("", tag)] = RawValue(values=(value,), text=value, line=number)
    return out


def parse_quantus(text: str) -> dict[SiteKey, RawValue]:
    """Quantus command file: sections at column 0, ``-option value...`` inside.

    The whole file is tokenized rather than matched line by line, which is
    what makes the four layouts one case instead of four: ``-option value``
    (inline), ``-option`` plus a trailing backslash with the value on the next
    line, ``-output_xy`` followed by eight quoted values one per line, and a
    section header that carries its first option on the same line
    (``input_db -type calibre``).
    """

    out: dict[SiteKey, RawValue] = {}
    section = ""
    option: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        starts_section = not line[:1].isspace()
        body = stripped[:-1].rstrip() if stripped.endswith("\\") else stripped
        tokens = _tokenize(body)
        if not tokens:
            continue
        if starts_section:
            section = tokens[0]
            tokens = tokens[1:]
            option = None
        for token in tokens:
            if _QUANTUS_OPTION.match(token):
                option = token
                out[(section, option)] = RawValue(values=(), text="", line=number)
                continue
            if option is None:
                continue
            previous = out[(section, option)]
            joined = f"{previous.text} {token}".strip() if previous.text else token
            out[(section, option)] = RawValue(
                values=(*previous.values, token), text=joined, line=previous.line
            )
    return out


#: Target syntax (``RenderTargetSpec.syntax``) -> the parser that reads it.
PARSERS: dict[str, Callable[[str], dict[SiteKey, RawValue]]] = {
    "skill": parse_skill,
    "calibre_runset": parse_calibre,
    "quantus_cmd": parse_quantus,
    "xml": parse_xml,
}


def parse_by_syntax(syntax: str, text: str) -> dict[SiteKey, RawValue]:
    """Parse ``text`` with the parser registered for ``syntax``.

    Raises :class:`ReadBackError` naming the syntax rather than returning an
    empty dict: "nothing was found" and "nothing could be looked for" are
    different answers, and a caller that cannot tell them apart reports an
    unreadable file as a clean one.
    """

    parser = PARSERS.get(syntax)
    if parser is None:
        raise ReadBackError(f"no read-back parser for syntax {syntax!r}")
    return parser(strip_block_tags(text))


#: ``[% ... %]`` -- a Jinja BLOCK tag. Not ``[[ ... ]]``, which is a value
#: placeholder and must survive: the whole point of parsing the template is to
#: find those and match them against the user's literals.
_BLOCK_TAG = re.compile(r"\[%.*?%\]")


def strip_block_tags(text: str) -> str:
    """Remove Jinja block tags so a statement keeps its own name.

    The command-file parsers key a site by ``(statement, option)`` and read
    the statement name off the start of a line. The house rule is that a
    block tag shares a line with the text it governs -- ``trim_blocks`` is
    off, so a tag alone on a line would emit a blank one -- and that puts
    ``[% endfor %]extraction_setup`` in the template where the rendered file
    says ``extraction_setup``.

    Without this the whole statement goes missing from the TEMPLATE side of
    the import solver, so none of its options can be recovered and every one
    of them is reported as a manual edit. That is exactly what happened when
    the extract block became a loop: the visible symptom was
    ``-parasitic_blocking_device_cells_file``, eight lines further down,
    turning into a hunk.

    A no-op for any text that is not a template, which is the common case --
    the same function runs over the user's file, and their file has no tags.
    """

    return _BLOCK_TAG.sub("", text)


def _special_run_qrc_query(raw: RawValue) -> Any:
    return "-query_input" in raw.text


def _special_query_cmd_name(raw: RawValue) -> Any:
    match = re.search(r"-query_input\s+(\S+)", raw.text)
    if not match:
        raise NotALiteral("no -query_input clause in the post-trigger line")
    name = PurePosixPath(match.group(1)).name
    if has_jinja(name):
        raise NotALiteral("the query_cmd file name is itself a variable")
    return name


def _special_basename(raw: RawValue) -> Any:
    name = PurePosixPath(raw.text.strip()).name
    if has_jinja(name):
        raise NotALiteral("the file name is itself a variable")
    return name


def _special_rules_pattern(raw: RawValue) -> Any:
    name = PurePosixPath(raw.text.strip()).name
    name = name.replace("[[calibre_lvs_basename]]", "{basename}")
    name = name.replace("[[lvs_variant]]", "{suffix}")
    if has_jinja(name):
        raise NotALiteral("the rules-file name carries variables we do not model")
    return name


#: Catalog keys whose value shares a line with something else, so the generic
#: "the option's value is the value" rule does not apply.
SPECIAL_READBACK: dict[str, Callable[[RawValue], Any]] = {
    "run_qrc_query": _special_run_qrc_query,
    "qrc_query_cmd_name": _special_query_cmd_name,
    "qrc_preserve_cell_list_name": _special_basename,
    "lvs_rules_filename_pattern": _special_rules_pattern,
}


def _coerce_bool(token: str) -> bool:
    lowered = token.strip().lower()
    if lowered in _TRUE_TOKENS:
        return True
    if lowered in _FALSE_TOKENS:
        return False
    raise NotALiteral(f"{token!r} is not a boolean this parser recognises")


def _coerce(option: OptionSpec, quoting: Quoting, raw: RawValue) -> Any:
    """Turn a raw site value into the type the catalog says the option has."""

    if has_jinja(raw.text):
        raise NotALiteral("the value is a Jinja expression, not a literal")
    if option.type is OptionType.LIST:
        if quoting is Quoting.SKILL_LIST:
            return re.findall(r'"([^"]*)"', raw.text)
        if len(raw.values) > 1:
            return list(raw.values)
        return raw.text.split()
    if not raw.values:
        raise NotALiteral("the option is present but carries no value")
    token = raw.values[0]
    if option.type is OptionType.BOOL:
        return _coerce_bool(token)
    if option.type is OptionType.INT:
        try:
            return int(float(token))
        except ValueError as exc:
            raise NotALiteral(f"{token!r} is not an integer") from exc
    if option.type is OptionType.FLOAT:
        try:
            # WrittenFloat, not float: ``-temperature 125`` and
            # ``-temperature 125.0`` are one number with two spellings, and the
            # render side has to give back the one the file used or the
            # difference is recorded as a patch that pins the literal.
            return WrittenFloat(token)
        except ValueError as exc:
            raise NotALiteral(f"{token!r} is not a number") from exc
    return token


def site_key(site: LandingSite, target_spec: RenderTargetSpec) -> SiteKey:
    """Where to look this site up in the parsed file.

    Section is part of the key only for Quantus command files: ``-type`` means
    three different things in three sections there, while ``si.env``,
    ``lvs.qci`` and ``jivaro.xml`` all have globally unique option names and
    carry logical (not textual) section labels in the catalog.
    """

    if target_spec.syntax == "quantus_cmd":
        return (site.section, site.option)
    return ("", site.option)


def composite_sites(
    catalog: Catalog | None = None,
) -> dict[tuple[RenderTarget, str, str], list[str]]:
    """Landing sites shared by more than one catalog row.

    Keyed by ``(target, section, option)``, valued with the option keys that
    share it. One physical line, several modelled values: ``*lvsRulesFile``
    carries a directory, a basename, a deck variant and a filename pattern;
    ``-design_cell_name`` carries cell, view and library; jivaro's
    ``inputView`` carries library, cell and out_file.

    Reading such a site with the generic rule gives every row the *whole* line,
    which against a real export is a wrong value rather than a missing one --
    ``lvs.deck_variant = "/pdk/.../CFXXX.wodio.qcilvs"``. Callers that read
    real exports must refuse these sites; the four keys in
    :data:`SPECIAL_READBACK` are the ones that already know how to take only
    their own share of the line.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    seen: dict[tuple[RenderTarget, str, str], list[str]] = {}
    for opt in cat.options:
        for site in opt.lands_in:
            if site.target is None:
                continue
            seen.setdefault((site.target, site.section, site.option), []).append(opt.key)
    return {where: keys for where, keys in seen.items() if len(keys) > 1}


def read_back_from_templates(
    texts: Mapping[RenderTarget, str],
    *,
    catalog: Catalog | None = None,
    owners: Sequence[Owner] = DEFAULT_READBACK_OWNERS,
) -> TemplateReadBack:
    """Recover every catalog-known literal from a set of template texts.

    ``texts`` maps render target to the *user's* template source. Rows whose
    landing site holds a Jinja expression are skipped on purpose: those values
    arrive through the knob / config layer instead, and reading ``[[cell]]``
    as a literal is exactly the silent corruption this whole exercise exists
    to prevent.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    parsed: dict[RenderTarget, dict[SiteKey, RawValue]] = {}
    for target, text in texts.items():
        spec = cat.target(target)
        parsed[target] = parse_by_syntax(spec.syntax, text)

    values: dict[str, Any] = {}
    unread: dict[str, str] = {}
    diverged: dict[str, tuple[Any, Any]] = {}
    sites: dict[str, ReadSite] = {}

    for option in cat.options:
        if option.owner not in owners:
            continue
        if not option.lands_in:
            unread[option.key] = f"not written to any file (currently: {option.currently})"
            continue
        reasons: list[str] = []
        recovered: list[tuple[RenderTarget | None, Any, ReadSite]] = []
        for site in option.lands_in:
            if site.target is None or site.target not in parsed:
                reasons.append(f"{site.target or site.stage}: file not part of this project")
                continue
            spec = cat.target(site.target)
            raw = parsed[site.target].get(site_key(site, spec))
            if raw is None:
                reasons.append(f"{site.target}: {site.option} not found in the file")
                continue
            found = ReadSite(
                target=site.target, section=site.section, option=site.option, line=raw.line
            )
            try:
                handler = SPECIAL_READBACK.get(option.key)
                if handler is not None:
                    recovered.append((site.target, handler(raw), found))
                else:
                    recovered.append(
                        (site.target, _coerce(option, site.render(spec).quoting, raw), found)
                    )
            except NotALiteral as exc:
                reasons.append(f"{site.target}: {exc}")
        if not recovered:
            unread[option.key] = "; ".join(reasons) or "no readable landing site"
            continue
        first = recovered[0][1]
        disagreeing = [f"{tgt}={val!r}" for tgt, val, _ in recovered[1:] if val != first]
        if disagreeing:
            unread[option.key] = (
                f"the same value is spelled differently per file "
                f"({recovered[0][0]}={first!r}, {', '.join(disagreeing)}); "
                "one Recipe field cannot hold both"
            )
            continue
        values[option.key] = first
        sites[option.key] = recovered[0][2]
        if option.default is not None and first != option.default:
            diverged[option.key] = (option.default, first)

    return TemplateReadBack(
        values=values,
        unread=unread,
        diverged=diverged,
        targets=tuple(texts),
        sites=sites,
    )
