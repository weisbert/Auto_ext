"""Persisted data structures for the patch escape hatch.

See ``docs/refactor/02-patch.md``. The escape hatch lets a user edit the
*generated* EDA input file directly (the only way to reach a knob the
catalog does not model yet) while the system stores the edit as a diff
against the **masked** generated file. Masked space is what makes one
stored patch survive:

* a per-cell re-render (``${cell}`` instead of a frozen ``pll_top``),
* a PDK-profile swap (``${qrc_deck_dir}`` instead of ``/pdk/tsmc22ull/qrc``),
* a catalog upgrade that only shifts line numbers.

Everything here is pure pydantic v2 with ``extra="forbid"``; the matching
engine lives in :mod:`auto_ext.core.patch` and imports these. Keeping the
models in their own module breaks the cycle that would otherwise exist
between :class:`HunkOutcome` (needs :class:`PatchStatus`) and the engine
(needs :class:`TemplatePatch`).

Mask grammar, in one line: ``${name}`` is a Jinja-variable slot, ``$$`` is
a literal ``$``, everything else is literal text. Storage is LF-only.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Stage(str, Enum):
    """The five Cadence steps, mirroring ``runner.STAGE_ORDER``.

    Kept as an enum here (rather than the runner's ``tuple[str, ...]``) so a
    persisted patch cannot name a stage that does not exist. If a stage is
    ever added to ``runner.STAGE_ORDER`` it must be mirrored here.

    :class:`auto_ext.model.common.Stage` is the structurally identical enum on
    the model side; ``tests/catalog/test_catalog.py`` asserts the two never
    drift apart. Collapsing them into one is a C2 job -- ``core`` must not
    import ``model`` while ``model`` imports ``core`` leaf modules.
    """

    SI = "si"
    STRMOUT = "strmout"
    CALIBRE = "calibre"
    QUANTUS = "quantus"
    JIVARO = "jivaro"


class FuzzyPolicy(str, Enum):
    """What a recipe does when a hunk can only be placed by similarity.

    ``BLOCK`` is the default and refuses the run. ``ACCEPT`` is a deliberate,
    typed, reviewable opt-in; it only ever downgrades :attr:`PatchStatus.REVIEW`
    -- ``AMBIGUOUS`` / ``LOST`` / ``OVERLAP`` block under every policy because
    those are not "fuzzy", they are "we do not know where this goes".
    """

    BLOCK = "block"
    ACCEPT = "accept"


class PatchStatus(str, Enum):
    """Outcome of placing one hunk into a freshly generated file."""

    CLEAN = "clean"          # exact / masked-exact match at full context
    SHIFTED = "shifted"      # placed with reduced ctx, wildcards, ws-norm or ordinal
    REVIEW = "review"        # placed by similarity only -- needs human sign-off
    ABSORBED = "absorbed"    # the catalog now emits `after`; the patch is redundant
    NOOP = "noop"            # `before` == `after` once materialised
    AMBIGUOUS = "ambiguous"  # >1 candidate site, no way to choose
    LOST = "lost"            # the anchor is gone entirely
    OVERLAP = "overlap"      # collides with another hunk's resolved range
    DISABLED = "disabled"    # enabled=false; recorded but not applied


#: Statuses that refuse to start the stage. See ``02-patch.md`` section 3.3:
#: a mis-placed patch in this domain does not crash, it silently produces a
#: netlist with the wrong parasitics, so "applied with a warning" is not an
#: acceptable default.
BLOCKING_STATUSES: frozenset[PatchStatus] = frozenset(
    {
        PatchStatus.REVIEW,
        PatchStatus.AMBIGUOUS,
        PatchStatus.LOST,
        PatchStatus.OVERLAP,
    }
)

#: Statuses that let the run proceed.
OK_STATUSES: frozenset[PatchStatus] = frozenset(
    {
        PatchStatus.CLEAN,
        PatchStatus.SHIFTED,
        PatchStatus.ABSORBED,
        PatchStatus.NOOP,
        PatchStatus.DISABLED,
    }
)

#: Worst-first ordering for the UI badge colour. Higher == worse.
STATUS_SEVERITY: dict[PatchStatus, int] = {
    PatchStatus.CLEAN: 0,
    PatchStatus.DISABLED: 1,
    PatchStatus.NOOP: 2,
    PatchStatus.ABSORBED: 3,
    PatchStatus.SHIFTED: 4,
    PatchStatus.REVIEW: 5,
    PatchStatus.AMBIGUOUS: 6,
    PatchStatus.LOST: 7,
    PatchStatus.OVERLAP: 8,
}


class Binding(str, Enum):
    """How ``${slot}`` tokens are compiled while searching for an anchor."""

    EXACT = "exact"                # every slot -> its current value
    CHANGED_WILD = "changed_wild"  # slots whose value drifted -> wildcard
    ALL_WILD = "all_wild"          # every slot -> wildcard


class BaseFingerprint(BaseModel):
    """What the patch was captured against.

    ``masked_sha256`` is the fast-path key: when the masked render of the new
    base hashes to the same value, the template source, the profile and every
    non-masked value are identical to capture time, so the recorded line
    offsets are still valid -- *including* for a different cell, because the
    cell lives in a slot and never reaches the masked text.

    The other three fields never gate anything; they exist so that "why did
    this stop matching?" has a diffable answer in the UI.
    """

    model_config = ConfigDict(extra="forbid")

    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    masked_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_version: str | None = None
    profile_id: str | None = None
    captured_at: datetime


class PatchHunk(BaseModel):
    """One contiguous manual edit, expressed in MASKED space.

    ``before`` / ``after`` / ``context_*`` are whole strings (not line lists)
    so a YAML dump emits them as block scalars that stay readable and
    hand-editable; the ``*_lines`` properties give the keepends line lists the
    matcher wants.

    A hunk with an empty ``before`` is a pure insertion and therefore needs
    *both* context anchors: without them there is nothing to place it against.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[0-9a-f]{8}$")
    enabled: bool = True

    #: Free text written by the user: *why* this edit exists. Surfaced in the
    #: UI row and copied into ``run.json`` so the reason survives the person.
    intent: str = ""

    before: str = ""
    after: str = ""
    context_before: str = ""
    context_after: str = ""

    #: Force ``start == 0`` / ``end == len(base)`` when matching at full
    #: context. Set when the capture-time context window reached the file edge.
    anchored_at_head: bool = False
    anchored_at_tail: bool = False

    #: "this was match k of n at capture time" -- a cheap and very effective
    #: disambiguator for repeated structures (two ``output_db`` blocks, the
    #: ``*cmnLSFSlaveTbl`` / ``*cmnGridSlaveTbl`` twins in a ``.qci``, ...).
    occurrence_index: int | None = Field(default=None, ge=0)
    occurrence_count: int | None = Field(default=None, ge=1)

    #: slot -> value at capture time. Powers the ``CHANGED_WILD`` tier and the
    #: "tech_name: tsmc22ull_1p10m -> tsmc16ffc_1p13m" diagnostic table.
    captured_values: dict[str, str] = Field(default_factory=dict)

    #: 0-indexed line in the capture-time base. ONLY read by the fast path,
    #: which additionally verifies the slice before trusting it. The ladder
    #: never looks at this field.
    recorded_start: int = Field(default=0, ge=0)

    # ---- derived ----

    @property
    def before_lines(self) -> list[str]:
        return self.before.splitlines(keepends=True)

    @property
    def after_lines(self) -> list[str]:
        return self.after.splitlines(keepends=True)

    @property
    def context_before_lines(self) -> list[str]:
        return self.context_before.splitlines(keepends=True)

    @property
    def context_after_lines(self) -> list[str]:
        return self.context_after.splitlines(keepends=True)

    @property
    def is_insertion(self) -> bool:
        return not self.before

    # ---- validation ----

    @field_validator("before", "after", "context_before", "context_after")
    @classmethod
    def _no_crlf(cls, value: str) -> str:
        if "\r" in value:
            raise ValueError("patch text must use LF line endings only")
        return value

    @model_validator(mode="after")
    def _check(self) -> PatchHunk:
        if not self.before and not self.after:
            raise ValueError("hunk is empty on both sides")
        if not self.before and not (self.context_before and self.context_after):
            raise ValueError(
                "a pure-insertion hunk needs BOTH context anchors to be placed"
            )
        if (
            self.occurrence_index is not None
            and self.occurrence_count is not None
            and self.occurrence_index >= self.occurrence_count
        ):
            raise ValueError("occurrence_index out of range")
        return self


class TemplatePatch(BaseModel):
    """Every manual edit a recipe makes to ONE generated file.

    A patch is per-(recipe, template), never per-cell: it is applied to every
    DUT's render. That is only sound because the stored text is masked --
    per-cell values live in ``${slots}``, not in the patch body.
    """

    model_config = ConfigDict(extra="forbid")

    stage: Stage
    template_id: str = Field(pattern=r"^[a-z0-9_]+/[A-Za-z0-9_.-]+$")
    base: BaseFingerprint
    hunks: list[PatchHunk] = Field(default_factory=list)
    on_fuzzy: FuzzyPolicy = FuzzyPolicy.BLOCK

    @model_validator(mode="after")
    def _unique_ids(self) -> TemplatePatch:
        ids = [h.id for h in self.hunks]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate hunk id")
        return self

    @property
    def enabled_count(self) -> int:
        return sum(1 for h in self.hunks if h.enabled)

    def hunk(self, hunk_id: str) -> PatchHunk | None:
        """Look one hunk up by id; ``None`` when it is not in this patch."""
        for candidate in self.hunks:
            if candidate.id == hunk_id:
                return candidate
        return None


class HunkOutcome(BaseModel):
    """One hunk's fate during one run. Archived verbatim in ``run.json``."""

    model_config = ConfigDict(extra="forbid")

    hunk_id: str
    intent: str = ""
    status: PatchStatus
    #: Already-rendered human sentence from ``Fuzz.describe()``.
    fuzz: str = ""
    start_line: int | None = None
    end_line: int | None = None
    udiff: str = ""
    message: str = ""


class StagePatchReport(BaseModel):
    """Per-(stage, template) patch outcome, stored alongside the recipe
    snapshot in ``run.json`` so "what exactly ran" is answerable months later
    without any external state."""

    model_config = ConfigDict(extra="forbid")

    stage: Stage
    template_id: str
    fast_path: bool = False
    outcomes: list[HunkOutcome] = Field(default_factory=list)
    blocked: bool = False


__all__ = [
    "BLOCKING_STATUSES",
    "OK_STATUSES",
    "STATUS_SEVERITY",
    "BaseFingerprint",
    "Binding",
    "FuzzyPolicy",
    "HunkOutcome",
    "PatchHunk",
    "PatchStatus",
    "Stage",
    "StagePatchReport",
    "TemplatePatch",
]
