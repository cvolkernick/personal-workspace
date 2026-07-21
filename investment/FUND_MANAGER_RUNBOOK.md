# Agentic fund manager — operating model

**Status:** `live_autopilot`  
**Schedule:** ~**1 automated review per trading day** (mid-session preferred)  
**Trade cap:** **none** — a review may hold or place many orders  
**Owner role:** **optional observer** via FCC (not required to kick off or reply)

## Design intent

| You want | How we handle it |
|----------|------------------|
| See agent thinking / debate | Decision log on **FCC dashboard** + JSONL + journal |
| Step in only when needed | No required feedback; intervene via policy/`live`/capital when you choose |
| Automated as possible | Scheduled runner (Pi/cron) — **not** “open dashboard to run” |
| Not day-trading | Prefer mid-session; avoid open/close noise; no HFT mandate |
| Multiple trades if justified | **No max trades/day** — only schedule is ~daily attention |

## Where to watch (no action required)

FCC → **Brokerage** → **Decision log / rationale**

Shows team votes, why now, actions. Refresh the page anytime; it does **not** start a review.

Also:
- `treasury/snapshots/fund_manager_decisions.jsonl`
- `investment/fund_manager_journal.md`

## Team (debate → single executor)

| Role | Orders? | Job |
|------|---------|-----|
| Scout | No | Book, weights, light context |
| Thesis | No | 40/60 + thesis fit |
| Risk | No | Trade or not; size |
| Critic | No | Challenge / block / cut size |
| **Executor** | **Yes** | Only place/cancel on RH MCP; must log rationale |

Quorum: Risk + Thesis OK; Critic can force hold or size-down.

## Unattended automation

```bash
# Manual test from repo root
./treasury/fund_manager_daily.sh
```

**Pi / always-on (preferred):**
1. Clone/sync `personal-workspace` on the Pi  
2. Ensure `grok` CLI + Robinhood MCP auth work headless  
3. Install timer units from `treasury/deploy/` (edit paths/user)  
   - `fund-manager.service` + `fund-manager.timer`  
   - Or cron: `30 12 * * 1-5 /path/treasury/fund_manager_daily.sh` with `TZ=America/New_York`  
4. Logs: `treasury/snapshots/fund_manager_daily_*.log`

Dashboard load is **observe-only** — never the scheduler.

## Kill switches

- `"live": false` in `investment/fund_manager.json`  
- Withdraw agentic capital  
- Disable timer / disconnect MCP  

## Strategy reminder

Modernized **40% BTC & digital credit / 60% stocks** on **agentic account only**. Risk = deposits into that account.
