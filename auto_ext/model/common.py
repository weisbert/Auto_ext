"""Shared types for the persisted object model.

This is the home of the four things every model module needs and no model
module should re-invent: the two pydantic bases, the two constrained string
aliases, the stage / render-target enums, and the two helpers (``slugify`` /
``utcnow``).

Written against ``docs/refactor/01-schema.md`` section 1.0.

Why it exists at all: S1 landed :mod:`auto_ext.model.run` before there was
anywhere shared to put ``Base`` / ``Frozen``, so they lived there. Everything
here moved out of that module unchanged -- same class bodies, same regexes,
same fallbacks -- and ``auto_ext.model.run`` re-exports them so no existing
import breaks. ``tests/conftest.py`` patches ``auto_ext.model.run.utcnow``;
that still works, because rebinding a module attribute is what the fixture
does and :mod:`auto_ext.model.run` resolves ``utcnow`` through its own
module globals at call time.

Import direction: this module imports nothing from ``auto_ext`` at all. Every
other model module may import it; it may import none of them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, WrapValidator

__all__ = [
    "STAGE_ORDER",
    "AsWritten",
    "Base",
    "Frozen",
    "PathExpr",
    "RenderTarget",
    "Slug",
    "Stage",
    "WrittenFloat",
    "slugify",
    "utcnow",
]

#: Identifier-style short name: ``recipe_id`` / ``profile_id`` / corner name /
#: ``check_id``. Lowercase because these end up in file names and, for a run,
#: in a directory name -- and NTFS is case-insensitive while ext4 is not, so a
#: mixed-case identifier is two names on Linux and one on Windows.
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=64)]

#: A path expression. The grammar is exactly the one ``core/env.py`` already
#: implements, unchanged:
#:
#: * env references ``$X`` / ``${X}`` / ``$env(X)`` (``substitute_env``), and
#: * an optional ``|parent`` suffix filter (``resolve_path_expr``).
#:
#: i.e. the value syntax of ``ProjectConfig.paths`` carried over verbatim.
PathExpr = Annotated[str, StringConstraints(min_length=1)]


class WrittenFloat(float):
    """A float that renders the way it was spelled in the file it came from.

    ``125`` and ``125.0`` are the same number, and ``str()`` spells both of
    them ``125.0``. That one byte is enough to break the importer: it reads
    ``-temperature 125`` into ``recipe.extraction.temperature_c``, renders the
    baseline back as ``125.0``, sees a difference and stores it as a patch --
    which pins the literal ``125`` into every future render and kills the very
    field the value was just read into. The read side and the render side have
    to spell a number the same way, and the only thing that knows how it was
    spelled is the file it was read from.

    So a number recovered from a file carries its spelling with it. It *is* a
    float everywhere else: same value, same arithmetic, same comparisons, same
    JSON. Only ``str()`` (which is what Jinja calls) and ``format()`` with an
    empty spec answer with the original text.

    A number that was computed, or that comes from a catalog default, is an
    ordinary ``float`` and keeps ``str()``'s answer. That is deliberate: it is
    why replacing a literal in a ``.j2`` with a ``[[placeholder]]`` fed from
    the catalog default does not move a single byte
    (``tests/catalog/test_byte_fidelity.py``). No global rule could do both --
    trimming ``.0`` off every whole float would round-trip a hand-written
    ``125`` and change the shipped ``-decoupling_factor 1.0`` at the same
    time, so the spelling has to travel with the value that has one.

    Where the spelling stops: serialization. ``model_dump(mode="json")`` and
    therefore every YAML and JSON file this project writes emit a plain float,
    because a field typed ``number`` has to stay one. A recipe saved and
    loaded again renders ``125.0`` where the imported one rendered ``125`` --
    the same number, and still a live field, which is the property that
    matters. Spelling fidelity is what keeps the *import* from mistaking a
    formatting difference for a user's edit and freezing it into a patch.
    """

    __slots__ = ("written",)

    def __new__(cls, text: object) -> WrittenFloat:
        """Build from the *text* a file carried; ``ValueError`` if it is not a number."""

        spelling = str(text)
        value = super().__new__(cls, spelling)
        object.__setattr__(value, "written", spelling)
        return value

    def __str__(self) -> str:
        return self.written

    def __repr__(self) -> str:
        return self.written

    def __format__(self, spec: str) -> str:
        # An explicit spec asks for float formatting and gets it; a bare
        # ``f"{value}"`` is the same request as ``str()`` and gets the spelling.
        return self.written if spec == "" else float.__format__(self, spec)

    def __reduce__(self) -> tuple[type[WrittenFloat], tuple[str]]:
        # copy / deepcopy / pickle rebuild through ``__new__`` with the
        # spelling, so a slot that no default reconstructor knows about cannot
        # go missing and leave ``str()`` raising.
        return (WrittenFloat, (self.written,))


def _keep_written(value: object, handler: Callable[[object], float]) -> float:
    """Validate as a float, but do not throw away a spelling that came with it.

    pydantic re-builds a plain ``float`` from any float-like input, which is
    exactly right for every other purpose and would drop the spelling here.
    Anything that is not already a :class:`WrittenFloat` goes through the
    stock validator untouched, so error messages and coercion rules are
    pydantic's, not ours.
    """

    if isinstance(value, WrittenFloat):
        handler(float(value))
        return value
    return handler(value)


#: A ``float`` field that keeps the spelling a :class:`WrittenFloat` arrived
#: with. Behaves as ``float`` in every other respect -- same coercion, same
#: error messages, same ``number`` in the JSON schema, and ``model_dump`` in
#: JSON mode still emits a plain float.
AsWritten = Annotated[float, WrapValidator(_keep_written)]


class Base(BaseModel):
    """Base for editable objects: unknown keys are an error, never swallowed."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Frozen(BaseModel):
    """Base for record objects: unknown keys are an error, fields are read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Stage(StrEnum):
    """The five Cadence steps. Values match ``runner.STAGE_ORDER`` exactly.

    ``auto_ext.core.patch_models`` declares a structurally identical enum for
    the patch layer; ``tests/catalog/test_catalog.py`` asserts the two never
    drift apart. Collapsing them into one is a C2 job -- doing it here would
    mean editing a module this round does not own.
    """

    SI = "si"
    STRMOUT = "strmout"
    CALIBRE = "calibre"
    QUANTUS = "quantus"
    JIVARO = "jivaro"


#: Execution order. The runner's ``STAGE_ORDER`` is the same sequence as bare
#: strings; comparing the two is a cheap guard and ``Stage`` is a ``StrEnum``,
#: so ``"si" in STAGE_ORDER`` and ``Stage.SI in ("si", ...)`` both hold.
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.SI,
    Stage.STRMOUT,
    Stage.CALIBRE,
    Stage.QUANTUS,
    Stage.JIVARO,
)


class RenderTarget(StrEnum):
    """One rendered EDA input file a run may produce.

    Replaces the four bound template slots of ``ProjectConfig.templates``: the
    user no longer names a path per stage, the catalog provides the template
    per target. Patches mount per target too (one diff per target), which is
    why ``quantus`` splits into two values -- ``ext.cmd`` and ``dspf.cmd`` are
    two files with two different patch surfaces, not one slot with two
    possible occupants.

    ``strmout`` has no member on purpose: it has no rendered file, its argv is
    assembled in ``auto_ext/tools/strmout.py``. Catalog entries for it carry a
    landing site with ``target: null`` and an explicit ``stage: strmout``.
    """

    SI_ENV = "si.env"
    LVS_QCI = "lvs.qci"
    QUANTUS_EXT = "quantus.ext.cmd"
    QUANTUS_DSPF = "quantus.dspf.cmd"
    JIVARO_XML = "jivaro.xml"


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 24) -> str:
    """Reduce ``text`` to ``[a-z0-9-]``, collapsing runs of anything else.

    Never returns an empty string (``"x"`` is the fallback), so a cell named
    entirely out of punctuation still produces a usable directory name. Path
    separators, ``..``, drive colons and shell metacharacters all collapse to
    ``-`` here; ``auto_ext.model.run.validate_run_slug`` is the enforcing gate.
    """

    s = _SLUG_STRIP.sub("-", text.strip().lower()).strip("-")
    return s[:max_len].rstrip("-") or "x"


def utcnow() -> datetime:
    """Timezone-aware "now" in UTC.

    The single injection point for time in the model layer: tests monkeypatch
    this attribute on the importing module (see the ``frozen_clock`` fixture)
    rather than patching :class:`datetime.datetime` itself.
    """

    return datetime.now(timezone.utc)
