#!/usr/bin/env bash
# Pull origin/master into the Pi monorepo clone and restart dashboard units when HEAD moves.
# Intended for systemd timer on the always-on host.
set -euo pipefail

DIR="${WORKSPACE_DIR:-$HOME/personal-workspace}"
BRANCH="${SYNC_BRANCH:-master}"
REMOTE="${SYNC_REMOTE:-origin}"
LOG_TAG="workspace-sync"

# Load GITHUB_TOKEN etc. for private HTTPS remotes
if [[ -f "${HOME}/.config/workflow-scheduler.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  # shellcheck disable=SC1090
  source "${HOME}/.config/workflow-scheduler.env"
  set +a
fi

log() { echo "[$LOG_TAG] $*"; }

cd "$DIR"

if [[ ! -d .git ]]; then
  log "ERROR: $DIR is not a git repo"
  exit 1
fi

# Prefer authenticated remote when token present
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  AUTH_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/cvolkernick/personal-workspace.git"
  git remote set-url "$REMOTE" "$AUTH_URL" 2>/dev/null || true
fi

BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"

# Drop local modifications that block pull (Pi is deploy target, not edit host)
# Keep untracked data (iot/data etc.) — only stash tracked dirty.
if ! git diff --quiet || ! git diff --cached --quiet; then
  log "stashing local tracked changes before pull"
  git stash push -m "workspace-sync auto $(date -u +%Y%m%dT%H%M%SZ)" --quiet || true
fi

# Detach from feature branches; track master
CURRENT="$(git branch --show-current 2>/dev/null || true)"
if [[ "$CURRENT" != "$BRANCH" ]]; then
  log "checking out $BRANCH (was: ${CURRENT:-detached})"
  git fetch "$REMOTE" "$BRANCH"
  git checkout -B "$BRANCH" "$REMOTE/$BRANCH"
else
  git fetch "$REMOTE" "$BRANCH"
  git merge --ff-only "$REMOTE/$BRANCH" || {
    log "ff-only failed — hard reset to $REMOTE/$BRANCH (deploy clone)"
    git reset --hard "$REMOTE/$BRANCH"
  }
fi

AFTER="$(git rev-parse HEAD)"
log "HEAD $BEFORE → $AFTER"

if [[ "$BEFORE" == "$AFTER" ]]; then
  log "no code change — skip restart"
  exit 0
fi

log "code updated — restarting dashboard units"
UNITS=(
  orchestra-dashboard.service
  financial-command.service
  workflow-dashboard.service
  holistic-dashboard.service
  iot-dashboard.service
  resistance-dashboard.service
)
for u in "${UNITS[@]}"; do
  systemctl --user try-restart "$u" 2>/dev/null || true
done
log "restart requested"
exit 0
