#!/usr/bin/env bash
# Unattended daily fund-manager review (mid-session style).
# 1) RH refresh (MCP via grok if available)
# 2) Rules path: HOLD if in band (no LLM)
# 3) Else team/LLM via grok headless
# 4) Notify ntfy only on need_llm / error / stale RH
# 5) Write FCC treasury JSON
#
# Cron (ET):  30 12 * * 1-5  /path/to/treasury/fund_manager_daily.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_LOG="${LOG_DIR}/fund_manager_daily_${STAMP}.log"

exec >>"$RUN_LOG" 2>&1
echo "=== fund_manager_daily start ${STAMP} ==="
echo "ROOT=${ROOT}"

DOW="$(date +%u)"
if [[ "${FM_SKIP_WEEKENDS:-1}" == "1" && "${DOW}" -ge 6 ]]; then
  echo "Weekend — skip (set FM_SKIP_WEEKENDS=0 to force)."
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

# Rules path first (cheap HOLD when 40/60 ok)
set +e
python3 -m treasury.fund_manager --rules-review --notify
RR=$?
set -e
echo "rules_review exit=${RR}"

if [[ "${LIVE}" != "1" ]]; then
  echo "live:false — stop after rules observe"
  python3 -m treasury.run_treasury --offline || true
  exit 0
fi

PROMPT_FILE="${ROOT}/treasury/fund_manager_daily_prompt.txt"
# RR=2 → need LLM team; RR=0 hold; RR=1 error
if [[ "${RR}" -eq 2 ]]; then
  if command -v grok >/dev/null 2>&1 && [[ -f "${PROMPT_FILE}" ]]; then
    echo "Rules need_llm — running team/LLM headless review…"
    grok -p "$(cat "${PROMPT_FILE}")" \
      --cwd "${ROOT}" \
      --yolo \
      --output-format plain \
      || echo "WARN: grok headless exited non-zero"
    # Notify that LLM path ran (may have traded)
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
  fi
elif [[ "${RR}" -eq 0 ]]; then
  echo "Rules HOLD — no LLM (saves cost; glass box already logged if first hold today)"
else
  echo "Rules error path"
fi

python3 -m treasury.run_treasury --offline || true
ln -sfn "${RUN_LOG}" "${LOG_DIR}/fund_manager_daily_latest.log" 2>/dev/null || cp "${RUN_LOG}" "${LOG_DIR}/fund_manager_daily_latest.log"
echo "=== fund_manager_daily done ==="
