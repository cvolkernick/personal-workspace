#!/usr/bin/env bash
# Deploy Horizon Macro dashboard to Pi (prod).
# Usage: bash research/horizon/deploy/install_remote.sh [user@host]
set -euo pipefail

REMOTE="${1:-prism-agent@192.168.100.98}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
UNIT_SRC="$ROOT/research/horizon/deploy/horizon-dashboard.service"
UNIT_NAME="horizon-dashboard.service"
REMOTE_WS="/home/prism-agent/personal-workspace"
REMOTE_UNIT_DIR='~/.config/systemd/user'

echo "→ Sync research/horizon → $REMOTE:$REMOTE_WS/research/horizon"
rsync -az --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'data/history' \
  "$ROOT/research/horizon/" \
  "$REMOTE:$REMOTE_WS/research/horizon/"

# Ensure package parents exist for import research.horizon
ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$REMOTE_WS/research' && \
  touch '$REMOTE_WS/research/__init__.py' 2>/dev/null || true; \
  test -f '$REMOTE_WS/research/horizon/__init__.py' || echo '' > '$REMOTE_WS/research/horizon/__init__.py'"

echo "→ Install user systemd unit"
scp -q "$UNIT_SRC" "$REMOTE:$REMOTE_UNIT_DIR/$UNIT_NAME"
ssh -o BatchMode=yes "$REMOTE" "systemctl --user daemon-reload && \
  systemctl --user enable --now $UNIT_NAME && \
  systemctl --user restart $UNIT_NAME && \
  sleep 1 && \
  systemctl --user is-active $UNIT_NAME && \
  curl -fsS http://127.0.0.1:8795/api/health"

echo "✓ Horizon Macro on Pi"
echo "  LAN:       http://192.168.100.98:8795/"
echo "  Tailscale: http://100.67.114.2:8795/"
echo "  Health:    http://192.168.100.98:8795/api/health"
