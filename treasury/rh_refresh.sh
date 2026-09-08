#!/usr/bin/env bash
# Refresh Robinhood dual-account snapshot for FCC.
#
# Issue #518 — prism/Pi is the SoT producer:
#   1) Producer (TREASURY_RH_ROLE=producer): local Grok + robinhood-trading MCP
#   2) Consumer (Mac default): pull Pi snapshot only — never push RH back
#   3) Backup (Mac, optional): local MCP for laptop FCC; still no RH push
#   4) Auth/MCP failure leaves existing as_of untouched (honest stale)
#
# Schedule every 3h (under FCC ~6h NTFY stale threshold):
#   Pi:      treasury/deploy/rh-refresh.timer
#   Mac:     do not load launchd (backup-only). See treasury/deploy/RH_PRODUCER.md
#
# Env:
#   TREASURY_RH_ROLE=producer|backup|consumer
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
ROLE="${TREASURY_RH_ROLE:-}"
echo "=== rh_refresh ${STAMP} (role=${ROLE:-auto} SoT=prism/Pi) ==="

# Role-aware sync (producer: local MCP; consumer: Pi pull; no RH dual-write)
if python3 -m treasury.rh_snapshot_sync --print; then
  echo "=== rh_refresh done (ok) ==="
  ln -sfn "$LOG" "${LOG_DIR}/rh_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/rh_refresh_latest.log"
  exit 0
fi

echo "WARN: rh_snapshot_sync failed — leaving existing snapshot (no invent)"
# Re-evaluate offline + NTFY (title includes producer host + error class)
python3 -m treasury.fund_manager --write --notify 2>/dev/null || true
python3 -m treasury.run_treasury --offline 2>/dev/null || true
ln -sfn "$LOG" "${LOG_DIR}/rh_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/rh_refresh_latest.log"
echo "=== rh_refresh done (failed) ==="
exit 1
