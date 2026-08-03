#!/usr/bin/env bash
# Refresh Braiins Pool mining snapshot for local FCC + push to Pi.
#
# Mac is the live producer (token at ~/.config/braiins/token). Pi has no pool
# token by default — it serves braiins_latest.json offline after we push.
#
# Schedule every 4h (under FCC 6h stale threshold), e.g.:
#   launchd: treasury/deploy/com.personalworkspace.braiins-refresh.plist
#   cron:    25 */4 * * * /path/to/treasury/braiins_refresh.sh
#
# Env:
#   TREASURY_SKIP_PUSH_PI=1   skip Mac → Pi snapshot push
#   BRAIINS_POOL_TOKEN / ~/.config/braiins/token
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOG_DIR}/braiins_refresh_${STAMP}.log"
exec >>"$LOG" 2>&1
echo "=== braiins_refresh ${STAMP} ==="

rc=0
if python3 -m treasury.braiins_sync; then
  echo "braiins_sync: ok"
else
  rc=$?
  echo "WARN: braiins_sync exit ${rc}"
fi

# Best-effort push even if sync failed but an older good file exists
if python3 -m treasury.rh_snapshot_sync --push-only; then
  echo "push_to_pi: ok (or disabled)"
else
  echo "WARN: push_to_pi failed (Pi off-LAN is fine)"
fi

# Re-merge treasury offline so capital-flows / main dash pick up mining pane
python3 -m treasury.run_treasury --offline 2>/dev/null || true

ln -sfn "$LOG" "${LOG_DIR}/braiins_refresh_latest.log" 2>/dev/null \
  || cp "$LOG" "${LOG_DIR}/braiins_refresh_latest.log"

if [[ "$rc" -eq 0 ]]; then
  echo "=== braiins_refresh done (ok) ==="
  exit 0
fi
echo "=== braiins_refresh done (failed) ==="
exit 1
