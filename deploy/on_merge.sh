#!/usr/bin/env bash
# Path-scoped merge → Pi deploy orchestrator (Phase 1 / issue #25).
#
# Modes:
#   local   — run on the Pi after git already updated (used by workspace_sync.sh)
#   remote  — run from Mac: path map + optional install_remote.sh --only
#   dry-run — map only (pass --dry-run)
#
# Usage (monorepo root):
#   bash deploy/on_merge.sh --before <sha> --after <sha> [--mode local|remote] [--dry-run]
#   bash deploy/on_merge.sh --before HEAD~1 --after HEAD --dry-run
#   bash deploy/on_merge.sh --paths orchestra/server.py,iot/server.py --mode local
#
# Safety:
#   - Never thrash-all units
#   - Never auto-deploy treasury/secrets/deploy glue
#   - One deploy at a time (lockfile)
#   - Health-check mapped services after restart
#   - Optional Buzz notify to #workflow
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAP_PY="$ROOT/deploy/map_changed_paths.py"
LOCK_DIR="${SDLC_DEPLOY_LOCK_DIR:-${XDG_RUNTIME_DIR:-/tmp}}"
LOCK_FILE="$LOCK_DIR/sdlc-on-merge.lock"
CHANNEL_UUID="${BUZZ_WORKFLOW_CHANNEL:-db0e8f97-0c81-4976-b299-1c460b87134e}"
PI_HOST="${PI_HOST:-${DASHBOARD_HOST:-192.168.100.98}}"
REMOTE_TARGET="${SDLC_DEPLOY_REMOTE:-prism-agent@${PI_HOST}}"
LOG_TAG="on-merge"

BEFORE=""
AFTER=""
MODE="local"
DRY=0
NOTIFY=1
DO_DEPLOY=1
PATHS_CSV=""
SKIP_LOCK=0

log() { echo "[$LOG_TAG] $*"; }

usage() {
  sed -n '2,22p' "$0"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --before) BEFORE="$2"; shift 2 ;;
    --after) AFTER="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --paths) PATHS_CSV="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --no-notify) NOTIFY=0; shift ;;
    --no-deploy) DO_DEPLOY=0; shift ;;
    --skip-lock) SKIP_LOCK=1; shift ;;
    --remote) REMOTE_TARGET="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

cd "$ROOT"

if [[ ! -f "$MAP_PY" ]]; then
  log "ERROR: missing $MAP_PY"
  exit 1
fi

map_json() {
  if [[ -n "$PATHS_CSV" ]]; then
    local args=()
    local p
    local IFS=','
    for p in $PATHS_CSV; do
      p="$(echo "$p" | xargs)"
      [[ -n "$p" ]] && args+=(--path "$p")
    done
    python3 "$MAP_PY" "${args[@]}" --format json
  else
    AFTER="${AFTER:-HEAD}"
    if [[ -z "$BEFORE" ]]; then
      BEFORE="$(git rev-parse HEAD^ 2>/dev/null || echo none)"
    fi
    python3 "$MAP_PY" --before "$BEFORE" --after "$AFTER" --format json
  fi
}

RESULT="$(map_json)"
ACTION="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("action","noop"))')"
UNITS_CSV="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin).get("units") or []))')"
ONLY="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("only") or "")')"
SERVICE_KEYS="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin).get("service_keys") or []))')"
BEFORE_SHA="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("before") or "")')"
AFTER_SHA="$(printf '%s' "$RESULT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("after") or "")')"
SUMMARY="$(printf '%s' "$RESULT" | python3 -c '
import json, sys
d = json.load(sys.stdin)
units = ",".join(d.get("units") or []) or "-"
print(
    "action=%s units=%s manual=%s ignored=%s unmapped=%s thrash_all=%s"
    % (
        d.get("action"),
        units,
        len(d.get("manual_paths") or []),
        len(d.get("ignored_paths") or []),
        len(d.get("unmapped_paths") or []),
        d.get("thrash_all"),
    )
)
')"

# Prefer CLI before/after when provided (git mode may omit them on --paths)
BEFORE="${BEFORE:-$BEFORE_SHA}"
AFTER="${AFTER:-$AFTER_SHA}"

log "$SUMMARY"

if [[ "$DRY" -eq 1 ]]; then
  log "dry-run — no restart/deploy/notify"
  printf '%s\n' "$RESULT"
  exit 0
fi

acquire_lock() {
  if [[ "$SKIP_LOCK" -eq 1 ]]; then
    return 0
  fi
  mkdir -p "$LOCK_DIR"
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
      log "another deploy holds $LOCK_FILE — exit"
      exit 0
    fi
  else
    if [[ -f "$LOCK_FILE" ]]; then
      local age mtime now
      mtime="$(stat -f %m "$LOCK_FILE" 2>/dev/null || stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0)"
      now="$(date +%s)"
      age=$((now - mtime))
      if [[ "$age" -lt 900 ]]; then
        log "lock busy (age ${age}s) — exit"
        exit 0
      fi
    fi
    echo $$ >"$LOCK_FILE"
    trap 'rm -f "$LOCK_FILE"' EXIT
  fi
}

