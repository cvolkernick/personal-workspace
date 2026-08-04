#!/usr/bin/env bash
# Ensure Financial Command Center is listening and healthy on :8000.
# Used by hourly launchd/cron: start the treasury-worktree server if down or hung.
#
# Install (macOS launchd — preferred):
#   cp treasury/deploy/com.personalworkspace.fcc-ensure.plist ~/Library/LaunchAgents/
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personalworkspace.fcc-ensure.plist
#
# Install (cron alternative):
#   crontab -e  →  7 * * * * /path/to/treasury/ensure_fcc_server.sh
#
set -euo pipefail

PORT="${FCC_PORT:-8000}"
ROOT="${FCC_WORKTREE_ROOT:-$HOME/personal-workspace-worktrees/treasury}"
# Prefer worktree; fall back to monorepo shim (re-execs into worktree)
SERVER="$ROOT/financial-command/server.py"
if [[ ! -f "$SERVER" ]]; then
  SERVER="$HOME/personal-workspace/financial-command/server.py"
  ROOT="$HOME/personal-workspace"
fi
LOG_DIR="$ROOT/treasury/snapshots"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/fcc_ensure.log"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

log() { echo "[$STAMP] $*" >>"$LOG"; }

health_ok() {
  local code body
  body="$(curl -sf -m 3 "http://127.0.0.1:${PORT}/api/health" 2>/dev/null || true)"
  if [[ -z "$body" ]]; then
    return 1
  fi
  echo "$body" | grep -q '"ok"[[:space:]]*:[[:space:]]*true'
}

if health_ok; then
  # Quiet success — only log occasionally is unnecessary; skip
  exit 0
fi

log "FCC unhealthy or down on :${PORT} — restarting from $SERVER"

# Drop hung/stale listeners on the port
if command -v lsof >/dev/null 2>&1; then
  pids="$(lsof -t -iTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids:-}" ]]; then
    log "killing hung PIDs: $pids"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true
    sleep 0.5
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
    sleep 0.3
  fi
fi

if [[ ! -f "$SERVER" ]]; then
  log "ERROR: server script not found ($SERVER)"
  exit 1
fi

cd "$ROOT"
# launchd/cron often starts with PATH=/usr/bin:/bin — coinbase CLI lives in homebrew.
# Without this, FCC Refresh reports ok but CB ages never move (file fallback).
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:${HOME}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin${PATH:+:$PATH}"
# Prefer python3 on PATH (homebrew), fall back to system
PY="$(command -v python3 || true)"
if [[ -z "${PY}" ]]; then
  PY="/usr/bin/python3"
fi
# Detach so launchd/cron exit does not kill the server
nohup "$PY" "$SERVER" --port "$PORT" --no-browser --offline \
  >>"$LOG_DIR/fcc_server.out.log" 2>&1 &
disown || true

# Wait briefly for health
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  if health_ok; then
    log "FCC back up (pid $(lsof -t -iTCP:${PORT} -sTCP:LISTEN 2>/dev/null | head -1 || echo '?'))"
    exit 0
  fi
done

log "ERROR: FCC still unhealthy after restart"
exit 1
