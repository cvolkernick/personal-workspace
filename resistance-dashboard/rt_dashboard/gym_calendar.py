"""FitDash gym sessions → Google Calendar (mirror of meal_calendar).

Coach places one tagged 1.5h ``Gym`` event per training day at plan-generation
time. Rest days get no event; a leftover tagged event is deleted.

Reconciliation contract (coach + assistant-side monitor):
- Idempotency key is ``[fitdash-gym:YYYY-MM-DD]`` — exactly one event per date.
- Coach owns the default: creates tagged events when the workout plan is built.
- Monitor may create if a training day has none (coach never ran / no session).
- Monitor may move a tagged event when its window now overlaps another event.
- Neither side moves an event whose start ≠ ``fitdashGymPlannedStart``
  (user moved it = locked). Monitor reports any move it makes.
- Rest day: delete the tagged event (even if the coach didn't).
- Session-bound Calendar API only (main login, not Health OAuth). Stored
  refresh tokens / headless daily run are out of scope (#609).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from . import gcal_session as gcal

PROP_GYM = "fitdashGym"
PROP_DATE = "fitdashGymDate"
PROP_PLANNED_START = "fitdashGymPlannedStart"

DESC_TAG_RE = re.compile(r"\[fitdash-gym:(\d{4}-\d{2}-\d{2})\]")

DURATION = timedelta(hours=1, minutes=30)
POPUP_REMINDER_MINUTES = 30
GYM_TZ_NAME = "America/New_York"
BUSYNESS_REL = "fitness/gym/busyness.json"
EVENT_TITLE = "Gym"

HOME_LOCATION = "Planet Fitness, 3853 Cleveland Ave, Fort Myers, FL 33901"
ALT_LOCATION = "18911 S Tamiami Trail, Fort Myers, FL"
SECOND_ALT_LOCATION = "15201 N Cleveland Ave, North Fort Myers"
HOME_CLOSED_START = "2026-09-17T12:00:00"
HOME_CLOSED_END = "2026-09-19T12:00:00"

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_FALLBACK_RANKED = {
    "mon": [("05:00", "06:30", 30), ("22:30", "00:00", 33), ("06:30", "08:00", 38)],
    "tue": [("05:00", "06:30", 31), ("22:30", "00:00", 35), ("07:00", "08:30", 43)],
    "wed": [("22:30", "00:00", 30), ("05:00", "06:30", 31), ("06:30", "08:00", 40)],
    "thu": [("22:00", "23:30", 28), ("05:00", "06:30", 30), ("06:30", "08:00", 37)],
    "fri": [("05:00", "06:30", 30), ("21:30", "23:00", 45), ("07:00", "08:30", 47)],
    "sat": [("05:00", "06:30", 20), ("22:00", "23:30", 32), ("07:00", "08:30", 40)],
    "sun": [("05:00", "06:30", 18), ("22:00", "23:30", 23), ("07:00", "08:30", 30)],
}


@dataclass(frozen=True)
class QuietWindow:
    start_hhmm: str
    end_hhmm: str
    occupancy_pct: int


@dataclass
class GymDay:
    day: str
    is_rest: bool = False
    session_type: str = ""


@dataclass
class ChosenSlot:
    start: datetime
    end: datetime
    occupancy_pct: int
    window: QuietWindow


def gym_tz() -> ZoneInfo:
    return ZoneInfo(GYM_TZ_NAME)


def weekday_key(day: str) -> str:
    dt = datetime.strptime(str(day)[:10], "%Y-%m-%d")
    return _WEEKDAYS[dt.weekday()]


def gym_desc_tag(day: str) -> str:
    return f"[fitdash-gym:{str(day)[:10]}]"


def parse_dt(raw: str) -> Optional[datetime]:
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
        dt = dt.replace(tzinfo=gym_tz())
    return dt


def clock_label(dt: datetime) -> str:
    """Gym clock in America/New_York, matching nutrition eat_at_label (h:mm AM/PM)."""
    local = dt.astimezone(gym_tz()) if dt.tzinfo else dt.replace(tzinfo=gym_tz())
    h24 = local.hour
    h = h24 % 12 or 12
    return f"{h}:{local.minute:02d} {'AM' if h24 < 12 else 'PM'}"


def gym_clock_label_from_event(ev: dict) -> str:
    dt = parse_dt(event_start_iso(ev))
    if dt is None:
        return ""
    return clock_label(dt)


def same_instant(a: str, b: str) -> bool:
    da, db = parse_dt(a), parse_dt(b)
    if da is None or db is None:
        return str(a or "").strip() == str(b or "").strip()
    return da.astimezone(timezone.utc) == db.astimezone(timezone.utc)


def _busyness_candidates() -> List[Path]:
    here = Path(__file__).resolve()
    rel = Path(BUSYNESS_REL)
    ordered: List[Path] = []
    if len(here.parents) >= 3:
        ordered.append(here.parents[2] / rel)
    if len(here.parents) >= 2:
        ordered.append(here.parents[1] / rel)
    cwd = Path.cwd().resolve()
    ordered.append(cwd / rel)
    seen = set()
    out: List[Path] = []
    for cand in ordered:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


_BUSYNESS_CACHE: Optional[dict] = None


def load_busyness() -> dict:
    global _BUSYNESS_CACHE
    if _BUSYNESS_CACHE is not None:
        return _BUSYNESS_CACHE
    for path in _busyness_candidates():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("ranked_windows"):
            _BUSYNESS_CACHE = data
            return data
    _BUSYNESS_CACHE = {}
    return _BUSYNESS_CACHE


def reset_busyness_cache() -> None:
    global _BUSYNESS_CACHE
    _BUSYNESS_CACHE = None


def ranked_windows_for(day: str) -> List[QuietWindow]:
    data = load_busyness()
    key = weekday_key(day)
    raw = (data.get("ranked_windows") or {}).get(key) or []
    windows: List[QuietWindow] = []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            start = str(row.get("start") or "").strip()
            end = str(row.get("end") or "").strip()
            if not start or not end:
                continue
            try:
                occ = int(round(float(row.get("occupancy_pct") or 0)))
            except (TypeError, ValueError):
                occ = 0
            windows.append(QuietWindow(start, end, occ))
    if windows:
        return windows
    return [
        QuietWindow(s, e, occ) for s, e, occ in _FALLBACK_RANKED.get(key, _FALLBACK_RANKED["mon"])
    ]


def occupancy_map(day: str) -> Dict[int, float]:
    data = load_busyness()
    key = weekday_key(day)
    raw = (data.get("occupancy") or {}).get(key) or {}
    out: Dict[int, float] = {}
    if isinstance(raw, dict):
        for hk, val in raw.items():
            try:
                out[int(hk)] = float(val)
            except (TypeError, ValueError):
                continue
    return out


def window_occupancy(occ: Dict[int, float], start_hour: float, duration_h: float = 1.5) -> float:
    total = 0.0
    covered = 0.0
    t = start_hour
    end = start_hour + duration_h
    while t < end - 1e-9:
        hour = int(t) % 24
        hour_end = float(int(t) + 1)
        slot = min(end - t, hour_end - t)
        if slot <= 0:
            break
        total += float(occ.get(hour, 100.0)) * slot
        covered += slot
        t += slot
    return total / covered if covered else 100.0


def _parse_hhmm(text: str) -> Tuple[int, int]:
    parts = str(text or "").strip().split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def window_bounds(day: str, window: QuietWindow, *, tz: Optional[ZoneInfo] = None) -> Tuple[datetime, datetime]:
    zone = tz or gym_tz()
    y, m, d = (int(p) for p in str(day)[:10].split("-"))
    sh, sm = _parse_hhmm(window.start_hhmm)
    eh, em = _parse_hhmm(window.end_hhmm)
    start = datetime(y, m, d, sh, sm, tzinfo=zone)
    end = datetime(y, m, d, eh, em, tzinfo=zone)
    if end <= start:
        end = end + timedelta(days=1)
    return start, end


def extra_windows_for(day: str) -> List[QuietWindow]:
    """Occupancy-ranked 30-min starts when the published shortlist is all busy."""
    occ = occupancy_map(day)
    if not occ:
        return []
    seen = {(w.start_hhmm, w.end_hhmm) for w in ranked_windows_for(day)}
    extras: List[Tuple[float, QuietWindow]] = []
    start_hour = 5.0
    while start_hour <= 22.5 + 1e-9:
        end_hour = start_hour + 1.5
        if end_hour > 24.0 + 1e-9:
            start_hour += 0.5
            continue
        sh, sm = int(start_hour), int(round((start_hour % 1) * 60))
        eh_raw = end_hour if end_hour < 24.0 - 1e-9 else 0.0
        eh, em = int(eh_raw), int(round((eh_raw % 1) * 60))
        start_hhmm = f"{sh:02d}:{sm:02d}"
        end_hhmm = f"{eh:02d}:{em:02d}"
        if (start_hhmm, end_hhmm) in seen:
            start_hour += 0.5
            continue
        avg = window_occupancy(occ, start_hour)
        extras.append((avg, QuietWindow(start_hhmm, end_hhmm, int(round(avg)))))
        start_hour += 0.5
    extras.sort(key=lambda row: (row[0], row[1].start_hhmm))
    return [w for _, w in extras]


def candidate_windows(day: str) -> List[QuietWindow]:
    return ranked_windows_for(day) + extra_windows_for(day)


def intervals_overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and b0 < a1


def event_interval(ev: dict, *, tz: Optional[ZoneInfo] = None) -> Optional[Tuple[datetime, datetime]]:
    zone = tz or gym_tz()
    start_block = (ev or {}).get("start") or {}
    end_block = (ev or {}).get("end") or {}
    if start_block.get("dateTime"):
        start = parse_dt(str(start_block["dateTime"]))
        end = parse_dt(str(end_block.get("dateTime") or ""))
        if start is None:
            return None
        if end is None:
            end = start + DURATION
        return start, end
    if start_block.get("date"):
        try:
            day0 = datetime.strptime(str(start_block["date"])[:10], "%Y-%m-%d")
            day1 = datetime.strptime(str(end_block.get("date") or start_block["date"])[:10], "%Y-%m-%d")
        except ValueError:
            return None
        start = datetime(day0.year, day0.month, day0.day, tzinfo=zone)
        end = datetime(day1.year, day1.month, day1.day, tzinfo=zone)
        if end <= start:
            end = start + timedelta(days=1)
        return start, end
    return None


def _private(ev: dict) -> dict:
    props = (ev or {}).get("extendedProperties") or {}
    private = props.get("private") if isinstance(props, dict) else {}
    return private if isinstance(private, dict) else {}


def event_gym_date(ev: dict) -> str:
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
    """True when the user moved the event off fitdashGymPlannedStart."""
    planned = event_planned_start(ev)
    current = event_start_iso(ev)
    if not planned or not current:
        return False
    return not same_instant(planned, current)


def is_gym_event(ev: dict, day: Optional[str] = None) -> bool:
    private = _private(ev)
    if str(private.get(PROP_GYM) or "") != "1" and not DESC_TAG_RE.search(
        str((ev or {}).get("description") or "")
    ):
        return False
    if day and event_gym_date(ev) != str(day)[:10]:
        return False
    return True


def home_closed_bounds() -> Tuple[datetime, datetime]:
    data = load_busyness()
    closed = data.get("home_closed") if isinstance(data.get("home_closed"), dict) else {}
    start = parse_dt(str(closed.get("start") or HOME_CLOSED_START)) or parse_dt(HOME_CLOSED_START)
    end = parse_dt(str(closed.get("end") or HOME_CLOSED_END)) or parse_dt(HOME_CLOSED_END)
    assert start is not None and end is not None
    if start.tzinfo is None:
        start = start.replace(tzinfo=gym_tz())
    if end.tzinfo is None:
        end = end.replace(tzinfo=gym_tz())
    return start, end


def location_for(start: datetime, end: datetime) -> Tuple[str, bool]:
    """Return (location, using_alternate). Alternate during home-club closure."""
    data = load_busyness()
    club = data.get("club") if isinstance(data.get("club"), dict) else {}
    home = str(club.get("home") or HOME_LOCATION)
    alt = str(club.get("alternate") or ALT_LOCATION)
    closed0, closed1 = home_closed_bounds()
    if intervals_overlap(start, end, closed0, closed1):
        return alt, True
    return home, False


def session_blurb(session_type: str) -> str:
    letter = str(session_type or "").strip().lower()
    if letter in ("push", "pull", "legs"):
        return f"{letter.capitalize()} day per FitDash"
    if letter and letter != "rest":
        return f"{letter.capitalize()} session per FitDash"
    return "Training day per FitDash"


def event_body(day: str, slot: ChosenSlot, *, session_type: str = "") -> dict[str, Any]:
    loc, alt = location_for(slot.start, slot.end)
    tag = gym_desc_tag(day)
    lines = [
        session_blurb(session_type),
        (
            f"Time placed in a least-busy window "
            f"(~{slot.occupancy_pct}% typical occupancy)."
        ),
    ]
    if alt:
        data = load_busyness()
        club = data.get("club") if isinstance(data.get("club"), dict) else {}
        second = str(club.get("second_alternate") or SECOND_ALT_LOCATION)
        reason = "equipment install"
        closed = data.get("home_closed") if isinstance(data.get("home_closed"), dict) else {}
        if closed.get("reason"):
            reason = str(closed["reason"])
        lines.append(
            f"Home club closed for {reason}; using {loc}. Second alternate: {second}."
        )
    lines.append(tag)
    start_iso = slot.start.isoformat(timespec="seconds")
    return {
        "summary": EVENT_TITLE,
        "location": loc,
        "description": "\n".join(lines),
        "start": {"dateTime": start_iso, "timeZone": GYM_TZ_NAME},
        "end": {
            "dateTime": slot.end.isoformat(timespec="seconds"),
            "timeZone": GYM_TZ_NAME,
        },
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": POPUP_REMINDER_MINUTES}],
        },
        "extendedProperties": {
            "private": {
                PROP_GYM: "1",
                PROP_DATE: str(day)[:10],
                PROP_PLANNED_START: start_iso,
            }
        },
        "status": "confirmed",
    }


def slot_overlaps_busy(slot: ChosenSlot, busy: Sequence[Tuple[datetime, datetime]]) -> bool:
    return any(intervals_overlap(slot.start, slot.end, b0, b1) for b0, b1 in busy)


def pick_slot(
    day: str,
    busy: Sequence[Tuple[datetime, datetime]],
    *,
    prefer: Optional[Tuple[datetime, datetime]] = None,
) -> ChosenSlot:
    """First ranked quiet stretch with no overlap. Keep prefer if still free."""
    if prefer is not None:
        p0, p1 = prefer
        if p1 - p0 >= timedelta(minutes=80) and not any(
            intervals_overlap(p0, p1, b0, b1) for b0, b1 in busy
        ):
            occ = 0
            for window in ranked_windows_for(day):
                w0, w1 = window_bounds(day, window)
                if w0 == p0 and w1 == p1:
                    occ = window.occupancy_pct
                    return ChosenSlot(p0, p1, occ, window)
            return ChosenSlot(
                p0,
                p1,
                occ,
                QuietWindow(p0.strftime("%H:%M"), p1.strftime("%H:%M"), occ),
            )
    first: Optional[ChosenSlot] = None
    for window in candidate_windows(day):
        start, end = window_bounds(day, window)
        slot = ChosenSlot(start, end, window.occupancy_pct, window)
        if first is None:
            first = slot
        if not slot_overlaps_busy(slot, busy):
            return slot
    if first is None:
        window = ranked_windows_for(day)[0]
        start, end = window_bounds(day, window)
        first = ChosenSlot(start, end, window.occupancy_pct, window)
    return first


def gym_day_from_workout(workout: Optional[dict], day: str) -> Optional[GymDay]:
    """Training or rest from a real plan. Empty/unknown boards do not create or delete."""
    plan = workout if isinstance(workout, dict) else {}
    letter = str(plan.get("session_type") or "").strip().lower()
    rest = bool(plan.get("is_rest_day")) or letter == "rest"
    if rest:
        return GymDay(day=str(day)[:10], is_rest=True, session_type="")
    has_lifts = bool(plan.get("exercises"))
    trained = bool(plan.get("already_trained_today"))
    if letter in ("push", "pull", "legs") or has_lifts or trained:
        return GymDay(day=str(day)[:10], is_rest=False, session_type=letter)
    return None


def list_day_gym_events(calendar_id: str, day: str) -> List[dict]:
    if not day:
        return []
    return gcal.list_events(
        calendar_id,
        private_props={PROP_GYM: "1", PROP_DATE: str(day)[:10]},
    )


def lookup_gym_clock_label(day: str) -> str:
    """Start clock of today's tagged gym event. Empty when none / no Calendar."""
    civil = str(day or "")[:10]
    if not civil:
        return ""
    try:
        status = gcal.credentials_status()
        if not status.get("ok"):
            return ""
        cal_id = gcal.resolve_calendar_id()
        events = list_day_gym_events(cal_id, civil)
    except Exception:  # noqa: BLE001 — display-only; never fail the quest payload
        return ""
    clocks: List[Tuple[datetime, str]] = []
    for ev in events:
        if not is_gym_event(ev, civil):
            continue
        label = gym_clock_label_from_event(ev)
        dt = parse_dt(event_start_iso(ev))
        if not label or dt is None:
            continue
        clocks.append((dt, label))
    if not clocks:
        return ""
    clocks.sort(key=lambda row: row[0])
    return clocks[0][1]


