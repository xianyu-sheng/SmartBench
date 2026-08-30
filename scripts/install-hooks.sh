#!/usr/bin/env bash
# install-hooks.sh: symlink versioned hooks from scripts/ into .git/hooks/.
# Run once after cloning: `bash scripts/install-hooks.sh`
# or via `make install-hooks`.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"
SCRIPTS_DIR="$REPO_ROOT/scripts"

HOOKS=(pre-push)

for hook in "${HOOKS[@]}"; do
    src="$SCRIPTS_DIR/$hook"
    dst="$HOOKS_DIR/$hook"

    if [[ ! -f "$src" ]]; then
        echo "✗ Hook source not found: $src" >&2
        exit 1
    fi

    chmod +x "$src"

    if [[ -e "$dst" && ! -L "$dst" ]]; then
        echo "⚠ $dst exists and is not a symlink — skipping (remove it manually to install)."
        continue
    fi

    ln -sf "$src" "$dst"
    echo "✓ Installed $hook → .git/hooks/$hook"
done

echo ""
echo "All hooks installed. They will run automatically on the relevant git operations."
