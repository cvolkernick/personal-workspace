"""Car-centric card helpers for Auto Fleet.

Additive on top of the Gmail dump path. Does not invent VINs, plates,
balances, trips, or guest fields. Invoice-ready stays Google Tasks.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
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

_TRIP_IN_TEXT = re.compile(
    r"(?:trip|reservation|booking)\s+(?:id|number|#)\s*[:#]?\s*([A-Z0-9-]{4,})",
    re.I,
)
_TRIP_HASH = re.compile(r"(?:^|[\s(])#\s*(\d{6,8})\b")


def host_label_for(unit: Mapping[str, Any]) -> Optional[str]:
    """Turo-role units are Mike's. Personal units: only if roster names a host."""
    raw = (unit.get("host") or "").strip()
    if raw:
        if raw.lower().endswith("'s") or raw.lower().endswith("’s"):
            return raw
        return f"{raw}'s"
    if (unit.get("role") or "").lower() == "turo":
        return "Mike's"
    return None


def locked_finance_for(unit_id: str) -> dict[str, Any]:
    locked = LOCKED_FINANCE.get(str(unit_id) or "")
    if not locked:
        return {"lender": None, "apr_pct": None, "show_balances": False}
    return dict(locked)


def _date_only(value: Any) -> Optional[Any]:
    if value is None or value == "":
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _as_dt(now: Any) -> datetime:
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
    return dt.astimezone(timezone.utc)


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
        for key in ("extra_drivers", "guest_asks"):
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


def _phase(trip: Mapping[str, Any], today) -> str:
    if str(trip.get("status") or "").lower() in CANCEL_STATUSES:
        return "canceled"
    start = _date_only(trip.get("start"))
    end = _date_only(trip.get("end"))
    if end and end < today:
        return "past"
    if start and end and start <= today <= end:
        return "active"
    if start and start <= today and not end:
        return "active"
    if start and start > today:
        return "upcoming"
    return "upcoming"


def schedule_for_bookings(
    bookings: Sequence[Mapping[str, Any]] | None,
    now: Any = None,
) -> list[dict[str, Any]]:
    """Upcoming/active trips + cancel for still-open reservations.

    Past/closed trips drop off the card. Payouts are not bookings.
    Same trip_id with a later cancel collapses to canceled.
    """
    today = _as_dt(now).date()
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
        phase = _phase(trip, today)
        trip["phase"] = phase
        end = _date_only(trip.get("end"))
        start = _date_only(trip.get("start"))
        if phase in LIVE_PHASES:
            out.append(trip)
            continue
        if phase == "canceled":
            last = end or start
            if last is None or last >= today:
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


def match_invoice_unit(
    item: Mapping[str, Any],
    units: Sequence[Mapping[str, Any]],
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> Optional[str]:
    """Attach a GT Turo item to one roster unit. None = unmatched, never guessed.

    Trip # wins when it maps to exactly one unit's bookings. Else year +
    make/model via the same matcher as the Gmail dump. Yearless Corolla
    stays unmatched.
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
    uid = turo_inbox.match_unit(fake, roster)
    if uid:
        return uid
    hay = turo_inbox._norm_text(blob)
    hits: list[str] = []
    for unit in roster:
        year = str(unit.get("year") or "")
        model = str(unit.get("model") or "").lower()
        if not year or not model:
            continue
        if year in hay and model in hay:
            hits.append(str(unit["id"]))
    if len(hits) == 1:
        return hits[0]
    return None


def attach_invoice_items(
    items: Sequence[Mapping[str, Any]] | None,
    units: Sequence[Mapping[str, Any]],
    bookings_by_unit: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Split GT items into per-unit lists + unmatched leftover."""
    by_unit: dict[str, list[dict[str, Any]]] = {
        str(u["id"]): [] for u in units if isinstance(u, dict) and u.get("id")
    }
    unmatched: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        rec = dict(raw)
        uid = rec.get("unit_id") or match_invoice_unit(rec, units, bookings_by_unit)
        rec["unit_id"] = uid
        if uid and uid in by_unit:
            by_unit[uid].append(rec)
        else:
            unmatched.append(rec)
    return {"by_unit": by_unit, "unmatched": unmatched}
