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
| **STRC** | ~40% complex | Digital credit | **Small bias** within 40% — BTC-fundamental high-yield / frequent dividends (prefer real seat; yield edge vs USDC/USDG cash) |
| **SATA** | ~40% complex | Digital credit | **Small bias** within 40% — pair with STRC (same yield thesis; not cash) |
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

## Thematic watchlist (ready for consideration — not holdings)

Machine source: [`watchlist.json`](./watchlist.json). Owner 2026-08-04: watchlist = **active allocation interest** → auto deep-dive → **`ready`** for each systemic deploy consider set. Still **not** auto-buys.

| Symbol | Theme | Status | Notes |
|--------|-------|--------|-------|
| **BE** | Energy (Bloom Energy) | ready | Post-Q2 refresh 2026-08-04. Thesis strong; val/beta harsh. `research/BE_deep_dive.md` |
| **GOOGL** | AI stack (Alphabet / Google) | ready | Dive 2026-08-04. Liquid AI + cash compounder. Class A preferred. |
| **AAPL** | AI stack (Apple) | ready | Dive 2026-08-04. Quality/ecosystem AI; behind GOOGL/NVDA on pure AI. |
| **NVDA** | AI stack (NVIDIA) | ready | Dive 2026-08-04. Pure AI infra; next print ~2026-08-26 event risk. |
| **PLTR** | AI harness/apps (Palantir) | ready | Dive 2026-08-04. Medium priority; high multiple / gov gates. |

**Rules**
1. Prefer **core allowlist** for routine rebalances toward 60/40 when relative value favors it; **strong theme bias**.
2. Scout/Thesis **must** include every `ready` watchlist name each allocation assessment; reject with reasons if not sized.
3. **Owner add** → immediately run **`/position-deep-dive symbol=TICKER`** → set `ready` (unless explicit `pass`). Do not leave owner names parked on `monitor`.
4. Refresh deep-dives on ~**90-day** age, material news/earnings/drawdown, or before first buy if stale.
5. Run **`/fund-manager-research`** periodically / on need_llm; agents may propose candidates (start `monitor` → dive → `ready`). Never auto-buy.
6. Reports land in [`research/`](./research/). Promote to core only deliberately (update `fund_manager.json` + this table).

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
