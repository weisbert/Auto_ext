"""End-to-end CLI smoke tests, stitched along the seams the v2 model has.

These tests fill a gap that single-stage unit tests cannot cover: the seams
*between* subcommands. The original file walked ``init-project`` ->
``check-env`` -> ``run --dry-run`` and existed because the ``dspf_out_path``
bug class lived exactly there -- both halves passed their unit tests and the
stitched pipeline still produced a wrong file. The seams moved with the
object model, so the walk moved with them:

    profile discover  ->  check-env  ->  recipe + cells  ->  run  ->  runs show
    (scan a PDK tree)     (can I run)    (what to render)   (do it)  (read back)

One test per seam, plus the two that pay for the file's existence: the
cross-PDK portability claim (the same Recipe against two technologies must
differ only where the PDK binds -- the transmigrated
``test_init_project_cross_project_abstraction``) and the dspf path agreement
that was bug 1+2's home.

Mock policy: no EDA subprocess is ever spawned. ``--dry-run`` covers the one
place a stage would, and :func:`_e2e_patch_run_subprocess` is a recording
no-op net under it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from auto_ext.cli import app
from auto_ext.core.run_store import read_record
from auto_ext.model.cells import CELLS_FILENAME, save_cells
from auto_ext.model.recipe import save_recipe
from auto_ext.model.workspace import WORKSPACE_FILENAME, save_workspace
from tests.support.v2 import (
    make_cell_book,
    make_healthy_profile,
    make_pdk_tree,
    make_recipe,
    make_workspace,
)


# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
def _e2e_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def _e2e_raw_dir() -> Path:
    """The shipped raw EDA samples (a real Calibre runset export lives here)."""

    return Path(__file__).resolve().parent / "fixtures" / "raw"


@pytest.fixture
def _e2e_pdk(tmp_path: Path) -> dict[str, Path]:
    """A PDK directory tree a scan can actually find things in."""

    return make_pdk_tree(tmp_path / "pdk")


@pytest.fixture
def _e2e_shell(
    _e2e_pdk: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Point the four scanned env vars at ``_e2e_pdk``, as a setup script would.

    This is the whole input to ``profile discover``: the command reads the
    shell, never a config file, which is the property the first test pins.
    """

    setup = _e2e_pdk["setup"]
    env = {
        "SETUP_ROOT": setup.as_posix(),
        "VERIFY_ROOT": (_e2e_pdk["root"] / "verify").as_posix(),
        "PDK_LAYER_MAP_FILE": (setup / "layers.map").as_posix(),
        "calibre_source_added_place": (_e2e_pdk["lvs"] / "empty.cdl").as_posix(),
        "WORK_ROOT": _e2e_pdk["root"].as_posix(),
        "WORK_ROOT2": _e2e_pdk["root"].as_posix(),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return env


@pytest.fixture
def _e2e_workarea(tmp_path: Path) -> Path:
    """The cwd the tools expect: ``cds.lib`` + ``.cdsinit`` placeholders."""

    wa = tmp_path / "workarea"
    wa.mkdir()
    (wa / "cds.lib").write_text("; mock cds.lib\n", encoding="utf-8")
    (wa / ".cdsinit").write_text("; mock .cdsinit\n", encoding="utf-8")
    return wa


@pytest.fixture
def _e2e_patch_run_subprocess(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Defensive subprocess stub.

    ``--dry-run`` short-circuits before :func:`run_subprocess` would be
    invoked; this records anything that gets past it, so a future change that
    starts spawning during a dry run fails a test instead of a machine.
    """

    import auto_ext.tools.base as base

    calls: list[dict[str, Any]] = []

    def _fake(argv, cwd, env, log_path, *, cancel_token=None) -> int:
        calls.append({"argv": list(argv), "cwd": cwd, "log_path": log_path})
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("e2e-stub\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(base, "run_subprocess", _fake)
    return calls


def _write_project(
    root: Path, *, profile_id: str = "hn001", pdk: dict[str, Path] | None = None
) -> Path:
    """Write a complete v2 tree at ``root`` and return it.

    ``config/workspace.yaml`` + ``config/cells.yaml`` +
    ``config/profiles/<id>.yaml`` + ``recipes/<id>.yaml`` -- the four files a
    run reads, written through the same savers the GUI uses.
    """

    from auto_ext.core.profile_discover import write_profile_yaml

    config = root / "config"
    config.mkdir(parents=True, exist_ok=True)
    save_workspace(make_workspace(pdk_profile=profile_id), config / WORKSPACE_FILENAME)
    save_cells(make_cell_book(), config / CELLS_FILENAME)
    if pdk is not None:
        write_profile_yaml(
            config / "profiles" / f"{profile_id}.yaml",
            make_healthy_profile(pdk, profile_id=profile_id),
        )
    recipe = make_recipe()
    save_recipe(recipe, root / "recipes" / f"{recipe.recipe_id}.yaml")
    return root


def _rendered(root: Path) -> dict[str, str]:
    """Every rendered file of the single run under ``root``, by file name."""

    files = sorted((root / "runs").glob("*/rendered/*"))
    assert files, f"nothing rendered under {root / 'runs'}"
    return {path.name: path.read_text(encoding="utf-8") for path in files}


# ---- seam 1: the shell -> a PdkProfile on disk -------------------------------


def test_e2e_discover_writes_a_profile_this_shell_can_run(
    _e2e_runner: CliRunner,
    _e2e_shell: dict[str, str],
    _e2e_raw_dir: Path,
    tmp_path: Path,
    mocks_on_path: Path,
) -> None:
    """``profile discover --write`` then ``check-env``: two commands, one claim.

    The claim is that the file the scan writes is the file the health check
    reads, and that a shell the scan was happy with is a shell that can start
    a run. Nothing else in the suite crosses that boundary: the discovery
    tests stop at the object and the health tests start from one.

    ``_e2e_shell`` is requested for its side effect: it is what puts the four
    scanned variables in the environment, and the scan reads nothing else.
    """

    assert _e2e_shell["SETUP_ROOT"]
    root = tmp_path / "proj"
    discovered = _e2e_runner.invoke(
        app,
        [
            "profile", "discover",
            "--profile-id", "hn001",
            "--raw-calibre", str(_e2e_raw_dir / "calibre_sample.qci"),
            "--auto-ext-root", str(root),
            "--write",
        ],
    )
    # Exit 1 means "drafted, with gaps"; both verdicts write the file.
    assert discovered.exit_code in (0, 1), discovered.output

    profile_path = root / "config" / "profiles" / "hn001.yaml"
    assert profile_path.is_file(), discovered.output
    text = profile_path.read_text(encoding="utf-8")
    # The scan found the deck directory from $calibre_source_added_place, and
    # the corner table from the technology library beside $SETUP_ROOT.
    assert "lvs_decks" in text
    assert "TYPICAL" in text

    checked = _e2e_runner.invoke(app, ["check-env", "--auto-ext-root", str(root)])
    assert "Profile health: hn001" in checked.output
    assert "Env resolution" in checked.output


# ---- seam 2: profile + recipe + cells -> rendered files ----------------------


def test_e2e_a_run_renders_every_target_into_its_own_run_directory(
    _e2e_runner: CliRunner,
    _e2e_pdk: dict[str, Path],
    _e2e_workarea: Path,
    _e2e_patch_run_subprocess: list[dict[str, Any]],
    tmp_path: Path,
) -> None:
    """The middle of the walk: four files in, tool inputs out, nothing spawned.

    Two assertions carry the seam. **No placeholder survives** -- neither an
    unresolved ``${VAR}`` nor an unrendered ``[[jinja]]`` -- which is the whole
    point of resolving env before Jinja rather than hoping. And the files land
    under ``runs/<id>/rendered/``, not in the workarea, so a second run of the
    same cell cannot overwrite the evidence of the first.
    """

    root = _write_project(tmp_path / "proj", pdk=_e2e_pdk)
    result = _e2e_runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(root / "config"),
            "--auto-ext-root", str(root),
            "--workarea", str(_e2e_workarea),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output

    rendered = _rendered(root)
    assert {"si.env", "lvs.qci", "ext.cmd"} <= set(rendered)
    for name, text in rendered.items():
        assert "${" not in text, f"{name} kept an unresolved env reference"
        assert "$env(" not in text, f"{name} kept an unresolved env reference"
        assert "[[" not in text, f"{name} kept an unrendered Jinja slot"

    # dry-run means dry: the net caught nothing.
    assert _e2e_patch_run_subprocess == []


# ---- seam 3: one Recipe, two technologies ------------------------------------


def test_e2e_one_recipe_two_pdks_differ_only_where_the_pdk_binds(
    _e2e_runner: CliRunner,
    _e2e_workarea: Path,
    tmp_path: Path,
) -> None:
    """The core promise of the whole object model, end to end.

    ``test_init_project_cross_project_abstraction`` made this claim about two
    generated ``project.yaml`` files. It is a stronger claim here, and a more
    useful one: take **one** Recipe, bind it to two PDK trees, and the rendered
    tool inputs must differ only on the lines that name process facts. Every
    extraction setting -- the corner *name*, the temperature, the coupling
    thresholds, the netlist format -- has to survive the move byte for byte,
    because that is what "portable recipe" means.

    The two trees differ in every path, in the tech name and in the deck
    version; the Recipe is copied, not re-authored. (The corner *literal* is
    held equal here on purpose -- see the next test for why that one axis
    cannot move yet.)
    """

    from auto_ext.core.profile_discover import write_profile_yaml

    rendered: list[dict[str, str]] = []
    for index, (profile_id, tech_name) in enumerate(
        [("hn001", "HN001"), ("cf028", "CF028")]
    ):
        pdk = make_pdk_tree(tmp_path / f"pdk{index}")
        root = _write_project(tmp_path / f"proj{index}", profile_id=profile_id)
        profile = make_healthy_profile(
            pdk, profile_id=profile_id, tech_name=tech_name
        )
        write_profile_yaml(root / "config" / "profiles" / f"{profile_id}.yaml", profile)

        result = _e2e_runner.invoke(
            app,
            [
                "run",
                "--config-dir", str(root / "config"),
                "--auto-ext-root", str(root),
                "--workarea", str(_e2e_workarea),
                "--recipe", "rc-coupled-typical",
                "--no-health-check",
                "--no-progress",
                "--dry-run",
            ],
        )
        assert result.exit_code == 0, result.output
        rendered.append(_rendered(root))

    first, second = rendered
    assert set(first) == set(second)

    saw_a_difference = False
    for name in first:
        a = first[name].splitlines()
        b = second[name].splitlines()
        assert len(a) == len(b), f"{name}: the two PDKs produced different shapes"
        for left, right in zip(a, b, strict=True):
            if left == right:
                continue
            saw_a_difference = True
            # Every difference has to be traceable to a process fact: a deck
            # path, the layer map, the tech name, the run id.
            assert any(
                token in left or token in right
                for token in ("pdk0", "pdk1", "proj0", "proj1", "HN001", "CF028")
            ), f"{name}: {left!r} vs {right!r} is not a PDK-bound line"
    assert saw_a_difference, "the two profiles were not actually different"

    # ...and the settings the recipe owns are identical in both. (The Quantus
    # command file wraps its arguments, so the value is on its own line.)
    assert "-temperature" in first["ext.cmd"]
    assert "55.0" in first["ext.cmd"]
    assert "-technology_corner" in first["ext.cmd"]


def test_e2e_a_corner_the_template_freezes_is_refused_not_mis_rendered(
    _e2e_runner: CliRunner,
    _e2e_pdk: dict[str, Path],
    _e2e_workarea: Path,
    tmp_path: Path,
) -> None:
    """The one axis portability does not reach yet, pinned as a fact.

    ``templates/quantus/ext.cmd.j2`` still writes ``-technology_corner
    "TYPICAL"`` as a literal, so a PDK that calls its typical corner anything
    else cannot be rendered for. The design's answer is not to guess: the
    render refuses, names the row and the file, and writes nothing -- because
    the alternative is an extraction run against the wrong corner that looks
    exactly like a successful one.

    When the catalog parameterises that row, this test fails and the previous
    one grows the corner back into its difference list.
    """

    from auto_ext.core.profile_discover import write_profile_yaml
    from auto_ext.model.pdk import CornerSpec

    root = _write_project(tmp_path / "proj")
    write_profile_yaml(
        root / "config" / "profiles" / "hn001.yaml",
        make_healthy_profile(
            _e2e_pdk,
            corners=[
                CornerSpec(
                    name="typical",
                    technology_corner="NOM_28",
                    default_temperature_c=55.0,
                )
            ],
        ),
    )

    result = _e2e_runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(root / "config"),
            "--auto-ext-root", str(root),
            "--workarea", str(_e2e_workarea),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0, result.output
    assert "hardcode" in result.output
    assert "NOM_28" in result.output
    assert "TYPICAL" in result.output
    # The stage that would have carried the wrong corner wrote nothing. The
    # stages before it did render -- refusal is per target, and a file that
    # never mentions the corner is not made wrong by one that does.
    names = {p.name for p in (root / "runs").glob("*/rendered/*")}
    assert "ext.cmd" not in names
    assert names <= {"si.env", "lvs.qci"}


# ---- seam 4: the dspf path the run records vs. the one the GUI previews ------


def test_e2e_dspf_path_the_run_records_is_the_one_the_helper_computes(
    _e2e_runner: CliRunner,
    _e2e_pdk: dict[str, Path],
    _e2e_workarea: Path,
    tmp_path: Path,
) -> None:
    """The original bug class: two code paths, one path string, no agreement.

    ``resolve_dspf_path`` is what the GUI shows in its preview label; the
    runner resolves the same pattern on its way to ``run.json``. They were two
    implementations once and disagreed on a real config while both unit test
    suites stayed green. They are one function now -- and this is the test that
    keeps them one, by comparing what the *run* wrote with what the *helper*
    returns for the same inputs.
    """

    from auto_ext.core.runner import resolve_dspf_path

    root = _write_project(tmp_path / "proj", pdk=_e2e_pdk)
    result = _e2e_runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(root / "config"),
            "--auto-ext-root", str(root),
            "--workarea", str(_e2e_workarea),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output

    run_dir = next(p for p in (root / "runs").iterdir() if (p / "run.json").is_file())
    record = read_record(run_dir)

    workspace_pattern = make_workspace().dspf_out_pattern
    previewed, error = resolve_dspf_path(
        workspace_pattern,
        {"WORK_ROOT2": _e2e_pdk["root"].as_posix()},
        cell=record.dut.cell,
        library=record.dut.library,
        task_id=record.dut_label,
    )
    assert error is None, error
    assert record.recipe.dspf_out_path is not None
    assert previewed.endswith(f"{record.dut.cell}.dspf")
    assert record.dspf_path == previewed


# ---- seam 5: a hole in the environment stops the run -------------------------


def test_e2e_a_missing_env_var_stops_the_run_before_a_file_is_written(
    _e2e_runner: CliRunner,
    _e2e_pdk: dict[str, Path],
    _e2e_workarea: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``strict_env``, observed from outside: refuse, do not render a stub.

    A tool input carrying a literal ``${WORK_ROOT}`` is worse than no file --
    Quantus will read it, resolve nothing, and write results somewhere nobody
    looks. So the run has to stop, name the variable, and leave the run
    directory without a rendered file.
    """

    from auto_ext.core.profile_discover import write_profile_yaml

    root = _write_project(tmp_path / "proj", pdk=_e2e_pdk)
    # Take one binding away and put nothing in the shell to replace it.
    # ``PDK_LAYER_MAP_FILE`` is the one behind ``layer_map``, which strmout
    # and Quantus both read, so nothing downstream can paper over it.
    profile = make_healthy_profile(_e2e_pdk)
    stripped = {
        name: value
        for name, value in profile.env_overrides.items()
        if name != "PDK_LAYER_MAP_FILE"
    }
    write_profile_yaml(
        root / "config" / "profiles" / "hn001.yaml",
        profile.model_copy(update={"env_overrides": stripped}),
    )
    monkeypatch.delenv("PDK_LAYER_MAP_FILE", raising=False)

    result = _e2e_runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(root / "config"),
            "--auto-ext-root", str(root),
            "--workarea", str(_e2e_workarea),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0, result.output
    assert "PDK_LAYER_MAP_FILE" in result.output
    assert list((root / "runs").glob("*/rendered/*")) == []


# ---- seam 6: the run -> the record -> the reader -----------------------------


def test_e2e_run_then_runs_show_describes_the_same_run(
    _e2e_runner: CliRunner,
    _e2e_pdk: dict[str, Path],
    _e2e_workarea: Path,
    tmp_path: Path,
) -> None:
    """The archive seam: what the runner wrote is what the reader renders.

    ``run.json`` is the only thing that outlives the process, so a field the
    writer sets and the reader ignores is a field that does not exist. Walking
    ``run`` -> ``runs list`` -> ``runs show`` is the cheapest way to notice.
    """

    root = _write_project(tmp_path / "proj", pdk=_e2e_pdk)
    result = _e2e_runner.invoke(
        app,
        [
            "run",
            "--config-dir", str(root / "config"),
            "--auto-ext-root", str(root),
            "--workarea", str(_e2e_workarea),
            "--recipe", "rc-coupled-typical",
            "--no-health-check",
            "--no-progress",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output

    run_dir = next(p for p in (root / "runs").iterdir() if (p / "run.json").is_file())
    record = read_record(run_dir)

    listed = _e2e_runner.invoke(app, ["runs", "list", "--auto-ext-root", str(root)])
    assert listed.exit_code == 0, listed.output
    assert record.dut.cell in listed.output

    shown = _e2e_runner.invoke(
        app, ["runs", "show", record.run_id, "--auto-ext-root", str(root)]
    )
    assert shown.exit_code == 0, shown.output
    assert record.dut.library in shown.output
    assert record.dut.cell in shown.output
    assert "Stages" in shown.output


@pytest.fixture(autouse=True)
def _e2e_no_writes_outside_tmp(tmp_path: Path) -> Iterator[None]:
    """Every fixture above roots its paths in ``tmp_path``; this says so.

    A guard rather than a test: an e2e file that walks real CLI commands is
    the one most likely to grow a path that escapes the sandbox, and the
    symptom (files in the developer's checkout) is easy to miss.
    """

    before = {p for p in Path.cwd().iterdir()}
    yield
    after = {p for p in Path.cwd().iterdir()}
    assert after == before, f"the e2e walk wrote into the cwd: {sorted(after - before)}"
