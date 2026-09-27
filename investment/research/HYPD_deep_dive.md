# HYPD deep dive (Hyperion DeFi, Inc.) — owner-add 2026-09-27

**Report run:** 2026-09-27
**Process:** Emulated `/position-deep-dive symbol=HYPD` (host workflow not invoked this session). Identity verified before the watchlist write (commit `88c16a0`).
**Coverage:** Partial — primary sources (SEC 10-Q / 8-K, company press releases, Robinhood read-only market data). No sell-side coverage exists; post-6/30 token count and buyback execution are not yet disclosed (see Coverage limitations).
**Status after this dive:** `ready` — eligible for Thesis/Risk **proposal** only; **not** an order
**Sleeve if owned:** `stocks_growth` (non-BTC digital-asset equity) · **Priority:** low · **Core allowlist:** no
**On watchlist:** yes · **Held agentic:** no
**Agentic book frame (RH agentic `treasury/snapshots/robinhood_latest.json` `as_of` 2026-09-27 09:57 ET):** total **$331.39** · BP **$0.03** · positions **TSLA 0.448384 sh + SPCX 1.104498 sh** only. `auto_buy` false. `min_trade_notional_usd` $1.
**Chairman standing order 2026-09-22 (`investment/consider_share.json`):** stocks sleeve = **50% SPCX / 50% TSLA**; `stocks_sleeve_only_symbols` [TSLA, SPCX]; `never_theme_gap_other_equities` true. Unchanged by this dive.
**Scope:** Agentic Robinhood only. **Research ≠ order. Not Executor authority.**
**Owner policy 2026-08-04:** watchlist entry ⇒ auto deep-dive ⇒ status `ready` for allocation *consideration* each deploy (unless explicit `pass`).
**Owner ask:** Chris 2026-09-27 — add HYPD ("Hyperion") to the Agentic Fund public watchlist.

**Identity check:** Robinhood `search HYPD` → exactly one instrument, *Hyperion DeFi, Inc. Common Stock* (instrument 43844a2d-6090-47f9-b6f0-ca51cbd067d2). SEC EDGAR CIK **1682639**: registrant **HYPERION DEFI, INC.**, ticker HYPD, exchange Nasdaq; former name **EYENOVIA, INC.** (2016-08-24 → 2025-06-27). 10-Q Q2'26 cover: common stock, $0.0001 par, **Nasdaq Capital Market**. Delaware; HQ 3090 Nowitzki Way, Dallas TX. HYPD maps to exactly one security; this is the listed HYPE-treasury company.

---

## Executive findings

1. **HYPD is a listed HYPE treasury, not an operating DeFi business yet.** Ex-Eyenovia (ophthalmic) re-purposed June 2025 to accumulate Hyperliquid's native token HYPE and build on it (staking, validator with Kinetiq/MAVAN, HYPE lending/HAUS deals, yield enhancement). Legacy biotech wound down by 6/30/26; remaining Optejet IP sold to Arctic Vision July 2026. Gross HYPE tokens **2,042,113** at 6/30/26 (1.31M a year earlier, +56%).

2. **Economics are ~all HYPE beta.** Q2'26 GAAP revenue **$357,693** vs GAAP net income **$30.95M** (diluted EPS $0.92) — the income is mark-to-market on HYPE. Operating layer: Adjusted Gross Profit **$1.15M** vs Opex ex-SBC **$2.34M**; Adjusted net operating cash flow **−$2.12M**. Sept 8 guide: Q3'26 core operating earnings **$0.0–0.5M** (first break-even), FY'26 Adj. GP **$7–8M** (from $5–7M). Even at guide, ops are rounding error vs a ~$190M HYPE stack.

3. **Headline discount to NAV is real but smaller than it looks.** Basic market cap **$62.8M** (RH, $4.04) vs company NAV **$134.2M** at 6/30 (HYPE $64.95). Re-marked to HYPE **$92.39** (RH mark 2026-09-27 10:03 ET) holding 6/30 token count: NAV ≈ **$190.3M**. But common holders share it with **15.71M** as-converted preferred shares and **32.6M** $3.25 warrants (in the money at $4.04). Derived price/NAV: **~0.66×** (common + preferred, no warrants) · **~0.80×** (treasury-stock method on ITM warrants) · **~0.87×** (full warrant exercise incl. $107.4M cash). Management said 20–30% discount on 2026-09-08 — consistent with the diluted view.

