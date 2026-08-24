# Auto Fleet

Internal **fleet management** dashboard for five owned units. Everything is
organized **by car**: each card holds vehicle details, schedule (rentals +
ops dates), money (invoice-ready GT + locked lender/APR), and expandable
trip detail. Not a financial interface. Not FCC. Not a global bookings inbox.

**Not TREAD.** Do not fold this into `safewheels-website`, the host-platform
tree, or `financial-command/`.

Port **8796**. Prod is `auto-fleet.service` on `prism-gateway`
(`http://100.67.114.2:8796/` · `http://prism-gateway:8796/`). Mac is dev.

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
| GET | `/api/fleet` | Roster units + strips (intranet) |
| GET | `/api/agent/fleet` | Read-only Helm brief (token / loopback). No Tailscale required when a published snapshot is present |
| GET | `/api/turo-tasks` | Open Google Tasks on the **Turo** list |
| POST | `/api/turo-tasks/complete` | Checkbox write-back (`task_id`, `list_id`) |

Tests (full package):

```bash
python3 -m unittest discover -s auto-fleet/tests -v
```

## Layout

| Path | Purpose |
|------|---------|
| `server.py` / `index.html` | Dashboard + JSON API (car-centric cards) |
| `agent_fleet.py` / `service_auth.py` | Read-only Helm brief + service-token gate (#295) |
| `data/agent_fleet_latest.json` | Shipped empty/stale snapshot — no invented trips |
| `fleet.py` | Assemble `/api/fleet` |
| `car_cards.py` | Locked lender/APR, trip schedule, GT→car match |
| `glance.py` | Miles / SoC / stale / PTP presentation helpers |
| `static/fleet/` | Per-unit stills (`m3-2020`, `m3-2022`, `r1s-2023`, both Corollas). Not TREAD chrome |
| `dimo_client.py` | DIMO stub + optional live path |
| `turo_inbox.py` | Local JSON / maildir / Gmail-dump parser |
| `gtasks.py` / `turo_tasks.py` | Prism Google Tasks client + Turo list read/complete |
| `turo_gmail.py` | Write `~/.config/auto-fleet/turo_inbox.json` (`--fetch` or `--from-json`) |
| `turo_media.py` | Persist image MIME parts next to the dump (`turo_inbox_media/`) |
| `data/roster.json` | Five-unit seed |
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
# optional — Helm /api/agent/fleet (never commit):
AUTO_FLEET_SERVICE_TOKEN=
AUTO_FLEET_AGENT_SNAPSHOT=
```

Missing file / missing keys → DIMO `status: unconfigured`. The process does not crash.

Google Tasks (invoice-ready strip) uses the same prism files as FitDash-on-Pi:
`~/.config/google-tasks-mcp/{token,client_secret}.json`. Do **not** put
`GOOGLE_TASKS_*` on Vercel — Auto Fleet is intranet-only.

## What is stubbed

| Source | MVP behavior |
|--------|----------------|
| **DIMO** | Unconfigured until env + vehicle token ids exist. Live path mints a Developer JWT then a **Vehicle JWT** via `dimo-python-sdk`. Never send `DIMO_API_KEY` as a telemetry Bearer. |
| **Turo** | Parses a **local** JSON fixture / maildir / `~/.config/auto-fleet/turo_inbox.json`. Default fixture is empty. Prod writer is the Pi 15m timer (`auto-fleet-turo-writer.timer`) — not a Mac Grok/MCP poll. Historical / `label:Turo` 2024 mail is dropped. The server does not call Gmail. Payout dest is **X Money**. |
| **Costs / notes** | Reads `tabs.Fleet` (`role: fleet_ops`) from this checkout's `treasury/snapshots/expenses_latest.json`, or — if that snapshot has no Fleet tab — the treasury worktree (`~/personal-workspace-worktrees/treasury/.../expenses_latest.json`). Override with `--expenses` / `AUTO_FLEET_EXPENSES`. Unit cards never use `summary.combined_monthly`. Missing Fleet tab → `stale: true` and roster + `notes.json`. |
| **Lien-holders** | No scrape. `notes.json` is a dated portal snapshot. Principal / PTP / payoff-quote fields are **not** live. |

## Invoice-ready (Google Tasks)

Open items from the Google Tasks list named **Turo** nest under the matching
car card (year/make/model or reservation # — never guess which Corolla).
Find-or-create that list only — no extra lists, no Fleet-local task JSON.
Title and notes come from the GT item Helm files; this page does not invent
amounts, VINs, or trips. Checkbox completes the item in Google Tasks.
Unmatched items stay in a leftover **Unassigned invoice-ready** strip.
No open items → strip omitted (no empty-state theater). Missing creds →
honest error, not fake rows.

Auto Fleet is the standing surface for invoice-ready Turo items. Orchestra
may later show the same Google Task only when it is NOW/NEXT in that
window — do not add Orchestra chrome here. Chat ping is not this page.

## Honest empty states

- No Turo bookings → inbox status explains empty vs parse error and names X Money as payout dest. The UI does not invent trips.
- No Fleet tab → costs `stale: true`, no invented payoffs.
- No DIMO env → `unconfigured`, odometer/range null.

## Turo Gmail dump (forward-only, 15m)

Host mail forwards into `cvolkern@gmail.com` starting **2026-08-18**. The Pi
timer runs every 15 minutes. Do **not** ingest historical Turo.

```bash
python3 -m auto-fleet.turo_gmail --fetch
python3 auto-fleet/turo_gmail.py --from-json /path/to/messages.json
python3 auto-fleet/turo_gmail.py --from-json - < messages.json
```

Writes `~/.config/auto-fleet/turo_inbox.json` (mode 600). Image / MMS MIME
parts land in `~/.config/auto-fleet/turo_inbox_media/<message_id>/` (mode
600) and are listed on each message as `attachments[]` (`path`, `relpath`,
`mime`, `sha256`, `size`). Helm opens those files or
`GET /api/turo-inbox-media/<relpath>`. Text-only mail stays text-only.
“Contains photo(s)” without image parts is flagged `photos_missing` — no
invented bytes. Override dump path with `AUTO_FLEET_TURO_INBOX` or `--out`.

**Gmail query:** `after:2026/08/18 from:(turo.com OR mail.turo.com OR transactional.turo.com)`

`--fetch` uses `~/.config/auto-fleet/gmail-token.json` (gmail.readonly OAuth)
or `GMAIL_REFRESH_TOKEN` + `GMAIL_CLIENT_ID` + `GMAIL_CLIENT_SECRET` in
`~/.config/auto-fleet/env`. Missing creds write `source=gmail_unconfigured`
and zero messages — honest empty, not a crash.

Current-host subject hint: `Mike's vehicle` (same shape as the old
`(Jessica's vehicle) — …` mail). Jessica / Kia / Spark stay out.
Unit match uses the mail **body** year (`Toyota Corolla 2024` →
`corolla-2024`) plus reservation #. Yearless Corolla stays unmatched.
Mike host mail does not attach to Chris personal units. Bookings paint
on the car card; they are not Google Tasks.

