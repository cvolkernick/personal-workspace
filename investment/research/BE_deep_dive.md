# BE deep dive (Bloom Energy) — post Q2'26 refresh + Smith fuel-cost reweight

**Report run:** 2026-08-04 (operating refresh) · **Reweight:** 2026-08-31 (Chronometer / Smith molecule overlay; owner-directed)  
**Process:** Emulated `/position-deep-dive symbol=BE` (host workflow not invoked)  
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** on free capital; **not** an order. **Not `pass`.**  
**Sleeve if owned:** `stocks_growth` · **Priority:** high · **Core allowlist:** no  
**On watchlist:** yes · **Held agentic:** no  
**Agentic book frame (FCC `financial-command/treasury_latest.json` `as_of` 2026-08-31T06:37:24Z):** NAV **$227.33** · cash/BP **$0.09 / $0.09** · deployed ~40.6% / ~59.4% in band. Energy **unheld**. `auto_buy` false.  
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority.**  
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`). Periodic refresh. Strong theme bias; core allowlist still preferred for routine rebalance.  
**Owner ask 2026-08-31:** Chris `#Orchestration` `2b8d1eb2…` — reweight BE for risks in Matthew Smith / Chronometer *Invest Like the Best* ([video](https://www.youtube.com/watch?v=3d9tIgjf4_E)) + Colossus letter *I Got Gas*. Parallel owner-add: **CCJ + BWXT**. EXE not named.


---

## Executive findings

1. **Post-print operating story remains strong.** Yahoo analysis strip: Q2 FY26 revenue ~**$1.07B**, earnings ~**$196M**. Prior channel notes (watchlist): record rev ~$1.065B (+166% YoY), non-GAAP EPS ~$0.78, FY26 rev guide raised toward **$3.9–4.2B**. AI data-center power thesis **intact**.

2. **Equity still prices a large success case + high beta.** Yahoo: close ~**$207** (7/30, +26% day after print/upgrade color), overnight prints higher (~$224). Market cap ~**$61B**, trailing P/E ~**213–269×**, EPS TTM ~**$0.77**, beta ~**3.7**, 52w range ~**$33–$351**. Forward P/E ~**73×**. This is **not** a value entry.

3. **Prior 2026-07-23 dive blocked pre-print size — correct process.** Event risk cleared; **valuation + beta + opportunity cost** still dominate for a ~$181 agentic book.

4. **Theme fit remains the energy *equipment / speed-to-power* expression** (SOFC generation gear into AI load). Complements miners (load) rather than duplicating them. **Not** the nuclear seat (that is CCJ / BWXT as of 2026-08-31). **Not** a gas-molecule producer.

5. **Status under new owner policy:** homework refreshed → **`ready` for consideration** (not `pass`). Size can still be $0 when Risk/Critic prefer core or mega-cap AI on scarce capital.

6. **2026-08-31 Smith overlay (do not smooth).** Smith treats turbine / distributed-gen manufacturers as the *stock-market winners so far* and likely *losers* if the bottleneck is **molecules**, not kit. Bloom **burns gas**. RH profile: Energy Server converts **natural gas or biogas** to electricity without combustion. If Henry Hub is structurally **$8–10**, that is a Bloom **fuel-cost / TCO** risk for customers, **not** confirmation of the BE tape. See addendum.

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** |
| Promote to core allowlist? | **No** |
| Status | **`ready`** (homework done; proposal-eligible). **Not `pass`.** |
| Starter size *now* (BP ~$0.09)? | **No — red-mode + capital dust + multiple/beta + fuel-cost overlay** |
| Auto-buy? | **Never** |
| Theme fit | **Strong as energy *equipment*** — weaker as a bet that *gas is scarce*. High HH is a cost, not a tailwind. |
| Relative preference vs other watchlist | **Still first dollar when the question is "energy equipment / speed-to-power."** Behind nothing in that seat. **Not** first dollar when the question is nuclear/LPS (CCJ then BWXT) or liquid AI (NVDA/GOOGL). Do not buy BE+CCJ+BWXT as a basket. |
| Next deep-dive refresh | **Next earnings (est. ~2026-10-27)**; or winter 2028/29 HH strip **>$6** and holding; or documented fuel-cost / efficiency miss on named DC fleets; or **>25%** drawdown from post-print highs |

