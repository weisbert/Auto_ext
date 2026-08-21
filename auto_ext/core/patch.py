"""Patch escape hatch: the user edits the *generated* file, we store the diff
against the *masked* generated file so it survives catalog upgrades, PDK
profile swaps and per-cell re-renders.

Replaces ``core/preset.py`` (single-line anchors, exact byte compare, no
fallback) and ``core/clone_template.py`` (whole-file fork that goes stale the
moment the PDK moves). Pure Python -- no Qt.

Why masked space
----------------
One recipe renders one template once **per cell**, and ``[[cell]]`` /
``[[library]]`` / ``[[out_file]]`` are scattered through every production
template. A unified diff captured on ``cell=pll_top`` carries ``pll_top`` in
its context lines and rejects on ``cell=vco_core`` -- and if the user's edit
itself mentions the cell, the diff freezes the wrong name into the *output*
without failing, which is the worst class of error in an RC extraction flow.
Storing ``${cell}`` instead removes both failure modes at the source.

The masked text is not produced by searching for values in the output (that
mis-fires: ``ground_net="VSS"`` would blank out every unrelated ``VSS``). It
is produced by **rendering the same template a second time** with maskable
variables bound to ``${name}`` tokens, so Jinja itself does the provenance
tracking and the two renders are line-for-line aligned.

Mask grammar
------------
``${name}`` is a variable slot, ``$$`` is a literal ``$``, everything else is
literal text. Safe as a grammar because ``render_template()`` substitutes
every ``$X`` / ``${X}`` / ``$env(X)`` reference away *before* Jinja runs, so
generated output contains essentially no ``$`` (see
``project_auto_ext_rules.md`` and :func:`auto_ext.core.template.render_template`).

Unverified assumptions
----------------------
Everything in this module was developed against the checked-in templates and
mocked EDA binaries; no run against a real Cadence installation has happened
yet. The two numbers that would move first if the real corpus disagrees are
:data:`SIMILARITY_MIN_RATIO` and :data:`SIMILARITY_MIN_MARGIN`; they are
tuned for ``.qci`` files, whose lines are all ``*key: value`` and therefore
have a high random-pair similarity floor. They are the only tunables in the
module and are deliberately gathered at the top so the office-side
re-calibration is a one-place edit.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jinja2 import nodes

from auto_ext.core.errors import AutoExtError
from auto_ext.core.patch_models import (
    BLOCKING_STATUSES,
    OK_STATUSES,
    STATUS_SEVERITY,
    BaseFingerprint,
    Binding,
    FuzzyPolicy,
    HunkOutcome,
    PatchHunk,
    PatchStatus,
    Stage,
    StagePatchReport,
    TemplatePatch,
)
from auto_ext.core.template import _make_jinja_env

# --- tunables ---------------------------------------------------------------

#: ``${name}`` is a variable slot; ``$$`` is a literal ``$``.
_MASK_RE = re.compile(r"\$(?:\$|\{([A-Za-z_][A-Za-z0-9_]*)\})")
_WS_RUN_RE = re.compile(r"[ \t]+")

#: A value shorter than this is never masked -- too likely to collide with
#: unrelated text and turn a discriminating anchor into a wildcard.
MIN_MASK_LEN = 3

#: Lines of context kept on each side of a hunk. ``PresetHunk`` used 1, which
#: is far too little in a ``.qci`` where every line is ``*key: value``.
DEFAULT_CTX_LINES = 3

#: Similarity tier acceptance floor and the minimum lead over the runner-up.
SIMILARITY_MIN_RATIO = 0.80
SIMILARITY_MIN_MARGIN = 0.10

#: Windows scoring below this are not even reported. Lower than the
#: acceptance floor on purpose: a LOST hunk still has to tell the user where
#: the closest thing is, and "no idea" is a useless conflict report.
SIMILARITY_DIAGNOSTIC_FLOOR = 0.50

#: A single-line needle with every slot wildcarded and no context has to carry
#: at least this many literal characters, otherwise it would match anything.
LOW_DISCRIMINATION_MIN_LITERAL_CHARS = 12

#: Internal sentinel used while re-masking user-typed text, so a freshly
#: inserted ``${name}`` token can never be chewed up by a later, shorter
#: value. Never reaches storage.
_SENTINEL = "\x00"


class PatchConflictError(AutoExtError):
    """A patch could not be placed and the recipe policy refuses the run.

    Carries the full :class:`PatchApplyReport` so the caller can show the
    per-hunk diagnosis instead of a one-line failure.
    """

    def __init__(self, report: PatchApplyReport, *, template_id: str = "") -> None:
        self.report = report
        self.template_id = template_id
        where = f" in {template_id}" if template_id else ""
        super().__init__(f"manual edits could not be applied{where}: {report.summary()}")


# --- mask grammar -----------------------------------------------------------


def escape_literal(text: str) -> str:
    """Escape raw text so it round-trips through the mask grammar.

    Used for text the *user typed*, which is literal by definition.
    """
    return text.replace("$", "$$")


def mask_escape(text: str, slot_names: Iterable[str]) -> str:
    """Escape a *masked render* so it round-trips through the mask grammar.

    The masked render is raw Jinja output: its ``${name}`` tokens must stay
    tokens, but any other ``$`` in it (a literal ``A$B`` net name, a stray
    ``$$``) is data and has to be escaped. This is the asymmetric twin of
    :func:`escape_literal` and the reason a literal ``$$`` in a tool file does
    not silently collapse to ``$`` on the way back out.
    """
    names = set(slot_names)
    out: list[str] = []
    pos = 0
    for match in _MASK_RE.finditer(text):
        out.append(text[pos : match.start()].replace("$", "$$"))
        name = match.group(1)
        if name is not None and name in names:
            out.append(match.group(0))
        else:
            out.append(match.group(0).replace("$", "$$"))
        pos = match.end()
    out.append(text[pos:].replace("$", "$$"))
    return "".join(out)


def unmask(masked: str, values: Mapping[str, str], *, missing: str = "keep") -> str:
    """Materialise ``${var}`` slots with current values.

    ``missing='keep'`` leaves unknown slots as-is -- at match time they can
    still be treated as wildcards. ``missing='raise'`` raises :class:`KeyError`
    and is what the replacement path wants when it must not emit a literal
    ``${x}`` into a tool input file.
    """

    def repl(match: re.Match[str]) -> str:
        if match.group(1) is None:
            return "$"
        name = match.group(1)
        if name in values:
            return values[name]
        if missing == "keep":
            return match.group(0)
        raise KeyError(name)

    return _MASK_RE.sub(repl, masked)


def slots_in(masked: str) -> set[str]:
    """Every ``${name}`` slot referenced by a piece of masked text."""
    return {m.group(1) for m in _MASK_RE.finditer(masked) if m.group(1)}


# --- masked rendering -------------------------------------------------------


def condition_vars(template_source: str) -> set[str]:
    """Names whose value steers control flow in a template.

    These MUST be bound to their real values during the masked render: a
    ``[% if connect_by_name %]`` bound to the string ``"${connect_by_name}"``
    is truthy no matter what the real value is, the two renders take different
    branches, and every line offset after that point is wrong.

    Collected from the Jinja AST -- ``[% if %]`` / ``[% elif %]`` tests,
    inline ``a if b else c`` conditionals, and ``[% for x in y %]`` iterables
    (a masked string would iterate character by character). Filters, attribute
    access and comparisons inside the expression are walked too, so
    ``[% if cell.startswith('x') %]`` yields ``cell``.

    ``template_source`` should be the same text Jinja is given, i.e. after
    :func:`auto_ext.core.env.substitute_env`; env substitution never touches
    ``[% %]`` blocks, so passing the raw source gives the same answer.
    """
    env = _make_jinja_env()
    ast = env.parse(template_source)
    expressions: list[Any] = []
    for if_node in ast.find_all(nodes.If):
        expressions.append(if_node.test)
    for cond in ast.find_all(nodes.CondExpr):
        expressions.append(cond.test)
    for for_node in ast.find_all(nodes.For):
        expressions.append(for_node.iter)
        if for_node.test is not None:
            expressions.append(for_node.test)
    out: set[str] = set()
    for expr in expressions:
        out |= _names_in(expr)
    return out


def _names_in(expr: Any) -> set[str]:
    """Every ``Name`` in an expression, *including the expression itself*.

    ``Node.find_all`` only walks descendants, so a bare ``[% if flag %]``
    whose test node *is* the ``Name`` would otherwise come back empty -- and a
    control-flow variable that escapes this set gets masked, takes the wrong
    branch during the masked render, and silently misaligns every hunk after
    it.
    """
    found = {expr.name} if isinstance(expr, nodes.Name) else set()
    return found | {node.name for node in expr.find_all(nodes.Name)}


def _mask_plan(
    template_source: str, context: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Split a render context into (masked context, slot -> real value).

    A variable is masked iff it is not a control-flow variable, is a plain
    ``str`` / ``int`` / ``float`` (never ``bool``, never a container or a
    ``Path`` whose attributes a template might reach for), stringifies to at
    least :data:`MIN_MASK_LEN` characters, and contains no newline -- a
    multi-line value would break the line alignment the whole design rests on.
    """
    cond = condition_vars(template_source)
    masked: dict[str, Any] = {}
    values: dict[str, str] = {}
    for key, val in context.items():
        if key in cond or isinstance(val, bool) or not isinstance(val, (str, int, float)):
            masked[key] = val
            continue
        text = str(val)
        if len(text) < MIN_MASK_LEN or "\n" in text:
            masked[key] = val
            continue
        masked[key] = "${%s}" % key
        values[key] = text
    return masked, values


