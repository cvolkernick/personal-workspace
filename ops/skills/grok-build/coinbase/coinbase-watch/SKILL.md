---
name: coinbase-watch
description: Watches a live Coinbase data stream and triggers an action when a condition becomes true, via the CLI's `--until` flag. Use when the user wants to "do X when price hits Y", "buy on a dip", "wait until my order fills then…", or any cross-product / spread / volume trigger that a single resting order cannot express. Places real orders when chained.
requires: coinbase env configured with Trade permission (see `coinbase` skill); CLI lane only (not MCP)
---

# coinbase-watch (`--until`)

`coinbase products ticker|book | orders list <ARGS> --until "<predicate>"` watches a live stream, blocks until the predicate is true, prints the matching event as JSON to stdout, and exits 0 (timeout → 124, permanent failure → 2). Chain an action onto it with `&&`.

**Use `--until` ONLY for triggers native `orders` cannot express.** It does not replace native order types — those are durable and server-side; a `--until` watcher is a monitored, session-lived process (see Safety).

## Use native `orders` (see `coinbase-trading`) for — do NOT rebuild with `--until`

- Stop-loss → `type=stop_limit` sell, `stop_direction=down`
- Take-profit / sell-at-price → `type=limit` (or `stop_limit`)
- Triggered / breakout / dip entry on the *same* product's price → `type=stop_limit`
- Resting buy/sell at a price → `type=limit`
- Price ladder → several `type=limit` orders

## Reach for `--until` only when a single resting order can't watch the trigger

Cross-product (act on one product based on ANOTHER's price):
```sh
coinbase products ticker BTC-USDC ETH-USDC --until "price BTC-USDC >= 65000" --until-timeout 3600 \
  && coinbase orders create client_order_id=$(uuidgen) product_id=ETH-USDC side=BUY type=market quote_size=50
```

Compound, multi-field / multi-product:
```sh
coinbase products ticker BTC-USDC ETH-USDC --until "price BTC-USDC >= 65k && price ETH-USDC <= 1500" --until-timeout 3600 \
  && coinbase orders create client_order_id=$(uuidgen) product_id=ETH-USDC side=BUY type=market quote_size=50
```

Non-price trigger — spread / volume / 24h move:
```sh
# only execute when the book is tight (low slippage)
coinbase products book BTC-USDC --until "spread <= 1" --until-timeout 600 \
  && coinbase orders create client_order_id=$(uuidgen) product_id=BTC-USDC side=BUY type=market quote_size=50
# enter only after a sharp 24h drawdown
coinbase products ticker SOL-USDC --until "pct_24h <= -8" --until-timeout 86400 \
  && coinbase orders create client_order_id=$(uuidgen) product_id=SOL-USDC side=BUY type=market quote_size=25
```

Fill / state sequencing — run the next leg only AFTER another order fills:
```sh
coinbase orders list --until "status == FILLED" --until-timeout 86400 \
  && coinbase orders create client_order_id=$(uuidgen) product_id=ETH-USDC side=BUY type=market quote_size=50
```

Arbitrary "then" — the matched event is JSON on stdout; the chained action can be anything (notify, call another tool, place a *different* order), not just an order.

## Predicate syntax

Form: `field [product] op value`. Operators `== != > < >= <=`, combined with `&& || ( )`; numeric suffixes `k m b`. Scope to a product when watching several: `price BTC-USDC >= 65k`.

**Don't memorize the fields — they're per-stream and authoritative in `--help`** (generated from the spec). Run `coinbase products ticker --help` (or `products book` / `orders list`) for the valid fields, types, and ready examples for that surface.

## Safety rules

- Gate every action on a match: chain with `&&` (runs only on exit 0). A timeout (124) or failure (2) must NOT trade. Always set `--until-timeout`.
- Resolve relative targets ("3% below") from LIVE spot at arm time, and confirm the resolved absolute number with the user before arming:
  `SPOT=$(coinbase products ticker BTC-USDC --jq '.trades[0].price' | tr -d '"')`
- These are REAL orders. Confirm the full plan before arming; do NOT auto-run `orders get` afterward.
- A `--until` watcher is MONITORED and session-lived, not a resting server-side order — if it stops, the trigger is gone. For anything that must survive, use a native `limit` / `stop_limit`.
