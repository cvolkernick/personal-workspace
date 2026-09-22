# Fund manager research — 2026-09-22 (~15:31 ET, close-window HOLD)

**As of:** 2026-09-22 ~19:31Z (~15:31 ET). Regular hours, **inside the last 30 minutes** (cadence avoids the close).  
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read and not traded.  
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it cannot create buying power or override the same-day Chairman order.  
**Trigger:** Rules re-fire at 19:29Z (`BTC-complex 0% / stocks 100%` vs numeric 40/60).  
**Live NAV:** **$339.76**. Equity **$339.75**. Cash / buying power **$0.01 / $0.01**. Unsettled **$188.20** (already inside the 17:48Z buys; not spendable). Pending deposits **$0**.  
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh, **SPCX** 1.104498 sh |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), GOOGL, NVDA, CCJ, and every other ready name |
| Crypto | $0. Listed `BTC` equity ~$38.25 is not spot bitcoin |
| Open orders | None (queued empty). Today's 17:47Z sells and 17:48Z buys are filled |
| Quote equity ~19:31Z | TSLA **$170.01** (50.09%) / SPCX **$169.39** (49.91%) of **$339.41** |
| 50/50 gap | **$0.31**, under the $1 minimum |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Crypto and cash unchanged. Live rebalance already filled: 13 market sells at 17:47Z (MSTR, MARA, BITA, IREN, CLSK, STRC, SATA, RIOT, GOOGL, WULF, NVDA, CCJ, BWXT) and TSLA **$88.10** + SPCX **$100.12** buys at 17:48Z.

### Live marks used for relative value (~19:31Z)

| Symbol | Last | Prior close | Bid / ask | Spread |
|--------|------|-------------|-----------|--------|
| TSLA | 379.17 | 375.30 | 379.14 / 379.20 | 0.06 |
| SPCX | 153.365 | 151.85 | 153.35 / 153.38 | 0.03 |
| STRC | 99.04 | 98.78 | 99.01 / 99.05 | **0.04** |
| SATA | 100.005 | 100.01 | 100.00 / 100.01 | **0.01** |
| MSTR | 168.72 | 168.50 | 168.68 / 168.73 | 0.05 |
| BITA | 64.61 | 64.71 | 64.55 / 64.72 | 0.17 |
| ASST | 29.53 | 30.33 | 29.50 / 29.52 | 0.02 |
| MARA | 13.70 | 13.28 | 13.69 / 13.70 | 0.01 |
| RIOT | 24.565 | 24.18 | 24.56 / 24.57 | 0.01 |
| CLSK | 15.09 | 14.76 | 15.08 / 15.09 | 0.01 |
| WULF | 17.345 | 17.45 | 17.34 / 17.35 | 0.01 |
| IREN | 48.36 | 47.23 | 48.34 / 48.36 | 0.02 |
| STRK | 75.21 | 76.28 | 75.15 / 75.53 | **0.38** |
| GOOGL | 353.38 | 354.97 | tight | ~0.01 |
| NVDA | 229.58 | 227.38 | tight | 0.01 |
| AAPL | 340.30 | 338.98 | 340.28 / 340.34 | 0.06 |
| PLTR | 184.74 | 183.09 | 184.63 / 184.68 | 0.05 |
| AMZN | 255.98 | 258.45 | tight | 0.01 |
| RKLB | 71.965 | 69.89 | 71.95 / 71.98 | 0.03 |
| CCJ | 94.59 | 93.23 | 94.57 / 94.61 | 0.04 |
| BWXT | 144.91 | 147.47 | 144.86 / 144.93 | 0.07 |
| EVGO | 1.485 | 1.49 | 1.48 / 1.49 | 0.01 |
| BE | 273.16 | 272.89 | wide | 0.41 |
| BTC (equity) | 38.245 | 38.25 | 38.25 / 38.26 | 0.01 |

