#!/usr/bin/env bash
# Offline installer for Auto_ext on the Linux server.
#
# Prereq: copy the whole Auto_ext/ directory (or at minimum pyproject.toml,
# the auto_ext/ package, and wheels/) to the server.
#
# Steps:
#   1. Resolve a Python 3.11 interpreter ($PYTHON > python3.11 > python3 > python).
#   2. Validate its version matches the MANIFEST target (cp311).
#   3. "$PYTHON" -m pip install --no-index --find-links ./wheels/ \
#          --target ./_vendor.new wheels/*.whl
#      -- installs every bundled third-party dependency INSIDE the project
#      directory, not into ~/.local. run.sh puts _vendor/ on PYTHONPATH.
#      The auto_ext package itself is NOT pip-installed (run.sh sets
#      PYTHONPATH so no absolute workarea path leaks into site-packages).
#   4. Smoke-test against _vendor.new with the user site switched off, so a
#      stale ~/.local copy cannot hide a hole in the new tree.
#   5. Swap _vendor.new -> _vendor (old one moved aside first, deleted last):
#      a failed install never leaves a half-populated _vendor/ behind.
#   6. Report bundle packages that ~/.local still holds (now shadowed).
#      Nothing is removed from ~/.local -- it is yours.
#   7. On any failure, dump MANIFEST.txt + pip list + Python info for debugging.
#
# Env overrides:
#   PYTHON=/path/to/python3.11    Force a specific interpreter. Recommended when
#                                 the default `python` in PATH is not 3.11
#                                 (typical on RHEL/CentOS where `python` is 2.7
#                                 and the real 3.11 lives at a site path like
#                                 /software/public/python/3.11.4/bin/python).  # redzone-scan-ok: shared tool mount path, not project/employee identity
#
# Flags:
#   --no-dev    Currently a no-op on what gets installed -- every .whl in
#               the bundle is installed regardless. Drop --include-dev in
#               scripts/download_wheels.py to produce a slimmer bundle.
#
# The script is idempotent: every run is a clean rebuild of _vendor/.
# Deleting the project directory removes the dependencies with it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WHEELS_DIR="${PROJECT_ROOT}/wheels"
MANIFEST="${WHEELS_DIR}/MANIFEST.txt"

# Third-party dependencies live here, inside the project, and nowhere else.
# _vendor.new is the build dir, _vendor.old the outgoing copy during the swap;
# neither exists after a successful run. All three are gitignored, and
# deploy.sh never moves any of them.
VENDOR_DIR="${PROJECT_ROOT}/_vendor"
VENDOR_NEW="${PROJECT_ROOT}/_vendor.new"
VENDOR_OLD="${PROJECT_ROOT}/_vendor.old"
# Read by run.sh (python_version) and deploy/_env_check.py. Keep the
# "key: value" shape -- both parse it with a plain prefix match.
VENDOR_MARKER_NAME="AUTO_EXT_VENDOR.txt"

WITH_DEV=1
for arg in "$@"; do
    case "${arg}" in
        --no-dev) WITH_DEV=0 ;;
        -h|--help)
            # Everything between the shebang and `set -euo pipefail`.
            awk 'NR > 1 && /^set -euo pipefail/ {exit} NR > 1 {print}' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "[install_offline] unknown argument: ${arg}" >&2
            exit 2
            ;;
    esac
done

