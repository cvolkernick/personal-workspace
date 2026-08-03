# Workflow Management — personal-workspace

Pre-reset readiness **and** git/session protection for this monorepo.

## Session backup: what goes in git?

| Content | In git? | Why |
|---------|---------|-----|
| Full `~/.grok/sessions` (~100MB+, transcripts) | **No** | Huge, secret-prone, high churn |
| `ops/session-index/` (IDs, titles, resume cmds) | **Yes** | Enough to recover resume after machine loss |
| Offline tarball of summaries | **Outside repo** (`~/Backups/grok-sessions/`) | Optional DR |

```bash
python3 projects-dashboard/session_backup.py index      # write ops/session-index
python3 projects-dashboard/session_backup.py archive    # ~/Backups/... summaries tar
python3 projects-dashboard/session_backup.py archive --full  # full dirs — private disk only
```

## Auto commit + push after work

```bash
# Preferred one-shot for agents / you after a completed unit of work:
python3 projects-dashboard/git_workflow.py sync

# Or from the dashboard: Protect & push
# Or API: POST /api/sync
```

`sync` = refresh session index → commit on `work/<area>` if needed → push.

## Branches

| Branch | Role |
|--------|------|
| `master` | Integration |
| `work/<area>` | Active work for a top-level area |
| `feature/<slug>` | Optional longer features |

```bash
python3 projects-dashboard/git_workflow.py start treasury
python3 projects-dashboard/git_workflow.py status
python3 projects-dashboard/git_workflow.py protect --auto          # durable only → work/*
python3 projects-dashboard/git_workflow.py protect "feat: …"      # full (product + durable)
python3 projects-dashboard/git_workflow.py sync "fix: …"         # session index + full protect
```

**Protect modes:** bare `protect`/`sync` = **auto** (snapshots/journals/session-index only;
push only on `work/*`). Explicit message or dashboard button = **full**. Never pushes
`master`. See `Agents.md` — *auto-save keeps the lights on; PRs change the product.*

### Branch graph (gitk-style)

The dashboard **Branches** section draws a linked-list / DAG view of commits
(same topology as `gitk` / `git log --graph`), from local git — no GitHub API.

```bash
python3 projects-dashboard/branch_graph.py --max 40
# API while server is running:
curl -s 'http://127.0.0.1:8765/api/branch-graph?max=80&remotes=1' | python3 -m json.tool | head
```

| | |
|--|--|
| UI | Workflow Management → **Branches** → Graph / List |
| API | `GET /api/branch-graph?max=80&remotes=1` |
| External | [GitHub Branches](https://github.com/cvolkernick/personal-workspace/branches) · [Network](https://github.com/cvolkernick/personal-workspace/network) |

## Day bridge (Workflow ↔ Time allocator)

Macro backlog stays in Workflow; day minutes stay in Holistic. Bridge links them:

```bash
python3 projects-dashboard/bridge.py status
python3 projects-dashboard/bridge.py send <backlog-id>
python3 projects-dashboard/bridge.py send-top 1
```

Dashboard: **Send to today** on a backlog card, or **Send top to today** in the Day bridge strip.  
Orchestra shows the same candidates + already-linked day tasks (read-only deep links).

## Strategy vs projects

| Path | Role on dashboard |
|------|-------------------|
| `strategy/`, `initiatives/`, `ops/` | **Meta content** — not project cards. `strategy/today.md` feeds **Today's focus** + recommendations |
| `resistance-dashboard/`, `treasury/`, etc. | **Execution projects** — dirty status, sessions, exit readiness |

## Backlog (start later → plan → MVP)

Git-tracked ideas under `ops/backlog/items.json`.

| Action | How |
|--------|-----|
| Add item | Dashboard form or `POST /api/backlog` |
| **Initiate** | Writes seed + objective under `ops/backlog/seeds/`, status→`planning`, optional Terminal launch |
| Grok session | Paste `/goal` text (or run `bash ops/backlog/seeds/….launch.sh`) — plan spec then build MVP |

```bash
python3 projects-dashboard/backlog.py list
python3 projects-dashboard/backlog.py add "My future project"
python3 projects-dashboard/backlog.py initiate <id>
python3 projects-dashboard/backlog.py import   # from initiatives/*.md
```

### Recommendations (approve / reject)

Dynamic next-step actions + new backlog proposals from backlog status, `strategy/today.md`, dirty monorepo areas, and Grok session index.

```bash
python3 projects-dashboard/recommendations.py refresh
python3 projects-dashboard/recommendations.py list
python3 projects-dashboard/recommendations.py approve <id>
python3 projects-dashboard/recommendations.py reject <id>
```

| Kind | Approve does |
|------|----------------|
| `action` | Updates linked backlog notes (idea→ready when appropriate) or creates a ready item |
| `new_item` | Adds a full backlog entry (title, priority, area, MVP, description) |

Persisted in `ops/backlog/suggestions.json`.

## Launch dashboard

```bash
python3 projects-dashboard/server.py   # http://127.0.0.1:8765/
python3 -m unittest discover -s projects-dashboard/tests -v
```

### Always-on on Raspberry Pi (prism-gateway)

Same pattern as Orchestra / IoT / FCC: systemd user unit on the Pi, bind `0.0.0.0:8765`.

| Path | URL |
|------|-----|
| **LAN** | http://192.168.100.98:8765/ |
| **Tailscale** | http://100.67.114.2:8765/ or http://prism-gateway:8765/ |
| **Health** | `/api/health` |
| **Branch graph** | `/api/branch-graph?max=80&remotes=1` |

```bash
# From Mac monorepo root — re-deploy this package only:
bash deploy/install_remote.sh prism-agent@192.168.100.98 --only workflow
# Or rsync this worktree's projects-dashboard/ then:
ssh prism-agent@192.168.100.98 'systemctl --user restart workflow-dashboard'
```

Unit: `deploy/units/workflow-dashboard.service` (`--bind 0.0.0.0 --port 8765 --no-browser --local`).  
Server accepts `--local` / `--host` for Pi unit compatibility (API is always local on the backend process).