"""Tests for :mod:`auto_ext.migrate` -- the one-shot v1 -> v2 conversion.

The two configs that ship with the repository are the real end-to-end cases:
``config/`` (what the user actually runs) and ``examples/demo/``. For both, the
suite asserts *semantic equivalence*: the same cell set, and the same effective
value for every parameter that reaches a tool. Everything else here is either a
property the migration promises (idempotence, never touching the source files,
no field disappearing) or a unit test of the template read-back that makes the
value-preservation claim true.

``elsewhere`` is applied to every test that deploys files into ``tmp_path``:
template resolution must not be able to fall through to the repository's own
``templates/`` and pass for the wrong reason.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from auto_ext.catalog import Owner, builtin_catalog
from auto_ext.core.profile_discover import builtin_profile
from auto_ext.legacy_v1 import (
    load_manifest_v1,
    load_project_v1,
    load_tasks_v1_with_raw,
    resolve_knob_values_v1,
)
from auto_ext.migrate import (
    MigrationDecision,
    MigrationError,
    MigrationReport,
    _parse_calibre,
    _parse_quantus,
    _parse_skill,
    _parse_xml,
    _resolve_template_file,
    format_report,
    migrate_v1_to_v2,
    read_back_from_templates,
)
from auto_ext.model.cells import load_cells
from auto_ext.model.common import RenderTarget
from auto_ext.model.recipe import OutputKind, load_recipe
from auto_ext.model.workspace import WorkspaceConfig, load_workspace

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The shipped catalog templates. Only the path-resolution tests read these,
#: and only for their paths: their *contents* are being parameterised round by
#: round, so nothing here asserts what literal a shipped template carries.
TEMPLATES = REPO_ROOT / "templates"

#: The archived v1 tree -- ``.j2`` bodies *and* their ``*.j2.manifest.yaml``
#: knob sidecars, which the shipped tree no longer carries. This is the
#: migration's input, so it is deliberately frozen: a v1 tree on a user's disk
#: does not change when Auto_ext ships a new template.
V1_TEMPLATES = REPO_ROOT / "examples" / "legacy" / "templates"

REAL_CONFIG = REPO_ROOT / "examples" / "legacy" / "config"
DEMO_CONFIG = REPO_ROOT / "examples" / "legacy" / "demo" / "config"

EXPECTED_FILES = {
    "config/workspace.yaml",
    "config/cells.yaml",
    "config/resources.yaml",
}


@pytest.fixture
def elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand outside the repository while the test runs.

    ``resolve_template_path``-style lookups start cwd-relative, so a test whose
    cwd is still the repository can hit the repository's own templates instead
    of the ones it deployed. ``migrate`` avoids that by construction; this
    fixture is what proves it.
    """

    monkeypatch.chdir(tempfile.gettempdir())


def _relative_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }


def _get(model: Any, path: str) -> Any:
    node = model
    for part in path.split("."):
        node = getattr(node, part)
    return node


def _stage_templates(task: Any) -> dict[str, Path]:
    slots = {
        "si": task.templates.si,
        "calibre": task.templates.calibre,
        "quantus": task.templates.quantus,
        "jivaro": task.templates.jivaro,
    }
    out: dict[str, Path] = {}
    for stage, raw in slots.items():
        resolved = _resolve_template_file(
            raw, template_root=V1_TEMPLATES, repo_root=V1_TEMPLATES.parent
        )
        if resolved is not None:
            out[stage] = resolved
    return out


def assert_semantics_preserved(config_dir: Path, report: MigrationReport) -> None:
    """The migrated objects describe the same runs the old config described."""

    project = load_project_v1(config_dir / "project.yaml")
    tasks, _raw = load_tasks_v1_with_raw(config_dir / "tasks.yaml", project)
    catalog = builtin_catalog()

    # ---- same cell set, same per-DUT settings
    assert report.cells.keys == [task.task_id for task in tasks]
    for task in tasks:
        entry = report.cells.entry(task.task_id)
        assert entry.library == task.library
        assert entry.cell == task.cell
        assert entry.layout_view == task.lvs_layout_view
        assert entry.source_view == task.lvs_source_view
        assert entry.ground_net == task.ground_net
        assert entry.out_file == task.out_file
        assert entry.display_name == (task.label or None)
        assert entry.enabled is True

    # ---- every cell is bound to exactly one recipe
    owner: dict[str, list[str]] = {}
    for recipe_id, keys in report.bindings.items():
        for key in keys:
            owner.setdefault(key, []).append(recipe_id)
    assert sorted(owner) == sorted(report.cells.keys)
    assert all(len(ids) == 1 for ids in owner.values())

    by_key = {
        key: recipe
        for recipe in report.recipes
        for key in report.bindings[recipe.recipe_id]
    }

    # ---- same effective value for every knob, per task
    for task in tasks:
        recipe = by_key[task.task_id]
        for stage, template in _stage_templates(task).items():
            effective = resolve_knob_values_v1(
                load_manifest_v1(template),
                dict(project.knobs.get(stage, {})),
                dict(task.knobs.get(stage, {})),
            )
            for name, value in effective.items():
                option = catalog.by_template_var(name)
                assert option is not None, name
                assert option.recipe_field_path is not None, name
                assert _get(recipe, option.recipe_field_path) == value, name

        assert recipe.reduction.enabled == task.jivaro.enabled
        if task.jivaro.frequency_limit is not None:
            assert recipe.reduction.frequency_limit_ghz == task.jivaro.frequency_limit
        if task.jivaro.error_max is not None:
            assert recipe.reduction.error_max_pct == task.jivaro.error_max
        assert recipe.policy.continue_on_lvs_fail == task.continue_on_lvs_fail

    # ---- same PDK paths
    assert report.profile.lvs_decks.dir_expr == project.paths.get("calibre_lvs_dir")
    assert str(report.profile.layer_map) == str(project.layer_map)
    assert report.profile.tech_name == project.tech_name
    assert report.workspace.intermediate_dir == project.intermediate_dir
    assert report.workspace.pdk_profile == report.profile.profile_id


def run_migration(config_dir: Path, out_root: Path, **kwargs: Any) -> MigrationReport:
    return migrate_v1_to_v2(
        config_dir / "project.yaml",
        config_dir / "tasks.yaml",
        template_root=V1_TEMPLATES,
        out_root=out_root,
        **kwargs,
    )


# ---- end to end on the two real configs --------------------------------------


