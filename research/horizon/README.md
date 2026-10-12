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
carries the same tags in its head.

#### Own origin (separate app from FCC)

FCC's manifest and root worker use scope `/` on the FCC origin, so while
Horizon lives under `/horizon/` there a browser treats it as part of FCC and
only offers "Open in FCC". Horizon therefore gets its own tailnet origin via
Tailscale Serve on its own HTTPS port, served at the root. `8443` is taken
(Funnel → :8799), so Horizon uses `10000`:

```bash
sudo tailscale serve --bg --yes --https=10000 http://127.0.0.1:8795
# → https://<tailnet-magicdns-host>:10000/
```

No base-path setting is needed: the server is root-native, the manifest's
relative `start_url`/`scope` resolve to `/`, and `pwa.js` registers `sw.js`
with scope `/`. Then point FCC's `/horizon/*` at it with a
`financial-command.service` drop-in and restart FCC:

```ini
[Service]
Environment=HORIZON_PUBLIC_ORIGIN=https://<tailnet-magicdns-host>:10000
```

With `HORIZON_PUBLIC_ORIGIN` set, FCC 302-redirects GET/HEAD `/horizon/<path>?<query>`
to `<origin>/<path>?<query>` (so `/brief/*`, published `/horizon/daily-brief/...`
links and FCC's Horizon nav keep working). `/horizon/api/*` and non-GET calls
still proxy, and `/horizon/sw.js` serves a small worker that clears
`horizon-shell-*` and unregisters the old `/horizon/` registration. Unset, FCC
keeps the same-origin proxy. Install from the own-origin URL (iPhone: Safari →
Share → Add to Home Screen).

### Site nav

Every HTML page (front page, `/daily-brief`, the archive, edition permalinks,
`/model`) gets one sticky top nav: Home, Daily Brief, Archive, Model. Links are
relative: behind FCC's lens (`X-Forwarded-Prefix`, `<base href="/horizon/">`)
they are base-relative; on the own origin they climb from the page directory.
Additive only; the brief keeps its "Daily Brief, not Horizon model output" label.

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
