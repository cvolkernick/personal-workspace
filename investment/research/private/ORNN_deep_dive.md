# Ornn (private) — deep dive v0

> **Frame update 2026-08-12 (owner):** Ornn is watched as the **project that enables commoditized compute futures** — not as a private-equity allocation candidate / ticker. Removed from `private_watchlist.json`. Deep dive kept as background on the OCPI/ICE path only.


**As of:** 2026-08-12  
**Id:** `ORNN`  
**Liquidity:** private (pre-IPO)  
**Deploy today:** **No** — not on agentic consider set  
**Owner trigger:** Chris put “this” on watch — [Yahoo Finance commodities article](https://finance.yahoo.com/markets/commodities/articles/startup-bets-investors-want-trade-092007400.html) (Ornn seed / compute marketplace)  
**Primary sources:** [a16z crypto invest note](https://a16zcrypto.com/posts/article/investing-in-ornn/) (2026-06-24); [ICE press](https://ir.theice.com/press/news-details/2026/ICE-and-Ornn-to-Launch-GPU-Compute-Futures-Contracts/default.aspx) (2026-05-19); Axios/Bloomberg/Yahoo secondary (2026-06/07)

---

## 1. What the company is

Ornn is building **commodity-market infrastructure for AI compute**:

1. **Price discovery** — Ornn Compute Price Index (**OCPI**): GPU compute spot prices from **cleared/printed transactions** (not scraped list prices), spanning hardware types, regions, contract durations. Live on **Bloomberg Terminal**.  
2. **Token layer** — Ornn Token Price Indices (**OTPI**): realized **inference token** costs from major model providers (Anthropic/OpenAI-class — per a16z note / PR).  
3. **Risk transfer** — Planned **futures** (with **ICE**) so operators/enterprises can hedge forward GPU rental economics; residual-value products aimed at replacing crude straight-line GPU depreciation; capital-markets products for exposure to data-center economics without operating hardware.

Founders **Kush Bavaria** and **Wayne Nelms** (MIT; markets + quant + systems). Seed **$33M** led by **a16z / a16z crypto** (~2026-06), with Galaxy Ventures, Nordstar, SV Angel and others (press).

**Business model (inferred, not audited):** sell/standardize **data + market infrastructure** (index licensing, venue, structured products) — closer to an **index provider + derivatives franchise** than a GPU rental marketplace alone. a16z frames it as making compute “as financeable as oil or real estate.”

---

## 2. Why Chris might care (book linkage)

| Book sleeve | Link |
|-------------|------|
| **NVDA / silicon** | OCPI residual-value framing is a **macro input** to GPU useful life, rental rates, and depreciation narratives that drive NVDA cycle debates |
| **Energy / AI power (BE, Boom private)** | Transparent compute $/hr + power constraint = joint scarcity story; Ornn prices the *compute* side |
| **ICE (public)** | Exchange partner for planned GPU futures — **only clear public ticker** directly in the product path today |
| **Chamath AI stack** | Fills “missing market plumbing” between silicon and clouds — does **not** replace LPS or harness |
| **Private stack** | Complements Anduril/Saronic/Boom as **financial layer** on AI buildout, not a defense or power OEM |

---

## 3. Product / market maturity

| Milestone | Status (as of research date) | Source class |
|-----------|------------------------------|--------------|
| OCPI live | Yes — multi-SKU GPU indices; example H100 print cited on ornn.com marketing (~$2.67 class — treat as marketing snapshot) | Company site + Bloomberg distribution PR |
| OCPI on Bloomberg | Yes (PR ~2026-04-02) | Company PR |
| OTPI (token costs) | Claimed launched / extended per a16z | Investor note |
| ICE GPU futures | **Announced**, cash-settled USD, OCPI reference, **pending regulatory approval** | ICE IR 2026-05-19 |
| Retail/agentic access | **None** for Ornn equity or OCPI futures today | Inference |
| IPO / public equity | **None** | Press |

**Honest gap:** Seed + index + exchange *announcement* is strong **institutional narrative**. Cleared open interest, real hedge adoption, and reg-approved futures **are not yet proven** in public data we can verify from this pass.

---

## 4. Bull case

1. Compute is becoming a multi-trillion industrial asset with **oil-like need for forward curves** — if true, first credible index + futures franchise can be a durable data/exchange business.  
2. **ICE** distribution is a legitimacy step most AI seed companies never get.  
3. Residual-value products attack a real CFO pain (GPU depreciation vs market).  
4. Theme alignment with existing book (AI infra, power, ICE) means **watching is cheap**; no new capital required to monitor.

## 5. Bear / failure modes

1. **Indices without liquidity** — benchmarks that nobody hedges against die as marketing.  
2. **Regulatory / product delay** — futures “pending approval” can stall years.  
3. **Hyperscaler opacity** — if true prices stay in private contracts, transaction-based indices may thin or bias.  
4. **Competition** — exchanges, data vendors, or GPU clouds publish competing series.  
5. **Private forever** — no IPO; secondary illiquid; agentic book never gets a ticker.  
6. **Wrong instrument for our size** — even if futures list, notional/margin may not fit agentic dust NAV.

## 6. Public expressions (if thesis is “compute commodity markets”)

| Expression | Role | Status |
|------------|------|--------|
| **ICE** | Exchange partner; futures infrastructure pure-play | Public; **not** yet on public watchlist — candidate if futures go live |
| **NVDA** | Underlying asset residual value | Already public watchlist `ready` |
| **GPU cloud / landlords** (e.g. CRWV-class if held) | Spot/rental economics | Case-by-case |
| **Ornn equity** | Direct | Private only |

**Do not** invent an Ornn equity buy. **Do** flag ICE + NVDA residual narrative when futures milestones hit.

---

## 7. Capital / SIC gate

- **Private market authority:** false (fund policy).  
- **Cash stack / red free-dollar freeze:** no discretionary spend to “get in” via angel/secondary.  
- **Deploy:** only after public path + deep-dive refresh + residual BP.

Nakatoshi owns ongoing capital ranking vs other private names and any future size recommendation. Grok owns SIC visibility + eng/process only if a public product requires tooling (not required now).

---

## 8. Verdict

| Field | Value |
|-------|--------|
| **Status** | `private` |
| **Homework** | Done (v0) |
| **Ready for public deploy consider set?** | **No** |
| **Ready for private watch / periodic refresh?** | **Yes** |
| **Refresh cadence** | 30d monitor / 90d deep-dive (private_watchlist policy) |
| **Next real catalysts** | ICE futures approval + first trade; OCPI adoption metrics; next financing; any S-1 |
| **last_verdict** | `private_watch_ai_compute_markets_homework_done` |

---

## 9. Sources

1. a16z crypto — *Investing in Ornn: A Market for Compute* (2026-06-24)  
2. ICE IR — *ICE and Ornn to Launch GPU Compute Futures Contracts* (2026-05-19)  
3. Yahoo Finance / Axios secondary — seed narrative (2026-07-06 class)  
4. ornn.com product marketing (OCPI SKUs) — treat prices as non-SoT  
5. Nest cross-links: `RESEARCH/CHAMATH_AI_STACK_AUG2026.md`, public `NVDA_deep_dive.md`, private Anduril/Saronic/Boom stack