@pytest.mark.parametrize("config_dir", [REAL_CONFIG, DEMO_CONFIG], ids=["real", "demo"])
def test_shipped_config_migrates_with_equivalent_semantics(
    config_dir: Path, tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(config_dir, tmp_path)
    assert_semantics_preserved(config_dir, report)


@pytest.mark.parametrize("config_dir", [REAL_CONFIG, DEMO_CONFIG], ids=["real", "demo"])
def test_shipped_config_writes_the_expected_file_set(
    config_dir: Path, tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(config_dir, tmp_path)
    expected = EXPECTED_FILES | {f"config/profiles/{report.profile.profile_id}.yaml"} | {
        f"recipes/{recipe.recipe_id}.yaml" for recipe in report.recipes
    }
    assert _relative_files(tmp_path) == expected
    assert {path.relative_to(tmp_path).as_posix() for path in report.written} == expected
    assert report.skipped == []


@pytest.mark.parametrize("config_dir", [REAL_CONFIG, DEMO_CONFIG], ids=["real", "demo"])
def test_written_files_reload_as_the_objects_that_were_written(
    config_dir: Path, tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(config_dir, tmp_path)
    assert load_cells(tmp_path / "config/cells.yaml").model_dump() == report.cells.model_dump()
    assert (
        load_workspace(tmp_path / "config/workspace.yaml").model_dump()
        == report.workspace.model_dump()
    )
    for recipe in report.recipes:
        reloaded = load_recipe(tmp_path / "recipes" / f"{recipe.recipe_id}.yaml")
        assert reloaded.model_dump() == recipe.model_dump()


def test_real_config_produces_one_recipe_named_after_its_parameters(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    assert [recipe.recipe_id for recipe in report.recipes] == ["rc-typical-55c"]
    recipe = report.recipes[0]
    assert recipe.extraction.extract_type.value == "rc_coupled"
    assert recipe.extraction.corner == "typical"
    assert recipe.extraction.temperature_c == 55.0
    # project knob 100 is overridden by the task knob 200
    assert recipe.extraction.exclude_floating_nets_limit == 200
    assert recipe.name == "rc_coupled, corner typical, 55C, extracted_view"


def test_demo_config_carries_the_jivaro_block_into_the_recipe(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(DEMO_CONFIG, tmp_path)
    recipe = report.recipes[0]
    assert recipe.reduction.enabled is True
    assert recipe.reduction.frequency_limit_ghz == 14.0
    assert recipe.reduction.error_max_pct == 2.0
    assert report.profile.profile_id == "hn001"
    assert report.profile.tech_name == "HN001"


def test_demo_config_expansion_becomes_two_explicit_rows(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(DEMO_CONFIG, tmp_path)
    assert report.cells.keys == [
        "DEMO_LIB__inv__layout__schematic",
        "DEMO_LIB__inv__layout_test__schematic",
    ]
    text = (tmp_path / "config/cells.yaml").read_text(encoding="utf-8")
    assert "layout_test" in text
    assert "[" not in text.split("cells:")[1]  # no list-valued axis survived


def test_profile_absorbs_the_pdk_fields(tmp_path: Path, elsewhere: None) -> None:
    report = run_migration(DEMO_CONFIG, tmp_path)
    profile = report.profile
    assert profile.lvs_decks.dir_expr == "$calibre_source_added_place|parent"
    assert profile.qrc.dir_expr.endswith("QCI_deck")
    assert profile.qrc.query_cmd_name == "query_cmd"
    assert profile.qrc.preserve_cell_list_name == "preserveCellList.txt"
    assert profile.lvs_decks.filename_pattern == "{basename}.{suffix}.qcilvs"
    assert profile.lvs_decks.basename is None
    assert [variant.name for variant in profile.lvs_decks.variants] == ["wodio", "widio"]
    assert profile.default_corner == "typical"
    assert profile.corners[0].technology_corner == "TYPICAL"
    assert "VDD" in profile.power_names
    assert "VSS" in profile.ground_names
    assert profile.env_overrides["WORK_ROOT"] == "/tmp/demo"


def test_required_env_excludes_the_synthetic_path_tokens(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    assert "WORK_ROOT" in report.profile.required_env
    assert "SETUP_ROOT" in report.profile.required_env
    assert "calibre_source_added_place" in report.profile.required_env
    for token in ("output_dir", "intermediate_dir", "calibre_lvs_dir", "qrc_deck_dir"):
        assert token not in report.profile.required_env


def test_resources_are_read_back_and_kept_out_of_the_recipe(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    assert report.resources.lvs_num_turbo == 2
    assert report.resources.lvs_license_wait_time == 10
    assert report.resources.lvs_run_mt is True
    assert report.resources.reduction_cpu == 1
    recipe_fields = report.recipes[0].model_dump()
    assert "lvs_num_turbo" not in str(recipe_fields)


def test_unfilled_qrc_placeholder_is_surfaced(tmp_path: Path, elsewhere: None) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    assert any(decision.key == "pdk.qrc_deck_dir" for decision in report.decisions)
    assert any("<runset>" in warning for warning in report.warnings)


def test_demo_config_has_no_qrc_placeholder_decision(tmp_path: Path, elsewhere: None) -> None:
    report = run_migration(DEMO_CONFIG, tmp_path)
    assert not any(decision.key == "pdk.qrc_deck_dir" for decision in report.decisions)


# ---- the three promises ------------------------------------------------------


def test_source_files_are_never_touched(tmp_path: Path, elsewhere: None) -> None:
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (REAL_CONFIG / "project.yaml", REAL_CONFIG / "tasks.yaml")
    }
    run_migration(REAL_CONFIG, tmp_path)
    for path, (data, mtime) in before.items():
        assert path.read_bytes() == data
        assert path.stat().st_mtime_ns == mtime
    assert {"project.yaml", "tasks.yaml"} <= _relative_files(REAL_CONFIG)
    assert not any(name.endswith((".j2", "recipes")) for name in _relative_files(REAL_CONFIG))


def test_nothing_is_written_outside_out_root(tmp_path: Path, elsewhere: None) -> None:
    out = tmp_path / "out"
    report = run_migration(REAL_CONFIG, out)
    assert all(out in path.parents for path in report.written)
    assert _relative_files(tmp_path) == {
        f"out/{name}" for name in _relative_files(out)
    }


def test_migration_is_idempotent(tmp_path: Path, elsewhere: None) -> None:
    first = run_migration(REAL_CONFIG, tmp_path)
    snapshot = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    second = run_migration(REAL_CONFIG, tmp_path)

    assert second.written == []
    assert sorted(second.skipped) == sorted(first.written)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == snapshot
    assert [r.recipe_id for r in second.recipes] == [r.recipe_id for r in first.recipes]
    assert any("identical content" in warning for warning in second.warnings)


def test_a_hand_edited_output_file_is_left_alone(tmp_path: Path, elsewhere: None) -> None:
    edited = tmp_path / "config" / "cells.yaml"
    edited.parent.mkdir(parents=True)
    edited.write_text("# I edited this by hand\ncells: []\n", encoding="utf-8")

    report = run_migration(REAL_CONFIG, tmp_path)

    assert edited.read_text(encoding="utf-8") == "# I edited this by hand\ncells: []\n"
    assert edited in report.skipped
    assert any("differs from what this migration would write" in w for w in report.warnings)


def test_dry_run_writes_nothing_but_reports_everything(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path, write=False)
    assert _relative_files(tmp_path) == set()
    assert report.written == []
    assert report.skipped == []
    assert report.recipes and report.cells.keys and report.decisions
    assert report.open_questions


def test_every_field_of_both_inputs_has_a_disposition(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    sources = {item.source for item in report.dispositions}
    for expected in (
        "project.yaml:layer_map",
        "project.yaml:env_overrides",
        "project.yaml:paths.calibre_lvs_dir",
        "project.yaml:paths.qrc_deck_dir",
        "project.yaml:extraction_output_dir",
        "project.yaml:intermediate_dir",
        "project.yaml:dspf_out_path",
        "project.yaml:templates.si",
        "project.yaml:templates.quantus",
        "tasks.yaml:library",
        "tasks.yaml:ground_net",
        "tasks.yaml:out_file",
        "tasks.yaml:label",
        "tasks.yaml:jivaro",
        "tasks.yaml:continue_on_lvs_fail",
        "knobs:quantus.exclude_floating_nets_limit",
        "knobs:calibre.lvs_variant",
    ):
        assert expected in sources, expected
    assert set(report.disposition_counts()) <= {
        "moved",
        "dropped",
        "folded",
        "seeded_from_template",
        "decision",
    }


def test_seed_patches_is_blocked_not_faked(tmp_path: Path, elsewhere: None) -> None:
    with pytest.raises(NotImplementedError, match="seed_patches is not implemented"):
        run_migration(REAL_CONFIG, tmp_path, seed_patches=True)
    assert _relative_files(tmp_path) == set()


def test_byte_fidelity_is_reported_as_unverified(tmp_path: Path, elsewhere: None) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    assert any("byte fidelity" in warning for warning in report.warnings)
    assert report.seeded_patches == []
    assert all(recipe.patches == [] for recipe in report.recipes)


# ---- decisions ---------------------------------------------------------------


def write_config(
    root: Path, *, project: str, tasks: str
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "project.yaml").write_text(project, encoding="utf-8")
    (root / "tasks.yaml").write_text(tasks, encoding="utf-8")
    return root


BASE_PROJECT = """\
env_overrides:
  WORK_ROOT: /tmp/x
  WORK_ROOT2: /tmp/x
  VERIFY_ROOT: /tmp/x/verify
  SETUP_ROOT: /tmp/x/setup
  PDK_LAYER_MAP_FILE: /tmp/x/layers.map
  calibre_source_added_place: /tmp/x/lvs/CFXXX/empty.cdl
paths:
  calibre_lvs_dir: $calibre_source_added_place|parent
  qrc_deck_dir: $VERIFY_ROOT/qrc/QCI_deck
tech_name: HN001
templates:
  si: templates/si/default.env.j2
  calibre: templates/calibre/calibre_lvs.qci.j2
  quantus: templates/quantus/ext.cmd.j2
  jivaro: templates/jivaro/default.xml.j2
"""

ONE_TASK = """\
- library: LIB
  cell: inv
  lvs_layout_view: layout
  lvs_source_view: schematic
  ground_net: vss
  out_file: av_ext
"""


def test_two_knob_sets_become_two_recipes_named_by_what_differs(
    tmp_path: Path, elsewhere: None
) -> None:
    config = write_config(
        tmp_path / "cfg",
        project=BASE_PROJECT,
        tasks=(
            "- library: LIB\n"
            "  cell: inv\n"
            "  lvs_layout_view: layout\n"
            "  out_file: av_ext\n"
            "  knobs:\n    quantus:\n      exclude_floating_nets_limit: 100\n"
            "- library: LIB\n"
            "  cell: buf\n"
            "  lvs_layout_view: layout\n"
            "  out_file: av_ext\n"
            "  knobs:\n    quantus:\n      exclude_floating_nets_limit: 900\n"
        ),
    )
    report = run_migration(config, tmp_path / "out")
    assert sorted(recipe.recipe_id for recipe in report.recipes) == [
        "rc-typical-55c-fl100",
        "rc-typical-55c-fl900",
    ]
    limits = {r.recipe_id: r.extraction.exclude_floating_nets_limit for r in report.recipes}
    assert limits == {"rc-typical-55c-fl100": 100, "rc-typical-55c-fl900": 900}
    assert_semantics_preserved(config, report)


def test_two_jivaro_settings_become_two_recipes(tmp_path: Path, elsewhere: None) -> None:
    config = write_config(
        tmp_path / "cfg",
        project=BASE_PROJECT,
        tasks=(
            "- library: LIB\n  cell: inv\n  lvs_layout_view: layout\n  out_file: av_ext\n"
            "  jivaro: {enabled: true, frequency_limit: 14, error_max: 2}\n"
            "- library: LIB\n  cell: buf\n  lvs_layout_view: layout\n  out_file: av_ext\n"
            "  jivaro: {enabled: false}\n"
        ),
    )
    report = run_migration(config, tmp_path / "out")
    assert sorted(r.recipe_id for r in report.recipes) == [
        "rc-typical-55c-nored",
        "rc-typical-55c-red",
    ]
    by_id = {r.recipe_id: r for r in report.recipes}
    assert by_id["rc-typical-55c-red"].reduction.enabled is True
    assert by_id["rc-typical-55c-nored"].reduction.enabled is False


def test_recipe_ids_are_stable_across_runs(tmp_path: Path, elsewhere: None) -> None:
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=ONE_TASK)
    first = run_migration(config, tmp_path / "a")
    second = run_migration(config, tmp_path / "b")
    assert [r.recipe_id for r in first.recipes] == [r.recipe_id for r in second.recipes]


def test_a_resolver_can_rename_a_recipe(tmp_path: Path, elsewhere: None) -> None:
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=ONE_TASK)

    def resolver(decision: MigrationDecision) -> Any:
        if decision.key == "recipe.1.id":
            return "nightly-signoff"
        return decision.default

    report = run_migration(config, tmp_path / "out", resolve=resolver)
    assert [r.recipe_id for r in report.recipes] == ["nightly-signoff"]
    assert (tmp_path / "out/recipes/nightly-signoff.yaml").is_file()
    assert report.answers["recipe.1.id"] == "nightly-signoff"


def test_an_answer_outside_the_offered_options_is_refused(
    tmp_path: Path, elsewhere: None
) -> None:
    project = BASE_PROJECT + 'extraction_output_dir: "${WORK_ROOT}/QCI_{task_id}"\n'
    config = write_config(tmp_path / "cfg", project=project, tasks=ONE_TASK)

    def resolver(decision: MigrationDecision) -> Any:
        return "banana" if decision.options else decision.default

    with pytest.raises(MigrationError, match="not one of"):
        run_migration(config, tmp_path / "out", resolve=resolver)


def test_task_id_in_a_path_pattern_becomes_a_decision(
    tmp_path: Path, elsewhere: None
) -> None:
    project = BASE_PROJECT + (
        'extraction_output_dir: "${WORK_ROOT}/cds/verify/QCI_{task_id}"\n'
        'dspf_out_path: "${WORK_ROOT2}/{task_id}.dspf"\n'
    )
    config = write_config(tmp_path / "cfg", project=project, tasks=ONE_TASK)
    report = run_migration(config, tmp_path / "out")

    decision = next(d for d in report.decisions if d.key == "path.task_id")
    assert decision.options == ["{run_slug}", "{cell}"]
    assert report.workspace.output_dir_pattern.endswith("QCI_{run_slug}")
    assert report.workspace.dspf_out_pattern.endswith("{run_slug}.dspf")


def test_task_id_decision_can_be_answered_with_cell(tmp_path: Path, elsewhere: None) -> None:
    project = BASE_PROJECT + 'extraction_output_dir: "${WORK_ROOT}/QCI_{task_id}"\n'
    config = write_config(tmp_path / "cfg", project=project, tasks=ONE_TASK)

    def resolver(decision: MigrationDecision) -> Any:
        return "{cell}" if decision.key == "path.task_id" else decision.default

    report = run_migration(config, tmp_path / "out", resolve=resolver)
    assert report.workspace.output_dir_pattern.endswith("QCI_{cell}")


def test_retired_view_format_keys_are_rewritten(tmp_path: Path, elsewhere: None) -> None:
    project = BASE_PROJECT + (
        'extraction_output_dir: "${WORK_ROOT}/QCI_{cell}_{lvs_layout_view}"\n'
    )
    config = write_config(tmp_path / "cfg", project=project, tasks=ONE_TASK)
    report = run_migration(config, tmp_path / "out")
    assert report.workspace.output_dir_pattern.endswith("QCI_{cell}_{layout_view}")


def test_per_task_dspf_override_collapses_into_one_pattern(
    tmp_path: Path, elsewhere: None
) -> None:
    tasks = ONE_TASK + '  dspf_out_path: "${WORK_ROOT2}/custom_{cell}.dspf"\n'
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=tasks)

    report = run_migration(config, tmp_path / "out")
    decision = next(d for d in report.decisions if d.key == "path.dspf_out")
    assert "${WORK_ROOT2}/custom_{cell}.dspf" in decision.options
    assert report.workspace.dspf_out_pattern == "${WORK_ROOT2}/{cell}.dspf"

    def resolver(d: MigrationDecision) -> Any:
        return "${WORK_ROOT2}/custom_{cell}.dspf" if d.key == "path.dspf_out" else d.default

    chosen = run_migration(config, tmp_path / "out2", resolve=resolver)
    assert chosen.workspace.dspf_out_pattern == "${WORK_ROOT2}/custom_{cell}.dspf"


def test_excluded_combinations_are_reported_and_dropped_by_default(
    tmp_path: Path, elsewhere: None
) -> None:
    tasks = (
        "- library: LIB\n"
        "  cell: [inv, buf]\n"
        "  lvs_layout_view: [layout, layout_test]\n"
        "  out_file: av_ext\n"
        "  exclude:\n"
        "    - {cell: buf, lvs_layout_view: layout_test}\n"
    )
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=tasks)
    report = run_migration(config, tmp_path / "out")

    assert len(report.cells) == 3
    assert "LIB__buf__layout_test__schematic" not in report.cells.keys
    dropped = [
        item
        for item in report.dispositions
        if item.source == "tasks.yaml:exclude" and item.action == "dropped"
    ]
    assert len(dropped) == 1
    assert "LIB__buf__layout_test__schematic" in dropped[0].note


def test_excluded_combinations_can_become_parked_rows(
    tmp_path: Path, elsewhere: None
) -> None:
    tasks = (
        "- library: LIB\n"
        "  cell: [inv, buf]\n"
        "  lvs_layout_view: [layout, layout_test]\n"
        "  out_file: av_ext\n"
        "  exclude:\n"
        "    - {cell: buf, lvs_layout_view: layout_test}\n"
    )
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=tasks)

    def resolver(decision: MigrationDecision) -> Any:
        return "disable" if decision.key == "cells.excluded" else decision.default

    report = run_migration(config, tmp_path / "out", resolve=resolver)
    assert len(report.cells) == 4
    parked = report.cells.entry("LIB__buf__layout_test__schematic")
    assert parked.enabled is False
    assert parked.note is not None
    assert len(report.cells.enabled_cells()) == 3


def test_a_cell_under_two_recipes_asks_for_confirmation(
    tmp_path: Path, elsewhere: None
) -> None:
    tasks = (
        "- library: LIB\n  cell: inv\n  lvs_layout_view: layout\n  out_file: av_ext\n"
        "  knobs: {quantus: {temperature: 25.0}}\n"
        "- library: LIB\n  cell: inv\n  lvs_layout_view: layout\n  out_file: av_ext\n"
        "  knobs: {quantus: {temperature: 85.0}}\n"
    )
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=tasks)
    report = run_migration(config, tmp_path / "out")

    assert sorted(r.recipe_id for r in report.recipes) == ["rc-typical-25c", "rc-typical-85c"]
    assert len(report.cells) == 1
    assert any(d.key == "cells.multi_recipe" for d in report.decisions)
    assert any("more than one recipe" in w for w in report.warnings)


def test_answering_no_to_the_multi_recipe_question_aborts(
    tmp_path: Path, elsewhere: None
) -> None:
    tasks = (
        "- library: LIB\n  cell: inv\n  lvs_layout_view: layout\n  out_file: av_ext\n"
        "  knobs: {quantus: {temperature: 25.0}}\n"
        "- library: LIB\n  cell: inv\n  lvs_layout_view: layout\n  out_file: av_ext\n"
        "  knobs: {quantus: {temperature: 85.0}}\n"
    )
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=tasks)

    def resolver(decision: MigrationDecision) -> Any:
        return "no" if decision.key == "cells.multi_recipe" else decision.default

    with pytest.raises(MigrationError, match="more than one recipe"):
        run_migration(config, tmp_path / "out", resolve=resolver)


def test_the_same_cell_with_conflicting_settings_is_refused(
    tmp_path: Path, elsewhere: None
) -> None:
    tasks = (
        "- library: LIB\n  cell: inv\n  lvs_layout_view: layout\n  ground_net: vss\n"
        "- library: LIB\n  cell: inv\n  lvs_layout_view: layout\n  ground_net: gnd\n"
    )
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=tasks)
    with pytest.raises(MigrationError, match="twice with different settings"):
        run_migration(config, tmp_path / "out")


