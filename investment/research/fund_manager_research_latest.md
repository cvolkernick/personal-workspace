# Fund manager research — 2026-09-28 (~10:29 ET, mid-session HOLD)

**As of:** 2026-09-28 ~14:29Z (~10:29 ET). Regular hours. Past the first 30 minutes (open window ended 10:00 ET). Not the close. This pass closes the 14:26Z rules re-fire (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60 ±5%).
**Account:** agentic ••••1752 only. Primary margin was read ($0.09, no equity) and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus the 2026-09-28 digest.
**Live NAV:** broker **$324.59** (equity **$324.56**, cash **$0.03**, buying power **$0.03**). Quote equity at 14:29Z **$324.56**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none (confirmed and queued empty).
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Quote equity 14:29Z | TSLA **$161.97** (49.90%) / SPCX **$162.59** (50.10%) |
| 50/50 gap | **$0.31** one-way (was $0.15 at 14:13Z, $1.19 at 13:59Z) |
| Day | TSLA **−2.93%** (361.22 vs 372.11). SPCX **−0.99%** (147.21 vs 148.68) |
| Books | TSLA 1.1 bp (361.17 / 361.21). SPCX 1.4 bp (147.20 / 147.22) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |
| Cash | **$0.03** unallocated dust, below the $1 minimum |

Chairman standing order in `investment/consider_share.json` (2026-09-22), restated in `fund_manager.json` `targets.symbol_targets`: stocks sleeve is **50% SPCX / 50% TSLA** by market value. No other equity. Crypto and cash unchanged unless the Chairman says otherwise. Today's digest proposed no pin change and kept the 40% sleeve closed. `bias_weight_staged.json` pending queue was empty at sweep time.

Held names are 14:29Z. The rest of the consider set is 14:27Z. Daily change uses adjusted previous close.

| Symbol | Last | Prior | Day | Bid / ask | Spread |
|--------|------|-------|-----|-----------|--------|
| TSLA | 361.22 | 372.11 | −2.93% | 361.17 / 361.21 | 1.1 bp |
| SPCX | 147.21 | 148.68 | −0.99% | 147.20 / 147.22 | 1.4 bp |
| STRC | 98.9699 | 98.54 | +0.44% | 98.95 / 98.97 | 2.0 bp |
| SATA | 100.00 | 99.9584 | +0.04% | 99.99 / 100.00 | 1.0 bp |
| MSTR | 157.12 | 158.61 | −0.94% | 157.05 / 157.11 | 3.8 bp |
| BITA | 63.00 | 63.26 | −0.41% | 62.79 / 63.02 | 36.6 bp |
| ASST | 29.72 | 29.44 | +0.95% | 29.70 / 29.72 | 6.7 bp |
| MARA | 12.45 | 12.55 | −0.80% | 12.45 / 12.46 | 8.0 bp |
| RIOT | 22.36 | 23.00 | −2.78% | 22.35 / 22.36 | 4.5 bp |
| CLSK | 13.77 | 13.95 | −1.29% | 13.76 / 13.77 | 7.3 bp |
| WULF | 15.505 | 15.74 | −1.49% | 15.50 / 15.51 | 6.4 bp |
| IREN | 43.51 | 44.125 | −1.39% | 43.50 / 43.52 | 4.6 bp |
| GOOGL | 341.375 | 343.92 | −0.74% | 341.35 / 341.40 | 1.5 bp |
| AAPL | 341.63 | 341.07 | +0.16% | 341.60 / 341.66 | 1.8 bp |
| NVDA | 231.46 | 225.07 | +2.84% | 231.46 / 231.47 | 0.4 bp |
| PLTR | 187.85 | 189.67 | −0.96% | 187.81 / 187.91 | 5.3 bp |
| AMZN | 246.935 | 249.67 | −1.10% | 246.92 / 246.96 | 1.6 bp |
| EVGO | 1.365 | 1.38 | −1.09% | 1.36 / 1.37 | 73.3 bp |
| RKLB | 73.195 | 73.95 | −1.02% | 73.17 / 73.20 | 4.1 bp |
| STRK | 75.25 | 74.89 | +0.48% | 75.25 / 75.40 | 19.9 bp |
| CCJ | 87.29 | 88.07 | −0.89% | 87.24 / 87.34 | 11.5 bp |
| BWXT | 136.70 | 138.47 | −1.28% | 136.62 / 136.77 | 11.0 bp |
| HYPD | 3.95 | 4.04 | −2.23% | 3.93 / 3.95 | 50.8 bp |
| BE | 266.31 | 288.70 | −7.76% | 266.22 / 266.41 | 7.1 bp |
| GLDM | 81.905 | 84.89 | −3.52% | 81.90 / 81.91 | 1.2 bp |

Spot **BTC-USD** mark **$83,378.90** vs prior close **$83,336.50** (+0.05%) at 14:27Z. No position.

**Starship Flight 14 (SPCX context):** liftoff succeeded this morning. Super Heavy splashdown succeeded. Ship 41 reached orbit and deployed 26 Starlink V3 satellites. Deorbit and Pacific splashdown were still ahead at decision time. SPCX-positive. It does not authorize selling the pin or seating another equity.

## Research / rotate

**How new capital best serves the themes now:** it does not, because there is no deployable capital. A hypothetical stock dollar follows the pin (marginal dollar to TSLA while SPCX is $0.31 overweight) and is not raised by selling SPCX today. A hypothetical reopen of the 40% sleeve — not authorized — is STRC and SATA first (2.0 bp and 1.0 bp, near par, daily-accrual proposal in today's digest), then a diversified miner set. RIOT is the deepest liquid miner tape (−2.78%, 4.5 bp). That is not path-dependence toward names already held. The book is two names because the Chairman exited everything else on 2026-09-22.

**Chosen:** TSLA (growth equity) and SPCX (space), both hold.

**Rejected with reasons:** see the decision record. Short form: pin rotate blocked ($0.31, under $1, inside the ~$3.25 1% band). STRC/SATA not illiquid and not covered by MSTR. Miners not blocked for overlap. Watchlist ready names, including NVDA +2.84% and new HYPD, not seated under the pin. Gold research-only. BE blocked. Private names not deployable.

No deep-dive refresh. Ready dives are inside 90 days (newest HYPD 2026-09-27). This pass proposes no first buy.

## Thesis / risk / critic

Thesis: hold. Risk: ok to do nothing; the $0.31 rotate is liquid and still the wrong ticket. Critic: block the rotate and block the rules-engine 40/60 rebuild. STRC/SATA under-allocation is real versus the numeric 40% target and is not a buy this pass, because no BTC-complex dollar is being deployed. Executor: no orders.

## Do not trade

- Do not sell SPCX to buy TSLA today.
- Do not reopen the BTC sleeve by selling the locked book.
- Do not seat GOOGL, AAPL, NVDA, PLTR, AMZN, RKLB, CCJ, BWXT, EVGO, STRK, or HYPD.
- Do not buy BE.
- Do not buy gold until the owner calls it.
