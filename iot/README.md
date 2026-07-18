# IoT

Local control for home devices (currently **Wiz** entryway lights) plus a small dashboard.

## Quick start

```bash
# From monorepo root
pip3 install -r iot/requirements.txt

# Dashboard (http://127.0.0.1:8780/)
python3 iot/server.py
# or
python3 iot/server.py --port 8780 --no-browser

# CLI
python3 iot/wiz-lights/wiz-lights.py all cyan
python3 iot/wiz-lights/wiz-lights.py entryway1 off
```

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

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | Service + registry summary |
| GET | `/api/devices` | Configured devices (`?status=1` for live state) |
| GET | `/api/presets` | Color names |
| GET | `/api/discover` | Wiz discovery + mDNS merge |
| POST | `/api/control` | `{"target":"entryway1\|all","color":"cyan","brightness":180}` |
| POST | `/api/status` | Live status for all or one target |

## Tests

```bash
python3 -m unittest discover -s iot/tests -v
```

See **[INVENTORY.md](./INVENTORY.md)** for live probe notes and known network devices.