def test_employee_id_is_flagged_rather_than_written_to_the_home_dir(
    tmp_path: Path, elsewhere: None
) -> None:
    config = write_config(
        tmp_path / "cfg", project=BASE_PROJECT + "employee_id: e12345\n", tasks=ONE_TASK
    )
    report = run_migration(config, tmp_path / "out")
    entry = next(i for i in report.dispositions if i.source == "project.yaml:employee_id")
    assert entry.target == "~/.auto_ext/site.yaml:employee_id"
    assert any("site.yaml" in w for w in report.warnings)
    assert _relative_files(tmp_path / "out")  # everything stayed under out_root


# ---- template read-back ------------------------------------------------------


def v1_texts() -> dict[RenderTarget, str]:
    """The five frozen v1 template bodies -- the migration's actual input.

    The v1 tree and not ``templates/``: the shipped tree is being
    parameterised row by row, so its literals are moving into the catalog
    *by design* and a read-back of it gets thinner with every round. What
    the catalog's defaults were transcribed from, and what a user's disk
    still holds when they migrate, is this frozen copy -- which is where
    a divergence between catalog and reality is worth failing over.
    """

    return {
        RenderTarget.SI_ENV: (V1_TEMPLATES / "si/default.env.j2").read_text(encoding="utf-8"),
        RenderTarget.LVS_QCI: (V1_TEMPLATES / "calibre/calibre_lvs.qci.j2").read_text(
            encoding="utf-8"
        ),
        RenderTarget.QUANTUS_EXT: (V1_TEMPLATES / "quantus/ext.cmd.j2").read_text(
            encoding="utf-8"
        ),
        RenderTarget.QUANTUS_DSPF: (V1_TEMPLATES / "quantus/dspf.cmd.j2").read_text(
            encoding="utf-8"
        ),
        RenderTarget.JIVARO_XML: (V1_TEMPLATES / "jivaro/default.xml.j2").read_text(
            encoding="utf-8"
        ),
    }


