# AMZN deep dive (Amazon.com)

**Report run:** 2026-08-24 (inline multi-agent: Plan → claims with sources → adversarial Verify → Critic fail-closed → Report)  
**Process:** Emulated `/position-deep-dive symbol=AMZN` (host workflow not invoked this session). Owner add 2026-08-22 was still `monitor` — homework debt closed this pass.  
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** on free capital; **not** an order  
**Sleeve if owned:** `stocks_growth` · **Priority:** medium-high (behind held GOOGL and NVDA into 8/26 print) · **Core allowlist:** no  
**On watchlist:** yes · **Held agentic:** no  
**Agentic book frame (live MCP 2026-08-24 ~14:10Z):** NAV ~**$227.54** · cash/BP **$0.09** · held **MSTR, STRC, SATA, BITA, MARA, IREN, CLSK, TSLA, SPCX, GOOGL** · deployed ~40.2% BTC-complex / ~59.8% stocks; in ±5% band  
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority. auto_buy remains false.**  
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`). Periodic refresh. Strong theme bias; core allowlist still preferred for routine rebalance.

---

## Executive findings

1. **AWS + retail + ads mega-cap under the 60% sleeve.** Amazon is a liquid AI/cloud expression (AWS, Trainium, Bedrock, advertising) plus a cash-generative commerce engine. Theme fit is real; it is **not** a substitute for BTC-complex, and it overlaps the already-held **GOOGL** AI/cloud seat.

2. **Q2'26 operating print was strong; GAAP EPS is not the operating story.** Company IR (2026-07-30): net sales **$200.6B** (+20%), operating income **$27.5B** (+43%), AWS sales **$42.2B** (+37%, fastest in 18 quarters), AWS operating income **$16.6B**. Net income **$62.6B / $5.75 diluted** includes **$53.4B pre-tax non-operating other income, primarily Anthropic investments**. Do not size off the $5.75 EPS print.

3. **Capex / FCF is the binding fundamental risk.** TTM operating cash flow **$161.4B** (+33%); TTM free cash flow **outflow $7.6B** on a **$66.1B** YoY increase in net PP&E, “primarily investments in artificial intelligence.” Street/WSJ color: 2026 capex guide raised to **~$220B**. Capacity spend can keep working if AWS demand holds; it is also the path where the multiple compresses.

4. **Valuation is not extreme vs NVDA/AAPL.** RH fundamentals 2026-08-24: last ~**$260.75**, market cap ~**$2.82T**, trailing P/E ~**20.8×**, P/B ~**5.1×**, 52w **$196–$287**. Cheaper than NVDA (~33×) and AAPL (~35×); richer than GOOGL (~17×) which we already hold.

5. **Book fit today:** Homework complete. **No size this pass** — cash/BP $0.09 < $1 min. On next free capital, AMZN is a valid AI/cloud diversifier **behind** adding to the GOOGL seat or waiting out NVDA’s 2026-08-26 AMC print. Not core. Never auto-buy.

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** |
| Promote to core allowlist? | **No** |
| Status | **`ready`** (homework done; proposal-eligible) |
| Starter size *now* (BP $0.09)? | **No — capital dust** |
| Auto-buy? | **Never** |
| Theme fit | **Strong — AI/cloud (AWS) + growth equity; weaker AI-purity than NVDA, more commerce cyclicality than GOOGL** |
| Relative preference vs other watchlist | **Behind held GOOGL and NVDA (into 8/26 print) for scarce residual; ahead of PLTR/EVGO; vs BE this is liquid mega-cap not energy equipment** |
| Next deep-dive refresh | **Next earnings (est. ~2026-10-29, *unconfirmed*) or material FCF/capex/AWS-growth shift; also refresh if Anthropic mark reverses hard** |

**One-line for the fund team:**  
*AMZN is homework-complete and **ready for consideration** — AWS/AI operating story is real, but GAAP EPS is Anthropic-mark noise, TTM FCF is negative on AI capex, and GOOGL already fills the first liquid AI mega-cap seat. Not auto-buy; BP is dust today.*

---

## Frame / policy

| Field | Value |
|-------|--------|
| Symbol | **AMZN** |
| Name | Amazon.com, Inc. |
| Theme | ai_stack · cloud · growth_equity · e_commerce · advertising |
| Sleeve if owned | `stocks_growth` |
| Deep-dive required before buy | Satisfied by this report (still need quorum to order) |
| Added | 2026-08-22 by owner (jot via Grok); dive delayed until this 2026-08-24 review |

---

## Plan (bounded questions)

| ID | Question | Angle |
|----|----------|--------|
| Q1 | What did Q2'26 actually print (sales, AWS, operating income vs GAAP NI)? | Claim / IR primary |
| Q2 | Is AWS still accelerating, and is that AI-driven? | Thesis fit |
| Q3 | What is the capex / FCF cost of that growth? | Risk |
| Q4 | How does valuation/liquidity compare to GOOGL/NVDA/AAPL on this book? | Relative value |
| Q5 | Event calendar — any near-term print that should block first size? | Timing |
| Q6 | Does AMZN add a *new* theme vs already-held GOOGL + TSLA/SPCX? | Opportunity cost |

---

## Research claims (with sources)

| ID | Claim | Evidence | Source locator | Confidence |
|----|--------|----------|----------------|------------|
| C1 | Q2'26 net sales $200.6B, +20% YoY vs $167.7B. | Company release 2026-07-30. | [Amazon IR Q2 2026](https://ir.aboutamazon.com/news-release/news-release-details/2026/Amazon-com-Announces-Second-Quarter-Results/) | 0.95 |
| C2 | AWS net sales $42.2B, +37% YoY (fastest in 18 quarters); AWS op. income $16.6B vs $10.2B. | Same IR; Jassy quote “36.7%”. | Amazon IR Q2 2026 | 0.95 |
| C3 | Operating income $27.5B (+43% vs $19.2B). GAAP net income $62.6B / $5.75 includes **$53.4B pre-tax non-operating other income, primarily Anthropic**. | IR explicit footnote on NI. | Amazon IR Q2 2026 | 0.95 |
| C4 | TTM OCF $161.4B (+33%); TTM FCF **outflow $7.6B** on +$66.1B YoY net PP&E, “primarily investments in artificial intelligence.” | IR cash-flow bullets. | Amazon IR Q2 2026 | 0.9 |
| C5 | Live RH mark ~$260.75; mkt cap ~$2.82T; trailing P/E ~20.8×; 52w $196–$287. Tight spread (~$0.07). | RH `get_equity_quotes` + `get_equity_fundamentals` 2026-08-24 ~14:10Z. | Robinhood MCP | 0.9 |
| C6 | Next earnings not company-confirmed; RH calendar shows 2026-10-29 pm `verified: false`; other calendars split 10/22 vs 10/29. | RH `get_earnings_results`; Wall Street Horizon / Yahoo / TipRanks disagree. | RH MCP + third-party calendars | 0.6 |
| C7 | NVDA reports **2026-08-26 AMC**, verified. | NVIDIA IR event + RH earnings (`verified: true`). | [NVIDIA IR](https://investor.nvidia.com/events-and-presentations/events-and-presentations/default.aspx); RH MCP | 0.95 |

**Uncertainties:** FY26 capex **$220B** is widely reported (WSJ 2026-07-31) but was **not** in the IR bullets fetched for this dive — treat as secondary until the 10-Q/guide table is re-read. Cloud growth vs Azure/GCP mix is narrative, not needed to size. Anthropic mark can reverse.

---

## Verify (adversarial, fail-closed)

| Claim | Verdict | Note |
|-------|---------|------|
| C1 sales | **Keep** | Primary IR. |
| C2 AWS | **Keep** | Primary IR; CNBC 2026-07-30 corroborates beat vs ~31% Street. |
| C3 NI / Anthropic | **Keep, with hard qualifier** | Fail-closed on using $5.75 as “earnings power.” Operating income $27.5B is the surviving operating claim. |
| C4 FCF | **Keep** | Primary IR; this is the bear case, not a nit. |
| C5 marks | **Keep** | Live venue. |
| C6 next print | **Partial** | Date is **tentative**. Do not treat 10/29 as confirmed. |
| C7 NVDA print | **Keep** | Relevant as *book* event risk (competing AI residual), not AMZN’s own print. |
| $220B capex guide | **Partial / not promoted to Keep** | Secondary press; not independently verified from IR HTML this pass. Directionally consistent with TTM FCF outflow. |

No Keep claim was dropped. Report is **Complete** on operating print + valuation; **Partial** on exact capex guide and next earnings date.

---

## Thesis fit (north star)

| Strategy theme | Linkage | Fit |
|----------------|---------|-----|
| AI stack | AWS, Trainium/Graviton, Bedrock, agent tooling | Strong, but **infrastructure+cloud** not silicon-pure |
| Growth equity | Commerce + ads + AWS | Strong |
| Energy | Data-center power user, not equipment vendor like BE | Weak / indirect |
| BTC / digital credit | None | None — stocks sleeve only |

AMZN **does** add AWS-scale cloud and a second hyperscaler vs GOOGL-only. It **does not** fill energy, miners, or digital credit. Opportunity cost vs topping **GOOGL** (already held, cheaper multiple) is the real allocation question, not “is AWS real.”

---

## Risk / Critic (fail-closed)

**Critic blocks / conditions:**
- **No first buy on dust BP.** $0.09 < $1 min.
- **Do not treat GAAP $5.75 EPS as run-rate.** Anthropic mark dominates NI.
- **Do not first-buy AMZN merely because GOOGL is “already owned.”** Relative value: GOOGL ~17× trailing vs AMZN ~21×; GOOGL is the existing AI mega-cap seat. Adding AMZN is a *second* hyperscaler, not the first AI dollar.
- **Do not size into NVDA’s 2026-08-26 print with the AI residual** if capital appears this week — that residual, if any, is a GOOGL top-up vs wait-for-NVDA question, not an AMZN opener.
- **FCF/capex can stay negative** if AI spend outruns AWS growth. That is a valid skip, not a “we already have GOOGL” skip.
- **Never auto-buy. Not core.**

**Not a block:** liquidity (mega-cap, ~3¢–7¢ spread); fractional tradability on agentic.

---

## Market snapshot (RH ~2026-08-24 14:10Z)

| Metric | Approx | Source |
|--------|--------|--------|
| Last / bid-ask | $260.75 · $260.71 × $260.78 | RH quotes |
| Chg vs 2026-08-21 close | +0.8% ($258.63) | RH |
| Market cap | ~$2.82T | RH fundamentals |
| Trailing P/E | ~20.8× | RH |
| P/B | ~5.1× | RH |
| 52-week | $196.00 – $287.20 | RH |
| Avg volume | ~33.4M | RH |
| Next earnings | est. 2026-10-29 pm, **unconfirmed** | RH earnings `verified: false` |

Peer marks same tape: **GOOGL** $348.02 (~17.3×, **held**), **NVDA** $209.25 (~32.9×, print 8/26 AMC), **AAPL** $312.69 (~35.5×), **BE** $193.44 (~274×).

---

## Verdict

**`ready_consider`** — consider-set from this pass forward. **No size until free capital ≥ $1** and Thesis/Risk/Critic re-open the 60% sleeve. Prefer GOOGL top-up or post-NVDA print before a first AMZN ticket unless AMZN is specifically the name winning relative-value on that future pass.

Research ≠ order.
