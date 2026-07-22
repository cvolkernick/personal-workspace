# Goal seed: Panamerica Auto Website

- **Backlog id:** `6b74d2ca-d0ca-4c87-8c6c-60777e099a88`
- **Priority:** medium
- **Status:** planning
- **Area:** business
- **Created:** 2026-07-17T06:18:43.331881+00:00
- **Initiated:** 2026-07-22T04:40:35.240564+00:00

## Problem / intent

Create a business website / platform for Panamerica Auto

## MVP scope

basic website featuring high level services

## Success criteria (draft)

- [x] Spec written (this file refined)
- [x] MVP implemented and runnable (`business/panamerica-auto/`)
- [x] Basic verification (test or manual checklist) passes
- [x] Changes committed and pushed (MVP on master via PR #4; backlog close-out on worker branch)

## Non-goals

- Full multi-user polish
- Premature optimization

## Notes

**Shipped MVP path:** `business/panamerica-auto/`

- Single-page site: hero, 6 service cards, why-us, process, contact form
- `python3 server.py` → http://127.0.0.1:8765/
- Verify: `python3 -m unittest discover -s business/panamerica-auto/tests -v`

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
