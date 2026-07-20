# Agentic fund manager — live runbook

**Status:** `live_autopilot` (policy + MCP). Full multi-name deployment blocked until RH investor profile is complete.

## One-time human step (required now)

Robinhood blocked trades after the **first** agentic order until the investment profile is filled:

→ **[Complete investor profile for agentic account](https://applink.robinhood.com/investment_profile?account_number=674601752&context=second_trade)**  
(Desktop recommended.)

After that, tell the agent: *“profile done — finish fund manager deploy”* (or any new session with fund manager skill will resume).

## What is already live

| Piece | Location |
|-------|----------|
| Policy | `investment/fund_manager.json` (`live: true`, no trade approval) |
| Thesis / allowlist | `investment/README.md`, `positions.md` |
| Weights engine | `python3 treasury/fund_manager.py --write` |
| RH dual snapshot | `treasury/rh_sync.py` + MCP |
| FCC panel | Brokerage → Agentic fund manager |
| Grok skill | `robinhood-agentic` |
| MCP | `https://agent.robinhood.com/mcp/trading` |

## Autopilot rules (v1)

1. Trade **only** agentic account `••••1752` / config `agentic_account_number`.
2. **No** per-trade user confirmation.
3. **No** max order notional — discretion within agentic capital.
4. Targets: **~40%** BTC & digital credit · **~60%** stocks (of deployed equity).
5. Risk budget = **money you deposit** into agentic.
6. Kill switch = withdraw agentic funds / disconnect MCP.

## Bootstrap deploy (2026-07-20 after hours)

| Symbol | $ | Sleeve | Result |
|--------|---|--------|--------|
| MSTR | 1.65 | BTC-complex | **Queued** for next regular open (`order_id` `6a5e9611-…`) |
| MARA | 1.65 | BTC-complex | Blocked — investor profile |
| TSLA | 2.50 | Stocks | Blocked — investor profile |
| SPCX | 2.40 | Stocks | Blocked — investor profile |

Dollar market orders use `regular_hours` so they queue if placed after the close.

## Ongoing agent loop

```text
1. get_accounts / get_portfolio / get_equity_positions (agentic)
2. rh_sync envelope → robinhood_latest.json
3. python3 treasury/run_treasury.py --offline   # updates FCC + fund_manager_latest.json
4. Read fund_manager analysis (weights, hints)
5. If fair_game and live: rebalance / deploy via place_equity_order on agentic
6. Log notable actions in investment/fund_manager_journal.md
```

## After profile complete — finish deploy

1. Confirm BP and no open errors.
2. Place remaining sleeve buys (MARA + TSLA + SPCX or updated discretion) with `dollar_amount` + `type=market` + `regular_hours`.
3. After fills: refresh snapshot; verify deployed weights ≈ 40/60.
4. Resume discretionary active management (no weekly DCA).

## Kill / pause

- Soft pause: set `"live": false` in `fund_manager.json`.
- Hard stop: withdraw capital from agentic or disable MCP connector.
