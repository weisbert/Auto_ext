"""Scan an environment into a draft :class:`~auto_ext.model.pdk.PdkProfile`.

The user should never have to fill a PDK profile in by hand on a machine
where the setup script has already been sourced -- every fact this module
needs is somewhere in ``os.environ`` or on the deck filesystem. What it
cannot see, it leaves **empty** and explains, as a :class:`DiscoveryNote`
naming the field, the rule that failed, and what the human has to do::

    result = discover_profile(profile_id="hn001")
    write_profile_yaml(Path("config/profiles/hn001.yaml"), result.profile)
    for note in result.notes:
        print(note.field, "->", note.fix_hint)

Nothing here invents a value. An unreachable deck directory yields
``lvs_decks.dir_expr = None`` and an empty variant table, not ``wodio``; an
unreadable ``assura_tech.lib`` yields ``corners = []``, not ``TYPICAL``. A
guessed default would be indistinguishable from a discovered one three weeks
later, and would send a run at the wrong deck without complaining.

UNVERIFIED SCAN RULES
=====================

We have exactly one real sample of a deck layout: two path lines in
``docs/calibre_raw.txt``. Every rule below that is not marked "observed" is a
hypothesis, and all of them live in :data:`SCAN_RULES` so a single edit fixes
them once the answers come back from the office (see
``docs/refactor/OFFICE_TODO.md``, items 2 and 3).

``R1``-``R8`` are quoted verbatim in :data:`SCAN_RULES`; each note that a scan
emits carries the id of the rule that could not be applied, so a report can
point at exactly which assumption did not hold.
"""

from __future__ import annotations

import io
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from auto_ext.core.env import (
    EnvResolution,
    derive_parent_dir_from_env_candidates,
    discover_required_vars,
    resolve_env,
    resolve_path_expr,
)
from auto_ext.core.errors import ConfigError
from auto_ext.model.pdk import (
    DEFAULT_CDL_INCLUDE_FILE,
    DEFAULT_LAYER_MAP,
    DEFAULT_TECH_LIBRARY_FILE,
    DEFAULT_TECH_NAME_ENV_VARS,
    PDK_PROFILE_SCHEMA_VERSION,
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    PdkProfile,
    QrcDeck,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BASE_ENV_CANDIDATES",
    "CALIBRE_LVS_DIR_EXPR",
    "DiscoveryNote",
    "DiscoveryResult",
    "SCAN_RULES",
    "discover_profile",
    "profile_to_yaml",
    "read_profile_yaml",
    "write_profile_yaml",
]


