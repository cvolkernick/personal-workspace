# Fund manager research — 2026-09-23 (~15:33 ET, close-window HOLD)

**As of:** 2026-09-23 ~19:32Z (~15:32 ET). Regular hours, inside the avoided last 30 minutes before the 16:00 ET cash close. Not an open scalp.
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value $0.09, cash $0.09, crypto dust $0.004, no equity positions) and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$334.24**. Broker equity **$334.22**. Quote equity **$334.07**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders open: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~19:32Z | TSLA **$170.20** (50.95%) / SPCX **$163.87** (49.05%) of **$334.07** |
| 50/50 gap | **$3.17** one-way ($2.17 over the $1 minimum; +$0.20 vs the 15:22 ET HOLD of $2.97) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~19:32Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 379.585 | 378.90 | +0.18% | 379.54 / 379.60 | 0.06 (1.6 bp) |
| SPCX | 148.365 | 154.72 | −4.11% | 148.36 / 148.37 | 0.01 (0.7 bp) |
| STRC | 98.97 | 99.06 | −0.09% | book 98.87 / 98.98 | **0.11** (11 bp) |
| SATA | 100.0094 | 100.01 (adj 99.9584) | +0.05% vs adj | 100.00 / 100.01 | **0.01** (1.0 bp) |
| MSTR | 163.57 | 167.33 | −2.25% | 163.55 / 163.63 | 0.08 |
| BITA | 63.525 | 64.46 | −1.45% | 63.50 / 63.56 | 0.06 |
| ASST | 29.12 | 29.35 | −0.78% | 29.11 / 29.13 | 0.02 |
| MARA | 13.515 | 13.63 | −0.84% | 13.51 / 13.52 | 0.01 |
| RIOT | 24.875 | 24.93 | −0.22% | 24.87 / 24.88 | 0.01 |
| CLSK | 14.645 | 15.14 | −3.27% | 14.64 / 14.65 | 0.01 |
| WULF | 16.565 | 17.33 | −4.41% | 16.56 / 16.57 | 0.01 |
| IREN | 48.175 | 48.55 | −0.77% | 48.17 / 48.18 | 0.01 |
| STRK | 73.28 (19:26Z) | 74.61 | −1.78% | 73.21 / 73.60 (19:30Z) | **0.39** (~53 bp) |
| GOOGL | 339.29 | 351.16 | −3.38% | 339.27 / 339.29 | 0.02 |
| NVDA | 225.2499 | 228.87 | −1.58% | 225.24 / 225.25 | 0.01 |
| AAPL | 336.22 | 339.75 | −1.04% | 336.17 / 336.21 | 0.04 |
| PLTR | 191.89 | 184.99 | +3.73% | 191.88 / 191.91 | 0.03 |
| AMZN | 250.125 | 254.98 | −1.90% | 250.12 / 250.14 | 0.02 |
| RKLB | 70.9786 | 71.98 | −1.39% | 70.97 / 70.98 | 0.01 |
| CCJ | 91.41 | 94.59 | −3.36% | 91.39 / 91.44 | 0.05 |
| BWXT | 142.40 | 144.51 | −1.46% | 142.20 / 142.51 | 0.31 |
| EVGO | 1.3701 | 1.47 | −6.80% | 1.37 / 1.38 | 0.01 |
| GLDM | 84.755 | 86.33 | −1.82% | 84.75 / 84.76 | 0.01 |
| BTC-USD | 84,415.33 | 86,699.43 | −2.63% | mark | tight |

STRC displayed book ~15:32 ET: bid 98.87 × 100 / ask 98.98 × 1,667 (11 bp). Next bid 98.81 × 300; next ask 98.99 × 1,959. Wider than the 1 bp book at 15:03 ET. The top bid is only 100 shares. A ticket this account could fund is a fraction of a share and would not move the book. The wider spread is not an illiquidity skip. SATA book: bid 100.00 × 4,217 / ask 100.01 × 127,886 (1 bp). Liquidity is not a reason to skip STRC or SATA.

A minute later the TSLA book was 379.67 × 113 / 379.70 × 39 and the SPCX book was 148.41 × 381 / 148.42 × 95. Using those mids, the one-way gap is still about $3.16. The penny does not change the decision.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold was quoted (GLDM) and stays research-only — no owner buy call, below BTC, not a ticket. Private names are not in the deploy set. Today's digest reported no IPO movement; this pass did not re-scan private listings. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.7% today; GOOGL is about −3.4%. None of these is a Chairman override. No new watchlist symbols proposed: the ready set already covers AI, nuclear, space, and the STRK credit sibling, and there is no capital to seat a new name.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$134) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (SATA spread 1 bp; STRC spread widened to 11 bp but the book is still deeper than a dust ticket; STRC −0.09% and SATA +0.05% vs MSTR −2.25%) — not MSTR by habit, and not because miners overlap.

