# Fund manager research — 2026-09-23 (~13:01 ET, mid-session HOLD)

**As of:** 2026-09-23 ~17:01Z (~13:01 ET). Regular hours, mid-session (not the open or the close).
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value $0.09, cash $0.09, crypto dust $0.004, no equity positions) and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$336.88**. Broker equity **$336.86**. Quote equity **$336.86**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~17:01Z | TSLA **$169.70** (50.38%) / SPCX **$167.16** (49.62%) of **$336.86** |
| 50/50 gap | **$1.27** one-way ($0.27 over the $1 minimum; −$0.08 vs the 16:46Z HOLD) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~17:01Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 378.48 | 378.90 | −0.11% | 378.44 / 378.47 | 0.03 |
| SPCX | 151.345 | 154.72 | −2.18% | 151.34 / 151.37 | 0.03 |
| STRC | 98.81 | 99.06 | −0.25% | 98.78 / 98.82 | **0.04** (4 bp) |
| SATA | 100.01 | 100.01 (adj 99.9584) | flat / +0.05% vs adj | 100.00 / 100.01 | **0.01** (1 bp) |
| MSTR | 163.45 | 167.33 | −2.32% | 163.41 / 163.46 | 0.05 |
| BITA | 63.382 (print 16:15Z) | 64.46 | −1.67% | 63.09 / 63.34 | 0.25 |
| ASST | 28.905 | 29.35 | −1.52% | 28.91 / 28.93 | 0.02 |
| MARA | 13.445 | 13.63 | −1.36% | 13.45 / 13.46 | 0.01 |
| RIOT | 24.93 | 24.93 | 0.00% | 24.91 / 24.92 | 0.01 |
| CLSK | 14.68 | 15.14 | −3.04% | 14.68 / 14.70 | 0.02 |
| WULF | 16.825 | 17.33 | −2.91% | 16.82 / 16.83 | 0.01 |
| IREN | 47.84 | 48.55 | −1.46% | 47.83 / 47.84 | 0.01 |
| STRK | 73.76 | 74.61 | −1.14% | 73.25 / 73.75 | **0.50** |
| GOOGL | 338.11 | 351.16 | −3.72% | 338.03 / 338.10 | 0.07 |
| NVDA | 224.74 | 228.87 | −1.80% | 224.74 / 224.76 | 0.02 |
| AAPL | 336.99 | 339.75 | −0.81% | 337.00 / 337.01 | 0.01 |
| PLTR | 190.735 | 184.99 | +3.11% | 190.70 / 190.75 | 0.05 |
| AMZN | 248.435 | 254.98 | −2.57% | 248.43 / 248.46 | 0.03 |
| RKLB | 70.88 | 71.98 | −1.53% | 70.87 / 70.91 | 0.04 |
| CCJ | 91.86 | 94.59 | −2.89% | 91.81 / 91.90 | 0.09 |
| BWXT | 143.68 | 144.51 | −0.57% | 143.56 / 143.79 | 0.23 |
| EVGO | 1.39 | 1.47 | −5.44% | 1.39 / 1.40 | 0.01 |
| BTC-USD | 84,099 | 86,706.52 | −3.01% | 84,095.63 / 84,102.37 | tight |

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold is research-only (not quoted; no owner buy call). Private names are not in the deploy set; today's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.1% today; GOOGL is about −3.7%. None of these is a Chairman override.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$135) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads 4 bp and 1 bp; STRC −0.25% and SATA flat vs MSTR −2.32%) — not MSTR by habit, and not because miners overlap.

The only mechanically legal ticket is a $1.27 TSLA→SPCX pin chase (sell ~0.0034 TSLA, buy ~0.0084 SPCX). That is 38 basis points of drift, $0.27 over the dust floor, and it is narrower than the 16:46Z gap of $1.35 because TSLA gave back a few cents. Spreads are not the cost (about 1 bp each). The trade is the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −3.01% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $1.27. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread 4 bp. Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread 1 bp. Same capital and standing-order block. Not a cash proxy. Flat on the official close. |
| MSTR | Sold 2026-09-22. −2.32% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread 39 bp; last print 16:15Z. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal is caution, not the block. RIOT (flat) is the least-weak miner today and still not a ticket. |
| BTC | Crypto sleeve stays $0. Mark $84,099 is −3.01% vs prior close. Not a ticket. |
| STRK | Ready, junior to STRC. Spread $0.50 vs STRC $0.04. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.1% and GOOGL −3.7% do not reopen it. |
| RKLB | Ready space optionality. SPCX is the space core. Stocks pin. Mild contract news is not a new-money pin. |
| CCJ, BWXT | No other equities under the pin. CCJ −2.9% is digest context, not a reseat. |
| EVGO | Ready, low-priority show-me. −5.4% does not create a seat. Stocks pin. |
| BE | Blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM (gold) | Research-only until the owner calls a buy. Below bitcoin. Not quoted. |
| TSLA→SPCX $1.27 | Clears the $1 floor by $0.27. Critic blocks it: 38 bp of noise, and it sells the Semi-event name to buy the lockup name. Narrower than the $1.35 gap already blocked at 16:46Z. |

## 3) Votes

- **Scout:** Book is the two pins. Nothing to deploy. Gap $1.27, down $0.08 from the 16:46Z HOLD.
- **Thesis:** Hold. The 0/100 mix is the Chairman book, not an unpaid 40/60 gap. Do not chase the pin with $1.27. If the 40% sleeve is reopened later, first dollars are STRC and/or SATA, then a diversified miner set.
- **Risk:** OK to hold. The gap clears $1, but a two-leg market round trip is not required. Concentration is the intended two-name book. STRC/SATA liquidity is not the block (spreads 4 bp / 1 bp).
- **Critic:** Block the $1.27 rotate and block any BTC-complex rebuild. Empty STRC/SATA is a real gap versus the yield bias; the accepted rebuttal is the standing exit plus $0.02 buying power, not liquidity, not miner overlap, and not "we already own MSTR." Do not buy PLTR on today's strength.
- **Executor:** No orders.
