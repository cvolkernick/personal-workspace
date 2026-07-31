# Fund manager research — mid-session 2026-07-31 (team full review, ~11:58 ET)

**As of:** 2026-07-31 ~16:13Z  
**Account:** agentic ••••1752 only  
**Process:** Uniform research/rotate (size-invariant)  
**Live NAV (agentic RH total):** ~$177.91  
**cash/BP:** ~$0.04 · **min_trade:** $1.00  
**Equity (Yahoo live marks):** ~$170.56 · NAV+cash Yahoo ~$170.60  
**RH snapshot as_of:** 2026-07-31T08:43:58Z (**~7h stale** — `rh_refresh` failed this cycle; MCP auth)  
**MCP (this Grok session):** robinhood-trading tools **unavailable** (auth required; search_tool empty) — Executor cannot place  
**Owner prefs (2026-07-27):** elevate **STRC/SATA** in BTC-complex deploys; **multi-miner diversification good**  
**Trigger:** mid-session fund-manager review (need_llm on free capital any cash/BP>0 dust residual)

## Scout snapshot

| Field | Value |
|-------|--------|
| Held | MSTR, BITA, MARA, IREN, CLSK, TSLA, SPCX |
| Deployed sleeve mix | **Policy 40% / 60%** BTC-complex / stocks · Yahoo live **~39.5% / 60.5%** of equity — **in ±5% band** |
| Weights of NAV (policy) | BTC ~39.2% · stocks ~58.9% · cash ~0.02% |
| Idle cash | ~$0.04 (below min trade $1) |
| Theme coverage | Digital credit (MSTR, BITA) · miners/infra (MARA, IREN, CLSK) · growth (TSLA, SPCX) |
| Gaps | **STRC/SATA unheld** (elevated digital credit) · ASST unheld · RIOT/WULF optional · pure AI mega-cap · energy (BE) |

### Live market values (agentic qty × Yahoo marks ~11:58 ET)

| Symbol | Sleeve | Theme | ~$ | % of equity (approx) |
|--------|--------|-------|-----|----------------------|
| MSTR | btc | digital credit | 26.94 | ~15.8% |
| BITA | btc | BTC yield | 14.63 | ~8.6% |
| MARA | btc | miner | 10.98 | ~6.4% |
| IREN | btc | miner/power infra | 9.84 | ~5.8% |
| CLSK | btc | miner | 5.01 | ~2.9% |
| TSLA | stocks | growth | 59.79 | ~35.1% |
| SPCX | stocks | growth | 43.37 | ~25.4% |

**Sub-sleeve notes (BTC complex ~$67.40 Yahoo):**
- Digital credit/yield (MSTR+BITA): ~$41.57 (~62% of complex) — **no STRC/SATA seat**
- Miners/infra (MARA+IREN+CLSK): ~$25.83 (~38% of complex) — multi-miner stack OK per owner pref
- Unheld digital credit quotes: STRC ~$89.02 · SATA ~$97.09 · ASST ~$11.06
- Unheld miner quotes: RIOT ~$20.75 · WULF ~$17.74
- Watchlist quotes: BE ~$209.80 · GOOGL ~$351.73 · AAPL ~$301.48 · NVDA ~$196.28
- BTC-USD ~$62,666

## Research / rotate (required)

### Names considered
MSTR, BITA, MARA, IREN, CLSK, RIOT, WULF, **STRC**, **SATA**, ASST, TSLA, SPCX, BTC (RH), BE, GOOGL, AAPL, NVDA; held-only top-up path; forced rotate sells (credit/miners → STRC/SATA).

### Names chosen (this pass — HOLD book)

| Name | Theme map | Stance |
|------|-----------|--------|
| **MSTR** | digital credit | Hold; liquid flagship credit proxy |
| **BITA** | BTC yield / digital credit | Hold |
| **MARA, IREN, CLSK** | BTC infrastructure miners | Hold; multi-miner diversification intentional |
| **TSLA, SPCX** | growth equity | Hold; core stocks sleeve |

### Names rejected *for action this pass* (with reasons)

