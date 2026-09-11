---
name: coinbase-portfolios
description: Manages Coinbase Advanced Trade portfolios — list, get, create, edit, delete portfolios, view balances, and move funds between portfolios. Use when the user wants to see their portfolios, create a new one (e.g. "day trading"), check balances, or transfer funds between portfolios. Covers the per-portfolio API-key scoping model.
requires: coinbase env configured (see `coinbase` skill); transfers need Transfer permission
---

# coinbase-portfolios

Manage portfolios and move funds between them.

## Balances

```sh
coinbase balance                       # all account balances (active portfolio)
coinbase balance show_zero==true       # include zero-balance accounts
```

## Portfolios

```sh
coinbase portfolios list
coinbase portfolios get <portfolio_uuid>
coinbase portfolios create name="Day Trading"
coinbase portfolios edit <portfolio_uuid> name="Renamed"
coinbase portfolios delete <portfolio_uuid>
```

## The per-key scoping model

Each CDP API key is scoped to ONE portfolio — the key can only trade within it. To operate across portfolios, register one env per portfolio using the `live-<portfolio>` convention:

```sh
coinbase env live-primary --key-file primary-key.json
coinbase env live-trading --key-file trading-key.json
```

Switch with `coinbase env live-trading`, or per-command with `-e live-primary`.

## Move funds between portfolios

Switch to the SOURCE portfolio's key first, then transfer:

```sh
coinbase transfer amount=1000 currency=USD from=<src_uuid> to=<dst_uuid>
```

## Funding a fresh account

The brokerage API does not buy crypto with a payment method directly. To fund for trading:

1. Deposit USD or crypto via coinbase.com or the mobile app — deposits land in the **default portfolio**.
2. If trading from a non-default portfolio, transfer from default (see above).
3. Verify: `coinbase balance`