#: Every assumption this module makes about how a PDK lays its decks out.
#: Change these strings and the corresponding constant / regex below them;
#: nothing else in the codebase encodes deck layout.
SCAN_RULES: dict[str, str] = {
    "R1": (
        "observed: the PDK setup exports $calibre_source_added_place pointing at a file "
        "inside the Calibre LVS deck directory, so the deck directory is "
        "'$calibre_source_added_place|parent' (config/project.yaml says so in prose, and "
        "that is the value shipped as paths.calibre_lvs_dir)."
    ),
    "R2": (
        "observed: the rules-file basename equals the last segment of the deck directory "
        "('.../CFXXX/CFXXX.wodio.qcilvs'). This is what runner._build_context already "
        "derives; docs/calibre_raw.txt line 2 is the only sample."
    ),
    "R3": (
        "observed: deck files are named '<basename>.<variant>.qcilvs'. CONFIRMED "
        "2026-08-24 on a real deck directory: BOTH 'wodio' and 'widio' exist as files, "
        "so widio is no longer manifest-only folklore. Each variant also ships a '.lvs' "
        "sibling next to the '.qcilvs'; we want the '.qcilvs'. Variants stay globbed "
        "rather than assumed -- a third suffix on another PDK would still be found."
    ),
    "R4": (
        "observed: the path segment immediately after a segment named 'LVS' (resp. "
        "'QRC') is the runset version ('Ver_LVS_A', resp. 'Ver_QRC_B'). CONFIRMED "
        "2026-08-24 on the real tree, where the two versions are indeed unrelated "
        "strings. Provenance only -- no path is ever assembled from it."
    ),
    "R5": (
        "observed-and-corrected: the QRC deck sits at "
        "'$VERIFY_ROOT/runset/Calibre_QRC/QRC/<version>/<pdk_subdir>/QCI_deck', one "
        "directory deeper than the LVS deck. The 'same <pdk_subdir> as the LVS deck' "
        "half of this rule was FALSIFIED 2026-08-24: on the real tree the LVS subdir "
        "names the LVS deck release and the QRC subdir names the QRC deck release, and "
        "they share no substring beyond the PDK prefix. Pinning the glob to the LVS "
        "basename therefore matched nothing. Both segments are now wildcards, with the "
        "LVS-pinned glob kept only as a first, narrowing attempt for PDKs where the "
        "names do line up. Only a unique hit is accepted; several matches means a human "
        "must choose -- which is the normal outcome, since shipping two QRC deck "
        "releases side by side is itself normal."
    ),
    "R6": (
        "FALSIFIED as a discovery source, kept as a best-effort: corner names were "
        "assumed to appear in assura_tech.lib as a quoted token following a word "
        "containing 'corner' (techCorner( \"TYPICAL\" )), or as an unquoted upper-case "
        "token after 'corner ='. On the first real assura_tech.lib anyone has looked at "
        "(2026-08-24), `grep -i corner` returns NOTHING -- the corner list lives in the "
        "Quantus RuleSet, which is a GUI list this scanner cannot read. The parse still "
        "runs because another PDK may well carry the literals, but the fix hint now "
        "sends the user to the RuleSet, not to a grep that is known to come back empty. "
        "When the pattern misses, the corner table stays empty rather than invented."
    ),
    "R7": (
        "observed: a raw Calibre runset export carries the global supply lists as the "
        "'*lvsPowerNames:' and '*lvsGroundNames:' lines, whitespace-separated "
        "(docs/calibre_raw.txt lines 17-18)."
    ),
    "R8": (
        "observed: tech_name is the parent directory name of the first resolvable "
        "candidate in tech_name_env_vars -- unchanged from "
        "env.derive_parent_dir_from_env_candidates."
    ),
}

#: R1. The path expression for the Calibre LVS deck directory.
CALIBRE_LVS_DIR_EXPR = "$calibre_source_added_place|parent"

#: R5. Where to look for the QRC deck, as a filesystem globs relative to
#: ``$VERIFY_ROOT``, tried narrowest first.
#:
#: The narrow one pins the PDK subdirectory to the LVS deck basename. That was
#: the ONLY glob until 2026-08-24, when a real tree showed the two subdirectory
#: names are independent deck releases -- the LVS one names an LVS deck, the QRC
#: one names a QRC deck -- so the pinned glob matched nothing and the scan gave
#: up on a directory that was sitting right there. It is kept because where the
#: names DO line up it disambiguates for free; the wide glob is the fallback.
QRC_DECK_GLOB = "runset/Calibre_QRC/QRC/*/{pdk_subdir}/QCI_deck"
QRC_DECK_GLOB_WIDE = "runset/Calibre_QRC/QRC/*/*/QCI_deck"

#: Env vars a PDK profile is expected to need. They are always listed in
#: ``required_env`` even when the scan cannot resolve them -- that is exactly
#: when the user needs to see them flagged.
BASE_ENV_CANDIDATES: tuple[str, ...] = (
    "SETUP_ROOT",
    "VERIFY_ROOT",
    "calibre_source_added_place",
    "PDK_LAYER_MAP_FILE",
)

#: R3. ``<basename>.<variant>.qcilvs``; group 1 is the variant.
_VARIANT_RE_TEMPLATE = r"^{basename}\.([^.]+)\.qcilvs$"

#: R4. Version segment lookup: the segment after ``LVS`` / ``QRC``.
_VERSION_ANCHORS = ("LVS", "QRC")

