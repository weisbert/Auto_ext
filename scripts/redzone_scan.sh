#!/bin/sh
# Red-zone identifier gate -- run before every commit/push so site coordinates
# never reach the public GitHub repo.
#
# Two layers, and the difference between them is the whole design:
#   1. generic SHAPE rules (in this file; contain no real identifier, so this
#      script is itself safe to publish)
#   2. a site WORDLIST (`.redzone_patterns.local`, never committed -- the
#      wordlist IS the list of secrets)
#
# Usage:
#   sh scripts/redzone_scan.sh              scan everything that will enter git
#   sh scripts/redzone_scan.sh --staged     scan staged files only (pre-commit)
#
# Exit code: 0 = clean, 1 = hits (refuse the commit).
#
# ---------------------------------------------------------------------------
# Waivers -- two kinds, and they are deliberately not equivalent:
#
#   line-scoped   `redzone-scan-ok` in a comment on the offending line.
#                 Skips BOTH layers for that line. For code/docs that must name
#                 a real shared path (the office python) or must MATCH a site
#                 path shape (importer.py's de-identification regexes).
#
#   file-scoped   `redzone-scan-ok: file` within the first 20 lines.
#                 Skips layer 1 ONLY -- the site wordlist still runs. For test
#                 fixtures whose whole point is to carry realistically SHAPED
#                 but invented coordinates (alice/bob/projA/foo). The one thing
#                 that must never happen to such a file is a real value being
#                 pasted into it, and layer 2 still catches exactly that.
#
# Every waiver must carry its reason on the same line. `grep -rn redzone-scan-ok`
# is the audit.
# ---------------------------------------------------------------------------
#
# Dev-machine only: it needs git, and its job is to keep coordinates OUT of a
# public repo -- meaningless inside the red zone, where there is neither git nor
# GitHub. `.gitattributes` export-ignores it so it never ships across the gap.
set -u
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "not a git repo"; exit 1; }
cd "$ROOT" || exit 1

if [ "${1:-}" = "--staged" ]; then
    FILES=$(git diff --cached --name-only --diff-filter=ACM)
else
    # NOT a bare `git ls-files`: that lists only TRACKED files, so freshly
    # written code that has not been `git add`-ed yet would be invisible and the
    # gate would report clean over a pile of brand-new files.
    # `--cached --others --exclude-standard` = tracked + untracked-but-not-ignored,
    # which is exactly "everything that will end up in git". Ignored files
    # (runs/, wheels/, *.local.*) stay unscanned on purpose: they are never
    # published.
    FILES=$(git ls-files --cached --others --exclude-standard)
fi
[ -z "$FILES" ] && exit 0

GEN=$(mktemp) || exit 1
LOC=$(mktemp) || exit 1
trap 'rm -f "$GEN" "$LOC"' EXIT

# ---- layer 1: generic shape rules (no real identifiers) --------------------
# Each rule requires a CONCRETE segment after the root, so the placeholders this
# repo uses on purpose (`/data/RFIC3/<project>/`, `$WORK_ROOT/...`, `CFXXX`)
# never fire: `<` and `$` are outside every character class below.
cat > "$GEN" <<'GENERIC'
/data/RFIC[0-9]*/[A-Za-z0-9_]
/tmpdata/RFIC/rfic_share/[A-Za-z0-9_]
/software/[A-Za-z0-9_]+/[A-Za-z0-9_]
/home/[a-z][a-z0-9_]{2,}
[^A-Za-z0-9_.][a-z][0-9]{7,9}[^A-Za-z0-9_]
GENERIC

# ---- layer 2: site wordlist (local file, never committed) ------------------
LOCAL="$ROOT/.redzone_patterns.local"
HAVE_LOCAL=0
if [ -f "$LOCAL" ]; then
    grep -v '^[[:space:]]*#' "$LOCAL" | grep -v '^[[:space:]]*$' > "$LOC"
    [ -s "$LOC" ] && HAVE_LOCAL=1
fi
if [ "$HAVE_LOCAL" -eq 0 ]; then
    echo "redzone_scan: no .redzone_patterns.local -- shape rules only."
    echo "              (normal for a public clone; on your own box do:"
    echo "               cp .redzone_patterns.example .redzone_patterns.local  and fill it in)"
fi

# Directories whose files are synthetic parser INPUT: their bytes are the test
# input, so an in-file `redzone-scan-ok: file` marker cannot live there without
# changing what is being parsed. Same semantics as the marker -- layer 1 off,
# layer 2 (the real wordlist) still on.
is_shape_exempt() {
    case "$1" in
        tests/fixtures/*) return 0 ;;
    esac
    head -20 "$1" | grep -q 'redzone-scan-ok: file'
}

HITS=0
WAIVED_FILES=0
for f in $FILES; do
    [ -f "$f" ] || continue
    case "$f" in
        scripts/redzone_scan.sh) continue ;;   # carries the rules, matches itself
    esac

    # Line-scoped waivers: blank the line (keeps line numbers meaningful).
    BODY=$(sed 's/.*redzone-scan-ok.*//' "$f")

    OUT=""
    if is_shape_exempt "$f"; then
        WAIVED_FILES=$((WAIVED_FILES + 1))
    else
        OUT=$(printf '%s\n' "$BODY" | grep -nEi -f "$GEN")
    fi
    if [ "$HAVE_LOCAL" -eq 1 ]; then
        LOCAL_OUT=$(printf '%s\n' "$BODY" | grep -nEi -f "$LOC")
        if [ -n "$LOCAL_OUT" ]; then
            OUT=$(printf '%s\n%s' "$OUT" "$LOCAL_OUT" | grep -v '^$')
        fi
    fi

    if [ -n "$OUT" ]; then
        echo "--- $f"
        printf '%s\n' "$OUT" | head -8
        HITS=$((HITS + 1))
    fi
done

if [ "$HITS" -gt 0 ]; then
    echo
    echo "FAIL  $HITS file(s) carry site coordinates. They must not enter a public repo."
    echo "      Fix by replacing the value with a placeholder"
    echo "      (<project> / <sub-project> / <employee-id> / CFXXX / \$WORK_ROOT),"
    echo "      or -- if the line must NAME a shared path or MATCH a shape rather"
    echo "      than contain a real value -- waive it; see this script's header."
    exit 1
fi
NFILES=$(printf '%s\n' "$FILES" | wc -l | tr -d ' ')
if [ "$WAIVED_FILES" -gt 0 ]; then
    echo "ok  clean over $NFILES file(s)  ($WAIVED_FILES synthetic-fixture file(s) exempt from shape rules)"
else
    echo "ok  clean over $NFILES file(s)"
fi
exit 0
