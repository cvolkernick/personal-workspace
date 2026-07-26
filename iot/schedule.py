"""Routines / schedule config and pure evaluation (no network control)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

from iot.solar import event_datetime_local, sun_times_local

IOT_DIR = Path(__file__).resolve().parent
DEFAULT_SCHEDULE_PATH = IOT_DIR / "schedule.json"
DEFAULT_STATE_PATH = IOT_DIR / "data" / "schedule_state.json"

# Fire window: if we're within this many minutes after the event and haven't
# fired yet today, run it (covers server restarts / poll interval).
# Also used as the retry window when a fire failed (we only mark fired on ok).
FIRE_WINDOW_MINUTES = 30

# Append-only log of schedule fire attempts (for postmortems).
DEFAULT_FIRE_LOG_PATH = IOT_DIR / "data" / "schedule_fire_log.jsonl"


def load_schedule(path: Optional[Path | str] = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_SCHEDULE_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("schedule.json must be an object")
    data.setdefault("location", {})
    data.setdefault("routines", [])
    return data


def save_schedule(data: Mapping[str, Any], path: Optional[Path | str] = None) -> None:
    p = Path(path) if path else DEFAULT_SCHEDULE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_state(path: Optional[Path | str] = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_STATE_PATH
    if not p.is_file():
        return {"fired": {}}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"fired": {}}
    data.setdefault("fired", {})
    return data


def save_state(state: Mapping[str, Any], path: Optional[Path | str] = None) -> None:
    p = Path(path) if path else DEFAULT_STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def resolve_timezone(name: Optional[str] = None) -> str:
    if name:
        return name
    # macOS / Linux: try local zone name
    try:
        link = os.readlink("/etc/localtime")  # type: ignore[attr-defined]
        # .../zoneinfo/America/Denver
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except Exception:  # noqa: BLE001
        pass
    env = os.environ.get("TZ")
    if env:
        return env
    # fallback from system offset is messy; default UTC
    return "UTC"


def location_from_schedule(sched: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    loc = dict(sched.get("location") or {})
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        return None
    try:
        return {
            "latitude": float(lat),
            "longitude": float(lon),
            "timezone": resolve_timezone(loc.get("timezone")),
            "label": loc.get("label") or "",
        }
    except (TypeError, ValueError):
        return None


def list_routines(sched: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
    s = dict(sched) if sched is not None else load_schedule()
    out = []
    for r in s.get("routines") or []:
        if isinstance(r, dict) and r.get("id"):
            out.append(dict(r))
    return out


def upcoming_for_day(
    sched: Mapping[str, Any],
    d: Optional[date] = None,
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Describe each enabled routine's fire time for a local calendar day."""
    loc = location_from_schedule(sched)
    if not loc:
        return []
    tz = ZoneInfo(loc["timezone"])
    if d is None:
        n = now or datetime.now(tz)
        if n.tzinfo is None:
            n = n.replace(tzinfo=tz)
        d = n.astimezone(tz).date()
    items = []
    for r in list_routines(sched):
        if not r.get("enabled", True):
            continue
        trigger = str(r.get("trigger") or "").lower()
        if trigger not in ("sunrise", "sunset"):
            continue
        when = event_datetime_local(
            d,
            trigger,
            loc["latitude"],
            loc["longitude"],
            loc["timezone"],
            int(r.get("offset_minutes") or 0),
        )
        items.append(
            {
                "id": r["id"],
                "name": r.get("name") or r["id"],
                "trigger": trigger,
                "offset_minutes": int(r.get("offset_minutes") or 0),
                "target": r.get("target") or "all",
                "color": r.get("color") or "warm",
                "brightness": r.get("brightness"),
                "fire_at": when.isoformat() if when else None,
                "fire_hhmm": when.strftime("%H:%M") if when else None,
                "enabled": True,
            }
        )
    return items


def fire_key(routine_id: str, d: date) -> str:
    return f"{routine_id}:{d.isoformat()}"


def due_routines(
    sched: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    window_minutes: int = FIRE_WINDOW_MINUTES,
) -> list[dict[str, Any]]:
    """Routines that should fire now (within window after event, not yet fired)."""
    loc = location_from_schedule(sched)
    if not loc:
        return []
    tz = ZoneInfo(loc["timezone"])
    n = now or datetime.now(tz)
    if n.tzinfo is None:
        n = n.replace(tzinfo=tz)
    else:
        n = n.astimezone(tz)
    d = n.date()
    fired = dict((state.get("fired") or {}))
    due = []
    for item in upcoming_for_day(sched, d, now=n):
        if not item.get("fire_at"):
            continue
        when = datetime.fromisoformat(item["fire_at"])
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
        # Only after the event, within window
        if n < when:
            continue
        if n > when + timedelta(minutes=window_minutes):
            continue
        key = fire_key(item["id"], d)
        if fired.get(key):
            continue
        due.append({**item, "fire_key": key})
    return due


