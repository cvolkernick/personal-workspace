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
    ops/board/youtube_groom_tick_report.json
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
  # Journals: *journal.md / *journal.jsonl only — never .py/.pyc. Broad *journal*
  # re-overlays treasury/fund_manager_journal_sync.py after every hard-reset (#758).
  find treasury investment ops fitness financial-command iot -maxdepth 3 \
    \( -name '*journal.md' -o -name '*journal.jsonl' -o -name '*_latest.json' -o -name 'secrets.json' \) \
    ! -name '*.py' ! -name '*.pyc' 2>/dev/null >>"$list" || true
  sort -u "$list" -o "$list"
  # Expand directories so in-flight poll logs and locks are not in the tar.
  # Untar used to replace fund_manager_bp_poll_*.log mid-write (#1013).
  expanded=$(mktemp)
  while IFS= read -r p; do
    [[ -z "$p" ]] && continue
    if [[ -d "$p" ]]; then
      find "$p" \( -type f -o -type l \) \
        ! -name '*.log' ! -name '*.lock' ! -name '*.tmp' \
        >>"$expanded" 2>/dev/null || true
    elif [[ -e "$p" || -L "$p" ]]; then
      case "$p" in
        *.log|*.lock|*.tmp) ;;
        *) printf '%s\n' "$p" >>"$expanded" ;;
      esac
    fi
  done <"$list"
  sort -u "$expanded" -o "$expanded"
  if [[ -s "$expanded" ]]; then
    tar -czf "$DURABLE_TAR" -T "$expanded" 2>/dev/null || true
    log "preserved $(wc -l <"$expanded") durable path(s)"
  fi
  rm -f "$list" "$expanded"
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

# Compare committed treasury/config.json with the durable Pi copy (#1013).
# Prints key paths only. Never prints values.
#   warn COMMITTED DURABLE
#   reconcile BEFORE AFTER DURABLE
_config_drift_py() {
  python3 - "$@" <<'PY'
import json
import os
import sys
from pathlib import Path

def load(path: Path):
    if not path.is_file() or path.stat().st_size == 0:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None

def leaves(obj, prefix=""):
    out = {}
    if not isinstance(obj, dict):
        return out
    for key, val in obj.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(val, dict):
            out.update(leaves(val, path))
        else:
            out[path] = val
    return out

def get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True

def set_path(obj, path, value):
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value

def del_path(obj, path):
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)

mode = sys.argv[1]
if mode == "warn":
    committed = load(Path(sys.argv[2]))
    durable = load(Path(sys.argv[3]))
    if committed is None or durable is None:
        sys.exit(0)
    for key, val in sorted(leaves(committed).items()):
        got, ok = get_path(durable, key)
        if ok and got != val:
            print(f"WARN durable config drifts from HEAD at {key}; local value kept")
    sys.exit(0)

if mode != "reconcile":
    print(f"WARN config reconcile skipped (unknown mode {mode})")
    sys.exit(0)

before = load(Path(sys.argv[2]))
after = load(Path(sys.argv[3]))
durable_path = Path(sys.argv[4])
durable = load(durable_path)
if before is None:
    print("WARN config reconcile skipped (no committed config before pull); durable copy kept")
    sys.exit(0)
if after is None or durable is None:
    print("WARN config reconcile skipped (after or durable unreadable)")
    sys.exit(0)

b = leaves(before)
a = leaves(after)
changed = False
for key in sorted(set(b) - set(a)):
    del_path(durable, key)
    print(f"reapplied removed committed key {key}")
    changed = True
for key in sorted(a):
    if key not in b or b[key] != a[key]:
        set_path(durable, key, a[key])
        print(f"reapplied committed key {key}")
        changed = True
    else:
        got, ok = get_path(durable, key)
        if ok and got != a[key]:
            print(f"WARN durable config drifts from HEAD at {key}; local value kept")
if changed:
    payload = json.dumps(durable, indent=2, ensure_ascii=False) + "\n"
    tmp = durable_path.parent / f".{durable_path.name}.{os.getpid()}.tmp"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, durable_path)
PY
}

_log_config_lines() {
  local line
  while IFS= read -r line; do
    [[ -n "$line" ]] && log "$line"
  done
}

warn_durable_config_drift() {
  local committed
  [[ -f treasury/config.json ]] || return 0
  committed=$(mktemp)
  if ! git show "HEAD:treasury/config.json" >"$committed" 2>/dev/null; then
    rm -f "$committed"
    return 0
  fi
  _log_config_lines < <(_config_drift_py warn "$committed" treasury/config.json)
  rm -f "$committed"
}

reconcile_durable_config() {
  local before="$1" after="$2" durable="$3"
  if [[ ! -f "$before" || ! -f "$after" || ! -f "$durable" ]]; then
    log "WARN config reconcile skipped (missing before, after, or durable)"
    return 0
  fi
  _log_config_lines < <(_config_drift_py reconcile "$before" "$after" "$durable")
  chmod 600 "$durable" 2>/dev/null || true
}

head_matches_origin() {
  local origin_sha head_now branch_now
  origin_sha="$(git rev-parse "$REMOTE/$BRANCH" 2>/dev/null || true)"
  head_now="$(git rev-parse HEAD 2>/dev/null || true)"
  branch_now="$(git branch --show-current 2>/dev/null || true)"
  [[ -n "$origin_sha" && "$branch_now" == "$BRANCH" && "$head_now" == "$origin_sha" ]] || return 1
  [[ ! -d .git/rebase-merge && ! -d .git/rebase-apply ]] || return 1
  [[ ! -f .git/MERGE_HEAD && ! -f .git/CHERRY_PICK_HEAD && ! -f .git/REVERT_HEAD ]] || return 1
  return 0
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
    -e 'ops/board/youtube_groom_tick_report.json' \
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

# Fetch before any tar/reset. A failed fetch must not untar over live logs.
if ! git_auth fetch --prune "$REMOTE" "$BRANCH"; then
  log "ERROR: git fetch failed (check network / GITHUB_TOKEN in ~/.config/workflow-scheduler.env)"
  exit 1
fi

if head_matches_origin; then
  # Every 5 minutes used to hard-reset tracked treasury_latest.json back to the
  # committed copy (no Plaid block) for the gap before untar (#1013).
  log "HEAD ${BEFORE:0:8} matches $REMOTE/$BRANCH — skip reset and durable untar"
  warn_durable_config_drift
else
  cfg_before=$(mktemp)
  cfg_after=$(mktemp)
  git show "HEAD:treasury/config.json" >"$cfg_before" 2>/dev/null || true
  preserve_durable
  clear_in_progress_git_ops
  clean_blocking_untracked
  if ! land_on_remote_branch; then
    restore_durable
    rm -f "$cfg_before" "$cfg_after"
    exit 1
  fi
  if [[ -f treasury/config.json ]]; then
    cp treasury/config.json "$cfg_after"
  fi
  restore_durable
  reconcile_durable_config "$cfg_before" "$cfg_after" treasury/config.json
  rm -f "$cfg_before" "$cfg_after"
fi

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
    log "WARN: fcc tip health not ok (unknown or drift; GitHub #701 is the timer's job)"
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
