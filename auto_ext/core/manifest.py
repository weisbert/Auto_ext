"""Template manifest schema + loader + knob precedence resolver.

Each parameterised template ``<name>.j2`` may be paired with a sidecar
``<name>.j2.manifest.yaml`` that declares its "knobs" — tunable literals
(numeric limits, thresholds, toggles) referenced by ``[[knob_name]]`` in
the template. Knob values are merged at render time with precedence

    manifest.default < project.yaml.knobs.<stage> < tasks.yaml[...].knobs.<stage> < --knob CLI

so users can tune a single run, a project, or the template default
without hand-editing ``.j2`` files.

Identity-level variables (``cell``, ``library``, etc.) are NOT knobs;
their names are reserved and a manifest that tries to shadow them is
rejected at load time so the collision surfaces at template authoring
time rather than during a run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from auto_ext.core.errors import ConfigError

#: Render-context keys produced by :func:`auto_ext.core.runner._build_context`.
#: A knob sharing any of these names would silently shadow the identity
#: value after merge; reject at manifest load instead. Keep in sync with
#: ``runner._build_context``.
_IDENTITY_KEYS: frozenset[str] = frozenset(
    {
        "library",
        "cell",
        "lvs_layout_view",
        "lvs_source_view",
        "ground_net",
        "out_file",
        "task_id",
        "output_dir",
        "intermediate_dir",
        "dspf_out_path",
        "layer_map",
        "employee_id",
        "jivaro_frequency_limit",
        "jivaro_error_max",
        "tech_name",
        "pdk_subdir",
        "project_subdir",
        "lvs_runset_version",
        "qrc_runset_version",
    }
)


# ---- schema ----------------------------------------------------------------


class SourceRef(BaseModel):
    """Pointer back to the raw EDA-file key a knob was promoted from.

    Set by the importer (Phase 4b1) when a literal is promoted via
    ``knob promote``; read on re-import to locate the same literal in a
    refreshed raw export so user-added knobs survive a second import.
    ``None`` on any knob authored by hand — those are user-defined and
    not importer-managed.
    """

    model_config = ConfigDict(extra="forbid")

    tool: Literal["calibre", "si", "quantus", "jivaro"]
    key: str


class KnobSpec(BaseModel):
    """One knob declaration inside a :class:`TemplateManifest`.

    ``range`` is inclusive on both ends and applies only to numeric types.
    ``choices`` constrains a ``str`` knob to a closed set (rendered as a
    QComboBox in the GUI); mutually exclusive with ``range``.
    ``unit`` is display-only (kept for future GUI use).
    ``source`` links a knob back to the importer's raw-file key so smart
    re-import can re-substitute it; left ``None`` for hand-authored knobs.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["int", "float", "str", "bool"]
    default: Any
    description: str | None = None
    range: tuple[Any, Any] | None = None
    choices: list[Any] | None = None
    unit: str | None = None
    source: SourceRef | None = None

    @model_validator(mode="after")
    def _validate(self) -> "KnobSpec":
        self.default = _coerce_typed(self.default, self.type, "default")
        if self.range is not None:
            if self.type not in ("int", "float"):
                raise ValueError(
                    f"range is only valid for int or float knobs (got type={self.type!r})"
                )
            low = _coerce_typed(self.range[0], self.type, "range[0]")
            high = _coerce_typed(self.range[1], self.type, "range[1]")
            if low > high:
                raise ValueError(f"range low {low} > high {high}")
            self.range = (low, high)
            if not (low <= self.default <= high):
                raise ValueError(
                    f"default {self.default} is outside range [{low}, {high}]"
                )
        if self.choices is not None:
            if self.type != "str":
                raise ValueError(
                    f"choices is only valid for str knobs (got type={self.type!r})"
                )
            if self.range is not None:
                raise ValueError("choices and range are mutually exclusive")
            if len(self.choices) == 0:
                raise ValueError("choices must contain at least one value")
            coerced = [_coerce_typed(c, self.type, f"choices[{i}]")
                       for i, c in enumerate(self.choices)]
            if len(set(coerced)) != len(coerced):
                raise ValueError(f"choices contains duplicates: {coerced!r}")
            self.choices = coerced
            if self.default not in coerced:
                raise ValueError(
                    f"default {self.default!r} is not in choices {coerced!r}"
                )
        return self


class TemplateManifest(BaseModel):
    """Sidecar metadata for one template. ``template`` is the .j2 filename."""

    model_config = ConfigDict(extra="forbid")

    template: str
    description: str | None = None
    knobs: dict[str, KnobSpec] = Field(default_factory=dict)


# ---- loader ----------------------------------------------------------------


def manifest_path_for(template_path: Path) -> Path:
    """Return the sidecar path for a given template: ``<file>.manifest.yaml``."""
    return template_path.with_name(template_path.name + ".manifest.yaml")


