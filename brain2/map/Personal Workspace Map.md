# Personal Workspace Map

How B2 sits relative to the on-disk personal command system.

## Canonical roots

| Path | Role |
|------|------|
| `~/B2` → `~/personal-workspace/brain2` | **This vault** — global knowledge (B2) |
| `~/personal-workspace/b2-ux` | B2 web UX + Ask Grok server |
| `~/personal-workspace` | Dashboards, strategy MD, ops data |
| `~/.grok/sessions` | Grok session transcripts (not git) |

## Dashboards (Orchestra + subordinates)

| Area | Port | Launch idea |
|------|------|-------------|
| **Orchestra** (top-level) | 8790 | `python3 launch.py` |
| financial-command | 8000 | FCC / liquidity UI |
| projects-dashboard | 8765 | workflow / pre-reboot |
| holistic | 8770 | time allocator |
| iot | 8780 | Wiz lights |
| resistance-dashboard | 8787 | fitness coach UI |
| **B2 web UX** | **8792** | `b2/start.sh` |

Orchestra coordinates; it does not replace subordinate UIs. B2 is a **knowledge layer**, not a new command center.

## Domain folders (execution)

- `strategy/`, `initiatives/` — planning source MD (feeds B2 seeds)
- `treasury/`, `financial-command/`, `investment/` — finance execution
- `fitness/`, `resistance-dashboard/` — health & lifts
- `holistic/` — time allocation
- `iot/` — home automation
- `projects-dashboard/`, `ops/` — workflow metadata
- `orchestra/` — multi-domain synthesis UI

## Git / worktrees

Domain work lands on `work/<area>` branches with optional worktrees under `~/personal-workspace-worktrees/`. B2 seed content lives on the main repo; prefer not to fork vault notes across worktrees.

## Link graph

- Hub: [[00 Home - B2 Hub]]
- Domains: [[Strategy & Bets]] · [[Finance & Investment]] · [[Fitness & Health]] · [[Agents & Tooling]] · [[Home & IoT]] · [[Workflow & Projects]]
- Usage: [[HOWTO - Using B2]]
