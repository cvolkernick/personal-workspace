"""Cookie-less SuperGrok generate + persist for agent Today (#493).

Reuses ``generate_grok_plans`` (same path as UI POST /api/ask/plan).
Never invents exercise lists. Idempotent once per user+civil day.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from .grok_planner import generate_grok_plans, honest_empty_workout
from .workout_plan_store import (
    flatten_plan_exercises,
    is_good_workout_plan,
    load_last_good_workout_plan,
    persist_key,
    save_last_good_workout_plan,
)
from .workout_store import stamp_today_session

_GEN_LOCK = threading.Lock()


def house_plan_user_id() -> str:
    """Cookie-less tenant for SuperGrok persist.

    Prefer a real Google ``user_id`` so grok_sessions resolve. ``default`` only
    when nothing else is configured.
    """
    for key in ("FITDASH_USER_ID", "FITDASH_INVENTORY_AGENT_USER_ID"):
        val = (os.environ.get(key) or "").strip()
        if val and val != "default":
            return val
    return (os.environ.get("FITDASH_USER_ID") or "default").strip() or "default"


def overlay_workout_on_payload(payload: dict, plan: dict) -> dict:
    """Copy SuperGrok exercises onto dashboard-shaped workout + store.plan."""
    if not isinstance(payload, dict) or not isinstance(plan, dict):
        return payload
    exercises = flatten_plan_exercises(plan.get("exercises"))
    slot = dict(payload.get("workout") or {})
    slot["session_type"] = plan.get("session_type") or slot.get("session_type")
    slot["is_rest_day"] = bool(plan.get("is_rest_day"))
    slot["exercises"] = exercises
    slot["empty"] = not exercises and not slot["is_rest_day"]
    if plan.get("message"):
        slot["message"] = plan.get("message")
    if plan.get("next_session_type"):
        slot["next_session_type"] = plan.get("next_session_type")
    slot["source"] = plan.get("source") or "grok"
    if plan.get("generate_error"):
        slot["generate_error"] = plan.get("generate_error")
    else:
        slot.pop("generate_error", None)
    payload["workout"] = slot
    store = dict(payload.get("workout_store") or {})
    store_plan = dict(store.get("plan") or {})
    store_plan["session_type"] = slot.get("session_type")
    store_plan["is_rest_day"] = slot.get("is_rest_day")
    store_plan["exercises"] = exercises
    store_plan["empty"] = slot.get("empty")
    store_plan["message"] = slot.get("message")
    store_plan["source"] = slot.get("source")
    if slot.get("next_session_type"):
        store_plan["next_session_type"] = slot.get("next_session_type")
    if slot.get("generate_error"):
        store_plan["generate_error"] = slot.get("generate_error")
    else:
        store_plan.pop("generate_error", None)
    store["plan"] = store_plan
    payload["workout_store"] = store
    return payload


def persist_grok_result(user_id: str, local_today: str, result: dict) -> dict:
    """Persist a successful SuperGrok workout. Meal persist is separate."""
    workout = result.get("workout") if isinstance(result, dict) else None
    if not result or not result.get("ok"):
        return {
            "ok": False,
            "store": None,
            "key": persist_key(user_id, local_today),
            "error": (result or {}).get("error") or "generate failed",
        }
    return save_last_good_workout_plan(user_id, local_today, workout or {})


def _letter(value) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _covers_today(saved: Optional[dict], letter: Optional[str]) -> bool:
    if not is_good_workout_plan(saved):
        return False
    want = _letter(letter)
    have = _letter(saved.get("session_type"))
    if not want:
        return True
    return have == want


def _load_generate_kwargs(user_id: str, headers=None, query: str = "") -> Dict[str, Any]:
    """Same planner inputs as UI Ask, without a Google cookie."""
    from api.dashboard import (
        _load_health,
        _load_sessions,
        _today_consumed,
        request_tz_name,
    )
    from rt_dashboard.equipment_store import load_preview_equipment
    from rt_dashboard.inventory_store import load_preview_inventory
    from rt_dashboard.library_store import apply_library_overlay, load_library_overlay
    from rt_dashboard.models import HealthSnapshot
    from rt_dashboard.nutrition_store import load_workspace_targets
    from rt_dashboard.recovery import compute_recovery_status
    from rt_dashboard.sleep_series import expand_sleep_calendar
    from rt_dashboard.timeutil import local_now, local_today_iso
    from rt_dashboard.workout_store import (
        apply_goals_volume_caps,
        brief_sessions,
        load_workspace_catalog,
        load_workspace_goals,
    )

    tz_name = request_tz_name(headers or {}, query)
    now = local_now(tz_name)
    today = local_today_iso(tz_name, now=now)
    sessions, _err, _src = _load_sessions(user_id, fallback_house=True)
    health = HealthSnapshot()
    try:
        from rt_dashboard.google_health import GoogleHealthClient

        if GoogleHealthClient().credentials_present():
            health, _herr = _load_health()
    except Exception:  # noqa: BLE001
        health = HealthSnapshot()
    had_real_sleep = any(
        float(getattr(s, "sleep_hours", 0) or 0) > 0
        and str(getattr(s, "source", "") or "") != "implied_zero"
        for s in (health.sleep or [])
    )
    recovery: Dict[str, Any] = {}
    try:
        sleep_for_recovery = expand_sleep_calendar(
            health.sleep or [],
            as_of=today,
            window_days=90,
            fill_hours=0.0,
            fill_source="implied_zero",
        )
        rec = compute_recovery_status(
            weight=health.weight or [],
            sleep=sleep_for_recovery,
            sessions=sessions,
            as_of=today,
        )
        recovery = rec.to_dict() if hasattr(rec, "to_dict") else {}
        recovery["sparse"] = not had_real_sleep
    except Exception:  # noqa: BLE001
        recovery = {"sparse": not had_real_sleep}
    goals, _gs = load_workspace_goals()
    catalog, _cs = load_workspace_catalog()
    overlay, _ls = load_library_overlay(user_id)
    catalog = apply_library_overlay(catalog, overlay)
    catalog = apply_goals_volume_caps(catalog, goals)
    equipment, _es = load_preview_equipment(user_id)
    inventory, _isrc = load_preview_inventory(user_id)
    targets, _ts = load_workspace_targets()
    consumed = _today_consumed(health, today) or {}
    food_logs = [
        f.to_dict() if hasattr(f, "to_dict") else f
        for f in (health.food_logs or [])
        if str(getattr(f, "date", "") or "")[:10] == today
    ]
    nxt = None
    stamped = stamp_today_session(
        {"session_type": None, "is_rest_day": False, "exercises": [], "empty": True},
        sessions,
        goals,
        recovery,
        as_of=today,
        fill_rest=True,
    )
    nxt = stamped.get("next_session_type") or stamped.get("session_type")
    return {
        "day": today,
        "sessions": sessions,
        "sessions_brief": brief_sessions(sessions, limit=5),
        "goals": goals,
        "catalog": catalog,
        "equipment": equipment,
        "inventory": inventory,
        "targets": targets,
        "consumed": consumed,
        "food_logs_today": food_logs,
        "recovery": recovery,
        "next_session_type": nxt,
        "stamped": stamped,
    }


def ensure_today_grok_plan(
    user_id: str,
    *,
    day: Optional[str] = None,
    auto: bool = False,
    force: bool = False,
    context: Optional[dict] = None,
    headers=None,
    query: str = "",
) -> Dict[str, Any]:
    """Generate+persist today's SuperGrok workout, or skip.

    Skip rest days. Skip when a good plan for this letter already exists.
    Fail loudly (ok=False + error) instead of inventing lifts.
    """
    uid = (user_id or "").strip() or house_plan_user_id()
    from rt_dashboard.timeutil import local_today_iso

    probe_day = str(day or "")[:10] or local_today_iso()
    if not force:
        saved = load_last_good_workout_plan(uid, probe_day)
        if is_good_workout_plan(saved):
            return {
                "ok": True,
                "skipped": "already_generated",
                "generated": False,
                "workout": saved,
                "persist": {
                    "ok": True,
                    "store": "existing",
                    "key": persist_key(uid, probe_day),
                },
                "error": None,
            }
    ctx = context if isinstance(context, dict) else None
    if ctx is None or not ctx.get("catalog"):
        loaded = _load_generate_kwargs(uid, headers=headers, query=query)
        if ctx:
            loaded.update({k: v for k, v in ctx.items() if v is not None})
        ctx = loaded
    local_today = str(day or ctx.get("day") or probe_day or "")[:10]
    stamped = ctx.get("stamped") if isinstance(ctx.get("stamped"), dict) else {}
    if not stamped:
        stamped = stamp_today_session(
            {"session_type": None, "is_rest_day": False, "exercises": [], "empty": True},
            ctx.get("sessions") or [],
            ctx.get("goals") or {},
            ctx.get("recovery") or {},
            as_of=local_today or None,
            fill_rest=True,
        )
    is_rest = bool(stamped.get("is_rest_day"))
    letter = _letter(stamped.get("session_type"))
    empty = honest_empty_workout()
    empty = stamp_today_session(
        empty,
        ctx.get("sessions") or [],
        ctx.get("goals") or {},
        ctx.get("recovery") or {},
        as_of=local_today or None,
        fill_rest=True,
        next_st_override=ctx.get("next_session_type"),
    )
    if is_rest or letter == "rest":
        return {
            "ok": True,
            "skipped": "rest",
            "generated": False,
            "workout": empty,
            "persist": {"ok": False, "error": "rest day"},
            "error": None,
        }
    with _GEN_LOCK:
        saved = None if force else load_last_good_workout_plan(uid, local_today)
        if _covers_today(saved, letter):
            return {
                "ok": True,
                "skipped": "already_generated",
                "generated": False,
                "workout": saved,
                "persist": {"ok": True, "store": "existing", "key": persist_key(uid, local_today)},
                "error": None,
            }
        grok_kwargs = {
            "targets": ctx.get("targets") or {},
            "consumed": ctx.get("consumed") or {},
            "food_logs_today": ctx.get("food_logs_today") or [],
            "recovery": ctx.get("recovery") or {},
            "sessions_brief": ctx.get("sessions_brief") or [],
            "goals": ctx.get("goals") or {},
            "catalog": ctx.get("catalog") or {},
            "next_session_type": letter or ctx.get("next_session_type"),
            "inventory": ctx.get("inventory"),
            "equipment": ctx.get("equipment"),
        }
        result = generate_grok_plans(uid, **grok_kwargs)
        # Pi/Mac cookie-less: Turso grok_sessions may miss; ~/.grok/auth.json is the local SuperGrok path.
        if (
            not result.get("ok")
            and not (os.environ.get("VERCEL") or "").strip()
            and "Connect SuperGrok" in str(result.get("error") or "")
        ):
            result = generate_grok_plans("", **grok_kwargs)
        workout = result.get("workout") if isinstance(result.get("workout"), dict) else empty
        if not result.get("ok"):
            return {
                "ok": False,
                "skipped": None,
                "generated": False,
                "workout": workout,
                "persist": {"ok": False, "error": result.get("error")},
                "error": result.get("error") or "SuperGrok generate failed",
            }
        persist = persist_grok_result(uid, local_today, result)
        if not persist.get("ok") or not is_good_workout_plan(workout):
            err = persist.get("error") or "SuperGrok returned no exercises"
            return {
                "ok": False,
                "skipped": None,
                "generated": True,
                "workout": workout,
                "persist": persist,
                "error": err,
            }
        return {
            "ok": True,
            "skipped": None,
            "generated": True,
            "workout": workout,
            "persist": persist,
            "error": None,
            "meal": result.get("meal"),
            "model": result.get("model"),
        }


def stamp_and_fill_workout(
    user_id: str,
    *,
    day: str,
    sessions=None,
    goals=None,
    recovery=None,
    headers=None,
    query: str = "",
) -> dict:
    """Stamp today's letter, then SuperGrok-fill when empty.

    Shared by Pi GET ``/api/agent/today`` and the Vercel cookie-less Today path.
    ``load_dashboard_data`` has no top-level ``workout``; the letter comes from
    ``dashboard_plan_slots``, not from the payload.
    """
    from .grok_planner import dashboard_plan_slots

    _meal, workout = dashboard_plan_slots(
        user_id,
        sessions=sessions,
        goals=goals,
        recovery=recovery,
        as_of=day,
    )
    return fill_stamped_workout(
        user_id,
        day=day,
        workout=workout,
        sessions=sessions,
        goals=goals,
        recovery=recovery,
        headers=headers,
        query=query,
    )


def fill_stamped_workout(
    user_id: str,
    *,
    day: str,
    workout: Optional[dict] = None,
    sessions=None,
    goals=None,
    recovery=None,
    headers=None,
    query: str = "",
) -> dict:
    """Saved plan, else SuperGrok once when letter stamps + not rest + empty.

    Shared by Pi GET ``/api/agent/today`` and the Vercel cookie-less Today path.
    Never invents lifts. Surfaces ``generate_error`` on the slot when SuperGrok fails.
    """
    from .workout_store import brief_sessions

    plan_uid = str(user_id or "").strip() or house_plan_user_id()
    local_day = str(day or "")[:10]
    slot = dict(workout) if isinstance(workout, dict) else {}
    saved = load_last_good_workout_plan(plan_uid, local_day)
    if is_good_workout_plan(saved):
        return saved
    letter = _letter(slot.get("session_type"))
    if not letter or slot.get("is_rest_day") or letter == "rest":
        return slot
    filled = ensure_today_grok_plan(
        plan_uid,
        day=local_day,
        auto=True,
        context={
            "day": local_day,
            "sessions": sessions or [],
            "sessions_brief": brief_sessions(sessions or [], limit=5),
            "goals": goals or {},
            "recovery": recovery or {},
            "stamped": slot,
            "next_session_type": slot.get("next_session_type"),
        },
        headers=headers,
        query=query,
    )
    if filled.get("ok") and is_good_workout_plan(filled.get("workout")):
        return filled["workout"]
    if filled.get("error"):
        slot["generate_error"] = filled["error"]
        msg = str(slot.get("message") or "")
        if "Generate today's" in msg or not msg.strip():
            slot["message"] = filled["error"]
    return slot
