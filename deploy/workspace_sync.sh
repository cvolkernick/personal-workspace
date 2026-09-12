#!/usr/bin/env bash
# Pull origin/work/treasury into the Pi FCC live clone and restart dashboard units
# when HEAD moves. Preserves durable runtime state (secrets, snapshots, backlog,
# fitness data, sprint ceremony).
#
# Product → branch (#560): FCC/treasury = work/treasury only; FitDash = master
# (Vercel). This script is the FCC live-root sync — it refuses master and
# work/holistic before any git mutation. Do not reset this checkout to master.
#
# Package-level rsync (e.g. resistance-dashboard/deploy/install_remote.sh) is
# emergency / pre-merge only — the next successful sync hard-resets code trees
# to origin/work/treasury.
set -euo pipefail

DIR="${WORKSPACE_DIR:-$HOME/personal-workspace}"
BRANCH="${SYNC_BRANCH:-work/treasury}"
REMOTE="${SYNC_REMOTE:-origin}"
LOG_TAG="workspace-sync"
DURABLE_TAR="${TMPDIR:-/tmp}/workspace-sync-durable-$$.tgz"
LOG_DIR="${HOME}/.local/share/workspace-sync"
LOG_FILE="${LOG_DIR}/sync.log"
SERVED_SHA_FILE="${HOME}/.config/personal-workspace/last_served_origin_sha"

