# Pi setup — unattended fund manager + RH refresh

## Prerequisites
- `personal-workspace` cloned/synced on the Pi
- `python3` available
- `grok` CLI installed with `~/.grok/config.toml` including:
  ```toml
  [mcp_servers.robinhood-trading]
  url = "https://agent.robinhood.com/mcp/trading"
  enabled = true
  ```
- Robinhood MCP authenticated for headless use (complete OAuth once on that host)
- Host timezone `America/New_York` (or adjust OnCalendar)

## Install timers (systemd user or system)

```bash
# Edit WorkingDirectory / paths in unit files first
sudo cp treasury/deploy/fund-manager.service treasury/deploy/fund-manager.timer /etc/systemd/system/
sudo cp treasury/deploy/rh-refresh.service treasury/deploy/rh-refresh.timer /etc/systemd/system/
# Fix paths in those files to your clone
sudo systemctl daemon-reload
sudo systemctl enable --now rh-refresh.timer
sudo systemctl enable --now fund-manager.timer
systemctl list-timers | grep -E 'fund|rh-refresh'
```

## Verify
```bash
# Dry rules path (no LLM if in band)
python3 -m treasury.fund_manager --rules-review --notify

# Manual daily script
./treasury/fund_manager_daily.sh
tail -50 treasury/snapshots/fund_manager_daily_latest.log

# RH age
python3 -c "import json;from pathlib import Path;d=json.loads(Path('treasury/snapshots/robinhood_latest.json').read_text());print(d.get('as_of'))"
```

## Behavior
| Timer | Interval | Action |
|-------|----------|--------|
| `rh-refresh` | ~3h | MCP snapshot so FCC RH trade stays green |
| `fund-manager` | weekdays ~12:30 | Rules HOLD if 40/60 ok; else Grok team review |

## Notifications
`config.json` → `notifications.ntfy_topic` (default matches Grok ntfy). Alerts on need_llm / error / stale RH — quiet on routine HOLD.