#: R6. Two conservative shapes; anything looser produces false corners out of
#: prose like "corner extraction". No ``\b`` before ``corner``: the key is
#: spelled ``techCorner`` in the Cadence files whose shape we know, and a word
#: boundary would never match that.
_CORNER_RES = (
    re.compile(r'(?i)corner\w*\s*[:=(]?\s*"([A-Za-z][\w.+-]*)"'),
    re.compile(r"(?i)corner\w*\s*[:=]\s*([A-Z][A-Z0-9_]*)\b"),
)
#: Cap on how many corners one file may contribute, so a pathological match
#: cannot fill the profile with noise.
_MAX_CORNERS = 32

#: R7. The two supply lines of a raw Calibre runset export.
_POWER_RE = re.compile(r"^\*lvsPowerNames:\s*(.+)$", re.MULTILINE)
_GROUND_RE = re.compile(r"^\*lvsGroundNames:\s*(.+)$", re.MULTILINE)

#: The literal the Quantus templates hardcode today. When the scan finds a
#: corner spelled like this it becomes ``default_corner``, which keeps a
#: migration byte-neutral; no other corner is ever promoted automatically.
_LEGACY_DEFAULT_CORNER = "TYPICAL"

_SLUG_STRIP = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True)
class DiscoveryNote:
    """One thing the scan could not determine, and what to do about it."""

    #: Dotted profile field that stayed empty, e.g. ``lvs_decks.variants``.
    field: str
    #: Key into :data:`SCAN_RULES` -- which assumption did not hold.
    rule: str
    #: What was actually observed.
    reason: str
    #: Concrete next step for a human. English, names a command or a field.
    fix_hint: str


@dataclass(frozen=True)
class DiscoveryResult:
    """A draft profile plus everything the scan could not answer."""

    profile: PdkProfile
    notes: list[DiscoveryNote] = field(default_factory=list)
    #: Paths and inputs that were actually looked at, for provenance.
    scanned: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """No field was left for a human to fill in."""

        return not self.notes

    def note_for(self, field_name: str) -> DiscoveryNote | None:
        for n in self.notes:
            if n.field == field_name:
                return n
        return None


# ---- scanning ----------------------------------------------------------------


def discover_profile(
    *,
    profile_id: str = "default",
    display_name: str | None = None,
    resolution: EnvResolution | None = None,
    env_overrides: Mapping[str, str] | None = None,
    raw_calibre_text: str | None = None,
    use_filesystem: bool = True,
) -> DiscoveryResult:
    """Scan this machine and return a draft profile plus its gaps.

    ``resolution`` defaults to resolving :data:`BASE_ENV_CANDIDATES` plus the
    ``tech_name`` candidates against the current shell; pass one in to scan a
    hypothetical environment. ``raw_calibre_text`` is the text of a real
    Calibre runset export (the shape of ``docs/calibre_raw.txt``) and is the
    only source for the global power / ground name lists. ``use_filesystem``
    turns off every directory listing, which is what a unit test that only
    cares about env handling wants.

    Never raises for a missing anything: an empty environment produces a valid
    profile with every optional field unset and one note per gap.
    """

    overrides = dict(env_overrides or {})
    if resolution is None:
        resolution = resolve_env(
            set(BASE_ENV_CANDIDATES) | set(DEFAULT_TECH_NAME_ENV_VARS), overrides
        )
    notes: list[DiscoveryNote] = []
    scanned: list[str] = []

    tech_name = _scan_tech_name(resolution, notes)
    lvs_decks, lvs_dir_resolved = _scan_lvs_decks(
        resolution, notes, scanned, use_filesystem=use_filesystem
    )
    qrc = _scan_qrc_deck(
        resolution,
        notes,
        scanned,
        pdk_subdir=lvs_decks.basename,
        use_filesystem=use_filesystem,
    )
    corners, default_corner = _scan_corners(
        resolution, notes, scanned, use_filesystem=use_filesystem
    )
    power_names, ground_names = _scan_supply_names(raw_calibre_text, notes, scanned)

    profile = PdkProfile(
        schema_version=PDK_PROFILE_SCHEMA_VERSION,
        profile_id=_slug(profile_id),
        # Never empty: display_name is required, and _slug always returns
        # something, so a caller passing profile_id="" still gets a profile.
        display_name=display_name or tech_name or _slug(profile_id),
        tech_name=tech_name,
        env_overrides=overrides,
        required_env=_required_env(lvs_decks, qrc),
        lvs_decks=lvs_decks,
        qrc=qrc,
        corners=corners,
        default_corner=default_corner,
        power_names=power_names,
        ground_names=ground_names,
        discovered_from=scanned,
    )
    logger.info(
        "discovered profile %s: %d corner(s), %d lvs variant(s), %d open note(s)",
        profile.profile_id,
        len(profile.corners),
        len(profile.lvs_decks.variants),
        len(notes),
    )
    if lvs_dir_resolved:
        logger.debug("lvs deck directory resolved to %s", lvs_dir_resolved)
    return DiscoveryResult(profile=profile, notes=notes, scanned=scanned)