4. **Dilution is the dominant equity risk.** Common shares **8.68M (12/31/25) → 15.54M (8/10/26), +79%** via ATM (Cantor/Chardan), May '26 public offering ($9.3M net), PIK preferred dividends paid in common, RSUs and preferred conversions. Overhang still outstanding: 5,235,897 Series A preferred (3:1 convertible, participating, $50.74M liquidation preference) and **33.8M** warrants. The new **$20M buyback** (12 months) is authorized, not executed; 6/30 cash + stablecoins were **$11.8M**.

5. **No thesis seat and no capacity.** North star is **BTC first** + digital credit + AI + energy + space. HYPE is a non-BTC L1 token; HYPD is not BTC-complex and not an AI/energy/space name. Chairman 2026-09-22 locks the stocks sleeve to TSLA/SPCX only. Agentic BP **$0.03 < $1** min trade. **Ready / no size.**

### Conclusions (actionable)

| Decision | Conclusion |
|----------|------------|
| Stay on watchlist? | **Yes** (owner-named) |
| Promote to core allowlist? | **No** |
| Status | **`ready`** (homework done; proposal-eligible) |
| Starter size *now*? | **No** — Chairman TSLA/SPCX stocks pin + BP $0.03 + no thesis theme + dilution overhang |
| Auto-buy? | **Never** |
| Theme fit | **Weak** — non-BTC digital asset; does not fit BTC & digital credit (40%) or the AI/energy/space lenses of the 60% |
| Relative preference | Behind every held/ready name for scarce stock dollars; would need a Chairman pin change *and* an owner theme call for non-BTC crypto before any dollar |
| Priority | **low** |
| Next deep-dive refresh | Q3'26 print (est. ~2026-11-02, third-party/unconfirmed) testing break-even guide; buyback execution; preferred/warrant restructuring; HYPE ±25% from $92.39; HYPD ±25% from $4.04; Chairman pin change; or 90d age |

**One-line for the fund team:**
*HYPD is **ready / low / no size** — a ~$63M Nasdaq micro-cap HYPE treasury trading at ~0.7–0.9× diluted NAV, whose returns are HYPE beta with a heavy preferred/warrant overhang; no BTC-thesis seat and the Chairman's TSLA/SPCX pin blocks any stocks dollar.*

---

## Frame / policy

| Field | Value |
|-------|--------|
| Symbol | **HYPD** (Nasdaq Capital Market) |
| Legal name | Hyperion DeFi, Inc. (formerly Eyenovia, Inc.; SEC CIK 1682639; DE) |
| Theme | `digital_assets` (HYPE / Hyperliquid treasury equity) |
| Sleeve | `stocks_growth` if ever owned (non-BTC-primary equity) — **not** `btc_digital_credit` |
| Added | 2026-09-27 by **owner** |
| Core allowlist | No |
| Blocking pins | consider_share.json Chairman 2026-09-22 TSLA50/SPCX50; `stocks_sleeve_only_symbols` [TSLA, SPCX] |

---

## Market / instrument

| Metric | Print | Source |
|--------|-------|--------|
| Last trade | **$4.04** (2026-09-25 close, 3:59 PM ET) | Robinhood `get_equity_quotes` |
| Prior close | $4.17 (2026-09-24, official SIP) | Robinhood |
| 6/30/26 close | $3.14 → **+28.7%** to $4.04 | Robinhood `get_equity_historicals` |
| 52-week | **$2.34** (2026-07-28) – **$11.89** (2025-10-02) | Robinhood `get_equity_fundamentals` |
| Market cap (basic) | **$62.8M** on 15.54M sh | Robinhood |
| Float | 12.80M sh | Robinhood |
| Avg volume | ~611K sh/day (30d) · ~552K (2w) ≈ $2.2–2.5M/day | Robinhood |
| P/B (GAAP) | 0.61 — GAAP book understates (LSTs at low-water-mark carrying value) | Robinhood; 8-K ex99.1 |
| Analyst coverage | **None** returned | Robinhood `get_equity_analyst_ratings` |
| HYPE-USD mark | **$92.39** (2026-09-27 10:03 ET) vs $64.95 at 6/30 → **+42.2%** | Robinhood `get_crypto_quotes` |
| Next earnings | est. ~2026-11-02 (third-party, not company-confirmed) | public.com |

HYPD lagged HYPE since 6/30 (+28.7% vs +42.2%) even after the Sept 8 guide raise + buyback (+8.5% that day, $3.30 → $3.58).

---

## Fundamentals (Q2'26, unaudited)