Parser cutoff: `AUTO_FLEET_TURO_SINCE` (default `2026-08-18T02:00:00+00:00`;
`off` disables, tests only).

## Helm read-only brief (#295)

Same class of gate as FitDash `GET /api/agent/today` (#293). Seats do **not**
need Tailscale to read units + bookings paint + inbox status when a published
snapshot is on a shared path.

```bash
# Pi loopback (AUTO_FLEET_SERVICE_LOOPBACK=1, default)
curl -sS http://127.0.0.1:8796/api/agent/fleet

# Shared token — Bearer
curl -sS -H "Authorization: Bearer $AUTO_FLEET_SERVICE_TOKEN" \
  http://127.0.0.1:8796/api/agent/fleet

# Shared token — custom header
curl -sS -H "X-Auto-Fleet-Service-Token: $AUTO_FLEET_SERVICE_TOKEN" \
  http://127.0.0.1:8796/api/agent/fleet

# No Tailscale: read the published packet (prism writer writes this next to the dump)
python3 -m auto-fleet.agent_fleet --read
# or: AUTO_FLEET_AGENT_SNAPSHOT=/path/to/agent_fleet.json
```

The 15m writer (`auto-fleet-turo-writer.timer`) publishes
`agent_fleet.json` next to the inbox dump after `--fetch`. Override with
`AUTO_FLEET_AGENT_SNAPSHOT`. Cookie-less / token-less non-loopback is **401**.
`/api/fleet` and invoice-ready `/api/turo-tasks` stay on the intranet
dashboard. Payload is dump/email-ingest derived — no invented trips. Missing
writer → honest empty / stale. No DIMO / Gmail / GT keys, no lender accounts,
no VIN, no absolute `~/.config` paths.

## Pi install (Slice C)

```bash
bash deploy/install_remote.sh prism-agent@192.168.100.98 --only auto-fleet
```

Installs `auto-fleet.service` (`0.0.0.0:8796`, `Restart=always`) and
`auto-fleet-turo-writer.timer` (15m `--fetch`). Path map prefix `auto-fleet/`
restarts only `auto-fleet.service` on merge sync.

## Out of this slice

- Live DIMO after Chris creates a Developer License and shares vehicles
- Lien-holder APIs
- Gmail OAuth consent (drop the token on the Pi; Mac MCP is not the prod writer)
