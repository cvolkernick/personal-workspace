"""Cookie-less SuperGrok generate + persist for agent Today (#493).

Reuses ``generate_grok_plans`` (same path as UI POST /api/ask/plan).
Never invents exercise lists. A good plan is reused for that user and
civil day until the day closes. A persisted close rebuilds the next
letter, replaces the store, and drops the unlocked gym chip (#951).
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
    load_most_recent_good_workout_plan,
    persist_key,
    save_last_good_workout_plan,
)
from .workout_store import stamp_today_session

_GEN_LOCK = threading.Lock()


def _ctx_last_wake(ctx: Optional[dict]) -> Optional[str]:
    if not isinstance(ctx, dict):
        return None
    wake = ctx.get("last_wake_at")
    if wake:
        return str(wake)
    recovery = ctx.get("recovery") if isinstance(ctx.get("recovery"), dict) else {}
    battery = (
        recovery.get("sleep_battery")
        if isinstance(recovery.get("sleep_battery"), dict)
        else {}
    )
    found = battery.get("last_wake_at") or recovery.get("last_wake_at")
    return str(found) if found else None


def _with_gym_calendar(
    result: dict,
    workout: Optional[dict],
    day: str,
    *,
    user_id: Optional[str] = None,
    rearm: bool = False,
    sessions=None,
    last_wake_at: Optional[str] = None,
) -> dict:
    """Best-effort gym Calendar sync. Never fails SuperGrok persist."""
    out = dict(result)
    board = dict(workout) if isinstance(workout, dict) else None
    if board is not None and isinstance(workout, dict) and isinstance(workout.get("context"), dict):
        board["context"] = dict(workout["context"])
    if board is not None and sessions is not None:
        board["sessions"] = sessions
    if board is not None and last_wake_at and not board.get("last_wake_at"):
        board["last_wake_at"] = last_wake_at
    try:
        from .gym_calendar import sync_gym_from_workout

        out["gym_calendar"] = sync_gym_from_workout(
            board if board is not None else workout,
            day=day,
            role="coach",
            rearm=rearm,
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001
        out["gym_calendar"] = {
            "ok": False,
            "skipped": True,
            "error": str(exc),
            "error_code": "calendar_error",
        }
    if isinstance(workout, dict) and isinstance(board, dict):
        created = (board.get("context") or {}).get("gym_chip_created")
        if created:
            ctx = dict(workout.get("context") or {})
            ctx["gym_chip_created"] = created
            workout["context"] = ctx
    return out


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
    """Copy SuperGrok exercises onto workout, store.plan, and coach.today.workout.

    Empty lists stay authoritative so canned local-planner lifts cannot leak.
    """
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
    # #1304: closed-day flags follow the plan. An open next-letter plan must
    # not inherit the stamp's "already trained" flags (stale header/letter).
    for key in ("already_trained_today", "session_closed_today", "ppl_logged_today"):
        if key in plan:
            slot[key] = plan.get(key)
        elif exercises and not slot["is_rest_day"]:
            slot.pop(key, None)
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
    coach = dict(payload.get("coach") or {})
    today = dict(coach.get("today") or {})
    today["workout"] = dict(slot)
    coach["today"] = today
    payload["coach"] = coach
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


def _recovery_score(recovery: Optional[dict]) -> Optional[float]:
    if not isinstance(recovery, dict) or recovery.get("score") is None:
        return None
    try:
        return float(recovery.get("score"))
    except (TypeError, ValueError):
        return None


def _wake_from_context(ctx: dict) -> Optional[str]:
    from .training_day import last_wake_from

    rec = ctx.get("recovery") if isinstance(ctx.get("recovery"), dict) else {}
    return last_wake_from(
        recovery=rec,
        last_wake_at=ctx.get("last_wake_at"),
        payload=ctx,
    )


def _library_for_plan(ctx: dict) -> tuple:
    """Workspace catalog and equipment when the Today fill omits them.

    ``GET /api/agent/today`` stamps the letter, then fills without putting
    the library on the context. After a close that path still has to build
    the next letter (#951). An explicit catalog or equipment dict wins.
    """
    catalog = ctx.get("catalog") if isinstance(ctx.get("catalog"), dict) else None
    if not catalog or not (catalog.get("exercises") or []):
        from .workout_store import load_workspace_catalog

        catalog, _src = load_workspace_catalog()
    equipment = ctx.get("equipment") if isinstance(ctx.get("equipment"), dict) else None
    if equipment is None:
        from .equipment_store import load_workspace_equipment

        equipment, _src = load_workspace_equipment()
    return catalog or {"exercises": []}, equipment


def _catalog_template_workout(ctx: dict, letter: Optional[str]) -> dict:
    """PPL names already in the catalog. No invented lifts."""
    catalog = ctx.get("catalog") if isinstance(ctx.get("catalog"), dict) else {}
    goals = ctx.get("goals") if isinstance(ctx.get("goals"), dict) else {}
    want = _letter(letter)
    try:
        cap = max(1, int(goals.get("exercises_per_session") or 5))
    except (TypeError, ValueError):
        cap = 5
    try:
        hard = max(1, int(goals.get("default_hard_sets") or 2))
    except (TypeError, ValueError):
        hard = 2
    exercises = []
    for raw in catalog.get("exercises") or []:
        if not isinstance(raw, dict):
            continue
        if raw.get("available") is False:
            continue
        name = str(raw.get("name") or raw.get("exercise") or "").strip()
        if not name:
            continue
        types = [str(item).strip().lower() for item in (raw.get("session_types") or [])]
        if want and types and want not in types:
            continue
        exercises.append(
            {
                "name": name,
                "prescription": {
                    "sets": hard,
                    "reps": raw.get("default_reps") or raw.get("reps") or "8-12",
                },
                "primary_muscles": raw.get("primary_muscles") or [],
            }
        )
        if len(exercises) >= cap:
            break
    return {
        "session_type": want,
        "is_rest_day": False,
        "exercises": exercises,
        "empty": not exercises,
        "source": "fallback_template",
        "message": "",
    }


def fallback_workout(
    ctx: dict,
    letter: Optional[str],
    error: str,
    user_id: str,
    day: str,
) -> dict:
    """Last good plan for this letter, else a library template. Never persists."""
    prior = load_most_recent_good_workout_plan(user_id, day, letter=letter)
    if is_good_workout_plan(prior):
        out = dict(prior or {})
        out["exercises"] = flatten_plan_exercises(out.get("exercises"))
        out["session_type"] = _letter(letter) or out.get("session_type")
        out["is_rest_day"] = False
        out["empty"] = False
        out["source"] = "fallback_last"
        out["generate_error"] = error
        out["message"] = error
        return out
    workout: dict = {}
    if isinstance(ctx.get("equipment"), dict):
        try:
            workout = _deterministic_workout(ctx, session_type=letter)
        except Exception:
            workout = {}
    if not is_good_workout_plan(workout):
        workout = _catalog_template_workout(ctx, letter)
    if not is_good_workout_plan(workout):
        return {}
    out = dict(workout)
    out["session_type"] = _letter(letter) or out.get("session_type")
    out["is_rest_day"] = False
    out["empty"] = False
    out["source"] = "fallback_template"
    out["generate_error"] = error
    out["message"] = error
    return out


def _deterministic_workout(ctx: dict, *, session_type: Optional[str] = None) -> dict:
    """Library plan for the stamped letter. No invented lifts."""
    from .test_noise import coerce_sessions
    from .workout_planner import generate_workout_plan

    rec = ctx.get("recovery") if isinstance(ctx.get("recovery"), dict) else {}
    day = str(ctx.get("day") or ctx.get("training_day") or "")[:10] or None
    catalog, equipment = _library_for_plan(ctx)
    from .daily_plan_tasks import cached_training_objective

    return generate_workout_plan(
        catalog,
        ctx.get("goals") or {},
        # ``load_dashboard_data`` hands sessions over as to_dict() payloads; the
        # planner reads ``.exercises`` (#1242).
        coerce_sessions(ctx.get("sessions")),
        recovery_label=rec.get("label"),
        recovery_score=_recovery_score(rec),
        recovery_sparse=bool(rec.get("sparse")),
        session_type=session_type,
        as_of=day,
        equipment=equipment,
        last_wake_at=_wake_from_context(ctx),
        now=ctx.get("now"),
        weighins=ctx.get("weighins"),
        training_objective=cached_training_objective(day)
        or cached_training_objective(ctx.get("training_day")),
    )


def _loud_empty(workout: Optional[dict], error: str) -> dict:
    """Empty plan must not keep a normal suggestion message (#951)."""
    out = dict(workout) if isinstance(workout, dict) else {}
    out["exercises"] = []
    out["empty"] = True
    out["generate_error"] = error
    out["message"] = error
    return out


def _letter(value) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _session_closed(slot: Optional[dict]) -> bool:
    """True when today's letter already has a persisted close (#951)."""
    if not isinstance(slot, dict):
        return False
    if slot.get("session_closed_today"):
        return True
    ctx = slot.get("context")
    return bool(isinstance(ctx, dict) and ctx.get("session_closed_today"))


# A saved plan below this is a stub, not a training day (#1304). A frozen
# remainder ("Remaining sets …") is exempt: it is meant to be short.
MIN_PLAN_LIFTS = 3
MIN_SETS_PER_LIFT = 2


def _is_remainder_plan(plan: dict) -> bool:
    if plan.get("remainder") or (plan.get("context") or {}).get("remainder"):
        return True
    return any(
        str((ex or {}).get("rationale") or "").startswith("Remaining sets")
        for ex in plan.get("exercises") or []
        if isinstance(ex, dict)
    )


def _sets_of(ex: dict) -> int:
    """Hard sets of a plan row: flat ``sets`` or library ``prescription.sets``."""
    raw = ex.get("sets")
    if raw is None and isinstance(ex.get("prescription"), dict):
        raw = ex["prescription"].get("sets")
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def saved_plan_problem(
    saved: Optional[dict],
    letter: Optional[str] = None,
    required: Optional[list] = None,
) -> Optional[str]:
    """Why a saved plan must not be served (None when it is fine) (#1304).

    Rejects: not a good plan; a closed-day stub (already trained / session
    closed flags); lifts whose push/pull/legs disagree with the plan's own
    ``session_type``; a different letter than today's stamp; and a
    degenerate plan (fewer than ``MIN_PLAN_LIFTS`` lifts or fewer than
    ``MIN_SETS_PER_LIFT`` sets per lift on average) that is not a frozen
    remainder.
    """
    if not is_good_workout_plan(saved):
        return "not a good workout_plan"
    if saved.get("already_trained_today") or saved.get("session_closed_today"):
        return "closed-day stub"
    have = _letter(saved.get("session_type"))
    exercises = [e for e in saved.get("exercises") or [] if isinstance(e, dict)]
    from .daily_plan_tasks import session_header_from_lifts

    derived, _n = session_header_from_lifts(exercises, "")
    derived = derived.lower() if derived != "SESSION" else ""
    if have in ("push", "pull", "legs") and derived and derived != have:
        return f"mislabelled: session_type {have} but lifts are {derived}"
    want = _letter(letter)
    if want and have != want:
        return f"other letter: saved {have}, today {want}"
    if not _is_remainder_plan(saved):
        sets = sum(_sets_of(ex) for ex in exercises)
        thin = all(_sets_of(ex) < MIN_SETS_PER_LIFT for ex in exercises)
        if len(exercises) < MIN_PLAN_LIFTS and thin:
            return f"degenerate: {len(exercises)} lifts, {sets} sets"
        if required:
            # A plan that drops a pinned (or library-required) lift is
            # regenerated, not served (#1316).
            from .workout_planner import missing_required_lifts

            miss = missing_required_lifts(saved, required)
            if miss:
                return "missing required lift(s): " + ", ".join(miss)
    return None


def _covers_today(
    saved: Optional[dict], letter: Optional[str], required: Optional[list] = None
) -> bool:
    return saved_plan_problem(saved, letter, required) is None


def _pins_for(ctx: dict, letter: Optional[str]) -> list:
    """Pinned lifts the library can program for ``letter`` (#1316)."""
    want = _letter(letter)
    if want not in ("push", "pull", "legs"):
        return []
    try:
        from .workout_planner import normalize_goals, required_pinned_lifts

        goals = normalize_goals(ctx.get("goals") if isinstance(ctx.get("goals"), dict) else {})
        catalog, equipment = _library_for_plan(ctx)
        return required_pinned_lifts(goals, want, catalog, equipment)
    except Exception:
        return []


def _library_mains(rolled: Optional[dict], pins: list, goals: Optional[dict]) -> tuple:
    """(ids, rows) SuperGrok must keep after the library plan is rejected.

    Pins first, then the library's main lifts in its order. Rows carry the
    library's SOP prescription, with sets floored at the main-lift floor.
    """
    from .muscle_sets import is_plan_accessory
    from .workout_planner import MAIN_LIFT_MIN_SETS, lift_key

    rows = [e for e in ((rolled or {}).get("exercises") or []) if isinstance(e, dict)]
    by_key = {lift_key(e): e for e in rows}
    ids = list(pins)
    for e in rows:
        k = lift_key(e)
        if k not in ids and not is_plan_accessory(e, goals or {}):
            ids.append(k)
    out = []
    for k in ids:
        e = by_key.get(k) or {}
        rx = e.get("prescription") if isinstance(e.get("prescription"), dict) else e
        try:
            sets = max(MAIN_LIFT_MIN_SETS, int(rx.get("sets") or 0))
        except (TypeError, ValueError):
            sets = MAIN_LIFT_MIN_SETS
        out.append(
            {
                "id": k,
                "name": e.get("name") or k,
                "min_sets": sets,
                "reps": rx.get("reps"),
                "weight_lbs": rx.get("weight_lbs"),
            }
        )
    return ids, out


def _same_letter_history(uid: str, day: str, letter: str, ctx: dict) -> dict:
    """Last plan and last log for ``letter``, beyond the 5-session brief (#1316)."""
    from datetime import date, timedelta

    out: Dict[str, Any] = {"last_plan": None, "last_log": None}
    try:
        prev = (date.fromisoformat(str(day)[:10]) - timedelta(days=1)).isoformat()
        plan = load_most_recent_good_workout_plan(uid, prev, letter=letter)
    except Exception:
        plan = None
    if isinstance(plan, dict):
        out["last_plan"] = {
            "date": plan.get("date"),
            "session_type": plan.get("session_type"),
            "exercises": [
                {
                    "name": e.get("name"),
                    "sets": (e.get("prescription") or e).get("sets"),
                    "reps": (e.get("prescription") or e).get("reps"),
                    "weight_lbs": (e.get("prescription") or e).get("weight_lbs"),
                }
                for e in flatten_plan_exercises(plan.get("exercises"))
                if isinstance(e, dict)
            ],
        }
    best = None
    for sess in ctx.get("sessions") or []:
        d = sess.to_dict() if hasattr(sess, "to_dict") else sess
        if not isinstance(d, dict) or _letter(d.get("session_type")) != letter:
            continue
        if best is None or str(d.get("date") or "") > str(best.get("date") or ""):
            best = d
    if best is not None:
        out["last_log"] = {
            "date": best.get("date"),
            "exercises": [
                {"name": e.get("name"), "sets": e.get("sets")}
                for e in best.get("exercises") or []
                if isinstance(e, dict)
            ],
        }
    return out


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
    from rt_dashboard.custom_movements import load_universe_catalog
    from rt_dashboard.library_store import apply_library_overlay, load_library_overlay
    from rt_dashboard.models import HealthSnapshot
    from rt_dashboard.nutrition_store import load_preview_targets
    from rt_dashboard.recovery import compute_recovery_status
    from rt_dashboard.sleep_series import expand_sleep_calendar
    from rt_dashboard.timeutil import local_now, local_today_iso
    from rt_dashboard.workout_store import (
        apply_goals_volume_caps,
        brief_sessions,
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
            health, _herr = _load_health(tz_name=tz_name)
    except Exception:  # noqa: BLE001
        health = HealthSnapshot()
    had_real_sleep = any(
        float(getattr(s, "sleep_hours", 0) or 0) > 0
        and str(getattr(s, "source", "") or "") != "implied_zero"
        for s in (health.sleep or [])
    )
    bat = None
    sleep_intervals = list(getattr(health, "sleep_intervals", None) or [])
    try:
        from rt_dashboard.sleep_battery import sleep_battery_from_fitdash_sleep

        bat = sleep_battery_from_fitdash_sleep(
            health.sleep or [],
            now=now,
            tz_name=tz_name,
            sleep_intervals=sleep_intervals,
        )
    except Exception:  # noqa: BLE001
        bat = None
    recovery: Dict[str, Any] = {}
    try:
        from rt_dashboard.training_day import last_wake_from, training_day_iso

        last_wake = last_wake_from(sleep_battery=bat)
        train_day = training_day_iso(
            now=now, last_wake_at=last_wake, tz_name=tz_name
        )
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
            as_of=train_day,
            rhr=health.resting_heart_rate or [],
            sleep_battery=bat if isinstance(bat, dict) else None,
            sleep_intervals=sleep_intervals,
            now=now,
            last_wake_at=last_wake,
            tz_name=tz_name,
        )
        recovery = rec.to_dict() if hasattr(rec, "to_dict") else {}
        recovery["sparse"] = not had_real_sleep
    except Exception:  # noqa: BLE001
        recovery = {"sparse": not had_real_sleep}
    if isinstance(bat, dict):
        recovery["sleep_battery"] = bat
    goals, _gs = load_workspace_goals()
    catalog, _cs = load_universe_catalog(user_id)
    overlay, _ls = load_library_overlay(user_id)
    catalog = apply_library_overlay(catalog, overlay)
    catalog = apply_goals_volume_caps(catalog, goals)
    equipment, _es = load_preview_equipment(user_id)
    inventory, _isrc = load_preview_inventory(user_id)
    targets, _ts = load_preview_targets(user_id)
    consumed = _today_consumed(
        health, today, now=now, tz_name=tz_name, sleep_battery=bat
    ) or {}
    from rt_dashboard.nutrition_day import compose_nutrition_today

    composed = compose_nutrition_today(
        now=now,
        tz_name=tz_name,
        sleep_intervals=list(getattr(health, "sleep_intervals", None) or []),
        sleep_battery=bat if isinstance(bat, dict) else None,
        daily_sleep=health.sleep or [],
        food_logs=health.food_logs or [],
    )
    food_logs = list(composed.get("food_logs_today") or [])
    if not food_logs:
        food_logs = [
            f.to_dict() if hasattr(f, "to_dict") else f
            for f in (health.food_logs or [])
            if str(getattr(f, "date", "") or "")[:10] == today
        ]
    from rt_dashboard.training_day import last_wake_from, training_day_iso

    wake = last_wake_from(recovery=recovery)
    train_day = training_day_iso(now=now, last_wake_at=wake, tz_name=tz_name)
    from .daily_plan_tasks import cached_training_objective

    nxt = None
    objective = cached_training_objective(today) or cached_training_objective(train_day)
    stamped = stamp_today_session(
        {"session_type": None, "is_rest_day": False, "exercises": [], "empty": True},
        sessions,
        goals,
        recovery,
        as_of=today,
        fill_rest=True,
        last_wake_at=wake,
        now=now,
        training_objective=objective,
    )
    nxt = stamped.get("next_session_type") or stamped.get("session_type")
    return {
        "day": today,
        "training_day": train_day,
        "last_wake_at": wake,
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
        "weighins": list(getattr(health, "weight", None) or []),
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

    Skip rest days. Skip when a good plan for this letter already exists
    on the current training day (wake window). A persisted close rolls the
    plan onto the next letter from the equipment library and cancels
    today's gym chip. Fail loudly (ok=False + error) instead of inventing
    lifts or leaving an empty list under a normal message.
    """
    uid = (user_id or "").strip() or house_plan_user_id()
    from .ai_planner import force_requested

    if force_requested(query):
        force = True
    from rt_dashboard.timeutil import local_today_iso

    probe_day = str(day or "")[:10] or local_today_iso()
    ctx = context if isinstance(context, dict) else None
    if ctx is None or not ctx.get("catalog"):
        loaded = _load_generate_kwargs(uid, headers=headers, query=query)
        if ctx:
            loaded.update({k: v for k, v in ctx.items() if v is not None})
        ctx = loaded
    # Cookie-less Today passes ``load_dashboard_data`` sessions (dicts); the
    # closed-today and fallback planners need Session objects (#1242).
    from .test_noise import coerce_sessions

    ctx = {**ctx, "sessions": coerce_sessions(ctx.get("sessions"))}
    # The plan row is keyed by the plan day the feed reads (``day``), not
    # the training day. Before the 04:00 cutoff those differ, and keying by
    # ``training_day`` wrote yesterday's row while Today read today's (#1304).
    local_today = str(
        day or ctx.get("day") or ctx.get("training_day") or probe_day or ""
    )[:10]
    from .daily_plan_tasks import cached_training_objective

    objective = cached_training_objective(
        ctx.get("day") or ctx.get("training_day") or local_today
    )
    stamped = ctx.get("stamped") if isinstance(ctx.get("stamped"), dict) else {}
    if not stamped:
        stamped = stamp_today_session(
            {"session_type": None, "is_rest_day": False, "exercises": [], "empty": True},
            ctx.get("sessions") or [],
            ctx.get("goals") or {},
            ctx.get("recovery") or {},
            as_of=str(ctx.get("day") or local_today or None),
            fill_rest=True,
            last_wake_at=ctx.get("last_wake_at"),
            training_objective=objective,
        )
    is_rest = bool(stamped.get("is_rest_day"))
    letter = _letter(stamped.get("session_type"))

    def _gym(result: dict, workout: Optional[dict], day: str, *, rearm: bool = False) -> dict:
        return _with_gym_calendar(
            result,
            workout,
            day,
            user_id=uid,
            rearm=rearm,
            sessions=ctx.get("sessions"),
            last_wake_at=_ctx_last_wake(ctx),
        )

    nxt = _letter(stamped.get("next_session_type"))
    rolled = None
    if stamped.get("session_closed_today"):
        # A persisted close rolls the plan onto the next letter (#951), and
        # the letter is now passed explicitly (#1304). Without it the planner
        # returned an "Already trained today (PUSH)" stub carrying a 1-set
        # preview of the next letter, which was saved and served.
        rolled = _deterministic_workout(
            ctx, session_type=nxt if nxt in ("push", "pull", "legs") else None
        )
        nxt_pins = _pins_for(ctx, nxt)
        if nxt in ("push", "pull", "legs") and saved_plan_problem(rolled, nxt, nxt_pins):
            # The library cannot build a real plan for the next letter. Plan
            # it like any open day (saved plan or SuperGrok) instead, keeping
            # the library's main lifts and history in front of SuperGrok (#1316).
            req_ids, req_rows = _library_mains(rolled, nxt_pins, ctx.get("goals"))
            ctx = {
                **ctx,
                "required_lifts": req_ids,
                "required_rows": req_rows,
                "same_letter_history": _same_letter_history(uid, local_today, nxt, ctx),
            }
            rolled = None
            letter = nxt
            stamped = {
                **stamped,
                "session_type": nxt,
                "session_closed_today": False,
                "already_trained_today": False,
            }
            ctx = {**ctx, "next_session_type": nxt}
    if stamped.get("session_closed_today"):
        workout = rolled if rolled is not None else _deterministic_workout(ctx)
        good = is_good_workout_plan(workout)
        err = None
        persist = {"ok": False, "error": None}
        if good:
            persist = save_last_good_workout_plan(uid, local_today, workout)
            if not persist.get("ok"):
                good = False
                err = persist.get("error") or "not a good workout_plan"
                workout = _loud_empty(workout, err)
        else:
            err = (
                workout.get("generate_error")
                or workout.get("message")
                or "No plan could be built from the library."
            )
            workout = _loud_empty(workout, err)
        return _gym(
            {
                "ok": good,
                "skipped": "session_closed",
                "generated": good,
                "workout": workout,
                "persist": persist,
                "error": err,
            },
            workout,
            local_today,
        )
    if stamped.get("already_trained_today"):
        return _gym(
            {
                "ok": True,
                "skipped": "already_trained",
                "generated": False,
                "workout": stamped,
                "persist": {"ok": False, "error": "already trained this wake"},
                "error": None,
            },
            stamped,
            local_today,
        )
    empty = honest_empty_workout()
    empty = stamp_today_session(
        empty,
        ctx.get("sessions") or [],
        ctx.get("goals") or {},
        ctx.get("recovery") or {},
        as_of=local_today or None,
        fill_rest=True,
        next_st_override=ctx.get("next_session_type"),
        training_objective=cached_training_objective(
            local_today or ctx.get("training_day")
        ),
    )
    if is_rest or letter == "rest":
        rest_workout = dict(empty)
        rest_workout["is_rest_day"] = True
        rest_workout["session_type"] = str(rest_workout.get("session_type") or "rest")
        return _gym(
            {
                "ok": True,
                "skipped": "rest",
                "generated": False,
                "workout": empty,
                "persist": {"ok": False, "error": "rest day"},
                "error": None,
            },
            rest_workout,
            local_today,
        )
    with _GEN_LOCK:
        saved = None if force else load_last_good_workout_plan(uid, local_today)
        required = list(ctx.get("required_lifts") or _pins_for(ctx, letter))
        required_rows = ctx.get("required_rows") or [
            {"id": r, "name": r, "min_sets": 2} for r in required
        ]
        if _covers_today(saved, letter, required):
            return _gym(
                {
                    "ok": True,
                    "skipped": "already_generated",
                    "generated": False,
                    "workout": saved,
                    "persist": {"ok": True, "store": "existing", "key": persist_key(uid, local_today)},
                    "error": None,
                },
                saved,
                local_today,
            )
        grok_kwargs = {
            "targets": ctx.get("targets") or {},
            "consumed": ctx.get("consumed") or {},
            "food_logs_today": ctx.get("food_logs_today") or [],
            "recovery": ctx.get("recovery") or {},
            "sessions_brief": ctx.get("sessions_brief") or [],
            "sessions": ctx.get("sessions"),
            "goals": ctx.get("goals") or {},
            "catalog": ctx.get("catalog") or {},
            "next_session_type": letter or ctx.get("next_session_type"),
            "inventory": ctx.get("inventory"),
            "equipment": ctx.get("equipment"),
            "as_of": local_today,
            "force": force,
            "weighins": ctx.get("weighins"),
        }
        if required:
            grok_kwargs["required_lifts"] = required_rows
        if ctx.get("same_letter_history"):
            grok_kwargs["same_letter_history"] = ctx.get("same_letter_history")
        result = generate_grok_plans(uid, **grok_kwargs)
        # Pi/Mac cookie-less: Turso grok_sessions may miss; ~/.grok/auth.json is the local SuperGrok path.
        if (
            not result.get("ok")
            and not (os.environ.get("VERCEL") or "").strip()
            and "Connect SuperGrok" in str(result.get("error") or "")
        ):
            result = generate_grok_plans("", **grok_kwargs)
        if result.get("ok") and required:
            from .workout_planner import missing_required_lifts

            miss = missing_required_lifts(result.get("workout"), required, ordered=True)
            if miss:
                # SuperGrok dropped (or reordered) a pinned or library-required
                # lift: reject the result, never save it (#1316).
                result = {
                    **result,
                    "ok": False,
                    "error": "SuperGrok plan dropped required lift(s): " + ", ".join(miss),
                }
        workout = result.get("workout") if isinstance(result.get("workout"), dict) else empty
        if not result.get("ok"):
            err = result.get("error") or "SuperGrok generate failed"
            if "Connect SuperGrok" not in str(err):
                fallback = fallback_workout(ctx, letter, str(err), uid, local_today)
                if is_good_workout_plan(fallback):
                    return _gym(
                        {
                            "ok": True,
                            "skipped": None,
                            "generated": False,
                            "workout": fallback,
                            "persist": {"ok": False, "error": "fallback not persisted"},
                            "error": err,
                            "meal": result.get("meal"),
                        },
                        fallback,
                        local_today,
                    )
            return {
                "ok": False,
                "skipped": None,
                "generated": False,
                "workout": workout,
                "persist": {"ok": False, "error": result.get("error")},
                "error": err,
            }
        persist = persist_grok_result(uid, local_today, result)
        if persist.get("store") == "guard_refused" and is_good_workout_plan(workout):
            # Smoke / parity guard (#1304): serve the plan, write nothing.
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
        if not persist.get("ok") or not is_good_workout_plan(workout):
            # Library fallback only when equipment is in context. A name-only
            # catalog with no gear filter is not a plan (#951).
            fallback = (
                _deterministic_workout(ctx, session_type=letter)
                if isinstance(ctx.get("equipment"), dict)
                else {}
            )
            if is_good_workout_plan(fallback):
                persist = save_last_good_workout_plan(uid, local_today, fallback)
                if persist.get("ok"):
                    return _gym(
                        {
                            "ok": True,
                            "skipped": None,
                            "generated": True,
                            "workout": fallback,
                            "persist": persist,
                            "error": None,
                        },
                        fallback,
                        local_today,
                        rearm=force,
                    )
            err = (
                (fallback or {}).get("generate_error")
                or "SuperGrok returned no exercises"
            )
            if err == "not a good workout_plan":
                err = "SuperGrok returned no exercises"
            return {
                "ok": False,
                "skipped": None,
                "generated": True,
                "workout": _loud_empty(workout, err),
                "persist": persist,
                "error": err,
            }
        return _gym(
            {
                "ok": True,
                "skipped": None,
                "generated": True,
                "workout": workout,
                "persist": persist,
                "error": None,
                "meal": result.get("meal"),
                "model": result.get("model"),
            },
            workout,
            local_today,
            rearm=force,
        )


def stamp_and_fill_workout(
    user_id: str,
    *,
    day: str,
    sessions=None,
    goals=None,
    recovery=None,
    headers=None,
    query: str = "",
    read_only: bool = False,
) -> dict:
    """Stamp today's letter, then SuperGrok-fill when empty.

    Shared by Pi GET ``/api/agent/today`` and the Vercel cookie-less Today path.
    ``load_dashboard_data`` has no top-level ``workout``; the letter comes from
    ``dashboard_plan_slots``, not from the payload.

    ``read_only`` (the ``FITDASH_AGENT_TOKEN`` principal, #1295) returns the
    saved plan or the stamp. It does not generate or persist.
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
        read_only=read_only,
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
    read_only: bool = False,
) -> dict:
    """Saved plan, else SuperGrok once when letter stamps + not rest + empty.

    Shared by Pi GET ``/api/agent/today`` and the Vercel cookie-less Today path.
    Never invents lifts. Surfaces ``generate_error`` on the slot when SuperGrok fails.

    A good plan saved earlier the same civil day can still be the pre-close
    letter. Returning it skips the roll and the gym sync. When the stamp
    says the day is already closed, fall through so the next letter
    replaces that store and the unlocked chip is deleted (#951).

    ``read_only`` stops before that fall-through. A closed day returns the
    stamp. An open day returns the saved plan or the stamp. No SuperGrok.
    """
    from .workout_store import brief_sessions

    plan_uid = str(user_id or "").strip() or house_plan_user_id()
    local_day = str(day or "")[:10]
    slot = dict(workout) if isinstance(workout, dict) else {}
    saved = load_last_good_workout_plan(plan_uid, local_day)
    pin_ctx = {"goals": goals or {}}
    saved_pins = _pins_for(pin_ctx, (saved or {}).get("session_type")) if saved else []
    if saved_plan_problem(saved, None, saved_pins) is None and not _session_closed(slot):
        return saved
    if read_only:
        # Token path (#1304): a valid saved plan is the answer. A closed day
        # still returns it when it is the same letter, so plan_exercises is
        # not []. A saved plan on another letter is not today's plan.
        if _covers_today(saved, slot.get("session_type"), saved_pins):
            return saved
        # A closed day's valid plan is the next letter's (#1304), e.g. the
        # Push stamp closed and the saved 10/21 plan is a full Pull day.
        nxt = _letter(slot.get("next_session_type"))
        if nxt and _session_closed(slot) and _covers_today(saved, nxt, saved_pins):
            return saved
        return slot
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
        if not slot.get("is_rest_day"):
            slot["message"] = filled["error"]
            slot["exercises"] = []
    return slot
