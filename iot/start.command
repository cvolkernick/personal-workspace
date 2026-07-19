#!/bin/bash
# Prefer IoT worktree so multi-dashboard work stays on work/iot.
set -euo pipefail
WT_BASE="${PERSONAL_WORKSPACE_WORKTREES:-$HOME/personal-workspace-worktrees}"
WT_ROOT="$WT_BASE/iot"
if [ -d "$WT_ROOT/iot" ]; then
  ROOT="$WT_ROOT"
  echo "Using IoT worktree: $ROOT (work/iot)"
else
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
exec python3 iot/server.py --port 8780
