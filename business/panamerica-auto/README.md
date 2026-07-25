# Panamerica Auto Website

Business website MVP for **Panamerica Auto** — a high-level overview of automotive services (sales, service, parts, fleet, financing, inspections).

## Run (local)

From this directory:

```bash
python3 server.py
# optional: python3 server.py --port 8795
```

Open: [http://127.0.0.1:8795/](http://127.0.0.1:8795/)

Or from the repo root:

```bash
python3 business/panamerica-auto/server.py
```

You can also open `index.html` directly in a browser (CSS/JS load as relative paths).

## Run from this Mac terminal (Pi preferred)

Double-click or:

```bash
bash business/panamerica-auto/start.command
```

Opens the always-on Pi site (`http://192.168.100.98:8795/`) when reachable; otherwise starts a local server.

Override: `PANAMERICA_URL=http://other-host:8795/ bash business/panamerica-auto/start.command`

## Deploy to Raspberry Pi

```bash
# From monorepo root (SSH key required):
bash business/panamerica-auto/deploy/install_remote.sh prism-agent@192.168.100.98
# or via monorepo deploy:
bash deploy/install_remote.sh prism-agent@192.168.100.98 --only panamerica
```

- **Port:** `8795` (bound `0.0.0.0` on the Pi)
- **Unit:** `panamerica-auto.service` (systemd user)
- **URL:** http://192.168.100.98:8795/

```bash
ssh prism-agent@192.168.100.98 'systemctl --user status panamerica-auto'
curl -sS http://192.168.100.98:8795/ | head
```

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
5. Pi URL responds with brand + services after deploy.

## Layout

```
business/panamerica-auto/
  index.html          # Single-page site
  server.py           # Local / Pi static server (default :8795)
  start.command       # Open Pi site (or local fallback)
  static/
    styles.css
    app.js
  tests/
    test_site.py
  deploy/
    install_remote.sh
    panamerica-auto.service
  README.md
```

## Next iteration (out of MVP)

- Real contact delivery (email/CRM)
- Location / hours / phone
- Inventory or sample listings
- Multi-page SEO and language variants
- Custom domain / HTTPS reverse proxy
