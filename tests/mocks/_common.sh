# Shared helpers for mock EDA binaries.
#
# Each mock accepts either the real tool's argv plus an optional ``--fail``
# flag (anywhere in argv) and returns nonzero when requested.
#
# The mocks intentionally do the absolute minimum to look like the real
# thing: echo a one-line banner, create a zero-byte artifact where the
# runner expects one, and exit.
#
# Don't ``set -e`` here; callers may want to handle failures themselves.
set -u

_mock_should_fail() {
    local arg
    for arg in "$@"; do
        [ "$arg" = "--fail" ] && return 0
    done
    # Integration tests trigger a specific mock's failure via an env var so the
    # runner (which does not know about --fail) can exercise the failure path.
    # AUTO_EXT_MOCK_FORCE_FAIL is a comma-separated list of mock basenames.
    local name
    name=$(basename "$0")
    case ",${AUTO_EXT_MOCK_FORCE_FAIL:-}," in
        *",${name},"*) return 0 ;;
    esac
    return 1
}

# Read a flag value from argv. Supports "--flag value" only (not "--flag=value").
# Usage: _mock_flag --runset "$@"
_mock_flag() {
    local want="$1"; shift
    local i=1
    local n=$#
    while [ $i -le $n ]; do
        local a
        eval "a=\${$i}"
        if [ "$a" = "$want" ]; then
            local j=$((i + 1))
            [ $j -le $n ] && eval "echo \${$j}"
            return 0
        fi
        i=$((i + 1))
    done
    return 1
}

_mock_out_dir() {
    # Prefer $AUTO_EXT_MOCK_OUT if set, else ./mock_out relative to cwd.
    echo "${AUTO_EXT_MOCK_OUT:-./mock_out}"
}
