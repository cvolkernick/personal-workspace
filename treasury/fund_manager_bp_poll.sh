#!/usr/bin/env bash
# Poll agentic RH for free cash / buying power and run the fund-manager process when
# either is > $0. Intended for launchd/cron during market hours so settlement unlocks
# and capital deposits get deployed without a manual kickoff.
#
# launchd: treasury/deploy/com.personalworkspace.fund-manager-bp-poll.plist
# manual:  ./treasury/fund_manager_bp_poll.sh
# force outside hours: FM_BP_POLL_FORCE=1 ./treasury/fund_manager_bp_poll.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${ROOT}/treasury/snapshots"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_LOG="${LOG_DIR}/fund_manager_bp_poll_${STAMP}.log"
LOCK="${LOG_DIR}/fund_manager_bp_poll.lock"

exec >>"$RUN_LOG" 2>&1
echo "=== fund_manager_bp_poll start ${STAMP} ==="
echo "ROOT=${ROOT}"

# Serialize overlapping polls (refresh + team can be long)
if command -v flock >/dev/null 2>&1; then
  exec 9>"${LOCK}"
  if ! flock -n 9; then
    echo "Another bp poll holds lock — exit"
    exit 0
  fi
else
  # macOS often lacks flock; simple mkdir lock
  if ! mkdir "${LOCK}.d" 2>/dev/null; then
    # stale lock > 45 min → take over
    if [[ -d "${LOCK}.d" ]]; then
      age=$(( $(date +%s) - $(stat -f %m "${LOCK}.d" 2>/dev/null || echo 0) ))
      if [[ "${age}" -gt 2700 ]]; then
        rmdir "${LOCK}.d" 2>/dev/null || rm -rf "${LOCK}.d"
        mkdir "${LOCK}.d" || { echo "lock busy"; exit 0; }
      else
        echo "Another bp poll holds lock — exit"
        exit 0
      fi
    fi
  fi
  trap 'rmdir "${LOCK}.d" 2>/dev/null || true' EXIT
fi

# Market hours America/New_York (weekdays 9:30–16:00 ET) unless forced
if [[ "${FM_BP_POLL_FORCE:-0}" != "1" ]]; then
  eval "$(python3 - <<'PY'
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
except Exception:
    from datetime import timezone, timedelta
    et = timezone(timedelta(hours=-4))  # fallback EDT
now = datetime.now(et)
# Mon=0 .. Sun=6
wd = now.weekday()
mins = now.hour * 60 + now.minute
open_m, close_m = 9 * 60 + 30, 16 * 60
ok = wd < 5 and open_m <= mins < close_m
print(f"IN_HOURS={'1' if ok else '0'}")
print(f"ET_NOW={now.isoformat()}")
PY
)"
  if [[ "${IN_HOURS}" != "1" ]]; then
    echo "Outside market hours (${ET_NOW}) — skip (FM_BP_POLL_FORCE=1 to override)"
    ln -sfn "${RUN_LOG}" "${LOG_DIR}/fund_manager_bp_poll_latest.log" 2>/dev/null || true
    exit 0
  fi
  echo "Market hours OK (${ET_NOW})"
fi

LIVE="$(python3 - <<'PY'
import json
from pathlib import Path
p = Path("investment/fund_manager.json")
d = json.loads(p.read_text()) if p.is_file() else {}
print("1" if d.get("live") else "0")
PY
)"
if [[ "${LIVE}" != "1" ]]; then
  echo "live:false — skip bp poll deploy"
  exit 0
fi

# Fresh RH snapshot (MCP via grok) so BP/cash are current
if [[ -x "${ROOT}/treasury/rh_refresh.sh" ]]; then
  echo "RH refresh before BP check…"
  bash "${ROOT}/treasury/rh_refresh.sh" || echo "WARN: rh_refresh failed"
fi

# Rules: any cash>0 or BP>0 → need_llm
set +e
python3 -m treasury.fund_manager --rules-review --notify
RR=$?
set -e
echo "rules_review exit=${RR}"

PROMPT_FILE="${ROOT}/treasury/fund_manager_daily_prompt.txt"
if [[ "${RR}" -eq 2 ]]; then
  if command -v grok >/dev/null 2>&1 && [[ -f "${PROMPT_FILE}" ]]; then
    echo "Free capital detected — running full team/LLM review…"
    grok -p "$(cat "${PROMPT_FILE}")" \
      --cwd "${ROOT}" \
      --yolo \
      --output-format plain \
      || echo "WARN: grok headless exited non-zero"
    python3 - <<'PY' || true
from treasury.fund_manager import notify_if_needed, load_decision_log
from treasury.adapters import load_json, SNAPSHOTS_DIR
recent = load_decision_log(limit=1)
dec = recent[0] if recent else {"kind": "deploy", "summary": "BP poll team review completed"}
tre = load_json(SNAPSHOTS_DIR / "treasury_latest.json") or {}
print(notify_if_needed(decision_or_review=dec, treasury_eval=tre.get("evaluation") or tre, force=True))
PY
  else
    echo "need_llm but grok missing — rules outcome only"
  fi
elif [[ "${RR}" -eq 0 ]]; then
  echo "Rules HOLD — no free capital / no action"
else
  echo "Rules error path (exit ${RR})"
fi

python3 -m treasury.run_treasury --offline || true
ln -sfn "${RUN_LOG}" "${LOG_DIR}/fund_manager_bp_poll_latest.log" 2>/dev/null || cp "${RUN_LOG}" "${LOG_DIR}/fund_manager_bp_poll_latest.log"
echo "=== fund_manager_bp_poll done ==="
