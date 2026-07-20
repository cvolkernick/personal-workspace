# Investment Portfolio

> **Liquidity UI / book of record:** [Financial Command Center](../financial-command/index.html)  
> Serve from repo root (`python3 launch.py` or `python3 financial-command/server.py`). FCC aggregates Coinbase, Robinhood (primary + agentic), YNAB, and sheet estimates.

Tracking for crypto, equities, and related sleeves. **Canonical live balances, buying power, and RH positions come from the FCC** (treasury snapshots + MCP). This folder holds **thesis, target structure, and allowlists** for humans and for agentic Robinhood trading—not a second ledger.

## Strategy

- **Actively managed** book via an **agentic fund manager** (Robinhood Trading MCP) — not a fixed weekly DCA schedule.
- **Modernized 60/40** target structure (see below)—not classic bonds/equities.
- Manager may rebalance, rotate within the allowlist, and size into strength/weakness subject to **guardrails** (FCC liquidity floors, max trade size, approval mode).
- **Agentic RH** may only place orders on the **agentic** account; primary margin is read/policy unless you change that elsewhere.
- Optional **DCA-like** top-ups remain allowed when the manager chooses them; they are **not** the default cadence.

## Thesis (core)

Long **Bitcoin & hard money**, **AI**, and **digital credit**, with **energy** as an over-arching theme:

| Theme | Idea | Examples |
|-------|------|----------|
| **Bitcoin & hard money** | Store of value / hard money stack | BTC; gold/silver (e.g. PAXG, GLDM or similar—optional sleeve) |
| **Digital credit** | BTC-linked corporate / structured credit, yield, and fixed-income proxies | MSTR, STRC, ASST, SATA, **BITA** (BTC-based yield / fixed income) |
| **Bitcoin infrastructure** | Mining / energy-intensive BTC infra | MARA, RIOT, CLSK, WULF, IREN |
| **AI stack** | Infrastructure (hardware) through foundational models and app layers (software) | Broad AI exposure over time; not limited to “chips only” |
| **Equity / growth (stocks sleeve)** | Non-BTC-primary growth names in the “60” | e.g. TSLA, SPCX |
| **Energy** | Overarches BTC mining, AI infra, and hard-money macro | **No fixed tickers today**; agentic manager may add options with user approval |

### Macro framing

- **AI** = full stack: infra/hardware, foundation models, and application software—not only semiconductor hardware.
- **Bitcoin + digital credit + miners** = the hard-money / BTC complex.
- **Energy** is the shared constraint (mining, AI power, electrification); opportunistic, not a mandatory line-item yet.

## Target structure — “modernized 60/40”

| Sleeve | Target weight | What goes here |
|--------|---------------|----------------|
| **Bitcoin & digital credit complex** | **~40%** | BTC, digital credit / BTC yield (MSTR, STRC, ASST, SATA, **BITA**), BTC infra/miners (MARA, RIOT, CLSK, WULF, IREN), and optional hard-money metals (gold/silver if held—not required) |
| **Stocks / growth** | **~60%** | Broader equity and growth names aligned with AI stack and growth equities (e.g. TSLA, SPCX); room for energy or AI names the agent proposes |

Weights are **targets for the investable equity+crypto book** (not cash buffers, Morpho collateral, or One Card float). The agentic manager steers toward these bands when capital and FCC stress allow—not on a rigid calendar.

## Data source

| Source | Role |
|--------|------|
| **Financial Command Center** | **Primary** — live RH (primary + agentic), Coinbase liquid/vault manual fields, YNAB checking/card, expense estimates, policy stress |
| This `investment/` folder | Thesis, target weights, position allowlist, notes for agentic RH |

Do **not** treat this markdown as authoritative for quantities or prices; refresh FCC (MCP + `treasury/rh_sync.py` / `run_treasury.py`) for those.

## Active position allowlist (agentic + thesis book)

See [positions.md](./positions.md). Current named holdings focus:

**~40% complex:** BTC, MSTR, SATA, STRC, ASST, BITA, MARA, RIOT, CLSK, WULF, IREN  

**~60% stocks:** TSLA, SPCX  

Energy: open for agentic proposals (no fixed list). Gold/silver optional under ~40%.

## Portfolio assessment

### Strengths
- Coherent **BTC + digital credit + miners** sleeve with explicit ~40% target.
- **AI** framed as full stack (infra → models → apps), not only semis.
- **Energy** recognized as cross-cutting (mining + AI power).
- Clear split for agentic RH: 40% BTC-complex vs 60% stocks/growth.

### Risks
- High correlation within the BTC-complex and growth equities in risk-off.
- Miner and digital-credit names can be more volatile than spot BTC.
- Small agentic account / thin RH book vs target weights—**fund the agentic account** before meaningful automation.
- Gold/silver hard-money sleeve optional and may be empty.
- Active management still inherits BTC/tech correlation; guardrails matter more than cadence.

### What this is *not*
- Classic 60/40 stocks/bonds.
- A fixed **weekly DCA** program (superseded by agentic active management).
- Kalshi or prediction-market book (removed).
- A substitute for FCC live data.

## Agentic fund manager (operating model)

| Layer | Role |
|-------|------|
| **Thesis** (`investment/`) | Allowlist, 40/60 targets, themes (energy open) |
| **FCC / treasury** | Liquidity floors, stress, dual RH snapshot, human vs agent actions |
| **Robinhood MCP** | Quotes, positions, place/cancel **on agentic account only** |
| **Human** | Fund agentic book; set approval mode; expand allowlist; hard veto |

See [positions.md](./positions.md) for allowlist + manager rules; [treasury-action-items.md](./treasury-action-items.md) for wiring status.

## Related

- Action items / automation: [treasury-action-items.md](./treasury-action-items.md)
- Liquidity policy (floors / stress — not trade alpha): `treasury/policy.py`, `treasury/config.json`
- RH agentic skill: Grok skill `robinhood-agentic`
