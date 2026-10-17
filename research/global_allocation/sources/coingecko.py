"""CoinGecko global market cap.

Live fetch is off. ``api.coingecko.com/robots.txt`` (fetched 2026-10-17) has
``User-agent: *`` / ``Disallow: /api/v3``. The class uses the manual anchor.
``parse`` remains for a permitted payload and is not called by ``fetch``.
"""

from __future__ import annotations

import json

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable

SOURCE_ID = "coingecko"
NAME = "CoinGecko"
URL = "https://api.coingecko.com/api/v3/global"
REASON = "robots.txt Disallow /api/v3 for User-agent *; demoted to manual anchor"


def fetch() -> SourceRecord:
    return unavailable(SOURCE_ID, NAME, URL, REASON)


def parse(body: bytes) -> SourceRecord:
    data = json.loads(body)["data"]
    payload = {
        "total_market_cap_usd": data["total_market_cap"]["usd"],
        "btc_dominance_pct": data["market_cap_percentage"]["btc"],
    }
    return ok_record(SOURCE_ID, NAME, URL, None, payload, body)
