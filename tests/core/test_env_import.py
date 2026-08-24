"""Reading a project's own environment values back out of what it produced.

This is the half ``check-env`` structurally cannot do. It asks the shell; on a
machine where the PDK setup script has not been sourced there is no second
place for it to look. The files the project has already written are that
second place -- they contain the rendered form of every path, and the template
that produced them says what the un-rendered form was.

So the tests start from real rendered output (the same ``_render_all`` the
recipe-import tests use, so a sample is byte-for-byte what a run of this tool
puts on disk) and assert the values come back out. What makes this worth
testing rather than trusting: every failure mode here is silent. A variable
that is not recovered simply does not appear; a variable recovered *wrongly*
is a plausible absolute path that would be pinned into a profile and send
every future run at the wrong deck.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_ext.core import env_import
from auto_ext.core import recipe_import as ri
from auto_ext.core.errors import AutoExtError
from auto_ext.model.common import RenderTarget
from tests.core.test_recipe_import import _render_all, _sources
from tests.support.v2 import ENV, make_profile


@pytest.fixture
def shipped(tmp_path: Path) -> dict[RenderTarget, str]:
    return _render_all(tmp_path)


def _result(texts: dict[RenderTarget, str], **kwargs) -> env_import.EnvImportResult:
    kwargs.setdefault("profile", make_profile())
    kwargs.setdefault("environ", {})
    return env_import.import_env(_sources(texts), **kwargs)


def _by_name(result: env_import.EnvImportResult) -> dict[str, env_import.SolvedEnvVar]:
    return {var.name: var for var in result.solved}


# ---- the point of the whole thing -------------------------------------------


def test_a_variable_the_shell_cannot_supply_is_recovered_from_the_file(
    shipped: dict[RenderTarget, str],
) -> None:
    """With an EMPTY environment, which is the situation this exists for."""

    result = _result(shipped)
    found = _by_name(result)
    assert found, result.summary()
    for name, var in found.items():
        assert var.value == ENV[name], f"{name} was recovered wrongly"
        assert var.shell_value is None


def test_setup_root_comes_back_out_of_the_technology_library_line(
    shipped: dict[RenderTarget, str],
) -> None:
    """The named case: a value the templates no longer spell as $env(X).

    The parameterisation round moved it into a profile expression, so the only
    way back is matching that expression against what the file actually says.
    """

    found = _by_name(_result(shipped))
    assert "SETUP_ROOT" in found, sorted(found)
    var = found["SETUP_ROOT"]
    assert var.value == ENV["SETUP_ROOT"]
    assert "assura_tech.lib" in var.via


def test_the_report_says_where_every_value_came_from(
    shipped: dict[RenderTarget, str],
) -> None:
    """An env value decides where every stage reads and writes.

    A user asked to accept one is entitled to see what it was read out of --
    accepting it blind is how this machine's paths get pinned into a profile
    that then travels.
    """

    for var in _result(shipped).solved:
        assert var.source, var.name
        assert var.via, var.name


# ---- what the shell says ----------------------------------------------------


def test_a_value_the_shell_already_agrees_with_is_not_a_change(
    shipped: dict[RenderTarget, str],
) -> None:
    """Pinning what the shell already exports is not harmless.

    It freezes today's answer into a file that travels, so the next machine
    inherits this one's paths. Worth marking as nothing to do.
    """

    result = _result(shipped, environ=dict(ENV))
    for var in result.solved:
        assert var.agrees_with_shell is True
        assert var.would_change_anything is False
    assert result.changes == ()


def test_a_shell_that_disagrees_is_shown_not_resolved(
    shipped: dict[RenderTarget, str],
) -> None:
    environ = dict(ENV) | {"SETUP_ROOT": "/somewhere/else"}
    var = _by_name(_result(shipped, environ=environ))["SETUP_ROOT"]
    assert var.shell_value == "/somewhere/else"
    assert var.value == ENV["SETUP_ROOT"]
    assert var.agrees_with_shell is False
    assert var.would_change_anything is True


def test_a_value_already_pinned_to_the_same_thing_is_not_a_change(
    shipped: dict[RenderTarget, str],
) -> None:
    profile = make_profile(env_overrides={"SETUP_ROOT": ENV["SETUP_ROOT"]})
    var = _by_name(_result(shipped, profile=profile))["SETUP_ROOT"]
    assert var.pinned_value == ENV["SETUP_ROOT"]
    assert var.would_change_anything is False


def test_an_existing_pin_beats_the_shell_when_deciding_what_would_change(
    shipped: dict[RenderTarget, str],
) -> None:
    """Because a pin is what will be in effect, whatever the shell exports."""

    profile = make_profile(env_overrides={"SETUP_ROOT": "/old/pin"})
    var = _by_name(_result(shipped, profile=profile, environ=dict(ENV)))["SETUP_ROOT"]
    assert var.would_change_anything is True


# ---- several files ----------------------------------------------------------


def test_every_readable_file_is_reported_as_read(
    shipped: dict[RenderTarget, str],
) -> None:
    result = _result(shipped)
    assert {target for _label, target in result.read} == set(shipped)


def test_files_that_disagree_are_reported_rather_than_averaged(
    shipped: dict[RenderTarget, str],
) -> None:
    """Two files from one project disagreeing is a fact about the project.

    Picking a winner by vote or by order would be inventing a rule this
    project does not have, and the wrong answer is an absolute path that looks
    exactly as plausible as the right one.
    """

    texts = dict(shipped)
    quantus = RenderTarget.QUANTUS_EXT
    texts[quantus] = texts[quantus].replace(ENV["SETUP_ROOT"], "/other/setup")

    var = _by_name(_result(texts))["SETUP_ROOT"]
    both = {var.value} | {value for value, _source in var.disagreements}
    assert both == {"/other/setup", ENV["SETUP_ROOT"]}, "an answer vanished"
    # and the report names the file each answer came from
    assert var.source
    assert all(source for _value, source in var.disagreements)


def test_one_unreadable_file_does_not_discard_the_others(
    shipped: dict[RenderTarget, str],
) -> None:
    """A user offers several files because they do not know which one has it."""

    sources = _sources(shipped)
    sources.append(ri.ImportSource(label="NOTES.md", text="# notes\nalpha = 1\n" * 20))

    result = env_import.import_env(sources, profile=make_profile(), environ={})
    assert result.solved
    assert [item.label for item in result.unreadable] == ["NOTES.md"]
    assert "NOTES.md" in result.unreadable[0].reason


def test_a_missing_path_is_skipped_not_fatal(
    shipped: dict[RenderTarget, str], tmp_path: Path
) -> None:
    real = tmp_path / "si.env"
    real.write_text(shipped[RenderTarget.SI_ENV], encoding="utf-8")
    result = env_import.import_env(
        [real, tmp_path / "not_there.cmd"], profile=make_profile(), environ={}
    )
    assert [label for label, _t in result.read] == [str(real)]


# ---- refusals ---------------------------------------------------------------


def test_nothing_readable_at_all_is_an_error(tmp_path: Path) -> None:
    """An empty report would read as "these files have nothing in them"."""

    junk = ri.ImportSource(label="NOTES.md", text="# notes\nalpha = 1\n" * 20)
    with pytest.raises(env_import.EnvImportError) as exc:
        env_import.import_env([junk], profile=make_profile(), environ={})
    assert "NOTES.md" in str(exc.value)


def test_no_files_at_all_is_an_error() -> None:
    with pytest.raises(env_import.EnvImportError):
        env_import.import_env([], profile=make_profile(), environ={})


def test_the_error_is_an_auto_ext_error() -> None:
    """The CLI catches AutoExtError to fail one command rather than traceback."""

    assert issubclass(env_import.EnvImportError, AutoExtError)


# ---- what could not be answered ---------------------------------------------


def test_a_variable_no_file_could_ever_carry_is_not_listed_as_missing(
    shipped: dict[RenderTarget, str],
) -> None:
    """Listing it would be true and useless, and would bury the real misses."""

    profile = make_profile(required_env=["SETUP_ROOT", "SOMETHING_NO_TEMPLATE_USES"])
    result = _result(shipped, profile=profile)
    assert "SOMETHING_NO_TEMPLATE_USES" not in result.unanswered


def test_a_variable_the_files_did_not_answer_is_listed(
    shipped: dict[RenderTarget, str],
) -> None:
    """One file cannot carry everything; the report has to say what is left."""

    only_si = {RenderTarget.SI_ENV: shipped[RenderTarget.SI_ENV]}
    # SETUP_ROOT reaches a quantus .cmd, never si.env, so exactly one of these
    # two is answerable from this file -- which is the case worth reporting.
    profile = make_profile(required_env=["SETUP_ROOT", "calibre_source_added_place"])
    result = _result(only_si, profile=profile)

    assert "calibre_source_added_place" in _by_name(result)
    assert result.unanswered == ("SETUP_ROOT",)


# ---- the report itself ------------------------------------------------------


def test_nothing_is_written(shipped: dict[RenderTarget, str], tmp_path: Path) -> None:
    """Adopting a site fact is a decision; this only reports."""

    before = set(tmp_path.rglob("*"))
    _result(shipped)
    assert set(tmp_path.rglob("*")) == before


def test_the_summary_counts_files_and_changes(
    shipped: dict[RenderTarget, str],
) -> None:
    result = _result(shipped)
    assert f"{len(result.read)} file(s)" in result.summary()
    assert "differ from what is in effect now" in result.summary()


def test_a_result_with_no_values_is_falsey(tmp_path: Path) -> None:
    assert not env_import.EnvImportResult()
