"""Read-only Today brief for machine agents (Frankenfit / Restore / Pulse / Nourish).

Slices an existing dashboard payload (Pi cache or Vercel Turso/Hidrate load).
Never invents ml, loads, sessions, sip timestamps, or Active Zone Minutes.
Missing → honest empty (``today.active_zone_minutes`` is ``[]``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from .workout_store import flatten_logged_exercise


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
        "plan_exercises": [],
        "logged_exercises": [],
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

    has_signal = bool(session_type) or is_rest or bool(plan_exercises) or bool(logged)
    if not has_signal:
        return _empty_workout()
    return {
        "session_type": session_type,
        "is_rest_day": is_rest,
        "plan_exercises": plan_exercises,
        "logged_exercises": logged,
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


def export_agent_today(
    payload: Optional[Dict[str, Any]] = None,
    *,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Slice a dashboard payload into ``{ok, today, error?}``. Honest empties."""
    data = payload if isinstance(payload, dict) else {}
    meta = _as_dict(data.get("meta"))
    coach = _as_dict(data.get("coach"))
    today_board = _as_dict(coach.get("today"))
    day = _civil_day(today_board.get("date") or meta.get("local_today"))

    today = {
        "date": day or None,
        "workout": _workout_today(data, today_board, day),
        "hydration_wake": _hydration_wake(data),
        "bottle": _bottle(data),
        "wake_window": _wake_window(data),
        "active_zone_minutes": _active_zone_minutes(data),
    }
    out: Dict[str, Any] = {"ok": True, "today": today}
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
    return {
        "sessions": sess_out,
        "workout": workout if isinstance(workout, dict) else {},
        "workout_store": {"plan": workout_plan if isinstance(workout_plan, dict) else {}},
        "hydration_bars": hydration_bars if isinstance(hydration_bars, dict) else {"pacing": None},
        "hidrate_bottle": hidrate_bottle,
        "sleep_battery": sleep_battery,
        "health": health_out if isinstance(health_out, dict) else {},
        "coach": {"today": {"date": date}},
        "meta": {"local_today": date, "error": meta_error},
    }
