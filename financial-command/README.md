# Financial Command Center

Dual-venue liquidity UI for **Coinbase** (liquid USDC/BTC + manual Morpho/vault/card fields) and **Robinhood** (portfolio / buying power / DCA governor).

Folder path: `financial-command/` (URL-safe). Distinct from `resistance-dashboard/`.

## Launch

From repo root:

```bash
python3 launch.py
# or
python3 financial-command/server.py --port 8000
```

Opens: http://localhost:8000/financial-command/index.html

### APIs (FCC server)

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/treasury` | GET | Latest evaluation |
| `/api/config` | GET/POST | Read/merge-save `treasury/config.json` |
| `/api/refresh` | POST | Re-run evaluation (`{"offline": true}` optional) |

## Data flow

1. Live Coinbase balances + BTC-USD price via CLI.
2. Robinhood from `treasury/snapshots/robinhood_latest.json` (agent MCP).
3. **Coinbase One Card** via YNAB API (`~/.config/ynab/token`) → `treasury/snapshots/one_card_latest.json`.
4. **Personal Expense Sheet** (Google) via gviz CSV → `treasury/snapshots/expenses_latest.json`.
5. Manual Morpho LTV / vault (and optional card override) from `treasury/config.json`.
6. Pure policy in `treasury/policy.py` → `financial-command/treasury_latest.json`.

```bash
python3 treasury/ynab_sync.py      # refresh One Card from YNAB
python3 treasury/expenses_sync.py  # refresh expense sheet
python3 treasury/run_treasury.py   # full evaluation
```

## Panels

- Data quality & completeness (missing app fields, staleness)
- Stress board (LTV, liquid, card, RH, DQ)
- Policy floors + sleeves
- Priority actions (agent vs human)
- Copyable agent brief
- Manual field editor with **Save to config**
