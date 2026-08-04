#!/usr/bin/env bash
# Deploy FitDash (resistance-dashboard) to a Raspberry Pi / always-on host.
#
# Usage (from monorepo root on your Mac, on the same LAN as the Pi):
#   bash resistance-dashboard/deploy/install_remote.sh prism-agent@192.168.100.98
#   bash resistance-dashboard/deploy/install_remote.sh user@host --dir /home/user/personal-workspace
#
# Serves UI + API on 0.0.0.0:8787.
# Off-LAN: use Tailscale (or equivalent) — do NOT port-forward bare HTTP to the internet
# while FitDash is still single-user / no multi-tenant auth.
set -euo pipefail

REMOTE=""
REMOTE_DIR=""
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *)
      if [[ -z "$REMOTE" ]]; then REMOTE="$1"; shift
      else echo "Unknown arg: $1" >&2; exit 1
      fi
      ;;
  esac
done

if [[ -z "$REMOTE" ]]; then
  echo "Usage: $0 user@host [--dir PATH] [--dry-run]" >&2
  exit 1
fi

if [[ "$REMOTE" == *@* ]]; then
  RUSER="${REMOTE%%@*}"
  RHOST="${REMOTE#*@}"
else
  RUSER="pi"
  RHOST="$REMOTE"
  REMOTE="pi@$REMOTE"
fi

REMOTE_DIR="${REMOTE_DIR:-/home/${RUSER}/personal-workspace}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_SRC="$ROOT/resistance-dashboard"
UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/resistance-dashboard.service"
# Fitness data (workout markdown logs) lives next to the app in the monorepo
FITNESS_SRC="$ROOT/fitness"

if [[ ! -d "$APP_SRC" ]]; then
  echo "Missing app source: $APP_SRC" >&2
  exit 1
fi
if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing unit file: $UNIT_SRC" >&2
  exit 1
fi

echo "Local monorepo: $ROOT"
echo "Remote:         $REMOTE"
echo "Remote dir:     $REMOTE_DIR"
echo "Unit:           resistance-dashboard.service (port 8787)"

echo "→ Testing SSH…"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "echo ok && uname -a && python3 --version"

echo "→ Creating remote directories…"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR/resistance-dashboard' '$REMOTE_DIR/fitness'"