# Resolve a Python 3.x interpreter. Must happen BEFORE trap/dump_debug uses $PYTHON.
# Capture the caller's PYTHON= override FIRST: it used to be read only after
# the PYTHON="" below had already wiped it, so the documented override was
# silently ignored and the PATH search always ran.
PYTHON_OVERRIDE="${PYTHON:-}"
PYTHON=""
resolve_python() {
    # Explicit override wins.
    if [ -n "${PYTHON_OVERRIDE:-}" ]; then
        if command -v "${PYTHON_OVERRIDE}" >/dev/null 2>&1; then
            PYTHON="${PYTHON_OVERRIDE}"
            return 0
        fi
        echo "[install_offline] FATAL: PYTHON=${PYTHON_OVERRIDE} not found on PATH." >&2
        exit 1
    fi
    # Try interpreters in priority order. Skip Python 2 — some sites put a
    # random `python` (e.g. from OpenOffice) on PATH that can't even parse
    # f-strings, and we don't want to trust that.
    local candidate
    for candidate in python3.11 python3 python; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            local major
            major=$("${candidate}" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo "")
            if [ "${major}" = "3" ]; then
                PYTHON="${candidate}"
                return 0
            fi
        fi
    done
    echo "[install_offline] FATAL: no Python 3 interpreter found on PATH." >&2
    echo "  Tried: python3.11, python3, python. Re-run with PYTHON=/abs/path/to/python3.11." >&2
    exit 1
}
resolve_python
echo "[install_offline] using interpreter: ${PYTHON} ($(command -v "${PYTHON}"))"

dump_debug() {
    echo "==== install_offline.sh: dumping debug info ===="
    echo "-- resolved python --"
    command -v "${PYTHON}" 2>/dev/null || echo "(not resolved)"
    "${PYTHON}" --version 2>&1 || true
    echo "-- pip (via ${PYTHON} -m pip) --"
    "${PYTHON}" -m pip --version 2>&1 || true
    echo "-- stray PATH python / pip --"
    command -v python 2>/dev/null || echo "  no python"
    command -v pip 2>/dev/null || echo "  no pip"
    echo "-- wheels dir --"
    ls -la "${WHEELS_DIR}" 2>/dev/null || echo "(missing)"
    echo "-- MANIFEST.txt --"
    if [ -f "${MANIFEST}" ]; then
        cat "${MANIFEST}"
    else
        echo "(missing)"
    fi
    echo "-- pip list (via ${PYTHON} -m pip) --"
    "${PYTHON}" -m pip list 2>&1 || true
    echo "-- _vendor (what ./run.sh uses) --"
    if [ -f "${VENDOR_DIR}/${VENDOR_MARKER_NAME}" ]; then
        cat "${VENDOR_DIR}/${VENDOR_MARKER_NAME}"
    elif [ -d "${VENDOR_DIR}" ]; then
        echo "(present, but no ${VENDOR_MARKER_NAME} -- incomplete)"
    else
        echo "(none)"
    fi
    if [ -d "${VENDOR_NEW}" ]; then
        echo "-- _vendor.new (this run's partial build; left for inspection, the next run deletes it) --"
        "${PYTHON}" -m pip list --path "${VENDOR_NEW}" 2>&1 || ls -la "${VENDOR_NEW}" 2>&1 || true
    fi
    echo "-- _vendor/ is only replaced after every check passed (look for 'swapped' above) --"
}
trap 'rc=$?; if [ $rc -ne 0 ]; then dump_debug; fi; exit $rc' EXIT

if [ ! -f "${MANIFEST}" ]; then
    echo "[install_offline] FATAL: ${MANIFEST} not found." >&2
    echo "[install_offline] Run scripts/download_wheels.py on the Windows dev box first and copy wheels/ over." >&2
    exit 1
fi

# Parse the python_target line (e.g. "# python_target: cp311") and extract "311".
target_py="$(awk -F': ' '/^# python_target:/ {print $2; exit}' "${MANIFEST}" | tr -d '[:space:]')"
if [ -z "${target_py}" ]; then
    echo "[install_offline] FATAL: MANIFEST.txt missing python_target line." >&2
    exit 1
fi
# target_py looks like "cp311" -> want "3.11"
target_tag="${target_py#cp}"
target_major="${target_tag:0:1}"
target_minor="${target_tag:1}"
expected="${target_major}.${target_minor}"

# NB: avoid f-strings so even if $PYTHON accidentally resolves to 2.x, the
# version probe still reports something useful instead of SyntaxError.
actual="$("${PYTHON}" -c 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))')"
if [ "${actual}" != "${expected}" ]; then
    echo "[install_offline] FATAL: Python version mismatch." >&2
    echo "  expected (from MANIFEST):       ${expected}" >&2
    echo "  actual (${PYTHON}): ${actual}" >&2
    echo "  Re-run with PYTHON=/abs/path/to/python3.11 or switch interpreters." >&2
    exit 1
