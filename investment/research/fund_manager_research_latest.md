# Fund manager research — 2026-09-23 (~12:46 ET, mid-session HOLD)

**As of:** 2026-09-23 ~16:46Z (~12:46 ET). Regular hours, mid-session (not the open or the close).  
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value ~$0.09, cash $0.09, no equity positions) and not traded.  
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.  
**Live NAV:** **$337.68**. Broker equity **$337.66**. Quote equity **$337.54**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.  
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~16:46Z | TSLA **$170.12** (50.40%) / SPCX **$167.42** (49.60%) of **$337.54** |
| 50/50 gap | **$1.35** one-way ($0.35 over the $1 minimum; +$0.08 vs the 15:46Z HOLD) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~16:46Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 379.41 | 378.90 | +0.13% | 379.40 / 379.44 | 0.04 |
| SPCX | 151.58 | 154.72 | −2.03% | 151.57 / 151.59 | 0.02 |
| STRC | 98.80 | 99.06 | −0.26% | 98.79 / 98.81 | **0.02** |
| SATA | 100.01 | 100.01 (adj 99.9584) | +0.05% vs adj | 100.00 / 100.01 | **0.01** |
| MSTR | 163.83 | 167.33 | −2.09% | 163.76 / 163.86 | 0.10 |
| BITA | 63.382 (print 16:15Z) | 64.46 | −1.67% | 63.19 / 63.37 | 0.18 |
| ASST | 29.04 | 29.35 | −1.06% | 29.05 / 29.09 | 0.04 |
| MARA | 13.44 | 13.63 | −1.39% | 13.43 / 13.44 | 0.01 |
| RIOT | 24.855 | 24.93 | −0.30% | 24.85 / 24.87 | 0.02 |
| CLSK | 14.73 | 15.14 | −2.71% | 14.73 / 14.74 | 0.01 |
| WULF | 16.92 | 17.33 | −2.37% | 16.92 / 16.93 | 0.01 |
| IREN | 48.09 | 48.55 | −0.95% | 48.05 / 48.07 | 0.02 |
| STRK | 73.38 (print 16:39Z) | 74.61 | −1.65% | 73.25 / 73.76 | **0.51** |
| GOOGL | 338.63 | 351.16 | −3.57% | 338.62 / 338.64 | 0.02 |
| NVDA | 224.27 | 228.87 | −2.01% | 224.27 / 224.28 | 0.01 |
| AAPL | 336.57 | 339.75 | −0.94% | 336.56 / 336.59 | 0.03 |
| PLTR | 190.51 | 184.99 | +2.98% | 190.50 / 190.56 | 0.06 |
| AMZN | 248.76 | 254.98 | −2.44% | 248.75 / 248.77 | 0.02 |
| RKLB | 71.24 | 71.98 | −1.03% | 71.24 / 71.27 | 0.03 |
| CCJ | 92.10 | 94.59 | −2.63% | 92.06 / 92.17 | 0.11 |
| BWXT | 143.605 | 144.51 | −0.63% | 143.47 / 143.70 | 0.23 |
| EVGO | 1.39 | 1.47 | −5.44% | 1.39 / 1.40 | 0.01 |
| BTC-USD | 84,049.29 | 86,699.43 | −3.06% | 84,049.28 / 84,049.29 | tight |

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold is research-only (not quoted; no owner buy call). Private names are not in the deploy set; today's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.0% today; GOOGL is about −3.6%. None of these is a Chairman override.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$135) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads $0.02 and $0.01; STRC −0.26% and SATA flat vs MSTR −2.09%) — not MSTR by habit, and not because miners overlap.

The only mechanically legal ticket is a $1.35 TSLA→SPCX pin chase (sell ~0.0036 TSLA, buy ~0.0089 SPCX). That is 40 basis points of drift, $0.35 over the dust floor, and it widened because SPCX is −2.03% into tomorrow's lockup while TSLA is +0.13% into tomorrow's Semi event. Spreads are not the cost (about 1 bp each). The trade is the wrong side of two named catalysts and would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −3.06% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $1.35. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread $0.02 (2 bp). Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread $0.01. Same capital and standing-order block. Not a cash proxy. Flat on the day. |
| MSTR | Sold 2026-09-22. −2.09% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread $0.18; last print 16:15Z. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal is caution, not the block. RIOT (−0.30%) is the least-weak miner today and still not a ticket. |
| BTC | Crypto sleeve stays $0. Mark $84,049 is −3.06% vs prior close. Not a ticket. |
| STRK | Ready, junior to STRC. Spread $0.51 vs STRC $0.02. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.0% and GOOGL −3.6% do not reopen it. |
| RKLB | Ready space optionality. SPCX is the space core. Stocks pin. Mild contract news is not a new-money pin. |
| CCJ, BWXT | No other equities under the pin. CCJ −2.6% is digest context, not a reseat. |
| EVGO | Ready, low-priority show-me. −5.4% does not create a seat. Stocks pin. |
| BE | Blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM (gold) | Research-only until the owner calls a buy. Below bitcoin. Not quoted. |
| TSLA→SPCX $1.35 | Clears the $1 floor by $0.35. Critic blocks it: 40 bp of noise, and it sells the Semi-event name to buy the lockup name. |

## 3) Votes

- **Scout:** Book is the two pins. Nothing to deploy. Gap $1.35, up $0.08 from the 15:46Z HOLD.
- **Thesis:** Hold. The 0/100 mix is the Chairman book, not an unpaid 40/60 gap. Do not chase the pin with $1.35. If the 40% sleeve is reopened later, first dollars are STRC and/or SATA, then a diversified miner set.
- **Risk:** OK to hold. The gap clears $1, but a two-leg market round trip is not required. Concentration is the intended two-name book. STRC/SATA liquidity is not the block (spreads $0.02 / $0.01).
- **Critic:** Block the $1.35 rotate and block any BTC-complex rebuild. Empty STRC/SATA is a real gap versus the yield bias; the accepted rebuttal is the standing exit plus $0.02 buying power, not liquidity, not miner overlap, and not "we already own MSTR." Do not buy PLTR on today's strength.
- **Executor:** No orders.
