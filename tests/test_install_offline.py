"""``scripts/install_offline.sh``: the wheels go into ``<install>/_vendor``.

The script's job is a transaction: build ``_vendor.new``, check it, swap it in,
and never leave ``_vendor`` half-built -- not when pip fails, not when the smoke
test fails, not when an earlier run died mid-swap, and not when NFS refuses to
delete the old tree because a running ``./run.sh gui`` still has its ``.so``
files mapped.

Real wheels cannot be installed here (they are manylinux), and the transaction
is the interesting part anyway, so the interpreter is a stand-in: a shell
script that answers each of the script's calls and, for
``pip install --target X``, creates X with exactly the litter a real pip leaves
(a ``direct_url.json`` holding an absolute path, setuptools'
``distutils-precedence.pth``). The script under test is the real one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "scripts" / "install_offline.sh"
MARKER = "AUTO_EXT_VENDOR.txt"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="no bash on this machine")

#: Answers every call install_offline.sh makes. ``pip install --target X``
#: builds X; FAKE_PIP_FAIL / FAKE_SMOKE_RC / FAKE_EXTRA_PTH steer the failure
#: and warning paths. Anything unexpected exits 99 so a new call cannot pass
#: silently.
FAKE_PYTHON = r"""#!/bin/sh
log() { if [ -n "${FAKE_LOG:-}" ]; then echo "$*" >> "$FAKE_LOG"; fi; }
if [ "$1" = "--version" ]; then echo "Python 3.11.9"; exit 0; fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  shift 2
  case "$1" in
    --version) echo "pip 23.2.1 from /fake (python 3.11)"; exit 0 ;;
    uninstall|list) exit 0 ;;
    install)
      log "PIP_USER=${PIP_USER-<unset>}"
      target=""
      while [ $# -gt 0 ]; do
        if [ "$1" = "--target" ]; then target="$2"; shift; fi
        shift
      done
      if [ -n "${FAKE_PIP_FAIL:-}" ]; then echo "ERROR: fake pip failure" >&2; exit 1; fi
      if [ -z "$target" ]; then echo "fake pip: no --target" >&2; exit 3; fi
      mkdir -p "$target/jinja2" "$target/jinja2-3.1.6.dist-info" "$target/bin"
      echo "# ${FAKE_BUILD:-new}" > "$target/jinja2/__init__.py"
      echo '{"url": "file:///abs/install/dir/wheels/jinja2.whl"}' \
        > "$target/jinja2-3.1.6.dist-info/direct_url.json"
      echo "Name: jinja2" > "$target/jinja2-3.1.6.dist-info/METADATA"
      echo "import os; var = 'SETUPTOOLS_USE_DISTUTILS'" > "$target/distutils-precedence.pth"
      if [ -n "${FAKE_EXTRA_PTH:-}" ]; then echo "import os" > "$target/$FAKE_EXTRA_PTH"; fi
      exit 0 ;;
  esac
  exit 0
fi
if [ "$1" = "-s" ] && [ "$2" = "-" ]; then cat > /dev/null; exit "${FAKE_SMOKE_RC:-0}"; fi
if [ "$1" = "-s" ] && [ "$2" = "-c" ]; then echo "auto_ext core import OK"; exit 0; fi
if [ "$1" = "-" ]; then cat > /dev/null; exit 0; fi
if [ "$1" = "-c" ]; then
  case "$2" in
    *version_info*) echo "3.11"; exit 0 ;;
    *hashlib*) echo "0123abcd"; exit 0 ;;
    *PyQt5*) echo "No module named PyQt5" >&2; exit 1 ;;
  esac
fi
echo "fake python: unexpected argv: $*" >&2
exit 99
"""

#: NFS with a running ``./run.sh gui``: deleting the outgoing tree fails with
#: "Directory not empty" (.nfsXXXX placeholders for the mapped .so files).
RM_THAT_CANNOT_DELETE_VENDOR_OLD = r"""#!/bin/sh
for a in "$@"; do
  case "$a" in
    *_vendor.old) echo "rm: cannot remove '$a': Directory not empty" >&2; exit 1 ;;
  esac
