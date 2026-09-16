# Investment Portfolio

> **Liquidity UI:** Open [../financial-command/index.html](../financial-command/index.html) (serve from repo root). Dual-venue treasury, stress, and agent/human actions.

Tracking for crypto, stocks, prediction markets, and other investments.

## Strategy
Weekly DCA (Dollar Cost Averaging)

## Accounts to Track
- Bitcoin (via Coinbase API)
- Prediction markets (Kalshi)
- Stocks (to be added via manual entry)

## Data Sources
- Coinbase API for crypto
- Mercury (bank for transfers to investment accounts)
- Manual entry for stock positions

## Active Positions

### Macro Composition (May 2025)

| Category | Holdings | Approx Weight |
|----------|---------|---------------|
| Semiconductors | ASML, SMTC, AMD, AVGO, NVDA, TSM, CBRS | ~40% |
| Bitcoin | BTC, MSTR, SATA, STRC | ~25% |
| Tech/EV | TSLA, GOOGL | ~10% |
| Nuclear | LEU, CCJ | ~10% |
| Gold | PAXG, GLDM | ~5% |
| Tech/Cloud | CRWV | ~5% |
| Pending IPO | SPCX | ~5% |

## Portfolio Assessment

### Strengths
- Heavy semiconductor exposure (NVIDIA, AMD, ASML, TSM, CBRS) — aligned with AI/semi supercycle
- Direct + indirect Bitcoin exposure (BTC + MSTR)
- Diversified across subsectors

### Risks
- High correlation — tech sell-off hits most positions
- No defensive/low-beta plays (utilities, consumer staples)
- Uranium (CCJ) adds nuclear exposure but thin

### Macro Thesis
Seems to bet on AI hardware demand + Bitcoin. Aggressive growth portfolio.

## Tracking
- Prices to be fetched via exchange APIs where available
- Bitcoin via CoinGecko or Coinbase API
- US stocks: manual entry or via Yahoo Finance (unofficial)
## Fund developments digest series

> Migrated from issue #716 (closed 2026-09-16). Decision: Grok SIC, 2026-09-13.

The daily agentic fund-developments digest is **human-only** — it is Naka's capital-allocation loop, not engineering work.

- File each dated digest issue **without** `status:ready` and **with** `human-only`.
- Never route the digest series through the eng-gate dispatcher: a Ready stamp would staff Forge/Nakatoshi as eng WIP and put a capital-allocation digest on the Buzz Board every cycle.
- Notify Naka on the issue and/or in `#agentic-finance` — not via `[pi-dispatch]` eng-gate pickup.
- `[Agents] Naka: …` titles remain the convention for actual Naka **eng** work (FCC/product).

Background: #715 (2026-09-13) was mis-stamped `status:ready` at creation and got auto-staffed to Forge; Grok confirmed restaff to Naka for that cycle only. The bug was the Ready stamp, not the owner matcher.
