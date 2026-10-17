"""ECB reference rates via Frankfurter. FX basis for the snapshot, not a class."""

from __future__ import annotations

import json

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "frankfurter"
NAME = "Frankfurter (ECB reference)"
URL = "https://api.frankfurter.dev/v1/latest?base=USD"


def parse(body: bytes) -> SourceRecord:
    data = json.loads(body)
    rates = data.get("rates")
    if not isinstance(rates, dict) or not rates:
        return unavailable(SOURCE_ID, NAME, URL.split("?")[0], "no rates")
    payload = {"as_of": data.get("date"), "base": data.get("base") or "USD", "rates": rates}
    return ok_record(SOURCE_ID, NAME, URL.split("?")[0], payload["as_of"], payload, body)


def fetch() -> SourceRecord:
    return call(SOURCE_ID, NAME, URL, parse)
