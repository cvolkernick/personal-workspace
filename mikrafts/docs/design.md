# MiKrafts v1 design

Public customer site for **MiKrafts LLC** (Mike Volkernick, Owner).
Product owner: Chris. Aesthetic template: Mike's business card.

## Brand (from the card — do not reinvent)

- Split **white** (top) / **royal plum** (bottom)
- **Charcoal** text on white; **silver** text on plum
- Accents: **gold** + **lavender**
- Circle/shield mark: charcoal 3D-printer nozzle printing a gold cube and a
  lavender cube, italic **MiKrafts** wordmark (M/K caps) on a black banner
- Clean medium-weight sans-serif

Copy used exactly:

- MiKrafts LLC
- 3D Printing Services
- 2526 NW 11th Street, Cape Coral, FL 33993
- Mike Volkernick, Owner
- MiKraftsLLC@gmail.com
- 239-989-4878

Landing also names the three print kinds the product owner asked for:
custom / proofs of concept / products. No prices, quotes, or carts.

## Surfaces

| File | Role |
|------|------|
| `index.html` | Split-hero card + what we print + contact |
| `catalog.html` | Grid from `catalog/items.json` |
| `feedback.html` | Mailto form to Chris (`mikrafts feedback`) |
| `static/styles.css` | Shared card palette |
| `static/catalog.js` | Renders cards or the honest empty state |
| `static/feedback.js` | Builds the no-secret mailto URL |
| `catalog/items.json` | Array of live prints (starts empty) |
| `catalog/images/` | Processed JPEGs |

## Example catalog card (NOT live)

The following row is for design review and tests only. It must never be
appended to the shipped `catalog/items.json`.

```json
{
  "id": "example-print-testdoc",
  "title": "Example print",
  "note": "EXAMPLE — design docs and tests only. Not a live catalog row.",
  "image": "tests/fixtures/example-print.jpg",
  "added": "2026-08-20"
}
```

Rendered contract (same markup as `static/catalog.js` / `ingest.render_catalog_cards`):

```html
<article class="catalog-card">
  <img src="tests/fixtures/example-print.jpg" alt="Example print">
  <h2 class="catalog-title">Example print</h2>
  <p class="catalog-note">EXAMPLE — design docs and tests only. Not a live catalog row.</p>
  <time class="catalog-added" datetime="2026-08-20">Added 2026-08-20</time>
</article>
```

Honest empty (live catalog with zero items):

```html
<p class="catalog-empty">No prints in the catalog yet.</p>
```

## Hosting

Own `mikrafts/vercel.json`. Separate Vercel project (Grok publishes; see
`docs/hosting.md`). Not on the Pi intranet. Not in Orchestra, FCC, Horizon,
or FitDash nav. FitDash `resistance-dashboard/vercel.json` and
`vercel-ignore-paths.txt` stay untouched.
