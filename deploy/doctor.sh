#!/usr/bin/env bash
#
# doctor.sh -- red-zone environment check for Auto_ext.
#
# The red zone has no network: nothing can be pip-installed from PyPI and no
# venv is created. So the only question that matters after a deploy is "what can
# THIS box already run?". This script answers it per interpreter, in three tiers:
#
#   tier 1  render templates, dry-run, run the shipped test suite
#                             needs Python >= 3.11 + the offline dependency wheels
#   tier 2  really drive the flow            + si / strmout / calibre / qrc on PATH
#   tier 3  GUI                              + an importable PyQt5 and a $DISPLAY
#
# One box-wide comfort is reported alongside tier 3: a desktop file opener
# (xdg-open or gio). Without one, every "Open the log / the report" button in
# the GUI can do nothing at all -- no window, no error -- so it is worth knowing
# before the click, not after. It gates nothing.
#
# Tiers are cumulative. A missing tier-3 dependency is a DEGRADE, not a failure:
# tiers 1-2 still run, and a plain ssh session is SUPPOSED to have no $DISPLAY.
# Tier 2 usually needs your site's Cadence modules loaded first -- an interpreter
# stuck at tier 1 is normally a bare login shell, not a bad install.
#
# Usage (login shell is often tcsh -- always invoke with bash):
#   bash deploy/doctor.sh                 probe every candidate interpreter
#   bash deploy/doctor.sh --test          ... and run the shipped test suite
#   bash deploy/doctor.sh --python /path/to/python3.11
#   AUTOEXT_PYTHON=/path/to/python3.11 bash deploy/doctor.sh
#
# Exit codes: 0 = at least one interpreter reaches tier 1 (and, with --test, the
# suite passed); 1 = no usable interpreter, or the suite failed; 2 = --test was
# asked for but cannot be answered on this box (pytest is not installed).
#
# Tier 1 is the bar because tier 1 already does the work this tool exists for
# (render every file a run would feed the tools, dry-run it, run the whole test
# suite); the EDA tools come and go with a module load and must not make a good
# install look broken.
#
# Everything this script writes stays under <install>/.deploy/ -- never /tmp.
#
# English on purpose: this file runs on a box where LANG is often C.
#
set -uo pipefail

SELF="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SELF")"
ROOT="$(dirname "$SCRIPT_DIR")"
PROBE="$SCRIPT_DIR/_env_check.py"

RUN_TESTS=0
FORCED_PY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --test|-t)    RUN_TESTS=1; shift ;;
    --python|-p)  FORCED_PY="${2:-}"; shift 2 ;;
    -h|--help)    sed -n '3,36p' "$SELF" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

[[ -f "$PROBE" ]] || { echo "ERROR: $PROBE missing -- incomplete install." >&2; exit 1; }

# Scratch stays inside the install dir -- never /tmp, /var or anywhere else on
# the box. Same rule the deployer follows: everything under ./.deploy/.
TMP="$ROOT/.deploy/tmp"
rm -rf "$TMP"
mkdir -p "$TMP" || { echo "ERROR: cannot create $TMP -- is $ROOT writable?" >&2; exit 1; }
trap 'rm -rf "$TMP"' EXIT

getval() { sed -n "s/^$1=//p" "$2" | head -1; }
mark() { if [[ "$1" == "OK" || "$1" == "YES" ]]; then echo "OK "; else echo "-- "; fi; }

echo "=== Auto_ext -- red-zone environment doctor ==="
echo "install : $ROOT"
if [[ -f "$ROOT/VERSION" ]]; then
  echo "version :"
  sed -n '1,4p' "$ROOT/VERSION" | sed 's/^/     /'
fi
echo

# --- collect candidate interpreters -----------------------------------------
CANDIDATES=()
add_candidate() {
  local _c="$1" _r _e
  [[ -n "$_c" ]] || return 0
  _r="$(command -v "$_c" 2>/dev/null || true)"
  [[ -n "$_r" ]] || return 0
  _r="$(readlink -f "$_r" 2>/dev/null || echo "$_r")"
  for _e in ${CANDIDATES[@]+"${CANDIDATES[@]}"}; do
    if [[ "$_e" == "$_r" ]]; then return 0; fi
  done
  CANDIDATES+=("$_r")
}

