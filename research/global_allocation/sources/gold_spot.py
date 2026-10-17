"""Keyless gold spot. Primary price for tonnes × price. Terms were unchecked."""

from __future__ import annotations

import json

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "gold_spot"
NAME = "gold-api"
URL = "https://api.gold-api.com/price/XAU"


def parse(body: bytes) -> SourceRecord:
    data = json.loads(body)
    price = data.get("price")
    if price is None:
        return unavailable(SOURCE_ID, NAME, URL, "missing price")
    as_of = data.get("updatedAt")
    payload = {"price": float(price), "updated_at": as_of}
    return ok_record(SOURCE_ID, NAME, URL, as_of if isinstance(as_of, str) else None, payload, body)


def fetch() -> SourceRecord:
    return call(SOURCE_ID, NAME, URL, parse)