def masked_context(
    template_source: str, context: Mapping[str, Any]
) -> dict[str, Any]:
    """The render context that produces the *masked* twin of the base."""
    return _mask_plan(template_source, context)[0]


def mask_values(
    template_source: str, context: Mapping[str, Any]
) -> dict[str, str]:
    """The inverse map used at apply time: slot name -> current string value."""
    return _mask_plan(template_source, context)[1]


def render_masked(template_source: str, context: Mapping[str, Any]) -> str:
    """Render ``template_source`` with maskable variables bound to tokens.

    Companion to :func:`auto_ext.core.template.render_template`: the caller
    passes the same post-``substitute_env`` source and the same merged
    context, and gets back the masked twin that :func:`capture_patch` and the
    fast path both need.
    """
    env = _make_jinja_env()
    return env.from_string(template_source).render(
        **masked_context(template_source, context)
    )


# --- runtime views ----------------------------------------------------------


@dataclass(frozen=True)
class Fuzz:
    """How much had to be relaxed to place a hunk.

    ``binding`` alone never makes a match fuzzy: a wildcarded slot is a
    position Jinja told us is a variable, so matching it loosely is exactly
    right. Dropping context, normalising whitespace, falling back to the
    recorded ordinal, or scoring by similarity all are fuzzy.
    """

    dropped_context: int = 0
    binding: Binding = Binding.EXACT
    normalized: bool = False
    by_occurrence: bool = False
    similarity: float | None = None

    @property
    def is_clean(self) -> bool:
        return (
            self.dropped_context == 0
            and not self.normalized
            and not self.by_occurrence
            and self.similarity is None
        )

    def describe(self) -> str:
        bits: list[str] = []
        if self.dropped_context:
            plural = "" if self.dropped_context == 1 else "s"
            bits.append(f"context shortened by {self.dropped_context} line{plural}")
        if self.binding is Binding.CHANGED_WILD:
            bits.append("changed variable slots relaxed")
        elif self.binding is Binding.ALL_WILD:
            bits.append("all variable slots relaxed")
        if self.normalized:
            bits.append("whitespace differences ignored")
        if self.by_occurrence:
            bits.append("disambiguated by recorded occurrence")
        if self.similarity is not None:
            bits.append(f"similarity match {self.similarity:.0%}")
        return ", ".join(bits) or "exact match"


