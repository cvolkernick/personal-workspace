#!/usr/bin/env bash
# Deploy all personal-workspace dashboard backends to a Raspberry Pi (or always-on host).
#
# Usage (from monorepo root on your Mac):
#   bash deploy/install_remote.sh prism-agent@192.168.100.98
#   bash deploy/install_remote.sh user@host --dir /home/user/personal-workspace
#   bash deploy/install_remote.sh user@host --only orchestra,iot
#
# Installs systemd --user units for:
#   orchestra:8790  financial-command:8000  workflow:8765
#   holistic:8770   iot:8780                 resistance:8787
#
# Each unit binds 0.0.0.0, --no-browser, --local (API on the Pi, not proxy).
# Terminal frontends on a laptop use --backend http://<pi-or-tailscale>:PORT
#
# Prerequisites: SSH key access; remote python3.
set -euo pipefail

REMOTE=""
REMOTE_DIR=""
ONLY=""
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"
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
  echo "Usage: $0 user@host [--dir PATH] [--only svc1,svc2]" >&2
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
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNITS_SRC="$(cd "$(dirname "$0")/units" && pwd)"

ALL_UNITS=(
  orchestra-dashboard.service
  financial-command.service
  workflow-dashboard.service
  holistic-dashboard.service
  iot-dashboard.service
  resistance-dashboard.service
)

select_units() {
  if [[ -z "$ONLY" ]]; then
    printf '%s\n' "${ALL_UNITS[@]}"
    return
  fi
  local want
  IFS=',' read -ra want <<< "$ONLY"
  local u short
  for u in "${ALL_UNITS[@]}"; do
    short="${u%.service}"
    short="${short%-dashboard}"
    for w in "${want[@]}"; do
      w="$(echo "$w" | tr '[:upper:]' '[:lower:]' | xargs)"
      if [[ "$short" == *"$w"* ]] || [[ "$u" == *"$w"* ]]; then
        echo "$u"
        break
      fi
    done
  done
}

UNITS=()
while IFS= read -r line; do
  [[ -n "$line" ]] && UNITS+=("$line")
done < <(select_units)
if [[ ${#UNITS[@]} -eq 0 ]]; then
  echo "No units matched --only $ONLY" >&2
  exit 1
fi

echo "Local monorepo: $ROOT"
echo "Remote:         $REMOTE"
echo "Remote dir:     $REMOTE_DIR"
echo "Units:          ${UNITS[*]}"

echo "→ Testing SSH…"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "echo ok && uname -a && python3 --version"

echo "→ Creating remote directories…"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR'"

echo "→ Rsync monorepo (excludes heavy/noise)…"
RSYNC_ARGS=(-az
  --exclude '.git'
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.DS_Store'
  --exclude 'ops/backlog/dashboard.log'
  --exclude 'ops/backlog/scheduler.log'
  --exclude 'fitness/charts'
  --exclude 'resistance-dashboard/tests/fixtures'
)
if [[ "$DRY" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run -v)
fi

rsync "${RSYNC_ARGS[@]}" \
  "$ROOT/" "$REMOTE:$REMOTE_DIR/"

echo "→ Patch unit paths for $REMOTE_DIR …"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
for f in "${UNITS[@]}"; do
  sed "s|%h/personal-workspace|${REMOTE_DIR}|g" "$UNITS_SRC/$f" > "$TMP/$f"
done

echo "→ Installing systemd user units…"
ssh "$REMOTE" "mkdir -p ~/.config/systemd/user"
for f in "${UNITS[@]}"; do
  scp "$TMP/$f" "$REMOTE:~/.config/systemd/user/"
done

# shellcheck disable=SC2087
ssh "$REMOTE" bash -s -- "$REMOTE_DIR" "${UNITS[*]}" <<'REMOTE'
set -euo pipefail
DIR="$1"
shift
UNITS="$*"

loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload

# Optional IoT dep
python3 -m pip install --user -q 'pywizlight>=0.6.0' 2>/dev/null || true

# Prefer monorepo user units over legacy system-wide iot-dashboard (port 8780 clash)
if printf '%s\n' $UNITS | grep -q 'iot-dashboard'; then
  if systemctl is-active --quiet iot-dashboard.service 2>/dev/null; then
    echo "→ Stopping legacy system iot-dashboard (frees :8780)…"
    sudo -n systemctl stop iot-dashboard.service 2>/dev/null || true
    sudo -n systemctl disable iot-dashboard.service 2>/dev/null || true
  fi
fi

for u in $UNITS; do
  systemctl --user enable --now "$u"
  systemctl --user status "$u" --no-pager | head -12 || true
done

echo ""
echo "Listening (ss/netstat if available):"
ss -lntp 2>/dev/null | grep -E ':(8000|8765|8770|8780|8787|8790)\b' || true
REMOTE

echo ""
echo "Deploy complete."
echo "  Backends (LAN):  http://$RHOST:8790  :8000  :8765  :8770  :8780  :8787"
echo "  Off-network:     install Tailscale on Pi + client; use MagicDNS/IP instead of LAN IP"
echo "  Terminal UI:     python3 orchestra/server.py --backend http://<pi-or-tailscale>:8790"
echo "  Docs:            deploy/README.md"
echo "  Logs:            ssh $REMOTE 'journalctl --user -u orchestra-dashboard -f'"
