# Projects Dashboard (personal-workspace)

Status viewer for the **personal-workspace** monorepo only.

Grok Build work on this machine maps to top-level areas inside this repo
(`resistance-dashboard`, `financial-command`, `treasury`, …). The dashboard
shows:

1. **Repo header** — branch, remotes, dirty/clean, ahead/behind for the whole repo  
2. **Project cards** — each top-level area, dirty files in that area, Grok sessions that edited it  
3. **Live sessions** — from `~/.grok/active_sessions.json`

## Launch

```bash
python3 projects-dashboard/server.py
# or double-click start.command
```

http://127.0.0.1:8765/

| Endpoint | Description |
|----------|-------------|
| `GET /api/projects` | Workspace status + project areas |
| `GET /api/projects?only_touched=1` | Only areas with Grok edits |

```bash
python3 projects-dashboard/workspace.py
python3 -m unittest discover -s projects-dashboard/tests -v
```
