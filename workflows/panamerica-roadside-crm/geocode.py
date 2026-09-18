"""Reverse-geocode EXIF GPS to a road/area label.

Dry-run uses FakeGeocoder (no HTTP). Live uses Nominatim with a required
User-Agent. Failures return "" — callers keep GPS and flag ops.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_UA = "panamerica-roadside-crm/1.0 (personal ops; roadside intake)"


def road_label(address: dict[str, Any]) -> str:
    if not address:
        return ""
    road = (
        address.get("road")
        or address.get("pedestrian")
        or address.get("residential")
        or address.get("neighbourhood")
        or address.get("suburb")
        or address.get("hamlet")
        or ""
    )
    area = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("county")
        or ""
    )
    road = str(road).strip()
    area = str(area).strip()
    if road and area and area.lower() not in road.lower():
        return f"{road}, {area}"
    return road or area


class FakeGeocoder:
    def __init__(self, mapping: Optional[dict[tuple[float, float], str]] = None) -> None:
        self.mapping = dict(mapping or {})
        self.calls: list[tuple[float, float]] = []

    def reverse(self, lat: float, lon: float) -> str:
        pair = (float(lat), float(lon))
        self.calls.append(pair)
        if pair in self.mapping:
            return self.mapping[pair]
        key = (round(pair[0], 3), round(pair[1], 3))
        for stored, label in self.mapping.items():
            if (round(stored[0], 3), round(stored[1], 3)) == key:
                return label
        return ""


class NominatimGeocoder:
    def __init__(self, user_agent: str = DEFAULT_UA, timeout: int = 15) -> None:
        self.user_agent = user_agent or DEFAULT_UA
        self.timeout = timeout
        self.calls: list[tuple[float, float]] = []

    def reverse(self, lat: float, lon: float) -> str:
        self.calls.append((float(lat), float(lon)))
        params = urllib.parse.urlencode(
            {
                "lat": f"{float(lat):.7f}",
                "lon": f"{float(lon):.7f}",
                "format": "jsonv2",
                "zoom": "18",
                "addressdetails": "1",
            }
        )
        req = urllib.request.Request(
            f"{NOMINATIM_URL}?{params}",
            headers={"Accept": "application/json", "User-Agent": self.user_agent},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
        except (urllib.error.URLError, TimeoutError, OSError):
            return ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        if not isinstance(data, dict):
            return ""
        address = data.get("address") if isinstance(data.get("address"), dict) else {}
        return road_label(address)
