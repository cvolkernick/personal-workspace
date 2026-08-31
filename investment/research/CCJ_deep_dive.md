# CCJ deep dive (Cameco) — owner-add 2026-08-31

**Report run:** 2026-08-31
**Process:** Emulated `/position-deep-dive symbol=CCJ` (host workflow not invoked this session)
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** on free capital; **not** an order
**Sleeve if owned:** `stocks_growth` (energy opportunistic) · **Priority:** high (owner-named, option A) · **Core allowlist:** no
**On watchlist:** yes (this write) · **Held agentic:** no
**Agentic book frame (FCC worktree `financial-command/treasury_latest.json` `as_of` 2026-08-31T06:37:24Z):** NAV **$227.33** · equity MV **$199.73** · cash/BP **$0.09 / $0.09** · deployed **40.6% / 59.4%** in ±5% band. Energy held: **none.** GOOGL $14 is the only watchlist seat filled. `auto_buy` false.
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority.**
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`). Periodic refresh. Strong theme bias; core allowlist still preferred for routine rebalance.
**Owner ask:** Chris `#Orchestration` `2b8d1eb2…` (reply to `aacfd0d6…`) confirmed option **A** = **CCJ + BWXT**. Source: Matthew Smith / Chronometer, *Invest Like the Best* + Colossus *I Got Gas*. Nuclear gap named in `strategy/bets.md` / Chamath LPS. PAVE/GRID reject explicitly left nuclear open.

**Not this ticker:** LEU (Centrus, enrichment) — north-star adjacent, **not named**. Brookfield BN/BAM/BEP — diluted 51% of Westinghouse; wait for WEC IPO. EXE — molecule option, **not named**.

---

## Executive findings

1. **CCJ is the named nuclear-fuel + Westinghouse seat.** Cameco is U3O8 production (McArthur River / Key Lake, Cigar Lake, JV Inkai) plus Fuel Services (conversion/fabrication) plus **49% of Westinghouse** (Brookfield 51%). That is the public instrument that closes the nuclear gap PAVE/GRID left open. It is **not** Bloom (SOFC equipment) and **not** BWXT (Navy + commercial hardware).

2. **Operating uranium is fine; ttm PE is noise.** Q2'26 (CAD unless noted): NI **$25M**, adj NI **$77M**, adj EBITDA **$391M**. Uranium EBT **$170M** / adj EBITDA **$252M**. Average realized **$93.13/lb** vs produced-and-purchased cash cost **$55.84/lb**. 2026 production guide **19.5–21.5M lb** (share) **unchanged** after Key Lake / McArthur / Cigar disruptions. Five-year contracted deliveries **>28M lb/year** average. Balance sheet: **$1.1B** cash, **$1.0B** debt, **$1.0B** undrawn revolver. The print *looks* weak because Westinghouse equity earnings fell (Dukovany 2025 one-time ~US$170M in Cameco's share). Do **not** size on ttm PE **~170×**.

3. **Westinghouse is the option, and it is now on an IPO clock.** Confidential Form S-1 submitted **2026-07-31**. Share count / price range **undetermined**. Cameco stays 49% until terms. Kill if the IPO **strands** the 49% (dilution, lockup, related-party terms that make CCJ a residual U3O8 miner with a marked-down JV). Do not buy BN/BAM/BEP as a "Westinghouse proxy" — CCJ already *is* the 49%.

4. **Theme fit is nuclear / LPS-adjacent, not "we need energy."** Energy equipment gap remains **BE**. CCJ is the name you take when the *question* is uranium + reactor OEM, not speed-to-power SOFC and not a 2028 gas curve.

5. **No size today.** FCC overall **red** (card + CB liquid + Morpho LTV **0.50** at max). One Card **$470.36**. HY LTV Buffer **$239** vs $1,000 floor (gap ~$761). BP **$0.09 < min_trade $1**. SIC cash-stack lock: no new free-dollar residual. Inclusion ≠ buy. Same as RKLB, STRK, BE.

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** (owner-named, option A) |
| Promote to core allowlist? | **No** |
| Status | **`ready`** (homework done; proposal-eligible) |
| Starter size *now*? | **No** — red-mode + dust BP + ttm multiple noise + 11-name consider-set tax |
| Auto-buy? | **Never** |
| Theme fit | **Strong as nuclear fuel + 49% Westinghouse.** Weak as a generic energy add. |
| Relative preference vs other consider names | Behind **BE** when the question is energy *equipment / speed-to-power*. **First nuclear name.** Ahead of BWXT (fuel before cycle). Out of the BTC-complex race. Not a basket with BE. |
| Next deep-dive refresh | Westinghouse S-1 terms / IPO; uranium contract-book roll-off; production guide cut; **>25%** from $100.01; or 90d age |

