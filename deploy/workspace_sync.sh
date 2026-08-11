#!/usr/bin/env bash
# Pull origin/master into the Pi monorepo clone and restart dashboard units when HEAD moves.
# Preserves durable runtime state (secrets, snapshots, backlog, fitness data, sprint ceremony).
#
# Prod truth: after merge to master, this timer (≤5m) is the default deploy path.
# Package-level rsync (e.g. resistance-dashboard/deploy/install_remote.sh) is emergency /
# pre-merge only — the next successful sync hard-resets code trees to origin/master.
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

# Unstick mid-rebase / merge / cherry-pick that leave HEAD detached and break checkout.
# Incident 2026-08-11: Pi sat at (rebasing master) 1/215 with workspace-sync disabled;
# deploy/ scripts vanished from the working tree so the oneshot failed with status 127.
clear_in_progress_git_ops() {
  if [[ -d .git/rebase-merge || -d .git/rebase-apply ]]; then
    log "clearing stuck rebase"
    if ! git rebase --abort 2>/dev/null; then
      log "rebase --abort failed — force-clear rebase state"
      rm -rf .git/rebase-merge .git/rebase-apply
    fi
  fi
  if [[ -f .git/MERGE_HEAD ]]; then
    log "clearing stuck merge"
    git merge --abort 2>/dev/null || rm -f .git/MERGE_HEAD .git/MERGE_MSG .git/MERGE_MODE
  fi
  if [[ -f .git/CHERRY_PICK_HEAD ]]; then
    log "clearing stuck cherry-pick"
    git cherry-pick --abort 2>/dev/null || rm -f .git/CHERRY_PICK_HEAD
  fi
  if [[ -f .git/REVERT_HEAD ]]; then
    log "clearing stuck revert"
    git revert --abort 2>/dev/null || rm -f .git/REVERT_HEAD
  fi
}

# Drop untracked/ignored noise that blocks checkout -f / reset --hard when
# package rsync left files that master also tracks (or vice versa).
clean_blocking_untracked() {
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
}

land_on_remote_branch() {
  # Prefer atomic force-checkout; fall back to symbolic-ref + hard reset.
  if git_auth checkout -f -B "$BRANCH" "$REMOTE/$BRANCH" 2>/dev/null; then
    git_auth reset --hard "$REMOTE/$BRANCH"
    return 0
  fi
  log "checkout -B failed — symbolic-ref + hard reset"
  git_auth symbolic-ref HEAD "refs/heads/$BRANCH" 2>/dev/null || true
  # Second clean: abort may have left untracked files that track on tip.
  clean_blocking_untracked
  if ! git_auth reset --hard "$REMOTE/$BRANCH"; then
    log "ERROR: cannot reset to $REMOTE/$BRANCH"
    return 1
  fi
  return 0
}

BEFORE="$(git rev-parse HEAD 2>/dev/null || echo none)"
CURRENT="$(git branch --show-current 2>/dev/null || true)"
log "sync start branch=${CURRENT:-detached} HEAD=${BEFORE:0:8}"

preserve_durable
clear_in_progress_git_ops
clean_blocking_untracked

if ! git_auth fetch --prune "$REMOTE" "$BRANCH"; then
  log "ERROR: git fetch failed (check network / GITHUB_TOKEN in ~/.config/workflow-scheduler.env)"
  restore_durable
  exit 1
fi

# Re-clear after fetch in case a concurrent process started a rebase (rare).
clear_in_progress_git_ops
clean_blocking_untracked

if ! land_on_remote_branch; then
  restore_durable
  exit 1
fi

restore_durable

AFTER="$(git rev-parse HEAD)"
ON_BRANCH="$(git branch --show-current 2>/dev/null || echo '?')"
log "HEAD ${BEFORE:0:8} → ${AFTER:0:8} on ${ON_BRANCH}"

if [[ "$ON_BRANCH" != "$BRANCH" ]]; then
  log "ERROR: expected branch $BRANCH after sync, got ${ON_BRANCH:-detached}"
  exit 1
fi

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
