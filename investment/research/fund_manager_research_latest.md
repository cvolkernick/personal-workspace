# Fund manager research — 2026-09-23 (~11:31 ET, mid-session HOLD)

**As of:** 2026-09-23 ~15:31Z (~11:31 ET). Regular hours, mid-session.  
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (~$0.09, no equity positions) and not traded.  
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads snapshots, cannot create buying power, and cannot override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest. It closes the 15:30:16Z rules re-fire.  
**Trigger:** Rules re-fire at 15:30:16Z (`BTC-complex 0% / stocks 100%` vs numeric 40/60). Prior team HOLD at 15:21Z blocked a $1.17 pin gap. This pass remeasures that gap.  
**Live NAV:** **$338.63**. Broker equity **$338.61**. Quote equity **$338.61**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none (confirmed and queued empty).  
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~15:31Z | TSLA **$170.55** (50.37%) / SPCX **$168.06** (49.63%) of **$338.61** |
| 50/50 gap | **$1.24** one-way ($0.24 over the $1 minimum; +$0.07 vs the 15:21Z HOLD) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~15:31Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 380.355 | 378.90 | +0.38% | 380.34 / 380.42 | 0.08 |
| SPCX | 152.16 | 154.72 | −1.65% | 152.16 / 152.18 | 0.02 |
| STRC | 98.80 | 99.06 | −0.26% | 98.76 / 98.82 | **0.06** |
| SATA | 100.00 | 100.01 | −0.01% | 99.99 / 100.00 | **0.01** |
| MSTR | 164.67 | 167.33 | −1.59% | 164.63 / 164.72 | 0.09 |
| BITA | 63.42 (print 14:48Z) | 64.46 | −1.61% | 63.48 / 63.58 | 0.10 |
| ASST | 28.92 | 29.35 | −1.47% | 28.90 / 28.93 | 0.03 |
| MARA | 13.43 | 13.63 | −1.47% | 13.42 / 13.43 | 0.01 |
| RIOT | 24.905 | 24.93 | −0.10% | 24.90 / 24.91 | 0.01 |
| CLSK | 14.89 | 15.14 | −1.65% | 14.88 / 14.89 | 0.01 |
| WULF | 17.15 | 17.33 | −1.04% | 17.14 / 17.15 | 0.01 |
| IREN | 48.57 | 48.55 | +0.04% | 48.57 / 48.58 | 0.01 |
| STRK | 73.725 | 74.61 | −1.19% | 73.50 / 75.00 | **1.50** |
| GOOGL | 340.67 | 351.16 | −2.99% | 340.66 / 340.70 | 0.04 |
| NVDA | 225.38 | 228.87 | −1.52% | 225.37 / 225.39 | 0.02 |
| AAPL | 336.36 | 339.75 | −1.00% | 336.34 / 336.36 | 0.02 |
| PLTR | 191.23 | 184.99 | +3.37% | 191.23 / 191.27 | 0.04 |
| AMZN | 250.29 | 254.98 | −1.84% | 250.28 / 250.31 | 0.03 |
| RKLB | 71.92 | 71.98 | −0.08% | 71.91 / 71.93 | 0.02 |
| CCJ | 92.685 | 94.59 | −2.01% | 92.63 / 92.72 | 0.09 |
| BWXT | 144.21 | 144.51 | −0.21% | 144.15 / 144.29 | 0.14 |
| EVGO | 1.41 | 1.47 | −4.08% | 1.40 / 1.41 | 0.01 |
| BE | 277.36 | 276.53 | +0.30% | 276.86 / 277.47 | 0.61 |
| BTC-USD | 84,428.83 | 86,706.52 | −2.63% | tight | — |

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold research-only (not quoted; no owner buy call). Private names are not in the deploy set; this morning's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.4% today; GOOGL is about −3.0%. None of these is a Chairman override.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$135) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order.

The only mechanically legal ticket is a $1.24 TSLA→SPCX pin chase (sell ~0.0033 TSLA, buy ~0.0082 SPCX). That is 37 basis points of drift, $0.24 over the dust floor, and it exists because SPCX is −1.65% into tomorrow's lockup while TSLA is +0.38% into tomorrow's Semi event. Spreads are not the cost (about 2 bps and 1 bp). The trade is the wrong side of two named catalysts and would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

If the Chairman later reopens the 40% sleeve, the first dollars are **STRC and/or SATA** (spreads $0.06 and $0.01 — not an illiquidity skip, and not "MSTR already covers credit"; MSTR is −1.59% today vs STRC −0.26% and SATA flat). Then a **diversified miner set** (MARA, IREN, CLSK, RIOT, WULF — overlap is not a veto). Stocks deposits stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes. Do not reseat GOOGL, NVDA, CCJ, BWXT, or BE under the current pin.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $1.24. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread $0.06 (6 bps). Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread $0.01. Same capital and standing-order block. Not a cash proxy. |
| MSTR | Sold 2026-09-22. −1.59% today. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread $0.10; last print 14:48Z. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal is caution, not the block. IREN is the relative-strength miner today (+0.04%) and still not a ticket. |
| BTC | Crypto sleeve stays $0. Mark $84,429 is −2.63% vs prior close. Not a ticket. |
| STRK | Ready, junior to STRC. Spread $1.50 vs STRC $0.06. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.4% and GOOGL −3.0% do not reopen it. |
| RKLB | Ready space optionality. SPCX is the space core. Stocks pin. Flat on the day. |
| CCJ, BWXT | No other equities under the pin. CCJ −2.0% is digest context, not a reseat. |
| EVGO | Ready, low-priority show-me. −4.1% does not create a seat. Stocks pin. |
| BE | Blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM (gold) | Research-only until the owner calls a buy. Below bitcoin. Not quoted. |
| TSLA→SPCX $1.24 | Clears the $1 floor by $0.24. Critic blocks it: 37 bp of noise, and it sells the Semi-event name to buy the lockup name. |

## 3) Votes

- **Scout:** Book is the two pins. Nothing to deploy. Gap $1.24, up $0.07 from the 15:21Z HOLD. Closes the 15:30Z rules re-fire.
- **Thesis:** Hold. The 0/100 mix is the Chairman book, not an unpaid 40/60 gap. Do not chase the pin with $1.24.
- **Risk:** OK to hold. The gap clears $1, but a two-leg market round trip is not required. Concentration is the intended two-name book. STRC/SATA liquidity is not the block.
- **Critic:** Block the $1.24 rotate and block any BTC-complex rebuild. Empty STRC/SATA is a real gap versus the yield bias; the accepted rebuttal is the standing exit plus $0.02 buying power, not liquidity, not miner overlap, not "we already own MSTR."
- **Executor:** No orders.
