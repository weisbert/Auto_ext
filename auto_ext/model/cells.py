"""The cell table: which DUTs this workspace knows about.

``config/cells.yaml`` after the migration, one explicit row per DUT. Written
against ``docs/refactor/01-schema.md`` section 1.4.

What changed against ``tasks.yaml``
-----------------------------------
The legacy :class:`auto_ext.core.config.TaskSpec` was three things at once: a
table of DUTs, a Cartesian *generator* over four axes, and a carrier for
per-task render settings. Only the first survives here.

* **Cartesian expansion happens once, at add time.** :func:`expand_cells`
  turns a batch entry into explicit rows and the rows are what gets stored;
  loading a :class:`CellBook` expands nothing. A table you can read is worth
  more than a table you have to simulate in your head, and the run history
  now points at rows, not at a generator whose output moves when a list in
  the YAML grows an element.
* **``exclude`` is gone.** Its two uses split cleanly: "never run this
  combination" means do not create the row, and "do not run it for now" is
  :attr:`CellEntry.enabled`. A selector language that could silently match
  nothing was the third possibility, and nobody wanted it.
* **Duplicate rows are rejected, not warned about.** ``task_id`` used to be
  the run identity, so ``_warn_on_duplicate_task_ids`` could only warn --
  refusing would have broken workflows that relied on the collision. Identity
  is now the run directory's timestamp, so a duplicate row buys nothing and
  costs a table you cannot edit confidently. It is an editing accident, and
  the load fails.
* **Render settings left.** ``jivaro`` / ``knobs`` / ``templates`` /
  ``continue_on_lvs_fail`` / ``dspf_out_path`` are Recipe and Workspace
  business. What is left is what genuinely varies per DUT.

Import direction: this module imports :mod:`auto_ext.model.common` and
:mod:`auto_ext.core.errors` only, so ``migrate`` and the model layer can both
depend on it without a cycle.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator, Sequence
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.errors import ConfigError
from auto_ext.model.common import Base

__all__ = [
    "CELLS_FILENAME",
    "CELLS_SCHEMA_VERSION",
    "EXPANSION_AXES",
    "CellBook",
    "CellEntry",
    "dump_cells_yaml",
    "expand_cells",
    "load_cells",
    "load_cells_with_raw",
    "save_cells",
]

#: Bumped only when a load of the previous layout would be wrong rather than
#: merely incomplete. Additive fields do not bump it.
CELLS_SCHEMA_VERSION = 1

#: Canonical file name under ``config/``.
CELLS_FILENAME = "cells.yaml"

#: The expansion axes, in the order :func:`expand_cells` nests them. Same
#: order as the legacy ``config._expand_spec`` so a migrated table lists its
#: rows in exactly the order the old loader produced tasks.
EXPANSION_AXES: tuple[str, ...] = ("library", "cell", "layout_view", "source_view")


class CellEntry(Base):
    """One DUT: the four identity axes plus the three per-DUT settings.

    :attr:`key` is the table's unique key and the display form of what used
    to be ``task_id``. It never reaches a path -- run directories are named
    from a timestamp plus a slug -- so it is free to contain whatever the
    library and cell names contain.
    """

    #: Cadence library holding the cell. Scalar: the list form that
    #: ``TaskSpec.library`` accepted is an :func:`expand_cells` input now.
    library: str = Field(min_length=1)
    #: Cell name. Scalar, as above.
    cell: str = Field(min_length=1)
    #: Layout view name. Renamed from ``TaskSpec.lvs_layout_view``: the
    #: ``lvs_`` prefix said which stage consumed it, but strmout and Quantus
    #: read it too. The Jinja variable keeps the old spelling
    #: (``[[lvs_layout_view]]``); the catalog row ``layout_view`` records the
    #: mapping.
    layout_view: str = Field(min_length=1)
    #: Schematic-side view name (``TaskSpec.lvs_source_view``).
    source_view: str = Field(default="schematic", min_length=1)
    #: Which net Quantus grounds capacitance to. Per DUT, not per Recipe: it
    #: is a property of the block's supply scheme.
    ground_net: str = Field(default="vss", min_length=1)
    #: Extracted-view name (Quantus ``-view_name``, Jivaro ``inputView``).
    #: ``None`` means the flow has no extracted view for this DUT, which the
    #: reduction stage refuses to run against.
    out_file: str | None = None
    #: Human-readable name for the UI. The only piece of UX sugar that
    #: survived from ``TaskSpec.label``. ``None`` renders :attr:`key`.
    display_name: str | None = None
    #: ``False`` keeps the row but takes it out of every batch -- the
    #: "temporarily not this one" half of the old ``exclude``.
    enabled: bool = True
    #: Free-form. Why this row exists, or why it is disabled.
    note: str | None = None

    @field_validator("out_file", "display_name", "note", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """``""`` round-trips to ``None`` so the YAML stays free of empties.

        ``TaskSpec.label`` had the same rule; a GUI that clears a text box
        writes an empty string, and an empty string is not a display name.
        """

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def key(self) -> str:
        """Unique key within the table, and the display form of ``task_id``.

        Deliberately identical to the legacy ``task_id`` spelling so a
        migrated run history and a migrated cell table can still be matched
        up by eye. It is not part of any path.
        """

        return f"{self.library}__{self.cell}__{self.layout_view}__{self.source_view}"

    @property
    def label(self) -> str:
        """What a UI should print: :attr:`display_name` when set, else :attr:`key`."""

        return self.display_name or self.key


class CellBook(Base):
    """``config/cells.yaml`` -- the whole table."""

    schema_version: int = CELLS_SCHEMA_VERSION
    cells: list[CellEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_duplicate_rows(self) -> "CellBook":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for entry in self.cells:
            if entry.key in seen:
                duplicates.add(entry.key)
            seen.add(entry.key)
        if duplicates:
            raise ValueError(
                "duplicate cell rows: "
                + ", ".join(sorted(duplicates))
                + " -- each library/cell/layout_view/source_view combination may "
                "appear once; use enabled: false to park a row instead of "
                "listing it twice"
            )
        return self

    def __len__(self) -> int:
        return len(self.cells)

    def __iter__(self) -> Iterator[CellEntry]:  # type: ignore[override]
        return iter(self.cells)

    @property
    def keys(self) -> list[str]:
        """Row keys in table order."""

        return [entry.key for entry in self.cells]

    def entry(self, key: str) -> CellEntry:
        """The row with this :attr:`CellEntry.key`, or :class:`KeyError`."""

        for entry in self.cells:
            if entry.key == key:
                return entry
        raise KeyError(key)

    def enabled_cells(self) -> list[CellEntry]:
        """Rows a batch would actually run."""

        return [entry for entry in self.cells if entry.enabled]

    def with_added(self, entries: Iterable[CellEntry]) -> "CellBook":
        """A new book with ``entries`` appended.

        Returns a new object rather than mutating: duplicate detection is a
        model validator, so building the candidate is what runs the check,
        and a rejected batch add must leave the original table untouched.
        """

        return CellBook(schema_version=self.schema_version, cells=[*self.cells, *entries])


def expand_cells(
    *,
    library: str | Sequence[str],
    cell: str | Sequence[str],
    layout_view: str | Sequence[str],
    source_view: str | Sequence[str] = "schematic",
    ground_net: str = "vss",
    out_file: str | None = None,
    display_name: str | None = None,
    enabled: bool = True,
    note: str | None = None,
) -> list[CellEntry]:
    """Cartesian-expand a batch entry into explicit rows.

    This is an input convenience for "add these six cells with the same
    settings", not a storage format: the caller stores the returned rows. The
    nesting order is :data:`EXPANSION_AXES`, matching the legacy loader, so a
    migration that replays a ``tasks.yaml`` spec produces rows in the order
    that ``tasks.yaml`` produced tasks.

    ``display_name`` lands on *every* generated row unchanged -- it was never
    an expansion axis, and two rows sharing a display name is legal (the key
    is what has to be unique).

    Raises :class:`ValueError` for an empty axis or a repeated value inside
    one axis; both produce a table the user did not mean to ask for.
    """

    axes: dict[str, list[str]] = {}
    for name, value in (
        ("library", library),
        ("cell", cell),
        ("layout_view", layout_view),
        ("source_view", source_view),
    ):
        values = [value] if isinstance(value, str) else list(value)
        if not values:
            raise ValueError(f"axis {name!r} is empty; nothing to expand")
        duplicated = sorted({v for v in values if values.count(v) > 1})
        if duplicated:
            raise ValueError(
                f"axis {name!r} repeats {', '.join(duplicated)}; "
                "every combination would be generated twice"
            )
        axes[name] = values

    return [
        CellEntry(
            library=lib,
            cell=cel,
            layout_view=layout,
            source_view=source,
            ground_net=ground_net,
            out_file=out_file,
            display_name=display_name,
            enabled=enabled,
            note=note,
        )
        for lib, cel, layout, source in itertools.product(
            axes["library"], axes["cell"], axes["layout_view"], axes["source_view"]
        )
    ]


# ---- YAML round trip --------------------------------------------------------
# Same protocol as core/config.py and model/recipe.py: ruamel in round-trip
# mode, so a table the user has commented stays commented after a GUI write.


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.preserve_quotes = True
    return yaml


def _plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {key: _plain(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_plain(value) for value in obj]
    return obj


def load_cells(path: Path) -> CellBook:
    """Load and validate ``config/cells.yaml``."""

    book, _raw = load_cells_with_raw(path)
    return book


def load_cells_with_raw(path: Path) -> tuple[CellBook, Any]:
    """Load the table and also return ruamel's comment-carrying tree.

    Accepts both the mapping form (``schema_version`` + ``cells:``) and a
    bare list of rows, because ``tasks.yaml`` allowed a bare list and a user
    hand-writing the new file will do the same. A bare list is normalised to
    the mapping form on the way back out.
    """

    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"cells file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = _yaml().load(handle)
    except YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error: {exc}") from exc

    if data is None:
        raise ConfigError(f"{path}: file is empty")
    if isinstance(data, list):
        payload: Any = {"cells": _plain(data)}
    elif isinstance(data, dict):
        payload = _plain(data)
    else:
        raise ConfigError(
            f"{path}: expected a mapping or a list at top level, got {type(data).__name__}"
        )

    version = payload.get("schema_version", CELLS_SCHEMA_VERSION)
    if not isinstance(version, int):
        raise ConfigError(f"{path}: schema_version must be an integer, got {version!r}")
    if version > CELLS_SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: cells schema v{version} is newer than this build "
            f"(v{CELLS_SCHEMA_VERSION}); upgrade Auto_ext to read it"
        )

    try:
        book = CellBook.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return book, data


def dump_cells_yaml(book: CellBook, *, raw: Any = None) -> str:
    """Serialize the table to YAML text.

    ``raw`` (from :func:`load_cells_with_raw`) is used only when it is the
    mapping form; a file that was a bare list is rewritten as a mapping,
    which loses its comments but gains the schema version. Pass ``raw=None``
    to emit a plain document.
    """

    payload = book.model_dump(mode="json", exclude_none=True)
    tree: Any = payload
    if isinstance(raw, dict):
        tree = _merge_into(raw, payload)
    buffer = StringIO()
    _yaml().dump(tree, buffer)
    return buffer.getvalue()


def _merge_into(target: Any, source: Any) -> Any:
    """Write ``source`` into ruamel's ``target`` in place, keeping comments.

    Lists are replaced wholesale: a row list has no stable identity to match
    comments against once rows are added or removed, and half-attached
    comments are worse than none.
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


def save_cells(book: CellBook, path: Path, *, raw: Any = None) -> None:
    """Write the table to ``path``, creating the directory if needed."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_cells_yaml(book, raw=raw), encoding="utf-8", newline="\n")
