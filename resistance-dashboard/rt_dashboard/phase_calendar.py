"""Staged Goal 3b phase calendar.

``effective(date)`` is None unless the rules flag is enabled, ``approved_by``
is set, and the date is inside the calendar. Draw tokens (``@draw``,
``@draw-14d``, ``@draw-2d``, ``@draw+1d``) follow a labs booking date when
one is present and otherwise the plan placeholder. The offset is a day
count. It is not a weekday name.
"""

from __future__ import annotations

import copy
import re
from datetime import date, timedelta
from typing import Any, Dict, Optional

from .coach_steps import load_rules, parse_day, read_json

_TOKEN = re.compile(r"^@draw(?P<sign>[+-])(?P<n>\d+)d$")
_BOOKING_KEYS = (
    "labs_booking_date",
    "booked_date",
    "draw_date",
    "next_draw_date",
    "date",
)


def calendar_block(rules: Optional[dict] = None) -> dict:
    rules = load_rules() if rules is None else rules
    blob = (rules or {}).get("phase_calendar")
    return blob if isinstance(blob, dict) else {}


def calendar_enabled(rules: Optional[dict] = None) -> bool:
    return bool(calendar_block(rules).get("enabled") is True)


def approved_by(rules: Optional[dict] = None) -> Optional[str]:
    raw = calendar_block(rules).get("approved_by")
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def calendar_active(rules: Optional[dict] = None) -> bool:
    """True only when enabled is true and approved_by is a non-empty string."""
    return calendar_enabled(rules) and approved_by(rules) is not None


def load_calendar(rules: Optional[dict] = None) -> Optional[dict]:
    block = calendar_block(rules)
    path = str(block.get("path") or "fitness/coach/phase_calendar_goal3b.json")
    return read_json(path)


def _iso(day: Optional[date]) -> Optional[str]:
    return day.isoformat() if day else None


def _coerce_date(raw: Any) -> Optional[date]:
    if isinstance(raw, date):
        return raw
    return parse_day(raw)


def booking_from_labs(labs: Any) -> Optional[date]:
    """Null-safe booking date from a labs payload. Missing keys stay None."""
    if labs is None:
        return None
    if isinstance(labs, str):
        return _coerce_date(labs)
    if not isinstance(labs, dict):
        return None
    booking = labs.get("booking")
    if isinstance(booking, str):
        found = _coerce_date(booking)
        if found:
            return found
    if isinstance(booking, dict):
        for key in _BOOKING_KEYS:
            found = _coerce_date(booking.get(key))
            if found:
                return found
    for key in ("labs_booking_date", "next_draw_date", "draw_date", "booked_date"):
        found = _coerce_date(labs.get(key))
        if found:
            return found
    return None


def booking_from_store() -> Optional[date]:
    """Call a labs-store booking reader only when that attribute exists.

    The October-panel labs change is a separate PR. A missing attribute,
    a None return, or a reader error leaves the placeholder in place.
    """
    try:
        from . import labs_store
    except Exception:
        return None
    for name in ("booking_date", "next_draw_date", "labs_booking_date"):
        fn = getattr(labs_store, name, None)
        if not callable(fn):
            continue
        try:
            return _coerce_date(fn())
        except Exception:
            return None
    return None


def resolve_draw_date(
    calendar: Optional[dict],
    *,
    rules: Optional[dict] = None,
    booking_date: Any = None,
    labs: Any = None,
    use_store: bool = True,
) -> Dict[str, Any]:
    """Booking wins. Otherwise the plan placeholder (2027-01-28)."""
    cal = calendar if isinstance(calendar, dict) else {}
    draw = cal.get("draw") if isinstance(cal.get("draw"), dict) else {}
    placeholder = _coerce_date(draw.get("date"))
    explicit = _coerce_date(booking_date)
    if explicit:
        return {"date": _iso(explicit), "source": "labs_booking", "placeholder": _iso(placeholder)}
    from_labs = booking_from_labs(labs)
    if from_labs:
        return {"date": _iso(from_labs), "source": "labs_booking", "placeholder": _iso(placeholder)}
    configured = _coerce_date(calendar_block(rules).get("labs_booking_date"))
    if configured:
        return {"date": _iso(configured), "source": "labs_booking", "placeholder": _iso(placeholder)}
    if use_store:
        stored = booking_from_store()
        if stored:
            return {"date": _iso(stored), "source": "labs_booking", "placeholder": _iso(placeholder)}
    return {
        "date": _iso(placeholder),
        "source": str(draw.get("date_source") or "plan_default"),
        "placeholder": _iso(placeholder),
    }


