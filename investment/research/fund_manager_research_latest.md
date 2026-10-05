# Fund manager research — 2026-10-05 (~10:52 ET, mid-session HOLD)

**As of:** 2026-10-05 ~14:52Z (~10:52 ET). Regular hours. Inside the mid-session window (after the first 30 minutes, before the last 30). Not the open, not the close. This is a fresh book read. The 14:36Z rules `need_llm` was already closed at 10:38 ET; this pass does not reopen it.
**Account:** agentic ••••1752 only. Primary margin was read (value $0.09, equity $0, crypto about $0.004, cash/BP $0.09, pending deposits $0) and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor) on live Robinhood quotes. The `fund-manager-research` workflow was not launched: it cannot see this pass's live quotes, cannot create buying power, and cannot override the Chairman 2026-09-22 order not to sell TSLA/SPCX to finish the 40%. Morning digest `investment/digests/2026-10-05.md` is the theme scan.
**Live NAV:** broker **$427.17** (equity **$427.03**, cash **$0.14**, buying power **$0.14**). Quote equity at ~14:52Z **$426.95**. Unsettled **$0**. Pending deposits **$75** (not buying power). Crypto value **$0**. Crypto buying power **$0.14**. Open equity orders: none (`new`, `queued`, `confirmed`, `partially_filled` empty).
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | STRC, SATA, IREN, RIOT, CLSK, WULF, MARA, TSLA, SPCX |
| Quote equity ~14:52Z | Complex **$74.02 (17.3%)** / stocks **$352.92 (82.7%)** |
| Credit inside the complex | STRC **$16.05** + SATA **$14.00** = **$30.05 (40.6%)**. Five miners **$43.98 (59.4%)**. |
| Stocks pin | TSLA **$168.35 (47.7%)** / SPCX **$184.58 (52.3%)**. One-way gap **$16.23** (~4.6% of the sleeve). |
| Cash | **$0.14**, below the $1 minimum |
| Gap to 40% without selling | **~$161** of new complex capital. Pending $75, once it is buying power, lifts the complex to about **29.7%** and does not reach the 35% band (~$116 still needed). |

| Symbol | Last | Friday close | Day | Bid / ask | Spread | Role |
|--------|------|--------------|-----|-----------|--------|------|
| TSLA | 375.45 | 370.59 | +1.3% | 375.43 / 375.50 | 2 bp | held, pin 50 |
| SPCX | 167.115 | 158.96 | +5.1% | 167.10 / 167.13 | 2 bp | held, pin 50. Flight 14 tape. Not a chase. |
| STRC | 99.60 | 99.41 | +0.2% | book 99.57 x 1101 / 99.60 x 1120 | 3 bp | held preferred core (Strategy). NBBO was 5 bp. Not a liquidity skip. |
| SATA | 100.005 | 100.01 | flat | book 100.00 x 17994 / 100.01 x 2411 | 1 bp | held preferred core (Strive, not Strategy). At par. |
| IREN | 40.09 | 41.76 | −4.0% | 40.08 / 40.09 | 3 bp | held miner / HPC. Write-down headline is not a sale. |
| WULF | 14.745 | 15.49 | −4.8% | 14.74 / 14.75 | 7 bp | held miner / power. Worst print, not a sale. |
| MARA | 10.935 | 11.23 | −2.6% | 10.93 / 10.94 | 9 bp | held miner. Smallest seat. |
| CLSK | 12.31 | 12.74 | −3.4% | 12.30 / 12.31 | 8 bp | held miner |
| RIOT | 19.235 | 19.73 | −2.5% | 19.23 / 19.24 | 5 bp | held miner |
| MSTR | 162.615 | 160.01 | +1.6% | book 162.46 x 10 / 162.58 x 125 | 7 bp | unheld. Sized-down starter on the next complex dollar. MSCI open through Oct 16. NBBO ~4 bp. |
| BITA | 63.58 | 62.9153 | +1.1% | book 63.50 x 1 / 63.63 x 200 | 20 bp | unheld. Tighter than the 10:38 ~38 bp print. Under 100 bp. $5 seat on the next dollar, not ahead of the preferreds. |
| ASST | 30.80 | 30.03 | +2.6% | 30.79 / 30.84 | 16 bp | unheld Strive common. Behind its preferred SATA. |
| STRK | 76.565 | 76.25 | +0.4% | 76.50 / 76.84 | 44 bp | watch preferred. Wider than the 10:38 ~26 bp print. Junior 8%. Not a substitute for STRC. |
| GOOGL | 343.83 | 343.50 | +0.1% | tight | 1 bp | watch. Pin blocks. |
| AAPL | 334.115 | 333.69 | +0.1% | tight | 1 bp | watch. Pin blocks. Behind NVDA/GOOGL/PLTR. |
| NVDA | 237.27 | 233.95 | +1.4% | tight | <1 bp | watch. Pin blocks. |
| PLTR | 188.065 | 188.75 | −0.4% | 188.01 / 188.11 | 5 bp | watch. Pin blocks. |
| AMZN | 251.635 | 251.52 | flat | tight | 1 bp | watch. Pin blocks. Behind GOOGL/NVDA. |
| RKLB | 72.445 | 73.92 | −2.0% | 72.43 / 72.45 | 3 bp | watch. Neutron unflown. Not a dip-buy. SPCX is the space core. |
| EVGO | 1.36 | 1.38 | −1.4% | 1.36 / 1.37 | 73 bp | watch, low, show-me. Wide. |
| CCJ | 88.015 | 85.18 | +3.3% | 87.96 / 88.04 | 9 bp | watch nuclear. Pin blocks. Not a BE substitute. |
| BWXT | 137.66 | 134.86 | +2.1% | 137.55 / 137.70 | 11 bp | watch. Behind CCJ. Pin blocks. |
| HYPD | 3.82 | 3.73 | +2.4% | 3.81 / 3.85 | 104 bp | watch, low, non-BTC. Wide. Pin blocks. |
| GLDM | 81.95 | 82.05 | −0.1% | 81.95 / 81.96 | 1 bp | gold research-only. No buy called. |
| BTC (equity) | 37.84 | 37.25 | +1.6% | 37.82 / 37.83 | 3 bp | not spot bitcoin |

