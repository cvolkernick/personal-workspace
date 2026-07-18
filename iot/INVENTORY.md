# IoT MVP inventory & test feedback

**Date:** 2026-07-17  
**Scope:** `personal-workspace/iot/` — Wiz entryway bulbs + local control dashboard.

## What exists (MVP)

| Piece | Path | Role |
|--------|------|------|
| Bulb registry | `wiz-lights/bulbs.json` | Named devices → IP + MAC |
| CLI | `wiz-lights/wiz-lights.py` | `python3 iot/wiz-lights/wiz-lights.py <name\|all> <color\|off>` |
| Pure control helpers | `control.py` | Load registry, color intents, merge discovery |
| Network adapter | `wiz_adapter.py` | `pywizlight` transport + injectable `FakeTransport` |
| Discovery extras | `discover.py` | mDNS browse (dns-sd) + broadcast guess |
| Dashboard | `server.py` + `index.html` | Local HTTP UI/API on port **8780** |
| Tests | `tests/test_control.py`, `tests/test_server.py` | Pure + HTTP gates |

### Configured devices (after IP fix)

| Name | IP | MAC | Notes |
|------|-----|-----|--------|
| entryway1 | 192.168.100.106 | 6c2990089296 | Reachable; control OK |
| entryway2 | 192.168.100.118 | 6c2990d5075a | Reachable |
| entryway3 | **192.168.100.184** | 6c29904e244e | Was `.185` in old config; DHCP moved it |
| entryway4 | 192.168.100.207 | 6c29903d3195 | Reachable |

### Color presets

`white`, `red`, `green`, `blue`, `cyan`, `magenta`, `yellow`, `orange`, `purple`, `warm`, `off`

### Dependency

- **`pywizlight`** (≥0.6) — required for live control/discovery. Install: `pip3 install -r iot/requirements.txt`
- Was **not** installed in the default environment at plan time; installed for live probes.

## How to test

```bash
# From monorepo root
pip3 install -r iot/requirements.txt

# Unit tests (no hardware required)
python3 -m unittest discover -s iot/tests -v

# CLI (live LAN)
python3 iot/wiz-lights/wiz-lights.py entryway1 cyan
python3 iot/wiz-lights/wiz-lights.py all off

# Dashboard
python3 iot/server.py --port 8780 --no-browser
# → http://127.0.0.1:8780/
# API: GET /api/health | /api/devices | /api/devices?status=1 | /api/discover
#      POST /api/control  {"target":"entryway1","color":"cyan","brightness":180}
```

## Live probe results (2026-07-17)

### ICMP

- entryway1/2/4: ping OK (~70–165 ms)
- entryway3 at **.185**: 100% loss (stale IP)

### Wiz UDP discovery (`pywizlight.discovery`, broadcast `192.168.100.255`)

Found **4** bulbs:

| IP | MAC |
|----|-----|
| 192.168.100.207 | 6c29903d3195 |
| 192.168.100.118 | 6c2990d5075a |
| 192.168.100.106 | 6c2990089296 |
| 192.168.100.184 | 6c29904e244e ← matches entryway3 MAC |

### Control

- `turn_on` cyan on `192.168.100.106`: **OK**
- State read works; note: `updateState()` may return a **list** of `PilotParser` on pywizlight 0.6.4 — adapter normalizes this.

### Additional LAN devices (mDNS, not controlled by this MVP)

| Service | Instance | Controllable here? |
|---------|----------|--------------------|
| `_googlecast._tcp` | Nest-Audio-… | No (Cast not wired) |
| `_vivint-admin._tcp` | VivintSmarthub-C0C955 | No |
| `_airplay` / `_raop` / `_companion-link` | (present) | No |
| `_clawdbot-gw` | (present) | No |

No HomeKit HAP (`_hap._tcp`) instances observed in short browse. No auto-added non-Wiz control path (by design for MVP).

## Pass / fail summary

| Check | Result |
|-------|--------|
| Registry load + CLI wiring | Pass |
| Unit tests (fake transport) | Pass (see CI/local run) |
| Live Wiz discovery | Pass — 4 bulbs |
| Live control (entryway1) | Pass |
| entryway3 IP accuracy | Fixed (.185 → .184) |
| Non-Wiz control | N/A — discovery notes only |
| Dashboard launch | `python3 iot/server.py` |

## Honest limitations

- DHCP can move bulb IPs; re-run Discover or check MAC match (`ip_mismatch` flag on merge).
- UDP discovery needs same LAN / not blocked by client isolation.
- mDNS devices are informational only until a driver is added.
- Dashboard binds **127.0.0.1** only (no auth, not for internet exposure).
