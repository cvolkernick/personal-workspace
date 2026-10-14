---
title: "Install Pi heartbeat timer (Orchestra)"
tags: [ops, pi, orchestra, heartbeat, systemd]
status: active
created: 2026-08-06
---

# Install Pi heartbeat → Orchestra

**Issue:** #50  
**Contract:** nest `RESEARCH/HEARTBEAT_CONTRACT_V0.md`  
**Collector:** `orchestra/collect_heartbeat.py`  
**Artifact:** `orchestra/data/heartbeat/latest.json`  
**API:** `GET /api/heartbeat` on orchestra-dashboard (`:8790`)

## What it does

Every **60s** on prod Pi (`prism-gateway`):

1. `systemctl --user is-active` for each watched unit  
2. Loopback HTTP health probe (`/api/health` or FitDash `/api/healthz`)  
3. Atomic write of schema v1 JSON to `orchestra/data/heartbeat/latest.json`  
4. Orchestra serves the file at `GET /api/heartbeat`

**Critical services** (any unhealthy → document `ok: false`):  
orchestra, workflow, horizon, resistance (FitDash), financial-command  

**Yellow** (degraded only): iot-dashboard, b2 (optional unit)

**Lock-in:** when a health endpoint was probed, `health_ok` outweighs unit `active` for health judgment.

## Pi install (systemd user timer)

```bash
# On prism-gateway as prism-agent, after monorepo has this branch/master:
mkdir -p ~/.config/systemd/user ~/personal-workspace/orchestra/data/heartbeat

cp ~/personal-workspace/deploy/units/pi-heartbeat.service \
   ~/personal-workspace/deploy/units/pi-heartbeat.timer \
   ~/.config/systemd/user/

systemctl --user daemon-reload
systemctl --user enable --now pi-heartbeat.timer
systemctl --user start pi-heartbeat.service   # one immediate scrape
systemctl --user list-timers | grep heartbeat
systemctl --user status pi-heartbeat.service --no-pager

# Verify
python3 -c "import json; print(json.load(open('orchestra/data/heartbeat/latest.json'))['ok'])"
curl -sS http://127.0.0.1:8790/api/heartbeat | head -c 400; echo
```

Requires monorepo at `~/personal-workspace` (user unit paths use `%h/personal-workspace`).

## Manual one-shot

```bash
cd ~/personal-workspace
python3 orchestra/collect_heartbeat.py --print
```

## Observed prod units (2026-08-06, prism-gateway)

Recorded while implementing #50:

| Unit | Active (user) |
|------|----------------|
| orchestra-dashboard.service | active |
| workflow-dashboard.service | active |
| horizon-dashboard.service | active |
| resistance-dashboard.service | active |
| financial-command.service | active |
| holistic-dashboard.service | active (not on v0 critical set) |
| iot-dashboard.service | **inactive** (yellow; health may still answer if process elsewhere) |
| b2.service | **not installed** (optional yellow) |
| panamerica-auto.service | **retired** (legacy site archived; live site is Vercel Next.js app) |
| workflow-scheduler.service | failed (not on v0 set) |

## Orchestra restart

`GET /api/heartbeat` is served by `orchestra-dashboard.service`. After deploying server.py changes:

```bash
systemctl --user restart orchestra-dashboard.service
curl -sS http://127.0.0.1:8790/api/heartbeat | python3 -m json.tool | head
```

## Durable path

`orchestra/data/heartbeat/` is runtime state. Prefer preserving it across `workspace-sync` the same way other `*_latest.json` paths are snapshotted.

## Host vitals + threshold alerts (v0.2.0)

`latest.json` now carries a `vitals` block (hostname, disk % for `/`, load per
core 1m/5m/15m, mem %, SoC temp °C, `throttled` flag + raw hex, uptime) plus
`status` (`green`/`amber`/`red`) and `alerts[]`.

Thresholds (`orchestra/heartbeat_alerts.py`; override with env or a JSON file
named by `HEARTBEAT_THRESHOLDS`):

| Check | Default | Env |
|-------|---------|-----|
| Disk `/` | amber ≥80%, red ≥90% | `HEARTBEAT_DISK_WARN_PCT`, `HEARTBEAT_DISK_CRIT_PCT` |
| Load per core | 15-min loadavg / cores > 1.5 (= sustained 15 min) | `HEARTBEAT_LOAD_PER_CORE` |
| Temp | ≥75 °C | `HEARTBEAT_TEMP_C` |
| Memory used | amber ≥85%, red ≥95% | `HEARTBEAT_MEM_WARN_PCT`, `HEARTBEAT_MEM_CRIT_PCT` |
| Unit down | 2 consecutive checks | `HEARTBEAT_UNIT_DOWN_CHECKS` |

**Transport (#704):** a comment on #701 only, silent to the owner, posted only
when the alert set changes (new / level change / recovery). State lives in
`orchestra/data/heartbeat/alerts_state.json`. The token comes from
`GITHUB_TOKEN`/`GH_TOKEN` (env, or `HEARTBEAT_ENV_FILE`, default
`~/.config/workflow-scheduler.env`; values never logged). Without a token the
collector logs `posted=False no-github-token` and keeps going. A failed POST is
retried on the next check. `HEARTBEAT_ALERTS=0` or `--no-alerts` disables it.

## Second node (finley-gateway)

finley has no Orchestra, so it uses a per-node watchlist plus a small
read-only server for `/api/heartbeat`:

```bash
cd ~/personal-workspace
cp deploy/units/pi-heartbeat.service deploy/units/pi-heartbeat.timer \
   deploy/units/pi-heartbeat-serve.service ~/.config/systemd/user/
mkdir -p ~/.config/systemd/user/pi-heartbeat.service.d
cat > ~/.config/systemd/user/pi-heartbeat.service.d/finley.conf <<'CONF'
[Service]
Environment=HEARTBEAT_HOST=finley-gateway
Environment=HEARTBEAT_HOST_ROLE=backup
Environment=HEARTBEAT_WATCHLIST=%h/personal-workspace/deploy/pi-heartbeat/watchlist.finley.json
CONF
systemctl --user daemon-reload
systemctl --user enable --now pi-heartbeat.timer pi-heartbeat-serve.service
curl -s http://127.0.0.1:8797/api/heartbeat
```

The finley watchlist covers `b2` (:8792) and the
`b2-puller` backup via freshness of `~/b2-pulls/prism/MANIFEST.json`
(must be under 7800 s old and have `"ok": true`).
