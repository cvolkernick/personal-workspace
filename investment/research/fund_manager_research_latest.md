# Fund manager research — 2026-09-23 (~14:02 ET, mid-session HOLD)

**As of:** 2026-09-23 ~18:02Z (~14:02 ET). Regular hours, mid-session (not the open or the close).
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value $0.09, cash $0.09, crypto dust $0.004, no equity positions) and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$335.70**. Broker equity **$335.68**. Quote equity **$335.71**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~18:01Z | TSLA **$170.24** (50.71%) / SPCX **$165.47** (49.29%) of **$335.71** |
| 50/50 gap | **$2.39** one-way ($1.39 over the $1 minimum; +$0.60 vs the 17:31Z HOLD of $1.79) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~18:01Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 379.68 | 378.90 | +0.21% | 379.58 / 379.68 | 0.10 (2.6 bp) |
| SPCX | 149.8101 | 154.72 | −3.17% | 149.81 / 149.82 | 0.01 (0.7 bp) |
| STRC | 98.905 | 99.06 | −0.16% | 98.89 / 98.92 | **0.03** (3 bp) |
| SATA | 100.01 | 100.01 (adj 99.9584) | +0.05% vs adj | 100.00 / 100.01 | **0.01** (1 bp) |
| MSTR | 162.99 | 167.33 | −2.59% | 162.98 / 163.03 | 0.05 |
| BITA | 63.3744 | 64.46 | −1.68% | 63.24 / 63.49 | 0.25 (40 bp) |
| ASST | 28.875 | 29.35 | −1.62% | 28.87 / 28.88 | 0.01 |
| MARA | 13.595 | 13.63 | −0.26% | 13.59 / 13.60 | 0.01 |
| RIOT | 24.92 | 24.93 | −0.04% | 24.92 / 24.93 | 0.01 |
| CLSK | 14.81 | 15.14 | −2.18% | 14.80 / 14.81 | 0.01 |
| WULF | 16.81 | 17.33 | −3.00% | 16.80 / 16.81 | 0.01 |
| IREN | 48.01 | 48.55 | −1.11% | 47.98 / 48.01 | 0.03 |
| STRK | 72.81 | 74.61 | −2.41% | 72.53 / 73.77 | **1.24** (~170 bp) |
| GOOGL | 338.905 | 351.16 | −3.49% | 338.89 / 338.92 | 0.03 |
| NVDA | 225.38 | 228.87 | −1.52% | 225.37 / 225.39 | 0.02 |
| AAPL | 337.535 | 339.75 | −0.65% | 337.52 / 337.54 | 0.02 |
| PLTR | 191.2801 | 184.99 | +3.40% | 191.29 / 191.34 | 0.05 |
| AMZN | 249.1501 | 254.98 | −2.29% | 249.17 / 249.18 | 0.01 |
| RKLB | 70.70 | 71.98 | −1.78% | 70.68 / 70.71 | 0.03 |
| CCJ | 91.8093 | 94.59 | −2.94% | 91.74 / 91.79 | 0.05 |
| BWXT | 143.185 | 144.51 | −0.92% | 143.20 / 143.30 | 0.10 |
| EVGO | 1.385 | 1.47 | −5.78% | 1.38 / 1.39 | 0.01 |
| GLDM | 84.935 | 86.33 | −1.62% | 84.93 / 84.94 | 0.01 |
| BTC-USD | 84,276.29 | 86,706.52 | −2.80% | 84,278.71 / 84,273.87 | tight |

STRC displayed book ~18:01Z: bid 98.89 × 2,600 / ask 98.92 × 272. A ticket this account could fund would not move that book. Liquidity is not a reason to skip STRC.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold was quoted (GLDM) and stays research-only — no owner buy call, below BTC, not a ticket. Private names are not in the deploy set; today's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.4% today; GOOGL is about −3.5%. None of these is a Chairman override. No new watchlist symbols proposed: the ready set already covers AI, nuclear, space, and the STRK credit sibling, and there is no capital to seat a new name.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$134) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads 3 bp and 1 bp; STRC −0.16% and SATA +0.05% vs MSTR −2.59%) — not MSTR by habit, and not because miners overlap. The book is deeper than the last pass, which removes liquidity as a rebuttal rather than creating a ticket.

