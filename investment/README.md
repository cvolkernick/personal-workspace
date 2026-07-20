# Investment Portfolio

> **Liquidity UI / book of record:** [Financial Command Center](../financial-command/index.html)  
> Serve from repo root (`python3 launch.py` or `python3 financial-command/server.py`). FCC aggregates Coinbase, Robinhood (primary + agentic), YNAB, and sheet estimates.

Tracking for crypto, equities, and related sleeves. **Canonical live balances, buying power, and RH positions come from the FCC** (treasury snapshots + MCP). This folder holds **thesis, target structure, and allowlists** for humans and for agentic Robinhood trading—not a second ledger.

## Strategy

- **Weekly DCA** into thesis-aligned names when policy floors allow (see `treasury/` DCA governor / RH BP floor).
- **Modernized 60/40** target structure (see below)—not classic bonds/equities.
- **Agentic RH** may only trade the agentic account; respect FCC policy and this thesis allowlist.

## Thesis (core)

Long **Bitcoin & hard money**, **AI**, and **digital credit**, with **energy** as an over-arching theme:

| Theme | Idea | Examples |
|-------|------|----------|
| **Bitcoin & hard money** | Store of value / hard money stack | BTC; gold/silver (e.g. PAXG, GLDM or similar—optional sleeve) |
| **Digital credit** | BTC-linked corporate / structured credit proxies | MSTR, STRC, ASST, SATA |
| **Bitcoin infrastructure** | Mining / energy-intensive BTC infra | MARA, RIOT, CLSK, WULF, IREN |
| **AI stack** | Infrastructure (hardware) through foundational models and app layers (software) | Broad AI exposure over time; not limited to “chips only” |
| **Equity / growth (stocks sleeve)** | Non-BTC-primary growth names in the “60” | e.g. TSLA, SPCX, BITA |
| **Energy** | Overarches BTC mining, AI infra, and hard-money macro | **No fixed tickers today**; agentic manager may add options with user approval |

### Macro framing

- **AI** = full stack: infra/hardware, foundation models, and application software—not only semiconductor hardware.
- **Bitcoin + digital credit + miners** = the hard-money / BTC complex.
- **Energy** is the shared constraint (mining, AI power, electrification); opportunistic, not a mandatory line-item yet.

## Target structure — “modernized 60/40”

| Sleeve | Target weight | What goes here |
|--------|---------------|----------------|
| **Bitcoin & digital credit complex** | **~40%** | BTC, digital credit (MSTR / STRC / ASST / SATA), BTC infra/miners (MARA, RIOT, CLSK, WULF, IREN), and related hard-money (gold/silver if held) |
| **Stocks / growth** | **~60%** | Broader equity and growth names aligned with AI stack and growth equities (e.g. TSLA, SPCX, BITA); room for energy or AI names the agent proposes |

Weights are **targets for the investable equity+crypto book** (not cash buffers, Morpho collateral, or One Card float). Rebalance when FCC shows room above floors and user/agent policy allows.

## Data source

| Source | Role |
|--------|------|
| **Financial Command Center** | **Primary** — live RH (primary + agentic), Coinbase liquid/vault manual fields, YNAB checking/card, expense estimates, policy stress |
| This `investment/` folder | Thesis, target weights, position allowlist, notes for agentic RH |

Do **not** treat this markdown as authoritative for quantities or prices; refresh FCC (MCP + `treasury/rh_sync.py` / `run_treasury.py`) for those.

## Active position allowlist (agentic + thesis book)

See [positions.md](./positions.md). Current named holdings focus:

BTC, MSTR, SATA, STRC, MARA, RIOT, CLSK, WULF, IREN, TSLA, SPCX, BITA  

Energy: open for agentic proposals (no fixed list).

## Portfolio assessment

### Strengths
- Coherent **BTC + digital credit + miners** sleeve with explicit ~40% target.
- **AI** framed as full stack (infra → models → apps), not only semis.
- **Energy** recognized as cross-cutting (mining + AI power).
- Clear split for agentic RH: 40% BTC-complex vs 60% stocks/growth.

### Risks
- High correlation within the BTC-complex and growth equities in risk-off.
- Miner and digital-credit names can be more volatile than spot BTC.
- Small agentic account / thin RH book vs target weights—funding and DCA matter.
- Gold/silver hard-money sleeve optional and may be empty.

### What this is *not*
- Classic 60/40 stocks/bonds.
- Kalshi or prediction-market book (removed).
- A substitute for FCC live data.

## Related

- Action items / automation: [treasury-action-items.md](./treasury-action-items.md)
- Policy / DCA: `treasury/policy.py`, `treasury/config.json`
- RH agentic skill: Grok skill `robinhood-agentic`
