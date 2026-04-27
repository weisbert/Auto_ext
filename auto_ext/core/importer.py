"""Raw-EDA-export → parameterised template importer.

Phase 4b1 entry point. Takes a raw export from one of the four tools and
returns an :class:`ImportResult` carrying:

- the inferred :class:`Identity` (cell / library / view / ...),
- a ``template_body`` with identity values substituted to ``[[name]]``
  placeholders at recognised key positions only (no global string replace
  — that would false-positive on substrings like ``"INV"`` inside
  ``"INVPROJECT"``),
- a list of :class:`Candidate` literals a user may want to promote to
  knobs via ``./run.sh knob promote``,
- a list of :class:`PdkToken` hardcoded values surfaced for the review
  report (cross-file aggregation is Phase 4b2).

The importer never writes files — the CLI layer composes ``ImportResult``
+ the existing :mod:`auto_ext.core.manifest` to produce the ``.j2`` and
``.manifest.yaml`` on disk, with backup and smart-merge logic.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from auto_ext.core.errors import AutoExtError
from auto_ext.core.manifest import KnobSpec, TemplateManifest

logger = logging.getLogger(__name__)


TOOL = Literal["calibre", "si", "quantus", "jivaro"]
_TOOLS: tuple[TOOL, ...] = ("calibre", "si", "quantus", "jivaro")


class ImportError(AutoExtError):
    """Raised when a raw file cannot be parsed or identities disagree."""


# ---- dataclasses -----------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """Identity values extracted from a raw EDA file.

    Every field is optional because each tool only surfaces a subset
    (e.g. Quantus carries ``ground_net`` but not ``lvs_source_view``).
    """

    cell: str | None = None
    library: str | None = None
    lvs_layout_view: str | None = None
    lvs_source_view: str | None = None
    out_file: str | None = None
    ground_net: str | None = None


@dataclass(frozen=True)
class Candidate:
    """One literal proposed for promotion to a knob."""

    key: str
    suggested_name: str
    type: Literal["int", "float", "str", "bool"]
    default: Any
    line: int
    confidence: Literal["high", "medium", "low"]


@dataclass(frozen=True)
class PdkToken:
    """Hardcoded value flagged for project-level review.

    Cross-file aggregation is Phase 4b2's job; 4b1 emits per-file hits so
    the review report can list them and so ``init-project`` has raw input.

    Phase 5.6.5 simplifies categories to ``tech_name`` + ``abs_path`` /
    ``unknown``: the pdk_subdir / runset_version / project_subdir
    segment-extraction model was abandoned in favor of whole-path
    capture (see :class:`ProjectConstants.paths`).
    """

    value: str
    category: Literal[
        "tech_name",
        "abs_path",
        "unknown",
    ]
    line: int


@dataclass
class ImportResult:
    """Output of importing one raw file."""

    tool: TOOL
    identity: Identity
    template_body: str
    candidates: list[Candidate] = field(default_factory=list)
    pdk_tokens: list[PdkToken] = field(default_factory=list)
    raw_source: str = ""
    auto_knobs: dict[str, KnobSpec] = field(default_factory=dict)


# ---- public entry point ----------------------------------------------------


def import_template(
    tool: TOOL,
    raw_source: str,
    *,
    identity_overrides: Identity | None = None,
) -> ImportResult:
    """Dispatch ``raw_source`` to the per-tool importer and enrich the
    result with candidate knobs and PdkToken surveys.

    ``identity_overrides`` wins over values inferred from the raw text; a
    mismatch between override and inferred value logs a warning but the
    override is applied.
    """

    if tool not in _TOOLS:
        raise ImportError(f"unknown tool {tool!r}; valid: {list(_TOOLS)}")

    dispatch: dict[TOOL, Any] = {
        "calibre": _import_calibre,
        "si": _import_si,
        "quantus": _import_quantus,
        "jivaro": _import_jivaro,
    }
    result = dispatch[tool](raw_source, identity_overrides)
    result.raw_source = raw_source
    result.candidates = _detect_candidates(tool, result.template_body)
    result.pdk_tokens = _detect_pdk_tokens(result.template_body)
    return result


# ---- identity / substitution plumbing --------------------------------------


_IDENTITY_FIELDS: tuple[str, ...] = (
    "cell",
    "library",
    "lvs_layout_view",
    "lvs_source_view",
    "out_file",
    "ground_net",
)


@dataclass(frozen=True)
class _Rule:
    """One raw-key substitution rule for the per-tool line scanner.

    ``value_pattern`` must fullmatch the value portion. ``fields`` lists
    ``(identity_field, capture_group)`` pairs extracted from the match;
    most rules have a single pair, but composite keys like Quantus
    ``-design_cell_name "cell layout library"`` bind three fields from
    one line. Non-identity field names (``output_dir``, ``jivaro_*``)
    are cross-checked for consistency but excluded from :class:`Identity`.

    ``replacement`` is expanded via :meth:`re.Match.expand` so it may
    include ``\\1`` backreferences to the raw value (used to interpolate
    a default into Jinja ``| default(...)`` filters).
    """

    fields: tuple[tuple[str, int], ...]
    value_pattern: re.Pattern[str]
    replacement: str


def _simple(field: str, value_pattern: str, replacement: str) -> _Rule:
    """Shorthand for single-group rules: one field, capture group 1."""
    return _Rule(
        fields=((field, 1),),
        value_pattern=re.compile(value_pattern),
        replacement=replacement,
    )


def _reconcile(
    field_name: str,
    inferred: str | None,
    override: str | None,
    *,
    prior: str | None,
) -> str | None:
    """Merge an override/prior with a freshly-inferred value.

    Policy:
      - If override is set and inferred disagrees, log a warning and
        return the override (override always wins).
      - If prior is set and inferred disagrees, raise :class:`ImportError`
        — that is a real inconsistency inside the raw file.
    """
    if override is not None:
        if inferred is not None and inferred != override:
            logger.warning(
                "importer: override %s=%r disagrees with inferred %r; using override",
                field_name,
                override,
                inferred,
            )
        return override
    if prior is not None and inferred is not None and inferred != prior:
        raise ImportError(
            f"{field_name!r} inconsistent across keys: first={prior!r}, now={inferred!r}"
        )
    return inferred if inferred is not None else prior


def _apply_rules(
    tool: TOOL,
    raw: str,
    line_pattern: re.Pattern[str],
    rules_by_key: dict[str, list[_Rule]],
    overrides: Identity | None,
) -> tuple[Identity, str, dict[str, str]]:
    """Scan ``raw`` line-by-line, applying every matching rule.

    Returns ``(identity, template_body, auxiliary)`` where ``auxiliary``
    holds non-identity fields (``output_dir`` etc.) that were extracted
    but are not part of :class:`Identity`.
    """

    extracted: dict[str, str | None] = {f: None for f in _IDENTITY_FIELDS}
    aux: dict[str, str] = {}

    override_map = (
        {f: getattr(overrides, f) for f in _IDENTITY_FIELDS} if overrides else {}
    )

    out_parts: list[str] = []

    for raw_line in raw.splitlines(keepends=True):
        m = line_pattern.match(raw_line)
        if not m:
            out_parts.append(raw_line)
            continue

        key = m.group("key")
        value = m.group("value")
        rules = rules_by_key.get(key)
        if rules is None:
            out_parts.append(raw_line)
            continue

        matched = False
        for rule in rules:
            vm = rule.value_pattern.fullmatch(value)
            if vm is None:
                continue
            for field_name, group_idx in rule.fields:
                inferred = vm.group(group_idx)
                if field_name in _IDENTITY_FIELDS:
                    new = _reconcile(
                        field_name,
                        inferred,
                        override_map.get(field_name),
                        prior=extracted[field_name],
                    )
                    extracted[field_name] = new
                else:
                    prior = aux.get(field_name)
                    if prior is not None and prior != inferred:
                        raise ImportError(
                            f"{field_name!r} inconsistent across keys in raw "
                            f"{tool} file: first={prior!r}, now={inferred!r}"
                        )
                    aux[field_name] = inferred

            prefix = raw_line[: m.start("value")]
            suffix = raw_line[m.end("value") :]
            out_parts.append(prefix + vm.expand(rule.replacement) + suffix)
            matched = True
            break

        if not matched:
            out_parts.append(raw_line)

    # Overrides whose field never appeared in the raw still take effect.
    for field_name, val in override_map.items():
        if val is not None and extracted[field_name] is None:
            extracted[field_name] = val

    identity = Identity(
        cell=extracted["cell"],
        library=extracted["library"],
        lvs_layout_view=extracted["lvs_layout_view"],
        lvs_source_view=extracted["lvs_source_view"],
        out_file=extracted["out_file"],
        ground_net=extracted["ground_net"],
    )
    return identity, "".join(out_parts), aux


# ---- per-tool importers ----------------------------------------------------


# Calibre ``.qci`` lines have the shape ``*<key>: <value>`` (colon + one
# space). Keys are word characters after the leading ``*``.
_CALIBRE_LINE_RE = re.compile(
    r"^(?P<prefix>\*(?P<key>[A-Za-z_]\w*):[ \t])(?P<value>.*?)(?P<nl>\r?\n)?$"
)

# Identity rules keyed by raw Calibre key name.
_CALIBRE_RULES: dict[str, list[_Rule]] = {
    "lvsLayoutPrimary": [_simple("cell", r"(.+?)", "[[cell]]")],
    "lvsLayoutLibrary": [_simple("library", r"(.+?)", "[[library]]")],
    "lvsLayoutView": [_simple("lvs_layout_view", r"(.+?)", "[[lvs_layout_view]]")],
    "lvsSourcePrimary": [_simple("cell", r"(.+?)", "[[cell]]")],
    "lvsSourceLibrary": [_simple("library", r"(.+?)", "[[library]]")],
    "lvsSourceView": [_simple("lvs_source_view", r"(.+?)", "[[lvs_source_view]]")],
    "lvsLayoutPaths": [_simple("cell", r"(.+?)\.calibre\.db", "[[cell]].calibre.db")],
    "lvsSourcePath": [_simple("cell", r"(.+?)\.src\.net", "[[cell]].src.net")],
    "lvsSpiceFile": [_simple("cell", r"(.+?)\.sp", "[[cell]].sp")],
    "lvsERCDatabase": [_simple("cell", r"(.+?)\.erc\.results", "[[cell]].erc.results")],
    "lvsERCSummaryFile": [_simple("cell", r"(.+?)\.erc\.summary", "[[cell]].erc.summary")],
    "lvsReportFile": [_simple("cell", r"(.+?)\.lvs\.report", "[[cell]].lvs.report")],
    "cmnFDILayoutLibrary": [_simple("library", r"(.+?)", "[[library]]")],
    "cmnFDILayoutView": [_simple("lvs_layout_view", r"(.+?)", "[[lvs_layout_view]]")],
    "cmnFDIDEFLayoutPath": [_simple("cell", r"(.+?)\.def", "[[cell]].def")],
    "lvsRunDir": [_simple("output_dir", r"(.+?)", "[[output_dir]]")],
    "cmnTemplate_RN": [_simple("output_dir", r"(.+?)", "[[output_dir]]")],
}


def _import_calibre(raw: str, overrides: Identity | None) -> ImportResult:
    preprocessed = _preprocess_employee_id(raw)
    identity, body, _aux = _apply_rules(
        "calibre", preprocessed, _CALIBRE_LINE_RE, _CALIBRE_RULES, overrides
    )
    auto_knobs: dict[str, KnobSpec] = {}
    body, knob = _auto_calibre_connect_by_name(body)
    if knob is not None:
        auto_knobs["connect_by_name"] = knob
    body, knob = _auto_calibre_lvs_variant(body)
    if knob is not None:
        auto_knobs["lvs_variant"] = knob
    return ImportResult(
        tool="calibre",
        identity=identity,
        template_body=body,
        auto_knobs=auto_knobs,
    )


# Anchor lines for the connect_by_name auto-knob.
#
# The bundled template wraps the optional ``*cmnVConnectNamesState: ALL``
# line in ``[% if connect_by_name %]...[% endif %]``. The two real-world
# variants of a Calibre raw export are:
#
#   ON  — has a literal ``*cmnVConnectNamesState: ALL`` line. Wrap it.
#   OFF — has no such line, but does carry ``*cmnShowOptions:``. Inject
#         the wrapped line right after that anchor (default = False).
#
# If neither anchor is present we leave the body alone and emit no knob.
_CALIBRE_CONNECT_ON_LINE = "*cmnVConnectNamesState: ALL\n"
_CALIBRE_CONNECT_BLOCK = (
    "[% if connect_by_name %]*cmnVConnectNamesState: ALL\n[% endif %]"
)
_CALIBRE_SHOW_OPTIONS_RE = re.compile(r"^\*cmnShowOptions:[^\n]*\n", re.MULTILINE)
_CONNECT_BY_NAME_DESCRIPTION = (
    "When true, emits *cmnVConnectNamesState ALL so Calibre LVS connects "
    "nets by name across the layout."
)


def _auto_calibre_connect_by_name(body: str) -> tuple[str, KnobSpec | None]:
    """Wrap (or inject) the *cmnVConnectNamesState ALL line under a
    ``connect_by_name`` toggle.

    Returns ``(new_body, knob_or_none)``. The knob is omitted when the
    raw lacks both the ON-variant line and the ``*cmnShowOptions:``
    anchor — there's nothing to parameterize.
    """
    if _CALIBRE_CONNECT_ON_LINE in body:
        # ON variant: wrap the existing line.
        new_body = body.replace(
            _CALIBRE_CONNECT_ON_LINE, _CALIBRE_CONNECT_BLOCK, 1
        )
        spec = KnobSpec(
            type="bool",
            default=True,
            description=_CONNECT_BY_NAME_DESCRIPTION,
        )
        return new_body, spec

    m = _CALIBRE_SHOW_OPTIONS_RE.search(body)
    if m is None:
        # Neither anchor present — leave body alone, emit no knob.
        return body, None

    # OFF variant: inject the wrapped block right after *cmnShowOptions:.
    insert_at = m.end()
    new_body = body[:insert_at] + _CALIBRE_CONNECT_BLOCK + body[insert_at:]
    spec = KnobSpec(
        type="bool",
        default=False,
        description=_CONNECT_BY_NAME_DESCRIPTION,
    )
    return new_body, spec


# *lvsRulesFile: ...<basename>.<variant>.qcilvs — capture only when the
# variant is a recognised wodio/widio so a custom suffix doesn't get
# silently swapped. The pattern anchors to the start of the lvsRulesFile
# line so a literal "wodio" elsewhere in the body cannot trigger.
_CALIBRE_LVS_VARIANT_RE = re.compile(
    r"^(\*lvsRulesFile:[^\n]*?\.)(wodio|widio)(\.qcilvs)",
    re.MULTILINE,
)
_LVS_VARIANT_DESCRIPTION = (
    "Calibre LVS rules-file suffix; both variants live under "
    "$VERIFY_ROOT/runset/Calibre_QRC/LVS/<lvs_runset_version>/<pdk_subdir>/."
)


def _auto_calibre_lvs_variant(body: str) -> tuple[str, KnobSpec | None]:
    """Substitute the wodio/widio suffix on ``*lvsRulesFile`` for
    ``[[lvs_variant]]`` and emit a matching enum knob.

    Returns ``(new_body, knob_or_none)``. The knob is omitted when the
    rules-file line is absent OR uses a suffix outside ``{wodio, widio}``.
    """
    m = _CALIBRE_LVS_VARIANT_RE.search(body)
    if m is None:
        return body, None
    variant = m.group(2)
    new_body = (
        body[: m.start(2)] + "[[lvs_variant]]" + body[m.end(2) :]
    )
    spec = KnobSpec(
        type="str",
        default=variant,
        choices=["wodio", "widio"],
        description=_LVS_VARIANT_DESCRIPTION,
    )
    return new_body, spec


# SI ``si.env`` uses SKILL-style ``<key> = <value>``. Identity values are
# always double-quoted — unquoted RHSs (``simNotIncremental = 't``, scalar
# numerics) carry no identity, so the rule pattern targets the quoted form
# only. The line pattern locks onto the content *between* the quotes: the
# quote characters themselves live in ``prefix``/``suffix`` and survive
# substitution untouched.
_SI_LINE_RE = re.compile(
    r'^(?P<prefix>(?P<key>[A-Za-z_]\w*)\s*=\s*")(?P<value>[^"\r\n]*)'
    r'(?P<tail>"\s*(?:\r?\n)?)$'
)

_SI_RULES: dict[str, list[_Rule]] = {
    "simLibName": [_simple("library", r"(.+?)", "[[library]]")],
    "simCellName": [_simple("cell", r"(.+?)", "[[cell]]")],
    "simViewName": [_simple("lvs_source_view", r"(.+?)", "[[lvs_source_view]]")],
    "hnlNetlistFileName": [_simple("cell", r"(.+?)\.src\.net", "[[cell]].src.net")],
    "simRunDir": [_simple("output_dir", r"(.+?)", "[[output_dir]]")],
}


_SI_RUN_DIR_LINE_RE = re.compile(r"^simRunDir\s*=", re.MULTILINE)


def _import_si(raw: str, overrides: Identity | None) -> ImportResult:
    preprocessed = _preprocess_employee_id(raw)
    identity, body, _aux = _apply_rules(
        "si", preprocessed, _SI_LINE_RE, _SI_RULES, overrides
    )
    # Some Cadence flows export si.env without ``simRunDir = "..."`` —
    # without that line si writes the netlist into its cwd instead of
    # the output_dir, and Quantus then aborts (LBRCXM-756) because it
    # can't find the netlist where the runner staged si.env. Inject the
    # canonical line so the imported template behaves like the bundled
    # one.
    if not _SI_RUN_DIR_LINE_RE.search(body):
        if body and not body.endswith("\n"):
            body += "\n"
        body += 'simRunDir = "[[output_dir]]"\n'
    return ImportResult(tool="si", identity=identity, template_body=body)


# Quantus ``.cmd`` uses Tcl-style ``-<option> <value>`` with leading
# indentation. Values are quoted (for strings / composite keys like
# ``-design_cell_name "cell layout library"``) or bare (numeric). Identity
# rules target the quoted form; unquoted numerics flow to the candidate
# detector.
_QUANTUS_LINE_RE = re.compile(
    r'^(?P<prefix>\s*-(?P<key>[A-Za-z_]\w*)\s+")(?P<value>[^"\r\n]*)'
    r'(?P<tail>"\s*\\?\s*(?:\r?\n)?)$'
)

_QUANTUS_RULES: dict[str, list[_Rule]] = {
    "ground_net": [_simple("ground_net", r"(.+?)", "[[ground_net]]")],
    "design_cell_name": [
        _Rule(
            fields=(("cell", 1), ("lvs_layout_view", 2), ("library", 3)),
            value_pattern=re.compile(r"(\S+) (\S+) (\S+)"),
            replacement="[[cell]] [[lvs_layout_view]] [[library]]",
        )
    ],
    # Negative lookahead skips ``/tmpdata/RFIC/rfic_share/...`` paths —
    # those carry employee_id (handled via a pre-pass, not output_dir)
    # and have the same ``/query_output`` suffix otherwise.
    "directory_name": [
        _simple(
            "output_dir",
            r"(?!/tmpdata/RFIC/rfic_share/)(.+?)/query_output",
            "[[output_dir]]/query_output",
        ),
    ],
    "layer_map_file": [
        _simple(
            "output_dir",
            r"(?!/tmpdata/RFIC/rfic_share/)(.+?)/query_output/Design\.gds\.map",
            "[[output_dir]]/query_output/Design.gds.map",
        ),
    ],
    "device_properties_file": [
        _simple(
            "output_dir",
            r"(?!/tmpdata/RFIC/rfic_share/)(.+?)/query_output/Design\.props",
            "[[output_dir]]/query_output/Design.props",
        ),
    ],
    "view_name": [_simple("out_file", r"(.+?)", "[[out_file]]")],
}


# Pre-pass: substitute employee_id in the two known path shapes that
# carry it. Applied to the raw source *before* rule matching so identity
# rules see stable prefixes.
#
# - ``/tmpdata/RFIC/rfic_share/<id>/`` → ``.../[[employee_id]]/`` (quantus)
# - ``/data/RFIC3/<project>/<employee>/`` → ``.../<project>/[[employee_id]]/``
#   (si's ``incFILE``). The project segment stays raw so the PdkToken
#   detector can still surface it as ``project_subdir``.
#
# Both regexes require a trailing ``/`` so "bar" in ``/data/RFIC3/foo/bar}``
# (no trailing slash) is not mis-substituted.
_EMPLOYEE_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(/tmpdata/RFIC/rfic_share/)([^/\s\"']+)(/)"),
    re.compile(r"(/data/RFIC3/[^/\s\"']+/)([^/\s\"']+)(/)"),
)


def _preprocess_employee_id(text: str) -> str:
    for pattern in _EMPLOYEE_ID_PATTERNS:
        text = pattern.sub(r"\1[[employee_id]]\3", text)
    return text


def _import_quantus(raw: str, overrides: Identity | None) -> ImportResult:
    preprocessed = _preprocess_employee_id(raw)
    identity, body, _aux = _apply_rules(
        "quantus", preprocessed, _QUANTUS_LINE_RE, _QUANTUS_RULES, overrides
    )
    return ImportResult(tool="quantus", identity=identity, template_body=body)


# Jivaro XML uses self-closing ``<key value="..."/>`` tags. The tag name
# is the "key"; its ``value`` attribute is what we substitute.
_JIVARO_LINE_RE = re.compile(
    r'^(?P<prefix>\s*<(?P<key>[A-Za-z_]\w*)\s+value=")(?P<value>[^"\r\n]*)'
    r'(?P<tail>"\s*/>\s*(?:\r?\n)?)$'
)

# frequencyLimit / errorMax stash their raw value into the Jinja
# ``default(...)`` filter so a rendered template without
# ``task.jivaro.frequency_limit`` set still produces byte-for-byte the
# same output as the raw source. The ``\1`` backreference is the raw
# value, interpolated via :meth:`re.Match.expand`.
_JIVARO_RULES: dict[str, list[_Rule]] = {
    "inputView": [
        _Rule(
            fields=(("library", 1), ("cell", 2), ("out_file", 3)),
            value_pattern=re.compile(r"([^/]+)/([^/]+)/([^/]+)"),
            replacement="[[library]]/[[cell]]/[[out_file]]",
        )
    ],
    "outputView": [_simple("out_file", r"(.+?)_red", "[[out_file]]_red")],
    "frequencyLimit": [
        _simple(
            "jivaro_frequency_limit",
            r"(.+?)",
            r"[[jivaro_frequency_limit | default(\1)]]",
        )
    ],
    "errorMax": [
        _simple(
            "jivaro_error_max",
            r"(.+?)",
            r"[[jivaro_error_max | default(\1)]]",
        )
    ],
}


def _import_jivaro(raw: str, overrides: Identity | None) -> ImportResult:
    preprocessed = _preprocess_employee_id(raw)
    identity, body, _aux = _apply_rules(
        "jivaro", preprocessed, _JIVARO_LINE_RE, _JIVARO_RULES, overrides
    )
    return ImportResult(tool="jivaro", identity=identity, template_body=body)


# ---- candidate knob detection ----------------------------------------------


# Key-name substrings that flip 0/1 integer literals from ``int`` to ``bool``.
# Heuristic only — surfaces with ``confidence="medium"`` so users can
# override via ``knob promote --type int``.
_BOOL_HEURISTIC_TOKENS: tuple[str, ...] = (
    "Enable",
    "Disable",
    "Run",
    "Use",
    "Abort",
    "Connect",
    "Show",
    "Warn",
    "Release",
    "Specify",
    "Hyper",
)

_INT_VALUE_RE = re.compile(r"-?\d+")
_FLOAT_VALUE_RE = re.compile(r"-?\d+\.\d+")
_QUOTED_VALUE_RE = re.compile(r'"([^"]*)"')

# Per-tool patterns for option-style lines scanned after identity
# substitution. The ``quantus`` pattern only handles the common
# same-line ``-option value`` form — continuation-lined values
# (``-temperature \\\n              55.0``) are missed by design;
# users can promote them manually with ``knob promote`` if needed.
_CAND_PATTERNS: dict[TOOL, re.Pattern[str]] = {
    "calibre": re.compile(r"^\*(?P<key>\w+):\s+(?P<value>.*?)\s*$"),
    "si": re.compile(r"^(?P<key>\w+)\s*=\s*(?P<value>.*?)\s*$"),
    "quantus": re.compile(r"-(?P<key>\w+)\s+(?P<value>\"[^\"]*\"|\S+)"),
    "jivaro": re.compile(r'<(?P<key>\w+)\s+value="(?P<value>[^"]*)"\s*/>'),
}


def _snake_case(name: str) -> str:
    """camelCase / PascalCase → snake_case (``cmnNumTurbo`` → ``cmn_num_turbo``)."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", s)
    return s.lower()


