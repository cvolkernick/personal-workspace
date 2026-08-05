#!/usr/bin/env bash
# Prune stale git worktrees for personal-workspace (Mac + Pi).
# Safe defaults: never touch main monorepo; never remove dirty worktrees.
#
# Usage:
#   bash ops/prune_stale_worktrees.sh           # apply prune + repair
#   bash ops/prune_stale_worktrees.sh --dry-run
#   bash ops/prune_stale_worktrees.sh --ensure-areas
#
# Env:
#   PERSONAL_WORKSPACE   monorepo root (default: ~/personal-workspace)
#   PERSONAL_WORKSPACE_WORKTREES  worktree base (default: ~/personal-workspace-worktrees)

set -euo pipefail

DRY=0
ENSURE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --ensure-areas) ENSURE=1 ;;
    -h|--help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
  esac
done

ROOT="${PERSONAL_WORKSPACE:-$HOME/personal-workspace}"
if [[ ! -d "$ROOT/.git" && ! -f "$ROOT/.git" ]]; then
  # worktree checkouts have .git file; still OK
  if [[ ! -e "$ROOT/.git" ]]; then
    echo "ERROR: monorepo not found at $ROOT" >&2
    exit 1
  fi
fi

LOG_DIR="${PERSONAL_WORKSPACE_LOG_DIR:-$HOME/Library/Logs/personal-workspace}"
# Linux / Pi fallback
if [[ ! -d "$(dirname "$LOG_DIR")" ]] || [[ "$(uname -s)" == "Linux" ]]; then
  LOG_DIR="${PERSONAL_WORKSPACE_LOG_DIR:-$ROOT/ops/logs}"
fi
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/prune-stale-worktrees.log"
ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

{
  echo "==== $(ts) host=$(hostname -s 2>/dev/null || hostname) root=$ROOT dry=$DRY ===="
  cd "$ROOT"
  git fetch origin --prune 2>&1 || true

  WT_PY="$ROOT/projects-dashboard/worktrees.py"
  if [[ ! -f "$WT_PY" ]]; then
    echo "ERROR: missing $WT_PY" >&2
    exit 1
  fi

  if [[ "$DRY" -eq 1 ]]; then
    python3 "$WT_PY" prune-stale
    python3 "$WT_PY" repair-areas
  else
    python3 "$WT_PY" prune-stale --apply
    python3 "$WT_PY" repair-areas --apply
  fi

  if [[ "$ENSURE" -eq 1 ]]; then
    python3 "$WT_PY" ensure
  fi

  echo "git worktree list:"
  git worktree list
  echo "==== $(ts) done ===="
} >>"$LOG" 2>&1

# Also mirror last run to stdout when interactive
if [[ -t 1 ]]; then
  tail -n 80 "$LOG"
fi

exit 0
