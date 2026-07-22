# Goal seed: Improve command center daily action planner (Today's Focus)

- **Backlog id:** `0cb116db-cfee-485e-b841-629a8593e61d`
- **Priority:** medium
- **Status:** planning
- **Area:** (tbd)
- **Created:** 2026-07-17T06:14:42.157807+00:00
- **Initiated:** 2026-07-22T04:24:34.710234+00:00

## Problem / intent

## Description
Evolve the command center from a good visual reader into the place where macro strategy (the bets + dynamic domains) becomes a trustworthy, low-friction daily action plan. The key is a synthesized "Today's Focus" view that surfaces the highest-leverage next actions without the user having to manually re-synthesize scattered notes every time.

This initiative itself is meta: building the tool that helps execute on the other bets.

## Current Next Action
Add / enhance the rendering of `strategy/today.md` in the dashboard HTML (prominent section, nice cards for the top items, easy visual link back to the source bets and initiatives). Make "Add new initiative" guidance point to creating a real structured MD like this one.

## Progress / Wins
- [x] Requirements gathered via Socratic process (user confirmed direction and that Today's Focus list is the single most valuable first slice).
- [ ] First implementation of the Today's Focus rendering + supporting MDs (this file + bets.md + today.md skeleton).
- [ ] User starts using the new flow for at least one real day of planning.

## Notes / Ideas
- Keep the source of truth as lightweight MD (frontmatter + body) so it's editable in any editor and portable to Obsidian later if desired.
- The dashboard HTML can fetch and render it live (using the existing marked.js pattern) when served over HTTP.
- Over time this can become more automated (Grok proposes updates to today.md based on initiative status changes).

See the parent requirements doc in `strategy/command-center-requirements.md` for the full context and other related initiatives.

## MVP scope

_Define the smallest shippable slice._

## Success criteria (draft)

- [ ] Spec written (this file refined)
- [ ] MVP implemented and runnable
- [ ] Basic verification (test or manual checklist) passes
- [ ] Changes committed on `work/<area>` and pushed

## Non-goals

- Full multi-user polish
- Premature optimization

## Notes

Add a prominent 'Today's Focus' section to dashboard/index.html that nicely renders strategy/today.md (and makes it easy to edit)

## Grok `/goal` objective

```
Backlog project: Improve command center daily action planner (Today's Focus)

Context:
## Description
Evolve the command center from a good visual reader into the place where macro strategy (the bets + dynamic domains) becomes a trustworthy, low-friction daily action plan. The key is a synthesized "Today's Focus" view that surfaces the highest-leverage next actions without the user having to manually re-synthesize scattered notes every time.

This initiative itself is meta: building the tool that helps execute on the other bets.

## Current Next Action
Add / enhance the rendering of `strategy/today.md` in the dashboard HTML (prominent section, nice cards for the top items, easy visual link back to the source bets and initiatives). Make "Add new initiative" guidance point to creating a real structured MD like this one.

## Progress / Wins
- [x] Requirements gathered via Socratic process (user confirmed direction and that Today's Focus list is the single most valuable first slice).
- [ ] First implementation of the Today's Focus rendering + supporting MDs (this file + bets.md + today.md skeleton).
- [ ] User starts using the new flow for at least one real day of planning.

## Notes / Ideas
- Keep the source of truth as lightweight MD (frontmatter + body) so it's editable in any editor and portable to Obsidian later if desired.
- The dashboard HTML can fetch and render it live (using the existing marked.js pattern) when served over HTTP.
- Over time this can become more automated (Grok proposes updates to today.md based on initiative status changes).

See the parent requirements doc in `strategy/command-center-requirements.md` for the full context and other related initiatives.

Do this in two phases without asking me to re-specify basics:
1) Planning — write a short design/spec (problem, users, success criteria, non-goals, MVP scope, file layout, risks) into the seed plan under ops/backlog/seeds/ and refine it as needed.
2) Build — implement the MVP: A minimal working slice we can use and iterate on, with tests or verification for the happy path, committed in personal-workspace.
 Place durable work under personal-workspace on an appropriate work/<area> branch.
Use git_workflow (work branch + sync/protect) so changes are committed and pushed. When MVP is usable, mark progress and leave clear next iteration steps.
```

## How to start

From personal-workspace (preferred — starts Grok with `/goal` already set):

```bash
bash ops/backlog/seeds/improve-command-center-daily-action-planner-toda-0cb116db.launch.sh
```

That runs: `grok --cwd personal-workspace "$(cat …prompt.txt)"` where the prompt
begins with `/goal …` plus backlog title/MVP/notes/seed path.

After planning, implement MVP and iterate. Update backlog status via the dashboard.