@dataclass
class HunkResolution:
    """Where one hunk ended up, and everything the UI needs to explain it."""

    hunk_id: str
    status: PatchStatus
    fuzz: Fuzz = field(default_factory=Fuzz)
    start: int | None = None  # 0-indexed line in the NEW base
    end: int | None = None  # half-open
    candidates: list[tuple[int, int]] = field(default_factory=list)
    nearest: tuple[int, int, float] | None = None  # (start, end, ratio)
    expected_text: str = ""  # materialised `before`
    found_text: str = ""  # what is actually at the resolved site
    #: Slots referenced by `after` that the current context cannot fill. A
    #: non-empty list is always blocking: emitting a literal ``${x}`` into a
    #: tool input file is a silent wrong-result bug.
    unresolved_slots: list[str] = field(default_factory=list)
    tried: list[str] = field(default_factory=list)  # audit trail
    message: str = ""

    @property
    def blocking(self) -> bool:
        return bool(self.unresolved_slots) or self.status in BLOCKING_STATUSES

    def blocking_under(self, policy: FuzzyPolicy) -> bool:
        """Blocking under a recipe's ``on_fuzzy`` policy.

        ``ACCEPT`` only ever forgives :attr:`PatchStatus.REVIEW`; a lost,
        ambiguous or overlapping anchor is not a fuzz question.
        """
        if self.unresolved_slots:
            return True
        if policy is FuzzyPolicy.ACCEPT and self.status is PatchStatus.REVIEW:
            return False
        return self.status in BLOCKING_STATUSES


@dataclass
class PatchApplyReport:
    """Result of applying one :class:`TemplatePatch` to one generated file.

    ``patched_text`` is always best-effort: every hunk that could be placed is
    placed, so the editor can show a preview even when the run is refused.
    Whether the run may start is :attr:`blocking` / :meth:`blocking_under`.
    """

    patched_text: str
    resolutions: list[HunkResolution]
    fast_path: bool = False

    @property
    def blocking(self) -> bool:
        return any(r.blocking for r in self.resolutions)

    def blocking_under(self, policy: FuzzyPolicy) -> bool:
        return any(r.blocking_under(policy) for r in self.resolutions)

    @property
    def counts(self) -> dict[PatchStatus, int]:
        out: dict[PatchStatus, int] = {}
        for res in self.resolutions:
            out[res.status] = out.get(res.status, 0) + 1
        return out

    @property
    def worst_status(self) -> PatchStatus | None:
        if not self.resolutions:
            return None
        return max(
            (r.status for r in self.resolutions), key=lambda s: STATUS_SEVERITY[s]
        )

    def resolution(self, hunk_id: str) -> HunkResolution | None:
        for res in self.resolutions:
            if res.hunk_id == hunk_id:
                return res
        return None

    def summary(self) -> str:
        parts = [
            f"{status.value}={n}"
            for status, n in sorted(self.counts.items(), key=lambda kv: kv[0].value)
        ]
        noun = "edit" if len(self.resolutions) == 1 else "edits"
        return f"{len(self.resolutions)} manual {noun}: " + ", ".join(parts)


# --- matching primitives ----------------------------------------------------


def _norm(line: str) -> str:
    """Whitespace-insensitive view of a line: strip ends, collapse runs."""
    return _WS_RUN_RE.sub(" ", line.strip())


def _norm_inline(value: str) -> str:
    """Collapse whitespace runs *without* stripping -- for slot values, whose
    position inside a normalised line must be preserved."""
    return _WS_RUN_RE.sub(" ", value)


def _line_pattern(
    masked_line: str,
    values: Mapping[str, str],
    wildcards: frozenset[str],
    *,
    normalized: bool,
) -> re.Pattern[str]:
    """Compile one masked line into a regex over base lines."""
    src = _norm(masked_line) if normalized else masked_line
    out: list[str] = []
    pos = 0
    for match in _MASK_RE.finditer(src):
        out.append(re.escape(src[pos : match.start()]))
        name = match.group(1)
        if name is None:
            out.append(re.escape("$"))
        elif name in wildcards or name not in values:
            out.append(r"[^\r\n]*?")
        else:
            value = values[name]
            out.append(re.escape(_norm_inline(value) if normalized else value))
        pos = match.end()
    out.append(re.escape(src[pos:]))
    return re.compile("".join(out))


def _literal_char_count(masked_line: str) -> int:
    """Characters in a masked line that are not part of a slot token."""
    return len(_MASK_RE.sub("", masked_line).strip())


def _match_seq(
    base_lines: Sequence[str],
    at: int,
    patterns: Sequence[re.Pattern[str]],
    *,
    normalized: bool,
) -> bool:
    if at < 0 or at + len(patterns) > len(base_lines):
        return False
    for off, pattern in enumerate(patterns):
        line = base_lines[at + off]
        if not pattern.fullmatch(_norm(line) if normalized else line):
            return False
    return True


def _find_sites(
    base_lines: Sequence[str],
    needle: Sequence[str],
    lead: Sequence[str],
    tail: Sequence[str],
    values: Mapping[str, str],
    wildcards: frozenset[str],
    *,
    normalized: bool,
    at_head: bool,
    at_tail: bool,
) -> list[tuple[int, int]]:
    """Every ``(start, end)`` -- needle region only -- that matches."""
    def compile_all(lines: Sequence[str]) -> list[re.Pattern[str]]:
        return [
            _line_pattern(line, values, wildcards, normalized=normalized)
            for line in lines
        ]

    p_lead, p_need, p_tail = compile_all(lead), compile_all(needle), compile_all(tail)
    n, n_lead, n_tail = len(needle), len(lead), len(tail)
    out: list[tuple[int, int]] = []
    lo, hi = n_lead, len(base_lines) - n - n_tail
    for start in range(lo, hi + 1):
        if at_head and start - n_lead != 0:
            continue
        if at_tail and start + n + n_tail != len(base_lines):
            continue
        if not _match_seq(base_lines, start - n_lead, p_lead, normalized=normalized):
            continue
        if n and not _match_seq(base_lines, start, p_need, normalized=normalized):
            continue
        if not _match_seq(base_lines, start + n, p_tail, normalized=normalized):
            continue
        out.append((start, start + n))
    return out


def _find_literal_sites(
    masked_lines: Sequence[str],
    needle: Sequence[str],
    lead: Sequence[str],
    tail: Sequence[str],
) -> list[tuple[int, int]]:
    """Capture-time site search: masked needle against masked base, byte-exact.

    Deliberately not :func:`_find_sites`: at capture time both sides live in
    masked space, so plain equality is both exact and immune to the ``$$``
    escaping asymmetry between the two spaces.
    """
    n, n_lead, n_tail = len(needle), len(lead), len(tail)
    out: list[tuple[int, int]] = []
    lo, hi = n_lead, len(masked_lines) - n - n_tail
    for start in range(lo, hi + 1):
        if list(masked_lines[start - n_lead : start]) != list(lead):
            continue
        if n and list(masked_lines[start : start + n]) != list(needle):
            continue
        if list(masked_lines[start + n : start + n + n_tail]) != list(tail):
            continue
        out.append((start, start + n))
    return out


