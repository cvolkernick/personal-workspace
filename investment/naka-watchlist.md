# Naka fund watchlist

- **Owner:** Naka. **Maintained by:** Naka (edit this file directly; one line per name).
- **Advisory only.** Nothing here authorizes or triggers a trade.
- **Role:** this file is the source of the daily monitored set for Naka's fund monitoring.
- **Pins:** target pins remain defined **only** by [`consider_share.json`](./consider_share.json). Listing a ticker here does not pin, size or approve it.
- **Last refreshed:** 2026-10-15. Tickers only: no quantities, balances or prices.
- A name marked `unverified` has not been confirmed against a primary source; verify before relying on it.

## A. Core (copied from `consider_share.json`)

| Ticker | Status | Why |
|---|---|---|
| SPCX | pinned | Stocks-sleeve pin (space / SpaceX exposure). |
| TSLA | pinned | Stocks-sleeve pin (growth equity, autonomy). |
| BE | watch only | Not pinned; exited by owner 2026-09-03 and blocked for new seats. Listed only to watch the energy-equipment read-across. |

## B. Current holdings (read live 2026-10-15 from the agentic fund account and Chris's Robinhood portfolio)

| Ticker | Why |
|---|---|
| SPCX | Pinned core holding (see A). |
| TSLA | Pinned core holding (see A). |
| MSTR | Strategy common; BTC-treasury parent of the preferreds below. |
| STRC | Strategy variable-rate preferred; digital-credit income seat. |
| STRK | Strategy 8% convertible preferred; digital-credit sibling of STRC. |
| SATA | Strive variable-rate daily-dividend preferred; digital-credit income seat. |
| IBIT | iShares Bitcoin Trust ETF; spot-BTC exposure. |
| MARA | Bitcoin miner; miner-to-compute optionality. |
| RIOT | Bitcoin miner. |
| CLSK | Bitcoin miner. |
| WULF | Bitcoin miner / HPC hosting pivot. |
| IREN | Bitcoin miner / AI-compute pivot. |
| USDC | Stablecoin cash balance (crypto account); cash-like, no thesis monitoring. |

Chris's separate Robinhood portfolio showed no additional tickers on this read. Refresh this section from live holdings; do not hand-copy from older notes.

## C. 11/11 energy and nuclear review names

`XLE` is explicitly **excluded** from this review. `ASML` is a watchlist name.

| Ticker | Why |
|---|---|
| CCJ | Cameco; review baseline (uranium and fuel cycle, Westinghouse stake). |
| BWXT | BWX Technologies; naval nuclear components and fuel, microreactor work. |
| VST | Vistra; power producer with nuclear fleet, AI-load power demand. |
| CEG | Constellation Energy; largest US nuclear fleet, hyperscaler PPAs. |
| NLR | VanEck Uranium and Nuclear ETF; basket benchmark for the group. |
| ASML | ASML; watchlist name (semiconductor lithography, AI-stack supplier), not an energy name. |
| OKLO | Oklo; advanced-fission developer. `unverified`: not confirmed as one of Chris's 10/3-10/4 names. |
| SMR | NuScale Power; small modular reactors. `unverified`: not confirmed as one of Chris's 10/3-10/4 names. |

Open item: add any other nuclear names Chris listed on 10/3-10/4 once confirmed (not recovered when this file was seeded).

## D. AI-stack names we track

| Ticker | Why |
|---|---|
| NVDA | AI infrastructure and GPUs; co-lead of the AI stack. |
| GOOGL | Cloud, Gemini foundation models and ad cash engine. |
| AMZN | AWS cloud, AI capex and power deals. |
| PLTR | Application and harness layer; government and enterprise AI. |
| AAPL | Devices and services; AI purity behind GOOGL and NVDA. |

## E. Macro and the 11/11 review add-on

- Macro items are covered by Meridian's daily file at `investment/brief/macro/YYYY-MM-DD.md`; this file does not duplicate them.
- **SATS** (Strive 15% Series A perpetual preferred): on the 11/11 review list. `unverified`: announced by Strive on 2026-10-13 as a proposed IPO and not yet listed, so confirm the trading ticker once it lists (the symbol SATS has been used by another issuer).

## F. Other names carried in the monitored set

| Ticker | Why |
|---|---|
| RKLB | Rocket Lab; space-economy watchlist name (Neutron launch cadence). |
| EVGO | EV fast charging; low-priority energy and electrification watch. |
| HYPD | Hyperion DeFi; Hyperliquid treasury-equity watch. |
| ASST | Strive common; BTC-treasury issuer of SATA and SATS. |
| BITA | Digital-credit name on the BTC-complex list. `unverified`: ticker not re-confirmed against a primary source. |
| BTC | Spot bitcoin, as a reference asset for the BTC sleeve. |

Private names (Anduril, Saronic, Boom Supersonic, Sail Research, Glow, Figure AI) are tracked in [`private_watchlist.json`](./private_watchlist.json), not here.
