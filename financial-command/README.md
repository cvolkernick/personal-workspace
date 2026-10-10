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

### Bind address

FCC listens on `127.0.0.1:8000` (`FCC_BIND_HOST`, default). Tailscale Serve
proxies to that address, so the tailnet path keeps working. `/horizon/*` still
proxies to Horizon at `127.0.0.1:8795` (it does not follow `HORIZON_BIND_HOST`).

`FCC_BIND_HOST=0.0.0.0` listens on all interfaces again. An explicit `--host`
wins over the env var. If the unit's `ExecStart` still passes `--host 0.0.0.0`,
remove that flag or the env var does not apply.

```bash
mkdir -p ~/.config/systemd/user/financial-command.service.d
printf '[Service]\nEnvironment=FCC_BIND_HOST=127.0.0.1\nEnvironment=PRISM_PUBLIC_BASE=https://<tailnet-magicdns-host>\n' \
  > ~/.config/systemd/user/financial-command.service.d/bind.conf
# Rollback: Environment=FCC_BIND_HOST=0.0.0.0
systemctl --user daemon-reload
systemctl --user restart financial-command.service
```

`PRISM_PUBLIC_BASE` is the HTTPS origin with no path: `https`, a real DNS host,
no userinfo, port, query, or fragment. `prism_public.py` is the only checker
(FCC, orchestra, and Daily Brief all import it). `https://prism.example.ts.net`
is documentation only; the `example` label is rejected and is never used as a
redirect target. If the var is unset or invalid, FCC logs `ERROR:` on stderr
at startup and serves `pwa.js` with an empty origin so an installed HTTP app
is not redirected. Under systemd (`INVOCATION_ID` set) that startup exits
non-zero. On prism set the var to the Tailscale Serve hostname so the PWA
install check and the bounce target match the cert.

Daily Brief publish reads `DAILY_BRIEF_PUBLIC_BASE` (`https://host` or
`https://host/horizon` only). If that var is set but invalid, publish does
not fall through to `PRISM_PUBLIC_BASE`. If both are unset or invalid, publish
still exits 0 and prints the relative path, plus an `ERROR:` warning on
stderr. The CLI does not exit just because the base is missing, including
when `INVOCATION_ID` is set. SSH does not see a systemd drop-in, so set the
vars in the shell that runs publish as well.

### PWA install origin

Install the desktop/phone app from **Tailscale HTTPS only** (`PRISM_PUBLIC_BASE`).
Until that env is a valid origin, there is no canonical host and `pwa.js` does
not redirect.

Plain HTTP on a non-canonical host stays a browser fallback. Those origins serve a `display: browser` manifest and do not register a service worker, so Chromium will not treat them as the installed app (every page would show "Not Secure"). Loopback (`http://127.0.0.1:8000`) is a secure context and stays installable for local tests. Direct LAN `:8000` is closed unless `FCC_BIND_HOST=0.0.0.0`.

An already-installed HTTP app (standalone display-mode) is redirected to the HTTPS origin only when `PRISM_PUBLIC_BASE` is valid.

### Same-origin Fleet + Horizon (installed PWA)

The PWA manifest is origin-scoped (`scope: "/"`, `start_url: "/"`). Cross-port `http://host:8796/` / `:8795/` links leave the standalone window and are mixed-content on the Tailscale HTTPS origin.

FCC `server.py` reverse-proxies:

- `/fleet/` → Auto Fleet backend (`auto-fleet/server.py` on the `work/treasury` pin, Pi unit `auto-fleet.service`, bind `:8796`)
- `/horizon/` → Horizon Macro (`research/horizon/server.py`, Pi unit `horizon-dashboard.service`, `127.0.0.1:8795`)

Nav emits `/fleet/` and `/horizon/` — no `http://` deep-links. Horizon's own port is loopback unless `HORIZON_BIND_HOST=0.0.0.0`. FCC does not link to a LAN IP or `:8795`.

Root-absolute assets from those apps (`fetch("/api/…")`, `<script src="/app.js">`) are rewritten under the prefix so they do not escape to FCC (same bug class as #677, in reverse).

### APIs (FCC server)

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/treasury` | GET | Latest evaluation |
| `/api/config` | GET/POST | Read/merge-save `treasury/config.json` |
| `/api/refresh` | POST | Re-run evaluation (`{"offline": true}` optional) |
| `/api/btc-network` | GET | Bitcoin network hashrate + difficulty (mempool.space, 6h cache) |
| `/api/runway` | GET | Cash-flow forecast. Default min-buffer from `policy.min_liquid_buffer_usd`. `?threshold=` overrides per-view. |
| `/brief` | GET | Daily Brief newspaper, newest edition (#1091, see `BRIEF.md`) |
| `/brief/YYYY-MM-DD/am\|pm` | GET | Edition permalink |
| `/brief/archive` | GET | Edition archive by Eastern date |
| `/api/brief/latest` | GET | Newest edition JSON |
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
6. **Solana** public RPC + Jupiter prices → `treasury/snapshots/solana_latest.json` (whitelist SOL / USDC).
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
