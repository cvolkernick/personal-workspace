"""Fed H.6 M2, seasonally adjusted, from the public preformatted CSV package."""

from __future__ import annotations

import csv
import io

from research.global_allocation.sources.base import SourceRecord, unavailable, ok_record
from research.global_allocation.sources.http import call

SOURCE_ID = "fed_h6"
NAME = "Fed H.6"
URL = (
    "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H6"
    "&series=67c512c9f4eb51a20a5b6da52135b09b&lastobs=6"
    "&filetype=csv&label=include&layout=seriescolumn&type=package"
)


def parse(body: bytes) -> SourceRecord:
    text = body.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    header_i = None
    for i, row in enumerate(rows):
        if row and row[0].strip() == "Time Period":
            header_i = i
            break
    if header_i is None:
        return unavailable(SOURCE_ID, NAME, URL, "no Time Period header")
    try:
        col = rows[header_i].index("M2.M")
    except ValueError:
        return unavailable(SOURCE_ID, NAME, URL, "no M2.M column")
    last = None
    for row in rows[header_i + 1 :]:
        if len(row) > col and row[0].strip() and row[col].strip():
            last = row
    if last is None:
        return unavailable(SOURCE_ID, NAME, URL, "no M2 observation")
    payload = {
        "as_of": last[0].strip(),
        "m2_billions": float(last[col]),
        "series": "M2.M",
        "unit": "billions_usd",
    }
    return ok_record(SOURCE_ID, NAME, URL, payload["as_of"], payload, body)


def parse_series(body: bytes) -> list[tuple[str, Decimal]]:
    """Every M2.M observation, oldest first. Values stay in billions of USD."""
    from decimal import Decimal

    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    header_i = None
    for i, row in enumerate(rows):
        if row and row[0].strip() == "Time Period":
            header_i = i
            break
    if header_i is None:
        return []
    try:
        col = rows[header_i].index("M2.M")
    except ValueError:
        return []
    points: list[tuple[str, Decimal]] = []
    for row in rows[header_i + 1 :]:
        if len(row) > col and row[0].strip() and row[col].strip():
            points.append((row[0].strip(), Decimal(row[col].strip())))
    points.sort(key=lambda item: item[0])
    return points


def fetch() -> SourceRecord:
    return call(SOURCE_ID, NAME, URL, parse)
