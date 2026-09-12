#!/usr/bin/env bash
# Refresh YNAB cash-balance snapshots for FCC (One Card, RH Checking, X Money).
#
# Issue #668 — ynab_sync was only a sidecar of fund-manager-daily (weekdays
# 12:30 ET) and fund-manager-bp-poll (market hours). Nights/weekends and the
# Pi live tree never ran it, so as_of froze. This job is the dedicated clock.
#
# Does not invent as_of: ynab_sync preserves a good on-disk snapshot when the
# live pull is empty or errors.
#
# Schedule every 3h (under FCC 6h stale threshold):
#   Pi systemd: treasury/deploy/ynab-refresh.timer
#   Mac launchd: treasury/deploy/com.personalworkspace.ynab-refresh.plist
#
# Auth: ~/.config/ynab/token or YNAB_TOKEN (never commit tokens).
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOG_DIR}/ynab_refresh_${STAMP}.log"
exec >>"$LOG" 2>&1
echo "=== ynab_refresh ${STAMP} host=${FCC_HOST_TAG:-local} ==="

if python3 -m treasury.ynab_sync; then
  echo "=== ynab_refresh done (ok) ==="
  ln -sfn "$LOG" "${LOG_DIR}/ynab_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/ynab_refresh_latest.log"
  exit 0
fi

echo "WARN: ynab_sync failed — leaving existing snapshots (no invent)"
ln -sfn "$LOG" "${LOG_DIR}/ynab_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/ynab_refresh_latest.log"
echo "=== ynab_refresh done (failed) ==="
exit 1
