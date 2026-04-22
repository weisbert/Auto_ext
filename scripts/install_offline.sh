#!/usr/bin/env bash
# Offline installer for Auto_ext on the Linux server.
#
# Prereq: copy the whole Auto_ext/ directory (or at minimum pyproject.toml,
# the auto_ext/ package, and wheels/) to the server.
#
# Steps:
#   1. Resolve a Python 3.11 interpreter ($PYTHON > python3.11 > python3 > python).
#   2. Validate its version matches the MANIFEST target (cp311).
#   3. "$PYTHON" -m pip install --no-index --find-links ./wheels/ -e .[dev].
#   4. Smoke-import auto_ext.core.config and PyQt5.QtCore.
#   5. On any failure, dump MANIFEST.txt + pip list + Python info for debugging.
#
# Env overrides:
#   PYTHON=/path/to/python3.11    Force a specific interpreter. Recommended when
#                                 the default `python` in PATH is not 3.11
#                                 (typical on RHEL/CentOS where `python` is 2.7
#                                 and the real 3.11 lives at a site path like
#                                 /software/public/python/3.11.4/bin/python).
#
# Flags:
#   --no-dev    Install without the [dev] extra (production deploy).
#
# The script is idempotent: rerunning is safe.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WHEELS_DIR="${PROJECT_ROOT}/wheels"
MANIFEST="${WHEELS_DIR}/MANIFEST.txt"

WITH_DEV=1
for arg in "$@"; do
    case "${arg}" in
        --no-dev) WITH_DEV=0 ;;
        -h|--help)
            sed -n '2,24p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "[install_offline] unknown argument: ${arg}" >&2
            exit 2
            ;;
    esac
done

# Resolve a Python 3.x interpreter. Must happen BEFORE trap/dump_debug uses $PYTHON.
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
PYTHON_OVERRIDE="${PYTHON:-}"
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

# Two-step install to dodge pip's resolver backtracking on
# (editable + extras + find-links + partially-satisfied-from-system-site).
# Step 1: install every wheel in the bundle as explicit file args -- pip
#         installs each wheel directly without consulting the resolver.
# Step 2: editable-install auto_ext itself with --no-deps; its deps are
#         already satisfied either by the wheels we just installed or by
#         system site-packages (PyQt5 in particular).
#
# Consequence: --no-dev still installs the dev wheels (pytest/ruff/mypy)
# because they're in the bundle. If you want a truly minimal production
# install, re-run scripts/download_wheels.py WITHOUT --include-dev first
# so the bundle itself has no dev wheels.

shopt -s nullglob
bundle_wheels=("${WHEELS_DIR}"/*.whl)
shopt -u nullglob
if [ "${#bundle_wheels[@]}" -eq 0 ]; then
    echo "[install_offline] FATAL: no *.whl files under ${WHEELS_DIR}" >&2
    exit 1
fi

echo "[install_offline] step 1/2: installing ${#bundle_wheels[@]} bundled wheels ..."
"${PYTHON}" -m pip install --no-index --find-links "${WHEELS_DIR}" "${bundle_wheels[@]}"

install_spec="."
if [ "${WITH_DEV}" -eq 1 ]; then
    install_spec=".[dev]"
fi
echo "[install_offline] step 2/2: editable install ${install_spec} (--no-deps) ..."
"${PYTHON}" -m pip install --no-index --find-links "${WHEELS_DIR}" --no-deps -e "${install_spec}"

echo "[install_offline] smoke test (core): importing auto_ext.core.config ..."
"${PYTHON}" -c "from auto_ext.core import config; print('auto_ext core import OK')"

# PyQt5 import is *not* a Phase 1 gate. The server's PyQt5 can have ABI
# problems against its libQt5Core.so.5 (happens when PyQt5 was built for
# Qt 5.15 but LD_LIBRARY_PATH picks up an older libQt5). Those are env
# problems, not Auto_ext problems -- flag, do not fail.
echo "[install_offline] smoke test (gui): importing PyQt5.QtCore ..."
if "${PYTHON}" -c "from PyQt5 import QtCore; print('PyQt5', QtCore.QT_VERSION_STR, 'OK')" 2>/tmp/autoext_pyqt5.$$; then
    cat /tmp/autoext_pyqt5.$$ 2>/dev/null || true
    rm -f /tmp/autoext_pyqt5.$$
else
    echo "[install_offline] WARN: PyQt5 import failed. Root cause is almost certainly" >&2
    echo "[install_offline] WARN: a Qt5 runtime-library mismatch (PyQt5 .so built against a" >&2
    echo "[install_offline] WARN: newer Qt than libQt5Core.so.5 on LD_LIBRARY_PATH)." >&2
    echo "[install_offline] WARN: Phase 1 install is still considered successful. Debug with:" >&2
    echo "[install_offline] WARN:   ldd \$(${PYTHON} -c 'import PyQt5, os; print(os.path.dirname(PyQt5.__file__))')/QtCore.abi3.so | grep -i qt" >&2
    sed 's/^/[install_offline] WARN: /' /tmp/autoext_pyqt5.$$ >&2 || true
    rm -f /tmp/autoext_pyqt5.$$
fi

echo "[install_offline] success."
# Let trap exit cleanly.
