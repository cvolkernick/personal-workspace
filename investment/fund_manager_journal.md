# Fund manager journal

## 2026-07-20 — go-live

- **Policy:** live_autopilot; agentic-only; no trade approval; no max notional.
- **Agentic BP:** ~$8.37 live.
- **Orders:**
  - **BUY MSTR $1.65** market GFD regular_hours — **queued**  
    `id=6a5e9611-650f-4757-a331-ab2e010bc0a9` · `placed_agent=agentic` · qty est. 0.01682
  - **BUY MARA $1.65**, **TSLA $2.50**, **SPCX $2.40** — **rejected**  
    Robinhood: complete investment profile before second trade  
    https://applink.robinhood.com/investment_profile?account_number=674601752&context=second_trade
- **Next:** user completes profile → agent finishes 40/60 deploy → ongoing active management.

## 2026-07-20T21:41:37 — deploy
**Summary:** Bootstrap agentic book toward modernized 40/60 (first order MSTR).
**Book:** NAV $8.37 · BP $8.37
**Weights before (deployed):** BTC-complex None · Stocks None
**Why now:** Go-live after policy + MCP wired; deploy idle cash once (not DCA cadence).
**Why not alternatives:** Avoided open/close scalp style; used small dollar markets on core allowlist only.
**Team:**
- **scout:** observe — NAV ~$8.37 all cash; BP live
- **thesis:** ok — MSTR for digital credit; later MARA + TSLA/SPCX for 40/60
- **risk:** ok — Risk bounded by agentic deposits only
- **critic:** ok — Tiny book; diversification limited by size
- **executor:** execute — Place MSTR $1.65; profile blocked further until cleared
**Actions:**
- BUY MSTR $1.65 [filled] 6a5e9611-650f-4757-a331-ab2e010bc0a9


## 2026-07-21T15:16:13 — deploy
**Summary:** Complete bootstrap after investor profile cleared: MARA + TSLA + SPCX.
**Book:** NAV $8.39 · BP $6.72
**Weights before (deployed):** BTC-complex 1.0 · Stocks 0.0
**Why now:** Second-trade block cleared; residual BP available; not open/close scalp.
**Why not alternatives:** Skipped BITA (wide spreads earlier); preferred MARA for BTC-infra; TSLA/SPCX for stocks sleeve.
**Team:**
- **scout:** observe — MSTR filled; BP ~$6.72
- **thesis:** ok — Need stocks sleeve + more BTC-complex
- **risk:** ok — Still within agentic capital
- **critic:** ok — Accept concentration on 4 names at this size
- **executor:** execute — MARA $1.65, TSLA $2.50, SPCX $2.40 market GFD
**Actions:**
- BUY MARA $1.65 [filled] 6a5f8d2e-f5b3-442b-a37b-8ba3aab75119
- BUY TSLA $2.5 [filled] 6a5f8d3d-0021-44b7-be91-39c8ab3930dd
- BUY SPCX $2.4 [filled] 6a5f8d3d-cb9d-4b74-b78e-eb319db463f9