def test_the_v1_templates_agree_with_every_catalog_default() -> None:
    """The strongest available cross-check of the catalog against reality."""

    readback = read_back_from_templates(v1_texts())
    assert readback.diverged == {}
    # 88 of the 120 recipe/profile/resource rows are literals in a shipped
    # template; the rest are knobs, jinja vars or not emitted at all.
    assert len(readback.values) >= 85


def test_every_unread_row_carries_a_reason() -> None:
    readback = read_back_from_templates(v1_texts())
    assert all(reason for reason in readback.unread.values())
    catalog = builtin_catalog()
    for key, reason in readback.unread.items():
        option = catalog.option(key)
        # A hardcoded literal that is present in a file we read must not be
        # silently unread: the only acceptable reasons are "the file is not
        # part of this project" and "the value is a Jinja expression".
        if option.currently.value == "hardcoded_literal":
            assert "not part of this project" in reason or "Jinja expression" in reason, key


def test_read_back_covers_every_owner_it_claims_to() -> None:
    readback = read_back_from_templates(v1_texts())
    catalog = builtin_catalog()
    owners = {catalog.option(key).owner for key in readback.values}
    assert owners <= {Owner.RECIPE, Owner.PROFILE, Owner.RESOURCES}
    assert Owner.RECIPE in owners and Owner.PROFILE in owners and Owner.RESOURCES in owners


