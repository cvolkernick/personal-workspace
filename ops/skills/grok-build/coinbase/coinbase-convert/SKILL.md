---
name: coinbase-convert
description: Converts between currencies on Coinbase (e.g. USDC ↔ USD, USD ↔ a stablecoin) using the quote-then-execute flow. Use when the user wants to convert, swap stablecoins, or move between fiat and USDC. Not for buying/selling crypto assets — use `coinbase-trading` (a spot order) for that.
requires: coinbase env configured with Trade permission (see `coinbase` skill)
---

# coinbase-convert

Convert between supported currency pairs (commonly USDC ↔ USD). Convert is a distinct flow from trading — for buying/selling a crypto asset, place a spot order via `coinbase-trading` instead of converting.

## Flow: quote → execute

```sh
# 1. Get a quote (returns a quote_id)
coinbase convert quote from=USD to=USDC amount=100

# 2. Execute the quote
coinbase convert execute <quote_id> from=USD to=USDC

# 3. Inspect a quote/trade
coinbase convert get <quote_id>
```

Always quote first, surface the rate/fees to the user, and confirm before executing — `execute` is a real conversion.

## Notes

- `from` / `to` are currency codes (e.g. `USD`, `USDC`).
- `amount` is denominated in the `from` currency.
- This is the conversion product, not a market order. "Buy N USDC worth of BTC" is a spot order (`coinbase-trading`), not a convert.
