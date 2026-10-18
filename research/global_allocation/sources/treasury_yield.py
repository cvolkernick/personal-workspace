"""US Treasury par real yield curve. The 10 YR column feeds later signals."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "treasury_real_yield"
NAME = "US Treasury real yield curve"
URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_real_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)


def parse(body: bytes) -> SourceRecord:
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        return unavailable(SOURCE_ID, NAME, URL, "empty csv")
    header = [cell.strip().strip('"') for cell in rows[0]]
    try:
        col = header.index("10 YR")
    except ValueError:
        return unavailable(SOURCE_ID, NAME, URL, "no 10 YR column")
    for row in rows[1:]:
        if len(row) <= col or not row[0].strip() or not row[col].strip():
            continue
        raw_date = row[0].strip()
        try:
            as_of = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            as_of = raw_date
        payload = {"as_of": as_of, "real_yield_10y": float(row[col])}
        return ok_record(SOURCE_ID, NAME, URL, as_of, payload, body)
    return unavailable(SOURCE_ID, NAME, URL, "no 10 YR observation")


def parse_series(body: bytes) -> list[tuple[str, Decimal]]:
    """Every 10 YR print, oldest first. Empty when the file has no usable rows."""
    from decimal import Decimal

    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        return []
    header = [cell.strip().strip('"') for cell in rows[0]]
    try:
        col = header.index("10 YR")
    except ValueError:
        return []
    points: list[tuple[str, Decimal]] = []
    for row in rows[1:]:
        if len(row) <= col or not row[0].strip() or not row[col].strip():
            continue
        raw_date = row[0].strip()
        try:
            as_of = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
        except ValueError:
            as_of = raw_date
        points.append((as_of, Decimal(row[col].strip())))
    points.sort(key=lambda item: item[0])
    return points


def fetch(year: int | None = None) -> SourceRecord:
    from datetime import date

    y = year or date.today().year
    return call(SOURCE_ID, NAME, URL.format(year=y), parse)
