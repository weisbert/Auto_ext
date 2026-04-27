"""Download every runtime (and optional dev) wheel for the Linux server.

Run on the Windows developer box::

    python scripts/download_wheels.py [--include-dev] [--clean]

- Reads ``pyproject.toml`` ``[project] dependencies`` and, if ``--include-dev``
  is passed, ``[project.optional-dependencies].dev``.
- Skips ``PyQt5``: the server already has 5.15.9 installed and the Qt wheel
  is multi-hundred-megabyte so we never bundle it.
- Calls ``pip download`` with a locked-down set of flags so the resolver
  only picks wheels compatible with **Python 3.11 + manylinux2014_x86_64**
  (the server is glibc 2.17 / CentOS 7-class and cannot run newer tags).
- Verifies every artifact in ``wheels/`` ends with ``.whl``; an ``.tar.gz``
  means a dependency has no prebuilt wheel and would force compilation on
  the offline server. The script fails loudly in that case.
- Writes ``wheels/MANIFEST.txt`` with the Python/ABI/platform target, the
  download timestamp, and the SHA256 of every wheel. ``install_offline.sh``
  cross-checks the Python target before installing.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
WHEELS_DIR = PROJECT_ROOT / "wheels"

PY_VERSION = "311"
IMPLEMENTATION = "cp"
ABI = "cp311"
PLATFORM = "manylinux2014_x86_64"

# Dependencies that must NOT be downloaded (already present on server).
# Compared case-insensitively against PEP 503 normalized names.
SKIP_PACKAGES = {"pyqt5"}

# PEP 517 build-system requirements from pyproject.toml must also be offline.
# ``pip install -e .`` triggers an isolated build env on the server; if
# setuptools/wheel aren't in the bundle, the install fails with
# "ERROR: Could not find a version that satisfies the requirement setuptools".
# We download them as pure-Python wheels alongside the project deps.
BUILD_REQUIREMENTS: list[str] = ["setuptools>=68", "wheel"]

_REQ_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)")


def _normalize(name: str) -> str:
    # PEP 503 name normalization.
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(spec: str) -> str:
    m = _REQ_NAME_RE.match(spec.strip())
    if not m:
        raise ValueError(f"Cannot extract package name from requirement: {spec!r}")
    return m.group(1)


def _read_requirements(include_dev: bool) -> list[str]:
    with PYPROJECT_PATH.open("rb") as fp:
        data = tomllib.load(fp)

    project = data.get("project", {})
    runtime: list[str] = list(project.get("dependencies", []))
    dev: list[str] = []
    if include_dev:
        opt = project.get("optional-dependencies", {})
        dev = list(opt.get("dev", []))

    specs = runtime + dev + BUILD_REQUIREMENTS
    kept: list[str] = []
    skipped: list[str] = []
    for spec in specs:
        name = _requirement_name(spec)
        if _normalize(name) in SKIP_PACKAGES:
            skipped.append(spec)
            continue
        kept.append(spec)

    print(
        f"[download_wheels] total specs: {len(specs)}  kept: {len(kept)}  "
        f"skipped: {len(skipped)}  (build-reqs: {len(BUILD_REQUIREMENTS)})"
    )
    if skipped:
        for s in skipped:
            print(f"  skip (pre-installed on server): {s}")
    return kept


def _run_pip_download(requirements: list[str]) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--dest",
        str(WHEELS_DIR),
        "--platform",
        PLATFORM,
        "--python-version",
        PY_VERSION,
        "--implementation",
        IMPLEMENTATION,
        "--abi",
        ABI,
        "--only-binary=:all:",
        *requirements,
    ]
    print("[download_wheels] " + " ".join(cmd))
    subprocess.run(cmd, check=True)


#: Files this script generates (or that are otherwise expected to live
#: alongside wheels) and must NOT be flagged by the sdist-fallback audit.
_AUDIT_IGNORE_NAMES: frozenset[str] = frozenset({"MANIFEST.txt", ".gitkeep"})


def _audit_wheels_dir() -> list[Path]:
    artifacts = sorted(p for p in WHEELS_DIR.iterdir() if p.is_file())
    non_wheel = [
        p for p in artifacts
        if p.suffix != ".whl" and p.name not in _AUDIT_IGNORE_NAMES
    ]
    if non_wheel:
        joined = ", ".join(p.name for p in non_wheel)
        raise SystemExit(
            f"[download_wheels] FAILED: non-wheel artifacts present in {WHEELS_DIR}: "
            f"{joined}\n"
            "This usually means a dependency has no prebuilt "
            f"cp311 / {PLATFORM} wheel and pip fell back to an sdist. "
            "Offline install on the server cannot compile. Pin a different "
            "version or add the dep to SKIP_PACKAGES if it's pre-installed."
        )
    return [p for p in artifacts if p.suffix == ".whl"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_manifest(wheels: list[Path], include_dev: bool) -> Path:
    manifest = WHEELS_DIR / "MANIFEST.txt"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "# Auto_ext wheel bundle manifest",
        f"# generated: {timestamp}",
        f"# python_target: cp{PY_VERSION}",
        f"# implementation: {IMPLEMENTATION}",
        f"# abi: {ABI}",
        f"# platform: {PLATFORM}",
        f"# include_dev: {str(include_dev).lower()}",
        f"# wheel_count: {len(wheels)}",
        "# format: <sha256>  <filename>",
        "",
    ]
    for w in wheels:
        lines.append(f"{_sha256(w)}  {w.name}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--include-dev",
        action="store_true",
        help="Also download [project.optional-dependencies].dev (pytest, ruff, mypy, ...).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove wheels/ before downloading to guarantee a pristine bundle.",
    )
    args = parser.parse_args(argv)

    if args.clean and WHEELS_DIR.exists():
        print(f"[download_wheels] --clean: removing {WHEELS_DIR}")
        shutil.rmtree(WHEELS_DIR)

    WHEELS_DIR.mkdir(parents=True, exist_ok=True)

    requirements = _read_requirements(include_dev=args.include_dev)
    if not requirements:
        raise SystemExit("[download_wheels] FAILED: no requirements to download.")

    _run_pip_download(requirements)
    wheels = _audit_wheels_dir()
    manifest = _write_manifest(wheels, include_dev=args.include_dev)

    print()
    print(f"[download_wheels] wrote {len(wheels)} wheels to {WHEELS_DIR}")
    print(f"[download_wheels] manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
