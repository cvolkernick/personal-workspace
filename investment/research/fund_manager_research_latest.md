# Fund manager research — 2026-10-05 (~10:22 ET, mid-session HOLD)

**As of:** 2026-10-05 ~14:22Z (~10:22 ET). Regular hours. Inside the mid-session window. Not the open, not the close. This pass closes the 14:20Z rules re-fire (`need_llm`: deployed mix outside 40/60 ±5%).
**Account:** agentic ••••1752 only. Primary margin was read (value $0.09, equity $0, crypto about $0.004, cash/BP $0.09) and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor) on live Robinhood quotes. The `fund-manager-research` workflow was not launched: it cannot see this pass's live quotes, cannot create buying power, and cannot override the Chairman 2026-09-22 order not to sell TSLA/SPCX to finish the 40%. Morning digest `investment/digests/2026-10-05.md` is the theme scan.
**Live NAV:** broker **$427.16** (equity **$427.02**, cash **$0.14**, buying power **$0.14**). Quote equity at ~14:21Z **$427.42**. Unsettled **$0**. Pending deposits **$75** (not buying power). Crypto value **$0**. Crypto buying power **$0.14**. Open equity orders: none (`new`, `queued`, `confirmed` empty).
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | STRC, SATA, IREN, RIOT, CLSK, WULF, MARA, TSLA, SPCX |
| Quote equity ~14:21Z | Complex **$74.06 (17.3%)** / stocks **$353.36 (82.7%)** |
| Credit inside the complex | STRC **$16.05** + SATA **$14.00** = **$30.05 (40.6%)**. Five miners **$44.02 (59.4%)**. |
| Stocks pin | TSLA **$169.20 (47.9%)** / SPCX **$184.15 (52.1%)**. One-way gap **$14.95** (~4.2% of the sleeve). |
| Cash | **$0.14**, below the $1 minimum |
| Gap to 40% without selling | **~$162** of new complex capital. Pending $75, once it is buying power, lifts the complex to about **29.7%** and does not reach the 35% band (~$116 still needed). |

| Symbol | Last | Friday close | Day | Bid / ask | Spread | Role |
|--------|------|--------------|-----|-----------|--------|------|
| TSLA | 377.36 | 370.59 | +1.8% | 377.30 / 377.39 | 2 bp | held, pin 50 |
| SPCX | 166.73 | 158.96 | +4.9% | 166.73 / 166.75 | 1 bp | held, pin 50. Flight 14 tape. Not a chase. |
| STRC | 99.615 | 99.41 | +0.2% | 99.60 / 99.63 | 3 bp | held preferred core (Strategy). L2 99.59 x 900 / 99.63 x 10. |
| SATA | 100.01 | 100.01 | flat | 100.00 / 100.01 | 1 bp | held preferred core (Strive, not Strategy). At par. |
| IREN | 40.185 | 41.76 | −3.8% | 40.18 / 40.19 | 3 bp | held miner / HPC. Write-down headline is not a sale. |
| WULF | 14.755 | 15.49 | −4.7% | 14.75 / 14.76 | 7 bp | held miner / power. Worst print, not a sale. |
| MARA | 10.926 | 11.23 | −2.7% | 10.92 / 10.93 | 9 bp | held miner. Smallest seat. |
| CLSK | 12.28 | 12.74 | −3.6% | 12.28 / 12.29 | 8 bp | held miner |
| RIOT | 19.32 | 19.73 | −2.1% | 19.31 / 19.33 | 10 bp | held miner |
| MSTR | 164.32 | 160.01 | +2.7% | 164.24 / 164.33 | 6 bp | unheld. Sized-down starter on the next complex dollar. MSCI open through Oct 16. |
| BITA | 63.91 | 62.9153 | +1.6% | 63.84 / 64.07 | 36 bp | unheld. Under 100 bp. $5 seat on the next dollar, not ahead of the preferreds. |
| ASST | 30.615 | 30.03 | +2.0% | 30.60 / 30.63 | 10 bp | unheld Strive common. Behind its preferred SATA. |
| STRK | 76.625 | 76.25 | +0.5% | 76.50 / 76.65 | 20 bp | watch preferred. Junior 8%. Not a substitute for STRC. |
| GOOGL | 344.60 | 343.50 | +0.3% | tight | 1 bp | watch. Pin blocks. |
| AAPL | 333.51 | 333.69 | flat | tight | 1 bp | watch. Pin blocks. Behind NVDA/GOOGL/PLTR. |
| NVDA | 236.71 | 233.95 | +1.2% | tight | 1 bp | watch. Pin blocks. |
| PLTR | 189.10 | 188.75 | +0.2% | 189.06 / 189.16 | 5 bp | watch. Pin blocks. |
| AMZN | 251.49 | 251.52 | flat | tight | 1 bp | watch. Pin blocks. Behind GOOGL/NVDA. |
| RKLB | 72.83 | 73.92 | −1.5% | 72.81 / 72.85 | 6 bp | watch. Neutron unflown. Not a dip-buy. SPCX is the space core. |
| EVGO | 1.365 | 1.38 | −1.1% | 1.36 / 1.37 | 73 bp | watch, low, show-me. Wide. |
| CCJ | 86.76 | 85.18 | +1.9% | 86.67 / 86.80 | 15 bp | watch nuclear. Pin blocks. Not a BE substitute. |
| BWXT | 138.33 | 134.86 | +2.6% | 138.16 / 138.49 | 24 bp | watch. Behind CCJ. Pin blocks. |
| HYPD | 3.86 | 3.73 | +3.5% | 3.84 / 3.87 | 78 bp | watch, low, non-BTC. Wide. Pin blocks. |
| GLDM | 81.89 | 82.05 | −0.2% | 81.91 / 81.92 | 1 bp | gold research-only. No buy called. |
| BTC (equity) | 38.13 | 37.25 | +2.4% | 38.14 / 38.15 | 3 bp | not spot bitcoin |

