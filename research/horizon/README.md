# Horizon

Global Macro & Geopolitical Intelligence System — world-state model + daily synthesis.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for design.

## Visual dashboard (primary UX)

```bash
python3 research/horizon/server.py --bootstrap
# → http://127.0.0.1:8795/
```

The server binds `127.0.0.1` (`HORIZON_BIND_HOST`, default). Tailscale Serve and
FCC's `/horizon/*` proxy both dial that address. `HORIZON_BIND_HOST=0.0.0.0`
listens on all interfaces again (previous behavior). An explicit `--host` wins
over the env var.

On prism, put the rollback or the default in a user drop-in and restart the unit
(workspace-sync does not restart `horizon-dashboard.service`):

```bash
mkdir -p ~/.config/systemd/user/horizon-dashboard.service.d
printf '[Service]\nEnvironment=HORIZON_BIND_HOST=127.0.0.1\n' \
  > ~/.config/systemd/user/horizon-dashboard.service.d/bind.conf
# Rollback: Environment=HORIZON_BIND_HOST=0.0.0.0
# If ExecStart still passes --host 0.0.0.0, remove that flag or the env is ignored.
systemctl --user daemon-reload
systemctl --user restart horizon-dashboard.service
```

Or double-click `research/horizon/start.command`.

Dashboard tabs: **Overview** (domain heat + top signals), **Executive brief**, **World state**, **My strategy**, **Watchlist**, **Connections**. Refresh re-runs the offline pipeline; **Live sources** tries RSS.

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