def resolve_token(raw: Any, draw: Optional[date]) -> Optional[str]:
    """Resolve ``@draw`` tokens by day offset from the draw date."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "@draw":
        return _iso(draw)
    match = _TOKEN.match(text)
    if match and draw is not None:
        n = int(match.group("n"))
        if match.group("sign") == "-":
            n = -n
        return _iso(draw + timedelta(days=n))
    parsed = _coerce_date(text)
    return _iso(parsed) if parsed else text or None


def _macros(raw: Any) -> Dict[str, int]:
    blob = raw if isinstance(raw, dict) else {}
    out = {}
    for key in ("protein", "carbs", "fat"):
        try:
            out[key] = int(blob.get(key))
        except (TypeError, ValueError):
            continue
    return out


def _step_on(phase: dict, day: date) -> Optional[int]:
    schedule = phase.get("step_schedule")
    if isinstance(schedule, list) and schedule:
        chosen = None
        chosen_from: Optional[date] = None
        for row in schedule:
            if not isinstance(row, dict):
                continue
            start = _coerce_date(row.get("from"))
            if start is None or start > day:
                continue
            if chosen_from is None or start >= chosen_from:
                chosen_from = start
                try:
                    chosen = int(row.get("step_target"))
                except (TypeError, ValueError):
                    chosen = None
        if chosen is not None:
            return chosen
    try:
        return int(phase.get("step_target"))
    except (TypeError, ValueError):
        return None


def _phase_rows(calendar: dict, draw: Optional[date]) -> list:
    rows = []
    for raw in calendar.get("phases") or []:
        if not isinstance(raw, dict):
            continue
        row = copy.deepcopy(raw)
        row["start"] = resolve_token(row.get("start"), draw)
        row["end"] = resolve_token(row.get("end"), draw)
        if row.get("no_program_change_from") is not None:
            row["no_program_change_from"] = resolve_token(row.get("no_program_change_from"), draw)
        # The 48-hour token is reminder copy only. Drop it from the phase
        # row so nothing can treat it as a training gate.
        row.pop("no_hard_training_from", None)
        rows.append(row)
    return rows


def _override(calendar: dict, day: date) -> Optional[dict]:
    for raw in calendar.get("day_overrides") or []:
        if isinstance(raw, dict) and _coerce_date(raw.get("date")) == day:
            return raw
    return None


def phase_bounds(calendar: Optional[dict], draw: Optional[date]) -> Optional[tuple]:
    rows = _phase_rows(calendar or {}, draw)
    starts = [_coerce_date(r.get("start")) for r in rows]
    ends = [_coerce_date(r.get("end")) for r in rows]
    starts = [d for d in starts if d]
    ends = [d for d in ends if d]
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def resolve_phases(
    *,
    rules: Optional[dict] = None,
    booking_date: Any = None,
    labs: Any = None,
    calendar: Optional[dict] = None,
    use_store: bool = False,
) -> Dict[str, Any]:
    """Resolved phase rows. Does not apply them. ``use_store`` stays off in tests."""
    rules = load_rules() if rules is None else rules
    cal = calendar if calendar is not None else load_calendar(rules)
    if not isinstance(cal, dict):
        return {"draw": None, "phases": [], "calendar": None}
    draw_info = resolve_draw_date(
        cal, rules=rules, booking_date=booking_date, labs=labs, use_store=use_store
    )
    draw = _coerce_date(draw_info.get("date"))
    rows = _phase_rows(cal, draw)
    flags = {}
    for row in rows:
        if row.get("id") == "P3":
            flags = {
                "draw": _iso(draw),
                "draw_source": draw_info.get("source"),
                "no_program_change_from": row.get("no_program_change_from"),
                "p3_end": row.get("end"),
                "p4_start": None,
            }
        if row.get("id") == "P4":
            flags["p4_start"] = row.get("start")
    return {"draw": draw_info, "phases": rows, "flags": flags, "calendar": cal}


def effective(
    as_of: Any,
    *,
    rules: Optional[dict] = None,
    booking_date: Any = None,
    labs: Any = None,
    calendar: Optional[dict] = None,
    use_store: bool = False,
) -> Optional[dict]:
    """Phase for ``as_of``, or None when the calendar is not in effect."""
    rules = load_rules() if rules is None else rules
    if not calendar_active(rules):
        return None
    day = _coerce_date(as_of)
    if day is None:
        return None
    resolved = resolve_phases(
        rules=rules,
        booking_date=booking_date,
        labs=labs,
        calendar=calendar,
        use_store=use_store,
    )
    cal = resolved.get("calendar")
    if not isinstance(cal, dict):
        return None
    draw = _coerce_date((resolved.get("draw") or {}).get("date"))
    for row in resolved.get("phases") or []:
        start = _coerce_date(row.get("start"))
        end = _coerce_date(row.get("end"))
        if start is None or end is None or not (start <= day <= end):
            continue
        kcal = row.get("kcal")
        macros = _macros(row.get("macros_g"))
        override = _override(cal, day)
        reason = None
        if override:
            if override.get("kcal") is not None:
                kcal = override.get("kcal")
            if isinstance(override.get("macros_g"), dict):
                macros = _macros(override.get("macros_g"))
            reason = override.get("reason")
        try:
            kcal_i = int(kcal)
        except (TypeError, ValueError):
            kcal_i = None
        band = row.get("rate_band_pct") or row.get("rate_band")
        target = row.get("rate_target_pct")
        floor = row.get("kcal_floor")
        try:
            floor_i = int(floor) if floor not in (None, "") else None
        except (TypeError, ValueError):
            floor_i = None
        return {
            "phase_id": row.get("id"),
            "phase": row.get("phase"),
            "start": row.get("start"),
            "end": row.get("end"),
            "kcal": kcal_i,
            "macros": macros,
            "rate_band": [float(band[0]), float(band[1])] if isinstance(band, (list, tuple)) and len(band) >= 2 else None,
            "rate_target": [float(target[0]), float(target[1])] if isinstance(target, (list, tuple)) and len(target) >= 2 else None,
            "step_target": _step_on(row, day),
            "floor": floor_i,
            "day_override": reason,
            "flags": {
                "too_slow": row.get("too_slow"),
                "mini_cut_allowed": row.get("mini_cut_allowed", True),
                "deload_window": bool(row.get("deload_window")),
                "no_program_change_from": row.get("no_program_change_from"),
                "draw": _iso(draw),
                "draw_source": (resolved.get("draw") or {}).get("source"),
            },
        }
    return None


def _advisory_offset_days(calendar: Optional[dict]) -> int:
    """Day count on the ``@draw-Nd`` reminder token. Not a training gate."""
    for row in (calendar or {}).get("phases") or []:
        if not isinstance(row, dict):
            continue
        match = _TOKEN.match(str(row.get("no_hard_training_from") or ""))
        if match and match.group("sign") == "-":
            return int(match.group("n"))
    return 2


def pre_draw_reminder(draw: Any, *, offset_days: int = 2) -> Optional[dict]:
    """Advisory sentence only. Nothing in the coach blocks training or alcohol."""
    day = _coerce_date(draw)
    if day is None:
        return None
    start = day - timedelta(days=abs(int(offset_days)))
    return {
        "kind": "pre_draw_reminder",
        "enforced": False,
        "chip": False,
        "from": start.isoformat(),
        "from_note": "AM",
        "draw": day.isoformat(),
        "text": (
            f"Advisory only: from {start.isoformat()} AM, no hard training or alcohol "
            f"before the draw on {day.isoformat()}. Not a rule and not a chip."
        ),
    }


def staged_panel(
    *,
    rules: Optional[dict] = None,
    booking_date: Any = None,
    labs: Any = None,
) -> Optional[dict]:
    """Read-only preview. Present only while the calendar file loads and is off."""
    rules = load_rules() if rules is None else rules
    if calendar_active(rules):
        return None
    cal = load_calendar(rules)
    if not isinstance(cal, dict):
        return None
    resolved = resolve_phases(
        rules=rules, booking_date=booking_date, labs=labs, calendar=cal, use_store=False
    )
    rows = []
    for row in resolved.get("phases") or []:
        steps = row.get("step_target")
        schedule = row.get("step_schedule")
        if isinstance(schedule, list) and schedule:
            steps = [
                {"from": item.get("from"), "step_target": item.get("step_target")}
                for item in schedule
                if isinstance(item, dict)
            ]
        rows.append(
            {
                "id": row.get("id"),
                "phase": row.get("phase"),
                "start": row.get("start"),
                "end": row.get("end"),
                "kcal": row.get("kcal"),
                "macros": _macros(row.get("macros_g")),
                "steps": steps,
                "no_program_change_from": row.get("no_program_change_from"),
            }
        )
    draw = resolved.get("draw") or {}
    return {
        "label": "Staged: waiting on Chris's yes. Not applied.",
        "applied": False,
        "enabled": False,
        "approved_by": approved_by(rules),
        "draw": draw.get("date"),
        "draw_source": draw.get("source"),
        "phases": rows,
        "pre_draw_reminder": pre_draw_reminder(
            draw.get("date"), offset_days=_advisory_offset_days(cal)
        ),
    }


def last_calendar_step(calendar: Optional[dict], draw: Optional[date]) -> Optional[int]:
    rows = _phase_rows(calendar or {}, draw)
    if not rows:
        return None
    last = rows[-1]
    try:
        return int(last.get("step_target"))
    except (TypeError, ValueError):
        return _step_on(last, _coerce_date(last.get("end")) or date(2027, 2, 12))
