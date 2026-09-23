"""FitDash gym sessions → Google Calendar (mirror of meal_calendar).

Coach places one tagged 1.5h ``Gym`` event per training day at plan-generation
time. Rest days get no event; a leftover tagged event is deleted.

Reconciliation contract (one writer, one key) (#901):
- Idempotency key is the description marker ``[fitdash-gym:YYYY-MM-DD]``.
  Exactly one event per date. The private extended property is a stamp
  the morning writer sets at creation; it is not a second lookup key.
- Single writer: ``role="coach"`` (morning / plan generation) creates.
  Create always writes the description marker and private
  ``fitdashGym``, ``fitdashGymDate``, ``fitdashGymPlannedStart``.
- Reconcile (``role="monitor"``) only updates that event or deletes
  extras. It does not create. A second create is a bug.
- Lookup scans the civil-day agenda for the description marker, then
  unions extended-property hits, so a marker-only morning event is the
  same id.
- Monitor may move a tagged event when its window now overlaps another event.
- Neither side moves an event whose start ≠ ``fitdashGymPlannedStart``
  (user moved it = locked). Monitor reports any move it makes.
- Rest day: delete the tagged event (even if the coach didn't).
- Dashboard load (#811): if today's tagged event *end* has passed and
  ``ppl_logged_for_planning`` is empty, reschedule in place via ``pick_slot``
  from now through end of civil day. In-progress, logged, user-locked, and
  no-remaining-slot cases stay put. Never roll to tomorrow.
- Cross-club fallback (#813): home club first. If a ranked window fits at
  or under ``HOME_OCCUPANCY_THRESHOLD_PCT`` (default 50) and home is open,
  keep that pick. Otherwise score (slot × club) across home + Tamiami +
  N. Fort Myers on occupancy + drive-time penalty. Alt clubs without
  captured occupancy are skipped. If no pair is feasible, keep the home
  pick rather than placing nothing.
- Overnight starts are allowed (#818). Ranked quiet lists still prefer
  daytime/evening as a soft heuristic; ``prefer=`` and the monitor must
  not hard-reject a 00:00–03:59 local start.
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

CLUB_HOME = "home"
CLUB_ALT = "alternate"
CLUB_SECOND = "second_alternate"
CLUB_IDS = (CLUB_HOME, CLUB_ALT, CLUB_SECOND)
# Phase 2 occupancy trigger (#813). Overridable via busyness.json.
HOME_OCCUPANCY_THRESHOLD_PCT = 50
DRIVE_PENALTY_PER_MINUTE = 0.5
DEFAULT_DRIVE_MINUTES = {CLUB_HOME: 0, CLUB_ALT: 20, CLUB_SECOND: 15}

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
    workout_logged: bool = False


@dataclass
class ChosenSlot:
    start: datetime
    end: datetime
    occupancy_pct: int
    window: QuietWindow
    club_id: str = CLUB_HOME
    location: str = ""
    drive_minutes: int = 0


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


def _club_blob(club_id: str) -> dict:
    data = load_busyness()
    if club_id in (None, "", CLUB_HOME):
        return data if isinstance(data, dict) else {}
    clubs = data.get("clubs") if isinstance(data.get("clubs"), dict) else {}
    blob = clubs.get(club_id)
    return blob if isinstance(blob, dict) else {}


def occupancy_threshold() -> int:
    data = load_busyness()
    try:
        return int(round(float(data.get("home_occupancy_threshold_pct", HOME_OCCUPANCY_THRESHOLD_PCT))))
    except (TypeError, ValueError):
        return HOME_OCCUPANCY_THRESHOLD_PCT


def drive_penalty_per_minute() -> float:
    data = load_busyness()
    try:
        return float(data.get("drive_penalty_per_minute", DRIVE_PENALTY_PER_MINUTE))
    except (TypeError, ValueError):
        return DRIVE_PENALTY_PER_MINUTE


def drive_minutes_for(club_id: str) -> int:
    data = load_busyness()
    raw = data.get("drive_minutes") if isinstance(data.get("drive_minutes"), dict) else {}
    if club_id in raw:
        try:
            return int(round(float(raw[club_id])))
        except (TypeError, ValueError):
            pass
    blob = _club_blob(club_id)
    if blob.get("drive_minutes") is not None:
        try:
            return int(round(float(blob["drive_minutes"])))
        except (TypeError, ValueError):
            pass
    return int(DEFAULT_DRIVE_MINUTES.get(club_id, 0))


def club_address(club_id: str) -> str:
    data = load_busyness()
    club = data.get("club") if isinstance(data.get("club"), dict) else {}
    blob = _club_blob(club_id)
    if blob.get("address"):
        return str(blob["address"])
    if club_id == CLUB_ALT:
        return str(club.get("alternate") or ALT_LOCATION)
    if club_id == CLUB_SECOND:
        return str(club.get("second_alternate") or SECOND_ALT_LOCATION)
    return str(club.get("home") or HOME_LOCATION)


def _occupancy_table(club_id: str) -> dict:
    if club_id in (None, "", CLUB_HOME):
        data = load_busyness()
        raw = data.get("occupancy")
        return raw if isinstance(raw, dict) else {}
    blob = _club_blob(club_id)
    raw = blob.get("occupancy")
    return raw if isinstance(raw, dict) else {}


def club_has_occupancy(club_id: str) -> bool:
    return bool(_occupancy_table(club_id))


def _ranked_table(club_id: str) -> dict:
    if club_id in (None, "", CLUB_HOME):
        data = load_busyness()
        raw = data.get("ranked_windows")
        return raw if isinstance(raw, dict) else {}
    blob = _club_blob(club_id)
    raw = blob.get("ranked_windows")
    return raw if isinstance(raw, dict) else {}


def ranked_windows_for(day: str, club_id: str = CLUB_HOME) -> List[QuietWindow]:
    key = weekday_key(day)
    raw = _ranked_table(club_id).get(key) or []
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
    if club_id not in (None, "", CLUB_HOME):
        return []
    return [
        QuietWindow(s, e, occ) for s, e, occ in _FALLBACK_RANKED.get(key, _FALLBACK_RANKED["mon"])
    ]


def occupancy_map(day: str, club_id: str = CLUB_HOME) -> Dict[int, float]:
    key = weekday_key(day)
    raw = _occupancy_table(club_id).get(key) or {}
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


def _clock(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(gym_tz())
    if now.tzinfo is None:
        return now.replace(tzinfo=gym_tz())
    return now


def extra_windows_for(day: str, club_id: str = CLUB_HOME) -> List[QuietWindow]:
    """Occupancy-ranked 30-min starts when the published shortlist is all busy."""
    occ = occupancy_map(day, club_id)
    if not occ:
        return []
    seen = {(w.start_hhmm, w.end_hhmm) for w in ranked_windows_for(day, club_id)}
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


def candidate_windows(day: str, club_id: str = CLUB_HOME) -> List[QuietWindow]:
    return ranked_windows_for(day, club_id) + extra_windows_for(day, club_id)


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
    if slot.location:
        loc = slot.location
        alt = slot.club_id != CLUB_HOME
        packed = alt and not home_closed_for(slot.start, slot.end)
    else:
        loc, alt = location_for(slot.start, slot.end)
        packed = False
    tag = gym_desc_tag(day)
    lines = [
        session_blurb(session_type),
        (
            f"Time placed in a least-busy window "
            f"(~{slot.occupancy_pct}% typical occupancy)."
        ),
    ]
    if packed:
        lines.append(
            f"Home club packed; using {loc} "
            f"(~{drive_minutes_for(slot.club_id)} min drive)."
        )
    elif alt:
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
    not_before: Optional[datetime] = None,
) -> Optional[ChosenSlot]:
    """First free quiet stretch. Keep prefer if still free. None if none free.

    ``not_before`` (dashboard elapsed-reschedule) restricts the search to
    windows whose start is >= that instant — remaining civil day, not the
    already-elapsed morning. Overnight prefer is kept (#818).
    """
    cutoff = None
    if not_before is not None:
        cutoff = not_before if not_before.tzinfo else not_before.replace(tzinfo=gym_tz())
        cutoff = cutoff.astimezone(gym_tz())
    if prefer is not None:
        p0, p1 = prefer
        prefer_ok = cutoff is None or p0 >= cutoff
        if (
            prefer_ok
            and p1 - p0 >= timedelta(minutes=80)
            and not any(intervals_overlap(p0, p1, b0, b1) for b0, b1 in busy)
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
    for window in candidate_windows(day):
        start, end = window_bounds(day, window)
        if cutoff is not None and start < cutoff:
            continue
        slot = ChosenSlot(start, end, window.occupancy_pct, window)
        if not slot_overlaps_busy(slot, busy):
            return slot
    return None


def home_closed_for(start: datetime, end: datetime) -> bool:
    closed0, closed1 = home_closed_bounds()
    return intervals_overlap(start, end, closed0, closed1)


def club_open(club_id: str, start: datetime, end: datetime) -> bool:
    """True when the slot sits inside captured club hours. Missing hours = 24h."""
    blob = _club_blob(club_id)
    hours = blob.get("hours") if isinstance(blob.get("hours"), dict) else None
    if not hours:
        return True
    key = _WEEKDAYS[start.astimezone(gym_tz()).weekday()]
    raw = hours.get(key) or []
    if not isinstance(raw, list) or not raw:
        return True
    zone = gym_tz()
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    y, m, d = local_start.year, local_start.month, local_start.day
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        sh, sm = _parse_hhmm(str(row[0]))
        end_text = str(row[1]).strip()
        open0 = datetime(y, m, d, sh, sm, tzinfo=zone)
        if end_text in ("24:00", "24:00:00"):
            open1 = datetime(y, m, d, tzinfo=zone) + timedelta(days=1)
        else:
            eh, em = _parse_hhmm(end_text)
            open1 = datetime(y, m, d, eh, em, tzinfo=zone)
            if open1 <= open0:
                open1 = open1 + timedelta(days=1)
        if open0 <= local_start and local_end <= open1:
            return True
    return False


def slot_score(slot: ChosenSlot) -> float:
    return float(slot.occupancy_pct) + drive_penalty_per_minute() * float(slot.drive_minutes)


def _cutoff(not_before: Optional[datetime]) -> Optional[datetime]:
    if not_before is None:
        return None
    raw = not_before if not_before.tzinfo else not_before.replace(tzinfo=gym_tz())
    return raw.astimezone(gym_tz())


def ranked_window_fits(
    day: str,
    busy: Sequence[Tuple[datetime, datetime]],
    *,
    not_before: Optional[datetime] = None,
    club_id: str = CLUB_HOME,
) -> bool:
    cutoff = _cutoff(not_before)
    for window in ranked_windows_for(day, club_id):
        start, end = window_bounds(day, window)
        if cutoff is not None and start < cutoff:
            continue
        if not club_open(club_id, start, end):
            continue
        if club_id == CLUB_HOME and home_closed_for(start, end):
            continue
        slot = ChosenSlot(start, end, window.occupancy_pct, window)
        if not slot_overlaps_busy(slot, busy):
            return True
    return False


def _with_club(slot: ChosenSlot, club_id: str) -> ChosenSlot:
    loc = club_address(club_id)
    return ChosenSlot(
        start=slot.start,
        end=slot.end,
        occupancy_pct=slot.occupancy_pct,
        window=slot.window,
        club_id=club_id,
        location="" if club_id == CLUB_HOME else loc,
        drive_minutes=drive_minutes_for(club_id),
    )


def feasible_slots(
    day: str,
    busy: Sequence[Tuple[datetime, datetime]],
    *,
    club_id: str = CLUB_HOME,
    not_before: Optional[datetime] = None,
) -> List[ChosenSlot]:
    cutoff = _cutoff(not_before)
    out: List[ChosenSlot] = []
    for window in candidate_windows(day, club_id):
        start, end = window_bounds(day, window)
        if cutoff is not None and start < cutoff:
            continue
        if not club_open(club_id, start, end):
            continue
        if club_id == CLUB_HOME and home_closed_for(start, end):
            continue
        slot = ChosenSlot(start, end, window.occupancy_pct, window)
        if slot_overlaps_busy(slot, busy):
            continue
        out.append(_with_club(slot, club_id))
    return out


def pick_placement(
    day: str,
    busy: Sequence[Tuple[datetime, datetime]],
    *,
    prefer: Optional[Tuple[datetime, datetime]] = None,
    not_before: Optional[datetime] = None,
) -> Optional[ChosenSlot]:
    """Two-phase hybrid (#813): home club first, then cross-club fallback.

    Phase 1 is today's ``pick_slot`` at the home club. Fallback runs only when
    no ranked window fits, home occupancy is above the threshold, home is
    closed, or no home slot exists. Alt clubs without captured occupancy are
    skipped. If no (slot, club) pair is feasible, keep the Phase 1 pick.
    """
    home_slot = pick_slot(day, busy, prefer=prefer, not_before=not_before)
    ranked_ok = ranked_window_fits(day, busy, not_before=not_before)
    closed = bool(home_slot and home_closed_for(home_slot.start, home_slot.end))
    packed = bool(
        home_slot is not None and home_slot.occupancy_pct > occupancy_threshold()
    )
    need_fallback = home_slot is None or packed or closed or not ranked_ok
    if not need_fallback:
        return home_slot
    pairs: List[ChosenSlot] = []
    for club_id in CLUB_IDS:
        if club_id != CLUB_HOME and not club_has_occupancy(club_id):
            continue
        pairs.extend(
            feasible_slots(day, busy, club_id=club_id, not_before=not_before)
        )
    alt_pairs = [slot for slot in pairs if slot.club_id != CLUB_HOME]
    if not alt_pairs:
        return home_slot
    pairs.sort(
        key=lambda slot: (
            slot_score(slot),
            slot.occupancy_pct,
            slot.drive_minutes,
            slot.start,
        )
    )
    return pairs[0]


def workout_logged_today(
    workout: Optional[dict],
    *,
    day: str,
    now: Optional[datetime] = None,
) -> bool:
    """True when a PPL session is already logged for planning today (#811).

    Prefers ``training_day.ppl_logged_for_planning`` when a session list is
    on the workout dict. Dashboard boards stamp that result as
    ``ppl_logged_today`` (and ``already_trained_today`` once the day is
    complete) — those stamps count when sessions are not attached.
    """
    plan = workout if isinstance(workout, dict) else {}
    sessions = plan.get("sessions")
    ctx = plan.get("context") if isinstance(plan.get("context"), dict) else {}
    wake = plan.get("last_wake_at") or ctx.get("last_wake_at")
    if isinstance(sessions, list):
        from .training_day import ppl_logged_for_planning

        return bool(
            ppl_logged_for_planning(
                sessions,
                as_of=str(day)[:10],
                last_wake_at=wake,
                now=now,
            )
        )
    pin = plan.get("ppl_logged_today") or ctx.get("ppl_logged_today")
    if str(pin or "").strip().lower() in ("push", "pull", "legs"):
        return True
    return bool(plan.get("already_trained_today") or ctx.get("already_trained_today"))


def gym_day_from_workout(
    workout: Optional[dict],
    day: str,
    *,
    now: Optional[datetime] = None,
) -> Optional[GymDay]:
    """Training or rest from a real plan. Empty/unknown boards do not create or delete."""
    plan = workout if isinstance(workout, dict) else {}
    letter = str(plan.get("session_type") or "").strip().lower()
    rest = bool(plan.get("is_rest_day")) or letter == "rest"
    logged = workout_logged_today(plan, day=day, now=now)
    if rest:
        return GymDay(
            day=str(day)[:10],
            is_rest=True,
            session_type="",
            workout_logged=logged,
        )
    has_lifts = bool(plan.get("exercises"))
    trained = bool(plan.get("already_trained_today"))
    if letter in ("push", "pull", "legs") or has_lifts or trained:
        return GymDay(
            day=str(day)[:10],
            is_rest=False,
            session_type=letter,
            workout_logged=logged,
        )
    return None


def list_day_gym_events(calendar_id: str, day: str) -> List[dict]:
    """Events for ``day``, keyed by the description marker.

    A morning event that only carries ``[fitdash-gym:YYYY-MM-DD]`` is the
    same event as one that also has ``fitdashGymDate``. The civil-day
    agenda is the primary scan. Extended-property hits are unioned so a
    tagged event whose start sits outside that window is still found.
    Description-marker hits come first so the morning id is the one kept
    when a later writer raced in a second copy.
    """
    civil = str(day or "")[:10]
    if not civil or not calendar_id:
        return []
    start, end = civil_day_bounds(civil)
    ordered: List[dict] = []
    seen = set()

    def _take(ev: dict) -> None:
        if not isinstance(ev, dict) or not is_gym_event(ev, civil):
            return
        eid = str(ev.get("id") or "")
        if not eid or eid in seen:
            return
        seen.add(eid)
        ordered.append(ev)

    for ev in gcal.list_events(
        calendar_id,
        time_min=start.isoformat(timespec="seconds"),
        time_max=end.isoformat(timespec="seconds"),
    ):
        _take(ev)
    for ev in gcal.list_events(
        calendar_id,
        private_props={PROP_GYM: "1", PROP_DATE: civil},
    ):
        _take(ev)
    return ordered


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
    now: Optional[datetime] = None,
    role: str = "coach",
) -> Dict[str, Any]:
    """Upsert tagged gym events for training days; delete rest leftovers.

    ``role="coach"`` is the single writer (morning / plan generation). It
    creates when the day has no tagged event, and that create stamps the
    description marker plus private extended properties. ``role="monitor"``
    only updates the existing event or deletes extras — a missing event
    stays missing. Either role may move on overlap, must not move a
    user-locked event, and reports ``moves``. Overnight starts are kept
    when they are the selected slot
    (#818). On dashboard load, an elapsed unlocked window with no logged
    workout is moved to a remaining quiet slot (never tomorrow).
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
            # Reconcile never creates. The morning/coach path is the only
            # writer (#901). A day the morning writer skipped stays empty.
            if keep is None and role == "monitor":
                continue
            ignore = [str(keep["id"])] if keep and keep.get("id") else []
            busy = busy_intervals(cal_id, day, ignore_ids=ignore)
            prefer = None
            elapsed_unlogged = False
            clock = _clock(now)
            if keep and is_user_locked(keep):
                locked += 1
                continue
            if keep:
                interval = event_interval(keep)
                if interval is not None:
                    clock_day = clock.astimezone(gym_tz()).strftime("%Y-%m-%d")
                    if (
                        interval[1] <= clock
                        and clock_day == day
                        and not gym_day.workout_logged
                    ):
                        # Full window elapsed, no log: remaining civil day only.
                        elapsed_unlogged = True
                    else:
                        prefer = interval
            slot = pick_placement(
                day,
                busy,
                prefer=prefer,
                not_before=clock if elapsed_unlogged else None,
            )
            if slot is None:
                continue
            body = event_body(day, slot, session_type=gym_day.session_type)
            if keep and keep.get("id"):
                prev = event_start_iso(keep)
                gcal.update_event(cal_id, str(keep["id"]), body)
                updated += 1
                new_start = body["start"]["dateTime"]
                if prev and not same_instant(prev, new_start):
                    if elapsed_unlogged:
                        reason = "elapsed"
                    else:
                        reason = "overlap"
                    moves.append(
                        {
                            "day": day,
                            "event_id": str(keep["id"]),
                            "from": prev,
                            "to": new_start,
                            "reason": reason,
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
    """Update or delete the day's gym event. Never creates (#901)."""
    return sync_gym_sessions(days, now=now, role="monitor")


def sync_gym_from_workout(
    workout: Optional[dict],
    *,
    day: str,
    role: str = "coach",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    gym = gym_day_from_workout(workout, day, now=now)
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
    return sync_gym_sessions([gym], role=role, now=now)


def cancel_gym_for_day(day: str) -> Dict[str, Any]:
    """Delete the tagged gym event for a day that became rest."""
    return sync_gym_sessions([GymDay(day=str(day)[:10], is_rest=True)], role="coach")
