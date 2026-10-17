"""Coinbase public spot prices for BTC-USD and PAXG-USD."""

from __future__ import annotations

import json

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

NAME = "Coinbase"


def parse_spot(body: bytes) -> SourceRecord:
    """Parse a Coinbase ``/v2/prices/{pair}/spot`` body.

    The source id is filled by the caller when the pair is known. Offline
    fixtures for BTC and PAXG both use this parser; ``collect`` overwrites
    ``source_id`` only via the dict key, so the record's own id is set here
    from the payload base symbol when present.
    """
    data = json.loads(body)["data"]
    base = str(data.get("base") or "")
    source_id = "coinbase_btc" if base == "BTC" else "coinbase_paxg" if base == "PAXG" else "coinbase"
    pair = f"{base}-USD" if base else "spot"
    url = f"https://api.coinbase.com/v2/prices/{pair}/spot"
    amount = data.get("amount")
    if amount is None:
        return unavailable(source_id, NAME, url, "missing amount")
    payload = {"price": float(amount), "base": base, "currency": data.get("currency")}
    return ok_record(source_id, NAME, url, None, payload, body)


def fetch_btc() -> SourceRecord:
    url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    return call("coinbase_btc", NAME, url, parse_spot)


def fetch_paxg() -> SourceRecord:
    url = "https://api.coinbase.com/v2/prices/PAXG-USD/spot"
    return call("coinbase_paxg", NAME, url, parse_spot)
