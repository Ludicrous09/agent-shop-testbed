#!/usr/bin/env bash
set -euo pipefail

TESTBED_PATH="${1:-$HOME/code/personal/agent-shop-testbed/agent-shop/}"

FILES=(
    orchestrator.py
    worker.py
    reviewer.py
    fixer.py
    issue_source.py
    task_manager.py
)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Syncing to: $TESTBED_PATH"

for file in "${FILES[@]}"; do
    cp "$SCRIPT_DIR/$file" "$TESTBED_PATH/$file"
    echo "  Copied $file"
done

echo ""
echo "Done. Remember to commit and push in the testbed repo:"
echo "  cd $TESTBED_PATH && git add -A && git commit -m 'sync: update agent-shop files' && git push"
