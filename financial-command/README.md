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
   Tabs: **Personal/Essential** = burn; **Fleet** = `fleet_ops` (combined adds funded unique names only; empty-From out); **Collateral** = investments (not burn); **Productive / Consumer Discretionary** = capital targets.
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

## Vercel preview (separate project)

Root of the Vercel project is **this folder** (`financial-command/`). Not FitDash / `resistance-dashboard`.

- **Vercel Authentication ON** (Deployment Protection → Vercel Authentication → all deployments). Project-level; not expressible in `vercel.json`.
- **App-level lock** (Hobby production alias skips SSO): cookie-less GET of `/`, `/api/treasury`, `/capital-flows.html`, `/watchlist.html` → 401 `{"ok":false,"error":"auth_required"}` unless `_vercel_jwt` (SSO cookie) is present. Fail closed if that cookie is missing. `x-vercel-oidc-token` is Function deployment identity and does **not** count as login. Do not invent a bypass secret. Do not treat `VERCEL_OIDC_TOKEN` env as user auth. Do not load `FCC_TREASURY_JSON` this ship. HTML is not a static file on Vercel (`.vercelignore` + dispatcher).
- Read-only. `POST /api/config`, `/api/refresh`, `/api/trade`, `/api/mint` → 403 JSON. No venue API keys on Vercel.
- Always-on banner on Vercel only (hidden on Mac/Pi). Stale if snapshot `as_of` > 6h. Banner does not remove panels.
- Pages: `index.html`, `capital-flows.html`, `watchlist.html` (nav preserved, not glance-only, not thinned).
- Single function: `api/index.py` (1 / 12 Hobby cap). All `/api/*` rewrite there.
- `treasury_latest.json` is **not** a public URL (gitignored + `.vercelignore` + rewrite → 404 JSON). Publish from Mac → protected env `FCC_TREASURY_JSON` (optional: `FCC_CAPITAL_FLOWS_JSON`, `FCC_WATCHLIST_JSON`, `FCC_COACH_JSON`). Do not commit live numbers.
- Pi `server.py` / systemd untouched.

```bash
# from this directory
python3 tests/test_vercel_preview.py
```
