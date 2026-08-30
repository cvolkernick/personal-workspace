# STRK deep dive (Strategy Strike Preferred) — owner-add 2026-08-30

**Report run:** 2026-08-30
**Process:** Emulated `/position-deep-dive symbol=STRK` (host workflow not invoked this session)
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** on free capital; **not** an order
**Sleeve if owned:** `btc_digital_credit` · **Priority:** high (owner-named) · **Core allowlist:** no
**On watchlist:** yes · **Held agentic:** no
**Agentic book frame (FCC worktree `financial-command/treasury_latest.json` `as_of` 2026-08-30T18:36:00Z):** NAV **$228.21** · equity MV **$199.73** · cash/BP **$0.09 / $0.09** · deployed **40.6% / 59.4%** in ±5% band. Held credit: **MSTR $27.55 · BITA $15.13 · STRC $6.00 · SATA $5.00**. `auto_buy` false.
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority.**
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`). Periodic refresh. Strong theme bias; core allowlist still preferred for routine rebalance.
**Owner ask:** ChrisV.btc⚡ `#agentic-finance` 2026-08-30 — add STRK into agentic fund considerations.

**Not this ticker:** Jack Mallers' Strike (private payments company). Starknet token STRK. JR-strcUSX (Solana first-loss wrapper of **STRC**, already locked out of HY / Morpho). This dive is **Nasdaq:STRK** — Strategy Inc. 8.00% Series A Perpetual Strike Preferred Stock.

---

## Executive findings

1. **STRK is listed digital credit, not a new theme.** Strategy's convertible perpetual preferred: 8.00% cumulative on $100 stated amount, quarterly, convertible at the holder's option into **0.1 MSTR**. First Strategy preferred (IPO ~Feb 5 2025 at $80). Same issuer we already hold as **MSTR (common) + STRC (Stretch preferred)**. SATA is Strive, not Strategy.

2. **It is a hybrid that currently prices as a discounted preferred, not as equity.** Close **2026-08-28 $71.73** (−2.74%), ≈**28% below par**, effective yield **$8 / $71.73 ≈ 11.15%**. Conversion value at MSTR close **$127.31** is **0.1 × $127.31 = $12.73** vs $71.73 market — implied conversion **~$1,000 / MSTR**, ~**8× out of the money**. Until MSTR is near that reference, the conversion feature is cheap optionality, not the return.

3. **Junior to the preferred we already own.** Capital stack: debt → STRF → **STRC** → STRE → **STRK** → STRD → common. STRC is senior, variable ~**12%** stated (semi-monthly), last close ~**$97.33** (~12.3% effective). STRK pays less, ranks lower, and adds the same Strategy/BTC-treasury factor. Preferreds are **not** collateralized by the BTC pile.

4. **Credit seat is not empty.** STRC + SATA already exist (~$11). Owner 2026-08-04 bias is *presence + preferential residual* toward STRC/SATA, not 40% all-credit, not a third Strategy paper by default. Unheld core miners (RIOT/WULF) are the BTC-complex diversifiers. STRK is the name you take when the *question* is "discounted convertible vs held Stretch," not "we need digital credit."

5. **No size today.** FCC overall **red** (card + CB liquid + Morpho LTV **0.50** at max). One Card **$470.36**. HY LTV Buffer **$239** vs $1,000 floor. BP **$0.09 < min_trade $1**. SIC cash-stack lock: no new free-dollar residual. Existing RH BP is deployable in policy; **$0.09 is not a size**.

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** (owner-named) |
| Promote to core allowlist? | **No** — STRC/SATA remain preferred_core |
| Status | **`ready`** (homework done; proposal-eligible) |
| Starter size *now*? | **No** — red-mode + dust BP + STRC/SATA seats exist + same-issuer overlap + conversion OTM |
| Auto-buy? | **Never** |
| Theme fit | **Strong as convertible digital credit** — weaker as a *new* credit seat |
| Relative preference vs other consider names | Behind **STRC/SATA residual** (locked bias, seats exist) and **RIOT/WULF** (unheld miner diversifiers) for scarce BTC-complex dollars. Not in the 60% sleeve race (BE / NVDA / GOOGL). |
| Next deep-dive refresh | MSTR meaningfully toward conversion (~$1,000) or a dated change in STRK terms; STRC rate/par regime shift; >25% move from $71.73; missed dividend / stack event; or 90d age |

**One-line for the fund team:**
*STRK is **ready** as Strategy's convertible preferred — 8% stated / ~11% effective at a ~28% discount to par; conversion is ~8× OTM; junior to held STRC; no size while the credit seats exist and residual is empty.*

---

## Frame / policy

| Field | Value |
|-------|--------|
| Symbol | **STRK** |
| Name | Strategy Inc. 8.00% Series A Perpetual Strike Preferred Stock |
| Theme | `digital_credit` (BTC-fundamental convertible preferred) |
| Sleeve | `btc_digital_credit` (~40% complex) |
| Added | 2026-08-30 by **owner** (`#agentic-finance`) |
| Core allowlist | No (STRC / SATA remain preferred_core) |

---

## Market / instrument (as of 2026-08-28 close unless noted)

