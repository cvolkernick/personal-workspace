# personal-workspace

Personal tracking, data, and planning workspace for cvolkernick.

## How to Open the Command Center (Recommended)

**Easiest:** Double-click **`open-command-center.command`** right here in this folder.

It will start a local server and automatically open the beautiful visual dashboard in your browser.

### Manual alternative
```bash
python3 -m http.server 8000
```
Then open: http://localhost:8000/dashboard/index.html

> The dashboard gives you a much nicer visual overview than reading the raw Markdown files or GitHub.

All real content lives in clean, git-tracked Markdown + data + images. The dashboard is just the friendly visual layer on top.

## Contents
- **treasury/** — Dual-venue liquidity policy (Coinbase liquid + Robinhood BP/DCA), adapters, `run_treasury.py`.
- **dashboard/** — Financial command center UI (stress, buckets, agent vs human actions).
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
2. Refresh the dashboard in the browser.
3. Use Grok in this TUI to help log sessions, update snapshots, add initiatives, or improve the dashboard HTML itself.

Everything stays versioned and simple.

See `dashboard/README.md` for launch details and philosophy.

*Last updated as part of building the visual command center.*