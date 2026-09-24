# Fund manager research — 2026-09-24 (~12:35 ET, mid-session HOLD)

**As of:** 2026-09-24 ~16:35Z (~12:35 ET). Regular hours. Outside the open and the close. This pass answers the 16:32Z and 16:34Z rules re-fires (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60 ±5%).
**Account:** agentic ••••1752 only. Primary margin was read for the treasury snapshot and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** broker **$334.11** at ~12:35 ET (cash **$0.03**, buying power **$0.03**). Quote equity at 16:35Z **$334.04**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none.
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Quote equity 16:35Z | TSLA **$170.23** (50.96%) / SPCX **$163.80** (49.04%) |
| 50/50 gap | **$3.22** one-way (was $3.26 at 12:21 ET, $2.43 at 12:08 ET, $3.35 at 11:03 ET, $3.86 at 09:50 ET) |
| Day | TSLA **-0.12%**. SPCX **-0.04%** |
| Deployed mix | BTC-complex **0%** / stocks **100%** |
| Cash | **$0.03** unallocated dust, below the $1 minimum |

Chairman standing order in `investment/consider_share.json` (2026-09-22), restated in `fund_manager.json` `targets.symbol_targets`: stocks sleeve is **50% SPCX / 50% TSLA** by market value. No other equity. Crypto and cash unchanged unless the Chairman says otherwise. Today's digest proposed no pin change.

Held names and the rest of the consider set are 16:35Z, except STRK's last trade at 16:16Z. Daily change uses adjusted previous close.

| Symbol | Last | Prior | Day | Bid / ask | Spread |
|--------|------|-------|-----|-----------|--------|
| TSLA | 379.66 | 380.12 | -0.12% | 379.60 / 379.66 | 1.6 bp |
| SPCX | 148.305 | 148.36 | -0.04% | 148.30 / 148.31 | 0.7 bp |
| STRC | 98.735 | 98.91 | -0.18% | 98.72 / 98.75 | 3.0 bp |
| SATA | 100.005 | 99.9584 | +0.05% | 100.00 / 100.01 | 1.0 bp |
| MSTR | 161.715 | 162.20 | -0.30% | 161.69 / 161.77 | 4.9 bp |
| BITA | 63.58 | 63.475 | +0.17% | 63.52 / 63.63 | 17.3 bp |
| ASST | 30.24 | 28.99 | +4.31% | 30.22 / 30.24 | 6.6 bp |
| MARA | 12.998 | 13.35 | -2.64% | 12.99 / 13.00 | 7.7 bp |
| RIOT | 24.005 | 24.69 | -2.77% | 24.00 / 24.02 | 8.3 bp |
| CLSK | 14.185 | 14.46 | -1.90% | 14.18 / 14.19 | 7.0 bp |
| WULF | 16.315 | 16.35 | -0.21% | 16.31 / 16.32 | 6.1 bp |
| IREN | 46.088 | 47.05 | -2.04% | 46.08 / 46.09 | 2.2 bp |
| GOOGL | 341.265 | 337.83 | +1.02% | 341.24 / 341.29 | 1.5 bp |
| AAPL | 337.80 | 337.02 | +0.23% | 337.79 / 337.81 | 0.6 bp |
| NVDA | 223.37 | 225.51 | -0.95% | 223.37 / 223.38 | 0.4 bp |
| PLTR | 193.52 | 191.79 | +0.90% | 193.48 / 193.53 | 2.6 bp |
| AMZN | 247.81 | 249.27 | -0.59% | 247.81 / 247.84 | 1.2 bp |
| EVGO | 1.325 | 1.360 | -2.57% | 1.32 / 1.33 | 75.5 bp |
| RKLB | 74.135 | 70.31 | +5.44% | 74.12 / 74.15 | 4.0 bp |
| STRK | 73.50 | 73.25 | +0.34% | 73.28 / 73.75 | 64.0 bp |
| CCJ | 89.42 | 90.80 | -1.52% | 89.34 / 89.53 | 21.2 bp |
| BWXT | 141.19 | 141.81 | -0.44% | 141.01 / 141.35 | 24.1 bp |
| BE | 262.78 | 275.19 | -4.51% | 262.63 / 262.90 | 10.3 bp |
| GLDM | 84.485 | 84.75 | -0.31% | 84.50 / 84.51 | 1.2 bp |
| IAU | 80.33 | 80.52 | -0.24% | 80.29 / 80.30 | 1.2 bp |
| GLD | 391.695 | 392.88 | -0.30% | 391.68 / 391.74 | 1.5 bp |

Spot **BTC-USD** mark **$84,444.60** vs prior close **$83,929.02** (+0.61%) at 12:35 ET. No position. STRK last trade 16:16Z; the 64 bp book is the live quote.

## Research / rotate

**How new capital best serves the themes now:** it does not, because there is no deployable capital. A hypothetical stock dollar follows the pin (marginal dollar to SPCX while TSLA is overweight) and is not raised by selling TSLA today. A hypothetical reopen of the 40% sleeve is STRC and SATA first, then miners. RIOT is the deepest tape (-2.77%) and IREN remains the structural relative-value miner (digest discount vs MARA/WULF multiples, tight 2.2 bp book, -2.04% today). That is not path-dependence toward the two names already held. Those two names are the entire book because the Chairman exited everything else on 2026-09-22.

**Chosen:** TSLA (growth equity) and SPCX (space), both hold.

**Rejected with reasons:** see the decision record. Short form: rotate blocked (Semi day + unlock day + pin noise that narrowed, not widened). STRC/SATA not illiquid and not covered by MSTR. Miners not blocked for overlap. Watchlist ready names not seated under the pin. Gold research-only. BE blocked. Private names not deployable.

No deep-dive refresh. The newest required dives (2026-08-04 through 2026-08-31) are inside 90 days (oldest is 51 days), and this pass proposes no first buy.

## Thesis / risk / critic

Thesis: hold. Risk: ok to do nothing; the $3.22 rotate is affordable and liquid and still the wrong ticket. Critic: block the rotate and block the rules-engine 40/60 rebuild. Executor: no orders.

## Do not trade

- Do not sell TSLA to buy SPCX today.
- Do not reopen the BTC sleeve by selling the locked book.
- Do not seat GOOGL, AAPL, NVDA, PLTR, AMZN, RKLB, CCJ, BWXT, EVGO, or STRK.
- Do not buy BE.
- Do not buy gold until the owner calls it.
