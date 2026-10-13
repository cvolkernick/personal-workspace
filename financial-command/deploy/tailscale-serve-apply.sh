#!/usr/bin/env bash
# Re-apply prism's tailnet-only Tailscale Serve mappings (idempotent). Never Funnel.
#
#   443   -> http://127.0.0.1:8000  FCC
#   10000 -> http://127.0.0.1:8795  Horizon on its own origin (#1153)
#   10001 -> http://127.0.0.1:8787  FitDash (moved off Vercel; Google OAuth callback on this origin)
#
# `tailscale serve --bg` config persists in tailscaled state across reboots, so
# this is a safety net (e.g. after `tailscale serve reset`), not the source of
# truth. It waits for tailscaled to reach BackendState=Running first: at boot the
# user unit used to run ~3 s after tailscaled started, before the node was up,
# and failed once with no retry (fcc-tailscale-serve.service, failed since boot).
# Port 8443 (Funnel -> :8799) is owned elsewhere and is not touched here.
#
# Run with bash (no exec bit needed):
#   bash ~/personal-workspace/financial-command/deploy/tailscale-serve-apply.sh
set -euo pipefail

TS="${TAILSCALE_BIN:-/usr/bin/tailscale}"
WAIT_S="${TS_SERVE_WAIT_S:-120}"

state=""
for _ in $(seq 1 $((WAIT_S / 2))); do
  state="$("$TS" status --json 2>/dev/null \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState",""))' 2>/dev/null || true)"
  [ "$state" = "Running" ] && break
  sleep 2
done
if [ "$state" != "Running" ]; then
  echo "tailscale-serve-apply: tailscaled not Running after ${WAIT_S}s (state=${state:-none})" >&2
  exit 1
fi

# OperatorUser is unset on prism-gateway, so writing serve config needs root.
sudo -n "$TS" serve --bg --yes --https=443 http://127.0.0.1:8000
sudo -n "$TS" serve --bg --yes --https=10000 http://127.0.0.1:8795
sudo -n "$TS" serve --bg --yes --https=10001 http://127.0.0.1:8787

"$TS" serve status