def _classify_value(
    key: str, value: str
) -> tuple[Literal["int", "float", "str", "bool"], Any, Literal["high", "medium", "low"]] | None:
    """Classify a raw literal as ``(type, default, confidence)``.

    Returns ``None`` if ``value`` is already a Jinja placeholder, a
    complex structure (SKILL list, Tcl brace), or otherwise unsuitable
    for a knob.
    """
    if "[[" in value or "]]" in value:
        return None

    # Quoted strings: treat as str. Uses fullmatch to avoid e.g.
    # ``"$env(X)"`` — which contains complex markers — being surfaced.
    qm = _QUOTED_VALUE_RE.fullmatch(value)
    if qm is not None:
        inner = qm.group(1)
        if any(c in inner for c in "${}()[]\\"):
            return None
        return "str", inner, "high"

    # Reject anything with complex-structure markers.
    if any(c in value for c in "\"'(){}[]\\"):
        return None

    # Float literal.
    if _FLOAT_VALUE_RE.fullmatch(value):
        return "float", float(value), "high"

    # Integer literal, with bool-heuristic for 0/1 on toggle-style keys.
    if _INT_VALUE_RE.fullmatch(value):
        ival = int(value)
        if ival in (0, 1) and any(tok in key for tok in _BOOL_HEURISTIC_TOKENS):
            return "bool", bool(ival), "medium"
        return "int", ival, "high"

    return None