def _scan_tech_name(resolution: EnvResolution, notes: list[DiscoveryNote]) -> str | None:
    """R8."""

    candidates = list(DEFAULT_TECH_NAME_ENV_VARS)
    name = derive_parent_dir_from_env_candidates(candidates, resolution.resolved)
    if name is None:
        notes.append(
            DiscoveryNote(
                field="tech_name",
                rule="R8",
                reason=f"none of {', '.join(candidates)} is set to a path with a parent",
                fix_hint=(
                    "Set `tech_name:` in the profile to the value Quantus expects for "
                    "-technology_name, or export one of the candidate variables and re-scan."
                ),
            )
        )
    return name


def _scan_lvs_decks(
    resolution: EnvResolution,
    notes: list[DiscoveryNote],
    scanned: list[str],
    *,
    use_filesystem: bool,
) -> tuple[LvsDeckSet, str | None]:
    """R1 / R2 / R3 / R4."""

    if _has_unresolved(CALIBRE_LVS_DIR_EXPR, resolution):
        notes.append(
            DiscoveryNote(
                field="lvs_decks.dir_expr",
                rule="R1",
                reason="$calibre_source_added_place is not set in this shell",
                fix_hint=(
                    "Source the PDK setup script and re-scan, or set `lvs_decks.dir_expr:` "
                    "to the directory holding <basename>.<variant>.qcilvs."
                ),
            )
        )
        return LvsDeckSet(), None

    resolved_dir = resolve_path_expr(CALIBRE_LVS_DIR_EXPR, resolution.resolved)
    scanned.append(resolved_dir)
    basename = PurePosixPath(resolved_dir).name or None
    version = _version_segment(resolved_dir, "LVS")
    variants: list[LvsDeckVariant] = []

    if use_filesystem and basename:
        variants = _glob_variants(Path(resolved_dir), basename)
    if not variants:
        notes.append(
            DiscoveryNote(
                field="lvs_decks.variants",
                rule="R3",
                reason=(
                    f"no file matching {basename or '<basename>'}.*.qcilvs under "
                    f"{resolved_dir}"
                    if use_filesystem
                    else "filesystem scanning was disabled"
                ),
                fix_hint=(
                    f"Run `ls {resolved_dir}` on the server and add one "
                    "`lvs_decks.variants:` entry per <basename>.<suffix>.qcilvs file "
                    "(name: is yours, rules_suffix: is the middle segment)."
                ),
            )
        )

    default_variant = variants[0].name if len(variants) == 1 else None
    if len(variants) > 1:
        notes.append(
            DiscoveryNote(
                field="lvs_decks.default_variant",
                rule="R3",
                reason=f"{len(variants)} variants found: {', '.join(v.name for v in variants)}",
                fix_hint=(
                    "Set `lvs_decks.default_variant:` to the one your group normally runs; "
                    "a Recipe may still override it per run."
                ),
            )
        )

    decks = LvsDeckSet(
        dir_expr=CALIBRE_LVS_DIR_EXPR,
        basename=basename,
        variants=variants,
        default_variant=default_variant,
        runset_version=version,
    )
    return decks, resolved_dir


