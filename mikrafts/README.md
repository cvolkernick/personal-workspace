# MiKrafts LLC — public site (v1)

Public landing + catalog for **Mike Volkernick**'s 3D printing shop.
Product owner: Chris. First pass only — no pricing, cart, or purchase orders.

Open `index.html`, `catalog.html`, or `feedback.html` in a browser, or serve
this folder with any static file server. Grok publishes the own Vercel
project after merge (`docs/hosting.md`).

## Catalog

Live data is `catalog/items.json` (an array) plus JPEGs in `catalog/images/`.
The shipped catalog is empty on purpose.

To add a print later (not a mail watcher):

```bash
python3 mikrafts/ingest.py --image /path/to/photo.jpg --title "Title" --note "Optional note"
```

Email contracts: `docs/ingest.md` (`new print`) and `docs/feedback.md`
(`mikrafts feedback` → cvolkern@gmail.com). Brand notes: `docs/design.md`.

## Verify

```bash
python3 -m pip install -r mikrafts/requirements.txt
python3 -m unittest discover -s mikrafts/tests -v
```

## Hosting

This package owns `vercel.json`. It is not on the Pi intranet and is not
wired into Orchestra, FCC, Horizon, or FitDash.
