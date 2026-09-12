# FCC feed freshness

**#518 (2026-09):** Robinhood trade snapshot SoT is **prism/Pi**, not Mac.
Cutover order is locked in `RH_PRODUCER.md`: **(1) Pi grok + MCP + Chris OAuth
on Pi (Mac tokens do not travel) → (2) smoke writes snapshot + FCC as_of moves
→ (3) then stop Mac launchd → (4) NTFY = Pi host + error class.**
Mac re-auth is short-term only until step 2 is green. Do not disarm Mac before
Pi OAuth is healthy. Do not invent `as_of`.

Mac remains the live producer for **Coinbase CLI**. Braiins pool token and
snapshot live on **prism/Pi** (#689). Pi FCC is an offline consumer of Mac-pushed
CB files. RH + Braiins are Pi-produced.

## What changed

| # | Change |
|---|--------|
| 1 | Local MCP timeout **90 → 240s** (`TREASURY_RH_MCP_TIMEOUT_S` / `pi_sync.mcp_timeout_s`) |
| 2 | Pi pull accept window **12 → 6h** (matches FCC stale / NTFY threshold) |
| 3 | **#518** RH producer = prism/Pi (`TREASURY_RH_ROLE=producer`). Mac launchd unloaded or consumer-only |
| 4 | **#689** Braiins producer = prism/Pi (`braiins-refresh.timer` every **4h**). Mac launchd retired. Token at `~/.config/braiins/token` on prism-agent, never `treasury/config.json`. |
| 5 | After Mac **non-RH / non-Braiins** success → push CB/YNAB/Sheet/treasury → Pi. **Not** `robinhood_latest.json` or `braiins_latest.json` |
| 6 | **Coinbase + Solana** Mac producer hourly (`com.personalworkspace.cb-solana-refresh`) |
| 7 | **#555** ntfy gates: no page on `skipped` / `no_refresh_path`; no page on `local_mcp_timeout` if `as_of` is under 6h or MCP is not the live path; Mac leftover fund-manager must not treat `rh_checking` as RH brokerage. Do **not** reload Mac `rh-refresh`. |
| 8 | **#668** YNAB cash snapshots (One Card / RH Checking / X Money): dedicated `ynab-refresh` every **3h** on Pi systemd + Mac launchd. Not a fund-manager sidecar (weekdays/market-hours only). |
| 9 | **#689** Braiins cutover: see `BRAIINS_PRODUCER.md`. Smoke `payouts` list on Pi, then disarm Mac launchd. |

## Install / reload

Follow **`RH_PRODUCER.md` eng-gate sequence**. Do not disarm Mac first.

### 1–2) Pi OAuth + smoke (Mac launchd stays up)

```bash
# on Pi — OAuth must already exist on this host (not copied from Mac)
sudo cp treasury/deploy/rh-refresh.service treasury/deploy/rh-refresh.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rh-refresh.timer
./treasury/rh_refresh.sh   # must write robinhood_latest.json and move FCC as_of
```

### 3) Mac disarm — only after step 2 is green

```bash
UID_N=$(id -u)
launchctl bootout gui/$UID_N/com.personalworkspace.rh-refresh 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist
```

Keep **CB/Solana** Mac launchd. **Do not** reload Braiins launchd (#689 Pi SoT):

```bash
cd ~/personal-workspace-worktrees/treasury   # or monorepo root

cp treasury/deploy/com.personalworkspace.cb-solana-refresh.plist ~/Library/LaunchAgents/

UID_N=$(id -u)
launchctl bootout gui/$UID_N/com.personalworkspace.braiins-refresh 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.braiins-refresh.plist
launchctl bootout gui/$UID_N/com.personalworkspace.cb-solana-refresh 2>/dev/null || true
launchctl bootstrap gui/$UID_N ~/Library/LaunchAgents/com.personalworkspace.cb-solana-refresh.plist
```

## Manual

```bash
# Coinbase + Solana + push (RH file excluded from push)
bash treasury/cb_solana_refresh.sh

# Braiins on producer (Pi) — no Mac→Pi push
bash treasury/braiins_refresh.sh

# RH on producer (Pi)
TREASURY_RH_ROLE=producer TREASURY_SKIP_PI=1 python3 -m treasury.rh_snapshot_sync --local-only --print

# RH on Mac consumer (pull Pi only)
TREASURY_RH_ROLE=consumer TREASURY_SKIP_LOCAL_MCP=1 python3 -m treasury.rh_snapshot_sync --print

# Push only (non-RH snapshots)
python3 -m treasury.rh_snapshot_sync --push-only

# YNAB cash snapshots (One Card / RH Checking / X Money)
bash treasury/ynab_refresh.sh
```

## Logs

- `treasury/snapshots/rh_refresh_latest.log`
- `treasury/snapshots/rh_producer_status.json`
- `treasury/snapshots/braiins_refresh_latest.log`
- `treasury/snapshots/cb_solana_refresh_latest.log`

## Security

- No public port-forward of FCC.
- Push is SCP of **snapshot JSON only** (balances / hashrate ages) — not API tokens.
- Pool token stays on **prism** (`~/.config/braiins/token`, mode 600). Never `treasury/config.json`.
- RH OAuth tokens stay on **prism** (producer). Box MCP is spare only.
- Producer does not place orders.