def _glob_variants(deck_dir: Path, basename: str) -> list[LvsDeckVariant]:
    """R3. Every ``<basename>.<suffix>.qcilvs`` in ``deck_dir``, in name order."""

    pattern = re.compile(_VARIANT_RE_TEMPLATE.format(basename=re.escape(basename)))
    found: list[LvsDeckVariant] = []
    try:
        names = sorted(p.name for p in deck_dir.glob(f"{basename}.*.qcilvs"))
    except OSError as exc:
        logger.info("cannot list lvs deck dir %s: %s", deck_dir, exc)
        return []
    for name in names:
        m = pattern.match(name)
        if m is None:
            continue
        suffix = m.group(1)
        found.append(
            LvsDeckVariant(
                name=_slug(suffix),
                rules_suffix=suffix,
                description=f"discovered as {name}",
            )
        )
    return found


def _scan_qrc_deck(
    resolution: EnvResolution,
    notes: list[DiscoveryNote],
    scanned: list[str],
    *,
    pdk_subdir: str | None,
    use_filesystem: bool,
) -> QrcDeck:
    """R5. The QRC deck cannot be derived from the LVS deck -- only searched for.

    It carries an independent runset version and sits one directory deeper
    (``.../<pdk_subdir>/QCI_deck``), so ``|parent`` arithmetic on the LVS path
    produces a plausible-looking wrong answer. Hence: glob, accept only a
    unique hit, otherwise leave the field empty with the candidates listed.
    """

    def _give_up(reason: str, fix_hint: str) -> QrcDeck:
        notes.append(
            DiscoveryNote(field="qrc.dir_expr", rule="R5", reason=reason, fix_hint=fix_hint)
        )
        return QrcDeck()

    generic_hint = (
        "Run `ls $VERIFY_ROOT/runset/Calibre_QRC/QRC/*/*/QCI_deck` on the server and set "
        "`qrc.dir_expr:` to the directory that holds query_cmd. Its runset version is "
        "allowed to differ from the LVS deck's -- do not copy that one across."
    )
    if not use_filesystem:
        return _give_up("filesystem scanning was disabled", generic_hint)
    if pdk_subdir is None:
        return _give_up(
            "the LVS deck directory is unknown, so the PDK subdirectory name is too",
            generic_hint,
        )
    verify_root = resolution.resolved.get("VERIFY_ROOT", "")
    if not verify_root:
        return _give_up("$VERIFY_ROOT is not set in this shell", generic_hint)

    def _glob(pattern: str) -> list[str] | None:
        try:
            return sorted(str(p) for p in Path(verify_root).glob(pattern) if p.is_dir())
        except OSError as exc:
            notes.append(
                DiscoveryNote(
                    field="qrc.dir_expr",
                    rule="R5",
                    reason=f"cannot search under {verify_root}: {exc}",
                    fix_hint=generic_hint,
                )
            )
            return None

    # Narrow first: if this PDK happens to name the LVS and QRC subdirectories
    # alike, a single hit here settles the version with no question asked.
    pattern = QRC_DECK_GLOB.format(pdk_subdir=pdk_subdir)
    hits = _glob(pattern)
    if hits is None:
        return QrcDeck()

    if len(hits) != 1:
        # Widen. On the real tree the two subdirectory names are unrelated, so
        # the narrow glob returns nothing and everything depends on this.
        pattern = QRC_DECK_GLOB_WIDE
        wide = _glob(pattern)
        if wide is None:
            return QrcDeck()
        hits = wide

    if not hits:
        return _give_up(
            f"no directory matched {verify_root}/{pattern}",
            generic_hint,
        )
    if len(hits) > 1:
        return _give_up(
            f"{len(hits)} candidates matched, the runset version is ambiguous: "
            + ", ".join(hits),
            "Pick one of the matched directories and set `qrc.dir_expr:` to it: "
            + "; ".join(hits),
        )

    hit = Path(hits[0]).as_posix()
    scanned.append(hit)
    return QrcDeck(dir_expr=hit, runset_version=_version_segment(hit, "QRC"))


