# Panamerica Auto — Website (MVP)

Basic professional static website for Panamerica Auto featuring high-level services.

## Launch (local)

Simplest (recommended):

```bash
cd business
python3 -m http.server 8086
# then open http://localhost:8086/
```

Or use the convenience launcher:

```bash
bash business/start.command
```

The site is fully self-contained (`index.html` + inline CSS). It renders correctly when opened directly via `file://` in any modern browser and from any static file server.

## Featured High-Level Services

- **New & Pre-Owned Sales** — Curated passenger vehicles, SUVs, trucks with transparent pricing and trade-ins.
- **Maintenance & Repair** — Diagnostics, scheduled service, brakes, suspension, major repairs by trained techs using genuine parts.
- **Import & Logistics** — Vehicle importing, compliance, transport coordination, and delivery.
- **Genuine Parts & Accessories** — OEM + quality aftermarket; fast sourcing.
- **Fleet & Commercial Solutions** — Acquisition, maintenance programs, and support for business fleets.

## File layout

```
business/
  index.html     # the complete site
  README.md
  start.command  # launcher
  tests/
    test_site.py # reads real index.html from disk
```

## Next steps (post-MVP ideas)

See the seed plan `ops/backlog/seeds/panamerica-auto-website-6b74d2ca.md` for detailed next-iteration items (contact form stub, sample inventory teaser, deployment, photos, etc.).

## Verification

- Open index.html directly or via server — hero, services grid, about, and contact sections must be visible.
- Tests under `tests/` assert the shipped HTML contains "Panamerica Auto" + service keywords.

This MVP satisfies the basic website scope. All changes tracked on `work/business`.