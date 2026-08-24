"""Recipe -- the shared, semantic, portable description of how to extract.

Written against ``docs/refactor/01-schema.md`` section 1.2, with the
corrections from ``docs/refactor/05-catalog-critique.md`` applied. A Recipe is
the answer to "what extraction conditions were used", and it is deliberately
useless as an answer to "on which machine", "for which cell" or "with which
PDK" -- those are the ResourceProfile, the CellBook and the PdkProfile.

What it absorbs:

* the whole four-layer knob system (manifest < project < task < CLI), which
  covered exactly seven values;
* the several hundred literals typed into the five templates that are plainly
  extraction conditions (corner, extract type, metal fill, coupling
  thresholds, the entire si.env device-check block);
* ``TaskSpec.jivaro`` plus ``JivaroOverride``. The per-cell override is
  dropped on purpose: a cell that needs different reduction settings needs a
  second Recipe, not a patch on the first;
* ``TaskSpec.continue_on_lvs_fail``;
* the quantus slot of ``ProjectConfig.templates`` -- choosing between an
  extracted view and a DSPF by pointing a path at a different ``.j2`` becomes
  ``output.emit``, which is a list, so a run can finally emit both;
* ``core/clone_template.py``'s whole-file fork, demoted to ``patches``.

What it refuses: library / cell / view / ground_net / out_file (cells), any
absolute path or process literal (profile), any ``${WORK_ROOT}``-derived path
(workspace), any core count or license wait (resources).

Every field here has a row in ``auto_ext/catalog/options.yaml`` carrying its
provenance, its landing sites and its confidence, and
``tests/model/test_recipe.py`` asserts the two never drift apart -- in both
directions. :func:`recipe_from_catalog` builds the default Recipe straight
from that table.

Deviations from section 1.2, each forced by the critique or by the catalog
cross-check, and each one deliberate:

1. ``netlist.incremental`` is split into ``not_incremental`` and
   ``renetlist_all``. The draft folded ``simNotIncremental`` and
   ``simReNetlistAll`` into one tri-state, which cannot express the
   combination the shipped template actually uses (critique 2.12).
2. The parasitic device names (``cap_component`` / ``res_component`` and the
   four Jivaro models) live on ``PdkProfile.parasitics``, not here. They are
   one contract that Quantus and Jivaro must agree on, and they are properties
   of the library (critique D4).
3. ``num_turbo`` / ``run_mt`` / ``run_hyper`` / ``license_wait`` and the
   Jivaro ``cpu`` are NOT Recipe fields. They live in :class:`ResourceProfile`
   in this module: carry a Recipe from an 8-core box to a 64-core box and a
   resource field inside it would have to be edited, which defeats the single
   property a Recipe must keep (DECISIONS.md #21, critique 2.6).
4. ``output`` grows a ``common`` group. ``device_finger_delimiter`` and the
   four ``include_*_model`` switches appear in BOTH quantus templates with the
   same value, so they are one parameter with two landing sites and therefore
   one field -- duplicating them per output form would give the catalog two
   homes for one fact. Section 1.2 also omitted them from ``DspfOutput``
   despite the template carrying them.
5. Small additions the template has and section 1.2 lacked:
   ``extraction.max_fracture_length_unit`` (a fracture length without its unit
   is meaningless), ``output.dspf.net_name_space`` and
   ``output.dspf.input_hierarchy_delimiter``.
6. The patch models come from ``docs/refactor/02-patch.md`` section 4 (masked
   anchored hunks) rather than the unified-diff sketch in section 1.2, and are
   imported from :mod:`auto_ext.core.patch_models` rather than redeclared.

Nothing here has been validated against a real Cadence installation. Rows
whose value or legal set is unconfirmed are marked in ``options.yaml`` with a
``question:`` and mirrored in ``docs/refactor/OFFICE_TODO.md``; that file is
the single place to look, and answering one of its questions is a data edit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.catalog.spec import Catalog, Owner, builtin_catalog
from auto_ext.core.errors import ConfigError
from auto_ext.core.patch_models import (
    BaseFingerprint,
    FuzzyPolicy,
    PatchHunk,
    TemplatePatch,
)
from auto_ext.model.common import (
    STAGE_ORDER,
    AsWritten,
    Base,
    Frozen,
    Slug,
    Stage,
    slugify,
    utcnow,
)
from auto_ext.model.run import JivaroSnapshot, RecipeSnapshot

__all__ = [
    "CATALOG_EXEMPT_FIELDS",
    "DUT_FALLBACK_FIELDS",
    "PROFILE_FALLBACK_FIELDS",
    "RECIPE_SCHEMA_VERSION",
    "BaseFingerprint",
    "CommonOutputSettings",
    "DspfOutput",
    "ExtractType",
    "ExtractedViewOutput",
    "ExtractionSettings",
    "FuzzyPolicy",
    "LvsSettings",
    "MetalFill",
    "NetlistSettings",
    "OutputKind",
    "OutputSettings",
    "PatchHunk",
    "Recipe",
    "RecipeRef",
    "ReductionSettings",
    "ResourceProfile",
    "RunPolicy",
    "TemplatePatch",
    "dump_recipe_yaml",
    "load_recipe",
    "load_recipe_with_raw",
    "recipe_field_paths",
    "recipe_filename",
    "recipe_from_catalog",
    "save_recipe",
]

RECIPE_SCHEMA_VERSION = 1

#: Recipe fields that are nullable on purpose and resolved through the
#: PdkProfile at render time, so their model default is ``None`` while the
#: catalog records the literal in force today. Kept small and explicit: a test
#: asserts this set is exactly the set of divergences between the two.
PROFILE_FALLBACK_FIELDS: frozenset[str] = frozenset(
    {
        #: The catalog row is ``technology_corner``, owned by the profile,
        #: because the tool literal (``"TYPICAL"``) is a process fact. The
        #: Recipe only names a semantic corner and the profile maps it -- that
        #: seam is what lets one Recipe move between PDKs.
        "extraction.corner",
        #: Catalog default is the manifest's 55.0. ``None`` here means "use
        #: the temperature this corner suggests", which is the new behaviour;
        #: 55.0 stays available through :func:`recipe_from_catalog`.
        "extraction.temperature_c",
    }
)

#: Recipe fields whose model default is ``None`` meaning "take it from the
#: DUT". The profile equivalent is :data:`PROFILE_FALLBACK_FIELDS`; this set
#: is separate because the resolution needs the cell row, not the profile, and
#: therefore happens in :func:`auto_ext.core.render._recipe_tree` against the
#: ``DutSnapshot`` rather than in ``resolve_corner``.
DUT_FALLBACK_FIELDS: frozenset[str] = frozenset(
    {
        #: "the view Quantus just wrote" -- the cell's ``out_file``. See the
        #: field's own comment for why the old literal was a bug.
        "reduction.views_to_reduce",
    }
)

#: Recipe fields with no catalog row, and why. Everything else must have one.
CATALOG_EXEMPT_FIELDS: frozenset[str] = frozenset(
    {
        # Envelope: identity, lineage and bookkeeping, not extraction settings.
        "schema_version",
        "recipe_id",
        "name",
        "description",
        "version",
        "tags",
        "derived_from",
        "updated_at",
        # The escape hatch. By definition it holds what the catalog cannot.
        "patches",
    }
)


# ---- semantic enums ---------------------------------------------------------
# Enums exist only where the renderer or the runner has to branch on the value.
# Every other closed-looking string stays a plain ``str`` with a default,
# because the legal set is guesswork and a dropdown half full of invalid
# entries is worse than a text box (DECISIONS.md #19). The catalog rows for
# these three carry the same members, and a test asserts they stay equal.


class ExtractType(StrEnum):
    """``extract -type``. Spellings unconfirmed -- see OFFICE_TODO.md."""

    RC_COUPLED = "rc_coupled"
    RC_DECOUPLED = "rc_decoupled"
    R_ONLY = "r_only"
    C_ONLY = "c_only"


class MetalFill(StrEnum):
    """``metal_fill -type``.

    ``ext.cmd.j2`` has no metal_fill section at all while ``dspf.cmd.j2``
    asks for virtual fill. That the missing section means "none" is an
    inference, not a fact: with the section absent the behaviour is whatever
    the deck defaults to, which could equally be real fill.
    """

    NONE = "none"
    VIRTUAL = "virtual"
    ACTUAL = "actual"


class OutputKind(StrEnum):
    """What one quantus stage emits."""

    EXTRACTED_VIEW = "extracted_view"
    DSPF = "dspf"


# ---- semantic groups --------------------------------------------------------


class NetlistSettings(Base):
    """si.env, the CDL netlisting conditions.

    Every one of these is a literal in ``templates/si/default.env.j2`` today
    and the si manifest declares no knobs, so not one of them can currently be
    changed. Emission order is fixed by the catalog, not by this class: si
    exported ``checkCAPPERI`` after the diode group instead of with the
    capacitor group, and regrouping it would diff against every real si.env
    our users hold.
    """

    simulator: str = "auCdl"
    view_list: list[str] = Field(default_factory=lambda: ["auCdl", "schematic"])
    stop_list: list[str] = Field(default_factory=lambda: ["auCdl"])
    #: ``simNotIncremental``. Independent of :attr:`renetlist_all` -- see the
    #: module docstring, deviation 1.
    not_incremental: bool = True
    #: ``simReNetlistAll``.
    renetlist_all: bool = False
    short_res_ohm: AsWritten = 2000.0
    preserve_res: bool = True
    check_res_val: bool = True
    check_res_size: bool = False
    preserve_cap: bool = True
    check_cap_val: bool = True
    check_cap_area: bool = False
    check_cap_peri: bool = False
    preserve_dio: bool = True
    check_dio_area: bool = True
    check_dio_peri: bool = True
    check_ldd: bool = False
    #: ``checkScale``. Only "meter" has ever been observed; whether the Calibre
    #: deck dictates it is an open office question, and if it does this field
    #: moves to the PdkProfile.
    check_scale: str = "meter"
    shrink_factor: AsWritten = 0.0
    print_inherited_conn: bool = False
    preserve_bang: bool = False
    #: ``globalPowerSig`` / ``globalGndSig``: written into the CDL ``.GLOBAL``
    #: line. Empty today, so supply resolution relies entirely on the
    #: schematic's own globals. Distinct from ``CellEntry.ground_net``, which
    #: goes to Quantus.
    global_power_sig: str = ""
    global_gnd_sig: str = ""
    display_pin_info: bool = True
    preserve_all: bool = True


class LvsSettings(Base):
    """The Calibre LVS runset conditions.

    Resource settings that used to sit here in the section 1.2 sketch
    (``num_turbo`` / ``run_mt`` / ``run_hyper`` / ``license_wait``) live in
    :class:`ResourceProfile` instead.
    """

    #: Which rule deck variant to use, by semantic name. WHICH variants exist
    #: is a PDK fact (``PdkProfile.lvs_decks.variants``); this is the choice.
    #: Only "wodio" has ever been seen in a real path -- "widio" exists only in
    #: a hand-written manifest.
    deck_variant: Slug = "wodio"
    #: Emits ``*cmnVConnectNamesState: ALL``. When false the line is omitted
    #: entirely rather than written as NONE; whether those are equivalent to
    #: Calibre is not verified.
    connect_by_name: bool = False
    report_options: str = "S"
    recognize_gates: str = "NONE"
    abort_on_supply_error: bool = False
    svdb_cci: bool = True
    device_filter_options_enabled: bool = False
    layout_device_filter_options: str = "AG RC RE RG"
    source_device_filter_options: str = "AG RC RE RG"
    #: Whether the LVS post-trigger also runs
    #: ``calibre -query_input <qrc_deck>/query_cmd -query svdb``. Always on
    #: today because it is part of one hardcoded line; a recipe that only runs
    #: LVS should be able to skip it.
    run_qrc_query: bool = True


class ExtractionSettings(Base):
    """The Quantus extraction conditions, shared by both output forms.

    On :attr:`corner` and :attr:`temperature_c`: they sit in the same
    ``process_technology`` statement, but only temperature was ever promoted
    to a knob, so today one is tunable and its neighbour is frozen -- the
    named example of parameterisation going crooked. Both are typed fields
    now, and both fall back to the profile when unset.
    """

    #: Semantic corner name, looked up in ``PdkProfile.corners`` to get the
    #: tool literal. ``None`` uses ``profile.default_corner``. The literal is
    #: never written here; that is what makes a Recipe portable across PDKs.
    corner: Slug | None = None
    #: ``None`` uses ``CornerSpec.default_temperature_c``.
    temperature_c: AsWritten | None = None

    extract_type: ExtractType = ExtractType.RC_COUPLED
    selection: str = "all"
    decoupling_factor: AsWritten = 1.0
    net_name_space: str = "SCHEMATIC"

    exclude_self_cap: bool = True
    exclude_floating_nets: bool = True
    #: The live specimen of the four-layer knob problem: manifest 5000,
    #: project.yaml 100, tasks.yaml 200, and ``--knob`` for a fourth answer.
    exclude_floating_nets_limit: int = Field(default=5000, ge=100, le=100_000)
    #: Unit and magnitude disagree with physics: 0.01 F is a 10 mF threshold.
    #: Carried over unchanged so behaviour does not silently shift, but it is
    #: NOT a verified fact -- the manifest it came from was written by hand.
    coupling_cap_threshold_absolute: AsWritten = 0.01
    coupling_cap_threshold_relative: AsWritten = 0.001
    min_res_ohm: AsWritten = 0.001
    merge_parallel_res: bool = True
    remove_dangling_res: bool = True

    metal_fill: MetalFill = MetalFill.VIRTUAL
    array_vias_spacing: str = "auto"
    max_fracture_length: str = "infinite"
    #: Meaningless apart from :attr:`max_fracture_length`, and missing from the
    #: section 1.2 sketch.
    max_fracture_length_unit: str = "MICRONS"
    max_via_array_size: str = "auto"


class CommonOutputSettings(Base):
    """Output options both quantus forms carry, with the same value.

    One parameter with two landing sites is one field. Duplicating these per
    output form would give the catalog two homes for one fact and let the two
    drift apart silently.
    """

    device_finger_delimiter: str = "@"
    include_cap_model: bool = False
    include_parasitic_cap_model: bool = False
    include_res_model: bool = False
    #: Three-valued: "true" / "false" / "comment". That it is not a boolean is
    #: certain (the template says "comment"); what the three do is unknown.
    include_parasitic_res_model: str = "comment"


class ExtractedViewOutput(Base):
    """``output_db -type extracted_view``.

    The view name is not here -- it comes from ``CellEntry.out_file``, which
    is identity. Neither are ``cap_component`` / ``res_component``: those are
    half of the parasitic device contract and live on
    ``PdkProfile.parasitics``, because changing them on one side without the
    other means Jivaro cannot read what Quantus wrote.
    """

    cap_property_name: str = "c"
    res_property_name: str = "r"
    enable_cellview_check: bool = False


class DspfOutput(Base):
    """``output_db -type dspf``.

    The file path is not here -- that is ``WorkspaceConfig.dspf_out_pattern``,
    resolved per run.
    """

    subtype: str = "extended"
    netlist_coupling_values: str = "double"
    busbit_delimiter: str = "[]"
    hierarchy_delimiter: str = "/"
    #: ``input_db -hierarchy_delimiter``. DSPF only, like its output_db
    #: counterpart: ``ext.cmd`` has no delimiter setting anywhere, because an
    #: extracted view goes back into the DFII hierarchy and never flattens a
    #: name. Worth stating, or someone will "fix" the asymmetry.
    input_hierarchy_delimiter: str = "/"
    sub_node_char: str = "#"
    add_bulk_terminal: bool = False
    disable_instances: bool = False
    net_name_space: str = "SCHEMATIC"
    output_xy: list[str] = Field(
        default_factory=lambda: [
            "CANONICAL_RES",
            "PARASITIC_RES",
            "CANONICAL_CAP",
            "PARASITIC_CAP",
            "DIODE",
            "MOS",
            "BIPOLAR",
            "GENERIC",
        ]
    )


class OutputSettings(Base):
    """Which artifacts the extraction produces, and how they are shaped.

    Replaces the quantus slot of ``ProjectConfig.templates``. That slot is
    singular, so today a run structurally cannot produce both an extracted
    view and a DSPF -- choosing between them means pointing a template path at
    a different file. :attr:`emit` is a list; the quantus stage renders and
    runs once per entry.
    """

    emit: list[OutputKind] = Field(default_factory=lambda: [OutputKind.EXTRACTED_VIEW])
    common: CommonOutputSettings = Field(default_factory=CommonOutputSettings)
    extracted_view: ExtractedViewOutput = Field(default_factory=ExtractedViewOutput)
    dspf: DspfOutput = Field(default_factory=DspfOutput)

    @model_validator(mode="after")
    def _nonempty_unique(self) -> OutputSettings:
        if not self.emit:
            raise ValueError("output.emit must list at least one of extracted_view / dspf")
        if len(set(self.emit)) != len(self.emit):
            raise ValueError(f"output.emit has duplicates: {[k.value for k in self.emit]}")
        return self


class ReductionSettings(Base):
    """Jivaro. Absorbs ``JivaroConfig`` plus the literals in the reduction XML.

    ``JivaroOverride`` (the per-cell partial override) is deleted rather than
    migrated: a cell that needs different reduction settings needs a second
    Recipe. ``cpu`` moved to :class:`ResourceProfile`, and the four parasitic
    device models moved to ``PdkProfile.parasitics``.
    """

    enabled: bool = False
    #: The template carried ``| default(14)`` / ``| default(2)`` fallbacks;
    #: those go away and these defaults take the job.
    frequency_limit_ghz: AsWritten = 14.0
    error_max_pct: AsWritten = 2.0
    criterion: str = "standard"
    reduce_floating_nets: bool = False
    decoupling_auto_threshold: bool = False
    log_verbose_level: str = "trace"
    #: Which view Jivaro reduces. ``None`` -- the default -- means "the view
    #: Quantus just wrote", i.e. the cell's ``out_file``, which is also what
    #: ``inputView`` and Quantus ``-view_name`` already use.
    #:
    #: RESOLVED 2026-08-24. This was the catalog's most suspicious row: it
    #: held the separate literal ``"av_extracted"`` while both shipped task
    #: tables set ``out_file`` to ``"av_ext"``, so Jivaro was being pointed at
    #: a view that does not exist. In a run that has just extracted, the view
    #: to reduce is *necessarily* the extraction output, so the two cannot
    #: legitimately differ and the literal was never a setting.
    #:
    #: It stays overridable rather than being deleted: reducing a view some
    #: earlier run produced, without re-extracting, is the one case where an
    #: explicit name is right. Set it and it wins; leave it unset and it
    #: follows the DUT. :func:`auto_ext.core.render._recipe_tree` resolves it.
    views_to_reduce: str | None = None
    #: ``outputView`` = ``out_file`` + this. The reduced view is the one
    #: post-layout simulation actually runs on, so its name matters.
    output_view_suffix: str = "_red"

    @field_validator("views_to_reduce", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """``""`` means unset, not a view called the empty string.

        A GUI that clears a text box writes ``""``, and ``""`` stored here
        would defeat the DUT fallback in
        :func:`auto_ext.core.render._recipe_tree` and render an empty
        ``viewsToReduce``. Same rule ``CellEntry`` applies to ``out_file``.
        """

        if isinstance(value, str) and not value.strip():
            return None
        return value


class RunPolicy(Base):
    """How the run behaves when something goes wrong."""

    continue_on_lvs_fail: bool = False
    #: Today an unparsable LVS report raises and the run fails, with no way to
    #: say otherwise. Same behaviour by default, now stated.
    fail_on_unparsable_lvs_report: bool = True


class ResourceProfile(Base):
    """Machine- and site-local execution settings. NOT part of a Recipe.

    Everything here would have to be edited when the same recipe moves to a
    different machine, which is exactly why it is a separate object
    (DECISIONS.md #21, critique 2.6). It is stored per machine, not per
    project, and it never travels with a shared recipe.

    ``employee_id`` is site-local for the same reason but is NOT held here:
    per section 1.4 it lives in ``~/.auto_ext/site.yaml`` and is exposed to
    templates as ``site.employee_id``. Its catalog row carries the ``resources``
    owner only because the six-value owner enum has no ``site`` member.
    """

    schema_version: int = 1
    resource_id: Slug = "local"
    #: ``*cmnNumTurbo``.
    lvs_num_turbo: int = Field(default=2, ge=1)
    lvs_run_mt: bool = True
    lvs_run_hyper: bool = True
    #: ``*cmnLicenseWaitTime``. The unit is genuinely unknown; the draft
    #: asserted minutes without evidence.
    lvs_license_wait_time: int = Field(default=10, ge=0)
    #: Quantus has no multi_cpu section in either template, so extraction is
    #: single threaded and nothing is emitted for this until the office answer
    #: names the section.
    quantus_cpu_count: int = Field(default=1, ge=1)
    #: Jivaro ``<cpu>``.
    reduction_cpu: int = Field(default=1, ge=1)
    #: How many runs execute at once (CLI ``--jobs`` overrides per invocation).
    max_workers: int = Field(default=1, ge=1)


# ---- the recipe -------------------------------------------------------------


class Recipe(Base):
    """One recipe. File: ``<recipes_dir>/<recipe_id>.yaml``.

    Search path, in the order a ``recipe_id`` is resolved (later shadows
    earlier, and ``RecipeRef.source_path`` records which copy actually won):
    ``$AUTO_EXT_RECIPES`` -> ``~/.auto_ext/recipes`` ->
    ``<auto_ext_root>/recipes`` -> ``<config_dir>/recipes``.
    """

    schema_version: int = RECIPE_SCHEMA_VERSION
    recipe_id: Slug
    #: Display name. Chinese is fine here; code, comments and UI strings are
    #: English, but user data is the user's.
    name: str = Field(min_length=1)
    description: str | None = None
    #: Bumped by hand or by the GUI when the contents change. A RunRecord
    #: stores it so "which version of this recipe ran" has an answer.
    version: str = "1"
    tags: list[str] = Field(default_factory=list)
    #: Lineage for "save as". Replaces the preset concept.
    derived_from: Slug | None = None

    #: Which stages this recipe intends to run. Replaces CLI ``--stage`` as a
    #: persisted default; a CLI flag may still narrow it for one invocation,
    #: and the narrowed set is what ``RunRecord.requested_stages`` records.
    stages: list[Stage] = Field(default_factory=lambda: list(STAGE_ORDER))

    netlist: NetlistSettings = Field(default_factory=NetlistSettings)
    lvs: LvsSettings = Field(default_factory=LvsSettings)
    extraction: ExtractionSettings = Field(default_factory=ExtractionSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    reduction: ReductionSettings = Field(default_factory=ReductionSettings)
    policy: RunPolicy = Field(default_factory=RunPolicy)

    #: The escape hatch: manual edits to the generated files, stored as masked
    #: anchored hunks against the generated text (``docs/refactor/02-patch.md``).
    #: The GUI's "this recipe has N manual edits" is :attr:`manual_edit_count`.
    patches: list[TemplatePatch] = Field(default_factory=list)

    updated_at: datetime = Field(default_factory=lambda: utcnow())

    @model_validator(mode="after")
    def _check(self) -> Recipe:
        if not self.stages:
            raise ValueError("recipe.stages must not be empty")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError(f"recipe.stages has duplicates: {[s.value for s in self.stages]}")
        keys = [(p.stage, p.template_id) for p in self.patches]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "at most one TemplatePatch per (stage, template_id); merge the hunks instead"
            )
        return self

    # -- derived --------------------------------------------------------

    @property
    def manual_edit_count(self) -> int:
        """Enabled hunks across every patch. The GUI badge."""

        return sum(p.enabled_count for p in self.patches)

    def patch_for(self, stage: Stage | str, template_id: str) -> TemplatePatch | None:
        """The patch mounted on one generated file, if any."""

        want = Stage(stage).value
        for patch in self.patches:
            if patch.stage.value == want and patch.template_id == template_id:
                return patch
        return None

    def content_sha256(self) -> str:
        """Fingerprint of the settings, ignoring bookkeeping.

        Excludes ``updated_at`` and ``version`` so that saving a recipe twice
        without changing anything produces the same digest, which is what
        makes :class:`RecipeRef` able to answer "is this still the recipe that
        ran?".
        """

        payload = self.model_dump(mode="json", exclude={"updated_at", "version"})
        return hashlib.sha256(
            _canonical_json(payload).encode("utf-8"),
        ).hexdigest()

    def ref(self, *, source_path: Path | str | None = None) -> RecipeRef:
        """A reference plus fingerprint, for storing next to a run snapshot."""

        return RecipeRef(
            recipe_id=self.recipe_id,
            version=self.version,
            source_path=str(source_path) if source_path is not None else None,
            content_sha256=self.content_sha256(),
        )

    # -- forward compatibility -----------------------------------------

    def to_snapshot(
        self,
        *,
        templates: dict[str, str] | None = None,
        dspf_out_path: str | None = None,
        paths: dict[str, str] | None = None,
    ) -> RecipeSnapshot:
        """Convert to the S1 :class:`~auto_ext.model.run.RecipeSnapshot`.

        The runner still writes a ``RecipeSnapshot`` into ``run.json``. This
        lets it start building that snapshot from a real Recipe one call site
        at a time, instead of needing the whole runner rewritten in one go.

        ``knobs`` is filled with the seven legacy knob names under their old
        stage keys, so an existing ``run.json`` reader keeps working and a
        recipe-driven run stays comparable with a knob-driven one. ``templates``
        / ``dspf_out_path`` / ``paths`` are not recipe facts (they belong to
        the catalog, the workspace and the profile) and are passed in.
        """

        return RecipeSnapshot(
            recipe_id=self.recipe_id,
            name=self.name,
            version=self.version,
            templates=dict(templates or {}),
            knobs={
                "calibre": {
                    "lvs_variant": self.lvs.deck_variant,
                    "connect_by_name": self.lvs.connect_by_name,
                },
                "quantus": {
                    "exclude_floating_nets_limit": self.extraction.exclude_floating_nets_limit,
                    "coupling_cap_threshold_absolute": (
                        self.extraction.coupling_cap_threshold_absolute
                    ),
                    "coupling_cap_threshold_relative": (
                        self.extraction.coupling_cap_threshold_relative
                    ),
                    "min_res": self.extraction.min_res_ohm,
                    "temperature": self.extraction.temperature_c,
                },
            },
            jivaro=JivaroSnapshot(
                enabled=self.reduction.enabled,
                frequency_limit=self.reduction.frequency_limit_ghz,
                error_max=self.reduction.error_max_pct,
            ),
            dspf_out_path=dspf_out_path,
            paths=dict(paths or {}),
        )


class RecipeRef(Frozen):
    """A pointer to a Recipe plus a fingerprint of its contents.

    A RunRecord carries both this and the snapshot: the snapshot guarantees
    the run can be explained without any external state, the reference answers
    "is the recipe in the library still the one that ran?".
    """

    recipe_id: Slug
    version: str
    source_path: str | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


# ---- catalog -> recipe ------------------------------------------------------


def recipe_field_paths(model: type[Base] = Recipe, prefix: str = "") -> list[str]:
    """Every leaf field path of a model, e.g. ``extraction.min_res_ohm``.

    Nested ``Base`` submodels are walked; lists and scalars are leaves.
    """

    paths: list[str] = []
    for name, field in model.model_fields.items():
        annotation = field.annotation
        dotted = f"{prefix}{name}"
        if isinstance(annotation, type) and issubclass(annotation, Base):
            paths.extend(recipe_field_paths(annotation, prefix=f"{dotted}."))
        else:
            paths.append(dotted)
    return paths


def _assign(tree: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = tree
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def recipe_from_catalog(
    *,
    recipe_id: str = "catalog-default",
    name: str = "Catalog defaults",
    catalog: Catalog | None = None,
    **overrides: Any,
) -> Recipe:
    """Build a Recipe from the catalog's own defaults.

    Every recipe-owned catalog row is written into the field its
    ``context_path`` names, so the result reproduces exactly what the current
    templates emit -- including ``extraction.temperature_c = 55.0``, which the
    bare :class:`Recipe` default leaves as ``None`` so the profile's corner
    temperature can supply it.

    This doubles as the strongest available consistency check between the two:
    a catalog row naming a field that does not exist, or holding a value the
    field rejects, fails here rather than at render time. ``extra="forbid"``
    is what makes the first half of that true.
    """

    cat = catalog if catalog is not None else builtin_catalog()
    tree: dict[str, Any] = {"recipe_id": recipe_id, "name": name}
    for opt in cat.by_owner(Owner.RECIPE):
        path = opt.recipe_field_path
        if path is None:
            continue
        _assign(tree, path, opt.default)
    tree.update(overrides)
    try:
        return Recipe.model_validate(tree)
    except ValidationError as exc:
        raise ConfigError(
            f"catalog {cat.catalog_version} does not fit the Recipe model: {exc}"
        ) from exc


# ---- YAML round trip --------------------------------------------------------
# Same approach as core/config.py: ruamel in round-trip mode, so a recipe a
# user has commented stays commented after the GUI writes it back.


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.preserve_quotes = True
    return yaml


def _plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def load_recipe(path: Path) -> Recipe:
    """Load and validate one recipe YAML file."""

    recipe, _raw = load_recipe_with_raw(path)
    return recipe


def load_recipe_with_raw(path: Path) -> tuple[Recipe, Any]:
    """Load a recipe and also return ruamel's comment-carrying tree.

    Pass the second element back to :func:`dump_recipe_yaml` to write the file
    again with its comments and key order intact.
    """

    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"recipe file not found: {path}")
    yaml = _yaml()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.load(fh)
    except YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: expected a mapping at top level, got "
            f"{type(data).__name__ if data is not None else 'an empty file'}"
        )
    try:
        recipe = Recipe.model_validate(_plain(data))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return recipe, data


def _merge_into(target: Any, source: Any) -> Any:
    """Write ``source`` into ruamel's ``target`` in place, keeping comments.

    Existing keys are updated where the value changed (so ruamel keeps the
    comment attached to that key), new keys are appended, and keys the model
    no longer has are removed.
    """

    if not isinstance(target, dict) or not isinstance(source, dict):
        return source
    for key in [k for k in target if k not in source]:
        del target[key]
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _merge_into(target[key], value)
        elif key not in target or target[key] != value:
            target[key] = value
    return target


def dump_recipe_yaml(recipe: Recipe, *, raw: Any = None) -> str:
    """Serialize a recipe to YAML text.

    With ``raw`` (from :func:`load_recipe_with_raw`) the original comments and
    key order survive; without it a plain document is emitted.
    """

    payload = recipe.model_dump(mode="json")
    tree = _merge_into(raw, payload) if raw is not None else payload
    buf = StringIO()
    _yaml().dump(tree, buf)
    return buf.getvalue()


def save_recipe(recipe: Recipe, path: Path, *, raw: Any = None) -> None:
    """Write a recipe to ``path``, creating the directory if needed."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_recipe_yaml(recipe, raw=raw), encoding="utf-8")


def recipe_filename(recipe: Recipe) -> str:
    """``<recipe_id>.yaml``, with the id slugified defensively.

    ``recipe_id`` is already constrained to a slug pattern by the model; this
    is the belt-and-braces step for an id that arrived from somewhere the
    model did not validate.
    """

    return f"{slugify(recipe.recipe_id, max_len=64)}.yaml"


def _canonical_json(payload: Any) -> str:
    """Stable JSON for hashing: sorted keys, no incidental whitespace."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