def _detect_candidates(tool: TOOL, body: str) -> list[Candidate]:
    """Scan a post-identity-substitution body for knob candidates.

    One :class:`Candidate` per unique raw-file key; the first occurrence
    determines the default and line number. Multiple occurrences with
    differing values are a red flag the user should review, but surfacing
    only the first one keeps the suggest table readable.
    """
    pattern = _CAND_PATTERNS[tool]
    seen: dict[str, Candidate] = {}
    for line_no, line in enumerate(body.splitlines(), start=1):
        m = pattern.search(line)
        if m is None:
            continue
        key = m.group("key")
        if key in seen:
            continue
        value = m.group("value")
        cls = _classify_value(key, value)
        if cls is None:
            continue
        type_, default, confidence = cls
        seen[key] = Candidate(
            key=key,
            suggested_name=_snake_case(key),
            type=type_,
            default=default,
            line=line_no,
            confidence=confidence,
        )
    return list(seen.values())


# ---- PdkToken detection ----------------------------------------------------


# Per-file hardcoded-value survey. The Phase 5.6.5 simplification keeps
# only:
#
# - ``tech_name``   : ``HN<ALNUM>+`` anywhere (Cadence tech library prefix);
#                     promoted by :func:`aggregate_pdk_tokens` from quantus.
# - ``abs_path``    : a ``/tmpdata/RFIC/rfic_share/<id>/`` prefix that was
#                     *not* rewritten to ``[[employee_id]]`` by the quantus
#                     pre-pass; always unclassified for human review.
#
# pdk_subdir / runset_version / project_subdir went away when the project
# schema replaced the per-segment fields with whole-path entries under
# ``project.paths``; the importer now extracts those paths directly from
# anchor lines (``*lvsRulesFile`` / ``*lvsPostTriggers`` /
# ``-parasitic_blocking_device_cells_file``) rather than from regex
# searches over the body.
_PDK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "tech_name",
        re.compile(r"(?<![A-Za-z0-9_])HN[A-Za-z0-9_.]+?(?=[/\s\"]|$)"),
    ),
    ("abs_path", re.compile(r"/tmpdata/RFIC/rfic_share/(?P<name>[^/\s\"]+)/")),
)


