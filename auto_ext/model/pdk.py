"""PdkProfile -- one per process technology, discovered by scanning, invisible day to day.

A profile collects everything that is a *fact about the PDK* and therefore
must not travel inside a Recipe. Today those facts are scattered across three
places:

(a) ``project.yaml``: ``tech_name`` / ``tech_name_env_vars`` / ``layer_map`` /
    ``paths.*`` / ``env_overrides``;
(b) literals frozen into the templates (``assura_tech.lib``, the two 27-name
    power/ground lists, ``preserveCellList.txt``, ``-technology_corner
    "TYPICAL"``);
(c) enumerations that a hand-written manifest turned into knobs
    (``lvs_variant.choices = [wodio, widio]``).

The file lives at ``config/profiles/<profile_id>.yaml``; its health cache
lives next to it as ``config/profiles/<profile_id>.health.json`` and is
gitignored. See ``docs/refactor/01-schema.md`` section 1.1.

Two asymmetries this schema is deliberately built to express (they are real
observations from ``docs/calibre_raw.txt``, see ``05-catalog-critique.md`` B4):

* The LVS deck and the QRC deck carry **independent** runset versions --
  the one real sample has ``LVS/Ver_Plus_1.0l_0.9`` next to
  ``QRC/Ver_Plus_1.0a``. Nothing may force them to agree.
* The QRC deck directory has **one level more** than the LVS deck directory
  (``.../<pdk_subdir>/QCI_deck`` vs ``.../<pdk_subdir>``). This is precisely
  why ``qrc.dir_expr`` cannot be derived from ``lvs_decks.dir_expr`` with the
  ``|parent`` filter and has to be stored on its own.

Both ``dir_expr`` fields are optional. A profile produced by scanning a
machine where the PDK setup script was never sourced carries ``None`` there
and an empty ``corners`` / ``variants`` table -- never an invented default.
:mod:`auto_ext.core.health` turns each such hole into one check with a
concrete fix hint.

Import direction: this module sits directly on top of
:mod:`auto_ext.model.common` and imports nothing else from ``auto_ext.model``.
:mod:`auto_ext.model.run` and :mod:`auto_ext.model.recipe` import *this* one
(a ``RunRecord`` snapshots a whole profile), so a dependency in the other
direction would be a cycle.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import Field, field_validator, model_validator

from auto_ext.model.common import Base, Frozen, PathExpr, Slug, utcnow

__all__ = [
    "DEFAULT_CDL_INCLUDE_FILE",
    "DEFAULT_LAYER_MAP",
    "DEFAULT_TECH_LIBRARY_FILE",
    "DEFAULT_TECH_NAME_ENV_VARS",
    "PDK_PROFILE_SCHEMA_VERSION",
    "CheckStatus",
    "CornerSpec",
    "LvsDeckSet",
    "LvsDeckVariant",
    "ParasiticDeviceContract",
    "PdkCheck",
    "PdkCheckKind",
    "PdkCheckResult",
    "PdkHealthReport",
    "PdkProfile",
    "QrcDeck",
    #: Re-exported so this module owns its own clock attribute, the way every
    #: other model module does (``tests/conftest.py`` rebinds it per module).
    "utcnow",
]

#: ``config/profiles/<id>.yaml`` schema version.
PDK_PROFILE_SCHEMA_VERSION = 1

#: Candidates for auto-deriving ``tech_name``, moved verbatim from
#: ``ProjectConfig.tech_name_env_vars``.
DEFAULT_TECH_NAME_ENV_VARS: tuple[str, ...] = (
    "PDK_TECH_FILE",
    "PDK_LAYER_MAP_FILE",
    "PDK_DISPLAY_FILE",
)

#: Lifted from the Quantus templates' ``-technology_library_file`` literal.
DEFAULT_TECH_LIBRARY_FILE = "$env(SETUP_ROOT)/assura_tech.lib"

#: Lifted from ``ProjectConfig.layer_map``.
DEFAULT_LAYER_MAP = "${PDK_LAYER_MAP_FILE}"

#: Lifted from the si template's ``incFILE`` literal.
DEFAULT_CDL_INCLUDE_FILE = "$calibre_source_added_place"

#: Env-var names, as accepted by :func:`auto_ext.core.env.discover_required_vars`.
_ENV_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"

#: ``extra_paths`` keys become ``pdk.paths.<key>`` in the render context, so
#: they have to survive as Jinja attribute names.
_PATH_KEY_PATTERN = r"^[a-z_][a-z0-9_]*$"


# ---- value tables ------------------------------------------------------------


class CornerSpec(Base):
    """One process corner.

    A Recipe only ever writes the semantic ``name``; the tool literal is bound
    here. That is the seam that makes a Recipe portable across technologies:
    ``corner: typical`` means "whatever this PDK calls typical", not the string
    ``TYPICAL``.

    Origin: ``-technology_corner "TYPICAL"``, hardcoded in
    ``templates/quantus/*.cmd.j2``. Nothing else in the flow could change it.
    """

    #: Semantic name a Recipe refers to: ``typical`` / ``rcworst`` / ``cworst``.
    name: Slug
    #: Literal handed to Quantus as ``-technology_corner``.
    technology_corner: str = Field(min_length=1)
    #: Suggested temperature for this corner; used when a Recipe leaves it unset.
    default_temperature_c: float | None = None
    #: Older spellings kept so a migrated Recipe still resolves.
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None


class LvsDeckVariant(Base):
    """One Calibre LVS deck variant.

    Origin: the ``lvs_variant`` knob of
    ``templates/calibre/calibre_lvs.qci.j2.manifest.yaml`` (``type=str``,
    ``choices=[wodio, widio]``). Once the knob layer is gone, "which variants
    exist" is a PDK fact and lands here; "which one to use this time" stays a
    Recipe choice.

    Only ``wodio`` has ever been observed in a real export
    (``docs/calibre_raw.txt`` line 2). ``widio`` exists solely in the
    hand-written manifest, which is why this table is discovered by globbing
    the deck directory instead of being seeded with both.
    """

    #: Semantic name; ``Recipe.lvs.deck_variant`` refers to it.
    name: Slug
    #: Middle segment of the rules-file name, e.g. ``wodio``.
    rules_suffix: str = Field(min_length=1)
    description: str | None = None
    #: Whether this variant inherently wants ``*cmnVConnectNamesState: ALL``.
    #: ``None`` leaves the decision to the Recipe.
    connect_by_name_default: bool | None = None


class LvsDeckSet(Base):
    """How to locate the Calibre LVS rules file.

    Origin: line 1 of ``templates/calibre/calibre_lvs.qci.j2``::

        *lvsRulesFile: [[calibre_lvs_dir]]/[[calibre_lvs_basename]].[[lvs_variant]].qcilvs

    -- the directory came from ``project.paths.calibre_lvs_dir``, the basename
    was auto-derived by ``runner._build_context`` as
    ``PurePosixPath(calibre_lvs_dir).name``, and the suffix came from a knob.
    All three segments are explicit profile state now.
    """

    #: Directory holding ``<basename>.<suffix>.qcilvs``. Moved from
    #: ``project.yaml paths.calibre_lvs_dir`` (default there:
    #: ``$calibre_source_added_place|parent``). ``None`` means "not discovered,
    #: a human has to fill this in" -- never a guess.
    dir_expr: PathExpr | None = None
    #: Moved from the ``calibre_lvs_basename`` auto-derivation in
    #: ``runner._build_context``. ``None`` keeps that PDK convention: take the
    #: last segment of the resolved ``dir_expr`` (see :meth:`basename_for_dir`).
    basename: str | None = None
    #: Filename assembly, replacing the ``.{suffix}.qcilvs`` hardcoded in the
    #: template. Must use both ``{basename}`` and ``{suffix}``.
    filename_pattern: str = "{basename}.{suffix}.qcilvs"
    variants: list[LvsDeckVariant] = Field(default_factory=list)
    default_variant: Slug | None = None
    #: Runset version segment of the deck path (``Ver_Plus_1.0l_0.9`` in the one
    #: real sample). Provenance only -- ``dir_expr`` is the locator. It is
    #: recorded separately because it is legitimately allowed to differ from
    #: :attr:`QrcDeck.runset_version`.
    runset_version: str | None = None

    @field_validator("filename_pattern")
    @classmethod
    def _pattern_has_both_slots(cls, v: str) -> str:
        missing = [slot for slot in ("{basename}", "{suffix}") if slot not in v]
        if missing:
            raise ValueError(f"filename_pattern {v!r} is missing {', '.join(missing)}")
        return v

    @model_validator(mode="after")
    def _check(self) -> LvsDeckSet:
        names = [v.name for v in self.variants]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate lvs deck variant names: {names}")
        if self.default_variant and self.default_variant not in names:
            raise ValueError(f"default_variant {self.default_variant!r} not in {names}")
        return self

    @property
    def variant_names(self) -> list[str]:
        return [v.name for v in self.variants]

    def variant(self, name: str) -> LvsDeckVariant | None:
        for v in self.variants:
            if v.name == name:
                return v
        return None

    def basename_for_dir(self, resolved_dir: str) -> str:
        """Return the explicit :attr:`basename`, else the last path segment.

        ``resolved_dir`` must already be env-substituted; POSIX semantics are
        used unconditionally because every rendered artefact targets Linux.
        """

        if self.basename:
            return self.basename
        return PurePosixPath(resolved_dir).name

    def filename_for(self, variant_name: str, *, resolved_dir: str = "") -> str:
        """Assemble the rules-file name for ``variant_name``.

        Raises :class:`KeyError` when the variant is not in the table -- that
        is a Recipe referring to a variant this PDK does not have, and it must
        not silently produce a path that cannot exist.
        """

        variant = self.variant(variant_name)
        if variant is None:
            raise KeyError(
                f"lvs deck variant {variant_name!r} is not in this profile; "
                f"known variants: {self.variant_names or '(none discovered)'}"
            )
        return self.filename_pattern.format(
            basename=self.basename_for_dir(resolved_dir), suffix=variant.rules_suffix
        )

    def glob_pattern(self) -> str:
        """Filename glob matching every variant of this deck.

        Used by the health check to answer "is there any rules file at all in
        that directory", which is a better question than "does one specific
        variant exist" while the variant table is still empty. An unset
        :attr:`basename` widens the glob rather than guessing a name.
        """

        return self.filename_pattern.format(basename=self.basename or "*", suffix="*")


class QrcDeck(Base):
    """The QRC deck directory and the file names conventionally inside it.

    Origin: ``project.yaml paths.qrc_deck_dir`` plus two names frozen into the
    templates -- ``query_cmd`` (the Calibre ``lvsPostTriggers`` line) and
    ``preserveCellList.txt`` (Quantus
    ``-parasitic_blocking_device_cells_file``).

    This is a separate object from :class:`LvsDeckSet` on purpose: in the one
    real sample the QRC deck sits at
    ``.../QRC/Ver_Plus_1.0a/CFXXX/QCI_deck`` while the LVS deck sits at
    ``.../LVS/Ver_Plus_1.0l_0.9/CFXXX``. Different version segment, one extra
    directory level. ``config/project.yaml`` already says as much in prose
    ("No standard env-var convention"); here it is structure.
    """

    #: ``None`` means "not discovered". There is no env-var convention for
    #: this path, so scanning can only guess it, and a wrong guess is worse
    #: than an empty field.
    dir_expr: PathExpr | None = None
    query_cmd_name: str = "query_cmd"
    preserve_cell_list_name: str = "preserveCellList.txt"
    #: Runset version segment (``Ver_Plus_1.0a`` in the sample). Provenance
    #: only, and explicitly allowed to differ from the LVS deck's.
    runset_version: str | None = None


class ParasiticDeviceContract(Base):
    """The parasitic-device names Quantus writes and Jivaro reads back.

    Critique D4: Quantus maps parasitics onto ``presistor`` / ``pcapacitor``
    (``ext.cmd.j2`` ``-res_component`` / ``-cap_component``) while Jivaro
    binds four models -- ``analogLib/presistor/symbol``,
    ``analogLib/pcapacitor/symbol``, ``analogLib/pinductor/symbol``,
    ``analogLib/pmind/symbol`` (``jivaro/default.xml.j2`` lines 9-12). Change
    one side without the other and Jivaro reads no devices at all, silently.
    The validator below makes that impossible to express.

    The inductor and mutual-inductance entries have no Quantus counterpart
    because RLCK extraction is not enabled in this flow. That asymmetry is
    recorded rather than smoothed over: :attr:`ind_component` and
    :attr:`mutual_component` stay ``None`` until someone turns RLCK on.

    Unverified: that these four ``analogLib`` names are the right ones for the
    user's PDK (``docs/refactor/OFFICE_TODO.md``, "parasitic device mapping").
    The values below are the ones the current templates ship with, so a
    migration is byte-neutral; they are data, not code.
    """

    res_component: str = "presistor"
    cap_component: str = "pcapacitor"
    ind_component: str | None = None
    mutual_component: str | None = None

    res_model: str = "analogLib/presistor/symbol"
    cap_model: str = "analogLib/pcapacitor/symbol"
    ind_model: str = "analogLib/pinductor/symbol"
    mutual_model: str = "analogLib/pmind/symbol"

    @field_validator("res_model", "cap_model", "ind_model", "mutual_model")
    @classmethod
    def _three_segments(cls, v: str) -> str:
        if len(v.split("/")) != 3 or not all(v.split("/")):
            raise ValueError(f"{v!r} must be 'library/cell/view', e.g. analogLib/presistor/symbol")
        return v

    @model_validator(mode="after")
    def _sides_agree(self) -> ParasiticDeviceContract:
        pairs = [
            ("res", self.res_component, self.res_model),
            ("cap", self.cap_component, self.cap_model),
            ("ind", self.ind_component, self.ind_model),
            ("mutual", self.mutual_component, self.mutual_model),
        ]
        for kind, component, model in pairs:
            if component is None:
                continue
            cell = model.split("/")[1]
            if cell != component:
                raise ValueError(
                    f"parasitic {kind} contract is broken: Quantus writes {component!r} "
                    f"but Jivaro reads {model!r} (cell {cell!r}). Both sides must name the "
                    "same device, or Jivaro silently extracts nothing."
                )
        return self

    @property
    def model_cells(self) -> dict[str, str]:
        """``{"res": "presistor", ...}`` -- the cell name of each bound model."""

        return {
            "res": self.res_model.split("/")[1],
            "cap": self.cap_model.split("/")[1],
            "ind": self.ind_model.split("/")[1],
            "mutual": self.mutual_model.split("/")[1],
        }


# ---- health ------------------------------------------------------------------


class PdkCheckKind(StrEnum):
    """What a check looks at. ``target`` is interpreted per kind."""

    ENV_VAR = "env_var"          #: target is an env-var name; must be set and non-empty
    FILE_EXISTS = "file_exists"  #: target is a PathExpr; must resolve to a regular file
    DIR_EXISTS = "dir_exists"    #: target is a PathExpr; must resolve to a directory
    GLOB_NONEMPTY = "glob_nonempty"  #: target is "<PathExpr>/<glob>"; needs >= 1 hit
    ON_PATH = "on_path"          #: target is an executable name; looked up on PATH
    FIELD_SET = "field_set"      #: target is a dotted profile field; must be non-empty


class CheckStatus(StrEnum):
    """Outcome of one check.

    ``UNKNOWN`` is not a soft ``FAIL``: it means the check could not be
    evaluated at all (a path expression still containing unresolved env refs,
    a field the scan was never able to fill). The two need different fix
    hints, so they are different states.
    """

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class PdkCheck(Base):
    """One health check, declared. Results live in :class:`PdkCheckResult`.

    What this replaces: the implicit check that ``runner._discover_env_vars``
    plus ``EnvResolution.require()`` performed at the moment of launching an
    EDA binary -- no UI, no advance warning, no fix hint.
    """

    check_id: Slug
    #: One-line label. English, like every other string this app shows.
    title: str = Field(min_length=1)
    kind: PdkCheckKind
    #: Meaning depends on ``kind``.
    target: str = Field(min_length=1)
    #: ``False`` downgrades a failure to a warning: it is reported but it does
    #: not stop a run.
    required: bool = True
    #: Shown when the check is not OK. Must name the exact command to run or
    #: the exact field to edit -- "check your environment" is not a fix hint.
    fix_hint: str = Field(min_length=1)


class PdkCheckResult(Frozen):
    """One check's outcome. Cached in ``profiles/<id>.health.json``.

    ``title`` and ``fix_hint`` are copied in from the declaration so a reader
    of the cache file (CLI, Setup drawer) can render a complete report without
    also loading and re-resolving the profile.
    """

    check_id: Slug
    status: CheckStatus
    required: bool = True
    title: str = ""
    #: What was actually seen: the value, the resolved path, the miss reason.
    observed: str | None = None
    message: str | None = None
    #: Only meaningful when ``status`` is not OK.
    fix_hint: str | None = None
    checked_at: datetime

    @property
    def ok(self) -> bool:
        return self.status is CheckStatus.OK

    @property
    def blocking(self) -> bool:
        """This result alone is enough to say "you cannot run yet"."""

        return self.required and self.status in (CheckStatus.FAIL, CheckStatus.UNKNOWN)


class PdkHealthReport(Frozen):
    """The answer to "can I run right now", and the only source of it.

    Written to ``config/profiles/<id>.health.json`` (gitignored). The CLI's
    ``check-env`` and the GUI Setup drawer both read this rather than
    re-deriving their own verdicts.
    """

    profile_id: Slug
    checked_at: datetime
    results: list[PdkCheckResult] = Field(default_factory=list)
    #: sha256 of the profile that was checked; a cache whose fingerprint no
    #: longer matches the profile on disk is stale and must be re-run.
    profile_sha256: str | None = None

    @model_validator(mode="after")
    def _unique_ids(self) -> PdkHealthReport:
        ids = [r.check_id for r in self.results]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate check_id in report: {sorted({i for i in ids if ids.count(i) > 1})}")
        return self

    @property
    def ok(self) -> bool:
        """Every check passed, warnings included."""

        return all(r.ok for r in self.results)

    @property
    def can_run(self) -> bool:
        """No required check failed or came back undetermined."""

        return not any(r.blocking for r in self.results)

    @property
    def blocking(self) -> list[PdkCheckResult]:
        return [r for r in self.results if r.blocking]

    @property
    def warnings(self) -> list[PdkCheckResult]:
        return [r for r in self.results if not r.ok and not r.blocking]

    @property
    def exit_code(self) -> int:
        """0 when a run may proceed, 1 otherwise -- for ``check-env``."""

        return 0 if self.can_run else 1

    def result(self, check_id: str) -> PdkCheckResult | None:
        for r in self.results:
            if r.check_id == check_id:
                return r
        return None

    def counts(self) -> dict[str, int]:
        """``{"ok": 12, "warn": 1, "fail": 2, "unknown": 0}``."""

        out = {s.value: 0 for s in CheckStatus}
        for r in self.results:
            out[r.status.value] += 1
        return out


# ---- the profile -------------------------------------------------------------


class PdkProfile(Base):
    """Everything bound to one process technology. ``config/profiles/<id>.yaml``."""

    schema_version: int = PDK_PROFILE_SCHEMA_VERSION
    #: File stem; Workspace and RunRecord refer to the profile by it.
    profile_id: Slug
    #: Shown in the UI, e.g. "HN001 22nm (runset 2024.3)".
    display_name: str = Field(min_length=1)
    description: str | None = None

    # ---- identity ----
    #: Moved from ``ProjectConfig.tech_name``; Quantus ``-technology_name``.
    tech_name: str | None = None
    #: Moved from ``ProjectConfig.tech_name_env_vars``. Candidates for
    #: auto-deriving ``tech_name``, with the "take the parent directory name"
    #: semantics of :func:`auto_ext.core.env.derive_parent_dir_from_env_candidates`.
    tech_name_env_vars: list[str] = Field(
        default_factory=lambda: list(DEFAULT_TECH_NAME_ENV_VARS)
    )
    #: Moved out of the Quantus template, where it was the literal
    #: ``-technology_library_file "$env(SETUP_ROOT)/assura_tech.lib"``.
    tech_library_file: PathExpr = DEFAULT_TECH_LIBRARY_FILE
    #: Moved from ``ProjectConfig.layer_map``; strmout ``-layerMap``.
    layer_map: PathExpr = DEFAULT_LAYER_MAP
    #: Moved out of the si template, where it was ``incFILE =
    #: "$calibre_source_added_place"``. A list because critique C4 asks for it:
    #: a PDK may need several CDL files prepended. ``si.env`` has exactly one
    #: slot today, so :attr:`cdl_include_file` refuses to flatten more than one
    #: rather than silently dropping the rest.
    cdl_include_files: list[PathExpr] = Field(
        default_factory=lambda: [DEFAULT_CDL_INCLUDE_FILE]
    )

    # ---- env resolution ----
    #: Moved from ``ProjectConfig.env_overrides``; semantics unchanged
    #: (override > shell > missing).
    env_overrides: dict[str, str] = Field(default_factory=dict)
    #: New. The old code re-derived this by scanning template text on every
    #: run, so it drifted whenever a template changed. Declaring it makes
    #: ``check-env`` answerable without reading a single template.
    required_env: list[str] = Field(default_factory=list)

    # ---- deck directories ----
    lvs_decks: LvsDeckSet = Field(default_factory=LvsDeckSet)
    qrc: QrcDeck = Field(default_factory=QrcDeck)
    #: Escape hatch: any further path key, i.e. the entries of the old
    #: ``ProjectConfig.paths`` other than ``calibre_lvs_dir`` / ``qrc_deck_dir``.
    #: Exposed to templates as ``pdk.paths.<key>``.
    extra_paths: dict[str, PathExpr] = Field(default_factory=dict)

    # ---- value tables ----
    corners: list[CornerSpec] = Field(default_factory=list)
    default_corner: Slug | None = None
    #: Moved out of ``calibre_lvs.qci.j2``, where the whole ``*lvsPowerNames``
    #: line (27 names) was frozen into the template. A pure PDK fact.
    power_names: list[str] = Field(default_factory=list)
    #: Same, from ``*lvsGroundNames``.
    ground_names: list[str] = Field(default_factory=list)
    parasitics: ParasiticDeviceContract = Field(default_factory=ParasiticDeviceContract)

    # ---- health ----
    checks: list[PdkCheck] = Field(default_factory=list)

    # ---- provenance ----
    #: Where this profile was scanned from (setup script, raw export, env).
    discovered_from: list[str] = Field(default_factory=list)
    scanned_at: datetime = Field(default_factory=utcnow)
    #: Set once a human edits the file, so a re-scan reports conflicts instead
    #: of overwriting their work.
    hand_edited: bool = False

    @field_validator("tech_name_env_vars", "required_env")
    @classmethod
    def _env_names(cls, v: list[str]) -> list[str]:
        for name in v:
            if not re.match(_ENV_NAME_PATTERN, name):
                raise ValueError(f"{name!r} is not a valid environment variable name")
        if len(v) != len(set(v)):
            raise ValueError(f"duplicate env var names: {v}")
        return v

    @field_validator("extra_paths")
    @classmethod
    def _path_keys(cls, v: dict[str, str]) -> dict[str, str]:
        for key in v:
            if not re.match(_PATH_KEY_PATTERN, key):
                raise ValueError(
                    f"extra_paths key {key!r} must match {_PATH_KEY_PATTERN} "
                    "(it becomes pdk.paths.<key> in the render context)"
                )
        return v

    @field_validator("corners")
    @classmethod
    def _unique_corners(cls, v: list[CornerSpec]) -> list[CornerSpec]:
        names = [c.name for c in v]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate corner names: {names}")
        taken = set(names)
        for corner in v:
            for alias in corner.aliases:
                if alias in taken and alias != corner.name:
                    raise ValueError(
                        f"corner alias {alias!r} on {corner.name!r} collides with another corner"
                    )
                taken.add(alias)
        return v

    @field_validator("checks")
    @classmethod
    def _unique_checks(cls, v: list[PdkCheck]) -> list[PdkCheck]:
        ids = [c.check_id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate check_id: {ids}")
        return v

    @model_validator(mode="after")
    def _default_corner_exists(self) -> PdkProfile:
        if self.default_corner and self.default_corner not in {c.name for c in self.corners}:
            raise ValueError(
                f"default_corner {self.default_corner!r} not in "
                f"{[c.name for c in self.corners]}"
            )
        return self

    # ---- lookups ----

    def corner(self, name: str) -> CornerSpec | None:
        """Resolve a semantic corner name (or one of its aliases)."""

        for c in self.corners:
            if c.name == name or name in c.aliases:
                return c
        return None

    def lvs_variant(self, name: str) -> LvsDeckVariant | None:
        return self.lvs_decks.variant(name)

    @property
    def cdl_include_file(self) -> str | None:
        """The single ``incFILE`` value for ``si.env``.

        ``si.env`` has one slot for it. Rendering several CDL includes needs a
        SKILL list form nobody has verified against a real si run, so more than
        one entry is refused outright instead of quietly using the first.
        """

        if not self.cdl_include_files:
            return None
        if len(self.cdl_include_files) > 1:
            raise NotImplementedError(
                f"profile {self.profile_id!r} lists {len(self.cdl_include_files)} CDL include "
                "files, but si.env has a single incFILE slot and the SKILL list form for it "
                "has not been verified against a real si run "
                "(docs/refactor/OFFICE_TODO.md, 'device CDL prelude'). "
                "Keep cdl_include_files to one entry until it has."
            )
        return self.cdl_include_files[0]

    @property
    def deck_versions(self) -> tuple[str | None, str | None]:
        """``(lvs_runset_version, qrc_runset_version)``.

        They are allowed to differ; the one real sample has
        ``Ver_Plus_1.0l_0.9`` against ``Ver_Plus_1.0a``. Nothing in this
        codebase may treat a mismatch as an error.
        """

        return (self.lvs_decks.runset_version, self.qrc.runset_version)

    def fingerprint(self) -> str:
        """sha256 of the canonical JSON form, minus the fields that are just
        bookkeeping (``scanned_at``, ``hand_edited``).

        Used to decide whether a cached health report still describes this
        profile.
        """

        payload = self.model_dump(mode="json", exclude={"scanned_at", "hand_edited"})
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
