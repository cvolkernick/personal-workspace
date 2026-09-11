---
name: robinhood-agentic
description: Robinhood Agentic Trading MCP + active fund manager (agentic account only, autopilot v1)
---

# Robinhood Agentic Fund Manager

## Connection

```toml
[mcp_servers.robinhood-trading]
url = "https://agent.robinhood.com/mcp/trading"
enabled = true
```

## Status

**`live_autopilot`** when `investment/fund_manager.json` has `"live": true`.

**Cadence:** ~1 **scheduled** review/day (mid-session ET). **Not** a max-trades limit — many orders per review OK if quorum agrees. Not open/close day-trading.

**Team:** Scout / Thesis / Risk / Critic debate; only **Executor** places orders (quorum: Risk+Thesis OK; Critic can block).

**Rationale (required for glass box):** After every decision, `append_decision(...)` so FCC shows debate. Owner feedback is **optional** — do not wait for them.

**Automation:** Daily `fund_manager_daily.sh` + **BP poll** `fund_manager_bp_poll.sh` (~15m, any cash/BP>0) + RH refresh ~3h. Prefer launchd/Pi — not dashboard load.

If the second trade fails with investment profile error, send the user the RH investor-profile link for the agentic account.

## v1 policy (user-confirmed)

| Rule | Value |
|------|--------|
| Book for weights | **Agentic account only** |
| Approval | **None** — if funded, fair game |
| Max order $ | **None** — manager discretion |
| Risk budget | **Deposits into agentic account only** |
| Cadence | Active (not weekly DCA) |
| Targets | ~40% BTC/digital credit complex · ~60% stocks |

Machine policy: `investment/fund_manager.json`  
Runbook: `investment/FUND_MANAGER_RUNBOOK.md`  
Journal: `investment/fund_manager_journal.md`  
Engine: `python3 treasury/fund_manager.py --write`

## Accounts

| Role | Config key | MCP trades |
|------|------------|------------|
| Primary margin | `robinhood.account_number` | **No** for this agent |
| **Agentic** | `robinhood.agentic_account_number` | **Yes** — fund manager book |

## Manager loop (uniform — every deploy / full review)

**Size-invariant:** same process at $10 or $10k. Only notionals scale. **No held-only default.**

1. `get_accounts` / `get_portfolio` / `get_equity_positions` on **agentic** (primary read OK for FCC)
2. Sync RH → `robinhood_latest.json`; optional `run_treasury --offline`
3. **Research/rotate (required):** `/fund-manager-research` or emulate — held + **unheld** allowlist, themes, watchlist; list considered / chosen / rejected
4. Deep-dive non-core/watchlist first buys when required (`/position-deep-dive symbol=SYM`)
5. Team: Scout → Thesis → Risk → Critic → quorum → **Executor** only on agentic
6. Log decision with full rationale + team_votes (owner reviews **after** pass)
7. Re-sync after fills

## Watchlist & research (native workflows)

- `investment/watchlist.json` — monitor only; not auto-buy
- `/fund-manager-research` — **required flavor** on capital deploy / full review
- `/position-deep-dive symbol=…` — first buy of flagged names
- Emulate phases if host lacks workflow tool; still write `investment/research/`
- Owner feedback: **after** pass, optional; apply next cycle

## Trading

- **Do not** ask for mid-pass trade confirmation in v1.
- Deploy where themes + 40/60 make most sense **now**; document why alternatives lost.
- Prefer limit when spreads wide; market OK for liquid small notionals in regular hours.
- No trade if agentic BP/cash is zero.
- Kill switch = withdraw capital / `live:false` / disconnect MCP.

## Workspace map

- `investment/fund_manager.json` — policy  
- `investment/watchlist.json` — thematic candidates  
- `investment/research/` — deep-dive reports  
- `.grok/workflows/position-deep-dive.rhai` — recurring research workflow  
- `treasury/fund_manager.py` — weights / hints / watchlist surface  
- `treasury/rh_sync.py` — dual RH snapshot  
- FCC → Brokerage → Agentic fund manager panel  
