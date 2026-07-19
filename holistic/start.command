#!/bin/bash
# Double-click or: bash holistic/start.command
# Prefer Time worktree so multi-dashboard work stays on work/holistic.
set -euo pipefail
WT_BASE="${PERSONAL_WORKSPACE_WORKTREES:-$HOME/personal-workspace-worktrees}"
WT_ROOT="$WT_BASE/holistic"
if [ -d "$WT_ROOT/holistic" ]; then
  ROOT="$WT_ROOT"
  echo "Using Time worktree: $ROOT (work/holistic)"
else
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi
cd "$ROOT" || exit 1
exec python3 holistic/server.py --port 8770