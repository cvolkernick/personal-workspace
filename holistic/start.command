#!/bin/bash
# Double-click or: bash holistic/start.command
# Prefer Time worktree so multi-dashboard work stays on work/holistic
# (main checkout may be on another branch and would serve stale UI).
set -euo pipefail
WT_BASE="${PERSONAL_WORKSPACE_WORKTREES:-$HOME/personal-workspace-worktrees}"
WT_ROOT="$WT_BASE/holistic"
MAIN_CANDIDATE="$(cd "$(dirname "$0")/.." && pwd)"

if [ ! -d "$WT_ROOT/holistic" ]; then
  # Auto-create worktree when missing (common after clone / cleanup).
  if [ -f "$MAIN_CANDIDATE/projects-dashboard/worktrees.py" ]; then
    echo "Holistic worktree missing — creating at $WT_ROOT …"
    python3 "$MAIN_CANDIDATE/projects-dashboard/worktrees.py" ensure holistic || true
  elif [ -f "$HOME/personal-workspace/projects-dashboard/worktrees.py" ]; then
    echo "Holistic worktree missing — creating at $WT_ROOT …"
    python3 "$HOME/personal-workspace/projects-dashboard/worktrees.py" ensure holistic || true
  fi
fi

if [ -d "$WT_ROOT/holistic" ]; then
  ROOT="$WT_ROOT"
  echo "Using Time worktree: $ROOT (work/holistic)"
else
  ROOT="$MAIN_CANDIDATE"
  BRANCH="$(git -C "$ROOT" branch --show-current 2>/dev/null || true)"
  if [ "$BRANCH" != "work/holistic" ] && [ "$BRANCH" != "master" ]; then
    echo "WARNING: no holistic worktree and branch is '$BRANCH' — UI may be stale." >&2
    echo "  Fix: python3 projects-dashboard/worktrees.py ensure holistic" >&2
  fi
fi
cd "$ROOT" || exit 1
# Free port if a dead/wrong-branch server is still bound
if command -v lsof >/dev/null 2>&1; then
  PIDS="$(lsof -ti :8770 2>/dev/null || true)"
  if [ -n "${PIDS:-}" ]; then
    echo "Stopping previous process(es) on :8770 …"
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    sleep 0.4
  fi
fi
exec python3 holistic/server.py --port 8770