def _detect_pdk_tokens(body: str) -> list[PdkToken]:
    """Scan ``body`` for hardcoded PDK-level values.

    Emits one :class:`PdkToken` per regex hit. Matches that reduce to
    ``[[employee_id]]`` after the per-tool pre-pass are skipped so the
    substituted placeholder is not flagged.
    """
    tokens: list[PdkToken] = []
    for line_no, line in enumerate(body.splitlines(), start=1):
        for category, pattern in _PDK_PATTERNS:
            for m in pattern.finditer(line):
                if category == "abs_path":
                    name = m.group("name")
                    if name == "[[employee_id]]":
                        continue
                    value = m.group(0)
                else:
                    value = m.group(0)
                tokens.append(PdkToken(value=value, category=category, line=line_no))
    tokens.sort(key=lambda t: (t.line, t.category, t.value))
    return tokens


# ---- smart re-import merge -------------------------------------------------


def _substitute_at_key(
    tool: TOOL, body: str, key: str, replacement: str
) -> tuple[str, str | None]:
    """Find the first ``key`` line in ``body``, swap its value for
    ``replacement``. Returns ``(new_body, raw_literal)``; raw_literal is
    ``None`` if the key is absent or already contains a ``[[...]]``
    placeholder.
    """
    pattern = _CAND_PATTERNS[tool]
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        for m in pattern.finditer(line):
            if m.group("key") != key:
                continue
            literal = m.group("value")
            if "[[" in literal:
                return body, None
            new_line = (
                line[: m.start("value")] + replacement + line[m.end("value") :]
            )
            lines[i] = new_line
            return "".join(lines), literal
    return body, None


