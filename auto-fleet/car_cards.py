"""Car-centric card helpers for Auto Fleet.

Additive on top of the Gmail dump path. Does not invent VINs, plates,
balances, trips, or guest fields. Invoice-ready stays Google Tasks.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
from typing import Any, Mapping, Optional, Sequence

try:
    from . import turo_inbox
except ImportError:  # script / unittest path
    import turo_inbox  # type: ignore

# Product-locked lender/APR (Chris + Helm). No invented balances.
LOCKED_FINANCE: dict[str, dict[str, Any]] = {
    "m3-2020": {
        "lender": "Wells Fargo",
        "apr_pct": 5.65,
        "show_balances": False,
        "note": "Mike-paid. Lender/APR metadata only — not an FCC venue.",
    },
    "m3-2022": {
        "lender": "GM Financial",
        "apr_pct": 18.15,
        "show_balances": True,
    },
    "corolla-2022": {
        "lender": "Capital One",
        "apr_pct": 11.14,
        "show_balances": True,
    },
    "corolla-2024": {
        "lender": "Santander",
        "apr_pct": 10.18,
        "show_balances": True,
    },
    "r1s-2023": {
        "lender": "Vivek",
        "apr_pct": 0,
        "monthly": 1350,
        "show_balances": False,
    },
}

CANCEL_STATUSES = {"canceled", "cancelled"}
PAYOUT_STATUSES = {"payout"}
LIVE_PHASES = {"upcoming", "active"}
FLEET_TZ = ZoneInfo("America/New_York")
# Date-only start on its calendar day stays upcoming until local noon ET.
DATE_ONLY_SAME_DAY_ACTIVE_AFTER = time(12, 0, 0)

_TRIP_IN_TEXT = re.compile(
    r"(?:trip|reservation|booking)\s+(?:id|number|#)\s*[:#]?\s*([A-Z0-9-]{4,})",
    re.I,
)
_TRIP_HASH = re.compile(r"(?:^|[\s(])#\s*(\d{6,8})\b")


# Static public host profile. Never fetched live from Turo.
MIKE_TURO_HOST_LABEL = "Mike's"
MIKE_TURO_DRIVER_ID = "27172979"
MIKE_TURO_PUBLIC_URL = "https://turo.com/us/en/drivers/27172979"
# Corollas: trip mail. Rivian: Turo Support case 20260826-S3MDJC
# (VIN 7PDSGABA3PN028624 listed on Mike's account) + owner 2026-08-29.
# Fleet-tagged turo / Mike's is not enough. m3-2022 stays gated.
MAIL_PROVEN_HOST_UNIT_IDS = frozenset(
    {"corolla-2022", "corolla-2024", "r1s-2023"}
)


def host_label_for(unit: Mapping[str, Any]) -> Optional[str]:
    """Turo-role units are Mike's. Personal units: only if roster names a host."""
    raw = (unit.get("host") or "").strip()
    if raw:
        if raw.lower().endswith("'s") or raw.lower().endswith("’s"):
            return raw
        return f"{raw}'s"
    if (unit.get("role") or "").lower() == "turo":
        return MIKE_TURO_HOST_LABEL
    return None


def host_identity_for(unit: Mapping[str, Any]) -> Optional[dict[str, str]]:
    """Thin static Mike Turo chip. Listing-proven units only. No live Turo read."""
    uid = str(unit.get("id") or "")
    if uid not in MAIL_PROVEN_HOST_UNIT_IDS:
        return None
    if (unit.get("role") or "").lower() != "turo":
        return None
    if host_label_for(unit) != MIKE_TURO_HOST_LABEL:
        return None
    return {
        "host_label": MIKE_TURO_HOST_LABEL,
        "driver_id": MIKE_TURO_DRIVER_ID,
        "public_url": MIKE_TURO_PUBLIC_URL,
    }


def locked_finance_for(unit_id: str) -> dict[str, Any]:
    locked = LOCKED_FINANCE.get(str(unit_id) or "")
    if not locked:
        return {"lender": None, "apr_pct": None, "show_balances": False}
    return dict(locked)


def _as_et(now: Any) -> datetime:
    if isinstance(now, datetime):
        dt = now
    elif now:
        try:
            dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(FLEET_TZ)


