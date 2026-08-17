# Auto Fleet

Internal ops dashboard for four owned units (DIMO + Turo email + Fleet-tab finance).

**Not TREAD.** Do not fold this into `safewheels-website` or the host-platform tree.

Port **8796**. Pi systemd unit is slice C — this README is Mac-dev only.

## Quick start (Mac)

```bash
# From monorepo root (prefer the auto-fleet worktree if it exists)
python3 auto-fleet/server.py
# or
python3 auto-fleet/server.py --host 127.0.0.1 --port 8796 --no-browser
```

Open http://127.0.0.1:8796/

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | `{ok, service: "auto-fleet", port}` |
| GET | `/api/fleet` | Four roster units + strips |

Tests (full package):

```bash
python3 -m unittest discover -s auto-fleet/tests -v
```

## Layout

| Path | Purpose |
|------|---------|
| `server.py` / `index.html` | Dashboard + JSON API |
| `fleet.py` | Assemble `/api/fleet` |
| `dimo_client.py` | DIMO stub + optional live path |
| `turo_inbox.py` | Local JSON / maildir parser |
| `data/roster.json` | Four-unit seed |
| `data/notes.json` | 2026-08-13 portal override (stale on purpose) |
| `data/turo_inbox.json` | Empty fixture — no invented trips |
| `tests/` | Unit + HTTP tests |

## Secrets

`~/.config/auto-fleet/env` (mode 600). **Never commit.**

```
DIMO_CLIENT_ID=
DIMO_DOMAIN=
DIMO_API_KEY=
# optional:
DIMO_PRIVATE_KEY=
DIMO_DEVELOPER_JWT=
DIMO_VEHICLE_TOKENS={"m3-2022": 123, "corolla-2022": 456}
# or per unit:
DIMO_TOKEN_M3_2022=
DIMO_TOKEN_COROLLA_2022=
```

Missing file / missing keys → DIMO `status: unconfigured`. The process does not crash.

## What is stubbed

| Source | MVP behavior |
|--------|----------------|
| **DIMO** | Unconfigured until env + vehicle token ids exist. Live path uses `dimo-python-sdk` if installed, else raw HTTP when `DIMO_DEVELOPER_JWT` is set. |
| **Turo** | Parses a **local** JSON fixture or maildir. Default fixture is empty. Live Gmail is out of process until Chris forwards the real host inbox. |
| **Finance** | Reads `tabs.Fleet` (`role: fleet_ops`) from this checkout's `treasury/snapshots/expenses_latest.json`, or — if that snapshot has no Fleet tab — the treasury worktree (`~/personal-workspace-worktrees/treasury/.../expenses_latest.json`). Override with `--expenses` / `AUTO_FLEET_EXPENSES`. Unit cards never use `summary.combined_monthly` (Slice A burn is still revising). Missing Fleet tab → `stale: true` and roster + `notes.json`. |
| **Lien-holders** | No scrape. `notes.json` is a dated portal snapshot. Principal / PTP / payoff-quote fields are **not** live. |

## Honest empty states

- No Turo bookings → inbox status explains empty vs parse error. The UI does not invent trips.
- No Fleet tab → finance `stale: true`, no invented payoffs.
- No DIMO env → `unconfigured`, odometer/range null.

## Out of this slice

- Pi `auto-fleet.service` / `deploy/install_remote.sh --only auto-fleet`
- Live DIMO after Chris creates a Developer License and shares vehicles
- Live Turo once a host-inbox label exists
- Lien-holder APIs
