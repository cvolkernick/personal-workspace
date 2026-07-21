#!/usr/bin/env bash
# Deploy Workflow Management scheduler (and optional dashboard) to a Raspberry Pi.
#
# Usage (from monorepo root on your Mac):
#   bash projects-dashboard/deploy/install_remote.sh pi@192.168.x.x
#   bash projects-dashboard/deploy/install_remote.sh pi@host --dashboard
#   bash projects-dashboard/deploy/install_remote.sh pi@host --dir /home/pi/personal-workspace
#
# Prerequisites:
#   - SSH key access (ssh pi@host works)
#   - Remote python3
#   - Prefer a full git clone of personal-workspace on the Pi
set -euo pipefail

REMOTE=""
REMOTE_DIR=""
MODE="timer"   # timer | dashboard (timer always; dashboard optional)
WITH_DASH=0
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dashboard) WITH_DASH=1; shift ;;
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help)
      sed -n '2,16p' "$0"
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
  echo "Usage: $0 user@host [--dashboard] [--dir PATH]" >&2
  exit 1
fi

if [[ "$REMOTE" == *@* ]]; then
  RUSER="${REMOTE%%@*}"
else
  RUSER="pi"
  REMOTE="pi@$REMOTE"
fi

REMOTE_DIR="${REMOTE_DIR:-/home/${RUSER}/personal-workspace}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEPLOY_SRC="$(cd "$(dirname "$0")" && pwd)"

echo "Local monorepo: $ROOT"
echo "Remote:         $REMOTE"
echo "Remote dir:     $REMOTE_DIR"
echo "Dashboard unit: $WITH_DASH"

echo "→ Testing SSH…"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "echo ok && uname -a && python3 --version"

echo "→ Ensuring remote monorepo exists…"
ssh "$REMOTE" "mkdir -p '$(dirname "$REMOTE_DIR")'"
if ssh "$REMOTE" "test -d '$REMOTE_DIR/.git'"; then
  echo "→ Remote git repo present; rsync projects-dashboard + ops/backlog defaults…"
else
  echo "→ No git repo at $REMOTE_DIR — rsyncing full lightweight tree (consider git clone instead)…"
fi

RSYNC_ARGS=(-az
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.DS_Store'
  --exclude 'ops/backlog/dashboard.log'
  --exclude 'ops/backlog/scheduler.log'
  --exclude 'ops/session-index'
)
if [[ "$DRY" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run -v)
fi

# Prefer syncing the whole monorepo lightly; user can also git pull on device
rsync "${RSYNC_ARGS[@]}" \
  --exclude '.git' \
  "$ROOT/projects-dashboard/" "$REMOTE:$REMOTE_DIR/projects-dashboard/"
rsync "${RSYNC_ARGS[@]}" \
  "$ROOT/ops/backlog/" "$REMOTE:$REMOTE_DIR/ops/backlog/" \
  || true
# Agents.md / root helpers if missing
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR/ops/backlog/reports' '$REMOTE_DIR/ops/backlog/seeds'"

echo "→ Patching unit WorkingDirectory to $REMOTE_DIR…"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
for f in workflow-scheduler.service workflow-scheduler.timer workflow-dashboard.service; do
  sed "s|%h/personal-workspace|$REMOTE_DIR|g" "$DEPLOY_SRC/$f" > "$TMP/$f"
done

echo "→ Installing systemd user units…"
ssh "$REMOTE" "mkdir -p ~/.config/systemd/user"
scp "$TMP/workflow-scheduler.service" "$TMP/workflow-scheduler.timer" \
  "$REMOTE:~/.config/systemd/user/"
if [[ "$WITH_DASH" -eq 1 ]]; then
  scp "$TMP/workflow-dashboard.service" "$REMOTE:~/.config/systemd/user/"
fi

ssh "$REMOTE" "bash -s" <<REMOTE
set -euo pipefail
DIR='$REMOTE_DIR'
# Enable linger so user timers run without login
loginctl enable-linger "\$USER" 2>/dev/null || true
systemctl --user daemon-reload
# Seed scheduler config for raspi
python3 - <<'PY'
import json
from pathlib import Path
p = Path("$REMOTE_DIR") / "ops/backlog/scheduler.json"
p.parent.mkdir(parents=True, exist_ok=True)
cfg = {}
if p.is_file():
    try:
        cfg = json.loads(p.read_text())
    except Exception:
        cfg = {}
cfg.update({
    "enabled": True,
    "backend": "raspi",
    "execution_mode": cfg.get("execution_mode") or "auto",
    "spawn_grok": False,
    "auto_queue_scheduled": True,
    "cron_expression": "*/15 * * * *",
})
p.write_text(json.dumps(cfg, indent=2) + "\n")
print("scheduler.json →", cfg.get("backend"), cfg.get("execution_mode"))
PY
systemctl --user enable --now workflow-scheduler.timer
systemctl --user start workflow-scheduler.service || true
systemctl --user status workflow-scheduler.timer --no-pager || true
if [[ "$WITH_DASH" -eq 1 ]]; then
  systemctl --user enable --now workflow-dashboard.service
  systemctl --user status workflow-dashboard.service --no-pager || true
fi
echo "Done. Timer:"
systemctl --user list-timers --all | grep -i workflow || true
REMOTE

echo ""
echo "OK. Pi owns 24/7 ticks. On your Mac:"
echo "  1. Uninstall local cron if both would tick: dashboard → Remove cron"
echo "  2. Set backend=raspi, execution_mode=auto (or queue) in ops/backlog/scheduler.json"
echo "  3. Use dashboard → Launch pending on this Mac for pending_terminal jobs"
echo "  4. Or install Grok on the Pi and set execution_mode=spawn"
