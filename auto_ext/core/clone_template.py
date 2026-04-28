"""Clone-and-edit a template variant (Feature #1, 2026-04-28).

Pure-Python helpers for cloning a Jinja template plus its manifest
sidecar to a new path under the same stage directory. The Templates
tab calls these from a small toolbar dialog; the rules themselves
know nothing about Qt so they're unit-testable without a
``QApplication``.

Conventions
-----------
* Templates live under ``<auto_ext_root>/templates/<stage>/`` where
  ``stage`` is one of ``calibre / quantus / jivaro / si / presets``.
* A template file name is shaped like ``<basename>.<ext>.j2`` where
  ``<ext>`` is the EDA tool's native extension (``qci``, ``cmd``,
  ``xml``, ``env``, ``tcl``, ``ile``). When a suffix is added the
  result keeps the same ``<ext>.j2`` tail:

    ``calibre_lvs.qci.j2`` + ``noconnect`` -> ``calibre_lvs_noconnect.qci.j2``

  Templates without a recognised ``<ext>.j2`` (just ``foo.j2``) get
  the suffix inserted right before ``.j2``:

    ``foo.j2`` + ``v2`` -> ``foo_v2.j2``

* The manifest sidecar (``<template>.j2.manifest.yaml``) is copied
  byte-for-byte alongside the new ``.j2`` so knob declarations
  survive. Templates without a sidecar (e.g. presets) clone the
  ``.j2`` only and the caller logs a warning.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from auto_ext.core.manifest import manifest_path_for

#: Native EDA-tool extensions that may appear before the ``.j2`` suffix.
#: The pin lists ``.qci / .tcl / .xml / .ile``; the live templates tree
#: also uses ``.cmd`` (quantus) and ``.env`` (si). All six are treated
#: the same way: stripped before suffix insertion, restored afterwards.
KNOWN_TOOL_EXTS: tuple[str, ...] = (
    "qci", "cmd", "xml", "env", "tcl", "ile",
)

#: A clone suffix is conservatively restricted so it cannot escape the
#: stage directory or break shell quoting. Empty string is rejected
#: explicitly by :func:`validate_suffix`.
_SUFFIX_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class CloneTemplateError(ValueError):
    """Raised when the clone request is invalid (bad suffix, missing
    source, destination already exists, source is not a ``.j2``)."""


def validate_suffix(suffix: str) -> None:
    """Raise :class:`CloneTemplateError` if the suffix is not allowed.

    Rules: non-empty, matches ``[A-Za-z0-9_-]+``. Anything else
    (whitespace, dot, slash, backslash) is rejected so users cannot
    accidentally produce a path-traversal destination.
    """
    if not suffix:
        raise CloneTemplateError("suffix must not be empty")
    if not _SUFFIX_RE.fullmatch(suffix):
        raise CloneTemplateError(
            f"suffix {suffix!r} contains disallowed characters; "
            "use letters, digits, underscore or dash only"
        )


def _split_template_name(name: str) -> tuple[str, str]:
    """Split a template file name into ``(stem, tail)`` where ``tail``
    is everything from the recognised tool ext (or just ``.j2``) onwards.

    Examples:
        ``calibre_lvs.qci.j2`` -> ``("calibre_lvs", ".qci.j2")``
        ``default.xml.j2``      -> ``("default", ".xml.j2")``
        ``dspf.cmd.j2``         -> ``("dspf", ".cmd.j2")``
        ``foo.j2``              -> ``("foo", ".j2")``

    The recognised tool exts come from :data:`KNOWN_TOOL_EXTS`. An
    unrecognised middle ext (``foo.bar.j2``) is treated as part of
    the stem so we don't accidentally split user-named templates we
    don't understand:

        ``foo.bar.j2``          -> ``("foo.bar", ".j2")``
    """
    if not name.endswith(".j2"):
        raise CloneTemplateError(
            f"template file {name!r} must end in .j2"
        )
    head = name[: -len(".j2")]  # strip ".j2"
    # Inspect the trailing ".<ext>" of head if it's a recognised EDA ext.
    dot = head.rfind(".")
    if dot >= 0:
        ext = head[dot + 1 :]
        if ext in KNOWN_TOOL_EXTS:
            return head[:dot], f".{ext}.j2"
    return head, ".j2"


def derive_clone_destination(source: Path, suffix: str) -> Path:
    """Compute the destination path for a clone, in the same directory.

    Raises :class:`CloneTemplateError` for invalid suffixes or sources
    that don't end in ``.j2``. Does **not** check whether the
    destination already exists; the caller does that so it can show a
    user-facing error message and offer to retry with a different
    suffix.
    """
    validate_suffix(suffix)
    stem, tail = _split_template_name(source.name)
    new_name = f"{stem}_{suffix}{tail}"
    return source.with_name(new_name)


def clone_template(
    source: Path, dest: Path, *, overwrite: bool = False
) -> tuple[Path, Path | None]:
    """Copy ``source.j2`` (and its manifest sidecar if present) to ``dest``.

    Returns ``(dest_template_path, dest_manifest_path_or_None)``. The
    manifest path is ``None`` when the source has no sidecar (e.g.
    a preset). The ``.j2`` is always copied, byte-for-byte.

    Refuses to overwrite an existing destination unless
    ``overwrite=True``. Refuses to clone if the source ``.j2`` is
    missing.

    Both files are copied with :func:`shutil.copy2` so timestamps are
    preserved — useful when bisecting "did template X change since
    yesterday".
    """
    if not source.is_file():
        raise CloneTemplateError(f"source template not found: {source}")
    if not source.name.endswith(".j2"):
        raise CloneTemplateError(
            f"source must be a .j2 template, got {source.name!r}"
        )
    if dest.exists() and not overwrite:
        raise CloneTemplateError(
            f"destination already exists: {dest} "
            "(pick a different suffix)"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)

    src_manifest = manifest_path_for(source)
    dest_manifest: Path | None = None
    if src_manifest.is_file():
        dest_manifest = manifest_path_for(dest)
        if dest_manifest.exists() and not overwrite:
            # Roll back the .j2 we just wrote so the on-disk state is
            # consistent (either both files or neither).
            try:
                dest.unlink()
            except OSError:
                pass
            raise CloneTemplateError(
                f"manifest destination already exists: {dest_manifest}"
            )
        shutil.copy2(src_manifest, dest_manifest)
    return dest, dest_manifest


__all__ = [
    "CloneTemplateError",
    "KNOWN_TOOL_EXTS",
    "clone_template",
    "derive_clone_destination",
    "validate_suffix",
]
