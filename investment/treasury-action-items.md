# Treasury action items — dual-venue liquidity strategy

**Last updated:** 2026-07-17  
**Surfaces:** Coinbase (BTC collateral loan, High Yield vault, One Card, liquid USDC) ↔ Robinhood (equity/margin, DCA) via USDC bridge.

## Do once (human / in-app)

- [ ] **Coinbase loan protection** — enable on each Morpho USDC loan; set LTV trigger as close as possible to your comfort band (strategy target **&lt; 50%** LTV; protocol liquidates ~86%). Re-enable after it fires (one-shot).
- [ ] **Coinbase One Card autopay** — schedule in mobile app (statement or fixed amount) with primary + backup payment method. No public card API.
- [ ] **Fill `treasury/config.json` → `coinbase_manual`** after each app check:
  - `loan_principal_usdc`, `collateral_btc_usd` or `ltv`
  - `vault_usdc` (High Yield / Core)
  - `card_balance`, `card_available_credit`
- [ ] **Set policy floors** in `treasury/config.json` → `policy` (card float, loan buffer, bridge dry powder, RH BP floor). Defaults: card $500, loan buffer $1000, bridge $200, RH BP floor $500.
- [ ] **Weekly LTV checklist** (5 min): open Coinbase Borrow → confirm real LTV vs config; confirm vault liquidity; confirm card available credit; re-run `python3 treasury/run_treasury.py`.

## Agent-automatable (already implemented)

| Operation | How |
| --- | --- |
| Coinbase liquid USDC/BTC read | `coinbase balance --paginate` via `treasury/adapters.py` |
| Robinhood portfolio / BP read | MCP `get_portfolio` → write `treasury/snapshots/robinhood_latest.json` |
| Policy evaluate + priority actions | `python3 treasury/run_treasury.py` → `dashboard/treasury_latest.json` |
| DCA governor | Pause if BP &lt; floor or margin use &gt; max; allow/slow otherwise |
| Buffer vs excess classification | Pure `classify_liquid_usdc` |
| Bridge CB↔RH | **Recommend only** (amount, direction, reason) — not auto-send |

## Never claim automated (no public write API)

- Morpho retail loan open / repay / add collateral (except native **loan protection**)
- Coinbase Lend High Yield vault deposit / withdraw
- One Card paydown / available credit management (except **autopay**)
- External USDC withdraw via Advanced Trade `transfer` (portfolio-internal only)

## Bridge handoff

When the dashboard shows **Recommend bridge**:

1. Confirm LTV green and floors still funded after the move.
2. Move USDC in Coinbase/Robinhood apps (or future allowlisted Send Money).
3. Re-run treasury script; update manual fields if loan/card changed.

## Double-leverage warning

Do not fund Robinhood margin-driven stock DCA with freshly borrowed Coinbase USDC without an explicit risk budget. BTC drawdowns often hit miners + growth equities together.

## Daily / weekly agent loop

1. Refresh RH snapshot (MCP portfolio) + run `treasury/run_treasury.py`.
2. Open Financial Command Center; execute **agent** actions (DCA pause only unless user authorizes buys).
3. Present **human** actions (LTV, vault, card, bridge) with amounts from the action list.
4. After human completes in-app steps, update `coinbase_manual` and re-run.

## Related

- Feasibility research: `research/coinbase-automation-feasibility.md`
- Policy code: `treasury/policy.py`
- UI: `financial-command/index.html`
