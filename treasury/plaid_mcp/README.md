# plaid-bank — read-only Plaid MCP (X Money)

Self-hosted MCP + JSON API wrapping **Plaid Trial** for Chris’s four X Money checkings. Audit SoT next to YNAB. **No transfers, payments, or write APIs.**

Issue: [#549](https://github.com/cvolkernick/personal-workspace/issues/549)

## Scope

| last4 | Space |
|------:|-------|
| 2201 | Main |
| 0895 | Auto Fleet |
| 3326 | Collateral |
| 4867 | Utilities |

**Out of scope:** EveryDay Checking – **8680** (Navy Federal). ACH/transfer. First-party SpaceXAI Plaid connector.

Tools: `get_balances`, `list_accounts`, `list_transactions`. Amounts: dollars + integer cents, `ROUND_HALF_EVEN`, `as_of` UTC.

## Layout

| Path | Role |
|------|------|
| `python3 -m treasury.plaid_mcp` | HTTP MCP (`/mcp`) + JSON (`/api/balances`) |
| `python3 -m treasury.plaid_mcp.server --stdio` | stdio MCP (local Grok) |
| `python3 -m treasury.plaid_mcp.link_server` | One-shot **localhost** Plaid Link (human in browser) |
| `~/.config/plaid-bank/` | Secrets (mode 600). Never commit. |

## Secrets (never chat / never git)

```
~/.config/plaid-bank/client_id
~/.config/plaid-bank/secret
~/.config/plaid-bank/access_token
~/.config/plaid-bank/mcp_token
~/.config/plaid-bank/env          # optional EnvironmentFile for systemd
```

Or env: `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ACCESS_TOKEN`, `PLAID_ENV` (`sandbox` \| `production`), `PLAID_MCP_TOKEN`.

Trial is live Production with a 10-Item cap. One X Money login = one Item.

## Human Link (once)

1. Sign up at `dashboard.plaid.com` (US Trial). Copy client id + secret into `~/.config/plaid-bank/` (mode 600). `PLAID_ENV=production`.
2. On the Mac (browser required):

```bash
python3 -m treasury.plaid_mcp.link_server
```

3. Complete Plaid Link for **X Money** only. Access token is written to `~/.config/plaid-bank/access_token` (not printed).
4. Copy the four files to Pi `prism-agent@192.168.100.98:~/.config/plaid-bank/` and `chmod 600`.

Link is **not** an MCP tool. Agents cannot create Items or exchange public tokens.

## Run

```bash
# loopback, no bearer required
python3 -m treasury.plaid_mcp --host 127.0.0.1 --port 18801

# mesh/LAN — refuses to bind 0.0.0.0 without PLAID_MCP_TOKEN
PLAID_MCP_TOKEN=… python3 -m treasury.plaid_mcp --host 0.0.0.0 --port 18801
```

| URL | Auth |
|-----|------|
| `GET /healthz` | none (probe) |
| `POST /mcp` | Bearer `PLAID_MCP_TOKEN` when set |
| `GET /api/balances` | Bearer |
| `GET /api/accounts` | Bearer |
| `GET /api/transactions?days=14` | Bearer |

## Pi unit

```bash
bash deploy/install_remote.sh prism-agent@192.168.100.98 --only plaid-bank
```

Unit: `plaid-bank-mcp.service` · port **18801** · Tailscale `http://100.67.114.2:18801/healthz`

**Do not** public port-forward `:18801`. Grok Bot custom MCP that needs a public URL is a separate authenticated tunnel (Chris order) — not this PR.

## Restart / rotate / re-Link

```bash
# status / logs
ssh prism-agent@192.168.100.98 'systemctl --user status plaid-bank-mcp --no-pager'
ssh prism-agent@192.168.100.98 'journalctl --user -u plaid-bank-mcp -n 80 --no-pager'

# restart
ssh prism-agent@192.168.100.98 'systemctl --user restart plaid-bank-mcp'

# rotate MCP bearer (does not touch Plaid Item)
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
# write to ~/.config/plaid-bank/mcp_token on Pi (chmod 600), restart unit

# rotate Plaid secret: dashboard.plaid.com → new secret → replace ~/.config/plaid-bank/secret → restart
# re-Link if ITEM_LOGIN_REQUIRED / Item expired: run link_server on Mac, scp access_token, restart
```

## FCC / Nakatoshi

```bash
curl -sS -H "Authorization: Bearer $PLAID_MCP_TOKEN" \
  http://192.168.100.98:18801/api/balances
```

Do not dual-write YNAB. This is an independent venue SoT.

## Tests

```bash
python3 -m unittest discover -s treasury/tests
```
