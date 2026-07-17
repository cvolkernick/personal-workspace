# Agent Instructions (personal-workspace)

## Git / remote sync (standing rule)
- After **any and all** durable project changes that should persist, **commit and push** without waiting to be asked.
- Prefer automation:
  ```bash
  python3 projects-dashboard/git_workflow.py sync
  ```
  This refreshes `ops/session-index/`, commits on a `work/<area>` branch if needed, and pushes to `origin`.
- Never commit secrets (`.env`, OAuth tokens, `~/.config/**`, credentials).

## Branch conventions
| Branch | Purpose |
|--------|---------|
| `master` | Integration only — keep green and pushed; merge work branches when stable |
| `work/<area>` | Active work for a monorepo top-level area (`work/treasury`, `work/projects-dashboard`, …) |
| `feature/<slug>` | Optional longer-lived features |

- Do **not** pile unfinished work only on `master`. Start or continue the area branch:
  ```bash
  python3 projects-dashboard/git_workflow.py start <area>
  ```
- After completing a unit of work: `git_workflow.py sync` (or dashboard **Protect & push**).
- Confirm local branch tracks remote after push (`git status -sb`). If remote moved, `git pull --rebase` then push.

## Grok sessions vs git
- Full session transcripts live under `~/.grok/sessions` (survive reboot; not for git).
- Lightweight **session index** (IDs, titles, resume commands) → `ops/session-index/` via `session_backup.py` / sync.
- Optional offline tarball (outside repo): `python3 projects-dashboard/session_backup.py archive`
