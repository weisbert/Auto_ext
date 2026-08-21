"""Where this project's EDA work lands, and which PDK profile it uses.

``config/workspace.yaml`` -- what is left of ``project.yaml`` once the PDK
fields move to :class:`~auto_ext.model.pdk.PdkProfile`, the knobs move to
:class:`~auto_ext.model.recipe.Recipe`, the template slots are replaced by the
catalog, and the three display-only root paths are dropped. Fourteen keys
become five. Written against ``docs/refactor/01-schema.md`` section 1.4.

Two things deliberately did **not** move here:

* ``work_root`` / ``verify_root`` / ``setup_root`` are dropped. No code ever
  consumed them -- they existed so a GUI panel could print something, and
  that panel should read the shell, which is where the values actually live.
* ``employee_id`` is site-level, not project-level. Keeping it here would
  make it travel with the project (and with the project's git history); it
  belongs in ``~/.auto_ext/site.yaml`` and reaches templates as
  ``site.employee_id``, with the existing ``$USER`` / ``$USERNAME`` /
  ``"unknown"`` fallback chain unchanged.

The two path patterns keep the grammar they had in ``project.yaml``: env
references (``$X`` / ``${X}`` / ``$env(X)``) resolved by
:func:`auto_ext.core.env.substitute_env`, then ``str.format`` for the
axis-derived keys listed in :data:`FORMAT_KEYS`. What changed is that
``{task_id}`` is gone -- identity is the run directory now, and a path key
that silently pinned two different runs to one directory is exactly what the
Run object exists to stop.
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.errors import ConfigError
from auto_ext.model.common import Base, Slug

__all__ = [
    "FORMAT_KEYS",
    "RETIRED_FORMAT_KEYS",
    "WORKSPACE_FILENAME",
    "WORKSPACE_SCHEMA_VERSION",
    "WorkspaceConfig",
    "dump_workspace_yaml",
    "load_workspace",
    "load_workspace_with_raw",
    "save_workspace",
]

WORKSPACE_SCHEMA_VERSION = 1

#: Canonical file name under ``config/``.
WORKSPACE_FILENAME = "workspace.yaml"

#: ``str.format`` keys the two path patterns may use.
#:
#: ``{layout_view}`` / ``{source_view}`` drop the ``lvs_`` prefix that
#: ``ProjectConfig.extraction_output_dir`` used, matching
#: :class:`~auto_ext.model.cells.CellEntry`. ``{recipe}`` / ``{run_id}`` /
#: ``{run_slug}`` are new: they are how a user asks for one Cadence workspace
#: per recipe or per run instead of one per cell.
FORMAT_KEYS: frozenset[str] = frozenset(
    {
        "cell",
        "library",
        "layout_view",
        "source_view",
        "recipe",
        "run_id",
        "run_slug",
    }
)

#: Format keys that existed in ``project.yaml`` and are refused here, mapped
#: to what to write instead. ``{task_id}`` was the old identity; a migration
#: has to ask the user which replacement they meant, because the two answers
#: mean different things (one workspace per run vs. one per cell).
RETIRED_FORMAT_KEYS: dict[str, str] = {
    "task_id": "{run_slug} for one workspace per run, or {cell} to keep reusing one per cell",
    "lvs_layout_view": "{layout_view}",
    "lvs_source_view": "{source_view}",
}

#: ``{name}`` not preceded by ``$``. The negative lookbehind is what keeps
#: ``${WORK_ROOT}`` (an env reference, handled by ``substitute_env``) from
#: being read as a format key called ``WORK_ROOT``.
_FORMAT_KEY_RE = re.compile(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _check_pattern(value: str, field: str) -> str:
    """Reject unknown / retired format keys at load time.

    ``str.format`` fails at render time with a bare ``KeyError``, which in
    the middle of a batch is a stack trace instead of a config error. Every
    key is knowable now, so check now.
    """

    if not value.strip():
        raise ValueError(f"{field} must not be empty")
    for key in _FORMAT_KEY_RE.findall(value):
        if key in RETIRED_FORMAT_KEYS:
            raise ValueError(
                f"{field}: {{{key}}} is no longer a format key; "
                f"use {RETIRED_FORMAT_KEYS[key]}"
            )
        if key not in FORMAT_KEYS:
            raise ValueError(
                f"{field}: unknown format key {{{key}}}; "
                f"available keys are {', '.join('{' + k + '}' for k in sorted(FORMAT_KEYS))}"
            )
    return value


class WorkspaceConfig(Base):
    """``config/workspace.yaml``."""

    schema_version: int = WORKSPACE_SCHEMA_VERSION

    #: Which :class:`~auto_ext.model.pdk.PdkProfile` this project runs
    #: against; the file is ``config/profiles/<pdk_profile>.yaml``. Replaces
    #: the PDK fields that used to be scattered through ``project.yaml``.
    pdk_profile: Slug

    #: Where the Cadence work lands (``ProjectConfig.extraction_output_dir``).
    #: Demoted from identity to location: two runs sharing this directory is
    #: legal and normal, and the run directory keeps them apart.
    output_dir_pattern: str = "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"

    #: ``ProjectConfig.intermediate_dir``, unchanged.
    intermediate_dir: str = "${WORK_ROOT2}"

    #: ``ProjectConfig.dspf_out_path``. The per-task override that
    #: ``TaskSpec.dspf_out_path`` provided is gone: ``{cell}`` covered every
    #: real use of it, and a per-DUT output path is a Cells column if it ever
    #: turns out to be needed.
    dspf_out_pattern: str = "${WORK_ROOT2}/{cell}.dspf"

    #: How many run directories to keep; ``0`` means keep everything. Read by
    #: the prune entry point only -- nothing else in the schema depends on it.
    keep_runs: int = Field(default=0, ge=0)

    @field_validator("output_dir_pattern")
    @classmethod
    def _check_output_dir(cls, value: str) -> str:
        return _check_pattern(value, "output_dir_pattern")

    @field_validator("dspf_out_pattern")
    @classmethod
    def _check_dspf(cls, value: str) -> str:
        return _check_pattern(value, "dspf_out_pattern")

    @field_validator("intermediate_dir")
    @classmethod
    def _check_intermediate(cls, value: str) -> str:
        return _check_pattern(value, "intermediate_dir")

    def format_keys_used(self) -> set[str]:
        """Every format key the three patterns actually reference.

        The migration uses this to tell the user which keys their patterns
        depend on, and a future renderer uses it to decide whether a value is
        needed before it is computed.
        """

        used: set[str] = set()
        for value in (self.output_dir_pattern, self.intermediate_dir, self.dspf_out_pattern):
            used.update(_FORMAT_KEY_RE.findall(value))
        return used


# ---- YAML round trip --------------------------------------------------------


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


def load_workspace(path: Path) -> WorkspaceConfig:
    """Load and validate ``config/workspace.yaml``."""

    workspace, _raw = load_workspace_with_raw(path)
    return workspace


def load_workspace_with_raw(path: Path) -> tuple[WorkspaceConfig, Any]:
    """Load the workspace config and ruamel's comment-carrying tree."""

    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"workspace file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = _yaml().load(handle)
    except YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error: {exc}") from exc

    if data is None:
        raise ConfigError(f"{path}: file is empty")
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: expected a mapping at top level, got {type(data).__name__}"
        )

    payload = _plain(data)
    version = payload.get("schema_version", WORKSPACE_SCHEMA_VERSION)
    if not isinstance(version, int):
        raise ConfigError(f"{path}: schema_version must be an integer, got {version!r}")
    if version > WORKSPACE_SCHEMA_VERSION:
        raise ConfigError(
            f"{path}: workspace schema v{version} is newer than this build "
            f"(v{WORKSPACE_SCHEMA_VERSION}); upgrade Auto_ext to read it"
        )

    try:
        workspace = WorkspaceConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return workspace, data


def _merge_into(target: Any, source: Any) -> Any:
    """Write ``source`` into ruamel's ``target`` in place, keeping comments."""

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


def dump_workspace_yaml(workspace: WorkspaceConfig, *, raw: Any = None) -> str:
    """Serialize the workspace config to YAML text."""

    payload = workspace.model_dump(mode="json")
    tree = _merge_into(raw, payload) if isinstance(raw, dict) else payload
    buffer = StringIO()
    _yaml().dump(tree, buffer)
    return buffer.getvalue()


def save_workspace(workspace: WorkspaceConfig, path: Path, *, raw: Any = None) -> None:
    """Write the workspace config to ``path``, creating the directory if needed."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_workspace_yaml(workspace, raw=raw), encoding="utf-8", newline="\n")
