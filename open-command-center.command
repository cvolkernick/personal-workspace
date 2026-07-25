#!/bin/bash
# Open Orchestrator from this Mac terminal.
# Prefers the always-on Raspberry Pi backend; falls back to a local server if Pi is down.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# Override: ORCHESTRATOR_URL=http://prism-gateway:8790/ bash open-command-center.command
PI_URL="${ORCHESTRATOR_URL:-http://192.168.100.98:8790/}"
LOCAL_PORT="${PORT:-8790}"

open_url() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then
    open "$url"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url"
  else
    echo "Open in browser: $url"
  fi
}

echo "Orchestrator"
echo "Checking Pi backend: $PI_URL"
if curl -sf --connect-timeout 3 --max-time 5 "${PI_URL%/}/api/health" >/dev/null; then
  echo "OK — opening Pi-hosted Orchestrator"
  open_url "$PI_URL"
  echo "$PI_URL"
  echo ""
  echo "Domain deep-links on the page point at the Pi LAN host."
  echo "Off-network: set ORCHESTRATOR_URL=http://<tailscale-host>:8790/"
  exit 0
fi

echo "Pi not reachable — starting local Orchestrator on 127.0.0.1:$LOCAL_PORT"
cd "$ROOT"
(sleep 0.8 && open_url "http://127.0.0.1:${LOCAL_PORT}/") &
exec python3 orchestra/server.py --host 127.0.0.1 --port "$LOCAL_PORT" --local --no-browser
