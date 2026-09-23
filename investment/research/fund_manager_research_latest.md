# Fund manager research — 2026-09-23 (~13:17 ET, mid-session HOLD)

**As of:** 2026-09-23 ~17:16Z (~13:17 ET). Regular hours, mid-session (not the open or the close). Closes the 17:15Z rules re-fire (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60).
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value $0.09, cash $0.09, crypto dust $0.004, no equity positions) and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$336.27**. Broker equity **$336.25**. Quote equity **$336.25**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~17:16Z | TSLA **$169.58** (50.43%) / SPCX **$166.66** (49.57%) of **$336.25** |
| 50/50 gap | **$1.46** one-way ($0.46 over the $1 minimum; +$0.19 vs the 17:01Z HOLD of $1.27) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~17:16Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 378.21 | 378.90 | −0.18% | 378.17 / 378.24 | 0.07 |
| SPCX | 150.8963 | 154.72 | −2.47% | 150.88 / 150.90 | 0.02 |
| STRC | 98.815 | 99.06 | −0.25% | 98.81 / 98.82 | **0.01** (1 bp) |
| SATA | 100.0001 | 100.01 (adj 99.9584) | +0.04% vs adj | 100.00 / 100.01 | **0.01** (1 bp) |
| MSTR | 162.94 | 167.33 | −2.62% | 162.90 / 162.99 | 0.09 |
| BITA | 63.11 | 64.46 | −2.09% | 62.95 / 63.22 | 0.27 |
| ASST | 28.97 | 29.35 | −1.29% | 28.96 / 28.98 | 0.02 |
| MARA | 13.5223 | 13.63 | −0.79% | 13.52 / 13.53 | 0.01 |
| RIOT | 24.975 | 24.93 | +0.18% | 24.97 / 24.98 | 0.01 |
| CLSK | 14.655 | 15.14 | −3.20% | 14.65 / 14.66 | 0.01 |
| WULF | 16.745 | 17.33 | −3.38% | 16.74 / 16.75 | 0.01 |
| IREN | 47.8394 | 48.55 | −1.46% | 47.81 / 47.83 | 0.02 |
| STRK | 72.451 | 74.61 | −2.89% | 72.05 / 73.77 | **1.72** (~237 bp; book ~17:13Z) |
| GOOGL | 338.185 | 351.16 | −3.69% | 338.17 / 338.20 | 0.03 |
| NVDA | 224.42 | 228.87 | −1.94% | 224.41 / 224.43 | 0.02 |
| AAPL | 337.445 | 339.75 | −0.68% | 337.41 / 337.46 | 0.05 |
| PLTR | 190.845 | 184.99 | +3.17% | 190.81 / 190.89 | 0.08 |
| AMZN | 248.185 | 254.98 | −2.66% | 248.19 / 248.20 | 0.01 |
| RKLB | 70.8199 | 71.98 | −1.61% | 70.79 / 70.82 | 0.03 |
| CCJ | 91.635 | 94.59 | −3.12% | 91.58 / 91.68 | 0.10 |
| BWXT | 143.29 | 144.51 | −0.84% | 142.92 / 143.32 | 0.40 |
| EVGO | 1.385 | 1.47 | −5.78% | 1.38 / 1.39 | 0.01 |
| BTC-USD | 83,877.76 | 86,706.52 | −3.26% | 83,877.75 / 83,877.76 | tight |

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold is research-only (not quoted; no owner buy call). Private names are not in the deploy set; today's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.2% today; GOOGL is about −3.7%. None of these is a Chairman override. No new watchlist symbols proposed: the ready set already covers AI, nuclear, space, and the STRK credit sibling, and there is no capital to seat a new name.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$134) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads 1 bp and 1 bp; STRC −0.25% and SATA +0.04% vs MSTR −2.62%) — not MSTR by habit, and not because miners overlap. STRC/SATA are tighter than at 17:01Z, which removes liquidity as a rebuttal rather than creating a ticket.

The only mechanically legal ticket is a $1.46 TSLA→SPCX pin chase (sell ~0.0039 TSLA, buy ~0.0097 SPCX). That is 43 basis points of drift, $0.46 over the dust floor, and it is wider than the 17:01Z gap of $1.27 because SPCX fell further (−2.47% vs −2.18%) while TSLA is only −0.18%. Spreads are not the cost (about 2 bp and 1 bp). The trade is the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and the extra $0.19 of gap is the same unlock-day move, not a new fact. It would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −3.26% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today. Do not buy RIOT because it is the only green miner.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $1.46. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. The −2.47% day is the reason the gap widened, not a dip to chase. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread 1 bp. Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread 1 bp. Same capital and standing-order block. Not a cash proxy. Slightly green vs the adjusted close. |
| MSTR | Sold 2026-09-22. −2.62% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread 43 bp. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal and WULF −3.38% are caution, not the block. RIOT (+0.18%) is the strong miner today and still not a ticket. |
| BTC | Crypto sleeve unchanged. −3.26% is not a policy override. |
| STRK | Ready sibling, junior to STRC. Spread ~$1.72 vs STRC $0.01. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.17% is not a pin break. AMZN's digest mild-up is not a new-money pin. |
| RKLB | Ready space optionality vs held SPCX. Neutron still the gate. Stocks pin. Not a second space seat today. |
| CCJ, BWXT | Ready nuclear. CCJ −3.12% (digest mild-down). No residual under the stocks pin. Not a basket. |
| EVGO | Low-priority ready. −5.78% is not a show-me entry. |
| BE | `pass` / blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / gold | Research-only. No owner buy call. Below BTC. Not quoted for a ticket. |

## 3) Thesis

Hold. Map: stocks sleeve stays the Chairman 50/50; the numeric 40% BTC complex stays closed. No idle cash to map onto STRC/SATA. Do not treat the 0/100 mix as an unpaid 40/60 gap that this pass must fill by selling the Sep 22 fills.

## 4) Risk

Buying power $0.02 is under the $1 minimum, so no new-money ticket exists. The pin gap clears $1 by $0.46, so a two-leg round trip is above the dust floor and is a real choice, not a mechanical skip. Concentration in two names is the intended book, not a breach. STRC/SATA liquidity is not a risk block (1 bp / 1 bp). STRK's 237 bp book is a liquidity fail versus STRC, which only matters if someone were sizing STRK. No capital-bounds breach because no new capital is deployed. Multi-miner is not a concentration block; there is no miner position to concentrate.

## 5) Critic

Block the $1.46 TSLA→SPCX chase. Block any sale of TSLA/SPCX to rebuild STRC, SATA, miners, or watchlist names. Held-only inertia does not apply: the consider set included unheld core and every ready watchlist name, and the hold is the standing exit, not "we already own it." Under-allocation to STRC/SATA is real versus the numeric 40% and is rebutted only by the Chairman exit plus $0.02 buying power — not by liquidity, not by "MSTR covers credit," and not by miner overlap. Do not buy PLTR or RIOT because they are green.

## 6) Quorum

Risk OK to hold. Thesis OK to hold. Critic forces the pin chase and the sleeve rebuild to size zero. Executor places nothing.

## Explicit do-not-trade

BE; any non-TSLA/SPCX equity; spot BTC; STRK ahead of STRC; gold; private names; a TSLA→SPCX pin chase into the Sep 24 Semi event and SPCX unlock.
