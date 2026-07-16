# Coinbase automation feasibility — BTC collateral loan, USDC vault, liquid USDC, Coinbase One cards

**Date:** 2026-07-16  
**Goal:** Map what can be automated via MCP / programmatic APIs vs what stays in-app/manual for the user’s collateralized-BTC → USDC loan → High Yield vault / credit-card policy loop.

> Durable copy for personal-workspace. Evidence/scratch twin: session implementer scratch `coinbase-automation-feasibility.md` + `coinbase-capability-sources.md`.

---

## Executive summary

| Policy target | Automatable today? | One-line reason |
| --- | --- | --- |
| Keep BTC-collateralized retail loan LTV &lt; 50% | **Partially** | In-app **loan protection** can auto top-up collateral near liquidation; **no public API** to read LTV or repay/add collateral. Scheduled agents can only alert (if LTV is observed elsewhere) or trade liquid balances via Advanced Trade. |
| Maximize Coinbase One credit available credit | **Not** (API) / **Partial** (in-app) | Card balance, paydown, and USDC security deposit are **mobile-app only**; use **autopay** + manual deposit management. No card-servicing API. |
| Park excess liquid USDC in High Yield Morpho vault | **Partially** | **Coinbase Lend** vault deposit/withdraw is app-driven. **AgentKit/Morpho MCP** can deposit/withdraw only from a **wallet the agent controls** (separate Base path), not proven for Coinbase-custodied Lend positions. |
| Pull vault/liquid USDC to pay credit card | **Not** (agent) | Card payments: app / autopay only. Liquid USDC **read + convert + trade** via Advanced Trade MCP/CLI **is** automatable. |
| Pull funds to add loan collateral | **Not** (agent write) | Add collateral documented as **mobile app**; loan protection is the native auto path. |
| Liquid USDC balance management (spot) | **Fully** (read/write trade) | Advanced Trade MCP/CLI: balances, convert USDC↔USD, spot orders, portfolio transfers. |

**Bottom line:** A scheduled agent can **fully** manage **liquid Advanced Trade balances** (buy BTC, hold USDC, convert, transfer portfolios). The **policy brain** that rebalances Morpho loan LTV, Coinbase Lend vault, and Coinbase One card **cannot** be closed-loop via public Coinbase APIs today. Best hybrid: **agent monitoring + alerts + liquid-balance prep** + **in-app loan protection + card autopay** + optional **parallel onchain Morpho vault** on a self-custodied Base wallet.

---

## Critical product split (do not conflate)

| Product | Who | Backend | Public programmatic surface |
| --- | --- | --- | --- |
| **Retail crypto-backed USDC loans** | Consumer Coinbase app | Morpho on Base (cbBTC etc. collateral) | **None** documented (app/web repay; app add collateral; in-app loan protection) |
| **Retail USDC Lend (Core / High Yield vaults)** | Consumer Coinbase app | Morpho vaults, Steakhouse curator | **None** for Coinbase-custodied positions; onchain Morpho if agent controls the depositing wallet |
| **Exchange Loans Program** | Institutional Exchange clients | Coinbase Exchange loan APIs | **Full** REST: overview (collateralization %), open, repay principal/interest, assets/options |
| **Advanced Trade spot** | Retail/pro trading | Coinbase brokerage | **Full** via Coinbase CLI/MCP + REST |
| **Coinbase One credit (incl. USDC-secured)** | Retail + Cardless | In-app card servicing | **None** public; autopay in mobile app |
| **Coinbase debit** | Retail card rails | Card network / Coinbase Card | **None** for agent merchant payments |

---

## Capability matrix

