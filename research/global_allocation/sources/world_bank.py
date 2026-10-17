"""World Bank market-cap cross-check. Not the equity class value."""

from __future__ import annotations

import json

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "world_bank"
NAME = "World Bank"
URL = "https://api.worldbank.org/v2/country/WLD/indicator/CM.MKT.LCAP.CD?format=json&mrnev=2"


def parse(body: bytes) -> SourceRecord:
    data = json.loads(body)
    rows = data[1] if isinstance(data, list) and len(data) > 1 else None
    if not rows:
        return unavailable(SOURCE_ID, NAME, URL.split("?")[0], "no observations")
    chosen = None
    for row in rows:
        if row.get("value") is not None:
            chosen = row
            break
    if chosen is None:
        return unavailable(SOURCE_ID, NAME, URL.split("?")[0], "null value")
    payload = {"as_of": str(chosen.get("date")), "value_usd": chosen["value"]}
    return ok_record(SOURCE_ID, NAME, URL.split("?")[0], payload["as_of"], payload, body)


def fetch() -> SourceRecord:
    return call(SOURCE_ID, NAME, URL, parse)