def gym_quest_label_for_day(day: str) -> str:
    """Blue quest header text, e.g. ``Gym · 5:00 AM``. Silent skip when no event."""
    clock = lookup_gym_clock_label(day)
    if not clock:
        return ""
    return f"{EVENT_TITLE} · {clock}"


def civil_day_bounds(day: str) -> Tuple[datetime, datetime]:
    zone = gym_tz()
    y, m, d = (int(p) for p in str(day)[:10].split("-"))
    start = datetime(y, m, d, tzinfo=zone)
    return start, start + timedelta(days=1)


def busy_intervals(
    calendar_id: str,
    day: str,
    *,
    ignore_ids: Optional[Iterable[str]] = None,
) -> List[Tuple[datetime, datetime]]:
    start, end = civil_day_bounds(day)
    ignore = {str(i) for i in (ignore_ids or []) if i}
    events = gcal.list_events(
        calendar_id,
        time_min=start.isoformat(timespec="seconds"),
        time_max=end.isoformat(timespec="seconds"),
    )
    out: List[Tuple[datetime, datetime]] = []
    for ev in events:
        eid = str(ev.get("id") or "")
        if eid and eid in ignore:
            continue
        if str(ev.get("transparency") or "").lower() == "transparent":
            continue
        if is_gym_event(ev, day):
            continue
        interval = event_interval(ev)
        if interval is None:
            continue
        out.append(interval)
    return out


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
        "moves": [],
    }


