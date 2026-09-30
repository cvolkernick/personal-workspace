# Fund manager research — 2026-09-30 (~12:36 ET, mid-session HOLD)

**As of:** 2026-09-30 ~16:35Z (~12:35 ET). Regular hours. Inside the mid-session window. Not the open, not the close. This pass closes the 16:32Z and 16:33Z rules re-fires (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60 ±5%).
**Account:** agentic ••••1752 only. Primary margin was read (equity $0, cash $0.09, value $0.09) and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it cannot see this pass's live quotes, cannot create buying power, and cannot override the Chairman 2026-09-23 order not to sell TSLA/SPCX to rebuild the 40%. Live MCP marks plus the 2026-09-30 digest (reopen context only; its "held" labels for exited names are stale).
**Live NAV:** broker **$324.33** (equity **$324.30**, cash **$0.03**, buying power **$0.03**). Quote equity at ~16:34Z **$324.39**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none (new, queued, confirmed, partially filled all empty).
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Quote equity ~16:34Z | TSLA **$157.28** (48.49%) / SPCX **$167.11** (51.51%) |
| 50/50 gap | **$4.91** one-way (1.5% of quote equity). A 1% band is ~$3.24. The written sleeve band is ±5% (~$16). |
| Day | TSLA **−0.59%** (350.775 vs 352.84). SPCX **+1.38%** (151.295 vs 149.24) |
| Books | TSLA ~2 bp (350.72 / 350.79). SPCX ~1 bp (151.28 / 151.30) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |
| Cash | **$0.03** unallocated dust, below the $1 minimum |
| Gap to 40% without selling | **~$216** of new BTC-complex capital |

Chairman standing order in `investment/consider_share.json` (2026-09-22) and `fund_manager.json` `targets.symbol_targets`: stocks sleeve is **50% SPCX / 50% TSLA** by market value for stock dollars. No other equity. The 2026-09-22 filled exit sold the BTC-complex names. Chairman 2026-09-23: do not sell TSLA/SPCX to rebuild the 40%. New capital only. The #768 bias loop does **not** delete those pins — it refuses to auto-change pins or the 60/40 targets and stages them for the Chairman. `bias_weight_staged.json` pending queue is empty. The digest's "15/15" and "nothing pinned" lines are stale relative to the file on disk.

| Symbol | Last | Prior | Day | Bid / ask | Spread | Role |
|--------|------|-------|-----|-----------|--------|------|
| TSLA | 350.775 | 352.84 | −0.59% | 350.72 / 350.79 | ~2 bp | held, pin 50 |
| SPCX | 151.295 | 149.24 | +1.38% | 151.28 / 151.30 | ~1 bp | held, pin 50 |
| STRC | 99.28 | 99.56 | −0.28% | 99.25 / 99.30 | ~5 bp | unheld preferred core; vol 974k |
| SATA | 100.01 | 100.01 | ~0% | 100.00 / 100.01 | ~1 bp | unheld preferred core (Strive, not Strategy); vol 270k |
| MSTR | 154.66 | 154.67 | ~0% | 154.54 / 154.63 | ~6 bp | unheld; −3.9% from the 160.88 open |
| BITA | 63.465 | 63.04 | +0.7% | 63.30 / 63.57 | ~43 bp | unheld; today vol 4.4k vs ~37k avg |
| ASST | 29.70 | 29.31 | +1.3% | 29.69 / 29.71 | ~7 bp | unheld Strive common |
| MARA | 11.705 | 11.99 | −2.4% | 11.70 / 11.71 | ~9 bp | unheld miner; vol 20M |
| RIOT | 20.685 | 21.39 | −3.3% | 20.68 / 20.69 | ~5 bp | unheld miner |
| CLSK | 12.955 | 13.31 | −2.7% | 12.95 / 12.96 | ~8 bp | unheld miner |
| WULF | 14.825 | 15.09 | −1.8% | 14.82 / 14.83 | ~7 bp | unheld miner |
| IREN | 41.26 | 41.38 | −0.3% | 41.26 / 41.27 | ~2 bp | unheld miner / power |
| STRK | 75.455 | 74.66 | +1.1% | 75.40 / 75.45 | ~7 bp | watch preferred; vol 85k |
| GOOGL | 351.72 | 340.92 | +3.2% | tight | ~1 bp | watch, exited, pin blocks |
| AAPL | 336.57 | 329.40 | +2.2% | tight | ~0 bp | watch, pin blocks |
| NVDA | 230.29 | 227.21 | +1.4% | tight | ~0 bp | watch, exited, pin blocks |
| PLTR | 189.95 | 186.97 | +1.6% | tight | ~2 bp | watch, pin blocks |
| AMZN | 251.67 | 246.67 | +2.0% | tight | ~2 bp | watch, pin blocks |
| RKLB | 72.03 | 69.70 | +3.3% | 72.00 / 72.02 | ~3 bp | watch; Neutron still the gate |
| EVGO | 1.32 | 1.33 | −0.8% | 1.32 / 1.33 | ~76 bp | watch, low, show-me |
| CCJ | 87.85 | 86.88 | +1.1% | 87.86 / 87.92 | ~7 bp | watch, exited, pin blocks |
| BWXT | 139.40 | 138.01 | +1.0% | 139.27 / 139.61 | ~24 bp | watch, behind CCJ, pin blocks |
| HYPD | 3.80 | 3.99 | −4.8% | 3.78 / 3.81 | ~79 bp | watch, low, non-BTC |

