#!/usr/bin/env bash
# Open always-on Resistance / Fitness dashboard on the Pi (no local server).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# Prefer monorepo root that contains deploy/open_dashboard.sh
if [[ ! -f "$ROOT/deploy/open_dashboard.sh" ]]; then
  # worktree layout: resistance-dashboard/resistance-dashboard/start.sh
  if [[ -f "$SCRIPT_DIR/../deploy/open_dashboard.sh" ]]; then
    ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  elif [[ -f "$HOME/personal-workspace/deploy/open_dashboard.sh" ]]; then
    ROOT="$HOME/personal-workspace"
  fi
fi
exec bash "$ROOT/deploy/open_dashboard.sh" resistance-dashboard