| Balance / product | Read access | Write / action access | Recommended automation surface |
| --- | --- | --- | --- |
| **BTC-collateralized USDC loan (retail Morpho)** LTV, principal, collateral | **App/web only** for official LTV health; possible **partial onchain read** if Morpho position address known | Repay / add collateral / open: **app/web** (add collateral: mobile). **Loan protection** (in-app auto top-up). **No** Advanced Trade MCP loan tools | **In-app loan protection** for hard floor near liquidation; agent **alerts** only unless onchain position readable; do **not** use Exchange `/loans/*` |
| **Exchange institutional loan** (if ever eligible) | Exchange API `GET /loans/lending-overview` etc. | Open / repay principal & interest via Exchange REST | Exchange API + institutional keys — **different product** |
| **High Yield / Core USDC Morpho vault (Coinbase Lend)** | App; onchain vault shares if wallet known | Deposit/withdraw in **app**; AgentKit Morpho deposit/withdraw only for **agent wallet** | **App** for Coinbase Lend positions; **AgentKit / Morpho MCP + Base wallet** for parallel self-custody vault strategy |
| **Liquid USDC (spot / Advanced Trade)** | **Yes** — MCP `balance`, portfolios, CLI `coinbase balance` | Convert, spot trade, portfolio transfer — **yes** | **Coinbase MCP/CLI** (connected in this env as `live`) |
| **BTC spot hold (pre-collateral)** | **Yes** — Advanced Trade balances | Buy/sell spot — **yes**; **moving into Morpho collateral** — app | MCP for spot; app for collateral lock |
| **Coinbase One debit spend path** | Card UX; liquid crypto balances via API | Merchant spend: card rails **not** agent-API | Keep liquid USDC via MCP; spend remains card/app |
| **Coinbase One credit — balance & available credit** | **App only** | Paydown, autopay schedule, USDC security deposit sizing: **app only** | **In-app autopay**; agent cannot maximize available credit programmatically |
| **USDC security deposit securing the credit card** | App | Designate/change deposit: app (product rules) | Manual / in-app; no MCP |

---

## Policy automation classification (detail)

### 1. Keep BTC loan LTV under 50%

| Layer | Feasibility |
| --- | --- |
| **Read LTV on a schedule** | **Not** via Advanced Trade MCP. **Maybe** via public Morpho/Base if position is queryable. **Yes** if user (or future API) exports metrics. |
| **Act when LTV rises (repay USDC or add BTC)** | **Not** via MCP. **Yes** in-app manually. **Partial:** enable **loan protection** with trigger set well below 86% (e.g. toward 50–60% if UI allows) so Coinbase auto-adds collateral from free balance once. |
| **Scheduled agent role** | Monitor BTC price + any available LTV feed → notify user; optionally **pre-stage** free BTC/USDC in liquid balances (MCP) so user or loan protection can apply them. |

**Verdict: Partially automatable** (protection + alerts + liquidity staging; not closed-loop agent repay/collateral).

### 2. Maximize Coinbase One credit available credit

- Available credit rises when balance is paid down and/or (for secured product) deposit supports limit.
- Payments and deposit management: **mobile app**; autopay for recurring minimum/statement amounts.
- **No** API to “pay card from USDC and re-deposit excess to vault” in one agent loop.

**Verdict: Not automatable via agent/API**; **partial** via **in-app autopay** only.

### 3. Excess liquidity → High Yield USDC vault

- After reserving cash for card float / loan buffer, surplus should go to High Yield vault.
- Coinbase Lend deposit: **app**.
- Workaround for automation: USDC in a **self-custody Base wallet** → AgentKit/Morpho MCP vault deposit on schedule. That is a **parallel** position, not the in-app Lend balance, unless the agent can sign for Coinbase’s smart wallet (generally not without deliberate setup).

**Verdict: Partially automatable** only with **self-custody Morpho path**; Coinbase Lend path **not** MCP-automatable.

### 4. Vault/liquid → card paydown or collateral top-up when needed

| Destination | Agent can execute? |
| --- | --- |
| Liquid USDC already on Advanced Trade | Hold/convert via MCP — **yes** |
| Withdraw from Coinbase Lend vault | App — **no** API |
| Pay Coinbase One credit | App/autopay — **no** API |
| Add Morpho loan collateral | App / loan protection — **no** agent write API |

**Verdict: Not fully automatable**; **liquid prep** only.

---

## What a scheduled agent / cron **can** do today

**Connected in research session:** Coinbase Advanced Trade MCP (`coinbase`, env `live`) + CLI `coinbase`. Also: github, gmail, google_calendar, google_drive, robinhood_trading, tasks. **Not connected:** CDP CLI MCP, Payments/Agentic Wallet MCP, Morpho MCP, AgentKit MCP.

### Automatable cron/agent jobs

1. **Balance sentinel** — poll balances/portfolios; alert if liquid USDC below reserve or above invest threshold.  
2. **Price / risk sentinel** — BTC-USD via products ticker/candles; approximate LTV only if collateral & principal are maintained in side config (drifts with interest).  
3. **Spot ops** — buy BTC with USDC, convert USD↔USDC, move funds between Advanced portfolios.  
4. **Calendar/email alerts** — LTV check reminders, card statement week.  
5. **If Morpho MCP + controlled wallet** — scheduled vault deposit/withdraw on **that** wallet.