def dump_manifest_yaml(manifest: TemplateManifest) -> str:
    """Serialize a :class:`TemplateManifest` as a YAML string.

    Used by ``init-project`` to write the empty-knob sidecar that pairs
    each freshly-imported template, and by ``import`` / ``knob promote``
    when re-emitting an updated manifest. Round-trip mode preserves the
    user's mapping order on subsequent edits.
    """
    from io import StringIO

    data: dict[str, Any] = {"template": manifest.template}
    if manifest.description is not None:
        data["description"] = manifest.description
    data["knobs"] = {
        name: spec.model_dump(exclude_none=True)
        for name, spec in manifest.knobs.items()
    }

    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


def append_knob_to_manifest_yaml(
    template_path: Path,
    knob_name: str,
    knob_spec: KnobSpec,
    *,
    description: str | None = None,
) -> Path:
    """Append a knob to the template's manifest sidecar (ruamel round-trip).

    Idempotent: if the knob already exists with an identical spec, no
    write happens and the path is returned. If it exists with a
    different spec, raises :class:`ConfigError` rather than silently
    clobbering hand-authored knob metadata.

    When the sidecar does not exist this creates one with the minimal
    ``{template: <basename>, knobs: {<name>: <spec>}}`` form. When it
    exists, the new knob is appended to the existing ``knobs`` mapping,
    preserving comments and key order via ruamel round-trip mode.

    Returns the path of the manifest file (created or updated).
    """
    sidecar = manifest_path_for(template_path)
    spec_dict = knob_spec.model_dump(exclude_none=True)
    if description is not None and "description" not in spec_dict:
        spec_dict["description"] = description

    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True

    if not sidecar.exists():
        data: dict[str, Any] = {
            "template": template_path.name,
            "knobs": {knob_name: spec_dict},
        }
        with sidecar.open("w", encoding="utf-8") as fh:
            yaml.dump(data, fh)
        return sidecar

    try:
        with sidecar.open("r", encoding="utf-8") as fh:
            data = yaml.load(fh)
    except YAMLError as exc:
        raise ConfigError(f"{sidecar}: YAML parse error: {exc}") from exc
    if data is None or not isinstance(data, dict):
        raise ConfigError(
            f"{sidecar}: cannot append knob to a malformed manifest"
        )

    knobs = data.get("knobs")
    if knobs is None:
        data["knobs"] = {knob_name: spec_dict}
    elif knob_name in knobs:
        existing = _plain(knobs[knob_name])
        if existing == spec_dict:
            return sidecar
        raise ConfigError(
            f"{sidecar}: knob {knob_name!r} already declared with a "
            f"different spec; refusing to overwrite (existing={existing!r}, "
            f"new={spec_dict!r})"
        )
    else:
        knobs[knob_name] = spec_dict

    with sidecar.open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
    return sidecar


def load_manifest(template_path: Path) -> TemplateManifest | None:
    """Load the sidecar manifest for ``template_path``.

    Returns ``None`` if no sidecar is present — the template simply has no
    knobs. Raises :class:`ConfigError` if the sidecar exists but is
    malformed, names a different template, collides with an identity
    variable, or declares a default that fails its own type / range check.
    """
    path = manifest_path_for(template_path)
    if not path.is_file():
        return None

    yaml = YAML(typ="rt")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.load(fh)
    except YAMLError as exc:
        raise ConfigError(f"{path}: YAML parse error: {exc}") from exc

    if data is None:
        raise ConfigError(f"{path}: manifest is empty")
    if not isinstance(data, dict):
        raise ConfigError(
            f"{path}: expected a mapping at top level, got {type(data).__name__}"
        )

    try:
        manifest = TemplateManifest.model_validate(_plain(data))
    except ValidationError as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    if manifest.template != template_path.name:
        raise ConfigError(
            f"{path}: 'template' field {manifest.template!r} does not match "
            f"sidecar filename {template_path.name!r}"
        )

    for knob_name in manifest.knobs:
        if knob_name in _IDENTITY_KEYS:
            raise ConfigError(
                f"{path}: knob {knob_name!r} collides with reserved identity variable"
            )
        if "." in knob_name:
            raise ConfigError(
                f"{path}: knob name {knob_name!r} must not contain '.' "
                "(ambiguates CLI parsing)"
            )

    return manifest


# ---- precedence resolver ---------------------------------------------------


