#!/bin/bash
# Always start the canonical treasury-worktree FCC (not monorepo static server).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WT="${FCC_WORKTREE_ROOT:-$HOME/personal-workspace-worktrees/treasury}"
SERVER="$WT/financial-command/server.py"
if [[ ! -f "$SERVER" ]]; then
  echo "Treasury worktree FCC missing: $SERVER"
  echo "Run: python3 projects-dashboard/worktrees.py ensure"
  exit 1
fi
echo "Starting Financial Command Center from treasury worktree:"
echo "  $SERVER"
echo "Press Ctrl+C to stop."
(sleep 1.2 && open "http://127.0.0.1:8000/financial-command/index.html") &
cd "$WT"
exec python3 financial-command/server.py --port 8000
