#!/bin/bash
# Unpack an Auto_ext bundle (from scripts/pack_for_office.ps1 on Windows) over
# the current Linux working tree. Files with the same path are OVERWRITTEN.
# Files that were deleted on Windows but still exist on Linux are NOT removed
# (this script does not do "rsync --delete" semantics).
#
# Restores exec bits that Windows tar.exe drops on run.sh and scripts/*.sh.
#
# Safety: refuses to extract over a non-empty target that does not look like
# an Auto_ext repo (must have pyproject.toml with name="auto_ext" AND
# auto_ext/cli.py). This guards against the failure mode where the user runs
# unpack from one level too high and silently merges into an unrelated
# directory tree (e.g. a sibling project that happens to share the
# auto_ext/ name). Override with --force when you really mean it.
#
# Usage:
#     bash scripts/unpack_in_office.sh [--force] <bundle.tar.gz> [target_dir]
#
# Defaults:
#     target_dir = the repo root (this script's parent's parent)

set -euo pipefail

force=0
if [ "${1:-}" = "--force" ]; then
    force=1
    shift
fi

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    cat <<EOF
Usage: $0 [--force] <bundle.tar.gz> [target_dir]

  --force        skip the target sanity check (use with care)
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

# --- Target sanity check ---------------------------------------------------
# If target is non-empty and lacks the Auto_ext signature, confirm before
# extracting. Empty dirs (legit first-time install) and recognised Auto_ext
# repos pass through silently.
target_has_files=0
if [ -n "$(ls -A "$target" 2>/dev/null | grep -v '^Auto_ext_bundle\.tar\.gz$' || true)" ]; then
    target_has_files=1
fi

target_looks_like_auto_ext=0
if [ -f "$target/pyproject.toml" ] \
   && grep -q 'name *= *"auto_ext"' "$target/pyproject.toml" 2>/dev/null \
   && [ -f "$target/auto_ext/cli.py" ]; then
    target_looks_like_auto_ext=1
fi

if [ "$force" -eq 0 ] \
   && [ "$target_has_files" -eq 1 ] \
   && [ "$target_looks_like_auto_ext" -eq 0 ]; then
    cat >&2 <<EOF
WARNING: target does not look like an Auto_ext repo.

  Target: $target
  Missing signature: pyproject.toml [name="auto_ext"] AND auto_ext/cli.py

If you proceed, every file in this target with a path that matches one
inside the bundle will be OVERWRITTEN (135 paths under auto_ext/ and
tests/, plus the rest of the bundle). This has caused real harm before
when users accidentally pointed unpack at workarea root, where an
unrelated same-named auto_ext/ project lived.

If this really is the right target, re-run with --force.
EOF
    if [ -t 0 ]; then
        printf 'Type "yes" to proceed anyway: ' >&2
        read -r confirm
        if [ "$confirm" != "yes" ]; then
            echo "Aborted." >&2
            exit 1
        fi
    else
        echo "stdin not a tty; aborting (use --force for non-interactive runs)." >&2
        exit 1
    fi
fi

# --- Extract ---------------------------------------------------------------
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
