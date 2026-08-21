"""``config/workspace.yaml`` -- the five keys left of ``project.yaml``.

This is where the surviving half of ``tests/core/test_config.py``'s
``ProjectConfig`` schema block lives now. Fourteen keys became five, so most
of what that block asserted went out with its mechanism -- the template slots,
the ``paths`` vocabulary, the knob block -- but three groups had to come with
the move, and they are the three sections below:

* **the two path patterns** keep their grammar (env references, then
  ``str.format`` keys) and gain something ``project.yaml`` never had: the key
  set is checked at *load* time. ``str.format`` fails with a bare ``KeyError``,
  and in the middle of a batch that is a stack trace where a config error
  belongs.
* **the loader's posture** -- unknown key refused, empty file refused, wrong
  top-level type refused, future schema version refused with advice. A config
  loader that accepts a typo is how a run silently uses a default.
* **ruamel fidelity**, which is a project rule rather than a nicety.

``{task_id}`` is the interesting deletion. It was a *format key*, so a
workspace pattern could pin two different runs of one cell to one directory;
:data:`RETIRED_FORMAT_KEYS` refuses it by name and offers the two
replacements, because the two answers mean different things.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auto_ext.core.errors import ConfigError
from auto_ext.model.workspace import (
    FORMAT_KEYS,
    RETIRED_FORMAT_KEYS,
    WORKSPACE_FILENAME,
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceConfig,
    dump_workspace_yaml,
    load_workspace,
    load_workspace_with_raw,
    save_workspace,
)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / WORKSPACE_FILENAME
    path.write_text(body, encoding="utf-8")
    return path


# ---- defaults and identity --------------------------------------------------


def test_only_the_profile_id_is_required() -> None:
    """Which PDK is the one thing nothing can default.

    Every other field has an answer that is right for most projects; the
    process is not one of them, and guessing it is how a run reaches the wrong
    deck directory.
    """

    workspace = WorkspaceConfig(pdk_profile="hn001")
    assert workspace.pdk_profile == "hn001"
    assert workspace.schema_version == WORKSPACE_SCHEMA_VERSION
    assert workspace.output_dir_pattern == "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"
    assert workspace.intermediate_dir == "${WORK_ROOT2}"
    assert workspace.dspf_out_pattern == "${WORK_ROOT2}/{cell}.dspf"
    assert workspace.keep_runs == 0


def test_the_profile_id_must_be_a_slug() -> None:
    """It becomes a file name (``config/profiles/<id>.yaml``)."""

    with pytest.raises(ValidationError):
        WorkspaceConfig(pdk_profile="not a slug")
    with pytest.raises(ValidationError):
        WorkspaceConfig(pdk_profile="../escape")


def test_the_three_dropped_root_paths_are_gone() -> None:
    """``work_root`` / ``verify_root`` / ``setup_root`` had no reader.

    They existed so a GUI panel could print something, and the panel should
    read the shell. Asserting their absence is what stops them coming back as
    "display only" fields that somebody later resolves a path against.
    """

    for dropped in ("work_root", "verify_root", "setup_root", "employee_id"):
        assert dropped not in WorkspaceConfig.model_fields


def test_keep_runs_cannot_be_negative() -> None:
    """``0`` means keep everything; a negative would have no meaning at all."""

    assert WorkspaceConfig(pdk_profile="hn001", keep_runs=0).keep_runs == 0
    with pytest.raises(ValidationError):
        WorkspaceConfig(pdk_profile="hn001", keep_runs=-1)


# ---- the path patterns ------------------------------------------------------


@pytest.mark.parametrize("field", ["output_dir_pattern", "dspf_out_pattern", "intermediate_dir"])
def test_an_unknown_format_key_is_refused_at_load_time(field: str) -> None:
    """``{bogus}`` is a typo, and ``str.format`` would only say so at render.

    By then a batch is running and the message is a ``KeyError`` from inside
    the runner. Every key is knowable when the file is read, so it is checked
    when the file is read -- and the error lists what is available.
    """

    with pytest.raises(ValidationError) as excinfo:
        WorkspaceConfig(pdk_profile="hn001", **{field: "/out/{bogus}/x"})
    message = str(excinfo.value)
    assert "bogus" in message
    assert "{cell}" in message


@pytest.mark.parametrize("key", sorted(RETIRED_FORMAT_KEYS))
def test_a_retired_format_key_is_refused_by_name_with_its_replacement(key: str) -> None:
    """``{task_id}`` is the one that mattered: it *was* the identity.

    A workspace pattern keyed on it pinned every run of a cell to one
    directory, which is what the Run object exists to undo. The migration
    cannot pick the replacement on the user's behalf -- ``{run_slug}`` and
    ``{cell}`` mean different things -- so the error names both.
    """

    with pytest.raises(ValidationError) as excinfo:
        WorkspaceConfig(pdk_profile="hn001", output_dir_pattern="/out/{%s}/x" % key)
    message = str(excinfo.value)
    assert key in message
    assert RETIRED_FORMAT_KEYS[key].split()[0] in message


@pytest.mark.parametrize("key", sorted(FORMAT_KEYS))
def test_every_advertised_format_key_is_accepted(key: str) -> None:
    """The error message above lists these; each one has to actually work."""

    workspace = WorkspaceConfig(
        pdk_profile="hn001", output_dir_pattern="/out/{%s}/x" % key
    )
    assert key in workspace.format_keys_used()


def test_an_env_reference_is_not_read_as_a_format_key() -> None:
    """``${WORK_ROOT}`` is resolved by ``substitute_env``, not by ``.format``.

    The negative lookbehind that keeps those apart is one character wide, and
    getting it wrong would refuse every default pattern in the schema.
    """

    workspace = WorkspaceConfig(
        pdk_profile="hn001",
        output_dir_pattern="${WORK_ROOT}/cds/${WORK_ROOT2}/QCI_PATH_{cell}",
    )
    assert workspace.format_keys_used() == {"cell"}


def test_format_keys_used_reports_across_all_three_patterns() -> None:
    """The migration prints this, and a renderer decides what to compute."""

    workspace = WorkspaceConfig(
        pdk_profile="hn001",
        output_dir_pattern="/out/{cell}/{library}",
        intermediate_dir="/tmp/{recipe}",
        dspf_out_pattern="/out/{run_slug}/{cell}.dspf",
    )
    assert workspace.format_keys_used() == {"cell", "library", "recipe", "run_slug"}


@pytest.mark.parametrize("field", ["output_dir_pattern", "dspf_out_pattern", "intermediate_dir"])
def test_an_empty_pattern_is_refused(field: str) -> None:
    """An empty output pattern resolves to the cwd; nothing good follows."""

    with pytest.raises(ValidationError):
        WorkspaceConfig(pdk_profile="hn001", **{field: "   "})


# ---- the loader's posture ---------------------------------------------------


def test_load_reads_a_complete_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "schema_version: 1\n"
        "pdk_profile: hn001\n"
        'output_dir_pattern: "${WORK_ROOT}/cds/{cell}"\n'
        'intermediate_dir: "${WORK_ROOT2}"\n'
        'dspf_out_pattern: "${WORK_ROOT2}/{cell}.dspf"\n'
        "keep_runs: 50\n",
    )
    workspace = load_workspace(path)
    assert workspace.pdk_profile == "hn001"
    assert workspace.keep_runs == 50


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    """A typo must not fall through to a default and change what runs."""

    path = _write(tmp_path, "pdk_profile: hn001\nkeep_run: 5\n")
    with pytest.raises(ConfigError, match="keep_run"):
        load_workspace(path)


def test_a_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_workspace(tmp_path / "nope.yaml")


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="empty"):
        load_workspace(_write(tmp_path, ""))


def test_a_non_mapping_top_level_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="mapping"):
        load_workspace(_write(tmp_path, "- just\n- a list\n"))


def test_broken_yaml_is_a_config_error_not_a_traceback(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML"):
        load_workspace(_write(tmp_path, "pdk_profile: [unclosed\n"))


def test_a_future_schema_version_is_refused_with_advice(tmp_path: Path) -> None:
    """Reading a v2 file with a v1 build would silently drop whatever v2 added."""

    path = _write(tmp_path, f"schema_version: {WORKSPACE_SCHEMA_VERSION + 1}\npdk_profile: hn001\n")
    with pytest.raises(ConfigError, match="newer than this build"):
        load_workspace(path)


def test_a_non_integer_schema_version_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="schema_version"):
        load_workspace(_write(tmp_path, "schema_version: one\npdk_profile: hn001\n"))


def test_an_invalid_pattern_on_disk_is_a_config_error(tmp_path: Path) -> None:
    """The validator's message has to survive the loader's wrapping."""

    path = _write(tmp_path, "pdk_profile: hn001\ndspf_out_pattern: /out/{task_id}.dspf\n")
    with pytest.raises(ConfigError, match="task_id"):
        load_workspace(path)


# ---- ruamel fidelity --------------------------------------------------------


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    workspace = WorkspaceConfig(
        pdk_profile="hn001", output_dir_pattern="/out/{cell}", keep_runs=7
    )
    path = tmp_path / "config" / WORKSPACE_FILENAME
    save_workspace(workspace, path)
    assert load_workspace(path).model_dump() == workspace.model_dump()


def test_the_written_file_ends_in_exactly_one_newline(tmp_path: Path) -> None:
    """A file the GUI rewrites on every save must not grow a blank line a run."""

    path = tmp_path / WORKSPACE_FILENAME
    save_workspace(WorkspaceConfig(pdk_profile="hn001"), path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_a_comment_survives_an_edit(tmp_path: Path) -> None:
    """The project rule: ruamel because it preserves comments.

    These patterns are cryptic enough that the comment beside them is the
    documentation, and the GUI rewrites this file whenever the user changes
    the retention count.
    """

    path = _write(
        tmp_path,
        "# Where this project's Cadence work lands.\n"
        "pdk_profile: hn001  # config/profiles/hn001.yaml\n"
        "# one workspace per cell, reused between runs\n"
        'output_dir_pattern: "${WORK_ROOT}/cds/verify/QCI_PATH_{cell}"\n',
    )
    workspace, raw = load_workspace_with_raw(path)
    dumped = dump_workspace_yaml(workspace.model_copy(update={"keep_runs": 20}), raw=raw)

    assert "# Where this project's Cadence work lands." in dumped
    assert "# config/profiles/hn001.yaml" in dumped
    assert "# one workspace per cell, reused between runs" in dumped
    assert "keep_runs: 20" in dumped


def test_dump_without_a_raw_tree_is_still_valid_yaml(tmp_path: Path) -> None:
    """A workspace built in memory (the wizard's first save) has no raw tree."""

    text = dump_workspace_yaml(WorkspaceConfig(pdk_profile="hn001"))
    path = _write(tmp_path, text)
    assert load_workspace(path).pdk_profile == "hn001"