| Line | Q2'26 | Source |
|------|-------|--------|
| GAAP revenue / gross profit | $357,693 | 10-Q; RH `get_financials` |
| GAAP net income | $30,950,963 (6M: $39.79M) | 10-Q |
| Diluted EPS | $0.92 (17.07M wtd diluted sh) | 8-K ex99.1 |
| Adjusted Gross Profit (non-GAAP) | $1.15M (+20% q/q) — staking $527K, yield enh. $334K, DeFi monetization $158K, ecosystem $90K, validator $42K | 8-K ex99.1 |
| Opex ex-SBC (non-GAAP) | $2.34M | 8-K ex99.1 |
| Adj. net operating cash flow | −$2.12M | 8-K ex99.1 |
| Cash (GAAP) / cash + stablecoins | $9.64M / $11.76M | 10-Q; 8-K |
| Total assets / liabilities / equity | $113.14M / $11.20M / $101.93M | 10-Q |
| Debt | Avenue loan principal $8.51M (8%, half PIK; interest-only to 2027-01-31; matures 2028-07-01) | 10-Q |
| Gross HYPE tokens / holdings | 2,042,113 / $132.6M | 8-K ex99.1 |
| Company NAV (non-GAAP) | $134.2M | 8-K ex99.1 |
| Prior quarters net income | Q3'25 +$6.63M · Q4'25 −$39.77M · Q1'26 +$8.84M | RH `get_financials` |

Guidance (2026-09-08): Q3'26 Adj. GP $2.00–2.50M; opex ex-SBC $2.00–2.25M; core operating earnings $0.00–0.50M; Adj. net op. CF −$0.50M to +$0.25M; FY'26 Adj. GP $7–8M. $20M repurchase authorized for up to 12 months (10b-18 / 10b5-1 permitted; may be suspended any time).

### Derived NAV math (this dive — estimates, not company figures)

Inputs: 6/30 NAV $134,226,478 at HYPE $64.95; 2,042,113 gross HYPE; HYPE $92.3895; HYPD $4.04; common 15,539,434 (8/10); Series A 5,235,897 × 3 = 15,707,691; warrants 32,615,381 @ $3.25 + 350,000 @ $4.00 (6/30).

| View | Shares | NAV/share | Price / NAV |
|------|--------|-----------|-------------|
| Re-marked NAV (6/30 tokens × $92.39) | — | **$190.3M** | mkt cap $62.8M basic |
| Common + as-converted preferred | 31.25M | $6.09 | **~0.66×** |
| + ITM warrants, treasury-stock method | 37.63M | $5.06 | **~0.80×** |
| + full warrant exercise (+$107.4M cash) | 64.21M | $4.64 | **~0.87×** |

Ignores: post-6/30 token changes, opex, ATM, buyback, RSUs (~1.3M unvested), OTM warrants, preferred liquidation preference mechanics, tax. Directionally: the discount compresses sharply once the capital structure is counted.

---

## Thesis fit

**Northstar:** Bitcoin & hard money first; digital credit; AI; energy; space. HYPD fits **none** of the named lenses. It is a leveraged-to-HYPE equity wrapper on a non-BTC L1.

**Bull case (real):**
- Cheapest listed HYPE exposure on an NAV basis (sub-1× even diluted) with a $20M buyback against a ~$63M cap and a management pledge to improve the capital structure.
- Ops inflecting: Adj. GP +162% (Q3'25 → Q2'26), opex −46%, Q3'26 guided to core break-even; 40–50% of Adj. GP earned in cash/stablecoins.
- Real Hyperliquid positioning: Kinetiq x Hyperion validator (~7M HYPE delegated 7/31/26; Blockdaemon partner), HAUS deals (Skew HIP-4 500K HYPE, Entropy HIP-3 500K, Silhouette 100K), ecosystem equity/token rights (Kinetiq, HyperLend, Silhouette, Skew). Institutional custody tooling (Porto by Anchorage, Ledger).

**Why not (for this book):**
- Not BTC. Owner thesis is *BTC first*; non-BTC tokens have no sleeve. Adding HYPD is a new theme call, not a consider-set addition.
- Chairman 2026-09-22: stocks sleeve TSLA/SPCX only; no theme-gap into other equities.
- Micro-cap + dilution machine: +79% share count in ~7 months, 33.8M warrants, 15.7M as-converted preferred, active ATM.

---

## Risks (material)

