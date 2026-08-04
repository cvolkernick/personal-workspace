# BE deep dive (Bloom Energy) — post Q2'26 refresh

**Report run:** 2026-08-04 (inline multi-agent style: frame → market/thesis/risk → critic → synthesize)  
**Process:** Emulated `/position-deep-dive symbol=BE` (host workflow not invoked this session)  
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** on free capital; **not** an order  
**Sleeve if owned:** `stocks_growth` · **Priority:** high · **Core allowlist:** no  
**On watchlist:** yes · **Held agentic:** no  
**Agentic book frame (snapshot 2026-08-04):** NAV ~**$181** · BP/cash ~**$0.09** · held **MSTR, MARA, TSLA, SPCX, BITA, IREN, CLSK** · deployed ~40% BTC-complex / ~60% stocks_growth; in band
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority. auto_buy remains false.**
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`). Periodic refresh. Strong theme bias; core allowlist still preferred for routine rebalance.


---

## Executive findings

1. **Post-print operating story remains strong.** Yahoo analysis strip: Q2 FY26 revenue ~**$1.07B**, earnings ~**$196M**. Prior channel notes (watchlist): record rev ~$1.065B (+166% YoY), non-GAAP EPS ~$0.78, FY26 rev guide raised toward **$3.9–4.2B**. AI data-center power thesis **intact**.

2. **Equity still prices a large success case + high beta.** Yahoo: close ~**$207** (7/30, +26% day after print/upgrade color), overnight prints higher (~$224). Market cap ~**$61B**, trailing P/E ~**213–269×**, EPS TTM ~**$0.77**, beta ~**3.7**, 52w range ~**$33–$351**. Forward P/E ~**73×**. This is **not** a value entry.

3. **Prior 2026-07-23 dive blocked pre-print size — correct process.** Event risk cleared; **valuation + beta + opportunity cost** still dominate for a ~$181 agentic book.

4. **Theme fit remains best-in-list for Energy / AI power equipment** (generation gear into AI load). Complements miners (load) rather than duplicating them.

5. **Status under new owner policy:** homework refreshed → **`ready` for consideration** (not `pass`). Size can still be $0 when Risk/Critic prefer core or mega-cap AI on scarce capital.

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** |
| Promote to core allowlist? | **No** |
| Status | **`ready`** (homework done; proposal-eligible) |
| Starter size *now* (BP ~$0.09)? | **No — capital dust; wait for free BP** |
| Auto-buy? | **Never** |
| Theme fit | **Strong — purest energy/AI-power equipment on watchlist** |
| Relative preference vs other watchlist | **Top energy expression; vs AI mega-caps, take when free capital and energy sleeve is the gap being filled** |
| Next deep-dive refresh | **Next earnings (est. ~2026-10-27) or >25% drawdown from post-print highs** |

**One-line for the fund team:**  
*BE post-print is **ready** for consideration as energy/AI-power equipment — thesis strong, multiple/beta still harsh; no auto-buy; BP dust today.*

---

## Frame / policy

| Field | Value |
|-------|--------|
| Symbol | **BE** |
| Name | Bloom Energy Corporation |
| Theme | energy · electrification · ai_power · distributed_generation |
| Sleeve if owned | `stocks_growth` |
| Deep-dive required before buy | Satisfied by this report (still need quorum to order) |
| Sources | Yahoo Finance quote/key stats pages fetched ~2026-08-04; company public narrative; book snapshot `treasury/snapshots/fund_manager_latest.json` |

---

## Market snapshot (public quotes ~2026-08-04)

| Metric | Approx | Source |
|--------|--------|--------|
| Price | ~$207 close 7/30 (volatile; overnight higher) | Yahoo BE |
| Market cap | ~$61B | Yahoo |
| Trailing P/E | ~213–269× | Yahoo |
| Forward P/E | ~73× | Yahoo |
| EPS (TTM) | ~$0.77 | Yahoo |
| Revenue (TTM) | ~$3.1B | Yahoo |
| Q2 FY26 rev | ~$1.07B | Yahoo analysis strip |
| Cash (mrq) | ~$2.7B | Yahoo |
| Debt/Equity | ~172% | Yahoo |
| Beta | ~3.74 | Yahoo |
| 52w range | ~$33–$351 | Yahoo |

**Finding:** Liquidity is **not** the constraint on RH fractional. **Opportunity cost vs core allowlist, theme correlation with held TSLA/SPCX, and deployable capital** are.

---

## Thesis fit (north star)

| Strategy theme | Linkage |
|----------------|---------|
| Energy | **Direct pure-play** SOFC on-site generation |
| AI | Power bottleneck for AI factories / DC |
| BTC / miners | Complementary (generation vs load) |

**Finding:** Keep as primary energy opportunistic candidate.

---

## Risk / critic (fail-closed notes)

- **Valuation + beta:** violent tape; tiny tickets add noise without material NAV impact until capital scales.
- **Execution / partner concentration** on large frameworks.
- **Leverage / WC** for growth still matter (D/E elevated).
- **Critic:** Ready ≠ must-buy; prefer size when (a) free capital meaningful, (b) energy gap is the thesis priority, (c) not chasing a +26% post-print day.

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
| B1 | Q2 FY26 rev ~$1.07B on Yahoo strip; prior notes ~$1.065B beat + guide raise | Yahoo + watchlist notes | high |
| B2 | Mkt cap ~$61B, beta ~3.7, rich trailing multiple | Yahoo | high |
| B3 | SOFC Energy Server for on-site power; AI DC demand narrative | Yahoo profile + prior dive | high |
| B4 | Prior dive 2026-07-23 correctly blocked pre-print buy | BE_deep_dive history | high |
| B5 | BP dust — no size now | fund_manager_latest | high |

---

## Open questions for next refresh

1. Delivery cadence on multi-GW frameworks into 2027?  
2. Margin sustainability after print?  
3. Energy sleeve target weight when capital scales?

---

*Written 2026-08-04 by Nakatoshi (CFO) — owner watchlist loop-close. Research only; no orders.*