def resolve_knob_values(
    manifest: TemplateManifest | None,
    project_knobs: dict[str, Any],
    task_knobs: dict[str, Any],
    cli_knobs: dict[str, Any],
) -> dict[str, Any]:
    """Merge knob values in precedence order and return a flat dict.

    Precedence, low to high: manifest defaults, project, task, CLI. CLI
    values are assumed to be strings (``typer`` parses them that way) and
    are coerced per :attr:`KnobSpec.type`. The project and task layers
    are loaded from YAML and expected to carry the native Python type
    already.

    Raises :class:`ConfigError` for unknown knob names, type-coercion
    failures, range violations, or any override when the template has no
    manifest.
    """
    if manifest is None:
        for layer_name, layer in (
            ("project", project_knobs),
            ("task", task_knobs),
            ("cli", cli_knobs),
        ):
            if layer:
                raise ConfigError(
                    f"{layer_name} knob override(s) {sorted(layer)} given for a "
                    "template that has no manifest; declare knobs in a "
                    "<template>.manifest.yaml sidecar first"
                )
        return {}

    result: dict[str, Any] = {
        name: spec.default for name, spec in manifest.knobs.items()
    }

    for layer_name, layer, from_string in (
        ("project", project_knobs, False),
        ("task", task_knobs, False),
        ("cli", cli_knobs, True),
    ):
        for knob_name, raw in layer.items():
            if knob_name not in manifest.knobs:
                raise ConfigError(
                    f"{layer_name} knob {knob_name!r} is not declared in the "
                    f"manifest for {manifest.template}; known knobs: "
                    f"{sorted(manifest.knobs)}"
                )
            spec = manifest.knobs[knob_name]
            label = f"{layer_name} knob {knob_name}"
            try:
                if from_string:
                    value = _coerce_from_string(str(raw), spec.type, label)
                else:
                    value = _coerce_typed(raw, spec.type, label)
            except ValueError as exc:
                raise ConfigError(str(exc)) from exc
            if spec.range is not None:
                low, high = spec.range
                if not (low <= value <= high):
                    raise ConfigError(
                        f"{label}={value} is outside allowed range [{low}, {high}]"
                    )
            if spec.choices is not None and value not in spec.choices:
                raise ConfigError(
                    f"{label}={value!r} is not in allowed choices {spec.choices!r}"
                )
            result[knob_name] = value

    return result


def current_knob_value(
    manifest: TemplateManifest,
    project_knobs: dict[str, dict[str, Any]],
    stage: str,
    knob: str,
) -> tuple[Any, str]:
    """Return ``(value, provenance)`` for one knob in the project layer only.

    Provenance is ``"project"`` when ``project_knobs[stage][knob]`` is set,
    ``"default"`` otherwise (manifest default is used). Coercion + range
    checks mirror :func:`resolve_knob_values`'s project layer so the GUI
    never displays a value the runner would later reject. Task and CLI
    layers are intentionally not consulted — they exist only at run time.

    Raises :class:`ConfigError` if ``knob`` is not declared in ``manifest``,
    surfacing stale ``project.yaml`` entries that no longer match a knob.
    """
    if knob not in manifest.knobs:
        raise ConfigError(
            f"knob {knob!r} is not declared in the manifest for "
            f"{manifest.template}; known knobs: {sorted(manifest.knobs)}"
        )
    spec = manifest.knobs[knob]
    stage_layer = project_knobs.get(stage, {})
    if knob not in stage_layer:
        return (spec.default, "default")
    label = f"project knob {knob}"
    try:
        value = _coerce_typed(stage_layer[knob], spec.type, label)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if spec.range is not None:
        low, high = spec.range
        if not (low <= value <= high):
            raise ConfigError(
                f"{label}={value} is outside allowed range [{low}, {high}]"
            )
    if spec.choices is not None and value not in spec.choices:
        raise ConfigError(
            f"{label}={value!r} is not in allowed choices {spec.choices!r}"
        )
    return (value, "project")


# ---- internals -------------------------------------------------------------


def _plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


def _coerce_typed(value: Any, type_name: str, label: str) -> Any:
    """Accept a value already at its native Python type.

    ``int`` is ``int`` (bool is rejected though it subclasses int).
    ``float`` accepts int via numeric promotion. Ruamel scalar subclasses
    (``ScalarInt``, ``ScalarFloat``, ``PlainScalarString``) are normalised
    to plain Python types so downstream comparisons do not surprise on
    subclass checks.
    """
    if type_name == "int":
        if isinstance(value, bool):
            raise ValueError(f"{label}: expected int, got bool ({value!r})")
        if isinstance(value, int):
            return int(value)
        raise ValueError(f"{label}: expected int, got {type(value).__name__} ({value!r})")
    if type_name == "float":
        if isinstance(value, bool):
            raise ValueError(f"{label}: expected float, got bool ({value!r})")
        if isinstance(value, (int, float)):
            return float(value)
        raise ValueError(f"{label}: expected float, got {type(value).__name__} ({value!r})")
    if type_name == "str":
        if isinstance(value, str):
            return str(value)
        raise ValueError(f"{label}: expected str, got {type(value).__name__} ({value!r})")
    if type_name == "bool":
        if isinstance(value, bool):
            return bool(value)
        raise ValueError(f"{label}: expected bool, got {type(value).__name__} ({value!r})")
    raise ValueError(f"{label}: unknown type {type_name!r}")


def _coerce_from_string(s: str, type_name: str, label: str) -> Any:
    """Parse a CLI-style string into the target type. No silent truncation."""
    if type_name == "int":
        try:
            return int(s)
        except ValueError:
            raise ValueError(f"{label}: cannot parse {s!r} as int") from None
    if type_name == "float":
        try:
            return float(s)
        except ValueError:
            raise ValueError(f"{label}: cannot parse {s!r} as float") from None
    if type_name == "str":
        return s
    if type_name == "bool":
        sl = s.strip().lower()
        if sl in ("true", "yes", "1", "on"):
            return True
        if sl in ("false", "no", "0", "off"):
            return False
        raise ValueError(f"{label}: cannot parse {s!r} as bool (use true/false/yes/no/1/0)")
    raise ValueError(f"{label}: unknown type {type_name!r}")
