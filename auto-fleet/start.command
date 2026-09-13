#!/bin/bash
# Prefer the auto-fleet worktree so this TLD stays on feature/auto-fleet-mvp.
set -euo pipefail
WT_BASE="${PERSONAL_WORKSPACE_WORKTREES:-$HOME/personal-workspace-worktrees}"
WT_ROOT="$WT_BASE/auto-fleet"
if [ -d "$WT_ROOT/auto-fleet" ]; then
  ROOT="$WT_ROOT"
  echo "Using auto-fleet worktree: $ROOT"
else
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
exec python3 auto-fleet/server.py --port 8796