def test_read_back_ignores_jinja_sites() -> None:
    readback = read_back_from_templates(v1_texts())
    for key in ("lvs_deck_variant", "temperature_c", "reduction_frequency_limit_ghz"):
        assert key not in readback.values
        assert "Jinja expression" in readback.unread[key]


def test_quantus_parser_handles_all_four_layouts() -> None:
    parsed = _parse_quantus(v1_texts()[RenderTarget.QUANTUS_DSPF])
    # inline
    assert parsed[("extract", "-type")].values == ("rc_coupled",)
    # value on the next line
    assert parsed[("process_technology", "-technology_corner")].values == ("TYPICAL",)
    # one value per line
    assert parsed[("output_db", "-output_xy")].values[0] == "CANONICAL_RES"
    assert len(parsed[("output_db", "-output_xy")].values) == 8
    # an option carried on the section header line
    assert parsed[("output_db", "-type")].values == ("dspf",)
    assert parsed[("input_db", "-type")].values == ("calibre",)


def test_quantus_parser_keeps_sections_apart() -> None:
    parsed = _parse_quantus(v1_texts()[RenderTarget.QUANTUS_DSPF])
    assert parsed[("extract", "-type")].values == ("rc_coupled",)
    assert parsed[("metal_fill", "-type")].values == ("virtual",)
    assert parsed[("output_db", "-type")].values == ("dspf",)


def test_skill_parser_reads_values_and_lists() -> None:
    parsed = _parse_skill(v1_texts()[RenderTarget.SI_ENV])
    assert parsed[("", "shortRES")].values == ("2000.0",)
    assert parsed[("", "preserveRES")].values == ("'t",)
    assert parsed[("", "checkRESSIZE")].values == ("'nil",)
    assert parsed[("", "globalPowerSig")].values == ("",)
    assert '"auCdl" "schematic"' in parsed[("", "simViewList")].text


