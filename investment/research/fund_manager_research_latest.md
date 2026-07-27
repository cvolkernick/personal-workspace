# Fund manager research — mid-session review

**As of:** 2026-07-27 (mid-session ET)  
**Account:** agentic ••••1752 only  
**Process:** Uniform research/rotate (size-invariant)  
**Live NAV:** ~$173.47 · cash/BP ~$0.04 · **pending deposits $25**

## Executive findings

1. **Free-capital signal was real** (rules path / BP poll: cash/BP ~$25.54 on stale snapshot), but **live MCP scout** found residual **BP/cash $0.04** after concurrent agentic market fills at **18:30:22Z**.
2. Concurrent fills (~**$25.50** total): **CLSK $5** (new allowlist miner), **MSTR $5.20**, **TSLA $7.60**, **SPCX $7.70** — roughly **40/60** of the cash just deployed.
3. **Deployed sleeves remain ~40% BTC-complex / ~60% stocks** (in band). No forced rebalance.
4. **Pending deposit $25** is **not** buying power yet — next deploy pass when settled.
5. **Watchlist stays monitor-only:** BE into 7/28 earnings; GOOGL / AAPL / NVDA lack required deep-dives.
6. **This pass Executor: HOLD** (dust BP). Research/rotate still completed; alternatives documented.

## Book inventory (post concurrent fills)

| Symbol | Sleeve | Theme | Role |
|--------|--------|-------|------|
| **MSTR** | btc_digital_credit | Digital credit / BTC proxy | Core credit; topped +$5.20 |
| **BITA** | btc_digital_credit | BTC yield / premium income | Hold (not topped this cash) |
| **MARA** | btc_digital_credit | BTC infra miner | Hold |
| **IREN** | btc_digital_credit | BTC infra / energy-intensive | Hold |
| **CLSK** | btc_digital_credit | BTC infra miner | **NEW** ~$5 allowlist diversifier |
| **TSLA** | stocks_growth | Growth / energy-adjacent | Topped +$7.60 |
| **SPCX** | stocks_growth | Growth equity | Topped +$7.70 |

**Unheld core allowlist:** STRC, SATA, ASST, RIOT, WULF, BTC (spot).  
**Watchlist:** BE, GOOGL, AAPL, NVDA (all deep-dive gates).

## Names considered → chosen / rejected

### Chosen (hold / affirm post-fill book)
| Name | Why |
|------|-----|
| MSTR | Liquid digital-credit centerpiece; concurrent top-up on relative strength OK |
| BITA | Explicit BTC yield product; keep sleeve completeness even if not topped today |
| MARA | Pure miner; keep |
| IREN | Miner/power-infra diversifier vs MARA-only |
| CLSK | Unheld allowlist miner; concurrent add avoids pure held-only inertia; small ticket |
| TSLA | Primary growth name for 60% sleeve |
| SPCX | Second core stocks name |

### Rejected (with reasons)
| Name | Why not now |
|------|-------------|
| **BE** | Deep-dive = monitor_no_buy; **earnings 2026-07-28 AMC**; valuation/beta |
| **GOOGL** | Watchlist; **no deep-dive completed** — first buy blocked |
| **AAPL** | Watchlist; **no deep-dive completed** — first buy blocked |
| **NVDA** | Watchlist; **no deep-dive**; concentration/valuation critic gate (even on soft day) |
| **STRC / SATA / ASST** | Digital-credit overcrowding vs MSTR+BITA at ~$173 NAV |
| **RIOT / WULF** | Miner overcrowding — already MARA+IREN+CLSK |
| **BTC spot on RH** | Prefer equity vehicles on agentic RH; CB for spot/vault |
| **Forced sells / rotate** | In-band; cash-account settlement risk; churn |

## Theme mapping (how capital serves themes *now*)

| Theme | Expression in book | Gap? |
|-------|-------------------|------|
| Bitcoin / hard money | Via MSTR/BITA/miners (not RH spot) | Optional metals empty — OK |
| Digital credit / BTC yield | MSTR + BITA | Covered |
| BTC infrastructure | MARA + IREN + **CLSK** | Covered (3 miners — enough at this NAV) |
| Growth equity | TSLA + SPCX | Covered core |
| AI stack (broad) | Via TSLA/SPCX only | Watchlist GOOGL/AAPL/NVDA **monitor** until deep-dive |
| Energy opportunistic | Via mining/power infra; BE watchlist | No BE until post-print |

## Team votes

| Role | Vote | Note |
|------|------|------|
| Scout | ok | Live MCP: NAV~$173 BP$0.04 pending$25; 7 names; concurrent fills logged |
| Thesis | ok | Affirm 40/60 7-name theme book; no watchlist buys |
| Risk | ok | Dust BP — no trade; pending not spendable |
| Critic | ok | HOLD; block BE/AI first buys; no further miners; re-research when pending settles |
| Executor | hold | Zero new orders this pass |

## Concurrent fill blotter (observed)

| Symbol | Notional | Order id | Status |
|--------|----------|----------|--------|
| CLSK | $5.00 | `6a67a3be-1e89-4a65-8e5d-e0482dbdf8d1` | filled |
| TSLA | $7.60 | `6a67a3be-5924-440f-9b58-37db43c18147` | filled |
| SPCX | $7.70 | `6a67a3be-e7d7-4b45-a9d4-e7ebb2268fe3` | filled |
| MSTR | $5.20 | `6a67a3be-fae3-403f-a493-715dcf48f270` | filled |

## Next actions

1. **When pending $25 → BP:** run full research/rotate again (size-invariant; do not auto top-up held only).
2. **Post 2026-07-28:** re-evaluate BE after Q2 print (or re-run deep-dive).
3. **Optional research queue:** position-deep-dive for **NVDA** / **GOOGL** / **AAPL** before any first buy.
4. Owner feedback optional after this brief.

## Size-invariant process check

- Considered held **and** unheld allowlist + full watchlist.
- Reject reasons written for each non-chosen name.
- Did **not** skip research because book is small.
- Only constraint on action count was **BP = $0.04** (execution), not simplified strategy.