def _coerce_literal(literal: str, spec_type: str) -> Any:
    """Coerce a raw-file literal (always a string) to the knob's type."""
    if spec_type == "int":
        return int(literal)
    if spec_type == "float":
        return float(literal)
    if spec_type == "str":
        if len(literal) >= 2 and literal[0] == '"' and literal[-1] == '"':
            return literal[1:-1]
        return literal
    if spec_type == "bool":
        if literal in ("1", "true", "True", "yes"):
            return True
        if literal in ("0", "false", "False", "no"):
            return False
        raise ValueError(f"cannot parse {literal!r} as bool")
    raise ValueError(f"unknown knob type {spec_type!r}")


@dataclass
class MergeOutcome:
    """Result of re-applying a Phase 4a manifest onto a fresh import.

    ``body`` is the new template body with user-promoted knobs
    re-substituted. ``manifest`` has knob defaults refreshed from the raw
    where the source key still exists. ``messages`` is a human-readable
    log of merge decisions for the CLI to print.
    """

    body: str
    manifest: TemplateManifest
    messages: list[str] = field(default_factory=list)


def merge_reimport(
    new_result: ImportResult,
    existing_manifest: TemplateManifest,
) -> MergeOutcome:
    """Re-apply user-promoted knobs from ``existing_manifest`` to a fresh
    :class:`ImportResult`.

    Policy:
      - Knobs with ``source`` set → re-substitute their raw-file key in
        the new body, refresh ``default`` from the new raw literal.
      - Knobs without ``source`` (user-authored) → left untouched; they
        are not tracked in the raw file and the importer must not
        invent a source for them.
      - If a source key has disappeared from the new raw, the knob
        reference is kept (still valid Jinja) but the default is stale;
        a warning surfaces in :attr:`MergeOutcome.messages`.
      - Manifest-level edits (description, range, unit) round-trip
        unchanged because we only ever update ``default``.
    """

    messages: list[str] = []
    body = new_result.template_body
    merged_knobs: dict[str, KnobSpec] = {}

    for knob_name, spec in existing_manifest.knobs.items():
        if spec.source is None:
            merged_knobs[knob_name] = spec
            messages.append(
                f"{knob_name}: user-defined knob (no source); template body untouched"
            )
            continue

        if spec.source.tool != new_result.tool:
            merged_knobs[knob_name] = spec
            messages.append(
                f"{knob_name}: source.tool={spec.source.tool} does not match "
                f"import tool {new_result.tool}; skipped"
            )
            continue

        body, literal = _substitute_at_key(
            new_result.tool, body, spec.source.key, f"[[{knob_name}]]"
        )
        if literal is None:
            merged_knobs[knob_name] = spec
            messages.append(
                f"{knob_name}: source key {spec.source.key!r} not found in "
                "new raw; default is now stale"
            )
            continue

        try:
            new_default = _coerce_literal(literal, spec.type)
        except ValueError as exc:
            merged_knobs[knob_name] = spec
            messages.append(
                f"{knob_name}: cannot coerce {literal!r} to {spec.type}: {exc}"
            )
            continue

        if new_default != spec.default:
            try:
                merged = KnobSpec.model_validate(
                    {**spec.model_dump(), "default": new_default}
                )
            except ValidationError as exc:
                merged_knobs[knob_name] = spec
                messages.append(
                    f"{knob_name}: new default {new_default!r} rejected by "
                    f"schema (kept {spec.default!r}): {exc}"
                )
                continue
            merged_knobs[knob_name] = merged
            messages.append(
                f"{knob_name}: default updated {spec.default!r} → {new_default!r}"
            )
        else:
            merged_knobs[knob_name] = spec

    merged_manifest = existing_manifest.model_copy(update={"knobs": merged_knobs})
    return MergeOutcome(body=body, manifest=merged_manifest, messages=messages)