def _similarity_scan(
    base_lines: Sequence[str], needle_materialised: Sequence[str]
) -> list[tuple[float, int]]:
    """Score every same-length window of the base against the needle.

    Pre-filtered by the two cheap upper bounds ``difflib`` offers, against the
    *diagnostic* floor rather than the acceptance floor: everything above the
    acceptance floor is therefore still scored, and a hunk that will end up
    LOST can still name its closest region.
    """
    if not needle_materialised:
        return []
    n = len(needle_materialised)
    target = "".join(_norm(line) + "\n" for line in needle_materialised)
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(target)
    scored: list[tuple[float, int]] = []
    for start in range(0, max(0, len(base_lines) - n) + 1):
        window = "".join(_norm(line) + "\n" for line in base_lines[start : start + n])
        matcher.set_seq1(window)
        if matcher.real_quick_ratio() < SIMILARITY_DIAGNOSTIC_FLOOR:
            continue
        if matcher.quick_ratio() < SIMILARITY_DIAGNOSTIC_FLOOR:
            continue
        ratio = matcher.ratio()
        if ratio >= SIMILARITY_DIAGNOSTIC_FLOOR:
            scored.append((ratio, start))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored


# --- the ladder -------------------------------------------------------------


def _ladder(ctx_full: int) -> Iterator[tuple[int, Binding, bool]]:
    """Yield ``(ctx_lines, binding, normalize)`` from strictest to loosest."""
    for ctx_n in range(ctx_full, -1, -1):
        for normalized in (False, True):
            for binding in (Binding.EXACT, Binding.CHANGED_WILD, Binding.ALL_WILD):
                yield ctx_n, binding, normalized


def _wildcards_for(
    binding: Binding,
    all_slots: frozenset[str],
    captured: Mapping[str, str],
    current: Mapping[str, str],
) -> frozenset[str]:
    if binding is Binding.EXACT:
        return frozenset()
    if binding is Binding.ALL_WILD:
        return all_slots
    return frozenset(
        slot
        for slot in all_slots
        if slot not in current or captured.get(slot) != current.get(slot)
    )


def _discriminating(needle: Sequence[str], ctx_n: int, binding: Binding) -> bool:
    """Reject a ladder rung that would match essentially any line.

    Without this, ``-min_res ${min_res}`` with no context and every slot
    wildcarded is the regex ``-min_res [^\\r\\n]*?`` and lands wherever it
    likes.
    """
    if ctx_n > 0 or binding is not Binding.ALL_WILD:
        return True
    if len(needle) >= 2:
        return True
    return bool(needle) and (
        _literal_char_count(needle[0]) >= LOW_DISCRIMINATION_MIN_LITERAL_CHARS
    )


def _locate(
    base_lines: Sequence[str],
    needle: Sequence[str],
    ctx_before: Sequence[str],
    ctx_after: Sequence[str],
    *,
    values: Mapping[str, str],
    captured: Mapping[str, str],
    occurrence_index: int | None,
    occurrence_count: int | None,
    at_head: bool,
    at_tail: bool,
    tried: list[str],
) -> tuple[tuple[int, int] | None, Fuzz, list[tuple[int, int]]]:
    """Run the ladder. Returns ``(site | None, fuzz, strictest_candidates)``.

    ``strictest_candidates`` is the candidate set from the *tightest* rung
    that matched anything -- that is the honest answer to "which sites did
    this look like?", and what the AMBIGUOUS report shows.
    """
    joined = (*needle, *ctx_before, *ctx_after)
    all_slots = (
        frozenset().union(*(slots_in(line) for line in joined))
        if joined
        else frozenset()
    )
    ctx_full = max(len(ctx_before), len(ctx_after))
    strictest: list[tuple[int, int]] = []

    for ctx_n, binding, normalized in _ladder(ctx_full):
        if not needle and ctx_n == 0:
            continue  # a pure insertion needs both anchors to exist
        if not _discriminating(needle, ctx_n, binding):
            continue
        lead = list(ctx_before)[len(ctx_before) - min(ctx_n, len(ctx_before)) :]
        tail = list(ctx_after)[: min(ctx_n, len(ctx_after))]
        wild = _wildcards_for(binding, all_slots, captured, values)
        sites = _find_sites(
            base_lines,
            needle,
            lead,
            tail,
            values,
            wild,
            normalized=normalized,
            at_head=at_head and ctx_n >= len(ctx_before),
            at_tail=at_tail and ctx_n >= len(ctx_after),
        )
        tried.append(
            f"ctx={ctx_n} binding={binding.value} norm={int(normalized)} "
            f"-> {len(sites)}"
        )
        if sites and not strictest:
            strictest = sites
        if not sites:
            continue
        fuzz = Fuzz(
            dropped_context=ctx_full - ctx_n,
            binding=binding,
            normalized=normalized,
        )
        if len(sites) == 1:
            return sites[0], fuzz, strictest
        if (
            occurrence_count is not None
            and occurrence_index is not None
            and len(sites) == occurrence_count
        ):
            # Same shape as capture time -- trust the recorded ordinal, but
            # never call that CLEAN: using the ordinal has to leave a trace.
            return (
                sites[occurrence_index],
                Fuzz(
                    dropped_context=fuzz.dropped_context,
                    binding=fuzz.binding,
                    normalized=fuzz.normalized,
                    by_occurrence=True,
                ),
                strictest,
            )
    return None, Fuzz(), strictest


# --- apply ------------------------------------------------------------------


