# Panamerica Auto Website

Business website MVP for **Panamerica Auto** — Turo & private auto rentals plus fleet management (rental ops, maintenance and service coordination). Not a dealership / vehicle sales site.

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

1. Home hero loads with brand **Panamerica Auto** and rental/fleet positioning.
2. Six service cards: Turo rentals, Private rentals, Fleet management, Rental management, Maintenance coordination, Service coordination.
3. Site does **not** present vehicle sales as a primary offering.
4. Nav links jump to Services / Why us / Process / Contact; Cybercab goes to `/cybercab-fleet.html`.
5. Contact form shows an error if fields are empty; success message when filled.
6. Pi URL responds with brand + services after deploy.
7. `/cybercab-fleet.html` shows the DEMO banner, `noindex`, three scenario cards, and an interest form — no “Invest now” CTA.

## Cybercab fleet interest demo

`cybercab-fleet.html` is a **demo** — not a live securities offering, not a Tesla order form.

- Persistent `DEMO — not an offer to sell securities` banner
- `noindex,nofollow`
- Interest form only (client-side, same pattern as contact)
- Unit economics from `~/Projects/tesla-robotaxi-pitch` shown as a range; **base case does not beat Turo**
- Tesla’s form is [tesla.com/robotaxi/interest](https://www.tesla.com/robotaxi/interest) (theirs)
- Hero is a real Cybercab photograph (Wikimedia Commons, CC BY 4.0); credit in the figcaption and `static/img/CREDITS.md`

Do **not** deploy to the Pi (`:8795`) until Chris explicitly says so. The DEMO banner and noindex stay if it does ship.

Local: [http://127.0.0.1:8795/cybercab-fleet.html](http://127.0.0.1:8795/cybercab-fleet.html)

## Layout

```
business/panamerica-auto/
  index.html              # Home — rentals / fleet ops
  cybercab-fleet.html     # SWFL Cybercab interest demo (noindex)
  server.py               # Local / Pi static server (default :8795)
  start.command           # Open Pi site (or local fallback)
  static/
    styles.css
    app.js
    img/tesla-cybercab-hero.jpg
    img/CREDITS.md
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
- Counsel-led offering path (only if Chris opens it) — this HTML is not that path
