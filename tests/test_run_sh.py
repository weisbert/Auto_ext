"""``run.sh`` is the only launcher, and it rewrites argv before Python sees it.

The rewriting exists because the launcher must ``cd`` to the workarea (si
-batch reads ``si.env`` from cwd) and that cd silently broke every relative
path the caller typed -- the whole documented office-validation path failed at
step one. ``docs/refactor/DEPLOY_FINDINGS.md`` section 1 has the incident.

Two kinds of check live here:

1. **The flag inventory cannot go stale.** ``AUTO_EXT_PATH_FLAGS`` is a
   hand-written list of every CLI option that takes a filesystem path. A list
   like that rots the day someone adds an option and never opens the shell
   script, and the failure mode is invisible: the new flag simply keeps the
   old broken behaviour. So the list is derived again here from ``cli.py``'s
   AST and compared in both directions.

2. **The rewriting itself behaves**, driven through ``AUTO_EXT_ARGV_DEBUG``,
   which prints the rewritten argv and exits before an interpreter is picked.

Why the AST and not ``--help``: importing Typer and walking the command tree
would test the app that was importable at test time, and the point of check 1
is to catch a *source* edit. ``ast`` reads the file on disk with no import.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUN_SH = REPO / "run.sh"
CLI_PY = REPO / "auto_ext" / "cli.py"

#: Options that name a filesystem location but must NOT be absolutized, with
#: the reason. They are patterns resolved against the workarea at render time
#: and may carry ``{cell}``-style placeholders, so a caller-relative rewrite
#: would send the output somewhere else. They are typed ``str`` in ``cli.py``,
#: so the AST scan below already excludes them; this table exists to assert
#: that the exclusion is deliberate rather than an oversight.
DELIBERATELY_NOT_PATHS = {
    "--to": "output pattern, workarea-relative, may contain {cell}",
    "--layout-out": "output pattern, workarea-relative, may contain {cell}",
}


def _declared_path_flags() -> set[str]:
    """Every flag in ``cli.py`` whose value is annotated ``Path``."""

    tree = ast.parse(CLI_PY.read_text(encoding="utf-8"))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        args = node.args.args
        defaults = node.args.defaults or []
        # Defaults bind to the LAST n parameters.
        for arg, default in zip(args[len(args) - len(defaults) :], defaults):
            if arg.annotation is None or "Path" not in ast.unparse(arg.annotation):
                continue
            if not isinstance(default, ast.Call):
                continue
            if not ast.unparse(default.func).endswith(("typer.Option", "typer.Argument")):
                continue
            # typer.Option(<default>, "--flag", "-f", ...): the string
            # positionals after the default are the flag spellings.
            for item in default.args[1:]:
                if (
                    isinstance(item, ast.Constant)
                    and isinstance(item.value, str)
                    and item.value.startswith("-")
                ):
                    flags.add(item.value)
    return flags


def _run_sh_path_flags() -> set[str]:
    """The ``AUTO_EXT_PATH_FLAGS`` list as ``run.sh`` declares it."""

    text = RUN_SH.read_text(encoding="utf-8")
    match = re.search(r'AUTO_EXT_PATH_FLAGS="\n(.*?)\n"', text, re.DOTALL)
    assert match is not None, "AUTO_EXT_PATH_FLAGS is gone from run.sh"
    return {line.strip() for line in match.group(1).splitlines() if line.strip()}


def test_run_sh_knows_every_path_flag_the_cli_declares() -> None:
    """A new ``Path`` option must be taught to the launcher in the same edit.

    Failing here is not a test problem: it means that one option still
    resolves against the workarea instead of against the caller, i.e. the bug
    this whole mechanism exists to kill has quietly come back for it.
    """

    missing = _declared_path_flags() - _run_sh_path_flags()
    assert not missing, (
        "these cli.py options take a Path but run.sh will not absolutize them: "
        f"{sorted(missing)} -- add them to AUTO_EXT_PATH_FLAGS in run.sh"
    )


def test_run_sh_lists_no_flag_the_cli_no_longer_has() -> None:
    """The other direction: a renamed flag leaves a dead entry behind."""

    stale = _run_sh_path_flags() - _declared_path_flags()
    assert not stale, (
        f"run.sh absolutizes {sorted(stale)}, which cli.py no longer declares "
        "as a Path option"
    )


def test_pattern_options_are_not_declared_as_paths() -> None:
    """``--to`` / ``--layout-out`` must stay ``str``, or they get rewritten.

    Annotating either as ``Path`` would pull it into the scan above, and a
    pattern absolutized against the caller's cwd renders to the wrong place.
    """

    declared = _declared_path_flags()
    for flag, why in DELIBERATELY_NOT_PATHS.items():
        assert flag not in declared, f"{flag} must not be a Path option: {why}"


# ---- behaviour ------------------------------------------------------------

bash_required = pytest.mark.skipif(
    shutil.which("bash") is None, reason="no bash on this machine"
)


def _argv(tmp_path: Path, *args: str) -> list[str]:
    """Run ``run.sh`` in argv-debug mode from ``tmp_path`` and return argv."""

    proc = subprocess.run(
        ["bash", str(RUN_SH), *args],
        cwd=tmp_path,
        env={"AUTO_EXT_ARGV_DEBUG": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    prefix = "argv="
    return [
        line[len(prefix) :]
        for line in proc.stdout.splitlines()
        if line.startswith(prefix)
    ]


def _cwd_of(tmp_path: Path, *args: str) -> str:
    """The caller cwd ``run.sh`` saw, in the shell's own path spelling.

    On Windows the test's ``tmp_path`` is ``C:\\...`` while bash reports
    ``/c/...``; comparing rewritten paths needs the shell's spelling, and
    only the shell can supply it.
    """

    proc = subprocess.run(
        ["bash", str(RUN_SH), *args],
        cwd=tmp_path,
        env={"AUTO_EXT_ARGV_DEBUG": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("invocation_cwd="):
            return line[len("invocation_cwd=") :]
    raise AssertionError(f"run.sh printed no invocation_cwd:\n{proc.stdout}")


@bash_required
def test_relative_config_dir_is_read_from_the_callers_cwd(tmp_path: Path) -> None:
    """The original incident: ``--config-dir config`` from the install dir."""

    (tmp_path / "config").mkdir()
    cwd = _cwd_of(tmp_path)
    argv = _argv(tmp_path, "check-env", "--config-dir", "config")
    assert argv == ["check-env", "--config-dir", f"{cwd}/config"]


@bash_required
def test_equals_form_is_rewritten_too(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    cwd = _cwd_of(tmp_path)
    argv = _argv(tmp_path, "check-env", "--config-dir=config")
    assert argv == ["check-env", f"--config-dir={cwd}/config"]


@bash_required
def test_absolute_paths_pass_through_untouched(tmp_path: Path) -> None:
    argv = _argv(tmp_path, "check-env", "--config-dir", "/somewhere/config")
    assert argv == ["check-env", "--config-dir", "/somewhere/config"]


@bash_required
def test_non_path_option_values_are_never_rewritten(tmp_path: Path) -> None:
    """A recipe id that happens to look like a relative path stays a recipe id."""

    argv = _argv(
        tmp_path, "run", "--recipe", "rc-typical-55c", "--stage", "strmout", "--dry-run"
    )
    assert argv == [
        "run",
        "--recipe",
        "rc-typical-55c",
        "--stage",
        "strmout",
        "--dry-run",
    ]


@bash_required
def test_output_patterns_stay_relative(tmp_path: Path) -> None:
    """``--to`` is workarea-relative by design, so it must survive the cd as-is.

    The real values carry ``{cell}``-style placeholders, but the braces cannot
    be asserted here: on Windows the MSYS runtime rewrites them while building
    ``bash.exe``'s command line, before ``run.sh`` ever sees them. Staying
    relative is the property that matters and it is testable everywhere.
    """

    (tmp_path / "reliability").mkdir()
    argv = _argv(tmp_path, "export-gds", "--to", "reliability/out.gds")
    assert argv == ["export-gds", "--to", "reliability/out.gds"]


@bash_required
def test_pytest_positional_paths_survive_the_cd(tmp_path: Path) -> None:
    """``./run.sh test tests/core`` broke the same way and is fixed the same way."""

    (tmp_path / "tests" / "core").mkdir(parents=True)
    cwd = _cwd_of(tmp_path)
    argv = _argv(tmp_path, "test", "tests/core", "-k", "progress")
    assert argv == ["test", f"{cwd}/tests/core", "-k", "progress"]


@bash_required
def test_pytest_node_ids_are_rewritten_by_their_path_half(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
    cwd = _cwd_of(tmp_path)
    argv = _argv(tmp_path, "test", "tests/test_x.py::test_one")
    assert argv == ["test", f"{cwd}/tests/test_x.py::test_one"]


@bash_required
def test_a_positional_that_is_not_a_file_is_left_alone(tmp_path: Path) -> None:
    """``-k progress`` and friends must not become paths just by being bare."""

    argv = _argv(tmp_path, "test", "-k", "progress")
    assert argv == ["test", "-k", "progress"]


@bash_required
def test_positional_rewriting_is_off_for_other_subcommands(tmp_path: Path) -> None:
    """Only ``test`` and ``recipe import`` take positional paths.

    ``run`` takes none, so a bare word must survive even when a same-named
    file happens to sit in the caller's cwd.
    """

    (tmp_path / "strmout").write_text("", encoding="utf-8")
    argv = _argv(tmp_path, "run", "--stage", "strmout")
    assert argv == ["run", "--stage", "strmout"]


@bash_required
def test_recipe_import_positional_files_are_absolutized(tmp_path: Path) -> None:
    (tmp_path / "cell.qci").write_text("", encoding="utf-8")
    cwd = _cwd_of(tmp_path)
    argv = _argv(tmp_path, "recipe", "import", "cell.qci")
    assert argv == ["recipe", "import", f"{cwd}/cell.qci"]


@bash_required
def test_a_dangling_path_flag_is_left_for_the_cli_to_report(tmp_path: Path) -> None:
    """``--config-dir --dry-run`` is a user error; do not turn it into a path."""

    argv = _argv(tmp_path, "check-env", "--config-dir", "--dry-run")
    assert argv == ["check-env", "--config-dir", "--dry-run"]


@bash_required
def test_no_arguments_is_not_an_error(tmp_path: Path) -> None:
    assert _argv(tmp_path) == []
