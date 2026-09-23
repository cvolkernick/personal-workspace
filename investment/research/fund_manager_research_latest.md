# Fund manager research — 2026-09-23 (~10:46 ET, mid-session HOLD)

**As of:** 2026-09-23 ~14:46Z (~10:46 ET). Regular hours, mid-session (after the open, before the close).  
**Account:** agentic ••••1752 only (`674601752`). Primary margin was not traded.  
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it cannot create buying power or override the Chairman 2026-09-22 standing order. Today's digest plus live quotes cover the theme scan.  
**Trigger:** Rules re-fire at 14:45Z (`BTC-complex 0% / stocks 100%` vs numeric 40/60). A prior team HOLD at 14:34Z is the same book on earlier marks (one-way gap $0.82). This pass closes the 14:45Z row.  
**Live NAV:** **$338.76**. Equity **$338.74**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none (confirmed and queued empty).  
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0. Spot BTC-USD mark **$84,258.92** (prior close $86,699.43, about −2.8%). Not a position |
| Quote equity ~14:46Z | TSLA **$170.00** (50.19%) / SPCX **$168.69** (49.81%) of **$338.69** |
| 50/50 gap | **$0.66** one-way, under the $1 minimum |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22 17:47Z. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~14:46Z)

| Symbol | Last | Prior close | Bid / ask | Spread |
|--------|------|-------------|-----------|--------|
| TSLA | 379.15 | 378.90 | 379.16 / 379.23 | 0.07 |
| SPCX | 152.73 | 154.72 | 152.72 / 152.74 | 0.02 |
| STRC | 98.83 | 99.06 | 98.83 / 98.87 | **0.04** |
| SATA | 99.99 | 100.01 | 99.98 / 99.99 | **0.01** |
| MSTR | 164.08 | 167.33 | 163.96 / 164.07 | 0.11 |
| BITA | 63.46 | 64.46 | 63.45 / 63.49 | 0.04 |
| ASST | 28.74 | 29.35 | 28.70 / 28.73 | 0.03 |
| MARA | 13.53 | 13.63 | 13.53 / 13.54 | 0.01 |
| RIOT | 25.09 | 24.93 | 25.08 / 25.09 | 0.01 |
| CLSK | 14.97 | 15.14 | 14.97 / 14.98 | 0.01 |
| WULF | 17.11 | 17.33 | 17.09 / 17.10 | 0.01 |
| IREN | 48.68 | 48.55 | 48.66 / 48.68 | 0.02 |
| STRK | 73.49 | 74.61 | 73.01 / 73.83 | **0.82** |
| GOOGL | 341.95 | 351.16 | 342.05 / 342.07 | 0.02 |
| NVDA | 226.02 | 228.87 | 226.03 / 226.05 | 0.02 |
| AAPL | 337.72 | 339.75 | 337.71 / 337.72 | 0.01 |
| PLTR | 190.29 | 184.99 | 190.24 / 190.33 | 0.09 |
| AMZN | 249.26 | 254.98 | 249.26 / 249.29 | 0.03 |
| RKLB | 71.35 | 71.98 | 71.31 / 71.34 | 0.03 |
| CCJ | 92.55 | 94.59 | 92.49 / 92.55 | 0.06 |
| BWXT | 144.07 | 144.51 | 144.07 / 144.35 | 0.28 |
| EVGO | 1.41 | 1.47 | 1.41 / 1.42 | 0.01 |
| BE | 276.59 | 276.53 | 276.21 / 276.81 | 0.60 |

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold research-only. Private names not deployable. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. None of these is a Chairman override.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$135) into STRC/SATA plus miners would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order.

If the Chairman later reopens the 40% sleeve, the first dollars are **STRC and/or SATA** (spreads $0.04 and $0.01 — not an illiquidity skip, and not "MSTR already covers credit"; MSTR is −1.9% today vs STRC −0.2%). Then a **diversified miner set** (MARA, IREN, CLSK, RIOT, WULF — overlap is not a veto). Stocks deposits stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes. Do not reseat GOOGL, NVDA, CCJ, BWXT, or BE under the current pin.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a trim. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread $0.04. Not skipped for liquidity or because MSTR covers credit. No buying power. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread $0.01. Same capital and standing-order block. Not a cash proxy. |
| MSTR | Sold 2026-09-22. −1.9% today. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal is caution, not the block. |
| BTC | Crypto sleeve stays $0. Spot mark $84,259 is context, not a ticket. |
| STRK | Ready, junior to STRC. Spread $0.82 vs STRC $0.04. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. GOOGL −2.6% and PLTR +2.9% do not reopen the pin. |
| RKLB | Ready space optionality. SPCX is the space core. Stocks pin. |
| CCJ, BWXT | Sold 2026-09-22. No other equities under the pin. |
| EVGO | Ready, low-priority show-me. Stocks pin. |
| BE | Blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM (gold) | Research-only until the owner calls a buy. Below bitcoin. |
| $1 round-up of the $0.66 gap | Would overshoot the pin and sell TSLA into the Semi event while buying SPCX into the Sep 24 unlock. |

## 3) Votes

- **Scout:** Book is the two pins. Nothing to deploy.
- **Thesis:** Hold. The 0/100 mix is the Chairman book, not an unpaid 40/60 gap.
- **Risk:** No trade. Buying power and the pin gap are both under $1. Do not sell yesterday's fills.
- **Critic:** Block a reversal. Empty STRC/SATA is a real gap versus the yield bias; the only accepted rebuttal is the standing exit plus $0.02 buying power.
- **Executor:** No orders.
