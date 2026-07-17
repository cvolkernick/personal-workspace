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

## Next iteration

1. Rule-based **recommend next** action from the list.
2. Mid-day **coach loop** (progress notes + re-allocate).
3. Day budget defaults and optional blocked windows.
4. Stable read schema for agent sessions / skills.
5. Optional sync into `strategy/today.md` or the projects dashboard.

See seed plan: `ops/backlog/seeds/time-allocator-bfdc9db1.md`.
