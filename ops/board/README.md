# Board day constraints (P3-W)

Live freeze packet for Orchestra workflow gates / Next 3 work candidates.

| Path | Role |
|------|------|
| **`ops/board/day_constraints.json`** | Snapshot Orchestra **reads** (`collect_workflow`) |
| **Buzz Board Project #1** | Write SoT (Status columns) |
| **`ops/sprint/current.json`** | Optional owner/size overlays + agent roster |

**Not SoT:** `ops/backlog`, dashboard sessions alone.

## Fields (frozen)

`ready_count` (eng-only), `process_ready_count`, `ready_top` (≤3, eng-only), `in_progress`, `pending_review_count`, `blocked`, `wip_overload`, `free_agent_count`, `pipeline_pressure`, `as_of`, `fresh_for_hours=4`, `stale`, `fetch_ok`, `summary`, `confidence`, `deep_link`.

Rules:

- Fetch fail **or** age **>4h** → `stale=true`; fail → `fetch_ok=false` and **no invented Ready 0 / free agents**
- Ready + label `process` → `process_ready_count` only (not `ready_count` / `ready_top`)
- Ready + label `human-only` → exclude from both Ready counts
- `wip_overload` when any `primary_owner` has **>1** In Progress (Pending Review does **not** busy)
- Missing owner on IP → `blocked` (process), not overload
- `pipeline_pressure`: `dry` = eng Ready0 + free≥1; `stuck` = eng Ready>0 + free0 + PR0 + IP busy; else `ok`. Process-only Ready is **dry**.

## Refresh path (keep age &lt;4h)

### Explicit script (primary)

```bash
# From monorepo root; requires GITHUB_TOKEN or GH_TOKEN (scopes: project, repo)
./scripts/buzz-board day-export
# → writes ops/board/day_constraints.json
./scripts/buzz-board day-export --dry-run   # print JSON only
./scripts/buzz-board day-export --json      # write + print path summary JSON
```

### Continuous export (recommended — Pi / always-on)

Unified script (Board export + FitDash poke):

```bash
bash scripts/export-day-packets.sh
# --board-only / --fit-only / --json supported
```

systemd user timer (installed by `deploy/install_remote.sh`):

| Unit | Cadence |
|------|---------|
| `board-day-export.timer` | every **15m** (+2m after boot) |
| `board-day-export.service` | oneshot → `scripts/export-day-packets.sh` |

```bash
systemctl --user enable --now board-day-export.timer
systemctl --user start board-day-export.service   # run once now
journalctl --user -u board-day-export.service -n 40 --no-pager
```

Requires `GITHUB_TOKEN`/`GH_TOKEN` in `~/.config/workflow-scheduler.env` on Pi
(or `gh auth` on Mac). Fit poke needs FitDash on `FITDASH_URL` (default `:8787`).

### Cron (fallback)

Every 15–30 minutes on Mac or Pi operator host:

```cron
*/15 * * * * cd /path/to/personal-workspace && bash scripts/export-day-packets.sh >/tmp/board-day-export.log 2>&1
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
