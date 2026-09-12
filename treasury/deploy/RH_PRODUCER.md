# Robinhood snapshot producer — prism/Pi SoT (#518)

**Source of truth host: `prism` (Pi, `prism-agent@192.168.100.98`).**
Box RH MCP is spare only — not primary. No live trades from this producer.

**Do not merge this Issue until the eng-gate sequence below is green.**
Buzz Grok / Naka lock. Chris OAuth on Pi is the kill-switch.

## Eng-gate sequence (locked order)

Do these in order. **Do not skip ahead.**

| Step | Who | Gate | Stop if |
|------|-----|------|---------|
| **1** | Chris on **Pi** | `grok` CLI + `[mcp_servers.robinhood-trading]` + **Chris OAuth on Pi** | Mac tokens are copied / `sync_pi_grok_auth` is treated as SoT |
| **2** | Pi | Smoke: `./treasury/rh_refresh.sh` writes `robinhood_latest.json` **and** FCC RH `as_of` **moves** | `as_of` invented, unchanged, or FCC still stale |
| **3** | Mac | **Then** `launchctl bootout` `com.personalworkspace.rh-refresh` | Mac launchd still loaded after Pi is SoT |
| **4** | Naka | Failure NTFY names **Pi host** + **error class** | Alert says `mac` or has no `error_class` |
| **5** | Ops | Mac re-auth is **short-term only** until step 2 is green | Mac re-auth used as the durable fix |

**Wrong if:**

- Mac launchd is still writing RH after Pi is declared SoT (dual-writer)
- Mac launchd is disarmed **before** Pi OAuth is healthy (cutover-too-early)
- A failed refresh invents or bumps `as_of` (honest stale required)

Mac tokens **do not travel**. Do not copy `~/.grok` from the laptop onto Pi.
Create OAuth on the producer host. Box MCP stays spare.

## AC map

| AC | Behavior |
|----|----------|
| AC1 | SoT host documented here + `config.rh_producer.sot_host=prism` |
| AC2 | After step 2 is green, Pi timer refreshes while Mac is offline / revoked |
| AC3 | Step 2: Pi `robinhood_latest.json` + FCC `as_of` move; 3h timer under 6h NTFY |
| AC4 | Step 4: NTFY title/body `producer=<Pi host>` + `error_class=` |
| AC5 | Step 1: OAuth SOP is **on Pi** (not Mac) |
| AC6 | Step 3: Mac launchd stopped **after** smoke green; RH omitted from Mac→Pi push |
| AC7 | Success moves `as_of`; auth-fail leaves prior `as_of` (no invent) |

`rh_refresh_prompt.txt` is read-only (`get_accounts` / `get_portfolio` /
`get_equity_positions` → `rh_sync.py`).

## Roles

| Role | Where | What writes RH | Pushes RH to Pi? |
|------|-------|----------------|------------------|
| **producer** | prism/Pi systemd | Local Grok + `robinhood-trading` MCP | No (already SoT) |
| **consumer** | Mac (after step 3) | Pull from Pi only | No |
| **backup** | Mac, opt-in later | Local MCP for laptop FCC | **No** |

Env: `TREASURY_RH_ROLE=producer|backup|consumer`. Pi unit sets `producer`.

---

## Step 1 — Pi install + OAuth (Mac tokens do not travel)

On prism (`ssh prism-agent@192.168.100.98`):

```bash
cd /home/prism-agent/personal-workspace   # edit unit paths if the clone differs
git fetch origin
git checkout work/treasury   # or cursor/rh-producer-pi-d520 until merged
git pull --ff-only

which grok
test -f ~/.grok/config.toml
```

`~/.grok/config.toml` on **this host** (create here; do not scp from Mac):

```toml
[mcp_servers.robinhood-trading]
url = "https://agent.robinhood.com/mcp/trading"
enabled = true
```

1. Open Grok / SuperGrok **on Pi**.
2. Complete Robinhood OAuth / device login for `robinhood-trading` **on Pi**.
3. Do **not** run `sync_pi_grok_auth` as the producer path. Mac tokens do not travel.
4. Do **not** treat Box RH MCP as primary (spare only).

Install the timer **but keep Mac launchd loaded** until step 2 is green
(Mac remains the short-term producer so FCC does not go dark):

```bash
sudo cp treasury/deploy/rh-refresh.service treasury/deploy/rh-refresh.timer /etc/systemd/system/
# If the clone is not /home/prism-agent/personal-workspace, edit WorkingDirectory/ExecStart first.
sudo systemctl daemon-reload
sudo systemctl enable --now rh-refresh.timer
systemctl list-timers | grep rh-refresh
```

---

## Step 2 — Smoke (must move as_of; must not invent)

Still on Pi. Leave Mac launchd **running**.

```bash
BEFORE=$(python3 -c "import json; print(json.load(open('treasury/snapshots/robinhood_latest.json')).get('as_of'))")
TREASURY_RH_ROLE=producer TREASURY_SKIP_PI=1 ./treasury/rh_refresh.sh
AFTER=$(python3 -c "import json; print(json.load(open('treasury/snapshots/robinhood_latest.json')).get('as_of'))")
echo "as_of $BEFORE -> $AFTER"
python3 -c "import json; print(json.load(open('treasury/snapshots/rh_producer_status.json')))"
```

