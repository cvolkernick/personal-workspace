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

### Same-origin Fleet + Horizon (installed PWA)

The PWA manifest is origin-scoped (`scope: "/"`, `start_url: "/"`). Cross-port `http://host:8796/` / `:8795/` links leave the standalone window and are mixed-content on the Tailscale HTTPS origin.

FCC `server.py` reverse-proxies:

- `/fleet/` → Auto Fleet backend (`auto-fleet/server.py`, Pi unit `auto-fleet.service`, bind `:8796`)
- `/horizon/` → Horizon Macro (`research/horizon/server.py`, Pi unit `horizon-dashboard.service`, bind `:8795`)

Nav emits `/fleet/` and `/horizon/` — no `http://` deep-links. Direct `:8795` / `:8796` stay as LAN-debug fallbacks; they are not linked from FCC.

Root-absolute assets from those apps (`fetch("/api/…")`, `<script src="/app.js">`) are rewritten under the prefix so they do not escape to FCC (same bug class as #677, in reverse).

### APIs (FCC server)

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/treasury` | GET | Latest evaluation |
| `/api/config` | GET/POST | Read/merge-save `treasury/config.json` |
| `/api/refresh` | POST | Re-run evaluation (`{"offline": true}` optional) |
| `/api/btc-network` | GET | Bitcoin network hashrate + difficulty (mempool.space, 6h cache) |
| `/api/runway` | GET | Cash-flow forecast. Default min-buffer from `policy.min_liquid_buffer_usd`. `?threshold=` overrides per-view. |
| `/fleet/*` | * | Reverse-proxy → Auto Fleet (`127.0.0.1:8796`). Same-origin for the installed PWA. |
| `/horizon/*` | * | Reverse-proxy → Horizon Macro (`127.0.0.1:8795`). Same-origin for the installed PWA. |

## Data flow

1. Live Coinbase balances + BTC-USD price via CLI.
2. Robinhood dual-account snapshot (`primary` margin + **Agentic**) via Pi Grok MCP `robinhood-trading` → `treasury/rh_sync.py` → `robinhood_latest.json` (SoT host **prism**; see `treasury/deploy/RH_PRODUCER.md`).
3. **Coinbase One Card** via YNAB API (`~/.config/ynab/token`) → `treasury/snapshots/one_card_latest.json`.
4. **Personal Expense Sheet** (Google) via CSV export-by-gid → `treasury/snapshots/expenses_latest.json`.
   Tabs: **Personal/Essential** = burn; **Fleet** = `fleet_ops` (combined adds funded unique names only; empty-From out); **Collateral** = investments (not burn); **Productive / Consumer Discretionary** = capital targets.
   **Forecast tab "Buffer target"** is the monthly planning floor. Runway's daily min-buffer is `treasury/config.json` `policy.min_liquid_buffer_usd` (currently $200, matching the sheet). They are intentionally separate; disagreement is visible drift on Runway, never silent.
5. Manual Morpho LTV / vault (and optional card override) from `treasury/config.json`.
6. **Solana** public RPC + Jupiter prices → `treasury/snapshots/solana_latest.json` (whitelist SOL / USDC / JR-strcUSX; JR is not HY).
7. Pure policy in `treasury/policy.py` → `financial-command/treasury_latest.json`.
8. **Bitcoin network** hashrate + difficulty via mempool.space → `treasury/snapshots/btc_network_latest.json` (public; Pi can fetch).

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
