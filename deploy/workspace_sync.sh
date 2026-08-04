#!/usr/bin/env bash
# Pull origin/master into the Pi monorepo clone and restart dashboard units when HEAD moves.
# Deploy clone: force worktree to match origin/master (local edits on Pi are not preserved).
set -euo pipefail

DIR="${WORKSPACE_DIR:-$HOME/personal-workspace}"
BRANCH="${SYNC_BRANCH:-master}"
REMOTE="${SYNC_REMOTE:-origin}"
LOG_TAG="workspace-sync"

# Load GITHUB_TOKEN etc. for private HTTPS remotes (never echo token)
if [[ -f "${HOME}/.config/workflow-scheduler.env" ]]; then
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

# Keep origin URL free of embedded credentials
git remote set-url "$REMOTE" "https://github.com/cvolkernick/personal-workspace.git" 2>/dev/null || true

git_auth() {
  # Usage: git_auth <git-args…> — use token via insteadOf (never print token)
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    # PAT as x-access-token over HTTPS (works for classic + fine-grained with Contents)
    git -c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/" "$@"
  else
    git "$@"
  fi
}

BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
CURRENT="$(git branch --show-current 2>/dev/null || true)"
log "sync start branch=${CURRENT:-detached} HEAD=${BEFORE:0:8}"

# Fetch master
if ! git_auth fetch --prune "$REMOTE" "$BRANCH"; then
  log "ERROR: git fetch failed (check network / GITHUB_TOKEN in ~/.config/workflow-scheduler.env)"
  exit 1
fi

# Force deploy worktree onto origin/master (handles dirty + untracked rsync leftovers)
# Preserve runtime data that must not be wiped.
git clean -fd \
  -e 'iot/data/' \
  -e '**/data/schedule_state.json' \
  -e '.env' \
  -e '*.env' \
  -e 'ops/backlog/dashboard.log' \
  -e 'ops/backlog/scheduler.log' \
  >/dev/null 2>&1 || true

if ! git_auth checkout -f -B "$BRANCH" "$REMOTE/$BRANCH"; then
  log "checkout failed — hard reset path"
  git_auth symbolic-ref HEAD "refs/heads/$BRANCH" 2>/dev/null || true
  git_auth reset --hard "$REMOTE/$BRANCH"
fi
git_auth reset --hard "$REMOTE/$BRANCH"

AFTER="$(git rev-parse HEAD)"
log "HEAD ${BEFORE:0:8} → ${AFTER:0:8} on $(git branch --show-current 2>/dev/null || echo '?')"

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
# Brief settle then show who is active
sleep 1
for u in "${UNITS[@]}"; do
  st="$(systemctl --user is-active "$u" 2>/dev/null || echo unknown)"
  log "unit $u → $st"
done
log "restart done"
exit 0
