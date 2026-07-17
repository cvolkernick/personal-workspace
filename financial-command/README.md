# financial-command

Dual-venue liquidity UI for **Coinbase** (liquid USDC/BTC + manual Morpho/vault/card fields) and **Robinhood** (portfolio / buying power / DCA governor).

Named distinctly from `resistance-dashboard/` and any future generic dashboards.

## Launch

From repo root:

```bash
python3 treasury/run_treasury.py    # refresh live CB + RH snapshot evaluation
python3 launch.py                   # http://localhost:8000/financial-command/index.html
```

Or double-click `../open-command-center.command`.

## Data flow

1. `treasury/run_treasury.py` reads Coinbase via CLI (`coinbase balance --paginate`), Robinhood from `treasury/snapshots/robinhood_latest.json` (written by agent MCP or tests), and manual fields from `treasury/config.json`.
2. Pure policy in `treasury/policy.py` scores stress and priority actions.
3. Writes `financial-command/treasury_latest.json` for the UI.

## What is automatable

| Action | Who |
| --- | --- |
| Read liquid CB balances | Agent / CLI |
| Read RH BP / portfolio | Agent MCP → snapshot file |
| DCA pause/allow decision | Agent (policy) |
| Bridge recommend | Recommend-only (human) |
| Morpho LTV / vault / One Card | Human in Coinbase app |

See `../investment/treasury-action-items.md`.
