"""FitDash nightly sleep block → Google Calendar (successor of wind-down).

One tagged 9-hour ``Sleep`` event per night, timed from the sleep battery
``empty_at`` (start of wind-down). The block already bakes in the buffers:

- first 30 min = wind-down
- middle 8 h = sleep (default target)
- last 30 min = wake buffer

Duration is ``sleep_around_hours`` (sleep target + onset buffer, default 9h).
If ``planned_wake_at`` is after ``empty_at`` and in a plausible overnight
window, that instant is the end instead.

Reconciliation contract (same hybrid as #653 wind-down / #610 gym):
- Idempotency key is ``[fitdash-sleep:YYYY-MM-DD]`` — one event per night.
- Time is physiology: ``empty_at`` + duration. Do not shift for overlap.
- Re-sync updates the existing tagged event, never stacks a duplicate.
- Neither side moves an event whose start ≠ ``fitdashSleepPlannedStart``
  (user moved it = locked).
- Nearby unlocked tagged leftovers (``empty_at`` crossed midnight) are deleted.
- Manual / untagged Sleep events are never updated or deleted. Overlapping
  ones (Pulse) are flagged on the result until ownership is settled.
- Standalone ``[fitdash-winddown:*]`` events from #653 are retired (deleted
  when unlocked) once this block is written.
- Session-bound Calendar API only (main login, not Health OAuth). Stored
  refresh tokens / headless daily run are out of scope (#609).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import gcal_session as gcal
from .sleep_battery import (
    DEFAULT_ONSET_BUFFER_HOURS,
    DEFAULT_SLEEP_TARGET_HOURS,
)
from .timeutil import local_tz
from .winddown_calendar import (
    is_user_locked as is_winddown_locked,
    is_winddown_event,
    list_nearby_winddown_events,
    list_night_winddown_events,
)

PROP_SLEEP = "fitdashSleep"
PROP_DATE = "fitdashSleepDate"
PROP_PLANNED_START = "fitdashSleepPlannedStart"

DESC_TAG_RE = re.compile(r"\[fitdash-sleep:(\d{4}-\d{2}-\d{2})\]")

DEFAULT_DURATION = timedelta(
    hours=DEFAULT_SLEEP_TARGET_HOURS + DEFAULT_ONSET_BUFFER_HOURS
)
WINDDOWN_SLICE = timedelta(
    minutes=int(round((DEFAULT_ONSET_BUFFER_HOURS * 60) / 2))
)
POPUP_REMINDER_MINUTES = 10
EVENT_TITLE = "Sleep"
FALLBACK_TZ_NAME = "America/New_York"
FOREIGN_SLEEP_TITLES = frozenset(
    {"sleep", "sleep block", "night sleep", "nightly sleep"}
)
PLANNED_WAKE_MIN_HOURS = 4.0
PLANNED_WAKE_MAX_HOURS = 14.0


def sleep_tz(preferred: Optional[str] = None) -> ZoneInfo:
    tz = local_tz(preferred)
    return tz if isinstance(tz, ZoneInfo) else ZoneInfo(FALLBACK_TZ_NAME)


def tz_name_of(dt: datetime) -> str:
    key = getattr(dt.tzinfo, "key", None)
    if key:
        return str(key)
    return FALLBACK_TZ_NAME


def sleep_desc_tag(day: str) -> str:
    return f"[fitdash-sleep:{str(day)[:10]}]"


def parse_dt(raw: Any, *, tz: Optional[ZoneInfo] = None) -> Optional[datetime]:
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz or sleep_tz())
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
        dt = dt.replace(tzinfo=tz or sleep_tz())
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


def duration_from_battery(sleep_battery: Optional[dict]) -> timedelta:
    """9h default = sleep target + onset buffer (30m wind-down + 30m wake)."""
    if not isinstance(sleep_battery, dict):
        return DEFAULT_DURATION
    around = sleep_battery.get("sleep_around_hours")
    if around is not None and str(around).strip() != "":
        try:
            hours = float(around)
        except (TypeError, ValueError):
            hours = 0.0
        if hours >= 4.0:
            return timedelta(hours=hours)
    try:
        target = float(
            sleep_battery.get("sleep_target_hours") or DEFAULT_SLEEP_TARGET_HOURS
        )
    except (TypeError, ValueError):
        target = DEFAULT_SLEEP_TARGET_HOURS
    try:
        buffer = float(
            sleep_battery.get("onset_buffer_hours") or DEFAULT_ONSET_BUFFER_HOURS
        )
    except (TypeError, ValueError):
        buffer = DEFAULT_ONSET_BUFFER_HOURS
    hours = max(4.0, target + buffer)
    return timedelta(hours=hours)


def block_end(start: datetime, sleep_battery: Optional[dict]) -> datetime:
    """End is planned wake when it is a plausible overnight after empty_at."""
    default_end = start + duration_from_battery(sleep_battery)
    if not isinstance(sleep_battery, dict):
        return default_end
    planned = parse_dt(sleep_battery.get("planned_wake_at"))
    if planned is None or planned <= start:
        return default_end
    hours = (planned - start).total_seconds() / 3600.0
    if PLANNED_WAKE_MIN_HOURS <= hours <= PLANNED_WAKE_MAX_HOURS:
        return planned
    return default_end


def night_date_of(start: datetime) -> str:
    local = start.astimezone(start.tzinfo or sleep_tz())
    return local.strftime("%Y-%m-%d")


def _private(ev: dict) -> dict:
    props = (ev or {}).get("extendedProperties") or {}
    private = props.get("private") if isinstance(props, dict) else {}
    return private if isinstance(private, dict) else {}


def event_sleep_date(ev: dict) -> str:
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


def event_end_iso(ev: dict) -> str:
    end = ((ev or {}).get("end") or {}).get("dateTime") or ""
    return str(end).strip()


def is_user_locked(ev: dict) -> bool:
    """True when the user moved the event off fitdashSleepPlannedStart."""
    planned = event_planned_start(ev)
    current = event_start_iso(ev)
    if not planned or not current:
        return False
    return not same_instant(planned, current)


def is_sleep_event(ev: dict, day: Optional[str] = None) -> bool:
    private = _private(ev)
    if str(private.get(PROP_SLEEP) or "") != "1" and not DESC_TAG_RE.search(
        str((ev or {}).get("description") or "")
    ):
        return False
    if day and event_sleep_date(ev) != str(day)[:10]:
        return False
    return True


def is_foreign_sleep_event(ev: dict) -> bool:
    """Untagged Sleep (Pulse / manual). Never update or delete these."""
    if is_sleep_event(ev) or is_winddown_event(ev):
        return False
    summary = str((ev or {}).get("summary") or "").strip().lower()
    return summary in FOREIGN_SLEEP_TITLES


def events_overlap(ev: dict, start: datetime, end: datetime) -> bool:
    ev_start = parse_dt(event_start_iso(ev))
    ev_end = parse_dt(event_end_iso(ev)) or ev_start
    if ev_start is None or ev_end is None:
        return False
    return ev_start < end and ev_end > start


def event_body(
    day: str,
    start: datetime,
    end: datetime,
    *,
    flagged: Optional[List[dict]] = None,
) -> dict[str, Any]:
    tag = sleep_desc_tag(day)
    start_iso = start.isoformat(timespec="seconds")
    tz_name = tz_name_of(start)
    hours = max(0.0, (end - start).total_seconds() / 3600.0)
    wind_h = WINDDOWN_SLICE.total_seconds() / 3600.0
    mid_h = max(0.0, hours - 2.0 * wind_h)
    desc = (
        "FitDash sleep block (sleep battery empty_at → empty_at + "
        f"{hours:g}h).\n"
        f"First {wind_h:g}h = wind-down; middle {mid_h:g}h = sleep; "
        f"last {wind_h:g}h = wake buffer. "
        "Move this event to lock the time; untagged Sleep events are not touched.\n"
    )
    if flagged:
        desc += (
            "Note: overlapping untagged Sleep event(s) left in place "
            "(Pulse/other; do not overwrite until ownership sync).\n"
        )
    desc += tag
    return {
        "summary": EVENT_TITLE,
        "description": desc,
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
                PROP_SLEEP: "1",
                PROP_DATE: str(day)[:10],
                PROP_PLANNED_START: start_iso,
            }
        },
        "status": "confirmed",
    }


def list_night_sleep_events(calendar_id: str, day: str) -> List[dict]:
    if not day:
        return []
    return gcal.list_events(
        calendar_id,
        private_props={PROP_SLEEP: "1", PROP_DATE: str(day)[:10]},
    )


def list_nearby_sleep_events(calendar_id: str, start: datetime) -> List[dict]:
    return gcal.list_events(
        calendar_id,
        private_props={PROP_SLEEP: "1"},
        time_min=(start - timedelta(hours=36)).isoformat(timespec="seconds"),
        time_max=(start + timedelta(hours=14)).isoformat(timespec="seconds"),
    )


def list_window_events(
    calendar_id: str, start: datetime, end: datetime
) -> List[dict]:
    return gcal.list_events(
        calendar_id,
        time_min=start.isoformat(timespec="seconds"),
        time_max=end.isoformat(timespec="seconds"),
    )


def _delete_quiet(calendar_id: str, event_id: str) -> bool:
    try:
        result = gcal.delete_event(calendar_id, event_id)
        return bool(result.get("ok") or result.get("deleted") or True)
    except Exception:
        return False


def _flagged_row(ev: dict) -> dict[str, Any]:
    return {
        "id": str(ev.get("id") or ""),
        "summary": str(ev.get("summary") or ""),
        "start": event_start_iso(ev),
        "end": event_end_iso(ev),
        "reason": "foreign_sleep",
    }


def _collect_tagged(
    calendar_id: str, night: str, start: datetime
) -> List[dict]:
    tagged: List[dict] = []
    seen: set[str] = set()
    for ev in list_night_sleep_events(calendar_id, night) + list_nearby_sleep_events(
        calendar_id, start
    ):
        eid = str(ev.get("id") or "")
        if not eid or eid in seen:
            continue
        if not is_sleep_event(ev):
            continue
        seen.add(eid)
        tagged.append(ev)
    return tagged


def _split_keep_extras_stale(
    tagged: List[dict], night: str
) -> Tuple[Optional[dict], List[dict], List[dict]]:
    keep: Optional[dict] = None
    extras: List[dict] = []
    stale: List[dict] = []
    for ev in tagged:
        ev_day = event_sleep_date(ev) or night
        if ev_day == night:
            if keep is None:
                keep = ev
            else:
                extras.append(ev)
        else:
            stale.append(ev)
    return keep, extras, stale


def _retire_winddown(
    calendar_id: str, night: str, start: datetime
) -> Tuple[int, int]:
    """Delete unlocked #653 wind-down events; leave user-locked ones."""
    retired = 0
    locked = 0
    seen: set[str] = set()
    pool: List[dict] = []
    for ev in list_night_winddown_events(
        calendar_id, night
    ) + list_nearby_winddown_events(calendar_id, start):
        eid = str(ev.get("id") or "")
        if not eid or eid in seen or not is_winddown_event(ev):
            continue
        seen.add(eid)
        pool.append(ev)
    for ev in pool:
        if is_winddown_locked(ev):
            locked += 1
            continue
        if ev.get("id") and _delete_quiet(calendar_id, str(ev["id"])):
            retired += 1
    return retired, locked


