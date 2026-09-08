# Pi setup — unattended fund manager + RH refresh

> **#518 (see `RH_PRODUCER.md`):** prism/Pi is the **live RH producer**.
> Mac launchd `com.personalworkspace.rh-refresh` must be **unloaded** (no dual-write).
> Braiins / Coinbase CLI can stay Mac-produced and pushed. RH OAuth lives on Pi.

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

### 4) Disable Mac RH launchd (no dual-writer — #518)
On Mac:
```bash
launchctl bootout gui/$(id -u)/com.personalworkspace.rh-refresh 2>/dev/null \
  || launchctl unload ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist
# Optional: also bootout fund-manager-bp-poll if that timer now runs on Pi
# Do not keep Mac as a second RH writer. Re-auth SOP is on Pi (RH_PRODUCER.md).
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
See **`RH_PRODUCER.md`** re-auth SOP. Tokens belong on prism, not Mac.
Box RH MCP is spare only. Success must move `as_of`; auth-fail must leave it.
