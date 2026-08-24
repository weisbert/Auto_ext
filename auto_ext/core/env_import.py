"""Read a project's own environment values out of files that project produced.

The half ``check-env`` can never do
-----------------------------------
``check-env`` asks the shell. On a machine where the PDK setup script has been
sourced that answers everything; on a new box it answers whatever that box
happens to export, and for the rest there is no second place to look. The
variable is not hard to find -- there is nowhere to find it.

But the project has already written the answer down. A runset, a ``.cmd``, an
``si.env`` produced under that project's environment contains the *rendered*
form of every path the flow uses, and the template that produced it says what
the un-rendered form was. The difference between the two is the value::

    template   $env(SETUP_ROOT)/assura_tech.lib
    the file   /pdk/hn001/setup/assura_tech.lib
                                  ->  SETUP_ROOT = /pdk/hn001/setup

:func:`auto_ext.core.recipe_import.solve_env_from_file` is that inversion, and
it is the same solver the recipe importer uses -- one parser, one set of
answers. This module is the part around it: several files at once, the
disagreements between them, and what the shell currently says about each
variable so the user can see whether accepting one would change anything.

What this module does NOT do
----------------------------
It does not write. :func:`import_env` returns a report and nothing else, for
the same reason ``import_recipe`` does: an environment value is a site fact
that decides where every stage reads and writes, and adopting one is a
decision. The caller writes accepted values into ``PdkProfile.env_overrides``,
which is already the field meaning "used INSTEAD of the shell" and which the
health checks already draw with the exchange glyph rather than a tick.

Unverified, and worth saying out loud
--------------------------------------
The solver underneath was developed against this repository's templates and
files rendered from them, never against a ``.cmd`` a real Quantus GUI wrote.
The first use of this on a real office file is also the first test of that
assumption; :data:`~auto_ext.core.recipe_import.MIN_SITE_HITS` and
:data:`~auto_ext.core.recipe_import.MIN_SITE_COVERAGE` are the numbers that
would move first, and :attr:`EnvImportResult.unreadable` is where a file that
did not clear them is reported rather than dropped.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from auto_ext.core.errors import AutoExtError
from auto_ext.core.recipe_import import (
    ImportSource,
    RecipeImportError,
    detect_target,
    env_vars_solvable_from_files,
    solve_env_from_file,
)
from auto_ext.model.common import RenderTarget
from auto_ext.model.pdk import PdkProfile

__all__ = [
    "EnvImportError",
    "EnvImportResult",
    "SolvedEnvVar",
    "UnreadableFile",
    "import_env",
]


class EnvImportError(AutoExtError):
    """Nothing offered could be read at all."""


@dataclass(frozen=True)
class SolvedEnvVar:
    """One variable, as recovered -- and as the shell currently answers it."""

    name: str
    value: str
    #: How it was recovered, in the user's terms.
    via: str
    #: The file it came out of.
    source: str
    #: ``os.environ[name]`` at the time of the scan, or ``None``. Not a check:
    #: it is the sentence "your shell says something else" that a user needs
    #: before accepting a pin, because the pin will win over it afterwards.
    shell_value: str | None = None
    #: Whether the profile already pins this name, and to what.
    pinned_value: str | None = None
    #: Other answers other files gave for the same name, as
    #: ``(value, source)``. Never resolved here: two files from the same
    #: project disagreeing about a site path is a fact about the project.
    disagreements: tuple[tuple[str, str], ...] = ()

    @property
    def agrees_with_shell(self) -> bool:
        return self.shell_value is not None and self.shell_value == self.value

    @property
    def would_change_anything(self) -> bool:
        """False when accepting this pin is a no-op.

        A pin that restates what the shell already exports is not harmless: it
        freezes today's answer into a file that travels, so the next machine
        gets this machine's path. Worth showing as "nothing to do here".
        """

        current = self.pinned_value if self.pinned_value is not None else self.shell_value
        return current != self.value


@dataclass(frozen=True)
class UnreadableFile:
    """A file that was offered and could not be used, and why."""

    label: str
    reason: str


@dataclass(frozen=True)
class EnvImportResult:
    """Everything the scan recovered, disagreed about, and could not read."""

    solved: tuple[SolvedEnvVar, ...] = ()
    #: Files that were read, as ``(label, target)``.
    read: tuple[tuple[str, RenderTarget], ...] = ()
    unreadable: tuple[UnreadableFile, ...] = ()
    #: Variables this profile needs that these files could not answer, with
    #: whether the shell can. Split because the two have different fixes: a
    #: variable the shell exports needs no action, one nothing can answer
    #: needs a different file or a hand-typed value.
    unanswered: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:
        return bool(self.solved)

    @property
    def changes(self) -> tuple[SolvedEnvVar, ...]:
        """Only the ones where accepting would actually change something."""

        return tuple(var for var in self.solved if var.would_change_anything)

    def summary(self) -> str:
        read = f"{len(self.read)} file(s)"
        if not self.solved:
            return f"read {read}; no environment value could be recovered"
        changing = len(self.changes)
        return (
            f"read {read}; recovered {len(self.solved)} value(s), "
            f"{changing} of which differ from what is in effect now"
        )


def import_env(
    files: Iterable[Path | str | ImportSource],
    *,
    profile: PdkProfile | None,
    environ: Mapping[str, str] | None = None,
    catalog=None,
    templates_root: Path | None = None,
) -> EnvImportResult:
    """Recover environment values from files this project produced.

    ``files`` is anything :class:`~auto_ext.core.recipe_import.ImportSource`
    accepts -- paths, or buffers a GUI already has in hand. ``environ``
    defaults to :data:`os.environ` and is injectable so a test does not depend
    on the machine it runs on.

    A file that cannot be classified or cannot be parsed is reported in
    :attr:`EnvImportResult.unreadable` rather than raising: the user offered
    several files precisely because they did not know which one carries the
    answer, and one bad file must not discard the others. Raising is reserved
    for "none of them could be read", where returning an empty report would
    look like "these files have nothing in them".
    """

    env = dict(os.environ if environ is None else environ)
    sources = _load(files)
    if not sources:
        raise EnvImportError("no files to read")

    read: list[tuple[str, RenderTarget]] = []
    unreadable: list[UnreadableFile] = []
    notes: list[str] = []
    #: name -> [(value, via, source)], in the order the files were given.
    found: dict[str, list[tuple[str, str, str]]] = {}

    for source in sources:
        try:
            target = source.target or detect_target(
                source.text, label=source.label, catalog=catalog
            )
        except RecipeImportError as exc:
            unreadable.append(UnreadableFile(source.label, str(exc)))
            continue
        try:
            solutions, file_notes = solve_env_from_file(
                source.text,
                target=target,
                profile=profile,
                catalog=catalog,
                templates_root=templates_root,
            )
        except (RecipeImportError, OSError) as exc:
            unreadable.append(UnreadableFile(source.label, str(exc)))
            continue

        read.append((source.label, target))
        notes.extend(f"{source.label}: {note}" for note in file_notes)
        for solution in solutions:
            found.setdefault(solution.name, []).append(
                (solution.value, solution.via, source.label)
            )

    if not read:
        detail = "; ".join(f"{item.label}: {item.reason}" for item in unreadable)
        raise EnvImportError(f"none of these files could be read. {detail}")

    pinned = dict(profile.env_overrides) if profile is not None else {}
    solved = tuple(
        _merge(name, answers, env=env, pinned=pinned)
        for name, answers in sorted(found.items())
    )

    return EnvImportResult(
        solved=solved,
        read=tuple(read),
        unreadable=tuple(unreadable),
        unanswered=_unanswered(profile, found, catalog, templates_root),
        notes=tuple(notes),
    )


def _load(files: Iterable[Path | str | ImportSource]) -> list[ImportSource]:
    """Read every input, turning an unreadable path into no input at all.

    A path that cannot be read raises out of ``ImportSource.from_path``; it is
    caught here and re-raised only if it leaves nothing, so one bad path among
    four does not stop the scan.
    """

    loaded: list[ImportSource] = []
    for item in files:
        if isinstance(item, ImportSource):
            loaded.append(item)
            continue
        try:
            loaded.append(ImportSource.from_path(item))
        except RecipeImportError:
            continue
    return loaded


def _merge(
    name: str,
    answers: list[tuple[str, str, str]],
    *,
    env: Mapping[str, str],
    pinned: Mapping[str, str],
) -> SolvedEnvVar:
    """One variable out of every answer the files gave for it.

    The first answer wins and the rest are reported. Picking a winner by vote
    or by file order would be inventing a rule the project does not have --
    two files from one project disagreeing about a site path is something the
    user has to look at, not something to average.
    """

    value, via, source = answers[0]
    others = tuple(
        (other_value, other_source)
        for other_value, _via, other_source in answers[1:]
        if other_value != value
    )
    return SolvedEnvVar(
        name=name,
        value=value,
        via=via,
        source=source,
        shell_value=env.get(name),
        pinned_value=pinned.get(name),
        disagreements=others,
    )


def _unanswered(
    profile: PdkProfile | None,
    found: Mapping[str, list[tuple[str, str, str]]],
    catalog,
    templates_root: Path | None,
) -> tuple[str, ...]:
    """Variables this profile needs that the files did not give up.

    Limited to what an import *could* have answered
    (:func:`~auto_ext.core.recipe_import.env_vars_solvable_from_files`)
    intersected with what the profile requires: listing a variable no file
    could ever carry as "not found" would be true and useless, and would bury
    the ones a different file really would answer.
    """

    if profile is None:
        return ()
    try:
        solvable = env_vars_solvable_from_files(
            profile, catalog=catalog, templates_root=templates_root
        )
    except OSError:  # pragma: no cover - templates are shipped with the package
        return ()
    wanted = set(profile.required_env) & solvable
    return tuple(sorted(wanted - set(found)))
