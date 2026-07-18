"""Sunrise / sunset via the classic NOAA/USNO day-of-year algorithm (no deps)."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo


def _day_of_year(d: date) -> int:
    return d.timetuple().tm_yday


def _sun_utc_for_day(
    d: date,
    latitude: float,
    longitude: float,
    *,
    rising: bool,
    zenith: float = 90.833,
) -> Optional[datetime]:
    """Return UTC datetime of sunrise (rising=True) or sunset for local calendar date.

    Uses the widely ported "Almanac for Computers" / NOAA spreadsheet steps.
    Longitude: degrees east-positive (west is negative), same as GPS.
    """
    day = _day_of_year(d)
    # Convert longitude to hour value
    lng_hour = longitude / 15.0
    # Approximate time
    t = day + ((6.0 - lng_hour) / 24.0) if rising else day + ((18.0 - lng_hour) / 24.0)

    # Sun's mean anomaly
    m = (0.9856 * t) - 3.289

    # Sun's true longitude
    l = (
        m
        + (1.916 * math.sin(math.radians(m)))
        + (0.020 * math.sin(math.radians(2 * m)))
        + 282.634
    ) % 360.0

    # Right ascension
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l)))) % 360.0
    # Same quadrant as L
    l_quad = (math.floor(l / 90.0)) * 90.0
    ra_quad = (math.floor(ra / 90.0)) * 90.0
    ra = ra + (l_quad - ra_quad)
    ra = ra / 15.0  # hours

    # Declination
    sin_dec = 0.39782 * math.sin(math.radians(l))
    cos_dec = math.cos(math.asin(sin_dec))

    # Local hour angle
    cos_h = (
        math.cos(math.radians(zenith)) - (sin_dec * math.sin(math.radians(latitude)))
    ) / (cos_dec * math.cos(math.radians(latitude)))
    if cos_h > 1.0 or cos_h < -1.0:
        return None  # polar night / day

    h = math.degrees(math.acos(cos_h))
    if rising:
        h = 360.0 - h
    h = h / 15.0  # hours

    # Local mean time
    t_local = h + ra - (0.06571 * t) - 6.622
    # UTC
    ut = (t_local - lng_hour) % 24.0
    hours = int(ut)
    minutes = int((ut - hours) * 60.0)
    seconds = int((((ut - hours) * 60.0) - minutes) * 60.0)
    # Result is on UTC clock; map to the correct civil date near d
    base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    result = base.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        hours=hours, minutes=minutes, seconds=seconds
    )
    return result


def _sun_times_utc(
    d: date, latitude: float, longitude: float
) -> Tuple[Optional[datetime], Optional[datetime]]:
    rise = _sun_utc_for_day(d, latitude, longitude, rising=True)
    sett = _sun_utc_for_day(d, latitude, longitude, rising=False)
    return rise, sett


def sun_times_local(
    d: date,
    latitude: float,
    longitude: float,
    tz_name: str = "UTC",
) -> dict:
    """Sunrise/sunset in the given IANA timezone for local calendar date d."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
        tz_name = "UTC"

    # Compute using the UTC date that covers local midnight of d
    # Use d as the "civil day" for the algorithm's day-of-year input.
    rise_utc, set_utc = _sun_times_utc(d, latitude, longitude)

    # Adjust if local conversion falls on wrong calendar day by ±1 day
    def to_local(utc_dt: Optional[datetime], prefer_date: date, evening: bool) -> Optional[datetime]:
        if utc_dt is None:
            return None
        local = utc_dt.astimezone(tz)
        # If local date is far off, try neighboring UTC days
        for delta in (0, -1, 1, -2, 2):
            if delta == 0:
                cand_utc = utc_dt
            else:
                cand_utc = _sun_utc_for_day(
                    prefer_date + timedelta(days=delta),
                    latitude,
                    longitude,
                    rising=not evening,
                )
                if cand_utc is None:
                    continue
            cand = cand_utc.astimezone(tz)
            if cand.date() == prefer_date:
                return cand
        return local

    rise_local = to_local(rise_utc, d, evening=False)
    set_local = to_local(set_utc, d, evening=True)

    # Final ordering: if both present and rise after set on same logic day, swap
    if rise_local and set_local and rise_local > set_local:
        # try recompute sunset with rising=False on adjacent day
        for delta in (-1, 0, 1):
            alt = _sun_utc_for_day(
                d + timedelta(days=delta), latitude, longitude, rising=False
            )
            if alt is None:
                continue
            alt_l = alt.astimezone(tz)
            if alt_l.date() == d and alt_l > rise_local:
                set_local = alt_l
                set_utc = alt
                break

    out: dict = {
        "date": d.isoformat(),
        "latitude": latitude,
        "longitude": longitude,
        "timezone": tz_name,
        "sunrise": rise_local.astimezone(timezone.utc).isoformat() if rise_local else None,
        "sunset": set_local.astimezone(timezone.utc).isoformat() if set_local else None,
        "sunrise_local": rise_local.isoformat() if rise_local else None,
        "sunset_local": set_local.isoformat() if set_local else None,
        "ok": rise_local is not None and set_local is not None,
    }
    if rise_local is not None:
        out["sunrise_hhmm"] = rise_local.strftime("%H:%M")
    if set_local is not None:
        out["sunset_hhmm"] = set_local.strftime("%H:%M")
    return out


def event_datetime_local(
    d: date,
    trigger: str,
    latitude: float,
    longitude: float,
    tz_name: str,
    offset_minutes: int = 0,
) -> Optional[datetime]:
    """Local datetime for sunrise/sunset (+ offset) on date d."""
    times = sun_times_local(d, latitude, longitude, tz_name)
    key = "sunrise_local" if trigger == "sunrise" else "sunset_local"
    raw = times.get(key)
    if not raw:
        return None
    dt = datetime.fromisoformat(raw)
    if offset_minutes:
        dt = dt + timedelta(minutes=int(offset_minutes))
    return dt
