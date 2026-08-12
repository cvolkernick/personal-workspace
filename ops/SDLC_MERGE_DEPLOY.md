# SDLC: merge → path-scoped Pi auto-deploy (issue #25)

**Status:** Phase 1 shipped on branch `feature/sdlc-auto-deploy-25`.  
**Human gate:** PR merge stays Chris-only. This automation only runs **after** merge to `master`.

## What happens after you merge

```
merge to master
  → GitHub Action deploy-on-merge (map + optional SSH kick)
  → Pi workspace-sync.timer (≤5 min) OR immediate systemctl start
  → git pull origin/master (durable state preserved)
  → deploy/on_merge.sh --mode local
       · map changed paths via deploy/path_unit_map.json
       · restart ONLY mapped dashboard/platform units
       · health-check those units
       · post result to #workflow when buzz CLI is available
```

## Safety rails

| Rule | Behavior |
|------|----------|
| Path-scoped | Only units mapped from changed prefixes restart |
| No thrash-all | Full unit list is never the default |
| Treasury / secrets | **Manual only** — no auto-restart |
| Deploy glue (`deploy/`) | Lands via git pull; unit install remains operator-run |
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
| `business/panamerica-auto/` | `panamerica-auto.service` | panamerica |
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
| **Default:** merge → `workspace-sync.timer` | After PR lands on `master` | `git` hard-reset to `origin/master` + path-scoped unit restart | Always matches `master` tip |
| **Mac `deploy/install_remote.sh --only …`** | Unit files changed, first-time install, or sync broken | rsync selected packages + reinstall systemd units | Does **not** advance git; next sync overwrites code from `master` |
| **Package rsync** (e.g. `resistance-dashboard/deploy/install_remote.sh`) | Emergency / pre-merge hot fix only | rsync one app tree | Leaves monorepo **dirty vs git**; next successful `workspace-sync` **replaces** rsynced code with `master` |

**Do not** treat package rsync as durable prod. If you hot-fix, either merge the same tree to `master` before the next sync cycle, or expect prod to snap back to merged tip.

**Do not** disable `workspace-sync.timer` without a written reason + re-enable plan. When it is off, Pi freezes at whatever last landed (the 2026-08-11 FitDash “old shell” incident).

## Recovery: stuck rebase / missing `deploy/` / timer dead

Symptoms:

- `git status` shows `(no branch, rebasing master)` or detached HEAD far behind `origin/master`
- `systemctl --user status workspace-sync.service` → status 127 / `deploy/workspace_sync.sh: No such file`
- `workspace-sync.timer` inactive/disabled while dashboards still run stale trees

On Pi (`prism-agent@prism-gateway`):

```bash
cd ~/personal-workspace
# Prefer the script once any copy exists (Mac can scp deploy/workspace_sync.sh first):
bash deploy/workspace_sync.sh
# Or force:
git rebase --abort 2>/dev/null || rm -rf .git/rebase-merge .git/rebase-apply
git fetch origin master
git checkout -f -B master origin/master
git reset --hard origin/master
# re-enable timer (unit files live under deploy/units/)
cp -f deploy/units/workspace-sync.{service,timer} ~/.config/systemd/user/
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
