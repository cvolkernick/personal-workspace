#!/usr/bin/env bash
# Install B2 / knowledge graph + prism puller on finley-gateway (role b2-puller).
#
# Forge / operator on the LAN (this cloud VM cannot reach the LAN):
#   bash deploy/b2-puller/install_finley.sh finley-agent@192.168.100.216
#   bash deploy/b2-puller/install_finley.sh finley-agent@finley-gateway
#   bash deploy/b2-puller/install_finley.sh --local   # already on finley
#
# Installs only:
#   b2.service          :8792 knowledge graph (app Pi queries this; do not rsync B2 onto prism)
#   b2-puller.timer     PULSE LOCK hourly :20 America/New_York — one job:
#                       books + youtube-groom + published + units FROM prism-gateway
#                       (no prism self-backup, no venue keys, no units-only timer, no replica)
#   workspace-sync.timer git pull of this repo (no app-books units)
#
# Does not install FCC, FitDash, Orchestra, Auto Fleet, youtube-groom.
# Does not copy venue keys. Does not write FCC_TREASURY_JSON / Vercel / Mac dests.
set -euo pipefail

REMOTE=""
REMOTE_DIR=""
LOCAL=0
DRY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) REMOTE_DIR="$2"; shift 2 ;;
    --local) LOCAL=1; shift ;;
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

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
UNITS_SRC="$ROOT/deploy/units"
FINLEY_UNITS=(
  b2.service
  b2-puller.service
  b2-puller.timer
  workspace-sync.service
  workspace-sync.timer
)

if [[ "$LOCAL" -eq 1 ]]; then
  RUSER="${USER:-finley-agent}"
  REMOTE_DIR="${REMOTE_DIR:-$HOME/personal-workspace}"
  echo "Local install as $RUSER"
  echo "Dir: $REMOTE_DIR"
  mkdir -p "$HOME/.config/systemd/user" "$HOME/B2" "$HOME/b2-pulls/prism"
  for f in "${FINLEY_UNITS[@]}"; do
    src="$UNITS_SRC/$f"
    [[ -f "$src" ]] || { echo "missing $src" >&2; exit 1; }
    sed "s|%h/personal-workspace|${REMOTE_DIR}|g" "$src" > "$HOME/.config/systemd/user/$f"
  done
  chmod +x "$REMOTE_DIR/deploy/b2-puller/pull_from_prism.py" \
           "$REMOTE_DIR/deploy/b2-puller/install_finley.sh" \
           "$REMOTE_DIR/deploy/workspace_sync.sh" 2>/dev/null || true
  # Refuse if this looks like a Mac or Vercel tree
  case "$HOME" in
    /Users/*)
      echo "REFUSE: will not install pull dest under a Mac home ($HOME)" >&2
      exit 2
      ;;
  esac
  if [[ "$DRY" -eq 1 ]]; then
    echo "dry-run: units copied, not enabled"
    exit 0
  fi
  loginctl enable-linger "$RUSER" 2>/dev/null || true
  systemctl --user daemon-reload
  systemctl --user enable --now b2.service
  systemctl --user enable --now b2-puller.timer
  systemctl --user enable --now workspace-sync.timer
  systemctl --user start b2-puller.service || true
  systemctl --user status b2.service --no-pager | head -12 || true
  echo "B2 / knowledge graph: http://127.0.0.1:8792/  (LAN/Tailscale: finley-gateway:8792)"
  echo "Puller dest: $HOME/b2-pulls/prism"
  echo "Need SSH from this host to prism-agent@prism-gateway (or 100.67.114.2)."
  exit 0
fi

if [[ -z "$REMOTE" ]]; then
  echo "Usage: $0 finley-agent@HOST | --local" >&2
  exit 1
fi

if [[ "$REMOTE" == *@* ]]; then
  RUSER="${REMOTE%%@*}"
else
  RUSER="finley-agent"
  REMOTE="finley-agent@$REMOTE"
fi
REMOTE_DIR="${REMOTE_DIR:-/home/${RUSER}/personal-workspace}"

echo "Local monorepo: $ROOT"
echo "Remote:         $REMOTE  (role b2-puller)"
echo "Remote dir:     $REMOTE_DIR"

if [[ "$RUSER" == "prism-agent" ]]; then
  echo "REFUSE: this installer is for finley-agent / finley-gateway, not prism-agent." >&2
  exit 2
fi

echo "→ Testing SSH…"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "echo ok && hostname && python3 --version"

echo "→ Creating remote directories…"
ssh "$REMOTE" "mkdir -p '$REMOTE_DIR' ~/B2 ~/b2-pulls/prism ~/.config/systemd/user"

echo "→ Rsync monorepo (scripts only; not a B2 tree, not venue keys)…"
RSYNC_ARGS=(-az
  --exclude '.git'
  --exclude '__pycache__'
  --exclude '*.pyc'
  --exclude '.DS_Store'
  --exclude 'treasury/snapshots/'
  --exclude 'iot/secrets.json'
  --exclude '**/.env'
  --exclude '**/*.env'
)
if [[ "$DRY" -eq 1 ]]; then
  RSYNC_ARGS+=(--dry-run -v)
fi
rsync "${RSYNC_ARGS[@]}" "$ROOT/" "$REMOTE:$REMOTE_DIR/"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
for f in "${FINLEY_UNITS[@]}"; do
  sed "s|%h/personal-workspace|${REMOTE_DIR}|g" "$UNITS_SRC/$f" > "$TMP/$f"
done
scp "$TMP"/* "$REMOTE:~/.config/systemd/user/"

ssh "$REMOTE" bash -s -- "$REMOTE_DIR" <<'REMOTE'
set -euo pipefail
DIR="$1"
case "$HOME" in
  /Users/*)
    echo "REFUSE: Mac home is not a pull dest" >&2
    exit 2
    ;;
esac
loginctl enable-linger "$USER" 2>/dev/null || true
chmod +x "$DIR/deploy/b2-puller/pull_from_prism.py" \
         "$DIR/deploy/b2-puller/install_finley.sh" \
         "$DIR/deploy/workspace_sync.sh" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now b2.service
systemctl --user enable --now b2-puller.timer
systemctl --user enable --now workspace-sync.timer
systemctl --user start b2-puller.service || true
systemctl --user status b2.service --no-pager | head -12 || true
echo "B2 / knowledge graph :8792"
echo "Need SSH: this host → prism-agent@prism-gateway"
REMOTE

echo "Install complete (finley role b2-puller)."
echo "  Query from prism: http://finley-gateway:8792/api/health"
echo "  Do not rsync ~/B2 onto prism."
