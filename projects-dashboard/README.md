# Projects Dashboard

Visualize **Grok Build** projects: sessions matched to local git repos via edit hunks, plus path, remotes, branch, dirty/clean, ahead/behind.

## How matching works

1. Scan `~/.grok/sessions/<cwd>/<session-id>/` (parent sessions only by default).
2. Read `hunk_records.jsonl` file edits → map each path to its **git root**.
3. Group sessions under that project; attach live flags from `active_sessions.json`.
4. Collect git status (branch, remotes, dirty, ahead/behind) for each project.

Sessions with no project edits show under “Sessions without a mapped project”.

## Launch

```bash
# From personal-workspace root:
python3 projects-dashboard/server.py

# Or double-click:
# projects-dashboard/start.command
```

Opens http://127.0.0.1:8765/

| Endpoint | Description |
|----------|-------------|
| `GET /api/projects` | Default: Grok session match + git status |
| `GET /api/projects?mode=all` | Legacy filesystem root scan |
| `GET /api/sessions` | Raw session → project map |

```bash
python3 projects-dashboard/server.py --port 8765 --no-browser
curl -sS 'http://127.0.0.1:8765/api/projects' | python3 -m json.tool
```

## Collectors (no UI)

```bash
python3 projects-dashboard/collectors.py          # grok mode
python3 projects-dashboard/collectors.py all      # filesystem mode
python3 projects-dashboard/sessions.py
python3 -m unittest discover -s projects-dashboard/tests -v
```