The only mechanically legal ticket is a $3.17 TSLA→SPCX pin chase (sell ~0.0083 TSLA, buy ~0.021 SPCX). That is 95 basis points of drift, $2.17 over the dust floor, and it is $0.20 wider than the 15:22 ET gap of $2.97 because SPCX fell further (−4.11% vs the prior print). Spreads are not the cost (about 1 bp each). The trade is the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and it would print inside the last 30 minutes, which the cadence says to avoid. The extra $0.20 of gap is the same unlock-day move, not a new fact. It would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −2.63% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today. Do not buy RIOT because it is the least-weak miner.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event, and do not sell in the close window, for $3.17. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. The −4.11% day is the reason the gap widened, not a dip to chase into the close. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Book 98.87 × 100 / 98.98 × 1,667 (11 bp; next bid 98.81 × 300). Wider than 15:03 ET, still not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Book 100.00 × 4,217 / 100.01 × 127,886 (1 bp). Same capital and standing-order block. Not a cash proxy. Slightly green vs the adjusted close. |
| MSTR | Sold 2026-09-22. −2.25% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread ~9 bp. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK −3.27% and WULF −4.41% are caution, not the block. RIOT (−0.22%) is the least-weak miner today and still not a ticket. |
| BTC | Crypto sleeve unchanged. −2.63% is not a policy override. |
| STRK | Ready sibling, junior to STRC. Spread ~$0.39 (~53 bp) vs STRC 11 bp — a relative-value fail. Last trade 19:26Z. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.73% is not a pin break. AMZN's digest mild-up is not a new-money pin. GOOGL −3.38% is not a dip to buy through the pin. |
| RKLB | Ready space optionality vs held SPCX. Neutron still the gate. Stocks pin. Not a second space seat today. |
| CCJ, BWXT | Ready nuclear. CCJ −3.36% (digest mild-down). No residual under the stocks pin. Not a basket. |
| EVGO | Low-priority ready. −6.80% is not a show-me entry. |
| BE | `pass` / blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / gold | Quoted −1.82%. Research-only. No owner buy call. Below BTC. Not a ticket. |

## 3) Thesis

Hold. Map: stocks sleeve stays the Chairman 50/50; the numeric 40% BTC complex stays closed. No idle cash to map onto STRC/SATA. Do not treat the 0/100 mix as an unpaid 40/60 gap that this pass must fill by selling the Sep 22 fills. Do not chase the pin inside the close window.

## 4) Risk

Buying power $0.02 is under the $1 minimum, so no new-money ticket exists. The pin gap clears $1 by $2.17, so a two-leg round trip is above the dust floor and is a real choice, not a mechanical skip. It is still inside the last 30 minutes, which the cadence avoids. Concentration in two names is the intended book, not a breach. STRC/SATA liquidity is not a risk block (11 bp / 1 bp; the 100-share STRC top bid is not a fail for a dust ticket). STRK's ~53 bp book is a liquidity and relative-value fail versus STRC, which only matters if someone were sizing STRK. No capital-bounds breach because no new capital is deployed. Multi-miner is not a concentration block; there is no miner position to concentrate.

## 5) Critic

Block the $3.17 TSLA→SPCX chase. Block any sale of TSLA/SPCX to rebuild STRC, SATA, miners, or watchlist names. Held-only inertia does not apply: the consider set included unheld core and every ready watchlist name, and the hold is the standing exit plus the close window, not "we already own it." Under-allocation to STRC/SATA is real versus the numeric 40% and is rebutted only by the Chairman exit plus $0.02 buying power — not by liquidity, not by "MSTR covers credit," and not by miner overlap. Do not buy PLTR or RIOT because they are the strong tape. Do not treat the wider gap as a reason to chase: it widened by $0.20 because SPCX fell further into the unlock, and the clock is past 15:30 ET.

## 6) Quorum

Risk OK to hold. Thesis OK to hold. Critic forces the pin chase and the sleeve rebuild to size zero. Executor places nothing.

## Explicit do-not-trade

BE; any non-TSLA/SPCX equity; spot BTC; STRK ahead of STRC; gold; private names; a TSLA→SPCX pin chase into the close, the Sep 24 Semi event, and the SPCX unlock.
