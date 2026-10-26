#!/usr/bin/env bash
# Unattended daily fund-manager review (mid-session style).
# 1) RH refresh (MCP via grok if available)
# 2) Rules path: HOLD if in band (no LLM)
# 3) Else team/LLM via grok headless
# 4) append_decision posts full what+why to GitHub #701 (including first HOLD
#    of the day, #736). --notify still comments stale RH *brokerage* and thin
#    need_llm/error fallbacks that skipped the log.
#    (#555: rh_checking / skipped / no_refresh_path / fresh as_of do not alert)
# 5) Write FCC treasury JSON
#
# Pi timer: treasury/deploy/fund-manager.timer (#729 sole producer)
# Mac launchd com.personalworkspace.fund-manager-daily is retired — do not reload.
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_LOG="${LOG_DIR}/fund_manager_daily_${STAMP}.log"

sync_fm_journal() {
  # #737: commit+push journal; never fail the run; retry next cycle on git error.
  echo "Journal sync (#737)…"
  python3 -m treasury.fund_manager_journal_sync || echo "WARN: journal_sync non-zero (run still ok)"
}

exec >>"$RUN_LOG" 2>&1
echo "=== fund_manager_daily start ${STAMP} ==="
echo "ROOT=${ROOT}"

DOW="$(date +%u)"
if [[ "${FM_SKIP_WEEKENDS:-1}" == "1" && "${DOW}" -ge 6 ]]; then
  echo "Weekend — skip (set FM_SKIP_WEEKENDS=0 to force)."
  sync_fm_journal
  exit 0
fi

LIVE="$(python3 - <<'PY'
import json
from pathlib import Path
p = Path("investment/fund_manager.json")
d = json.loads(p.read_text()) if p.is_file() else {}
print("1" if d.get("live") else "0")
PY
)"

if [[ -x "${ROOT}/treasury/rh_refresh.sh" ]]; then
  echo "Pre-review RH refresh…"
  bash "${ROOT}/treasury/rh_refresh.sh" || echo "WARN: rh_refresh failed"
fi

# Live YNAB + treasury eval *before* rules notify so rh_checking/x_money as_of
# are current (offline path freezes aged snapshots → false "stale RH" alert).
echo "Pre-review live YNAB + treasury eval…"
python3 -m treasury.ynab_sync || echo "WARN: ynab_sync failed"
python3 -m treasury.run_treasury || echo "WARN: run_treasury live failed"

# Rules path first (cheap HOLD when 60/40 ok)
set +e
python3 -m treasury.fund_manager --rules-review --notify
RR=$?
set -e
echo "rules_review exit=${RR}"

if [[ "${LIVE}" != "1" ]]; then
  echo "live:false — stop after rules observe"
  sync_fm_journal
  exit 0
fi

PROMPT_FILE="${ROOT}/treasury/fund_manager_daily_prompt.txt"
# RR=2 → need LLM team; RR=0 hold; RR=1 error
if [[ "${RR}" -eq 2 ]]; then
  if command -v grok >/dev/null 2>&1 && [[ -f "${PROMPT_FILE}" ]]; then
    echo "Rules need_llm — running team/LLM headless review…"
    # #1093 no-sell-to-rebalance: grok runs with order-placing RH tools DENIED
    # (deny rules still apply in always-approve mode). It writes buy intents to a file;
    # treasury.fund_manager_order_gate validates (buys only, <= deployable cash,
    # sells blocked+logged+alerted) and is the only submit path. Fail closed:
    # no deny list => no grok run.
    GROK_DENY=()
    while IFS= read -r _l; do [[ -n "${_l}" ]] && GROK_DENY+=("${_l}"); done \
      < <(python3 -m treasury.fund_manager_order_gate --print-grok-deny-args || true)
    if [[ "${#GROK_DENY[@]}" -lt 2 ]]; then
      echo "ERROR: order-gate deny list unavailable — NOT running grok (fail closed)"
    else
      INTENTS_FILE="${ROOT}/treasury/snapshots/fund_manager_intents.json"
      rm -f "${INTENTS_FILE}"
      # AI step is time-boxed; if it errors/times out the gate still runs and the
      # deterministic planner invests all settled cash (floating 35% -> IBIT).
      # Advisory only (2026-10-26): grok may write intents but never execute them.
      # Its env has no broker wiring and carries FM_ADVISORY_ONLY=1; the gate and
      # adapter also refuse under any grok ancestor. Only the gate call below
      # call (run by this script after grok exits) can submit.
      env -u FM_BROKER_ADAPTER -u FM_BROKER_CLIENT -u FM_BROKER_GROK_BIN -u FM_BROKER_GROK_TIMEOUT_S \
        FM_ADVISORY_ONLY=1 \
        timeout "${FM_GROK_TIMEOUT_S:-1800}" grok -p "$(cat "${PROMPT_FILE}")" \
        --cwd "${ROOT}" \
        --yolo \
        "${GROK_DENY[@]}" \
        --output-format plain \
        || echo "WARN: grok headless exited non-zero"
      python3 -m treasury.fund_manager_order_gate --execute-intents "${INTENTS_FILE}" \
        || echo "WARN: order gate blocked/failed (exit $?) — see #701 blocked_sell"
    fi
    # Stale-RH / thin fallback only — full decision already on #701 via append_decision
    python3 - <<'PY' || true
from treasury.fund_manager import notify_if_needed, load_decision_log
from treasury.adapters import load_json, SNAPSHOTS_DIR
recent = load_decision_log(limit=1)
dec = recent[0] if recent else {"kind": "review", "summary": "LLM daily review completed"}
tre = load_json(SNAPSHOTS_DIR / "treasury_latest.json") or {}
print(notify_if_needed(decision_or_review=dec, treasury_eval=tre.get("evaluation") or tre, force=dec.get("kind") != "hold"))
PY
  else
    echo "need_llm but grok missing — logged rules outcome only"
    # AI step unavailable: deterministic planner still runs (floating 35% -> IBIT).
    rm -f "${ROOT}/treasury/snapshots/fund_manager_intents.json"
    python3 -m treasury.fund_manager_order_gate --execute-intents "${ROOT}/treasury/snapshots/fund_manager_intents.json" \
      || echo "WARN: order gate blocked/failed (exit $?)"
  fi
elif [[ "${RR}" -eq 0 ]]; then
  echo "Rules HOLD — no LLM (saves cost; glass box already logged if first hold today)"
else
  echo "Rules error path"
fi

# Final dashboard refresh (live already ran pre-notify; offline is a no-network fallback)
python3 -m treasury.run_treasury --offline || true
sync_fm_journal
ln -sfn "${RUN_LOG}" "${LOG_DIR}/fund_manager_daily_latest.log" 2>/dev/null || cp "${RUN_LOG}" "${LOG_DIR}/fund_manager_daily_latest.log"
echo "=== fund_manager_daily done ==="