def _flag_foreign(
    calendar_id: str, start: datetime, end: datetime
) -> List[dict]:
    flagged: List[dict] = []
    seen: set[str] = set()
    for ev in list_window_events(calendar_id, start, end):
        eid = str(ev.get("id") or "")
        if not eid or eid in seen:
            continue
        if not is_foreign_sleep_event(ev):
            continue
        if not events_overlap(ev, start, end):
            continue
        seen.add(eid)
        flagged.append(_flagged_row(ev))
    return flagged


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
        "winddown_retired": 0,
        "flagged": [],
        "flagged_count": 0,
        "night": None,
        "empty_at": None,
        "end_at": None,
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
        "winddown_retired": 0,
        "flagged": [],
        "flagged_count": 0,
        "night": None,
        "empty_at": None,
        "end_at": None,
    }


def sync_sleep_block_from_battery(
    sleep_battery: Optional[dict],
    *,
    role: str = "coach",
) -> Dict[str, Any]:
    """Upsert the tagged sleep block for this wake cycle's empty_at.

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
    end = block_end(start, sleep_battery)
    try:
        cal_id = gcal.resolve_calendar_id()
        created = 0
        updated = 0
        deleted = 0
        locked = 0

        tagged = _collect_tagged(cal_id, night, start)
        keep, extras, stale = _split_keep_extras_stale(tagged, night)

        for ev in extras:
            if ev.get("id") and _delete_quiet(cal_id, str(ev["id"])):
                deleted += 1

        for ev in stale:
            if is_user_locked(ev):
                locked += 1
                continue
            if ev.get("id") and _delete_quiet(cal_id, str(ev["id"])):
                deleted += 1

        winddown_retired, winddown_locked = _retire_winddown(cal_id, night, start)
        locked += winddown_locked

        flagged = _flag_foreign(cal_id, start, end)
        body = event_body(night, start, end, flagged=flagged)

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
                "winddown_retired": winddown_retired,
                "flagged": flagged,
                "flagged_count": len(flagged),
                "night": night,
                "empty_at": start.isoformat(timespec="seconds"),
                "end_at": end.isoformat(timespec="seconds"),
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
            "winddown_retired": winddown_retired,
            "flagged": flagged,
            "flagged_count": len(flagged),
            "night": night,
            "empty_at": start.isoformat(timespec="seconds"),
            "end_at": end.isoformat(timespec="seconds"),
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
            "winddown_retired": 0,
            "flagged": [],
            "flagged_count": 0,
            "night": night,
            "empty_at": start.isoformat(timespec="seconds"),
            "end_at": end.isoformat(timespec="seconds"),
        }


def reconcile_sleep_block(
    sleep_battery: Optional[dict],
) -> Dict[str, Any]:
    """Assistant-side reconcile. Same tag; must not fight a locked event."""
    return sync_sleep_block_from_battery(sleep_battery, role="monitor")
