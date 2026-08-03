# Pi setup — unattended fund manager + RH refresh

> **P0 2026-08-03 (see `FEEDS_P0.md`):** Mac is the **live** RH/Braiins producer.
> Pi FCC runs **offline** and receives snapshots via Mac **push** after refresh.
> Full Pi-side RH MCP timers below remain optional cutover if you later run grok+MCP on the Pi.
> Prefer Mac launchd (`com.personalworkspace.rh-refresh` + `braiins-refresh`) for daily freshness.

Run automation on the **Pi** so ntfy alerts and deploys do not depend on the Mac being awake/reauthed in launchd.

## Prerequisites
- `personal-workspace` cloned/synced on the Pi (prefer `work/treasury` until merged)
- `python3` available
- `grok` CLI installed with `~/.grok/config.toml` including:
  ```toml
  [mcp_servers.robinhood-trading]
  url = "https://agent.robinhood.com/mcp/trading"
  enabled = true
  ```
- Robinhood MCP authenticated for **headless** use on the Pi
- Host timezone `America/New_York` (or adjust OnCalendar)
- Optional: Mac → Pi auth sync (`com.personalworkspace.sync-pi-grok-auth` / `projects-dashboard/sync_pi_grok_auth.sh`) after laptop reauths

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
Edit if your clone is not `/home/pi/personal-workspace`:
- `treasury/deploy/fund-manager*.service`
- `treasury/deploy/rh-refresh.service`
- `treasury/deploy/fund-manager-bp-poll.service`

Ensure systemd can find `grok`:
```ini
# optional in [Service]
Environment=PATH=/home/pi/.grok/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=/home/pi
Environment=FCC_HOST_TAG=pi
```

### 3) Install timers on Pi
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

### 4) Disable Mac launchd (avoid double ntfy / double trades)
On Mac:
```bash
launchctl bootout gui/$(id -u)/com.personalworkspace.fund-manager-bp-poll 2>/dev/null \
  || launchctl unload ~/Library/LaunchAgents/com.personalworkspace.fund-manager-bp-poll.plist 2>/dev/null || true
launchctl bootout gui/$(id -u)/com.personalworkspace.rh-refresh 2>/dev/null \
  || launchctl unload ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist 2>/dev/null || true
# Keep sync-pi-grok-auth loaded so Pi receives Mac reauths
```

### 5) Verify on Pi
```bash
which grok
python3 -m treasury.fund_manager --rules-review --notify
./treasury/fund_manager_bp_poll.sh
# force outside hours:
FM_BP_POLL_FORCE=1 ./treasury/fund_manager_bp_poll.sh
tail -50 treasury/snapshots/fund_manager_bp_poll_latest.log
```

### 6) ntfy host tags
Alerts include hostname in **title** and **body** (`[hostname] …`) so you can tell Pi vs Mac.
Override with env `FCC_HOST_TAG=pi` or `config.json` → `notifications.host_tag`.

**ntfy reply is not a CLI prompt** — inbound replies are not wired to Grok. Alerts only.

## Behavior
| Timer | Interval | Action |
|-------|----------|--------|
| `rh-refresh` | ~3h | MCP snapshot so FCC RH trade stays green |
| `fund-manager` | weekdays ~12:30 | Rules HOLD if 40/60 ok; else Grok team review |
| `fund-manager-bp-poll` | ~15m | If agentic cash>0 or BP>0 → full team deploy (market hours) |

## Mac local FCC + Pi snapshots
Local Mac FCC does **not** share the Pi filesystem. To keep **RH trade** green on the laptop:

1. Pi continues writing `treasury/snapshots/robinhood_latest.json` on its schedule.
2. On Mac, `python3 -m treasury.rh_snapshot_sync` (also used by `rh_refresh.sh` and FCC **Refresh**):
   - **First** SSH/SCP pull from Pi (`pi_sync` in `treasury/config.json`, or `TREASURY_PI_SSH` / `TREASURY_PI_ROOT`)
   - **If Pi unreachable / missing / too stale** → local Grok + Robinhood MCP fallback

```bash
# on Mac (work/treasury)
python3 -m treasury.rh_snapshot_sync --print
# force local MCP only:
TREASURY_SKIP_PI=1 python3 -m treasury.rh_snapshot_sync
```

Default SSH target: `prism-agent@192.168.100.98` → `/home/prism-agent/personal-workspace`.

## Notifications
`config.json` → `notifications.ntfy_topic` (or default topic).  
Alerts on need_llm / error / stale RH — quiet on routine HOLD.  
Host tag identifies which machine posted.

## Auth after Mac reauth
1. Complete Robinhood MCP auth on Mac (or Pi)
2. Run / wait for `sync_pi_grok_auth` so Pi gets tokens
3. Confirm on Pi: `./treasury/rh_refresh.sh` succeeds without “grok not available”
