"""Bitcoin tip height from mempool.space. The body is a decimal integer."""

from __future__ import annotations

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "mempool_height"
NAME = "mempool.space"
URL = "https://mempool.space/api/blocks/tip/height"


def parse(body: bytes) -> SourceRecord:
    text = body.decode("utf-8").strip()
    try:
        height = int(text)
    except ValueError:
        return unavailable(SOURCE_ID, NAME, URL, "height not an integer")
    if height < 0:
        return unavailable(SOURCE_ID, NAME, URL, "negative height")
    return ok_record(SOURCE_ID, NAME, URL, None, {"height": height}, body)


def fetch() -> SourceRecord:
    return call(SOURCE_ID, NAME, URL, parse)