done
exec /bin/rm "$@"
"""


def _script(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A project dir with the real installer, a MANIFEST and two wheels."""

    root = tmp_path / "proj"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(INSTALLER, root / "scripts" / "install_offline.sh")
    (root / "wheels").mkdir()
    (root / "wheels" / "MANIFEST.txt").write_text(
        "# Auto_ext wheel bundle manifest\n# python_target: cp311\n", encoding="utf-8", newline="\n"
    )
    for name in ("jinja2-3.1.6-py3-none-any.whl", "rich-15.0.0-py3-none-any.whl"):
        (root / "wheels" / name).write_bytes(b"PK\x03\x04 pretend")
    _script(tmp_path / "fakepy", FAKE_PYTHON)
    return root


def _install(proj: Path, *, path_prefix: Path | None = None, **env: str) -> subprocess.CompletedProcess:
    tmp = proj.parent
    full = {
        "PATH": (f"{path_prefix.as_posix()}:" if path_prefix else "") + "/usr/bin:/bin",
        "PYTHON": (tmp / "fakepy").as_posix(),
        "FAKE_LOG": (tmp / "fake.log").as_posix(),
        "TMPDIR": tmp.as_posix(),
        **env,
    }
    return subprocess.run(
        ["bash", "scripts/install_offline.sh"],
        cwd=proj,
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _ok(proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _leftovers(proj: Path) -> list[str]:
    return sorted(p.name for p in proj.iterdir() if p.name.startswith("_vendor."))


# ---- the happy path ---------------------------------------------------------------


def test_a_clean_install_builds_vendor_and_swaps_it_in(proj: Path) -> None:
    proc = _install(proj)
    _ok(proc)
    vendor = proj / "_vendor"
    assert (vendor / "jinja2" / "__init__.py").is_file()
    marker = (vendor / MARKER).read_text(encoding="utf-8")
    assert "python_version: 3.11" in marker
    assert "wheel_count: 2" in marker
    assert "manifest_sha256: 0123abcd" in marker
    assert _leftovers(proj) == [], "no _vendor.new / _vendor.old after success"
    assert "swapped" in proc.stdout


def test_the_python_override_is_honoured(proj: Path) -> None:
    """PYTHON= used to be wiped before it was read; on the server the PATH
    search then picked whatever python3 came first."""

    proc = _install(proj)
    _ok(proc)
    fake = (proj.parent / "fakepy").as_posix()
    assert f"using interpreter: {fake}" in proc.stdout


def test_pip_runs_with_pip_user_off(proj: Path) -> None:
    """A site pip.conf / PIP_USER=1 forcing --user makes pip refuse --target."""

    _ok(_install(proj, PIP_USER="1"))
    log = (proj.parent / "fake.log").read_text(encoding="utf-8")
    assert "PIP_USER=0" in log


def test_pth_and_direct_url_litter_is_removed(proj: Path) -> None:
    """``.pth`` files are not processed in a PYTHONPATH entry, and
    ``direct_url.json`` records the install dir's absolute path."""

    _ok(_install(proj))
    vendor = proj / "_vendor"
    assert not list(vendor.glob("*.pth"))
    assert not list(vendor.glob("*.dist-info/direct_url.json"))
    assert (vendor / "jinja2-3.1.6.dist-info" / "METADATA").is_file(), "only the litter goes"


def test_an_unknown_pth_is_warned_about(proj: Path) -> None:
    proc = _install(proj, FAKE_EXTRA_PTH="some-hook.pth")
    _ok(proc)
    assert "some-hook.pth" in proc.stderr
    assert "will NOT be processed" in proc.stderr


def test_a_rerun_is_a_clean_rebuild(proj: Path) -> None:
    _ok(_install(proj))
    (proj / "_vendor" / "STALE_FROM_LAST_TIME").write_text("x", encoding="utf-8")
    _ok(_install(proj, FAKE_BUILD="second"))
    assert not (proj / "_vendor" / "STALE_FROM_LAST_TIME").exists()
    assert (proj / "_vendor" / "jinja2" / "__init__.py").read_text(encoding="utf-8") == "# second\n"
    assert _leftovers(proj) == []


# ---- failures leave _vendor exactly as it was -----------------------------------


@pytest.mark.parametrize(
    "failure", [{"FAKE_PIP_FAIL": "1"}, {"FAKE_SMOKE_RC": "1"}], ids=["pip", "smoke-test"]
)
def test_a_failed_install_leaves_vendor_byte_identical(proj: Path, failure: dict[str, str]) -> None:
    _ok(_install(proj))
    before = _snapshot(proj / "_vendor")
    proc = _install(proj, FAKE_BUILD="never-installed", **failure)
    assert proc.returncode != 0
    assert _snapshot(proj / "_vendor") == before
    assert not (proj / "_vendor.old").exists()


# ---- recovering from an interrupted earlier run ----------------------------------


def test_only_vendor_old_with_its_marker_is_restored_first(proj: Path) -> None:
    """A run killed between the two renames leaves only _vendor.old: the last
    good install, restored before anything else -- so even if THIS run fails,
    run.sh works."""

    _ok(_install(proj))
    (proj / "_vendor").rename(proj / "_vendor.old")
    proc = _install(proj, FAKE_PIP_FAIL="1")
    assert proc.returncode != 0
    assert "restoring _vendor/" in proc.stdout
    assert (proj / "_vendor" / MARKER).is_file()


def test_a_vendor_old_without_its_marker_is_never_restored(proj: Path) -> None:
    """retire_dir deletes the marker first, so a markerless _vendor.old may be
    half deleted. It must not come back as the live tree."""

    _ok(_install(proj))
    (proj / "_vendor").rename(proj / "_vendor.old")
    (proj / "_vendor.old" / MARKER).unlink()
    proc = _install(proj, FAKE_PIP_FAIL="1")
    assert proc.returncode != 0
    assert "restoring" not in proc.stdout
    assert not (proj / "_vendor").exists()
    assert not (proj / "_vendor.old").exists()


def test_both_present_keeps_vendor_and_discards_the_old_one(proj: Path) -> None:
    _ok(_install(proj))
    shutil.copytree(proj / "_vendor", proj / "_vendor.old")
    (proj / "_vendor.new").mkdir()
    (proj / "_vendor.new" / "half-built").write_text("x", encoding="utf-8")
    proc = _install(proj, FAKE_BUILD="after")
    _ok(proc)
    assert "restoring" not in proc.stdout
    assert (proj / "_vendor" / "jinja2" / "__init__.py").read_text(encoding="utf-8") == "# after\n"
    assert _leftovers(proj) == []


# ---- NFS: the old tree cannot be deleted yet --------------------------------------


def test_an_undeletable_vendor_old_does_not_fail_a_finished_install(proj: Path) -> None:
    """The swap has happened; failing to clean up must not turn it into exit 1,
    and must not leave a _vendor.old that kills every later run."""

    _ok(_install(proj))
    shims = proj.parent / "shims"
    shims.mkdir()
    _script(shims / "rm", RM_THAT_CANNOT_DELETE_VENDOR_OLD)

    proc = _install(proj, path_prefix=shims, FAKE_BUILD="swapped-in")
    _ok(proc)
    assert (proj / "_vendor" / "jinja2" / "__init__.py").read_text(encoding="utf-8") == "# swapped-in\n"
    assert "moved it aside" in proc.stderr
    asides = _leftovers(proj)
    assert len(asides) == 1 and asides[0].startswith("_vendor.old."), asides
    assert not (proj / asides[0] / MARKER).exists(), "an aside never looks restorable"

    # ...and the next ordinary run (NFS let go) cleans the aside up.
    _ok(_install(proj))
    assert _leftovers(proj) == []


def test_the_hints_quote_their_paths(proj: Path) -> None:
    proc = _install(proj)
    _ok(proc)
    line = next(ln for ln in proc.stdout.splitlines() if "env PYTHONPATH=" in ln)
    assert 'PYTHONPATH="' in line
    assert '-m auto_ext' in line
