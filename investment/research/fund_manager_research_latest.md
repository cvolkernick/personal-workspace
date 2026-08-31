# Fund manager research — 2026-08-31 (~09:50 ET)

**As of:** 2026-08-31 ~13:50Z (~09:50 ET, **regular hours**; still inside open-first-30m avoid window)  
**Account:** agentic ••••1752 only (`674601752`)  
**Process:** Uniform research/rotate (size-invariant) — Scout → Thesis → Risk → Critic → Executor  
**Live NAV (RH total):** **$254.01**  
**cash/BP:** **$0.09 / $0.09** · **min_trade:** $1.00 · **unsettled_funds $0**  
**pending_deposits $25:** **not spendable.** Live `buying_power` is **$0.09**. This is the same RH phantom field as 8/24; this morning’s $25 was already deployed (RIOT/WULF/BE filled 13:30Z).  
**MCP:** robinhood-trading HTTP MCP with stored OAuth (this Grok session’s MCP tools disconnected / auth-required). Venue reachable.  
**Owner prefs:** elevate **STRC/SATA** in BTC-complex deploys (presence + preferential residual; not 40% all-credit); **multi-miner diversification good**; every `ready` watchlist name in the consider set  
**Trigger:** User-run fund-manager review (team LLM) after rules path 13:45Z flagged free capital using stale cash $25.09 vs BP $0.09. Mid-session-style review; not open/close scalp.

BTC-USD (Coinbase) **$77,942**.

## 1) Scout snapshot

| Field | Value |
|-------|--------|
| Held | MSTR, **STRC**, **SATA**, BITA, MARA, IREN, CLSK, **RIOT**, **WULF**, TSLA, SPCX, **GOOGL**, **BE** |
| Deployed mix (qty × last ~13:48Z) | **~39.2% / 60.8%** BTC-complex / stocks — in ±5% band |
| Idle cash / BP | **$0.09** (< min $1) — **cannot deploy** |
| Theme coverage | Digital credit (**STRC, SATA**, MSTR, BITA) · miners **5-name** (MARA, IREN, CLSK, **RIOT**, **WULF**) · growth (TSLA, SPCX) · AI mega-cap (**GOOGL**) · energy equipment (**BE**) |
| Remaining gaps | Unheld core **ASST** · nuclear **CCJ / BWXT** · NVDA (AI silicon) · watchlist rest |

Account type: **limited_margin**, `agentic_allowed=true`, unsettled **$0**. No open/queued orders.

### This morning’s fills (pre-market queue → open)

| Symbol | Notional | Qty | Avg fill | Filled at |
|--------|----------|-----|----------|-----------|
| **RIOT** | $5.00 | 0.265577 | $18.8269 | 13:30:01Z |
| **WULF** | $5.00 | 0.327868 | $15.25 | 13:30:00Z |
| **BE** | $15.00 | 0.071811 | $208.88 | 13:30:03Z |

Order IDs: `6a9559bc-1f1b-4cfb-9240-0071f9d8e36c` / `6a9559bc-9fbc-43f0-a266-9adf1c6b351f` / `6a9559bd-add9-4fba-945d-00c769801182`. Residual after those tickets: **$0.09**.

### Live marks (qty × last ~13:48Z)

| Symbol | Sleeve | Theme | ~$ | % of equity |
|--------|--------|-------|-----|-------------|
| TSLA | stocks | growth | 69.61 | 27.3% |
| SPCX | stocks | growth | 56.99 | 22.4% |
| MSTR | btc | digital credit | 37.27 | 14.6% |
| BITA | btc | BTC yield | 17.42 | 6.8% |
| **BE** | stocks | energy equipment | 14.70 | 5.8% |
| GOOGL | stocks | AI stack | 13.76 | 5.4% |
| MARA | btc | miner | 10.08 | 4.0% |
| IREN | btc | miner/power | 10.02 | 3.9% |
| STRC | btc | digital credit | 6.05 | 2.4% |
| SATA | btc | digital credit | 5.00 | 2.0% |
| **WULF** | btc | miner/power | 4.98 | 2.0% |
| **RIOT** | btc | miner | 4.94 | 1.9% |
| CLSK | btc | miner | 4.16 | 1.6% |

**Sub-sleeve (BTC complex ~$99.9):**
- Credit/yield (MSTR+BITA+**STRC+SATA**): **~$65.7 (~66% of complex)** — **STRC/SATA seats exist** (~$11.05)
- Miners (MARA+IREN+CLSK+**RIOT+WULF**): **~$34.2 (~34% of complex)** — **5-name sleeve complete**
- Regular-hours spreads (not premarket): STRC $0.11 · SATA **$0.05** (premarket $8.85 was a tape artifact) · BITA $0.22 · STRK $0.92 · NVDA $0.03 · CCJ $0.26 · BWXT $0.61 · BE $0.51

**Watchlist ready (must consider):** BE (**held**), GOOGL (**held**), AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT  
**Private (not deployable):** ANDURIL, SARONIC, BOOM (context only)

**NVDA print:** FY27 Q2 **2026-08-26 AMC** — EPS **$2.22 vs $2.09 est, beat**. Event risk **cleared**. Next ~2026-11-17.

## 2) Research / rotate (REQUIRED)

### Names considered
MSTR, BITA, MARA, IREN, CLSK, **RIOT**, **WULF**, **STRC**, **SATA**, ASST, TSLA, SPCX, BTC (RH), **BE**, GOOGL, AAPL, **NVDA**, PLTR, EVGO, AMZN, RKLB, **STRK**, **CCJ**, **BWXT**; held-only top-up of TSLA/SPCX/MSTR/GOOGL; forced rotate sells (trim TSLA/SPCX into NVDA/CCJ).

