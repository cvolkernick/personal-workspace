# Fund manager research — 2026-09-23 (~15:03 ET, mid-session HOLD)

**As of:** 2026-09-23 ~19:03Z (~15:03 ET). Regular hours. Not the open. The avoided close window starts at 15:30 ET; this pass is still outside it.
**Account:** agentic ••••1752 only (`674601752`). Primary margin was read (account value $0.09, cash $0.09, crypto dust $0.004, no equity positions) and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$334.79**. Broker equity **$334.77**. Quote equity **$334.65**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~19:03Z | TSLA **$170.14** (50.84%) / SPCX **$164.51** (49.16%) of **$334.65** |
| 50/50 gap | **$2.81** one-way ($1.81 over the $1 minimum; +$0.08 vs the 14:17 ET HOLD of $2.73) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~19:03Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 379.45 | 378.90 | +0.15% | 379.40 / 379.45 | 0.05 (1.3 bp) |
| SPCX | 148.9494 | 154.72 | −3.73% | 148.93 / 148.95 | 0.02 (1.3 bp) |
| STRC | 98.97 | 99.06 | −0.09% | 98.98 / 98.99 | **0.01** (1.0 bp) |
| SATA | 100.0099 | 100.01 (adj 99.9584) | +0.05% vs adj | 100.00 / 100.01 | **0.01** (1.0 bp) |
| MSTR | 162.63 | 167.33 | −2.81% | 162.61 / 162.64 | 0.03 |
| BITA | 63.3744 | 64.46 | −1.68% | 63.26 / 63.49 | 0.23 (36 bp) |
| ASST | 28.9669 | 29.35 | −1.31% | 28.96 / 28.98 | 0.02 |
| MARA | 13.445 | 13.63 | −1.36% | 13.44 / 13.45 | 0.01 |
| RIOT | 24.895 | 24.93 | −0.14% | 24.89 / 24.90 | 0.01 |
| CLSK | 14.65 | 15.14 | −3.24% | 14.65 / 14.66 | 0.01 |
| WULF | 16.6418 | 17.33 | −3.97% | 16.64 / 16.65 | 0.01 |
| IREN | 47.98 | 48.55 | −1.17% | 47.97 / 47.98 | 0.01 |
| STRK | 73.24 (18:42Z) | 74.61 | −1.84% | 72.91 / 73.20 (18:57Z) | **0.29** (~40 bp) |
| GOOGL | 339.27 | 351.16 | −3.39% | 339.27 / 339.30 | 0.03 |
| NVDA | 225.285 | 228.87 | −1.57% | 225.28 / 225.29 | 0.01 |
| AAPL | 337.13 | 339.75 | −0.77% | 337.11 / 337.13 | 0.02 |
| PLTR | 191.15 | 184.99 | +3.33% | 191.14 / 191.19 | 0.05 |
| AMZN | 249.91 | 254.98 | −1.99% | 249.90 / 249.92 | 0.02 |
| RKLB | 71.06 | 71.98 | −1.28% | 71.05 / 71.07 | 0.02 |
| CCJ | 91.52 | 94.59 | −3.25% | 91.50 / 91.54 | 0.04 |
| BWXT | 142.935 | 144.51 | −1.09% | 142.77 / 142.99 | 0.22 |
| EVGO | 1.375 | 1.47 | −6.46% | 1.37 / 1.38 | 0.01 |
| GLDM | 84.86 | 86.33 | −1.70% | 84.85 / 84.86 | 0.01 |
| BTC-USD | 84,297.63 | 86,699.43 | −2.77% | mark | tight |

STRC displayed book ~15:03 ET: bid 98.98 × 24 / ask 98.99 × 400 (1 bp). The top bid is only 24 shares; the next bid is 98.97 × 540. SATA book: bid 100.00 × 3,647 / ask 100.01 × 134,612 (1 bp). A ticket this account could fund would not move either book. The thin STRC top bid is not an illiquidity skip. Liquidity is not a reason to skip STRC or SATA.

SPCX's book a second later was 148.95 × 100 / 148.96 × 34. That penny does not change the $2.81 gap.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold was quoted (GLDM) and stays research-only — no owner buy call, below BTC, not a ticket. Private names are not in the deploy set. Today's digest reported no IPO movement; this pass did not re-scan private listings. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.3% today; GOOGL is about −3.4%. None of these is a Chairman override. No new watchlist symbols proposed: the ready set already covers AI, nuclear, space, and the STRK credit sibling, and there is no capital to seat a new name.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$134) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (spreads 1 bp and 1 bp; STRC −0.09% and SATA +0.05% vs MSTR −2.81%) — not MSTR by habit, and not because miners overlap. The book is deeper than a dust ticket needs, which removes liquidity as a rebuttal rather than creating a ticket.

