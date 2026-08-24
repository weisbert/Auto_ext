"""What a user may edit on a project, and with which kind of control.

A *project* is a config directory (``docs/refactor/PROJECTS_AND_SETUP.md`` §1),
and the two objects a user owns inside it that had no UI at all until now are
:class:`~auto_ext.model.workspace.WorkspaceConfig` -- where this project's work
lands -- and :class:`~auto_ext.model.pdk.PdkProfile` -- the site and process
facts every path in the flow hangs off.

Why a field list here and NOT in the screen
-------------------------------------------
:mod:`auto_ext.ui.screens.recipes_screen` deliberately has no field list: its
form is generated from ``auto_ext/catalog/options.yaml``, because a Recipe
field IS a catalog row landing in a template. Neither of the two objects here
is like that. A profile's ``lvs_decks.dir_expr`` lands in no template -- it is
what the *renderer* resolves a template's landing site against -- so it has no
``OptionSpec``, no ``template_var`` and no ``lands_in``. Synthesising one would
put a lie in the vocabulary the catalog's own self-checks read.

So these objects get their own, smaller vocabulary, and it lives in a module
with no Qt import so the audit can read it without a display.

Every leaf path of both models is either in a ``FIELDS`` tuple or in an
``UNREACHABLE`` map with a reason, and ``tests/ui/test_reachability.py``
fails when that stops being true. The reason is the load-bearing part: an
exemption is a claim that a human decided, and the eight defects the first
real office session found were all "nobody ever asked whether you could reach
this".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "CHECK_FIELDS",
    "PROFILE_FIELDS",
    "PROFILE_GROUP_ORDER",
    "PROFILE_UNREACHABLE",
    "WORKSPACE_FIELDS",
    "WORKSPACE_GROUP_ORDER",
    "WORKSPACE_UNREACHABLE",
    "FieldKind",
    "ProjectField",
    "bound_paths",
    "field_for_check",
    "get_path",
    "set_path",
]


class FieldKind(StrEnum):
    """Which control a field gets.

    Deliberately coarser than :class:`~auto_ext.ui.widgets.option_editor.
    EditorKind`: there is no catalog row behind these fields, so there is no
    range, no unit and no advisory band to render.
    """

    #: One line of free text.
    TEXT = "text"
    #: A whole number.
    INT = "int"
    #: A list of strings, one per line.
    LIST = "list"
    #: ``name -> value`` pairs, one per row.
    MAPPING = "mapping"
    #: One of a closed set computed from elsewhere in the object.
    CHOICE = "choice"
    #: A list of submodels, edited as a table.
    TABLE = "table"


@dataclass(frozen=True)
class ProjectField:
    """One editable field of a project object."""

    #: Dotted leaf path, the same vocabulary
    #: :func:`~auto_ext.model.recipe.recipe_field_paths` produces.
    path: str
    label: str
    group: str
    kind: FieldKind
    #: One sentence under the control. Says what the field DOES, not what it
    #: is called -- the label already says that.
    help: str
    placeholder: str = ""
    #: For :attr:`FieldKind.CHOICE`: the name of the choice source the screen
    #: resolves (``corners``, ``deck_variants``, ``profile_ids``). Not a
    #: literal list, because every one of them depends on the object being
    #: edited or on the config directory around it.
    choices_from: str | None = None
    #: For :attr:`FieldKind.CHOICE`: whether "unset" is a legal answer, and
    #: what it then means. Empty when the field is required.
    unset_means: str = ""
    #: For :attr:`FieldKind.TABLE`: the submodel fields shown as columns.
    columns: tuple[str, ...] = ()
    #: For :attr:`FieldKind.MAPPING`: the header over the two columns.
    key_label: str = "name"
    value_label: str = "value"


# ---------------------------------------------------------------------------
# WorkspaceConfig
# ---------------------------------------------------------------------------

WORKSPACE_GROUP_ORDER: tuple[str, ...] = ("Where work lands", "History")

WORKSPACE_FIELDS: tuple[ProjectField, ...] = (
    ProjectField(
        path="pdk_profile",
        label="PDK profile",
        group="Where work lands",
        kind=FieldKind.CHOICE,
        choices_from="profile_ids",
        help=(
            "Which profile in config/profiles/ this project runs against. "
            "Switching it switches every deck path, corner and env value at once."
        ),
    ),
    ProjectField(
        path="output_dir_pattern",
        label="Output directory",
        group="Where work lands",
        kind=FieldKind.TEXT,
        placeholder="${WORK_ROOT}/{library}/{cell}",
        help=(
            "Where the extracted result for one DUT goes. Accepts $VAR / ${VAR} / "
            "$env(VAR) and the format keys {cell} {library} {layout_view} "
            "{source_view} {recipe} {run_id} {run_slug}."
        ),
    ),
    ProjectField(
        path="intermediate_dir",
        label="Cadence workspace",
        group="Where work lands",
        kind=FieldKind.TEXT,
        placeholder="${WORK_ROOT2}/{cell}",
        help=(
            "The directory the EDA tools work in. Use {run_slug} for one workspace "
            "per run, {cell} to keep reusing one per cell."
        ),
    ),
    ProjectField(
        path="dspf_out_pattern",
        label="DSPF output",
        group="Where work lands",
        kind=FieldKind.TEXT,
        placeholder="${WORK_ROOT}/{cell}.dspf",
        help="Where a DSPF netlist is written, for the recipes that emit one.",
    ),
    ProjectField(
        path="keep_runs",
        label="Runs to keep",
        group="History",
        kind=FieldKind.INT,
        help=(
            "How many run directories `auto-ext runs prune` keeps; 0 keeps every "
            "one. Runs are never overwritten, so pruning is the only thing that "
            "ever deletes one."
        ),
    ),
)

#: WorkspaceConfig leaf paths with no control, and why.
WORKSPACE_UNREACHABLE: dict[str, str] = {
    "schema_version": (
        "format version of the file; changing it is a migration this build "
        "would have to perform, not a value a user picks"
    ),
}


# ---------------------------------------------------------------------------
# PdkProfile
# ---------------------------------------------------------------------------

PROFILE_GROUP_ORDER: tuple[str, ...] = (
    "Identity",
    "Process",
    "Environment",
    "LVS decks",
    "QRC decks",
    "Corners",
    "Supply nets",
    "Parasitic devices",
)

PROFILE_FIELDS: tuple[ProjectField, ...] = (
    # ---- Identity ----
    ProjectField(
        path="display_name",
        label="Name",
        group="Identity",
        kind=FieldKind.TEXT,
        help="What this profile is called in the profile picker. Free text.",
    ),
    ProjectField(
        path="description",
        label="Description",
        group="Identity",
        kind=FieldKind.TEXT,
        help="One line for whoever opens this profile next. Nothing reads it.",
    ),
    # ---- Process ----
    ProjectField(
        path="tech_name",
        label="Technology name",
        group="Process",
        kind=FieldKind.TEXT,
        help=(
            "The PDK's own name, as the tools spell it. Left empty, it is derived "
            "from the first environment variable below that is set."
        ),
    ),
    ProjectField(
        path="tech_name_env_vars",
        label="Derive name from",
        group="Process",
        kind=FieldKind.LIST,
        help=(
            "Environment variables searched, in order, when the technology name is "
            "empty. One per line."
        ),
    ),
    ProjectField(
        path="tech_library_file",
        label="Technology library",
        group="Process",
        kind=FieldKind.TEXT,
        placeholder="$env(SETUP_ROOT)/assura_tech.lib",
        help="The technology library Quantus is pointed at. A path expression.",
    ),
    ProjectField(
        path="layer_map",
        label="Layer map",
        group="Process",
        kind=FieldKind.TEXT,
        placeholder="$PDK_LAYER_MAP_FILE",
        help="The layer map strmout uses when it writes GDS. A path expression.",
    ),
    ProjectField(
        path="cdl_include_files",
        label="CDL preludes",
        group="Process",
        kind=FieldKind.LIST,
        help=(
            "Files included ahead of the source netlist in LVS. One per line, in "
            "the order they are included."
        ),
    ),
    # ---- Environment ----
    ProjectField(
        path="env_overrides",
        label="Pinned values",
        group="Environment",
        kind=FieldKind.MAPPING,
        key_label="variable",
        value_label="value",
        help=(
            "Values used INSTEAD of what the shell exports. Setup marks a check "
            "that resolved this way with the exchange glyph rather than a tick: "
            "you deliberately deviated, and the badge should keep saying so."
        ),
    ),
    ProjectField(
        path="required_env",
        label="Must be set",
        group="Environment",
        kind=FieldKind.LIST,
        help=(
            "Variables Setup treats as blocking when unset and unpinned. Derived "
            "from this profile's own path expressions; edit to add site rules."
        ),
    ),
    # ---- LVS decks ----
    ProjectField(
        path="lvs_decks.dir_expr",
        label="Deck directory",
        group="LVS decks",
        kind=FieldKind.TEXT,
        placeholder="$calibre_source_added_place|parent",
        help=(
            "The directory holding the Calibre rules files. A path expression; "
            "|parent takes the containing directory of a file-valued variable."
        ),
    ),
    ProjectField(
        path="lvs_decks.basename",
        label="Rules basename",
        group="LVS decks",
        kind=FieldKind.TEXT,
        help=(
            "The stem of the rules file. Left empty it is the deck directory's own "
            "last segment, which is what every observed deck layout does."
        ),
    ),
    ProjectField(
        path="lvs_decks.filename_pattern",
        label="Rules filename",
        group="LVS decks",
        kind=FieldKind.TEXT,
        placeholder="{basename}.{suffix}.qcilvs",
        help="How basename and the variant's suffix become a file name.",
    ),
    ProjectField(
        path="lvs_decks.variants",
        label="Deck variants",
        group="LVS decks",
        kind=FieldKind.TABLE,
        columns=("name", "rules_suffix", "description"),
        help=(
            "The deck flavours this PDK ships. A recipe names one by its handle, "
            "so a recipe is portable only across variants this table lists."
        ),
    ),
    ProjectField(
        path="lvs_decks.default_variant",
        label="Default variant",
        group="LVS decks",
        kind=FieldKind.CHOICE,
        choices_from="deck_variants",
        unset_means="the recipe must name a variant itself",
        help="Used by a recipe that does not name a variant of its own.",
    ),
    ProjectField(
        path="lvs_decks.runset_version",
        label="Runset version",
        group="LVS decks",
        kind=FieldKind.TEXT,
        help="The deck release this site is on, when the path expression needs it.",
    ),
    # ---- QRC decks ----
    ProjectField(
        path="qrc.dir_expr",
        label="Deck directory",
        group="QRC decks",
        kind=FieldKind.TEXT,
        placeholder="$VERIFY_ROOT/qrc/QCI_deck",
        help="The directory holding the QRC extraction decks. A path expression.",
    ),
    ProjectField(
        path="qrc.query_cmd_name",
        label="Query command file",
        group="QRC decks",
        kind=FieldKind.TEXT,
        placeholder="query_cmd",
        help="The name of the query command file inside the deck directory.",
    ),
    ProjectField(
        path="qrc.preserve_cell_list_name",
        label="Preserve-cell list",
        group="QRC decks",
        kind=FieldKind.TEXT,
        placeholder="preserveCellList.txt",
        help="The name of the preserve-cell list inside the deck directory.",
    ),
    ProjectField(
        path="qrc.runset_version",
        label="Runset version",
        group="QRC decks",
        kind=FieldKind.TEXT,
        help="The deck release this site is on, when the path expression needs it.",
    ),
    # ---- Corners ----
    ProjectField(
        path="corners",
        label="Corner table",
        group="Corners",
        kind=FieldKind.TABLE,
        columns=("name", "technology_corner", "default_temperature_c", "description"),
        help=(
            "The handle a recipe refers to, and the literal Quantus is given. A "
            "recipe is portable only across corners this table names."
        ),
    ),
    ProjectField(
        path="default_corner",
        label="Default corner",
        group="Corners",
        kind=FieldKind.CHOICE,
        choices_from="corners",
        unset_means="the recipe must name a corner itself",
        help="Used by a recipe that leaves its corner unset.",
    ),
    # ---- Supply nets ----
    ProjectField(
        path="power_names",
        label="Power nets",
        group="Supply nets",
        kind=FieldKind.LIST,
        help="Nets LVS treats as power. One per line.",
    ),
    ProjectField(
        path="ground_names",
        label="Ground nets",
        group="Supply nets",
        kind=FieldKind.LIST,
        help="Nets LVS treats as ground. One per line.",
    ),
    # ---- Parasitic devices ----
    ProjectField(
        path="parasitics.res_component",
        label="Resistor component",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        help="The component name extraction gives a parasitic resistor.",
    ),
    ProjectField(
        path="parasitics.cap_component",
        label="Capacitor component",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        help="The component name extraction gives a parasitic capacitor.",
    ),
    ProjectField(
        path="parasitics.ind_component",
        label="Inductor component",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        help="Only read by an extraction that includes inductance.",
    ),
    ProjectField(
        path="parasitics.mutual_component",
        label="Mutual component",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        help="Only read by an extraction that includes mutual inductance.",
    ),
    ProjectField(
        path="parasitics.res_model",
        label="Resistor model",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        placeholder="analogLib/presistor/symbol",
        help="The library/cell/view the resistor component resolves to.",
    ),
    ProjectField(
        path="parasitics.cap_model",
        label="Capacitor model",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        placeholder="analogLib/pcapacitor/symbol",
        help="The library/cell/view the capacitor component resolves to.",
    ),
    ProjectField(
        path="parasitics.ind_model",
        label="Inductor model",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        placeholder="analogLib/pinductor/symbol",
        help="The library/cell/view the inductor component resolves to.",
    ),
    ProjectField(
        path="parasitics.mutual_model",
        label="Mutual model",
        group="Parasitic devices",
        kind=FieldKind.TEXT,
        placeholder="analogLib/pmind/symbol",
        help="The library/cell/view the mutual component resolves to.",
    ),
    # ---- Paths that belong to no group above ----
    ProjectField(
        path="extra_paths",
        label="Other paths",
        group="Process",
        kind=FieldKind.MAPPING,
        key_label="name",
        value_label="path expression",
        help=(
            "Path expressions this site needs that the fields above do not name. "
            "They reach a template as project.paths.<name>."
        ),
    ),
)

#: PdkProfile leaf paths with no control, and why.
PROFILE_UNREACHABLE: dict[str, str] = {
    "schema_version": (
        "format version of the file; changing it is a migration this build "
        "would have to perform, not a value a user picks"
    ),
    "profile_id": (
        "names the file AND is what workspace.pdk_profile points at. Renaming "
        "it is a file move plus a rebind, not a field edit -- the display name "
        "is what the Identity group edits"
    ),
    "checks": (
        "an override for the WHOLE check list. Empty -- which every shipped "
        "profile is -- means health.default_checks derives the list from the "
        "profile, so a field here is derived from every other field on this "
        "screen. Writing one by hand is a YAML-level extension"
    ),
    "discovered_from": (
        "provenance: the paths a scan or a migration read this profile out of. "
        "Written by those two, meaningless to edit"
    ),
    "scanned_at": "a timestamp the discovery and save paths stamp",
    "hand_edited": (
        "set by the save path to record that a human touched a discovered "
        "profile; editing the flag would be editing the record of the edit"
    ),
}


# ---------------------------------------------------------------------------
# Which field a failing Setup check is about
# ---------------------------------------------------------------------------

#: ``check_id`` -> the profile field its fix hint tells the user to edit.
#:
#: Every hint that :func:`auto_ext.core.health.default_checks` writes ends in
#: "...in ``config/profiles/<id>.yaml``", naming a field. Until this screen
#: existed that sentence was the whole answer, because there was nowhere to go.
#: Now the drawer can offer to open the field, and this is the map -- kept here
#: rather than in the drawer because it is a claim about the field inventory,
#: and ``tests/ui/test_project_screen.py`` checks every value is a bound path.
#:
#: ``PdkCheck.target`` is deliberately NOT used as the path. It holds a field
#: name only for ``FIELD_SET`` checks; for the others it is a resolved path
#: expression or an executable name, and reading it as a field path would
#: produce a plausible-looking miss rather than an obvious one.
#:
#: ``env.*`` checks are absent on purpose: a missing environment variable has
#: two different fixes ("source the setup script" and "pin it here"), the
#: drawer already offers the second as its own control, and pointing at
#: ``env_overrides`` would quietly recommend deviating from the shell.
#: ``tool.*`` is absent because no field on this screen can fix PATH.
CHECK_FIELDS: dict[str, str] = {
    "pdk.tech_name": "tech_name",
    "pdk.layer_map": "layer_map",
    "pdk.tech_library_file": "tech_library_file",
    "pdk.cdl_include": "cdl_include_files",
    "pdk.corners": "corners",
    "pdk.power_names": "power_names",
    "pdk.ground_names": "ground_names",
    "lvs.deck_dir": "lvs_decks.dir_expr",
    "lvs.rules_files": "lvs_decks.filename_pattern",
    "lvs.variants": "lvs_decks.variants",
    "qrc.deck_dir": "qrc.dir_expr",
    "qrc.query_cmd": "qrc.query_cmd_name",
    "qrc.preserve_cell_list": "qrc.preserve_cell_list_name",
}


def field_for_check(check_id: str) -> str | None:
    """The field path a Setup check is about, or ``None``.

    ``pdk.cdl_include.2`` and friends are numbered per entry of a list field,
    so a suffix after the mapped id still resolves to the list itself.
    """

    check_id = str(check_id)
    if check_id in CHECK_FIELDS:
        return CHECK_FIELDS[check_id]
    head, _, _tail = check_id.rpartition(".")
    return CHECK_FIELDS.get(head)


# ---------------------------------------------------------------------------
# Path access
# ---------------------------------------------------------------------------


def bound_paths(fields: Sequence[ProjectField]) -> set[str]:
    """The leaf paths ``fields`` puts a control in front of."""

    return {spec.path for spec in fields}


def get_path(model: Any, path: str) -> Any:
    """Read a dotted leaf path off a model."""

    node = model
    for part in path.split("."):
        node = getattr(node, part)
    return node


def set_path(model: Any, path: str, value: Any) -> None:
    """Assign a dotted leaf path on a model, in place.

    In place rather than ``model_copy(update=...)`` because the models use
    ``validate_assignment=True``: assigning through the attribute is what runs
    the field validators, and a value that does not validate must raise here
    -- while the user is looking at the control they just typed into -- rather
    than at save time, three screens later.
    """

    parts = path.split(".")
    node = model
    for part in parts[:-1]:
        node = getattr(node, part)
    setattr(node, parts[-1], value)