# ---- cross-file PDK aggregation (Phase 4b2) --------------------------------


@dataclass(frozen=True)
class UnclassifiedToken:
    """A :class:`PdkToken` that :func:`aggregate_pdk_tokens` did not
    promote. Carries the originating tool so the review report can cite
    which raw file the token came from.
    """

    tool: TOOL
    token: PdkToken


@dataclass(frozen=True)
class ProjectConstants:
    """Cross-file PDK constants inferred from a set of :class:`ImportResult`.

    Populated by :func:`aggregate_pdk_tokens`. Phase 5.6.5 collapsed the
    five segment fields (``pdk_subdir`` / ``project_subdir`` /
    ``runset_versions.{lvs,qrc}``) into a single ``paths`` dict whose
    canonical entries are ``calibre_lvs_dir`` and ``qrc_deck_dir`` — the
    whole-path values referenced directly by ``[[<key>]]`` in templates.

    ``unclassified`` surfaces hardcoded values that couldn't be confidently
    promoted (e.g. cross-tool conflicts on the QRC deck dir, ``abs_path``
    leaks); the review report renders them as-is for user review.
    """

    tech_name: str | None = None
    paths: dict[str, str] = field(default_factory=dict)
    unclassified: tuple[UnclassifiedToken, ...] = ()