The only mechanically legal ticket is a $2.39 TSLA→SPCX pin chase (sell ~0.0063 TSLA, buy ~0.0159 SPCX). That is 71 basis points of drift, $1.39 over the dust floor, and it is wider than the 17:31Z gap of $1.79 because SPCX fell further (−3.17% vs −2.72%) while TSLA flipped to +0.21%. Spreads are not the cost (about 3 bp and 1 bp). The trade is the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and the extra $0.60 of gap is the same unlock-day move, not a new fact. It would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −2.80% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today. Do not buy RIOT because it is the least-weak miner.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $2.39. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. The −3.17% day is the reason the gap widened, not a dip to chase. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread 3 bp; book 98.89 × 2,600 / 98.92 × 272. Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread 1 bp. Same capital and standing-order block. Not a cash proxy. Slightly green vs the adjusted close. |
| MSTR | Sold 2026-09-22. −2.59% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread 40 bp. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal and WULF −3.00% are caution, not the block. RIOT (−0.04%) is the least-weak miner today and still not a ticket. |
| BTC | Crypto sleeve unchanged. −2.80% is not a policy override. |
| STRK | Ready sibling, junior to STRC. Spread ~$1.24 (~170 bp) vs STRC $0.03. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.40% is not a pin break. AMZN's digest mild-up is not a new-money pin. GOOGL −3.49% is not a dip to buy through the pin. |
| RKLB | Ready space optionality vs held SPCX. Neutron still the gate. Stocks pin. Not a second space seat today. |
| CCJ, BWXT | Ready nuclear. CCJ −2.94% (digest mild-down). No residual under the stocks pin. Not a basket. |
| EVGO | Low-priority ready. −5.78% is not a show-me entry. |
| BE | `pass` / blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / gold | Quoted −1.62%. Research-only. No owner buy call. Below BTC. Not a ticket. |

## 3) Thesis

Hold. Map: stocks sleeve stays the Chairman 50/50; the numeric 40% BTC complex stays closed. No idle cash to map onto STRC/SATA. Do not treat the 0/100 mix as an unpaid 40/60 gap that this pass must fill by selling the Sep 22 fills.

## 4) Risk

Buying power $0.02 is under the $1 minimum, so no new-money ticket exists. The pin gap clears $1 by $1.39, so a two-leg round trip is above the dust floor and is a real choice, not a mechanical skip. Concentration in two names is the intended book, not a breach. STRC/SATA liquidity is not a risk block (3 bp / 1 bp). STRK's ~170 bp book is a liquidity fail versus STRC, which only matters if someone were sizing STRK. No capital-bounds breach because no new capital is deployed. Multi-miner is not a concentration block; there is no miner position to concentrate.

## 5) Critic

Block the $2.39 TSLA→SPCX chase. Block any sale of TSLA/SPCX to rebuild STRC, SATA, miners, or watchlist names. Held-only inertia does not apply: the consider set included unheld core and every ready watchlist name, and the hold is the standing exit, not "we already own it." Under-allocation to STRC/SATA is real versus the numeric 40% and is rebutted only by the Chairman exit plus $0.02 buying power — not by liquidity, not by "MSTR covers credit," and not by miner overlap. Do not buy PLTR or RIOT because they are the strong tape. Do not treat the wider gap as a reason to chase: it widened because SPCX fell further into the unlock.

## 6) Quorum

Risk OK to hold. Thesis OK to hold. Critic forces the pin chase and the sleeve rebuild to size zero. Executor places nothing.

## Explicit do-not-trade

BE; any non-TSLA/SPCX equity; spot BTC; STRK ahead of STRC; gold; private names; a TSLA→SPCX pin chase into the Sep 24 Semi event and SPCX unlock.
