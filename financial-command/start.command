#!/bin/bash
# Canonical FCC launcher (treasury worktree).
set -euo pipefail
cd "$(dirname "$0")/.."
echo "Starting Financial Command Center (treasury worktree) on port 8000..."
echo "  root: $(pwd)"
echo "Press Ctrl+C to stop."
(sleep 1.2 && open "http://127.0.0.1:8000/financial-command/index.html") &
exec python3 financial-command/server.py --port 8000