# ---- path extraction (calibre + quantus anchor lines) ----------------------


# *lvsRulesFile: <path>/<basename>.<variant>.qcilvs
# Capture the dirname (everything before the last "/") and the basename.
# We assume the calibre per-tool importer hasn't touched this line — the
# rules file value is not an identity field.
_CALIBRE_RULES_FILE_RE = re.compile(
    r"^\*lvsRulesFile:\s+(?P<dir>\S+)/(?P<basename>[^/\s]+?)\.[^/.\s]+\.qcilvs\s*$",
    re.MULTILINE,
)
# *lvsPostTriggers: ... -query_input <path>/query_cmd ...
_CALIBRE_QUERY_CMD_RE = re.compile(
    r"-query_input\s+(?P<dir>\S+)/query_cmd"
)
# Quantus: -parasitic_blocking_device_cells_file "<path>/preserveCellList.txt"
_QUANTUS_PRESERVE_RE = re.compile(
    r'-parasitic_blocking_device_cells_file\s+"(?P<dir>[^"]+)/preserveCellList\.txt"'
)


def _extract_calibre_lvs_dir(body: str) -> str | None:
    m = _CALIBRE_RULES_FILE_RE.search(body)
    return m.group("dir") if m else None


def _extract_calibre_lvs_basename(body: str) -> str | None:
    m = _CALIBRE_RULES_FILE_RE.search(body)
    return m.group("basename") if m else None


def _extract_qrc_deck_from_calibre(body: str) -> str | None:
    m = _CALIBRE_QUERY_CMD_RE.search(body)
    return m.group("dir") if m else None


def _extract_qrc_deck_from_quantus(body: str) -> str | None:
    m = _QUANTUS_PRESERVE_RE.search(body)
    return m.group("dir") if m else None


# ``$env(VERIFY_ROOT)`` (Tcl) and ``$VERIFY_ROOT`` / ``${VERIFY_ROOT}`` (shell)
# all denote the same env var; the cross-check between calibre and quantus
# qrc_deck_dir candidates needs to treat them as equivalent. Normalize all
# three forms to the bare ``$NAME`` shape before comparison + emission.
_ENV_NORMALIZE_TCL = re.compile(r"\$env\(([A-Za-z_][A-Za-z0-9_]*)\)")
_ENV_NORMALIZE_BRACE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _canonicalize_env_refs(value: str) -> str:
    value = _ENV_NORMALIZE_TCL.sub(r"$\1", value)
    value = _ENV_NORMALIZE_BRACE.sub(r"$\1", value)
    return value


