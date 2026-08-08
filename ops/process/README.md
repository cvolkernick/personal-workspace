# Process schedule snapshot

Cadence/Forge-maintained snapshot for the Workflow Management **Schedule** tab
(`GET /api/process`, `?tab=schedule`).

## SoT rules (#61)

| Data | Source of truth | Who writes |
|------|-----------------|------------|
| Live ceremony workflows | **Buzz relay** via `buzz workflows list --channel` on #workflow + #standup | Relay / workflow owners |
| Offline / Pi fallback | **`workflows_snapshot.json`** (this dir) | Cadence or Forge after ceremony changes |
| Process flow edges (how ceremonies chain) | Code constants in `projects-dashboard/process_schedule.py` aligned with `GUIDES/CADENCE_SCRUM_CEREMONIES.md` | Forge (docs-locked) |
| ops/backlog auto-start | Separate Plan-tab system | Do **not** merge into Schedule rows |

## Files

- `workflows_snapshot.json` — schema **v1** list of workflows (name, cron, channel, kicks, status)
- This README

## Status

- **active** — continuous-flow ceremonies (status, replenish, deep groom, eng-gate, harvest)
- **inert** — `zzz-*`, retired, probe workflows (hidden by default in UI; `?inert=1` to show)

## Refresh snapshot

```bash
# From a machine with buzz auth:
buzz workflows list --channel db0e8f97-0c81-4976-b299-1c460b87134e
buzz workflows list --channel 1bafc96c-299a-48ef-aff6-4b6190e643e4
# Then re-export into workflows_snapshot.json (or re-run dashboard live path).
```

Prefer live relay when `buzz` is available to the dashboard process. Snapshot exists so Pi/Mac still render if buzz is offline.

## Non-goals (v1)

- Editing workflows in the UI
- Full run history (`buzz workflows runs` may be empty)
- Replacing Cadence ceremony ownership

## Related

- Playbook: nest `GUIDES/CADENCE_SCRUM_CEREMONIES.md` (or monorepo copy if present)
- Sibling: Sprint tab `ops/sprint/` (#56)
