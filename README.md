# personal-workspace

Personal tracking, data, and planning workspace for cvolkernick.

## How to Open the Orchestra (Recommended)

**Easiest:** Double-click **`open-command-center.command`** right here in this folder.

It starts the **Orchestra** top-level dashboard — the single interface that ties together strategy, workflow, finance, fitness, and time-allocation, and surfaces overlaps, synergies, and a coordinated action plan.

### Manual alternative
```bash
python3 launch.py
# or
python3 orchestra/server.py --port 8790
```
Then open: http://localhost:8790/

API: `/api/orchestra` · `/api/synergies` · `/api/priorities` · `/api/health`

> Orchestra aggregates on-disk sources (and optional live port probes). It does not replace subordinate UIs — it coordinates them and deep-links out.

## Subordinate dashboards

| Area | Port | Launch |
|------|------|--------|
| **Orchestra** (top-level) | **8790** | `python3 launch.py` |
| financial-command | 8000 | `python3 financial-command/server.py` |
| projects-dashboard | 8765 | `python3 projects-dashboard/server.py` |
| holistic (time allocator) | 8770 | `python3 holistic/server.py` |
| resistance-dashboard | 8787 | `python3 resistance-dashboard/server.py` |
| iot (Wiz lights) | 8780 | `python3 iot/server.py` |

## Contents
- **orchestra/** — **Top-level Orchestra** UI + collectors (synergies, priorities, multi-domain status).
- **treasury/** — Dual-venue liquidity policy (Coinbase liquid + Robinhood BP/DCA), adapters, `run_treasury.py`.
- **financial-command/** — Financial Command Center UI (stress, buckets, agent vs human actions).
- **projects-dashboard/** — Workflow Management / pre-reboot readiness for monorepo + Grok sessions.
- **holistic/** — Time allocator dashboard and rolling plan.
- **investment/** — Positions + `treasury-action-items.md`.
- **research/** — Coinbase automation feasibility matrix.
- **strategy/** — High-conviction bets and daily micro plan.
- **initiatives/** — Structured projects with next_action.
- **fitness/** — PPL workouts, nutrition, Fitbit.
- **resistance-dashboard/** — Fitness/health coaching UI over fitness data.
- **iot/** — Wiz smart bulbs CLI + local control dashboard (port 8780).

### Treasury refresh
```bash
python3 treasury/run_treasury.py
# optional: update RH snapshot via agent MCP get_portfolio → treasury/snapshots/robinhood_latest.json
```

## Editing Workflow
1. Edit the `.md` files (or CSVs/JSON) in your editor of choice.
2. Refresh Orchestra (or a subordinate dashboard) in the browser.
3. Use Grok in this TUI to help log sessions, update snapshots, or improve dashboards.

Everything stays versioned and simple.

See `orchestra/README.md` for Orchestra details.

*Last updated as part of building the top-level Orchestra command center.*