### Not automatable via cron against Coinbase account APIs

- Open/repay retail Morpho loan; set LTV target 50%.  
- Deposit/withdraw Coinbase Lend High Yield vault (custodied path).  
- Read/pay Coinbase One credit; adjust available credit or USDC security deposit.  
- Debit-card merchant payments.  
- Exchange `/loans/*` unless institutional Exchange credentials and eligibility exist.

---

## MCP inventory

### Environment (observed 2026-07-16)

| Server | Status | Relevance |
| --- | --- | --- |
| **coinbase** | **Connected** (Advanced Trade tools; env `live`) | Spot balances, orders, convert, portfolios — **not** loans/vaults/cards |
| robinhood_trading | Connected | Unrelated |
| github, gmail, google_calendar, google_drive, tasks | Connected | Orchestration/alerts |
| robinhood-trading (alt) | Auth failed | N/A |

### External Coinbase-related MCP products

| Product | Install / URL | Operates on |
| --- | --- | --- |
| Coinbase for Agents MCP | `coinbase mcp` / `https://agents.coinbase.com/mcp` | Advanced Trade |
| CDP CLI MCP | `cdp mcp` | CDP wallets & onchain APIs |
| Payments / Agentic Wallet MCP | `npx @coinbase/payments-mcp` | Agentic wallet + x402 |
| CDP Docs MCP | `https://docs.cdp.coinbase.com/mcp` | Docs only |
| AgentKit + MCP extension | AgentKit framework | Morpho deposit/withdraw on agent wallet |
| Morpho MCP | `https://mcp.morpho.org/` | Morpho prepare txs (needs wallet signing) |

---

## Gaps and workarounds

| Gap | Workaround |
| --- | --- |
| No retail loan LTV API | In-app **loan protection**; optional public Morpho monitoring if address known |
| No Coinbase Lend vault API | App for Lend; **or** self-custody Base + AgentKit/Morpho |
| No One Card API | **Autopay** in app; liquid USDC buffer; manual available-credit management |
| Transfer is portfolio-to-portfolio only | External Morpho funding needs Coinbase withdraw UX |
| Exchange loan APIs look complete | **Wrong product** for Morpho retail borrow |

### Realistic hybrid architecture

```
[Cron / agent]
  ├─ Read liquid USDC/BTC (Coinbase MCP) ✅
  ├─ Trade/convert liquid balances ✅
  ├─ Alert on BTC price / reserve breaches ✅
  ├─ (Optional) Morpho vault on self-custody wallet ✅
  ├─ Coinbase Lend High Yield (app) ❌ API
  ├─ Retail loan repay / add collateral (app + loan protection) ❌ API / ✅ protection
  └─ One Card paydown / available credit (app + autopay) ❌ API / ✅ autopay
```

**Recommended policy encoding:**

1. Set **loan protection** with aggressive LTV trigger and pre-funded free BTC/USDC.  
2. Set **card autopay** to statement balance (or chosen amount).  
3. Agent keeps **liquid USDC buffer** (card + loan top-up reserve); vault parking remains manual or self-custody Morpho.  
4. Weekly human checklist: LTV in app, vault allocation, card available credit.

---

## Risks

- Region/account feature gates (Morpho often excl. NY; secured card offer-based).  
- APIs may expand later — re-check Coinbase for Agents supported list.  
- Onchain automation ≠ Coinbase app position control without wallet control.  
- Interest accrual drifts offline LTV models.  
- Research only — no live rebalancing implemented.

---

## Primary sources

- https://docs.cdp.coinbase.com/coinbase-for-agents/overview  
- https://docs.cdp.coinbase.com/get-started/build-with-ai/comparing-agentic-tools  
- https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/loan/get-lending-overview  
- https://help.coinbase.com/coinbase/trading-and-funding/loan/collateral  
- https://help.coinbase.com/en/coinbase/trading-and-funding/loan/loan-payment  
- https://help.coinbase.com/coinbase/trading-and-funding/loan/lending-intro  
- https://help.coinbase.com/creditcard/payment  
- https://help.coinbase.com/creditcard/security-deposit-supported-card  
- https://www.coinbase.com/borrow  
- https://www.coinbase.com/blog/earn-competitive-yields-by-lending-your-usdc  
- https://github.com/coinbase/agentkit (Morpho deposit/withdraw)
