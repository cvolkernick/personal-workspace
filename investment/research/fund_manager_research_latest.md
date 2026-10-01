# Fund manager research — 2026-10-01 (~11:53 ET, mid-session HOLD)

**As of:** 2026-10-01 ~15:53Z (~11:53 ET). Regular hours. Inside the mid-session window. Not the open, not the close. This pass closes the 15:51Z rules re-fire (`need_llm`: deployed mix outside 40/60 ±5%).
**Account:** agentic ••••1752 only. Primary margin was read (value $0.09, equity $0, cash $0.09) and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor) on live Robinhood quotes. The `fund-manager-research` workflow was not launched: it cannot see this pass's live quotes, cannot create buying power, and cannot override the Chairman 2026-09-23 order not to sell TSLA/SPCX to finish the 40%. Morning digest `investment/digests/2026-10-01.md` is reopen context; its "live book is TSLA+SPCX only" line is stale — the complex was reopened earlier today.
**Live NAV:** broker **$399.78** (equity **$399.75**, cash **$0.03**, buying power **$0.03**). Quote equity at ~15:53Z **$399.70**. Unsettled **$0**. Pending deposits **$75** (not buying power). Crypto value **$0**. Crypto buying power **$0.03**. Open equity orders: none (`new`, `queued`, `confirmed` empty).
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | STRC, SATA, IREN, RIOT, CLSK, WULF, MARA, TSLA, SPCX. All complex quantities are intraday (bought 2026-10-01). |
| Quote equity ~15:53Z | Complex **$73.71 (18.4%)** / stocks **$325.99 (81.6%)** |
| Credit inside the complex | STRC **$16.00** + SATA **$14.00** = **$30.00 (40.7%)**. Five miners **$43.71 (59.3%)**. |
| Stocks pin | TSLA **$159.71 (49.0%)** / SPCX **$166.28 (51.0%)**. One-way gap **$3.29** (~1.0% of the sleeve). Written band is ±5% (~$16). |
| Cash | **$0.03**, below the $1 minimum |
| Gap to 40% without selling | **~$144** of new complex capital. Pending $75, once it is buying power, does not close the gap. |

| Symbol | Last | Prior close | Day | Bid / ask | Spread | Role |
|--------|------|-------------|-----|-----------|--------|------|
| TSLA | 356.19 | 354.81 | +0.4% | 356.18 / 356.22 | ~1 bp | held, pin 50. Deliveries Friday 2026-10-02. |
| SPCX | 150.552 | 150.86 | −0.2% | 150.54 / 150.56 | ~1 bp | held, pin 50 |
| STRC | 99.32 | 99.35 | ~0% | 99.30 / 99.34 | ~4 bp | held preferred core (Strategy). At par. |
| SATA | 100.00 | 100.01 | ~0% | 100.00 / 100.01 | ~1 bp | held preferred core (Strive, not Strategy). At par. |
| IREN | 39.89 | 40.88 | −2.4% | 39.88 / 39.89 | ~3 bp | held miner / HPC |
| WULF | 14.555 | 14.80 | −1.7% | 14.55 / 14.56 | ~7 bp | held miner / power |
| MARA | 11.035 | 11.33 | −2.6% | 11.03 / 11.04 | ~9 bp | held miner |
| CLSK | 12.2295 | 12.88 | −5.1% | 12.22 / 12.23 | ~8 bp | held miner. Red tape, not a sale. |
| RIOT | 18.98 | 20.15 | −5.8% | 18.97 / 18.98 | ~5 bp | held miner. Weakest day, not overlap, not a sale. |
| MSTR | 156.64 | 153.09 | +2.3% | 156.60 / 156.70 | ~6 bp | unheld. Behind STRC/SATA into MSCI ~Oct 16. |
| ASST | 30.07 | 29.41 | +2.2% | 30.05 / 30.09 | ~13 bp | unheld Strive common. Behind its preferred SATA. |
| BITA | 63.385 | 63.1052 | +0.4% | 63.25 / 63.47 | ~35 bp | unheld. Last print 15:28Z. Wide vs the preferreds. |
| STRK | 74.69 | 74.16 | +0.7% | 74.60 / 74.82 | ~29 bp | watch preferred. Junior to held STRC. |
| GOOGL | 339.83 | 344.08 | −1.2% | tight | ~2 bp | watch. Pin blocks. |
| AAPL | 326.04 | 333.02 | −2.1% | tight | ~1 bp | watch. Pin blocks. Behind NVDA/GOOGL/PLTR. |
| NVDA | 229.10 | 228.38 | +0.3% | tight | ~1 bp | watch. Pin blocks. |
| PLTR | 188.05 | 187.05 | +0.5% | 187.92 / 188.07 | ~8 bp | watch. Pin blocks. Multiple still the gate. |
| AMZN | 246.50 | 249.15 | −1.1% | tight | ~1 bp | watch. Pin blocks. Behind GOOGL/NVDA. |
| RKLB | 70.22 | 69.68 | +0.8% | 70.20 / 70.24 | ~6 bp | watch. Neutron still unflown. SPCX is the space core. |
| EVGO | 1.27 | 1.26 | +0.8% | 1.27 / 1.28 | ~78 bp | watch, low, show-me. Wide. |
| CCJ | 83.95 | 86.67 | −3.1% | 83.93 / 84.00 | ~8 bp | watch nuclear. Pin blocks. Not a BE substitute. |
| BWXT | 137.07 | 137.00 | ~0% | 136.84 / 137.15 | ~23 bp | watch. Behind CCJ. Pin blocks. |
| HYPD | 3.715 | 3.745 | −0.8% | 3.68 / 3.72 | ~108 bp | watch, low, non-BTC. Wide. Pin blocks. |