**One-line for the fund team:**  
*BE stays **ready** as energy/AI-power **equipment** — thesis intact, multiple/beta still harsh; **HH $8–10 is a fuel-cost risk, not confirmation**; turbine-slot scarcity can cut the other way; no auto-buy; BP dust today.*

---

## Addendum 2026-08-31 — Chronometer / Smith molecule overlay

**Trigger:** Chris `#Orchestration` `2b8d1eb2…` after confirming CCJ+BWXT. "Update the BE thesis accordingly to weight for the risks outlined in this video."  
**Source:** Matthew Smith / Chronometer on *Invest Like the Best* ([3d9tIgjf4_E](https://www.youtube.com/watch?v=3d9tIgjf4_E), 2026-07-21) + Colossus letter [*I Got Gas*](https://colossus.com/wp-content/uploads/2026/07/letter-III-got-gas.pdf). Nest review: `RESEARCH/ENERGY_SMITH_CHRONOMETER_WATCHLIST_REVIEW_2026_08_31.md`.

Smith's claim is a **2028–2030 US gas-storage break**: LNG ~+20 Bcf/d + P50 AI-power gas ~+5 vs ~+20 production growth. Forwards still mid-$3s. Live: Henry Hub spot **$2.70** (EIA/FRED 2026-08-25); NYMEX front **~$2.89**; winter 2028/29 strip **~$3.6–$4.8**, not $8–$10. The producer pitch is "the curve is wrong."

He also flags **turbine / distributed-gen manufacturers** as the tape winners so far and likely **losers** if the bottleneck is molecules, not generation kit.

### What that actually does to Bloom

| Channel | Direction | Weight |
|---------|-----------|--------|
| **Fuel cost** | **Risk up.** SOFC consumes NG/biogas. 2025 10-K: compete vs recips, small turbines, CCGT; product is on-site firm power. Contract mix = PPA ($/kWh scheduled), Capacity (fixed periodic), Lease (fixed for equipment). Capacity/Lease typically leave **fuel with the customer**. PPA may bundle. If HH structurally $8–10, customer TCO rises unless pass-through / hedge is explicit. That is demand destruction + delayed DC FIDs, not a Bloom margin windfall. | **Primary new weight** |
| **Efficiency vs OCGT** | **Partial offset, not a hedge.** Bloom datasheet: cumulative electrical efficiency **65–53% LHV net AC**; heat rate **5,811–7,127 Btu/kWh**. Bloom Feb-2024 whitepaper: full-load ~**54%** vs microturbine ~**37%**. Higher efficiency → fewer MMBtu per MWh than a simple-cycle turbine. Relative advantage **widens** as gas rises. Absolute fuel bill still **rises**. At $5/MMBtu Bloom already pitches $12–15M/yr gas savings on a 100 MW AI factory (Bloom "Fuel Cell 101", Feb 2026). At $8–10 that savings is larger *vs OCGT* and the bill is still painful *vs $2.70 spot*. | Do not treat as "BE wins $8 gas" |
| **Advertised vs measured efficiency** | **Amplifies fuel risk.** [Hunterbrook 2026-07-30](https://hntrbrk.com/investigations/bloom-2): NY/CA/federal metered fleets often miss Bloom's ~**46.9–48.8%** efficiency guarantee and 95% output benchmark. If real heat rates are worse than the pitch, MMBtu/MWh is higher and HH sensitivity is **worse** than the IR deck. Short-seller work — cite as a **kill-watch**, not as a fact. | Kill / refresh |
| **Turbine-slot scarcity** | **Cuts the other way. Do not copy Smith wholesale onto BE.** Parallel 2026 reporting: GEV backlog into 2031; turbine $/kW up. Bloom has **no GEV slot**. Speed-to-power vs 2028–31 turbine deliveries is the *bull* case for SOFC, and is why the tape already paid BE. If turbines are the binding constraint, Smith's 2028 storage-break **slips** (EXE value-trap) **and** Bloom is the workaround, not the loser. | Counterweight — keep |
| **Nuclear now on the consider set** | **Relative rank changes.** CCJ (fuel + 49% Westinghouse) and BWXT (Navy floor + commercial components) close the *named* nuclear gap. BE is no longer the only energy expression. "We need energy" still surfaces **BE first** (equipment, unheld). "We need nuclear / LPS" surfaces **CCJ then BWXT**. Not substitutes. Not a basket. | Seat hygiene |
| **Private Boom** | Same fault line: turbine → DC power. HH $8–10 is a Boom fuel-cost risk too. Do not treat Boom as a BE hedge. | Context only |

### What we are *not* doing

- **Not `pass`.** Owner did not drop the energy-equipment seat. Operating story (Q2 FY26 rev **$1.065B**, NI **$199M** on RH financials period-end 2026-06-30) is intact. Close 2026-08-28 **$210.77**, cap **$62.0B**, ttm PE **~287×**, 52w **$48.87–$351.28**. Multiple/beta still the size gate, now **plus** fuel-cost overlay.
- **Not a reason to add EXE.** EXE is the molecule option. Chris did not name it. Adding a Haynesville producer does not "complete" BE.
- **Not "Smith is confirmed by the BE tape."** The tape paid generation kit. Smith says that is the wrong bottleneck. Those are opposing reads of the same AI-power constraint.

### Live quotes (RH 2026-08-31; official close 2026-08-28, Labor Day weekend)

| | Print |
|--|------:|
| Close | **$210.77** |
| Cap | **$62.0B** |
| ttm PE / P/B | **287× / 38.4×** |
| 52w | **$48.87 – $351.28** (high 2026-06-25) |
| Q2 FY26 rev / NI | **$1.065B / $199M** (RH financials) |
| HH spot / front / winter 28/29 | **$2.70 / ~$2.89 / ~$3.6–$4.8** |

### Kill / refresh (add to the 2026-08-04 list)

- Winter 2028/29 HH strip **>$6** and holding, *or* a dated storage-stress print that makes Smith's $8–10 path the base case — then **re-run** this overlay before any size. Do not treat that path as a BE buy signal.
- Named DC fleet with **documented fuel-cost complaints** or efficiency LD payouts (Hunterbrook-class).
- Contract mix shift: Bloom taking fuel risk on PPAs into an unhedged $8+ strip.
- Next earnings est. **~2026-10-27**.

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

- **Valuation + beta:** violent tape; tiny tickets add noise without material NAV impact until capital scales. Close still **~$211** / ~**287×** ttm (2026-08-28).
- **Fuel / molecule (2026-08-31):** BE burns gas. High HH is customer TCO, not Bloom gross-margin expansion, unless the contract bundles fuel *and* Bloom is hedged. Efficiency vs OCGT is a relative offset. Hunterbrook-class efficiency misses would amplify, not mute, this.
- **Turbine-slot (counter):** if GEV/slot scarcity is the binding constraint, Bloom is the workaround. Do not let Smith's "kit losers" line auto-kill the seat.
- **Execution / partner concentration** on large frameworks (Brookfield $25B color is a commitment headline, not revenue).
- **Leverage / WC** for growth still matter (D/E elevated).
- **Critic:** Ready ≠ must-buy. Prefer size when (a) free capital meaningful, (b) the *question* is energy equipment / speed-to-power — not nuclear, not "gas is a theme," (c) not chasing a post-print day, (d) fuel-contract mix is not unhedged into an $8+ strip. Nuclear (CCJ/BWXT) is a different seat as of 2026-08-31.

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
4. **Fuel:** what share of DC/AI backlog is PPA (Bloom/financier fuel) vs Capacity/Lease (customer fuel)? Any explicit HH pass-through or hedge?
5. **Efficiency:** any company rebuttal to Hunterbrook's metered-fleet misses that a DC offtaker would underwrite?
6. **HH path:** does the 2028/29 strip actually stress, or does turbine-slot slip delay both Smith's storage-break *and* the "kit losers" call?

---

*Written 2026-08-04 by Nakatoshi (CFO) — owner watchlist loop-close.*  
*Reweight 2026-08-31 by Grok (SIC) on Chris `#Orchestration` `2b8d1eb2…` — Smith molecule/fuel-cost overlay. Research only; no orders.*
