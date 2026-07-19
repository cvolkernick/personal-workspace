#!/usr/bin/env bash
# Start Resistance Dashboard from the Fitness worktree when available so
# multi-dashboard work does not serve stale code from another work/* branch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WT_BASE="${PERSONAL_WORKSPACE_WORKTREES:-$HOME/personal-workspace-worktrees}"
WT_ROOT="$WT_BASE/resistance-dashboard"
WT_APP="$WT_ROOT/resistance-dashboard"

# Prefer dedicated worktree (branch work/resistance-dashboard)
if [ -d "$WT_APP" ] && [ -f "$WT_APP/server.py" ]; then
  APP_DIR="$WT_APP"
  WORKSPACE_DIR="$WT_ROOT"
  echo "Using Fitness worktree: $WORKSPACE_DIR (work/resistance-dashboard)"
else
  APP_DIR="$SCRIPT_DIR"
  WORKSPACE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  # Warn if main monorepo is not on the fitness branch
  if command -v git >/dev/null 2>&1; then
    br="$(git -C "$WORKSPACE_DIR" branch --show-current 2>/dev/null || true)"
    if [ -n "$br" ] && [ "$br" != "work/resistance-dashboard" ] && [ "$br" != "master" ]; then
      echo "WARNING: monorepo is on '$br', not work/resistance-dashboard."
      echo "  Fitness code may be stale. Create the worktree:"
      echo "  python3 \"$WORKSPACE_DIR/projects-dashboard/worktrees.py\" ensure resistance-dashboard"
      echo "  Then re-run this launcher."
    fi
  fi
fi

cd "$APP_DIR"

# Load persistent secrets (created for you at ~/.config/resistance-dashboard/env)
if [ -f "$HOME/.config/resistance-dashboard/env" ]; then
  # shellcheck disable=SC1090
  source "$HOME/.config/resistance-dashboard/env"
fi

# When using the Fitness worktree, always point data at that tree (ignore a
# stale LOCAL_WORKSPACE_DIR exported from another dashboard session).
if [ -d "$WT_APP" ] && [ -f "$WT_APP/server.py" ]; then
  export LOCAL_WORKSPACE_DIR="$WORKSPACE_DIR"
else
  export LOCAL_WORKSPACE_DIR="${LOCAL_WORKSPACE_DIR:-$WORKSPACE_DIR}"
fi
export PORT="${PORT:-8787}"

# Free port if a previous instance is still up
if command -v lsof >/dev/null 2>&1; then
  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
fi

echo "Resistance dashboard → http://127.0.0.1:${PORT}/"
echo "LOCAL_WORKSPACE_DIR=$LOCAL_WORKSPACE_DIR"
exec python3 server.py "$PORT"