def _scan_corners(
    resolution: EnvResolution,
    notes: list[DiscoveryNote],
    scanned: list[str],
    *,
    use_filesystem: bool,
) -> tuple[list[CornerSpec], str | None]:
    """R6. Parse the technology library for corner literals."""

    expr = DEFAULT_TECH_LIBRARY_FILE
    fix_hint = (
        "Open the Quantus GUI and copy the RuleSet list -- that, not the technology "
        "library, is where this PDK's corner names actually live: on the first real "
        "assura_tech.lib anyone checked, `grep -i corner` came back empty. Add one "
        "`corners:` entry per RuleSet entry (name: is the semantic name a Recipe "
        "writes, technology_corner: is the literal Quantus expects). Expect ONE "
        "generation of naming, not both -- either the plain CBEST/CWORST/RCBEST/"
        "RCWORST family or the CBEST_CCBEST family, never the union. Until then no "
        "Recipe can name a corner."
    )

    def _give_up(reason: str) -> tuple[list[CornerSpec], str | None]:
        notes.append(
            DiscoveryNote(field="corners", rule="R6", reason=reason, fix_hint=fix_hint)
        )
        return [], None

    if not use_filesystem:
        return _give_up("filesystem scanning was disabled")
    if _has_unresolved(expr, resolution):
        return _give_up(f"cannot resolve {expr}: $SETUP_ROOT is not set")

    path = Path(resolve_path_expr(expr, resolution.resolved))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _give_up(f"cannot read {path}: {exc}")
    scanned.append(path.as_posix())

    literals = _parse_corner_literals(text)
    if not literals:
        return _give_up(f"no corner literal matched in {path}")

    corners = [
        CornerSpec(
            name=_slug(literal),
            technology_corner=literal,
            description=f"discovered in {path.name}",
        )
        for literal in literals
    ]
    default = next(
        (c.name for c in corners if c.technology_corner.upper() == _LEGACY_DEFAULT_CORNER),
        None,
    )
    if default is None:
        notes.append(
            DiscoveryNote(
                field="default_corner",
                rule="R6",
                reason=(
                    f"none of the {len(corners)} discovered corners is spelled "
                    f"{_LEGACY_DEFAULT_CORNER!r}, which is the literal the templates use today"
                ),
                fix_hint=(
                    "Set `default_corner:` to the corner your group runs by default: "
                    + ", ".join(c.name for c in corners)
                ),
            )
        )
    return corners, default


def _parse_corner_literals(text: str) -> list[str]:
    """R6. Ordered, de-duplicated corner literals found in ``text``."""

    seen: dict[str, None] = {}
    for pattern in _CORNER_RES:
        for m in pattern.finditer(text):
            literal = m.group(1).strip()
            if literal and literal not in seen:
                seen[literal] = None
            if len(seen) >= _MAX_CORNERS:
                return list(seen)
    return list(seen)


def _scan_supply_names(
    raw_calibre_text: str | None,
    notes: list[DiscoveryNote],
    scanned: list[str],
) -> tuple[list[str], list[str]]:
    """R7. Global power / ground lists, only ever from a real runset export."""

    if raw_calibre_text is None:
        notes.append(
            DiscoveryNote(
                field="power_names",
                rule="R7",
                reason="no raw Calibre runset export was supplied to the scan",
                fix_hint=(
                    "Re-scan with a raw export (`auto-ext init-project --raw-calibre "
                    "<file>`), or paste the *lvsPowerNames / *lvsGroundNames lists into "
                    "`power_names:` / `ground_names:`."
                ),
            )
        )
        return [], []

    scanned.append("<raw calibre export>")
    power = _split_names(_POWER_RE.search(raw_calibre_text))
    ground = _split_names(_GROUND_RE.search(raw_calibre_text))
    for name, values in (("power_names", power), ("ground_names", ground)):
        if not values:
            notes.append(
                DiscoveryNote(
                    field=name,
                    rule="R7",
                    reason=f"the export has no *lvs{name.split('_')[0].capitalize()}Names line",
                    fix_hint=(
                        f"Add the list to `{name}:` by hand; an export made without global "
                        "supplies declared does not carry it."
                    ),
                )
            )
    return power, ground


def _split_names(match: re.Match[str] | None) -> list[str]:
    if match is None:
        return []
    return match.group(1).split()


# ---- helpers -----------------------------------------------------------------


