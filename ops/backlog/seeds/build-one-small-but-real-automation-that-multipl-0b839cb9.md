# Goal seed: Build one small but real automation that multiplies output on creative or wealth-building work

- **Backlog id:** `0b839cb9-27d0-4fe8-b48c-0e16361f6aa3`
- **Priority:** medium
- **Status:** planning
- **Area:** (tbd)
- **Created:** 2026-07-17T06:14:42.157420+00:00
- **Initiated:** 2026-07-17T17:30:32.588308+00:00

## Problem / intent

## Description
One of the highest-leverage things that advances the AI/Autonomy/Robotics bet is actually *using* and *shipping* small agentic automations that multiply the user's own output on the other bets (creative work, wealth research, command center maintenance itself, etc.).

This is a standing "template" style initiative: pick a real, painful, repeatable task and remove friction with a small tool, prompt, or structured workflow. The act of shipping these compounds.

## Current Next Action (example)
Pick one concrete manual step that currently contributes to the "scattered capture → overwhelming synthesis" problem and make a tiny improvement (could be a better prompt template, a small script that parses something into the right MD format, an improved section in the command center, etc.). Document it here or in a related note.

## Progress / Wins
- (To be filled as small automations are shipped.)

## Notes
- Keep scope tiny and shippable in hours, not days.
- Each win can be logged and can feed the "wins" part of reviews or the Today plan.
- Over time these small wins become evidence of progress on the AI/Autonomy/Robotics bet.

See `strategy/command-center-requirements.md` and `strategy/bets.md` for the broader context. This is the kind of initiative that should appear in the Actionable Plans / Today's Focus when it has a defined next action.

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

Identify one painful, repeatable manual step in the current workflow (e.g. turning scattered notes from a chat or memory file into a structured initiative or today.md entry) and prototype a tiny script or Grok prompt + workflow that reduces the friction.

## Grok `/goal` objective

```
Backlog project: Build one small but real automation that multiplies output on creative or wealth-building work

Context:
## Description
One of the highest-leverage things that advances the AI/Autonomy/Robotics bet is actually *using* and *shipping* small agentic automations that multiply the user's own output on the other bets (creative work, wealth research, command center maintenance itself, etc.).

This is a standing "template" style initiative: pick a real, painful, repeatable task and remove friction with a small tool, prompt, or structured workflow. The act of shipping these compounds.

## Current Next Action (example)
Pick one concrete manual step that currently contributes to the "scattered capture → overwhelming synthesis" problem and make a tiny improvement (could be a better prompt template, a small script that parses something into the right MD format, an improved section in the command center, etc.). Document it here or in a related note.

## Progress / Wins
- (To be filled as small automations are shipped.)

## Notes
- Keep scope tiny and shippable in hours, not days.
- Each win can be logged and can feed the "wins" part of reviews or the Today plan.
- Over time these small wins become evidence of progress on the AI/Autonomy/Robotics bet.

See `strategy/command-center-requirements.md` and `strategy/bets.md` for the broader context. This is the kind of initiative that should appear in the Actionable Plans / Today's Focus when it has a defined next action.

Do this in two phases without asking me to re-specify basics:
1) Planning — write a short design/spec (problem, users, success criteria, non-goals, MVP scope, file layout, risks) into the seed plan under ops/backlog/seeds/ and refine it as needed.
2) Build — implement the MVP: A minimal working slice we can use and iterate on, with tests or verification for the happy path, committed in personal-workspace.
 Place durable work under personal-workspace on an appropriate work/<area> branch.
Use git_workflow (work branch + sync/protect) so changes are committed and pushed. When MVP is usable, mark progress and leave clear next iteration steps.
```

## How to start

From personal-workspace (preferred — starts Grok with `/goal` already set):

```bash
bash ops/backlog/seeds/build-one-small-but-real-automation-that-multipl-0b839cb9.launch.sh
```

That runs: `grok --cwd personal-workspace "$(cat …prompt.txt)"` where the prompt
begins with `/goal …` plus backlog title/MVP/notes/seed path.

After planning, implement MVP and iterate. Update backlog status via the dashboard.
