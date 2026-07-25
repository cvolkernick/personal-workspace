#!/usr/bin/env bash
# Deploy Panamerica Auto website to Raspberry Pi (systemd user unit).
#
# Usage (from monorepo root on your Mac):
#   bash business/panamerica-auto/deploy/install_remote.sh prism-agent@192.168.100.98
#   bash business/panamerica-auto/deploy/install_remote.sh user@host --dir /home/user/personal-workspace
#
# Serves on 0.0.0.0:8795 — open http://<pi-ip>:8795/ from a Mac/terminal on the LAN.
set -euo pipefail

REMOTE=""
REMOTE_DIR=""
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
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
  echo "Usage: $0 user@host [--dir PATH]" >&2
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
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SITE_SRC="$ROOT/business/panamerica-auto"
UNIT_SRC="$ROOT/deploy/units/panamerica-auto.service"
if [[ ! -f "$UNIT_SRC" ]]; then
  UNIT_SRC="$(cd "$(dirname "$0")" && pwd)/panamerica-auto.service"
fi

if [[ ! -d "$SITE_SRC" ]]; then
  echo "Missing site source: $SITE_SRC" >&2
  exit 1
fi
if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing unit file: $UNIT_SRC" >&2
  exit 1
fi

echo "Local monorepo: $ROOT"
echo "Remote:         $REMOTE"
echo "Remote dir:     $REMOTE_DIR"
echo "Unit:           panamerica-auto.service (port 8795)"

echo "→ Testing SSH…"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "echo ok && uname -a && python3 --version"

echo "→ Creating remote directories…"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR/business/panamerica-auto'"

echo "→ Rsync site…"
RSYNC_ARGS=(-az
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.DS_Store'
)
if [[ "$DRY" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run -v)
fi

rsync "${RSYNC_ARGS[@]}" \
  "$SITE_SRC/" "$REMOTE:$REMOTE_DIR/business/panamerica-auto/"

echo "→ Patch unit paths for $REMOTE_DIR …"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
sed "s|%h/personal-workspace|${REMOTE_DIR}|g" "$UNIT_SRC" > "$TMP/panamerica-auto.service"

echo "→ Installing systemd user unit…"
ssh "$REMOTE" "mkdir -p ~/.config/systemd/user"
scp "$TMP/panamerica-auto.service" "$REMOTE:~/.config/systemd/user/"

# shellcheck disable=SC2087
ssh "$REMOTE" bash -s -- "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
DIR="$1"

loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now panamerica-auto.service
systemctl --user restart panamerica-auto.service
systemctl --user status panamerica-auto.service --no-pager | head -15 || true

echo ""
echo "Listening on :8795 (if available):"
ss -lntp 2>/dev/null | grep -E ':8795\b' || true
REMOTE

echo ""
echo "Deploy complete."
echo "  Site (LAN):  http://$RHOST:8795/"
echo "  From Mac:    bash business/panamerica-auto/start.command"
echo "  Or open:     open http://$RHOST:8795/"
echo "  Logs:        ssh $REMOTE 'journalctl --user -u panamerica-auto -f'"
