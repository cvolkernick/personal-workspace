# Fund manager research — 2026-09-24 (~09:50 ET, open-window HOLD)

**As of:** 2026-09-24 ~13:50Z (~09:50 ET). Regular hours, inside the avoided first 30 minutes after the 09:30 ET cash open. Not an open scalp. This pass closes the 13:46:52Z rules re-fire (`need_llm`: deployed mix 0% BTC-complex / 100% stocks vs numeric 40/60 ±5%).
**Account:** agentic ••••1752 only (`674601752`). Primary margin was identified on `get_accounts` (not tradable by this agent) and was not read for balances and not traded.
**Process:** Uniform research/rotate, emulated inline (Scout → Thesis → Risk → Critic → Executor). The `fund-manager-research` workflow was not launched: it reads policy and snapshots, cannot see this pass's live quotes, and cannot create buying power or override the Chairman 2026-09-22 standing order. This pass uses live MCP marks plus today's digest.
**Live NAV:** broker **$332.15** (equity **$332.12**, cash **$0.03**). Quote equity at 13:49Z **$331.31**. Buying power **$0.03**. Unsettled **$0**. Pending deposits **$0**. Crypto **$0**. Open equity orders: none.
**Decision:** **HOLD.** No orders.

## 1) Scout

| Field | Value |
|--------|--------|
| Held | **TSLA** 0.448384 sh @ 347.25 avg, **SPCX** 1.104498 sh @ 138.45 avg |
| Not held | Entire BTC complex (MSTR, STRC, SATA, BITA, ASST, MARA, IREN, CLSK, RIOT, WULF), spot BTC, and every ready watchlist name |
| Crypto | $0. BTC-USD mark ~$83,914 vs prior close ~$83,929 (about flat) |
| Quote equity ~13:49Z | TSLA **$169.51** (51.16%) / SPCX **$161.80** (48.84%) of **$331.31** |
| 50/50 gap | **$3.86** one-way ($2.86 over the $1 minimum; **+$0.57** vs the Sep 23 regular-close HOLD of $3.29) |
| Day | TSLA **−0.54%** (378.05 vs 380.12). SPCX **−1.26%** (146.49 vs 148.36) |
| Deployed mix | BTC-complex **0%** / stocks **100%** |
| Clock | 09:50 ET is 20 minutes after the open. Cadence avoids the first 30 minutes (until 10:00 ET) |

Chairman standing order in `investment/consider_share.json` (2026-09-22, still the bias source of truth, and restated in `fund_manager.json` `targets.symbol_targets`): stocks sleeve is **50% SPCX / 50% TSLA** by market value. Exit every other equity. Ongoing stocks capital is 50/50 SPCX+TSLA only. Crypto and cash unchanged unless the Chairman says otherwise. That exit filled 2026-09-22. It has not been reopened. Today's digest proposes **no pin change** and keeps the 40% sleeve closed.

### Live marks used for relative value

Held names refreshed ~13:49Z. Allowlist and watchlist marks ~13:48Z unless noted. RKLB and EVGO ~13:50Z.

| Symbol | Last | Prior close | Day | Bid / ask | Spread |
|--------|------|-------------|-----|-----------|--------|
| TSLA | 378.05 | 380.12 | −0.54% | 378.00 / 378.08 | ~2 bp |
| SPCX | 146.49 | 148.36 | −1.26% | 146.49 / 146.52 | ~2 bp |
| STRC | 98.825 | 98.91 | −0.09% | 98.82 / 98.83 | **1 bp** |
| SATA | 100.00 | 100.01 | ~flat | 99.99 / 100.00 | **1 bp** |
| MSTR | 160.785 | 162.20 | −0.87% | 160.78 / 160.88 | tight |
| BITA | 63.475 (no print today) | 63.475 | — | 63.11 / 63.33 | **~35 bp** |
| ASST | 29.20 | 28.99 | +0.72% | 29.20 / 29.25 | tight |
| MARA | 13.315 | 13.35 | −0.26% | 13.30 / 13.32 | tight |
| RIOT | 24.549 | 24.69 | −0.57% | 24.55 / 24.57 | tight |
| CLSK | 14.48 | 14.46 | +0.14% | 14.46 / 14.48 | tight |
| WULF | 16.41 | 16.35 | +0.37% | 16.40 / 16.42 | tight |
| IREN | 46.56 | 47.05 | −1.04% | 46.56 / 46.58 | tight |
| STRK | 73.50 | 73.25 | +0.34% | 73.00 / 74.00 | **~136 bp** |
| GOOGL | 338.275 | 337.83 | +0.13% | 338.24 / 338.31 | tight |
| NVDA | 222.75 | 225.51 | −1.22% | 222.76 / 222.78 | tight |
| AAPL | 335.24 | 337.02 | −0.53% | 335.20 / 335.25 | tight |
| PLTR | 189.045 | 191.79 | −1.43% | 188.93 / 189.18 | tight |
| AMZN | 246.63 | 249.27 | −1.06% | 246.60 / 246.63 | tight |
| RKLB | 69.82 | 70.31 | −0.69% | 69.76 / 69.81 | tight |
| CCJ | 89.57 | 90.80 | −1.35% | 89.39 / 89.58 | ~21 bp |
| BWXT | 140.265 | 141.81 | −1.09% | 140.01 / 140.34 | ~23 bp |
| EVGO | 1.330 | 1.36 | −2.20% | 1.33 / 1.34 | 1 cent |
| BTC-USD | mark 83,914 | open 83,929 | ~flat | use mark | — |

