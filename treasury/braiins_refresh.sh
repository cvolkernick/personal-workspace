#!/usr/bin/env bash
# Refresh Braiins Pool mining snapshot for FCC.
#
# Issue #689 — Pi (prism-agent) is the live producer. Token lives at
# ~/.config/braiins/token (mode 600) on this host, never treasury/config.json.
# Mac launchd com.personalworkspace.braiins-refresh is retired (no dual-writer).
#
# Schedule every 4h (under FCC 6h stale threshold):
#   Pi systemd: treasury/deploy/braiins-refresh.timer
#
# Env:
#   TREASURY_SKIP_PUSH_PI=1   Pi unit sets this; do not Mac→Pi overwrite
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
echo "=== braiins_refresh ${STAMP} host=${FCC_HOST_TAG:-local} ==="

rc=0
if python3 -m treasury.braiins_sync; then
  echo "braiins_sync: ok"
else
  rc=$?
  echo "WARN: braiins_sync exit ${rc}"
fi

# Producer does not push. Mac→Pi braiins_latest.json is omitted from push_files.
if [[ "${TREASURY_SKIP_PUSH_PI:-}" != "1" && "${FCC_HOST_TAG:-}" != "prism" ]]; then
  echo "WARN: unexpected non-Pi host; refusing snapshot push (no dual-write)"
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
