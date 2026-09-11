---
name: coinbase-trading
description: Places, previews, edits, and cancels spot orders on Coinbase Advanced Trade — market buys/sells, limit orders, stop-loss / take-profit, and order management. Use when the user wants to buy or sell crypto, place an order, set a limit or stop, or check/cancel existing orders. These are real orders.
requires: coinbase env configured with Trade permission (see `coinbase` skill)
---

# coinbase-trading

Place and manage spot orders. **These are REAL orders.** Confirm the full plan (product, side, size) with the user before placing.

## Market orders

Side determines which size field you use:
- **Market BUY uses `quote_size`** (amount of quote currency, e.g. USD/USDC, to spend).
- **Market SELL uses `base_size`** (amount of the asset to sell).

`client_order_id` is auto-generated — omit it. Place market orders directly in one call; preview only for large or limit/stop orders.

```sh
# Buy $100 of BTC at market
coinbase orders create product_id=BTC-USD side=BUY type=market quote_size=100

# "Buy N USDC worth of <ASSET>" → BUY on <ASSET>-USDC with quote_size=N
coinbase orders create product_id=BTC-USDC side=BUY type=market quote_size=1

# Sell 0.5 ETH at market
coinbase orders create product_id=ETH-USD side=SELL type=market base_size=0.5
```

**Report the result from the create response.** Do NOT automatically run `coinbase orders get <order_id>` afterward — check back with the user first; only run the confirmation `get` if they ask.

## Preview (large or limit/stop orders)

Skip preview for small market buys — place directly. For a large order, or any limit/stop, preview first to check fees, fill price, and slippage:

```sh
coinbase orders preview product_id=BTC-USD side=BUY type=limit base_size=0.5 limit_price=50000
```

## Limit orders (resting, server-side)

```sh
# Resting buy at a price
coinbase orders create product_id=BTC-USD side=BUY type=limit base_size=0.01 limit_price=50000

# Take-profit / sell-at-price
coinbase orders create product_id=ETH-USD side=SELL type=limit base_size=0.5 limit_price=4000
```

## Stop orders (server-side, durable)

```sh
# Stop-loss sell
coinbase orders create product_id=BTC-USD side=SELL type=stop_limit \
  base_size=0.01 limit_price=48000 stop_price=49000 stop_direction=down
```

Prefer native order types — they are durable and server-side. Only use a `--until` watcher (see `coinbase-watch`) when the trigger is something a single resting order cannot express (cross-product price, spread/volume, fill sequencing).

## Manage orders

```sh
coinbase orders list status==OPEN
coinbase orders get <order_id>
coinbase orders cancel order_ids:='["id-1","id-2"]'
coinbase orders edit <order_id> limit_price=50000
coinbase orders fills order_id==<order_id>
coinbase orders close_position product_id=BTC-USD
```

## Portfolio scope

CDP API keys derive portfolio scope from the key itself — no `retail_portfolio_id` needed in the body. To trade in a different portfolio, switch envs (`-e live-trading`); see `coinbase-portfolios`.
