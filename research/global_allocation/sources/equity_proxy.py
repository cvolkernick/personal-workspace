"""VT daily close from Alpha Vantage. No key, no request.

Twelve Data is not called. Its robots.txt is ``User-agent: *`` / ``Disallow: /``.
"""

from __future__ import annotations

import json
import os

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "equity_proxy"
NAME = "Alpha Vantage"
URL = "https://www.alphavantage.co/query"
ENV_KEY = "ALPHAVANTAGE_API_KEY"


def parse(body: bytes) -> SourceRecord:
    data = json.loads(body)
    if isinstance(data, dict) and data.get("Note"):
        return unavailable(SOURCE_ID, NAME, URL, "rate limited")
    if isinstance(data, dict) and data.get("Error Message"):
        return unavailable(SOURCE_ID, NAME, URL, "error message")
    series = data.get("Time Series (Daily)") if isinstance(data, dict) else None
    if not isinstance(series, dict) or not series:
        return unavailable(SOURCE_ID, NAME, URL, "no daily series")
    day = sorted(series)[-1]
    close = series[day].get("4. close")
    if close is None:
        return unavailable(SOURCE_ID, NAME, URL, "no close")
    payload = {"symbol": "VT", "as_of": day, "price": float(close)}
    return ok_record(SOURCE_ID, NAME, URL, day, payload, body)


def fetch() -> SourceRecord:
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        return unavailable(SOURCE_ID, NAME, URL, f"missing {ENV_KEY}")
    url = f"{URL}?function=TIME_SERIES_DAILY&symbol=VT&outputsize=compact&apikey={key}"
    # ``call`` strips the query string from error URLs so the key is not logged.
    return call(SOURCE_ID, NAME, url, parse)
