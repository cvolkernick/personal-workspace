# Sprint ceremony state

Cadence-owned snapshot for the Workflow Management **Sprint** tab (`GET /api/sprint`).

## SoT rules (#56 / schema v2)

| Data | Source of truth | Who writes |
|------|-----------------|------------|
| Column membership (Parked → Done) | **Buzz Board** GitHub Project #1 | Cadence / Grok via `scripts/buzz-board` — **never** dashboard UI |
| Sprint goal, card size/owner overlays, not-this-sprint flags, agent roster, ceremony calendar | **`current.json`** (this dir) | **Cadence** |
| Free vs busy agents | **Computed** by dashboard from Board In Progress owners + `card_overlays` + `agents` roster | Dashboard read-only |
| ops/backlog auto-start queue | Separate system | Do **not** merge into Sprint cards |

## Files

- `current.json` — schema **v2** (see nest plan `PLANS/SPRINT_TAB_WORKFLOW_DASHBOARD.md`)
- This README

## Free-agent rule (playbook)

- **Agent WIP cap = 1** (`agent_wip_cap`)
- Free ⇔ zero cards in Board **In Progress** for that owner
- **Pending Review does not count as busy**
- Missing owner on an In Progress card → surface `agents.data_gap`; do not invent ownership

## Deprecated (v1)

Do not use ceremony `in_progress` / `ready` arrays or board-wide `wip_limit: 3` as primary column/WIP SoT when live Board is available.

## Nest plan

`~/.buzz/PLANS/SPRINT_TAB_WORKFLOW_DASHBOARD.md` · playbook `GUIDES/CADENCE_SCRUM_CEREMONIES.md`