| Name / path | Why not **now** |
|-------------|-----------------|
| **Any buy** | BP/cash $0.04 < min $1; no spendable free capital |
| **STRC, SATA (buy now)** | Preferred for **next** BTC-complex deploy per owner pref — **not** rejected on thesis; blocked only by zero deployable cash + MCP down this session |
| **forced rotate → STRC/SATA** | Considered; deferred — sleeves in band, prefer free-capital path first; no thesis break on held credit/miners; cash-account settlement risk on sell-to-buy |
| **ASST** | Secondary digital credit; prefer STRC/SATA first when capital returns |
| **RIOT, WULF** | Valid multi-miner diversifiers — **not** rejected for “overlap.” Skip this pass for capital only |
| **BTC spot on RH** | Prefer equity vehicles on agentic RH; CB for spot/vault elsewhere |
| **BE** | Prior dive 2026-07-23 `monitor_no_buy`; Q2 print beat; price strong (~$210). Still **not first-buy ready** until **post-print deep-dive refresh** + quorum. Residual dust cannot size. |
| **GOOGL, AAPL, NVDA** | Real AI stack gap; `deep_dive_required_before_buy` and no completed deep-dive → not first-buy ready |
| **Held-only inertia as default** | Explicitly rejected as strategy: next free capital must re-run full consider list with STRC/SATA priority in BTC sleeve |

### How new capital best serves themes *now* (and next deploy)

**This pass:** no capital → HOLD.

**When free BP/cash ≥ $1 (next deposit / poll):**
1. Maintain ~**40/60** of deploy notional.
2. Within BTC-complex leg: allocate a **meaningful share to STRC and/or SATA** (owner elevated digital credit) — **not** MSTR-only by habit. MSTR/BITA may still receive residual credit capital.
3. Miner leg: multi-miner OK (existing MARA/IREN/CLSK; RIOT/WULF eligible if diversifying further — do **not** block on “already have a miner”).
4. Stocks leg: prefer core **TSLA/SPCX** unless a watchlist AI deep-dive is complete and quorum OK.
5. Do **not** default to “only top up largest held.”
6. **BE:** re-evaluate only after **post-print deep-dive** (earnings out 7/28; strong print does not auto-buy).

### Critic overrides of prior weak rationales
- Prior “STRC/SATA illiquid/overcrowding vs MSTR+BITA” is **insufficient** under 2026-07-27 owner prefs unless a **strong** liquidity/structure rebuttal is logged with ticket size evidence.
- Prior “RIOT/WULF miner overlap” is a **false risk block** for sleeve diversification; only true concentration vs credit/whole-book, liquidity, or thesis failure may block.
- BE post-print strength ≠ order authority without deep-dive + Risk/Critic.
- MCP outage / stale RH snapshot this session is an **execution/ops constraint**, not a reason to skip research/rotate.

## Team votes (this pass)

| Role | Vote | Note |
|------|------|------|
| Scout | ok | Snapshot agentic ••••1752: NAV~$177.91 equity 7 names cash/BP $0.04; sleeves ~40/60 in band; RH snapshot ~7h stale; MCP tools unavailable this Grok session |
| Thesis | ok (hold) | Book on target; plan next deploy with STRC/SATA seat in BTC complex; multi-miner retained |
| Risk | ok (hold) | No trade: BP dust; agentic-only; no leverage; TSLA elevated (~35% Yahoo equity) but thesis primary growth name — no forced cut |
| Critic | ok (hold) + process flag | Force HOLD on residual dust. Flag STRC/SATA under-allocation for next capital. Reject false miner-overlap blocks. BE still not first-buy ready post-print without dive. Block undived AI first-buys |
| Executor | hold | No MCP tools this session; even if live, $0.04 cannot fill min notional |

## Decision
**HOLD.** Residual capital dust. Full research/rotate complete. Next free capital → deploy with **STRC/SATA emphasis** in BTC complex + multi-miner allowed.

## Next cycle hooks
1. When **cash/BP ≥ $1** → full research/rotate again; include STRC/SATA in BTC-complex proposal unless strong rebuttal.
2. **BE post 7/28 print** → re-run `/position-deep-dive symbol=BE` before any size-in.
3. Optional **GOOGL/NVDA** deep-dives for AI stack under stocks when capital + time allow.
4. Restore **robinhood-trading MCP** auth/tools in agent sessions so Executor can act unattended; fix `rh_refresh` (currently failing on MCP).
