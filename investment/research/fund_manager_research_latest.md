# Fund manager research — 2026-09-29 (~10:05 ET, mid-session HOLD)

**As of:** 2026-09-29 ~14:05Z (~10:05 ET). Regular hours. Thirty-five minutes after the open (avoid window ended 10:00 ET). Not the close. This pass closes the 14:01Z rules re-fire (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60 ±5%).
**Account:** agentic ••••1752 only. Primary margin was read (equity $0, cash $0.09) and was not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus the 2026-09-29 digest, corrected against the live book (that digest still labels exited names as held).
**Live NAV:** broker **$319.04** (equity **$319.01**, cash **$0.03**, buying power **$0.03**). Quote equity at ~14:03Z **$319.00**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none (confirmed and queued empty).
**Decision:** **HOLD.** No orders.

## Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Quote equity ~14:03Z | TSLA **$158.22** (49.60%) / SPCX **$160.78** (50.40%) |
| 50/50 gap | **$1.28** one-way (40 bp of NAV; 1% band is ~$3.19) |
| Day | TSLA **−1.28%** (352.87 vs 357.45). SPCX **+0.07%** (145.57 vs 145.47) |
| Books | TSLA 2.0 bp (352.86 / 352.93). SPCX 1.4 bp (145.54 / 145.56) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |
| Cash | **$0.03** unallocated dust, below the $1 minimum |

Chairman standing order in `investment/consider_share.json` (2026-09-22), restated in `fund_manager.json` `targets.symbol_targets`: stocks sleeve is **50% SPCX / 50% TSLA** by market value. No other equity. The filled 17:47Z exit that day sold the BTC-complex equities as well (STRC, SATA, MSTR, BITA, miners). Crypto stays $0. `bias_weight_staged.json` pending queue is empty. New capital, if it arrives, is the only path back toward 40/60; this pass does not sell the locked book to rebuild it.

Held marks are ~14:03Z. Daily change uses adjusted previous close. BITA's last trade is the prior close; its live book is the bid/ask below.

| Symbol | Last | Prior | Day | Bid / ask | Spread | Role |
|--------|------|-------|-----|-----------|--------|------|
| TSLA | 352.87 | 357.45 | −1.28% | 352.86 / 352.93 | 2.0 bp | held, pin 50 |
| SPCX | 145.57 | 145.47 | +0.07% | 145.54 / 145.56 | 1.4 bp | held, pin 50 |
| STRC | 99.21 | 99.10 | +0.11% | 99.18 / 99.23 | 5.0 bp | unheld preferred core |
| SATA | 99.99 | 99.96 | +0.03% | 99.99 / 100.00 | 1.0 bp | unheld preferred core |
| MSTR | 156.22 | 157.14 | −0.59% | 156.25 / 156.30 | 3.2 bp | unheld credit equity |
| BITA | 62.89 (stale last) | 62.89 | — | 63.19 / 63.36 | 26.9 bp | unheld |
| ASST | 29.08 | 29.00 | +0.28% | 29.05 / 29.11 | 20.6 bp | unheld |
| MARA | 12.10 | 12.11 | −0.09% | 12.08 / 12.09 | 8.3 bp | unheld miner |
| RIOT | 21.67 | 21.62 | +0.21% | 21.66 / 21.67 | 4.6 bp | unheld miner |
| CLSK | 13.32 | 13.34 | −0.15% | 13.32 / 13.33 | 7.5 bp | unheld miner |
| WULF | 14.98 | 15.12 | −0.96% | 14.97 / 14.98 | 6.7 bp | unheld miner |
| IREN | 41.35 | 41.72 | −0.89% | 41.35 / 41.36 | 2.4 bp | unheld miner |
| GOOGL | 339.79 | 342.75 | −0.86% | 339.76 / 339.82 | 1.8 bp | watch, exited |
| AAPL | 333.06 | 338.40 | −1.58% | 333.03 / 333.10 | 2.1 bp | watch |
| NVDA | 229.98 | 228.86 | +0.49% | 229.93 / 229.95 | 0.9 bp | watch, exited |
| PLTR | 186.65 | 187.48 | −0.44% | 186.57 / 186.67 | 5.4 bp | watch |
| AMZN | 245.24 | 246.15 | −0.37% | 245.21 / 245.24 | 1.2 bp | watch |
| EVGO | 1.355 | 1.34 | +1.12% | 1.35 / 1.36 | 73.8 bp | watch, low |
| RKLB | 71.37 | 72.19 | −1.14% | 71.34 / 71.39 | 7.0 bp | watch |
| STRK | 75.02 | 75.15 | −0.17% | 75.00 / 76.00 | 133 bp | watch preferred |
| CCJ | 87.61 | 87.04 | +0.65% | 87.53 / 87.68 | 17.1 bp | watch, exited |
| BWXT | 138.94 | 134.35 | +3.41% | 138.84 / 139.11 | 19.4 bp | watch |
| HYPD | 3.93 | 3.915 | +0.38% | 3.93 / 3.96 | 76.3 bp | watch, low |
| GLDM | 82.36 | 81.56 | +0.98% | 82.35 / 82.36 | 1.2 bp | research-only gold |

Spot **BTC-USD** mark **$83,986.72** vs prior close **$83,047.23** (+1.13%) at 10:03 ET. No position. Not an agentic equity ticket; the coin path in the thesis is Coinbase / self-custody, and crypto buying power is the same $0.03.

## Research / rotate

**How new capital best serves the themes now:** it does not, because there is no deployable capital. A hypothetical new dollar is not "add to TSLA and SPCX because those are the names we hold." Stock dollars stay on the 50/50 pin. BTC-complex dollars, which this book does not have, would open with **STRC and SATA** (5.0 bp and 1.0 bp, near par, company STRC repurchase still in the digest), then a diversified miner set. That order is relative value, not path-dependence. The book is two names because the Chairman exited everything else on 2026-09-22.

**Chosen:** TSLA (growth equity) and SPCX (space), both hold.

**Rejected with reasons:** see the decision record. Short form: pin rotate blocked ($1.28, 40 bp of NAV, day before the NHTSA Cybercab response). STRC/SATA not illiquid and not covered by MSTR. Miners not blocked for overlap. Watchlist ready names, including BWXT +3.41% and NVDA +0.49%, not seated under the pin. STRK's 133 bp book is a real liquidity rebuttal and it is junior to STRC. Gold research-only. BE blocked. Private names not deployable.

No deep-dive refresh. Ready dives are inside 90 days (oldest 2026-08-04, newest HYPD 2026-09-27). This pass proposes no first buy.

## Thesis / risk / critic

Thesis: hold. Risk: ok to do nothing; the $1.28 rotate is liquid and still the wrong ticket, and $0.03 cannot fund a sleeve. Critic: block the pin chase and block a rules-engine 40/60 rebuild by sale. STRC/SATA under-allocation is real versus the numeric 40% target and is not a buy this pass, because no BTC-complex dollar is being deployed. Executor: no orders.

## Do not trade

- Do not sell SPCX to buy TSLA today.
- Do not reopen the BTC sleeve by selling the locked book.
- Do not seat GOOGL, AAPL, NVDA, PLTR, AMZN, RKLB, CCJ, BWXT, EVGO, STRK, or HYPD.
- Do not buy BE.
- Do not buy gold until the owner calls it.
- Do not buy spot BTC on this account with dust buying power.