1. **HYPE price.** 8-K shows the pattern: Q4'25 HYPE $45.2 → $25.4 produced a −$39.8M quarter. HYPD is levered beta to one token.
2. **Capital-structure overhang.** $3.25 warrants are ITM at $4.04; preferred is participating and convertible 3:1 with a $50.74M liquidation preference; PIK dividends are paid in common.
3. **Ongoing dilution.** ATM remains the stated funding plan (10-Q). Buyback may never be executed at size ($11.8M cash + stablecoins at 6/30).
4. **Self-custody / on-chain risk.** Anchorage does not support HyperCore staking, so staked HYPE sits in self-custodied wallets; HAUS counterparties (e.g., USDH sunset ended the Native Markets/Felix deals in June 2026) and LST/DeFi protocol risk.
5. **Micro-cap liquidity.** ~$2–2.5M/day; wide quoted spreads outside RTH (quote at 2026-09-25 8:00 PM ET: bid $3.50 / ask $5.00).
6. **GAAP noise.** LSTs carried at low-water mark; net income swings with HYPE, not operations. Non-GAAP NAV / Adj. GP are company-defined and unaudited.

---

## Coverage limitations

- **Unavailable:** HYPE token count and NAV after 6/30/26 (next disclosure = Q3'26 print); buyback shares repurchased to date; Q3'26 actuals; sell-side estimates/targets (none on Robinhood); confirmed Q3 earnings date.
- **Headline only:** 2026-09-24 PR (supports DoubleZero Edge Hyperliquid data feeds) — body not reviewed; no financial impact assumed.
- **Stale vendor text:** Robinhood fundamentals description still lists Optejet / Laguna Hills CA / Health Technology; SEC filings supersede (Dallas TX; biotech wound down).
- Derived NAV table is this dive's arithmetic on sourced inputs, not a company metric.

---

## Critic

Owner-add is correct process; do not confuse "consider" with "buy." Critic weighed an explicit `pass` (no thesis theme, Chairman pin, dilution) but the policy default is `ready` once homework is done unless the verdict is explicitly negative on the *name*. The blockers here are **book-level** (pin, theme, BP), not a finding that HYPD is uninvestable, so **ready / low / no size**. Any future dollar requires, in order: (a) Chairman lifts or amends the TSLA/SPCX stocks pin, (b) owner names a non-BTC digital-asset theme, (c) Q3'26 print confirms core break-even and the buyback actually retires shares, (d) residual-after-floors or BP ≥ min_trade. Do not treat the basic-share NAV discount as the value case.

---

## Synthesize

Owner wants HYPD on the consider set — done. **Ready / low / no size / not core.** Each deploy must name it and **reject with reasons** while the Chairman pin stands. **Kill / refresh triggers:** Q3'26 print; buyback execution or capital-structure action; HYPE ±25% from $92.39; HYPD ±25% from $4.04; pin change; 90-day age.

---

## Sources

- Robinhood MCP (read-only), 2026-09-27: `search` HYPD; `get_equity_quotes` HYPD; `get_equity_fundamentals` HYPD; `get_financials` HYPD (quarterly); `get_equity_historicals` HYPD 2026-06-26 → 2026-09-25 daily; `get_equity_analyst_ratings` HYPD (none); `get_crypto_quotes` HYPE-USD
- SEC EDGAR submissions, CIK 1682639 (name, former name EYENOVIA, INC., ticker, exchange): https://data.sec.gov/submissions/CIK0001682639.json
- 10-Q for quarter ended 2026-06-30 (filed 2026-08-13): https://www.sec.gov/Archives/edgar/data/1682639/000110465926095181/hypd-20260630x10q.htm
- 8-K ex99.1, Q2'26 results (2026-08-12): https://www.sec.gov/Archives/edgar/data/1682639/000110465926094849/tm2622908d1_ex99-1.htm
- Press release 2026-09-08, raised guidance + $20M repurchase: https://ir.hyperiondefi.com/news-events/press-releases/detail/319/hyperion-defi-raises-guidance-and-announces-20m-share-repurchase-program (also GlobeNewswire https://www.globenewswire.com/news-release/2026/09/08/3357577/0/en/hyperion-defi-raises-guidance-and-announces-20m-share-repurchase-program.html)
- IR home (news list incl. 2026-09-24 DoubleZero Edge headline): https://ir.hyperiondefi.com/
- Earnings date estimate: https://public.com/stocks/hypd/earnings
- Repo: `investment/consider_share.json` (Chairman 2026-09-22), `investment/fund_manager.json` (limits, allowlist), `treasury/snapshots/robinhood_latest.json` (agentic `as_of` 2026-09-27 09:57 ET)
