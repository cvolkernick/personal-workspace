"""Global broad money: sum of m2index ``value_usd``. CC BY 4.0. One CSV per run."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "m2index"
NAME = "M2 Index"
URL = "https://m2index.com/data/broad-money.csv"


def parse(body: bytes) -> SourceRecord:
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        return unavailable(SOURCE_ID, NAME, URL, "empty csv")
    total = Decimal(0)
    used = 0
    skipped = 0
    periods: list[str] = []
    for row in rows:
        raw = (row.get("value_usd") or "").strip()
        if not raw:
            skipped += 1
            continue
        total += Decimal(raw)
        used += 1
        period = (row.get("period") or "").strip()
        if period:
            periods.append(period)
    if used == 0:
        return unavailable(SOURCE_ID, NAME, URL, "no value_usd rows")
    as_of = max(periods) if periods else None
    payload = {
        "as_of": as_of,
        "oldest_period": min(periods) if periods else None,
        "value_usd": str(total),
        "rows_used": used,
        "rows_skipped_blank_usd": skipped,
    }
    return ok_record(SOURCE_ID, NAME, URL, as_of, payload, body)


def fetch() -> SourceRecord:
    return call(SOURCE_ID, NAME, URL, parse, timeout=90)