def apply_patch(
    base_text: str,
    patch: TemplatePatch,
    values: Mapping[str, str],
    *,
    base_masked_text: str | None = None,
) -> PatchApplyReport:
    """Apply ``patch`` to a freshly generated ``base_text``.

    ``values`` maps slot name -> current rendered string value, i.e. the
    output of :func:`mask_values` for this render. ``base_masked_text`` is the
    masked twin of ``base_text``; supplying it enables the fast path.
    """
    base_text = base_text.replace("\r\n", "\n")
    base_lines = base_text.splitlines(keepends=True)

    if base_masked_text is not None:
        masked_lines = base_masked_text.replace("\r\n", "\n").splitlines(keepends=True)
        if _sha256(base_masked_text) == patch.base.masked_sha256 and _fast_path_ok(
            masked_lines, patch, values
        ):
            return _apply_fast_path(base_lines, patch, values)

    resolutions: list[HunkResolution] = []
    edits: list[tuple[int, int, list[str], HunkResolution]] = []

    for hunk in patch.hunks:
        res, edit = _resolve_hunk(base_lines, hunk, values)
        resolutions.append(res)
        if edit is not None:
            edits.append((*edit, res))

    _flag_overlaps(edits)
    edits = [
        e
        for e in edits
        if e[3].status
        not in (PatchStatus.OVERLAP, PatchStatus.ABSORBED, PatchStatus.NOOP)
    ]

    out = list(base_lines)
    for start, end, replacement, _res in sorted(edits, key=lambda e: (-e[0], -e[1])):
        out[start:end] = replacement
    return PatchApplyReport("".join(out), resolutions)


def _flag_overlaps(
    edits: list[tuple[int, int, list[str], HunkResolution]],
) -> None:
    """Mark every hunk whose resolved range collides with another's.

    Both sides are marked and neither is applied: applying one of two
    conflicting edits produces a file the user never asked for and never
    reviewed.
    """
    ordered = sorted(edits, key=lambda e: (e[0], e[1]))
    for (s1, e1, _r1, res1), (s2, e2, _r2, res2) in zip(ordered, ordered[1:]):
        if s2 < e1:
            for res in (res1, res2):
                res.status = PatchStatus.OVERLAP
                res.message = (
                    f"Overlaps another manual edit: lines {s1 + 1}-{e1} "
                    f"and lines {s2 + 1}-{e2} resolve to the same region."
                )


def _resolve_hunk(
    base_lines: Sequence[str],
    hunk: PatchHunk,
    values: Mapping[str, str],
) -> tuple[HunkResolution, tuple[int, int, list[str]] | None]:
    res = HunkResolution(hunk_id=hunk.id, status=PatchStatus.LOST)
    if not hunk.enabled:
        res.status = PatchStatus.DISABLED
        res.message = "Temporarily disabled; recorded but not applied."
        return res, None

    before = hunk.before_lines
    after = hunk.after_lines
    mat_before = [unmask(line, values) for line in before]
    mat_after = [unmask(line, values) for line in after]
    res.expected_text = "".join(mat_before)

    missing = sorted(slots_in(hunk.after) - set(values))
    if missing:
        res.unresolved_slots = missing
        res.message = (
            "Replacement text references variable slot(s) the current render "
            f"cannot fill: {', '.join(missing)}."
        )

    # 1. no-op: the variables converged and this edit changes nothing.
    if mat_before == mat_after:
        res.status = PatchStatus.NOOP
        res.message = (
            "Variable values have converged; this edit no longer changes "
            "anything and can be deleted."
        )
        return res, None

    # 2. locate `before`. While `before` is still there the catalog has not
    #    adopted the edit, so the ABSORBED probe must not run.
    site, fuzz, candidates = _locate(
        base_lines,
        before,
        hunk.context_before_lines,
        hunk.context_after_lines,
        values=values,
        captured=hunk.captured_values,
        occurrence_index=hunk.occurrence_index,
        occurrence_count=hunk.occurrence_count,
        at_head=hunk.anchored_at_head,
        at_tail=hunk.anchored_at_tail,
        tried=res.tried,
    )

    if site is not None:
        start, end = site
        res.start, res.end, res.fuzz = start, end, fuzz
        res.status = PatchStatus.CLEAN if fuzz.is_clean else PatchStatus.SHIFTED
        res.found_text = "".join(base_lines[start:end])
        if not res.message:
            res.message = (
                "Applied cleanly."
                if fuzz.is_clean
                else f"Applied with relaxed matching: {fuzz.describe()}."
            )
        replacement = (
            _rebase_indent(mat_after, base_lines[start:end], mat_before)
            if fuzz.normalized
            else mat_after
        )
        return res, (start, end, replacement)

    # 3. `before` is gone -- has the catalog absorbed the edit?
    res.tried.append("-- absorbed probe (needle = after) --")
    absorbed_site, absorbed_fuzz, _ = _locate(
        base_lines,
        after,
        hunk.context_before_lines,
        hunk.context_after_lines,
        values=values,
        captured=hunk.captured_values,
        occurrence_index=hunk.occurrence_index,
        occurrence_count=hunk.occurrence_count,
        at_head=hunk.anchored_at_head,
        at_tail=hunk.anchored_at_tail,
        tried=res.tried,
    )
    if absorbed_site is not None and absorbed_fuzz.is_clean:
        res.status = PatchStatus.ABSORBED
        res.start, res.end = absorbed_site
        res.found_text = "".join(base_lines[absorbed_site[0] : absorbed_site[1]])
        res.message = (
            "The catalog now produces this content on its own; this manual "
            "edit has been adopted and can be deleted."
        )
        return res, None

    # 4. several sites looked plausible but none could be preferred.
    if len(candidates) > 1:
        res.status = PatchStatus.AMBIGUOUS
        res.candidates = candidates
        res.message = (
            f"Ambiguous anchor: {len(candidates)} sites match and the recorded "
            "occurrence does not resolve the choice."
        )
        return res, None

    # 5. similarity tier -- applied, but never silently.
    scored = _similarity_scan(base_lines, mat_before)
    if scored:
        best_ratio, best_start = scored[0]
        n = len(mat_before)
        runner_up = next(
            (r for r, s in scored[1:] if abs(s - best_start) >= n), 0.0
        )
        res.nearest = (best_start, best_start + n, best_ratio)
        if (
            best_ratio >= SIMILARITY_MIN_RATIO
            and best_ratio - runner_up >= SIMILARITY_MIN_MARGIN
        ):
            res.status = PatchStatus.REVIEW
            res.start, res.end = best_start, best_start + n
            res.fuzz = Fuzz(
                dropped_context=len(hunk.context_before_lines),
                binding=Binding.ALL_WILD,
                normalized=True,
                similarity=best_ratio,
            )
            res.found_text = "".join(base_lines[best_start : best_start + n])
            res.message = (
                f"The generated content changed; this edit could only be "
                f"placed by {best_ratio:.0%} similarity. Confirm the location "
                f"before running."
            )
            replacement = _rebase_indent(
                mat_after, base_lines[best_start : best_start + n], mat_before
            )
            return res, (best_start, best_start + n, replacement)

    # 6. nothing left to try.
    res.status = PatchStatus.LOST
    res.message = "Anchor is gone: the regenerated file has no matching location."
    if res.nearest is not None:
        res.message += (
            f" The closest region starts at line {res.nearest[0] + 1} "
            f"(similarity {res.nearest[2]:.0%})."
        )
    return res, None


