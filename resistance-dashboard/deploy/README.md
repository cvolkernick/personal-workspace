# FitDash on Raspberry Pi (remote access)

Deploy the full FitDash stack (UI + API) to an always-on Pi so you can open it from
phones and laptops on LAN or over **Tailscale**.

| Item | Value |
|------|--------|
| Port | **8787** |
| Bind | `0.0.0.0` (LAN + mesh) |
| Unit | `resistance-dashboard.service` (systemd user) |
| Known LAN host | `192.168.100.98` (same Pi as other dashboards) |
| SSH example | `prism-agent@192.168.100.98` |

## Prerequisites

1. Pi online; SSH key works: `ssh prism-agent@192.168.100.98`
2. Python 3 on the Pi
3. On this Mac: monorepo with FitDash (`resistance-dashboard/`)
4. Optional but recommended: `~/.config/resistance-dashboard/env` (Google + GitHub tokens) — the installer copies it

## Deploy (when on home LAN)

From monorepo root:

```bash
bash resistance-dashboard/deploy/install_remote.sh prism-agent@192.168.100.98
```

Or only this service via monorepo umbrella (if `deploy/` is present on your branch):

```bash
bash deploy/install_remote.sh prism-agent@192.168.100.98 --only resistance
```

## Verify

```bash
curl -sS http://192.168.100.98:8787/api/healthz
open http://192.168.100.98:8787/
ssh prism-agent@192.168.100.98 'systemctl --user status resistance-dashboard'
ssh prism-agent@192.168.100.98 'journalctl --user -u resistance-dashboard -f'
```

## Off-LAN (remote access)

**Do not** port-forward :8787 to the public internet while FitDash is single-user
(shared Google token + your lift history, no multi-tenant login yet).

1. Install **Tailscale** on the Pi and on each client (phone / laptop).
2. With Tailscale up, open `http://<pi-magicdns-or-100.x.y.z>:8787/`.
3. Optional later: Cloudflare Tunnel with Access policy (still private).

## Server CLI (after this branch)

```bash
python3 server.py --host 0.0.0.0 --port 8787 --no-browser --local
# legacy still works:
python3 server.py 8787
```

## Security note

Until Phase 1 (per-user Google OAuth + SQLite isolation), treat FitDash as
**you-only**, reachable only on LAN / private mesh. Secrets live in
`~/.config/resistance-dashboard/env` on the Pi (mode 600).

## Related roadmap

- SQLite workout store (drop GitHub requirement for multi-user)
- Per-user Google OAuth for Health data
- Mobile layout pass
- Optional hardening before any public URL