| Metric | Print | Source |
|--------|-------|--------|
| Last close | **$71.73** (−2.74%) | MarketWatch / stockinvest 2026-08-28 |
| Stated amount | **$100.00** | Strategy STRK briefing (as of 2026-08-23) |
| Price vs par | **≈ −28.3%** | True North snapshot 2026-08-30 |
| Stated dividend | **8.00%** / **$8.00** per share annually; **$2.00** quarterly | Strategy briefing; Q1–Q2 2026 paid |
| Effective yield | **~11.15%** ($8 / $71.73) | MarketWatch; True North |
| Convertible | **0.1 MSTR** per STRK, holder election, any time | Strategy 424B5 / True North |
| Implied conversion | **~$1,000 / MSTR** | $100 stated / 0.1 |
| Conversion value now | **$12.73** (MSTR $127.31 × 0.1) | MSTR close 2026-08-28 |
| Shares / notional | **14.02M** / **~$1.40B** | Strategy briefing; MarketWatch |
| Avg volume (recent) | **~139k sh / day** | MarketWatch |
| 52-week | **$49.80 – $100.25** | CoinDesk / MarketWatch |
| IPO | Feb 5 2025 settlement; $80 offer | Strategy briefing |
| Next record / pay | **2026-09-15 / 2026-09-30** | strategy.com/strike (page copy 2026-08-30) |
| 2025 tax (issuer) | Distributions reported **100% return of capital** | Strategy 10-K 2025 via True North |
| Stack rank | Below STRF, **STRC**, STRE; above STRD and MSTR | True North; Strategy 8-K language |

STRC contrast (held): variable Stretch, **12.00%** stated as of Aug 2026, semi-monthly, last close **$97.33** (stockanalysis 2026-08-28), FCC MV **$6.00**. Senior to STRK. Designed to hug par; STRK is designed as convertible digital credit with a lower coupon.

---

## Thesis fit

**Northstar:** Bitcoin & digital credit — the ~40% complex. STRK is an *expression* of the existing STRC/SATA/MSTR credit sleeve, not a new theme and not a 60% stocks name.

**Bull case (real):**
- Listed, T+1, already on major brokerages (including RH). Least-wrong STRK expression is this paper, not a Solana wrapper.
- Discount to par + cumulative 8% is a higher running yield than the coupon, with a (currently cheap) call on MSTR if common ever rerates toward $1,000.
- Same BTC-treasury credit the book already wants a seat in. Owner asked it onto the consider set.

**Why not instead of STRC/SATA:**
- STRC is **senior**, **higher current yield**, **already held**, and is the locked preferred_core (with SATA).
- STRK's differentiator is conversion. At ~8× OTM that is not the reason to spend scarce residual.
- Adding STRK is **more Strategy factor** (MSTR + STRC already in the book), not a hedge.

**Why not JR-strcUSX:** category error already locked 2026-08-13/17. JR is first-loss on STRC, `counts_toward_hy=false`, never Morpho-funded. Listed STRK is a different instrument and still not a free-dollar buy today.

---

## Risks (material)

1. **Same-issuer concentration.** MSTR + STRC already in the 40%. STRK is the same BTC-treasury credit, mid-stack, not collateralized by BTC.
2. **Conversion is OTM.** Paying a hybrid coupon for an option that needs ~8× MSTR from $127 does not earn the "equity participation" story until the common moves.
3. **Subordination + discretionary dividends.** STRF/STRC/STRE must be current first. Cash dividend is not guaranteed; board declares. Cumulative helps; it does not print USDC.
4. **ATM / dilution.** Strategy can issue more preferred ranking equal or senior. Conversion rate may not adjust for all equity issuance.
5. **Liquidity.** ~139k sh/day vs STRC's much heavier tape. Tiny-ticket OK; not a dump-pad.
6. **Capital stack / red-mode.** Card, LTV at max, HY gap, SIC overdue-first. Residual-after-floors is empty.

---

## Critic

Owner-add is correct process. Do not confuse "consider" with "buy," and do not confuse STRK with STRC. The book already did the digital-credit homework: STRC/SATA seats exist. STRK at 11% effective, junior, conversion worthless at $127 MSTR, is a **worse residual use** than (a) topping STRC/SATA if the idle-vs-cash rule fires, or (b) an unheld miner diversifier. Scarce 60% dollars still go to the energy gap (BE) or liquid AI (NVDA/GOOGL), not a second Strategy preferred. **Ready / no size.** Revisit when residual exists *and* there is a logged RV case for the conversion discount versus held Stretch — not on ticker-narrative.

---

## Synthesize

Owner wants STRK on the consider set. That is correct. **Ready / no size / not core.** Next deploy must name it and **reject with reasons** unless residual-after-floors (or meaningful RH BP) *and* a logged case that the convertible discount beats STRC/SATA residual and miner diversification.

**Kill / refresh triggers:** MSTR path toward ~$1,000 conversion; STRK terms/dividend event; STRC regime change; >25% from $71.73; 90-day age.

---

## Sources

- Strategy STRK investor briefing (as of 2026-08-23): 8.00% Series A Perpetual Strike Preferred, Nasdaq STRK — https://assets.contentstack.io/v3/assets/bltf8d808d9b8cebd37/blt73df15fd06fe404d/6a8c342fc8ced9693b054af4/STRK_Investor_Briefing_As_of_8-23-2026_1.pdf
- True North STRK profile (data as of 2026-08-30): https://tnorth.com/digital-credit/markets/strk/
- MarketWatch STRK quote (2026-08-28 close $71.73, yield 11.15%, 52w $49.80–$100.25)
- stockanalysis STRC (2026-08-28 close $97.33, annual $12.00, yield ~12.33%)
- stockanalysis / MarketWatch MSTR (2026-08-28 close $127.31)
- CoinDesk STRK page (2026-08-21 tape; 52-week range)
- FCC worktree `financial-command/treasury_latest.json` `as_of` 2026-08-30T18:36:00Z; RH agentic `treasury/snapshots/robinhood_latest.json` `as_of` 2026-08-30T16:34:59Z
- Nest: `RESEARCH/JR_STRCUSX_CFO_VERDICT_2026_08_13.md`, `RESEARCH/CAPITAL_SIC_LOCK_2026_08_12.md`, `RESEARCH/FINANCIAL_MO_RED_MODE_2026_08_02.md`
