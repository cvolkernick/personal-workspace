# personal-workspace

Personal tracking, data, and planning workspace for cvolkernick.

## How to Open the Orchestra (Recommended)

**Easiest:** Double-click **`open-command-center.command`** right here in this folder.

It starts the **Orchestra** top-level dashboard — the single interface that ties together strategy, workflow, finance, fitness, time-allocation, and home IoT, and surfaces overlaps, synergies, and a coordinated action plan.

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
| iot (Wiz lights) | 8780 | `python3 iot/server.py` |
| resistance-dashboard | 8787 | `python3 resistance-dashboard/server.py` |

### Pi backends 24/7 (open in browser — no local server)

**Pi hosts backends 24/7** via systemd (`deploy/install_remote.sh` → bind `0.0.0.0`, restart always).  
**Double-click / open launchers open the Pi URL** (not localhost). Default host: `192.168.100.98` (`deploy/endpoints.json`; override with `PI_HOST`).  
**Pi auto-pulls `origin/master` every 5 minutes** (`workspace-sync.timer`) and restarts units when code changes.  
**Off-network:** Tailscale (or equivalent) + `PI_HOST=<mesh>` — not public port-forward.

```bash
# On Pi (once): install all six always-on backends + auto-sync
bash deploy/install_remote.sh prism-agent@192.168.100.98

# On Mac — open always-on UIs (no server process):
open-command-center.command          # Orchestra :8790
# or:
bash deploy/open_dashboard.sh orchestra
bash deploy/open_dashboard.sh iot
# Off-LAN:
PI_HOST=100.x.y.z bash deploy/open_dashboard.sh orchestra
```

| Service | Always-on URL (LAN) |
|---------|---------------------|
| Orchestra | http://192.168.100.98:8790/ |
| financial-command | http://192.168.100.98:8000/financial-command/index.html |
| projects-dashboard | http://192.168.100.98:8765/ |
| holistic | http://192.168.100.98:8770/ |
| iot | http://192.168.100.98:8780/ |
| resistance-dashboard | http://192.168.100.98:8787/ |

Full guide: [`deploy/README.md`](deploy/README.md). Helpers: `dashboard_endpoints.py`, `remote_backend.py`.

## Top-level directories (TLDs)

Grouped by domain. Git work branches follow the same groups (see `Agents.md`).

| Domain | Directories | Work branch |
|--------|-------------|-------------|
| **Orchestra** | `orchestra/` | `work/orchestra` |
| **Finance** | `treasury/`, `financial-command/`, `investment/`, `research/` | `work/treasury` |
| **Workflow** | `projects-dashboard/`, `ops/` | `work/projects-dashboard` |
| **Fitness** | `resistance-dashboard/`, `fitness/` | `work/resistance-dashboard` |
| **Time** | `holistic/` | `work/holistic` |
| **IoT** | `iot/` | `work/iot` |
| **Planning** | `strategy/`, `initiatives/` | meta (not a work area card) |

### What each folder is
- **orchestra/** — Top-level Orchestra UI + collectors (synergies, priorities, multi-domain status).
- **treasury/** — Dual-venue liquidity policy (Coinbase + Robinhood), adapters, `run_treasury.py`.
- **financial-command/** — Financial Command Center UI (stress, bills, agent vs human actions).
- **investment/** — Positions + `treasury-action-items.md`.
- **research/** — Coinbase automation feasibility notes.
- **projects-dashboard/** — Workflow Management / pre-reboot readiness + Grok sessions.
- **ops/** — Session index + backlog (workflow metadata).
- **holistic/** — Time allocator dashboard and rolling plan.
- **fitness/** — PPL workouts, nutrition, Fitbit data.
- **resistance-dashboard/** — Fitness/health coaching UI over fitness data.
- **iot/** — Wiz smart bulbs, groups, schedules, Pi deploy.
- **strategy/** / **initiatives/** — Planning content (bets, daily focus, initiative briefs).

### Git
```bash
python3 projects-dashboard/git_workflow.py start treasury   # finance / FCC
python3 projects-dashboard/git_workflow.py start iot
python3 projects-dashboard/git_workflow.py sync             # commit + push
```

### Treasury refresh
```bash
python3 treasury/run_treasury.py
```

## Editing workflow
1. Check out the correct `work/<area>` for the domain you are changing.
2. Edit files; refresh Orchestra or the subordinate dashboard.
3. `git_workflow.py sync` (or Protect & push) so work is on the remote.

See `orchestra/README.md` and `Agents.md` for details.