Spot **BTC-USD** mark **$86,283.98** vs midnight **$86,068.50** (+0.25%) at 10:21 ET. No position. Coinbase / self-custody remains the coin path.

Ready deep-dives are inside 90 days (oldest public set 2026-08-04, newest HYPD 2026-09-27). This pass proposes no first buy, so none were refreshed. No new watchlist add. Private names stay out of the deploy set.

## Research / rotate

**How new capital best serves the themes now.** There is no deployable capital. The book is 17% complex / 83% stocks because the 2026-09-22 exit was only partly refilled on 2026-10-01, not because TSLA and SPCX won a relative-value contest for the next dollar. SPCX +4.9% widened the pin gap; that is a reason not to chase the stocks sleeve. A hypothetical new dollar — including the pending $75 once it is buying power — goes to the **BTC complex** until that sleeve is inside the 40% band.

Inside that dollar: **keep STRC and SATA in the ticket** (already 40.6% of the complex, so not the entire ticket and not zero). Tilt the new credit dollars to the smaller, higher-stated-rate seat (SATA ~$12, STRC ~$10, about $22 together, ~34.9% of the enlarged complex). Then a sized-down **MSTR ~$10** (MSCI still open; the unconfirmed 334 BTC social report is not an 8-K; +2.7% vs Friday is about 42 bp ahead of the Friday spot mark, a cap not a drop). Then a smaller **BITA ~$5** (36 bp clears $5; do not fund it by skipping the preferreds; cut only if the book re-widens past ~100 bp, and do not backfill that cut into the preferreds or one miner). The remaining **~$38** is multi-miner diversification: MARA ~$9, RIOT ~$8, CLSK ~$8, WULF ~$7, IREN ~$6. Today's red miner tape is not an overlap block and not a thesis failure.

**Chosen this pass (hold):** TSLA and SPCX (growth / space pin). STRC and SATA (digital credit). IREN, RIOT, CLSK, WULF, MARA (BTC infrastructure, diversified on purpose).

**Rejected this pass:** any buy (BP $0.14). Any sale of TSLA/SPCX to manufacture the 40% or to tighten the $14.95 pin gap. Any sale of the red miners. MSTR, BITA, ASST, and STRK as a buy today (no cash). ASST and STRK also rejected on the future ticket (structure: SATA already expresses Strive; STRK is 8% and junior to held STRC). Ready watchlist equities under the stocks pin. BE blocked. Gold research-only. Equity-ticker BTC. Private names not deployable.

## Thesis / risk / critic

Thesis: hold. The 17/83 mix is the allocation fact; the funding path is new complex capital, not a sale and not a stock top-up. Risk: ok to do nothing. $0.14 is below $1. The pending $75 is not buying power. No name is a concentration breach at this NAV. Critic: block held-only top-up of TSLA/SPCX, block a rules-engine rebuild by selling the pin, block a "miner overlap" sale of the red miners, and do not treat the existing STRC/SATA seat as a reason to skip them on the next complex dollar. Executor: no orders.
