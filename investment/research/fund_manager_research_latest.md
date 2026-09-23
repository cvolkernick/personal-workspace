# Fund manager research — 2026-09-23 (~11:50 ET, mid-session HOLD)

**As of:** 2026-09-23 ~15:46Z (~11:46 ET). Regular hours, mid-session (not the open or the close).  
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value ~$0.09, cash $0.09, no equity positions) and not traded.  
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.  
**Live NAV:** **$339.07**. Broker equity **$339.05**. Quote equity **$339.05**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.  
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~15:46Z | TSLA **$170.79** (50.37%) / SPCX **$168.26** (49.63%) of **$339.05** |
| 50/50 gap | **$1.27** one-way ($0.27 over the $1 minimum; +$0.03 vs the 15:31Z HOLD) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~15:46Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 380.90 | 378.90 | +0.53% | 380.76 / 380.79 | 0.03 |
| SPCX | 152.34 | 154.72 | −1.54% | 152.32 / 152.35 | 0.03 |
| STRC | 98.805 | 99.06 | −0.26% | 98.81 / 98.82 | **0.01** |
| SATA | 100.01 | 100.01 | flat (+0.05% vs adjusted 99.9584) | 100.00 / 100.01 | **0.01** |
| MSTR | 164.11 | 167.33 | −1.92% | 164.06 / 164.13 | 0.07 |
| BITA | 63.48 (print 15:32Z) | 64.46 | −1.52% | 63.25 / 63.50 | 0.25 |
| ASST | 28.905 | 29.35 | −1.52% | 28.90 / 28.92 | 0.02 |
| MARA | 13.475 | 13.63 | −1.14% | 13.47 / 13.48 | 0.01 |
| RIOT | 24.975 | 24.93 | +0.18% | 24.97 / 24.98 | 0.01 |
| CLSK | 14.935 | 15.14 | −1.35% | 14.93 / 14.94 | 0.01 |
| WULF | 17.185 | 17.33 | −0.84% | 17.18 / 17.19 | 0.01 |
| IREN | 48.68 | 48.55 | +0.27% | 48.69 / 48.70 | 0.01 |
| STRK | 74.02 | 74.61 | −0.79% | 73.25 / 74.02 | **0.77** |
| GOOGL | 340.955 | 351.16 | −2.91% | 340.94 / 340.97 | 0.03 |
| NVDA | 225.435 | 228.87 | −1.50% | 225.43 / 225.44 | 0.01 |
| AAPL | 336.425 | 339.75 | −0.98% | 336.41 / 336.43 | 0.02 |
| PLTR | 192.15 | 184.99 | +3.87% | 192.11 / 192.20 | 0.09 |
| AMZN | 250.03 | 254.98 | −1.94% | 250.02 / 250.04 | 0.02 |
| RKLB | 71.82 | 71.98 | −0.22% | 71.81 / 71.83 | 0.02 |
| CCJ | 92.59 | 94.59 | −2.11% | 92.56 / 92.62 | 0.06 |
| BWXT | 143.88 | 144.51 | −0.44% | 143.96 / 144.22 | 0.26 |
| EVGO | 1.405 | 1.47 | −4.42% | 1.40 / 1.41 | 0.01 |
| BTC-USD | 84,327.50 | 86,706.52 | −2.74% | 84,327.44 / 84,327.55 | tight |

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold is research-only (not quoted; no owner buy call). Private names are not in the deploy set; this morning's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.9% today; GOOGL is about −2.9%. None of these is a Chairman override.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$136) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads $0.01 and $0.01; STRC −0.26% and SATA flat vs MSTR −1.92%) — not MSTR by habit, and not because miners overlap.

The only mechanically legal ticket is a $1.27 TSLA→SPCX pin chase (sell ~0.0033 TSLA, buy ~0.0083 SPCX). That is 37 basis points of drift, $0.27 over the dust floor, and it exists because SPCX is −1.54% into tomorrow's lockup while TSLA is +0.53% into tomorrow's Semi event. Spreads are not the cost (about 1 bp and 2 bp). The trade is the wrong side of two named catalysts and would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −2.74% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $1.27. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread $0.01 (1 bp). Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread $0.01. Same capital and standing-order block. Not a cash proxy. Flat on the day. |
| MSTR | Sold 2026-09-22. −1.92% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread $0.25; last print 15:32Z. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal is caution, not the block. IREN (+0.27%) and RIOT (+0.18%) are the relative-strength miners today and still not tickets. |
| BTC | Crypto sleeve stays $0. Mark $84,327 is −2.74% vs prior close. Not a ticket. |
| STRK | Ready, junior to STRC. Spread $0.77 vs STRC $0.01. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.9% and GOOGL −2.9% do not reopen it. |
| RKLB | Ready space optionality. SPCX is the space core. Stocks pin. Mild contract news is not a new-money pin. |
| CCJ, BWXT | No other equities under the pin. CCJ −2.1% is digest context, not a reseat. |
| EVGO | Ready, low-priority show-me. −4.4% does not create a seat. Stocks pin. |
| BE | Blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM (gold) | Research-only until the owner calls a buy. Below bitcoin. Not quoted. |
| TSLA→SPCX $1.27 | Clears the $1 floor by $0.27. Critic blocks it: 37 bp of noise, and it sells the Semi-event name to buy the lockup name. |

## 3) Votes

- **Scout:** Book is the two pins. Nothing to deploy. Gap $1.27, up $0.03 from the 15:31Z HOLD.
- **Thesis:** Hold. The 0/100 mix is the Chairman book, not an unpaid 40/60 gap. Do not chase the pin with $1.27. If the 40% sleeve is reopened later, first dollars are STRC and/or SATA, then a diversified miner set.
- **Risk:** OK to hold. The gap clears $1, but a two-leg market round trip is not required. Concentration is the intended two-name book. STRC/SATA liquidity is not the block (spreads $0.01 / $0.01).
- **Critic:** Block the $1.27 rotate and block any BTC-complex rebuild. Empty STRC/SATA is a real gap versus the yield bias; the accepted rebuttal is the standing exit plus $0.02 buying power, not liquidity, not miner overlap, and not "we already own MSTR." Do not buy PLTR on today's strength.
- **Executor:** No orders.