def mark_fired(
    state: dict[str, Any],
    fire_key_str: str,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    out = dict(state)
    fired = dict(out.get("fired") or {})
    fired[fire_key_str] = (now or datetime.now(timezone.utc)).isoformat()
    # prune old keys (keep ~14 days)
    out["fired"] = fired
    return out


def schedule_status(
    sched: Optional[Mapping[str, Any]] = None,
    state: Optional[Mapping[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    s = dict(sched) if sched is not None else load_schedule()
    st = dict(state) if state is not None else load_state()
    loc = location_from_schedule(s)
    tz_name = loc["timezone"] if loc else resolve_timezone()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = timezone.utc
        tz_name = "UTC"
    n = now or datetime.now(tz)
    if n.tzinfo is None:
        n = n.replace(tzinfo=tz)
    else:
        n = n.astimezone(tz)
    today = n.date()
    sun = None
    if loc:
        sun = sun_times_local(today, loc["latitude"], loc["longitude"], tz_name)
    upcoming = upcoming_for_day(s, today, now=n)
    # also tomorrow so "next" can cross midnight
    tomorrow = upcoming_for_day(s, today + timedelta(days=1), now=n)
    # annotate fired
    fired = st.get("fired") or {}
    for u in upcoming:
        u["fired_today"] = bool(fired.get(fire_key(u["id"], today)))
        try:
            when = datetime.fromisoformat(u["fire_at"]) if u.get("fire_at") else None
            if when is not None:
                if when.tzinfo is None:
                    when = when.replace(tzinfo=tz)
                u["seconds_until"] = int((when - n).total_seconds())
        except Exception:  # noqa: BLE001
            u["seconds_until"] = None

    next_event = None
    candidates = []
    for u in upcoming + tomorrow:
        if not u.get("fire_at"):
            continue
        try:
            when = datetime.fromisoformat(u["fire_at"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=tz)
            if when >= n:
                candidates.append((when, u))
        except Exception:  # noqa: BLE001
            continue
    if candidates:
        candidates.sort(key=lambda x: x[0])
        when, u = candidates[0]
        next_event = {
            "id": u["id"],
            "name": u.get("name") or u["id"],
            "trigger": u.get("trigger"),
            "fire_at": when.isoformat(),
            "fire_hhmm": when.strftime("%H:%M"),
            "seconds_until": int((when - n).total_seconds()),
            "target": u.get("target"),
            "color": u.get("color"),
        }

    return {
        "ok": True,
        "now_local": n.isoformat(),
        "location": loc,
        "location_configured": loc is not None,
        "sun_today": sun,
        "routines": list_routines(s),
        "upcoming_today": upcoming,
        "next_event": next_event,
        "window_minutes": FIRE_WINDOW_MINUTES,
        "note": (
            None
            if loc
            else "Set location.latitude and location.longitude in schedule.json "
            "(or POST /api/schedule/location) for sunrise/sunset routines."
        ),
    }


ControlFn = Callable[[str, str, Optional[int]], dict[str, Any]]


def _invoke_control(control: ControlFn, item: Mapping[str, Any]) -> dict[str, Any]:
    bri = item.get("brightness")
    bri_i = int(bri) if bri is not None else None
    try:
        if bri_i is not None:
            return control(str(item["target"]), str(item["color"]), bri_i)
        return control(str(item["target"]), str(item["color"]), None)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _append_fire_log(
    entry: Mapping[str, Any], path: Optional[Path] = None
) -> None:
    p = path or DEFAULT_FIRE_LOG_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def run_due(
    *,
    control: ControlFn,
    schedule_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Evaluate due routines and invoke control(target, color, brightness).

    Only marks a routine as fired when control reports ok — failures stay
    eligible for retry until the fire window ends.
    """
    sched = load_schedule(schedule_path)
    state = load_state(state_path)
    due = due_routines(sched, state, now=now)
    results = []
    for item in due:
        cr = _invoke_control(control, item)
        ok = bool(cr.get("ok"))
        entry = {
            "ts": (now or datetime.now(timezone.utc)).isoformat(),
            "routine_id": item.get("id"),
            "fire_key": item.get("fire_key"),
            "target": item.get("target"),
            "color": item.get("color"),
            "ok": ok,
            "control": cr,
        }
        _append_fire_log(entry)
        # Only suppress retries when the action succeeded
        if ok:
            state = mark_fired(state, item["fire_key"], now=now)
            save_state(state, state_path)
        results.append({"routine": item, "control": cr})
    return results


def run_routine_now(
    routine_id: str,
    *,
    control: ControlFn,
    schedule_path: Optional[Path] = None,
    state_path: Optional[Path] = None,
    mark: bool = False,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Manually run a routine's action immediately (does not require sun window).

    If mark=True, records as fired for today so the auto scheduler skips it.
    """
    sched = load_schedule(schedule_path)
    routines = {r["id"]: r for r in list_routines(sched)}
    r = routines.get(routine_id)
    if not r:
        return {"ok": False, "error": f"unknown routine: {routine_id}"}
    item = {
        "id": r["id"],
        "name": r.get("name") or r["id"],
        "target": r.get("target") or "all",
        "color": r.get("color") or "warm",
        "brightness": r.get("brightness"),
        "trigger": r.get("trigger"),
    }
    cr = _invoke_control(control, item)
    if mark:
        loc = location_from_schedule(sched)
        tz_name = loc["timezone"] if loc else resolve_timezone()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            tz = timezone.utc
        n = now or datetime.now(tz)
        if n.tzinfo is None:
            n = n.replace(tzinfo=tz)
        else:
            n = n.astimezone(tz)
        state = load_state(state_path)
        state = mark_fired(state, fire_key(routine_id, n.date()), now=n)
        save_state(state, state_path)
    return {"ok": bool(cr.get("ok")), "routine": item, "control": cr}
