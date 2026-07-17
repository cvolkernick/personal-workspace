# Goal seed: Time allocator

- **Backlog id:** `bfdc9db1-82f9-4100-8829-87e0883f39ae`
- **Priority:** critical
- **Status:** done (MVP usable)
- **Area:** holistic
- **Created:** 2026-07-17T06:17:34.246779+00:00
- **Initiated:** 2026-07-17T17:37:15.653376+00:00

## Problem / intent

Daily time is finite and goals compete. Without a durable, editable list of tasks/goals and a simple way to assign priority and time slots, planning stays ad-hoc and agentic coaching has nothing structured to work with.

**Long-term vision (post-MVP):** an agentic loop that recommends next actions, coaches progress across goals, and re-allocates time by precedence during a typical day.

**MVP intent:** ship the foundation — a local, runnable list of tasks/goals with add/remove and basic time/priority allocation — under `holistic/`.

## Users

- Primary: the personal-workspace owner (solo), planning an average day from the CLI.
- Secondary: future agent sessions that read the same store to recommend next steps (not required for MVP).

## Success criteria

- [x] Spec written (this file refined with problem, users, success criteria, non-goals, MVP, file layout, risks)
- [x] Runnable MVP under `holistic/`: core task/goal list with durable add and remove
- [x] Workflow without manual file editing: list items; show priority and allocated minutes; re-allocate across the list
- [x] Real in-repo tests exercise shipped add/remove/list/allocate path; entry point launch verified
- [x] Changes committed on `work/holistic` and pushed via `git_workflow`
- [x] Next-iteration steps documented for agentic coaching / day loop

## Non-goals

- Full agentic coaching loop, multi-goal recommender, or continuous day-loop process
- Calendar, email, notifications, multi-user, or mobile UI
- ML-based priority estimation or LLM calls on the MVP path
- Dashboard polish beyond a minimal CLI that proves list + allocate

## MVP scope

1. **Core list** of tasks/goals (starter seed list + durable JSON store).
2. **Add / remove** tasks or goals by id or title.
3. **List** current items with priority and allocated minutes.
4. **Allocate**: set minutes on an item and/or distribute a daily total across items by priority weight.
5. **CLI entry** (`python3 -m holistic.time_allocator` or `run_time_allocator.py`) with `--data` override for tests/isolation.

## File layout

```
holistic/
  README.md                 # usage + next iteration
  __init__.py
  time_allocator/
    __init__.py             # public re-exports
    __main__.py             # python -m holistic.time_allocator
    domain.py               # pure add/remove/list/allocate (no I/O)
    store.py                # JSON load/save
    cli.py                  # argparse entry
  data/
    tasks.json              # default durable store (created on first write)
  tests/
    test_time_allocator.py  # real domain + store + CLI path
```

## Risks

| Risk | Mitigation |
|------|------------|
| Vision (agentic day-loop) swallows MVP | Hard non-goals; only document next steps |
| Manual JSON editing becomes the UX | All mutations via CLI; tests drive CLI/domain API |
| Default data path pollutes repo during tests | `--data` / env override; tests use temp files |
| Priority scheme ambiguity | Document: higher `priority` = more weight when allocating |

## Notes

MVP implemented under `holistic/time_allocator`. Promote backlog status to `done` (or `ready` for next phase) when verification passes.

## Next iteration (post-MVP)

Shipped since MVP: dashboard UI, **ongoing targets/KPIs**, **rolling 24h plan** (sleep reserve → fixed daily → weekly sessions → ad-hoc → Lyft fill), personal seed (sleep 8h/7d, Duchess 130m, workout 3–5×, Lyft fill).

Still open:
1. **Recommend:** top-1 next action for “now” from the live plan (rule-based first).
2. **Coach loop:** mid-window progress notes + re-plan.
3. Blocked calendar windows; split Duchess into two walk blocks; Lyft hours logging.
4. **Agent hooks:** stable JSON for Grok/skills.
5. Optional: strategy/today.md or projects-dashboard card.

## Grok `/goal` objective

```
Backlog project: Time allocator

Context:
Agentic assistant to allocate time across tasks and intelligently advise/recommend next action items during an average day. I would like to be able to make an ongoing / looping process that allows goals to be set and then uses agentic coaching to identify and recommend steps to progress toward various goals and allocate time according to priority/precedence etc.

Do this in two phases without asking me to re-specify basics:
1) Planning — write a short design/spec (problem, users, success criteria, non-goals, MVP scope, file layout, risks) into the seed plan under ops/backlog/seeds/ and refine it as needed.
2) Build — implement the MVP: core list of tasks to allocate time to as a starting point, optionality to add or remove tasks / goals
 Prefer living under personal-workspace/holistic/.
Use git_workflow (work branch + sync/protect) so changes are committed and pushed. When MVP is usable, mark progress and leave clear next iteration steps.
```

## How to start

From personal-workspace (preferred — starts Grok with `/goal` already set):

```bash
bash ops/backlog/seeds/time-allocator-bfdc9db1.launch.sh
```

That runs: `grok --cwd personal-workspace "$(cat …prompt.txt)"` where the prompt
begins with `/goal …` plus backlog title/MVP/notes/seed path.

After planning, implement MVP and iterate. Update backlog status via the dashboard.