def test_calibre_parser_reads_the_supply_lists() -> None:
    parsed = _parse_calibre(v1_texts()[RenderTarget.LVS_QCI])
    assert parsed[("", "*lvsReportOptions")].values == ("S",)
    assert "VDD" in parsed[("", "*lvsPowerNames")].text.split()
    assert parsed[("", "*cmnNumTurbo")].values == ("2",)


def test_xml_parser_reads_the_jivaro_elements() -> None:
    parsed = _parse_xml(v1_texts()[RenderTarget.JIVARO_XML])
    assert parsed[("", "criterion")].values == ("standard",)
    assert parsed[("", "rModel")].values == ("analogLib/presistor/symbol",)


def test_typed_read_back_values() -> None:
    values = read_back_from_templates(v1_texts()).values
    assert values["netlist_short_res"] == 2000.0
    assert values["netlist_preserve_res"] is True
    assert values["netlist_check_res_size"] is False
    assert values["netlist_view_list"] == ["auCdl", "schematic"]
    assert values["netlist_global_power_sig"] == ""
    assert values["lvs_svdb_cci"] is True
    assert values["lvs_device_filter_enabled"] is False
    assert values["power_names"][0] == "AHVDD"
    assert values["output_xy"][-1] == "GENERIC"
    assert values["run_qrc_query"] is True
    assert values["qrc_query_cmd_name"] == "query_cmd"
    assert values["qrc_preserve_cell_list_name"] == "preserveCellList.txt"
    assert values["lvs_rules_filename_pattern"] == "{basename}.{suffix}.qcilvs"
    assert values["technology_corner"] == "TYPICAL"
    assert values["lvs_num_turbo"] == 2


def test_edited_template_literals_win_over_the_catalog(
    tmp_path: Path, elsewhere: None
) -> None:
    """A cloned, hand-edited template migrates its values, not ours."""

    templates = tmp_path / "templates"
    for relative in (
        "si/default.env.j2",
        "calibre/calibre_lvs.qci.j2",
        "quantus/ext.cmd.j2",
        "jivaro/default.xml.j2",
    ):
        target = templates / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            (V1_TEMPLATES / relative).read_text(encoding="utf-8"), encoding="utf-8"
        )
        manifest = V1_TEMPLATES / (relative + ".manifest.yaml")
        if manifest.is_file():
            (templates / (relative + ".manifest.yaml")).write_text(
                manifest.read_text(encoding="utf-8"), encoding="utf-8"
            )

    si = templates / "si/default.env.j2"
    si.write_text(
        si.read_text(encoding="utf-8").replace("shortRES = 2000.0", "shortRES = 1234.0"),
        encoding="utf-8",
    )
    qci = templates / "calibre/calibre_lvs.qci.j2"
    qci.write_text(
        qci.read_text(encoding="utf-8").replace("*lvsReportOptions: S", "*lvsReportOptions: SA"),
        encoding="utf-8",
    )
    ext = templates / "quantus/ext.cmd.j2"
    ext.write_text(
        ext.read_text(encoding="utf-8").replace("-decoupling_factor 1.0", "-decoupling_factor 2.5"),
        encoding="utf-8",
    )

    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=ONE_TASK)
    report = migrate_v1_to_v2(
        config / "project.yaml",
        config / "tasks.yaml",
        template_root=templates,
        out_root=tmp_path / "out",
    )

    recipe = report.recipes[0]
    assert recipe.netlist.short_res_ohm == 1234.0
    assert recipe.lvs.report_options == "SA"
    assert recipe.extraction.decoupling_factor == 2.5
    assert any("differs from the built-in" in warning for warning in report.warnings)
    assert any("netlist_short_res" in warning for warning in report.warnings)
    seeded = {
        item.target
        for item in report.dispositions
        if item.action == "seeded_from_template"
    }
    assert "recipe:netlist.short_res_ohm" in seeded


def test_a_dspf_quantus_slot_migrates_to_emit_dspf(tmp_path: Path, elsewhere: None) -> None:
    project = BASE_PROJECT.replace(
        "quantus: templates/quantus/ext.cmd.j2", "quantus: templates/quantus/dspf.cmd.j2"
    )
    config = write_config(tmp_path / "cfg", project=project, tasks=ONE_TASK)
    report = run_migration(config, tmp_path / "out")

    recipe = report.recipes[0]
    assert recipe.output.emit == [OutputKind.DSPF]
    assert recipe.output.dspf.subtype == "extended"
    assert recipe.output.dspf.output_xy[0] == "CANONICAL_RES"
    assert recipe.extraction.metal_fill.value == "virtual"
    assert recipe.recipe_id == "rc-typical-55c"


def test_template_resolution_never_falls_back_to_the_repository(
    tmp_path: Path, elsewhere: None
) -> None:
    empty = tmp_path / "empty_templates"
    empty.mkdir()
    assert (
        _resolve_template_file(
            Path("templates/si/default.env.j2"), template_root=empty, repo_root=empty.parent
        )
        is None
    )
    assert (
        _resolve_template_file(
            Path("templates/si/default.env.j2"), template_root=TEMPLATES, repo_root=REPO_ROOT
        )
        == TEMPLATES / "si/default.env.j2"
    )


