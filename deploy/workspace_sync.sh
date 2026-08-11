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
    ops/board/day_constraints.json
    fitness/data/day_constraints.json
    financial-command/treasury_latest.json
    orchestra/data/heartbeat/latest.json
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
  -e 'ops/board/day_constraints.json' \
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

# Path-scoped restart (issue #25): never thrash-all; never auto treasury/secrets.
# on_merge.sh maps BEFORE..AFTER → units, restarts only those, health-checks, optional Buzz notify.
ON_MERGE="$DIR/deploy/on_merge.sh"
if [[ ! -x "$ON_MERGE" && -f "$ON_MERGE" ]]; then
  chmod +x "$ON_MERGE" 2>/dev/null || true
fi
if [[ -f "$ON_MERGE" ]]; then
  log "code updated — path-scoped on_merge (local)"
  # Buzz may be absent on Pi; on_merge falls back to structured log.
  bash "$ON_MERGE" --before "$BEFORE" --after "$AFTER" --mode local || {
    log "ERROR: on_merge failed (HEAD already advanced; units may need manual restart)"
    exit 1
  }
  log "path-scoped deploy done"
  exit 0
fi

# Fallback if on_merge missing (should not happen after #25 lands)
log "WARN: deploy/on_merge.sh missing — refusing thrash-all restart"
log "Operator: bash deploy/on_merge.sh --before $BEFORE --after $AFTER --mode local"
exit 1
