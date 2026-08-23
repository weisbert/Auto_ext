#!/bin/sh
# Install the pre-commit gate. Hooks live in .git/, which is not versioned, so
# this has to be run once per clone.
set -e
ROOT=$(git rev-parse --show-toplevel)
cat > "$ROOT/.git/hooks/pre-commit" <<'HOOK'
#!/bin/sh
exec sh "$(git rev-parse --show-toplevel)/scripts/redzone_scan.sh" --staged
HOOK
chmod +x "$ROOT/.git/hooks/pre-commit"
echo "installed: .git/hooks/pre-commit -> scripts/redzone_scan.sh --staged"
