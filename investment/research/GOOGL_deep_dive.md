# GOOGL deep dive (Alphabet / Google)

**Report run:** 2026-08-04 (inline multi-agent style: frame → market/thesis/risk → critic → synthesize)  
**Process:** Emulated `/position-deep-dive symbol=GOOGL` (host workflow not invoked this session)  
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** on free capital; **not** an order  
**Sleeve if owned:** `stocks_growth` · **Priority:** high · **Core allowlist:** no  
**On watchlist:** yes · **Held agentic:** no  
**Agentic book frame (snapshot 2026-08-04):** NAV ~**$181** · BP/cash ~**$0.09** · held **MSTR, MARA, TSLA, SPCX, BITA, IREN, CLSK** · deployed ~40% BTC-complex / ~60% stocks_growth; in band
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority. auto_buy remains false.**
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`). Periodic refresh. Strong theme bias; core allowlist still preferred for routine rebalance.


---

## Executive findings

1. **Full-stack AI mega-cap with cash engine.** Search/ads + YouTube + GCP + Gemini/AI infra is the cleanest single-name public expression of **AI + cash-flow compounder** on our watchlist — maps directly to open AI theme under ~60% stocks/growth.

2. **Valuation is relatively sane vs peers.** Yahoo key stats (~2026-07-31 / quote ~2026-08-04): price ~**$378**, market cap ~**$4.6T**, trailing P/E ~**18–19×**, EPS (TTM) ~**$19.95**, revenue (TTM) ~**$446B**, net margin ~**55%**, cash ~**$242B**. Forward P/E ~**17×**. That is **not** a pure multiple-expansion story the way high-P/S software is.

3. **AI capex / FCF tension is the main fundamental risk.** Street narrative emphasizes rising AI data-center capex and lease burden; FCF conversion can compress even when revenue/EPS print well. Cloud backlog strength is a positive offset (Big Tech cloud backlog narrative multi-trillion scale across hyperscalers).

4. **Regulatory / ad-cycle risk remains structural** (antitrust, ad market cyclicality) but is already a multi-year known.

5. **Book fit:** Excellent theme fit; liquid; fractional-friendly. **Not** a substitute for BTC/engines. Competes for residual dollars with NVDA/AAPL and core TSLA/SPCX top-ups.

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** |
| Promote to core allowlist? | **No (not yet — keep watchlist/ready)** |
| Status | **`ready`** (homework done; proposal-eligible) |
| Starter size *now* (BP ~$0.09)? | **No — capital dust; wait for free BP** |
| Auto-buy? | **Never** |
| Theme fit | **Strong — AI stack + growth cash compounder** |
| Relative preference vs other watchlist | **Top-tier watchlist candidate among liquid AI mega-caps; prefer over PLTR for first AI sleeve dollar if only one name** |
| Next deep-dive refresh | **Next earnings (~2026-10-28 est.) or material FCF/capex guide shift** |

**One-line for the fund team:**  
*GOOGL is homework-complete and **ready for consideration** on free capital — liquid AI + cash compounder at a non-extreme multiple; still not auto-buy and BP is dust today.*

---

## Frame / policy

| Field | Value |
|-------|--------|
| Symbol | **GOOGL** |
| Name | Alphabet Inc. (Class A) |
| Theme | ai_stack · cloud · foundation_models · advertising |
| Sleeve if owned | `stocks_growth` |
| Deep-dive required before buy | Satisfied by this report (still need quorum to order) |
| Sources | Yahoo Finance quote/key stats pages fetched ~2026-08-04; company public narrative; book snapshot `treasury/snapshots/fund_manager_latest.json` |

---

## Market snapshot (public quotes ~2026-08-04)

| Metric | Approx | Source |
|--------|--------|--------|
| Price | ~$378 | Yahoo GOOGL quote |
| Market cap | ~$4.6T | Yahoo |
| Trailing P/E | ~19× | Yahoo |
| Forward P/E | ~17× | Yahoo |
| EPS (TTM) | ~$19.95 | Yahoo |
| Revenue (TTM) | ~$446B | Yahoo |
| Profit margin | ~55% | Yahoo |
| Cash (mrq) | ~$242B | Yahoo |
| Beta (5Y) | ~1.24 | Yahoo |
| 52w range | ~$194–$409 | Yahoo |
| Next earnings (est.) | ~2026-10-28 | Yahoo |

**Finding:** Liquidity is **not** the constraint on RH fractional. **Opportunity cost vs core allowlist, theme correlation with held TSLA/SPCX, and deployable capital** are.

---

## Thesis fit (north star)

| Strategy theme | Linkage |
|----------------|---------|
| AI | **Direct** — models (Gemini), cloud AI, YouTube/search distribution |
| Energy | Indirect via data-center power demand (not a pure energy play) |
| Bitcoin | Unrelated (no BTC treasury thesis) |
| Autonomy / robotics | Partial (Waymo / Other Bets — not the core investable case for this book) |

**Finding:** Among watchlist AI names, GOOGL best balances **AI optionality + cash generation + non-extreme multiple**.

---

## Risk / critic (fail-closed notes)

- **Capex / FCF:** AI infra spend can disappoint FCF bulls even on revenue beats.
- **Correlation:** High with Mag-7 / growth factor; already own TSLA+SPCX in growth sleeve.
- **Reg:** Antitrust / ad regulation tail risk.
- **Critic gate for size:** Prefer when free capital ≥ min ticket *and* Thesis can justify vs STRC/SATA residual priority and vs NVDA pure-play.
- **Fail-closed:** Do not promote to core from this dive alone.

---

## Portfolio construction for *this* book

- NAV ~$181 with BP ~$0.09: any first ticket should wait for **meaningful free capital** (min ticket / settlement hygiene).
- Stocks sleeve already **~60% of deployed** via **TSLA + SPCX**. Adding mega-cap AI is theme-correct but **increases Mag-7 / growth-factor correlation** with existing growth sleeve.
- Prefer **core residual** first when free capital is small and BTC-complex still missing STRC/SATA per owner 2026-07-27 prefs — *unless* Thesis argues watchlist AI fills a larger gap than another miner/credit ticket.
- When free capital appears: treat this name as **in the consider set** with core allowlist peers; log reject-with-reasons if not sized.

---

## Verified claims (source-backed)

| ID | Claim | Evidence / locator | Confidence |
|----|-------|--------------------|------------|
| G1 | GOOGL trades ~$378 with mkt cap ~$4.6T | Yahoo Finance quote GOOGL ~2026-08-04 | high |
| G2 | Trailing P/E ~19×, EPS TTM ~$19.95 | Yahoo key statistics | high |
| G3 | TTM revenue ~$446B, cash ~$242B | Yahoo key statistics | high |
| G4 | Segments: Google Services, Google Cloud, Other Bets; Cloud includes AI infra / Gemini enterprise | Yahoo profile / company description | high |
| G5 | Agentic BP insufficient for first buy today | fund_manager_latest.json BP ~$0.09 | high |

---

## Open questions for next refresh

1. GCP growth rate and AI contribution vs MSFT/AMZN peers next print?  
2. Capex guide vs FCF trajectory for 2H26–2027?  
3. Desired max weight for AI mega-cap sleeve vs TSLA/SPCX core?

---

*Written 2026-08-04 by Nakatoshi (CFO) — owner watchlist loop-close. Research only; no orders.*