STRC and SATA are both 1 bp. Liquidity is not a reason to skip them. BITA has not printed today and the book is ~35 bp. STRK's $1 spread fails a small-ticket test versus STRC.

## 2) Research / rotate

Themes checked: bitcoin / hard money, digital credit (STRC/SATA bias), BTC infrastructure (multi-miner), stocks growth (TSLA/SPCX pin), AI stack, energy / nuclear, space. Gold was not re-quoted and stays research-only — no owner buy call, below BTC, not a ticket. Private names are not in the deploy set. Ready watchlist named and marked: GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN, RKLB, STRK, CCJ, BWXT. Deep dives are inside 90 days (oldest 2026-08-04, 51 days). No first buy, so no new dive. BE is `pass` / blocked.

Digest context that does not change the pin: TSLA mild-up on the ZET SCALE Semi order and tonight's 9pm ET Semi Rollout livestream. SPCX down on today's day-105 lockup (about 319–328 million shares). STRC stays near par with the company bid and a declared $0.50 Oct 15 dividend. Nothing in the digest is a Chairman override.

### How capital serves themes now

There is no deployable capital. Buying power is $0.03. Rebuilding ~40% (~$133) into STRC/SATA plus a diversified miner set would sell the 2026-09-22 TSLA and SPCX fills and undo "exit every other equity." On this tape, if that sleeve were reopened, the first dollars would still be **STRC and SATA** (both 1 bp) — not MSTR by habit, and not because miners overlap. After a credit seat, miners would be diversified across MARA, RIOT, CLSK, WULF, and IREN. WULF (+0.37%) and CLSK (+0.14%) being the least-weak is not a reason to lead with them.

The only mechanically legal ticket is a ~$3.86 TSLA→SPCX pin chase (sell ~0.010 TSLA, buy ~0.026 SPCX). That is about 116 basis points of the quote book and **$0.57 wider** than Wednesday's $3.29 close gap because SPCX is down more at the open. Spreads are not the cost (~2 bp each). The trade sells TSLA on Semi Rollout day and buys SPCX into the day-105 unlock, inside the first 30 minutes. A wider gap is the unlock printing, not a new reason to buy it.

Stocks deposits, when they exist, stay **50/50 SPCX + TSLA**. Spot BTC stays at $0 until crypto policy changes. Do not reseat GOOGL, NVDA, PLTR, CCJ, BWXT, RKLB, or BE under the current pin.

### Names chosen

| Symbol | Theme | Action |
|--------|--------|--------|
| TSLA | growth equity | Hold. Do not sell into Semi Rollout day for $3.86. |
| SPCX | space / growth | Hold. Do not buy the day-105 unlock in the first 30 minutes. |

### Names rejected

| Name | Why not this pass |
|------|-------------------|
| STRC, SATA | Preferred first dollars **if** the 40% sleeve reopens. Not this pass: sleeve closed, BP $0.03. **Not** an illiquidity skip — both 1 bp. |
| MSTR, BITA, ASST | Behind STRC/SATA on a reopen. BITA has no print today and a ~35 bp book. |
| MARA, RIOT, CLSK, WULF, IREN | Diversify after a credit seat if the sleeve reopens. Rejected for the Chairman exit and zero cash, **not** for miner overlap. |
| BTC | Crypto sleeve unchanged and empty. Flat mark is not an override. |
| STRK | Ready sibling, junior to STRC, ~136 bp, no residual. |
| GOOGL, AAPL, NVDA, PLTR, AMZN | Ready AI stack. Stocks pin is TSLA/SPCX only. No residual. |
| CCJ, BWXT | Ready nuclear. Same pin. Not a BE substitute. |
| RKLB | Ready space optionality. Do not add a second space name into the SPCX unlock. |
| EVGO | Ready, low priority, show-me. |
| BE | `pass` / blocked. Do not reseat. |
| Gold | Research-only. No owner buy call. Not quoted this pass. |

No new watchlist symbols proposed.

## 3) Thesis / risk / critic

Hold. Do not map the unpaid numeric 40% onto a sale of the Sep 22 fills. Risk: BP $0.03 is under the $1 minimum. The pin gap is a real ticket and both names are liquid; the block is the clock and the two catalysts. Critic sizes the $3.86 chase to zero and sizes any reopen to zero. STRC/SATA under-allocation versus 40% is real and is rebutted only by the Chairman exit plus $0.03 of buying power — not by liquidity, not by MSTR covering credit, and not by miner overlap. Executor places nothing.