**Green only if all of these are true:**

- `robinhood_latest.json` was written on Pi
- `AFTER` is **newer** than `BEFORE` (FCC RH `as_of` moves; age ≪ 6h)
- `rh_producer_status.json` has `"ok": true`, `producer_host` = prism/Pi
- No invented balances / no `as_of` bump on a failed MCP call

**Auth-fail honesty (do not invent):** if OAuth is down, re-run. `as_of` **stays**
`$BEFORE`. Status `error_class=auth_fail`. That is a failed step 1 — fix OAuth
on Pi. Do **not** go to step 3.

Until this step is green, Mac re-auth may still be used as a **short-term**
unblock. It is not the durable SoT.

---

## Step 3 — Then stop Mac launchd (no dual-writer)

**Only after step 2 is green.** Cutting over earlier leaves FCC with no writer.

```bash
# on Mac
UID_N=$(id -u)
launchctl bootout gui/$UID_N/com.personalworkspace.rh-refresh 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.rh-refresh.plist
launchctl print gui/$UID_N/com.personalworkspace.rh-refresh 2>&1 | head
```

Confirm the unit is gone. Leaving it loaded after Pi SoT = dual-writer = wrong.

Leave `com.personalworkspace.cb-solana-refresh` loaded if you still want Mac
CB/Solana → Pi push. That push set **excludes** `robinhood_latest.json`
unless `TREASURY_RH_PUSH=1` (emergency only).

Do not load the Mac plist as `backup` during cutover. Backup is a later
laptop-FCC option and still must not push RH.

---

## Step 4 — NTFY = Pi host + error class (#699 Option B)

Failure / stale RH alerts must name the **producer** (Pi), not the Mac
alerter alone:

- Title like `FCC · RH auth_fail · prism`
- Body starts `producer=prism error_class=auth_fail`
- Host tag: `FCC_HOST_TAG=prism` on the systemd unit

**Routing (2026-09-12):**

| Signal | Sink |
|--------|------|
| Stale RH (gated #555) | GitHub comment on standing [#701](https://github.com/cvolkernick/personal-workspace/issues/701) |
| `need_llm` / deploy / rebalance | GitHub #701 only |
| `error`, or `FCC_ALERT_KILL_SWITCH=1` | GitHub #701 **and** ntfy pri-5 |

ntfy stays the paging channel. Optional `NTFY_TOKEN` (Bearer) in
`~/.config/workflow-scheduler.env` — do not commit it. Live topic ACL on
ntfy.sh needs a reserved/Pro topic or a self-hosted server; the publisher
sends the token when present.

Cooldown stays 6h (shared with stale-RH). Auth down leaves the last honest
`as_of` in place — dashboard stays stale; the ops issue (and ntfy on error)
tells you why.

---

## Step 5 — Mac re-auth is short-term only

Until step 2 is green, a Mac `robinhood-trading` re-auth can keep launchd
feeding FCC. After step 2 + step 3:

- Scheduled freshness does **not** depend on Mac
- Further Mac re-auths do not fix Pi OAuth
- Next revoke is fixed with **step 1 on Pi**, then re-smoke step 2

---

## NTFY / as_of reference

- Freshness window: **6h** (`TREASURY_RH_MAX_AGE_HOURS`, FCC stale threshold)
- Producer timer: **3h** (`rh-refresh.timer`)
- Status sidecar: `treasury/snapshots/rh_producer_status.json`
- Error classes: `auth_fail`, `timeout`, `grok`, `mcp`, `stale`, `unreachable`, `skipped`, …

## #555 — do not page expected skip / leftover Mac / timeout-while-fresh

Post-#518 cutover, status/log still records `skipped` / `no_refresh_path` /
`local_mcp_timeout`. **Do not ntfy** those when:

1. Host is a gateway / non-producer (`no_refresh_path`, `skipped`) — expected.
2. Mac leftover fund-manager / `rh_checking` scores a frozen local age (~17.8h)
   while prism `robinhood.as_of` is fresh. Mac `rh-refresh` stays **unloaded**.
3. `local_mcp_timeout` and existing `robinhood_latest.json` as_of is **under 6h**,
   or `rh_mcp_enabled` is false/null and local MCP is not the live producer path.

Quiet leftover Mac RH freshness ntfy (do **not** reload `rh-refresh`):

```bash
# on Mac — only if these units are still paging false RH / rh_checking stale
launchctl bootout gui/$(id -u)/com.personalworkspace.fund-manager-daily 2>/dev/null || true
launchctl bootout gui/$(id -u)/com.personalworkspace.fund-manager-bp-poll 2>/dev/null || true
rm -f ~/Library/LaunchAgents/com.personalworkspace.fund-manager-daily.plist
rm -f ~/Library/LaunchAgents/com.personalworkspace.fund-manager-bp-poll.plist
# keep com.personalworkspace.rh-refresh unloaded
```

## Revert

```bash
# only if rolling back before step 3, or after a failed step 2
sudo systemctl disable --now rh-refresh.timer   # on Pi

# Mac: keep / restore com.personalworkspace.rh-refresh until Pi smoke is green
```
