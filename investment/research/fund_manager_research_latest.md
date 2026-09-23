# Fund manager research — 2026-09-23 (~13:32 ET, mid-session HOLD)

**As of:** 2026-09-23 ~17:31Z (~13:32 ET). Regular hours, mid-session (not the open or the close). Closes the 17:30Z rules re-fire (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60).
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value $0.09, cash $0.09, crypto dust $0.004, no equity positions) and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$336.08**. Broker equity **$336.06**. Quote equity **$336.06**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~17:31Z | TSLA **$169.82** (50.53%) / SPCX **$166.24** (49.47%) of **$336.06** |
| 50/50 gap | **$1.79** one-way ($0.79 over the $1 minimum; +$0.33 vs the 17:16Z HOLD of $1.46) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~17:31Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 378.745 | 378.90 | −0.04% | 378.67 / 378.81 | 0.14 |
| SPCX | 150.5075 | 154.72 | −2.72% | 150.51 / 150.52 | 0.01 |
| STRC | 98.80 | 99.06 | −0.26% | 98.79 / 98.81 | **0.02** (2 bp) |
| SATA | 100.01 | 100.01 (adj 99.9584) | +0.05% vs adj | 100.00 / 100.01 | **0.01** (1 bp) |
| MSTR | 163.065 | 167.33 | −2.55% | 163.02 / 163.11 | 0.09 |
| BITA | 63.16 | 64.46 | −2.02% | 63.16 / 63.37 | 0.21 |
| ASST | 28.95 | 29.35 | −1.36% | 28.94 / 28.96 | 0.02 |
| MARA | 13.63 | 13.63 | 0.00% | 13.63 / 13.64 | 0.01 |
| RIOT | 25.165 | 24.93 | +0.94% | 25.16 / 25.17 | 0.01 |
| CLSK | 14.755 | 15.14 | −2.54% | 14.75 / 14.76 | 0.01 |
| WULF | 16.82 | 17.33 | −2.94% | 16.81 / 16.82 | 0.01 |
| IREN | 48.10 | 48.55 | −0.93% | 48.09 / 48.10 | 0.01 |
| STRK | 73.065 | 74.61 | −2.07% | 72.40 / 73.77 | **1.37** (~188 bp) |
| GOOGL | 338.36 | 351.16 | −3.65% | 338.34 / 338.37 | 0.03 |
| NVDA | 224.455 | 228.87 | −1.93% | 224.45 / 224.46 | 0.01 |
| AAPL | 336.94 | 339.75 | −0.83% | 336.92 / 336.95 | 0.03 |
| PLTR | 190.81 | 184.99 | +3.15% | 190.82 / 190.92 | 0.10 |
| AMZN | 247.92 | 254.98 | −2.77% | 247.91 / 247.94 | 0.03 |
| RKLB | 70.91 | 71.98 | −1.49% | 70.90 / 70.91 | 0.01 |
| CCJ | 91.8489 | 94.59 | −2.90% | 91.78 / 91.92 | 0.14 |
| BWXT | 143.395 | 144.51 | −0.77% | 143.31 / 143.37 | 0.06 |
| EVGO | 1.385 | 1.47 | −5.78% | 1.38 / 1.39 | 0.01 |
| BTC-USD | 84,051.74 | 86,706.52 | −3.06% | 84,049.57 / 84,053.91 | tight |

STRC displayed book ~17:31Z: bid 98.78 × 2,200 / ask 98.81 × 115. A ticket this account could fund would not move that book. Liquidity is not a reason to skip STRC.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold is research-only (not quoted; no owner buy call). Private names are not in the deploy set; today's digest reported no IPO movement. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.2% today; GOOGL is about −3.6%. None of these is a Chairman override. No new watchlist symbols proposed: the ready set already covers AI, nuclear, space, and the STRK credit sibling, and there is no capital to seat a new name.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$134) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads 2 bp and 1 bp; STRC −0.26% and SATA +0.05% vs MSTR −2.55%) — not MSTR by habit, and not because miners overlap. The book is as tight as the last pass, which removes liquidity as a rebuttal rather than creating a ticket.