Spot **BTC-USD** mark **$85,576.60** vs midnight **$86,068.50** (−0.57%) at 10:52 ET. About +1.4% versus the Friday **$84,364** mark carried from the morning note. No position. Coinbase / self-custody remains the coin path.

Ready deep-dives are inside 90 days (oldest public set 2026-08-04, newest HYPD 2026-09-27). This pass proposes no first buy, so none were refreshed. No new watchlist add. Private names stay out of the deploy set.

## Research / rotate

**How new capital best serves the themes now.** There is no deployable capital. The book is 17% complex / 83% stocks because the 2026-09-22 exit was only partly refilled on 2026-10-01, not because TSLA and SPCX won a relative-value contest for the next dollar. SPCX +5.1% keeps the pin gap wide; that is a reason not to chase the stocks sleeve. A hypothetical new dollar — including the pending $75 once it is buying power — goes to the **BTC complex** until that sleeve is inside the 40% band.

Inside that dollar: **keep STRC and SATA in the ticket** (already 40.6% of the complex, so not the entire ticket and not zero). Tilt the new credit dollars to the smaller, higher-stated-rate seat (SATA ~$12, STRC ~$10, about $22 together, ~34.9% of the enlarged complex). The STRC book is 3 bp with more than 1,000 shares a side. SATA is 1 bp at par. Neither spread is a skip. Then a sized-down **MSTR ~$10** (MSCI still open; the unconfirmed 334 BTC social report is not an 8-K; +1.6% vs Friday is about 19 bp ahead of the Friday spot mark because spot faded, a cap not a drop; 7 bp book clears $10). Then a smaller **BITA ~$5** (book tightened to ~20 bp from ~38 bp at 10:38; last $63.58 is inside the book; do not fund it by skipping the preferreds; cut only if the book re-widens past ~100 bp, and do not backfill that cut into the preferreds or one miner). The remaining **~$38** is multi-miner diversification: MARA ~$9, RIOT ~$8, CLSK ~$8, WULF ~$7, IREN ~$6. Today's red miner tape is not an overlap block and not a thesis failure.

**Chosen this pass (hold):** TSLA and SPCX (growth / space pin). STRC and SATA (digital credit). IREN, RIOT, CLSK, WULF, MARA (BTC infrastructure, diversified on purpose).

**Rejected this pass:** any buy (BP $0.14). Any sale of TSLA/SPCX to manufacture the 40% or to tighten the $16.23 pin gap. Any sale of the red miners. MSTR, BITA, ASST, and STRK as a buy today (no cash). ASST and STRK also rejected on the future ticket (structure: SATA already expresses Strive; STRK is 8%, junior, and the book widened to ~44 bp). Ready watchlist equities under the stocks pin. BE blocked. Gold research-only. Equity-ticker BTC. Private names not deployable. The displayed $75 deposit.

## Thesis / risk / critic

Thesis: hold. The 17/83 mix is the allocation fact; the funding path is new complex capital, not a sale and not a stock top-up. Risk: ok to do nothing. $0.14 is below $1. The pending $75 is not buying power. No name is a concentration breach at this NAV. Multi-miner is diversification, not a block. Critic: block held-only top-up of TSLA/SPCX, block a rules-engine rebuild by selling the pin, block a "miner overlap" sale of the red miners, and do not treat the existing STRC/SATA seat as a reason to skip them on the next complex dollar. The wider STRK book is a stronger reject, not a buy. The tighter BITA book does not jump BITA ahead of the preferreds. Executor: no orders.
