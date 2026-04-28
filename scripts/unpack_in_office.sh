#!/bin/bash
# Unpack an Auto_ext bundle (from scripts/pack_for_office.ps1 on Windows) over
# the current Linux working tree. Files with the same path are OVERWRITTEN.
# Files that were deleted on Windows but still exist on Linux are NOT removed
# (this script does not do "rsync --delete" semantics).
#
# Restores exec bits that Windows tar.exe drops on run.sh and scripts/*.sh.
#
# Usage:
#     bash scripts/unpack_in_office.sh <bundle.tar.gz> [target_dir]
#
# Defaults:
#     target_dir = the repo root (this script's parent's parent)

set -euo pipefail

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    cat <<EOF
Usage: $0 <bundle.tar.gz> [target_dir]

  bundle.tar.gz  file produced by scripts/pack_for_office.ps1 on Windows
  target_dir     where to extract (default: repo root, derived from this
                 script's location)
EOF
    exit 1
fi

bundle="$1"
script_dir="$(cd "$(dirname "$0")" && pwd)"
default_target="$(cd "$script_dir/.." && pwd)"
target="${2:-$default_target}"

if [ ! -f "$bundle" ]; then
    echo "Bundle not found: $bundle" >&2
    exit 1
fi

if [ ! -d "$target" ]; then
    echo "Target dir does not exist: $target" >&2
    exit 1
fi

bundle_abs="$(cd "$(dirname "$bundle")" && pwd)/$(basename "$bundle")"

echo "Unpacking Auto_ext bundle..."
echo "  Bundle: $bundle_abs"
echo "  Target: $target"
echo ""

cd "$target"
tar -xzf "$bundle_abs" --overwrite

chmod +x run.sh 2>/dev/null || true
find scripts -maxdepth 1 -name "*.sh" -type f -exec chmod +x {} \; 2>/dev/null || true

echo "Done."
echo ""
echo "Recommended next steps:"
echo "  ./run.sh test          # confirm tests still pass after the sync"
echo ""
echo "Note: deleted-on-Windows files are NOT removed here. For a clean sync,"
echo "manually rm the relevant subdir first, then re-run unpack."
