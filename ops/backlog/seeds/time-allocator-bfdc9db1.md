# Goal seed: Time allocator

- **Backlog id:** `bfdc9db1-82f9-4100-8829-87e0883f39ae`
- **Priority:** critical
- **Status:** planning
- **Area:** holistic
- **Created:** 2026-07-17T06:17:34.246779+00:00
- **Initiated:** 2026-07-17T17:27:42.540258+00:00

## Problem / intent

Agentic assistant to allocate time across tasks and intelligently advise/recommend next action items during an average day. I would like to be able to make an ongoing / looping process that allows goals to be set and then uses agentic coaching to identify and recommend steps to progress toward various goals and allocate time according to priority/precedence etc.

## MVP scope

core list of tasks to allocate time to as a starting point, optionality to add or remove tasks / goals

## Success criteria (draft)

- [ ] Spec written (this file refined)
- [ ] MVP implemented and runnable
- [ ] Basic verification (test or manual checklist) passes
- [ ] Changes committed on `work/<area>` and pushed

## Non-goals

- Full multi-user polish
- Premature optimization

## Notes

Promote to ready: clarify MVP for “Time allocator”, then Initiate goal planning.

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

From personal-workspace:

```bash
# Option A — launch helper (opens instruction + copies objective path)
bash ops/backlog/seeds/time-allocator-bfdc9db1.launch.sh

# Option B — in an existing Grok session:
# /goal <paste objective above>
```

After planning, implement MVP and iterate. Update backlog status via the dashboard.
