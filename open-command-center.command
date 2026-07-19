#!/bin/bash
# One-click launcher for the Orchestra top-level command center
# Prefers the Orchestra worktree when present (multi-dashboard safe).

set -euo pipefail
WT_BASE="${PERSONAL_WORKSPACE_WORKTREES:-$HOME/personal-workspace-worktrees}"
WT_ROOT="$WT_BASE/orchestra"
if [ -d "$WT_ROOT/orchestra" ]; then
  ROOT="$WT_ROOT"
  echo "Using Orchestra worktree: $ROOT (work/orchestra)"
else
  ROOT="$(cd "$(dirname "$0")" && pwd)"
fi
cd "$ROOT"

echo "Starting Orchestra Command Center..."
echo ""
echo "UI:  http://localhost:8790/"
echo "API: http://localhost:8790/api/orchestra"
echo "     domains · synergies · priorities / action plan"
echo ""
echo "Subordinates (start separately if needed):"
echo "  financial-command    :8000"
echo "  projects-dashboard   :8765"
echo "  holistic             :8770"
echo "  iot                  :8780"
echo "  resistance-dashboard :8787"
echo ""
echo "Worktrees: python3 projects-dashboard/worktrees.py ensure"
echo "Press Ctrl+C to stop."
echo ""
python3 orchestra/server.py --port 8790
