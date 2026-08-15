# Holistic — Time allocator (MVP)

Local tool to maintain a **core list of tasks/goals**, add/remove them, and **allocate a daily minute budget by priority**.

This is the foundation for a later agentic coaching / day-loop system. MVP does **not** call LLMs or calendars.

## Dashboard (recommended)

```bash
python3 holistic/server.py
# or: bash holistic/start.command
```

Opens **http://127.0.0.1:8770/** — list, add/remove, edit priority/minutes, allocate day budget, load starter list.

```bash
python3 holistic/server.py --port 8770 --no-browser
python3 holistic/server.py --data /path/to/tasks.json
```

## CLI

From the personal-workspace repo root:

```bash
# Load starter core list
python3 holistic/run_time_allocator.py seed

# List (priority + minutes)
python3 holistic/run_time_allocator.py list

# Add / remove
python3 holistic/run_time_allocator.py add "Ship time allocator" --kind task --priority 6
python3 holistic/run_time_allocator.py remove seed-admin

# Distribute an 8h day by priority weight
python3 holistic/run_time_allocator.py allocate 480

# Tweak one item
python3 holistic/run_time_allocator.py set seed-fitness --priority 5 --minutes 45
```

Module form:

```bash
python3 -m holistic.time_allocator list
```

Default data file: `holistic/data/tasks.json`  
Override: `--data /path/to.json` or env `TIME_ALLOCATOR_DATA`.

## Priority model

- Higher `priority` integer ⇒ more important ⇒ larger share of `allocate TOTAL`.
- `allocate` always makes sum(minutes) equal the total (remainder to highest priority).

## Tests

```bash
python3 -m unittest discover -s holistic/tests -v
```

## Rolling 24h model

The plan is always a **rolling 24-hour window** from “now”:

1. **Reserve** sleep (default 8h / 480m) from ongoing sleep KPI  
2. **Fixed daily** targets (e.g. Walk Duchess **30–60 min**, plan default 45m)  
3. **Weekly frequency** sessions if behind min days (e.g. workout 3–5×/week)  
4. **Ad-hoc** tasks/goals by priority (your errands, deep work, etc.)  
5. **Fill remainder** of active time (Lyft driving)

Dashboard features:
- **NOW / NEXT / THEN** strip (`GET /api/now`) — clock-following secretary over the filed plan  
- **Next actions** ranked by urgency / priority  
- **Pie chart** of the rolling 24h blocks  
- **Sync sleep** via Google Health OAuth (same credentials as resistance-dashboard) or fallback `fitness/data/health-metrics.json`

Log KPI progress from the dashboard (or Sync). Rebuild plan with **Rebuild 24h plan**.

## Next iteration

1. Rule-based **recommend next** action from the live plan.  
2. Mid-day **coach loop** (progress notes + re-plan).  
3. Optional blocked windows / calendar.  
4. Agent hooks / skills reading the same JSON.  
5. Split Duchess into two walk blocks; Lyft hour logging.

See seed plan: `ops/backlog/seeds/time-allocator-bfdc9db1.md`.
