#!/usr/bin/env bash
# Launch Auto_ext with the correct cwd for the Cadence flow.
#
# Rationale: every EDA tool (si, strmout, calibre, qrc, jivaro) expects to run
# from the workarea root (the parent of Auto_ext/), not from inside Auto_ext/.
# `si -batch` in particular reads `si.env` from cwd, so we chdir explicitly.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workarea="$(cd "${here}/.." && pwd)"

cd "${workarea}"
exec python -m auto_ext "$@"
