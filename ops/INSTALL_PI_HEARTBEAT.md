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
| panamerica-auto.service | activating/auto-restart (not on v0 set) |
| workflow-scheduler.service | failed (not on v0 set) |

## Orchestra restart

`GET /api/heartbeat` is served by `orchestra-dashboard.service`. After deploying server.py changes:

```bash
systemctl --user restart orchestra-dashboard.service
curl -sS http://127.0.0.1:8790/api/heartbeat | python3 -m json.tool | head
```

## Durable path

`orchestra/data/heartbeat/` is runtime state. Prefer preserving it across `workspace-sync` the same way other `*_latest.json` paths are snapshotted.