def test_missing_template_is_a_warning_not_a_crash(tmp_path: Path, elsewhere: None) -> None:
    templates = tmp_path / "templates"
    (templates / "quantus").mkdir(parents=True)
    for relative in ("si/default.env.j2", "calibre/calibre_lvs.qci.j2", "jivaro/default.xml.j2"):
        target = templates / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            (V1_TEMPLATES / relative).read_text(encoding="utf-8"), encoding="utf-8"
        )
    (templates / "calibre/calibre_lvs.qci.j2.manifest.yaml").write_text(
        (V1_TEMPLATES / "calibre/calibre_lvs.qci.j2.manifest.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    project = BASE_PROJECT.replace("  quantus: templates/quantus/ext.cmd.j2\n", "")
    config = write_config(tmp_path / "cfg", project=project, tasks=ONE_TASK)
    report = migrate_v1_to_v2(
        config / "project.yaml",
        config / "tasks.yaml",
        template_root=templates,
        out_root=tmp_path / "out",
    )
    assert any("no quantus template" in warning for warning in report.warnings)
    assert report.recipes[0].output.emit  # keeps the catalog default


# ---- open questions and the report -------------------------------------------


def test_open_questions_only_name_values_that_were_written(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    written = _relative_files(tmp_path)
    assert report.open_questions
    for question in report.open_questions:
        assert question.file in written, question.file
        assert question.question
        assert question.field_path


def test_open_questions_are_written_next_to_the_value(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    recipe_text = (tmp_path / "recipes" / f"{report.recipes[0].recipe_id}.yaml").read_text(
        encoding="utf-8"
    )
    assert "NEEDS CONFIRMATION" in recipe_text
    assert "coupling_cap_threshold_absolute" in recipe_text
    lines = recipe_text.splitlines()
    index = next(i for i, line in enumerate(lines) if "coupling_cap_threshold_absolute:" in line)
    assert any("NEEDS CONFIRMATION" in line for line in lines[max(0, index - 3) : index])


def test_generated_files_say_the_sources_were_left_alone(
    tmp_path: Path, elsewhere: None
) -> None:
    run_migration(REAL_CONFIG, tmp_path)
    for name in ("config/cells.yaml", "config/workspace.yaml", "config/resources.yaml"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert "left untouched" in text
        assert "never overwritten" in text


def test_format_report_covers_every_section(tmp_path: Path, elsewhere: None) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    text = format_report(report)
    for heading in (
        "=== produced ===",
        "=== field dispositions ===",
        "=== decisions taken",
        "=== needs confirmation",
        "=== warnings ===",
        "=== files ===",
    ):
        assert heading in text
    assert report.recipes[0].recipe_id in text
    assert "TODO_LIBRARY_NAME__TODO_CELL_NAME__layout__schematic" in text


def test_needs_confirmation_merges_decisions_and_questions(
    tmp_path: Path, elsewhere: None
) -> None:
    report = run_migration(REAL_CONFIG, tmp_path)
    lines = report.needs_confirmation()
    assert len(lines) == len(report.decisions) + len(report.open_questions)
    assert any("corner.semantic_name" in line for line in lines)


# ---- WorkspaceConfig ---------------------------------------------------------
# The workspace object is a migration product, so its model tests live with the
# migration rather than in a module of their own.


def test_workspace_defaults() -> None:
    workspace = WorkspaceConfig(pdk_profile="hn001")
    assert workspace.output_dir_pattern == "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    assert workspace.intermediate_dir == "${WORK_ROOT2}"
    assert workspace.dspf_out_pattern == "${WORK_ROOT2}/{cell}.dspf"
    assert workspace.keep_runs == 0
    assert workspace.format_keys_used() == {"cell"}


def test_workspace_rejects_the_retired_task_id_key() -> None:
    with pytest.raises(ValueError, match="run_slug"):
        WorkspaceConfig(pdk_profile="x", output_dir_pattern="${WORK_ROOT}/QCI_{task_id}")


def test_workspace_rejects_an_unknown_format_key() -> None:
    with pytest.raises(ValueError, match="unknown format key"):
        WorkspaceConfig(pdk_profile="x", dspf_out_pattern="${WORK_ROOT2}/{wibble}.dspf")


def test_workspace_does_not_mistake_an_env_reference_for_a_format_key() -> None:
    workspace = WorkspaceConfig(
        pdk_profile="x",
        output_dir_pattern="${WORK_ROOT}/${SOME_OTHER}/{run_slug}",
        intermediate_dir="${WORK_ROOT2}",
        dspf_out_pattern="${WORK_ROOT2}/out.dspf",
    )
    assert workspace.format_keys_used() == {"run_slug"}


def test_workspace_accepts_every_documented_format_key() -> None:
    pattern = "${WORK_ROOT}/{library}/{cell}/{layout_view}/{source_view}/{recipe}/{run_id}/{run_slug}"
    workspace = WorkspaceConfig(pdk_profile="x", output_dir_pattern=pattern)
    assert len(workspace.format_keys_used()) == 7


def test_workspace_round_trips_through_disk(tmp_path: Path) -> None:
    from auto_ext.model.workspace import save_workspace

    workspace = WorkspaceConfig(pdk_profile="hn001", keep_runs=20)
    path = tmp_path / "workspace.yaml"
    save_workspace(workspace, path)
    assert load_workspace(path).model_dump() == workspace.model_dump()


def test_workspace_future_schema_version_is_refused(tmp_path: Path) -> None:
    from auto_ext.core.errors import ConfigError

    path = tmp_path / "workspace.yaml"
    path.write_text("schema_version: 99\npdk_profile: x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="newer than this build"):
        load_workspace(path)


def test_workspace_negative_keep_runs_is_refused() -> None:
    with pytest.raises(ValueError):
        WorkspaceConfig(pdk_profile="x", keep_runs=-1)


def test_an_excluded_combination_another_spec_produces_does_not_duplicate_the_row(
    tmp_path: Path, elsewhere: None
) -> None:
    """The parked-row answer must not collide with a row that really runs."""

    tasks = (
        "- library: LIB\n"
        "  cell: [inv, buf]\n"
        "  lvs_layout_view: layout\n"
        "  out_file: av_ext\n"
        "  exclude:\n"
        "    - {cell: buf}\n"
        "- library: LIB\n"
        "  cell: buf\n"
        "  lvs_layout_view: layout\n"
        "  out_file: av_ext\n"
    )
    config = write_config(tmp_path / "cfg", project=BASE_PROJECT, tasks=tasks)

    def resolver(decision: MigrationDecision) -> Any:
        return "disable" if decision.key == "cells.excluded" else decision.default

    report = run_migration(config, tmp_path / "out", resolve=resolver)
    assert report.cells.keys == ["LIB__inv__layout__schematic", "LIB__buf__layout__schematic"]
    assert report.cells.entry("LIB__buf__layout__schematic").enabled is True
    assert any("already in the table" in warning for warning in report.warnings)


# ---- tables a legacy config structurally cannot hold -------------------------
#
# The first real red-zone migration succeeded and then failed `check-env` on
# two blocking rows -- `pdk.corners (empty)` and `lvs.variants (empty)` --
# because the old script had no such tables to migrate. The only way out was
# to hand-merge the shipped profile (which has the PDK facts) with the
# migrated one (which has the site's real paths); neither is a superset of the
# other. `docs/refactor/DEPLOY_FINDINGS.md` section 2 is the incident.


def _templates_without(root: Path, *, corner: bool = False, variants: bool = False) -> Path:
    """A copy of the v1 template tree with one unmigratable table removed.

    Removing rather than synthesising: the point is a tree that genuinely
    cannot answer the question, which is what a real legacy tree looks like.
    """

    dest = root / "templates"
    shutil.copytree(V1_TEMPLATES, dest)
    if corner:
        # The literal spans two continued lines in the .cmd; dropping both is
        # what a template that never named a corner looks like.
        corner_lines = '              -technology_corner \\\n              "TYPICAL" \\\n'
        for cmd in (dest / "quantus").glob("*.cmd.j2"):
            text = cmd.read_text(encoding="utf-8")
            assert corner_lines in text, cmd
            cmd.write_text(text.replace(corner_lines, ""), encoding="utf-8")
    if variants:
        # No `choices:` means no variant table, which is how _lvs_variants
        # reads "this manifest cannot tell you the alternatives".
        manifest = dest / "calibre" / "calibre_lvs.qci.j2.manifest.yaml"
        text = manifest.read_text(encoding="utf-8")
        choices = "    choices: [wodio, widio]\n"
        assert choices in text, manifest
        manifest.write_text(text.replace(choices, ""), encoding="utf-8")
    return dest


def _migrate_with_templates(config_dir: Path, out_root: Path, templates: Path) -> MigrationReport:
    return migrate_v1_to_v2(
        config_dir / "project.yaml",
        config_dir / "tasks.yaml",
        template_root=templates,
        out_root=out_root,
    )


def test_the_real_config_needs_no_shipped_fallback(tmp_path: Path, elsewhere: None) -> None:
    """The guard for every other test here: fallback fires only when needed.

    A migration that quietly reached for the shipped profile while the user's
    own templates could answer would break value-neutrality, and every
    assertion below would still pass.
    """

    report = run_migration(REAL_CONFIG, tmp_path)
    assert report.shipped_fallbacks == []


def test_a_missing_corner_table_comes_from_the_shipped_profile(
    tmp_path: Path, elsewhere: None
) -> None:
    templates = _templates_without(tmp_path, corner=True)
    report = _migrate_with_templates(REAL_CONFIG, tmp_path / "out", templates)

    shipped = builtin_profile()
    assert shipped is not None and shipped.corners, "the shipped profile lost its corners"
    assert report.shipped_fallbacks == ["corners"]
    assert [c.name for c in report.profile.corners] == [c.name for c in shipped.corners]
    assert report.profile.default_corner == shipped.default_corner


def test_a_missing_variant_table_comes_from_the_shipped_profile(
    tmp_path: Path, elsewhere: None
) -> None:
    templates = _templates_without(tmp_path, variants=True)
    report = _migrate_with_templates(REAL_CONFIG, tmp_path / "out", templates)

    shipped = builtin_profile()
    assert shipped is not None and shipped.lvs_decks.variants
    assert report.shipped_fallbacks == ["lvs_decks.variants"]
    assert [v.name for v in report.profile.lvs_decks.variants] == [
        v.name for v in shipped.lvs_decks.variants
    ]
    assert report.profile.lvs_decks.default_variant == shipped.lvs_decks.default_variant


def test_the_seeded_table_still_carries_the_sites_own_deck_path(
    tmp_path: Path, elsewhere: None
) -> None:
    """The half the shipped profile does NOT have must survive the fallback.

    This is exactly what made the hand-merge necessary: the seed profile has
    the PDK facts and the migrated profile has the site's real paths.
    """

    templates = _templates_without(tmp_path, corner=True, variants=True)
    report = _migrate_with_templates(REAL_CONFIG, tmp_path / "out", templates)
    project = load_project_v1(REAL_CONFIG / "project.yaml")
    assert report.profile.lvs_decks.dir_expr == project.paths.get("calibre_lvs_dir")
    assert report.profile.qrc.dir_expr == project.paths.get("qrc_deck_dir")


def test_a_seeded_table_is_reported_four_ways(tmp_path: Path, elsewhere: None) -> None:
    """Silence is the failure mode here: a value that is not the user's."""

    templates = _templates_without(tmp_path, corner=True)
    out = tmp_path / "out"
    report = _migrate_with_templates(REAL_CONFIG, out, templates)

    # 1. named on the report
    assert "corners" in report.shipped_fallbacks
    # 2. a disposition, so "no field disappears silently" still holds
    seeded = [d for d in report.dispositions if d.action == "seeded_from_shipped_profile"]
    assert [d.target for d in seeded] == [f"profile:{report.profile.profile_id}.corners"]
    # 3. a warning, which is what makes `migrate` exit 1
    assert any("NOT from your config" in warning for warning in report.warnings)
    # 4. its own section in the plain-text report
    assert "NOT from your config" in format_report(report)


def test_the_written_yaml_says_the_table_is_not_the_users(
    tmp_path: Path, elsewhere: None
) -> None:
    """The file has to say so on its own -- nobody keeps the report around."""

    templates = _templates_without(tmp_path, corner=True, variants=True)
    out = tmp_path / "out"
    report = migrate_v1_to_v2(
        REAL_CONFIG / "project.yaml",
        REAL_CONFIG / "tasks.yaml",
        template_root=templates,
        out_root=out,
        write=True,
    )
    text = (out / "config" / "profiles" / f"{report.profile.profile_id}.yaml").read_text(
        encoding="utf-8"
    )
    assert "NOT FROM YOUR CONFIG" in text
    assert "only the corner the templates hardcode" not in text


def test_an_empty_table_survives_when_there_is_no_shipped_profile(
    tmp_path: Path, elsewhere: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No shipped profile is not a migration failure -- it is the old behaviour."""

    monkeypatch.setattr("auto_ext.migrate.builtin_profile", lambda: None)
    templates = _templates_without(tmp_path, corner=True, variants=True)
    report = _migrate_with_templates(REAL_CONFIG, tmp_path / "out", templates)
    assert report.shipped_fallbacks == []
    assert report.profile.corners == []
    assert report.profile.lvs_decks.variants == []


def test_a_corner_the_templates_do_have_is_never_replaced(
    tmp_path: Path, elsewhere: None
) -> None:
    """Value-neutrality still holds wherever the templates can answer.

    One corner read back from the user's templates is *worse* than the nine in
    the shipped profile by every measure except the one that matters: it is
    theirs.
    """

    report = run_migration(REAL_CONFIG, tmp_path)
    assert [c.technology_corner for c in report.profile.corners] == ["TYPICAL"]
    assert "corners" not in report.shipped_fallbacks
