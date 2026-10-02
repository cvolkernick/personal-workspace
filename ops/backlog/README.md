# Project backlog

Ideas and projects to start later. Managed via the Workflow Management dashboard or `projects-dashboard/backlog.py`.

- `items.json` — source of truth (local runtime, gitignored)
- `jobs.json` / `scheduler.json` — scheduler tick state (local runtime, gitignored)
- `jobs.archive.json` — terminal jobs older than `completed_omit_days` (default 7)
- `suggestions.json` — recommendation approve/reject state (local runtime, gitignored). A missing file is created empty.
- `seeds/` — goal planning seeds when you **Initiate** an item

Initiate → writes a seed plan + `/goal` objective → open Grok in personal-workspace and paste the objective (or run the launch script).
