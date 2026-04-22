#!/usr/bin/env bash
# Launch Auto_ext with the correct cwd + PYTHONPATH for the Cadence flow.
#
# Rationale:
#  - EDA tools (si, strmout, calibre, qrc, jivaro) expect cwd to be the
#    workarea root (the parent of this directory); `si -batch` in
#    particular reads si.env from cwd.
#  - We deliberately do NOT `pip install` the auto_ext package (editable
#    install would write the absolute path of this directory into
#    ~/.local/lib/python3.11/site-packages/, exposing it to anyone who
#    lists pip packages or cats the .pth file). Instead this script puts
#    the project root on PYTHONPATH so `python -m auto_ext` finds the
#    package without leaving any trace outside this directory.
#
# Env overrides:
#  PYTHON=/abs/path/to/python3.11   force a specific interpreter.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workarea="$(cd "${here}/.." && pwd)"

# Pick a Python 3.x interpreter. Skip `python` if it is Python 2 (some
# sites put /software/public/openoffice/.../python on PATH first).
pick_python() {
    if [ -n "${PYTHON:-}" ]; then
        echo "${PYTHON}"
        return
    fi
    local c
    for c in python3.11 python3 python; do
        if command -v "${c}" >/dev/null 2>&1; then
            local major
            major=$("${c}" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo "")
            if [ "${major}" = "3" ]; then
                echo "${c}"
                return
            fi
        fi
    done
    echo "[run.sh] FATAL: no Python 3 interpreter found; set PYTHON=/abs/path." >&2
    exit 1
}
py="$(pick_python)"

export PYTHONPATH="${here}${PYTHONPATH:+:${PYTHONPATH}}"
cd "${workarea}"
exec "${py}" -m auto_ext "$@"
