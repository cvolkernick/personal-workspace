# Workflow & Projects

**Execution:** `projects-dashboard/`, `ops/`  
**Hub:** [[00 Home - B2 Hub]] · **Map:** [[Personal Workspace Map]]

## Goals

- Know what is in flight across Grok sessions and git worktrees
- Pre-reboot readiness: session index, protect & push, resume commands
- Avoid landing domain commits on the wrong `work/<area>` branch

## Session vs git

| Kind | Where | Git? |
|------|-------|------|
| Full Grok transcripts | `~/.grok/sessions` | No |
| Lightweight session index | `ops/session-index/` | Yes (metadata) |
| Domain code & notes | `personal-workspace` | Yes |
| B2 knowledge notes | `B2/` vault | Yes (durable prose) |

Resume: `grok --resume <session-id>` after reboot.

## Worktree rule

One monorepo checkout = one branch. Parallel domains use:

`~/personal-workspace-worktrees/<area>/` on `work/<area>`.

| Area | Branch |
|------|--------|
| resistance-dashboard | `work/resistance-dashboard` |
| holistic | `work/holistic` |
| orchestra | `work/orchestra` |
| iot | `work/iot` |
| projects-dashboard | `work/projects-dashboard` |
| treasury | `work/treasury` |

## Related

- [[Agents & Tooling]] — Grok + dashboards
- [[Strategy & Bets]] — initiatives that feed "today" focus
- [[HOWTO - Using B2]] — when knowledge leaves chat into the vault
