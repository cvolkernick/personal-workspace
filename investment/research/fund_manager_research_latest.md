# Fund manager research — mid-session 2026-07-27

**As of:** 2026-07-27 ~18:31Z (14:31 ET)  
**Account:** agentic ••••1752 only  
**Process:** Uniform research/rotate (size-invariant)  
**Live NAV:** ~$173.52 · **cash/BP:** ~$0.04 · **pending_deposits:** $25 (watch next cycle if settles to BP)

## Scout snapshot

| Field | Value |
|-------|--------|
| Held | MSTR, BITA, MARA, IREN, **CLSK** (new), TSLA, SPCX |
| Deployed sleeve mix | ~**40.2%** BTC-complex / ~**59.8%** stocks (in ±5% band) |
| Idle cash | ~$0.04 (below min trade $1) |
| Theme coverage | Digital credit (MSTR, BITA) · miners/infra (MARA, IREN, CLSK) · growth (TSLA, SPCX) |
| Gaps | Pure AI mega-cap (GOOGL/AAPL/NVDA watchlist) · energy (BE watchlist) · extra digital credit (STRC/SATA/ASST) |

### Live market values (RH quotes × qty)

| Symbol | Sleeve | Theme | ~$ | % NAV |
|--------|--------|-------|-----|-------|
| MSTR | btc | digital credit | ~28.7 | 16.6% |
| BITA | btc | BTC yield | ~15.1 | 8.7% |
| MARA | btc | miner | ~11.1 | 6.4% |
| IREN | btc | miner/power infra | ~9.8 | 5.6% |
| CLSK | btc | miner | ~5.0 | 2.9% |
| TSLA | stocks | growth | ~59.5 | 34.3% |
| SPCX | stocks | growth | ~44.3 | 25.5% |

### Concurrent deploy (same review window)

At ~18:30:22Z agentic orders **filled** (~$25.50 idle cash that triggered rules `need_llm`):

| Symbol | Side | Notional | Order id | Theme |
|--------|------|----------|----------|-------|
| CLSK | buy | $5.00 | `6a67a3be-1e89-4a65-8e5d-e0482dbdf8d1` | BTC infra miner (new name) |
| TSLA | buy | $7.60 | `6a67a3be-5924-440f-9b58-37db43c18147` | stocks growth |
| SPCX | buy | $7.70 | `6a67a3be-e7d7-4b45-a9d4-e7ebb2268fe3` | stocks growth |
| MSTR | buy | $5.20 | `6a67a3be-fae3-403f-a493-715dcf48f270` | digital credit |

**Sleeve of deploy:** ~$10.20 BTC-complex (40%) + ~$15.30 stocks (60%). Residual cash ~$0.04.

## Research / rotate (required)

### Names considered
MSTR, BITA, MARA, IREN, CLSK, RIOT, WULF, STRC, SATA, ASST, TSLA, SPCX, BTC (RH), BE, GOOGL, AAPL, NVDA; held-only top-up of prior 6 without new names.

### Names chosen (post-state / ratify concurrent deploy)
| Name | Theme map | Why |
|------|-----------|-----|
| **MSTR** | digital credit | Core liquid BTC-corporate proxy; top-up keeps credit centerpiece |
| **CLSK** | BTC infrastructure | Allowlist miner; diversifies beyond MARA+IREN at small ticket |
| **TSLA** | growth / AI-energy-adjacent | Primary stocks sleeve name |
| **SPCX** | growth equity | Second core stocks name; balances TSLA concentration slightly |

### Names rejected (with reasons)
| Name | Why not now |
|------|-------------|
| **BITA top-up** | Valid BTC-yield expression; concurrent path preferred CLSK diversification over yield top-up. Acceptable alternative — not wrong, just not selected this pass |
| **IREN / MARA top-up** | Already sized; third miner CLSK chosen instead of concentrating two |
| **RIOT, WULF** | Valid miners; at ~$174 NAV four+ miners over-concentrates mining beta (MARA+IREN+CLSK already three) |
| **STRC, SATA, ASST** | Digital credit allowlist; MSTR+BITA cover credit/yield; avoid overcrowding small book |
| **BTC spot on RH** | Prefer equity vehicles on agentic RH; CB for spot/vault elsewhere |
| **BE** | Watchlist deep-dive = monitor_no_buy; **earnings 2026-07-28 AMC** — do not buy into print |
| **GOOGL, AAPL, NVDA** | Real **AI stack gap** vs TSLA/SPCX-only stocks sleeve; `deep_dive_required_before_buy` and no completed deep-dive → **not** first-buy this pass. Prefer core for routine deploy. **Next cycle:** schedule `/position-deep-dive` on GOOGL and/or NVDA if stocks capital free |
| **Full held-only inertia** | Explicitly considered; concurrent path **did** add unheld allowlist **CLSK** — process not pure top-up of old 6 |
| **Rotate/sell CLSK same day** | Churn + spread vs $5 size; CLSK is core allowlist; no thesis break → hold |

### How new capital best serves themes *now*
Idle cash violated “cash is unallocated.” Best fit: maintain **40/60**, prefer **core allowlist**, allow one **unheld** miner for infra diversification (CLSK) rather than only topping the largest held names. AI pure-plays wait on deep-dive; energy waits post-BE print.

## Team votes (this pass)

| Role | Vote | Note |
|------|------|------|
| Scout | ok | Live MCP: NAV~$173.5 BP~$0.04; 7 names; concurrent 4 fills ~$25.50; sleeves in band |
| Thesis | ok | Ratify 40/60 deploy (MSTR+CLSK / TSLA+SPCX); no further orders |
| Risk | ok | BP dust; min notional; no leverage; agentic-only; pending_deposits $25 → re-scout if BP rises |
| Critic | ok (size caution) | CLSK = third miner vs prior clean-slate “MARA+IREN enough” — accept at 2.9% NAV; block BE into earnings; block watchlist AI without deep-dive; **do not reverse** fills (churn) |
| Executor | hold | No new MCP orders this pass; residual BP <$1; prior fills already agentic |

## Decision
**HOLD** residual capital. Book post-deploy is on-target. No rotate.

## Next cycle hooks
1. If **pending_deposits $25** becomes spendable BP → full research/rotate again (not auto held-only).  
2. After **BE 7/28 earnings** → re-run deep-dive / status.  
3. Optional **GOOGL/NVDA** deep-dives for AI stack under stocks sleeve when capital available.  
4. If miner sleeve >~15% NAV or fourth miner proposed → critic pressure to consolidate.
