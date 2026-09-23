# Fund manager research — 2026-09-23 (~15:48 ET, close-window HOLD)

**As of:** 2026-09-23 ~19:48Z (~15:48 ET). Regular hours, inside the avoided last 30 minutes before the 16:00 ET cash close. Not an open scalp. This pass closes the 19:46:33Z rules re-fire (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60 ±5%).
**Account:** agentic ••••1752 only (`674601752`). Primary margin was identified on `get_accounts` (not tradable by this agent) and was not read for balances and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** **$334.24**. Broker equity **$334.22**. Quote equity **$334.25**. Cash / buying power **$0.02 / $0.02**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Equity orders today: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0 |
| Quote equity ~19:47Z | TSLA **$170.10** (50.89%) / SPCX **$164.15** (49.11%) of **$334.25** |
| 50/50 gap | **$2.98** one-way ($1.98 over the $1 minimum; **−$0.19** vs the 15:33 ET HOLD of $3.17) |
| Later book mids ~15:48 ET | TSLA 379.565 / SPCX 148.745 → gap still **$2.95** |
| Deployed mix | BTC-complex **0%** / stocks **100%** |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes no pin change and keeps the 40% sleeve closed.

### Live marks used for relative value (~19:47Z)

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 379.365 | 378.90 | +0.12% | book 379.55 × 173 / 379.58 × 44 | 0.03 (0.8 bp) |
| SPCX | 148.620 | 154.72 | −3.94% | book 148.74 × 100 / 148.75 × 211 | 0.01 (0.7 bp) |
| STRC | 98.91 | 99.06 | −0.15% | book 98.92 × 1,600 / 98.93 × 16 | **0.01** (1 bp) |
| SATA | 100.01 | 100.01 (adj 99.9584) | +0.05% vs adj | book 100.00 × 4,215 / 100.01 × 124,405 | **0.01** (1 bp) |
| MSTR | 162.855 | 167.33 | −2.67% | 162.86 / 162.90 | 0.04 |
| BITA | 63.54 | 64.46 | −1.43% | 63.33 / 63.59 | 0.26 |
| ASST | 29.04 | 29.35 | −1.06% | 29.03 / 29.06 | 0.03 |
| MARA | 13.4027 | 13.63 | −1.67% | 13.40 / 13.41 | 0.01 |
| RIOT | 24.685 | 24.93 | −0.98% | 24.68 / 24.69 | 0.01 |
| CLSK | 14.578 | 15.14 | −3.71% | 14.58 / 14.59 | 0.01 |
| WULF | 16.4384 | 17.33 | −5.14% | 16.43 / 16.44 | 0.01 |
| IREN | 47.74 | 48.55 | −1.67% | 47.73 / 47.74 | 0.01 |
| STRK | 73.32 (19:42Z) | 74.61 | −1.73% | 73.41 / 74.00 (19:42Z) | **0.59** (~80 bp) |
| GOOGL | 338.00 | 351.16 | −3.75% | 338.02 / 338.04 | 0.02 |
| NVDA | 225.3799 | 228.87 | −1.52% | 225.36 / 225.37 | 0.01 |
| AAPL | 336.685 | 339.75 | −0.90% | 336.67 / 336.70 | 0.03 |
| PLTR | 191.305 | 184.99 | +3.41% | 191.28 / 191.32 | 0.04 |
| AMZN | 249.13 | 254.98 | −2.29% | 249.10 / 249.13 | 0.03 |
| RKLB | 70.6299 | 71.98 | −1.88% | 70.60 / 70.62 | 0.02 |
| CCJ | 91.16 | 94.59 | −3.63% | 91.13 / 91.16 | 0.03 |
| BWXT | 141.74 | 144.51 | −1.92% | 141.65 / 141.77 | 0.12 |
| EVGO | 1.37 | 1.47 | −6.80% | 1.37 / 1.38 | 0.01 |
| GLDM | 84.775 | 86.33 | −1.80% | 84.77 / 84.78 | 0.01 |
| BTC-USD | 84,500.06 | 86,699.43 | −2.54% | 84,500.05 / 84,500.06 | tight |

STRC displayed book ~15:48 ET: bid 98.92 × 1,600 / ask 98.93 × 16 (1 bp). Next ask 98.94 × 100. Tighter than the 11 bp book at 15:33 ET. The top ask is only 16 shares; a ticket this account could fund is a fraction of a share and would not move the book. The thin top ask is not an illiquidity skip. SATA book: bid 100.00 × 4,215 / ask 100.01 × 124,405 (1 bp). Liquidity is not a reason to skip STRC or SATA.

A minute later the TSLA book was 379.55 × 173 / 379.58 × 44 and the SPCX book was 148.74 × 100 / 148.75 × 211. Using those mids, the one-way gap is $2.95. The penny does not change the decision. The gap **narrowed** versus 15:33 ET because SPCX bounced off the lows (last 148.62 vs 148.37), not because a trade happened.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold was quoted (GLDM) and stays research-only — no owner buy call, below BTC, not a ticket. Private names are not in the deploy set. Today's digest reported no IPO movement; this pass did not re-scan private listings. Ready watchlist named: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, about 50 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: Strategy bought more BTC and repurchased STRC (reopen context, credit-first). Record BTC ETF inflows vs a hawkish dot path. MARA resumed buying BTC; CLSK proposed a large secured-notes deal (miner-tail caution, not an overlap veto). SPCX lockup tranche and Tesla Semi inauguration are both **2026-09-24**. RKLB contract news and AMZN power spend are mild-up watchlist items. PLTR is about +3.4% today; GOOGL is about −3.8%. None of these is a Chairman override. No new watchlist symbols proposed: the ready set already covers AI, nuclear, space, and the STRK credit sibling, and there is no capital to seat a new name.

