# Agent Instructions (personal-workspace)

## Git / remote sync (standing rule)
- After **any and all** durable project changes that should persist, **commit and push** without waiting to be asked.
- Prefer automation:
  ```bash
  python3 projects-dashboard/git_workflow.py sync
  ```
  This refreshes `ops/session-index/`, commits on a `work/<area>` branch if needed, and pushes to `origin`.
- Never commit secrets (`.env`, OAuth tokens, `~/.config/**`, credentials).
- **Always check `git branch --show-current` before commit.** Do not land finance work on `work/iot` or `work/orchestra`.

## Branch conventions
| Branch | Purpose |
|--------|---------|
| `master` | Integration only — keep green and pushed; merge work branches when stable |
| `work/<area>` | Active work for a **domain** (see TLD map below) |
| `feature/<slug>` | Optional longer-lived features |

### Top-level directories → work branches

| Domain | Top-level dirs (TLDs) | Work branch |
|--------|----------------------|-------------|
| **Finance** | `treasury/`, `financial-command/`, `investment/`, `research/` | `work/treasury` |
| **Orchestra** | `orchestra/`, root launchers (`launch.py`, `open-command-center.command`) | `work/orchestra` |
| **Workflow** | `projects-dashboard/`, `ops/` | `work/projects-dashboard` |
| **Fitness** | `resistance-dashboard/`, `fitness/` | `work/resistance-dashboard` |
| **Time** | `holistic/` | `work/holistic` |
| **IoT** | `iot/` | `work/iot` |
| **Planning** (meta) | `strategy/`, `initiatives/` | (no project card; use workflow branch if needed) |

- Do **not** pile unfinished work only on `master`. Start or continue the area branch:
  ```bash
  python3 projects-dashboard/git_workflow.py start treasury
  # or: start iot | orchestra | holistic | projects-dashboard | resistance-dashboard
  ```
- After completing a unit of work: `git_workflow.py sync` (or dashboard **Protect & push**).
- Confirm local branch tracks remote after push (`git status -sb`). If remote moved, `git pull --rebase` then push.

## Multi-dashboard / parallel work (worktrees)

**Problem:** one monorepo checkout can only be on one branch. Editing Fitness while the
main tree is on `work/holistic` or `work/orchestra` either lands commits on the wrong
branch or serves **stale** `resistance-dashboard/` files.

**Rule:** each domain has a dedicated worktree under
`~/personal-workspace-worktrees/<area>/` on `work/<area>`.

```bash
# Create / refresh worktrees
python3 projects-dashboard/worktrees.py ensure
python3 projects-dashboard/worktrees.py list

# Fitness path (use this for resistance edits & server)
python3 projects-dashboard/worktrees.py path resistance-dashboard
# → ~/personal-workspace-worktrees/resistance-dashboard
```

| Dashboard | Worktree dir | Branch |
|-----------|--------------|--------|
| Resistance / Fitness | `…/resistance-dashboard` | `work/resistance-dashboard` |
| Holistic / Time | `…/holistic` | `work/holistic` |
| Orchestra | `…/orchestra` | `work/orchestra` |
| IoT | `…/iot` | `work/iot` |
| Workflow | `…/projects-dashboard` | `work/projects-dashboard` |
| Finance | `…/treasury` | `work/treasury` |

- **Start scripts** (`resistance-dashboard/start.sh`, `holistic/start.command`, …) prefer
  the matching worktree when it exists, so launchers keep working even if the main
  checkout is on another branch.
- **Agents:** before editing a domain, `cd` into that worktree (or
  `git_workflow.py start <area>`). Never commit Fitness changes on `work/orchestra`
  or `work/holistic`.
- Env override: `PERSONAL_WORKSPACE_WORKTREES` (default `~/personal-workspace-worktrees`).

## Grok sessions vs git
- Full session transcripts live under `~/.grok/sessions` (survive reboot; not for git).
- Lightweight **session index** (IDs, titles, resume commands) → `ops/session-index/` via `session_backup.py` / sync.
- Optional offline tarball (outside repo): `python3 projects-dashboard/session_backup.py archive`
