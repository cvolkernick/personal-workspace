# FCC feed freshness P0 (2026-08-03)

Mac is the **live producer** for Robinhood trade + Braiins. Pi is an **offline consumer** of pushed snapshots (no RH MCP / no pool token required on Pi).

## What changed

| # | Change |
|---|--------|
| 1 | Local MCP timeout **90 → 240s** (`TREASURY_RH_MCP_TIMEOUT_S` / `pi_sync.mcp_timeout_s`) |
| 2 | Pi pull accept window **12 → 6h** (matches FCC stale threshold) |
| 3 | Mac launchd RH: **`TREASURY_SKIP_PI=1`** (never re-copy stale Pi RH) |
| 4 | New **braiins** launchd every **4h** (`com.personalworkspace.braiins-refresh`) |
| 5 | After Mac success → **push** RH/Braiins/FM/treasury **+ YNAB cash** (`one_card`, `rh_checking`, `x_money`, `expenses`) → Pi (+ `financial-command/treasury_latest.json`) |

## Install / reload (Mac)

```bash
cd ~/personal-workspace-worktrees/treasury   # or monorepo root on this branch

cp treasury/deploy/com.personalworkspace.rh-refresh.plist ~/Library/LaunchAgents/
cp treasury/deploy/com.personalworkspace.braiins-refresh.plist ~/Library/LaunchAgents/

UID_N=$(id -u)
launchctl bootout gui/$UID_N/com.personalworkspace.rh-refresh 2>/dev/null || true
launchctl bootout gui/$UID_N/com.personalworkspace.braiins-refresh 2>/dev/null || true
launchctl bootstrap gui/$UID_N ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist
launchctl bootstrap gui/$UID_N ~/Library/LaunchAgents/com.personalworkspace.braiins-refresh.plist

# optional kick
launchctl kickstart -k gui/$UID_N/com.personalworkspace.braiins-refresh
# RH kick is slow (Grok+MCP); only when you want a live test:
# launchctl kickstart -k gui/$UID_N/com.personalworkspace.rh-refresh
```

## Manual

```bash
# Braiins + push
bash treasury/braiins_refresh.sh

# RH local MCP + push (skip Pi pull)
TREASURY_SKIP_PI=1 python3 -m treasury.rh_snapshot_sync --local-only --print

# Push only (when already fresh)
python3 -m treasury.rh_snapshot_sync --push-only
```

## Logs

- `treasury/snapshots/rh_refresh_latest.log`
- `treasury/snapshots/braiins_refresh_latest.log`

## Security

- No public port-forward of FCC.
- Push is SCP of **snapshot JSON only** (balances / hashrate ages) — not API tokens.
- Pool token stays on Mac (`~/.config/braiins/token`).