if [[ -n "$FORCED_PY" ]]; then
  add_candidate "$FORCED_PY"
  if (( ${#CANDIDATES[@]} == 0 )); then
    echo "ERROR: --python '$FORCED_PY' is not executable." >&2; exit 1
  fi
else
  add_candidate "${AUTOEXT_PYTHON:-}"
  # `python` is probed LAST and on purpose: on the deployment target it is
  # Python 2.7 from an OpenOffice install, and probing it produces a clean
  # "SKIP (need >= 3.11)" line -- which is itself useful, because that trap has
  # cost time before.
  for c in python3.11 python3.12 python3.13 python3 /usr/bin/python3 python; do
    add_candidate "$c"
  done
fi

if (( ${#CANDIDATES[@]} == 0 )); then
  echo "ERROR: no Python interpreter found on PATH." >&2
  echo "       Load your site's python module (e.g. \`ma python/3.11.4\`), or" >&2
  echo "       point at one explicitly:" >&2
  echo "       bash deploy/doctor.sh --python /path/to/python3.11" >&2
  exit 1
fi

# --- desktop file opener (box-wide, not per interpreter) ---------------------
# Kept in step with auto_ext/ui/os_open.py's launcher list and with
# auto_ext.core.health.FILE_OPENERS; tests/ui/test_os_open.py asserts all three
# name the same binaries.
OPENER=""
for _o in xdg-open gio; do
  if command -v "$_o" >/dev/null 2>&1; then OPENER="$(command -v "$_o")"; break; fi
done

# --- probe each --------------------------------------------------------------
BEST=""; BEST_TIER=-1; BEST_VENV="NO"

idx=0
for py in "${CANDIDATES[@]}"; do
  idx=$((idx + 1))
  out="$TMP/probe.$idx"
  if ! "$py" "$PROBE" > "$out" 2> "$TMP/err.$idx"; then
    if [[ "$(getval PY_OK "$out")" == "NO" ]]; then
      printf '>> %-38s %s  SKIP (%s)\n' "$py" "$(getval PY_VERSION "$out")" "$(getval PY_WHY "$out")"
    else
      printf '>> %-38s SKIP (probe failed)\n' "$py"
      sed 's/^/     /' "$TMP/err.$idx" | tail -3
    fi
    echo
    continue
  fi

  pyver="$(getval PY_VERSION "$out")"
  echo ">> $py  ($pyver)"

  # --- offline dependencies (tier 1 gate) ---
  for k in jinja2 ruamel_yaml pydantic typer rich; do
    st="$(getval "DEP_$k" "$out")"
    human="$(getval "DEP_${k}_human" "$out")"
    detail="$(getval "DEP_${k}_detail" "$out")"
    if [[ "$st" == "OK" ]]; then
      printf '     %s dep %-14s %s\n' "$(mark OK)" "$human" "$detail"
    else
      printf '     %s dep %-14s MISSING\n' "$(mark MISSING)" "$human"
    fi
  done

  # --- the shipped package ---
  printf '     %s import auto_ext.core.runner\n' "$(mark "$(getval IMP_core_runner "$out")")"
  printf '     %s import auto_ext.core.render\n' "$(mark "$(getval IMP_core_render "$out")")"
  printf '     %s import auto_ext.cli\n'         "$(mark "$(getval IMP_cli "$out")")"
  for k in IMP_core_runner IMP_core_render IMP_cli; do
    if [[ "$(getval "$k" "$out")" == "FAIL" ]]; then
      printf '        why: %s\n' "$(getval "${k}_detail" "$out")"
    fi
  done

  # --- EDA tools ---
  for t in si strmout calibre qrc; do
    printf '     %s %-8s %s\n' "$(mark "$(getval "TOOL_$t" "$out")")" "$t" "$(getval "TOOL_${t}_detail" "$out")"
  done
  if [[ "$(getval TOOL_jivaro "$out")" == "OK" ]]; then
    printf '     %s %-8s %s\n' "$(mark OK)" "jivaro" "$(getval TOOL_jivaro_detail "$out")"
  else
    printf '     %s %-8s absent -- fine unless a recipe sets jivaro.enabled: true\n' "$(mark MISSING)" "jivaro"
  fi

  # --- site environment (informational) ---
  _envmissing=()
  for v in WORK_ROOT WORK_ROOT2 VERIFY_ROOT SETUP_ROOT PDK_LAYER_MAP_FILE; do
    if [[ -z "$(getval "ENV_$v" "$out")" ]]; then _envmissing+=("$v"); fi
  done
  if (( ${#_envmissing[@]} == 0 )); then
    printf '     %s env      all 5 site variables are set\n' "$(mark OK)"
  else
    printf '     %s env      unset: %s\n' "$(mark MISSING)" "${_envmissing[*]}"
    printf '        (source your Cadence/PDK setup in THIS shell; the authoritative\n'
    printf '         per-profile check is: ./run.sh check-env --config-dir config)\n'
  fi

  # --- Qt / GUI ---
  qt="$(getval MOD_PyQt5 "$out")"
  qtlib="$(getval QT_BUNDLED_LIB "$out")"
  disp="$(getval ENV_DISPLAY "$out")"
  qt_via_bundle=0
  if [[ "$qt" != "OK" && -n "$qtlib" ]]; then
    # Second pass. LD_LIBRARY_PATH is read by the dynamic loader at process
    # start, so this genuinely cannot be decided inside the first probe. On a
    # CentOS 7-class box the site-wide Qt5 inherits an ancient libstdc++ that
    # lacks C++14 sized-delete, and only the Qt5 bundled inside the PyQt5 wheel
    # resolves -- which is exactly the prepend run.sh performs for `gui`/`test`.
    if LD_LIBRARY_PATH="$qtlib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
         "$py" "$PROBE" --qt-only > "$TMP/qt.$idx" 2>/dev/null \
       && [[ "$(getval MOD_PyQt5 "$TMP/qt.$idx")" == "OK" ]]; then
      qt="OK"; qt_via_bundle=1
    fi
  fi
  if [[ "$qt" == "OK" && $qt_via_bundle -eq 1 ]]; then
    printf '     %s PyQt5    OK only with the wheel bundled Qt5 -- use ./run.sh gui,\n' "$(mark OK)"
    printf '              which sets LD_LIBRARY_PATH for you (bare python will fail)\n'
  elif [[ "$qt" == "OK" ]]; then
    printf '     %s PyQt5    %s\n' "$(mark OK)" "$(getval MOD_PyQt5_detail "$out")"
  else
    printf '     %s PyQt5    %s\n' "$(mark FAIL)" "$(getval MOD_PyQt5_detail "$out")"
  fi
  if [[ -n "$disp" ]]; then
    printf '     %s DISPLAY  %s\n' "$(mark OK)" "$disp"
  else
    printf '     %s DISPLAY  unset (headless -- no GUI; normal in a plain ssh session)\n' "$(mark MISSING)"
  fi
  if [[ -n "$OPENER" ]]; then
    printf '     %s open     %s\n' "$(mark OK)" "$OPENER"
  else
    printf '     %s open     no xdg-open and no gio -- the GUI cannot hand a log or a\n' "$(mark MISSING)"
    printf '              report to a viewer; it reads them in-app instead. Install\n'
    printf '              xdg-utils (or the GLib tools) to get those buttons back.\n'
  fi

  if [[ "$(getval PY_VENV "$out")" == "YES" ]]; then
    printf '        NOTE: this is a virtualenv, not a system interpreter.\n'
  fi

  # The probe decided the tier without knowing about the bundled-Qt second pass.
  # Recompute only the GUI step here; tiers 0-2 are unaffected by Qt.
  tier="$(getval TIER "$out")"
  [[ "$tier" =~ ^[0-9]+$ ]] || tier=0
  why="$(getval TIER_WHY "$out")"
  if (( qt_via_bundle )) && [[ "$tier" == "2" && -n "$disp" ]]; then
    tier=3; why=""
  fi

  cc="$(getval CAP_core "$out")"
  echo "     ------------------------------------------------"
  printf '     tier 1  render / dry-run / tests         %s\n' "$([[ "$cc" == YES ]] && echo AVAILABLE || echo "NOT AVAILABLE")"
  printf '     tier 2  drive the EDA flow               %s\n' "$([[ "$tier" -ge 2 ]] && echo AVAILABLE || echo "NOT AVAILABLE")"
  if (( tier >= 3 )); then
    printf '     tier 3  GUI                              AVAILABLE\n'
  elif [[ "$qt" == "OK" && -n "$disp" ]]; then
    printf '     tier 3  GUI                              Qt + X11 OK, held back by tier 2\n'
  elif [[ "$qt" == "OK" ]]; then
    printf '     tier 3  GUI                              code OK, needs X11 ($DISPLAY)\n'
  else
    printf '     tier 3  GUI                              NOT AVAILABLE\n'
  fi
  if [[ -n "$why" ]]; then printf '     (stops at tier %s: %s)\n' "$tier" "$why"; fi
  echo

  if (( tier > BEST_TIER )); then
    BEST_TIER=$tier; BEST="$py"; BEST_VENV="$(getval PY_VENV "$out")"
  fi
done

# --- verdict -----------------------------------------------------------------
if [[ -z "$BEST" || $BEST_TIER -lt 1 ]]; then
  echo "VERDICT: no interpreter on this box can run Auto_ext."
  echo
  echo "  Tier 1 needs a Python >= 3.11 that can import the package AND the five"
  echo "  offline dependencies. The two things that produce this verdict are:"
  echo "    * the wheels were never installed  ->  bash scripts/install_offline.sh"
  echo "    * the package did not land intact  ->  re-run: bash deploy.sh"
  echo "  To try a specific interpreter:"
  echo "     bash deploy/doctor.sh --python /path/to/python3.11"
  exit 1
fi

echo "RECOMMENDED: $BEST   (tier $BEST_TIER)"
if [[ "$BEST_VENV" == "YES" ]]; then
  echo "  (heads-up: that is a virtualenv. Fine if it works, but the red-zone"
  echo "   baseline is a system interpreter -- a venv may not exist for others.)"
fi
if (( BEST_TIER < 2 )); then
  echo
  echo "  Tier 1 only: the EDA tools are not on PATH, so this session can render"
  echo "  and dry-run but cannot run the flow. Load your site's Cadence modules in"
  echo "  this shell and re-run to reach tier 2."
fi
echo
echo "  cd $ROOT"
echo "  environment check   : ./run.sh check-env --config-dir config"
echo "  dry-run a recipe    : ./run.sh run --config-dir config --recipe <id> --dry-run"
if (( BEST_TIER >= 2 )); then
  echo "  run for real        : ./run.sh run --config-dir config --recipe <id>"
fi
if (( BEST_TIER >= 3 )); then
  echo "  GUI                 : ./run.sh gui --config-dir config"
fi
echo "  unit tests          : ./run.sh test"
echo

# --- optional self-test ------------------------------------------------------
if (( RUN_TESTS )); then
  echo "=== self-test with $BEST ==="
  # The suite needs pytest, which lives in the DEV half of the wheel bundle and
  # may legitimately not be installed here. Distinguish "cannot answer" from
  # "answered no" -- reporting a missing test runner as a test failure would
  # send someone hunting a bug that does not exist.
  if ! "$BEST" -c "import pytest" >/dev/null 2>&1; then
    echo
    echo "CANNOT SELF-TEST: pytest is not installed for $BEST." >&2
    echo "  The shipped suite is the strongest evidence available on a box with no" >&2
    echo "  network, so this is worth fixing: re-pack the wheels with the dev extras" >&2
    echo "  (scripts/download_wheels.py --include-dev on the yellow zone), upload the" >&2
    echo "  wheels bundle, then: bash scripts/install_offline.sh" >&2
    echo "  Everything above this line still stands -- the install itself is fine." >&2
    exit 2
  fi
  # TMPDIR keeps even tempfile.mkdtemp() inside .deploy/tmp, so a doctor run
  # leaves nothing at all in /tmp on the box.
  #
  # Run the suite THROUGH run.sh rather than calling pytest directly: run.sh is
  # the real entry point, and it is the thing that sets cwd and the Qt
  # LD_LIBRARY_PATH prepend. Testing around it would leave the launcher itself
  # unverified -- and the launcher is where the two known environment traps
  # (Python 2.7 on PATH, the Qt5 ABI) are handled.
  if ( cd "$ROOT" && TMPDIR="$TMP" TEMP="$TMP" TMP="$TMP" PYTHON="$BEST" bash ./run.sh test ); then
    echo
    echo "OK  self-test passed -- the package landed intact and this interpreter runs it."
  else
    echo
    echo "FAIL  self-test failed. The package or the environment is not sound;" >&2
    echo "      do not trust results from this install until it passes." >&2
    exit 1
  fi
fi
