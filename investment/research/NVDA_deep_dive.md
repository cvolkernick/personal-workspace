# NVDA deep dive (NVIDIA)

**Report run:** 2026-08-04 (inline multi-agent style: frame → market/thesis/risk → critic → synthesize)  
**Process:** Emulated `/position-deep-dive symbol=NVDA` (host workflow not invoked this session)  
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** on free capital; **not** an order  
**Sleeve if owned:** `stocks_growth` · **Priority:** high · **Core allowlist:** no  
**On watchlist:** yes · **Held agentic:** no  
**Agentic book frame (snapshot 2026-08-04):** NAV ~**$181** · BP/cash ~**$0.09** · held **MSTR, MARA, TSLA, SPCX, BITA, IREN, CLSK** · deployed ~40% BTC-complex / ~60% stocks_growth; in band
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority. auto_buy remains false.**
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`). Periodic refresh. Strong theme bias; core allowlist still preferred for routine rebalance.


---

## Executive findings

1. **Primary pure-play AI accelerator franchise.** Data-center GPU + CUDA software moat remains the default public **AI infra** equity. Strongest thematic match to “AI stack” among watchlist names.

2. **Scale and profitability are extraordinary.** Yahoo: price ~**$212**, mkt cap ~**$5.1T**, trailing P/E ~**31–33×**, EPS TTM ~**$6.52**, revenue TTM ~**$253B**, profit margin ~**63%**, ROE elevated. Q1 FY27 revenue print color ~**$81.6B** on Yahoo analysis strip. Forward P/E ~**23×**, PEG ~**0.55** on Yahoo (growth-adjusted multiple less extreme than raw AI software).

3. **Concentration / competition / export policy are the critic stack.** Customer concentration (hyperscalers), custom ASIC competition, China export rules, and **valuation sensitivity to any growth deceleration** remain the kill-switches.

4. **Next catalyst:** Earnings est. ~**2026-08-26** — event risk before any new size if capital appears into that window.

5. **Book fit:** Best pure AI expression; high beta (~**2.2**). Correlated with growth tech; does **not** diversify energy or BTC complex.

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** |
| Promote to core allowlist? | **No (optional later if owner wants AI silicon as permanent core)** |
| Status | **`ready`** (homework done; proposal-eligible) |
| Starter size *now* (BP ~$0.09)? | **No — capital dust; wait for free BP** |
| Auto-buy? | **Never** |
| Theme fit | **Very strong — pure AI infra** |
| Relative preference vs other watchlist | **Co-lead with GOOGL for first AI sleeve dollar (silicon vs full-stack cash compounder tradeoff)** |
| Next deep-dive refresh | **Post next earnings (~2026-08-26 est.) or material demand/export shock** |

**One-line for the fund team:**  
*NVDA is **ready** — dominant AI infra homework done; prefer meaningful free capital and respect Aug 26 event window; never auto-buy.*

---

## Frame / policy

| Field | Value |
|-------|--------|
| Symbol | **NVDA** |
| Name | NVIDIA Corporation |
| Theme | ai_stack · semiconductors · data_center · gpus |
| Sleeve if owned | `stocks_growth` |
| Deep-dive required before buy | Satisfied by this report (still need quorum to order) |
| Sources | Yahoo Finance quote/key stats pages fetched ~2026-08-04; company public narrative; book snapshot `treasury/snapshots/fund_manager_latest.json` |

---

## Market snapshot (public quotes ~2026-08-04)

| Metric | Approx | Source |
|--------|--------|--------|
| Price | ~$212 | Yahoo NVDA quote |
| Market cap | ~$5.1T | Yahoo |
| Trailing P/E | ~31–33× | Yahoo |
| Forward P/E | ~23× | Yahoo |
| EPS (TTM) | ~$6.52 | Yahoo |
| Revenue (TTM) | ~$253B | Yahoo |
| Profit margin | ~63% | Yahoo |
| Cash (mrq) | ~$53B | Yahoo |
| Beta | ~2.21 | Yahoo |
| 52w range | ~$164–$237 | Yahoo |
| Next earnings (est.) | ~2026-08-26 | Yahoo |

**Finding:** Liquidity is **not** the constraint on RH fractional. **Opportunity cost vs core allowlist, theme correlation with held TSLA/SPCX, and deployable capital** are.

---

## Thesis fit (north star)

| Strategy theme | Linkage |
|----------------|---------|
| AI | **Core pure-play** training/inference silicon + networking |
| Energy | Demand driver for power (complements BE theme, different expression) |
| Bitcoin | Unrelated (GPU mining legacy is not the thesis) |

**Finding:** If the agentic book wants **listed AI silicon**, NVDA is the default. Thesis does not require holding both NVDA and max TSLA growth beta without a correlation budget.

---

## Risk / critic (fail-closed notes)

- **Growth deceleration / multiple compression** is the primary drawdown mode.
- **Export controls / sovereign compute** can reprice demand geography.
- **Competition:** custom silicon + AMD/others chip away at edges.
- **Event:** Aug 26 print — avoid “guess the print” with tiny tickets.
- **Critic:** Size only with free capital + explicit reject of alternatives logged.

---

## Portfolio construction for *this* book

- NAV ~$181 with BP ~$0.09: any first ticket should wait for **meaningful free capital** (min ticket / settlement hygiene).
- Stocks sleeve already **~60% of deployed** via **TSLA + SPCX**. Adding mega-cap AI is theme-correct but **increases Mag-7 / growth-factor correlation** with existing growth sleeve.
- Prefer **core residual** first when free capital is small and BTC-complex still missing STRC/SATA per owner 2026-07-27 prefs — *unless* Thesis argues watchlist AI fills a larger gap than another miner/credit ticket.
- When free capital appears: treat this name as **in the consider set** with core allowlist peers; log reject-with-reasons if not sized.

---

## Verified claims (source-backed)

| ID | Claim | Evidence | Confidence |
|----|-------|----------|------------|
| N1 | NVDA ~$212, mkt cap ~$5.1T | Yahoo quote | high |
| N2 | TTM rev ~$253B, margin ~63%, EPS ~$6.52 | Yahoo stats | high |
| N3 | Trailing P/E ~31–33×, forward ~23× | Yahoo stats | high |
| N4 | Next earnings ~2026-08-26 | Yahoo | high |
| N5 | Business: data-center AI platforms + CUDA software moat | Yahoo profile / Morningstar blurb | high |

---

## Open questions for next refresh

1. Data-center growth rate next print vs Street?  
2. Networking vs GPU mix?  
3. Max AI silicon weight vs full-stack GOOGL?

---

*Written 2026-08-04 by Nakatoshi (CFO) — owner watchlist loop-close. Research only; no orders.*
