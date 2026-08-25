"""The part of the yellow -> red delivery chain that a Windows box can judge.

There is no Linux here and no red zone, so this file does not pretend to test a
deployment. It tests the five things that can be decided locally -- and each of
them is a failure that would otherwise surface in the one place with no
debugger, no network and no git:

1. **Line endings.** Every shell script the far side executes comes out of
   ``git archive`` with zero ``\\r``. CRLF makes bash die with
   ``$'\\r': command not found``.
2. **Package boundary.** What must cross the gap does, what must not does not.
   This is the regression that motivated replacing the old packer: it kept a
   hand-written include list and had silently stopped shipping ``recipes/``,
   a directory added months after the list was written.
3. **Tier decision.** ``deploy/_env_check.py``'s ``decide_tier`` is a pure
   function; it is driven with a hand-written truth table, plus negatives that
   prove removing one dependency DROPS the tier rather than scoring full marks.
4. **The probe survives an ancient interpreter.** Bare ``python`` on the target
   is 2.7, so the probe must parse there and report itself unusable instead of
   dying with a SyntaxError that reads like a corrupt package.
5. **The swap does not eat data**, end to end: a real ``bash deploy.sh`` run
   against a synthetic install.

Why the archive is taken from a WORKING-TREE tree and not from ``HEAD``: the
run that introduces a CRLF file is exactly the run where ``HEAD`` does not have
it yet, so a HEAD-based gate would skip politely at the only moment it matters.
A temporary index (``GIT_INDEX_FILE`` + ``git add -A`` + ``git write-tree``)
produces a tree containing the working tree; the check-in conversion and
``.gitattributes``' ``export-ignore`` apply exactly as they would to a real
package, but the gate has teeth the moment a file is written.
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "deploy" / "_env_check.py"
DEPLOY_SH = REPO / "deploy.sh"
DOCTOR_SH = REPO / "deploy" / "doctor.sh"
PACK_PS1 = REPO / "deploy" / "pack.ps1"

# Everything Linux executes. `run.sh` and the mocks are in the list because the
# shipped test suite runs them: a CRLF mock fails `doctor.sh --test` on a
# perfectly good install, which reads as "the package did not land".
SHIPPED_SHELL = (
    "deploy.sh",
    "deploy/doctor.sh",
    "run.sh",
    "scripts/install_offline.sh",
    "tests/mocks/_common.sh",
    "tests/mocks/calibre",
    "tests/mocks/jivaro",
    "tests/mocks/qrc",
    "tests/mocks/si",
    "tests/mocks/strmout",
)


# ---------------------------------------------------------------------------
# the archive, built once
# ---------------------------------------------------------------------------


def _require_checkout() -> None:
    """Skip when running from a DEPLOYED package rather than a git checkout.

    This suite ships across the gap and `deploy/doctor.sh --test` runs it there,
    where a green result is the strongest evidence obtainable on a box with no
    network. So every test that needs the development context has to SKIP, not
    fail -- a red suite over there means "the package did not land", and that
    signal is worthless if it also fires for "this file is dev-only and was
    correctly left out".

    `.gitattributes` is the probe: it is itself export-ignored, so it exists in
    a checkout and never in a package. (Rehearsing a real deploy is what found
    this: three tests failed on a perfectly good install.)
    """

    if not (REPO / ".gitattributes").is_file():
        pytest.skip("running from a deployed package, not a git checkout -- dev-only check")
    if shutil.which("git") is None:
        pytest.skip("platform skip: git not on PATH")


def _git(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    if env:
        full.update(env)
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO),
        env=full,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )


@pytest.fixture(scope="module")
def archive() -> dict[str, bytes]:
    """``{path inside the package: raw bytes}`` for the working tree."""

    _require_checkout()
    with tempfile.TemporaryDirectory(prefix="autoext_pack_") as tmp:
        env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
        added = _git(["add", "-A", "--", "."], env=env)
        if added.returncode != 0:
            pytest.skip("git add failed: " + added.stderr.decode("utf-8", "replace")[-300:])
        tree = _git(["write-tree"], env=env)
        if tree.returncode != 0:
            pytest.skip("git write-tree failed: " + tree.stderr.decode("utf-8", "replace")[-300:])
        oid = tree.stdout.decode().strip()
        packed = _git(["archive", "--format=tar", "--prefix=pkg/", oid], env=env)
        if packed.returncode != 0:
            pytest.skip("git archive failed: " + packed.stderr.decode("utf-8", "replace")[-300:])

    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(packed.stdout)) as tar:
        for info in tar.getmembers():
            if not info.isfile():
                continue
            handle = tar.extractfile(info)
            if handle is None:
                continue
            members[info.name[len("pkg/") :]] = handle.read()
    return members


# ---------------------------------------------------------------------------
# 1. line endings
# ---------------------------------------------------------------------------


def test_shipped_shell_scripts_have_zero_cr(archive: dict[str, bytes]) -> None:
    for name in SHIPPED_SHELL:
        assert name in archive, f"{name} is not in the package at all"
        assert b"\r" not in archive[name], (
            f"{name} would reach the red zone with CRLF; bash dies on it. "
            f"Fix: git add --renormalize {name}"
        )


def test_the_cr_check_can_actually_fail(archive: dict[str, bytes]) -> None:
    """Negative control: the assertion above is not vacuously true.

    A byte scan that never sees a CR would pass just as happily against an empty
    dict, so prove the predicate distinguishes the two cases.
    """

    assert b"\r" in archive["deploy.sh"].replace(b"\n", b"\r\n")


# ---------------------------------------------------------------------------
# 2. package boundary
# ---------------------------------------------------------------------------


MUST_SHIP = (
    "auto_ext/core/runner.py",
    "auto_ext/cli.py",
    "deploy.sh",
    "deploy/doctor.sh",
    "deploy/_env_check.py",
    "run.sh",
    "scripts/install_offline.sh",
    "pyproject.toml",
    "VERSION",
    "docs/refactor/OFFICE_TODO.md",
    # The regression this whole rewrite exists for: recipes/ is a top-level
    # directory added long after the previous packer's include list was written,
    # and it silently stopped crossing the gap.
    "recipes/rc-typical-55c.yaml",
    "config/workspace.yaml",
)

MUST_NOT_SHIP = (
    ".gitignore",
    ".gitattributes",
    ".redzone_patterns.example",
    # builds the package, so it cannot be in it
    "deploy/pack.ps1",
    "deploy/pack.bat",
    # dev-side commit gate: needs git, which the red zone does not have, so
    # shipping it means shipping a script that can only ever exit 1
    "scripts/redzone_scan.sh",
    "scripts/install_hooks.sh",
    # needs PyPI, which the red zone cannot reach
    "scripts/download_wheels.py",
)


def test_package_ships_what_the_red_zone_needs(archive: dict[str, bytes]) -> None:
    for name in MUST_SHIP:
        assert name in archive, f"{name} does not cross the gap"


def test_package_omits_yellow_zone_only_material(archive: dict[str, bytes]) -> None:
    for name in MUST_NOT_SHIP:
        assert name not in archive, f"{name} should be export-ignored but ships"


def test_package_omits_whole_directories(archive: dict[str, bytes]) -> None:
    """`docs/archive/` and the design canvas are export-ignored as directories."""

    for prefix in ("docs/archive/", "docs/refactor/design/"):
        offenders = [n for n in archive if n.startswith(prefix)]
        assert not offenders, f"{prefix} should be export-ignored, but ships: {offenders[:3]}"


def test_the_test_suite_itself_ships(archive: dict[str, bytes]) -> None:
    """`doctor.sh --test` runs the shipped suite; on a box with no network a
    green suite is the strongest evidence obtainable, so the tests must travel."""

    assert "tests/test_deploy.py" in archive
    assert any(n.startswith("tests/mocks/") for n in archive)


def test_wheels_never_enter_the_code_package(archive: dict[str, bytes]) -> None:
    """wheels/ is gitignored, so `git archive` structurally cannot ship it.

    That is the intent, not an oversight: ~40 MB of wheels change twice a year
    while the code changes daily. They cross the gap as their own upload.
    """

    assert not [n for n in archive if n.startswith("wheels/")]


def test_version_is_stamped_by_git_archive() -> None:
    """VERSION carries $Format:...$ in the tree and a real hash in a package."""

    _require_checkout()
    assert "$Format:%H$" in (REPO / "VERSION").read_text(encoding="utf-8")
    packed = _git(["archive", "--format=tar", "--prefix=pkg/", "HEAD", "--", "VERSION"])
    if packed.returncode != 0:
        pytest.skip("VERSION is not committed yet")
    with tarfile.open(fileobj=io.BytesIO(packed.stdout)) as tar:
        handle = tar.extractfile("pkg/VERSION")
        assert handle is not None
        stamped = handle.read().decode("utf-8")
    assert "$Format:" not in stamped.split("The $Format:")[0], (
        "export-subst did not fire; the red zone cannot tell which build is installed"
    )
    assert len([c for c in stamped.split() if len(c) == 40 and all(x in "0123456789abcdef" for x in c)]) == 1


def test_pack_and_deploy_agree_on_the_sentinel() -> None:
    """Both sides check the same file, or a good package gets rejected."""

    if not PACK_PS1.is_file():
        pytest.skip("deploy/pack.ps1 is export-ignored; absent from a package, as intended")
    pack = PACK_PS1.read_text(encoding="utf-8")
    deploy = DEPLOY_SH.read_text(encoding="utf-8")
    pack_sentinel = pack.split("$Sentinel = '", 1)[1].split("'", 1)[0]
    deploy_sentinel = deploy.split('SENTINEL="', 1)[1].split('"', 1)[0]
    assert pack_sentinel == deploy_sentinel
    assert (REPO / pack_sentinel).is_file(), "the agreed sentinel does not exist"


# ---------------------------------------------------------------------------
# 3. tier decision
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def probe():
    """Import deploy/_env_check.py directly (it is not part of the package)."""

    import importlib.util

    spec = importlib.util.spec_from_file_location("_env_check", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _all_deps(probe, present: bool = True) -> dict[str, bool]:
    return {name: present for name, _ in probe.DEPS}


def _all_tools(probe, present: bool = True) -> dict[str, str]:
    found = {}
    for name in probe.TOOLS_FOR_RUN + probe.TOOLS_OPTIONAL:
        found[name] = "/usr/bin/" + name if present else ""
    return found


def test_tier_table(probe) -> None:
    deps = _all_deps(probe)
    tools = _all_tools(probe)
    # (py_ok, deps, core_ok, tools, qt_ok, display_ok) -> tier
    table = [
        ((False, deps, True, tools, True, True), 0, "old interpreter"),
        ((True, _all_deps(probe, False), True, tools, True, True), 0, "no wheels installed"),
        ((True, deps, False, tools, True, True), 0, "package does not import"),
        ((True, deps, True, _all_tools(probe, False), True, True), 1, "no EDA tools"),
        ((True, deps, True, tools, False, True), 2, "no PyQt5"),
        ((True, deps, True, tools, True, False), 2, "headless"),
        ((True, deps, True, tools, True, True), 3, "everything"),
    ]
    for args, want, why in table:
        assert probe.decide_tier(*args) == want, why


def test_dropping_one_dependency_drops_the_tier(probe) -> None:
    """Negative control for the table above: a full-marks report must be earned.

    Removing any single tier-1 dependency has to knock the verdict down to 0 --
    otherwise the table is only proving that the function returns 3.
    """

    tools = _all_tools(probe)
    for name, _human in probe.DEPS:
        deps = _all_deps(probe)
        deps[name] = False
        assert probe.decide_tier(True, deps, True, tools, True, True) == 0, name

    for name in probe.TOOLS_FOR_RUN:
        tools_missing = _all_tools(probe)
        tools_missing[name] = ""
        assert probe.decide_tier(True, _all_deps(probe), True, tools_missing, True, True) == 1, name


def test_jivaro_is_reported_but_never_gates(probe) -> None:
    """Reduction is optional per recipe, so a box without jivaro is still tier 3."""

    tools = _all_tools(probe)
    tools["jivaro"] = ""
    assert probe.decide_tier(True, _all_deps(probe), True, tools, True, True) == 3
    assert "jivaro" not in probe.missing_tools(tools)


def test_tier_blocker_names_what_is_missing(probe) -> None:
    deps = _all_deps(probe)
    deps["pydantic"] = False
    msg = probe.tier_blocker(0, True, deps, True, _all_tools(probe), True, True)
    assert "pydantic" in msg
    assert "install_offline.sh" in msg, "say how to fix it, not just what is wrong"

    tools = _all_tools(probe, False)
    msg = probe.tier_blocker(1, True, _all_deps(probe), True, tools, True, True)
    for name in probe.TOOLS_FOR_RUN:
        assert name in msg


def test_min_py_matches_pyproject(probe) -> None:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.strip().startswith("requires-python"))
    declared = line.split(">=", 1)[1].strip().strip('"').strip("'")
    assert "%d.%d" % probe.MIN_PY == declared


# ---------------------------------------------------------------------------
# 4. the probe on an ancient interpreter
# ---------------------------------------------------------------------------


def test_probe_uses_no_syntax_newer_than_python_2(probe) -> None:
    """No f-strings, no annotations, no walrus.

    Bare `python` on the target is 2.7 (it comes from an OpenOffice install) and
    the doctor probes it. It must report "need >= 3.11", not die with a
    SyntaxError -- which would look exactly like a corrupt package.
    """

    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(node, ast.JoinedStr), "f-string at line %d" % node.lineno
        assert not isinstance(node, ast.AnnAssign), "annotation at line %d" % node.lineno
        assert not isinstance(node, ast.NamedExpr), "walrus at line %d" % node.lineno
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.returns is None, "return annotation on %s" % node.name
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                assert arg.annotation is None, "arg annotation on %s" % node.name


def test_probe_output_is_pure_ascii() -> None:
    """LANG is often C over there; one non-ASCII byte makes the probe die with
    UnicodeEncodeError, i.e. a healthy box would look broken."""

    raw = PROBE.read_bytes()
    assert all(b < 128 for b in raw), "deploy/_env_check.py contains non-ASCII bytes"


def test_probe_runs_and_emits_a_tier() -> None:
    out = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    text = out.stdout.decode("ascii")
    assert "TIER=" in text
    assert "PY_OK=YES" in text
    assert "IMP_core_runner=OK" in text, "the probe cannot import the package it ships with"


# ---------------------------------------------------------------------------
# 5. the shell scripts themselves
# ---------------------------------------------------------------------------


def _bash() -> str:
    found = shutil.which("bash")
    if found is None:
        pytest.skip("platform skip: no bash (Git Bash not installed)")
    return found


@pytest.mark.parametrize(
    "script",
    ["deploy.sh", "deploy/doctor.sh", "run.sh", "scripts/install_offline.sh", "scripts/redzone_scan.sh"],
)
def test_shell_scripts_parse(script: str) -> None:
    """A syntax error in deploy.sh is a brick once it is across the gap."""

    if not (REPO / script).is_file():
        # redzone_scan.sh and friends are export-ignored: absent from a deployed
        # package by design, and this suite runs there too.
        pytest.skip(script + " is dev-only and not present here")
    proc = subprocess.run(
        [_bash(), "-n", script],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def test_deploy_and_doctor_declare_bash_not_sh() -> None:
    """Both use bash-isms (arrays, `[[ ]]`, `shopt`). The red-zone login shell is
    tcsh, so they are started with `bash x.sh`; a wrong shebang would hand them
    to dash on some other box and produce unfindable syntax errors."""

    for path in (DEPLOY_SH, DOCTOR_SH):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert "bash" in first, f"{path.name} shebang is not bash: {first}"


def test_deploy_refuses_a_directory_that_is_not_an_install(tmp_path: Path) -> None:
    """No sentinel => not one byte may be touched.

    The cheapest guardrail there is: copy deploy.sh somewhere by accident, run
    it, and it must never open the non-atomic backup+swap window.
    """

    shutil.copy2(DEPLOY_SH, tmp_path / "deploy.sh")
    proc = subprocess.run(
        [_bash(), "deploy.sh"],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode != 0, "ran anyway in a directory that is not an install:\n" + out
    assert "not an Auto_ext install" in out
    assert not (tmp_path / ".deploy").exists(), "it started moving things before refusing"


def test_deploy_refuses_when_no_package_is_present(tmp_path: Path) -> None:
    """Same construction with the sentinel supplied: it must refuse for a
    DIFFERENT reason, proving the refusal is diagnosed rather than blanket."""

    sentinel = _sentinel()
    shutil.copy2(DEPLOY_SH, tmp_path / "deploy.sh")
    (tmp_path / sentinel).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / sentinel).write_text("# stand-in\n", encoding="utf-8")
    proc = subprocess.run(
        [_bash(), "deploy.sh"],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode != 0
    assert "no *.tar.gz found" in out
    assert "not an Auto_ext install" not in out, "sentinel supplied, yet the same complaint"
    assert not (tmp_path / ".deploy").exists()


def test_deploy_refuses_a_wheels_bundle_by_name(tmp_path: Path) -> None:
    """The wheels bundle sits next to the code package and is named similarly.

    Left to run, it would fail three steps later with "staged package missing
    auto_ext/core/runner.py", which reads like a corrupt code package. Caught by
    name it produces the one-line fix instead.
    """

    box = _make_install(tmp_path)
    (box / "Auto_ext_pro_wheels_37.tar.gz").write_bytes(b"not really a tarball")
    proc = subprocess.run(
        [_bash(), "deploy.sh"],
        cwd=str(box),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
    )
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode != 0
    assert "WHEELS bundle" in out
    assert "install_offline.sh" in out, "say what to do with it"


# ---------------------------------------------------------------------------
# 6. the swap, end to end
# ---------------------------------------------------------------------------


def _sentinel() -> str:
    return DEPLOY_SH.read_text(encoding="utf-8").split('SENTINEL="', 1)[1].split('"', 1)[0]


def _make_install(tmp_path: Path) -> Path:
    """A synthetic install that looks like a configured red-zone box."""

    box = tmp_path / "Auto_ext_pro"
    box.mkdir()
    shutil.copy2(DEPLOY_SH, box / "deploy.sh")
    sentinel = box / _sentinel()
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("# old runner\n", encoding="utf-8")
    # a module the new package no longer ships -- the delete-propagation case
    (box / "auto_ext" / "core" / "legacy_gone.py").write_text("# removed upstream\n", encoding="utf-8")
    # the operator's own material
    (box / "wheels").mkdir()
    (box / "wheels" / "pydantic-2.13.3.whl").write_bytes(b"PK\x03\x04 pretend")
    (box / "logs").mkdir()
    (box / "logs" / "si.log").write_text("old log\n", encoding="utf-8")
    (box / "runs" / "20260822T101500_demo").mkdir(parents=True)
    (box / "runs" / "20260822T101500_demo" / "run.json").write_text("{}", encoding="utf-8")
    (box / "config" / "profiles").mkdir(parents=True)
    (box / "config" / "workspace.yaml").write_text("site: real\n", encoding="utf-8")
    (box / "recipes").mkdir()
    (box / "recipes" / "mine.yaml").write_text("corner: RCWORST\n", encoding="utf-8")
    # Shipped code the operator edits in place. Not preservable -- the render
    # path resolves it at run time -- so the package's copy wins and this one
    # has to be archived instead.
    (box / "templates" / "calibre").mkdir(parents=True)
    (box / "templates" / "calibre" / "calibre_lvs.qci.j2").write_text(
        "validated by hand over months\n", encoding="utf-8"
    )
    return box


def _make_package(tmp_path: Path, name: str = "Auto_ext_pro_abc1234.tar.gz") -> Path:
    """A package whose contents differ from the install in every way that matters."""

    staging = tmp_path / "stage" / "Auto_ext_pro"
    (staging / "auto_ext" / "core").mkdir(parents=True)
    (staging / _sentinel()).write_text("# NEW runner\n", encoding="utf-8")
    (staging / "deploy.sh").write_bytes(DEPLOY_SH.read_bytes())
    (staging / "VERSION").write_text("Auto_ext\ncommit   abc1234\n", encoding="utf-8")
    (staging / "config" / "profiles").mkdir(parents=True)
    (staging / "config" / "workspace.yaml").write_text("site: PACKAGE DEFAULT\n", encoding="utf-8")
    (staging / "recipes").mkdir()
    (staging / "recipes" / "rc-typical-55c.yaml").write_text("shipped: true\n", encoding="utf-8")
    (staging / "templates" / "calibre").mkdir(parents=True)
    (staging / "templates" / "calibre" / "calibre_lvs.qci.j2").write_text(
        "shipped default\n", encoding="utf-8"
    )

    tarball = tmp_path / name
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(staging, arcname="Auto_ext_pro")
    return tarball


def _deploy(box: Path, *, path_prefix: Path | None = None) -> subprocess.CompletedProcess:
    env = None
    if path_prefix is not None:
        env = dict(os.environ)
        env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
    return subprocess.run(
        [_bash(), "deploy.sh"],
        cwd=str(box),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
        env=env,
    )


def _frozen_clock(tmp_path: Path, stamp: str = "20260825-101438") -> Path:
    """A directory whose ``date`` always answers ``stamp``.

    The backup directory is named from a second-resolution timestamp, so two
    deploys inside one second collide. Waiting for a real collision would make
    the test a race that only fires on a fast machine -- which is exactly how
    the bug reached the office box unnoticed. Freezing the clock makes the
    collision certain everywhere.
    """

    shim_dir = tmp_path / "frozen_clock"
    shim_dir.mkdir()
    shim = shim_dir / "date"
    shim.write_text(
        "#!/bin/sh\n" f"echo {stamp}\n", encoding="utf-8", newline="\n"
    )
    shim.chmod(0o755)
    return shim_dir


def test_a_deploy_swaps_the_code_and_keeps_every_kind_of_user_data(tmp_path: Path) -> None:
    box = _make_install(tmp_path)
    shutil.copy2(_make_package(tmp_path), box)

    proc = _deploy(box)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out
    assert "OK  deployed." in out

    # the code really was replaced...
    assert (box / _sentinel()).read_text(encoding="utf-8") == "# NEW runner\n"
    # ...and a file the package no longer ships is GONE. This is the whole point
    # of a full swap: the previous overwrite-in-place sync left deleted modules
    # behind, producing a half-old/half-new tree that imports the wrong thing.
    assert not (box / "auto_ext" / "core" / "legacy_gone.py").exists()

    # every kind of user data survived
    assert (box / "wheels" / "pydantic-2.13.3.whl").exists(), "wheels are expensive to re-cross the gap"
    assert (box / "logs" / "si.log").read_text(encoding="utf-8") == "old log\n"
    assert (box / "runs" / "20260822T101500_demo" / "run.json").exists()

    # site configuration wins over the package's starting point...
    assert (box / "config" / "workspace.yaml").read_text(encoding="utf-8") == "site: real\n"
    assert (box / "recipes" / "mine.yaml").exists()
    # ...and the package's copy is parked where it can be diffed, not dropped
    seeded = box / ".deploy" / "seed" / "config" / "workspace.yaml"
    assert seeded.read_text(encoding="utf-8") == "site: PACKAGE DEFAULT\n"
    assert not (box / "recipes" / "rc-typical-55c.yaml").exists()


def test_a_fresh_box_is_seeded_with_the_packages_config(tmp_path: Path) -> None:
    """Negative of the test above: with no config/ of your own, the package's is
    installed -- otherwise a first deploy would leave the box unconfigurable."""

    box = tmp_path / "Auto_ext_pro"
    box.mkdir()
    shutil.copy2(DEPLOY_SH, box / "deploy.sh")
    (box / _sentinel()).parent.mkdir(parents=True, exist_ok=True)
    (box / _sentinel()).write_text("# old\n", encoding="utf-8")
    shutil.copy2(_make_package(tmp_path), box)

    proc = _deploy(box)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out
    assert (box / "config" / "workspace.yaml").read_text(encoding="utf-8") == "site: PACKAGE DEFAULT\n"
    assert (box / "recipes" / "rc-typical-55c.yaml").exists()
    assert "seeded from the package" in out


def test_run_data_under_any_other_name_is_kept_too(tmp_path: Path) -> None:
    """The name list only knows `runs/`. Someone who pointed the runs root
    elsewhere is exactly the person whose results the swap would otherwise move
    into a backup that rotation later deletes."""

    box = _make_install(tmp_path)
    (box / "my_extractions" / "20260101T000000_x").mkdir(parents=True)
    (box / "my_extractions" / "20260101T000000_x" / "run.json").write_text("{}", encoding="utf-8")
    shutil.copy2(_make_package(tmp_path), box)

    proc = _deploy(box)
    assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")
    assert (box / "my_extractions" / "20260101T000000_x" / "run.json").exists()


def test_an_unrelated_directory_is_swept_and_the_warning_comes_last(tmp_path: Path) -> None:
    """Negative control for the rule above, plus the placement of the one warning
    that can mean "your data moved".

    It used to sit mid-report and scrolled past. Judging it by "is the sentence
    present" would not have caught that, so the assertion is about position.
    """

    box = _make_install(tmp_path)
    (box / "scratch_notes").mkdir()
    (box / "scratch_notes" / "todo.txt").write_text("remember\n", encoding="utf-8")
    shutil.copy2(_make_package(tmp_path), box)

    proc = _deploy(box)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out

    assert not (box / "scratch_notes").exists(), "an unknown directory should be swept into the backup"
    backups = list((box / ".deploy" / "backups").iterdir())
    assert len(backups) == 1
    assert (backups[0] / "scratch_notes" / "todo.txt").read_text(encoding="utf-8") == "remember\n"

    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-12:]
    assert any("YOUR OWN FILES WERE MOVED" in ln for ln in tail), (
        "the data-moved warning must be the last thing on screen, not buried mid-report:\n" + out
    )
    assert any("scratch_notes" in ln for ln in tail)


def _keepsakes(box: Path) -> list[Path]:
    root = box / ".deploy" / "yours"
    return sorted(root.iterdir()) if root.is_dir() else []


def test_an_edited_templates_dir_is_archived_before_the_package_overwrites_it(
    tmp_path: Path,
) -> None:
    """templates/ is shipped code the operator edits, so both claims are real.

    The package's copy has to win -- the render path resolves templates at run
    time, and a box left on last release's copy runs last release's flow with
    this release's code. So the outgoing copy is archived instead of kept.
    """

    box = _make_install(tmp_path)
    shutil.copy2(_make_package(tmp_path), box)

    proc = _deploy(box)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out

    installed = box / "templates" / "calibre" / "calibre_lvs.qci.j2"
    assert installed.read_text(encoding="utf-8") == "shipped default\n"

    kept = _keepsakes(box)
    assert len(kept) == 1, out
    assert (kept[0] / "calibre" / "calibre_lvs.qci.j2").read_text(
        encoding="utf-8"
    ) == "validated by hand over months\n"

    assert "YOUR EDITED COPIES WERE REPLACED" in out
    assert kept[0].name in out, "the warning must name the path, not just the fact"


def test_an_untouched_templates_dir_leaves_no_keepsake(tmp_path: Path) -> None:
    """Negative control: an unmodified box must not accumulate copies forever.

    Without this, every deploy on every box would leave another archive that
    nothing ever deletes, and the directory that is supposed to hold the one
    irreplaceable thing fills with noise.
    """

    box = _make_install(tmp_path)
    (box / "templates" / "calibre" / "calibre_lvs.qci.j2").write_text(
        "shipped default\n", encoding="utf-8"
    )
    shutil.copy2(_make_package(tmp_path), box)

    proc = _deploy(box)
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 0, out
    assert _keepsakes(box) == []
    assert "YOUR EDITED COPIES WERE REPLACED" not in out


def test_the_keepsake_outlives_the_backup_that_used_to_be_its_only_home(
    tmp_path: Path,
) -> None:
    """The actual bug: the originals survived on three backup rotations.

    Four deploys is one more than KEEP_BACKUPS, so the backup holding the
    edited templates is gone by the end. The archive must not be.
    """

    box = _make_install(tmp_path)
    shutil.copy2(_make_package(tmp_path), box)

    keep_backups = int(
        DEPLOY_SH.read_text(encoding="utf-8").split("KEEP_BACKUPS=", 1)[1].split("\n", 1)[0]
    )
    for _ in range(keep_backups + 1):
        proc = _deploy(box)
        assert proc.returncode == 0, proc.stdout.decode("utf-8", "replace")

    assert len(list((box / ".deploy" / "backups").iterdir())) == keep_backups

    kept = _keepsakes(box)
    assert len(kept) == 1, "only the first deploy had anything of the operator's to keep"
    assert (kept[0] / "calibre" / "calibre_lvs.qci.j2").read_text(
        encoding="utf-8"
    ) == "validated by hand over months\n"

    # and it really is out of rotation's reach
    surviving = {
        (backup / "templates" / "calibre" / "calibre_lvs.qci.j2").read_text(encoding="utf-8")
        for backup in (box / ".deploy" / "backups").iterdir()
        if (backup / "templates" / "calibre" / "calibre_lvs.qci.j2").is_file()
    }
    assert "validated by hand over months\n" not in surviving


def test_a_corrupt_package_leaves_the_install_untouched(tmp_path: Path) -> None:
    """Checksum mismatch must abort BEFORE the backup+swap window opens."""

    box = _make_install(tmp_path)
    tarball = _make_package(tmp_path)
    shutil.copy2(tarball, box)
    (box / (tarball.name + ".sha256")).write_text(
        "0" * 64 + "  " + tarball.name + "\n", encoding="utf-8"
    )

    proc = _deploy(box)
    out = proc.stdout.decode("utf-8", "replace")
    if "sha256sum not found" in out:
        pytest.skip("platform skip: no sha256sum, so the check cannot run here")
    assert proc.returncode != 0
    assert "checksum FAILED" in out
    assert (box / _sentinel()).read_text(encoding="utf-8") == "# old runner\n", "install was modified"
    assert not (box / ".deploy" / "backups").exists() or not list(
        (box / ".deploy" / "backups").iterdir()
    )


# ---------------------------------------------------------------------------
# 7. the self-test must not go red for environmental reasons
# ---------------------------------------------------------------------------


def test_cli_output_does_not_depend_on_terminal_width() -> None:
    """`doctor.sh --test` must not report a false red on a healthy install.

    It runs the shipped suite with TMPDIR inside the install dir, and the red
    zone's install path is long. Rich wraps at 80 columns when stdout is not a
    terminal, so a CLI test that asserts an absolute path appears in the output
    fails purely because the path got folded -- and the doctor then says "do not
    trust this install". This is a real observed failure, not a precaution:
    it appeared the first time a real package was deployed and self-tested.

    tests/conftest.py pins the width by giving every CliRunner its own COLUMNS.
    This guards that patch, which is invisible at every call site.
    """

    from click.testing import CliRunner

    assert CliRunner().env.get("COLUMNS"), (
        "tests/conftest.py no longer pins the CLI console width; long paths in "
        "CLI assertions will fold again and doctor.sh --test will go red on a "
        "perfectly good red-zone install"
    )


def test_two_deploys_in_the_same_second_get_separate_backups(tmp_path: Path) -> None:
    """The backup name is a second-resolution timestamp, so it can collide.

    Found on real Linux, where four deploys in a row fit inside one second and
    the second one tried to back up INTO the first one's backup. `mv` aborts on
    a non-empty directory -- which is the loud half. The quiet half is worse: a
    plain FILE moves in and overwrites, leaving one backup holding a mix of two
    installs and a rollback that restores neither.
    """

    box = _make_install(tmp_path)
    shutil.copy2(_make_package(tmp_path), box)
    clock = _frozen_clock(tmp_path)

    first = _deploy(box, path_prefix=clock)
    assert first.returncode == 0, first.stdout.decode("utf-8", "replace")
    second = _deploy(box, path_prefix=clock)
    assert second.returncode == 0, second.stdout.decode("utf-8", "replace")

    backups = sorted(p.name for p in (box / ".deploy" / "backups").iterdir())
    assert len(backups) == 2, backups
    # ...and neither is empty, i.e. each really holds its own install
    for name in backups:
        assert (box / ".deploy" / "backups" / name / _sentinel()).is_file(), name


def test_a_frozen_clock_really_would_collide(tmp_path: Path) -> None:
    """Negative control: prove the shim is what makes the timestamps equal.

    Without it this test would pass on a slow machine for the wrong reason --
    two genuinely different seconds -- and go on passing after the uniquifier
    was deleted.
    """

    clock = _frozen_clock(tmp_path, stamp="20260825-101438")
    stamps = {
        subprocess.run(
            [_bash(), "-c", "date +%Y%m%d-%H%M%S"],
            cwd=str(tmp_path),
            env={**os.environ, "PATH": f"{clock}{os.pathsep}{os.environ.get('PATH', '')}"},
            stdout=subprocess.PIPE,
            timeout=60,
        ).stdout.decode().strip()
        for _ in range(2)
    }
    assert stamps == {"20260825-101438"}
