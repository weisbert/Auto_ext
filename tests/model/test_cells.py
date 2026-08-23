"""Tests for :mod:`auto_ext.model.cells` -- the DUT table.

The load-bearing behaviours here are the ones that changed against
``tasks.yaml``: expansion happens at add time, duplicate rows are an error
rather than a warning, and ``enabled`` replaces ``exclude``. One test pins the
expansion order against the legacy loader, because a migrated table that
listed its rows in a different order than the old flow produced tasks would be
correct and still unreadable next to a run history.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_ext.core.config import load_tasks
from auto_ext.core.errors import ConfigError
from auto_ext.model.cells import (
    CELLS_SCHEMA_VERSION,
    CellBook,
    CellEntry,
    dump_cells_yaml,
    expand_cells,
    load_cells,
    load_cells_with_raw,
    save_cells,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_entry(**overrides: object) -> CellEntry:
    fields: dict[str, object] = {
        "library": "EXAMPLE_LIB",
        "cell": "inv",
        "layout_view": "layout",
    }
    fields.update(overrides)
    return CellEntry(**fields)  # type: ignore[arg-type]


# ---- CellEntry ---------------------------------------------------------------


def test_defaults_match_the_legacy_task_spec() -> None:
    entry = make_entry()
    assert entry.source_view == "schematic"
    assert entry.ground_net == "vss"
    assert entry.out_file is None
    assert entry.display_name is None
    assert entry.enabled is True
    assert entry.note is None


def test_key_is_the_legacy_task_id_spelling() -> None:
    entry = make_entry(source_view="cdl")
    assert entry.key == "EXAMPLE_LIB__inv__layout__cdl"


def test_label_falls_back_to_the_key() -> None:
    assert make_entry().label == "EXAMPLE_LIB__inv__layout__schematic"
    assert make_entry(display_name="the DCO").label == "the DCO"


@pytest.mark.parametrize("field", ["display_name", "out_file", "note"])
def test_blank_strings_round_trip_to_none(field: str) -> None:
    entry = make_entry(**{field: "   "})
    assert getattr(entry, field) is None


@pytest.mark.parametrize("field", ["library", "cell", "layout_view", "source_view", "ground_net"])
def test_empty_identity_fields_are_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        make_entry(**{field: ""})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_entry(lvs_layout_view="layout")
    assert "lvs_layout_view" in str(excinfo.value)


# ---- CellBook ----------------------------------------------------------------


def test_duplicate_rows_are_rejected_not_warned() -> None:
    row = make_entry()
    with pytest.raises(ValidationError) as excinfo:
        CellBook(cells=[row, row.model_copy()])
    assert "duplicate cell rows" in str(excinfo.value)
    assert row.key in str(excinfo.value)


def test_a_disabled_duplicate_is_still_a_duplicate() -> None:
    with pytest.raises(ValidationError):
        CellBook(cells=[make_entry(), make_entry(enabled=False)])


def test_rows_differing_only_in_an_axis_are_not_duplicates() -> None:
    book = CellBook(cells=[make_entry(), make_entry(layout_view="layout_test")])
    assert len(book) == 2


def test_enabled_cells_filters_parked_rows() -> None:
    book = CellBook(cells=[make_entry(), make_entry(cell="buf", enabled=False)])
    assert [entry.cell for entry in book.enabled_cells()] == ["inv"]
    assert len(book) == 2


def test_entry_lookup_and_missing_key() -> None:
    book = CellBook(cells=[make_entry()])
    assert book.entry("EXAMPLE_LIB__inv__layout__schematic").cell == "inv"
    with pytest.raises(KeyError):
        book.entry("nope")


def test_keys_preserve_table_order() -> None:
    book = CellBook(cells=[make_entry(cell="b"), make_entry(cell="a")])
    assert [key.split("__")[1] for key in book.keys] == ["b", "a"]


def test_iteration_yields_entries() -> None:
    book = CellBook(cells=[make_entry(), make_entry(cell="buf")])
    assert [entry.cell for entry in book] == ["inv", "buf"]


def test_with_added_returns_a_new_book_and_leaves_the_original_alone() -> None:
    book = CellBook(cells=[make_entry()])
    bigger = book.with_added([make_entry(cell="buf")])
    assert len(book) == 1
    assert len(bigger) == 2
    with pytest.raises(ValidationError):
        book.with_added([make_entry()])
    assert len(book) == 1


# ---- expand_cells ------------------------------------------------------------


def test_expansion_order_matches_the_legacy_loader(tmp_path: Path) -> None:
    """library -> cell -> layout_view -> source_view, same as ``_expand_spec``."""

    tasks_yaml = tmp_path / "tasks.yaml"
    tasks_yaml.write_text(
        "- library: [LIB_A, LIB_B]\n"
        "  cell: [inv, buf]\n"
        "  lvs_layout_view: [layout, layout_test]\n"
        "  lvs_source_view: [schematic, cdl]\n",
        encoding="utf-8",
    )
    legacy = [task.task_id for task in load_tasks(tasks_yaml)]

    rows = expand_cells(
        library=["LIB_A", "LIB_B"],
        cell=["inv", "buf"],
        layout_view=["layout", "layout_test"],
        source_view=["schematic", "cdl"],
    )
    assert [row.key for row in rows] == legacy
    assert len(rows) == 16


def test_scalar_axes_expand_to_one_row() -> None:
    rows = expand_cells(library="LIB", cell="inv", layout_view="layout")
    assert len(rows) == 1
    assert rows[0].key == "LIB__inv__layout__schematic"


def test_settings_land_on_every_generated_row() -> None:
    rows = expand_cells(
        library="LIB",
        cell=["inv", "buf"],
        layout_view="layout",
        ground_net="gnd",
        out_file="av_ext",
        display_name="the pair",
        enabled=False,
        note="parked",
    )
    assert all(row.display_name == "the pair" for row in rows)
    assert all(row.ground_net == "gnd" for row in rows)
    assert all(row.enabled is False for row in rows)
    assert all(row.note == "parked" for row in rows)


def test_empty_axis_is_rejected() -> None:
    with pytest.raises(ValueError, match="is empty"):
        expand_cells(library=[], cell="inv", layout_view="layout")


def test_repeated_axis_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="repeats"):
        expand_cells(library="LIB", cell=["inv", "inv"], layout_view="layout")


def test_expansion_output_is_a_valid_book() -> None:
    rows = expand_cells(library=["A", "B"], cell=["x", "y"], layout_view=["l1", "l2"])
    assert len(CellBook(cells=rows)) == 8


# ---- YAML --------------------------------------------------------------------


def test_round_trip_through_disk(tmp_path: Path) -> None:
    book = CellBook(
        cells=expand_cells(
            library="LIB", cell=["inv", "buf"], layout_view="layout", out_file="av_ext"
        )
    )
    path = tmp_path / "cells.yaml"
    save_cells(book, path)
    assert load_cells(path).model_dump() == book.model_dump()


def test_bare_list_form_loads(tmp_path: Path) -> None:
    path = tmp_path / "cells.yaml"
    path.write_text(
        "- library: LIB\n  cell: inv\n  layout_view: layout\n",
        encoding="utf-8",
    )
    book = load_cells(path)
    assert book.schema_version == CELLS_SCHEMA_VERSION
    assert book.keys == ["LIB__inv__layout__schematic"]


def test_mapping_form_keeps_comments_when_raw_is_passed(tmp_path: Path) -> None:
    path = tmp_path / "cells.yaml"
    path.write_text(
        "schema_version: 1\n# the DCO block\ncells:\n- library: LIB\n  cell: inv\n"
        "  layout_view: layout\n",
        encoding="utf-8",
    )
    book, raw = load_cells_with_raw(path)
    assert "# the DCO block" in dump_cells_yaml(book, raw=raw)
    assert "# the DCO block" not in dump_cells_yaml(book)


def test_duplicate_rows_on_disk_fail_the_load(tmp_path: Path) -> None:
    path = tmp_path / "cells.yaml"
    path.write_text(
        "cells:\n"
        "- {library: LIB, cell: inv, layout_view: layout}\n"
        "- {library: LIB, cell: inv, layout_view: layout}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="duplicate cell rows"):
        load_cells(path)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_cells(tmp_path / "nope.yaml")


def test_empty_file_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "cells.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        load_cells(path)


def test_scalar_top_level_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "cells.yaml"
    path.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="expected a mapping or a list"):
        load_cells(path)


def test_future_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cells.yaml"
    path.write_text("schema_version: 99\ncells: []\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="newer than this build"):
        load_cells(path)


def test_unknown_key_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cells.yaml"
    path.write_text("cells: []\nwibble: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_cells(path)


def test_dump_omits_none_valued_fields() -> None:
    text = dump_cells_yaml(CellBook(cells=[make_entry()]))
    assert "out_file" not in text
    assert "display_name" not in text
    assert "enabled: true" in text
