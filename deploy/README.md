# Deploy all dashboard backends on Raspberry Pi (24/7)

## Architecture

| Layer | Role |
|--------|------|
| **Pi (systemd user units)** | Always-on backends on `0.0.0.0` for all six dashboards |
| **Terminal / laptop** | Local frontend: serves UI; `--backend` / `backend.json` proxies `/api/*` to Pi |
| **Private mesh (Tailscale)** | Off-home-network reachability — **not** public port-forward of bare HTTP |

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

## Terminal frontends (UI local, API remote)

On a laptop (same LAN **or** over Tailscale):

```bash
# One-shot CLI:
python3 orchestra/server.py --backend http://PI_OR_TAILSCALE:8790 --no-browser
python3 projects-dashboard/server.py --backend http://PI_OR_TAILSCALE:8765
python3 holistic/server.py --backend http://PI_OR_TAILSCALE:8770
python3 iot/server.py --backend http://PI_OR_TAILSCALE:8780
python3 financial-command/server.py --backend http://PI_OR_TAILSCALE:8000 --offline
python3 resistance-dashboard/server.py --backend http://PI_OR_TAILSCALE:8787
```

Or per-dashboard `backend.json` next to `server.py`:

```json
{
  "url": "http://prism-gateway:8780",
  "label": "pi-tailscale"
}
```

- **`--local`** forces local API (ignore config / CLI backend).
- Empty / missing `backend.json` + no `--backend` → classic single-process local mode.

IoT already used this pattern; all six dashboards share `remote_backend.py`.

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

## Related

- Per-package IoT-only deploy: `iot/deploy/` (worker vs dashboard).
- Shared proxy helper: `remote_backend.py` at monorepo root.
