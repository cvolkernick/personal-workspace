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

# Optional: push Mac env secrets if present (Google tokens, GitHub PAT)
LOCAL_ENV="${HOME}/.config/resistance-dashboard/env"
if [[ -f "$LOCAL_ENV" ]]; then
  echo "→ Syncing ~/.config/resistance-dashboard/env to Pi (mode 600)…"
  scp "$LOCAL_ENV" "$REMOTE:~/.config/resistance-dashboard/env"
  ssh "$REMOTE" "chmod 600 ~/.config/resistance-dashboard/env"
else
  echo "NOTE: no $LOCAL_ENV on this Mac — Google Health on Pi will need tokens later."
fi

# shellcheck disable=SC2087
ssh "$REMOTE" bash -s -- "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
DIR="$1"

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
REMOTE

echo ""
echo "Deploy complete."
echo "  LAN:       http://$RHOST:8787/"
echo "  Healthz:   curl -sS http://$RHOST:8787/api/healthz"
echo "  Logs:      ssh $REMOTE 'journalctl --user -u resistance-dashboard -f'"
echo "  Off-LAN:   install Tailscale on Pi + client; open http://<tailscale-name>:8787/"
echo "  Security:  private mesh only until multi-user Google auth ships — no public port-forward."