def _rebase_indent(
    after: Sequence[str], found: Sequence[str], expected: Sequence[str]
) -> list[str]:
    """Re-apply the found region's indentation to the replacement lines.

    Only used when the match needed whitespace normalisation: the tool
    re-exported the file with a different continuation indent, so writing back
    the capture-time indent would leave the file with two indent styles mixed.
    """
    if not after or not found or not expected:
        return list(after)

    def lead(text: str) -> int:
        return len(text) - len(text.lstrip(" \t"))

    delta = lead(found[0]) - lead(expected[0])
    if delta == 0:
        return list(after)
    if delta > 0:
        return [" " * delta + line for line in after]
    return [line[min(-delta, lead(line)) :] for line in after]


def _fast_path_ok(
    masked_lines: Sequence[str], patch: TemplatePatch, values: Mapping[str, str]
) -> bool:
    """Verify the recorded offsets before trusting them.

    ``masked_sha256`` proves the *file* is the one we captured against, but
    not that ``recorded_start`` is right -- a hand-edited or imported patch can
    carry a stale or default offset. Re-check every slice; on any mismatch the
    caller falls back to the ladder rather than corrupting the output.
    """
    slot_names = set(values)
    ranges: list[tuple[int, int]] = []
    for hunk in patch.hunks:
        if not hunk.enabled:
            continue
        start = hunk.recorded_start
        end = start + len(hunk.before_lines)
        if end > len(masked_lines):
            return False
        actual = [mask_escape(line, slot_names) for line in masked_lines[start:end]]
        if actual != hunk.before_lines:
            return False
        ranges.append((start, end))
    ranges.sort()
    return all(s2 >= e1 for (_s1, e1), (s2, _e2) in zip(ranges, ranges[1:]))


def _apply_fast_path(
    base_lines: Sequence[str], patch: TemplatePatch, values: Mapping[str, str]
) -> PatchApplyReport:
    out = list(base_lines)
    by_id: dict[str, HunkResolution] = {}
    for hunk in patch.hunks:
        if not hunk.enabled:
            by_id[hunk.id] = HunkResolution(
                hunk_id=hunk.id,
                status=PatchStatus.DISABLED,
                message="Temporarily disabled; recorded but not applied.",
            )
            continue
        start = hunk.recorded_start
        end = start + len(hunk.before_lines)
        res = HunkResolution(
            hunk_id=hunk.id,
            status=PatchStatus.CLEAN,
            start=start,
            end=end,
            expected_text="".join(unmask(line, values) for line in hunk.before_lines),
            found_text="".join(base_lines[start:end]),
            tried=["fast path (masked_sha256 hit)"],
            message="Applied cleanly (masked base is byte-identical to capture time).",
        )
        missing = sorted(slots_in(hunk.after) - set(values))
        if missing:
            res.unresolved_slots = missing
            res.message = (
                "Replacement text references variable slot(s) the current "
                f"render cannot fill: {', '.join(missing)}."
            )
        by_id[hunk.id] = res

    for hunk in sorted(patch.hunks, key=lambda h: -h.recorded_start):
        if not hunk.enabled:
            continue
        start = hunk.recorded_start
        end = start + len(hunk.before_lines)
        out[start:end] = [unmask(line, values) for line in hunk.after_lines]

    resolutions = [by_id[h.id] for h in patch.hunks]
    return PatchApplyReport("".join(out), resolutions, fast_path=True)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    """Public hash helper so callers fingerprint templates the same way."""
    return _sha256(text)


# --- capture ----------------------------------------------------------------


