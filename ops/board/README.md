# Board day constraints (P3-W)

Live freeze packet for Orchestra workflow gates / Next 3 work candidates.

| Path | Role |
|------|------|
| **`ops/board/day_constraints.json`** | Snapshot Orchestra **reads** (`collect_workflow`) |
| **Buzz Board Project #1** | Write SoT (Status columns) |
| **`ops/sprint/current.json`** | Optional owner/size overlays + agent roster |

**Not SoT:** `ops/backlog`, dashboard sessions alone.

## Fields (frozen)

`ready_count`, `ready_top` (≤3), `in_progress`, `pending_review_count`, `blocked`, `wip_overload`, `free_agent_count`, `pipeline_pressure`, `as_of`, `fresh_for_hours=4`, `stale`, `fetch_ok`, `summary`, `confidence`, `deep_link`.

Rules:

- Fetch fail **or** age **>4h** → `stale=true`; fail → `fetch_ok=false` and **no invented Ready 0 / free agents**
- `wip_overload` when any `primary_owner` has **>1** In Progress (Pending Review does **not** busy)
- Missing owner on IP → `blocked` (process), not overload
- `pipeline_pressure`: `dry` = Ready0 + free≥1; `stuck` = Ready>0 + free0 + PR0 + IP busy; else `ok`

## Refresh path (keep age &lt;4h)

### Explicit script (primary)

```bash
# From monorepo root; requires GITHUB_TOKEN or GH_TOKEN (scopes: project, repo)
./scripts/buzz-board day-export
# → writes ops/board/day_constraints.json
./scripts/buzz-board day-export --dry-run   # print JSON only
./scripts/buzz-board day-export --json      # write + print path summary JSON
```

### Cron (recommended)

Every 15–30 minutes on Mac or Pi operator host:

```cron
*/15 * * * * cd /path/to/personal-workspace && ./scripts/buzz-board day-export >/tmp/board-day-export.log 2>&1
```

### Dashboard boot (optional side path)

Workflow dashboard (`projects-dashboard`, `:8765`) may call the same pure builder after a successful live Board fetch; the CLI remains the durable path when the dashboard is not running.

## Tests

```bash
python3 -m unittest discover -s ops/board/tests -v
python3 -m unittest discover -s orchestra/tests -v
```

## Honesty

Orchestra **never** writes Board Status or dual-writes cards. This package only refreshes the read-only day packet.