health_check() {
  local keys
  # shellcheck disable=SC2206
  keys=($SERVICE_KEYS)
  local ok=0 fail=0 key status url host_override=""
  if [[ ${#keys[@]} -eq 0 ]]; then
    echo "no services to probe"
    return 0
  fi
  if [[ "$MODE" == "local" ]]; then
    host_override="127.0.0.1"
  fi
  for key in "${keys[@]}"; do
    [[ -z "$key" || "$key" == "panamerica" || "$key" == "horizon" ]] && continue
    if [[ -n "$host_override" ]]; then
      if PI_HOST="$host_override" python3 "$ROOT/dashboard_endpoints.py" --probe "$key" >/dev/null 2>&1; then
        status="ok"
      else
        status="down"
      fi
      url="$(PI_HOST="$host_override" python3 "$ROOT/dashboard_endpoints.py" --health "$key" 2>/dev/null || echo "?")"
    else
      if python3 "$ROOT/dashboard_endpoints.py" --probe "$key" >/dev/null 2>&1; then
        status="ok"
      else
        status="down"
      fi
      url="$(python3 "$ROOT/dashboard_endpoints.py" --health "$key" 2>/dev/null || echo "?")"
    fi
    log "health $key → $status ($url)"
    if [[ "$status" == "ok" ]]; then
      ok=$((ok + 1))
    else
      fail=$((fail + 1))
    fi
  done
  echo "health_ok=$ok health_fail=$fail"
  [[ "$fail" -eq 0 ]]
}

notify_buzz() {
  local body="$1"
  if [[ "$NOTIFY" -ne 1 ]]; then
    return 0
  fi
  if ! command -v buzz >/dev/null 2>&1; then
    log "buzz CLI not available — structured log only"
    log "NOTIFY: $body"
    return 0
  fi
  printf '%s\n' "$body" | buzz messages send --channel "$CHANNEL_UUID" --content - >/dev/null 2>&1 \
    && log "posted result to #workflow" \
    || log "buzz notify failed (non-fatal)"
}

restart_local() {
  local units=() u
  local IFS=','
  for u in $UNITS_CSV; do
    u="$(echo "$u" | xargs)"
    [[ -n "$u" ]] && units+=("$u")
  done
  if [[ ${#units[@]} -eq 0 ]]; then
    log "no units to restart"
    return 0
  fi
  log "restarting (local): ${units[*]}"
  for u in "${units[@]}"; do
    systemctl --user try-restart "$u" 2>/dev/null || log "warn: try-restart $u failed"
  done
  sleep 2
  for u in "${units[@]}"; do
    st="$(systemctl --user is-active "$u" 2>/dev/null || echo unknown)"
    log "unit $u → $st"
  done
}

deploy_remote() {
  if [[ -z "$ONLY" ]]; then
    log "no --only mapping — skip remote install"
    return 0
  fi
  log "remote path-scoped deploy: $REMOTE_TARGET --only $ONLY"
  bash "$ROOT/deploy/install_remote.sh" "$REMOTE_TARGET" --only "$ONLY"
}

STATUS="ok"
DETAIL=""
B8="${BEFORE:0:8}"
A8="${AFTER:0:8}"

case "$ACTION" in
  noop)
    DETAIL="No dashboard/platform code changes — skip restart."
    log "$DETAIL"
    ;;
  manual)
    DETAIL="Manual-only paths changed (treasury/secrets/deploy/etc.) — no auto-restart."
    log "$DETAIL"
    STATUS="manual"
    notify_buzz "$(printf '**SDLC auto-deploy** (manual gate)\n\n`%s`\n\nManual paths present — no unit restart. SHA `%s` → `%s`' \
      "$SUMMARY" "$B8" "$A8")"
    ;;
  unmapped)
    DETAIL="Changed paths are unmapped — no auto-restart (safe default)."
    log "$DETAIL"
    STATUS="unmapped"
    ;;
  restart)
    acquire_lock
    if [[ "$DO_DEPLOY" -eq 1 ]]; then
      if [[ "$MODE" == "remote" ]]; then
        deploy_remote || STATUS="deploy_fail"
      else
        restart_local || STATUS="restart_fail"
      fi
    fi
    HEALTH_LINE="skipped"
    if [[ "$STATUS" == "ok" ]]; then
      if HEALTH_OUT="$(health_check)"; then
        HEALTH_LINE="$HEALTH_OUT"
      else
        HEALTH_LINE="${HEALTH_OUT:-health failed}"
        STATUS="health_fail"
      fi
    fi
    DETAIL="Restarted units: ${UNITS_CSV:-none}. only=${ONLY:-n/a}. $HEALTH_LINE"
    log "$DETAIL"
    notify_buzz "$(printf '**SDLC auto-deploy** `%s`\n\n| | |\n|--|--|\n| Status | **%s** |\n| Units | `%s` |\n| only | `%s` |\n| Health | %s |\n| SHA | `%s` → `%s` |\n| Mode | %s |\n\nIssue: #25 path-scoped merge→Pi' \
      "$STATUS" "$STATUS" "${UNITS_CSV:-—}" "${ONLY:-—}" "$HEALTH_LINE" "$B8" "$A8" "$MODE")"
    ;;
  *)
    log "unknown action: $ACTION"
    STATUS="error"
    ;;
esac

case "$STATUS" in
  health_fail|deploy_fail|restart_fail|error) exit 1 ;;
  *) exit 0 ;;
esac
