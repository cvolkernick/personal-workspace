#!/usr/bin/env bash
# Open an always-on Pi dashboard in the browser (no local server).
#
# Usage:
#   bash deploy/open_dashboard.sh orchestra
#   bash deploy/open_dashboard.sh iot
#   PI_HOST=100.x.y.z bash deploy/open_dashboard.sh orchestra   # Tailscale
#
# Services: orchestra | financial-command | projects-dashboard |
#           holistic | iot | resistance-dashboard | auto-fleet
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE="${1:-orchestra}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

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

URL="$(python3 -c "from dashboard_endpoints import service_url; print(service_url('${SERVICE}'))")"
HEALTH="$(python3 -c "from dashboard_endpoints import health_url; print(health_url('${SERVICE}'))")"
HOST="$(python3 -c "from dashboard_endpoints import pi_host; print(pi_host())")"

echo "Always-on host: $HOST"
echo "Probing $HEALTH …"
if curl -sf --connect-timeout 3 --max-time 6 "$HEALTH" >/dev/null; then
  echo "OK — opening $URL"
  open_url "$URL"
  echo "$URL"
  exit 0
fi

echo "ERROR: Pi dashboard not reachable at $HEALTH" >&2
echo "  • On home LAN: ensure Pi is up (ssh prism-agent@$HOST)" >&2
echo "  • Off-network: set PI_HOST to Tailscale IP/MagicDNS and retry" >&2
echo "  • Restart units: ssh prism-agent@$HOST 'systemctl --user restart ${SERVICE}-dashboard 2>/dev/null || systemctl --user restart ${SERVICE}'" >&2
exit 1
