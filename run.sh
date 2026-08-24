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

# ---- Absolutize relative path arguments, BEFORE the cd below ---------------
#
# The `cd "${workarea}"` further down is required -- si -batch reads si.env
# from cwd -- but it silently broke every relative path the caller typed.
# Standing in the install directory, `./run.sh check-env --config-dir config`
# resolved to <workarea>/config and died with "Directory 'config' does not
# exist" while config/ was sitting right there. Every documented command
# failed at step one (docs/refactor/DEPLOY_FINDINGS.md section 1).
#
# Fixing only the docs is treating the symptom: the next person types a
# relative path from instinct and hits the same wall. So the launcher restores
# the semantics the caller expects -- a relative path means "relative to where
# I am standing" -- by rewriting it to an absolute path before the cd.
#
# Path *patterns* (--to, --layout-out) are deliberately NOT in the list. They
# are resolved against the workarea by design and may carry {cell}-style
# placeholders, so absolutizing them would be wrong.
#
# AUTO_EXT_PATH_FLAGS must name every filesystem-path option in
# auto_ext/cli.py. tests/test_run_sh.py fails if the two drift apart -- this
# is the kind of hand-written inventory that goes stale the day someone adds a
# flag and never looks here.
AUTO_EXT_PATH_FLAGS="
--config-dir
--auto-ext-root
--workarea
--resources
--recipes-dir
--profiles-dir
--out-root
--template-root
--catalog
--raw-calibre
--raw-quantus
--raw-si
--raw-jivaro
--output-config-dir
--output-templates-dir
--input
--output
--out
--file
-f
"

invocation_cwd="${PWD}"

_auto_ext_is_path_flag() {
    local candidate
    for candidate in ${AUTO_EXT_PATH_FLAGS}; do
        if [ "${candidate}" = "${1}" ]; then
            return 0
        fi
    done
    return 1
}