def sync_gym_sessions(
    days: Sequence[GymDay],
    *,
    now: Optional[datetime] = None,  # noqa: ARG001 — monitor/coach share signature
    role: str = "coach",
) -> Dict[str, Any]:
    """Upsert tagged gym events for training days; delete rest leftovers.

    ``role="coach"`` is plan generation. ``role="monitor"`` is the assistant
    daily reconcile: same tag, may create a missing event, may move on overlap,
    must not move a user-locked event, reports ``moves``.
    """
    status = gcal.credentials_status()
    if not status.get("ok"):
        return _scope_skip(status)
    try:
        cal_id = gcal.resolve_calendar_id()
        created = 0
        updated = 0
        deleted = 0
        locked = 0
        moves: List[dict] = []
        for gym_day in days:
            day = str(gym_day.day or "")[:10]
            if not day:
                continue
            existing = list_day_gym_events(cal_id, day)
            keep: Optional[dict] = None
            extras: List[dict] = []
            for ev in existing:
                if keep is None and ev.get("id"):
                    keep = ev
                else:
                    extras.append(ev)
            for ev in extras:
                if ev.get("id") and _delete_quiet(cal_id, str(ev["id"])):
                    deleted += 1
            if gym_day.is_rest:
                if keep and keep.get("id") and _delete_quiet(cal_id, str(keep["id"])):
                    deleted += 1
                continue
            ignore = [str(keep["id"])] if keep and keep.get("id") else []
            busy = busy_intervals(cal_id, day, ignore_ids=ignore)
            prefer = None
            if keep and not is_user_locked(keep):
                interval = event_interval(keep)
                if interval is not None:
                    prefer = interval
            if keep and is_user_locked(keep):
                locked += 1
                continue
            slot = pick_slot(day, busy, prefer=prefer)
            body = event_body(day, slot, session_type=gym_day.session_type)
            if keep and keep.get("id"):
                prev = event_start_iso(keep)
                gcal.update_event(cal_id, str(keep["id"]), body)
                updated += 1
                new_start = body["start"]["dateTime"]
                if prev and not same_instant(prev, new_start):
                    moves.append(
                        {
                            "day": day,
                            "event_id": str(keep["id"]),
                            "from": prev,
                            "to": new_start,
                            "reason": "overlap",
                            "role": role,
                        }
                    )
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
            "moves": moves,
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
            "moves": [],
        }


def reconcile_gym_sessions(
    days: Sequence[GymDay],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Assistant-side daily reconcile. See module docstring for ownership."""
    return sync_gym_sessions(days, now=now, role="monitor")


def sync_gym_from_workout(
    workout: Optional[dict],
    *,
    day: str,
    role: str = "coach",
) -> Dict[str, Any]:
    gym = gym_day_from_workout(workout, day)
    if gym is None:
        return {
            "ok": True,
            "skipped": True,
            "error": None,
            "error_code": "no_workout",
            "upserted": 0,
            "created": 0,
            "updated": 0,
            "deleted": 0,
            "locked": 0,
            "moves": [],
        }
    return sync_gym_sessions([gym], role=role)


def cancel_gym_for_day(day: str) -> Dict[str, Any]:
    """Delete the tagged gym event for a day that became rest."""
    return sync_gym_sessions([GymDay(day=str(day)[:10], is_rest=True)], role="coach")