def _has_unresolved(expr: str, resolution: EnvResolution) -> bool:
    """True when ``expr`` references an env var this shell does not have.

    ``resolve_env`` maps a missing var to ``""``, so substituting first would
    turn ``$SETUP_ROOT/assura_tech.lib`` into ``/assura_tech.lib`` and then
    report that path as missing -- blaming the filesystem for an unset
    variable.
    """

    return any(
        resolution.sources.get(name, "missing") == "missing"
        for name in discover_required_vars([expr])
    )


def _version_segment(resolved_dir: str, anchor: str) -> str | None:
    """R4. The path segment right after ``anchor`` (``LVS`` / ``QRC``)."""

    if anchor not in _VERSION_ANCHORS:
        raise ValueError(f"unknown version anchor {anchor!r}; expected one of {_VERSION_ANCHORS}")
    parts = PurePosixPath(resolved_dir).parts
    for index, part in enumerate(parts[:-1]):
        if part == anchor:
            return parts[index + 1]
    return None


def _slug(text: str) -> str:
    """Lower-case ``text`` into the ``Slug`` shape, keeping ``. _ -``."""

    s = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-._")
    while s and not s[0].isalnum():
        s = s[1:]
    return s[:64] or "x"


def _required_env(lvs_decks: LvsDeckSet, qrc: QrcDeck) -> list[str]:
    """Vars the finished profile depends on: the base set plus its own paths."""

    exprs: list[str] = [
        DEFAULT_TECH_LIBRARY_FILE,
        DEFAULT_LAYER_MAP,
        DEFAULT_CDL_INCLUDE_FILE,
    ]
    if lvs_decks.dir_expr:
        exprs.append(lvs_decks.dir_expr)
    if qrc.dir_expr:
        exprs.append(qrc.dir_expr)
    return sorted(set(BASE_ENV_CANDIDATES) | discover_required_vars(exprs))


# ---- persistence -------------------------------------------------------------


def _ordered(value: object) -> object:
    """Recursively turn dicts into ``CommentedMap`` so field order survives.

    ruamel's safe representer sorts plain dict keys alphabetically, which
    would scatter ``lvs_decks`` across the file; the round-trip representer
    keeps ``CommentedMap`` insertion order, which is pydantic field order.
    """

    if isinstance(value, dict):
        out = CommentedMap()
        for key, item in value.items():
            out[key] = _ordered(item)
        return out
    if isinstance(value, list):
        return [_ordered(item) for item in value]
    return value


def profile_to_yaml(profile: PdkProfile) -> str:
    """Serialize a profile to YAML text in pydantic field order."""

    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 100
    buf = io.StringIO()
    yaml.dump(_ordered(profile.model_dump(mode="json")), buf)
    return buf.getvalue()


def write_profile_yaml(path: Path, profile: PdkProfile) -> Path:
    """Write ``config/profiles/<id>.yaml`` atomically; return the path.

    Same protocol as the run store: temp file next to the target, then
    :func:`os.replace`.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(profile_to_yaml(profile), encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"cannot write {path}: {exc}") from exc
    return path


def read_profile_yaml(path: Path) -> PdkProfile:
    """Load and validate ``config/profiles/<id>.yaml``.

    Raises :class:`~auto_ext.core.errors.ConfigError` for I/O failures, YAML
    syntax errors, a schema version this build cannot read, and any pydantic
    validation failure -- a profile that does not validate must never be
    silently degraded, because every path in the flow hangs off it.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read profile {path}: {exc}") from exc
    try:
        data = YAML(typ="rt").load(text)
    except YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(data).__name__}")
    version = data.get("schema_version", PDK_PROFILE_SCHEMA_VERSION)
    if not isinstance(version, int):
        raise ConfigError(f"{path}: schema_version must be an integer, got {version!r}")
    if version > PDK_PROFILE_SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: profile schema v{version} is newer than this build "
            f"(v{PDK_PROFILE_SCHEMA_VERSION}); upgrade Auto_ext to read it"
        )
    try:
        return PdkProfile.model_validate(dict(data))
    except Exception as exc:  # pydantic ValidationError and anything it wraps
        raise ConfigError(f"{path}: not a valid PDK profile: {exc}") from exc
