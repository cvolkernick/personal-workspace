# Treasury action items — dual-venue liquidity strategy

**Last updated:** 2026-07-20  
**UI:** Financial Command Center → `financial-command/` (`python3 launch.py`)  
**Surfaces:** Coinbase (BTC collateral loan, High Yield vault, One Card, liquid USDC) ↔ Robinhood (equity/margin + **Agentic MCP**, DCA) via USDC bridge.

## Audit findings (MVP → current)

| Gap | Status |
| --- | --- |
| Manual Morpho/vault/card fields empty | **Card balance via YNAB** wired; Morpho LTV + vault still manual |
| One Card via Plaid | **YNAB** → `treasury/ynab_sync.py` → FCC One Card panel |
| Personal Expense Sheet | **Google Sheet** → `expenses_sync.py`: Personal = upcoming est. bills; Discretionary = capital targets (not burn). Actual spend = YNAB |
| RH Banking / ACH | **YNAB RH Checking** (Plaid) → actual checking balance + txs; brokerage MCP cash separate |
| Card stress green when unknown | **Fixed** — unknown → yellow |
| No data completeness / freshness | **Added** — data quality panel + stale warnings |
| Liquid BTC without USD | **Added** — BTC-USD via Coinbase products |
| No agent handoff brief | **Added** — copyable agent brief |
| Policy floors hidden | **Added** — policy floors grid |
| Static server only (no save API) | **Added** — `financial-command/server.py` |
| RH only $144 BP visible | **Primary margin ••••9737** ~$146; **Agentic ••••1752** MCP-tradable (currently $0 — fund to trade) |
| RH Agentic MCP | **Wired** — MCP + dual snapshot; **fund manager v1** (`investment/fund_manager.json`, `treasury/fund_manager.py`) autopilot agentic-only |
| CB liquid ~$0 | **Expected if funds in Morpho vault/collateral** — enter vault/principal manually |
| External USDC bridge | Still recommend-only (no Advanced Trade external send) |
| RH positions (TSLA/STRC/…) | **In FCC Brokerage** from snapshot (primary positions list) |

## Do now (human)

- [ ] **Open Financial Command Center** and complete **manual fields** from Coinbase app (loan principal/collateral or LTV, vault USDC, card balance + available credit) → **Save to config**.
- [ ] **Enable loan protection** on Morpho USDC loan(s); target comfort band **&lt; 50%** LTV (liquidation ~86%).
- [ ] **Enable One Card autopay** in mobile app.
- [ ] **Confirm RH account** — if real book is not ••••9737 (~$144), update `treasury/config.json` `robinhood.account_number` and refresh snapshot via agent MCP.
- [ ] **Tune policy floors** in `treasury/config.json` if $500 card / $1000 loan buffer / $500 RH BP floor don’t match your plan.
- [ ] **Weekly checklist:** real LTV in app vs config; vault liquidity; card available credit; `Refresh live` in UI.

## Agent-automatable (implemented)

| Operation | How |
| --- | --- |
| Coinbase liquid USDC/BTC + BTC price | CLI via `treasury/adapters.py` / **Refresh live** |
| Robinhood portfolio / BP | MCP `get_accounts` + `get_portfolio` ×2 → `treasury/rh_sync.py` → `robinhood_latest.json` |
| RH agentic equity/option orders | MCP place/cancel **only** on agentic account (`agentic_allowed=true`) |
| Policy evaluate + priorities | `python3 treasury/run_treasury.py` or FCC server |
| DCA governor | Pause if BP &lt; floor or margin heat high |
| Buffer vs excess | `classify_liquid_usdc` |
| Bridge CB↔RH | **Recommend only** |
| Persist manual fields | POST `/api/config` (FCC server) |

## Never claim automated

- Morpho open/repay/add collateral (except native **loan protection**)
- Coinbase Lend vault deposit/withdraw
- One Card paydown / available credit (except **autopay**)
- External USDC withdraw via Advanced Trade `transfer`

## Enhancement backlog (next)

1. Fund agentic account + optional scheduled agent strategies (user-approved).
2. Scheduled cron: `run_treasury.py` + email/calendar alert on red stress.
3. Allowlisted Coinbase Send Money for capped bridge automation.
4. Onchain Morpho position read if smart-wallet address known.
5. Push-to-config from client localStorage when offline.
6. Options level upgrade on agentic book if needed for option strategies.

## Daily / weekly agent loop

1. MCP: refresh RH dual snapshot (`get_accounts` + portfolios) → `rh_sync.py` → `robinhood_latest.json`.
2. `python3 launch.py` or **Refresh live** in UI.
3. Execute **agent** actions only (DCA pause/throttle; agentic trades only with explicit user OK).
4. Present **human** actions with amounts; after app updates, save manual fields.

## Related

- Feasibility: `research/coinbase-automation-feasibility.md`
- Policy: `treasury/policy.py`
- UI: `financial-command/index.html`
- Server: `financial-command/server.py`
