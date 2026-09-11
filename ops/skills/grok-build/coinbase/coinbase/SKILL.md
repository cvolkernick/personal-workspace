---
name: coinbase
description: Umbrella skill for the coinbase CLI — the agent-first interface to the Coinbase Advanced Trade brokerage. Use when the user mentions Coinbase trading, spot orders, crypto prices, balances, portfolios, converting currencies, or wants to know which coinbase tool/lane to use. Indexes the granular journey skills (trading, market-data, watch, convert, portfolios) and explains auth, env, and MCP-vs-CLI selection.
---

# coinbase CLI

`coinbase` is an agent-first CLI for the Coinbase Advanced Trade brokerage API. All commands output JSON to stdout by default. Errors are JSON to stderr with non-zero exit codes.

These are REAL orders against a live brokerage. Confirm the full plan with the user before placing or arming anything.

## Auth & env

Authenticated commands need a CDP API key configured per environment.

1. https://portal.cdp.coinbase.com/projects/api-keys → **Create API Key**
2. Under **API restrictions → Coinbase App & Advanced Trade**: pick the **Portfolio** to scope the key to, enable **Trade** and **Transfer** (View is default).
3. Under **Advanced Settings**: select **ECDSA** (the brokerage SDK rejects Ed25519).
4. Download the key file, then:
   ```sh
   coinbase env live --key-file path/to/cdp_api_key.json
   coinbase balance          # verify
   ```

Each CDP key is scoped to one portfolio. To operate across portfolios, register one env per portfolio (`live-primary`, `live-trading`) and switch with `coinbase env live-trading` or per-command `-e live-primary`. See `coinbase-portfolios`.

## Permissions

By default agents prompt before each `coinbase` command. To reduce or remove those prompts, allow-list commands in your agent's permission settings. Offer the user two tiers:

- **Read-only** — never prompted for safe reads (prices, balances, history); still prompted before any order or transfer. Good default.
- **Everything** — never prompted at all, including orders and transfers. Confirm explicitly before enabling.

Allow-list rules for each tier:

| Tier | Rules |
| :--- | :--- |
| Read-only | `coinbase balance`, `coinbase products *`, `coinbase orders list`, `coinbase orders get *`, `coinbase orders fills`, `coinbase fees`, `coinbase env`, `coinbase portfolios list`, `coinbase portfolios get *` |
| Everything | `coinbase *` |

How to apply depends on the agent. For Claude Code, add the rules to `permissions.allow` in settings.json (e.g. `Bash(coinbase products *)`) — `~/.claude/settings.json` for all projects or `.claude/settings.json` to share with a team. Other agents have their own allow-list mechanism.

## MCP vs CLI — which lane

Both surfaces wrap the same API. Pick by the shape of the task:

- **MCP** (`coinbase_<resource>_<action>` tools) — for **atomic, structured calls**: place one order, fetch a ticker, list portfolios. Typed args, structured result, no shell needed.
- **CLI** (`coinbase ...` in Bash) — for **composition and streaming**: `--until` conditional watchers, piping (`| jq`), chaining with `&&`, `--paginate`, scripting multi-step flows. Anything MCP can't express as a single call.

Rule of thumb: one structured action → MCP; a pipeline, a stream, or a conditional → CLI.

Add MCP with: `claude mcp add --scope user --transport stdio coinbase -- coinbase mcp`

## Install agent skills

Skills give your agent step-by-step guidance for common Coinbase workflows (trading, market data, watch, convert, portfolios).

```sh
coinbase skills add
```

If non-interactive, pass the directory explicitly:

```sh
coinbase skills add --dir ~/.claude/skills
```

Skills auto-update on future `npm i -g @coinbase/coinbase-cli` upgrades.

## Discovery

```sh
coinbase --help                # all resources + commands
coinbase orders --help         # actions in a resource
coinbase orders create --template   # request body schema
```

## Field & flag syntax

| Syntax | Meaning |
| :---- | :---- |
| `key=value` | String body field |
| `key:=value` | Raw JSON body field |
| `key==value` | Query parameter |
| `a.b.c=value` | Nested body field |
| `@file.json` / `-` | Body from file / stdin |

| Flag | Behavior |
| :---- | :---- |
| `--template` | Print body schema, don't send |
| `--dry-run` | Assemble + print request, don't send |
| `--jq <expr>` | Filter JSON response |
| `--paginate` | Auto-follow `cursor` until exhausted |
| `-e <env>` | Override active environment |
| `-v` | Verbose; print headers (redacted) |

Output: `stdout` = raw JSON (parseable), `stderr` = human status/errors. Exit `0` success, `1` error.

## Journey skills (index)

| Skill | Use when the user wants to… |
| :---- | :---- |
| `coinbase-trading` | place / preview / cancel / edit spot orders, market & limit, stop-loss |
| `coinbase-market-data` | check prices, candles, order book, best bid/ask, ticker |
| `coinbase-watch` | trigger an action on a live condition (`--until` watchers) |
| `coinbase-convert` | convert between currencies (USDC ↔ USD, etc.) |
| `coinbase-portfolios` | list / create / manage portfolios, move funds between them |
