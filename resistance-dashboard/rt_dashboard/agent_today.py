"""Read-only Today brief for machine agents (Frankenfit / Restore / Pulse / Nourish).

Slices an existing dashboard payload (Pi cache or Vercel Turso/Hidrate load).
Never invents ml, loads, sessions, sip timestamps, Active Zone Minutes,
intake, weekly volume, or sleep. Missing → honest empty
(``today.nutrition`` consumed fields are ``null``; week lists are ``[]``).
``today.workout.session_type`` is the signed-in Today PPL letter
(``stamp_today_session`` over all real logs). ``recent_sessions`` is last
2+ sessions or ~14d; ``week.logged_sessions`` stays this ISO week.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .workout_store import flatten_logged_exercise

# Published books / DEFAULT_TARGETS. Not invented intake.
_BOOK_TARGETS = {"calories": 2100.0, "protein_g": 210.0}

# Last-session history for programming (not the ISO-week slice).
_RECENT_HISTORY_DAYS = 14
_RECENT_MIN_SESSIONS = 2


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _civil_day(value: Any) -> str:
    return str(value or "")[:10]


def _exercise_row(ex: Any) -> Optional[Dict[str, Any]]:
    if hasattr(ex, "to_dict"):
        try:
            ex = ex.to_dict()
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(ex, dict):
        return None
    name = ex.get("name") or ex.get("exercise")
    if not name:
        return None
    return flatten_logged_exercise(ex)


def _plan_exercise_row(ex: Any) -> Optional[Dict[str, Any]]:
    """Copy planned fields only. No default sets/reps/weight."""
    if hasattr(ex, "to_dict"):
        try:
            ex = ex.to_dict()
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(ex, dict):
        return None
    name = ex.get("name") or ex.get("exercise")
    if not str(name or "").strip():
        return None
    row: Dict[str, Any] = {"name": name}
    for key in (
        "sets",
        "reps",
        "weight_lbs",
        "target_sets",
        "target_reps",
        "cues",
        "notes",
    ):
        if ex.get(key) is not None:
            row[key] = ex[key]
    return row


def _logged_exercises_for_day(sessions: Sequence[Any], day: str) -> List[Dict[str, Any]]:
    day = _civil_day(day)
    if not day:
        return []
    out: List[Dict[str, Any]] = []
    for session in sessions or []:
        if hasattr(session, "to_dict"):
            try:
                raw = session.to_dict()
            except Exception:  # noqa: BLE001
                continue
        elif isinstance(session, dict):
            raw = session
        else:
            continue
        if _civil_day(raw.get("date")) != day:
            continue
        for ex in raw.get("exercises") or []:
            row = _exercise_row(ex)
            if row:
                out.append(row)
    return out


def _empty_workout() -> Dict[str, Any]:
    return {
        "session_type": None,
        "is_rest_day": False,
        "next_session_type": None,
        "plan_exercises": [],
        "logged_exercises": [],
        "recent_sessions": [],
        "message": None,
        "empty": True,
    }


def _empty_hydration_wake() -> Dict[str, Any]:
    return {
        "consumed": None,
        "target": None,
        "pace": None,
        "intake_source": None,
        "window_fraction": None,
        "status": None,
        "civil_day_ml": None,
        "sip_aware": False,
        "sip_count": 0,
    }


def _empty_bottle() -> Dict[str, Any]:
    return {
        "available": False,
        "percent": None,
        "status": None,
        "name": None,
        "field": None,
        "error": None,
    }


def _empty_wake_window() -> Dict[str, Any]:
    return {
        "last_wake_at": None,
        "empty_at": None,
        "pct_charged": None,
        "mode": None,
        "summary": None,
        "hours_awake": None,
        "hours_until_empty": None,
    }


def _ppl_letter(value: Any) -> Optional[str]:
    letter = str(value or "").strip().lower()
    return letter if letter in ("push", "pull", "legs") else None


def _has_real_exercises(row: Dict[str, Any]) -> bool:
    from .test_noise import is_test_exercise_name

    for ex in row.get("exercises") or []:
        name = ""
        if isinstance(ex, dict):
            name = str(ex.get("name") or "")
        else:
            name = str(getattr(ex, "name", "") or "")
        if name.strip() and not is_test_exercise_name(name):
            return True
    return False


def _planning_sessions(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Logged PPL sessions used for Today letter + history. Canaries dropped."""
    out: List[Dict[str, Any]] = []
    for session in payload.get("sessions") or []:
        row = _logged_session_row(session)
        if not row or not _has_real_exercises(row):
            continue
        out.append(row)
    return out