fi
echo "[install_offline] Python ${actual} matches MANIFEST target cp${target_tag}."

# Warn if pip is very old; editable installs with PEP 660 need pip >= 21.3.
pip_version="$("${PYTHON}" -m pip --version | awk '{print $2}')"
pip_major="${pip_version%%.*}"
if [ "${pip_major}" -lt 21 ]; then
    echo "[install_offline] WARN: pip ${pip_version} is very old; editable install may fail." >&2
fi
echo "[install_offline] pip ${pip_version} (bound to ${PYTHON})."

cd "${PROJECT_ROOT}"

# Install every third-party wheel in the bundle as explicit file args.
# Passing wheel paths directly skips pip's resolver backtracking entirely.
#
# --target puts them in the project's own _vendor/ instead of pip's default
# for a non-root user, ~/.local/lib/python3.11/site-packages. The user site
# had three costs: deleting the project left the deps behind, every colleague
# had to install into their own home, and every other python3.11 program that
# user ran saw (and could be broken by) these packages. --target implies
# --ignore-installed, so the bundle must be self-contained -- it is: nothing
# in it requires PyQt5, the one thing deliberately left to the server's
# read-only system site-packages (5.15.9).
#
# We intentionally do NOT `pip install -e .` for auto_ext itself. Editable
# install writes the project's absolute path into
# ~/.local/lib/python3.11/site-packages/__editable__.auto_ext-0.1.0.pth
# and leaks it via `pip list` / `pip show auto_ext`. This project is a
# tool launched via ./run.sh (which sets PYTHONPATH), not a library to be
# pip-installed.
#
# Note: --no-dev is a no-op on what gets installed from the bundle --
# every *.whl present in wheels/ is installed. Re-run download_wheels.py
# WITHOUT --include-dev to produce a slimmer bundle for production hosts.

