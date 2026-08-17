# Financial Command Center

Dual-venue liquidity UI for **Coinbase** (liquid USDC/BTC + manual Morpho/vault/card fields) and **Robinhood** (primary margin portfolio / BP / DCA + **Agentic MCP** tradable account).

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
2. Robinhood dual-account snapshot (`primary` margin + **Agentic**) via MCP → `treasury/rh_sync.py` → `robinhood_latest.json`.
3. **Coinbase One Card** via YNAB API (`~/.config/ynab/token`) → `treasury/snapshots/one_card_latest.json`.
4. **Personal Expense Sheet** (Google) via CSV export-by-gid → `treasury/snapshots/expenses_latest.json`.
   Tabs: **Personal** + **Fleet** = burn; **Collateral** = investments (not burn); **Productive / Consumer Discretionary** = capital targets.
5. Manual Morpho LTV / vault (and optional card override) from `treasury/config.json`.
6. **Solana** public RPC + Jupiter prices → `treasury/snapshots/solana_latest.json` (whitelist SOL / USDC / JR-strcUSX; JR is not HY).
7. Pure policy in `treasury/policy.py` → `financial-command/treasury_latest.json`.

```bash
# Agent refreshes RH via MCP, then:
python3 treasury/rh_sync.py --stdin < envelope.json
python3 treasury/ynab_sync.py      # refresh One Card from YNAB
python3 treasury/expenses_sync.py  # refresh expense sheet
python3 treasury/solana_sync.py    # refresh Solana whitelist book
python3 treasury/run_treasury.py   # full evaluation
```

**Robinhood Agentic:** Grok MCP `robinhood-trading` → `https://agent.robinhood.com/mcp/trading`. Orders only on the agentic account (`agentic_allowed=true`).

## UI (redesign)

- Sticky header: Refresh + overall status + feed freshness
- **Do now**: top actions (You / Agent / App only), expandable full list
- **At a glance** KPIs
- Cash & credit buffers; upcoming bills
- Collapsed: brokerage, YNAB txs, capital targets, settings

## Panels

- Data quality & completeness (missing app fields, staleness)
- Stress board (LTV, liquid, card, RH, DQ)
- Policy floors + sleeves
- Priority actions (agent vs human)
- Copyable agent brief
- Manual field editor with **Save to config**
