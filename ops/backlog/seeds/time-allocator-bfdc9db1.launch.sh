#!/bin/bash
# Launch a Grok Build planning session for backlog item: Time allocator
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
OBJ="ops/backlog/seeds/time-allocator-bfdc9db1.goal.txt"
SEED="ops/backlog/seeds/time-allocator-bfdc9db1.md"
echo "=== Backlog goal: Time allocator ==="
echo "Seed: $SEED"
echo "Objective file: $OBJ"
echo ""
echo "Paste into Grok (or run /goal with this text):"
echo "----------------------------------------------"
cat "$OBJ"
echo "----------------------------------------------"
echo ""
# Copy to clipboard on macOS when available
if command -v pbcopy >/dev/null 2>&1; then
  cat "$OBJ" | pbcopy
  echo "(Objective copied to clipboard)"
fi
echo ""
echo "Starting Grok in personal-workspace…"
echo "After it opens: /goal and paste (or Cmd+V)."
if command -v grok >/dev/null 2>&1; then
  exec grok
elif [ -x "$HOME/.grok/bin/grok" ]; then
  exec "$HOME/.grok/bin/grok"
else
  echo "grok CLI not found on PATH. Open Grok manually in: $ROOT"
  exit 0
fi
