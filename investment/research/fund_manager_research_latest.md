# Fund manager research — 2026-09-23 (~14:17 ET, mid-session HOLD)

**As of:** 2026-09-23 ~18:17Z (~14:17 ET). Regular hours, mid-session (not the open or the close; close is 16:00 ET).
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value $0.09, cash $0.09, crypto dust $0.004, no equity positions) and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$335.20**. Broker equity **$335.18**. Quote equity **$335.20**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~18:16Z | TSLA **$170.33** (50.81%) / SPCX **$164.87** (49.19%) of **$335.20** |
| 50/50 gap | **$2.73** one-way ($1.73 over the $1 minimum; +$0.34 vs the 14:02 ET HOLD of $2.39) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~18:16Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 379.87 | 378.90 | +0.26% | 379.83 / 379.92 | 0.09 (2.4 bp) |
| SPCX | 149.27 | 154.72 | −3.52% | 149.26 / 149.28 | 0.02 (1.3 bp) |
| STRC | 98.94 | 99.06 | −0.12% | 98.92 / 98.95 | **0.03** (3 bp) |
| SATA | 100.01 | 100.01 (adj 99.9584) | +0.05% vs adj | 100.00 / 100.01 | **0.01** (1 bp) |
| MSTR | 162.37 | 167.33 | −2.96% | 162.40 / 162.47 | 0.07 |
| BITA | 63.3744 | 64.46 | −1.68% | 63.20 / 63.44 | 0.24 (38 bp) |
| ASST | 28.87 | 29.35 | −1.64% | 28.86 / 28.88 | 0.02 |
| MARA | 13.575 | 13.63 | −0.40% | 13.57 / 13.58 | 0.01 |
| RIOT | 25.07 | 24.93 | +0.56% | 25.07 / 25.08 | 0.01 |
| CLSK | 14.855 | 15.14 | −1.88% | 14.85 / 14.86 | 0.01 |
| WULF | 16.82 | 17.33 | −2.94% | 16.82 / 16.83 | 0.01 |
| IREN | 48.2455 | 48.55 | −0.63% | 48.24 / 48.25 | 0.01 |
| STRK | 73.14 | 74.61 | −1.97% | 72.53 / 73.62 | **1.09** (~149 bp) |
| GOOGL | 339.685 | 351.16 | −3.27% | 339.68 / 339.70 | 0.02 |
| NVDA | 225.435 | 228.87 | −1.50% | 225.44 / 225.45 | 0.01 |
| AAPL | 337.05 | 339.75 | −0.79% | 337.02 / 337.05 | 0.03 |
| PLTR | 190.925 | 184.99 | +3.21% | 190.89 / 190.94 | 0.05 |
| AMZN | 249.5599 | 254.98 | −2.13% | 249.55 / 249.58 | 0.03 |
| RKLB | 70.7315 | 71.98 | −1.73% | 70.73 / 70.75 | 0.02 |
| CCJ | 91.76 | 94.59 | −2.99% | 91.71 / 91.79 | 0.08 |
| BWXT | 143.155 | 144.51 | −0.94% | 143.06 / 143.22 | 0.16 |
| EVGO | 1.38 | 1.47 | −6.12% | 1.37 / 1.38 | 0.01 |
| GLDM | 84.865 | 86.33 | −1.70% | 84.85 / 84.86 | 0.01 |
| BTC-USD | 84,195.29 | 86,706.52 | −2.90% | 84,195.28 / 84,195.29 | tight |

STRC displayed book ~14:17 ET: bid 98.94 × 1,300 / ask 98.98 × 1,400 (about 4 bp; NBBO a minute earlier was 3 bp). SATA book: bid 100.00 × 3,075 / ask 100.01 × 47,862 (1 bp). A ticket this account could fund would not move either book. Liquidity is not a reason to skip STRC or SATA.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold was quoted (GLDM) and stays research-only — no owner buy call, below BTC, not a ticket. Private names are not in the deploy set; today's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.2% today; GOOGL is about −3.3%. None of these is a Chairman override. No new watchlist symbols proposed: the ready set already covers AI, nuclear, space, and the STRK credit sibling, and there is no capital to seat a new name.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$134) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads about 4 bp and 1 bp; STRC −0.12% and SATA +0.05% vs MSTR −2.96%) — not MSTR by habit, and not because miners overlap. The book is deeper than a dust ticket needs, which removes liquidity as a rebuttal rather than creating a ticket.

