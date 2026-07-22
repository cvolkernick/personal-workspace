# Goal seed: Panamerica Auto Website

- **Backlog id:** `6b74d2ca-d0ca-4c87-8c6c-60777e099a88`
- **Priority:** medium
- **Status:** planning
- **Area:** business
- **Created:** 2026-07-17T06:18:43.331881+00:00
- **Initiated:** 2026-07-22T04:00:21.286283+00:00

## Problem / intent

Create a business website / platform for Panamerica Auto

## MVP scope

basic website featuring high level services

## Success criteria (draft)

- [ ] Spec written (this file refined)
- [ ] MVP implemented and runnable
- [ ] Basic verification (test or manual checklist) passes
- [ ] Changes committed on `work/<area>` and pushed

## Non-goals

- Full multi-user polish
- Premature optimization

## Notes



## Grok `/goal` objective

```
Backlog project: Panamerica Auto Website

Context:
Create a business website / platform for Panamerica Auto

Do this in two phases without asking me to re-specify basics:
1) Planning — write a short design/spec (problem, users, success criteria, non-goals, MVP scope, file layout, risks) into the seed plan under ops/backlog/seeds/ and refine it as needed.
2) Build — implement the MVP: basic website featuring high level services
 Prefer living under personal-workspace/business/.
Use git_workflow (work branch + sync/protect) so changes are committed and pushed. When MVP is usable, mark progress and leave clear next iteration steps.
```

## How to start

From personal-workspace (preferred — starts Grok with `/goal` already set):

```bash
bash ops/backlog/seeds/panamerica-auto-website-6b74d2ca.launch.sh
```

That runs: `grok --cwd personal-workspace "$(cat …prompt.txt)"` where the prompt
begins with `/goal …` plus backlog title/MVP/notes/seed path.

After planning, implement MVP and iterate. Update backlog status via the dashboard.
