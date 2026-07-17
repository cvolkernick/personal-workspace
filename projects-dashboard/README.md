# Graceful Exit — personal-workspace

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
python3 projects-dashboard/git_workflow.py protect "msg"
```

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
