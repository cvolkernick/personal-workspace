#!/usr/bin/env bash
# Sync Grok OIDC credentials from this Mac to the Pi worker.
#
# OIDC access tokens expire (~hours). Grok can refresh via refresh_token when
# the Pi owns a valid auth.json — but a stale copy fails with "Not signed in".
# This script copies a fresh Mac session to the Pi and verifies headless auth.
#
# Usage:
#   bash projects-dashboard/sync_pi_grok_auth.sh
#   bash projects-dashboard/sync_pi_grok_auth.sh prism-agent@192.168.100.98
#
# Optional LaunchAgent (every 20 min while Mac is awake):
#   bash projects-dashboard/install_mac_auth_sync.sh

set -euo pipefail

REMOTE="${1:-prism-agent@192.168.100.98}"
AUTH_SRC="${HOME}/.grok/auth.json"
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

if [[ ! -f "$AUTH_SRC" ]]; then
  echo "error: missing $AUTH_SRC — run: grok login" >&2
  exit 1
fi

# Refresh Mac token first so we don't ship an already-expired session
if command -v grok >/dev/null 2>&1 || [[ -x "${HOME}/.grok/bin/grok" ]]; then
  GROK_BIN="$(command -v grok 2>/dev/null || true)"
  [[ -z "$GROK_BIN" ]] && GROK_BIN="${HOME}/.grok/bin/grok"
  # Best-effort silent refresh (ignore failure — still try to copy)
  "$GROK_BIN" --single "auth-ping" --max-turns 1 --always-approve >/dev/null 2>&1 || true
fi

echo "→ copying auth.json to ${REMOTE}:~/.grok/auth.json"
scp "${SSH_OPTS[@]}" "$AUTH_SRC" "${REMOTE}:.grok/auth.json"
ssh "${SSH_OPTS[@]}" "$REMOTE" 'chmod 600 ~/.grok/auth.json'

echo "→ verifying headless Grok on Pi…"
# shellcheck disable=SC2029
ssh "${SSH_OPTS[@]}" "$REMOTE" 'bash -s' <<'REMOTE_SCRIPT'
set -euo pipefail
export HOME="${HOME:-/home/prism-agent}"
export PATH="$HOME/.grok/bin:/usr/local/bin:/usr/bin:/bin"
# Load optional XAI_API_KEY from scheduler env
if [[ -f "$HOME/.config/workflow-scheduler.env" ]]; then
  # shellcheck disable=SC1090
  set -a
  # only export KEY=value lines we care about
  while IFS= read -r line; do
    case "$line" in
      XAI_API_KEY=*|GITHUB_TOKEN=*|PATH=*|HOME=*) eval "export $line" ;;
    esac
  done < "$HOME/.config/workflow-scheduler.env"
  set +a
fi
out="$(grok --single "Reply with exactly: pong" --max-turns 1 --always-approve 2>&1)" || true
echo "$out" | tail -5
if echo "$out" | grep -qiE 'not signed in|authenticate|login'; then
  echo "VERIFY_FAILED"
  exit 2
fi
if ! echo "$out" | grep -qi 'pong'; then
  echo "VERIFY_UNEXPECTED: $out"
  exit 3
fi
python3 - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timezone
p = Path.home() / ".grok" / "auth.json"
d = json.loads(p.read_text(encoding="utf-8"))
for v in d.values():
    if isinstance(v, dict) and v.get("expires_at"):
        exp = datetime.fromisoformat(str(v["expires_at"]).replace("Z", "+00:00"))
        mins = (exp - datetime.now(timezone.utc)).total_seconds() / 60
        print(f"ok expires_at={v['expires_at']} mins_left={mins:.1f}")
PY
echo "VERIFY_OK"
REMOTE_SCRIPT

echo "✓ Pi Grok auth synced and verified"