**One-line for the fund team:**
*CCJ is **ready** as the nuclear-fuel + 49% Westinghouse seat — uranium unit-economy is real; ttm PE is Westinghouse noise; IPO can help or strand the 49%; no size while residual is empty.*

---

## Frame / policy

| Field | Value |
|-------|--------|
| Symbol | **CCJ** (NYSE) / CCO (TSX) |
| Name | Cameco Corporation |
| Theme | `energy` · `nuclear` · `uranium` · `ai_power` |
| Sleeve | `stocks_growth` via `energy_opportunistic` |
| Added | 2026-08-31 by **owner** (Orchestration option A, via Grok SIC → Nakatoshi) |
| Core allowlist | No (energy sleeve remains opportunistic / open) |

---

## Market / company (as of 2026-08-28 close unless noted)

| Metric | Print | Source |
|--------|-------|--------|
| Last close | **$100.01** | RH `get_equity_quotes` official close 2026-08-28 (Labor Day weekend; no 08-29/08-30 session) |
| Market cap | **$43.56B** | RH fundamentals 2026-08-31 |
| ttm P/E · P/B | **170.0× · 8.66×** | RH; **do not size on ttm PE** |
| Shares / float | 435.5M / 434.5M | RH |
| 52-week | **$73.20 – $135.24** (high 2026-01-29) | RH |
| Avg volume (2w) | **~2.94M sh/day** | RH |
| Dividend | **$0.17** / sh annual; yield **~0.17%**; last payable 2025-12-16 | RH |
| HQ / CEO | Saskatoon · Timothy S. Gitzel | RH |
| Q2'26 NI / adj NI / adj EBITDA | **$25M / $77M / $391M** (CAD) | [Cameco Q2'26](https://www.cameco.com/invest/financial-information/quarterly-reports/2026/q2) |
| Uranium Q2 EBT / adj EBITDA | **$170M / $252M** | Cameco Q2'26 |
| Q2 realized U3O8 / cash cost | **$93.13 / $55.84** per lb | Cameco Q2'26 (FinanceFeeds recap of print) |
| Q2 packaged production (share) | McArthur/Key Lake **2.3M** lb; Cigar **1.6M** lb; total packaged **~3.9M** | Cameco Q2'26 |
| Q2 deliveries / inventory | **7.1M** lb delivered; inventory **8.7M** lb @ $58.05 | Cameco Q2'26 |
| 2026 production guide | **19.5–21.5M lb** (share) — unchanged | Cameco Q2'26 |
| 2026 uranium sales | **29–32M lb**; revenue **$2.70–2.91B**; realized **$91–96/lb** | Cameco Q2 MD&A outlook |
| Westinghouse | **49%**; Q2 share adj EBITDA **$163M** (vs $352M YoY; Dukovany 2025 one-time) | Cameco Q2'26 |
| W 2026 share adj EBITDA guide | **US$370–430M** | Cameco Q2 MD&A |
| W confidential S-1 | **2026-07-31** | [Cameco statement](https://www.cameco.com/sites/default/files/documents/2026-07-31-Statement-on-S1-Filing.pdf); [WNN](https://world-nuclear-news.org/articles/cameco-announces-go-public-plans-for-westinghouse) |

RH description covers Uranium + Fuel Services only. The **49% Westinghouse** is the second engine; do not drop it because the RH blurb is a miner.

---

## Thesis fit

**Northstar:** Energy, including nuclear (`strategy/bets.md`). Chamath LPS map treats nuclear (LEU/CCJ) as the closest *public* proxy for energized land + interconnect + shell. Maps to ~60% stocks/growth, **not** the 40% BTC complex.

**Bull case (real):**
- Highest-quality public U3O8 book (high-grade Athabasca) plus conversion/fabrication. Contracting discipline (lower 2026 planned deliveries) is the right problem.
- 49% of the AP1000 OEM / nuclear-services franchise. Cameco cites **57%** of the global operating fleet of 417 reactors on Westinghouse technology and a pipeline of **up to 91 AP1000** opportunities. That is long-cycle nuclear, which is Smith's "winner" sleeve.
- Closes a **named** book gap. Energy opportunistic was BE-only. PAVE/GRID reject left nuclear open on purpose.

**Why not instead of BE:**
- BE is SOFC *equipment / speed-to-power*. CCJ is fuel + OEM. Different bottleneck. "We need energy" still surfaces BE first (unheld equipment). "We need nuclear / LPS" surfaces CCJ.

**Why not Brookfield / LEU / EXE:**
- BN/BAM/BEP dilute the 51%. Wait for WEC.
- LEU is enrichment; Chris did not name it.
- EXE is the 2028 molecule option; Chris did not name it. Adding a Haynesville producer does not complete CCJ.

---

## Risks (material)

1. **Westinghouse IPO terms.** Confidential S-1. Could crystallize value or strand the 49% (primary issuance, related-party, lockup). Until terms, the "OEM option" is opaque.
2. **Westinghouse earnings volatility.** Q2 NI share **−$10M** vs +$126M YoY because 2025 had Dukovany construction revenue. New-build milestones are lumpy. ttm PE **~170×** is that lump, not a uranium multiple.
3. **Uranium price / contract mix.** Spot can fade while realized lags (and vice versa). Five-year book >28M lb/year is the floor; 2029–30 commitments are *below* that average — roll risk.
4. **Operational / geopolitical.** Athabasca disruptions (already happened this quarter; guide held). JV Inkai (Kazakhstan) purchase allocation **4.2M lb** in 2026.
5. **Consider-set cost.** Eleven `ready` names on a $227 book. Every deploy must name CCJ. High ≠ first dollar.
6. **Capital stack / red-mode.** Card, LTV at max, HY gap, SIC overdue-first. Residual-after-floors is empty.

---

## Critic

Owner-add of the *named nuclear gap* is correct process. Do not confuse that with a buy, and do not confuse CCJ with BE. ttm 170× is a yellow light that the market is paying for Westinghouse optionality Cameco cannot yet mark. If the S-1 is a value-crystallization event, CCJ is the ticket; if it is a dilution event, we wanted the uranium miner at a uranium multiple and did not get it. Scarce residual still goes to **BE** (equipment gap) or **NVDA/GOOGL** (liquid AI) unless the *question* is nuclear/LPS. **Ready / no size.** BWXT is the complementary hardware seat — not a second copy of this name.

---

## Synthesize

Owner confirmed **A**. CCJ is the first nuclear name. **Ready / no size / not core.** Next deploy must name it and **reject with reasons** unless residual-after-floors (or meaningful RH BP) *and* the question on the table is nuclear fuel + Westinghouse, not "we need energy."

**Kill / refresh triggers:** Westinghouse IPO terms that strand the 49%; uranium contract book rolls off; production guide cut; **>25%** from $100.01; 90-day age.

---

## Sources

- Cameco Q2 2026 quarterly report / highlights — https://www.cameco.com/invest/financial-information/quarterly-reports/2026/q2
- Cameco Q2 2026 MD&A / FS (Westinghouse 49%, outlook, Dukovany) — https://www.cameco.com/sites/default/files/documents/2026-Q2-MDA-FS-Notes_0.pdf
- Cameco 2026-07-31 Westinghouse confidential S-1 statement — https://www.cameco.com/sites/default/files/documents/2026-07-31-Statement-on-S1-Filing.pdf
- World Nuclear News 2026-07-31 (Cameco 49% / Brookfield 51%; ~USD8B 2023) — https://world-nuclear-news.org/articles/cameco-announces-go-public-plans-for-westinghouse
- Reuters 2026-07-31 (confidential IPO) — https://www.reuters.com/business/nuclear-company-westinghouse-confidentially-files-us-ipo-2026-07-31/
- RH `get_equity_quotes` / `get_equity_fundamentals` 2026-08-31 (close 2026-08-28)
- FCC worktree `financial-command/treasury_latest.json` `as_of` 2026-08-31T06:37:24Z
- Nest: `RESEARCH/ENERGY_SMITH_CHRONOMETER_WATCHLIST_REVIEW_2026_08_31.md` · `RESEARCH/ENERGY_SMITH_CFO_GATE_2026_08_31.md` · `RESEARCH/PAVE_GRID_THEMATIC_DIVE_2026_08_17.md` · `RESEARCH/CHAMATH_AI_STACK_AUG2026.md`