# Load GITHUB_TOKEN etc. for private HTTPS remotes (never echo token)
if [[ -f "${HOME}/.config/workflow-scheduler.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HOME}/.config/workflow-scheduler.env"
  set +a
fi

log() {
  echo "[$LOG_TAG] $*"
  mkdir -p "$LOG_DIR" 2>/dev/null || true
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [$LOG_TAG] $*" >>"$LOG_FILE" 2>/dev/null || true
}

cd "$DIR"

if [[ ! -d .git ]]; then
  log "ERROR: $DIR is not a git repo"
  exit 1
fi

# Refuse wrong pulls before any git mutation (issue #560).
# Allowlist is work/treasury only on the FCC live root.
MAP_PY="$DIR/deploy/product_branch_map.py"
if command -v python3 >/dev/null 2>&1 && [[ -f "$MAP_PY" ]]; then
  if ! python3 "$MAP_PY" check-sync --branch "$BRANCH" >/dev/null; then
    log "ERROR: refused SYNC_BRANCH=${BRANCH} for FCC live root (allowlist: work/treasury only)"
    exit 1
  fi
elif [[ "$BRANCH" != "work/treasury" ]]; then
  log "ERROR: FCC live root refuses SYNC_BRANCH=${BRANCH} (allowlist: work/treasury only)"
  exit 1
fi

# Keep origin URL free of embedded credentials (tests may keep a local origin)
if [[ -z "${WORKSPACE_SYNC_KEEP_REMOTE:-}" ]]; then
  git remote set-url "$REMOTE" "https://github.com/cvolkernick/personal-workspace.git" 2>/dev/null || true
fi

git_auth() {
  # gc.auto=0: protect(treasury) + sync racing HEAD.lock was the #661 stall.
  local -a cfg=(-c gc.auto=0 -c maintenance.auto=false)
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    git "${cfg[@]}" -c "url.https://x-access-token:${GITHUB_TOKEN}@github.com/.insteadOf=https://github.com/" "$@"
  else
    git "${cfg[@]}" "$@"
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
    ops/board/youtube_groom_health.json
    fitness/data/day_constraints.json
    financial-command/treasury_latest.json
    financial-command/current-branch.txt
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
    -e 'ops/board/youtube_groom_health.json' \
    -e 'fitness/data/' \
    -e '**/data/schedule_state.json' \
    -e '.env' \
    -e '*.env' \
    -e 'ops/backlog/dashboard.log' \
    -e 'ops/backlog/scheduler.log' \
    >/dev/null 2>&1 || true
}

other_worktrees_holding_branch() {
  # Paths of worktrees (other than $DIR) that currently have $BRANCH checked out.
  local line wt
  while IFS= read -r line; do
    if [[ "$line" == *"[$BRANCH]"* ]]; then
      wt="${line%% *}"
      if [[ "$wt" != "$DIR" && "$wt" != "${DIR}/" ]]; then
        printf '%s\n' "$wt"
      fi
    fi
  done < <(git worktree list 2>/dev/null || true)
}

release_branch_from_other_worktrees() {
  # Issue #561: live FCC is this clone. Do not stay detached because
  # ~/personal-workspace-worktrees/treasury owns the branch name.
  local wt
  while IFS= read -r wt; do
    [[ -z "$wt" ]] && continue
    log "releasing $BRANCH from worktree $wt (detach — live FCC is $DIR)"
    git -C "$wt" checkout -f --detach HEAD >/dev/null 2>&1 || \
      git -C "$wt" checkout -f --detach >/dev/null 2>&1 || \
      log "WARN: could not detach $wt"
  done < <(other_worktrees_holding_branch)
}

land_on_remote_branch() {
  # Attach $BRANCH on this clone. If another worktree holds the name, detach
  # that worktree first — never leave live FCC detached (HEAD.lock dual-SoT).
  release_branch_from_other_worktrees
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

# Issue #661: local protect(treasury) / RH snapshot commits on the serving
# clone are phantom SHAs. Snapshots stay dirty; durable tar preserves them.
install_live_commit_hook() {
  local gitdir hookdir hook
  gitdir=$(git rev-parse --git-dir 2>/dev/null) || return 0
  hookdir="${gitdir}/hooks"
  mkdir -p "$hookdir"
  hook="${hookdir}/pre-commit"
  cat >"$hook" <<'HOOK'
#!/bin/bash
# Installed by deploy/workspace_sync.sh — FCC live clone (issue #661).
echo "FCC live clone refuses local commits (#661)." >&2
echo "Snapshots stay uncommitted; workspace-sync durable tar preserves them." >&2
echo "Do not git commit in ~/personal-workspace on prism-gateway." >&2
exit 1
HOOK
  chmod +x "$hook"
}

restart_fcc() {
  if command -v systemctl >/dev/null 2>&1 && \
     systemctl --user cat financial-command.service >/dev/null 2>&1; then
    log "restarting financial-command.service"
    systemctl --user restart financial-command.service
    return $?
  fi
  log "WARN: financial-command.service absent — skip FCC bounce (non-prod)"
  return 0
}

mark_served() {
  local sha="$1"
  mkdir -p "$(dirname "$SERVED_SHA_FILE")"
  printf '%s\n' "$sha" >"$SERVED_SHA_FILE"
  log "marked served origin ${sha:0:8}"
}

read_served() {
  if [[ -f "$SERVED_SHA_FILE" ]]; then
    tr -d '[:space:]' <"$SERVED_SHA_FILE"
  fi
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

# Stamp expected branch for FCC UI / tip-health (issue #628). File is in git on
# work/treasury; rewriting keeps it correct if a local protect overwrote it.
mkdir -p financial-command
echo "$BRANCH" >financial-command/current-branch.txt
install_live_commit_hook

AFTER="$(git rev-parse HEAD)"
ON_BRANCH="$(git branch --show-current 2>/dev/null || echo '?')"
ORIGIN_SHA="$(git rev-parse "$REMOTE/$BRANCH")"
log "HEAD ${BEFORE:0:8} → ${AFTER:0:8} on ${ON_BRANCH} origin=${ORIGIN_SHA:0:8}"

if [[ "$ON_BRANCH" != "$BRANCH" ]]; then
  log "ERROR: expected branch $BRANCH after sync, got ${ON_BRANCH:-detached}"
  exit 1
fi
if [[ "$AFTER" != "$ORIGIN_SHA" ]]; then
  log "ERROR: HEAD ${AFTER:0:8} does not match $REMOTE/$BRANCH ${ORIGIN_SHA:0:8}"
  exit 1
fi

run_fcc_tip_health() {
  # Issue #562: read-only SHA/branch assert. Never blocks sync; never mutates git.
  local py="${HOME}/.config/personal-workspace/fcc_tip_health.py"
  [[ -f "$py" ]] || py="$DIR/deploy/fcc_tip_health.py"
  [[ -f "$py" ]] || return 0
  python3 "$py" --workspace "$DIR" --no-fetch --dry-run >/dev/null || \
    log "WARN: fcc tip health mismatch (logged; GitHub #701 is the timer's job)"
}

# Issue #661: bounce FCC when origin SHA is not the last served SHA — even if
# this tick's BEFORE==AFTER (a prior tick reset HEAD then skipped restart
# because on_merge.sh is not on work/treasury). Do not treat a phantom local
# commit as a merge range for on_merge.
LAST_SERVED="$(read_served)"
ON_MERGE="$DIR/deploy/on_merge.sh"
if [[ ! -x "$ON_MERGE" && -f "$ON_MERGE" ]]; then
  chmod +x "$ON_MERGE" 2>/dev/null || true
fi

if [[ "$ORIGIN_SHA" == "$LAST_SERVED" ]]; then
  log "origin ${ORIGIN_SHA:0:8} already served — skip restart"
  run_fcc_tip_health
  exit 0
fi

log "origin ${ORIGIN_SHA:0:8} not yet served (last=${LAST_SERVED:-none})"

served_ok=0
if [[ "$BEFORE" != "$AFTER" && -f "$ON_MERGE" ]] && \
   git cat-file -e "${BEFORE}^{commit}" 2>/dev/null && \
   git merge-base --is-ancestor "$BEFORE" "$AFTER" 2>/dev/null; then
  log "code updated — path-scoped on_merge (local)"
  if bash "$ON_MERGE" --before "$BEFORE" --after "$AFTER" --mode local; then
    log "path-scoped deploy done"
    served_ok=1
  else
    log "ERROR: on_merge failed — falling back to FCC restart"
  fi
fi
if [[ "$served_ok" -ne 1 ]]; then
  if restart_fcc; then
    served_ok=1
  else
    log "ERROR: FCC restart failed; not marking served"
    run_fcc_tip_health
    exit 1
  fi
fi
if [[ "$served_ok" -eq 1 ]]; then
  mark_served "$ORIGIN_SHA"
fi
run_fcc_tip_health
exit 0
