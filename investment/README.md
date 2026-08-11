# Investment Portfolio

> **Liquidity UI / book of record:** [Financial Command Center](../financial-command/index.html)  
> Serve from repo root (`python3 launch.py` or `python3 financial-command/server.py`). FCC aggregates Coinbase, Robinhood (primary + agentic), YNAB, and sheet estimates.

Tracking for crypto, equities, and related sleeves. **Canonical live balances, buying power, and RH positions come from the FCC** (treasury snapshots + MCP). This folder holds **thesis, target structure, and allowlists** for humans and for agentic Robinhood trading—not a second ledger.

## Strategy

- **Actively managed** equity book via an **agentic fund manager** (Robinhood Trading MCP) — not a fixed weekly DCA schedule.
- **Modernized 60/40** target structure (see below)—not classic bonds/equities.
- Manager may rebalance, rotate within the allowlist, and size into strength/weakness subject to **guardrails** (FCC liquidity floors, max trade size, approval mode).
- **Agentic RH** may only place orders on the **agentic** account; primary margin is read/policy unless you change that elsewhere.
- Optional **DCA-like** top-ups remain allowed when the manager chooses them; they are **not** the default cadence.

### Dual-venue Morpho yield loops (cash float)

Parallel “borrow against risk assets → park stablecoin in Morpho yield” on **both** brokers, plus **X Money** cash yield:

