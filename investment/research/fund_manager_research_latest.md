# Fund manager research — 2026-10-07 (~12:26 ET, mid-session HOLD)

**As of:** 2026-10-07 ~16:26Z (~12:26 ET). Regular hours. Inside the mid-session window (after the first 30 minutes, before the last 30). Not the open, not the close.
**Account:** agentic ••••1752 only. Primary margin was read (value $0.09, equity $0, crypto about $0.004, cash/BP $0.09, pending deposits $0) and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor) on live Robinhood quotes and books. `/fund-manager-research` was not launched: it cannot see this pass's live quotes, cannot create buying power, and is not order authority. Morning digest `investment/digests/2026-10-07.md` is the theme scan. This pass closes the 16:22Z rules `need_llm` (cost-basis 19.5/80.5).
**Live NAV:** broker **$426.32** (equity **$426.16**, cash **$0.16**, buying power **$0.16**). Quote equity at ~16:23Z **$426.09**. Unsettled **$0**. Pending deposits **$0** (the Oct 6 displayed $75 did not post). Crypto value **$0**. Open equity orders: none (`new`, `queued`, `confirmed` empty).
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | STRC, SATA, IREN, WULF, RIOT, CLSK, MARA, TSLA, SPCX |
| Unheld core | MSTR, BITA, ASST, spot BTC. No spot position. |
| Quote equity ~16:23Z | Complex **$71.70 (16.8%)** / stocks **$354.39 (83.2%)** |
| Credit inside the complex | STRC **$16.04** + SATA **$13.99** = **$30.04 (41.9%)**. Five miners **$41.66 (58.1%)**. |
| Stocks pin | TSLA **$168.55 (47.6%)** / SPCX **$185.84 (52.4%)**. One-way gap **$8.65**. |
| Cash | **$0.16**, below the $1 minimum |
| Gap to 40% without selling | About **$99** of the current book, or about **$165** of new complex capital once the denominator grows. The 35% band still needs about **$119** of new complex capital. |

| Symbol | Last | Tue close | Day | Book / spread | Role |
|--------|------|-----------|-----|---------------|------|
| TSLA | 375.90 | 380.68 | −1.3% | ~1 bp | held, pin 50 |
| SPCX | 168.26 | 171.92 | −2.1% | ~1 bp | held, pin 50 |
| STRC | 99.59 | 99.47 | +0.1% | 99.57 × 9645 / 99.58 × 153, 1 bp | held preferred core |
| SATA | 99.945 | 100.01 | flat | 99.94 × 194 / 99.95 × 598, 1 bp | held preferred core (Strive). At par. |
| IREN | 38.57 | 41.28 | −6.6% | 38.54 × 991 / 38.55 × 225, 3 bp | held miner / HPC. Not a sale. |
| WULF | 14.22 | 14.97 | −5.0% | quote ~7 bp | held miner / power. Not a sale. |
| RIOT | 18.13 | 18.93 | −4.2% | quote ~6 bp | held miner. Least-bad miner print. |
| CLSK | 11.48 | 12.29 | −6.6% | quote ~9 bp | held miner |
| MARA | 10.185 | 10.97 | −7.2% | 10.17 × 2655 / 10.18 × 8529, 10 bp | held miner. Smallest seat, worst print. |
| MSTR | 155.79 | 164.55 | −5.3% | 155.78 × 7 / 155.82 × 12, 2.6 bp | unheld. Sized-down starter on the next complex dollar. ~468 bp behind spot. MSCI consultation still open through Oct 16. Inside ask clears $10. |
| BITA | 62.42 | 63.73 | −2.1% | 62.26 × 5200 / 62.53 × 200, 43 bp | unheld. Under the 100 bp cut. $5 seat, not ahead of the preferreds. |
| ASST | 28.47 | 29.75 | −4.3% | 28.47 × 119 / 28.50 × 130, 11 bp | unheld Strive common. Behind its preferred SATA. |
| STRK | 76.00 | 75.77 | +0.3% | 75.35 × 1 / 76.00 × 55, 86 bp | watch preferred. Wider than the 11:57 ~26 bp print. One-share bid. Junior 8%. Not a substitute for STRC. |
| BTC spot | 83,583 | midnight 84,133 | −0.65% | 1-tick book | unheld. Hard-money seat on the next dollar. |
| BTC equity | 36.88 | 37.84 | −2.5% | ~3 bp | not spot bitcoin. Identity reject. |
| GOOGL | 345.85 | 347.68 | −0.5% | tight | watch. Pin blocks. |
| AAPL | 334.98 | 333.63 | +0.4% | tight | watch. Pin blocks. Behind NVDA/GOOGL/PLTR. |
| NVDA | 237.14 | 239.24 | −0.9% | tight | watch. Pin blocks. |
| PLTR | 193.88 | 192.07 | +0.9% | tight | watch. Pin blocks. |
| AMZN | 259.13 | 256.29 | +1.1% | tight | watch. Pin blocks. Behind GOOGL/NVDA. |
| RKLB | 70.90 | 75.06 | −5.6% | ~4 bp | watch. Neutron unflown. Not a dip-buy. SPCX is the space core. |
| EVGO | 1.275 | 1.32 | −3.4% | ~78 bp | watch, low, show-me. Wide. |
| CCJ | 89.39 | 93.02 | −3.9% | ~6 bp | watch nuclear. Pin blocks. Not a BE substitute. |
| BWXT | 140.59 | 145.70 | −3.5% | ~14 bp | watch. Behind CCJ. Pin blocks. |
| HYPD | 3.57 | 3.82 | −6.5% | ~56 bp | watch, low, non-BTC. Pin blocks. |
| GLDM | 81.37 | 82.45 | −1.3% | 1 bp | gold research-only. No buy called. |

