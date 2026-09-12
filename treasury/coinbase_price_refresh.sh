#!/usr/bin/env bash
# Refresh Coinbase BTC/USD spot on the Pi for FCC mining valuation.
#
# Issue #695 — Pi (prism-agent) is the price producer. Public HTTP, no CLI,
# no node, no secrets. Liquid USDC/BTC balances stay Mac-CLI (out of scope).
# Mac must not push coinbase_latest.json (no dual-writer).
#
# Schedule every 2h (under FCC 6h stale threshold):
#   Pi systemd: treasury/deploy/coinbase-price-refresh.timer
#
# Env:
#   TREASURY_SKIP_PUSH_PI=1   Pi unit sets this; do not Mac→Pi overwrite
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOG_DIR}/coinbase_price_refresh_${STAMP}.log"
exec >>"$LOG" 2>&1
echo "=== coinbase_price_refresh ${STAMP} host=${FCC_HOST_TAG:-local} ==="

rc=0
if python3 -m treasury.coinbase_price_sync; then
  echo "coinbase_price_sync: ok"
else
  rc=$?
  echo "WARN: coinbase_price_sync exit ${rc}"
fi

# Producer does not push. Mac→Pi coinbase_latest.json is omitted from push_files.
if [[ "${TREASURY_SKIP_PUSH_PI:-}" != "1" && "${FCC_HOST_TAG:-}" != "prism" ]]; then
  echo "WARN: unexpected non-Pi host; refusing snapshot push (no dual-write)"
fi

# Re-merge treasury offline so capital-flows / Cash Streams pick up the price
python3 -m treasury.run_treasury --offline --skip-coinbase 2>/dev/null || true

ln -sfn "$LOG" "${LOG_DIR}/coinbase_price_refresh_latest.log" 2>/dev/null \
  || cp "$LOG" "${LOG_DIR}/coinbase_price_refresh_latest.log"

if [[ "$rc" -eq 0 ]]; then
  echo "=== coinbase_price_refresh done (ok) ==="
  exit 0
fi
echo "=== coinbase_price_refresh done (failed) ==="
exit 1
