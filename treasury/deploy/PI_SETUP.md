# Pi setup — unattended fund manager + RH refresh

> **#518 (see `RH_PRODUCER.md` eng-gate sequence):**
> **1)** Pi grok + `robinhood-trading` + Chris OAuth **on Pi** (Mac tokens do not travel)
> **2)** Smoke: Pi refresh writes `robinhood_latest.json` + FCC `as_of` moves
> **3)** **Then** unload Mac `com.personalworkspace.rh-refresh` (no dual-writer)
> **4)** NTFY = Pi host + error class
> Mac re-auth is short-term only until step 2 is green.
> Wrong: disarm Mac before Pi OAuth is healthy, or invent `as_of`.

Run automation on the **Pi** so ntfy alerts and RH freshness do not depend on the Mac being awake/reauthed in launchd.

## Prerequisites
- `personal-workspace` cloned/synced on the Pi (prefer `work/treasury` until merged)
- `python3` available
- `grok` CLI installed with `~/.grok/config.toml` including:
  ```toml
  [mcp_servers.robinhood-trading]
  url = "https://agent.robinhood.com/mcp/trading"
  enabled = true
  ```
- Robinhood MCP authenticated for **headless** use **on the Pi** (Chris OAuth created there)
- Host timezone `America/New_York` (or adjust OnCalendar)
- **Do not** copy Mac Grok/RH tokens onto Pi (`sync_pi_grok_auth` is not the producer path)

## Mac → Pi cutover checklist

### 1) Sync code on Pi
```bash
# on Pi
cd /home/pi/personal-workspace   # or your clone path
git fetch origin
git checkout work/treasury       # or master when merged
git pull --ff-only
```

### 2) Fix unit paths
`rh-refresh.service` defaults to `/home/prism-agent/personal-workspace`.
Edit if your clone differs:
- `treasury/deploy/fund-manager*.service`
- `treasury/deploy/rh-refresh.service`
- `treasury/deploy/fund-manager-bp-poll.service`

Ensure systemd can find `grok` (already in the #518 unit):
```ini
Environment=PATH=/home/prism-agent/.grok/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=/home/prism-agent
Environment=FCC_HOST_TAG=prism
Environment=TREASURY_RH_ROLE=producer
```

### 3) Install RH timer on Pi (keep Mac launchd up until smoke)
```bash
sudo cp treasury/deploy/fund-manager.service treasury/deploy/fund-manager.timer /etc/systemd/system/
sudo cp treasury/deploy/rh-refresh.service treasury/deploy/rh-refresh.timer /etc/systemd/system/
sudo cp treasury/deploy/fund-manager-bp-poll.service treasury/deploy/fund-manager-bp-poll.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rh-refresh.timer
sudo systemctl enable --now fund-manager.timer
sudo systemctl enable --now fund-manager-bp-poll.timer
systemctl list-timers | grep -E 'fund|rh-refresh'
```

### 4) Smoke on Pi (required before Mac disarm)
```bash
which grok
./treasury/rh_refresh.sh
# robinhood_latest.json as_of must move; FCC RH age ≪ 6h
# auth-fail must leave as_of unchanged (no invent)
```

### 5) Then disable Mac RH launchd (no dual-writer)
**Only after step 4 is green.** On Mac:
```bash
launchctl bootout gui/$(id -u)/com.personalworkspace.rh-refresh 2>/dev/null \
  || launchctl unload ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist
```

**#555:** leave `rh-refresh` unloaded. If Mac `fund-manager-daily` /
`fund-manager-bp-poll` still page false RH / `rh_checking` stale, boot those
out (plist comments + commands in `RH_PRODUCER.md`). ntfy is also gated in
nest: `skipped` / `no_refresh_path` / timeout-with-fresh-as_of do not page.

### 6) ntfy = Pi host + error class
Alerts include **producer host** and **error class** (`FCC · RH auth_fail · prism`).
Unit sets `FCC_HOST_TAG=prism`. Override with `config.json` → `notifications.host_tag`.

**ntfy reply is not a CLI prompt** — inbound replies are not wired to Grok. Alerts only.

## Behavior
| Timer | Interval | Action |
|-------|----------|--------|
| `rh-refresh` | ~3h | MCP snapshot so FCC RH trade stays green |
| `fund-manager` | weekdays ~12:30 | Rules HOLD if 60/40 ok; else Grok team review |
| `fund-manager-bp-poll` | ~15m | If agentic cash>0 or BP>0 → full team deploy (market hours) |

## Mac local FCC + Pi snapshots
Local Mac FCC does **not** share the Pi filesystem. To keep **RH trade** green on the laptop:

1. Pi (producer) writes `treasury/snapshots/robinhood_latest.json` on its 3h timer.
2. On Mac (consumer), `python3 -m treasury.rh_snapshot_sync` pulls via SCP.
   It does **not** push `robinhood_latest.json` back (no dual-write).
3. Local Mac MCP is backup-only (`TREASURY_RH_ROLE=backup`) — laptop FCC, not SoT.

```bash
# on Mac (work/treasury) — consumer pull
TREASURY_RH_ROLE=consumer TREASURY_SKIP_LOCAL_MCP=1 python3 -m treasury.rh_snapshot_sync --print
```

Default SSH target: `prism-agent@192.168.100.98` → `/home/prism-agent/personal-workspace`.

## Notifications
`config.json` → `notifications.ntfy_topic` (or default topic).  
Alerts on need_llm / error / stale RH — quiet on routine HOLD.  
Host tag identifies which machine posted.

## Auth (producer host = Pi)
See **`RH_PRODUCER.md` step 1**. Chris OAuth is created on Pi. Mac tokens do
not travel. Box RH MCP is spare only. Success must move `as_of`; auth-fail
must leave it. Mac re-auth is short-term only until Pi smoke is green.
