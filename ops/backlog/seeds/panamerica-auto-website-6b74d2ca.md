# Goal seed: Panamerica Auto Website

- **Backlog id:** `6b74d2ca-d0ca-4c87-8c6c-60777e099a88`
- **Priority:** medium
- **Status:** done (MVP usable)
- **Area:** business
- **Created:** 2026-07-17T06:18:43.331881+00:00
- **Initiated:** 2026-07-20T01:38:46.252801+00:00

## Problem / intent

Panamerica Auto (automotive sales, service, and import business) lacks a professional public web presence. A clean, fast-loading site is needed to:

- Present high-level services to potential customers and partners.
- Establish credibility with clear value props and contact info.
- Serve as the foundation for future expansion into a full platform (inventory, scheduling).

**MVP intent:** deliver a self-contained, professional static website under `business/` that highlights core services and is immediately viewable via simple local server or file:// .

Long-term vision (post-MVP): inventory browser, online booking, lead capture, real backend.

## Users

- Primary: prospective customers (retail buyers, small fleets) researching vehicle purchases, maintenance, or imports.
- Secondary: business partners, suppliers, and the owner/staff who share the site for quick capability overviews.
- Tertiary (future): web visitors arriving via search or referrals.

## Success criteria

- [x] Spec written (this file refined with problem, users, success criteria, non-goals, MVP scope, file layout, risks)
- [x] MVP implemented and runnable: self-contained `business/index.html` + README
- [x] Primary sections (hero + services) render correctly; site name + service descriptions present
- [x] Documented launch entry point runs cleanly (twice); HTTP fetches confirm content
- [x] Minimal tests read the real shipped HTML from disk and assert key content
- [x] All durable changes (seed + site) performed on `work/business` via git_workflow start + protect/sync; pushed
- [x] Seed updated to mark MVP usable + explicit next-iteration steps

## Non-goals

- Backend, database, auth, contact form processing/submission, booking, inventory system, or e-commerce.
- Multi-page SPA/router, external build tooling (webpack/vite), heavy frameworks, or external asset fetches.
- Hosting, custom domain, SEO, analytics, deployment pipelines, or production optimization.
- Worktree setup or changes to workspace.py / launch.py maps.
- Responsive design polish beyond basic mobile-friendly, or rich visuals (keep to inline styles + minimal).
- Full interactive demos or live user testing.

## MVP scope

1. Self-contained `business/index.html` (inline CSS, no external script/module src, works from file:// and any static server).
2. Professional header/hero introducing Panamerica Auto.
3. High-level services section featuring 4+ offerings (e.g. New & Pre-owned Sales, Maintenance & Repair, Import & Logistics, Parts & Accessories).
4. Short about blurb + contact stub (phone/email/location placeholder).
5. `business/README.md` documenting launch, structure, and services.
6. Minimal `business/start.command` (or documented `python -m http.server`).
7. One or more tests (business/tests/) that directly read the committed index.html and assert name + service text presence (drive real artifact).
8. Git workflow: start business, protect/sync after units; commits land on work/business.

## File layout

```
business/
  index.html            # self-contained static site (hero, services, about, contact)
  README.md             # how to run, services list, next steps
  start.command         # convenience launcher (optional thin wrapper)
  tests/
    test_site.py        # reads ../index.html from disk, asserts content
```

(Seed plan lives at `ops/backlog/seeds/panamerica-auto-website-6b74d2ca.md`; no other files.)

## Risks

| Risk | Mitigation |
|------|------------|
| Scope creep into "platform" features | Hard non-goals; only static HTML + thin launcher + tests. |
| Mixed paths (seed in ops/, site in business/) cause git_workflow to switch branches | Start on work/business; use protect(ensure_work_branch=False) for the change commit so it lands on instructed branch; run CLI sync + restore branch if needed; capture outputs. |
| Site looks empty or broken on launch | Self-contained; verification does direct file read + repeated server fetch + body contains checks. |
| No real images / visual assets | Use text + CSS; inline SVGs or simple hero if needed. Avoid external URLs. |
| Branch tracking after push | Always `git branch --show-current` + `git status -sb` before/after; assert work/business. |

## Notes

MVP is intentionally tiny: a professional one-page marketing site that can be opened instantly. All service descriptions are plain content testable by string presence in the shipped file.

**MVP usable** — basic website is live under business/, launchable, tested, committed & pushed on work/business. Next iteration items below. Update backlog item status accordingly.

## Next iteration (post-MVP)

Concrete follow-ups (pick in priority order):
1. Add a visible "Request a Quote" or contact form that is at least a mailto: or static HTML form (no backend processing yet).
2. Expand one service into a short detail section or modal with 3-4 bullet benefits (still static).
3. Hard-code a "Featured Vehicles" teaser grid (3 sample listings with year/make/model/price).
4. Add 1-2 inline SVG icons or a simple hero image (data URI) for visual interest without external files.
5. Document + test a one-line deploy (e.g. GitHub Pages from /business or copy to docs/).
6. Optional: make start.command also support --port and --no-browser like peers.
7. Backlog grooming: move this card to "done" or create follow-up cards for inventory platform phase.

Leave the site as the source of truth for the basic high-level services marketing site.

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
