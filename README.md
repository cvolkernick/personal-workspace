# personal-workspace

Personal tracking, data, and planning workspace for cvolkernick.

## How to Open the Command Center (Recommended)

**Easiest:** Double-click **`open-command-center.command`** right here in this folder.

It will start a local server and automatically open the beautiful visual dashboard in your browser.

### Manual alternative
```bash
python3 -m http.server 8000
```
Then open: http://localhost:8000/financial-command/index.html

> The financial command center is the visual layer for dual-venue liquidity (Coinbase + Robinhood).

All real content lives in clean, git-tracked Markdown + data + images.

## Contents
- **treasury/** — Dual-venue liquidity policy (Coinbase liquid + Robinhood BP/DCA), adapters, `run_treasury.py`.
- **financial-command/** — **Financial Command Center** UI (stress, buckets, agent vs human actions). Distinct from `resistance-dashboard/`.
- **projects-dashboard/** — Graceful-exit / pre-reboot readiness for monorepo + Grok sessions. `python3 projects-dashboard/server.py`
- **investment/** — Positions + `treasury-action-items.md` (loan protection, autopay, bridge handoff).
- **research/** — Coinbase automation feasibility matrix.
- **strategy/** — High-conviction bets and daily micro plan.
- **initiatives/** — Structured projects with next_action.
- **fitness/** — PPL workouts, nutrition, Fitbit.
- **iot/** — Wiz smart bulbs.

### Treasury refresh
```bash
python3 treasury/run_treasury.py
# optional: update RH snapshot via agent MCP get_portfolio → treasury/snapshots/robinhood_latest.json
```


## Editing Workflow
1. Edit the `.md` files (or CSVs/JSON) in your editor of choice.
2. Refresh the financial command center in the browser.
3. Use Grok in this TUI to help log sessions, update snapshots, or improve `financial-command/` HTML itself.

Everything stays versioned and simple.

See `financial-command/README.md` for launch details.

*Last updated as part of building the visual command center.*