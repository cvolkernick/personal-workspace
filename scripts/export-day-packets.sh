#!/usr/bin/env bash
# Continuous day_plan packet refresh for Orchestra (systems integration A).
#
# Board (Forge / Cadence): scripts/buzz-board day-export → ops/board/day_constraints.json
# Fit (Frankenfit write path): best-effort hit FitDash /api/day_constraints so the
#   existing load_dashboard exporter writes fitness/data/day_constraints.json.
# Orchestra only *reads* these files — never dual-writes domain SoT.
#
# Usage (from monorepo root or any cwd):
#   bash scripts/export-day-packets.sh
#   bash scripts/export-day-packets.sh --board-only
#   WORKSPACE_DIR=/path/to/personal-workspace bash scripts/export-day-packets.sh
#
# Auth for Board: GITHUB_TOKEN / GH_TOKEN, or `gh auth token` fallback.
# Fit: FitDash must be up on FITDASH_URL (default http://127.0.0.1:8787).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${WORKSPACE_DIR:-${LOCAL_WORKSPACE_DIR:-$ROOT}}"
BOARD_ONLY=0
FIT_ONLY=0
JSON=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --board-only) BOARD_ONLY=1; shift ;;
    --fit-only) FIT_ONLY=1; shift ;;
    --json) JSON=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

cd "$DIR"

# Load operator secrets (never echo). Prefer env already set.
if [[ -f "${HOME}/.config/workflow-scheduler.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HOME}/.config/workflow-scheduler.env"
  set +a
fi

# Prefer a live gh CLI token when env token is missing or stale (Mac operators).
if command -v gh >/dev/null 2>&1; then
  GH_LIVE="$(gh auth token 2>/dev/null || true)"
  if [[ -n "${GH_LIVE:-}" ]]; then
    export GITHUB_TOKEN="$GH_LIVE"
    export GH_TOKEN="$GH_LIVE"
  fi
fi

LOG_TAG="export-day-packets"
log() { echo "[$LOG_TAG] $*"; }

BOARD_RC=0
FIT_RC=0
BOARD_PATH="$DIR/ops/board/day_constraints.json"
FIT_PATH="$DIR/fitness/data/day_constraints.json"

if [[ "$FIT_ONLY" -eq 0 ]]; then
  log "board day-export → $BOARD_PATH"
  if [[ -x "$DIR/scripts/buzz-board" ]]; then
    set +e
    if [[ "$JSON" -eq 1 ]]; then
      "$DIR/scripts/buzz-board" day-export --workspace "$DIR" --json
      BOARD_RC=$?
    else
      "$DIR/scripts/buzz-board" day-export --workspace "$DIR"
      BOARD_RC=$?
    fi
    set -e
  else
    log "ERROR: scripts/buzz-board missing"
    BOARD_RC=4
  fi
  if [[ -f "$BOARD_PATH" ]]; then
    log "board packet present ($(wc -c <"$BOARD_PATH" | tr -d ' ') bytes)"
  else
    log "WARN: board packet not on disk after export"
  fi
fi

if [[ "$BOARD_ONLY" -eq 0 ]]; then
  # FitDash writes on load_dashboard / /api/day_constraints (Frankenfit).
  # Platform continuous path: poke the API so the file is refreshed without
  # Orchestra inventing body gates.
  FITDASH_URL="${FITDASH_URL:-http://127.0.0.1:8787}"
  log "fit day_constraints poke → ${FITDASH_URL}/api/day_constraints"
  set +e
  HTTP_CODE=$(curl -sS -m 25 -o /tmp/fit-day-constraints-$$.json -w "%{http_code}" \
    "${FITDASH_URL}/api/day_constraints" 2>/tmp/fit-day-constraints-$$.err)
  CURL_RC=$?
  set -e
  if [[ "$CURL_RC" -ne 0 || "$HTTP_CODE" != "200" ]]; then
    log "WARN: FitDash poke failed (curl_rc=${CURL_RC} http=${HTTP_CODE}) — Fit packet may go stale"
    FIT_RC=1
    [[ -f /tmp/fit-day-constraints-$$.err ]] && log "  $(head -c 200 /tmp/fit-day-constraints-$$.err)"
  else
    log "FitDash poke ok (http 200)"
    FIT_RC=0
  fi
  rm -f /tmp/fit-day-constraints-$$.json /tmp/fit-day-constraints-$$.err
  if [[ -f "$FIT_PATH" ]]; then
    log "fit packet present ($(wc -c <"$FIT_PATH" | tr -d ' ') bytes)"
  else
    log "WARN: fitness/data/day_constraints.json still absent (FitDash write path)"
    FIT_RC=1
  fi
fi

if [[ "$JSON" -eq 1 ]]; then
  python3 - <<PY
import json
from pathlib import Path
print(json.dumps({
  "workspace": "$DIR",
  "board_rc": $BOARD_RC,
  "fit_rc": $FIT_RC,
  "board_path": "$BOARD_PATH",
  "fit_path": "$FIT_PATH",
  "board_exists": Path("$BOARD_PATH").is_file(),
  "fit_exists": Path("$FIT_PATH").is_file(),
}, indent=2))
PY
fi

# Board fail still wrote honest fail packet — exit 0 if either path refreshed.
# Non-zero only when board export binary missing (hard fail).
if [[ "$FIT_ONLY" -eq 0 && "$BOARD_RC" -eq 4 ]]; then
  exit 4
fi
exit 0
