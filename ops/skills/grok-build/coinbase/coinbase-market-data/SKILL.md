---
name: coinbase-market-data
description: Reads market data from Coinbase Advanced Trade — current prices, candles/OHLCV, order book depth, best bid/ask, ticker, and product metadata. Use when the user wants to check a price, look at a chart, see the order book, get the spread, or list tradable products. Read-only.
requires: coinbase env configured (see `coinbase` skill)
---

# coinbase-market-data

Read-only market data. All commands return JSON to stdout.

## Current price / product info

```sh
coinbase products get BTC-USD
coinbase products ticker BTC-USD                      # latest trades
coinbase products ticker BTC-USD --jq '.trades[0].price'   # just the last price
```

Extract a bare spot price (useful when resolving relative targets):

```sh
SPOT=$(coinbase products ticker BTC-USD --jq '.trades[0].price' | tr -d '"')
```

## Candles (OHLCV)

```sh
coinbase products candles BTC-USD
coinbase products candles BTC-USD --jq '.candles[0]'
# narrow with query params, e.g. granularity / time range
coinbase products candles BTC-USD granularity==ONE_HOUR
```

## Order book & spread

```sh
coinbase products book BTC-USD --jq '.pricebook'
coinbase products best_bid_ask product_ids==BTC-USD
```

## List / discover products

```sh
coinbase products list
coinbase products list --jq '.products[].product_id' --paginate
```

## Notes

- For streaming/conditional reads ("tell me when BTC > 65k"), use `coinbase-watch` (`--until`), not polling loops.
- Market data is read-only and does not place orders. To act on a price, hand off to `coinbase-trading`.