def _goals_for_letter(payload: Dict[str, Any]) -> Dict[str, Any]:
    store = _as_dict(payload.get("workout_store"))
    for raw in (store.get("goals"), payload.get("goals")):
        if isinstance(raw, dict) and isinstance(raw.get("rotation"), list) and raw.get("rotation"):
            return raw
    from .workout_store import load_workspace_goals

    goals, _src = load_workspace_goals()
    return goals if isinstance(goals, dict) else {}


def _recent_logged_sessions(rows: Sequence[Dict[str, Any]], day: str) -> List[Dict[str, Any]]:
    """Last 2+ real sessions, preferring the last ~14d. Honest empty if none."""
    dated = [r for r in rows if _civil_day(r.get("date"))]
    dated.sort(key=lambda r: r["date"], reverse=True)
    if not dated:
        return []
    cutoff = None
    day = _civil_day(day)
    if day:
        try:
            end = datetime.strptime(day, "%Y-%m-%d")
            cutoff = (end - timedelta(days=_RECENT_HISTORY_DAYS)).strftime("%Y-%m-%d")
        except ValueError:
            cutoff = None
    in_window = [r for r in dated if cutoff and r["date"] >= cutoff]
    if len(in_window) >= _RECENT_MIN_SESSIONS:
        chosen = in_window
    else:
        chosen = dated[: max(_RECENT_MIN_SESSIONS, len(in_window))]
    chosen = sorted(chosen, key=lambda r: r["date"])
    return [
        {
            "date": r["date"],
            "session_type": r.get("session_type"),
            "volume": r.get("volume"),
            "exercises": list(r.get("exercises") or []),
        }
        for r in chosen
    ]


def _stamp_today_letter(
    payload: Dict[str, Any],
    planning: Sequence[Dict[str, Any]],
    day: str,
) -> Optional[Dict[str, Any]]:
    """Same hybrid fill as signed-in Today: next PPL + rest gate over all logs."""
    if not planning:
        return None
    from .workout_store import stamp_today_session

    goals = _goals_for_letter(payload)
    if not isinstance(goals.get("rotation"), list) or not goals.get("rotation"):
        return None
    recovery = payload.get("recovery")
    stamped = stamp_today_session(
        {"session_type": None, "is_rest_day": False, "exercises": [], "empty": True},
        list(planning),
        goals,
        recovery if isinstance(recovery, dict) else {},
        as_of=day or None,
        fill_rest=True,
    )
    next_st = _ppl_letter(stamped.get("next_session_type"))
    letter = _ppl_letter(stamped.get("session_type")) or next_st
    return {
        "session_type": letter,
        "is_rest_day": bool(stamped.get("is_rest_day")),
        "next_session_type": next_st,
        "message": stamped.get("message"),
    }


