# Fund manager research — 2026-09-23 (~11:02 ET, mid-session HOLD)

**As of:** 2026-09-23 ~15:02Z (~11:02 ET). Regular hours, mid-session.  
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read and not traded.  
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it cannot create buying power or override the Chairman 2026-09-22 standing order. Today's digest plus live quotes are the theme scan. This pass closes the 15:00Z rules re-fire.  
**Trigger:** Rules re-fire at 15:00:52Z (`BTC-complex 0% / stocks 100%` vs numeric 40/60). Prior team HOLD at 14:49Z was the same book on earlier marks (one-way gap $0.66).  
**Live NAV:** **$339.55**. Broker equity **$339.53**. Quote equity **$339.52**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none (confirmed and queued empty).  
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~15:02Z | TSLA **$170.65** (50.26%) / SPCX **$168.87** (49.74%) of **$339.52** |
| 50/50 gap | **$0.89** one-way, under the $1 minimum |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~15:02Z)

| Symbol | Last | Prior close | Bid / ask | Spread |
|--------|------|-------------|-----------|--------|
| TSLA | 380.59 | 378.90 | 380.60 / 380.68 | 0.08 |
| SPCX | 152.89 | 154.72 | 152.87 / 152.89 | 0.02 |
| STRC | 98.75 | 99.06 | 98.76 / 98.87 | **0.11** |
| SATA | 100.00 | 100.01 | 99.99 / 100.00 | **0.01** |
| MSTR | 164.85 | 167.33 | 164.80 / 164.91 | 0.11 |
| BITA | 63.42 | 64.46 | 63.43 / 63.64 | 0.21 |
| ASST | 28.71 | 29.35 | 28.69 / 28.73 | 0.04 |
| MARA | 13.56 | 13.63 | 13.55 / 13.56 | 0.01 |
| RIOT | 25.19 | 24.93 | 25.19 / 25.20 | 0.01 |
| CLSK | 14.99 | 15.14 | 14.98 / 14.99 | 0.01 |
| WULF | 17.30 | 17.33 | 17.29 / 17.30 | 0.01 |
| IREN | 49.35 | 48.55 | 49.35 / 49.36 | 0.01 |
| STRK | 73.52 | 74.61 | 73.01 / 74.03 | **1.02** |
| GOOGL | 342.03 | 351.16 | 342.02 / 342.05 | 0.03 |
| NVDA | 226.24 | 228.87 | 226.23 / 226.25 | 0.02 |
| AAPL | 338.09 | 339.75 | 338.08 / 338.11 | 0.03 |
| PLTR | 192.90 | 184.99 | 192.85 / 192.93 | 0.08 |
| AMZN | 250.52 | 254.98 | 250.49 / 250.54 | 0.05 |
| RKLB | 71.64 | 71.98 | 71.62 / 71.65 | 0.03 |
| CCJ | 92.64 | 94.59 | 92.55 / 92.64 | 0.09 |
| BWXT | 144.29 | 144.51 | 144.23 / 144.47 | 0.24 |
| EVGO | 1.415 | 1.47 | 1.41 / 1.42 | 0.01 |
| BE | 276.70 | 276.53 | 276.42 / 277.26 | 0.84 |

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold research-only. Private names not deployable. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +4.3% today. None of these is a Chairman override.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$136) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. The $0.89 one-way drift inside the 50/50 pin is below the $1 minimum and would sell TSLA into tomorrow's Semi event while buying SPCX into tomorrow's unlock.

If the Chairman later reopens the 40% sleeve, the first dollars are **STRC and/or SATA** (spreads $0.11 and $0.01 — not an illiquidity skip, and not "MSTR already covers credit"; MSTR is about −1.5% today vs STRC about −0.3%). Then a **diversified miner set** (MARA, IREN, CLSK, RIOT, WULF — overlap is not a veto). Stocks deposits stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes. Do not reseat GOOGL, NVDA, CCJ, BWXT, or BE under the current pin.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a trim. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread $0.11. Not skipped for liquidity or because MSTR covers credit. No buying power. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread $0.01. Same capital and standing-order block. Not a cash proxy. |
| MSTR | Sold 2026-09-22. About −1.5% today. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Wider spread ($0.21). No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal is caution, not the block. |
| BTC | Crypto sleeve stays $0. Not a ticket. |
| STRK | Ready, junior to STRC. Spread $1.02 vs STRC $0.11. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR's +4% day does not reopen the pin. |
| RKLB | Ready space optionality. SPCX is the space core. Stocks pin. |
| CCJ, BWXT | Sold 2026-09-22. No other equities under the pin. |
| EVGO | Ready, low-priority show-me. Stocks pin. |
| BE | Blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM (gold) | Research-only until the owner calls a buy. Below bitcoin. |
| $1 round-up of the $0.89 gap | Would overshoot the pin and sell TSLA into the Semi event while buying SPCX into the Sep 24 unlock. |

## 3) Votes

- **Scout:** Book is the two pins. Nothing to deploy. Gap $0.89.
- **Thesis:** Hold. The 0/100 mix is the Chairman book, not an unpaid 40/60 gap.
- **Risk:** No trade. Buying power and the pin gap are both under $1. Do not sell yesterday's fills.
- **Critic:** Block a reversal. Empty STRC/SATA is a real gap versus the yield bias; the only accepted rebuttal is the standing exit plus $0.02 buying power. Not liquidity, not miner overlap, not "we already own MSTR."
- **Executor:** No orders.