The only mechanically legal ticket is a $2.73 TSLA→SPCX pin chase (sell ~0.0072 TSLA, buy ~0.0183 SPCX). That is 81 basis points of drift, $1.73 over the dust floor, and it is wider than the 14:02 ET gap of $2.39 because SPCX fell further (−3.52% vs −3.17%) while TSLA is +0.26%. Spreads are not the cost (about 2 bp and 1 bp). The trade is the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and the extra $0.34 of gap is the same unlock-day move, not a new fact. It would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −2.90% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today. Do not buy RIOT because it is the strong miner.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $2.73. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. The −3.52% day is the reason the gap widened, not a dip to chase. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread ~4 bp; book 98.94 × 1,300 / 98.98 × 1,400. Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread 1 bp; book 100.00 × 3,075 / 100.01 × 47,862. Same capital and standing-order block. Not a cash proxy. Slightly green vs the adjusted close. |
| MSTR | Sold 2026-09-22. −2.96% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread 38 bp. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal and WULF −2.94% are caution, not the block. RIOT (+0.56%) is the strong miner today and still not a ticket. |
| BTC | Crypto sleeve unchanged. −2.90% is not a policy override. |
| STRK | Ready sibling, junior to STRC. Spread ~$1.09 (~149 bp) vs STRC ~$0.04. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.21% is not a pin break. AMZN's digest mild-up is not a new-money pin. GOOGL −3.27% is not a dip to buy through the pin. |
| RKLB | Ready space optionality vs held SPCX. Neutron still the gate. Stocks pin. Not a second space seat today. |
| CCJ, BWXT | Ready nuclear. CCJ −2.99% (digest mild-down). No residual under the stocks pin. Not a basket. |
| EVGO | Low-priority ready. −6.12% is not a show-me entry. |
| BE | `pass` / blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / gold | Quoted −1.70%. Research-only. No owner buy call. Below BTC. Not a ticket. |

## 3) Thesis

Hold. Map: stocks sleeve stays the Chairman 50/50; the numeric 40% BTC complex stays closed. No idle cash to map onto STRC/SATA. Do not treat the 0/100 mix as an unpaid 40/60 gap that this pass must fill by selling the Sep 22 fills.

## 4) Risk

Buying power $0.02 is under the $1 minimum, so no new-money ticket exists. The pin gap clears $1 by $1.73, so a two-leg round trip is above the dust floor and is a real choice, not a mechanical skip. Concentration in two names is the intended book, not a breach. STRC/SATA liquidity is not a risk block (~4 bp / 1 bp). STRK's ~149 bp book is a liquidity fail versus STRC, which only matters if someone were sizing STRK. No capital-bounds breach because no new capital is deployed. Multi-miner is not a concentration block; there is no miner position to concentrate.

## 5) Critic

Block the $2.73 TSLA→SPCX chase. Block any sale of TSLA/SPCX to rebuild STRC, SATA, miners, or watchlist names. Held-only inertia does not apply: the consider set included unheld core and every ready watchlist name, and the hold is the standing exit, not "we already own it." Under-allocation to STRC/SATA is real versus the numeric 40% and is rebutted only by the Chairman exit plus $0.02 buying power — not by liquidity, not by "MSTR covers credit," and not by miner overlap. Do not buy PLTR or RIOT because they are the strong tape. Do not treat the wider gap as a reason to chase: it widened because SPCX fell further into the unlock.

## 6) Quorum

Risk OK to hold. Thesis OK to hold. Critic forces the pin chase and the sleeve rebuild to size zero. Executor places nothing.

## Explicit do-not-trade

BE; any non-TSLA/SPCX equity; spot BTC; STRK ahead of STRC; gold; private names; a TSLA→SPCX pin chase into the Sep 24 Semi event and SPCX unlock.