def _workout_today(payload: Dict[str, Any], today_board: Dict[str, Any], day: str) -> Dict[str, Any]:
    slot = _as_dict(payload.get("workout"))
    store = _as_dict(payload.get("workout_store"))
    plan = _as_dict(store.get("plan"))
    coach_wo = _as_dict(today_board.get("workout"))

    session_type = (
        slot.get("session_type")
        if slot.get("session_type") not in (None, "")
        else None
    )
    if session_type is None:
        session_type = plan.get("session_type") or coach_wo.get("session_type") or None
        if session_type == "":
            session_type = None

    if "is_rest_day" in slot:
        is_rest = bool(slot.get("is_rest_day"))
    elif "is_rest_day" in plan:
        is_rest = bool(plan.get("is_rest_day"))
    elif "is_rest_day" in coach_wo:
        is_rest = bool(coach_wo.get("is_rest_day"))
    else:
        is_rest = False

    next_st = (
        _ppl_letter(slot.get("next_session_type"))
        or _ppl_letter(plan.get("next_session_type"))
        or _ppl_letter(coach_wo.get("next_session_type"))
        or _ppl_letter(_as_dict(slot.get("context")).get("next_session_type"))
    )

    plan_src = plan.get("exercises") or slot.get("exercises") or coach_wo.get("exercises") or []
    plan_exercises: List[Dict[str, Any]] = []
    for ex in plan_src:
        row = _plan_exercise_row(ex)
        if row:
            plan_exercises.append(row)

    logged = _logged_exercises_for_day(payload.get("sessions") or [], day)
    message = slot.get("message") or plan.get("message") or coach_wo.get("message")
    if message is not None:
        message = str(message) if message != "" else None

    planning = _planning_sessions(payload)
    recent = _recent_logged_sessions(planning, day)
    stamped = _stamp_today_letter(payload, planning, day)
    today_logged = [
        r
        for r in planning
        if _civil_day(r.get("date")) == day and _ppl_letter(r.get("session_type"))
    ]
    if today_logged:
        # Already training/logged today — letter is that session, not the next slot.
        session_type = _ppl_letter(today_logged[0].get("session_type"))
        is_rest = False
        if stamped:
            next_st = stamped["next_session_type"] or next_st
    elif stamped:
        # Signed-in Today letter (UI skips rest and shows next PPL).
        session_type = stamped["session_type"]
        is_rest = stamped["is_rest_day"]
        next_st = stamped["next_session_type"] or next_st
        if is_rest and stamped.get("message") and not (message or "").strip():
            message = str(stamped["message"])

    has_signal = (
        bool(session_type)
        or is_rest
        or bool(plan_exercises)
        or bool(logged)
        or bool(recent)
    )
    if not has_signal:
        return _empty_workout()
    return {
        "session_type": session_type,
        "is_rest_day": is_rest,
        "next_session_type": next_st,
        "plan_exercises": plan_exercises,
        "logged_exercises": logged,
        "recent_sessions": recent,
        "message": message,
        "empty": not plan_exercises and not logged and not session_type and not is_rest,
    }


def _sip_count(pacing: Dict[str, Any]) -> Optional[int]:
    raw = pacing.get("sip_count")
    if raw is None:
        win = pacing.get("window_intake")
        if isinstance(win, dict):
            raw = win.get("sample_count")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _sip_aware_pacing(pacing: Dict[str, Any]) -> bool:
    """True only when wake-window pace used real sip times — not civil midnight."""
    flag = pacing.get("sip_aware")
    if flag is True:
        return True
    if flag is False:
        return False
    status = pacing.get("status")
    if status in (None, "unknown"):
        return False
    intake = pacing.get("intake_source")
    return intake not in (None, "", "none") and status in (
        "on_pace",
        "ahead",
        "behind",
        "start",
        "no_target",
    )


