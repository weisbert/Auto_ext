"""Builders for the v2 object model: PdkProfile / Recipe / CellBook / Workspace.

One home for the scaffolding that ``docs/refactor/04-tests-disposition.md``
section 4.5 says every file was writing for itself. A test that needs a
complete, valid object of the new model calls a builder here (or the matching
fixture in ``tests/conftest.py``) instead of hand-writing another shape that
drifts from the others.

Three rules these builders keep
-------------------------------
**Nothing points outside the sandbox.** Every path-producing builder takes a
``tmp_path`` or writes under one. :data:`ENV` is the one exception and is
deliberately fictional (``/w/...``): it is a *string* environment fed to the
env resolver, never a location anything opens.

**Two profiles, not one.** :func:`make_profile` and :func:`make_other_profile`
describe two different technologies that name the same semantic corner and the
same LVS deck variant. That pair is what makes "one Recipe, two PDKs" testable
at all -- with a single profile, a recipe that had silently frozen a PDK
literal would still pass.

**Overrides are keyword pass-through.** Every builder ends with
``fields.update(overrides)``, so a test states only the field it is about.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_ext.core import render
from auto_ext.model.cells import CellBook, CellEntry
from auto_ext.model.pdk import (
    CornerSpec,
    LvsDeckSet,
    LvsDeckVariant,
    PdkProfile,
    QrcDeck,
)
from auto_ext.model.recipe import Recipe
from auto_ext.model.run import DutSnapshot
from auto_ext.model.workspace import WorkspaceConfig

#: Root of the fictional shared filesystem the env values describe.
WORK = "/w"

#: A resolved environment that satisfies every path expression the profiles
#: below declare. Values are strings, not real directories: the render path
#: substitutes them into tool input files and never stats them.
ENV: dict[str, str] = {
    "WORK_ROOT": WORK,
    "WORK_ROOT2": WORK,
    "VERIFY_ROOT": f"{WORK}/fake/verify",
    "SETUP_ROOT": f"{WORK}/fake/setup",
    "PDK_LAYER_MAP_FILE": f"{WORK}/fake/layers.map",
    "calibre_source_added_place": (
        f"{WORK}/fake/runset/Calibre_QRC/LVS/Ver_LVS_A/CFXXX/empty.cdl"
    ),
}

#: Env vars a second technology needs on top of :data:`ENV`.
OTHER_ENV: dict[str, str] = {
    "PDK2_ROOT": f"{WORK}/fake/pdk2",
}


def make_profile(**overrides: Any) -> PdkProfile:
    """A profile shaped like the one office scanning produces for HN001."""

    fields: dict[str, Any] = {
        "profile_id": "hn001",
        "display_name": "HN001 22nm",
        "tech_name": "HN001",
        "lvs_decks": LvsDeckSet(
            dir_expr="$calibre_source_added_place|parent",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="wodio",
            runset_version="Ver_LVS_A",
        ),
        "qrc": QrcDeck(
            dir_expr="$VERIFY_ROOT/runset/Calibre_QRC/QRC/Ver_QRC_B/CFXXX/QCI_deck",
            runset_version="Ver_QRC_B",
        ),
        "corners": [
            CornerSpec(
                name="typical",
                technology_corner="TYPICAL",
                default_temperature_c=55.0,
                aliases=["nominal"],
            )
        ],
        "default_corner": "typical",
    }
    fields.update(overrides)
    return PdkProfile(**fields)


def make_other_profile(**overrides: Any) -> PdkProfile:
    """A second technology that answers the same semantic questions differently.

    Same corner *name* (``typical``) and same deck variant *name* (``wodio``),
    different literals behind both, plus a different layer map and deck
    directory. A Recipe that renders identically against
    :func:`make_profile` and this one everywhere except the PDK-bound lines is
    the definition of portable.
    """

    fields: dict[str, Any] = {
        "profile_id": "cf028",
        "display_name": "CF028 28nm",
        "tech_name": "CF028",
        "layer_map": f"{WORK}/fake/pdk2/layers28.map",
        "lvs_decks": LvsDeckSet(
            dir_expr="$PDK2_ROOT/lvs/CF028",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wo_dio")],
            default_variant="wodio",
            runset_version="Ver_2.3",
        ),
        "qrc": QrcDeck(
            dir_expr="$PDK2_ROOT/qrc/CF028/QCI_deck",
            runset_version="Ver_2.3",
        ),
        "corners": [
            CornerSpec(
                name="typical",
                technology_corner="NOM_28",
                default_temperature_c=55.0,
            )
        ],
        "default_corner": "typical",
    }
    fields.update(overrides)
    return PdkProfile(**fields)


def make_recipe(**overrides: Any) -> Recipe:
    """A minimal valid Recipe; every unset field takes its schema default."""

    fields: dict[str, Any] = {
        "recipe_id": "rc-coupled-typical",
        "name": "RC coupled, typical",
    }
    fields.update(overrides)
    return Recipe(**fields)


def make_dut(**overrides: Any) -> DutSnapshot:
    """The DUT half of a render context."""

    fields: dict[str, Any] = {
        "library": "EXAMPLE_LIB",
        "cell": "inv",
        "layout_view": "layout",
        "source_view": "schematic",
        "ground_net": "vss",
        "out_file": "av_ext",
    }
    fields.update(overrides)
    return DutSnapshot(**fields)


def make_cell(**overrides: Any) -> CellEntry:
    """One row of the Cells table, matching :func:`make_dut`'s DUT."""

    fields: dict[str, Any] = {
        "library": "EXAMPLE_LIB",
        "cell": "inv",
        "layout_view": "layout",
        "source_view": "schematic",
        "ground_net": "vss",
        "out_file": "av_ext",
    }
    fields.update(overrides)
    return CellEntry(**fields)


