#!/bin/bash
# Prefer the oomwoo-status worktree so this TLD stays on feature/oomwoo-status.
set -euo pipefail
WT_BASE="${PERSONAL_WORKSPACE_WORKTREES:-$HOME/personal-workspace-worktrees}"
WT_ROOT="$WT_BASE/oomwoo-status"
if [ -d "$WT_ROOT/oomwoo" ]; then
  ROOT="$WT_ROOT"
  echo "Using oomwoo worktree: $ROOT"
else
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
exec python3 oomwoo/server.py --port 8798
