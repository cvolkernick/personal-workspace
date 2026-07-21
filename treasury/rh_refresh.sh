#!/usr/bin/env bash
# Refresh Robinhood dual-account snapshot for FCC (keeps "RH trade" feed green).
# RH data only updates via MCP (agent/grok) — pure Python cannot call Robinhood.
#
# Schedule every 3h (under FCC 6h stale threshold), e.g.:
#   launchd: treasury/deploy/com.personalworkspace.rh-refresh.plist
#   cron:    15 */3 * * * /path/to/treasury/rh_refresh.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOG_DIR}/rh_refresh_${STAMP}.log"
exec >>"$LOG" 2>&1
echo "=== rh_refresh ${STAMP} ==="

PROMPT="${ROOT}/treasury/rh_refresh_prompt.txt"
if command -v grok >/dev/null 2>&1 && [[ -f "$PROMPT" ]]; then
  echo "grok headless RH refresh…"
  grok -p "$(cat "$PROMPT")" --cwd "$ROOT" --yolo --output-format plain \
    || echo "WARN: grok RH refresh non-zero"
else
  echo "WARN: grok not available — cannot live-refresh RH snapshot from this host."
  echo "Run an agent session or install grok + robinhood-trading MCP auth."
fi

python3 -m treasury.fund_manager --write 2>/dev/null || true
python3 -m treasury.run_treasury --offline 2>/dev/null || true
ln -sfn "$LOG" "${LOG_DIR}/rh_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/rh_refresh_latest.log"
echo "=== rh_refresh done ==="
