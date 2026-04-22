#!/usr/bin/env bash
# Offline installer for Auto_ext on the Linux server.
#
# Prereq: copy the whole Auto_ext/ directory (or at minimum pyproject.toml,
# the auto_ext/ package, and wheels/) to the server.
#
# Steps:
#   1. Validate Python version matches the MANIFEST target (cp311).
#   2. pip install --no-index --find-links ./wheels/ -e .[dev] (or -e . with --no-dev).
#   3. Smoke-import auto_ext.core.config and PyQt5.QtCore.
#   4. On any failure, dump MANIFEST.txt + pip list + Python info for debugging.
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
            sed -n '2,20p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "[install_offline] unknown argument: ${arg}" >&2
            exit 2
            ;;
    esac
done

dump_debug() {
    echo "==== install_offline.sh: dumping debug info ===="
    echo "-- python --"
    command -v python || true
    python --version || true
    echo "-- pip --"
    command -v pip || true
    pip --version || true
    echo "-- wheels dir --"
    ls -la "${WHEELS_DIR}" 2>/dev/null || echo "(missing)"
    echo "-- MANIFEST.txt --"
    if [ -f "${MANIFEST}" ]; then
        cat "${MANIFEST}"
    else
        echo "(missing)"
    fi
    echo "-- pip list --"
    pip list 2>/dev/null || true
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

actual="$(python -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
if [ "${actual}" != "${expected}" ]; then
    echo "[install_offline] FATAL: Python version mismatch." >&2
    echo "  expected (from MANIFEST): ${expected}" >&2
    echo "  actual (python in PATH):  ${actual}" >&2
    echo "  Use a matching Python or rerun download_wheels.py targeting the correct version." >&2
    exit 1
fi
echo "[install_offline] Python ${actual} matches MANIFEST target cp${target_tag}."

# Warn if pip is very old; editable installs with PEP 660 need pip >= 21.3.
pip_version="$(pip --version | awk '{print $2}')"
pip_major="${pip_version%%.*}"
if [ "${pip_major}" -lt 21 ]; then
    echo "[install_offline] WARN: pip ${pip_version} is very old; editable install may fail." >&2
fi
echo "[install_offline] pip ${pip_version}."

cd "${PROJECT_ROOT}"

install_spec="."
if [ "${WITH_DEV}" -eq 1 ]; then
    install_spec=".[dev]"
fi
echo "[install_offline] installing ${install_spec} from ${WHEELS_DIR} ..."
pip install --no-index --find-links "${WHEELS_DIR}" -e "${install_spec}"

echo "[install_offline] smoke test: importing auto_ext.core.config + PyQt5.QtCore ..."
python -c "from auto_ext.core import config; from PyQt5 import QtCore; print('OK', QtCore.QT_VERSION_STR)"

echo "[install_offline] success."
# Let trap exit cleanly.
