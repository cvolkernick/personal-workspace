"""FitDash meal eat_at → Google Calendar timed reminders.

Google Tasks stay the checklist. Calendar is a reminder only — never a
second completion source of truth. No event when eat_at is missing.
Does not invent food or times.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from . import gcal_session as gcal

PROP_MEAL = "fitdashMeal"
PROP_DAY = "fitdashDay"
PROP_SLOT = "fitdashSlot"
PROP_TASK = "fitdashTaskId"
PROP_TASKS = "fitdashTaskIds"

DESC_TAG_RE = re.compile(r"\[fitdash-meal:(\d{4}-\d{2}-\d{2}):([^\]]+)\]")
TASKS_TAG_RE = re.compile(r"\[fitdash-tasks:([^\]]+)\]")
CAL_NOTE_RE = re.compile(r"\[fitdash-cal:([^\]]+)\]")
QUEST_MARK_RE = re.compile(r"\[fitdash-quest:(\d{4}-\d{2}-\d{2})\]")

DEFAULT_DURATION = timedelta(minutes=20)
POPUP_REMINDER_MINUTES = 10


@dataclass
class MealSlotReminder:
    day: str
    slot: str
    title: str
    eat_at: str
    task_ids: List[str] = field(default_factory=list)
    all_completed: bool = False
    next_eat_at: str = ""


def parse_eat_at(raw: str) -> Optional[datetime]:
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
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def event_end_iso(start: datetime, next_start: Optional[datetime] = None) -> str:
    """15–30m default (20m). Shorten if the block would overlap the next slot."""
    end = start + DEFAULT_DURATION
    if next_start is not None and start < next_start < end:
        end = next_start
    if end <= start:
        end = start + timedelta(minutes=1)
    return end.isoformat(timespec="seconds")


def eat_at_is_future(eat_at: str, *, now: Optional[datetime] = None) -> bool:
    """True when eat_at is still ahead of now. Past slots get zero events."""
    start = parse_eat_at(eat_at)
    if start is None:
        return False
    clock = now if now is not None else datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return start > clock.astimezone(start.tzinfo)


def should_create_missing(eat_at: str, *, now: Optional[datetime] = None) -> bool:
    """Create only when eat_at is still in the future (no uncomplete resurrect)."""
    return eat_at_is_future(eat_at, now=now)


def _grams_cue(portion_g: Any) -> str:
    """Only real portion_g. Do not invent grams from serving_label."""
    if portion_g is None or str(portion_g).strip() == "":
        return ""
    try:
        grams = float(portion_g)
    except (TypeError, ValueError):
        return ""
    if grams <= 0:
        return ""
    return f"{int(round(grams))}g"


def calendar_event_title(
    label: str, name: str, portion_g: Any = None
) -> str:
    """Meal label + primary item + portion_g. No invented food or grams."""
    label = str(label or "").strip()
    name = str(name or "").strip()
    grams = _grams_cue(portion_g)
    if name and grams:
        food = f"{name} {grams}"
    else:
        food = name
    if label and food:
        return f"{label} · {food}"
    return food or label


def meal_desc_tag(day: str, slot: str) -> str:
    return f"[fitdash-meal:{day}:{slot}]"


def tasks_desc_tag(task_ids: Sequence[str]) -> str:
    ids = [str(t).strip() for t in task_ids if str(t).strip()]
    if not ids:
        return ""
    return f"[fitdash-tasks:{','.join(ids)}]"


def event_body(slot: MealSlotReminder) -> dict[str, Any]:
    start = parse_eat_at(slot.eat_at)
    if start is None:
        raise ValueError("eat_at required")
    next_start = parse_eat_at(slot.next_eat_at) if slot.next_eat_at else None
    tags = [meal_desc_tag(slot.day, slot.slot)]
    tasks_tag = tasks_desc_tag(slot.task_ids)
    if tasks_tag:
        tags.append(tasks_tag)
    desc = (
        "FitDash meal reminder (checklist stays in Google Tasks).\n"
        + "\n".join(tags)
    )
    private = {
        PROP_MEAL: "1",
        PROP_DAY: slot.day,
        PROP_SLOT: slot.slot,
    }
    if slot.task_ids:
        private[PROP_TASK] = slot.task_ids[0]
        private[PROP_TASKS] = ",".join(slot.task_ids)
    title = (slot.title or "").strip()
    if not title:
        raise ValueError("calendar title required (no invented food)")
    return {
        "summary": title[:200],
        "description": desc,
        "start": {"dateTime": start.isoformat(timespec="seconds")},
        "end": {"dateTime": event_end_iso(start, next_start)},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": POPUP_REMINDER_MINUTES},
            ],
        },
        "extendedProperties": {"private": private},
        "status": "confirmed",
    }


def _private(ev: dict) -> dict:
    props = (ev or {}).get("extendedProperties") or {}
    private = props.get("private") if isinstance(props, dict) else {}
    return private if isinstance(private, dict) else {}


def event_slot_key(ev: dict) -> str:
    private = _private(ev)
    slot = str(private.get(PROP_SLOT) or "").strip()
    if slot:
        return slot
    match = DESC_TAG_RE.search(str((ev or {}).get("description") or ""))
    return match.group(2) if match else ""


def event_day(ev: dict) -> str:
    private = _private(ev)
    day = str(private.get(PROP_DAY) or "").strip()
    if day:
        return day
    match = DESC_TAG_RE.search(str((ev or {}).get("description") or ""))
    return match.group(1) if match else ""


def event_task_ids(ev: dict) -> List[str]:
    private = _private(ev)
    raw = str(private.get(PROP_TASKS) or private.get(PROP_TASK) or "")
    ids = [p.strip() for p in raw.split(",") if p.strip()]
    desc = str((ev or {}).get("description") or "")
    match = TASKS_TAG_RE.search(desc)
    if match:
        ids.extend(p.strip() for p in match.group(1).split(",") if p.strip())
    seen: set[str] = set()
    out: List[str] = []
    for tid in ids:
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def event_links_task(ev: dict, task_id: str) -> bool:
    tid = str(task_id or "").strip()
    if not tid:
        return False
    return tid in event_task_ids(ev)


def parse_cal_note(notes: str) -> str:
    match = CAL_NOTE_RE.search(notes or "")
    return match.group(1).strip() if match else ""


def quest_day_from_notes(notes: str) -> str:
    match = QUEST_MARK_RE.search(notes or "")
    return match.group(1) if match else ""


def list_day_meal_events(calendar_id: str, day: str) -> List[dict]:
    if not day:
        return []
    return gcal.list_events(
        calendar_id,
        private_props={PROP_MEAL: "1", PROP_DAY: day},
    )


def _delete_quiet(calendar_id: str, event_id: str) -> bool:
    try:
        result = gcal.delete_event(calendar_id, event_id)
        return bool(result.get("ok") or result.get("deleted") or True)
    except Exception:
        return False


def sync_meal_reminders(
    slots: Sequence[MealSlotReminder],
    *,
    day: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Upsert current eat_at slots; remove dropped / completed; no duplicates.

    Missing Calendar scope is an honest skip — Tasks checklist is unchanged.
    """
    status = gcal.credentials_status()
    if not status.get("ok"):
        return {
            "ok": False,
            "skipped": True,
            "error": status.get("error") or gcal.MISSING_CALENDAR_SCOPE,
            "error_code": status.get("error_code") or "missing_calendar_scope",
            "upserted": 0,
            "deleted": 0,
            "created": 0,
            "updated": 0,
        }
    try:
        cal_id = gcal.resolve_calendar_id()
        existing = list_day_meal_events(cal_id, day)
        by_slot: Dict[str, dict] = {}
        extras: List[dict] = []
        for ev in existing:
            key = event_slot_key(ev)
            if key and key not in by_slot:
                by_slot[key] = ev
            else:
                extras.append(ev)

        deleted = 0
        for ev in extras:
            if ev.get("id") and _delete_quiet(cal_id, str(ev["id"])):
                deleted += 1

        wanted = {s.slot: s for s in slots if s.slot and s.eat_at}
        for key, ev in list(by_slot.items()):
            slot = wanted.get(key)
            past = bool(slot and not eat_at_is_future(slot.eat_at, now=now))
            if slot is None or slot.all_completed or past:
                if ev.get("id") and _delete_quiet(cal_id, str(ev["id"])):
                    deleted += 1
                by_slot.pop(key, None)

        created = 0
        updated = 0
        for slot in slots:
            if not slot.eat_at or not slot.slot or slot.all_completed:
                continue
            if not eat_at_is_future(slot.eat_at, now=now):
                continue
            if not (slot.title or "").strip():
                continue
            body = event_body(slot)
            current = by_slot.get(slot.slot)
            if current and current.get("id"):
                gcal.update_event(cal_id, str(current["id"]), body)
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
            "upserted": created + updated,
            "created": created,
            "updated": updated,
            "deleted": deleted,
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
        }


