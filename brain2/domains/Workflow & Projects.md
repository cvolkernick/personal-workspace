# Workflow & Projects

**Hub:** [[00 Home - B2 Hub]] · **Map:** [[Personal Workspace Map]]  
**Execution:** `projects-dashboard/`, `ops/`, plus domain tools for each fleet

## Active projects (high-level)

- [[Bitcoin ASIC Mining Fleet]]
- [[Vehicle Rental Management Fleet]]
- [[X Account Growth]]

## Operating principles applied to projects

- Automate and simplify wherever possible.
- Avoid scope creep, analysis paralysis, and excessive pivoting that kills momentum.
- Maintain consistency with the plan while still allowing useful optimization and continuous improvement.
- Focus energy on highest return on invested energy (time / physical / capital) and abstract upward.
- Capital is the binding constraint for scaling the two primary fleets ([[Strategy & Bets]], [[Finance & Investment]]).

## Decision logging

Material choices of the form “chose X over Y because…” go in [[Decision Log]] (and individual notes under `decisions/` when needed) so patterns can be reused by agents and future self.

## Session vs git (execution hygiene)

| Kind | Where | Git? |
|------|-------|------|
| Full Grok transcripts | `~/.grok/sessions` | No |
| Lightweight session index | `ops/session-index/` | Yes (metadata) |
| Domain code | `personal-workspace` | Yes |
| B2 knowledge notes | `brain2/` vault | Yes (durable prose) |

Resume: `grok --resume <session-id>` or by title after reboot.

## Worktree rule

One monorepo checkout = one branch. Parallel domains use `~/personal-workspace-worktrees/<area>/` on `work/<area>`.

## Related

- [[Strategy & Bets]]
- [[Finance & Investment]]
- [[Agents & Tooling]]
- [[Ikigai & Identity]]
- [[Decision Log]]
- [[HOWTO - Using B2]]

---

*Updated 2026-07-26 from B2 seed interview.*
