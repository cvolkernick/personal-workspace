"""ECB M3 (BSI) csvdata. Cross-check only. Last two observations."""

from __future__ import annotations

import csv
import io

from research.global_allocation.sources.base import SourceRecord, ok_record, unavailable
from research.global_allocation.sources.http import call

SOURCE_ID = "ecb_m3"
NAME = "ECB M3"
URL = (
    "https://data-api.ecb.europa.eu/service/data/BSI/"
    "M.U2.Y.V.M30.X.1.U2.2300.Z01.E?format=csvdata&lastNObservations=2"
)


def parse(body: bytes) -> SourceRecord:
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
    if not rows:
        return unavailable(SOURCE_ID, NAME, URL.split("?")[0], "empty csv")
    last = rows[-1]
    if not last.get("OBS_VALUE") or not last.get("TIME_PERIOD"):
        return unavailable(SOURCE_ID, NAME, URL.split("?")[0], "missing OBS_VALUE")
    payload = {
        "as_of": last["TIME_PERIOD"],
        "obs_value": last["OBS_VALUE"],
        "unit": last.get("UNIT") or "",
        "unit_mult": last.get("UNIT_MULT") or "",
    }
    return ok_record(SOURCE_ID, NAME, URL.split("?")[0], payload["as_of"], payload, body)


def fetch() -> SourceRecord:
    return call(SOURCE_ID, NAME, URL, parse)