def cancel_reminder_for_task(
    task_id: str,
    *,
    day: Optional[str] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """Delete/cancel the linked meal event. Best-effort; never invents events."""
    tid = str(task_id or "").strip()
    if not tid:
        return {"ok": False, "skipped": True, "error": "missing task_id", "deleted": 0}
    status = gcal.credentials_status()
    if not status.get("ok"):
        return {
            "ok": False,
            "skipped": True,
            "error": status.get("error") or gcal.MISSING_CALENDAR_SCOPE,
            "error_code": status.get("error_code"),
            "deleted": 0,
        }
    day = str(day or quest_day_from_notes(notes) or "")[:10]
    try:
        cal_id = gcal.resolve_calendar_id()
        deleted = 0
        note_event = parse_cal_note(notes)
        if note_event and _delete_quiet(cal_id, note_event):
            deleted += 1
        seen: set[str] = {note_event} if note_event else set()
        events: List[dict] = []
        if day:
            events.extend(list_day_meal_events(cal_id, day))
        events.extend(
            gcal.list_events(
                cal_id,
                private_props={PROP_MEAL: "1", PROP_TASK: tid},
            )
        )
        for ev in events:
            eid = str(ev.get("id") or "")
            if not eid or eid in seen:
                continue
            if event_links_task(ev, tid) or str(_private(ev).get(PROP_TASK) or "") == tid:
                if _delete_quiet(cal_id, eid):
                    deleted += 1
                    seen.add(eid)
        return {
            "ok": True,
            "skipped": False,
            "deleted": deleted,
            "calendar_id": cal_id,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "skipped": True,
            "error": str(exc),
            "error_code": "calendar_error",
            "deleted": 0,
        }


def _meal_is_optional(ev: dict) -> bool:
    """Fourth planner slot and any card titled Optional snack."""
    title = str((ev or {}).get("summary") or "").lower()
    if "optional snack" in title:
        return True
    return event_slot_key(ev).strip().lower() in {"meal-3", "optional-snack"}


def _meal_start_iso(ev: dict) -> str:
    start = ((ev or {}).get("start") or {}).get("dateTime") or ""
    return str(start).strip()


def _meal_time_zone(ev: dict) -> str:
    return str(((ev or {}).get("start") or {}).get("timeZone") or "").strip()


def _timed_edge(dt: datetime, time_zone: str) -> dict:
    node = {"dateTime": dt.isoformat(timespec="seconds")}
    if time_zone:
        node["timeZone"] = time_zone
    return node


def _is_meal_event(ev: dict) -> bool:
    if str(_private(ev).get(PROP_MEAL) or "") == "1":
        return True
    return bool(DESC_TAG_RE.search(str((ev or {}).get("description") or "")))


def _overlaps_window(dt: datetime, windows: Sequence[tuple]) -> bool:
    for start, end in windows:
        if start <= dt < end:
            return True
    return False


def _meal_end_before_sleep(start: datetime, windows: Sequence[tuple]) -> datetime:
    end = start + DEFAULT_DURATION
    for sleep_start, _sleep_end in windows:
        if start < sleep_start < end:
            end = sleep_start
    if end <= start:
        end = start + timedelta(minutes=1)
    return end


def _shifted_meal_body(ev: dict, new_start: datetime, windows: Sequence[tuple]) -> dict:
    zone = _meal_time_zone(ev)
    body = {
        "summary": ev.get("summary") or "",
        "description": ev.get("description") or "",
        "start": _timed_edge(new_start, zone),
        "end": _timed_edge(_meal_end_before_sleep(new_start, windows), zone),
        "status": ev.get("status") or "confirmed",
    }
    if ev.get("reminders"):
        body["reminders"] = ev["reminders"]
    if ev.get("extendedProperties"):
        body["extendedProperties"] = ev["extendedProperties"]
    return body


def reflow_meals_out_of_sleep(
    windows: Sequence[Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Upsert ``[fitdash-meal:*]`` chips so none overlap a sleep block.

    Same event id and tag. Optional snacks that cannot land before the
    chip are deleted. No new events and no calendar migration.
    """
    spans: List[tuple] = []
    for raw in windows or []:
        if isinstance(raw, dict):
            start = parse_eat_at(str(raw.get("start") or ""))
            end = parse_eat_at(str(raw.get("end") or ""))
        elif isinstance(raw, (tuple, list)) and len(raw) >= 2:
            start, end = raw[0], raw[1]
            if not isinstance(start, datetime):
                start = parse_eat_at(str(start))
            if not isinstance(end, datetime):
                end = parse_eat_at(str(end))
        else:
            continue
        if start is None or end is None or end <= start:
            continue
        spans.append((start, end))
    if not spans:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_window",
            "updated": 0,
            "deleted": 0,
        }
    if now is None:
        now = datetime.now(spans[0][0].tzinfo or timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=spans[0][0].tzinfo or timezone.utc)
    status = gcal.credentials_status()
    if not status.get("ok"):
        return {
            "ok": False,
            "skipped": True,
            "error": status.get("error") or gcal.MISSING_CALENDAR_SCOPE,
            "error_code": status.get("error_code") or "missing_calendar_scope",
            "updated": 0,
            "deleted": 0,
        }
    try:
        from .nutrition_planner import clamp_eat_instants, parse_sleep_windows

        cal_id = gcal.resolve_calendar_id()
        lo = min(start for start, _end in spans) - timedelta(hours=18)
        hi = max(end for _start, end in spans)
        listed = gcal.list_events(
            cal_id,
            time_min=lo.isoformat(timespec="seconds"),
            time_max=hi.isoformat(timespec="seconds"),
        )
        meals: List[dict] = []
        seen: set[str] = set()
        for ev in listed:
            eid = str(ev.get("id") or "")
            if not eid or eid in seen or not _is_meal_event(ev):
                continue
            if _meal_start_iso(ev) == "":
                continue
            seen.add(eid)
            meals.append(ev)
        meals.sort(key=lambda ev: _meal_start_iso(ev))
        if not meals:
            return {
                "ok": True,
                "skipped": False,
                "updated": 0,
                "deleted": 0,
                "calendar_id": cal_id,
            }
        clock = now.astimezone(spans[0][0].tzinfo or now.tzinfo)
        sleep_windows = parse_sleep_windows(
            [
                {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds")}
                for start, end in spans
            ],
            clock.tzinfo,
        )
        times = []
        optional = []
        for ev in meals:
            eat = parse_eat_at(_meal_start_iso(ev))
            if eat is None:
                eat = clock
            times.append(eat)
            optional.append(_meal_is_optional(ev))
        placed = clamp_eat_instants(times, sleep_windows, optional=optional, now=clock)
        updated = 0
        deleted = 0
        for ev, new_at, old in zip(meals, placed, times):
            old_local = old.astimezone(clock.tzinfo)
            if not _overlaps_window(old_local, [(s, e) for s, e, _locked in sleep_windows]):
                continue
            eid = str(ev.get("id") or "")
            if new_at is None:
                if _delete_quiet(cal_id, eid):
                    deleted += 1
                continue
            old_min = old_local.replace(second=0, microsecond=0)
            new_min = new_at.astimezone(clock.tzinfo).replace(second=0, microsecond=0)
            if new_min == old_min:
                continue
            gcal.update_event(
                cal_id,
                eid,
                _shifted_meal_body(ev, new_min, spans),
            )
            updated += 1
        return {
            "ok": True,
            "skipped": False,
            "updated": updated,
            "deleted": deleted,
            "calendar_id": cal_id,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "skipped": True,
            "error": str(exc),
            "error_code": "calendar_error",
            "updated": 0,
            "deleted": 0,
        }