def _hydration_wake(payload: Dict[str, Any]) -> Dict[str, Any]:
    bars = _as_dict(payload.get("hydration_bars"))
    pacing = bars.get("pacing")
    if not isinstance(pacing, dict):
        return _empty_hydration_wake()
    sip_count = _sip_count(pacing)
    # Wake-window actual only. Civil-day ml is informational — never the pace source.
    if not _sip_aware_pacing(pacing):
        return {
            "consumed": None,
            "target": pacing.get("target"),
            "pace": None,
            "intake_source": pacing.get("intake_source") or "none",
            "window_fraction": pacing.get("window_fraction"),
            "status": "unknown",
            "civil_day_ml": pacing.get("civil_day_ml"),
            "sip_aware": False,
            "sip_count": sip_count if sip_count is not None else 0,
        }
    return {
        "consumed": pacing.get("consumed"),
        "target": pacing.get("target"),
        "pace": {
            "status": pacing.get("status"),
            "band": pacing.get("band"),
            "delta_vs_pace": pacing.get("delta_vs_pace"),
            "fill_pct": pacing.get("fill_pct"),
            "expected_pct": pacing.get("expected_pct"),
            "window_fraction": pacing.get("window_fraction"),
        },
        "intake_source": pacing.get("intake_source"),
        "window_fraction": pacing.get("window_fraction"),
        "status": pacing.get("status"),
        "civil_day_ml": pacing.get("civil_day_ml"),
        "sip_aware": True,
        "sip_count": sip_count,
    }


def _bottle(payload: Dict[str, Any]) -> Dict[str, Any]:
    bottle = payload.get("hidrate_bottle")
    if not isinstance(bottle, dict):
        bottle = _as_dict(payload.get("hydration_bars")).get("bottle")
    if not isinstance(bottle, dict):
        return _empty_bottle()
    return {
        "available": bottle.get("available"),
        "percent": bottle.get("percent"),
        "status": bottle.get("status"),
        "name": bottle.get("name"),
        "field": bottle.get("field"),
        "error": bottle.get("error"),
    }


def _wake_window(payload: Dict[str, Any]) -> Dict[str, Any]:
    bat = payload.get("sleep_battery")
    if not isinstance(bat, dict):
        bat = _as_dict(payload.get("recovery")).get("sleep_battery")
    if not isinstance(bat, dict) or not bat:
        return _empty_wake_window()
    return {
        "last_wake_at": bat.get("last_wake_at"),
        "empty_at": bat.get("empty_at"),
        "pct_charged": bat.get("pct_charged"),
        "mode": bat.get("mode"),
        "summary": bat.get("summary"),
        "hours_awake": bat.get("hours_awake"),
        "hours_until_empty": bat.get("hours_until_empty"),
    }


def _azm_day(item: Any) -> Optional[Dict[str, Any]]:
    """Copy one HealthSnapshot AZM day. No invented minutes from other series."""
    if hasattr(item, "to_dict"):
        try:
            item = item.to_dict()
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(item, dict):
        return None
    date = _civil_day(item.get("date"))
    if not date:
        return None
    row: Dict[str, Any] = {"date": date}
    for key in (
        "fat_burn_minutes",
        "cardio_minutes",
        "peak_minutes",
        "total_minutes",
        "source",
    ):
        if key in item:
            row[key] = item[key]
    return row


