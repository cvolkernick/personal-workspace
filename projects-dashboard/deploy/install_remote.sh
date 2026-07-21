#!/usr/bin/env bash
# Deploy Workflow Management scheduler (and optional dashboard) to a Raspberry Pi.
#
# Usage (from monorepo root on your Mac):
#   bash projects-dashboard/deploy/install_remote.sh prism-agent@192.168.100.98
#   bash projects-dashboard/deploy/install_remote.sh user@host --dashboard
#   bash projects-dashboard/deploy/install_remote.sh user@host --dir /home/user/personal-workspace
#
# Prerequisites:
#   - SSH key access (ssh user@host works)
#   - Remote python3
set -euo pipefail

REMOTE=""
REMOTE_DIR=""
WITH_DASH=0
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dashboard) WITH_DASH=1; shift ;;
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
  echo "Usage: $0 user@host [--dashboard] [--dir PATH]" >&2
  exit 1
fi

if [[ "$REMOTE" == *@* ]]; then
  RUSER="${REMOTE%%@*}"
else
  RUSER="pi"
  REMOTE="pi@$REMOTE"
fi

if [[ -z "$REMOTE_DIR" ]]; then
  REMOTE_DIR="/home/${RUSER}/personal-workspace"
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEPLOY_SRC="$(cd "$(dirname "$0")" && pwd)"

echo "Local monorepo: $ROOT"
echo "Remote:         $REMOTE"
echo "Remote dir:     $REMOTE_DIR"
echo "Dashboard unit: $WITH_DASH"

echo "-> Testing SSH..."
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "echo ok && uname -a && python3 --version"

echo "-> Creating remote directories..."
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR/projects-dashboard' '$REMOTE_DIR/ops/backlog/reports' '$REMOTE_DIR/ops/backlog/seeds'"

echo "-> Rsync projects-dashboard + ops/backlog..."
RSYNC_ARGS=(-az
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.DS_Store'
  --exclude 'ops/backlog/dashboard.log'
  --exclude 'ops/backlog/scheduler.log'
)
if [[ "$DRY" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run -v)
fi

rsync "${RSYNC_ARGS[@]}" \
  "$ROOT/projects-dashboard/" "$REMOTE:$REMOTE_DIR/projects-dashboard/"

rsync "${RSYNC_ARGS[@]}" \
  "$ROOT/ops/backlog/" "$REMOTE:$REMOTE_DIR/ops/backlog/"

# Minimal root files needed for imports
rsync -az "$ROOT/Agents.md" "$REMOTE:$REMOTE_DIR/" 2>/dev/null || true

echo "-> Patching systemd unit paths to $REMOTE_DIR ..."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
for f in workflow-scheduler.service workflow-scheduler.timer workflow-dashboard.service; do
  sed "s|%h/personal-workspace|${REMOTE_DIR}|g" "$DEPLOY_SRC/$f" > "$TMP/$f"
done

echo "-> Installing systemd user units..."
ssh "$REMOTE" "mkdir -p ~/.config/systemd/user"
scp "$TMP/workflow-scheduler.service" "$TMP/workflow-scheduler.timer" \
  "$REMOTE:~/.config/systemd/user/"
if [[ "$WITH_DASH" -eq 1 ]]; then
  scp "$TMP/workflow-dashboard.service" "$REMOTE:~/.config/systemd/user/"
fi

# shellcheck disable=SC2087
ssh "$REMOTE" bash -s -- "$REMOTE_DIR" "$WITH_DASH" <<'REMOTE'
set -euo pipefail
DIR="$1"
WITH_DASH="$2"

loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload

python3 - <<PY
import json
from pathlib import Path
p = Path("${DIR}") / "ops/backlog/scheduler.json"
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
print("scheduler.json ->", cfg.get("backend"), cfg.get("execution_mode"))
PY

# Quick import smoke test
cd "$DIR"
PYTHONPATH=projects-dashboard python3 -c "import scheduler; print('runtime', scheduler.detect_runtime())"

systemctl --user enable --now workflow-scheduler.timer
systemctl --user start workflow-scheduler.service || true
systemctl --user status workflow-scheduler.timer --no-pager || true

if [[ "$WITH_DASH" == "1" ]]; then
  systemctl --user enable --now workflow-dashboard.service
  systemctl --user status workflow-dashboard.service --no-pager || true
fi

echo "Timers:"
systemctl --user list-timers --all | grep -i workflow || true
REMOTE

echo ""
echo "OK. Pi owns 24/7 ticks."
echo "  Host: $REMOTE"
echo "  Dir:  $REMOTE_DIR"
echo "  On Mac: uninstall local cron if both would tick; set backend=raspi"
echo "  Claim jobs: dashboard -> Launch pending on this Mac"
