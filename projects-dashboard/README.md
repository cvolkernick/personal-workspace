# Projects Dashboard

Visualize active local git projects: path, remotes, current branch, dirty/clean, ahead/behind.

## Launch

```bash
# From personal-workspace root:
python3 projects-dashboard/server.py

# Or double-click:
# projects-dashboard/start.command
```

Opens http://127.0.0.1:8765/

- **UI** — project cards with branch, remote URLs, sync status
- **API** — `GET /api/projects` JSON payload

```bash
# Headless / agent:
python3 projects-dashboard/server.py --port 8765 --no-browser
curl -s http://127.0.0.1:8765/api/projects | python3 -m json.tool
```

## Collectors (no UI)

```bash
python3 projects-dashboard/collectors.py
python3 -m unittest discover -s projects-dashboard/tests -v
```

Default scan roots: `personal-workspace`, `~/tab-out`, `~/clawd`, `~/AwesomeProject`, `~/PycharmProjects/HNTpayments`.