def capture_patch(
    *,
    template_source: str,
    template_sha256: str,
    stage: Stage | str,
    template_id: str,
    profile_id: str | None,
    catalog_version: str | None,
    base_real: str,
    base_masked: str,
    edited_real: str,
    values: Mapping[str, str],
    intents: Mapping[int, str] | None = None,
    keep_literal: Iterable[str] = (),
    ctx_lines: int = DEFAULT_CTX_LINES,
    existing: TemplatePatch | None = None,
) -> TemplatePatch:
    """Turn a user edit of the generated file into a masked patch.

    ``base_real`` and ``base_masked`` MUST be line-aligned; that is guaranteed
    by :func:`masked_context` refusing to mask multi-line values and binding
    every control-flow variable to its real value. The check is enforced here
    rather than trusted, because a silent misalignment would attach every hunk
    to the wrong masked lines.

    ``keep_literal`` names slots whose value the user typed *as a literal*
    (the "keep literal" toggle in the editor). Their occurrences in the edited
    text stay literal instead of being re-masked -- the difference between
    "always use the recipe temperature" and "always use 85".

    ``existing`` is the patch being re-captured: hunks whose masked ``before``
    and ``after`` are unchanged keep their ``id``, ``intent`` and ``enabled``
    flag, so re-capturing does not reset the UI rows or lose the user's notes.
    """
    base_real = base_real.replace("\r\n", "\n")
    base_masked_raw = base_masked.replace("\r\n", "\n")
    edited_real = edited_real.replace("\r\n", "\n")

    real_lines = base_real.splitlines(keepends=True)
    masked_raw_lines = base_masked_raw.splitlines(keepends=True)
    if len(real_lines) != len(masked_raw_lines):
        raise ValueError(
            "real/masked renders are not line-aligned; a context value most "
            "likely contains a newline or drives an [% if %] branch"
        )

    slot_names = set(values)
    masked_lines = [mask_escape(line, slot_names) for line in masked_raw_lines]
    edit_lines = edited_real.splitlines(keepends=True)
    keep = frozenset(keep_literal)

    previous = {
        (h.before, h.after): h for h in (existing.hunks if existing else [])
    }
    used_ids: set[str] = set()
    hunks: list[PatchHunk] = []

    opcodes = [
        op
        for op in difflib.SequenceMatcher(
            a=real_lines, b=edit_lines, autojunk=False
        ).get_opcodes()
        if op[0] != "equal"
    ]

    for index, (_tag, i1, i2, j1, j2) in enumerate(opcodes):
        before_masked = masked_lines[i1:i2]
        lead = masked_lines[max(0, i1 - ctx_lines) : i1]
        tail = masked_lines[i2 : i2 + ctx_lines]
        # A pure insertion has no `before`, so its neighbourhood is the only
        # provenance signal available. Without this fallback an inserted
        # ``-extra_netlist "pll_top_extra.sp"`` would freeze the capture-time
        # cell into every other DUT's file -- the exact silent-wrong-output
        # failure the masked format exists to prevent.
        slot_source = before_masked if before_masked else [*lead, *tail]
        after_masked = _remask_edit(
            edit_lines[j1:j2], slot_source, values, keep_literal=keep
        )

        sites = _find_literal_sites(masked_lines, before_masked, lead, tail)
        occurrence_count = len(sites) or None
        occurrence_index = sites.index((i1, i2)) if (i1, i2) in sites else None

        before_text = "".join(before_masked)
        after_text = "".join(after_masked)
        prior = previous.get((before_text, after_text))
        hunk_id = prior.id if prior and prior.id not in used_ids else _new_id(used_ids)
        used_ids.add(hunk_id)

        hunks.append(
            PatchHunk(
                id=hunk_id,
                enabled=prior.enabled if prior else True,
                intent=(intents or {}).get(index, prior.intent if prior else ""),
                before=before_text,
                after=after_text,
                context_before="".join(lead),
                context_after="".join(tail),
                anchored_at_head=(i1 - len(lead) == 0),
                anchored_at_tail=(i2 + len(tail) == len(masked_lines)),
                occurrence_index=occurrence_index,
                occurrence_count=occurrence_count,
                captured_values={
                    slot: str(values[slot])
                    for slot in sorted(
                        slots_in("".join((*before_masked, *lead, *tail, *after_masked)))
                    )
                    if slot in values
                },
                recorded_start=i1,
            )
        )

    return TemplatePatch(
        stage=Stage(stage),
        template_id=template_id,
        base=BaseFingerprint(
            template_sha256=template_sha256,
            catalog_version=catalog_version,
            profile_id=profile_id,
            masked_sha256=_sha256(base_masked_raw),
            captured_at=datetime.now(timezone.utc),
        ),
        hunks=hunks,
        on_fuzzy=(existing.on_fuzzy if existing else FuzzyPolicy.BLOCK),
    )


def _new_id(used: set[str]) -> str:
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in used:
            return candidate


def _substitute_slots(
    lines: Sequence[str],
    values: Mapping[str, str],
    candidates: Iterable[str],
    keep_literal: frozenset[str],
) -> list[str]:
    """Replace known values with ``${slot}`` tokens, longest value first.

    Substitution goes through a sentinel so a token inserted early can never
    be partially eaten by a shorter value later in the pass.
    """
    names = sorted(
        (
            name
            for name in set(candidates)
            if name in values
            and name not in keep_literal
            and len(values[name]) >= MIN_MASK_LEN
        ),
        key=lambda name: (-len(values[name]), name),
    )
    out: list[str] = []
    for line in lines:
        text = escape_literal(line)
        for position, name in enumerate(names):
            needle = escape_literal(values[name])
            text = text.replace(needle, f"{_SENTINEL}{position}{_SENTINEL}")
        for position, name in enumerate(names):
            text = text.replace(f"{_SENTINEL}{position}{_SENTINEL}", "${%s}" % name)
        out.append(text)
    return out


def _remask_edit(
    after_raw: Sequence[str],
    slot_source: Sequence[str],
    values: Mapping[str, str],
    *,
    keep_literal: frozenset[str] = frozenset(),
) -> list[str]:
    """Re-mask the lines the user typed.

    Deliberately narrow: only values for slots that appear in ``slot_source``
    -- this hunk's masked ``before``, or its context window when ``before`` is
    empty -- are substituted. Anything else the user typed stays literal,
    because that is what they meant; re-masking against every slot in the file
    would turn a deliberate constant into a variable.
    """
    return _substitute_slots(
        after_raw, values, slots_in("".join(slot_source)), keep_literal
    )


def remask_text(
    lines: Sequence[str],
    values: Mapping[str, str],
    *,
    keep_literal: Iterable[str] = (),
) -> list[str]:
    """Re-mask arbitrary real-space text against every known slot value.

    Used by :func:`import_udiff`, where there is no capture-time ``before`` to
    narrow the candidate set down to.
    """
    return _substitute_slots(lines, values, values.keys(), frozenset(keep_literal))


# --- display and import -----------------------------------------------------


def render_hunk_as_udiff(
    hunk: PatchHunk, values: Mapping[str, str], *, label: str = "", context: int = 0
) -> str:
    """Unified diff of one hunk for the UI and ``run.json``.

    Storage stays masked; this is the *display* format. Pass
    ``values={}`` to show the masked form (``${cell}``), which is how the UI
    explains why the patch survives a cell change.
    """
    a = [unmask(line, values) for line in hunk.before_lines]
    b = [unmask(line, values) for line in hunk.after_lines]
    return "".join(
        difflib.unified_diff(
            a, b, fromfile=f"generated{label}", tofile=f"patched{label}", n=context
        )
    )


