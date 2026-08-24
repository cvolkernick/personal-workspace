# Fund manager research — 2026-08-24 (~10:36 ET)

**As of:** 2026-08-24 ~14:36Z (~10:36 ET, regular hours; user-run mid-session review)  
**Account:** agentic ••••1752 only (`674601752`)  
**Process:** Uniform research/rotate (size-invariant) — Scout → Thesis → Risk → Critic → Executor  
**Live NAV (RH total):** **$230.27**  
**cash/BP:** **$0.09** · **min_trade:** $1.00 · **pending_deposits field $25 is NOT spendable** (already deployed 09:50 ET)  
**MCP:** robinhood-trading HTTP MCP with stored OAuth (this Grok session’s MCP tools were disconnected / auth-required; Scout used the same venue via authenticated MCP). No orders.  
**Owner prefs:** elevate **STRC/SATA** in BTC-complex deploys; **multi-miner diversification good**; watchlist ready names in consider set  
**Trigger:** User-run fund-manager review. Rules path at 10:31 ET flagged “free capital $25.09” off a **stale Pi snapshot** (as_of 13:29Z, pre-fill). Live MCP is authoritative.

## 1) Scout snapshot

| Field | Value |
|-------|--------|
| Held | MSTR, **STRC**, **SATA**, BITA, MARA, IREN, CLSK, TSLA, SPCX, **GOOGL** |
| Deployed mix (qty × RH last ~14:36Z) | **~40.4% / 59.6%** BTC-complex / stocks — in ±5% band |
| Idle cash | **$0.09** (below min $1) |
| Theme coverage | Digital credit (**STRC, SATA**, MSTR, BITA) · miners (MARA, IREN, CLSK) · growth (TSLA, SPCX) · AI mega-cap (**GOOGL**) |
| Remaining gaps | ASST unheld · RIOT/WULF optional miners · NVDA/AAPL/BE/PLTR/EVGO/AMZN watchlist · energy (BE) |

### Live marks (qty × RH last ~14:36Z)

| Symbol | Sleeve | Theme | ~$ | % of equity | Δ vs 8/21 close |
|--------|--------|-------|-----|-------------|-----------------|
| TSLA | stocks | growth | 69.36 | 30.1% | −1.7% |
| SPCX | stocks | growth | 53.67 | 23.3% | −1.7% |
| MSTR | btc | digital credit | 37.18 | 16.2% | **+6.2%** |
| BITA | btc | BTC yield | 17.76 | 7.7% | +2.6% |
| GOOGL | stocks | AI stack | 14.08 | 6.1% | +1.1% |
| MARA | btc | miner | 11.51 | 5.0% | **+6.3%** |
| IREN | btc | miner/power | 11.17 | 4.9% | −3.0% |
| STRC | btc | digital credit | 6.02 | 2.6% | +0.6% |
| SATA | btc | digital credit | 5.00 | 2.2% | ~0% (near par $100.01) |
| CLSK | btc | miner | 4.43 | 1.9% | +2.3% |

**Sub-sleeve (BTC complex ~$93.06):**
- Credit/yield (MSTR+BITA+**STRC+SATA**): ~$65.96 (~71% of complex) — **STRC/SATA seats exist** (~$11.02)
- Miners (MARA+IREN+CLSK): ~$27.11 (~29% of complex) — multi-miner stack OK
- STRC last $96.77, spread ~$0.07  
- SATA last $100.01, spread ~$0.01 (near par)
- BITA spread ~$0.24 — still the thinner credit name
- BTC-USD (Coinbase) ~$79,675 — context only; no RH spot BTC ticket this pass

**Watchlist ready:** BE, GOOGL (**held**), AAPL, NVDA, PLTR, EVGO, AMZN (dive 2026-08-24)  
**Private (not deployable):** ANDURIL, SARONIC, BOOM, ORNN (context only)

**Stale-snapshot trap:** Pi `robinhood_latest.json` as_of 13:29Z still showed cash $25.09 and no STRC/SATA/GOOGL. Live MCP: cash/BP **$0.09**, 10 names including this morning’s fills. Do **not** treat pending_deposits $25 as free capital. Rules “need team/LLM $25.09” at 14:31Z was that stale file.

## 2) Research / rotate (REQUIRED)

### Names considered
MSTR, BITA, MARA, IREN, CLSK, RIOT, WULF, **STRC**, **SATA**, ASST, TSLA, SPCX, BTC (RH), BE, GOOGL, AAPL, NVDA, PLTR, EVGO, AMZN; held-only top-up of TSLA/SPCX/MSTR/GOOGL/MARA; forced rotate sells; re-spend of stale $25.

### Names chosen (this pass)

| Name | Theme map | Action | Why now |
|------|-----------|--------|---------|
| **—** | — | **HOLD** | No deployable cash. Mix in band. Morning deploy already filled the binding gaps (STRC/SATA + GOOGL). Today’s MSTR/MARA bounce is not a rotate signal. |

### Names rejected *for action this pass*