def _active_zone_minutes(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Reuse HealthSnapshot.active_zone_minutes. Missing → honest ``[]``."""
    health = payload.get("health")
    if hasattr(health, "to_dict"):
        try:
            health = health.to_dict()
        except Exception:  # noqa: BLE001
            health = None
    raw = None
    if isinstance(health, dict):
        raw = health.get("active_zone_minutes")
    if raw is None:
        raw = payload.get("active_zone_minutes")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        row = _azm_day(item)
        if row:
            out.append(row)
    return out


def _health_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    health = payload.get("health")
    if hasattr(health, "to_dict"):
        try:
            health = health.to_dict()
        except Exception:  # noqa: BLE001
            return {}
    return health if isinstance(health, dict) else {}


def _iso_week_bounds(day: str) -> Tuple[Optional[str], Optional[str]]:
    """Monday of the ISO week through ``day``. Missing day → (None, None)."""
    day = _civil_day(day)
    if not day:
        return None, None
    try:
        dt = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return None, None
    start = dt - timedelta(days=dt.weekday())
    return start.strftime("%Y-%m-%d"), day


def _in_week(day: str, start: Optional[str], end: Optional[str]) -> bool:
    day = _civil_day(day)
    if not day or not start or not end:
        return False
    return start <= day <= end


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _targets(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Live books when present, else published 2100 / 210P. Not intake."""
    nut = _as_dict(payload.get("nutrition_store"))
    raw = nut.get("targets")
    if not isinstance(raw, dict):
        return dict(_BOOK_TARGETS)
    calories = _float_or_none(raw.get("calories"))
    protein = _float_or_none(raw.get("protein_g"))
    if calories is None and protein is None:
        return dict(_BOOK_TARGETS)
    return {
        "calories": calories if calories is not None else _BOOK_TARGETS["calories"],
        "protein_g": protein if protein is not None else _BOOK_TARGETS["protein_g"],
    }


def _item_to_dict(item: Any) -> Optional[Dict[str, Any]]:
    if hasattr(item, "to_dict"):
        try:
            item = item.to_dict()
        except Exception:  # noqa: BLE001
            return None
    return item if isinstance(item, dict) else None


def _consumed_row(item: Any, *, source_fallback: str) -> Optional[Dict[str, Any]]:
    raw = _item_to_dict(item)
    if not raw:
        return None
    date = _civil_day(raw.get("date"))
    if not date:
        return None
    calories = _float_or_none(raw.get("calories"))
    protein = _float_or_none(raw.get("protein_g"))
    if calories is None and protein is None:
        return None
    src = raw.get("source")
    if src in (None, "", "none"):
        # Unsigned rollup zeros are not intake.
        if (calories or 0) == 0 and (protein or 0) == 0:
            return None
        src = source_fallback
    return {
        "date": date,
        "calories": calories,
        "protein_g": protein,
        "source": src,
    }


def _sum_food_logs_for_day(logs: Sequence[Any], day: str) -> Optional[Dict[str, Any]]:
    calories = 0.0
    protein = 0.0
    n = 0
    have_cal = False
    have_pro = False
    for item in logs or []:
        raw = _item_to_dict(item)
        if not raw or _civil_day(raw.get("date")) != day:
            continue
        n += 1
        cal = _float_or_none(raw.get("calories"))
        pro = _float_or_none(raw.get("protein_g"))
        if cal is not None:
            calories += cal
            have_cal = True
        if pro is not None:
            protein += pro
            have_pro = True
    if n == 0 or (not have_cal and not have_pro):
        return None
    return {
        "date": day,
        "calories": round(calories, 1) if have_cal else None,
        "protein_g": round(protein, 1) if have_pro else None,
        "source": "food_logs",
        "food_log_count": n,
    }


def _consumed_for_day(payload: Dict[str, Any], day: str) -> Optional[Dict[str, Any]]:
    """One civil day's logged intake. Missing logs → None (not 0)."""
    day = _civil_day(day)
    if not day:
        return None
    health = _health_dict(payload)
    nut = _as_dict(payload.get("nutrition_store"))
    if day == _civil_day(
        _as_dict(payload.get("coach")).get("today", {}).get("date")
        if isinstance(_as_dict(payload.get("coach")).get("today"), dict)
        else ""
    ) or day == _civil_day(_as_dict(payload.get("meta")).get("local_today")):
        stamped = nut.get("today_consumed")
        row = _consumed_row(stamped, source_fallback="daily_rollup")
        if row:
            return row
    for n in health.get("nutrition") or nut.get("nutrition") or []:
        row = _consumed_row(n, source_fallback="google_health")
        if row and row["date"] == day:
            return row
    logs = health.get("food_logs") or nut.get("food_logs") or nut.get("food_logs_today") or []
    return _sum_food_logs_for_day(logs, day)


def _meal_slots(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Copy meal-plan slots when present. Do not generate food."""
    nut = _as_dict(payload.get("nutrition_store"))
    plan = nut.get("meal_plan")
    if not isinstance(plan, dict):
        return []
    meals = plan.get("meals")
    if not isinstance(meals, list):
        return []
    out: List[Dict[str, Any]] = []
    for meal in meals:
        if not isinstance(meal, dict):
            continue
        items: List[Dict[str, Any]] = []
        for it in meal.get("items") or []:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or it.get("exercise")
            if not str(name or "").strip():
                continue
            row: Dict[str, Any] = {"name": name}
            for key in ("calories", "protein_g", "portion_g", "serving_label"):
                if it.get(key) is not None:
                    row[key] = it[key]
            items.append(row)
        label = meal.get("label")
        if not label and not items:
            continue
        slot: Dict[str, Any] = {"label": label, "items": items}
        if meal.get("eat_at"):
            slot["eat_at"] = meal.get("eat_at")
        out.append(slot)
    return out


def _empty_nutrition(targets: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "calories": None,
        "protein_g": None,
        "targets": dict(targets),
        "meals": [],
        "source": None,
    }


def _nutrition_today(payload: Dict[str, Any], day: str, targets: Dict[str, Any]) -> Dict[str, Any]:
    meals = _meal_slots(payload)
    row = _consumed_for_day(payload, day)
    if not row and not meals:
        return _empty_nutrition(targets)
    return {
        "calories": row["calories"] if row else None,
        "protein_g": row["protein_g"] if row else None,
        "targets": dict(targets),
        "meals": meals,
        "source": row["source"] if row else None,
    }


def _nutrition_week(
    payload: Dict[str, Any], start: Optional[str], end: Optional[str], targets: Dict[str, Any]
) -> Dict[str, Any]:
    days: List[Dict[str, Any]] = []
    if start and end:
        try:
            cursor = datetime.strptime(start, "%Y-%m-%d")
            last = datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            cursor = None
            last = None
        while cursor is not None and last is not None and cursor <= last:
            row = _consumed_for_day(payload, cursor.strftime("%Y-%m-%d"))
            if row:
                days.append(row)
            cursor += timedelta(days=1)
    if not days:
        return {
            "calories": None,
            "protein_g": None,
            "targets": dict(targets),
            "days": [],
        }
    cal_vals = [d["calories"] for d in days if d.get("calories") is not None]
    pro_vals = [d["protein_g"] for d in days if d.get("protein_g") is not None]
    return {
        "calories": round(sum(cal_vals), 1) if cal_vals else None,
        "protein_g": round(sum(pro_vals), 1) if pro_vals else None,
        "targets": dict(targets),
        "days": days,
    }


def _exercise_volume(ex: Dict[str, Any]) -> Optional[float]:
    vol = _float_or_none(ex.get("volume"))
    if vol is not None:
        return vol
    raw_sets = ex.get("sets")
    if not isinstance(raw_sets, list):
        weight = _float_or_none(ex.get("weight_lbs"))
        nsets = _float_or_none(ex.get("sets"))
        reps = _float_or_none(ex.get("reps"))
        if weight is None or nsets is None or reps is None:
            return None
        return weight * nsets * reps
    total = 0.0
    have = False
    for st in raw_sets:
        if not isinstance(st, dict):
            continue
        weight = _float_or_none(st.get("weight_lbs"))
        nsets = _float_or_none(st.get("sets"))
        reps = _float_or_none(st.get("reps"))
        if weight is None or nsets is None or reps is None:
            continue
        total += weight * nsets * reps
        have = True
    return total if have else None


def _logged_session_row(session: Any) -> Optional[Dict[str, Any]]:
    if hasattr(session, "to_dict"):
        try:
            raw = session.to_dict()
        except Exception:  # noqa: BLE001
            return None
    elif isinstance(session, dict):
        raw = session
    else:
        return None
    date = _civil_day(raw.get("date"))
    if not date:
        return None
    exercises: List[Dict[str, Any]] = []
    for ex in raw.get("exercises") or []:
        row = _exercise_row(ex)
        if row:
            exercises.append(row)
    volume = _float_or_none(raw.get("volume"))
    if volume is None:
        parts = [_exercise_volume(ex) for ex in exercises]
        known = [p for p in parts if p is not None]
        volume = sum(known) if known else None
    session_type = raw.get("session_type")
    if session_type == "":
        session_type = None
    return {
        "date": date,
        "session_type": session_type,
        "volume": volume,
        "exercises": exercises,
    }


def _logged_sessions_week(
    payload: Dict[str, Any], start: Optional[str], end: Optional[str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for session in payload.get("sessions") or []:
        row = _logged_session_row(session)
        if not row or not _in_week(row["date"], start, end):
            continue
        out.append(row)
    out.sort(key=lambda r: r["date"])
    return out


def _duration_hours(start: Optional[str], end: Optional[str]) -> Optional[float]:
    if not start or not end:
        return None
    try:
        st = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        en = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    seconds = (en - st).total_seconds()
    if seconds <= 0:
        return None
    return round(seconds / 3600.0, 2)


def _iso_prefix(value: Any) -> str:
    return str(value or "").replace("Z", "+00:00")[:19]


def _slim_battery(bat: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(bat, dict) or not bat:
        return None
    if bat.get("pct_charged") is None and not bat.get("summary") and not bat.get("mode"):
        return None
    return {
        "pct_charged": bat.get("pct_charged"),
        "mode": bat.get("mode"),
        "summary": bat.get("summary"),
    }


def _sleep_week(
    payload: Dict[str, Any], start: Optional[str], end: Optional[str]
) -> List[Dict[str, Any]]:
    """This-week sleep sessions. Intervals preferred; daily hours only if unsigned times."""
    health = _health_dict(payload)
    bat = payload.get("sleep_battery")
    if not isinstance(bat, dict):
        bat = _as_dict(payload.get("recovery")).get("sleep_battery")
    slim = _slim_battery(bat if isinstance(bat, dict) else None)
    last_wake = ""
    if isinstance(bat, dict):
        last_wake = str(bat.get("last_wake_at") or "")
    last_wake_prefix = _iso_prefix(last_wake)
    last_wake_day = _civil_day(last_wake)

    intervals = health.get("sleep_intervals")
    rows: List[Dict[str, Any]] = []
    if isinstance(intervals, list):
        for iv in intervals:
            if not isinstance(iv, dict):
                continue
            iv_start = iv.get("start")
            iv_end = iv.get("end")
            date = _civil_day(iv_end or iv_start)
            if not _in_week(date, start, end):
                continue
            hours = _float_or_none(iv.get("duration_hours"))
            if hours is None:
                hours = _duration_hours(iv_start, iv_end)
            if hours is None and iv_start is None and iv_end is None:
                continue
            battery = None
            if slim and last_wake_prefix and _iso_prefix(iv_end) == last_wake_prefix:
                battery = slim
            rows.append(
                {
                    "date": date,
                    "start": iv_start,
                    "end": iv_end,
                    "duration_hours": hours,
                    "source": iv.get("source") or "google_health",
                    "battery": battery,
                }
            )
        if rows:
            rows.sort(key=lambda r: (r.get("start") or r.get("date") or ""))
            return rows

    for item in health.get("sleep") or []:
        raw = _item_to_dict(item)
        if not raw:
            continue
        if str(raw.get("source") or "") == "implied_zero":
            continue
        date = _civil_day(raw.get("date"))
        if not _in_week(date, start, end):
            continue
        hours = _float_or_none(raw.get("sleep_hours"))
        if hours is None or hours <= 0:
            continue
        battery = None
        if slim and last_wake_day and date == last_wake_day:
            battery = slim
        rows.append(
            {
                "date": date,
                "start": None,
                "end": None,
                "duration_hours": hours,
                "source": raw.get("source") or "google_health",
                "battery": battery,
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def _empty_week() -> Dict[str, Any]:
    targets = dict(_BOOK_TARGETS)
    return {
        "start": None,
        "end": None,
        "nutrition": {
            "calories": None,
            "protein_g": None,
            "targets": targets,
            "days": [],
        },
        "logged_sessions": [],
        "sleep": [],
    }


def export_agent_today(
    payload: Optional[Dict[str, Any]] = None,
    *,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Slice a dashboard payload into ``{ok, today, week, error?}``. Honest empties."""
    data = payload if isinstance(payload, dict) else {}
    meta = _as_dict(data.get("meta"))
    coach = _as_dict(data.get("coach"))
    today_board = _as_dict(coach.get("today"))
    day = _civil_day(today_board.get("date") or meta.get("local_today"))
    targets = _targets(data)
    week_start, week_end = _iso_week_bounds(day)

    today = {
        "date": day or None,
        "workout": _workout_today(data, today_board, day),
        "hydration_wake": _hydration_wake(data),
        "bottle": _bottle(data),
        "wake_window": _wake_window(data),
        "active_zone_minutes": _active_zone_minutes(data),
        "nutrition": _nutrition_today(data, day, targets),
    }
    week = (
        _empty_week()
        if not week_start
        else {
            "start": week_start,
            "end": week_end,
            "nutrition": _nutrition_week(data, week_start, week_end, targets),
            "logged_sessions": _logged_sessions_week(data, week_start, week_end),
            "sleep": _sleep_week(data, week_start, week_end),
        }
    )
    if week.get("start") is None:
        week["nutrition"]["targets"] = dict(targets)
    out: Dict[str, Any] = {"ok": True, "today": today, "week": week}
    err = error if error is not None else meta.get("error")
    if err:
        out["error"] = err
    return out


def assemble_dashboard_slice(
    *,
    date: str,
    sessions: Optional[Sequence[Any]] = None,
    workout: Optional[dict] = None,
    workout_plan: Optional[dict] = None,
    hydration_bars: Optional[dict] = None,
    hidrate_bottle: Optional[dict] = None,
    sleep_battery: Optional[dict] = None,
    health: Optional[Any] = None,
    nutrition_store: Optional[dict] = None,
    goals: Optional[dict] = None,
    recovery: Optional[dict] = None,
    meta_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Minimal dashboard-shaped dict so export_agent_today has one code path."""
    sess_out: List[Any] = []
    for s in sessions or []:
        if hasattr(s, "to_dict"):
            try:
                sess_out.append(s.to_dict())
                continue
            except Exception:  # noqa: BLE001
                continue
        if isinstance(s, dict):
            sess_out.append(s)
    health_out: Any = health
    if hasattr(health, "to_dict"):
        try:
            health_out = health.to_dict()
        except Exception:  # noqa: BLE001
            health_out = {}
    elif health is None:
        health_out = {}
    store: Dict[str, Any] = {
        "plan": workout_plan if isinstance(workout_plan, dict) else {},
    }
    if isinstance(goals, dict):
        store["goals"] = goals
    return {
        "sessions": sess_out,
        "workout": workout if isinstance(workout, dict) else {},
        "workout_store": store,
        "hydration_bars": hydration_bars if isinstance(hydration_bars, dict) else {"pacing": None},
        "hidrate_bottle": hidrate_bottle,
        "sleep_battery": sleep_battery,
        "health": health_out if isinstance(health_out, dict) else {},
        "nutrition_store": nutrition_store if isinstance(nutrition_store, dict) else {},
        "recovery": recovery if isinstance(recovery, dict) else {},
        "coach": {"today": {"date": date}},
        "meta": {"local_today": date, "error": meta_error},
    }
