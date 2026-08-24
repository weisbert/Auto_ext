"""``auto-ext profile read-env`` -- the half ``check-env`` structurally cannot do.

The GUI has the same feature, and on the office box the CLI is the one that
gets used: X11 across a continent costs a round trip per redraw and this is a
question with a one-screen answer.

Two behaviours here matter beyond "it prints something":

* **The exit code is the answer.** 1 when something would change, 0 when
  nothing would, so ``read-env && echo all good`` means what it looks like.
* **``--write`` refuses to pick a side.** A value two of the user's own files
  disagree about is a question; an unattended write that answered it would
  pin an absolute path that looks exactly as plausible as the right one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app
from auto_ext.core.profile_discover import read_profile_yaml, write_profile_yaml
from auto_ext.model.common import RenderTarget
from tests.core.test_recipe_import import _render_all
from tests.support.v2 import ENV, make_profile

runner = CliRunner()


@pytest.fixture
def box(tmp_path: Path) -> Path:
    """A config directory with one profile, plus the files it produced."""

    profiles = tmp_path / "config" / "profiles"
    profiles.mkdir(parents=True)
    write_profile_yaml(profiles / "hn001.yaml", make_profile())

    produced = tmp_path / "produced"
    produced.mkdir()
    for target, text in _render_all(tmp_path).items():
        (produced / target.value).write_text(text, encoding="utf-8")
    return tmp_path


def _files(box: Path, *names: str) -> list[str]:
    directory = box / "produced"
    chosen = names or tuple(p.name for p in sorted(directory.iterdir()))
    return [str(directory / name) for name in chosen]


def _run(box: Path, *args: str, env: dict[str, str] | None = None):
    return runner.invoke(
        app,
        ["profile", "read-env", "--config-dir", str(box / "config"), *args],
        env=env or {},
    )


def test_it_recovers_a_value_the_shell_never_exported(box: Path) -> None:
    result = _run(box, *_files(box))
    assert "SETUP_ROOT" in result.stdout, result.stdout
    assert ENV["SETUP_ROOT"] in result.stdout


def test_it_says_where_each_value_was_read_from(box: Path) -> None:
    result = _run(box, *_files(box))
    assert "assura_tech.lib" in result.stdout


def test_the_exit_code_says_whether_anything_would_change(box: Path) -> None:
    changing = _run(box, *_files(box))
    assert changing.exit_code == 1, changing.stdout

    # ...and with the same values already pinned, nothing would.
    profile_path = box / "config" / "profiles" / "hn001.yaml"
    profile = read_profile_yaml(profile_path)
    write_profile_yaml(profile_path, profile.model_copy(update={"env_overrides": dict(ENV)}))

    unchanged = _run(box, *_files(box))
    assert unchanged.exit_code == 0, unchanged.stdout


def test_without_write_the_profile_is_untouched(box: Path) -> None:
    profile_path = box / "config" / "profiles" / "hn001.yaml"
    before = profile_path.read_bytes()
    _run(box, *_files(box))
    assert profile_path.read_bytes() == before


def test_write_pins_the_recovered_values(box: Path) -> None:
    profile_path = box / "config" / "profiles" / "hn001.yaml"
    result = _run(box, *_files(box), "--write")
    assert result.exit_code == 1, result.stdout
    overrides = read_profile_yaml(profile_path).env_overrides
    assert overrides["SETUP_ROOT"] == ENV["SETUP_ROOT"]


def test_write_refuses_to_settle_a_disagreement_between_the_files(box: Path) -> None:
    """An unattended write must not answer a question the files raise."""

    quantus = box / "produced" / RenderTarget.QUANTUS_EXT.value
    quantus.write_text(
        quantus.read_text(encoding="utf-8").replace(ENV["SETUP_ROOT"], "/other/setup"),
        encoding="utf-8",
    )

    result = _run(box, *_files(box), "--write")
    overrides = read_profile_yaml(box / "config" / "profiles" / "hn001.yaml").env_overrides
    assert "SETUP_ROOT" not in overrides
    assert "SETUP_ROOT" in result.stdout
    assert "disagree" in result.stdout.lower()


def test_an_unreadable_file_is_reported_and_the_rest_still_read(box: Path) -> None:
    junk = box / "produced" / "NOTES.md"
    junk.write_text("# notes\nalpha = 1\n" * 20, encoding="utf-8")
    result = _run(box, *_files(box))
    assert "NOTES.md" in result.stdout
    assert "SETUP_ROOT" in result.stdout


def test_nothing_readable_is_exit_2(box: Path, tmp_path: Path) -> None:
    junk = tmp_path / "NOTES.md"
    junk.write_text("# notes\nalpha = 1\n" * 20, encoding="utf-8")
    result = _run(box, str(junk))
    assert result.exit_code == 2
    # A refusal goes to stderr, so a caller piping stdout still sees it.
    assert "NOTES.md" in result.stderr


def test_naming_no_file_is_exit_2(box: Path) -> None:
    result = _run(box)
    assert result.exit_code == 2
    assert "at least one file" in result.stderr


def test_write_with_nothing_to_change_says_so(box: Path) -> None:
    profile_path = box / "config" / "profiles" / "hn001.yaml"
    profile = read_profile_yaml(profile_path)
    write_profile_yaml(profile_path, profile.model_copy(update={"env_overrides": dict(ENV)}))
    before = profile_path.read_bytes()

    result = _run(box, *_files(box), "--write")
    assert result.exit_code == 0
    assert "already in effect" in result.stdout
    assert profile_path.read_bytes() == before