_UDIFF_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def import_udiff(
    diff_text: str,
    values: Mapping[str, str],
    *,
    ctx_lines: int = DEFAULT_CTX_LINES,
    keep_literal: Iterable[str] = (),
) -> list[PatchHunk]:
    """Convert a pasted unified diff into masked hunks.

    Unified diff is a fine *interchange* format -- it is what a colleague
    pastes into chat -- it is just a bad storage format here. This turns one
    into the storage format: line numbers are dropped (they are worthless
    against a regenerated file), context is re-cut to ``ctx_lines``, and every
    known slot value is re-masked so the imported edit becomes cell- and
    profile-portable like a natively captured one.

    ``anchored_at_head`` / ``anchored_at_tail`` are left ``False`` and
    ``occurrence_*`` ``None``: a pasted diff carries no trustworthy evidence
    about file edges or repeated structures.
    """
    keep = frozenset(keep_literal)
    hunks: list[PatchHunk] = []
    used_ids: set[str] = set()

    for body in _split_udiff_bodies(diff_text):
        for start, stop in _change_runs(body):
            before_raw = [text for kind, text in body[start:stop] if kind == "-"]
            after_raw = [text for kind, text in body[start:stop] if kind == "+"]
            lead_raw = _context_before(body, start, ctx_lines)
            tail_raw = _context_after(body, stop, ctx_lines)

            before = remask_text(before_raw, values, keep_literal=keep)
            after = remask_text(after_raw, values, keep_literal=keep)
            lead = remask_text(lead_raw, values, keep_literal=keep)
            tail = remask_text(tail_raw, values, keep_literal=keep)

            if not before and not (lead and tail):
                raise ValueError(
                    "imported diff contains a pure insertion without context on "
                    "both sides; it cannot be anchored"
                )

            hunk_id = _new_id(used_ids)
            used_ids.add(hunk_id)
            hunks.append(
                PatchHunk(
                    id=hunk_id,
                    before="".join(before),
                    after="".join(after),
                    context_before="".join(lead),
                    context_after="".join(tail),
                    captured_values={
                        slot: values[slot]
                        for slot in sorted(
                            slots_in("".join((*before, *after, *lead, *tail)))
                        )
                        if slot in values
                    },
                )
            )
    if not hunks:
        raise ValueError("no hunks found in the supplied unified diff")
    return hunks


def _split_udiff_bodies(diff_text: str) -> list[list[tuple[str, str]]]:
    """Split a unified diff into per-``@@`` bodies of ``(kind, text)`` lines.

    ``kind`` is one of ``' '`` / ``'-'`` / ``'+'``; ``text`` keeps its
    trailing newline. ``\\ No newline at end of file`` markers are dropped.
    """
    lines = [
        raw.rstrip("\n")
        for raw in diff_text.replace("\r\n", "\n").splitlines()
    ]
    bodies: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] | None = None
    for index, stripped in enumerate(lines):
        if _UDIFF_HEADER_RE.match(stripped):
            current = []
            bodies.append(current)
            continue
        if stripped.startswith(("diff ", "index ")):
            current = None
            continue
        # A ``--- `` line is a file header only when a ``+++ `` line follows
        # it; inside a body it is the deletion of a line starting with ``-- ``.
        # Real diffs always pair the two, so this never mis-reads a deletion.
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        if stripped.startswith("--- ") and nxt.startswith("+++ "):
            current = None
            continue
        if stripped.startswith("+++ ") and lines[index - 1].startswith("--- "):
            continue
        if current is None:
            continue
        if stripped.startswith("\\"):
            continue
        if not stripped:
            current.append((" ", "\n"))
            continue
        kind, text = stripped[0], stripped[1:]
        if kind not in (" ", "-", "+"):
            continue
        current.append((kind, text + "\n"))
    return [b for b in bodies if b]


def _change_runs(body: Sequence[tuple[str, str]]) -> list[tuple[int, int]]:
    """Maximal runs of non-context lines inside one diff body."""
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(body):
        if body[index][0] == " ":
            index += 1
            continue
        start = index
        while index < len(body) and body[index][0] != " ":
            index += 1
        runs.append((start, index))
    return runs


def _context_before(
    body: Sequence[tuple[str, str]], start: int, ctx_lines: int
) -> list[str]:
    """The contiguous context lines immediately preceding a change run."""
    out: list[str] = []
    index = start - 1
    while index >= 0 and len(out) < ctx_lines and body[index][0] == " ":
        out.append(body[index][1])
        index -= 1
    out.reverse()
    return out


def _context_after(
    body: Sequence[tuple[str, str]], stop: int, ctx_lines: int
) -> list[str]:
    """The contiguous context lines immediately following a change run."""
    out: list[str] = []
    index = stop
    while index < len(body) and len(out) < ctx_lines and body[index][0] == " ":
        out.append(body[index][1])
        index += 1
    return out


# --- archiving --------------------------------------------------------------


def build_stage_report(
    patch: TemplatePatch,
    report: PatchApplyReport,
    values: Mapping[str, str],
) -> StagePatchReport:
    """Fold an apply report into the ``run.json`` record.

    Line numbers are emitted 1-indexed because that is what the user sees in
    an editor; ``HunkResolution.start`` stays 0-indexed everywhere else.
    """
    outcomes: list[HunkOutcome] = []
    for res in report.resolutions:
        hunk = patch.hunk(res.hunk_id)
        outcomes.append(
            HunkOutcome(
                hunk_id=res.hunk_id,
                intent=hunk.intent if hunk else "",
                status=res.status,
                fuzz=res.fuzz.describe(),
                start_line=None if res.start is None else res.start + 1,
                end_line=res.end,
                udiff=render_hunk_as_udiff(hunk, values) if hunk else "",
                message=res.message,
            )
        )
    return StagePatchReport(
        stage=patch.stage,
        template_id=patch.template_id,
        fast_path=report.fast_path,
        outcomes=outcomes,
        blocked=report.blocking_under(patch.on_fuzzy),
    )


__all__ = [
    "BLOCKING_STATUSES",
    "DEFAULT_CTX_LINES",
    "LOW_DISCRIMINATION_MIN_LITERAL_CHARS",
    "MIN_MASK_LEN",
    "OK_STATUSES",
    "SIMILARITY_DIAGNOSTIC_FLOOR",
    "SIMILARITY_MIN_MARGIN",
    "SIMILARITY_MIN_RATIO",
    "STATUS_SEVERITY",
    "BaseFingerprint",
    "Binding",
    "Fuzz",
    "FuzzyPolicy",
    "HunkOutcome",
    "HunkResolution",
    "PatchApplyReport",
    "PatchConflictError",
    "PatchHunk",
    "PatchStatus",
    "Stage",
    "StagePatchReport",
    "TemplatePatch",
    "apply_patch",
    "build_stage_report",
    "capture_patch",
    "condition_vars",
    "escape_literal",
    "import_udiff",
    "mask_escape",
    "mask_values",
    "masked_context",
    "remask_text",
    "render_hunk_as_udiff",
    "render_masked",
    "sha256_text",
    "slots_in",
    "unmask",
]