| Venue | Collateral / funding | Stablecoin / cash parked for yield | Product |
|-------|----------------------|-------------------------------------|---------|
| **Coinbase** | BTC as Morpho **loan collateral** → borrow **USDC** | High Yield USDC Morpho vault (working float) | Coinbase Lend / Morpho High Yield |
| **Robinhood** | **Equity book** (margin / buying power against stocks) → free cash → buy **USDG** | **Robinhood Earn**: lend USDG onchain via self-custody wallet into a **Morpho** vault (~**7%** estimated APY, variable; promo + protocol) | [Robinhood Earn](https://robinhood.com/us/en/support/articles/crypto-earn/) |
| **X Money** | Spend / float cash (YNAB “Checking – ####”) | Account cash balance | **X Money** ~**6% APY** on cash (product rate; confirm in app) |

**Confirmed naming:** product is **Robinhood Earn** on **USDG** (Paxos dollar stablecoin), powered by **Morpho** (Steakhouse-curated vault). Not FDIC/SIPC deposit insurance; rate is an estimate (protocol APR + incentives), can change; not “bank cash yield.”

**X Money:** separate cash sleeve from RH Checking ACH float. Balance via YNAB/Plaid; APY tracked in config as `ynab.x_money_apy_est` (default **0.06**). Prefer parking idle spend float here vs 0% checking when rates hold.

**How RH Earn shows in balances:** Official docs say active principal + lifetime rewards are **rolled into overall portfolio / crypto holdings** for convenience; lent USDG sits in a **self-custody wallet** + Morpho contracts—not a separate brokerage “cash” line. **MCP `get_portfolio` is unlikely to expose a clean Earn field** → track **manually in FCC Settings** (`robinhood.usdg_earn_usdg`).

**Strategy intent (mirror CB):**
1. Size equity collateral carefully (margin use vs liquidation risk).
2. Deploy freed cash into **USDG → Earn (Morpho)** rather than idle cash.
3. Treat Earn balance as **RH yield sleeve** (like CB vault), not agentic equity alpha.
4. Keep LTV / margin heat monitored; yield does not justify forced liquidation risk.

## Thesis (core)

Long **Bitcoin & hard money**, **AI**, and **digital credit**, with **energy** as an over-arching theme:

| Theme | Idea | Examples |
|-------|------|----------|
| **Bitcoin & hard money** | Store of value / hard money stack | BTC; gold/silver (e.g. PAXG, GLDM or similar—optional sleeve) |
| **Digital credit** | BTC-linked corporate / structured credit, yield, and fixed-income proxies | MSTR, **STRC**, **SATA**, ASST, **BITA** — **small bias within 40%** to STRC/SATA (BTC-fundamental high-yield / frequent dividends; yield edge vs USDC/USDG cash; not MSTR-only by habit) |
| **Bitcoin infrastructure** | Mining / energy-intensive BTC infra | MARA, RIOT, CLSK, WULF, IREN |
| **AI stack** | Infrastructure (hardware) through foundational models and app layers (software) | Broad AI exposure over time; not limited to “chips only” |
| **Equity / growth (stocks sleeve)** | Non-BTC-primary growth names in the “60” | e.g. TSLA, SPCX |
| **Energy** | Overarches BTC mining, AI infra, and hard-money macro | Watchlist (e.g. **BE** Bloom Energy); deep-dive before first buy |

### Macro framing

- **AI** = full stack: infra/hardware, foundation models, and application software—not only semiconductor hardware.
- **Bitcoin + digital credit + miners** = the hard-money / BTC complex.
- **Digital credit (STRC/SATA):** **Small bias inside the ~40% stack** toward a real STRC/SATA seat on deploys — BTC-fundamental high-yield / frequent-dividend credit, not cash. Owner 2026-08-04: yields roughly ~2× typical USDC/USDG cash, so prefer STRC/SATA over pure cash-like residual when deploying into the complex. Not “covered by MSTR+BITA” as a default skip; not 40% all-credit.
- **Miners:** Diversify across multiple names (MARA, IREN, CLSK, RIOT, WULF, …). Multi-miner is intentional; do not reject for “overlap” alone.
- **Energy** is the shared constraint (mining, AI power, electrification); opportunistic, not a mandatory line-item yet.

## Target structure — “modernized 60/40”

| Sleeve | Target weight | What goes here |
|--------|---------------|----------------|
| **Bitcoin & digital credit complex** | **~40%** | BTC, digital credit / BTC yield (MSTR, STRC, ASST, SATA, **BITA**), BTC infra/miners (MARA, RIOT, CLSK, WULF, IREN), and optional hard-money metals (gold/silver if held—not required) |
| **Stocks / growth** | **~60%** | Broader equity and growth names aligned with AI stack and growth equities (e.g. TSLA, SPCX); room for energy or AI names the agent proposes |

Weights are **targets for the investable equity book** (not cash buffers, CB Morpho collateral, RH USDG Earn sleeve, or One Card float). The agentic manager steers equity toward 60/40 (stocks/BTC+); **USDG Earn is a separate RH yield sleeve**, parallel to CB High Yield vault.

**Watchlist:** thematic monitor/consider list in [`watchlist.json`](./watchlist.json) (not the same as core allowlist). Deep-dives via workflow `position-deep-dive` → reports in [`research/`](./research/).

**Private companies watchlist:** pre-IPO / unlisted names in [`private_watchlist.json`](./private_watchlist.json) for future public allocation if/when they list. **Not** investable on the agentic book today; **not** in the deploy consider set; short briefs under [`research/private/`](./research/private/). On listing → promote to public `watchlist.json` + deep-dive before any size.

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
| **Policy** | [`fund_manager.json`](./fund_manager.json) — machine-readable rules |
| **Thesis** | Allowlist, 60/40 targets, themes (energy open) |
| **FCC / treasury** | Live weights via `treasury/fund_manager.py`; dual RH snapshot |
| **Robinhood MCP** | Quotes, positions, place/cancel **on agentic account only** |
| **Human** | **Fund agentic book** (sole risk budget); hard kill = withdraw capital |

### v1 rules (confirmed)

- **Weights:** agentic account only  
- **Approval:** none mid-pass — if capital is in agentic, it is fair game; owner may give **after-pass** feedback  
- **Max order notional:** none — manager discretion  
- **Risk control:** size of deposits to agentic only (cash account today)  
- **Uniform process:** every deploy runs research/rotate across themes + allowlist (held and unheld); size-invariant  

See [positions.md](./positions.md) and [FUND_MANAGER_RUNBOOK.md](./FUND_MANAGER_RUNBOOK.md); run `python3 treasury/fund_manager.py --write`.

## Related

- Action items / automation: [treasury-action-items.md](./treasury-action-items.md)
- Liquidity policy (floors / stress — not trade alpha): `treasury/policy.py`, `treasury/config.json`
- RH agentic skill: Grok skill `robinhood-agentic`
