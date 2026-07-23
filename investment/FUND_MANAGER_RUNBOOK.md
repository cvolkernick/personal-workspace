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

```text
rh_refresh → rules review
  ├─ HOLD (in band, low cash) → log, quiet (no ntfy, no LLM)
  └─ need_llm (drift / deploy) → Grok team → Executor → ntfy
```

```bash
# Rules only (cheap)
python3 -m treasury.fund_manager --rules-review --notify

# Full daily script
./treasury/fund_manager_daily.sh
```

**Pi / always-on (preferred):** see `treasury/deploy/PI_SETUP.md`

1. Clone/sync `personal-workspace` on the Pi  
2. `grok` CLI + Robinhood MCP auth headless  
3. Enable `rh-refresh.timer` (~3h) + `fund-manager.timer` (weekdays ~12:30 ET)  
4. Logs: `treasury/snapshots/fund_manager_daily_*.log`

**Notifications:** ntfy topic in `config.json` → `notifications.ntfy_topic`. Alerts on need_llm / error / stale RH only.

Dashboard is **observe-only** — never the scheduler.

## Kill switches

- `"live": false` in `investment/fund_manager.json`  
- Withdraw agentic capital  
- Disable timer / disconnect MCP  

## Watchlist & multi-agent research

| Artifact | Role |
|----------|------|
| [`watchlist.json`](./watchlist.json) | Thematic candidates (e.g. **BE** energy) — monitor, not holdings |
| [`research/`](./research/) | Verbose deep-dives + portfolio research reports |
| `.grok/workflows/position-deep-dive.rhai` | Single-name deep dive |
| `.grok/workflows/fund-manager-research.rhai` | Book + strategy + watchlist + **candidate discovery** |

```text
/fund-manager-research
/fund-manager-research focus=energy max_candidates=5
/position-deep-dive symbol=BE
```

**Native workflows:** these are real Grok Build `.rhai` workflows (same as `/workflows` in the TUI). Fund-manager sessions with slash commands / host `workflow` should use them. If a session only has coding tools (no `workflow` host API), emulate phases and still write under `research/`.

**Discovery:** research may propose **new** watchlist symbols (not only owner-added). Merge as `monitor` only; never auto-buy.

Scout/Thesis scan watchlist + latest research each review. Prefer **core allowlist** for rebalance; non-core needs deep-dive when required before size-in.

## Strategy reminder

Modernized **40% BTC & digital credit / 60% stocks** on **agentic account only**. Risk = deposits into that account.