def aggregate_pdk_tokens(
    results: dict[TOOL, ImportResult],
) -> ProjectConstants:
    """Cross-reference :class:`PdkToken`\\ s + per-tool anchor lines.

    Promotion policy (Phase 5.6.5):

    - ``tech_name``: first ``HN...`` token from quantus. Non-quantus
      hits → ``unclassified``.
    - ``paths.calibre_lvs_dir``: dirname of ``*lvsRulesFile`` in the
      calibre raw. Not extracted from any other tool.
    - ``paths.qrc_deck_dir``: dirname of the ``query_cmd`` argument in
      calibre's ``*lvsPostTriggers`` AND/OR dirname of quantus's
      ``-parasitic_blocking_device_cells_file``. If both are present and
      disagree, neither is promoted and both values land in
      ``unclassified``.
    - ``abs_path`` / ``unknown`` PdkTokens are always unclassified —
      surfacing absolute paths the user must review, never auto-substituted.
    """

    unclassified: list[UnclassifiedToken] = []

    # ---- tech_name --------------------------------------------------------
    tech_name: str | None = None
    for tool, result in results.items():
        for tok in result.pdk_tokens:
            if tok.category != "tech_name":
                continue
            if tool == "quantus":
                if tech_name is None:
                    tech_name = tok.value
                elif tok.value != tech_name:
                    unclassified.append(UnclassifiedToken(tool=tool, token=tok))
                # else: duplicate of promoted value, silently absorb.
            else:
                unclassified.append(UnclassifiedToken(tool=tool, token=tok))

    # ---- paths.calibre_lvs_dir -------------------------------------------
    paths: dict[str, str] = {}
    if "calibre" in results:
        d = _extract_calibre_lvs_dir(results["calibre"].template_body)
        if d:
            paths["calibre_lvs_dir"] = _canonicalize_env_refs(d)

    # ---- paths.qrc_deck_dir (calibre + quantus, with cross-check) --------
    qrc_from_calibre = (
        _extract_qrc_deck_from_calibre(results["calibre"].template_body)
        if "calibre" in results
        else None
    )
    qrc_from_quantus = (
        _extract_qrc_deck_from_quantus(results["quantus"].template_body)
        if "quantus" in results
        else None
    )
    qrc_calibre_norm = _canonicalize_env_refs(qrc_from_calibre) if qrc_from_calibre else None
    qrc_quantus_norm = _canonicalize_env_refs(qrc_from_quantus) if qrc_from_quantus else None
    if qrc_calibre_norm and qrc_quantus_norm:
        if qrc_calibre_norm == qrc_quantus_norm:
            paths["qrc_deck_dir"] = qrc_calibre_norm
        else:
            unclassified.append(
                UnclassifiedToken(
                    tool="calibre",
                    token=PdkToken(
                        value=qrc_calibre_norm, category="unknown", line=0
                    ),
                )
            )
            unclassified.append(
                UnclassifiedToken(
                    tool="quantus",
                    token=PdkToken(
                        value=qrc_quantus_norm, category="unknown", line=0
                    ),
                )
            )
    elif qrc_calibre_norm:
        paths["qrc_deck_dir"] = qrc_calibre_norm
    elif qrc_quantus_norm:
        paths["qrc_deck_dir"] = qrc_quantus_norm

    # ---- abs_path / unknown PdkTokens always unclassified ----------------
    for tool, result in results.items():
        for tok in result.pdk_tokens:
            if tok.category in ("abs_path", "unknown"):
                unclassified.append(UnclassifiedToken(tool=tool, token=tok))

    # Deterministic report order: by tool, then line, then value.
    unclassified.sort(key=lambda u: (u.tool, u.token.line, u.token.value))

    return ProjectConstants(
        tech_name=tech_name,
        paths=paths,
        unclassified=tuple(unclassified),
    )


# ---- body rewrite (apply ProjectConstants to a template body) -------------


def _compile_path_substitution_re(canonical: str) -> re.Pattern[str]:
    """Build a regex that matches every env-var-form variant of ``canonical``.

    A canonical path value uses the bare ``$NAME`` form; the body it's
    substituted into may use ``$NAME``, ``${NAME}``, or ``$env(NAME)`` for
    the same env var. The compiled regex tolerates any of those forms at
    each ``$NAME`` position while keeping the literal segments (and
    non-identifier boundary anchors) intact.
    """
    parts: list[str] = []
    last = 0
    for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_]*)", canonical):
        parts.append(re.escape(canonical[last : m.start()]))
        name = m.group(1)
        parts.append(rf"(?:\${name}|\${{{name}}}|\$env\({name}\))")
        last = m.end()
    parts.append(re.escape(canonical[last:]))
    return re.compile(
        r"(?<![A-Za-z0-9_])" + "".join(parts) + r"(?![A-Za-z0-9_])"
    )


def apply_project_constants(
    tool: TOOL, body: str, constants: ProjectConstants
) -> str:
    """Substitute promoted PDK constants in ``body`` with Jinja placeholders.

    Phase 5.6.5 substitutions:

    - ``tech_name`` → ``[[tech_name]]`` (any tool body that contains it;
      typically only quantus carries ``HN...``).
    - ``paths.calibre_lvs_dir`` → ``[[calibre_lvs_dir]]`` (calibre body only).
      The basename portion of the rules-file line is also rewritten to
      ``[[calibre_lvs_basename]]`` so the imported template matches the
      bundled production calibre template's shape.
    - ``paths.qrc_deck_dir`` → ``[[qrc_deck_dir]]`` (calibre + quantus only).

    Path values stored in ``constants.paths`` are in canonical ``$NAME``
    form. The body substitution accepts any equivalent env-var form
    (``$NAME`` / ``${NAME}`` / ``$env(NAME)``) at each position, so a
    calibre body with ``$VERIFY_ROOT/...`` and a quantus body with
    ``$env(VERIFY_ROOT)/...`` both get rewritten correctly.

    Boundary anchors guard against substring overshoot (e.g. ``/q/dir``
    inside ``/q/dir_extra`` stays intact). Order of application is
    descending canonical-value length so a short value cannot pre-empt a
    longer one that contains it.
    """
    substitutions: list[tuple[str, str]] = []
    if constants.tech_name:
        substitutions.append((constants.tech_name, "[[tech_name]]"))
    if tool == "calibre":
        # Rewrite the rules-file basename inline. We do this before path
        # substitutions so the rules-file dirname stays as a single token
        # for the calibre_lvs_dir replacement.
        basename = _extract_calibre_lvs_basename(body)
        if basename:
            body = re.sub(
                r"(\*lvsRulesFile:\s+\S*?)"
                + re.escape(basename)
                + r"(\.[^/.\s]+\.qcilvs)",
                r"\1[[calibre_lvs_basename]]\2",
                body,
            )
        d = constants.paths.get("calibre_lvs_dir")
        if d:
            substitutions.append((d, "[[calibre_lvs_dir]]"))
    if tool in ("calibre", "quantus"):
        d = constants.paths.get("qrc_deck_dir")
        if d:
            substitutions.append((d, "[[qrc_deck_dir]]"))

    substitutions.sort(key=lambda p: -len(p[0]))
    for raw, placeholder in substitutions:
        if "$" in raw:
            pattern = _compile_path_substitution_re(raw)
        else:
            pattern = re.compile(
                r"(?<![A-Za-z0-9_])" + re.escape(raw) + r"(?![A-Za-z0-9_])"
            )
        body = pattern.sub(placeholder, body)
    return body