shopt -s nullglob
bundle_wheels=("${WHEELS_DIR}"/*.whl)
shopt -u nullglob
if [ "${#bundle_wheels[@]}" -eq 0 ]; then
    echo "[install_offline] FATAL: no *.whl files under ${WHEELS_DIR}" >&2
    exit 1
fi

# Get a directory out of the way without ever failing the install over it.
#
# `rm -rf` can fail here for a reason that is not an error: on NFS, a running
# `./run.sh gui` still has _vendor's .so files mapped, and deleting them leaves
# .nfsXXXX placeholders that make the directory "not empty" until that process
# exits. Under `set -e` that used to kill the run AFTER a successful swap, and
# every rerun then died on the same leftover before installing anything.
#
# The marker goes first: a half-deleted tree must never look like a complete,
# restorable install (see the restore below). If the delete fails, the tree is
# renamed aside to <dir>.<pid> -- a rename works even with files in use -- and
# a later run removes it. Returns 1 only if it could not even be renamed.
retire_dir() {
    local dir="$1" aside
    [ -e "${dir}" ] || return 0
    rm -f "${dir}/${VENDOR_MARKER_NAME}" 2>/dev/null || true
    if rm -rf "${dir}" 2>/dev/null; then
        return 0
    fi
    aside="${dir}.$$"
    if mv "${dir}" "${aside}" 2>/dev/null; then
        echo "[install_offline] WARN: could not delete $(basename "${dir}") (files still in use? e.g. a running" >&2
        echo "[install_offline] WARN: ./run.sh gui on NFS); moved it aside to $(basename "${aside}")." >&2
        echo "[install_offline] WARN: the next run deletes it, or remove it yourself once nothing uses it." >&2
        return 0
    fi
    echo "[install_offline] WARN: could neither delete nor move aside ${dir}" >&2
    return 1
}

# Asides left by retire_dir on an earlier run. Best effort, silent on purpose:
# one still in use is simply tried again next time.
shopt -s nullglob
for _aside in "${VENDOR_OLD}".* "${VENDOR_NEW}".*; do
    rm -rf "${_aside}" 2>/dev/null || true
done
shopt -u nullglob

# Leftovers from an interrupted earlier run. _vendor.old WITHOUT _vendor means
# a run died between the two halves of the swap: the old tree is the last good
# install, so put it back first -- if this run fails too, run.sh still works.
# Only if it still has its marker, though: retire_dir deletes the marker before
# anything else, so a _vendor.old without one may be half deleted.
if [ -d "${VENDOR_OLD}" ]; then
    if [ ! -e "${VENDOR_DIR}" ] && [ -f "${VENDOR_OLD}/${VENDOR_MARKER_NAME}" ]; then
        echo "[install_offline] restoring _vendor/ from an interrupted earlier swap (_vendor.old)"
        mv "${VENDOR_OLD}" "${VENDOR_DIR}"
    elif ! retire_dir "${VENDOR_OLD}"; then
        echo "[install_offline] FATAL: ${VENDOR_OLD} is in the way of the swap. Remove it by hand and rerun." >&2
        exit 1
    fi
fi
if ! retire_dir "${VENDOR_NEW}"; then
    echo "[install_offline] FATAL: ${VENDOR_NEW} is in the way of the build. Remove it by hand and rerun." >&2
    exit 1
fi

echo "[install_offline] installing ${#bundle_wheels[@]} bundled third-party wheels into _vendor.new ..."
# PIP_USER=0: a site pip.conf / PIP_USER=1 that forces --user would make pip
# refuse outright ("Can not combine '--user' and '--target'").
PIP_USER=0 "${PYTHON}" -m pip install --no-index --find-links "${WHEELS_DIR}" \
    --target "${VENDOR_NEW}" "${bundle_wheels[@]}"

# --- .pth files are dead weight under PYTHONPATH ---------------------------
# Python processes *.pth only in site directories (site-packages, the user
# site), never in PYTHONPATH entries -- and _vendor/ is a PYTHONPATH entry.
# So anything a wheel does through a .pth silently does not happen here.
#
# The one known case is setuptools' distutils-precedence.pth, which installs
# the _distutils_hack shim at interpreter start. Losing it is harmless:
# setuptools/__init__.py does `import _distutils_hack.override` itself, so the
# shim is still applied the moment anything imports setuptools, and Python
# 3.11 still ships stdlib distutils for code that does not. (In the user site
# it fired for EVERY python3.11 program that user ran -- one of the side
# effects this move removes.) It is deleted so the check below stays a
# meaningful alarm rather than something everyone learns to ignore.
rm -f "${VENDOR_NEW}/distutils-precedence.pth"
shopt -s nullglob
stray_pth=("${VENDOR_NEW}"/*.pth)
shopt -u nullglob
if [ "${#stray_pth[@]}" -gt 0 ]; then
    echo "[install_offline] WARN: these .pth files landed in _vendor/ and will NOT be processed" >&2
    echo "[install_offline] WARN: (PYTHONPATH entries are not site dirs). Whatever they set up" >&2
    echo "[install_offline] WARN: -- an import hook, a namespace package, an extra path -- will" >&2
    echo "[install_offline] WARN: be missing at run time:" >&2
    for _pth in "${stray_pth[@]}"; do
        echo "[install_offline] WARN:   $(basename "${_pth}"): $(head -c 200 "${_pth}" | tr '\n' ' ')" >&2
    done
fi

# pip records every wheel installed from a file path in
# <dist>.dist-info/direct_url.json -- as an absolute file:// URL, i.e. the
# install dir's full path, which `pip freeze --path _vendor` then prints. Same
# leak the no-editable-install rule exists to prevent; the files carry nothing
# an import needs, so they go.
rm -f "${VENDOR_NEW}"/*.dist-info/direct_url.json

# Clean up any editable install left over from earlier script versions.
# Silent: if it was never installed, pip exits nonzero, we ignore.
"${PYTHON}" -m pip uninstall -y auto_ext auto-ext >/dev/null 2>&1 || true

# --- smoke test the NEW tree, before it replaces anything -------------------
# -s switches the user site off: if a module only imports because an old copy
# sits in ~/.local, that is a hole in _vendor.new and must fail HERE, not on
# the day someone cleans up ~/.local. Every runtime dependency must resolve to
# a file under _vendor.new. qtawesome/qtpy are located but not imported:
# importing them pulls in PyQt5 and the Qt runtime -- the GUI smoke test's job.
echo "[install_offline] smoke test (deps): every runtime dependency resolves inside _vendor.new ..."
PYTHONPATH="${PROJECT_ROOT}:${VENDOR_NEW}" "${PYTHON}" -s - "${VENDOR_NEW}" <<'PY'
import importlib, importlib.util, os, sys

vendor = os.path.normcase(os.path.realpath(sys.argv[1]))
IMPORT = ("jinja2", "markupsafe", "ruamel.yaml", "pydantic", "pydantic_core",
          "typer", "click", "rich")
LOCATE_ONLY = ("qtawesome", "qtpy")


def where(name):
    spec = importlib.util.find_spec(name)
    if spec is None:
        return None
    origin = spec.origin
    if origin in (None, "namespace") and spec.submodule_search_locations:
        origin = list(spec.submodule_search_locations)[0]
    return os.path.normcase(os.path.realpath(origin))


bad = []
for name in IMPORT + LOCATE_ONLY:
    try:
        if name in IMPORT:
            importlib.import_module(name)
        path = where(name)
    except Exception as exc:
        bad.append("%s: %s: %s" % (name, exc.__class__.__name__, exc))
        continue
    if path is None:
        bad.append("%s: not found" % name)
    elif not path.startswith(vendor + os.sep):
        bad.append("%s: resolved OUTSIDE _vendor.new, at %s" % (name, path))
if bad:
    for line in bad:
        print("[install_offline] FAIL " + line, file=sys.stderr)
    sys.exit(1)
print("[install_offline] all %d runtime dependencies resolve inside _vendor.new"
      % len(IMPORT + LOCATE_ONLY))
PY

echo "[install_offline] smoke test (core): importing auto_ext.core.config against _vendor.new ..."
PYTHONPATH="${PROJECT_ROOT}:${VENDOR_NEW}" \
    "${PYTHON}" -s -c "from auto_ext.core import config; print('auto_ext core import OK')"

# --- record what this tree was built for ------------------------------------
# run.sh compares python_version against the interpreter it is about to start
# and warns on a mismatch: pydantic_core and markupsafe are cp311 extension
# modules and will not load under any other minor version. No absolute paths
# in here -- the interpreter path and the project path are both left out.
manifest_sha="$("${PYTHON}" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "${MANIFEST}")"
{
    echo "# Written by scripts/install_offline.sh -- do not edit. Rerun it to rebuild."
    echo "python_target: cp${target_tag}"
    echo "python_version: ${actual}"
    echo "manifest_sha256: ${manifest_sha}"
    echo "wheel_count: ${#bundle_wheels[@]}"
    echo "installed_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "${VENDOR_NEW}/${VENDOR_MARKER_NAME}"

# --- swap _vendor.new into place ---------------------------------------------
# Two renames inside one directory. The only window where neither tree is at
# _vendor is between them; a run killed there leaves _vendor.old, which the
# next run restores before doing anything else.
if [ -e "${VENDOR_DIR}" ]; then
    mv "${VENDOR_DIR}" "${VENDOR_OLD}"
fi
if ! mv "${VENDOR_NEW}" "${VENDOR_DIR}"; then
    echo "[install_offline] FATAL: could not move _vendor.new into place; restoring the previous _vendor/" >&2
    if [ -d "${VENDOR_OLD}" ]; then mv "${VENDOR_OLD}" "${VENDOR_DIR}"; fi
    exit 1
fi
echo "[install_offline] swapped: _vendor/ now holds the ${#bundle_wheels[@]}-wheel bundle for Python ${actual}."
# The install is done at this point; failing to clean up must not undo that.
retire_dir "${VENDOR_OLD}" || \
    echo "[install_offline] WARN: the previous tree is left at ${VENDOR_OLD} (marker removed); the next run retries." >&2

# PyQt5 import is *not* a Phase 1 gate. The server's PyQt5 can have ABI
# problems against its libQt5Core.so.5 (happens when PyQt5 was built for
# Qt 5.15 but LD_LIBRARY_PATH picks up an older libQt5). Those are env
# problems, not Auto_ext problems -- flag, do not fail.
echo "[install_offline] smoke test (gui): importing PyQt5.QtCore ..."
# TMPDIR first: doctor.sh points it inside the install so a test run leaves /tmp alone.
PYQT5_ERR="${TMPDIR:-/tmp}/autoext_pyqt5.$$"
if "${PYTHON}" -c "from PyQt5 import QtCore; print('PyQt5', QtCore.QT_VERSION_STR, 'OK')" 2>"${PYQT5_ERR}"; then
    cat "${PYQT5_ERR}" 2>/dev/null || true
    rm -f "${PYQT5_ERR}"
else
    echo "[install_offline] WARN: PyQt5 import failed. Root cause is almost certainly" >&2
    echo "[install_offline] WARN: a Qt5 runtime-library mismatch (PyQt5 .so built against a" >&2
    echo "[install_offline] WARN: newer Qt than libQt5Core.so.5 on LD_LIBRARY_PATH)." >&2
    echo "[install_offline] WARN: Phase 1 install is still considered successful. Debug with:" >&2
    echo "[install_offline] WARN:   ldd \$(${PYTHON} -c 'import PyQt5, os; print(os.path.dirname(PyQt5.__file__))')/QtCore.abi3.so | grep -i qt" >&2
    sed 's/^/[install_offline] WARN: /' "${PYQT5_ERR}" >&2 || true
    rm -f "${PYQT5_ERR}"
fi

# --- old copies in the user site (informational only) -----------------------
# Earlier versions of this script installed the same bundle into ~/.local. Under
# ./run.sh those copies are now shadowed -- PYTHONPATH (and so _vendor/) comes
# before the user site on sys.path -- but they still load for every OTHER
# python3.11 program this user runs. They are NOT removed automatically: the
# user site is yours, and something else of yours may depend on them.
"${PYTHON}" - "${VENDOR_DIR}" "${PYTHON}" <<'PY' || true
import os, site, sys
from importlib import metadata

if not getattr(site, "ENABLE_USER_SITE", False):
    sys.exit(0)
user_site = site.getusersitepackages()
if not os.path.isdir(user_site):
    sys.exit(0)


def names(path):
    out = {}
    for dist in metadata.distributions(path=[path]):
        name = dist.metadata["Name"]
        if name:
            out[name.lower().replace("_", "-").replace(".", "-")] = (name, dist.version)
    return out


ours = names(sys.argv[1])
theirs = names(user_site)
both = sorted(k for k in ours if k in theirs)
if not both:
    sys.exit(0)
tag = "[install_offline] NOTE:"
print(tag + " %d bundle packages are ALSO installed in your user site:" % len(both))
print(tag + "   " + user_site)
for k in both:
    print(tag + "     %s %s" % theirs[k])
print(tag + " ./run.sh puts _vendor/ ahead of them, so Auto_ext no longer uses them.")
print(tag + " Nothing was removed. If no other program of yours needs them, clean up with")
print(tag + " (from a plain shell, NOT through ./run.sh; works in csh too):")
print(tag + '   "%s" -m pip uninstall -y %s' % (sys.argv[2], " ".join(theirs[k][0] for k in both)))
PY

echo "[install_offline] success."
echo "[install_offline] launch the tool with: ./run.sh [args]"
echo "[install_offline] or (bash and csh alike):"
echo "[install_offline]   env PYTHONPATH=\"${PROJECT_ROOT}:${VENDOR_DIR}\" \"${PYTHON}\" -m auto_ext [args]"
# Let trap exit cleanly.
