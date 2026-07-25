#!/bin/bash
# Open Panamerica Auto site from this Mac terminal.
# Prefers the Pi LAN backend (always-on); falls back to a local server if Pi is down.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PI_URL="${PANAMERICA_URL:-http://192.168.100.98:8795/}"
LOCAL_PORT="${PORT:-8795}"

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

echo "Checking Pi site: $PI_URL"
if curl -sf --connect-timeout 3 --max-time 5 "$PI_URL" >/dev/null; then
  echo "OK — opening Pi-hosted Panamerica Auto"
  open_url "$PI_URL"
  echo "$PI_URL"
  exit 0
fi

echo "Pi not reachable — starting local server on 127.0.0.1:$LOCAL_PORT"
cd "$ROOT"
# Open after short delay so first paint hits a live server
(sleep 0.8 && open_url "http://127.0.0.1:${LOCAL_PORT}/") &
exec python3 server.py --bind 127.0.0.1 --port "$LOCAL_PORT"