The only mechanically legal ticket is a $1.79 TSLA→SPCX pin chase (sell ~0.0047 TSLA, buy ~0.0119 SPCX). That is 53 basis points of drift, $0.79 over the dust floor, and it is wider than the 17:16Z gap of $1.46 because SPCX fell further (−2.72% vs −2.47%) while TSLA is −0.04%. Spreads are not the cost (about 4 bp and 1 bp). The trade is the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and the extra $0.33 of gap is the same unlock-day move, not a new fact. It would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −3.06% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today. Do not buy RIOT because it is the only clearly green miner.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $1.79. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. The −2.72% day is the reason the gap widened, not a dip to chase. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread 2 bp; book 98.78 × 2,200 / 98.81 × 115. Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread 1 bp. Same capital and standing-order block. Not a cash proxy. Slightly green vs the adjusted close. |
| MSTR | Sold 2026-09-22. −2.55% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread 33 bp. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK's notes deal and WULF −2.94% are caution, not the block. RIOT (+0.94%) is the strong miner today and still not a ticket. MARA is flat. |
| BTC | Crypto sleeve unchanged. −3.06% is not a policy override. |
| STRK | Ready sibling, junior to STRC. Spread ~$1.37 (~188 bp) vs STRC $0.02. Relative value fails. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.15% is not a pin break. AMZN's digest mild-up is not a new-money pin. GOOGL −3.65% is not a dip to buy through the pin. |
| RKLB | Ready space optionality vs held SPCX. Neutron still the gate. Stocks pin. Not a second space seat today. |
| CCJ, BWXT | Ready nuclear. CCJ −2.90% (digest mild-down). No residual under the stocks pin. Not a basket. |
| EVGO | Low-priority ready. −5.78% is not a show-me entry. |
| BE | `pass` / blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / gold | Research-only. No owner buy call. Below BTC. Not quoted for a ticket. |

## 3) Thesis

Hold. Map: stocks sleeve stays the Chairman 50/50; the numeric 40% BTC complex stays closed. No idle cash to map onto STRC/SATA. Do not treat the 0/100 mix as an unpaid 40/60 gap that this pass must fill by selling the Sep 22 fills. The rules engine's 17:30Z `weights_after_expected` of 40/60 is the numeric prompt, not the order.

## 4) Risk

Buying power $0.02 is under the $1 minimum, so no new-money ticket exists. The pin gap clears $1 by $0.79, so a two-leg round trip is above the dust floor and is a real choice, not a mechanical skip. Concentration in two names is the intended book, not a breach. STRC/SATA liquidity is not a risk block (2 bp / 1 bp). STRK's ~188 bp book is a liquidity fail versus STRC, which only matters if someone were sizing STRK. No capital-bounds breach because no new capital is deployed. Multi-miner is not a concentration block; there is no miner position to concentrate.

## 5) Critic

Block the $1.79 TSLA→SPCX chase. Block any sale of TSLA/SPCX to rebuild STRC, SATA, miners, or watchlist names. Held-only inertia does not apply: the consider set included unheld core and every ready watchlist name, and the hold is the standing exit, not "we already own it." Under-allocation to STRC/SATA is real versus the numeric 40% and is rebutted only by the Chairman exit plus $0.02 buying power — not by liquidity, not by "MSTR covers credit," and not by miner overlap. Do not buy PLTR or RIOT because they are green. Do not treat the wider gap as a reason to chase: it widened because SPCX fell further into the unlock.

## 6) Quorum

Risk OK to hold. Thesis OK to hold. Critic forces the pin chase and the sleeve rebuild to size zero. Executor places nothing.

## Explicit do-not-trade

BE; any non-TSLA/SPCX equity; spot BTC; STRK ahead of STRC; gold; private names; a TSLA→SPCX pin chase into the Sep 24 Semi event and SPCX unlock.
