#!/usr/bin/env bash
# Deploy IoT package to a remote host (Raspberry Pi) over SSH and optionally
# install a systemd unit for headless routines.
#
# Usage (from monorepo root on your Mac):
#   bash iot/deploy/install_remote.sh pi@192.168.100.XX
#   bash iot/deploy/install_remote.sh pi@raspberrypi.local --dashboard
#   bash iot/deploy/install_remote.sh pi@host --user pi --dir /home/pi/iot-workspace
#
# Prerequisites:
#   - SSH key access to the host (ssh pi@host works without password prompt ideally)
#   - Remote has python3
#   - Host is on the same LAN as the Wiz bulbs
set -euo pipefail

REMOTE=""
REMOTE_USER=""
REMOTE_DIR=""
MODE="worker"   # worker | dashboard
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dashboard) MODE="dashboard"; shift ;;
    --worker) MODE="worker"; shift ;;
    --user) REMOTE_USER="$2"; shift 2 ;;
    --dir) REMOTE_DIR="$2"; shift 2 ;;
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
  echo "Usage: $0 user@host [--worker|--dashboard] [--dir PATH]" >&2
  exit 1
fi

# Parse user@host
if [[ "$REMOTE" == *@* ]]; then
  RUSER="${REMOTE%%@*}"
  RHOST="${REMOTE#*@}"
else
  RUSER="${REMOTE_USER:-pi}"
  RHOST="$REMOTE"
  REMOTE="${RUSER}@${RHOST}"
fi

REMOTE_DIR="${REMOTE_DIR:-/home/${RUSER}/iot-workspace}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
echo "Local monorepo: $ROOT"
echo "Remote:         $REMOTE"
echo "Remote dir:     $REMOTE_DIR"
echo "Mode:           $MODE"

echo "→ Testing SSH…"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE" "echo ok && uname -a && python3 --version"

echo "→ Creating remote directory…"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR'"

echo "→ Rsync iot package + minimal monorepo layout…"
# Ship iot/ plus nothing else heavy; worker only needs iot package on PYTHONPATH=remote dir
# Layout: $REMOTE_DIR/iot/...  so PYTHONPATH=$REMOTE_DIR works (import iot.*)
RSYNC_ARGS=(-az --delete
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude 'data/schedule_state.json'
  --exclude 'secrets.json'
  --exclude '.DS_Store'
)
if [[ "$DRY" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run -v)
fi

rsync "${RSYNC_ARGS[@]}" \
  --exclude 'backend.json' \
  "$ROOT/iot/" \
  "$REMOTE:$REMOTE_DIR/iot/"
# Pi must not ship Mac's backend.json (would proxy to itself)
# secrets.json is excluded so rsync --delete never wipes VeSync credentials

echo "→ Create venv + install deps (PEP 668-safe)…"
ssh "$REMOTE" "python3 -m venv '$REMOTE_DIR/.venv' && \
  '$REMOTE_DIR/.venv/bin/pip' install -q --upgrade pip && \
  '$REMOTE_DIR/.venv/bin/pip' install -q -r '$REMOTE_DIR/iot/requirements.txt'"

echo "→ Ensure schedule data dir…"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR/iot/data'"

# If VeSync secrets live under personal-workspace but not iot-workspace, copy once.
echo "→ Ensure secrets.json for VeSync plugs (if present elsewhere on host)…"
ssh "$REMOTE" "if [[ ! -f '$REMOTE_DIR/iot/secrets.json' ]]; then
  for cand in \
    '/home/${RUSER}/personal-workspace/iot/secrets.json' \
    '/home/${RUSER}/workspace/iot/secrets.json'; do
    if [[ -f \"\$cand\" ]]; then
      cp -a \"\$cand\" '$REMOTE_DIR/iot/secrets.json'
      chmod 600 '$REMOTE_DIR/iot/secrets.json'
      echo \"  copied \$cand → $REMOTE_DIR/iot/secrets.json\"
      break
    fi
  done
fi
if [[ -f '$REMOTE_DIR/iot/secrets.json' ]]; then echo '  secrets.json present'; else echo '  WARNING: no secrets.json (VeSync plugs will fail)'; fi"

UNIT_SRC="iot-worker.service"
UNIT_NAME="iot-worker.service"
if [[ "$MODE" == "dashboard" ]]; then
  UNIT_SRC="iot-dashboard.service"
  UNIT_NAME="iot-dashboard.service"
fi

echo "→ Install systemd unit ($UNIT_NAME)…"
# Rewrite paths for this user/dir; use venv python
TMP_UNIT="$(mktemp)"
sed \
  -e "s|User=pi|User=${RUSER}|g" \
  -e "s|Group=pi|Group=${RUSER}|g" \
  -e "s|/home/pi/iot-workspace|${REMOTE_DIR}|g" \
  -e "s|/usr/bin/python3|${REMOTE_DIR}/.venv/bin/python|g" \
  "$(dirname "$0")/$UNIT_SRC" > "$TMP_UNIT"

scp "$TMP_UNIT" "$REMOTE:/tmp/$UNIT_NAME"
rm -f "$TMP_UNIT"

ssh "$REMOTE" "sudo mv /tmp/$UNIT_NAME /etc/systemd/system/$UNIT_NAME && \
  sudo systemctl daemon-reload && \
  sudo systemctl enable $UNIT_NAME && \
  sudo systemctl restart $UNIT_NAME && \
  sudo systemctl --no-pager --full status $UNIT_NAME | head -20"

echo ""
echo "Deploy complete."
echo "  Logs:   ssh $REMOTE 'journalctl -u $UNIT_NAME -f'"
echo "  Status: ssh $REMOTE 'systemctl status $UNIT_NAME'"
if [[ "$MODE" == "dashboard" ]]; then
  echo "  UI:     http://$RHOST:8780/  (ensure firewall allows 8780)"
else
  echo "  Worker is headless (routines only). Optional UI:"
  echo "    re-run with --dashboard"
fi
echo ""
echo "Verify schedule location on remote:"
echo "  ssh $REMOTE 'cat $REMOTE_DIR/iot/schedule.json | head -20'"