### Names chosen (this pass)

| Name | Theme map | Action | Why now |
|------|-----------|--------|---------|
| *(none)* | — | **HOLD** | Spendable BP **$0.09** < min $1. This morning already filled the theme holes (unheld miners + first energy equipment). No new ticket. |

### Names rejected *for action this pass*

| Name / path | Why not **now** |
|-------------|-----------------|
| **pending_deposits $25 / stale cash $25.09** | Phantom. Live BP $0.09. $25 already filled at the open. Rules 13:45Z `need_llm` used cash vs BP — spendable is BP. |
| **Held-only TSLA/SPCX/MSTR/GOOGL top-up** | No residual. TSLA still ~27% of equity — inertia, not a buy. |
| **STRC / SATA add** | **Seats exist (~$11).** Credit ~66% of complex after miner adds. Bias is presence + preferential residual, not 40% all-credit. Regular-hours SATA tape is now tight ($0.05) — **liquidity is no longer the rebuttal**; skip is **no residual + sub-sleeve already credit-heavy**. |
| **STRK** | Ready; junior to held STRC; conversion ~8× OTM; spread $0.92 vs STRC $0.11. No residual. RV vs STRC **fails**. |
| **ASST** | Unheld core credit. Secondary vs preferred SATA; no residual. |
| **MARA / IREN / CLSK / RIOT / WULF add** | 5-name miner sleeve **just filled**. Not overlap-reject — no dollars. |
| **BTC spot on RH** | Prefer equity/credit on agentic RH; CB for spot (~$77.9k). |
| **GOOGL add** | First AI seat exists. No residual. |
| **BE add** | First energy equipment seat filled this open ($15). Starter stands. Fuel-cost overlay (HH $8–10 TCO risk) is not a size-up. |
| **NVDA** | Ready; **post-print beat 8/26**; liquid. **Best next stocks residual** after GOOGL (AI silicon gap). Blocked only by $0.09. |
| **AAPL** | Ready; owner ranks behind NVDA/GOOGL/BE. |
| **AMZN** | Ready; second hyperscaler vs held GOOGL. Behind NVDA. |
| **PLTR** | Ready, medium priority, high multiple / gov gates. |
| **EVGO** | Ready, low-priority show-me Superchargers. |
| **RKLB** | Ready; Neutron unflown; SPCX already the space core. |
| **CCJ** | Ready (dive 2026-08-31). First *nuclear* name, **not** first energy dollar (that is now held BE). Not a basket. **Best next energy residual when the question is nuclear/LPS.** No residual. |
| **BWXT** | Ready; second nuclear seat behind CCJ. Same reject. |
| **Forced rotate sells (trim TSLA/SPCX → NVDA/CCJ)** | Sleeves in band. Cadence is not day-trading. Fills are **18 minutes old**, still in **open-first-30m** avoid window. No thesis break. Critic blocks churn. |

### How new capital best serves themes *now*

There is **no new capital**. This morning’s $25 already did the correct job:

1. **BTC-complex $10:** STRC/SATA seats existed and credit was ~73% of the complex → miner diversifiers **RIOT + WULF** (now held). Logged STRC/SATA skip stands; SATA tape is fine in regular hours so the live rebuttal is **no residual + credit still 66% of complex**, not illiquidity.
2. **Stocks $15:** empty energy sleeve → **BE** first equipment seat (now held). NVDA remains the next AI-silicon residual; CCJ/BWXT remain the nuclear gap — not a BE substitute and not a three-name energy basket.
3. Idle $0.09 is dust under min $1. Do **not** park a new ticket. Do **not** spend the $25 pending-deposits phantom.

## 3–5) Thesis / Risk / Critic

| Role | Vote | Note |
|------|------|------|
| Scout | ok | Live MCP NAV $254.01; BP/cash **$0.09**; unsettled $0; 13 names including new RIOT/WULF/BE; sleeves 39.2/60.8 in band; ~09:50 ET first-30m; pending $25 is phantom |
| Thesis | ok (**hold**) | Theme holes from 06:35Z pass are filled. Next residual rank: NVDA (AI silicon) then CCJ (nuclear). No rotate. Not MSTR/TSLA habit. |
| Risk | ok (**hold**) | Agentic cash only; BP $0.09 < min $1; limited_margin; no open orders; unsettled $0. Guardrail: no trade without deployable BP. Open-first-30m avoid. TSLA 27% is concentration observation, not a trim mandate 18m after fills. |
| Critic | ok (**hold**) | Block phantom $25. Block held-only. STRC/SATA under-allocation: **seats exist**; credit 66% of complex; skip is residual=0 not MSTR-cover. Miner sleeve is 5-name — that *is* the diversification preference, not overlap. Block CCJ+BWXT+BE basket. Block STRK. Block open-churn rotate. |
| Executor | **hold** | No place/cancel. Confirm prior three orders **filled**. |

## 6) Decision
**HOLD** on agentic ••••1752. No new orders. This morning’s RIOT $5 + WULF $5 + BE $15 are filled. Residual $0.09.

### Next cycle hooks
1. **NVDA** — first AI-silicon residual after GOOGL; print beat is in.
2. **CCJ** then **BWXT** — nuclear gap; size only when the *question* is nuclear/LPS, not “we need energy” (that is now held BE). Not a basket.
3. **STRC/SATA residual** — only if credit share of the complex compresses or idle dollars would otherwise sit cash-like. Regular-hours tape is tradable.
4. **STRK** — still needs a logged RV win vs held STRC plus residual-after-floors.
5. If another deposit lands, re-open the full consider set — **not** held-only. Treat `pending_deposits` as phantom unless `buying_power` actually rises.