echo "→ Rsync FitDash app…"
RSYNC_ARGS=(-az
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.DS_Store'
  --exclude 'tests/fixtures'
)
if [[ "$DRY" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run -v)
fi

rsync "${RSYNC_ARGS[@]}" \
  "$APP_SRC/" "$REMOTE:$REMOTE_DIR/resistance-dashboard/"

if [[ -d "$FITNESS_SRC" ]]; then
  echo "→ Rsync fitness/ (workout logs, etc.)…"
  rsync "${RSYNC_ARGS[@]}" \
    "$FITNESS_SRC/" "$REMOTE:$REMOTE_DIR/fitness/"
else
  echo "WARNING: no local fitness/ dir — Pi will have empty lift history until you sync it."
fi

echo "→ Patch unit paths for $REMOTE_DIR …"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed "s|%h/personal-workspace|${REMOTE_DIR}|g" "$UNIT_SRC" > "$TMP/resistance-dashboard.service"

echo "→ Installing systemd user unit…"
ssh "$REMOTE" "mkdir -p ~/.config/systemd/user ~/.config/resistance-dashboard"
scp "$TMP/resistance-dashboard.service" "$REMOTE:~/.config/systemd/user/"

# Optional: push Mac env secrets if present (Google tokens, GitHub PAT).
# Then pin Pi-only OAuth public URL so a Mac localhost env never becomes
# Google's redirect_uri (that yields "this site can't be reached" on phone).
LOCAL_ENV="${HOME}/.config/resistance-dashboard/env"
# Override with FITDASH_PUBLIC_URL=... when invoking deploy if MagicDNS changes.
PUBLIC_URL="${FITDASH_PUBLIC_URL:-https://prism-gateway.tailb1085a.ts.net}"
if [[ -f "$LOCAL_ENV" ]]; then
  echo "→ Syncing ~/.config/resistance-dashboard/env to Pi (mode 600)…"
  scp "$LOCAL_ENV" "$REMOTE:~/.config/resistance-dashboard/env"
  ssh "$REMOTE" "chmod 600 ~/.config/resistance-dashboard/env"
else
  echo "NOTE: no $LOCAL_ENV on this Mac — Google Health on Pi will need tokens later."
  ssh "$REMOTE" "mkdir -p ~/.config/resistance-dashboard; touch ~/.config/resistance-dashboard/env; chmod 600 ~/.config/resistance-dashboard/env"
fi

# SuperGrok / Grok Build session for Ask Grok (short-lived JWT in auth.json).
# Pi cannot run interactive `grok login` easily — copy from this Mac when present.
LOCAL_GROK_AUTH="${HOME}/.grok/auth.json"
if [[ -f "$LOCAL_GROK_AUTH" ]]; then
  echo "→ Syncing ~/.grok/auth.json to Pi (Ask Grok SuperGrok session)…"
  ssh "$REMOTE" "mkdir -p ~/.grok && chmod 700 ~/.grok"
  scp "$LOCAL_GROK_AUTH" "$REMOTE:~/.grok/auth.json"
  ssh "$REMOTE" "chmod 600 ~/.grok/auth.json"
else
  echo "NOTE: no $LOCAL_GROK_AUTH — Ask Grok needs \`grok login\` on this Mac then re-deploy, or XAI_API_KEY on the Pi."
fi

echo "→ Pinning FITDASH_PUBLIC_URL=$PUBLIC_URL on Pi (OAuth redirect base)…"
# shellcheck disable=SC2087
ssh "$REMOTE" bash -s -- "$REMOTE_DIR" "$PUBLIC_URL" <<'REMOTE'
set -euo pipefail
DIR="$1"
PUBLIC_URL="$2"

ENV="$HOME/.config/resistance-dashboard/env"
mkdir -p "$(dirname "$ENV")"
touch "$ENV"
grep -vE '^(export[[:space:]]+)?FITDASH_PUBLIC_URL=' "$ENV" > "$ENV.tmp" || true
mv "$ENV.tmp" "$ENV"
{
  echo ""
  echo "# OAuth redirect base (reachable from phone/browser — never 127.0.0.1)"
  printf "export FITDASH_PUBLIC_URL=%q\n" "$PUBLIC_URL"
} >> "$ENV"
if ! grep -qE '^(export[[:space:]]+)?FITDASH_REQUIRE_AUTH=' "$ENV"; then
  echo "export FITDASH_REQUIRE_AUTH=1" >> "$ENV"
fi
chmod 600 "$ENV"

loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now resistance-dashboard.service
systemctl --user restart resistance-dashboard.service
systemctl --user status resistance-dashboard.service --no-pager | head -20 || true

echo ""
echo "Listening on :8787 (if available):"
ss -lntp 2>/dev/null | grep -E ':8787\b' || true
echo ""
echo "Healthz:"
curl -sS -m 5 "http://127.0.0.1:8787/api/healthz" || echo "(healthz not ready yet)"
echo ""
echo "Auth status (oauth_redirect_uri must NOT be 127.0.0.1):"
curl -sS -m 5 "http://127.0.0.1:8787/api/auth/status" || true
echo
REMOTE

echo ""
echo "Deploy complete."
echo "  Preferred: https://prism-gateway.tailb1085a.ts.net/  (Tailscale Serve HTTPS)"
echo "  LAN:       http://$RHOST:8787/"
echo "  Healthz:   curl -sS http://$RHOST:8787/api/healthz"
echo "  OAuth:     ${PUBLIC_URL}/api/auth/google/callback  (must match Google Console)"
echo "  Logs:      ssh $REMOTE 'journalctl --user -u resistance-dashboard -f'"
echo "  Security:  private mesh only — no public port-forward."
