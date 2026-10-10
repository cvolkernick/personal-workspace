# Horizon

Global Macro & Geopolitical Intelligence System — world-state model + daily synthesis.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for design.

## Visual dashboard (primary UX)

```bash
python3 research/horizon/server.py --bootstrap
# → http://127.0.0.1:8795/
```

Or double-click `research/horizon/start.command`.

Dashboard tabs: **Overview** (domain heat + top signals), **Executive brief**, **World state**, **My strategy**, **Watchlist**, **Connections**. Refresh re-runs the offline pipeline; **Live sources** tries RSS.

### Install as an app (Add to Home Screen)

Horizon ships its own install shell, same pattern as FCC but Horizon-owned
files (FCC's `pwa.js` / `sw.js` are not touched):

| File | Served at | Notes |
|------|-----------|-------|
| `manifest.webmanifest` | `/manifest.webmanifest` | name/short_name "Horizon", standalone, `#060a10`; relative `start_url`/`scope` so behind FCC's lens they resolve to `/horizon/` |
| `pwa_icons.json` | `/icon-192.png`, `/icon-512.png`, `/icon-maskable-{192,512}.png`, `/apple-touch-icon.png`, `/favicon-32.png` | base64 PNGs; regenerate with `make_pwa_icons.py` (Pillow) |
| `sw.js` | `/sw.js` | app shell only; HTML and `api/`, `data/`, `daily-brief/api/`, `*.json` always go to the network |
| `pwa.js` | `/pwa.js` | registers `sw.js` with scope = the manifest's directory (`/horizon/` behind the lens); HTTPS or loopback only |

`server.py` adds the head tags (manifest, theme-color, apple-touch-icon, apple
meta) to the front page and every `/daily-brief` page; `index.html` (`/model`)
carries the same tags in its head. Install from the HTTPS lens URL at
`/horizon/` (iPhone: Safari → Share → Add to Home Screen).

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
