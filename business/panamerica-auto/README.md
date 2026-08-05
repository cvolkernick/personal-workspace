# Panamerica Auto Website

Business website MVP for **Panamerica Auto** — a high-level overview of automotive services (sales, service, parts, fleet, financing, inspections).

## Run

From this directory:

```bash
python3 server.py
# optional: python3 server.py --port 8765
```

Open: [http://127.0.0.1:8765/](http://127.0.0.1:8765/)

Or from the repo root:

```bash
python3 business/panamerica-auto/server.py
```

You can also open `index.html` directly in a browser (CSS/JS load as relative paths).

## Verify

```bash
# From repo root
python3 -m unittest discover -s business/panamerica-auto/tests -v

# Or from this directory
python3 -m unittest discover -s tests -v
```

Manual checklist:

1. Home hero loads with brand **Panamerica Auto**.
2. Six service cards under **High-level services**.
3. Nav links jump to Services / Why us / Process / Contact.
4. Contact form shows an error if fields are empty; success message when filled.

## Layout

```
business/panamerica-auto/
  index.html          # Single-page site
  server.py           # Local static server
  static/
    styles.css
    app.js            # Nav toggle + contact form validation
  tests/
    test_site.py      # Structural content checks
  README.md
```

## Next iteration (out of MVP)

- Real contact delivery (email/CRM)
- Location / hours / phone
- Inventory or sample listings
- Multi-page SEO and language variants