# Echo an absolute form of $1, read from the caller's cwd. Absolute paths and
# the empty string pass through untouched.
_auto_ext_abs() {
    case "${1}" in
        /*|"") printf '%s' "${1}" ;;
        *) printf '%s/%s' "${invocation_cwd}" "${1}" ;;
    esac
}

# Two subcommands take filesystem paths as POSITIONAL arguments: `test`
# forwards everything to pytest, and `recipe import` takes the EDA files.
# Positionals are rewritten only when they actually exist relative to the
# caller's cwd, so a recipe id or a stage name is never mistaken for a path.
positional_paths=0
case "${1:-}" in
    test) positional_paths=1 ;;
    recipe)
        if [ "${2:-}" = "import" ]; then
            positional_paths=1
        fi
        ;;
esac

_auto_ext_rewritten=()
_auto_ext_expect_path=0
for _auto_ext_arg in "$@"; do
    if [ "${_auto_ext_expect_path}" = "1" ]; then
        _auto_ext_expect_path=0
        # `--config-dir --something` is a user error, not a path; leave it for
        # the CLI to report.
        case "${_auto_ext_arg}" in
            -*) _auto_ext_rewritten+=("${_auto_ext_arg}") ;;
            *) _auto_ext_rewritten+=("$(_auto_ext_abs "${_auto_ext_arg}")") ;;
        esac
        continue
    fi
    case "${_auto_ext_arg}" in
        --*=*)
            _auto_ext_flag="${_auto_ext_arg%%=*}"
            if _auto_ext_is_path_flag "${_auto_ext_flag}"; then
                _auto_ext_rewritten+=("${_auto_ext_flag}=$(_auto_ext_abs "${_auto_ext_arg#*=}")")
            else
                _auto_ext_rewritten+=("${_auto_ext_arg}")
            fi
            ;;
        -*)
            if _auto_ext_is_path_flag "${_auto_ext_arg}"; then
                _auto_ext_expect_path=1
            fi
            _auto_ext_rewritten+=("${_auto_ext_arg}")
            ;;
        *)
            # pytest node ids are `<path>::<test>`; the path half is what has
            # to exist for this to be a path at all.
            _auto_ext_probe="${_auto_ext_arg%%::*}"
            if [ "${positional_paths}" = "1" ] && [ -n "${_auto_ext_probe}" ] \
               && [ -e "${invocation_cwd}/${_auto_ext_probe}" ]; then
                _auto_ext_rewritten+=("$(_auto_ext_abs "${_auto_ext_arg}")")
            else
                _auto_ext_rewritten+=("${_auto_ext_arg}")
            fi
            ;;
    esac
done
set -- ${_auto_ext_rewritten[@]+"${_auto_ext_rewritten[@]}"}

# Escape hatch for "what did run.sh actually pass?" -- the question this
# rewriting exists to answer, asked on a box with no debugger. Prints and
# exits before any interpreter is picked, so it works even on a broken install.
if [ -n "${AUTO_EXT_ARGV_DEBUG:-}" ]; then
    printf 'here=%s\n' "${here}"
    printf 'workarea=%s\n' "${workarea}"
    printf 'invocation_cwd=%s\n' "${invocation_cwd}"
    if [ "$#" -gt 0 ]; then
        printf 'argv=%s\n' "$@"
    fi
    exit 0
fi

# Pick a Python 3.x interpreter. Skip `python` if it is Python 2 (some
# sites put /software/public/openoffice/.../python on PATH first).  # redzone-scan-ok: shared tool mount path, not project/employee identity
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

# GUI entry (and `test`, since pytest-qt also imports PyQt5 at startup)
# needs PyQt5's bundled Qt5 on LD_LIBRARY_PATH. On CentOS 7 class
# servers, /usr/lib64/libstdc++.so.6 tops out at GLIBCXX_3.4.19 (GCC
# 4.8) and lacks _ZdaPvm (C++14 sized-delete). PyQt5 5.15.9's
# QtCore.abi3.so references _ZdaPvm@Qt_5 from libQt5Core.so.5, so any
# site-wide Qt5 that is U _ZdaPvm (inherits from the old libstdc++)
# fails to resolve. PyQt5's manylinux2014 wheel bundles a self-
# contained Qt5 with _ZdaPvm defined (T, not U), so preferring it
# fixes the import.
# Scope to subcommands that actually load Qt so non-GUI / non-test
# runs do not contaminate LD_LIBRARY_PATH inherited by EDA
# subprocesses. Safe-by-default.
needs_qt() {
    case "${1:-}" in
        gui|gui-*|test) return 0 ;;
        *) return 1 ;;
    esac
}
if needs_qt "$@"; then
    pyqt_qt5_lib="$("${py}" -c 'import PyQt5, os; print(os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "lib"))' 2>/dev/null || true)"
    if [ -n "${pyqt_qt5_lib}" ] && [ -d "${pyqt_qt5_lib}" ]; then
        export LD_LIBRARY_PATH="${pyqt_qt5_lib}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
    else
        echo "[run.sh] WARN: PyQt5 bundled Qt5 libs not found at expected path; Qt import may fail." >&2
    fi
fi

export PYTHONPATH="${here}${PYTHONPATH:+:${PYTHONPATH}}"

# Python 3.11+: prevent Python from prepending cwd / script-dir to sys.path.
# Without this, `cd workarea && python -m auto_ext` would shadow our package
# with any same-named auto_ext/ that happens to live at workarea root (a real
# incident: a user had a separate, unrelated `auto_ext/` project there).
# PYTHONPATH still works -- only the implicit cwd/script-dir entry is dropped.
export PYTHONSAFEPATH=1

cd "${workarea}"

# `test` is a launcher convenience, NOT an auto_ext subcommand.
# Forward any extra args to pytest (e.g. `./run.sh test tests/core -k progress`).
# Default to running the whole suite when no args given.
if [ "${1:-}" = "test" ]; then
    shift
    if [ "$#" -eq 0 ]; then
        exec "${py}" -m pytest "${here}/tests"
    else
        exec "${py}" -m pytest "$@"
    fi
fi

exec "${py}" -m auto_ext "$@"
