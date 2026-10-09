#!/usr/bin/env bash
# Refresh the Personal Expense Sheet snapshot for FCC (#1053).
#
# Prism FCC starts with --offline, so nothing on that host was pulling the
# sheet. This clock writes treasury/snapshots/expenses_latest.json.
# A failed pull leaves the existing as_of alone (expenses_sync does not
# rewrite the file on live_error).
#
# Schedule every 3h (under the 12h Sheet stale flag):
#   Pi systemd: treasury/deploy/expenses-refresh.timer
#
# The sheet export is public. No token. Does not push the file anywhere.
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOG_DIR}/expenses_refresh_${STAMP}.log"
exec >>"$LOG" 2>&1
echo "=== expenses_refresh ${STAMP} host=${FCC_HOST_TAG:-local} ==="

if python3 -m treasury.expenses_sync; then
  echo "=== expenses_refresh done (ok) ==="
  ln -sfn "$LOG" "${LOG_DIR}/expenses_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/expenses_refresh_latest.log"
  exit 0
fi

echo "WARN: expenses_sync failed — leaving existing snapshot as_of unchanged"
ln -sfn "$LOG" "${LOG_DIR}/expenses_refresh_latest.log" 2>/dev/null || cp "$LOG" "${LOG_DIR}/expenses_refresh_latest.log"
echo "=== expenses_refresh done (failed) ==="
exit 1
