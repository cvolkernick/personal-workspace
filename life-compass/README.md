# Life Compass v1

Event-driven personal attention system (#761). Linked to the **Life compass** goal (`goal_bf47e52a74b7`, slug `life-compass`).

Two halves:

1. **Pings (push)** — state transitions on watched items, not a clock. Silent when everything is attended.
2. **Dashboard (pull)** — private page of the one thing, goal chips, today's ops, radar, and the full watchlist.

## Run

```bash
python3 life-compass/server.py                  # http://127.0.0.1:8793/  (private default)
python3 life-compass/cli.py sweep
python3 life-compass/cli.py one-thing
python3 life-compass/cli.py missing
python3 life-compass/cli.py pings               # empty = stay silent in chat
```

Default bind is **127.0.0.1**. No CORS, no share link, `robots.txt` disallows `/`. Financial and health data must not be public.

Pi (when ordered, path-scoped): `bash deploy/install_remote.sh prism-agent@HOST --only life-compass`

This TLD is **not** on the FCC `workspace-sync` live root (`work/treasury`). Do not assume Pi has it after a master merge.

## V1 triggers

Evaluated each sweep, or marked **wiring planned** with the named dependency:

| Trigger | Source |
|---------|--------|
| No resistance session in 3+ days | FitDash — wiring planned |
| Weekly volume declining two weeks | FitDash — wiring planned |
| Weight stalled 2+ weeks into the cut | FitDash — wiring planned |
| Calories over target 3+ days in a week | FitDash — wiring planned |
| Bill due within 2 days, unscheduled | YNAB scheduled_transactions |
| Category overspent / underfunded vs Plan | YNAB month categories |
| No behind-expense payoff progress 14d | YNAB + local history |
| Turo pickup/return today unprepped | Google Calendar (`hatch_gws_cli`) |
| Invoice-ready unassigned | Turso / #747 — wiring planned |
| Radar (max 3/day) | GitHub + existing instruments |

## Nag control

Flag once on crossing into under-attended/unattended → 24h cooldown → one **still open** if unresolved → then quiet until resolved. Attended days produce zero pings.

Weekly constraint (issue comment): one bottleneck per ISO week, heuristic (highest-severity open item). UNKNOWN is valid. Quiet weeks stay quiet.

Attention/radar state: `life-compass/data/state.json` (gitignored, mode 600).
