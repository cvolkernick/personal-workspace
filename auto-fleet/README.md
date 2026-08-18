# Auto Fleet

Internal **fleet management** dashboard for four owned units: vehicle / DIMO
state, Turo bookings, then notes & costs. Not a financial interface. Not FCC.

**Not TREAD.** Do not fold this into `safewheels-website`, the host-platform
tree, or `financial-command/`.

Port **8796**. Pi systemd unit is slice C — this README is Mac-dev only.

**Turo payout destination:** **X Money**. Payout mail is a cash-landed
signal, not a booking record.

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
| `turo_inbox.py` | Local JSON / maildir / Gmail-dump parser |
| `turo_gmail.py` | Write `~/.config/auto-fleet/turo_inbox.json` from a JSON dump |
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
| **Turo** | Parses a **local** JSON fixture / maildir / `~/.config/auto-fleet/turo_inbox.json`. Default fixture is empty. A 15m agent poll writes that dump from Gmail (`after:2026/08/18`, Turo senders only). Historical / `label:Turo` 2024 mail is dropped. The server does not call Gmail. Payout dest is **X Money**. |
| **Costs / notes** | Reads `tabs.Fleet` (`role: fleet_ops`) from this checkout's `treasury/snapshots/expenses_latest.json`, or — if that snapshot has no Fleet tab — the treasury worktree (`~/personal-workspace-worktrees/treasury/.../expenses_latest.json`). Override with `--expenses` / `AUTO_FLEET_EXPENSES`. Unit cards never use `summary.combined_monthly`. Missing Fleet tab → `stale: true` and roster + `notes.json`. |
| **Lien-holders** | No scrape. `notes.json` is a dated portal snapshot. Principal / PTP / payoff-quote fields are **not** live. |

## Honest empty states

- No Turo bookings → inbox status explains empty vs parse error and names X Money as payout dest. The UI does not invent trips.
- No Fleet tab → costs `stale: true`, no invented payoffs.
- No DIMO env → `unconfigured`, odometer/range null.

## Turo Gmail dump (forward-only, 15m)

Host mail forwards into `cvolkern@gmail.com` starting **2026-08-18**. Poll
every 15 minutes. Do **not** ingest historical Turo.

```bash
python3 auto-fleet/turo_gmail.py --from-json /path/to/messages.json
python3 auto-fleet/turo_gmail.py --from-json - < messages.json
```

Writes `~/.config/auto-fleet/turo_inbox.json` (mode 600). Override with
`AUTO_FLEET_TURO_INBOX` or `--turo-inbox`.

**Gmail query:** `after:2026/08/18 from:(turo.com OR mail.turo.com OR transactional.turo.com)`

Current-host subject hint: `Mike's vehicle` (same shape as the old
`(Jessica's vehicle) — …` mail). Jessica / Kia / Spark stay out.

Parser cutoff: `AUTO_FLEET_TURO_SINCE` (default `2026-08-18T02:00:00+00:00`;
`off` disables, tests only).

## Out of this slice

- Pi `auto-fleet.service` / `deploy/install_remote.sh --only auto-fleet`
- Live DIMO after Chris creates a Developer License and shares vehicles
- Lien-holder APIs
