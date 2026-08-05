# IoT

Local control for home devices (currently **Wiz** entryway lights) plus a small dashboard.

## Quick start

```bash
# From monorepo root
pip3 install -r iot/requirements.txt

# Dashboard UI on Mac, API proxied to Pi (iot/backend.json → prism-gateway)
python3 iot/server.py
# Force direct Mac→bulbs (no Pi):
python3 iot/server.py --local
# Explicit backend:
python3 iot/server.py --backend http://192.168.100.98:8780

# CLI (always direct from this machine)
python3 iot/wiz-lights/wiz-lights.py all cyan
python3 iot/wiz-lights/wiz-lights.py entryway1 off
```

**Architecture (default):** Mac serves the web UI on `:8780` and reverse-proxies `/api/*` to the always-on Pi (`prism-gateway`). Sunrise/sunset run on the Pi so Mac sleep does not miss routines.

## Layout

| Path | Purpose |
|------|---------|
| `server.py` / `index.html` | Local dashboard + JSON API |
| `control.py` | Pure registry / intent / merge helpers |
| `wiz_adapter.py` | `pywizlight` + test fake transport |
| `discover.py` | mDNS / LAN notes |
| `wiz-lights/bulbs.json` | Named bulbs (IP + MAC) |
| `wiz-lights/wiz-lights.py` | CLI |
| `INVENTORY.md` | What works, probe results, how to test |
| `tests/` | Unit + HTTP tests |

## Groups

`groups.json` defines room toggles:

- **entryway** → entryway1–4  
- **livingroom** → livingroom1–2  

Control with `target: "entryway"` / `"livingroom"` (or `"all"`).

## Sunrise / sunset routines

`schedule.json` (requires lat/lon — set in the UI or via API):

- **Sunset** → all lights **on** (**magenta**)  
- **Sunrise** → all lights **off**  

**The process must stay running** (Mac sleep = missed routines). Prefer an always-on host:

```bash
# Headless worker only (Pi / server)
PYTHONPATH=. python3 iot/worker.py --interval 30

# Or dashboard without opening a browser
python3 iot/server.py --host 0.0.0.0 --port 8780 --no-browser
```

**Deploy to a Raspberry Pi over SSH:** see [deploy/README.md](./deploy/README.md).

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | Service + registry summary |
| GET | `/api/devices` | Configured devices (`?status=1` for live state) |
| GET | `/api/groups` | Room groups |
| GET | `/api/schedule` | Routines + today's sun times |
| GET | `/api/presets` | Color names |
| GET | `/api/discover` | Wiz discovery + mDNS merge |
| POST | `/api/control` | `{"target":"entryway\|livingroom\|all\|device","color":"…"}` |
| POST | `/api/schedule/location` | `{"latitude", "longitude", "timezone?"}` |
| POST | `/api/schedule/routine` | Patch routine `{id, enabled?, color?, …}` |
| POST | `/api/schedule/run` | Run a routine now `{id, mark?}` |
| POST | `/api/status` | Live status for all or one target |

## Tests

```bash
python3 -m unittest discover -s iot/tests -v
```

See **[INVENTORY.md](./INVENTORY.md)** for live probe notes and known network devices.