Spot **BTC-USD** mark **$84,113.69** vs prior **$83,706.14** (+0.5%) at 11:53 ET. No position. The equity ticker BTC at $37.20 is not spot bitcoin. Crypto buying power is the same $0.03. Coinbase / self-custody remains the coin path.

No material tape change versus the morning digest that would break a seat. Miners are down while spot BTC is flat-to-up; that is the known hash-vs-HPC dispersion, not a same-day thesis failure. STRC and SATA are at par on 4 bp and 1 bp books. No liquidity rebuttal.

Ready deep-dives are inside 90 days (oldest public set 2026-08-04, newest HYPD 2026-09-27). This pass proposes no first buy, so none were refreshed.

## Research / rotate

**How new capital best serves the themes now.** There is no deployable capital. The book is 18% complex / 82% stocks because the 2026-09-22 exit was only partly refilled by this morning's ~$75, not because TSLA and SPCX won a relative-value contest for the next dollar. A hypothetical new dollar — including the pending $75 once it is buying power — goes to the **BTC complex** until that sleeve is inside the 40% band. It does not top up the largest held names.

Inside that dollar: **keep STRC and SATA in the ticket** (already 40.7% of the complex, so not the entire ticket and not zero). Then **add across the miner sleeve** (IREN, RIOT, CLSK, WULF, MARA). Today's RIOT −5.8% and CLSK −5.1% are not an overlap block and not a reason to sell the seats bought this morning. MSTR is liquid and green today (+2.3%) and still sits behind the preferreds through the MSCI window (~Oct 16). BITA is last on liquidity (35 bp, stale print), not instead of STRC/SATA. ASST common stays behind SATA. STRK stays junior (29 bp, discount to par, not a substitute). Stock dollars, only after the complex is being rebuilt or the Chairman designates a stocks deposit, stay 50/50 TSLA/SPCX. Do not sell SPCX to fund the $3.29 pin gap, and do not sell TSLA into Friday's delivery print.

**Chosen this pass (hold):** TSLA and SPCX (growth / space pin). STRC and SATA (digital credit). IREN, RIOT, CLSK, WULF, MARA (BTC infrastructure, diversified on purpose).

**Rejected:** any buy (BP $0.03). Any sale of TSLA/SPCX to manufacture the 40%. Any same-day sale of the red miners. MSTR, BITA, ASST, STRK as the next dollar ahead of the seated preferreds. Ready watchlist equities under the stocks pin. BE blocked. Gold research-only. Equity-ticker BTC. Private names not deployable.

## Thesis / risk / critic

Thesis: hold. The 18/82 mix is the allocation fact; the funding path is new complex capital, not a sale and not a stock top-up. Risk: ok to do nothing. $0.03 is below $1. No name is a concentration breach at this NAV. Critic: block held-only top-up of TSLA/SPCX, block a rules-engine rebuild by selling the pin, block a "miner overlap" sale of RIOT/CLSK, and do not treat the existing STRC/SATA seat as a reason to skip them on the next complex dollar. Executor: no orders.
