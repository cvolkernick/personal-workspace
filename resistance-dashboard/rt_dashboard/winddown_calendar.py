"""FitDash nightly wind-down → Google Calendar (superseded by sleep block).

#672 writes a 9h tagged Sleep block that already includes this 30 min
window. New daily-tasks publishes go through ``sleep_block_calendar``;
this module stays for tests and for retiring leftover tagged wind-downs.

One tagged 30-minute ``Wind-down`` event per night, timed from the sleep
battery ``empty_at`` (start of wind-down, not already-asleep). FitDash
never wrote bedtime to Calendar before; untagged Sleep events from
elsewhere are left alone.

Reconciliation contract (same hybrid as #610 / #611 gym):
- Idempotency key is ``[fitdash-winddown:YYYY-MM-DD]`` — one event per night.
- Time is physiology: ``empty_at`` + 30 min. Do not shift for calendar overlap.
- Re-sync updates the existing tagged event, never stacks a duplicate.
- Neither side moves an event whose start ≠ ``fitdashWinddownPlannedStart``
  (user moved it = locked).
- Nearby unlocked tagged leftovers (``empty_at`` crossed midnight) are deleted.
- Session-bound Calendar API only (main login, not Health OAuth). Stored
  refresh tokens / headless daily run are out of scope (#609).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import gcal_session as gcal
from .sleep_battery import DEFAULT_ONSET_BUFFER_HOURS
from .timeutil import local_tz

PROP_WINDDOWN = "fitdashWinddown"
PROP_DATE = "fitdashWinddownDate"
PROP_PLANNED_START = "fitdashWinddownPlannedStart"

DESC_TAG_RE = re.compile(r"\[fitdash-winddown:(\d{4}-\d{2}-\d{2})\]")

# First half of the 1h around-sleep buffer (30 min wind-down + 30 min onset).
DURATION = timedelta(minutes=int(round((DEFAULT_ONSET_BUFFER_HOURS * 60) / 2)))
POPUP_REMINDER_MINUTES = 10
EVENT_TITLE = "Wind-down"
FALLBACK_TZ_NAME = "America/New_York"


def winddown_tz(preferred: Optional[str] = None) -> ZoneInfo:
    tz = local_tz(preferred)
    return tz if isinstance(tz, ZoneInfo) else ZoneInfo(FALLBACK_TZ_NAME)


def tz_name_of(dt: datetime) -> str:
    key = getattr(dt.tzinfo, "key", None)
    if key:
        return str(key)
    return FALLBACK_TZ_NAME


def winddown_desc_tag(day: str) -> str:
    return f"[fitdash-winddown:{str(day)[:10]}]"


def parse_dt(raw: Any, *, tz: Optional[ZoneInfo] = None) -> Optional[datetime]:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz or winddown_tz())
        return dt
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or winddown_tz())
    return dt


def same_instant(a: str, b: str) -> bool:
    da, db = parse_dt(a), parse_dt(b)
    if da is None or db is None:
        return str(a or "").strip() == str(b or "").strip()
    return da.astimezone(timezone.utc) == db.astimezone(timezone.utc)


def empty_at_from_battery(sleep_battery: Optional[dict]) -> Optional[datetime]:
    if not isinstance(sleep_battery, dict):
        return None
    return parse_dt(sleep_battery.get("empty_at"))


def night_date_of(start: datetime) -> str:
    local = start.astimezone(start.tzinfo or winddown_tz())
    return local.strftime("%Y-%m-%d")


def _private(ev: dict) -> dict:
    props = (ev or {}).get("extendedProperties") or {}
    private = props.get("private") if isinstance(props, dict) else {}
    return private if isinstance(private, dict) else {}


def event_winddown_date(ev: dict) -> str:
    private = _private(ev)
    day = str(private.get(PROP_DATE) or "").strip()[:10]
    if day:
        return day
    match = DESC_TAG_RE.search(str((ev or {}).get("description") or ""))
    return match.group(1) if match else ""


def event_planned_start(ev: dict) -> str:
    return str(_private(ev).get(PROP_PLANNED_START) or "").strip()


def event_start_iso(ev: dict) -> str:
    start = ((ev or {}).get("start") or {}).get("dateTime") or ""
    return str(start).strip()


def is_user_locked(ev: dict) -> bool:
    """True when the user moved the event off fitdashWinddownPlannedStart."""
    planned = event_planned_start(ev)
    current = event_start_iso(ev)
    if not planned or not current:
        return False
    return not same_instant(planned, current)


def is_winddown_event(ev: dict, day: Optional[str] = None) -> bool:
    private = _private(ev)
    if str(private.get(PROP_WINDDOWN) or "") != "1" and not DESC_TAG_RE.search(
        str((ev or {}).get("description") or "")
    ):
        return False
    if day and event_winddown_date(ev) != str(day)[:10]:
        return False
    return True


def event_body(day: str, start: datetime, end: datetime) -> dict[str, Any]:
    tag = winddown_desc_tag(day)
    start_iso = start.isoformat(timespec="seconds")
    tz_name = tz_name_of(start)
    return {
        "summary": EVENT_TITLE,
        "description": (
            "FitDash wind-down (sleep battery empty_at).\n"
            "Start of the 30 min wind-down before sleep onset. "
            "Move this event to lock the time; untagged Sleep events are not touched.\n"
            f"{tag}"
        ),
        "start": {"dateTime": start_iso, "timeZone": tz_name},
        "end": {
            "dateTime": end.isoformat(timespec="seconds"),
            "timeZone": tz_name,
        },
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": POPUP_REMINDER_MINUTES}],
        },
        "extendedProperties": {
            "private": {
                PROP_WINDDOWN: "1",
                PROP_DATE: str(day)[:10],
                PROP_PLANNED_START: start_iso,
            }
        },
        "status": "confirmed",
    }


def list_night_winddown_events(calendar_id: str, day: str) -> List[dict]:
    if not day:
        return []
    return gcal.list_events(
        calendar_id,
        private_props={PROP_WINDDOWN: "1", PROP_DATE: str(day)[:10]},
    )


def list_nearby_winddown_events(
    calendar_id: str, start: datetime
) -> List[dict]:
    return gcal.list_events(
        calendar_id,
        private_props={PROP_WINDDOWN: "1"},
        time_min=(start - timedelta(hours=36)).isoformat(timespec="seconds"),
        time_max=(start + timedelta(hours=12)).isoformat(timespec="seconds"),
    )


def _delete_quiet(calendar_id: str, event_id: str) -> bool:
    try:
        result = gcal.delete_event(calendar_id, event_id)
        return bool(result.get("ok") or result.get("deleted") or True)
    except Exception:
        return False


def _scope_skip(status: dict) -> dict[str, Any]:
    return {
        "ok": False,
        "skipped": True,
        "error": status.get("error") or gcal.MISSING_CALENDAR_SCOPE,
        "error_code": status.get("error_code") or "missing_calendar_scope",
        "upserted": 0,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "locked": 0,
        "night": None,
        "empty_at": None,
    }


def _no_empty_at() -> dict[str, Any]:
    return {
        "ok": True,
        "skipped": True,
        "error": None,
        "error_code": "no_empty_at",
        "upserted": 0,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "locked": 0,
        "night": None,
        "empty_at": None,
    }


def sync_winddown_from_battery(
    sleep_battery: Optional[dict],
    *,
    role: str = "coach",
) -> Dict[str, Any]:
    """Upsert the tagged wind-down for this wake cycle's empty_at.

    ``role="coach"`` is dashboard / daily-tasks. ``role="monitor"`` is the
    assistant reconcile: same tag, may create if missing, must not re-time
    a user-locked event. Time is never moved for calendar overlap.
    """
    start = empty_at_from_battery(sleep_battery)
    if start is None:
        return _no_empty_at()
    status = gcal.credentials_status()
    if not status.get("ok"):
        skip = _scope_skip(status)
        skip["empty_at"] = start.isoformat(timespec="seconds")
        skip["night"] = night_date_of(start)
        return skip
    night = night_date_of(start)
    end = start + DURATION
    body = event_body(night, start, end)
    try:
        cal_id = gcal.resolve_calendar_id()
        created = 0
        updated = 0
        deleted = 0
        locked = 0

        tagged: List[dict] = []
        seen: set[str] = set()
        for ev in list_night_winddown_events(cal_id, night) + list_nearby_winddown_events(
            cal_id, start
        ):
            eid = str(ev.get("id") or "")
            if not eid or eid in seen:
                continue
            if not is_winddown_event(ev):
                continue
            seen.add(eid)
            tagged.append(ev)

        keep: Optional[dict] = None
        extras: List[dict] = []
        stale: List[dict] = []
        for ev in tagged:
            ev_day = event_winddown_date(ev) or night
            if ev_day == night:
                if keep is None:
                    keep = ev
                else:
                    extras.append(ev)
            else:
                stale.append(ev)

        for ev in extras:
            if ev.get("id") and _delete_quiet(cal_id, str(ev["id"])):
                deleted += 1

        for ev in stale:
            if is_user_locked(ev):
                locked += 1
                continue
            if ev.get("id") and _delete_quiet(cal_id, str(ev["id"])):
                deleted += 1

        if keep and is_user_locked(keep):
            locked += 1
            return {
                "ok": True,
                "skipped": False,
                "error": None,
                "error_code": None,
                "calendar_id": cal_id,
                "role": role,
                "upserted": 0,
                "created": 0,
                "updated": 0,
                "deleted": deleted,
                "locked": locked,
                "night": night,
                "empty_at": start.isoformat(timespec="seconds"),
            }

        if keep and keep.get("id"):
            gcal.update_event(cal_id, str(keep["id"]), body)
            updated += 1
        else:
            gcal.create_event(cal_id, body)
            created += 1
        return {
            "ok": True,
            "skipped": False,
            "error": None,
            "error_code": None,
            "calendar_id": cal_id,
            "role": role,
            "upserted": created + updated,
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "locked": locked,
            "night": night,
            "empty_at": start.isoformat(timespec="seconds"),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "skipped": True,
            "error": str(exc),
            "error_code": "calendar_error",
            "upserted": 0,
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "locked": 0,
            "night": night,
            "empty_at": start.isoformat(timespec="seconds"),
        }


def reconcile_winddown(
    sleep_battery: Optional[dict],
) -> Dict[str, Any]:
    """Assistant-side reconcile. Same tag; must not fight a locked event."""
    return sync_winddown_from_battery(sleep_battery, role="monitor")
