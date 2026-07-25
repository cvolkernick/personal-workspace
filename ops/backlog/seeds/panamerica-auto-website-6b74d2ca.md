# Goal seed: Panamerica Auto Website

- **Backlog id:** `6b74d2ca-d0ca-4c87-8c6c-60777e099a88`
- **Priority:** medium
- **Status:** done (MVP shipped)
- **Area:** business
- **Created:** 2026-07-17T06:18:43.331881+00:00
- **Initiated:** 2026-07-25T06:00:34.245301+00:00
- **Completed (MVP):** 2026-07-25

## Problem / intent

Panamerica Auto needs a public-facing web presence that introduces the brand and its core automotive offerings. Prospects (retail buyers, small businesses, fleet operators) currently have no single place to scan services and start an inquiry. The longer-term “platform” idea (inventory, accounts, ops tooling) is out of scope for this pass; the goal is a credible, runnable **marketing site** that presents high-level services and a local contact path.

## Users

| Persona | Need from MVP |
|---------|----------------|
| Retail vehicle buyer | Understand sales / inspection / financing options; send an inquiry |
| Service customer | See maintenance/repair positioning; contact for service |
| Small business / fleet contact | See fleet support & parts; request a quote |
| Site owner (operator) | Local preview + clear next steps to productionize |

## Success criteria

- [x] Spec written (this file refined — problem, users, success, non-goals, MVP, layout, risks)
- [x] MVP implemented under `business/panamerica-auto/`: brand **Panamerica Auto** + multi-service section
- [x] In-repo structural tests pass against real shipped HTML/assets
- [x] Local server serves home page with brand + services content
- [x] Changes on `work/business`, committed and pushed via git_workflow
- [x] Next-iteration steps documented (seed + README)

## Non-goals

- Full multi-user platform, auth, CMS, inventory DB, or live payments
- Real email/CRM contact delivery (client-side validation + local confirmation only)
- Multi-language SEO suite, multi-location pages, production deploy/hosting pipeline
- Premature design-system polish or custom brand photography beyond a clean readable page
- Work outside `business/` and backlog seed/status paths

## MVP scope

**In:**

- Single-page static site for **Panamerica Auto**
- High-level services (assumed auto-business offerings for MVP when none were specified):
  1. Vehicle sales  
  2. Service & maintenance  
  3. Parts supply  
  4. Fleet support  
  5. Financing guidance  
  6. Inspections & prep  
- Nav + sections: Services, Why us, How it works, Contact
- Contact form with client-side validation (no backend)
- Tiny local HTTP server (`server.py`) + README run/verify docs
- Unittest structural checks on real files

**Out:** inventory listings, booking calendar, payments, CMS, multi-page SEO.

## File layout

```
business/panamerica-auto/
  index.html          # Single-page site (brand + services + contact)
  server.py           # Local static file server (stdlib)
  static/
    styles.css
    app.js            # Nav toggle + contact form validation
  tests/
    __init__.py
    test_site.py      # Structural content checks (real files)
  README.md           # Run / verify / next iteration

ops/backlog/seeds/
  panamerica-auto-website-6b74d2ca.md   # This seed/spec
```

## Risks

| Risk | Mitigation |
|------|------------|
| “Platform” wording vs basic-site MVP | Treat platform as aspirational; ship marketing MVP only |
| No real business details (address, phone, inventory) | Use clear placeholders; list next-iteration capture items |
| Agents.md TLD map omits `business/` → branch | Use OBJECTIVE branch `work/business` + git_workflow |
| Contact form looks “live” but does not deliver | Copy states local-only; wire email/CRM later |

## Next iteration (post-MVP)

1. Capture real contact info (phone, email, hours, location map)
2. Wire contact form to email or CRM
3. Sample inventory or featured vehicles page
4. Hosting/deploy (static host + custom domain)
5. Brand assets (logo, photos) and light SEO (OG tags, sitemap)
6. Optional: Spanish/English if market needs multi-language

## Notes

- Prior disk state already had a usable MVP under `business/panamerica-auto/`; this goal re-verified, refined the seed, confirmed tests/server, and closed progress on `work/business`.

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