| Name / path | Why not **now** |
|-------------|-----------------|
| **Re-spend $25 / pending_deposits / stale Pi cash $25.09** | Already filled 09:50 ET. Live BP $0.09. NAV $230.27 does not include another $25. |
| **Held-only TSLA/SPCX/MSTR/GOOGL/MARA top-up** | Path-dependent inertia. TSLA still ~30% of equity. MSTR +6% / MARA +6% today is not a reason to add the largest names. |
| **STRC / SATA add** | Seats exist (~$11.0). Owner bias is *presence + preferential residual*, not 40% all-credit. Skip because **no residual dollars**, not because credit is “covered.” |
| **ASST** | +9% today; secondary vs preferred SATA; more vol. No capital. Do not chase. |
| **MARA / IREN / CLSK add** | Miner sleeve already 3 names. Skip *this* pass because **no capital**, not “miner overlap.” |
| **RIOT, WULF** | Valid diversifiers — **not** rejected for overlap. Eligible on next BTC-shaped residual (STRC/SATA seats exist). Blocked only by $0.09. RIOT last $20.62 / WULF $15.91, liquid. |
| **BTC spot on RH** | Prefer equity/credit on agentic RH; CB for spot (~$79.7k). |
| **GOOGL add** | First seat filled this morning (~$14). No residual. |
| **NVDA** | Ready / co-lead AI; **event risk** — FY27 Q2 **confirmed 2026-08-26 AMC** (est. EPS $2.07, actual none). Last $210.01 (−2.2%). Block new size into print. |
| **AAPL** | Ready; owner ranks behind NVDA/GOOGL/BE. Last $313.16. |
| **BE** | Ready; best energy/AI-power expression. Last $197.78 (−1.8%), spread ~$0.44. Harsh multiple / beta. Energy gap remains for *next* stocks residual if energy is the sleeve being filled. |
| **PLTR** | Ready, medium priority, high multiple / gov gates. Last $175.51 (−2.5%). |
| **EVGO** | Ready, low-priority show-me Superchargers. Last $1.49. |
| **AMZN** | Ready (dive 2026-08-24). Last $261.47. Still **no size**: dust capital; second hyperscaler vs already-held GOOGL. |
| **Forced rotate sells** | Sleeves in band; no thesis break; cash-account settlement risk on same-day STRC/SATA/GOOGL lots. |

### How new capital would best serve themes *now* (no new capital)

Idle **$0.09** cannot fill min $1. **If** a deposit lands before NVDA 8/26:

1. **BTC-complex residual:** STRC/SATA seats exist — miner diversifier (**RIOT/WULF**) is eligible; small STRC/SATA top-up remains valid as preferential residual vs cash, not a required add. Do **not** default to MSTR because it is the largest held credit name (and is up +6% today).
2. **Stocks residual:** energy gap (**BE**) or wait for NVDA post-print; do **not** default to TSLA/SPCX/GOOGL top-up without beating BE/NVDA/AMZN on that pass. TSLA is already ~30% of equity.
3. Do **not** park residual in cash — STRC/SATA remain the preferred idle-capital expression inside the 40%.

## 3–5) Thesis / Risk / Critic

| Role | Vote | Note |
|------|------|------|
| Scout | ok | Live MCP NAV $230.27; BP $0.09; 10 names including STRC/SATA/GOOGL; sleeves 40.4/59.6 in band; ~10:36 ET regular hours; Pi snapshot was stale $25.09 |
| Thesis | ok (**hold**) | Binding gaps filled this morning. No free capital. Next dollar: miners RIOT/WULF *or* STRC/SATA residual in 40%; BE vs post-NVDA in 60% |
| Risk | ok (**hold**) | Agentic cash only; $0.09 < $1; no leverage; NVDA 8/26 AMC event risk; AMZN dive current; pending_deposits ignored; same-day lots not sold |
| Critic | ok (**hold**) | Block treating pending $25 / stale Pi cash as spendable. Block held-only default (incl. MSTR/MARA bounce chase) for *next* capital. Force STRC/SATA only when deploying *new* BTC-complex dollars — seats already exist. Miner skip is capital, not overlap. Block NVDA into print. |
| Executor | **hold** | No place/cancel. Venue reachable via stored OAuth HTTP. Dust cannot meet min $1. |

## 6) Decision
**HOLD** on agentic ••••1752. Residual cash ~$0.09. No orders.

### Next cycle hooks
1. **NVDA** — re-evaluate after **2026-08-26 AMC** print (confirmed; est. $2.07).
2. **BE** — energy gap remains; consider on next free *stocks* capital if energy is the sleeve being filled.
3. **RIOT / WULF** — eligible miner diversifiers on next BTC-shaped residual (STRC/SATA seats now exist).
4. **AMZN** — `ready`; still behind GOOGL/NVDA for scarce residual.
5. **Ops:** overwrite local + Pi `robinhood_latest.json` with this live MCP book so rules/bp_poll stop seeing phantom $25.09.
6. If another deposit lands, re-open the full consider set — **not** held-only.
