# FCC feed freshness

**#518 (2026-09):** Robinhood trade snapshot SoT is **prism/Pi**, not Mac.
See `RH_PRODUCER.md` for install, Mac disarm, re-auth SOP, and smoke notes.

Mac remains the live producer for **Coinbase CLI + Braiins pool token**.
Pi FCC is still an offline consumer of those pushed files. RH is the exception.

## What changed

| # | Change |
|---|--------|
| 1 | Local MCP timeout **90 → 240s** (`TREASURY_RH_MCP_TIMEOUT_S` / `pi_sync.mcp_timeout_s`) |
| 2 | Pi pull accept window **12 → 6h** (matches FCC stale / NTFY threshold) |
| 3 | **#518** RH producer = prism/Pi (`TREASURY_RH_ROLE=producer`). Mac launchd unloaded or consumer-only |
| 4 | Braiins launchd every **4h** (`com.personalworkspace.braiins-refresh`) — still Mac |
| 5 | After Mac **non-RH** success → push CB/YNAB/Sheet/Braiins/treasury → Pi. **Not** `robinhood_latest.json` |
| 6 | **Coinbase + Solana** Mac producer hourly (`com.personalworkspace.cb-solana-refresh`) |

## Install / reload

### Pi (RH SoT)

```bash
sudo cp treasury/deploy/rh-refresh.service treasury/deploy/rh-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rh-refresh.timer
```

### Mac (disarm RH launchd — no dual-writer)

```bash
UID_N=$(id -u)
launchctl bootout gui/$UID_N/com.personalworkspace.rh-refresh 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist
```

Keep Braiins + CB/Solana launchd if those Mac producers are still wanted:

```bash
cd ~/personal-workspace-worktrees/treasury   # or monorepo root

cp treasury/deploy/com.personalworkspace.braiins-refresh.plist ~/Library/LaunchAgents/
cp treasury/deploy/com.personalworkspace.cb-solana-refresh.plist ~/Library/LaunchAgents/

UID_N=$(id -u)
launchctl bootout gui/$UID_N/com.personalworkspace.braiins-refresh 2>/dev/null || true
launchctl bootout gui/$UID_N/com.personalworkspace.cb-solana-refresh 2>/dev/null || true
launchctl bootstrap gui/$UID_N ~/Library/LaunchAgents/com.personalworkspace.braiins-refresh.plist
launchctl bootstrap gui/$UID_N ~/Library/LaunchAgents/com.personalworkspace.cb-solana-refresh.plist
```

## Manual

```bash
# Coinbase + Solana + push (RH file excluded from push)
bash treasury/cb_solana_refresh.sh

# Braiins + push
bash treasury/braiins_refresh.sh

# RH on producer (Pi)
TREASURY_RH_ROLE=producer TREASURY_SKIP_PI=1 python3 -m treasury.rh_snapshot_sync --local-only --print

# RH on Mac consumer (pull Pi only)
TREASURY_RH_ROLE=consumer TREASURY_SKIP_LOCAL_MCP=1 python3 -m treasury.rh_snapshot_sync --print

# Push only (non-RH snapshots)
python3 -m treasury.rh_snapshot_sync --push-only
```

## Logs

- `treasury/snapshots/rh_refresh_latest.log`
- `treasury/snapshots/rh_producer_status.json`
- `treasury/snapshots/braiins_refresh_latest.log`
- `treasury/snapshots/cb_solana_refresh_latest.log`

## Security

- No public port-forward of FCC.
- Push is SCP of **snapshot JSON only** (balances / hashrate ages) — not API tokens.
- Pool token stays on Mac (`~/.config/braiins/token`).
- RH OAuth tokens stay on **prism** (producer). Box MCP is spare only.
- Producer does not place orders.
