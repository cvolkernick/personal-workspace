# Deploy all dashboard backends on Raspberry Pi (24/7)

## Architecture

| Layer | Role |
|--------|------|
| **Pi (systemd user units)** | Always-on backends on `0.0.0.0` for all six dashboards |
| **Terminal / laptop** | Browser only: open Pi URLs via `deploy/open_dashboard.sh` / `*.command` (no local server) |
| **workspace-sync.timer** | Every 5m: `git pull` `origin/master` on the Pi; restart units when HEAD moves |
| **Private mesh (Tailscale)** | Off-home-network reachability — **not** public port-forward of bare HTTP |

Default host is in `deploy/endpoints.json` (`pi_host`). Override anytime with env `PI_HOST` / `DASHBOARD_HOST`.

### Ports (authoritative with monorepo README)

| Service | Port | Unit |
|---------|------|------|
| Orchestra | **8790** | `orchestra-dashboard.service` |
| financial-command | **8000** | `financial-command.service` |
| projects-dashboard (workflow) | **8765** | `workflow-dashboard.service` |
| holistic | **8770** | `holistic-dashboard.service` |
| iot | **8780** | `iot-dashboard.service` |
| resistance-dashboard | **8787** | `resistance-dashboard.service` |

Each unit: **bind all interfaces (`0.0.0.0`)**, **`--no-browser`**, **`--local`** (API handled on the Pi), **Restart=always**.

## Install on Pi

From monorepo root (SSH key required):

```bash
bash deploy/install_remote.sh prism-agent@YOUR_PI_IP
# subset:
bash deploy/install_remote.sh prism-agent@HOST --only orchestra,iot
# custom path:
bash deploy/install_remote.sh user@HOST --dir /home/user/personal-workspace
```

### After install

```bash
ssh user@HOST 'systemctl --user status orchestra-dashboard'
ssh user@HOST 'journalctl --user -u iot-dashboard -f'
curl -sS http://PI_LAN_IP:8790/api/health
```

Keep a **git-synced clone** on the Pi so on-disk sources (strategy, fitness logs, treasury snapshots, ops) stay meaningful. Stale Pi data vs Mac edits is operational — pull regularly.

## Open dashboards (always-on Pi — preferred)

```bash
bash deploy/open_dashboard.sh orchestra
bash deploy/open_dashboard.sh financial-command
bash deploy/open_dashboard.sh projects-dashboard
bash deploy/open_dashboard.sh holistic
bash deploy/open_dashboard.sh iot
bash deploy/open_dashboard.sh resistance-dashboard

# Double-click on Mac: open-command-center.command, */start.command, resistance-dashboard/start.sh
```

These **only open the browser** to the Pi. They do not start a laptop process.

### Optional: local UI proxying to Pi

For development you can still run a local server that proxies `/api/*`:

```bash
python3 orchestra/server.py --backend http://PI_OR_TAILSCALE:8790 --no-browser
```

(`remote_backend.py` + per-package `backend.json` / `--backend` / `--local`.)

## Autonomous git sync on the Pi

`workspace-sync.timer` runs `deploy/workspace_sync.sh` every 5 minutes:

1. Uses `GITHUB_TOKEN` from `~/.config/workflow-scheduler.env` when present  
2. Checks out / fast-forwards **`master`** (stashes dirty tracked files if needed)  
3. If `HEAD` moved → `systemctl --user try-restart` all six dashboard units  

Manual: `systemctl --user start workspace-sync.service`

## Off-network access (Tailscale or equivalent)

**Do not** port-forward these dashboards to the open internet.

1. Install **Tailscale** (or WireGuard / Cloudflare Tunnel private path) on the **Pi** and on each **client**.
2. On the Pi, backends already listen on `0.0.0.0` (reachable on the mesh interface).
3. From anywhere with Tailscale up, set backend URL to the mesh hostname or IP, e.g.  
   `http://prism-gateway:8790` or `http://100.x.y.z:8790`.
4. Same terminal frontend commands work on-LAN and off-network — only the host part of the URL changes.

Optional: Cloudflare Tunnel for HTTPS URLs without a VPN app; still keep access private (access policies), not a bare open port.

## Security notes

- These servers are personal tools with little/no app-level auth.
- Prefer private mesh + home LAN over public exposure.
- Secrets (API keys, Grok auth) stay on the host that needs them; deploy does not provision third-party credentials.

## Merge → path-scoped auto-deploy (issue #25)

After **human** merge to `master`, Pi pulls via `workspace-sync.timer` and runs
`deploy/on_merge.sh` so **only mapped dashboard/platform units** restart.

| Piece | Path |
|-------|------|
| Path → unit map | `deploy/path_unit_map.json` |
| Mapper (testable) | `deploy/map_changed_paths.py` |
| Orchestrator | `deploy/on_merge.sh` |
| Pi pull + call on_merge | `deploy/workspace_sync.sh` |
| GH trigger (map + optional SSH) | `ops/github-workflows/deploy-on-merge.yml` (optional; copy to `.github/workflows/`) |
| Operator runbook | `ops/SDLC_MERGE_DEPLOY.md` |

```bash
# Dry-run what a commit would restart
bash deploy/on_merge.sh --before HEAD~1 --after HEAD --dry-run

# Mapper unit tests
python3 -m unittest discover -s deploy/tests -v
```

**Never auto:** `treasury/`, secrets, `deploy/` glue install, thrash-all units.  
**Optional fast path:** repo secrets `PI_SSH_HOST` + `PI_SSH_KEY` so the Action
starts `workspace-sync.service` immediately; otherwise the 5‑minute timer is enough.

## Related

- Per-package IoT-only deploy: `iot/deploy/` (worker vs dashboard).
- Shared proxy helper: `remote_backend.py` at monorepo root.
- Runbook: `ops/SDLC_MERGE_DEPLOY.md`.