Ready deep-dives are inside 90 days (oldest public set 2026-08-04, newest HYPD 2026-09-27; today is day 64). This pass proposes no first buy, so none were refreshed. No new watchlist add. Private names (Anduril, Saronic, Boom) stay out of the deploy set; no listing catalyst in today's digest.

## Research / rotate

**How new capital best serves the themes now.** There is no deployable capital. The book is 17% complex / 83% stocks because the 2026-09-22 exit was only partly refilled, not because TSLA and SPCX won a relative-value contest for the next dollar. Both pins are down today, so this is also not a chase of the stocks sleeve. A hypothetical new dollar goes entirely to the **BTC complex** until that sleeve is inside the 35% band. About $165 of new complex capital lands the sleeve on 40% without a sale. Chairman 2026-09-23 still bars selling TSLA or SPCX to manufacture that gap. `consider_share.json` remains TSLA 50 / SPCX 50 for the stocks sleeve only.

Inside that dollar, on this tape: **keep STRC and SATA in the ticket** (already 41.9% of the complex, so not the entire ticket and not zero). They are the only complex names that are flat, the books are 1 bp, and the owner's yield-versus-cash bias still applies. Tilt the new credit dollars to the smaller seat (SATA ~$15, STRC ~$13, about $28 together). On a $75 deposit that leaves preferreds at about 40% of the enlarged complex — a small bias, not all-credit. Then a sized-down **MSTR ~$10** (MSCI consultation still open through Oct 16; −5.3% is about 468 bp behind spot's −0.65%, which caps the starter and does not drop it; 2.6 bp book clears $10). Then **spot BTC ~$6** (the hard-money expression; do not buy the $36.88 equity ticker). Then a smaller **BITA ~$5** (43 bp, under the 100 bp cut; do not fund it by skipping the preferreds). The remaining **~$26** is multi-miner diversification: RIOT ~$6.50, CLSK ~$6, WULF ~$5.50, IREN ~$5, MARA ~$3. Today's red miner tape is not an overlap block and not a thesis failure. MARA is the smallest add because it is already the smallest seat, it is the worst print (−7.2%), and the carried 10b5-1 plus second bearish note is a relative-value tilt — not a sale and not an overlap rejection. Do not enlarge IREN or WULF for the carried AI/HPC headlines.

Scale the same order if the ticket is smaller. Do not drop names from the consider set because the book is small. Below three $1 minimums, the first dollars are SATA and STRC only.

**Chosen this pass (hold):** TSLA and SPCX (growth / space pin). STRC and SATA (digital credit). IREN, WULF, RIOT, CLSK, MARA (BTC infrastructure, diversified on purpose).

**Chosen on the next complex dollar (not an order):** SATA and STRC (digital credit, first), MSTR (unheld treasury equity, sized down), spot BTC (bitcoin), BITA (unheld credit, smaller), then all five miners.

**Rejected this pass:** any buy (BP $0.16). Any sale of TSLA/SPCX to manufacture the 40% or to tighten the $8.65 pin gap. Any sale of the red miners. MSTR, BITA, spot BTC, ASST, and STRK as a buy today (no cash). ASST also rejected on the future ticket (SATA already expresses Strive). STRK also rejected on the future ticket (8% junior preferred, and the book widened to ~86 bp with a one-share bid — no residual versus STRC). Ready watchlist equities under the stocks pin and because the stocks sleeve is already 83%. BE blocked. Gold research-only. Equity-ticker BTC. Private names not deployable. The displayed $75 deposit, which is not buying power.

## Thesis / risk / critic

Thesis: hold. The 17/83 mix is the allocation fact; the funding path is new complex capital, not a sale and not a stock top-up. The next dollar is not held-only: it opens MSTR, spot BTC, and BITA, and it keeps both preferreds.

Risk: ok to do nothing. $0.16 is below $1. No name is a concentration breach at this NAV. STRC and SATA liquidity is not a skip. BITA at 43 bp is acceptable for a $5 seat and is cut only if a later book is past about 100 bp, without backfilling that cut into the preferreds or one miner. STRK at 86 bp with a one-share bid is a liquidity reject on top of structure. Multi-miner is diversification, not a block. Do not sell into the red miner tape to fund an unheld name while the complex is underweight and no new cash exists.

Critic: block held-only top-up of TSLA/SPCX, block a rebuild of the 40% by selling the pin, block a "miner overlap" sale or skip of the red miners, and do not treat the existing STRC/SATA seat as a reason to skip them on the next complex dollar. The wider STRK book is a stronger reject, not a buy. The BITA book does not jump BITA ahead of the preferreds. MSTR's lag versus spot caps the starter; it does not delete it. Executor: no orders.