Spot **BTC-USD** mark **$84,259** vs prior close **$83,267** (+1.2%) at 12:35 ET. No position. The equity ticker BTC at $37.27 is not spot bitcoin and is not a substitute. Crypto buying power is the same $0.03. Coinbase / self-custody remains the coin path.

RH last distribution prints (frequency labeled "other" — not annualized here): STRC **$0.50**, payable 2026-09-30, ex-date 2026-09-15 (already passed). SATA **$0.0516**, payable 2026-09-30, ex-date 2026-09-29. Morning digest: SATA characterized as a 13% daily dividend (Strive); Strategy board approved daily accrual for STRC/STRF/STRK/STRD with a shareholder vote Oct 28 and STRC daily record from Nov 1, rates unchanged. Both preferreds trade at par. That is the yield-vs-cash case. It is not a ticket today.

## Research / rotate

**How new capital best serves the themes now.** There is no deployable capital, so nothing is bought. A hypothetical new dollar is not "add to TSLA and SPCX because those are the names we hold." The book is 100% stocks because of the 2026-09-22 exit, not because those two names won a relative-value contest against an empty complex. The next dollar goes to the **BTC complex** until that sleeve is inside the 40% band (~$216 to get there without selling). Inside that dollar: **STRC and SATA together first** (5 bp and 1 bp, near par, no liquidity rebuttal, MSTR does not cover them because MSTR is unheld and sits behind the preferreds through the MSCI window). Then a **diversified miner set** (MARA, RIOT, CLSK, WULF, IREN). Miners are red versus spot BTC today; that is not a thesis failure and not an overlap block — there is no miner held to overlap. Do not rank the sleeve by this morning's tape. BITA is last on liquidity (43 bp, 4.4k shares today), not instead of the preferreds. ASST common stays behind its own preferred SATA. Stock dollars, only after the complex is being rebuilt or the Chairman designates a stocks deposit, stay 50/50 TSLA/SPCX, with the next stock dollar toward TSLA while it is the light side. Do not sell SPCX to fund that.

**Chosen this pass:** TSLA (growth / energy-adjacent equity) hold. SPCX (space / growth) hold.

**Rejected with reasons:** see the decision record. Short form: $0.03 cannot fund a ticket. Pin rotate blocked (intraday SPCX strength vs TSLA weakness). Sale of the locked book to rebuild 40% blocked by the 2026-09-23 order. STRC/SATA not illiquid and not "covered by MSTR." Miners not blocked for overlap. STRK is liquid today (~7 bp, not yesterday's wide book) and still junior — no residual until STRC and SATA have seats. Ready watchlist equities stay unseated under the stocks pin. BE blocked. Gold research-only. Equity-ticker BTC is not spot. Private names not deployable.

No deep-dive refresh. Ready dives are inside 90 days (oldest public set 2026-08-04, newest HYPD 2026-09-27). This pass proposes no first buy.

## Thesis / risk / critic

Thesis: hold. The empty complex is the allocation fact; the funding path is new capital, not a sale. Risk: ok to do nothing. $0.03 is below the $1 minimum. The $4.91 pin gap is liquid and still the wrong ticket. Critic: block held-only top-up, block the pin chase, block a rules-engine 40/60 rebuild by sale. STRC/SATA under-allocation is real and is not a buy this pass, because no BTC-complex dollar is being deployed. Executor: no orders.
