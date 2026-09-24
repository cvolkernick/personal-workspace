# Fund manager research — 2026-09-24 (~12:21 ET, mid-session HOLD)

**As of:** 2026-09-24 ~16:21Z (~12:21 ET). Regular hours. Outside the open and the close. This pass answers the 16:17Z rules re-fire (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60 ±5%).
**Account:** agentic ••••1752 only. Primary margin was read for the treasury snapshot and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** broker **$333.77** at ~12:17 ET (cash **$0.03**, buying power **$0.03**). Quote equity at 16:21Z **$333.65**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none.
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Quote equity 16:21Z | TSLA **$170.08** (50.98%) / SPCX **$163.57** (49.02%) |
| 50/50 gap | **$3.26** one-way (was $2.43 at 12:08 ET, $3.35 at 11:03 ET, $3.86 at 09:50 ET) |
| Day | TSLA **-0.21%**. SPCX **-0.18%** |
| Deployed mix | BTC-complex **0%** / stocks **100%** |
| Cash | **$0.03** unallocated dust, below the $1 minimum |

Chairman standing order in `investment/consider_share.json` (2026-09-22), restated in `fund_manager.json` `targets.symbol_targets`: stocks sleeve is **50% SPCX / 50% TSLA** by market value. No other equity. Crypto and cash unchanged unless the Chairman says otherwise. Today's digest proposed no pin change.

Held names are 16:21Z. The rest of the consider set is 16:18Z. Daily change uses adjusted previous close.

| Symbol | Last | Prior | Day | Bid / ask | Spread |
|--------|------|-------|-----|-----------|--------|
| TSLA | 379.32 | 380.12 | -0.21% | 379.27 / 379.37 | 2.6 bp |
| SPCX | 148.09 | 148.36 | -0.18% | 148.08 / 148.11 | 2.0 bp |
| STRC | 98.72 | 98.91 | -0.19% | 98.68 / 98.74 | 6.1 bp |
| SATA | 100.00 | 99.96 | +0.04% | 100.00 / 100.01 | 1.0 bp |
| MSTR | 162.34 | 162.20 | +0.09% | 162.25 / 162.39 | 8.6 bp |
| BITA | 63.42 | 63.48 | -0.09% | 63.50 / 63.82 | 50.3 bp |
| ASST | 30.54 | 28.99 | +5.33% | 30.52 / 30.57 | 16.4 bp |
| MARA | 13.10 | 13.35 | -1.89% | 13.09 / 13.10 | 7.6 bp |
| RIOT | 24.18 | 24.69 | -2.09% | 24.17 / 24.18 | 4.1 bp |
| CLSK | 14.27 | 14.46 | -1.35% | 14.28 / 14.29 | 7.0 bp |
| WULF | 16.31 | 16.35 | -0.24% | 16.31 / 16.32 | 6.1 bp |
| IREN | 46.15 | 47.05 | -1.91% | 46.13 / 46.16 | 6.5 bp |
| GOOGL | 341.90 | 337.83 | +1.20% | 341.87 / 341.92 | 1.5 bp |
| AAPL | 338.82 | 337.02 | +0.53% | 338.82 / 338.86 | 1.2 bp |
| NVDA | 223.78 | 225.51 | -0.77% | 223.78 / 223.80 | 0.9 bp |
| PLTR | 192.65 | 191.79 | +0.45% | 192.60 / 192.69 | 4.7 bp |
| AMZN | 248.15 | 249.27 | -0.45% | 248.12 / 248.18 | 2.4 bp |
| EVGO | 1.335 | 1.360 | -1.84% | 1.330 / 1.340 | 74.9 bp |
| RKLB | 73.00 | 70.31 | +3.83% | 72.98 / 73.02 | 5.5 bp |
| STRK | 73.50 | 73.25 | +0.34% | 73.28 / 73.75 | 63.9 bp |
| CCJ | 89.48 | 90.80 | -1.45% | 89.40 / 89.57 | 19.0 bp |
| BWXT | 140.99 | 141.81 | -0.58% | 140.97 / 141.18 | 14.9 bp |
| BE | 261.14 | 275.19 | -5.10% | 261.10 / 261.46 | 13.8 bp |
| GLDM | 84.66 | 84.75 | -0.11% | 84.71 / 84.72 | 1.2 bp |
| IAU | 80.51 | 80.52 | -0.01% | 80.50 / 80.51 | 1.2 bp |
| GLD | 392.81 | 392.88 | -0.02% | 392.71 / 392.80 | 2.3 bp |

Spot **BTC-USD** mark **$84,802.80** vs prior close **$83,929.01** (+1.04%) at 12:18 ET. No position.

## Research / rotate

**How new capital best serves the themes now:** it does not, because there is no deployable capital. A hypothetical stock dollar follows the pin (marginal dollar to SPCX while TSLA is overweight) and is not raised by selling TSLA today. A hypothetical reopen of the 40% sleeve is STRC and SATA first, then miners, with IREN the best relative value on this tape. That is not path-dependence toward the two names already held. Those two names are the entire book because the Chairman exited everything else on 2026-09-22.

**Chosen:** TSLA (growth equity) and SPCX (space), both hold.

**Rejected with reasons:** see the decision record. Short form: rotate blocked (Semi day + unlock day + pin noise). STRC/SATA not illiquid and not covered by MSTR. Miners not blocked for overlap. Watchlist ready names not seated under the pin. Gold research-only. BE blocked. Private names not deployable.

No deep-dive refresh. The newest required dives (2026-08-04 through 2026-08-31) are inside 90 days, and this pass proposes no first buy.

## Thesis / risk / critic

Thesis: hold. Risk: ok to do nothing; the rotate is affordable and liquid and still the wrong ticket. Critic: block the rotate and block the rules-engine 40/60 rebuild. Executor: no orders.

## Do not trade

- Do not sell TSLA to buy SPCX today.
- Do not reopen the BTC sleeve by selling the locked book.
- Do not seat GOOGL, AAPL, NVDA, PLTR, AMZN, RKLB, CCJ, BWXT, EVGO, or STRK.
- Do not buy BE.
- Do not buy gold until the owner calls it.
