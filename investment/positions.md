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
| **Energy** | Overarching theme (mining power, AI electricity, electrification). **No mandated positions.** Agentic manager may propose equities/options with **explicit user approval** before orders |
| **AI stack (broad)** | Infra (hardware), foundation models, application software—additions should still fit modernized 60/40 and FCC floors |

## Out of scope

- **Kalshi / prediction markets** — not part of this book.
- Orders on **non-agentic** RH accounts (MCP rejects); use agentic account only for agent trades.
- Using this table as a balance sheet (always defer to FCC).

## Agentic fund manager rules

1. **Active management** — no required weekly DCA; rebalance and rotate when thesis + risk/reward justify it.
2. Prefer names on this allowlist unless the user expands it.
3. Steer the **agentic** book toward **~40% BTC-complex / ~60% stocks** using FCC portfolio + live quotes (not this file’s quantities).
4. Energy or new AI names: propose rationale + size, then wait for confirmation (default).
5. **Do not** trade if FCC overall stress is red for liquidity (card/loan buffers) unless user overrides.
6. Respect max notional / max position % once set in fund-manager policy config.
7. Fund and trade only the **agentic** RH account for MCP orders; primary is separate.

## Tracking

- **FCC** = balances, BP, positions, stress, actions.
- Refresh RH: MCP `get_accounts` / `get_portfolio` / `get_equity_positions` → `python3 treasury/rh_sync.py`.
