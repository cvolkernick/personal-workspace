#!/usr/bin/env bash
# Unattended daily fund-manager review (mid-session style).
# Intended for cron / systemd on an always-on host (e.g. Raspberry Pi).
# Does NOT require opening the FCC dashboard.
#
# Example cron (America/New_York — set TZ on host or use systemd):
#   30 12 * * 1-5  /path/to/personal-workspace/treasury/fund_manager_daily.sh
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

# Soft skip weekends if host TZ is already US/Eastern (optional)
DOW="$(date +%u)" # 1=Mon … 7=Sun
if [[ "${FM_SKIP_WEEKENDS:-1}" == "1" && "${DOW}" -ge 6 ]]; then
  echo "Weekend — skip (set FM_SKIP_WEEKENDS=0 to force)."
  exit 0
fi

# Live flag check
if command -v python3 >/dev/null; then
  LIVE="$(python3 - <<'PY'
import json
from pathlib import Path
p = Path("investment/fund_manager.json")
d = json.loads(p.read_text()) if p.is_file() else {}
print("1" if d.get("live") else "0")
PY
)"
  if [[ "${LIVE}" != "1" ]]; then
    echo "fund_manager.live is false — observe-only weights, no grok trade run."
    python3 -m treasury.fund_manager --write || true
    python3 -m treasury.run_treasury --offline || true
    exit 0
  fi
fi

PROMPT_FILE="${ROOT}/treasury/fund_manager_daily_prompt.txt"
if [[ ! -f "${PROMPT_FILE}" ]]; then
  echo "Missing prompt file: ${PROMPT_FILE}"
  exit 1
fi

# Fresh RH snapshot first (also kept green by rh_refresh.sh every 3h)
if [[ -x "${ROOT}/treasury/rh_refresh.sh" ]]; then
  echo "Pre-review RH refresh…"
  bash "${ROOT}/treasury/rh_refresh.sh" || echo "WARN: rh_refresh failed"
fi

# Prefer grok headless when available (full team + MCP trades)
if command -v grok >/dev/null 2>&1; then
  echo "Running grok headless daily review…"
  # --yolo / always-approve so unattended runs do not block on tool permission
  grok -p "$(cat "${PROMPT_FILE}")" \
    --cwd "${ROOT}" \
    --yolo \
    --output-format plain \
    || echo "WARN: grok headless exited non-zero"
else
  echo "grok CLI not found — weights-only fallback (no LLM debate / no MCP from this script)."
  python3 -m treasury.fund_manager --write || true
fi

# Always refresh FCC artifacts from latest snapshots
python3 -m treasury.run_treasury --offline || true

# Symlink/copy latest log pointer
ln -sfn "${RUN_LOG}" "${LOG_DIR}/fund_manager_daily_latest.log" 2>/dev/null || cp "${RUN_LOG}" "${LOG_DIR}/fund_manager_daily_latest.log"

echo "=== fund_manager_daily done ==="
