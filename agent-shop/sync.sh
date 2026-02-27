#!/usr/bin/env bash
set -uo pipefail

TESTBED_PATH="${1:-$HOME/code/personal/agent-shop-testbed/agent-shop/}"

if [[ ! -d "$TESTBED_PATH" ]]; then
  echo "Error: destination '$TESTBED_PATH' does not exist" >&2
  exit 1
fi

echo "Syncing to: $TESTBED_PATH"
count=0
for f in *.py; do
  cp "$f" "$TESTBED_PATH"
  echo "  Copied $f"
  count=$((count + 1))
done

cp sync.sh "$TESTBED_PATH" 2>/dev/null && echo "  Copied sync.sh" && count=$((count + 1))

echo "Done. $count files copied."
echo "Remember to commit and push in the testbed repo."