The only mechanically legal ticket is a $2.81 TSLA→SPCX pin chase (sell ~0.0074 TSLA, buy ~0.0189 SPCX). That is 84 basis points of drift, $1.81 over the dust floor, and it is $0.08 wider than the 14:17 ET gap of $2.73 because SPCX fell further (−3.73% vs −3.52%) while TSLA is +0.15%. Spreads are not the cost (about 1 bp each). The trade is the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and the extra $0.08 of gap is the same unlock-day move, not a new fact. It would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −2.77% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today. Do not buy RIOT because it is the least-weak miner.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event for $2.81. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. The −3.73% day is the reason the gap widened, not a dip to chase. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Spread 1 bp; book 98.98 × 24 / 98.99 × 400 (next bid 98.97 × 540). Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Spread 1 bp; book 100.00 × 3,647 / 100.01 × 134,612. Same capital and standing-order block. Not a cash proxy. Slightly green vs the adjusted close. |
| MSTR | Sold 2026-09-22. −2.81% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread 36 bp. Last print 17:48Z; live book agrees. No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. CLSK −3.24% and WULF −3.97% are caution, not the block. RIOT (−0.14%) is the least-weak miner today and still not a ticket. |
| BTC | Crypto sleeve unchanged. −2.77% is not a policy override. |
| STRK | Ready sibling, junior to STRC. Spread ~$0.29 (~40 bp) vs STRC 1 bp — tighter than the ~$1.09 book at 14:17 ET, still a relative-value fail. Last trade 18:42Z. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.33% is not a pin break. AMZN's digest mild-up is not a new-money pin. GOOGL −3.39% is not a dip to buy through the pin. |
| RKLB | Ready space optionality vs held SPCX. Neutron still the gate. Stocks pin. Not a second space seat today. |
| CCJ, BWXT | Ready nuclear. CCJ −3.25% (digest mild-down). No residual under the stocks pin. Not a basket. |
| EVGO | Low-priority ready. −6.46% is not a show-me entry. |
| BE | `pass` / blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / gold | Quoted −1.70%. Research-only. No owner buy call. Below BTC. Not a ticket. |

## 3) Thesis

Hold. Map: stocks sleeve stays the Chairman 50/50; the numeric 40% BTC complex stays closed. No idle cash to map onto STRC/SATA. Do not treat the 0/100 mix as an unpaid 40/60 gap that this pass must fill by selling the Sep 22 fills.

## 4) Risk

Buying power $0.02 is under the $1 minimum, so no new-money ticket exists. The pin gap clears $1 by $1.81, so a two-leg round trip is above the dust floor and is a real choice, not a mechanical skip. Concentration in two names is the intended book, not a breach. STRC/SATA liquidity is not a risk block (1 bp / 1 bp; the 24-share STRC top bid is not a fail). STRK's ~40 bp book is a liquidity and relative-value fail versus STRC, which only matters if someone were sizing STRK. No capital-bounds breach because no new capital is deployed. Multi-miner is not a concentration block; there is no miner position to concentrate.

## 5) Critic

Block the $2.81 TSLA→SPCX chase. Block any sale of TSLA/SPCX to rebuild STRC, SATA, miners, or watchlist names. Held-only inertia does not apply: the consider set included unheld core and every ready watchlist name, and the hold is the standing exit, not "we already own it." Under-allocation to STRC/SATA is real versus the numeric 40% and is rebutted only by the Chairman exit plus $0.02 buying power — not by liquidity, not by "MSTR covers credit," and not by miner overlap. Do not buy PLTR or RIOT because they are the strong tape. Do not treat the wider gap as a reason to chase: it widened by $0.08 because SPCX fell further into the unlock.

## 6) Quorum

Risk OK to hold. Thesis OK to hold. Critic forces the pin chase and the sleeve rebuild to size zero. Executor places nothing.

## Explicit do-not-trade

BE; any non-TSLA/SPCX equity; spot BTC; STRK ahead of STRC; gold; private names; a TSLA→SPCX pin chase into the Sep 24 Semi event and SPCX unlock.
