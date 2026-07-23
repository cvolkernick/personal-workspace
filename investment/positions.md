# Investment Portfolio — Positions & Allowlist

**As of:** 2026-07-20  
**Live sizes / prices:** Financial Command Center only (not this file).  
**Purpose:** Thesis allowlist and sleeve tags for humans + agentic Robinhood trading.

## Target structure (modernized 60/40)

| Sleeve | Target | Themes |
|--------|--------|--------|
| **BTC & digital credit complex** | **~40%** | Bitcoin, hard money, digital credit, BTC infrastructure (miners) |
| **Stocks / growth** | **~60%** | Equity growth, AI stack exposure, opportunistic energy |

## Core allowlist

| Symbol | Sleeve | Theme | Notes |
|--------|--------|-------|-------|
| **BTC** | ~40% complex | Bitcoin / hard money | Prefer Coinbase / self-custody stack where applicable; RH if available |
| **MSTR** | ~40% complex | Digital credit | BTC-linked corporate proxy |
| **STRC** | ~40% complex | Digital credit | Digital credit / BTC-linked structure |
| **SATA** | ~40% complex | Digital credit | Digital credit sleeve |
| **ASST** | ~40% complex | Digital credit | Digital credit sleeve (held / thesis-aligned) |
| **BITA** | ~40% complex | Digital credit / BTC yield | BTC-based yield / fixed income — **not** stocks sleeve |
| **MARA** | ~40% complex | BTC infrastructure | Miner |
| **RIOT** | ~40% complex | BTC infrastructure | Miner |
| **CLSK** | ~40% complex | BTC infrastructure | Miner |
| **WULF** | ~40% complex | BTC infrastructure | Miner / power-adjacent |
| **IREN** | ~40% complex | BTC infrastructure | Miner / energy-intensive infra |
| **TSLA** | ~60% stocks | Growth / energy-adjacent equity | Stocks sleeve |
| **SPCX** | ~60% stocks | Growth equity | Stocks sleeve |

## Optional / open sleeves (no fixed tickers)

| Sleeve | Policy |
|--------|--------|
| **Hard money metals** | Gold/silver (e.g. PAXG, GLDM or similar) allowed under the ~40% hard-money umbrella if sized deliberately |
| **Energy** | Overarching theme (mining power, AI electricity, electrification). **No mandated core tickers.** Candidates on **watchlist** (below); deep-dive before first buy |
| **AI stack (broad)** | Infra (hardware), foundation models, application software—additions should still fit modernized 60/40 and FCC floors |

## Thematic watchlist (monitor / consider — not holdings)

Machine source: [`watchlist.json`](./watchlist.json). FCC/fund manager analysis surfaces these as **candidates**, not auto-buys.

| Symbol | Theme | Status | Notes |
|--------|-------|--------|-------|
| **BE** | Energy (Bloom Energy) | monitor | Verbose re-dive 2026-07-23: thesis OK, **no buy** (val + ~$8 NAV + Q2 7/28). `research/BE_deep_dive.md` |

**Rules**
1. Prefer **core allowlist** for routine rebalances toward 40/60.
2. Scout/Thesis scan the watchlist each review; may propose size only if status is `ready` (or after a fresh deep-dive).
3. Run **`/position-deep-dive symbol=TICKER`** before first buy when `deep_dive_required_before_buy` is true.
4. Run **`/fund-manager-research`** periodically / on need_llm to refresh book+strategy view and **propose new watchlist candidates** (not auto-added buys).
5. Reports land in [`research/`](./research/). Promote to core only deliberately (update `fund_manager.json` + this table).

## Out of scope

- **Kalshi / prediction markets** — not part of this book.
- Orders on **non-agentic** RH accounts (MCP rejects); use agentic account only for agent trades.
- Using this table as a balance sheet (always defer to FCC).

## Agentic fund manager rules (v1)

Source of truth for automation: [`fund_manager.json`](./fund_manager.json).

1. **Active management** — no required weekly DCA; rebalance and rotate when thesis + risk/reward justify it.
2. **Uniform research/rotate on every deploy** — size-invariant ($10 or $10k). Consider held **and** unheld allowlist/theme names; do **not** default to topping existing positions without rejecting alternatives with reasons.
3. Prefer **core allowlist** for liquidity/fit; energy/AI from **watchlist** only after consideration / deep-dive (`allowlist.strict: false`).
4. Steer the **agentic** book toward **~40% BTC-complex / ~60% stocks** using agentic NAV + live quotes only.
5. **No per-trade user approval** mid-pass — capital in agentic is fair game. Owner may give **after-pass** feedback for the next cycle.
6. **No max single-order notional** — size at manager discretion (notional scales with NAV; process does not).
7. FCC card/loan stress does **not** block agentic trades in v1 (separate capital).
8. MCP orders **only** on the agentic account.
9. Log full fleet rationale (considered / chosen / rejected / votes) for owner review.

## Tracking

- **FCC** = balances, BP, positions, stress, actions.
- Refresh RH: MCP `get_accounts` / `get_portfolio` / `get_equity_positions` → `python3 treasury/rh_sync.py`.
