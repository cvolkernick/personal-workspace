# Robinhood snapshot producer — prism/Pi SoT (#518)

**Source of truth host: `prism` (Pi, `prism-agent@192.168.100.98`).**
Mac is consumer / optional backup. Box RH MCP is spare only — not primary.

This inverts the 2026-08-03 Mac-launchd producer (`FEEDS_P0.md`). Coinbase CLI
and Braiins pool token can stay Mac-produced; **RH trade snapshot does not.**

## AC map

| AC | Behavior |
|----|----------|
| AC1 | SoT host documented here + `config.rh_producer.sot_host=prism` |
| AC2 | Pi systemd timer refreshes while Mac is offline / OAuth-revoked |
| AC3 | Success writes Pi `treasury/snapshots/robinhood_latest.json`; FCC reads it; `as_of` inside ~6h NTFY window |
| AC4 | Failure NTFY title/body include `producer=<host>` + `error_class=` (auth_fail / timeout / grok / mcp / …) |
| AC5 | Re-auth SOP below (Pi Grok MCP `robinhood-trading`) |
| AC6 | Mac `com.personalworkspace.rh-refresh` unloaded or consumer-only; RH omitted from Mac→Pi push |
| AC7 | Smoke: success moves `as_of`; auth-fail leaves prior `as_of` (no invent) |

No live trades from this producer. `rh_refresh_prompt.txt` is read-only
(`get_accounts` / `get_portfolio` / `get_equity_positions` → `rh_sync.py`).

## Roles

| Role | Where | What writes RH | Pushes RH to Pi? |
|------|-------|----------------|------------------|
| **producer** | prism/Pi systemd | Local Grok + `robinhood-trading` MCP | No (already SoT) |
| **consumer** | Mac (default) | Pull from Pi only | No |
| **backup** | Mac, opt-in | Local MCP for laptop FCC | **No** |

Env: `TREASURY_RH_ROLE=producer|backup|consumer`. Pi unit sets `producer`.

## Install on Pi (producer)

```bash
# on prism (ssh prism-agent@192.168.100.98)
cd /home/prism-agent/personal-workspace   # or worktree path — edit unit WorkingDirectory
git fetch origin
git checkout work/treasury   # or the #518 branch until merged
git pull --ff-only

# grok + robinhood-trading MCP must already work headless (see Re-auth SOP)
which grok
test -f ~/.grok/config.toml

sudo cp treasury/deploy/rh-refresh.service treasury/deploy/rh-refresh.timer /etc/systemd/system/
# If the clone is not /home/prism-agent/personal-workspace, edit the unit paths first.
sudo systemctl daemon-reload
sudo systemctl enable --now rh-refresh.timer
systemctl list-timers | grep rh-refresh

# smoke (moves as_of on success)
./treasury/rh_refresh.sh
python3 -c "import json; d=json.load(open('treasury/snapshots/robinhood_latest.json')); print(d.get('as_of'), d.get('source'))"
```

Confirm FCC (same host or after pull) shows RH `as_of` within 6h.

## Disarm Mac launchd (no dual-writer)

```bash
# on Mac — required cutover
UID_N=$(id -u)
launchctl bootout gui/$UID_N/com.personalworkspace.rh-refresh 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist

# confirm gone
launchctl print gui/$UID_N/com.personalworkspace.rh-refresh 2>&1 | head
```

Leave `com.personalworkspace.cb-solana-refresh` loaded if you still want Mac
CB/Solana → Pi push. That push set **excludes** `robinhood_latest.json`
unless `TREASURY_RH_PUSH=1` (emergency only).

### Optional Mac backup (laptop FCC only)

Only if you need a local Mac snapshot when LAN to Pi is down. Still must not
push RH:

```bash
# TREASURY_RH_ROLE=backup in the plist; TREASURY_SKIP_PUSH_PI=1 already set
cp treasury/deploy/com.personalworkspace.rh-refresh.plist ~/Library/LaunchAgents/
# edit TREASURY_RH_ROLE from consumer → backup if you want Mac MCP
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist
```

Prefer leaving it **unloaded**. Consumer-mode plist (if loaded) only pulls Pi.

## Re-auth SOP (producer host)

Tokens live on **prism**, not Mac. Mac re-auth no longer unblocks scheduled RH.

1. On prism (SSH or console): open Grok CLI / SuperGrok.
2. Confirm `~/.grok/config.toml` has:
   ```toml
   [mcp_servers.robinhood-trading]
   url = "https://agent.robinhood.com/mcp/trading"
   enabled = true
   ```
3. Complete Robinhood OAuth / device login for `robinhood-trading` **on this host**.
4. Do **not** treat Box RH MCP as primary (spare only).
5. Smoke (must move `as_of`; must not invent on failure):
   ```bash
   BEFORE=$(python3 -c "import json; print(json.load(open('treasury/snapshots/robinhood_latest.json')).get('as_of'))")
   TREASURY_RH_ROLE=producer TREASURY_SKIP_PI=1 python3 -m treasury.rh_snapshot_sync --local-only --print
   AFTER=$(python3 -c "import json; print(json.load(open('treasury/snapshots/robinhood_latest.json')).get('as_of'))")
   echo "as_of $BEFORE -> $AFTER"
   ```
6. Success: `AFTER` is newer; FCC RH green / age &lt; 6h; no NTFY.
7. Auth-fail smoke: revoke or stop MCP, re-run. `as_of` **stays** `$BEFORE`.
   Status JSON `error_class=auth_fail`. NTFY title like
   `FCC · RH auth_fail · prism`.

Mac `sync_pi_grok_auth` is optional leftover — producer tokens should be
created on Pi, not copied from a laptop that keeps dying.

## NTFY / as_of

- Freshness window: **6h** (`TREASURY_RH_MAX_AGE_HOURS`, FCC stale threshold).
- Producer timer: **3h** (`rh-refresh.timer`).
- Status sidecar: `treasury/snapshots/rh_producer_status.json`
  (`ok`, `as_of`, `producer_host`, `error_class`).
- Failure NTFY (6h cooldown, shared with stale-RH): names **producer host**
  and **error class** (`auth_fail`, `timeout`, `grok`, `mcp`, `stale`, …).
- Auth down: existing snapshot is left in place. Dashboard stays honestly stale.

## Revert

```bash
# Pi
sudo systemctl disable --now rh-refresh.timer

# Mac (old Mac-producer plist lived in git history; restore TREASURY_SKIP_PI=1
# and TREASURY_RH_PUSH=1 only as a temporary unblock — not the durable path)
```