Digest figure “CCJ ~$70.47” does **not** match this tape (last $94.59, prior close $93.23). The quote is the book input. Either print still does not authorize a buy.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold research-only. Private names not deployable.

Ready watchlist (must be named): GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04). No first buy this pass, so no new dive. BE is `pass` / blocked.

### How capital serves themes now

There is no deployable capital. Buying power is $0.01. Unsettled $188.20 is the cash from today's sales that the 17:48Z TSLA and SPCX buys already used. A 40% BTC-complex rebuild would require selling those same-day buys during the close window, which reverses “exit every other equity” and the 50/50 pin. That is not the best use of capital. It is a standing-order violation.

If the Chairman later reopens the 40% sleeve, the first dollars are **STRC and/or SATA** (spreads $0.04 and $0.01 — not an illiquidity skip, and not “MSTR already covers credit”), then a **diversified miner set** (MARA, IREN, CLSK, RIOT, WULF — overlap is not a veto). Stocks deposits stay **50/50 SPCX + TSLA**. Do not reseat GOOGL, NVDA, CCJ, BWXT, or BE under the current pin.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. |
| SPCX | space / growth | Hold. Stocks pin 50%. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Empty after the 17:47Z exit. Spread $0.04. Preferred digital-credit core, not skipped for liquidity or because MSTR covers credit. No buying power. Rebuying means selling today's TSLA/SPCX. |
| SATA | Same. Spread $0.01. Pair to STRC, not a cash proxy, and not available without a reversal. |
| MSTR | Sold 17:47Z. Not the first dollar even if the sleeve reopens (MSCI classification date ~Oct 16 is the near risk). |
| BITA, ASST | Sold or never seated. Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Sold 17:47Z. **Not** rejected for miner overlap. Miner→compute tape (CleanSpark–Meta lease, Hyperscale “Patriot BTC”) is supportive for a later sleeve, not a reason to reverse today. |
| BTC | Crypto sleeve stays $0. The ~$38 equity is not spot bitcoin. |
| STRK | Ready, junior to STRC, conversion still far out of the money, spread $0.38 vs STRC $0.04. Relative value fails. No residual. |
| GOOGL, NVDA | Sold 17:47Z. Stocks rule is 50/50 SPCX+TSLA only. Do not reseat. |
| AAPL, PLTR, AMZN | Ready. Behind the stocks pin. No buying power. |
| RKLB | Ready. Electron cadence is fine; Neutron is still unflown. SPCX is the space core. Stocks pin. |
| CCJ, BWXT | CCJ sold 17:47Z. BWXT sold the same minute. Second nuclear seat stays watchlist. Pin says no other equities. |
| EVGO | Ready, low-priority show-me. Stocks pin. |
| BE | Blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / IAU / GLD | Research-only until the owner calls a buy. Below bitcoin. |
| Held-only top-up | Rejected as a rationale. TSLA and SPCX are the authorized pins, not “add to whatever is largest.” |
| Rebuild 40% this session | Would undo the filled Chairman rebalance inside the close window. Buying power is $0.01. |
| Trim TSLA vs SPCX | $0.31 gap is under the $1 minimum. |

Private (Anduril, Saronic, Boom, Sail, Glow): context only. Not in the deploy set.

## 3) Team

- **Scout:** Book is the filled Chairman stocks book. Dust buying power. No open orders.
- **Thesis:** Hold both pins. The 0/100 mix is the order, not an unpaid ticket. If the 40% sleeve reopens later, lead with STRC/SATA, then several miners.
- **Risk:** No trade. Do not spend unsettled proceeds twice. Do not touch primary. Two-name concentration is the standing order.
- **Critic:** Held-only inertia rejected. STRC/SATA underweight is real and is rebutted only by the Chairman exit plus zero buying power, not by liquidity or “we already own MSTR.” Miner-overlap veto rejected. Reversal blocked.
- **Executor:** No order.
