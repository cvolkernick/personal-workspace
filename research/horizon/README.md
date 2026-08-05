# Horizon

Global Macro & Geopolitical Intelligence System — world-state model + daily synthesis.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for design.

## Visual dashboard (primary UX)

```bash
# Mac dev (localhost only)
python3 research/horizon/server.py --bootstrap
# → http://127.0.0.1:8795/

# Pi prod (LAN + Tailscale) — or use the unit below
python3 research/horizon/server.py --host 0.0.0.0 --port 8795 --no-browser --bootstrap
```

Or double-click `research/horizon/start.command`.

**Prod (Pi `prism-gateway`):**

| Surface | URL |
|---------|-----|
| LAN | http://192.168.100.98:8795/ |
| Tailscale (off-LAN) | http://100.67.114.2:8795/ |
| Health | http://192.168.100.98:8795/api/health |

```bash
bash research/horizon/deploy/install_remote.sh prism-agent@192.168.100.98
# unit: horizon-dashboard.service (user systemd, Restart=always)
```

Dashboard tabs (web top / mobile bottom dock): **Overview**, **Brief**, **World**, **Strategy**, **Watch**, **Graph**. Browser tab favicon is the header 🌅 mark (`favicon.svg`). Refresh re-runs the offline pipeline; **Live sources** tries RSS.

### Not the seasonal planner

| App | Path | Port |
|-----|------|------|
| **Horizon Macro** (this package) | `research/horizon/` | **8795** |
| Seasonal plan (Ikigai themes) | `horizon/` | **8791** |

Orchestrator lists them separately as **Horizon Macro** vs **Seasonal plan**.

## CLI pipeline

```bash
# Offline (fixtures; CI-safe)
python3 research/horizon/run_horizon.py --offline

# Prefer live RSS + fixtures
python3 research/horizon/run_horizon.py

# Re-link strategy only
python3 research/horizon/run_horizon.py --link-only --offline
```

## Test

```bash
python3 -m unittest discover -s research/horizon/tests -v
```

## Outputs

- `data/world_state_latest.json` + `data/history/world_state_<id>.json`
- `data/briefs/brief_latest.{json,md}` + versioned copies

## Strategy sources

Read-only from workspace:

- `strategy/bets.md`
- `strategy/intent.json`
- `strategy/today.md`
- `investment/positions.md`
