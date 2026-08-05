#!/usr/bin/env bash
# Pull origin/master into the Pi monorepo clone and restart dashboard units when HEAD moves.
# Preserves durable runtime state (secrets, snapshots, backlog, fitness data, sprint ceremony).
set -euo pipefail

DIR="${WORKSPACE_DIR:-$HOME/personal-workspace}"
BRANCH="${SYNC_BRANCH:-master}"
REMOTE="${SYNC_REMOTE:-origin}"
LOG_TAG="workspace-sync"
DURABLE_TAR="${TMPDIR:-/tmp}/workspace-sync-durable-$$.tgz"

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
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    git -c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/" "$@"
  else
    git "$@"
  fi
}

# Snapshot durable paths so hard reset / clean cannot wipe prod runtime
preserve_durable() {
  local list
  list=$(mktemp)
  local paths=(
    treasury/config.json
    treasury/snapshots
    iot/secrets.json
    iot/groups.json
    iot/schedule.json
    iot/bulbs.json
    iot/backend.json
    iot/wiz-lights
    iot/data
    fitness/data
    fitness/exercises/goals.json
    fitness/nutrition
    ops/backlog/items.json
    ops/backlog/jobs.json
    ops/backlog/scheduler.json
    ops/backlog/suggestions.json
    ops/sprint
    financial-command/treasury_latest.json
    investment/fund_manager_journal.md
    investment/positions.md
  )
  : >"$list"
  for p in "${paths[@]}"; do
    [[ -e "$p" ]] && echo "$p" >>"$list"
  done
  find treasury investment ops fitness financial-command iot -maxdepth 3 \
    \( -name '*journal*' -o -name '*_latest.json' -o -name 'secrets.json' \) 2>/dev/null >>"$list" || true
  sort -u "$list" -o "$list"
  if [[ -s "$list" ]]; then
    tar -czf "$DURABLE_TAR" -T "$list" 2>/dev/null || true
    log "preserved $(wc -l <"$list") durable path(s)"
  fi
  rm -f "$list"
}

restore_durable() {
  if [[ -f "$DURABLE_TAR" ]]; then
    tar -xzf "$DURABLE_TAR" -C "$DIR" 2>/dev/null || true
    chmod 600 iot/secrets.json 2>/dev/null || true
    chmod 600 treasury/config.json 2>/dev/null || true
    rm -f "$DURABLE_TAR"
    log "restored durable runtime state"
  fi
}

BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
CURRENT="$(git branch --show-current 2>/dev/null || true)"
log "sync start branch=${CURRENT:-detached} HEAD=${BEFORE:0:8}"

preserve_durable

if ! git_auth fetch --prune "$REMOTE" "$BRANCH"; then
  log "ERROR: git fetch failed (check network / GITHUB_TOKEN in ~/.config/workflow-scheduler.env)"
  restore_durable
  exit 1
fi

git clean -fd \
  -e 'iot/data/' \
  -e 'iot/secrets.json' \
  -e 'iot/groups.json' \
  -e 'iot/schedule.json' \
  -e 'iot/bulbs.json' \
  -e 'treasury/snapshots/' \
  -e 'treasury/config.json' \
  -e 'ops/sprint/' \
  -e 'fitness/data/' \
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

restore_durable

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
  horizon-dashboard.service
)
for u in "${UNITS[@]}"; do
  systemctl --user try-restart "$u" 2>/dev/null || true
done
sleep 1
for u in "${UNITS[@]}"; do
  st="$(systemctl --user is-active "$u" 2>/dev/null || echo unknown)"
  log "unit $u → $st"
done
log "restart done"
exit 0