def make_cell_book(*entries: CellEntry, **overrides: Any) -> CellBook:
    """A Cells table; with no arguments, one row equal to :func:`make_cell`."""

    fields: dict[str, Any] = {"cells": list(entries) or [make_cell()]}
    fields.update(overrides)
    return CellBook(**fields)


def make_workspace(**overrides: Any) -> WorkspaceConfig:
    """A workspace config bound to :func:`make_profile`."""

    fields: dict[str, Any] = {"pdk_profile": "hn001"}
    fields.update(overrides)
    return WorkspaceConfig(**fields)


def make_pdk_tree(root: Path) -> dict[str, Path]:
    """Materialise a minimal but *real* PDK directory tree under ``root``.

    The health checks stat files, so a profile whose every check comes back
    green needs directories that exist. This is the "build a fake process
    directory" half of section 4.1 of the tests disposition -- the alternative
    (a back door that constructs a profile without scanning) would leave the
    scan itself untested.

    ``tests/core/test_health.py`` and ``tests/core/test_profile_discover.py``
    keep their own, differently-shaped trees on purpose: those two files are
    *about* what a scan finds and what a check stats, so each pins a layout
    that this one is free to change. Every other consumer wants "a PDK that
    works" and should come here.

    Returns the four anchors a caller needs to write assertions against.
    """

    lvs = root / "verify" / "LVS" / "Ver_1" / "CFXXX"
    qrc = root / "verify" / "QRC" / "Ver_2" / "CFXXX" / "QCI_deck"
    setup = root / "setup"
    for directory in (lvs, qrc, setup):
        directory.mkdir(parents=True, exist_ok=True)
    (lvs / "CFXXX.wodio.qcilvs").write_text("; deck\n", encoding="utf-8")
    (lvs / "empty.cdl").write_text("", encoding="utf-8")
    (qrc / "query_cmd").write_text("# query\n", encoding="utf-8")
    (qrc / "preserveCellList.txt").write_text("", encoding="utf-8")
    (setup / "assura_tech.lib").write_text('techCorner( "TYPICAL" )\n', encoding="utf-8")
    (setup / "layers.map").write_text("; layers\n", encoding="utf-8")
    return {"root": root, "lvs": lvs, "qrc": qrc, "setup": setup}


def make_healthy_profile(tree: dict[str, Path], **overrides: Any) -> PdkProfile:
    """A profile over :func:`make_pdk_tree` that is green *and* renderable.

    Two constraints pull in opposite directions and both have to hold:

    * **health** stats real files, so every path has to resolve to something
      inside ``tree``;
    * **render** refuses any profile value the shipped template still
      hardcodes -- silently ignoring it is the failure mode the catalog exists
      to remove -- so those fields have to keep their catalog defaults.

    The way to satisfy both is to leave ``layer_map`` / ``tech_library_file``
    / ``cdl_include_files`` / the supply-name tables at their defaults, which
    are env *expressions*, and bind the variables underneath them in
    ``env_overrides``. Only the two deck directories, which have no default at
    all, are given literal paths.
    """

    setup = tree["setup"]
    root = tree["root"].as_posix()
    fields: dict[str, Any] = {
        "profile_id": "hn001",
        "display_name": "HN001",
        "tech_name": "HN001",
        "required_env": [],
        "lvs_decks": LvsDeckSet(
            dir_expr=tree["lvs"].as_posix(),
            basename="CFXXX",
            variants=[LvsDeckVariant(name="wodio", rules_suffix="wodio")],
            default_variant="wodio",
            runset_version="Ver_1",
        ),
        "qrc": QrcDeck(dir_expr=tree["qrc"].as_posix(), runset_version="Ver_2"),
        "corners": [
            CornerSpec(
                name="typical", technology_corner="TYPICAL", default_temperature_c=55.0
            )
        ],
        "default_corner": "typical",
        # Every default above is an env expression; these are the bindings that
        # make them land inside ``tree`` -- and that point every output the run
        # produces at a directory under ``tmp_path``.
        "env_overrides": {
            "WORK_ROOT": root,
            "WORK_ROOT2": root,
            "VERIFY_ROOT": f"{root}/verify",
            "SETUP_ROOT": setup.as_posix(),
            "PDK_LAYER_MAP_FILE": (setup / "layers.map").as_posix(),
            "calibre_source_added_place": (tree["lvs"] / "empty.cdl").as_posix(),
        },
    }
    fields.update(overrides)
    return PdkProfile(**fields)


def make_run(tmp_path: Path, **overrides: Any) -> render.RunFacts:
    """Run-only facts, with every directory under ``tmp_path``."""

    fields: dict[str, Any] = {
        "run_id": "20260821T143205Z_inv-rc",
        "run_slug": "inv-rc",
        "run_dir": tmp_path / "runs" / "20260821T143205Z_inv-rc",
        "workarea": tmp_path / "workarea",
        "output_dir": f"{WORK}/cds/verify/QCI_PATH_inv",
        "intermediate_dir": WORK,
        "dspf_out_path": f"{WORK}/inv.dspf",
        "started_at": datetime(2026, 8, 21, 14, 32, 5, tzinfo=timezone.utc),
        "stages": ("si", "calibre", "quantus"),
    }
    fields.update(overrides)
    return render.RunFacts(**fields)
