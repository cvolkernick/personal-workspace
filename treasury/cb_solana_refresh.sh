#!/usr/bin/env bash
# Mac producer: live Coinbase CLI + Solana RPC, assemble treasury, push to Pi.
#
# Pi Refresh cannot live-fetch Coinbase (no CLI). Solana can live on Pi after
# work/treasury adapters are deployed; this timer still keeps Mac snapshots
# fresh under the FCC 6h stale threshold and pushes them.
#
# Schedule hourly:
#   launchd: treasury/deploy/com.personalworkspace.cb-solana-refresh.plist
#
# Env:
#   TREASURY_SKIP_PUSH_PI=1   skip Mac → Pi snapshot push
#
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:${HOME}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin${PATH:+:$PATH}"
LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="${LOG_DIR}/cb_solana_refresh_${STAMP}.log"
exec >>"$LOG" 2>&1
echo "=== cb_solana_refresh ${STAMP} ==="

PY="$(command -v python3 || true)"
if [[ -z "${PY}" ]]; then
  PY="/usr/bin/python3"
fi

rc=0
if "$PY" -c 'from treasury.adapters import fetch_coinbase_liquid; r=fetch_coinbase_liquid(prefer_live=True); print("coinbase", r.get("source"), r.get("as_of"), r.get("live_error") or "")'; then
  echo "coinbase: ok"
else
  rc=$?
  echo "WARN: coinbase live exit ${rc}"
fi

if "$PY" -m treasury.solana_sync; then
  echo "solana_sync: ok"
else
  sol_rc=$?
  rc="${sol_rc}"
  echo "WARN: solana_sync exit ${sol_rc}"
fi

# Re-merge dashboard JSON from snapshots (no second live CB)
if "$PY" -m treasury.run_treasury --offline --skip-coinbase; then
  echo "run_treasury offline: ok"
else
  echo "WARN: run_treasury offline failed"
fi

if "$PY" -m treasury.rh_snapshot_sync --push-only; then
  echo "push_to_pi: ok (or disabled)"
else
  echo "WARN: push_to_pi failed (Pi off-LAN is fine)"
fi

ln -sfn "$LOG" "${LOG_DIR}/cb_solana_refresh_latest.log" 2>/dev/null \
  || cp "$LOG" "${LOG_DIR}/cb_solana_refresh_latest.log"

if [[ "$rc" -eq 0 ]]; then
  echo "=== cb_solana_refresh done (ok) ==="
  exit 0
fi
echo "=== cb_solana_refresh done (failed) ==="
exit 1