def has_clock(value: Any) -> bool:
    """True when a stored start/end includes a clock, not just YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value.hour or value.minute or value.second or value.microsecond
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return False
    return bool(re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text))


def parse_trip_instant(value: Any, *, end: bool = False) -> Optional[datetime]:
    """Parse stored start/end into America/New_York.

    Date-only values become 00:00 ET (start) or 23:59:59 ET (end). Timed
    values keep their clock. Naive stamps are interpreted as ET.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=FLEET_TZ)
        return dt.astimezone(FLEET_TZ)
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = datetime.strptime(text, "%Y-%m-%d").date()
        clock = time(23, 59, 59) if end else time(0, 0, 0)
        return datetime.combine(day, clock, tzinfo=FLEET_TZ)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        try:
            day = datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        clock = time(23, 59, 59) if end else time(0, 0, 0)
        return datetime.combine(day, clock, tzinfo=FLEET_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FLEET_TZ)
    return dt.astimezone(FLEET_TZ)


def _merge_trip(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    keys = (
        "trip_id",
        "guest",
        "vehicle",
        "vin",
        "start",
        "end",
        "pickup",
        "drop_off",
        "phone",
        "host_label",
        "subject",
        "unit_id",
        "message_id",
    )
    for ev in events:
        if not isinstance(ev, dict):
            continue
        for key in keys:
            if merged.get(key) in (None, "") and ev.get(key) not in (None, ""):
                merged[key] = ev[key]
        for key in ("extra_drivers", "guest_asks", "attachments"):
            vals = ev.get(key)
            if not vals:
                continue
            existing = list(merged.get(key) or [])
            seq = vals if isinstance(vals, list) else [vals]
            for item in seq:
                if item and item not in existing:
                    existing.append(item)
            if existing:
                merged[key] = existing
    evs = [e for e in events if isinstance(e, dict)]
    canceled = any(
        str(e.get("status") or "").lower() in CANCEL_STATUSES for e in evs
    )
    latest = max(evs, key=lambda e: str(e.get("date") or "")) if evs else {}
    merged["status"] = "canceled" if canceled else (latest.get("status") or "booked")
    merged["kind"] = merged["status"]
    return merged


def _phase(trip: Mapping[str, Any], now: datetime) -> str:
    """Trip phase vs America/New_York now.

    Timed bounds: upcoming if now < start; active if start <= now <= end
    (inclusive end — Turo's stated end clock is still the trip); past if
    now > end. Date-only start/end become 00:00 / 23:59:59 ET, except a
    date-only start on *today* stays upcoming until local noon so a
    same-day morning is not marked active.
    """
    if str(trip.get("status") or "").lower() in CANCEL_STATUSES:
        return "canceled"
    start_raw = trip.get("start")
    end_raw = trip.get("end")
    start = parse_trip_instant(start_raw, end=False)
    end = parse_trip_instant(end_raw, end=True)
    if (
        start is not None
        and not has_clock(start_raw)
        and start.date() == now.date()
        and now.time() < DATE_ONLY_SAME_DAY_ACTIVE_AFTER
    ):
        if end and now > end:
            return "past"
        return "upcoming"
    if end and now > end:
        return "past"
    if start and now < start:
        return "upcoming"
    if start and end and start <= now <= end:
        return "active"
    if start and start <= now and not end:
        return "active"
    return "upcoming"


def schedule_for_bookings(
    bookings: Sequence[Mapping[str, Any]] | None,
    now: Any = None,
) -> list[dict[str, Any]]:
    """Upcoming/active trips + cancel for still-open reservations.

    Past/closed trips drop off the card. Payouts are not bookings.
    Same trip_id with a later cancel collapses to canceled. Phase uses
    clock time in America/New_York, not the start calendar day alone.
    """
    now_et = _as_et(now)
    groups: dict[str, list[Mapping[str, Any]]] = {}
    untitled = 0
    for raw in bookings or []:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status") or raw.get("kind") or "").lower()
        if status in PAYOUT_STATUSES:
            continue
        key = str(raw.get("trip_id") or raw.get("message_id") or "")
        if not key:
            untitled += 1
            key = f"_anon_{untitled}"
        groups.setdefault(key, []).append(raw)

    out: list[dict[str, Any]] = []
    for evs in groups.values():
        trip = _merge_trip(evs)
        phase = _phase(trip, now_et)
        trip["phase"] = phase
        if phase in LIVE_PHASES:
            out.append(trip)
            continue
        if phase == "canceled":
            last = parse_trip_instant(trip.get("end"), end=True) or parse_trip_instant(
                trip.get("start"), end=True
            )
            if last is None or now_et <= last:
                out.append(trip)
    out.sort(key=lambda t: (str(t.get("start") or ""), str(t.get("trip_id") or "")))
    return out


def live_trips(schedule: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(t) for t in (schedule or []) if t.get("phase") in LIVE_PHASES]


def extract_trip_id(text: str) -> Optional[str]:
    blob = text or ""
    m = _TRIP_IN_TEXT.search(blob)
    if m:
        return m.group(1)
    hm = _TRIP_HASH.search(blob)
    if hm:
        return hm.group(1)
    return None


def _unit_year(unit: Mapping[str, Any]) -> str:
    ident = unit.get("identity") if isinstance(unit.get("identity"), dict) else {}
    return str(unit.get("year") or ident.get("year") or "")


def _unit_model(unit: Mapping[str, Any]) -> str:
    ident = unit.get("identity") if isinstance(unit.get("identity"), dict) else {}
    return str(unit.get("model") or ident.get("model") or "")


def _unit_plate(unit: Mapping[str, Any]) -> str:
    ident = unit.get("identity") if isinstance(unit.get("identity"), dict) else {}
    plate = unit.get("plate") if unit.get("plate") is not None else ident.get("plate")
    return str(plate or "").strip().upper()


def _year_model_hits(blob: str, roster: Sequence[Mapping[str, Any]]) -> list[str]:
    hay = turo_inbox._norm_text(blob)
    hits: list[str] = []
    for unit in roster:
        year = _unit_year(unit)
        model = _unit_model(unit).lower()
        if not year or not model:
            continue
        if year in hay and model in hay:
            hits.append(str(unit["id"]))
    return hits


def _plate_hits(blob: str, roster: Sequence[Mapping[str, Any]]) -> list[str]:
    hay = turo_inbox._norm_text(blob).upper()
    hits: list[str] = []
    for unit in roster:
        plate = _unit_plate(unit)
        if not plate:
            continue
        if re.search(rf"(?<![A-Z0-9]){re.escape(plate)}(?![A-Z0-9])", hay):
            hits.append(str(unit["id"]))
    return hits


def match_invoice_unit(
    item: Mapping[str, Any],
    units: Sequence[Mapping[str, Any]],
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> Optional[str]:
    """Attach a GT Turo item to one roster unit. None = unmatched, never guessed.

    Trip # wins when it maps to exactly one unit's bookings. Else year +
    model in title/notes, or an exact plate token. Disagreeing signals stay
    unmatched. Yearless Corolla without a plate stays unmatched.
    """
    title = str(item.get("title") or "")
    notes = str(item.get("notes") or "")
    blob = f"{title}\n{notes}"
    trip = extract_trip_id(blob)
    if trip and bookings_by_unit:
        hits = [
            str(uid)
            for uid, books in bookings_by_unit.items()
            if any(str((b or {}).get("trip_id") or "") == trip for b in (books or []))
        ]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    roster = [u for u in units if isinstance(u, dict) and u.get("id")]
    fake = {
        "subject": title,
        "body": notes,
        "vehicle": blob,
        "trip_id": trip,
    }
    found: list[str] = []
    uid = turo_inbox.match_unit(fake, roster)
    if uid:
        found.append(str(uid))
    year_hits = _year_model_hits(blob, roster)
    if len(year_hits) > 1:
        return None
    if len(year_hits) == 1:
        found.append(year_hits[0])
    plate_hits = _plate_hits(blob, roster)
    if len(plate_hits) > 1:
        return None
    if len(plate_hits) == 1:
        found.append(plate_hits[0])
    unique = list(dict.fromkeys(found))
    if len(unique) == 1:
        return unique[0]
    return None


def _is_open_gt_item(item: Mapping[str, Any]) -> bool:
    status = str(item.get("status") or "needsAction")
    return status == "needsAction"


def attach_invoice_items(
    items: Sequence[Mapping[str, Any]] | None,
    units: Sequence[Mapping[str, Any]],
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Split open GT items into per-unit lists + unmatched leftover.

    Completed items are dropped — they do not paint on a car or the bucket.
    """
    by_unit: dict[str, list[dict[str, Any]]] = {
        str(u["id"]): [] for u in units if isinstance(u, dict) and u.get("id")
    }
    unmatched: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        if not _is_open_gt_item(raw):
            continue
        rec = dict(raw)
        uid = rec.get("unit_id") or match_invoice_unit(rec, units, bookings_by_unit)
        rec["unit_id"] = uid
        if uid and uid in by_unit:
            by_unit[uid].append(rec)
        else:
            unmatched.append(rec)
    return {"by_unit": by_unit, "unmatched": unmatched}
