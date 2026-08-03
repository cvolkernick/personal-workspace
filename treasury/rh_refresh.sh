#!/usr/bin/env bash
# Refresh Robinhood dual-account snapshot for local FCC.
#
# P0 (2026-08-03) — Mac is the live producer:
#   1) Local Grok + Robinhood MCP (Mac launchd sets TREASURY_SKIP_PI=1)
#   2) Optional Pi pull only if TREASURY_SKIP_PI is unset and remote ≤ max_age
#   3) On local MCP success, push snapshots → Pi (offline mobile FCC)
#
# Schedule every 3h (under FCC 6h stale threshold), e.g.:
#   launchd: treasury/deploy/com.personalworkspace.rh-refresh.plist
#   cron:    15 */3 * * * /path/to/treasury/rh_refresh.sh
#
# Env:
#   TREASURY_PI_SSH=prism-agent@192.168.100.98
#   TREASURY_PI_ROOT=/home/prism-agent/personal-workspace
#   TREASURY_SKIP_PI=1 / TREASURY_SKIP_LOCAL_MCP=1 / TREASURY_SKIP_PUSH_PI=1
#   TREASURY_RH_MCP_TIMEOUT_S=240
#   TREASURY_RH_MAX_AGE_HOURS=6
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOG_DIR}/rh_refresh_${STAMP}.log"
exec >>"$LOG" 2>&1
echo "=== rh_refresh ${STAMP} (mac MCP primary, push→Pi) ==="

# Smart sync (fund_manager + run_treasury offline on success; push on local MCP)
if python3 -m treasury.rh_snapshot_sync; then
  echo "=== rh_refresh done (ok) ==="
  ln -sfn "$LOG" "${LOG_DIR}/rh_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/rh_refresh_latest.log"
  exit 0
fi

echo "WARN: rh_snapshot_sync failed — leaving existing snapshot"
# Still re-evaluate offline so FCC can pick up any partial state
python3 -m treasury.fund_manager --write 2>/dev/null || true
python3 -m treasury.run_treasury --offline 2>/dev/null || true
ln -sfn "$LOG" "${LOG_DIR}/rh_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/rh_refresh_latest.log"
echo "=== rh_refresh done (failed) ==="
exit 1