### How capital serves themes now

There is no deployable capital. Buying power is $0.02. Rebuilding ~40% (~$134) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." That is not the best use of capital under the standing order. On today's tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (both 1 bp now; STRC −0.15% and SATA +0.05% vs MSTR −2.67%) — not MSTR by habit, and not because miners overlap.

The only mechanically legal ticket is a ~$2.98 TSLA→SPCX pin chase (sell ~0.0078 TSLA, buy ~0.020 SPCX). That is about 89 basis points of drift, $1.98 over the dust floor, and it is **$0.19 narrower** than the 15:33 ET gap of $3.17 because SPCX bounced. Spreads are not the cost (under 1 bp each). The trade is still the wrong side of two named catalysts — sell the Semi-event name, buy the lockup name — and it would print inside the last 12 minutes, which the cadence says to avoid. A smaller gap is not a new reason to trade. It would not restore the 40% sleeve. It is not how new capital — there is none — best serves the themes.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes (today's −2.54% is not an override). Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, or BE under the current pin. Do not buy PLTR because it is the strong name today. Do not buy RIOT because it is the least-weak miner.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Stocks pin 50%. Do not sell into the Sep 24 Semi event, and do not sell in the close window, for $2.98. |
| SPCX | space / growth | Hold. Stocks pin 50%. Unlock tomorrow is noted, not a buy. The −3.94% day is still the gap, and the bounce that narrowed it is not a dip to chase into the close. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC | Preferred digital-credit core. Book 98.92 × 1,600 / 98.93 × 16 (1 bp; next ask 98.94 × 100). Tighter than the 11 bp book at 15:33 ET. Not skipped for liquidity or because MSTR covers credit. BP $0.02. Rebuy reverses the Chairman exit. |
| SATA | Pair to STRC. Book 100.00 × 4,215 / 100.01 × 124,405 (1 bp). Same capital and standing-order block. Not a cash proxy. Slightly green vs the adjusted close. |
| MSTR | Sold 2026-09-22. −2.67% today, worse than STRC/SATA. Not the first dollar even if the sleeve reopens. |
| BITA | Secondary to STRC/SATA. Spread ~41 bp (63.33 / 63.59). No residual. |
| ASST | Secondary to STRC/SATA. No residual. |
| MARA, IREN, CLSK, RIOT, WULF | Diversification inside the miner sleeve is allowed. Rejected only because the equity exit is in force and buying power is $0.02. Not miner-overlap. WULF −5.14% and CLSK −3.71% are caution, not the block. RIOT (−0.98%) is the least-weak miner today and still not a ticket. |
| BTC | Crypto sleeve unchanged. −2.54% is not a policy override. |
| STRK | Ready sibling, junior to STRC. Spread ~$0.59 (~80 bp) vs STRC 1 bp — a relative-value fail, wider than the $0.39 book at 15:33 ET. Last trade 19:42Z. No residual. |
| GOOGL, NVDA, AAPL, PLTR, AMZN | Ready AI names. Stocks pin is 50/50 SPCX+TSLA only. PLTR +3.41% is not a pin break. AMZN's digest mild-up is not a new-money pin. GOOGL −3.75% is not a dip to buy through the pin. |
| RKLB | Ready space optionality vs held SPCX. Neutron still the gate. Stocks pin. Not a second space seat today. |
| CCJ, BWXT | Ready nuclear. CCJ −3.63% (digest mild-down). No residual under the stocks pin. Not a basket. |
| EVGO | Low-priority ready. −6.80% is not a show-me entry. |
| BE | `pass` / blocked. Owner exit 2026-09-03. Do not reseat. |
| GLDM / gold | Quoted −1.80%. Research-only. No owner buy call. Below BTC. Not a ticket. |

## 3) Thesis

Hold. Map: stocks sleeve stays the Chairman 50/50; the numeric 40% BTC complex stays closed. No idle cash to map onto STRC/SATA. Do not treat the 0/100 mix as an unpaid 40/60 gap that this pass must fill by selling the Sep 22 fills. Do not chase the pin inside the close window. The gap narrowed by $0.19; that is SPCX's bounce, not a new allocation case.

## 4) Risk

Buying power $0.02 is under the $1 minimum, so no new-money ticket exists. The pin gap clears $1 by $1.98, so a two-leg round trip is above the dust floor and is a real choice, not a mechanical skip. It is still inside the last 30 minutes, which the cadence avoids. Concentration in two names is the intended book, not a breach. STRC/SATA liquidity is not a risk block (1 bp / 1 bp; the 16-share STRC top ask is not a fail for a dust ticket). STRK's ~80 bp book is a liquidity and relative-value fail versus STRC, which only matters if someone were sizing STRK. No capital-bounds breach because no new capital is deployed. Multi-miner is not a concentration block; there is no miner position to concentrate.

## 5) Critic

Block the $2.98 TSLA→SPCX chase. Block any sale of TSLA/SPCX to rebuild STRC, SATA, miners, or watchlist names. Held-only inertia does not apply: the consider set included unheld core and every ready watchlist name, and the hold is the standing exit plus the close window, not "we already own it." Under-allocation to STRC/SATA is real versus the numeric 40% and is rebutted only by the Chairman exit plus $0.02 buying power — not by liquidity (spreads tightened to 1 bp), not by "MSTR covers credit," and not by miner overlap. Do not buy PLTR or RIOT because they are the strong tape. Do not treat the narrower gap as a reason to trade into the close: it shrank by $0.19 because SPCX bounced, and the clock is past 15:30 ET, closer to the cash close than the prior hold.

## 6) Quorum

Risk OK to hold. Thesis OK to hold. Critic forces the pin chase and the sleeve rebuild to size zero. Executor places nothing.
