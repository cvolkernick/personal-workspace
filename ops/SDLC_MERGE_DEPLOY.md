# SDLC: merge → path-scoped Pi auto-deploy (issue #25)

**Status:** Phase 1 shipped on branch `feature/sdlc-auto-deploy-25`.  
**Human gate:** PR merge stays Chris-only for dangerous paths; Grok eng-gate otherwise.

**Product → branch (#560):** FCC + treasury PRs land on **`work/treasury` only**. FitDash PRs land on **`master`** (Vercel). Pi `workspace-sync` allowlist is `work/treasury`; it refuses `master` / `work/holistic` and will not reset the FCC live tip to master. Map: `deploy/product_branch_map.py`.

**Live tree (#561):** Pi FCC is the **main** clone `~/personal-workspace`, **attached** `work/treasury`. Do not let `~/personal-workspace-worktrees/treasury` own that branch name (that forces detached HEAD + HEAD.lock races). `FCC_LIVE_TREE=main` skips ensure/repair of a treasury worktree on this host. Mac still uses the treasury worktree.

## What happens after you merge

```
FCC PR merge to work/treasury
  → Pi workspace-sync.timer (≤5 min) OR immediate systemctl start
  → land origin/work/treasury (durable state preserved; master/holistic refused)
  → bounce FCC when origin SHA ≠ last-served (on_merge.sh is not on work/treasury)
  → deploy/on_merge.sh --mode local when that script exists and BEFORE is an ancestor
       · map changed paths via deploy/path_unit_map.json
       · restart ONLY mapped dashboard/platform units
       · health-check those units
       · post result to #workflow when buzz CLI is available

FitDash PR merge to master
  → Vercel Git integration (not this Pi checkout)
```

## Safety rails

| Rule | Behavior |
|------|----------|
| Path-scoped | Only units mapped from changed prefixes restart |
| No thrash-all | Full unit list is never the default |
| Treasury / secrets | **Manual only** — no auto-restart |
| Deploy glue (`deploy/`) | **Does not** land via FCC `work/treasury` sync (`deploy/` is master-only). Permanent path: `scp` / `install_remote.sh` into `~/.config/personal-workspace/` (#664). Do not cherry-pick onto `work/treasury`. |
| One at a time | Lockfile under `$XDG_RUNTIME_DIR` / `/tmp` |
| Merge authority | Still human (Chris) |

## Path → unit map

Source of truth: `deploy/path_unit_map.json`.

| Path prefix | Unit | `--only` |
|-------------|------|----------|
| `orchestra/` | `orchestra-dashboard.service` | orchestra |
| `financial-command/` | `financial-command.service` | financial-command |
| `projects-dashboard/` | `workflow-dashboard.service` | workflow |
| `holistic/` | `holistic-dashboard.service` | holistic |
| `iot/` | `iot-dashboard.service` | iot |
| `resistance-dashboard/`, `fitness/` | `resistance-dashboard.service` | resistance |
| `research/horizon/`, `horizon/` | `horizon-dashboard.service` | horizon |
| `archive/legacy/panamerica-auto-website/` (archived, legacy static MVP) | `panamerica-auto.service` (retired) | panamerica |
| `remote_backend.py`, `dashboard_endpoints.py` | all auto units | (joined) |
| `treasury/`, secrets, `deploy/`, `investment/` | **manual** | — |

## Operator commands

```bash
# Dry-run map for last commit
bash deploy/on_merge.sh --before HEAD~1 --after HEAD --dry-run

# Dry-run explicit paths
python3 deploy/map_changed_paths.py --path iot/server.py --path treasury/config.json --format summary

# Mac-side path-scoped rsync deploy (optional; not the default runner)
bash deploy/on_merge.sh --before HEAD~1 --after HEAD --mode remote --no-notify

# Force Pi sync now (from Pi)
systemctl --user start workspace-sync.service
journalctl --user -u workspace-sync.service -n 50 --no-pager

# Manual full/subset install still available
bash deploy/install_remote.sh prism-agent@192.168.100.98 --only orchestra,iot
```

## GitHub secrets (optional fast path)

Without secrets, Pi still deploys within the 5-minute `workspace-sync.timer`.

To kick Pi immediately on every `master` push, set repo secrets:

| Secret | Example |
|--------|---------|
| `PI_SSH_HOST` | `192.168.100.98` or Tailscale IP |
| `PI_SSH_KEY` | private key for `prism-agent` |
| `PI_SSH_USER` | `prism-agent` (optional; default in workflow) |

## Health checks

`deploy/on_merge.sh` probes service keys via `dashboard_endpoints.py` against:

- **local mode (Pi):** `127.0.0.1`
- **remote mode (Mac):** `PI_HOST` / `endpoints.json` (`192.168.100.98`)

Failures exit non-zero so the systemd oneshot / CI can surface them. Buzz notify is best-effort.

## Deploy path contract (read this)

| Path | When to use | What it does | Git on Pi |
|------|-------------|--------------|-----------|
| **Default (FCC live):** `workspace-sync.timer` | After FCC PR lands on `work/treasury` | `git` hard-reset to `origin/work/treasury` + path-scoped unit restart | Always matches `work/treasury` tip |
| **FitDash prod** | After FitDash PR lands on `master` | Vercel Git integration | Not this checkout |
| **Mac `deploy/install_remote.sh --only …`** | Unit files changed, first-time install, or sync broken | rsync selected packages + reinstall systemd units | Does **not** advance git; next sync overwrites code from `work/treasury` |
| **Package rsync** (e.g. `resistance-dashboard/deploy/install_remote.sh`) | Emergency / pre-merge hot fix only | rsync one app tree | Leaves monorepo **dirty vs git**; next successful `workspace-sync` **replaces** rsynced code with `work/treasury` |

**Do not** treat package rsync as durable prod. If you hot-fix FCC, merge the same tree to `work/treasury` before the next sync cycle, or expect prod to snap back to that tip. **Do not** `git checkout master` / `git pull origin master` / `git pull origin work/holistic` on the Pi FCC clone.

**Do not** disable `workspace-sync.timer` without a written reason + re-enable plan. When it is off, Pi freezes at whatever last landed (the 2026-08-11 FitDash “old shell” incident).

## Recovery: stuck rebase / missing `deploy/` / timer dead

Symptoms:

- `git status` shows `(no branch, rebasing …)` or detached HEAD far behind `origin/work/treasury`
- `systemctl --user status workspace-sync.service` → status 127 / `deploy/workspace_sync.sh: No such file`
- `workspace-sync.timer` inactive/disabled while dashboards still run stale trees

On Pi (`prism-agent@prism-gateway`):

```bash
cd ~/personal-workspace
# Durable copy — never ~/personal-workspace/deploy/ (wiped on the next work/treasury reset).
# From Mac, if the durable script/units are missing:
#   scp deploy/workspace_sync.sh prism-agent@prism-gateway:~/.config/personal-workspace/
#   scp deploy/units/workspace-sync.{service,timer} prism-agent@prism-gateway:~/.config/systemd/user/
bash ~/.config/personal-workspace/workspace_sync.sh
# Or force — FCC live root, never master / work/holistic:
git rebase --abort 2>/dev/null || rm -rf .git/rebase-merge .git/rebase-apply
git fetch origin work/treasury
git checkout -f -B work/treasury origin/work/treasury
git reset --hard origin/work/treasury
systemctl --user daemon-reload
systemctl --user enable --now workspace-sync.timer
systemctl --user start workspace-sync.service
journalctl --user -u workspace-sync.service -n 40 --no-pager
```

`workspace_sync.sh` preserves durable runtime (fitness data, treasury snapshots, iot secrets, backlog, etc.) across hard reset.

## Related

- Plan: nest `PLANS/GENERALIZED_SDLC_PIPELINE.md`
- Policy: nest `GUIDES/PI_PROD_MAC_DEV.md`
- Deploy base: `deploy/README.md`, `deploy/install_remote.sh`, `deploy/workspace_sync.sh`
- Incident recovery notes: nest `RESEARCH/PI_MONOREPO_SYNC_RECOVERY_2026_08_11.md`
- Issue: https://github.com/cvolkernick/personal-workspace/issues/25


## Install GitHub Action (optional, one-time)

The workflow file ships under `ops/github-workflows/` because fine-grained PATs
without the `workflow` scope cannot push to `.github/workflows/`.

```bash
mkdir -p .github/workflows
cp ops/github-workflows/deploy-on-merge.yml .github/workflows/
git add .github/workflows/deploy-on-merge.yml
git commit -m "ci: enable deploy-on-merge workflow"
git push
```

Or paste via GitHub UI. Until installed, **Pi `workspace-sync.timer` is the sole trigger**.
