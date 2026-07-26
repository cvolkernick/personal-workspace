# Goal seed: Panamerica Auto Website

- **Backlog id:** `6b74d2ca-d0ca-4c87-8c6c-60777e099a88`
- **Priority:** medium
- **Status:** done (MVP shipped)
- **Area:** business
- **Created:** 2026-07-17T06:18:43.331881+00:00
- **Initiated:** 2026-07-25T06:00:34.245301+00:00
- **Completed (MVP):** 2026-07-25

## Problem / intent

Panamerica Auto needs a public-facing web presence that introduces the brand as a **rental and fleet operations** business — not a dealership. Core work: auto rentals via **Turo** and **private rentals**, plus **fleet management** (rental management, maintenance and service coordination). Prospects (renters, vehicle owners, partner fleets) need a single place to scan services and inquire. Reference peer: [SafeWheels Rentals SWFL](https://safewheelsrentalsswfl.com/) (Turo host + co-hosting; Panamerica has co-hosted with them). Longer-term “platform” remains out of scope; MVP is a marketing site with correct positioning.

## Users

| Persona | Need from MVP |
|---------|----------------|
| Turo / private renter | Understand rental options; send an inquiry |
| Vehicle owner / partner | See fleet / rental management and maintenance coordination |
| Co-host / peer operator | See partnership posture (e.g. past SafeWheels co-host work) |
| Site owner (operator) | Local preview, Pi deploy, clear next steps |

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
- High-level services (corrected 2026-07-25 — rentals/fleet, not sales):
  1. Turo rentals  
  2. Private rentals  
  3. Fleet management  
  4. Rental management  
  5. Maintenance coordination  
  6. Service coordination  
- Explicit non-offer: vehicle sales / dealership positioning
- Nav + sections: Services, Why us, How it works, Contact
- Contact form with client-side validation (no backend)
- Tiny local HTTP server (`server.py`) + Pi deploy (`:8795`) + README
- Unittest structural checks on real files (incl. anti-sales positioning)

**Out:** inventory listings, live Turo embed, booking calendar, payments, CMS, multi-page SEO.

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

1. Capture real contact info (phone, email, hours, service area)
2. Wire contact form to email or CRM
3. Link or embed live Turo profile / featured rental vehicles
4. Co-host / owner intake page (earnings estimate form) inspired by SafeWheels-style co-hosting
5. Custom domain + HTTPS reverse proxy
6. Brand assets (logo, fleet photos) and light SEO

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
