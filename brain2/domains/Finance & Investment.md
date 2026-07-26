# Finance & Investment

**Execution:** `treasury/`, `financial-command/`, `investment/`  
**Hub:** [[00 Home - B2 Hub]] · **Bets:** [[Strategy & Bets]]

## Dual-venue liquidity (treasury)

Bridge model: **Coinbase** (BTC collateral loan, High Yield vault, One Card, liquid USDC) ↔ **Robinhood** (equity/margin, DCA) via USDC.

Policy ideas (tune in `treasury/config.json`, not secrets here):

- Card available-credit comfort floors
- Loan LTV comfort band (target well under liquidation; e.g. comfort &lt; 50% vs ~86% liq)
- RH buying-power floors
- Weekly human checklist: real LTV in app vs config, vault liquidity, card credit, live refresh

**UI:** Financial Command Center (`financial-command/`, typically port 8000) · Orchestra aggregates status.

## Investment thesis (high level)

- Weekly **DCA** discipline
- Macro tilt: AI hardware / semiconductors + Bitcoin + energy/nuclear
- Categories seen in portfolio maps: semis, BTC stack, tech/EV, nuclear, gold, cloud

Detailed positions and live balances stay in dashboards and brokers — B2 holds **policy and thesis**, not account dumps.

## Data sources (conceptual)

- Coinbase Advanced Trade / CLI adapters
- Robinhood (MCP / snapshots)
- YNAB for card/checking lines where wired
- Manual Morpho/vault fields when APIs do not cover them

## Related

- [[Strategy & Bets]] — Energy, Bitcoin, AI legs
- [[Workflow & Projects]] — protect & push before risky ops
- [[Personal Workspace Map]] — ports and launchers
